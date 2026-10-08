# SPDX-License-Identifier: Apache-2.0
"""Desktop identity and existing system services; no privileged resident helper."""
from pathlib import Path
import os
import signal
import time
from gi.repository import Gio, GLib
from .model import Identity, application_unit, identity_candidates, stat_record


class DesktopCatalog:
    def __init__(self):
        self.refresh()
        self._shell_pids = {}
        self._shell_dirty = True
        self._shell_retry = 0
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._shell_signal = self._bus.signal_subscribe(
            'org.gnome.Shell.Introspect', 'org.gnome.Shell.Introspect', None,
            '/org/gnome/Shell/Introspect', None, Gio.DBusSignalFlags.NONE,
            self._shell_changed)
        self._owner_signal = self._bus.signal_subscribe(
            'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
            '/org/freedesktop/DBus', 'org.gnome.Shell.Introspect',
            Gio.DBusSignalFlags.NONE, self._shell_changed)

    def refresh(self):
        entries, hidden_entries = {}, {}
        for app in Gio.AppInfo.get_all():
            if not app.get_id():
                continue
            icon = app.get_icon()
            identity = Identity(app.get_id(), app.get_display_name(), icon.to_string() if icon else 'application-x-executable-symbolic', ' '.join([app.get_generic_name() or '', *(app.get_keywords() or [])]))
            target = hidden_entries if app.get_nodisplay() else entries
            target[app.get_id().removesuffix('.desktop')] = identity
        self.entries, self.hidden_entries = entries, hidden_entries

    def _shell_changed(self, *args):
        self._shell_dirty = True

    def refresh_running(self):
        # Event driven in the shell; a bounded retry handles shell replacement.
        if not self._shell_dirty and time.monotonic() < self._shell_retry:
            return
        self._shell_dirty = False
        self._shell_retry = time.monotonic() + 5
        mapping = {}
        try:
            apps = self._bus.call_sync(
                'org.gnome.Shell.Introspect', '/org/gnome/Shell/Introspect',
                'org.gnome.Shell.Introspect', 'GetMonitorApplications', None,
                GLib.VariantType.new('(a{sau})'), Gio.DBusCallFlags.NO_AUTO_START,
                1000, None).unpack()[0]
            self._shell_retry = float("inf")
            for app_id, pids in apps.items():
                key = app_id.removesuffix('.desktop')
                identity = self.entries.get(key) or self.hidden_entries.get(key)
                if identity is None:
                    continue
                for pid in pids:
                    try:
                        root = Path('/proc') / str(pid)
                        if root.stat().st_uid != os.getuid():
                            continue
                        start = stat_record((root / 'stat').read_text())['start']
                        mapping[pid] = (start, identity)
                    except (OSError, ValueError):
                        continue
        except GLib.Error:
            pass
        self._shell_pids = mapping

    def close(self):
        self._bus.signal_unsubscribe(self._shell_signal)
        self._bus.signal_unsubscribe(self._owner_signal)

    def resolve(self, cgroup, pid, command):
        mapped = self._shell_pids.get(pid)
        if mapped:
            try:
                start = stat_record((Path('/proc') / str(pid) / 'stat').read_text())['start']
                if start == mapped[0]:
                    return mapped[1]
            except (OSError, ValueError):
                pass
        for name in identity_candidates(cgroup):
            if name in self.entries:
                return self.entries[name]
        return None


def _same_process(process):
    root = Path('/proc') / str(process.pid)
    if stat_record((root / 'stat').read_text())['start'] != process.start:
        raise ProcessLookupError('This process has exited; refresh the selection.')
    groups = (root / 'cgroup').read_text().splitlines()
    if process.cgroup and not any(line.endswith(':' + process.cgroup) for line in groups):
        raise ProcessLookupError('The process moved to another group; refresh the selection.')


class ForceQuitError(OSError):
    def __init__(self,sent,total,cause):
        self.sent,self.total=sent,total
        super().__init__(f'Sent force quit to {sent} of {total} processes; {cause}')


def force_quit_processes(processes):
    """Signal only the exact, same-user processes selected before confirmation.

    Revalidate after pidfd_open so a reused PID cannot redirect SIGKILL. Handle
    one pidfd at a time: a large application can exceed the default 1024-fd
    soft limit, and its members may exit while the request is in progress.
    """
    processes=tuple(processes)
    if Path('/.flatpak-info').exists():
        from .host_sampler import host_call
        if not 1 <= len(processes) <= 1024: raise ValueError('Invalid process selection.')
        return host_call('ForceQuit', GLib.Variant('(a(ut))', ([(p.pid,p.start) for p in processes],)), '(uu)')
    if not processes:
        raise ValueError('No processes were selected.')
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        raise OSError('This system cannot safely force quit processes.')
    if len({p.pid for p in processes})!=len(processes):
        raise ValueError('The selection contains a process twice.')
    for process in processes:
        if process.pid<=2 or process.pid==os.getpid() or process.uid!=os.getuid() or not process.cgroup or process.start<=0:
            raise PermissionError('This process cannot be force quit from Monitor.')
    sent=0
    for process in processes:
        try:descriptor=os.pidfd_open(process.pid,0)
        except ProcessLookupError:continue
        except OSError as error:raise ForceQuitError(sent,len(processes),error) from error
        try:
            root=Path('/proc')/str(process.pid)
            if root.stat().st_uid!=process.uid:
                raise ProcessLookupError('The process owner changed; refresh the selection.')
            _same_process(process)
            signal.pidfd_send_signal(descriptor,signal.SIGKILL,None,0)
            sent+=1
        except (ProcessLookupError,FileNotFoundError,ValueError):
            # It exited, moved, or its PID was reused. The pidfd pins the old
            # process, and this target is skipped rather than reinterpreted.
            pass
        except OSError as error:raise ForceQuitError(sent,len(processes),error) from error
        finally:os.close(descriptor)
    return sent,len(processes)


def force_quit_row(row):
    if row.get('background') or not row.get('members'):
        raise ValueError('This app cannot be force quit.')
    return force_quit_processes(row['members'])


def quit_row(row):
    """Request an exported quit action, preserving the application's save prompt.

    No signal fallback until the shell window-close path is available: terminating
    an editor here would discard the very prompt this action promises to retain.
    """
    if row.get('background') or not row.get('members'):
        return False
    if Path('/.flatpak-info').exists():
        from .host_sampler import host_call
        return host_call('QuitApp', GLib.Variant('(s)', (row['id'],)), '(b)')[0]
    for process in row['members']:
        _same_process(process)
        if process.uid != os.getuid():
            return False
    desktop_id = row['members'][0].identity.id
    if not desktop_id.endswith('.desktop'):
        desktop_id += '.desktop'
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        requested = bus.call_sync(
            'org.gnome.Shell.Introspect', '/org/gnome/Shell/Introspect',
            'org.gnome.Shell.Introspect', 'RequestMonitorQuit',
            GLib.Variant('(sau)', (desktop_id, [p.pid for p in row['members']])),
            GLib.VariantType.new('(b)'), Gio.DBusCallFlags.NO_AUTO_START,
            1500, None).unpack()[0]
        return requested
    except GLib.Error:
        # Older shells retain the existing exported-action-only fallback.
        pass
    app_id = row['members'][0].identity.id.removesuffix('.desktop')
    if not Gio.dbus_is_name(app_id):
        return False
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    path = '/' + app_id.replace('.', '/').replace('-', '_')
    try:
        actions = bus.call_sync(app_id, path, 'org.gtk.Actions', 'List', None,
                               GLib.VariantType.new('(as)'), Gio.DBusCallFlags.NO_AUTO_START,
                               1500, None).unpack()[0]
        if 'quit' not in actions:
            return False
        bus.call_sync(app_id, path, 'org.freedesktop.Application', 'ActivateAction',
                      GLib.Variant('(sava{sv})', ('quit', [], {})), None,
                      Gio.DBusCallFlags.NO_AUTO_START, 1500, None)
        return True
    except GLib.Error:
        return False


def battery_info():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        path = bus.call_sync('org.freedesktop.UPower', '/org/freedesktop/UPower',
                            'org.freedesktop.UPower', 'GetDisplayDevice', None,
                            GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]
        data = bus.call_sync('org.freedesktop.UPower', path, 'org.freedesktop.DBus.Properties',
                            'GetAll', GLib.Variant('(s)', ('org.freedesktop.UPower.Device',)),
                            GLib.VariantType.new('(a{sv})'), Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]
        if not data.get('IsPresent') or data.get('Type') != 2:
            return None
        return {'percentage': data.get('Percentage'), 'remaining': data.get('TimeToEmpty') or None,
                'charging': data.get('State') == 1, 'plugged': data.get('State') in (4, 5),
                'draw': data.get('EnergyRate'), 'health': data.get('Capacity'), 'cycles': data.get('ChargeCycles')}
    except GLib.Error:
        return None


def performance_profile():
    """Read the active system profile without changing it."""
    try:
        bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
        result=bus.call_sync('net.hadess.PowerProfiles','/net/hadess/PowerProfiles',
                             'org.freedesktop.DBus.Properties','Get',
                             GLib.Variant('(ss)',('net.hadess.PowerProfiles','ActiveProfile')),
                             None,Gio.DBusCallFlags.NONE,1500,None).unpack()[0]
        if isinstance(result,GLib.Variant):result=result.unpack()
        return {'power-saver':'saver','balanced':'bal','performance':'perf'}.get(result)
    except GLib.Error:return None


def inhibitors():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        rows = bus.call_sync('org.freedesktop.login1', '/org/freedesktop/login1',
                            'org.freedesktop.login1.Manager', 'ListInhibitors', None,
                            None, Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]
        return {int(pid) for what, who, why, mode, uid, pid in rows if set(what.split(':')) & {'sleep', 'idle'}}
    except GLib.Error:
        return None
