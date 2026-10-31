#!/usr/bin/python3
"""Mobile mode on the Pixel 9 Pro Fold: calls and texts, or mobile data.

While IMS is up this CP takes one PDN only (a second gets cause 2253), so the
attach APN decides what works: "calls" attaches on ims (VoLTE calls and IMS
SMS, data on Wi-Fi), "data" attaches on the data APN (mobile data, no calls
or texts). Hook 82 prepares each mode's files in /run/luma-fold-mobile/<mode>/
and puts the saved mode's in place at boot. This service switches by putting
the other set in place and restarting ModemManager (about 80 s to register
again), and saves the choice in /var/lib/luma-fold/mobile-mode.

Data mode is NetworkManager's "Mobile data" connection when NetworkManager has
its wwan plugin (backend "nm"); without it this service connects the bearer
and configures its interface itself (backend "helper").

MMS needs a data PDN. In calls mode RequestMmsWindow (from luma-fold-mms in
the session, when mmsd-tng has a message to fetch or send) brings data up
without saving it as the mode, holds it MMS_HOLD seconds after the last
request, then goes back to calls. State is "mms" while the bearer is up.

D-Bus: org.projectluma.MobileMode1 on the system bus. SetMode and
RequestMmsWindow need NetworkManager's enable-disable-wwan authorization (yes
for the active session). For tests, as root: luma-fold-mobile-mode.py status | set calls|data
"""
import json
import os
import subprocess
import sys
import threading
import time
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)  # PyGObject API churn

NAME = "org.projectluma.MobileMode1"
PATH = "/org/projectluma/MobileMode1"
ERROR = NAME + ".Error"
POLKIT_ACTION = "org.freedesktop.NetworkManager.enable-disable-wwan"
MODES = ("calls", "data")
CONNECTION = "Mobile data"
REGISTER_TIMEOUT = 240  # registration took 77-83 s in every LTE run
IMS_TIMEOUT = 180       # IMS registered 70 s after a switch on the phone
KEEPER_INTERVAL = 30
MMS_HOLD = 40           # seconds of data after the last MMS window request

XML = """<node><interface name="org.projectluma.MobileMode1">
  <method name="SetMode"><arg type="s" name="mode" direction="in"/></method>
  <method name="RequestMmsWindow"/>
  <property name="Mode" type="s" access="read"/>
  <property name="State" type="s" access="read"/>
  <property name="DataAvailable" type="b" access="read"/>
</interface></node>"""


def log(message):
    print(f"luma-fold-mobile-mode: {message}", flush=True)


class Paths:
    """Every path this service touches, under LUMA_MOBILE_MODE_ROOT for tests."""

    def __init__(self, root=""):
        j = lambda p: root + p
        self.root = root
        self.prepared = j("/run/luma-fold-mobile")
        self.saved = j("/var/lib/luma-fold/mobile-mode")
        self.status = j("/run/luma/mobile-mode.json")
        self.mm_dropins = j("/run/systemd/system/ModemManager.service.d")
        self.nm_conf = j("/run/NetworkManager/conf.d/90-luma-fold-modem.conf")
        self.nm_dns = j("/run/NetworkManager/conf.d/91-luma-fold-mobile-dns.conf")
        self.nm_connection = j("/run/NetworkManager/system-connections/luma-mobile-data.nmconnection")
        self.stack = j("/run/luma-fold-modem.stack")
        self.radio_unit = j("/run/systemd/system/luma-fold-modem-radio.service")


def write_atomic(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def keyvalue(text):
    """mmcli -K output as a dict; list entries keep their [n] suffix."""
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip()] = value.strip()
    return values


class Modem:
    """Commands against the running system; replaced by a fake in tests."""

    def __init__(self, paths):
        self.paths = paths
        prefix = "/var/lib/luma-fold/modem-stack/usr" if read(paths.stack).strip() == "var" else "/usr"
        self.mmcli = f"{prefix}/bin/mmcli"
        self.env = dict(os.environ, LD_LIBRARY_PATH=f"{prefix}/lib64") if prefix != "/usr" else None

    def run(self, *args, timeout=60):
        try:
            done = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                  env=self.env if args[0] == self.mmcli else None)
            return done.returncode, done.stdout
        except (OSError, subprocess.SubprocessError) as error:
            return -1, str(error)

    def mm(self, *args, timeout=60):
        return self.run(self.mmcli, *args, timeout=timeout)

    def sleep(self, seconds):
        time.sleep(seconds)


class Switcher:
    """The mode switch itself, free of D-Bus so the tests can drive it."""

    def __init__(self, paths, modem, changed=lambda: None):
        self.paths, self.modem, self.changed = paths, modem, changed
        self.mode = read(f"{paths.prepared}/mode").strip() or "calls"
        self.state = "ready"
        self.lock = threading.Lock()
        self.keeper = None
        self.keeper_stop = threading.Event()
        self.bearer_iface = None
        self.window_until = None  # monotonic end of the MMS window while one runs

    # -- what is prepared ----------------------------------------------
    def available(self, mode):
        return os.path.isfile(f"{self.paths.prepared}/{mode}/luma-fold-attach-apn.conf")

    def backend(self):
        return read(f"{self.paths.prepared}/data/backend").strip() or "nm"

    def data_apn(self):
        return read(f"{self.paths.prepared}/data/apn").strip()

    # -- state -----------------------------------------------------------
    def publish(self, state):
        self.state = state
        window = self.window_until is not None
        status = {"mode": self.mode, "state": state, "calls": self.mode == "calls" and not window,
                  "data": self.mode == "data" or state == "mms"}
        write_atomic(self.paths.status, json.dumps(status) + "\n")
        log(f"mode={self.mode} state={state}")
        self.changed()

    def save(self, mode):
        lines = [l for l in read(self.paths.saved).splitlines() if l and not l.startswith("mode=")]
        write_atomic(self.paths.saved, "\n".join([f"mode={mode}"] + lines) + "\n")

    # -- switch ------------------------------------------------------------
    def start(self):
        """At service start in data mode, run the live switch once: at boot the
        CP registers before the data attach APN reaches it and keeps the IMS
        attach, so the internet PDN gets 2253 until a radio-off re-attach."""
        if self.mode != "data":
            return
        with self.lock:
            self.publish("switching")
        threading.Thread(target=self.switch, args=("data", "data"), daemon=True).start()

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("unknown mode")
        if not self.available(mode):
            raise LookupError("this mode is not prepared on this boot")
        with self.lock:
            if self.state == "switching" or self.window_until is not None:
                raise BlockingIOError("a switch is already running")
            if mode == self.mode and self.state == "ready":
                return False
            previous, self.mode = self.mode, mode
            self.publish("switching")
        threading.Thread(target=self.switch, args=(previous, mode), daemon=True).start()
        return True

    def switch(self, previous, mode):
        try:
            ok = self.apply(previous, mode)
        except Exception as error:  # keep serving; the state says what happened
            log(f"switch failed: {error}")
            ok = False
        self.publish("ready" if ok else "failed")

    def request_mms_window(self):
        """Data for MMS while in calls mode; repeated requests extend it."""
        with self.lock:
            if self.mode == "data" or not self.available("data"):
                return False
            if self.window_until is not None:
                self.window_until = max(self.window_until, time.monotonic() + MMS_HOLD)
                return True
            if self.state == "switching":
                raise BlockingIOError("a switch is already running")
            self.window_until = time.monotonic() + MMS_HOLD
            self.publish("switching")
        threading.Thread(target=self.window, daemon=True).start()
        return True

    def window(self):
        try:
            if self.apply("calls", "data", window=True):
                with self.lock:
                    self.window_until = max(self.window_until, time.monotonic() + MMS_HOLD)
                    self.publish("mms")
                log("MMS window open")
                while time.monotonic() < self.window_until:
                    self.modem.sleep(2)
            else:
                log("MMS window: data did not come up")
            with self.lock:
                self.publish("switching")
            ok = self.apply("data", "calls", window=True)
        except Exception as error:
            log(f"MMS window failed: {error}")
            ok = False
        with self.lock:
            self.window_until = None
            self.publish("ready" if ok else "failed")

    def apply(self, previous, mode, window=False):
        """window: an MMS window's data, not saved and not the mode on record."""
        m = self.modem
        started = int(time.time())
        if not window:
            self.save(mode)
        if previous == "data":
            self.stop_data()
        # Radio off first, so the CP detaches before the new attach APN goes out.
        m.mm("-m", "any", "--disable", timeout=40)
        src = f"{self.paths.prepared}/{mode}"
        os.makedirs(self.paths.mm_dropins, exist_ok=True)
        for name in ("luma-fold-attach-apn.conf", "luma-fold-voice-mode.conf"):
            write_atomic(f"{self.paths.mm_dropins}/{name}", read(f"{src}/{name}"))
        if os.path.isfile(f"{src}/90-luma-fold-modem.conf"):
            write_atomic(self.paths.nm_conf, read(f"{src}/90-luma-fold-modem.conf"))
        elif os.path.exists(self.paths.nm_conf):
            os.unlink(self.paths.nm_conf)
        write_atomic(self.paths.nm_connection, read(f"{src}/luma-mobile-data.nmconnection"), 0o600)
        if not window:
            write_atomic(f"{self.paths.prepared}/mode", mode + "\n")
        # NetworkManager first: the modem it sees after the restart is a new one,
        # and it decides managed or not from the reloaded configuration.
        m.run("nmcli", "general", "reload", "conf")
        m.run("nmcli", "connection", "reload")
        m.run("systemctl", "daemon-reload")
        rc, _ = m.run("systemctl", "restart", "ModemManager.service", timeout=90)
        if rc != 0:
            log("ModemManager restart failed")
            return False
        if os.path.exists(self.paths.radio_unit):
            m.run("systemctl", "restart", "--no-block", "luma-fold-modem-radio.service")
        if not self.wait_registered():
            log("not registered in time")
            return False
        if mode == "calls":
            return self.wait_ims(started)
        if self.backend() == "nm":
            return self.wait_nm_connected()
        ok = self.connect_bearer()
        if not window:
            self.start_keeper(connect_first=False)
        return ok

    def wait_registered(self):
        deadline = time.monotonic() + REGISTER_TIMEOUT
        while time.monotonic() < deadline:
            rc, out = self.modem.mm("-m", "any", "-K", timeout=15)
            v = keyvalue(out) if rc == 0 else {}
            if v.get("modem.generic.state") in {"registered", "connecting", "connected"}:
                return True
            self.modem.sleep(3)
        return False

    def wait_ims(self, since):
        """Calls mode is ready when IMS registers. The plugin says so at info
        level only, so the new ModemManager logs at info until then. Only
        grep -q on the line: it carries the subscriber's SIP identity."""
        self.modem.mm("-G", "INFO", timeout=15)
        deadline = time.monotonic() + IMS_TIMEOUT
        ok = False
        while time.monotonic() < deadline:
            rc, _ = self.modem.run("sh", "-c", "journalctl -u ModemManager.service --since @%d -o cat "
                                   "--no-pager | grep -q 'IMS registered'" % since, timeout=20)
            if rc == 0:
                ok = True
                break
            self.modem.sleep(3)
        self.modem.mm("-G", "MSG", timeout=15)
        if not ok:
            log("IMS did not register in time")
        return ok

    def wait_nm_connected(self, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, out = self.modem.run("nmcli", "-t", "-f", "TYPE,STATE", "device")
            if any(line.startswith("gsm:connected") for line in out.splitlines()):
                return True
            self.modem.sleep(3)
        return False

    # -- data without NetworkManager's wwan plugin --------------------------
    def start_keeper(self, connect_first):
        self.keeper_stop.clear()
        if self.keeper and self.keeper.is_alive():
            return
        self.keeper = threading.Thread(target=self.keep, args=(connect_first,), daemon=True)
        self.keeper.start()

    def keep(self, connect_first):
        if connect_first:
            self.connect_bearer()
        while not self.keeper_stop.wait(KEEPER_INTERVAL):
            if self.mode != "data" or self.state == "switching":
                continue
            if not self.bearer_connected():
                log("bearer down; reconnecting")
                self.connect_bearer()

    def bearer_path(self):
        rc, out = self.modem.mm("-m", "any", "-K", timeout=15)
        v = keyvalue(out) if rc == 0 else {}
        path = v.get("modem.generic.bearers.value[1]", "")
        return path if path.startswith("/") else None

    def bearer_connected(self):
        path = self.bearer_path()
        if not path:
            return False
        rc, out = self.modem.mm("-b", path.rsplit("/", 1)[-1], "-K", timeout=15)
        return rc == 0 and keyvalue(out).get("bearer.status.connected") == "yes"

    def connect_bearer(self):
        if not self.wait_registered():
            return False
        apn = self.data_apn()
        rc, _ = self.modem.mm("-m", "any", f"--simple-connect=apn={apn},ip-type=ipv4v6", timeout=90)
        path = self.bearer_path()
        if rc != 0 or not path:
            log("bearer did not connect")
            return False
        rc, out = self.modem.mm("-b", path.rsplit("/", 1)[-1], "-K", timeout=15)
        b = keyvalue(out)
        if b.get("bearer.status.connected") != "yes" or b.get("bearer.status.interface", "--") == "--":
            log("bearer did not connect")
            return False
        self.configure(b)
        return True

    def configure(self, b):
        """Raw-IP rmnet is point to point: device routes, no gateway needed.
        Metric 700 keeps Wi-Fi (600) first. The carrier's DNS goes to
        NetworkManager as global DNS, reached over the bearer by host routes."""
        run = self.modem.run
        iface = b["bearer.status.interface"]
        self.bearer_iface = iface
        run("ip", "addr", "flush", "dev", iface)
        mtu = b.get("bearer.ipv4-config.mtu", "--")
        run("ip", "link", "set", iface, "up", *(("mtu", mtu) if mtu.isdigit() else ()))
        dns = []
        a4 = b.get("bearer.ipv4-config.address", "--")
        if a4 != "--":
            run("ip", "addr", "add", f"{a4}/{b.get('bearer.ipv4-config.prefix', '32')}", "dev", iface)
            run("ip", "route", "replace", "default", "dev", iface, "metric", "700")
        a6 = b.get("bearer.ipv6-config.address", "--")
        if a6 != "--":
            run("ip", "-6", "addr", "add", f"{a6}/{b.get('bearer.ipv6-config.prefix', '64')}", "dev", iface)
            run("ip", "-6", "route", "replace", "default", "dev", iface, "metric", "700")
        for family in ("ipv4", "ipv6"):
            for n in (1, 2):
                server = b.get(f"bearer.{family}-config.dns.value[{n}]", "--")
                if server != "--":
                    dns.append(server)
                    run("ip", *(("-6",) if family == "ipv6" else ()), "route", "replace", server, "dev", iface)
        if dns:
            write_atomic(self.paths.nm_dns, "[global-dns-domain-*]\nservers=" + ",".join(dns) + "\n")
            run("nmcli", "general", "reload", "conf")
            run("nmcli", "general", "reload", "dns-full")
        log(f"bearer up on {iface}")

    def stop_data(self):
        if self.backend() == "nm":
            self.modem.run("nmcli", "connection", "down", CONNECTION)
            return
        self.keeper_stop.set()
        self.modem.mm("-m", "any", "--simple-disconnect", timeout=30)
        if self.bearer_iface:
            self.modem.run("ip", "addr", "flush", "dev", self.bearer_iface)
            self.modem.run("ip", "-6", "route", "flush", "dev", self.bearer_iface)
            self.modem.run("ip", "route", "flush", "dev", self.bearer_iface)
            self.bearer_iface = None
        if os.path.exists(self.paths.nm_dns):
            os.unlink(self.paths.nm_dns)
            self.modem.run("nmcli", "general", "reload", "conf")
            self.modem.run("nmcli", "general", "reload", "dns-full")


class Service:
    def __init__(self, bus, switcher):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus, self.switcher = bus, switcher
        switcher.changed = lambda: GLib.idle_add(self.emit_changed)
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        bus.register_object(PATH, node.interfaces[0], self.on_method, self.on_get, None)

    def props(self):
        GLib, s = self.GLib, self.switcher
        return {"Mode": GLib.Variant("s", s.mode), "State": GLib.Variant("s", s.state),
                "DataAvailable": GLib.Variant("b", s.available("data"))}

    def on_get(self, _c, _sender, _path, _iface, name):
        return self.props().get(name)

    def emit_changed(self):
        self.bus.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                             self.GLib.Variant("(sa{sv}as)", (NAME, self.props(), [])))
        return False

    def on_method(self, _c, sender, _path, _iface, method, params, invocation):
        if method == "RequestMmsWindow":
            return self.authorize(sender, lambda ok: self.mms_window(invocation) if ok else
                                  invocation.return_dbus_error(f"{ERROR}.NotAuthorized", "not allowed"))
        if method != "SetMode":
            return invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", "no such method")
        mode = params.unpack()[0]
        self.authorize(sender, lambda ok: self.set_mode(invocation, mode) if ok else
                       invocation.return_dbus_error(f"{ERROR}.NotAuthorized", "not allowed"))

    def set_mode(self, invocation, mode):
        try:
            self.switcher.set_mode(mode)
        except ValueError:
            return invocation.return_dbus_error(f"{ERROR}.InvalidMode", "unknown mode")
        except LookupError:
            return invocation.return_dbus_error(f"{ERROR}.Unavailable", "this mode is not available")
        except BlockingIOError:
            return invocation.return_dbus_error(f"{ERROR}.Busy", "a switch is already running")
        invocation.return_value(None)

    def mms_window(self, invocation):
        try:
            self.switcher.request_mms_window()
        except BlockingIOError:
            return invocation.return_dbus_error(f"{ERROR}.Busy", "a switch is already running")
        invocation.return_value(None)

    def authorize(self, sender, done):
        GLib, Gio = self.GLib, self.Gio
        subject = ("system-bus-name", {"name": GLib.Variant("s", sender)})
        args = GLib.Variant("((sa{sv})sa{ss}us)", (subject, POLKIT_ACTION, {}, 1, ""))

        def finished(conn, result):
            try:
                ok = conn.call_finish(result).unpack()[0][0]
            except GLib.Error as error:
                log(f"polkit: {error.message}")
                ok = False
            done(ok)
        self.bus.call("org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
                      "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization", args,
                      GLib.VariantType("((bba{ss}))"), Gio.DBusCallFlags.NONE, 120000, None, finished)


def serve(paths):
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    import signal
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    switcher = Switcher(paths, Modem(paths))
    Service(bus, switcher)
    loop = GLib.MainLoop()
    Gio.bus_own_name_on_connection(bus, NAME, Gio.BusNameOwnerFlags.NONE,
                                   lambda *_: log(f"owning {NAME}"), lambda *_: loop.quit())
    for number in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, lambda: loop.quit() or False)
    switcher.publish(switcher.state)
    switcher.start()
    loop.run()
    return 0


def main(argv):
    paths = Paths(os.environ.get("LUMA_MOBILE_MODE_ROOT", ""))
    if len(argv) > 1 and argv[1] == "status":
        print(read(paths.status, "{}\n"), end="")
        return 0
    if len(argv) > 2 and argv[1] == "set":
        bus = ["busctl", "--system", "call", NAME, PATH, NAME, "SetMode", "s", argv[2]]
        return subprocess.run(bus).returncode
    return serve(paths)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
