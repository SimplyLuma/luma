# SPDX-License-Identifier: MPL-2.0
"""Partition transactions through UDisks, with fresh topology checks at each step."""
from dataclasses import dataclass
from .backend import BUS, ROOT, DiskError, props, protected_paths, require_writes, volume_identity

MIB = 1024 * 1024


def aligned(value):
    return (value + MIB - 1) // MIB * MIB


def signature(objects, table):
    paths = [table] + sorted(p for p in objects if props(objects, p, 'Partition').get('Table') == table)
    return tuple((p, volume_identity(p, props(objects, p, 'Block')),
                  tuple((k, props(objects, p, 'Partition').get(k)) for k in
                        ('UUID', 'Table', 'Offset', 'Size', 'Type', 'IsContainer', 'IsContained')))
                 for p in paths)


@dataclass(frozen=True)
class PartitionPlan:
    table: str
    token: tuple
    generation: int
    scheme: str
    gaps: tuple
    volumes: tuple


def inspect(client, disk):
    objects = client.snapshot()
    block = props(objects, disk.block, 'Block')
    identity=volume_identity(disk.block,block)
    if len(disk.identity)>len(identity): identity+=(getattr(client,'topology_generation',0),)
    if not block or block.get('Size') != disk.size or (disk.identity and identity != disk.identity):
        raise DiskError('The disk changed. Select it again.')
    scheme = props(objects, disk.block, 'PartitionTable').get('Type')
    if scheme not in ('gpt', 'dos'):
        raise DiskError('This disk needs a GPT or MBR partition table.')
    if disk.block in protected_paths(objects) or block.get('ReadOnly'):
        raise DiskError('This disk is read-only or used by the running system.')
    if block.get('CryptoBackingDevice', '/') != '/' or block.get('MDRaid', '/') != '/' or block.get('MDRaidMember', '/') != '/':
        raise DiskError('Partition editing is unavailable for layered storage.')
    volumes = []
    for path in objects:
        part = props(objects, path, 'Partition')
        if part.get('Table') != disk.block:
            continue
        b = props(objects, path, 'Block')
        if part.get('IsContainer') or part.get('IsContained'):
            raise DiskError('Extended and logical MBR partitions cannot be edited here.')
        if props(objects, path, 'Filesystem').get('MountPoints'):
            raise DiskError('Unmount the volumes on this disk before editing partitions.')
        # A locked LUKS sibling still occupies ordinary, inspectable partition
        # geometry. An active cleartext mapping below is rejected separately.
        if b.get('ReadOnly') or b.get('IdUsage') == 'raid' or b.get('MDRaidMember', '/') != '/' or b.get('MDRaid', '/') != '/':
            raise DiskError('This disk contains read-only or layered storage.')
        if any(props(objects, child, 'Block').get('CryptoBackingDevice') == path for child in objects):
            raise DiskError('Lock encrypted volumes before editing their disk.')
        if b.get('IdType') == 'btrfs' and props(objects, path, 'Filesystem.BTRFS').get('NumDevices') != 1:
            raise DiskError('The Btrfs device layout could not be verified.')
        volumes.append((path, int(part.get('Offset', 0)), int(b.get('Size', 0)), b.get('IdType', '')))
    volumes.sort(key=lambda item: item[1])
    end = (disk.size - MIB) // MIB * MIB
    cursor, previous_end, gaps = MIB, 0, []
    for _, offset, size, _ in volumes:
        if offset < previous_end or size<=0 or offset+size>disk.size:
            raise DiskError('The partition layout overlaps. No changes were made.')
        stop = offset // MIB * MIB
        if stop > cursor:
            gaps.append((cursor, stop - cursor))
        previous_end=offset+size
        cursor = aligned(previous_end)
    if end > cursor:
        gaps.append((cursor, end - cursor))
    return PartitionPlan(disk.block, signature(objects, disk.block), getattr(client, 'topology_generation', 0),
                         scheme, tuple(gaps), tuple(volumes))


def validate(client, disk, plan):
    current = inspect(client, disk)
    if current.token != plan.token or current.generation != plan.generation or current.scheme!=plan.scheme:
        raise DiskError('The partition layout changed. Reopen Partitions before continuing.')
    return current


def create(client, disk, plan, offset, size, fmt, name, *, encrypt_passphrase=None):
    require_writes()
    validate(client, disk, plan)
    if size < MIB or size % MIB or offset % MIB or not any(offset >= start and offset + size <= start + length for start, length in plan.gaps):
        raise DiskError('Choose a size within the selected free space.')
    if plan.scheme == 'dos' and len(plan.volumes) >= 4:
        raise DiskError('An MBR disk supports four primary partitions.')
    if fmt not in client.formats():
        raise DiskError('The selected filesystem is unavailable.')
    if encrypt_passphrase is not None and len(encrypt_passphrase) < 8:
        raise DiskError('Use a passphrase with at least eight characters.')
    validate(client, disk, plan)
    options = {'label': client.GLib.Variant('s', name), 'no-discard': client.GLib.Variant('b', True), 'take-ownership': client.GLib.Variant('b', True), 'update-partition-type': client.GLib.Variant('b', True)}
    if encrypt_passphrase is not None:
        options['encrypt.passphrase'] = client.GLib.Variant('s', encrypt_passphrase)
        options['encrypt.type'] = client.GLib.Variant('s', 'luks2')
    with client.hold_loop(plan.table):
        validate(client,disk,plan)
        return client.call(plan.table, 'PartitionTable', 'CreatePartitionAndFormat', '(ttssa{sv}sa{sv})',
                           (offset, size, '', name if plan.scheme == 'gpt' else '', {}, fmt, options))


def delete(client, disk, plan, volume, confirmation):
    require_writes()
    validate(client, disk, plan)
    v = client.current(volume)
    if confirmation != v.name or v.path not in [p[0] for p in plan.volumes]:
        raise DiskError('Type the selected volume name to delete its partition.')
    validate(client, disk, plan)
    return client.call(v.path, 'Partition', 'Delete')


def resize_limits(client, plan, volume):
    rows = list(plan.volumes)
    index = next((i for i, p in enumerate(rows) if p[0] == volume.path), None)
    if index is None:
        raise DiskError('Select a partition to resize.')
    available, flags, _ = client.call(ROOT + '/Manager', 'Manager', 'CanResize', '(s)', (volume.format,))[0]
    if not available or not flags & 6:
        raise DiskError('This filesystem cannot be resized while unmounted.')
    # Never offer raw partition shrink: a successful filesystem resize is mandatory.
    end = rows[index + 1][1] if index + 1 < len(rows) else None
    tail = next((start + length for start, length in plan.gaps if start == aligned(volume.offset + volume.size)), None)
    maximum = ((end or tail or (volume.offset + volume.size)) - volume.offset) // MIB * MIB
    return flags, maximum


def resize(client, disk, plan, volume, size):
    require_writes()
    validate(client, disk, plan)
    v = client.current(volume)
    flags, maximum = resize_limits(client, plan, v)
    shrink = size < v.size
    if size < MIB or size % MIB or size > maximum or size == v.size or not flags & (2 if shrink else 4):
        raise DiskError('Choose a supported size within the adjacent free space.')
    if not client.check(volume):
        raise DiskError('Repair filesystem errors before resizing this partition.')
    validate(client, disk, plan)
    if shrink:
        client.call(v.path, 'Filesystem', 'Resize', '(ta{sv})', (size, {}))
        # Partition still has its original bounds. Abort if any external edit occurred.
        try:
            validate(client, disk, plan)
            client.call(v.path, 'Partition', 'Resize', '(ta{sv})', (size, {}))
        except DiskError as exc:
            raise DiskError('The filesystem was shrunk, but partition resizing did not finish. Refresh before continuing.', str(exc)) from exc
    else:
        client.call(v.path, 'Partition', 'Resize', '(ta{sv})', (size, {}))
        # Re-read after our own geometry change. Retain device/UUID/offset and sibling identity checks.
        fresh = inspect(client, disk)
        objects = client.snapshot()
        old_rows = {row[0]: row for row in plan.token}
        new_rows = {row[0]: row for row in fresh.token}
        if fresh.generation != plan.generation or fresh.scheme!=plan.scheme or old_rows.keys() != new_rows.keys() or any(old_rows[p] != new_rows[p] for p in old_rows if p != v.path):
            raise DiskError('The layout changed after enlarging the partition. Its filesystem has not been enlarged.')
        b = props(objects, v.path, 'Block')
        identity = volume_identity(v.path, b)
        if identity[:4] != v.identity[:4] or identity[5] != v.identity[5] or b.get('Size', 0) < size or props(objects, v.path, 'Partition').get('Offset') != v.offset:
            raise DiskError('The partition changed after resizing. Its filesystem has not been enlarged.')
        try:
            client.call(v.path, 'Filesystem', 'Resize', '(ta{sv})', (size, {}))
        except DiskError as exc:
            raise DiskError('The partition was enlarged, but filesystem expansion did not finish. Check the volume before continuing.', str(exc)) from exc
