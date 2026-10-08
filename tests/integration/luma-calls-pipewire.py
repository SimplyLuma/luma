#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Luma Calls against a real PipeWire, WirePlumber and pipewire-pulse.

Run inside a disposable Fedora container prepared by
``luma-calls-pipewire.sh``: a private session bus and PipeWire session, a
sine-wave "microphone" (``virtmic``) and a ``speakers`` null sink whose monitor
is recorded to measure what actually plays.  The producer is the real one --
real ``pw-dump --monitor``, real ``pw-cli`` writes -- with only the Semantic
Broker replaced by a recorder of what it would publish.

Every claim is measured from audio, not read from state: a muted microphone
records silence, a deafened call plays silence, and the published controls
match what the graph says.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "src/luma-platform/broker", ROOT / "src/luma-platform/calls"):
    sys.path.insert(0, str(path))
os.environ.setdefault(
    "LUMA_SEMANTIC_BROKER_XML",
    str(ROOT / "src/luma-platform/broker/org.projectluma.SemanticBroker1.xml"),
)

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from luma_calls import service  # noqa: E402

RUN = Path(os.environ.get("CALLS_TEST_RUN", "/run/pwtest"))
PAGE = Path(__file__).with_name("luma-calls-webrtc-loopback.html")
RESULTS: list[tuple[str, bool, str]] = []


class RecordingPublisher:
    """Stands in for the Semantic Broker: keeps the last published payload."""

    def __init__(self, _application, _app_id, _extension_id, build) -> None:
        self.build = build
        self.payload = None
        self.publication_id = ""
        self.provider_path = "/org/projectluma/LiveExtensionProvider/test"

    def sync(self) -> None:
        self.payload = self.build()
        self.publication_id = "test" if self.payload else ""

    def close(self) -> None:
        pass


service.ContinuitySource.attach = staticmethod(lambda *_args: None)
REAL_PUBLISHER = service.LiveExtensionPublisher


def rms(path: Path, seconds: float = 0.4) -> float:
    try:
        size = path.stat().st_size
    except OSError:
        return -1.0
    count = int(48000 * seconds)
    with path.open("rb") as handle:
        handle.seek(max(0, size - count * 2))
        data = handle.read(count * 2)
    samples = struct.unpack("<%dh" % (len(data) // 2), data[: len(data) // 2 * 2])
    return math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))


def page_rms() -> float:
    try:
        lines = (RUN / "chromium.log").read_text(errors="replace").splitlines()
    except OSError:
        return -1.0
    values = [float(m.group(1)) for line in lines if (m := re.search(r"local-rms ([0-9.]+)", line))]
    return values[-1] if values else -1.0


def spawn(argv: list[str], output: Path | None = None) -> subprocess.Popen:
    handle = output.open("wb") if output else subprocess.DEVNULL
    return subprocess.Popen(argv, stdout=handle, stderr=subprocess.DEVNULL, start_new_session=True)


def record(binary: str, device: str, output: Path) -> subprocess.Popen:
    return spawn([binary, "--record", "-d", device, "--raw", "--format=s16le", "--rate=48000",
                  "--channels=1", "--latency-msec=30"], output)


def play_loop(binary: str, device: str) -> subprocess.Popen:
    """One long-lived playback stream, as a call's far side is."""
    script = (f"while true; do cat {RUN}/sine.raw; done | {binary} --playback -d {device} "
              f"--raw --format=s16le --rate=48000 --channels=1 --latency-msec=30")
    return spawn(["bash", "-c", script])


def stop(process: subprocess.Popen | None) -> None:
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)


def remembered(key: str) -> dict:
    path = Path.home() / ".local/state/wireplumber/stream-properties"
    try:
        for line in path.read_text().splitlines():
            if line.startswith(key + "="):
                return json.loads(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return {}


def source_output(binary: str) -> int:
    listing = json.loads(subprocess.run(["pactl", "-f", "json", "list", "source-outputs"],
                                        capture_output=True, check=True).stdout)
    return [item["index"] for item in listing
            if item["properties"].get("application.process.binary") == binary][-1]


class Scenario:
    def __init__(self, producer: service.CallsProducer, loop: GLib.MainLoop) -> None:
        self.producer = producer
        self.loop = loop
        self.processes: list[subprocess.Popen] = []

    # -- helpers ---------------------------------------------------------

    def pump(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        context = self.loop.get_context()
        while time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)

    def wait_for(self, what: str, predicate, timeout: float = 12.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(0.1)
            if predicate():
                return True
        return False

    def actions(self) -> set[str]:
        payload = self.producer.publisher.payload or {}
        return {action["id"] for action in payload.get("actions", [])}

    def invoke(self, action_id: str) -> None:
        self.producer._invoke(action_id)
        self.producer._refresh_after_action()

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        RESULTS.append((name, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)

    # -- scenarios ---------------------------------------------------------

    def native_app(self) -> None:
        mic = RUN / "fake-mic.raw"
        speakers = RUN / "speakers.raw"
        capture = record("fakecall", "virtmic", mic)
        playback = play_loop("fakecall", "speakers")
        meter = record("meter", "speakers.monitor", speakers)
        self.processes += [capture, playback, meter]
        published = self.wait_for("call", lambda: self.producer.call is not None and "call.mute" in self.actions())
        self.check("an app with capture and playback is a call with mute and deafen",
                   published and self.actions() == {"call.mute", "call.deafen"}, str(sorted(self.actions())))
        self.pump(1.5)
        self.check("microphone audible before mute", rms(mic) > 1000, f"rms={rms(mic):.0f}")

        self.invoke("call.mute")
        self.wait_for("muted", lambda: "call.unmute" in self.actions(), 5)
        self.pump(1.5)
        self.check("mute silences the app's microphone stream", rms(mic) == 0, f"rms={rms(mic):.0f}")
        self.check("the island is told it is muted", "call.unmute" in self.actions())
        self.check("speakers untouched by mute", rms(speakers) > 1000, f"rms={rms(speakers):.0f}")

        # The application reopens its microphone mid-call.
        stop(capture)
        mic2 = RUN / "fake-mic-2.raw"
        capture = record("fakecall", "virtmic", mic2)
        self.processes.append(capture)
        self.pump(4.0)
        self.check("a reopened microphone stream stays muted", rms(mic2) == 0, f"rms={rms(mic2):.0f}")

        # Someone unmutes it in the volume panel.
        subprocess.run(["pactl", "set-source-output-mute", str(source_output("fakecall")), "0"], check=True)
        truthful = self.wait_for("unmuted elsewhere", lambda: "call.mute" in self.actions(), 5)
        self.pump(1.5)
        self.check("an unmute made elsewhere is shown and not fought", truthful and rms(mic2) > 1000,
                   f"rms={rms(mic2):.0f}")

        self.invoke("call.deafen")
        self.wait_for("deafened", lambda: "call.undeafen" in self.actions(), 5)
        self.pump(1.5)
        self.check("deafen silences what the app plays", rms(speakers, 0.3) == 0,
                   f"rms={rms(speakers, 0.3):.0f}")
        # The far side's playback stream is replaced mid-call.
        stop(playback)
        playback = play_loop("fakecall", "speakers")
        self.processes.append(playback)
        self.pump(3.0)
        self.check("a replaced playback stream stays deafened", rms(speakers, 0.3) == 0 and "call.undeafen" in self.actions(),
                   f"rms={rms(speakers, 0.3):.0f}")
        self.check("deafen also mutes the microphone", rms(mic2) == 0 and "call.unmute" in self.actions(),
                   f"rms={rms(mic2):.0f}")
        self.invoke("call.undeafen")
        self.wait_for("undeafened", lambda: "call.deafen" in self.actions(), 5)
        self.pump(3.0)
        self.check("undeafen restores sound and the microphone", rms(speakers, 0.3) > 1000 and rms(mic2) > 1000,
                   f"speakers={rms(speakers, 0.3):.0f} mic={rms(mic2):.0f}")

        # Mute, then the call ends while muted.
        self.invoke("call.mute")
        self.wait_for("muted", lambda: "call.unmute" in self.actions(), 5)
        self.wait_for("remembered", lambda: remembered("Input/Audio:application.name:fakecall").get("mute") is True, 6)
        self.check("WirePlumber remembers Luma's mute (the case the undo exists for)", remembered("Input/Audio:application.name:fakecall").get("mute") is True)
        stop(capture)
        stop(playback)
        self.wait_for("ended", lambda: self.producer.call is None, 8)
        self.pump(self.producer.audio.grace + 1.5)
        # The next call from the same application must not start muted.
        mic3 = RUN / "fake-mic-3.raw"
        capture = record("fakecall", "virtmic", mic3)
        self.processes.append(capture)
        self.pump(4.0)
        self.check("the next call does not start muted", rms(mic3) > 1000,
                   f"rms={rms(mic3):.0f} ledger={sorted(self.producer.audio.ledger.entries)}")
        self.check("WirePlumber's memory is restored",
                   self.wait_for("forgotten", lambda: remembered("Input/Audio:application.name:fakecall").get("mute") is False, 6))
        self.check("the undo ledger is empty", self.wait_for("ledger", lambda: not self.producer.audio.ledger.entries, 5))
        stop(capture)
        stop(meter)
        self.wait_for("idle", lambda: self.producer.call is None, 8)

    def chromium(self) -> None:
        log = RUN / "chromium.log"
        speakers = RUN / "speakers-chromium.raw"
        meter = record("meter", "speakers.monitor", speakers)
        # Chromium logs the page's console to stderr.
        with log.open("wb") as handle:
            browser = subprocess.Popen(
                ["chromium-browser", "--headless=new", "--no-sandbox", "--use-fake-ui-for-media-stream",
                 "--autoplay-policy=no-user-gesture-required", "--enable-logging=stderr",
                 f"--user-data-dir={RUN}/chromium-profile", PAGE.as_uri()],
                stdout=handle, stderr=handle, start_new_session=True)
        self.processes += [meter, browser]
        published = self.wait_for("chromium call", lambda: self.producer.call is not None
                                  and self.producer.call.group.startswith("chrom"), 25)
        self.check("a Chromium WebRTC call is detected", published,
                   self.producer.call.group if self.producer.call else "none")
        if not published:
            return
        self.check("Chromium offers mute and deafen and no hang-up",
                   self.actions() == {"call.mute", "call.deafen"}, str(sorted(self.actions())))
        self.pump(2.0)
        self.check("the page hears the microphone", page_rms() > 0.05, f"page={page_rms():.3f}")
        self.check("the far side plays", rms(speakers) > 500, f"rms={rms(speakers):.0f}")
        self.invoke("call.mute")
        self.pump(2.5)
        self.check("mute: the WebRTC capture track goes silent", page_rms() < 0.001, f"page={page_rms():.4f}")
        self.check("mute: nothing reaches the far side", rms(speakers) < 50, f"rms={rms(speakers):.0f}")
        self.invoke("call.unmute")
        self.pump(2.5)
        self.check("unmute: the page hears the microphone again", page_rms() > 0.05, f"page={page_rms():.3f}")
        self.invoke("call.deafen")
        self.pump(2.5)
        self.check("deafen: Chromium plays silence", rms(speakers) == 0, f"rms={rms(speakers):.0f}")
        self.check("deafen: the island says so", "call.undeafen" in self.actions() and "call.unmute" in self.actions())
        self.invoke("call.undeafen")
        self.pump(2.5)
        self.check("undeafen: Chromium plays again", rms(speakers) > 500, f"rms={rms(speakers):.0f}")

    def broker_dbus(self) -> None:
        """The whole chain the Shell uses, over D-Bus, with the real broker.

        Only identity is simulated (a container has no systemd scopes and no
        /usr/bin/gnome-shell); routing, policy, request objects, the provider
        call and the mute itself are all real.
        """
        mic = RUN / "broker-mic.raw"
        capture = record("fakecall", "virtmic", mic)
        playback = play_loop("fakecall", "speakers")
        self.processes += [capture, playback]
        shell = self.shell
        published = self.wait_for("published", lambda: self._list_live(shell), 15)
        self.check("the broker lists the call to the Shell", bool(published))
        if not published:
            return
        item = self._list_live(shell)[0]
        ids = [action["id"] for action in item["extension"]["actions"]]
        self.check("the broker carries mute and deafen, no hang-up", sorted(ids) == ["call.deafen", "call.mute"], str(ids))
        self.pump(1.5)
        self.check("microphone audible", rms(mic) > 1000, f"rms={rms(mic):.0f}")

        def invoke(options):
            return shell.call_sync(
                BROKER_NAME, BROKER_PATH, BROKER_NAME, "InvokeAction",
                GLib.Variant("(sssva{sv})", (item["publication_id"], item["extension"]["id"], "call.mute",
                                             GLib.Variant("a{sv}", {}), options)),
                GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 3000, None)

        # Luma Shell .89 and earlier sent no handle token.
        try:
            invoke({})
            refused = ""
        except GLib.Error as error:
            refused = error.message
        self.check("a request without a handle token is refused (why every island control was inert)",
                   "handle token" in refused, refused.split(":")[-1].strip())
        self.pump(1.0)
        self.check("and nothing was muted", rms(mic) > 1000, f"rms={rms(mic):.0f}")

        # What Shell .90 does: subscribe to the request's Response, then ask.
        responses: list[int] = []
        token = "luma_live_1"
        path = f"/org/projectluma/SemanticBroker1/request/{shell.get_unique_name()[1:].replace('.', '_')}/{token}"
        subscription = shell.signal_subscribe(
            BROKER_NAME, "org.projectluma.SemanticRequest1", "Response", path, None,
            Gio.DBusSignalFlags.NONE, lambda *args: responses.append(args[5].unpack()[0]))
        invoke({"handle_token": GLib.Variant("s", token)})
        answered = self.wait_for("response", lambda: responses, 10)
        shell.signal_unsubscribe(subscription)
        self.check("the broker answers the request with success", answered and responses[0] == 0, str(responses))
        self.pump(1.5)
        self.check("the microphone is muted through the whole chain", rms(mic) == 0, f"rms={rms(mic):.0f}")

        def published_ids():
            found = self._list_live(shell)
            return [a["id"] for a in found[0]["extension"]["actions"]] if found else []

        self.check("the Shell is told it is muted", self.wait_for("republished", lambda: "call.unmute" in published_ids(), 5))

    def _list_live(self, connection) -> list:
        try:
            reply = connection.call_sync(BROKER_NAME, BROKER_PATH, BROKER_NAME, "ListLiveExtensions", None,
                                         GLib.VariantType("(aa{sv})"), Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            return []
        return [item for item in reply.unpack()[0] if item.get("application_id") == "org.projectluma.Calls"]

    def close(self) -> None:
        for process in self.processes:
            stop(process)


BROKER_NAME = "org.projectluma.SemanticBroker1"
BROKER_PATH = "/org/projectluma/SemanticBroker1"


def start_broker():
    """The real SemanticBrokerService in its own process, identity simulated.

    It must be a separate process: a synchronous call from this process to a
    broker dispatched on this process's own main loop could never be answered.
    """
    address = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    shell = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    broker = subprocess.Popen([sys.executable, __file__, "--broker", shell.get_unique_name()],
                              stderr=(RUN / "broker.log").open("wb"), start_new_session=True)
    for _ in range(100):
        owner = shell.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "NameHasOwner", GLib.Variant("(s)", (BROKER_NAME,)), GLib.VariantType("(b)"),
                                Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        if owner:
            break
        time.sleep(0.1)
    return broker, shell


def run_broker(shell_name: str) -> int:
    from luma_semantic_broker.core import BrokerCore
    from luma_semantic_broker.identity import IdentityError
    from luma_semantic_broker.model import Identity, IdentityStrength
    from luma_semantic_broker.service import SemanticBrokerService

    class Grants:
        def load(self):
            return ()

        def save(self, _grants):
            return None

    class Audit:
        def append(self, _event):
            return None

        def tail(self, _limit):
            return ()

    class SimulatedIdentity:
        def resolve(self, sender):
            app_id = "" if sender == shell_name else "org.projectluma.Calls"
            return Identity(f"native:{sender}", app_id, "test", sender, os.getuid(), 1, IdentityStrength.MANAGED_NATIVE)

        def provider(self, sender, claimed):
            identity = self.resolve(sender)
            if identity.app_id != claimed:
                raise IdentityError("provider application identity does not match caller")
            return identity

        def require_shell_host(self, sender):
            if sender != shell_name:
                raise IdentityError("Live Extensions may only be read by the system Shell")
            return Identity(f"transient:{sender}", "", "GNOME Shell", sender, os.getuid(), 2,
                            IdentityStrength.TRANSIENT_NATIVE)

    loop = GLib.MainLoop()
    holder = {}

    def acquired(connection, _name):
        core = BrokerCore(grant_store=Grants(), audit_store=Audit(), locked=lambda: False)
        holder["service"] = SemanticBrokerService(connection, identity=SimulatedIdentity(), core=core)

    Gio.bus_own_name(Gio.BusType.SESSION, BROKER_NAME, Gio.BusNameOwnerFlags.NONE, acquired, None,
                     lambda *_args: loop.quit())
    loop.run()
    return 0


def main() -> int:
    import logging
    logging.basicConfig(level=logging.INFO, format="  luma-calls: %(message)s")
    application = Gio.Application(application_id="org.projectluma.CallsTest", flags=Gio.ApplicationFlags.IS_SERVICE)
    application.register(None)
    loop = GLib.MainLoop()
    ledger = RUN / "audio-undo.json"
    ledger.unlink(missing_ok=True)
    only = sys.argv[1:] or ["native_app", "chromium"]
    shell = None
    if only == ["broker_dbus"]:
        broker, shell = start_broker()
        service.LiveExtensionPublisher = REAL_PUBLISHER
    else:
        service.LiveExtensionPublisher = RecordingPublisher
    producer = service.CallsProducer(application, ledger_path=ledger)
    producer.audio.grace = 3.0
    scenario = Scenario(producer, loop)
    scenario.shell = shell
    try:
        for name in only:
            getattr(scenario, name)()
    finally:
        scenario.close()
        if shell is not None:
            stop(broker)
        producer.close()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"luma-calls-pipewire: {len(RESULTS) - len(failed)} passed, {len(failed)} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--broker"]:
        raise SystemExit(run_broker(sys.argv[2]))
    raise SystemExit(main())
