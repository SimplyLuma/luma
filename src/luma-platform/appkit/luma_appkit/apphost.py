# SPDX-License-Identifier: Apache-2.0
"""Start Luma applications from a pre-warmed process instead of a cold one.

A fresh Python GTK application spends most of its start before it can draw:
the interpreter starts, imports GObject introspection, GTK, libadwaita and the
kit, and the dynamic linker relocates the GL driver. The app host does that
work once per session and then forks for each launch, so a launch starts at
the application's own code.

An application's launcher calls :func:`handoff` before any heavy import. When
a host is listening on the session socket, the host forks, the fork becomes
the application (same argv, environment, working directory, standard streams
and, where it can, the launcher's cgroup), and the launcher stays as a thin
proxy that forwards signals and exits with the application's status. When no
host is running, or it refuses, :func:`handoff` returns and the launcher
starts the application normally, so the host is only ever an accelerator.

The parent imports GTK without a display and forks only while single-threaded.
One spare child initializes GTK, styles and a graphics context without mapping
any window, then sleeps on its request socket. It never forks. A matching
launch consumes the spare; a replacement is prepared two seconds later. Apps
with a different display/import environment use an ordinary fresh fork, and
an incompatible Python/linker environment always executes normally. No app is
frozen, no application data is preloaded, and no work is polled while idle.

Run the host with ``python3 -sP -m luma_appkit.apphost``; the
``luma-app-host.service`` user unit does that for the session.
"""

from __future__ import annotations

import array
import json
import os
import select
import signal
import socket
import struct
import sys

PROTOCOL = 2
_MAX_MESSAGE = 1 << 20
# Read once, when a process starts: the dynamic linker's search path and the
# interpreter's configuration. A fork keeps the host's, so a launch that asks
# for different ones starts the normal way.
EXEC_ENVIRONMENT = (
    "LD_LIBRARY_PATH", "LD_PRELOAD", "LD_BIND_NOW", "GLIBC_TUNABLES",
    "PYTHONHOME", "PYTHONPATH", "PYTHONOPTIMIZE", "PYTHONNOUSERSITE", "PYTHONSAFEPATH",
    "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE", "PYTHONWARNINGS", "PYTHONDEVMODE",
    "PYTHONMALLOC", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONHASHSEED", "PYTHONFAULTHANDLER",
    "GI_TYPELIB_PATH", "G_RESOURCE_OVERLAYS", "GTK_PATH", "GIO_EXTRA_MODULES", "GIO_USE_VFS",
    "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "LD_AUDIT", "GCONV_PATH",
    "FONTCONFIG_FILE", "FONTCONFIG_PATH", "GSETTINGS_SCHEMA_DIR", "GSETTINGS_BACKEND",
)
# Imported by the host before it serves. Each is optional: one missing on a
# given image only makes that part of a launch cold.
PRELOAD = (
    "luma_appkit.widgets",
    "luma_appkit.lumaui",
    "luma_appkit.action_center",
    "asyncio",
    "sqlite3",
    "json",
    "urllib.request",
    "html.parser",
    "zoneinfo",
)
# Shared libraries the GL renderer loads at the first frame. Loading them in
# the host pays their relocation once; every fork inherits the mapping.
PRELOAD_LIBRARIES = (
    "libEGL.so.1",
    "libGLESv2.so.2",
    "libEGL_mesa.so.0",
    "libgallium-*.so",
)

_in_child = False
_spare_original_oom = None
_HOST_INTERPRETER: dict | None = None


def _interpreter() -> dict:
    """What a fork cannot change about the interpreter it inherits."""
    flags = sys.flags
    return {
        "executable": os.path.realpath(sys.executable),
        "flags": [flags.isolated, flags.no_user_site, flags.optimize, flags.safe_path,
                  flags.dont_write_bytecode, flags.ignore_environment, flags.utf8_mode],
        "env": {name: os.environ.get(name) for name in EXEC_ENVIRONMENT},
    }


def socket_path() -> str | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(runtime, "luma", "app-host.sock") if runtime else None


def _send(connection: socket.socket, message: dict, fds: list[int] | None = None) -> None:
    data = json.dumps(message).encode()
    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))] if fds else []
    connection.sendmsg([data], ancillary)


def _receive(connection: socket.socket, max_fds: int = 0) -> tuple[dict | None, list[int]]:
    space = socket.CMSG_SPACE(max_fds * struct.calcsize("i")) if max_fds else 0
    data, ancillary, _flags, _address = connection.recvmsg(_MAX_MESSAGE, space)
    fds = array.array("i")
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            fds.frombytes(payload[: len(payload) - (len(payload) % fds.itemsize)])
    try:
        if _flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise ValueError("truncated launch request")
        if not data:
            return None, list(fds)
        return json.loads(data), list(fds)
    except (ValueError, UnicodeError):
        for fd in fds:
            os.close(fd)
        raise



def _own_cgroup() -> str | None:
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("0::"):
                    return line[3:].strip()
    except OSError:
        pass
    return None


def handoff(argv: list[str] | None = None) -> None:
    """Run this launch in the session's app host, if one is running.

    Returns only when the launch should continue in this process. Otherwise
    this process waits for the application and exits with its status.
    """
    if _in_child or os.environ.get("LUMA_APP_HOST") == "0" or os.path.exists("/.flatpak-info"):
        return
    path = socket_path()
    if path is None or not os.path.exists(path):
        return
    argv = list(sys.argv if argv is None else argv)
    if not argv or not argv[0]:
        return
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
    cwd = None
    try:
        # The working directory travels as a descriptor, so the application
        # inherits it exactly, even one its user could not open by path.
        cwd = os.open(".", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        connection.settimeout(2.0)
        connection.connect(path)
        _send(connection, {
            "protocol": PROTOCOL,
            "argv": [os.path.abspath(argv[0]), *argv[1:]],
            "env": dict(os.environ),
            "interpreter": _interpreter(),
            "cgroup": _own_cgroup(),
        }, [0, 1, 2, cwd])
        reply, fds = _receive(connection, max_fds=1)
    except (OSError, ValueError):
        connection.close()
        return
    finally:
        if cwd is not None:
            os.close(cwd)
    if not reply or "pid" not in reply or len(fds) != 1:
        for fd in fds:
            os.close(fd)
        connection.close()
        return
    pid, pidfd = int(reply["pid"]), fds[0]
    try:
        _send(connection, {"start": True})
    except OSError:
        os.close(pidfd)
        connection.close()
        return

    def forward(number, _frame):
        try:
            signal.pidfd_send_signal(pidfd, number)
        except ProcessLookupError:
            pass

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT, signal.SIGUSR1, signal.SIGUSR2):
        signal.signal(number, forward)

    # The host reports the app's status. If the host goes away first, the app
    # keeps running and this process waits for it through its pidfd, so the
    # launcher's unit or scope lives exactly as long as the app.
    connection.settimeout(None)
    poll = select.poll()
    poll.register(connection, select.POLLIN)
    poll.register(pidfd, select.POLLIN)
    host, running, status = True, True, 0
    while host or running:
        # Once the app has exited, its status follows at once or not at all.
        events = dict(poll.poll(None if running else 1000))
        if not events:
            break
        if pidfd in events:
            running = False
            poll.unregister(pidfd)
        if connection.fileno() in events:
            try:
                message, _fds = _receive(connection)
            except (OSError, ValueError):
                message = None
            if message is not None and "exit" in message:
                status = int(message["exit"])
                break
            if message is None:
                host = False
                poll.unregister(connection)
    os._exit(status)


# Host side ------------------------------------------------------------------


def _thread_count() -> int:
    try:
        return len(os.listdir("/proc/self/task"))
    except OSError:
        return 0


def _warm(log) -> None:
    import ctypes
    import glob
    import importlib

    # PyGObject initializes GTK when it is imported, which connects to the
    # display and the session bus and starts GLib and Pango threads that a
    # fork would not have. The host imports GTK uninitialized; each fork
    # initializes its own. Older PyGObject cannot skip this, and the thread
    # check in serve() then refuses to fork.
    display = {name: os.environ.pop(name) for name in ("WAYLAND_DISPLAY", "DISPLAY") if name in os.environ}
    try:
        import gi
        if hasattr(gi, "disable_legacy_autoinit"):
            gi.disable_legacy_autoinit()
        for namespace, version in (("Gtk", "4.0"), ("Gdk", "4.0"), ("Adw", "1"), ("Graphene", "1.0"), ("Pango", "1.0")):
            try:
                gi.require_version(namespace, version)
            except ValueError:
                pass
        from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: F401
        for namespace in ("LumaUI", "LumaAppearance", "LumaSemantics"):
            try:
                gi.require_version(namespace, "1")
                importlib.import_module(f"gi.repository.{namespace}")
            except (ImportError, ValueError):
                pass
    finally:
        os.environ.update(display)
    modules = list(PRELOAD)
    manifests = []
    for directory in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if os.path.isabs(directory):
            manifests.extend(glob.glob(glob.escape(directory) + "/luma-app-host/preload.d/*.json"))
    for path in sorted(manifests):
        try:
            with open(path, encoding="utf-8") as stream:
                names = json.load(stream)
            if isinstance(names, list) and all(isinstance(n, str) and n.isascii() and
                    all(part.isidentifier() for part in n.split(".")) for n in names):
                modules.extend(names)
        except (OSError, ValueError) as error:
            log(f"preload manifest {path}: {error}")
    for name in dict.fromkeys(modules):
        try:
            importlib.import_module(name)
        except Exception as error:  # noqa: BLE001 - a missing preload only costs speed
            log(f"preload {name}: {error}")
    # The same directories the dynamic linker will search for the app.
    directories = [d for d in os.environ.get("LD_LIBRARY_PATH", "").split(":") if d] + ["/usr/lib64", "/usr/lib"]
    for pattern in PRELOAD_LIBRARIES:
        found = next((sorted(glob.glob(os.path.join(d, pattern))) for d in directories if glob.glob(os.path.join(d, pattern))), [])
        if found:
            try:
                ctypes.CDLL(found[0])
            except OSError as error:
                log(f"preload {found[0]}: {error}")
    # Fontconfig's configuration and caches, which Pango would otherwise load
    # in every application. FcInit starts no threads.
    try:
        ctypes.CDLL("libfontconfig.so.1").FcInit()
    except (OSError, AttributeError) as error:
        log(f"preload fontconfig: {error}")


def _become(request: dict, fds: list[int], host: list) -> None:
    """In the fork: turn into the requested application and never return."""
    global _in_child
    _in_child = True
    if _spare_original_oom is not None:
        try:
            with open("/proc/self/oom_score_adj", "w", encoding="ascii") as stream:
                stream.write(str(_spare_original_oom))
        except OSError:
            pass
    status = 1
    try:
        # The host's sockets, selector and pidfds, closed through their owners
        # so no stale object can later close a descriptor the app reuses.
        for item in host:
            if isinstance(item, int):
                os.close(item)
            else:
                item.close()
        os.setsid()
        for target, fd in enumerate(fds[:3]):
            os.dup2(fd, target)
        os.fchdir(fds[3])
        for fd in fds:
            if fd > 2:
                os.close(fd)
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(number, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        cgroup = request.get("cgroup")
        if cgroup:
            _join_launch_scope(cgroup)
        os.environ.clear()
        os.environ.update(request["env"])
        argv = list(request["argv"])
        sys.argv = argv
        _set_process_name(os.path.basename(argv[0]))
        import locale
        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error:
            pass
        if "gi.repository.Gtk" in sys.modules:
            from gi.repository import GLib, Gtk
            GLib.set_prgname(os.path.basename(argv[0]))
            initialized = Gtk.init_check()
            overrides = sys.modules.get("gi.overrides.Gtk")
            if overrides is not None:
                overrides.initialized = initialized
            # GIO may finish creating the launcher's systemd scope just after
            # our very fast native client sends its request. Follow that one
            # transition without delaying the first frame or polling at idle.
            attempts = 0
            def adopt_scope():
                nonlocal attempts
                attempts += 1
                try:
                    with open(f"/proc/{request['_launcher_pid']}/cgroup", encoding="ascii") as stream:
                        target = next(line[3:].strip() for line in stream if line.startswith("0::"))
                    if _join_launch_scope(target):
                        return False
                except (OSError, StopIteration, KeyError):
                    return False
                return attempts < 10
            GLib.timeout_add(100, adopt_scope)
        import runpy
        try:
            runpy.run_path(argv[0], run_name="__main__")
            status = 0
        except SystemExit as exit_:
            code = exit_.code
            if code is None:
                status = 0
            elif isinstance(code, int):
                status = code
            else:
                print(code, file=sys.stderr)
                status = 1
        except BaseException:  # noqa: BLE001 - report like the interpreter would
            import traceback
            traceback.print_exc()
            status = 1
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
    except BaseException:  # noqa: BLE001 - setup failed before the app ran
        import traceback
        traceback.print_exc()
    finally:
        # os._exit is required after a fork, but it must not discard pending
        # executor writes or Python's ordinary exit handlers. Match interpreter
        # shutdown ordering before the final process exit.
        try:
            threads = sys.modules.get('threading')
            if threads is not None:
                threads._shutdown()
            handlers = sys.modules.get('atexit')
            if handlers is not None:
                handlers._run_exitfuncs()
        except BaseException:
            import traceback
            traceback.print_exc()
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os._exit(status)


def _join_launch_scope(cgroup):
    if not isinstance(cgroup, str) or not cgroup.startswith("/") or os.path.normpath(cgroup) != cgroup:
        return False
    unit = cgroup.rsplit("/", 1)[-1]
    if unit.startswith("org.gnome.Shell@") or unit.endswith(".slice"):
        return False
    if cgroup == _own_cgroup():
        return True
    try:
        with open(f"/sys/fs/cgroup{cgroup}/cgroup.procs", "w", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
        return True
    except OSError:
        pass
    # systemd owns transient scope cgroups. Its user manager can attach the
    # child without granting applications write access to cgroup control files.
    unit = cgroup.rsplit("/", 1)[-1]
    if not unit.endswith((".scope", ".service")):
        return False
    import ctypes
    bus = ctypes.c_void_p()
    try:
        library = ctypes.CDLL("libsystemd.so.0")
        library.sd_bus_open_user.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        library.sd_bus_open_user.restype = ctypes.c_int
        library.sd_bus_flush_close_unref.argtypes = [ctypes.c_void_p]
        library.sd_bus_flush_close_unref.restype = ctypes.c_void_p
        library.sd_bus_call_method.restype = ctypes.c_int
        if library.sd_bus_open_user(ctypes.byref(bus)) >= 0:
            return library.sd_bus_call_method(bus, b"org.freedesktop.systemd1", b"/org/freedesktop/systemd1",
                b"org.freedesktop.systemd1.Manager", b"AttachProcessesToUnit", None, None,
                b"ssau", unit.encode(), b"", ctypes.c_uint(1), ctypes.c_uint(os.getpid())) >= 0
    except (OSError, AttributeError):
        pass
    finally:
        if bus.value:
            library.sd_bus_flush_close_unref(bus)
    return False


def _set_process_name(name: str) -> None:
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        libc.prctl(15, name.encode()[:15], 0, 0, 0)  # PR_SET_NAME
    except (OSError, AttributeError):
        pass


# A single display-ready spare pays GTK/CSS/GL initialization before a tap.
# It never forks after connecting to the display; only the single-threaded
# parent forks. No application data or UI is loaded in the spare.
def _display_environment(environment):
    names = set(EXEC_ENVIRONMENT) | {
        'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
        'XDG_CONFIG_HOME', 'XDG_CONFIG_DIRS', 'XDG_DATA_HOME', 'XDG_DATA_DIRS',
        'HOME', 'LANG', 'LANGUAGE', 'LC_ALL', 'LC_CTYPE',
    }
    names.update(k for k in environment if k.startswith(('LUMA_', 'GDK_', 'GSK_', 'GTK_', 'ADW_', 'LC_')))
    return {k: environment.get(k) for k in names if environment.get(k) is not None}


def _prepare_display():
    from gi.repository import Adw, Gtk
    initialized = Gtk.init_check()
    if not initialized:
        raise RuntimeError('no GTK display')
    overrides = sys.modules.get('gi.overrides.Gtk')
    if overrides is not None:
        overrides.initialized = initialized
    Adw.init()
    from luma_appkit import install_appkit, install_lumaui
    install_appkit()
    install_lumaui()
    # Realize without mapping: initializes the shared display GL context,
    # creates no visible window, and requests no continuous frames.
    global _spare_window
    _spare_window = Gtk.Window()
    _spare_window.set_default_size(1, 1)
    controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    heading = Gtk.Label(label="Luma")
    heading.add_css_class("title-1")
    for widget in (heading, Gtk.Button(label="Open"), Gtk.Entry(), Gtk.ScrolledWindow()):
        controls.append(widget)
    _spare_window.set_child(controls)
    _spare_window.realize()
    # Resolve common widget properties, fonts and CSS before application code
    # asks for them. Measuring an unmapped widget requests no frame callbacks.
    controls.measure(Gtk.Orientation.HORIZONTAL, -1)
    controls.measure(Gtk.Orientation.VERTICAL, 360)
    # An unused cache is cheaper to reclaim than an actual running app.
    global _spare_original_oom
    try:
        with open("/proc/self/oom_score_adj", encoding="ascii") as stream:
            _spare_original_oom = int(stream.read())
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as stream:
            stream.write(str(max(500, _spare_original_oom)))
    except (OSError, ValueError):
        pass



def _runtime_security(pid):
    with open(f"/proc/{pid}/status", encoding="ascii") as stream:
        status = dict(line.split(":", 1) for line in stream if ":" in line)
    if status.get("Seccomp", "").strip() != "0" or status.get("NoNewPrivs", "").strip() != "0":
        return None
    fields = tuple(tuple(status.get(k, "").split()) for k in ("Uid", "Gid", "Groups"))
    try:
        with open(f"/proc/{pid}/attr/current", "rb") as stream:
            label = stream.read()
    except OSError as error:
        import errno
        if error.errno not in (errno.EINVAL, errno.ENOENT, errno.EOPNOTSUPP):
            raise
        label = None  # no LSM process-label interface on this kernel
    return fields, label


def _same_runtime_namespace(pid):
    # A fork must not escape a caller's namespaces, credentials, LSM label,
    # seccomp policy, or a no-new-privileges/Landlock boundary. Unknown peers
    # execute normally. The socket alone is never authority to unbox an app.
    try:
        own = _runtime_security('self')
        return own is not None and _runtime_security(pid) == own and all(
            os.stat(f"/proc/{pid}/ns/{name}").st_ino == os.stat(f"/proc/self/ns/{name}").st_ino
            for name in ("mnt", "user", "pid", "net", "ipc", "uts", "cgroup", "time"))
    except OSError:
        return False


def _import_environment(environment):
    # App modules can capture identity, storage paths or translated strings at
    # import time. A different request must execute in a fresh interpreter,
    # not inherit those already-imported constants from this session's host.
    names = {'HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
             'XDG_STATE_HOME', 'LANG', 'LANGUAGE', 'LC_ALL', 'TZ'}
    names.update(k for k in environment if k.startswith(('LUMA_', 'LC_'))
                 and not k.startswith('LUMA_APP_HOST'))
    return {k: environment[k] for k in names if k in environment}


def _compatible(request):
    if _import_environment(request["env"]) != _import_environment(os.environ):
        return False
    if not request.get("native"):
        return request.get("interpreter") == _HOST_INTERPRETER
    # The native client executes Python with -sP on fallback. Match exactly
    # what cannot be changed after fork, including the dynamic linker env.
    state = _HOST_INTERPRETER
    flags = state["flags"]
    environment = request["env"]
    optimization = environment.get("PYTHONOPTIMIZE", "0") or "0"
    optimization = int(optimization) if optimization.isdecimal() else 1
    return (request.get("safe_path") is True
            and os.path.realpath(request.get("python", "")) == state["executable"]
            and flags[:6] == [0, 1, optimization, True,
                              bool(environment.get("PYTHONDONTWRITEBYTECODE")), 0]
            and all(environment.get(k) == os.environ.get(k) for k in ("LANG", "LC_ALL", "LC_CTYPE"))
            and {k: request["env"].get(k) for k in EXEC_ENVIRONMENT} == state["env"])


class _LaunchHost:
    def __init__(self, listener, log, prewarm):
        import selectors
        self.selector = selectors.DefaultSelector()
        self.listener, self.log = listener, log
        self.children = {}
        self.spare = None
        self.prewarm = prewarm
        self.refill_at = None
        self.stopping = False
        self.environment = _display_environment(os.environ)
        self.wake_read, self.wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        self.selector.register(listener, selectors.EVENT_READ, ('listen', 0))
        self.selector.register(self.wake_read, selectors.EVENT_READ, ('wake', 0))
        self.previous_wakeup = signal.set_wakeup_fd(self.wake_write)

    def descriptors(self):
        items = [self.selector, self.listener, self.wake_read, self.wake_write]
        for connection, pidfd in self.children.values():
            items += [connection, pidfd]
        if self.spare:
            items += [self.spare['socket'], self.spare['pidfd']]
        return items

    def refill(self):
        import selectors
        self.refill_at = None
        if not self.prewarm or self.spare:
            return
        try:
            with open("/proc/meminfo", encoding="ascii") as stream:
                available = next(int(line.split()[1]) for line in stream if line.startswith("MemAvailable:"))
            if available < 512 * 1024:
                return
        except (OSError, StopIteration, ValueError):
            pass
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        inherited = self.descriptors() + [parent]
        pid = os.fork()
        if pid == 0:
            signal.set_wakeup_fd(-1)
            for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(number, signal.SIG_DFL)
            for item in inherited:
                os.close(item) if isinstance(item, int) else item.close()
            try:
                _prepare_display()
                _send(child, {'ready': True})
                request, fds = _receive(child, 4)
                child.close()
                if request and len(fds) == 4:
                    _become(request, fds, [])
            except BaseException as error:
                self.log(f'display spare unavailable: {error}')
            os._exit(1)
        child.close()
        pidfd = os.pidfd_open(pid)
        self.spare = {'socket': parent, 'pid': pid, 'pidfd': pidfd, 'ready': False}
        self.selector.register(parent, selectors.EVENT_READ, ('ready', pid))
        self.selector.register(pidfd, selectors.EVENT_READ, ('spare-exit', pid))

    def accept(self):
        import selectors
        import time
        connection, _address = self.listener.accept()
        fds = []
        try:
            connection.settimeout(0.1)
            request, fds = _receive(connection, 4)
            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            peer_pid, uid, _ = struct.unpack('3i', credentials)
            if (not isinstance(request, dict) or request.get('protocol') != PROTOCOL or uid != os.getuid()
                    or len(fds) != 4 or not isinstance(request.get('argv'), list) or not request['argv']
                    or not isinstance(request.get('env'), dict)):
                raise ValueError('malformed request')
            if not _same_runtime_namespace(peer_pid):
                raise ValueError('launcher sandbox differs from host')
            request['_launcher_pid'] = peer_pid
            if not _compatible(request):
                raise ValueError('interpreter or linker environment differs')
            if _thread_count() != 1:
                raise ValueError('host is no longer single-threaded')
            display_environment = _display_environment(request['env'])
            spare = self.spare if (self.spare and self.spare['ready'] and
                display_environment == self.environment) else None
            if self.spare and self.spare['ready'] and not spare:
                changed = sorted(k for k in self.environment.keys() | display_environment.keys()
                                 if self.environment.get(k) != display_environment.get(k))
                self.log('fresh display required: ' + ', '.join(changed))
            gate = None
            if spare:
                pid, pidfd = spare['pid'], spare['pidfd']
            else:
                gate, child_gate = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
                pid = os.fork()
                if pid == 0:
                    signal.set_wakeup_fd(-1)
                    signal.signal(signal.SIGTERM, signal.SIG_DFL)
                    gate.close()
                    try:
                        committed = child_gate.recv(1) == b'1'
                    except OSError:
                        committed = False
                    child_gate.close()
                    if committed:
                        _become(request, fds, self.descriptors() + [connection])
                    os._exit(1)
                child_gate.close()
                pidfd = os.pidfd_open(pid)
            try:
                # No app code runs until the client commits. A client that
                # times out can safely fall back without executing twice.
                _send(connection, {'pid': pid}, [pidfd])
                committed, extra = _receive(connection)
                for fd in extra:
                    os.close(fd)
                if not committed or committed.get('start') is not True:
                    raise ValueError('launcher did not commit')
            except (OSError, ValueError, AttributeError):
                if gate:
                    gate.close()
                    os.close(pidfd)
                    os.waitpid(pid, 0)
                raise ValueError('launcher disconnected before commit')
            if spare:
                _send(spare['socket'], request, fds)
                self.selector.unregister(pidfd)
                spare['socket'].close()
                self.spare = None
                self.log(f'using display-ready spare for {os.path.basename(request["argv"][0])}')
            else:
                gate.send(b'1')
                gate.close()
            self.children[pid] = (connection, pidfd)
            self.selector.register(pidfd, selectors.EVENT_READ, ('exit', pid))
            self.selector.register(connection, selectors.EVENT_READ, ('launcher', pid))
            # Refill after the new app's first frames, never alongside them.
            if self.prewarm and self.spare is None:
                self.refill_at = time.monotonic() + 2.0
        except (OSError, ValueError, TypeError) as error:
            self.log(f'declining a launch: {error}')
            connection.close()
        finally:
            for fd in fds:
                os.close(fd)

    def run(self):
        import time
        self.refill()
        while not self.stopping:
            timeout = max(0, self.refill_at - time.monotonic()) if self.refill_at else None
            for key, _events in self.selector.select(timeout):
                kind, pid = key.data
                if kind == 'wake':
                    os.read(self.wake_read, 4096)
                elif kind == 'listen':
                    self.accept()
                elif kind == 'ready' and self.spare and self.spare['pid'] == pid:
                    message, fds = _receive(self.spare['socket'])
                    self.selector.unregister(self.spare['socket'])
                    self.spare['ready'] = bool(message and message.get('ready'))
                    if self.spare['ready']:
                        self.log("display spare ready")
                elif kind == 'spare-exit' and self.spare and self.spare['pid'] == pid:
                    for item in (self.spare['socket'], self.spare['pidfd']):
                        try:
                            self.selector.unregister(item)
                        except KeyError:
                            pass
                        os.close(item) if isinstance(item, int) else item.close()
                    os.waitpid(pid, 0)
                    self.spare = None
                    # No crash/retry loop on unsupported display backends.
                    self.prewarm = False
                elif pid in self.children:
                    connection, pidfd = self.children[pid]
                    if kind == 'launcher':
                        self.selector.unregister(connection)
                        try:
                            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    elif kind == 'exit':
                        del self.children[pid]
                        self.selector.unregister(pidfd)
                        try:
                            self.selector.unregister(connection)
                        except KeyError:
                            pass
                        os.close(pidfd)
                        _, status = os.waitpid(pid, 0)
                        code = os.waitstatus_to_exitcode(status)
                        try:
                            _send(connection, {'exit': 128 - code if code < 0 else code})
                        except OSError:
                            pass
                        connection.close()
            if self.refill_at and time.monotonic() >= self.refill_at:
                self.refill()

    def close(self):
        signal.set_wakeup_fd(self.previous_wakeup)
        if self.spare:
            try:
                signal.pidfd_send_signal(self.spare['pidfd'], signal.SIGTERM)
            except ProcessLookupError:
                pass
            self.spare['socket'].close()
            os.close(self.spare['pidfd'])
        self.selector.close()
        os.close(self.wake_read)
        os.close(self.wake_write)


def serve(path: str | None = None, log=None, warm: bool = True) -> int:
    import gc
    log = log or (lambda text: print(f'luma-app-host: {text}', file=sys.stderr, flush=True))
    path = path or socket_path()
    if path is None:
        return 1
    if os.environ.get("XDG_SESSION_CLASS") == "greeter":
        return 0
    global _HOST_INTERPRETER
    _HOST_INTERPRETER = _interpreter()
    if warm:
        _warm(log)
    media = sys.modules.get('gi.repository.Gst')
    if media is not None and media.is_initialized():
        log('refusing to fork initialized media state; remove that preload')
        return 1
    if _thread_count() != 1:
        log('refusing to fork a multithreaded host')
        return 1
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
    temporary = f'{path}.{os.getpid()}'
    listener.bind(temporary)
    os.chmod(temporary, 0o600)
    listener.listen(16)
    os.replace(temporary, path)
    bound = os.stat(path).st_ino
    gc.freeze()
    host = _LaunchHost(listener, log, warm and os.environ.get('LUMA_APP_HOST_DISPLAY_SPARE', '1') != '0')
    def stop(*_args):
        host.stopping = True
    old_signals = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
    log(f'serving on {path}')
    try:
        host.run()
    finally:
        host.close()
        for s, handler in old_signals.items():
            signal.signal(s, handler)
        try:
            if os.stat(path).st_ino == bound:
                os.unlink(path)
        except FileNotFoundError:
            pass
        listener.close()
    return 0


if __name__ == "__main__":
    # Serve from the importable module, not __main__: launchers import
    # luma_appkit.apphost, and the fork must see its own state there.
    from luma_appkit import apphost

    raise SystemExit(apphost.serve())
