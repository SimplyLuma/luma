# SPDX-License-Identifier: MPL-2.0
"""Disks' headless UDisks2 client. No GTK, elevation, or command-line tools.

Only UDisks opens devices or changes them. Pure snapshot parsing and operation
policy are separated from the system-bus transport for negative-path tests.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import contextmanager
import math
import mmap
import os
from pathlib import Path
import stat
import time

BUS = 'org.freedesktop.UDisks2'
ROOT = '/org/freedesktop/UDisks2'
WRITE_SWITCH = 'LUMA_DISKS_ALLOW_WRITES'


def require_writes():
    """One fail-closed switch for every operation that can alter state or files."""
    if os.environ.get(WRITE_SWITCH) != '1':
        raise DiskError('Disk changes are disabled in this preview.')


def size_text(value: int | None) -> str:
    if value is None:
        return 'Not measured'
    n = max(0, value)
    for unit, divisor in [('TB', 10**12), ('GB', 10**9), ('MB', 10**6), ('kB', 1000)]:
        if n >= divisor:
            amount = n / divisor
            return f'{amount:.1f}'.removesuffix('.0') + ' ' + unit if amount < 10 else f'{amount:.0f} {unit}'
    return f'{n:.0f} bytes'


def text_bytes(value) -> str:
    if isinstance(value, str):
        return value.rstrip('\0')
    return bytes(value or []).rstrip(b'\0').decode('utf-8', 'replace')


def props(objects, path, interface):
    return objects.get(path, {}).get(BUS + '.' + interface, {})


@dataclass(frozen=True)
class Health:
    state: str
    title: str
    sentence: str
    powered_hours: int | None = None
    temperature: float | None = None
    written: int | None = None
    spare: int | None = None


def health_report(interfaces, attributes=None) -> Health:
    ata = interfaces.get(BUS + '.Drive.Ata', {})
    nvme = interfaces.get(BUS + '.NVMe.Controller', {})
    p = nvme or ata
    if not nvme and not ata.get('SmartSupported'):
        return Health('none', 'No health reporting', 'Health data is unavailable.')
    if not p.get('SmartUpdated'):
        return Health('none', 'Health report unavailable', 'Refresh to read the health report.')
    a = attributes if nvme and isinstance(attributes, dict) else {}
    hours = p.get('SmartPowerOnHours') if nvme else (p.get('SmartPowerOnSeconds', 0) // 3600 or None)
    temp = p.get('SmartTemperature', 0)
    facts = dict(powered_hours=hours or None, temperature=temp - 273.15 if temp else None,
                 written=a.get('total_data_written') or None, spare=a.get('avail_spare'))
    warnings = set(nvme.get('SmartCriticalWarning', []))
    if ata.get('SmartFailing') or warnings & {'degraded', 'readonly', 'volatile_mem', 'pmr_readonly'}:
        return Health('fail', 'Failing', 'Back up this drive and replace it.', **facts)
    bad = max(0, ata.get('SmartNumBadSectors', 0))
    if isinstance(attributes, (list, tuple)):
        # Attribute IDs collide across vendors. Use UDisks' interpreted names
        # and units, never decode raw vendor values as universal counters.
        sectors = [x[6] for x in attributes if len(x) >= 8 and x[7] == 3 and x[1] in
                   {'reallocated-sector-count', 'current-pending-sector', 'offline-uncorrectable'}]
        bad = max([bad] + sectors)
    if bad:
        return Health('warn', 'Needs attention', f'The drive reports {bad} bad or unreliable sector' + ('s' if bad != 1 else '') + '. Back up this drive.', **facts)
    if a.get('media_errors', 0) > 0:
        return Health('warn', 'Needs attention', 'Uncorrectable errors reported. Back up this drive.', **facts)
    if warnings or ata.get('SmartNumAttributesFailing', 0) > 0 or ata.get('SmartNumAttributesFailedInThePast', 0) > 0 or a.get('percent_used', 0) >= 100 or (a.get('spare_thresh', 0) > a.get('avail_spare', 100)):
        return Health('warn', 'Needs attention', 'A health warning or wear limit was reported. Back up this drive.', **facts)
    if a.get('warning_temp_time', 0) and temp >= a.get('wctemp', math.inf):
        return Health('warn', 'Needs attention', 'The drive is above its temperature warning limit.', **facts)
    return Health('ok', 'Healthy', 'No failure reported.', **facts)


@dataclass(frozen=True)
class Volume:
    path: str
    name: str
    device: str
    size: int
    format: str
    uuid: str
    mounts: tuple[str, ...]
    used: int | None
    free: int | None
    filesystem: bool
    protected: bool
    erase_reason: str
    identity: tuple
    offset: int = 0
    read_only: bool = False
    encrypted: bool = False
    locked: bool = False

    @property
    def role(self):
        if '/' in self.mounts or '/sysroot' in self.mounts:
            return 'Luma, your apps, and your files'
        if '/boot/efi' in self.mounts:
            return 'The firmware reads this volume to start your computer'
        if '/boot' in self.mounts:
            return 'Files used to start your computer'
        if self.format in ('exfat', 'vfat'):
            return 'A volume for sharing files between computers'
        if not self.filesystem:
            return 'Storage without a directly accessible filesystem'
        return 'A volume for files and folders'


@dataclass(frozen=True)
class Disk:
    path: str
    block: str
    name: str
    model: str
    device: str
    size: int
    group: str
    scheme: str
    volumes: tuple[Volume, ...]
    health: Health
    can_power_off: bool = False
    can_eject: bool = False
    identity: tuple = ()

    @property
    def unused(self):
        return max(0, self.size - sum(v.size for v in self.volumes))

    @property
    def free(self):
        if any(v.free is None for v in self.volumes):
            return None
        return self.unused + sum(v.free for v in self.volumes)


def protected_paths(objects):
    """Propagate root/system protection through partitions and crypto layers."""
    protected = set()
    for path in objects:
        b = props(objects, path, 'Block')
        mounts = [text_bytes(x) for x in props(objects, path, 'Filesystem').get('MountPoints', [])]
        table=props(objects,path,'Partition').get('Table',path)
        loop=props(objects,table,'Loop')
        owned_image=bool(loop) and loop.get('SetupByUID')==os.getuid()
        # UDisks marks loop devices as HintSystem even when created by this user.
        # Actual system mounts remain protected regardless of image ownership.
        if (b.get('HintSystem') and not owned_image) or props(objects,path,'Swapspace').get('Active') or any(x in ('/', '/sysroot', '/usr', '/boot', '/boot/efi') for x in mounts):
            protected.add(path)
    while True:
        before = set(protected)
        for path in before:
            b = props(objects, path, 'Block')
            for parent in (b.get('CryptoBackingDevice'), props(objects, path, 'Partition').get('Table')):
                if parent and parent != '/':
                    protected.add(parent)
        if before == protected:
            return protected


def volume_identity(path, b):
    return (path, b.get('DeviceNumber'), b.get('Id'), b.get('IdUUID'), b.get('Size'), b.get('IdLabel'))


def volume_from(objects, path, protected, stat=os.statvfs):
    b = props(objects, path, 'Block')
    encrypted = BUS + '.Encrypted' in objects[path]
    clear = props(objects, path, 'Encrypted').get('CleartextDevice', '/') if encrypted else '/'
    visible = clear if clear != '/' and clear in objects else path
    f = props(objects, visible, 'Filesystem')
    mounts = tuple(sorted((text_bytes(x) for x in f.get('MountPoints', [])), key=lambda path: (path not in ('/', '/sysroot'), len(path), path)))
    size = int(b.get('Size', 0))
    used = free = None
    if mounts:
        try:
            s = stat(mounts[0])
            # f_bfree includes reserved blocks; they are not files held on disk.
            # f_bavail describes space actually available to this user.
            used = max(0, (s.f_blocks - s.f_bfree) * s.f_frsize)
            free = max(0, s.f_bavail * s.f_frsize)
            if used > size or free > size:  # multi-device filesystem, not this partition's usage
                used = free = None
        except OSError:
            pass
    reason = ''
    is_fs = BUS + '.Filesystem' in objects[visible]
    if any(m in ('/', '/sysroot') for m in mounts):
        reason = 'Luma is running from this volume, so it cannot be erased while the computer is on.'
    elif path in protected:
        reason = 'This is a system volume. Erasing it is not supported here.'
    elif b.get('ReadOnly'):
        reason = 'This volume is read-only.'
    elif b.get('IdType') == 'btrfs' and props(objects, path, 'Filesystem.BTRFS').get('NumDevices') != 1:
        reason = 'The disk service has not confirmed that this Btrfs volume uses only one device. Erasing it here is disabled.'
    elif BUS + '.PartitionTable' in objects[path] or (encrypted and clear != '/') or (b.get('IdUsage') in ('crypto', 'raid') and not encrypted) or b.get('MDRaid', '/') != '/' or b.get('MDRaidMember', '/') != '/' or b.get('CryptoBackingDevice', '/') != '/':
        reason = 'This storage has layers or partitions. Erasing it is not supported in this version.'
    elif not is_fs and not encrypted:
        reason = 'Only directly accessible filesystem volumes can be erased in this version.'
    elif any(props(objects, other, 'Partition').get('Table') == path or props(objects, other, 'Block').get('CryptoBackingDevice') == path for other in objects):
        reason = 'This volume contains other storage devices and cannot be erased here.'
    dev = text_bytes(b.get('PreferredDevice') or b.get('Device'))
    visible_block = props(objects, visible, 'Block')
    return Volume(path, b.get('IdLabel') or dev.rsplit('/', 1)[-1] or 'Volume', dev, size,
                  visible_block.get('IdType') or b.get('IdType') or 'No filesystem', b.get('IdUUID') or '', mounts, used, free,
                  is_fs, path in protected, reason, volume_identity(path, b),
                  props(objects, path, 'Partition').get('Offset', 0),
                  bool(b.get('ReadOnly') or b.get('IdType') in ('iso9660','squashfs','erofs')),
                  encrypted, encrypted and visible == path)


def parse_disks(objects, attributes=None, stat=os.statvfs):
    protected = protected_paths(objects)
    disks = []
    for path, interfaces in objects.items():
        block = props(objects, path, 'Block')
        if not block or block.get('HintIgnore') or props(objects, path, 'Partition') or block.get('CryptoBackingDevice', '/') != '/':
            continue
        drive_path = block.get('Drive', '/')
        drive = props(objects, drive_path, 'Drive')
        loop = BUS + '.Loop' in interfaces
        if loop and not props(objects,path,'Loop').get('BackingFile'): continue
        if not drive and not loop:  # avoid zram, device-mapper and duplicate layered capacity
            continue
        children = [p for p in objects if props(objects, p, 'Partition').get('Table') == path and not props(objects, p, 'Partition').get('IsContainer')]
        if not children and BUS + '.PartitionTable' not in interfaces:
            children = [path]
        volumes = tuple(sorted((volume_from(objects, p, protected, stat) for p in children), key=lambda v: v.offset))
        group = 'Disk images' if loop else 'Plugged in' if drive.get('ConnectionBus') in ('usb', 'ieee1394', 'sdio') or drive.get('Removable') else 'In this computer'
        model = ' '.join(str(drive.get(x, '')).strip() for x in ('Vendor', 'Model')).strip()
        dev = text_bytes(block.get('PreferredDevice') or block.get('Device'))
        backing = text_bytes(props(objects, path, 'Loop').get('BackingFile'))
        name = block.get('HintName') or model or (Path(backing).name if loop and backing else 'Disk image' if loop else dev.rsplit('/', 1)[-1])
        scheme = {'gpt': 'GUID partition table', 'dos': 'Master boot record'}.get(props(objects, path, 'PartitionTable').get('Type'), 'No partition table')
        disks.append(Disk(drive_path if drive else path, path, name, model, dev, int(block.get('Size', 0)), group, scheme,
                          volumes, health_report(objects.get(drive_path, {}), (attributes or {}).get(drive_path)),
                          drive.get('CanPowerOff', False), drive.get('Ejectable', False), volume_identity(path, block)))
    return tuple(sorted(disks, key=lambda d: (('In this computer', 'Plugged in', 'Disk images').index(d.group), d.device)))


def ring_segments(volumes, total):
    """Minimum visible arcs normalized to one turn; never overlap tiny volumes."""
    sizes = [v.size for v in volumes]
    spare = max(0, total - sum(sizes))
    if spare:
        sizes.append(spare)
    weights = [max(s / max(1, total), .02) if i < len(volumes) else s / max(1, total)
               for i, s in enumerate(sizes)]
    scale = sum(weights) or 1
    at = 0.
    result = []
    for i, weight in enumerate(weights):
        width = weight / scale
        v = volumes[i] if i < len(volumes) else None
        result.append((at, width, None if v is None or v.used is None else min(1, v.used / max(1, v.size))))
        at += width
    return result


class DiskError(RuntimeError):
    def __init__(self, message, detail=''):
        super().__init__(message)
        self.detail = detail


class DiskCancelled(DiskError):
    pass


def explain_error(error):
    if isinstance(error, DiskError):
        return error
    text = str(error)
    if 'NotAuthorized' in text or 'PermissionDenied' in text:
        return DiskError('Permission was not granted. No further steps were started.', text)
    if 'DeviceBusy' in text:
        return DiskError('The volume is in use. Close files and apps using it, then try again.', text)
    if 'Cancelled' in text or 'Canceled' in text:
        return DiskError('The operation was cancelled.', text)
    return DiskError('The disk operation could not finish. Check that the drive is connected.', text)


class UDisksClient:
    def __init__(self, changed=lambda: None, *, interactive=True):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.interactive=interactive
        self.manager = Gio.DBusObjectManagerClient.new_for_bus_sync(Gio.BusType.SYSTEM, Gio.DBusObjectManagerClientFlags.NONE, BUS, ROOT, None, None, None)
        self.connection = self.manager.get_connection()
        self.attributes = {}
        self.generation = 0
        self.topology_generation = 0
        self.changed = changed
        self.pending = 0
        self.signals = []
        for signal in ('object-added', 'object-removed', 'interface-added', 'interface-removed', 'interface-proxy-properties-changed', 'notify::name-owner'):
            self.signals.append(self.manager.connect(signal, self._changed if signal == 'interface-proxy-properties-changed' else self._topology_changed))

    def _topology_changed(self, *_args):
        # Even a reinserted clone with identical UUID/label needs fresh consent.
        self.topology_generation += 1
        self.attributes.clear()
        self._changed()

    def _changed(self, *_args):
        self.generation += 1
        if not self.pending:
            self.pending = self.GLib.timeout_add(100, self._notify)

    def _notify(self):
        self.pending = 0
        self.changed()
        return False

    def close(self):
        for signal in self.signals:
            self.manager.disconnect(signal)
        if self.pending:
            self.GLib.source_remove(self.pending)
            self.pending = 0

    def snapshot(self):
        # A fresh bus snapshot is essential before consequential operations.
        return self.connection.call_sync(BUS, ROOT, 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects', None, None, self.Gio.DBusCallFlags.NONE, 10000, None).unpack()[0]

    def disks(self):
        disks = parse_disks(self.snapshot(), self.attributes)
        return tuple(replace(d, identity=d.identity+(self.topology_generation,), volumes=tuple(replace(v, identity=v.identity + (self.topology_generation,)) for v in d.volumes)) for d in disks)

    def authorization_options(self):
        return {} if getattr(self,'interactive',True) else {'auth.no_user_interaction':self.GLib.Variant('b',True)}

    def call(self, path, interface, method, signature='(a{sv})', args=({},)):
        # Backstop for future UI paths: only explicit read/query calls bypass
        # the switch. Methods not reviewed here default to being writes.
        if (interface, method) not in {
            ('Manager', 'CanFormat'), ('Manager', 'CanResize'),
            ('Drive.Ata', 'SmartUpdate'), ('Drive.Ata', 'SmartGetAttributes'),
            ('NVMe.Controller', 'SmartUpdate'), ('NVMe.Controller', 'SmartGetAttributes'),
        }:
            require_writes()
        if args and isinstance(args[-1],dict):
            args=(*args[:-1],{**args[-1],**self.authorization_options()})
        try:
            timeout = 86_400_000 if (interface, method) == ('Block', 'Format') else 3_600_000
            result = self.connection.call_sync(BUS, path, BUS + '.' + interface, method, self.GLib.Variant(signature, args), None, self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, timeout, None)
            return result.unpack()
        except Exception as e:
            raise explain_error(e) from e

    def current(self, volume):
        objects = self.snapshot()
        identity = volume_identity(volume.path, props(objects, volume.path, 'Block'))
        if hasattr(self, 'topology_generation'):
            identity += (self.topology_generation,)
        if volume.path not in objects or identity != volume.identity:
            raise DiskError('The selected volume changed or was disconnected. Select it again before continuing.')
        return volume_from(objects, volume.path, protected_paths(objects))

    def mount(self, volume):
        require_writes()
        v = self.current(volume)
        target = self._filesystem_target(v)
        if not v.filesystem:
            raise DiskError('This volume does not expose a mountable filesystem.')
        if v.mounts and v.protected:
            raise DiskError('This volume is used by the running system and cannot be unmounted here.')
        return self.call(target, 'Filesystem', 'Unmount' if v.mounts else 'Mount')

    def _filesystem_target(self, volume):
        if not volume.encrypted:
            return volume.path
        clear = props(self.snapshot(), volume.path, 'Encrypted').get('CleartextDevice', '/')
        if clear == '/':
            raise DiskError('Unlock this encrypted volume before using its filesystem.')
        return clear

    def _encrypted(self, volume):
        v = self.current(volume)
        if not v.encrypted or v.protected or v.read_only:
            raise DiskError('This is not an editable encrypted volume.')
        return v

    def unlock(self, volume, passphrase):
        require_writes()
        v = self._encrypted(volume)
        if not v.locked or not passphrase:
            raise DiskError('Enter a passphrase for a locked volume.')
        clear = self.call(v.path, 'Encrypted', 'Unlock', '(sa{sv})', (passphrase, {}))[0]
        objects = self.snapshot()
        if props(objects, clear, 'Block').get('CryptoBackingDevice') != v.path:
            raise DiskError('The volume unlocked, but its cleartext device could not be verified.')
        if BUS + '.Filesystem' not in objects.get(clear, {}):
            raise DiskError('The volume unlocked, but it does not contain a mountable filesystem.')
        try:
            self.call(clear, 'Filesystem', 'Mount')
        except DiskError as error:
            raise DiskError('The volume unlocked, but mounting did not finish.', str(error)) from error
        return clear

    def lock(self, volume):
        require_writes()
        v = self._encrypted(volume)
        if v.locked or v.mounts:
            raise DiskError('Unmount the encrypted volume before locking it.')
        return self.call(v.path, 'Encrypted', 'Lock')

    def change_passphrase(self, volume, old_passphrase, new_passphrase, backup_file):
        require_writes()
        v = self._encrypted(volume)
        if not old_passphrase or not new_passphrase or old_passphrase == new_passphrase:
            raise DiskError('Enter the current passphrase and a different new passphrase.')
        if Path(backup_file).exists():
            raise DiskError('Choose a new file for the LUKS header backup.')
        # HeaderBackup refuses to replace an existing file. A successful backup
        # is a hard prerequisite for changing the keyslot.
        self.call(v.path, 'Encrypted', 'HeaderBackup', '(sa{sv})', (str(backup_file), {}))
        self._encrypted(volume)
        return self.call(v.path, 'Encrypted', 'ChangePassphrase', '(ssa{sv})',
                         (old_passphrase, new_passphrase, {}))

    def rename(self, volume, label):
        require_writes()
        v = self.current(volume)
        if not v.filesystem or v.read_only:
            raise DiskError('This filesystem cannot be renamed.')
        if '\0' in label:
            raise DiskError('The volume name contains an invalid character.')
        return self.call(self._filesystem_target(v), 'Filesystem', 'SetLabel', '(sa{sv})', (label, {}))

    def check(self, volume, repair=False, confirmation=''):
        require_writes()
        v = self.current(volume)
        if v.mounts:
            raise DiskError('Unmount this volume before checking or repairing its filesystem.')
        if not v.filesystem or v.protected:
            raise DiskError('This volume cannot be checked here while the system is running.')
        if repair and confirmation != v.name:
            raise DiskError('Type the volume name exactly before repairing it. Repair may change or remove damaged files.')
        if repair and v.erase_reason:
            raise DiskError(v.erase_reason)
        return self.call(self._filesystem_target(v), 'Filesystem',
                         'Repair' if repair else 'Check')[0]

    def formats(self):
        result = []
        for fmt in ('btrfs', 'ext4', 'exfat', 'ntfs', 'vfat'):
            if self.call(ROOT + '/Manager', 'Manager', 'CanFormat', '(s)', (fmt,))[0][0]:
                result.append(fmt)
        return result

    @contextmanager
    def hold_loop(self, block):
        """Keep an image attached across UDisks' temporary ownership mount.

        OpenDevice owns authorization. A normal read FD pins a loop without
        excluding filesystem tools; physical disks never take this path.
        """
        objects=self.snapshot()
        table=props(objects,block,'Partition').get('Table',block)
        loop=dict(props(objects,table,'Loop'))
        if not loop:
            yield
            return
        if loop.get('SetupByUID')!=os.getuid() or not loop.get('BackingFile'):
            raise DiskError('This disk image was not attached by your account.')
        result,fds=self.connection.call_with_unix_fd_list_sync(
            BUS,table,BUS+'.Block','OpenDevice',self.GLib.Variant('(sa{sv})',('r',self.authorization_options())),
            None,self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,120000,None,None)
        fd=fds.get(result.unpack()[0])
        try:
            yield
        finally:
            try:
                now=props(self.snapshot(),table,'Loop')
                if now.get('BackingFile')==loop.get('BackingFile') and now.get('Autoclear')!=loop.get('Autoclear'):
                    self.call(table,'Loop','SetAutoclear','(ba{sv})',(bool(loop.get('Autoclear')),{}))
            finally:
                os.close(fd)

    def erase(self, volume, confirmation, fmt, label, *, zero=False, encrypt_passphrase=None):
        require_writes()
        v=self.current(volume)
        if v.erase_reason: raise DiskError(v.erase_reason)
        if confirmation!=v.name: raise DiskError('Type the volume name exactly to confirm erasing it.')
        with self.hold_loop(volume.path):
            return self._erase(volume,confirmation,fmt,label,zero=zero,
                               encrypt_passphrase=encrypt_passphrase)

    def _erase(self, volume, confirmation, fmt, label, *, zero=False, encrypt_passphrase=None):
        v = self.current(volume)
        if v.erase_reason:
            raise DiskError(v.erase_reason)
        if confirmation != v.name:
            raise DiskError('Type the volume name exactly to confirm erasing it.')
        if fmt not in ('btrfs', 'ext4', 'exfat', 'ntfs', 'vfat'):
            raise DiskError('Choose a supported filesystem format.')
        if fmt not in self.formats():
            raise DiskError('The selected filesystem is unavailable on this computer.')
        if v.mounts:
            self.call(v.path, 'Filesystem', 'Unmount')
        # Never format after a failed unmount, stale selection, or changed topology.
        v = self.current(volume)
        if v.erase_reason or v.mounts:
            raise DiskError(v.erase_reason or 'The volume is still mounted. It was not erased.')
        options = {'label': self.GLib.Variant('s', label), 'no-discard': self.GLib.Variant('b', True), 'take-ownership': self.GLib.Variant('b', True), 'update-partition-type': self.GLib.Variant('b', True)}
        if encrypt_passphrase is not None:
            if len(encrypt_passphrase) < 8:
                raise DiskError('Use a passphrase with at least eight characters.')
            options['encrypt.passphrase'] = self.GLib.Variant('s', encrypt_passphrase)
            options['encrypt.type'] = self.GLib.Variant('s', 'luks2')
        # Quick format is explicit: omit erase. Full overwrite requests only
        # the documented zero mode; never ATA/NVMe controller-wide sanitization.
        if zero:
            options['erase'] = self.GLib.Variant('s', 'zero')
        return self.call(v.path, 'Block', 'Format', '(sa{sv})', (fmt, options))

    def refresh_health(self, disk):
        objects = self.snapshot()
        interfaces = objects.get(disk.path, {})
        interface = 'NVMe.Controller' if BUS + '.NVMe.Controller' in interfaces else 'Drive.Ata'
        if BUS + '.' + interface not in interfaces:
            return
        options = {'nowakeup': self.GLib.Variant('b', True)} if interface == 'Drive.Ata' else {}
        self.call(disk.path, interface, 'SmartUpdate', '(a{sv})', (options,))
        self.attributes[disk.path] = self.call(disk.path, interface, 'SmartGetAttributes', '(a{sv})', (options,))[0]

    def start_selftest(self, disk):
        require_writes()
        objects = self.snapshot()
        block = props(objects, disk.block, 'Block')
        identity = volume_identity(disk.block, block) + (self.topology_generation,)
        if not block or identity != disk.identity:
            raise DiskError('The selected drive changed. Select it again.')
        interfaces = objects.get(disk.path, {})
        interface = ('NVMe.Controller' if BUS + '.NVMe.Controller' in interfaces else
                     'Drive.Ata' if BUS + '.Drive.Ata' in interfaces else None)
        if interface is None:
            raise DiskError('This drive does not support a SMART self-test.')
        return self.call(disk.path, interface, 'SmartSelftestStart', '(sa{sv})', ('short', {}))

    def _current_drive_objects(self, disk):
        objects = self.snapshot()
        block = props(objects, disk.block, 'Block')
        identity = volume_identity(disk.block, block) + (self.topology_generation,)
        if not block or identity != disk.identity or disk.path not in objects:
            raise DiskError('The selected drive changed. Select it again.')
        return objects

    def drive_settings(self, disk):
        objects = self._current_drive_objects(disk)
        drive = props(objects, disk.path, 'Drive')
        ata = props(objects, disk.path, 'Drive.Ata')
        config = drive.get('Configuration', {})
        def unpack(value):
            return value.unpack() if hasattr(value, 'unpack') else value
        return (unpack(config.get('ata-pm-standby', 0)),
                unpack(config.get('ata-write-cache-enabled', ata.get('WriteCacheEnabled', False))),
                bool(ata.get('PmSupported')), bool(ata.get('WriteCacheSupported')))

    def _backup_config(self, source: Path, prefix: str) -> Path:
        backup_dir = Path.home() / '.local/share/luma-disks/backups'
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = backup_dir / f'{prefix}-{time.time_ns()}.bak'
        data = source.read_bytes() if source.exists() else b''
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        return backup

    def set_drive_settings(self, disk, *, standby_minutes: int, write_cache: bool):
        require_writes()
        if standby_minutes not in (0, 10):
            raise DiskError('Choose never or 10 minutes for standby.')
        objects = self._current_drive_objects(disk)
        drive = props(objects, disk.path, 'Drive')
        ata = props(objects, disk.path, 'Drive.Ata')
        if not drive.get('Id') or not ata:
            raise DiskError('Drive settings are available only for an ATA drive with a stable ID.')
        if standby_minutes and not ata.get('PmSupported'):
            raise DiskError('This drive does not support standby settings.')
        if not ata.get('WriteCacheSupported'):
            raise DiskError('This drive does not support write cache settings.')
        original = dict(drive.get('Configuration', {}))
        updated = dict(original)
        updated['ata-pm-standby'] = self.GLib.Variant('i', 120 if standby_minutes else 0)
        updated['ata-write-cache-enabled'] = self.GLib.Variant('b', write_cache)
        config_path = Path('/etc/udisks2') / (drive['Id'] + '.conf')
        backup = self._backup_config(config_path, 'drive-settings')
        fresh = self._current_drive_objects(disk)
        if dict(props(fresh, disk.path, 'Drive').get('Configuration', {})) != original:
            raise DiskError(f'Drive settings changed before saving. Backup: {backup}')
        self.call(disk.path, 'Drive', 'SetConfiguration', '(a{sv}a{sv})', (updated, {}))
        return backup

    def mount_configuration(self, volume):
        v = self.current(volume)
        if not v.filesystem or v.protected or v.read_only or v.locked:
            raise DiskError('Mount options are unavailable for this volume.')
        target = self._filesystem_target(v)
        items = [item for item in props(self.snapshot(), target, 'Block').get('Configuration', [])
                 if item[0] == 'fstab']
        if len(items) > 1:
            raise DiskError('This volume has several mount entries; edit them with a system administrator.')
        details = items[0][1] if items else {}
        def value(key):
            raw = details.get(key, b'')
            return text_bytes(raw.unpack() if hasattr(raw, 'unpack') else raw)
        options = set(filter(None, value('opts').split(',')))
        return (value('dir'), 'noauto' not in options, 'ro' in options,
                'x-gvfs-hide' not in options, items[0] if items else None)

    def set_mount_configuration(self, volume, *, directory: str, at_startup: bool,
                                read_only: bool, show_in_filer: bool):
        require_writes()
        v = self.current(volume)
        if not v.filesystem or v.protected or v.read_only or v.locked:
            raise DiskError('This volume cannot have persistent mount options here.')
        target = self._filesystem_target(v)
        fs_uuid = props(self.snapshot(), target, 'Block').get('IdUUID')
        if not fs_uuid:
            raise DiskError('This filesystem has no stable UUID for startup mounting.')
        if (not directory.startswith('/') or directory == '/' or
                any(char in directory for char in ('\0', '\n', '\r', '\t', ' '))):
            raise DiskError('Choose a full mount directory without spaces.')
        _old_dir, _startup, _readonly, _show, item = self.mount_configuration(volume)
        details = dict(item[1]) if item else {}
        old_options = details.get('opts', b'')
        old_options = text_bytes(old_options.unpack() if hasattr(old_options, 'unpack') else old_options)
        options = [part for part in old_options.split(',') if part and part not in
                   ('auto', 'noauto', 'ro', 'rw', 'x-gvfs-show', 'x-gvfs-hide')]
        if not options:
            options = ['defaults']
        options += ['auto' if at_startup else 'noauto', 'ro' if read_only else 'rw',
                    'x-gvfs-show' if show_in_filer else 'x-gvfs-hide']
        def encoded(value: str):
            return self.GLib.Variant('ay', value.encode('utf-8') + b'\0')
        if not item:
            details['fsname'] = encoded('UUID=' + fs_uuid)
            details['type'] = encoded(v.format)
            details['freq'] = self.GLib.Variant('i', 0)
            details['passno'] = self.GLib.Variant('i', 0)
        details['dir'] = encoded(directory)
        details['opts'] = encoded(','.join(options))
        backup = self._backup_config(Path('/etc/fstab'), 'mount-options')
        self.current(volume)
        if self.mount_configuration(volume)[4] != item:
            raise DiskError(f'Mount options changed before saving. Backup: {backup}')
        new_item = ('fstab', details)
        if item:
            self.call(target, 'Block', 'UpdateConfigurationItem',
                      '((sa{sv})(sa{sv})a{sv})', (item, new_item, {}))
        else:
            self.call(target, 'Block', 'AddConfigurationItem',
                      '((sa{sv})a{sv})', (new_item, {}))
        return backup

    def open_read(self, block, *, benchmark=False):
        # UDisks authorizes and opens the FD. This app never opens a device path.
        flags = os.O_CLOEXEC | (os.O_DIRECT | os.O_SYNC if benchmark else os.O_EXCL)
        result, fds = self.connection.call_with_unix_fd_list_sync(BUS, block, BUS + '.Block', 'OpenDevice', self.GLib.Variant('(sa{sv})', ('r', {'flags': self.GLib.Variant('i', flags)})), None, self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, None, None)
        return fds.get(result.unpack()[0])

    def benchmark(self, volume, progress, cancel):
        v = self.current(volume)
        fd = self.open_read(v.path, benchmark=True)
        try:
            return read_benchmark(fd, v.size, progress, cancel)
        finally:
            os.close(fd)

    def save_image(self, disk, destination, progress, cancel):
        require_writes()
        objects = self.snapshot()
        b = props(objects, disk.block, 'Block')
        if not b or text_bytes(b.get('PreferredDevice') or b.get('Device')) != disk.device or b.get('Size') != disk.size:
            raise DiskError('This disk changed. Select it again before saving an image.')
        current = next((d for d in parse_disks(objects) if d.block == disk.block), None)
        if current is None or any(v.mounts for v in current.volumes):
            raise DiskError('Unmount every volume on this disk before saving an image. The running system disk must be backed up from another boot device.')
        fd = self.open_read(disk.block)
        target = Path(destination)
        created = False
        created_identity = None
        try:
            # Exclusive create: never replace the person's existing backup.
            output_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            created = True
            output_stat = os.fstat(output_fd)
            created_identity = (output_stat.st_dev, output_stat.st_ino)
            with os.fdopen(output_fd, 'wb') as out:
                copied = 0
                while copied < disk.size:
                    if cancel.is_set():
                        raise DiskCancelled('Image copy cancelled.')
                    data = os.read(fd, min(4 * 1024 * 1024, disk.size - copied))
                    if not data:
                        raise DiskError('The drive stopped reading before the image was complete.')
                    out.write(data)
                    copied += len(data)
                    progress(copied / max(1, disk.size))
                out.flush()
                os.fsync(out.fileno())
        except Exception:
            if created:
                try:
                    current_stat = target.lstat()
                    if (current_stat.st_dev, current_stat.st_ino) == created_identity:
                        target.unlink()  # only the incomplete file created above
                except FileNotFoundError:
                    pass
            raise
        finally:
            os.close(fd)

    def save_partition_image(self, volume, destination, progress, cancel):
        require_writes()
        v = self.current(volume)
        if v.mounts or v.protected or v.read_only or (v.encrypted and not v.locked):
            raise DiskError('Unmount this editable volume before saving its image.')
        fd = self.open_read(v.path)
        created = False
        created_identity = None
        target = Path(destination)
        try:
            output_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            created = True
            output_stat = os.fstat(output_fd)
            created_identity = (output_stat.st_dev, output_stat.st_ino)
            with os.fdopen(output_fd, 'wb') as out:
                copied = 0
                while copied < v.size:
                    if cancel.is_set():
                        raise DiskCancelled('Image copy cancelled.')
                    data = os.read(fd, min(4 * 1024 * 1024, v.size - copied))
                    if not data:
                        raise DiskError('The volume stopped reading before the image was complete.')
                    out.write(data)
                    copied += len(data)
                    progress(copied / max(1, v.size))
                out.flush()
                os.fsync(out.fileno())
        except Exception:
            if created:
                try:
                    current_stat = target.lstat()
                    if (current_stat.st_dev, current_stat.st_ino) == created_identity:
                        target.unlink()
                except FileNotFoundError:
                    pass
            raise
        finally:
            os.close(fd)

    def restore_image(self, target, image_file, backup_file, progress, cancel):
        """Back up the complete target before opening it writable through UDisks."""
        require_writes()
        source_fd = os.open(image_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise DiskError('Choose a regular disk image file.')
            if Path(image_file).resolve() == Path(backup_file).resolve() or Path(backup_file).exists():
                raise DiskError('Choose a new, separate file for the mandatory target backup.')
            disk_target = isinstance(target, Disk)
            if disk_target:
                disk = target
                self.save_image(disk, backup_file, lambda fraction: progress(fraction * .5), cancel)
                objects = self.snapshot()
                block = props(objects, disk.block, 'Block')
                current_id = volume_identity(disk.block, block) + (self.topology_generation,)
                if current_id != disk.identity or disk.block in protected_paths(objects) or block.get('ReadOnly'):
                    raise DiskError('The target disk changed after its backup. No image was restored.')
                if any(v.mounts for d in parse_disks(objects) if d.block == disk.block for v in d.volumes):
                    raise DiskError('Unmount every target volume before restoring an image.')
                path, size = disk.block, disk.size
            else:
                self.save_partition_image(target, backup_file,
                                          lambda fraction: progress(fraction * .5), cancel)
                v = self.current(target)
                if v.mounts or v.protected or v.read_only or (v.encrypted and not v.locked):
                    raise DiskError('The target volume changed after its backup. No image was restored.')
                path, size = v.path, v.size
            if source_stat.st_size != size:
                raise DiskError('The image size must exactly match the target size. The backup was saved; no restore began.')
            latest = os.fstat(source_fd)
            if (latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns, latest.st_ctime_ns) != (
                    source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
                    source_stat.st_mtime_ns, source_stat.st_ctime_ns):
                raise DiskError('The image file changed during backup. No restore began.')
            flags = os.O_EXCL | os.O_SYNC | os.O_CLOEXEC
            result, fds = self.connection.call_with_unix_fd_list_sync(
                BUS, path, BUS + '.Block', 'OpenDevice',
                self.GLib.Variant('(sa{sv})', ('w', {'flags': self.GLib.Variant('i', flags)})),
                None, self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
                120000, None, None)
            output_fd = fds.get(result.unpack()[0])
            try:
                copied = 0
                while copied < size:
                    if cancel.is_set():
                        raise DiskCancelled('Restore stopped. The target may now contain incomplete data.')
                    chunk = os.read(source_fd, min(4 * 1024 * 1024, size - copied))
                    if not chunk:
                        raise DiskError('The source image ended early. The target may now contain incomplete data.')
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(output_fd, view):]
                    copied += len(chunk)
                    progress(.5 + copied / max(1, size) * .5)
                os.fsync(output_fd)
            finally:
                os.close(output_fd)
            self.call(path, 'Block', 'Rescan')
        finally:
            os.close(source_fd)

    def format_disk(self, disk, backup_file, confirmation, fmt, label,
                    progress, cancel, *, zero=False, encrypt_passphrase=None):
        """Save a full image before formatting a removable whole disk."""
        require_writes()
        if confirmation != disk.name or disk.group == 'In this computer':
            raise DiskError('Only a selected external disk can be formatted here.')
        if fmt not in ('btrfs', 'ext4', 'exfat', 'ntfs', 'vfat'):
            raise DiskError('Choose a supported filesystem format.')
        if fmt not in self.formats():
            raise DiskError('The selected filesystem is unavailable on this computer.')
        if Path(backup_file).exists():
            raise DiskError('Choose a new file for the complete disk backup.')
        objects = self._current_drive_objects(disk)
        block = props(objects, disk.block, 'Block')
        if disk.block in protected_paths(objects) or block.get('ReadOnly'):
            raise DiskError('The selected disk is read-only or used by the system.')
        if any(v.mounts for d in parse_disks(objects) if d.block == disk.block for v in d.volumes):
            raise DiskError('Unmount every volume on this disk before formatting it.')
        self.save_image(disk, backup_file, progress, cancel)
        fresh = self._current_drive_objects(disk)
        block = props(fresh, disk.block, 'Block')
        if disk.block in protected_paths(fresh) or block.get('ReadOnly') or any(
                v.mounts for d in parse_disks(fresh) if d.block == disk.block for v in d.volumes):
            raise DiskError('The disk changed after backup. It was not formatted.')
        options = {'label': self.GLib.Variant('s', label),
                   'no-discard': self.GLib.Variant('b', True),
                   'take-ownership': self.GLib.Variant('b', True)}
        if zero:
            options['erase'] = self.GLib.Variant('s', 'zero')
        if encrypt_passphrase is not None:
            if len(encrypt_passphrase) < 8:
                raise DiskError('Use a passphrase with at least eight characters.')
            options['encrypt.passphrase'] = self.GLib.Variant('s', encrypt_passphrase)
            options['encrypt.type'] = self.GLib.Variant('s', 'luks2')
        self.call(disk.block, 'Block', 'Format', '(sa{sv})', (fmt, options))
        return backup_file

    def attach_image(self, filename):
        require_writes()
        fd = os.open(filename, os.O_RDONLY | os.O_CLOEXEC)
        try:
            fds = self.Gio.UnixFDList.new()
            index = fds.append(fd)
            self.connection.call_with_unix_fd_list_sync(BUS, ROOT + '/Manager', BUS + '.Manager', 'LoopSetup', self.GLib.Variant('(ha{sv})', (index, {'read-only': self.GLib.Variant('b', True)})), None, self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, fds, None)
        finally:
            os.close(fd)

    def remove_disk(self, disk, eject=False):
        require_writes()
        objects = self.snapshot()
        siblings = [d for d in parse_disks(objects, self.attributes) if d.path == disk.path]
        if not any(d.block == disk.block and d.device == disk.device for d in siblings):
            raise DiskError('This drive is no longer connected.')
        if any(v.mounts or v.protected for d in siblings for v in d.volumes):
            raise DiskError('Unmount every volume on this drive before disconnecting it.')
        return self.call(disk.path, 'Drive', 'Eject' if eject else 'PowerOff')


def read_benchmark(fd, size, progress, cancel, *, samples=24):
    """Bounded O_DIRECT reads into page-aligned memory, then small seek reads."""
    alignment = 4096
    chunk = min(1024 * 1024, size // alignment * alignment)
    if chunk < alignment:
        raise DiskError('This volume is too small to measure.')
    rates, latencies = [], []
    with mmap.mmap(-1, chunk) as buffer:
        for i in range(samples):
            if cancel.is_set():
                raise DiskCancelled('Speed test cancelled.')
            offset = int((size - chunk) * i / max(1, samples - 1)) // alignment * alignment
            start = time.perf_counter()
            count = os.preadv(fd, [buffer], offset)
            elapsed = time.perf_counter() - start
            if count != chunk:
                raise DiskError('The drive returned an incomplete read. The test stopped.')
            rates.append(count / max(elapsed, 1e-9))
            progress(tuple(rates))
        view = memoryview(buffer)[:alignment]
        try:
            for i in range(64):
                if cancel.is_set():
                    raise DiskCancelled('Speed test cancelled.')
                offset = ((i * 2654435761) % max(1, (size - alignment) // alignment)) * alignment
                start = time.perf_counter()
                count = os.preadv(fd, [view], offset)
                if count != alignment:
                    raise DiskError('The drive returned an incomplete read. The test stopped.')
                latencies.append((time.perf_counter() - start) * 1000)
        finally:
            view.release()
    return len(rates) / sum(1/rate for rate in rates), sum(latencies) / len(latencies)
