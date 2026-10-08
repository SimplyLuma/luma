# SPDX-License-Identifier: Apache-2.0
"""D-Bus activated, unprivileged first-launch data migration owner."""
import configparser
from itertools import islice
import os
from pathlib import Path
import re
import stat
import threading
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
from .app_data_migration import PATHS, MigrationError, migrate
from .native_app_roles import APPS

BUS = 'org.projectluma.AppData1'
OBJECT = '/org/projectluma/AppData1'
XML = '''<node><interface name="org.projectluma.AppData1">
<method name="Prepare"><arg name="result" type="s" direction="out"/></method>
<method name="Cancel"/>
</interface></node>'''

def _native_owner(connection, app):
    names = {
        'com.rhyme.viola': ('org.projectluma.Viola.NativeIntegration',),
        'org.projectluma.Grid': ('org.projectluma.Grid', 'io.luma.Grid'),
        'org.projectluma.Stage': ('org.projectluma.Stage', 'io.luma.Stage'),
    }.get(app, (app,))
    owners = []
    for name in names:
        try:
            owners.append(connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (name,)),
                GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0])
        except GLib.Error as error:
            if (Gio.DBusError.is_remote_error(error) and
                    Gio.DBusError.get_remote_error(error) == 'org.freedesktop.DBus.Error.NameHasNoOwner'):
                continue
            raise MigrationError('The native application state could not be checked. Your data is preserved.') from error
    # A signed sandbox may already own its canonical name. Its legacy alias
    # belongs to the native namespace and must still block the initial handoff.
    return owners[-1] if owners else ''

def _start(pid):
    # Field22, following the parenthesized command (which may contain spaces).
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def _process_identity(pid, uid):
    """Read live process identity without accepting a client-selected PID."""
    if not isinstance(pid, int) or pid <= 0:
        raise MigrationError('The application process is unavailable.')
    base = Path(f'/proc/{pid}')
    fields = (base / 'stat').read_text().rsplit(')', 1)[1].split()
    status = (base / 'status').read_text()
    uids = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
    if fields[0] in {'Z', 'X'} or len(uids) != 4 or any(int(value) != uid for value in uids):
        raise MigrationError('The application process has a different user or has exited.')
    return int(fields[1]), fields[19]


def _trusted_dbus_proxy(pid):
    """Accept the maintained host proxy executable, never a caller's program."""
    executable = Path(f'/proc/{pid}/exe').stat()
    for name in ('/usr/bin/xdg-dbus-proxy', '/usr/libexec/flatpak-dbus-proxy'):
        path = Path(name)
        try:
            trusted = path.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISREG(trusted.st_mode) and trusted.st_uid == 0
                and not trusted.st_mode & 0o022
                and (trusted.st_dev, trusted.st_ino) ==
                    (executable.st_dev, executable.st_ino)):
            return
    raise MigrationError('The application connection has no maintained Flatpak proxy.')


def _pipe_descriptors(pid, access):
    """Read actual kernel pipe endpoints, including their descriptor direction."""
    base = Path(f'/proc/{pid}')
    descriptors = list(islice((base / 'fd').iterdir(), 4097))
    if len(descriptors) > 4096:
        raise MigrationError('The application has too many process descriptors.')
    pipes = {}
    for descriptor in descriptors:
        try:
            status = descriptor.stat()
            if not stat.S_ISFIFO(status.st_mode):
                continue
            fields = dict(line.split(':', 1) for line in
                          (base / 'fdinfo' / descriptor.name).read_text().splitlines()
                          if ':' in line)
            if int(fields['flags'].strip(), 8) & os.O_ACCMODE != access:
                continue
            # FD replacement between stat and fdinfo must not supply authority.
            after = descriptor.stat()
            if (status.st_dev, status.st_ino, status.st_mode) != (
                    after.st_dev, after.st_ino, after.st_mode):
                raise MigrationError('The application pipe changed.')
            pipes[int(descriptor.name)] = (status.st_dev, status.st_ino)
        except FileNotFoundError:
            continue  # an unrelated closing descriptor cannot be selected
    return pipes


def _proxy_instance_pipe(proxy, child, uid):
    """Bind Flatpak's proxy to the sandbox's supported sync-pipe endpoint.

    flatpak-run-dbus.c passes the write end to xdg-dbus-proxy; the actual
    sandbox bwrap retains the read end. Descriptor numbers and argv are not
    identities. Missing or ambiguous kernel linkage fails closed.
    """
    identities = {pid: _process_identity(pid, uid)[1] for pid in (proxy, child)}
    readers = _pipe_descriptors(child, os.O_RDONLY)
    writers = _pipe_descriptors(proxy, os.O_WRONLY)
    def index(descriptors):
        endpoints = {}
        for descriptor, endpoint in descriptors.items():
            endpoints.setdefault(endpoint, []).append(descriptor)
        return endpoints
    read_ends, write_ends = index(readers), index(writers)
    shared = read_ends.keys() & write_ends.keys()
    if len(shared) != 1:
        raise MigrationError('The application proxy has no unique sandbox lifecycle pipe.')
    endpoint = next(iter(shared))
    if len(read_ends[endpoint]) != 1 or len(write_ends[endpoint]) != 1:
        raise MigrationError('The application lifecycle pipe has ambiguous descriptors.')
    read_fd, write_fd = read_ends[endpoint][0], write_ends[endpoint][0]
    device, inode = endpoint
    if (_pipe_descriptors(child, os.O_RDONLY).get(read_fd) != (device, inode)
            or _pipe_descriptors(proxy, os.O_WRONLY).get(write_fd) != (device, inode)
            or any(_process_identity(pid, uid)[1] != start
                   for pid, start in identities.items())):
        raise MigrationError('The application lifecycle pipe changed.')
    return read_fd, write_fd, device, inode


def _instance_child(info, app, sender_pid, uid, text):
    """Map Flatpak's D-Bus proxy to its live application's mount namespace.

    The proxy has /.flatpak-info but intentionally does not mount /app.
    Flatpak.Instance supplies the outer babysitter and sandbox child PIDs;
    the child must belong to the live outer process. Flatpak daemonizes its
    separately sandboxed proxy, so bind its maintained executable and kernel
    lifecycle pipe to the child as well as the live metadata. Deployment trust
    is still checked separately against the child's actual signed /app inode.
    """
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    instance_id = info.get('Instance', 'instance-id', fallback='')
    matches = [value for value in Flatpak.Instance.get_all()
               if value.get_id() == instance_id]
    if len(matches) != 1:
        raise MigrationError('The application is not a unique current Flatpak instance.')
    instance = matches[0]
    if (not instance.is_running() or instance.get_app() != app
            or instance.get_commit() != info.get('Instance', 'app-commit', fallback='')
            or instance.get_arch() != info.get('Instance', 'arch', fallback='')
            or instance.get_branch() != info.get('Instance', 'branch', fallback='')):
        raise MigrationError('The live application instance differs from its sandbox identity.')
    outer, child = instance.get_pid(), instance.get_child_pid()
    starts = {}
    def identity(pid):
        parent, start = _process_identity(pid, uid)
        if pid in starts and starts[pid] != start:
            raise MigrationError('The application process changed.')
        starts[pid] = start
        return parent
    identity(outer)
    def descendant(pid):
        seen = set()
        for _ in range(128):
            if pid in seen or pid <= 0:
                break
            seen.add(pid)
            parent = identity(pid)
            if pid == outer:
                return
            pid = parent
        raise MigrationError('The application connection belongs to a different process tree.')
    descendant(child)
    identity(sender_pid)
    # Actual Flatpak's proxy is reparented to PID1. A parent-tree assertion here
    # would reject normal production clients; never substitute a missing /app
    # check with an arbitrary sender-selected filesystem path.
    _trusted_dbus_proxy(sender_pid)
    _proxy_instance_pipe(sender_pid, child, uid)
    with Path(f'/proc/{child}/root/.flatpak-info').open() as stream:
        child_text = stream.read(65537)
    if child_text != text:
        raise MigrationError('The application process has different sandbox metadata.')
    for pid, start in starts.items():
        if _process_identity(pid, uid)[1] != start:
            raise MigrationError('The application process changed.')
    return child, starts

def authenticate_deployment(info, app, pid):
    """Bind a live instance to an official installed signed application.

    Flatpak's Instance metadata records build mode and --app-path overrides.
    Matching an app ID alone does not establish the provenance of its bytes.
    See flatpak-metadata(5), Instance app-commit/app-path/original-app-path.
    """
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    from .native_app_roles import installed, verify_deployed_commit
    from .depot_flatpak import BRANCHES
    for flag in ('build', 'devel'):
        if info.getboolean('Instance', flag, fallback=False):
            raise MigrationError('Development sandboxes cannot access application services.')
    if info.has_option('Instance', 'original-app-path'):
        raise MigrationError('Altered application deployments cannot access application services.')
    from .app_extensions import OFFICE_APPS, verify_application_extensions
    if app not in OFFICE_APPS and info.get('Instance', 'app-extensions', fallback=''):
        raise MigrationError('Altered application deployments cannot access application services.')
    commit = info.get('Instance', 'app-commit', fallback='')
    path = info.get('Instance', 'app-path', fallback='')
    branch = info.get('Instance', 'branch', fallback='')
    arch = info.get('Instance', 'arch', fallback='')
    if not re.fullmatch('[a-f0-9]{64}', commit) or not Path(path).is_absolute():
        raise MigrationError('The application has no installed signed deployment.')
    candidates = []
    for installation in (Flatpak.Installation.new_system(None), Flatpak.Installation.new_user(None)):
        refs = [ref for ref in installation.list_installed_refs(None)
                if ref.get_name() == app and ref.format_ref().startswith('app/')]
        if not refs:
            continue
        try:
            ref = installed(installation, app)
        except Exception:
            continue  # an unrelated/unsigned shadow never supplies authority
        expected = Path(ref.get_deploy_dir()) / 'files'
        if (ref.get_commit() == commit and ref.get_branch() == branch
                and ref.get_arch() == arch and str(expected) == path):
            candidates.append((expected, installation))
        elif ref.get_arch() == arch and branch in BRANCHES:
            # Flatpak 1.18 keeps a locked running deployment after an update:
            # common/flatpak-dir.c flatpak_dir_get_if_deployed() searches both
            # app/ID/ARCH/BRANCH/COMMIT and .removed/ID-COMMIT. The live metadata
            # retains its original app-path even when the host directory moves.
            # This preserves a signed running A's broker access while B is
            # installed; it does not authorize a caller-selected directory.
            base = Path(installation.get_path().get_path())
            original = base / 'app' / app / arch / branch / commit / 'files'
            if path != str(original):
                continue
            exact_ref = f'app/{app}/{arch}/{branch}'
            try:
                verify_deployed_commit(installation, ref, commit=commit, exact_ref=exact_ref)
            except Exception:
                continue
            mounted = Path(f'/proc/{pid}/root/app').stat()
            for retained in (original, base / '.removed' / f'{app}-{commit}' / 'files'):
                try:
                    status = retained.lstat()
                except FileNotFoundError:
                    continue
                if (stat.S_ISDIR(status.st_mode) and
                        (status.st_dev, status.st_ino) == (mounted.st_dev, mounted.st_ino)):
                    candidates.append((retained, installation))
    if len(candidates) != 1:
        raise MigrationError('The application is not a trusted installed or retained deployment.')
    expected, installation = candidates[0]
    # Every deployed host ancestor must be non-writable by another user and
    # free of symlink aliases. Match the mounted app directory itself as well.
    for member in (expected, *expected.parents):
        status = member.lstat()
        if (not stat.S_ISDIR(status.st_mode) or status.st_uid not in (0, os.getuid())
                or status.st_mode & 0o022):
            raise MigrationError('The installed application directory is unsafe.')
    actual = Path(f'/proc/{pid}/root/app').stat()
    deployed = expected.stat()
    if (actual.st_dev, actual.st_ino) != (deployed.st_dev, deployed.st_ino):
        raise MigrationError('The sandbox application mount differs from its signed deployment.')
    verify_application_extensions(info, app, pid, installation, commit)

def authenticate(connection, sender):
    def credential(method):
        return connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', method, GLib.Variant('(s)', (sender,)),
            GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    uid, pid = credential('GetConnectionUnixUser'), credential('GetConnectionUnixProcessID')
    if uid == 0 or uid != os.getuid():
        raise MigrationError('The application is not running as this user.')
    before = _start(pid)
    info = configparser.ConfigParser(interpolation=None, strict=True)
    path = Path(f'/proc/{pid}/root/.flatpak-info')
    with path.open() as stream:
        text = stream.read(65537)
    if len(text) > 65536:
        raise MigrationError('The sandbox identity is invalid.')
    info.read_string(text)
    app = info.get('Application', 'name', fallback='')
    instance = info.get('Instance', 'instance-id', fallback='')
    if app not in APPS or not instance or not instance.isdecimal():
        raise MigrationError('This application has no approved service identity.')
    runtime = Path(f'/run/user/{uid}/.flatpak/{instance}/info')
    if runtime.is_symlink() or runtime.read_text() != text:
        raise MigrationError('The sandbox is not a current Flatpak instance.')
    child, starts = _instance_child(info, app, pid, uid, text)
    lifecycle = _proxy_instance_pipe(pid, child, uid)
    authenticate_deployment(info, app, child)
    if _proxy_instance_pipe(pid, child, uid) != lifecycle:
        raise MigrationError('The application lifecycle pipe changed during verification.')
    for process, start in starts.items():
        if _process_identity(process, uid)[1] != start:
            raise MigrationError('The application process changed.')
    if _start(pid) != before:
        raise MigrationError('The application process changed.')
    return app

class Broker:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.bus = None
        self.active = False
        self.sender = None
        self.cancelled = None
        self.idle = GLib.timeout_add_seconds(60, self._idle)
        self.owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS, Gio.BusNameOwnerFlags.NONE,
                                    self._acquired, None, lambda *_: self.loop.quit())

    def _idle(self):
        if self.active: return GLib.SOURCE_CONTINUE
        self.loop.quit(); return GLib.SOURCE_REMOVE

    def _acquired(self, connection, *_):
        self.bus = connection
        connection.register_object(OBJECT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self._call, None, None)

    def _call(self, connection, sender, _path, _interface, method, _args, invocation):
        if method == 'Cancel':
            if self.active and sender == self.sender:
                self.cancelled.set()
                invocation.return_value(GLib.Variant('()', ()))
            else:
                invocation.return_dbus_error(BUS + '.Refused', 'No migration belongs to this connection.')
            return
        if method != 'Prepare':
            invocation.return_dbus_error(BUS + '.Unsupported', 'Unsupported operation.'); return
        if self.active:
            invocation.return_dbus_error(BUS + '.Busy', 'Another application data migration is running.'); return
        try:
            app = authenticate(connection, sender)
            if app not in PATHS:
                raise MigrationError('This application has no native profile migration.')
            # A live native app must close before its profile is handed off.
            complete = Path.home() / '.local/state/luma/app-migration' / (app + '.json')
            if not complete.exists():
                owner = _native_owner(connection, app)
                if owner and owner != sender:
                    raise MigrationError('Close the native application before migrating its data.')
        except Exception as error:
            invocation.return_dbus_error(BUS + '.Refused', str(error)); return
        self.active = True
        self.sender = sender
        self.cancelled = threading.Event()
        cancellation = self.cancelled
        watched = Gio.bus_watch_name_on_connection(connection, sender, Gio.BusNameWatcherFlags.NONE,
            lambda *_: None, lambda *_: cancellation.set())
        def work():
            try:
                migrate(app, cancelled=cancellation.is_set)
                GLib.idle_add(finish, '')
            except Exception as error:
                GLib.idle_add(finish, str(error))
        def finish(error):
            self.active = False
            self.sender = None
            Gio.bus_unwatch_name(watched)
            if error: invocation.return_dbus_error(BUS + '.Refused', error)
            else: invocation.return_value(GLib.Variant('(s)', ('ready',)))
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=False, name='luma-native-data-migration').start()

    def run(self):
        self.loop.run()
        Gio.bus_unown_name(self.owner)

if __name__ == '__main__': Broker().run()
