#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""End-to-end output policy scenarios in a headless PipeWire session.

Runs against real pipewire, wireplumber (with luma-audio-policy's fragment and
Lua hooks installed) and the installed luma-audio-devices service. Sound
devices are null sinks carrying the properties real ALSA and BlueZ nodes carry
(device.api, device.bus, vendor/product ids, api.bluez5.address, form factor),
which is what both the WirePlumber policy and the service read.

Prints one line per check and exits non-zero if any check failed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

LOG = Path(os.environ.get("FAKE_DESKTOP_LOG", "/tmp/fake-desktop.jsonl"))
SERVICE = os.environ.get("LUMA_AUDIO_DEVICES", "/usr/libexec/luma-audio-devices")
RESULTS: list[tuple[bool, str]] = []
_BUS: Gio.DBusConnection | None = None


def session_bus() -> Gio.DBusConnection:
    """GLib keeps the shared bus only while someone holds it: a connection
    dropped between calls closes, and the service rightly treats the closed
    client as gone (for example, a picker that stopped browsing)."""
    global _BUS
    if _BUS is None:
        _BUS = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return _BUS

SPEAKER = ("alsa_output.pci-0000_00_1f.3.test.HiFi__Speaker__sink", {
    "device.api": "alsa", "device.bus": "pci", "device.bus-path": "pci-0000:00:1f.3-test",
    "card.profile.device": "0", "device.profile.description": "Speaker", "device.icon_name": "audio-speakers",
    "node.description": "Test Series Processors HD Audio Speaker", "priority.session": "712"})
DAC = ("alsa_output.usb-C-Media_USB_Advanced_Audio_Device-00.analog-stereo", {
    "device.api": "alsa", "device.bus": "usb", "device.vendor.id": "0x0d8c", "device.product.id": "0x0014",
    "device.description": "USB Advanced Audio Device", "node.description": "USB Advanced Audio Device Analog Stereo",
    "priority.session": "1500"})
DISPLAY = ("alsa_output.pci-0000_00_1f.3.test.HiFi__HDMI1__sink", {
    "device.api": "alsa", "device.bus": "pci", "device.bus-path": "pci-0000:00:1f.3-test",
    "card.profile.device": "2", "device.icon_name": "video-display", "node.nick": "LG ULTRAWIDE",
    "device.profile.description": "HDMI / DisplayPort 1 Output", "priority.session": "664"})
BUDS = ("bluez_output.00_1B_66_AA_BB_CC.1", {
    "device.api": "bluez5", "api.bluez5.address": "00:1B:66:AA:BB:CC", "device.form-factor": "headphone",
    "device.description": "Test Buds", "priority.session": "1010"})
DOCK = ("alsa_output.usb-Lenovo_Dock_Audio-00.analog-stereo", {
    "device.api": "alsa", "device.bus": "usb", "device.vendor.id": "0x17ef", "device.product.id": "0xa396",
    "device.description": "Dock Audio", "priority.session": "1600"})
SPEAKERBAR = ("alsa_output.usb-Test_Soundbar-00.analog-stereo", {
    "device.api": "alsa", "device.bus": "usb", "device.vendor.id": "0x1234", "device.product.id": "0x5678",
    "device.description": "Soundbar", "priority.session": "1300"})
RAOP = ("raop_sink.Test-Mac.local.192.0.2.9.7000", {
    "node.network": "true", "sess.media": "raop", "node.description": "Test Mac", "priority.session": "5000"})


def check(ok: bool, text: str) -> bool:
    RESULTS.append((ok, text))
    print(("PASS " if ok else "FAIL ") + text, flush=True)
    return ok


def run(*args, check_rc=True, **kwargs) -> str:
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if check_rc and result.returncode != 0:
        raise RuntimeError(f"{args} failed: {result.stderr.strip()}")
    return result.stdout


def dump() -> list:
    return json.loads(run("pw-dump"))


def node_id(name: str) -> int | None:
    for obj in dump():
        if ((obj.get("info") or {}).get("props") or {}).get("node.name") == name:
            return obj["id"]
    return None


def wait_for(predicate, timeout: float, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def add(device) -> None:
    name, props = device
    fields = {"factory.name": "support.null-audio-sink", "node.name": name, "media.class": "Audio/Sink",
              "object.linger": "true", "audio.position": "[ FL FR ]", **props}
    text = " ".join(f"{k}={json.dumps(v)}" if not v.startswith("[") else f"{k}={v}" for k, v in fields.items())
    run("pw-cli", "create-node", "adapter", "{ " + text + " }")
    wait_for(lambda: node_id(name), 5)


def remove(device) -> None:
    oid = node_id(device[0])
    if oid is not None:
        run("pw-cli", "destroy", str(oid))
        wait_for(lambda: node_id(device[0]) is None, 5)


def metadata(key: str) -> str | None:
    out = run("pw-metadata", "-n", "default", "0", key, check_rc=False)
    match = re.search(r"value:'(.*?)' type:", out)
    if not match:
        return None
    try:
        return json.loads(match.group(1)).get("name")
    except ValueError:
        return None


def default() -> str | None:
    return metadata("default.audio.sink")


def choose(device) -> None:
    """What GNOME Settings does when the person picks an output."""
    run("pw-metadata", "-n", "default", "0", "default.configured.audio.sink",
        json.dumps({"name": device[0]}), "Spa:String:JSON")


def events() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def notifications(since: int, app_title_prefix: str = "") -> list[dict]:
    return [e for e in events()[since:] if e["event"] == "notify" and e["summary"].startswith(app_title_prefix)]


def mark() -> int:
    return len(events())


def service_property(name: str):
    bus = session_bus()
    value = bus.call_sync("org.projectluma.AudioDevices", "/org/projectluma/AudioDevices",
                          "org.freedesktop.DBus.Properties", "Get",
                          GLib.Variant("(ss)", ("org.projectluma.AudioDevices1", name)),
                          GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 5000, None)
    return value.unpack()[0]


def output_devices() -> dict[str, dict]:
    bus = session_bus()
    reply = bus.call_sync("org.projectluma.AudioDevices", "/org/projectluma/AudioDevices",
                          "org.projectluma.AudioDevices1", "GetOutputDevices", None,
                          GLib.VariantType.new("(aa{sv})"), Gio.DBusCallFlags.NONE, 5000, None)
    return {entry["key"]: entry for entry in reply.unpack()[0]}


def service_log() -> str:
    path = Path(os.environ.get("LUMA_AUDIO_DEVICES_LOGFILE", "/tmp/luma-audio-devices.log"))
    return path.read_text(encoding="utf-8") if path.exists() else ""


def decided(text: str, since: int) -> bool:
    """Wait on the service's own decision log rather than polling it over
    D-Bus, which in a headless session can starve its PipeWire events."""
    return text in service_log()[since:]


def auto_switch(key: str):
    return output_devices().get(key, {}).get("auto_switch")


def manual_only() -> list[str]:
    out = run("wpctl", "settings", "luma.audio.manual-only-outputs", check_rc=False)
    match = re.search(r"Value: (\[.*?\])", out)
    return json.loads(match.group(1)) if match else []


class Service:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        env = dict(os.environ, GNOTIFICATION_BACKEND="freedesktop", LUMA_AUDIO_DEVICES_STARTUP_GRACE="1.5",
                   PYTHONUNBUFFERED="1")
        self.log = open(os.environ.get("LUMA_AUDIO_DEVICES_LOGFILE", "/tmp/luma-audio-devices.log"), "a")
        self.process = subprocess.Popen([SERVICE, "--gapplication-service"], env=env, stdout=self.log,
                                        stderr=subprocess.STDOUT)
        bus = session_bus()

        def owned() -> bool:
            reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                  "NameHasOwner", GLib.Variant("(s)", ("org.projectluma.AudioDevices",)),
                                  GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
            return reply.unpack()[0]

        check(bool(wait_for(owned, 10)), "service owns org.projectluma.AudioDevices")
        time.sleep(2.0)  # past the startup grace

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            self.process.wait(10)
            self.process = None


def main() -> int:
    """Luma never asks about output devices (luma-audio-policy 1.luma.3)."""
    for device in (SPEAKER, DAC, DISPLAY, BUDS, DOCK, SPEAKERBAR, RAOP):
        remove(device)
    # What 1.luma.1-2 left behind for someone who answered "Don't ask again".
    run("wpctl", "settings", "--save", "luma.audio.manual-only-outputs", '["usb:0x17ef:0xa396"]', check_rc=False)
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "luma-audio-devices/state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"version": 1, "outputs": {"usb:0x17ef:0xa396": {
        "policy": "never", "snoozed_until": 0, "name": "Dock Audio", "icon": "audio-card-symbolic",
        "first_seen": 1, "last_seen": 1}}, "airplay": {}}), encoding="utf-8")
    add(SPEAKER)
    check(wait_for(lambda: default() == SPEAKER[0], 5) is True, "speaker is the default at login")

    service = Service()
    everything = mark()
    service.start()

    # 0. Leftovers of the prompts are cleaned up.
    check(wait_for(lambda: manual_only() == [], 5) == True, "the saved Don't-ask-again list is removed from WirePlumber")
    check(wait_for(lambda: "policy" not in state.read_text(encoding="utf-8"), 5) is True,
          "prompt answers are dropped from state.json")

    # 1. A USB DAC with a higher priority keeps the sound where it is.
    add(DAC)
    time.sleep(4)
    check(default() == SPEAKER[0], "USB DAC with higher priority.session keeps the speaker")
    remove(DAC)

    # 2. A display with speakers (the LG) keeps the sound where it is.
    add(DISPLAY)
    time.sleep(4)
    check(default() == SPEAKER[0], "LG ULTRAWIDE display audio keeps the speaker")

    # 3. A dock (formerly "Don't ask again") keeps the sound too, and can still be chosen.
    add(DOCK)
    time.sleep(4)
    check(default() == SPEAKER[0], "dock audio keeps the speaker")
    choose(DOCK)
    check(wait_for(lambda: default() == DOCK[0], 5) is True, "the person can choose the dock")
    remove(DOCK)
    check(wait_for(lambda: default() == SPEAKER[0], 5) is True, "sound returns to the speaker when the dock leaves")
    add(DOCK)
    check(wait_for(lambda: default() == DOCK[0], 5) is True,
          "a device the person chose is restored when it returns (no retired manual-only filter)")
    choose(SPEAKER)
    check(wait_for(lambda: default() == SPEAKER[0], 5) is True, "choosing the speaker again")
    remove(DOCK)

    # 4. Bluetooth headphones take the sound when connected, and give it back.
    add(BUDS)
    check(wait_for(lambda: default() == BUDS[0], 6) is True, "Bluetooth headphones take the sound when connected")
    remove(BUDS)
    check(wait_for(lambda: default() == SPEAKER[0], 5) is True, "sound returns to the speaker when they leave")
    time.sleep(3)
    check(auto_switch("bluez:00:1B:66:AA:BB:CC") is True, "unplugging is not taken as a manual choice")

    # 5. A manual choice away from them is remembered for that device.
    add(BUDS)
    check(wait_for(lambda: default() == BUDS[0], 6) is True, "headphones take the sound again")
    time.sleep(3)  # a person looks at Quick Settings after connecting, not within the same instant
    before = len(service_log())
    choose(SPEAKER)
    check(wait_for(lambda: default() == SPEAKER[0], 5) is True, "the person moves the sound to the speaker")
    time.sleep(2)
    remove(BUDS)
    check(wait_for(lambda: decided("remembered-choice: Test Buds", before), 5) is True
          and auto_switch("bluez:00:1B:66:AA:BB:CC") is False,
          "moving the sound away while connected is remembered")
    add(BUDS)
    time.sleep(4)
    check(default() == SPEAKER[0], "next time those headphones connect the sound stays")
    before = len(service_log())
    choose(BUDS)
    check(wait_for(lambda: default() == BUDS[0], 5) is True, "the person chooses the headphones")
    time.sleep(2)
    remove(BUDS)
    check(wait_for(lambda: decided("remembered-choice: Test Buds", before), 5) is True
          and auto_switch("bluez:00:1B:66:AA:BB:CC") is True,
          "choosing them again brings switching back")
    check(wait_for(lambda: default() == SPEAKER[0], 5) is True, "unplugged: back to the speaker")

    # 6. Five devices at once: none takes the sound.
    burst = [(f"alsa_output.usb-Burst_{i}-00.analog-stereo",
              {"device.api": "alsa", "device.bus": "usb", "device.vendor.id": f"0x70{i}0",
               "device.product.id": "0x0001", "device.description": f"Burst {i}", "priority.session": str(1000 + i)})
             for i in range(5)]
    for device in burst:
        add(device)
    time.sleep(7)
    check(default() == SPEAKER[0], "five devices at once: none takes the sound")
    for device in burst:
        remove(device)

    # 7. Network outputs never take over and are hidden from menus.
    add(RAOP)
    time.sleep(4)
    check(default() == SPEAKER[0], "discovered AirPlay sink with priority 5000 does not take over")
    check(RAOP[0] in service_property("HiddenOutputs"), "HiddenOutputs lists the unchosen network output")
    remove(RAOP)

    # 8. Switching to headphones can be turned off. (The person chose the
    # headphones last, so WirePlumber would restore them itself: choose the
    # speaker first, as someone who does not want them would have.)
    add(BUDS)
    wait_for(lambda: default() == BUDS[0], 6)
    remove(BUDS)
    choose(SPEAKER)
    run("gsettings", "set", "org.projectluma.audio-devices", "switch-to-headphones", "false")
    time.sleep(1)
    add(BUDS)
    time.sleep(4)
    check(default() == SPEAKER[0], "with switching off, headphones do not take the sound")
    remove(BUDS)
    run("gsettings", "set", "org.projectluma.audio-devices", "switch-to-headphones", "true")
    check("new-device-prompts" not in run("gsettings", "list-keys", "org.projectluma.audio-devices"),
          "the prompt setting is gone")

    # 9. Restart with devices present: nothing moves.
    add(DAC)
    add(SPEAKERBAR)
    service.stop()
    service.start()
    time.sleep(4)
    check(default() == SPEAKER[0], "devices present when the service starts change nothing")
    service.stop()
    for device in (DAC, SPEAKERBAR, DISPLAY):
        remove(device)

    shown = notifications(everything)
    check(shown == [], f"no notification about output devices at any point ({[n['summary'] for n in shown]})")
    log = Path(os.environ.get("LUMA_AUDIO_DEVICES_LOGFILE", "/tmp/luma-audio-devices.log")).read_text(encoding="utf-8")
    check("audio-routing" in log and "sound output switched: Test Buds" in log and "sound output kept: LG ULTRAWIDE" in log,
          "routing decisions are recorded for Vitals")

    failed = [text for ok, text in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
