#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Session smoke for Charlie's background mail agent.

Run inside a D-Bus session that has nothing else of Charlie's in it::

    dbus-run-session -- python3 tests/mail_agent_session_smoke.py

The script starts, all of its own:

* a tiny ``org.freedesktop.Notifications`` stub that records ``Notify`` and can
  emit ``ActionInvoked``; the same process stands in for Charlie's window
  (``org.freedesktop.Application`` on ``org.projectluma.Charlie``) to record
  the action that opens a message, and owns ``org.projectluma.Background1`` so
  it can deliver agent wakes the way luma-background does,
* ``gnome-keyring-daemon`` (secrets component, a throwaway login keyring
  under a temporary directory) unless a Secret Service is
  already on the bus; ``--keyring-root`` points at an extracted gnome-keyring
  when it is not installed,
* a local TLS IMAP server with a throwaway certificate authority, trusted by
  the agent through ``SSL_CERT_FILE`` (verification stays on).

It then runs ``python3 -m charlie_luma --agent`` or ``--agent-command``
as a managed agent
(``LUMA_BACKGROUND_MANAGED=1``) against a temporary XDG home, delivers mail and prints a JSON summary. It never
uses the real mail store, keyring or network.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

AGENT = "org.projectluma.Charlie.Agent"
ACCOUNT_ID = "mail-session-smoke"
ADDRESS = "reader@example.test"
PASSWORD = "session smoke password"
STUB_BUS = "org.projectluma.CharlieSmoke.Stub"
STUB_PATH = "/org/projectluma/CharlieSmoke/Stub"

STUB_XML = """<node>
<interface name="org.freedesktop.Notifications">
  <method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
    <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/></method>
  <method name="CloseNotification"><arg type="u" direction="in"/></method>
  <method name="GetCapabilities"><arg type="as" direction="out"/></method>
  <method name="GetServerInformation"><arg type="s" direction="out"/><arg type="s" direction="out"/>
    <arg type="s" direction="out"/><arg type="s" direction="out"/></method>
  <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
  <signal name="ActivationToken"><arg type="u"/><arg type="s"/></signal>
  <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
</interface>
<interface name="org.freedesktop.Application">
  <method name="Activate"><arg type="a{sv}" direction="in"/></method>
  <method name="Open"><arg type="as" direction="in"/><arg type="a{sv}" direction="in"/></method>
  <method name="ActivateAction"><arg type="s" direction="in"/><arg type="av" direction="in"/>
    <arg type="a{sv}" direction="in"/></method>
</interface>
<interface name="org.projectluma.CharlieSmoke.Stub">
  <method name="EmitAction"><arg type="u" direction="in"/><arg type="s" direction="in"/></method>
  <method name="SendWake"><arg type="s" direction="in"/><arg type="s" direction="in"/>
    <arg type="s" direction="out"/></method>
</interface>
</node>"""


# ---------------------------------------------------------------------------
# The stub process
# ---------------------------------------------------------------------------


def run_stub(record: Path) -> int:
    from gi.repository import Gio, GLib

    node = Gio.DBusNodeInfo.new_for_xml(STUB_XML)
    state = {"next": 0}
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def write(entry: dict) -> None:
        with record.open("a") as stream:
            stream.write(json.dumps(entry) + "\n")

    def plain(value):
        return value.unpack() if isinstance(value, GLib.Variant) else value

    def method(_connection, sender, path, interface, name, parameters, invocation):
        arguments = parameters.unpack()
        if interface == "org.freedesktop.Notifications":
            if name == "Notify":
                app_name, replaces, icon, summary, body, actions, hints, timeout = arguments
                if replaces:
                    identifier = replaces
                else:
                    state["next"] += 1
                    identifier = state["next"]
                write(dict(call="Notify", id=identifier, sender=sender, app_name=app_name, replaces=replaces,
                           icon=icon, summary=summary, body=body, actions=actions,
                           hints={key: plain(value) for key, value in hints.items()}, timeout=timeout))
                invocation.return_value(GLib.Variant("(u)", (identifier,)))
            elif name == "CloseNotification":
                write(dict(call="CloseNotification", id=arguments[0]))
                invocation.return_value(None)
                connection.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                                       "NotificationClosed", GLib.Variant("(uu)", (arguments[0], 3)))
            elif name == "GetCapabilities":
                invocation.return_value(GLib.Variant("(as)", (["actions", "body", "persistence"],)))
            else:
                invocation.return_value(GLib.Variant("(ssss)", ("smoke", "Project Luma", "1", "1.2")))
        elif interface == "org.freedesktop.Application":
            write(dict(call=name, path=path, arguments=json.loads(json.dumps(
                [plain(item) if not isinstance(item, list) else [plain(entry) for entry in item]
                 for item in arguments], default=str))))
            invocation.return_value(None)
        elif name == "SendWake":
            agent, reason = arguments

            def delivered(bus, result) -> None:
                try:
                    bus.call_finish(result)
                    invocation.return_value(GLib.Variant("(s)", ("",)))
                except GLib.Error as error:
                    invocation.return_value(GLib.Variant("(s)", (Gio.DBusError.get_remote_error(error) or "error",)))

            connection.call(agent, "/org/projectluma/BackgroundAgent1", "org.projectluma.BackgroundAgent1", "Wake",
                            GLib.Variant("(sa{sv})", (reason, {})), None, Gio.DBusCallFlags.NO_AUTO_START, 5000,
                            None, delivered)
        elif name == "EmitAction":
            identifier, action = arguments
            invocation.return_value(None)
            connection.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                                   "ActivationToken", GLib.Variant("(us)", (identifier, "smoke-token")))
            connection.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                                   "ActionInvoked", GLib.Variant("(us)", (identifier, action)))

    for path, interfaces in (("/org/freedesktop/Notifications", (0,)), ("/org/projectluma/Charlie", (1,)),
                             (STUB_PATH, (2,))):
        for index in interfaces:
            connection.register_object(path, node.interfaces[index], method, None, None)
    loop = GLib.MainLoop()
    owned = {"count": 0}

    def acquired(*_args):
        owned["count"] += 1
        if owned["count"] == 4:
            write(dict(call="ready"))

    for name in ("org.freedesktop.Notifications", "org.projectluma.Charlie", STUB_BUS, "org.projectluma.Background1"):
        Gio.bus_own_name_on_connection(connection, name, Gio.BusNameOwnerFlags.NONE, acquired, None)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, lambda: (loop.quit(), False)[1])
    loop.run()
    return 0


# ---------------------------------------------------------------------------
# The smoke
# ---------------------------------------------------------------------------


class Smoke:
    def __init__(self, arguments) -> None:
        from gi.repository import Gio

        self.arguments = arguments
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.temporary = tempfile.TemporaryDirectory(prefix="charlie-agent-smoke-")
        self.root = Path(self.temporary.name)
        self.record = self.root / "stub.jsonl"
        self.children: list[subprocess.Popen] = []
        self.summary: dict = {"checks": {}}
        self.env = dict(os.environ)
        for variable, directory in (("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                                    ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
                                    ("XDG_RUNTIME_DIR", "runtime")):
            path = self.root / directory
            path.mkdir(mode=0o700)
            self.env[variable] = str(path)
            os.environ[variable] = str(path)
        self.env["PYTHONPATH"] = arguments.pythonpath or str(ROOT)
        self.env["LUMA_BACKGROUND_MANAGED"] = "1"
        self.env["LUMA_BACKGROUND_APP_ID"] = "org.projectluma.Charlie"
        self.env["LUMA_BACKGROUND_CATEGORY"] = "mail"
        self.env["GSETTINGS_BACKEND"] = "memory"
        self.env.pop("LUMA_AGENT_DEBUG", None)

    # -- helpers -------------------------------------------------------------------
    def check(self, name: str, ok: bool, detail=None) -> None:
        self.summary["checks"][name] = {"ok": bool(ok), **({"detail": detail} if detail is not None else {})}
        if not ok:
            raise AssertionError(f"{name}: {detail}")

    def wait(self, predicate, timeout: float = 15.0, interval: float = 0.05):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(interval)
        return predicate()

    def records(self, call: str | None = None) -> list[dict]:
        try:
            lines = self.record.read_text().splitlines()
        except OSError:
            return []
        entries = [json.loads(line) for line in lines if line.strip()]
        return [entry for entry in entries if call is None or entry["call"] == call]

    def has_owner(self, name: str) -> bool:
        from gi.repository import Gio, GLib

        result = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                    "NameHasOwner", GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE,
                                    2000, None)
        return bool(result.unpack()[0])

    def owner_pid(self, name: str) -> int:
        from gi.repository import Gio, GLib

        result = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                    "GetConnectionUnixProcessID", GLib.Variant("(s)", (name,)), None,
                                    Gio.DBusCallFlags.NONE, 2000, None)
        return int(result.unpack()[0])

    def call(self, name: str, path: str, interface: str, method: str, parameters=None):
        from gi.repository import Gio

        return self.bus.call_sync(name, path, interface, method, parameters, None, Gio.DBusCallFlags.NO_AUTO_START,
                                  5000, None)

    def agent_property(self, name: str):
        from gi.repository import GLib

        value = self.call(AGENT, "/org/projectluma/BackgroundAgent1", "org.freedesktop.DBus.Properties", "Get",
                          GLib.Variant("(ss)", ("org.projectluma.BackgroundAgent1", name)))
        return value.unpack()[0]

    def published(self) -> dict:
        try:
            return self.agent_property("Values")
        except Exception:
            return {}

    @staticmethod
    def memory(pid: int) -> dict:
        values = {}
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"VmRSS", "VmHWM", "RssAnon", "RssFile", "Threads"}:
                values[key] = value.strip()
        try:
            for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                key, _, value = line.partition(":")
                if key in {"Pss", "Pss_Anon", "Pss_File"}:
                    values[key] = value.strip()
        except OSError:
            pass
        return values

    @staticmethod
    def cpu_ticks(pid: int) -> int:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])

    def spawn(self, command: list[str], **options) -> subprocess.Popen:
        process = subprocess.Popen(command, env=options.pop("env", self.env), **options)
        self.children.append(process)
        return process

    # -- setup -------------------------------------------------------------------------
    def start_stub(self) -> None:
        self.spawn([sys.executable, __file__, "--notification-stub", str(self.record)])
        self.check("notification stub ready", bool(self.wait(lambda: self.records("ready"), 10)))

    def start_keyring(self) -> None:
        if self.has_owner("org.freedesktop.secrets"):
            self.summary["keyring"] = "existing Secret Service on this bus"
            return
        env = dict(self.env)
        daemon = "gnome-keyring-daemon"
        if self.arguments.keyring_root:
            keyring_root = Path(self.arguments.keyring_root)
            daemon = str(keyring_root / "usr/bin/gnome-keyring-daemon")
            env["LD_LIBRARY_PATH"] = ":".join(filter(None, [str(keyring_root / "usr/lib64"),
                                                            env.get("LD_LIBRARY_PATH", "")]))
        command = [daemon, "--foreground", "--unlock", "--components=secrets"]
        if os.geteuid() == 0 and shutil.which("setpriv"):
            # Root in a build container: gnome-keyring cannot drop capabilities
            # it was never really given, so start it without any.
            command = ["setpriv", "--bounding-set=-all", "--inh-caps=-all", *command]
        process = self.spawn(command, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        # A throwaway login keyring under the temporary XDG_DATA_HOME; an empty
        # password would leave gnome-keyring without a login collection.
        process.stdin.write(b"charlie-smoke-keyring")
        process.stdin.close()
        ok = self.wait(lambda: self.has_owner("org.freedesktop.secrets"), 15)
        self.check("gnome-keyring secrets component on the bus", ok,
                   None if ok else (process.stderr.read(2000).decode(errors="replace") if process.poll() else "timeout"))
        self.summary["keyring"] = f"gnome-keyring-daemon ({'extracted' if self.arguments.keyring_root else 'installed'})"

    def seed(self, port: int) -> None:
        from charlie_luma.model import Account, ServerConfig
        from charlie_luma.secrets import SecretStore
        from charlie_luma.store import MailStore

        store = MailStore(Path(self.env["XDG_DATA_HOME"]) / "charlie" / "mail.db")
        store.upsert_account(Account(ACCOUNT_ID, "Reader", ADDRESS))
        store.upsert_server_config(ACCOUNT_ID, ServerConfig("localhost", port, "localhost", 465, ADDRESS))
        self.store = store
        secrets = SecretStore()
        secrets.store(ACCOUNT_ID, "password", PASSWORD)
        self.check("password stored in and read back from the keyring",
                   secrets.lookup(ACCOUNT_ID, "password") == PASSWORD)

    # -- run ----------------------------------------------------------------------------
    def run(self) -> dict:
        from fake_imap import FakeImapServer, make_certificates, make_message
        from gi.repository import GLib

        started = time.monotonic()
        self.start_stub()
        self.start_keyring()
        ca, cert, key = make_certificates(self.root / "tls")
        self.env["SSL_CERT_FILE"] = str(ca)
        server = FakeImapServer(cert, key, username=ADDRESS, password=PASSWORD).start()
        self.server = server
        self.seed(server.port)
        server.deliver(make_message(1, subject="Old unread 1"), make_message(2, subject="Old unread 2"))

        agent_command = (shlex.split(self.arguments.agent_command) if self.arguments.agent_command
                         else [sys.executable, "-m", "charlie_luma", "--agent"])
        self.summary["agent_command"] = agent_command
        self.summary["agent_pythonpath"] = self.env["PYTHONPATH"]
        agent = self.spawn(agent_command, cwd=ROOT, stderr=open(self.root / "agent.log", "wb"))
        self.check("agent owns its bus name", bool(self.wait(lambda: self.has_owner(AGENT), 20)))
        pid = self.owner_pid(AGENT)
        self.check("bus name belongs to the started process", pid == agent.pid, {"owner": pid, "agent": agent.pid})
        self.check("agent entered IMAP IDLE", bool(server.wait_for(lambda: bool(server.idling), 20)))
        published = self.wait(lambda: (value := self.published()).get("unread-count") == 2 and value, 10)
        self.check("first run publishes the unread count", bool(published), self.published())
        self.check("first run notifies nothing", not self.records("Notify"))
        self.check("Values carry unread-by-account and AppId is Charlie",
                   self.published().get("unread-by-account") == {ACCOUNT_ID: 2}
                   and self.agent_property("AppId") == "org.projectluma.Charlie", self.published())
        self.check("agent log names the contract backend", bool(self.wait(
            lambda: "Agent contract from" in (self.root / "agent.log").read_text(errors="replace"), 5)))
        log = (self.root / "agent.log").read_text(errors="replace")
        self.summary["backend"] = "kit" if "Agent contract from the kit" in log else "built-in"
        if self.arguments.expect_kit:
            self.check("agent runs on the kit's luma_appkit.background", self.summary["backend"] == "kit", log[-400:])
        else:
            self.check("agent runs on Charlie's built-in contract", self.summary["backend"] == "built-in", log[-400:])
        modules = subprocess.run(["grep", "-c", "-E", "libgtk|libadwaita|libwebkit",
                                  f"/proc/{pid}/maps"], capture_output=True, text=True).stdout.strip()
        self.check("no GTK, libadwaita or WebKit library mapped", modules == "0", modules)

        time.sleep(3)
        ticks_before, idle_started = self.cpu_ticks(pid), time.monotonic()
        time.sleep(10)
        idle_ticks = self.cpu_ticks(pid) - ticks_before
        self.summary["idle"] = {
            "memory": self.memory(pid),
            "cpu_seconds_over_window": idle_ticks / os.sysconf("SC_CLK_TCK"),
            "window_seconds": round(time.monotonic() - idle_started, 1),
        }

        (first_uid,) = server.deliver(make_message(3, sender="Grace Hopper <grace@example.test>",
                                                   subject="Compiler notes"))
        notify = self.wait(lambda: [entry for entry in self.records("Notify") if entry["summary"] == "Grace Hopper"])
        self.check("new mail during IDLE notifies", bool(notify))
        entry = notify[0]
        self.check("notification names subject, app and category",
                   entry["body"] == "Compiler notes" and entry["app_name"] == "Charlie"
                   and entry["hints"].get("desktop-entry") == "org.projectluma.Charlie"
                   and entry["hints"].get("category") == "email.arrived"
                   and entry["actions"] == ["default", "Open", "mark-read", "Mark Read"], entry)
        self.check("published unread count follows", bool(self.wait(lambda: self.published().get("unread-count") == 3)))
        stored = self.store.messages_by_ids((f"{ACCOUNT_ID}:inbox:{first_uid}",))
        self.check("new message upserted into the store", bool(stored) and stored[0].unread)

        second = self.spawn(agent_command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        second_started = time.monotonic()
        try:
            code = second.wait(10)
        except subprocess.TimeoutExpired:
            second.kill()
            code = None
        self.summary["second_instance"] = {"exit_code": code, "seconds": round(time.monotonic() - second_started, 2)}
        self.check("second start exits at once and leaves the running agent",
                   code is not None and self.summary["second_instance"]["seconds"] < 5 and self.owner_pid(AGENT) == pid,
                   self.summary["second_instance"])

        autostart = self.spawn([*agent_command, "--autostart"], cwd=ROOT, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        autostart_started = time.monotonic()
        try:
            code = autostart.wait(10)
        except subprocess.TimeoutExpired:
            autostart.kill()
            code = None
        self.summary["autostart_with_service"] = {"exit_code": code,
                                                  "seconds": round(time.monotonic() - autostart_started, 2)}
        self.check("--autostart exits 0 when luma-background is present",
                   code == 0 and self.owner_pid(AGENT) == pid, self.summary["autostart_with_service"])

        try:
            self.call(AGENT, "/org/projectluma/BackgroundAgent1", "org.projectluma.BackgroundAgent1", "Wake",
                      GLib.Variant("(sa{sv})", ("network", {})))
            refused = ""
        except GLib.Error as error:
            refused = error.message
        self.check("Wake from a peer that is not luma-background is refused", "NotAuthorized" in refused, refused)
        logins = server.logins
        answer = self.call(STUB_BUS, STUB_PATH, "org.projectluma.CharlieSmoke.Stub", "SendWake",
                           GLib.Variant("(ss)", (AGENT, "network"))).unpack()[0]
        self.check("network Wake from luma-background is accepted and reconnects",
                   answer == "" and bool(server.wait_for(lambda: server.logins > logins and bool(server.idling), 15)),
                   {"answer": answer, "logins_before": logins, "logins_after": server.logins})

        (second_uid,) = server.deliver(make_message(4, sender="Katherine Johnson <kj@example.test>",
                                                    subject="Trajectory"))
        notify = self.wait(lambda: [entry for entry in self.records("Notify") if entry["summary"] == "Katherine Johnson"])
        self.check("second message notifies separately", bool(notify) and notify[0]["id"] != entry["id"])
        self.call(STUB_BUS, STUB_PATH, "org.projectluma.CharlieSmoke.Stub", "EmitAction",
                  GLib.Variant("(us)", (notify[0]["id"], "mark-read")))
        self.check("mark-read sets \\Seen on the server",
                   bool(server.wait_for(lambda: "\\Seen" in server.flags(second_uid), 15)))
        self.check("mark-read withdraws the notification",
                   bool(self.wait(lambda: any(item["id"] == notify[0]["id"] for item in self.records("CloseNotification")))))
        self.check("mark-read updates the store", bool(self.wait(
            lambda: not self.store.messages_by_ids((f"{ACCOUNT_ID}:inbox:{second_uid}",))[0].unread)))
        self.check("unread count drops after mark-read", bool(self.wait(lambda: self.published().get("unread-count") == 3)))

        self.call(STUB_BUS, STUB_PATH, "org.projectluma.CharlieSmoke.Stub", "EmitAction",
                  GLib.Variant("(us)", (entry["id"], "default")))
        opened = self.wait(lambda: self.records("ActivateAction"))
        self.check("default action asks Charlie to open the exact message",
                   bool(opened) and opened[0]["arguments"][0] == "open-message"
                   and opened[0]["arguments"][1] == [f"{ACCOUNT_ID}:inbox:{first_uid}"]
                   and opened[0]["arguments"][2].get("activation-token") == "smoke-token", opened)

        logins = server.logins
        self.call(AGENT, "/org/projectluma/Charlie/MailAgent", "org.projectluma.Charlie.MailAgent1", "CheckNow")
        self.check("CheckNow reconnects and checks", bool(server.wait_for(lambda: server.logins > logins
                                                                                 and bool(server.idling), 15)),
                   {"before": logins, "after": server.logins})

        server.deliver(*(make_message(10 + index, sender=f"Sender {index} <s{index}@example.test>")
                         for index in range(25)))
        grouped = self.wait(lambda: [item for item in self.records("Notify") if item["summary"] == "25 new messages"], 30)
        self.check("25 new messages are grouped", bool(grouped) and grouped[0]["body"] == "Sender 0, Sender 1, Sender 2",
                   grouped[:1])
        self.check("unread count after 25", bool(self.wait(lambda: self.published().get("unread-count") == 28)))
        time.sleep(2)
        self.summary["after_25_messages"] = {"memory": self.memory(pid)}

        stop_started = time.monotonic()
        agent.send_signal(signal.SIGTERM)
        try:
            code = agent.wait(10)
        except subprocess.TimeoutExpired:
            agent.kill()
            code = None
        self.summary["sigterm"] = {"exit_code": code, "seconds": round(time.monotonic() - stop_started, 2)}
        self.check("SIGTERM stops the agent within 5 s", code == 0 and self.summary["sigterm"]["seconds"] < 5,
                   self.summary["sigterm"])
        log = (self.root / "agent.log").read_text(errors="replace")
        leaked = [needle for needle in (ADDRESS, "grace@example.test", "Grace", "Compiler", "Trajectory", PASSWORD)
                  if needle in log]
        self.check("agent log carries no addresses, names, subjects or secrets", not leaked, leaked)
        self.summary["agent_log_lines"] = len(log.splitlines())
        self.summary["notifications_posted"] = len(self.records("Notify"))
        self.summary["seconds"] = round(time.monotonic() - started, 1)
        server.close()
        return self.summary

    def close(self) -> None:
        for process in reversed(self.children):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
        if getattr(self, "store", None) is not None:
            self.store.close()
        self.temporary.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--notification-stub", metavar="RECORD")
    parser.add_argument("--keyring-root", help="root of an extracted gnome-keyring (and gcr3) payload")
    parser.add_argument("--agent-command", help="command that starts the agent, e.g. an installed "
                        "'/usr/bin/org.projectluma.Charlie --agent' (default: python3 -m charlie_luma --agent)")
    parser.add_argument("--pythonpath", help="PYTHONPATH for the agent (default: this checkout)")
    parser.add_argument("--expect-kit", action="store_true",
                        help="require the agent to run on luma_appkit.background (found on --pythonpath)")
    arguments = parser.parse_args()
    import gi

    gi.require_version("Gio", "2.0")
    gi.require_version("Secret", "1")
    if arguments.notification_stub:
        return run_stub(Path(arguments.notification_stub))
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        print(json.dumps({"ok": False, "error": "run inside dbus-run-session"}))
        return 2
    smoke = Smoke(arguments)
    try:
        summary = smoke.run()
        summary["ok"] = True
    except Exception as error:  # report what failed as JSON
        summary = dict(smoke.summary, ok=False, error=f"{type(error).__name__}: {error}")
        try:
            summary["agent_log_tail"] = (smoke.root / "agent.log").read_text(errors="replace").splitlines()[-20:]
        except OSError:
            pass
    finally:
        smoke.close()
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
