#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""End-to-end opt-in AirPlay scenarios against real receivers.

The receivers run in a second container on the same bridge network
(airplay-receivers.sh): shairport-sync with no password ("Luma Test Speaker"),
shairport-sync with a password ("Luma Locked Speaker"), and an RTSP responder
that accepts the connection check but refuses the stream ("Luma Refusing
Speaker"). Steps that need the receiver side to change (a receiver leaving the
network, audio arriving) are handed to the host through step files; see
run-airplay-e2e.sh.

Prints one line per check and exits non-zero if any check failed.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import wave

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenario_outputs as common  # noqa: E402

check, wait_for, default, choose = common.check, common.wait_for, common.default, common.choose
STEPS = Path(os.environ.get("AIRPLAY_STEP_DIR", "/tmp/airplay-steps"))
BUS_NAME, PATH, INTERFACE = "org.projectluma.AudioDevices", "/org/projectluma/AudioDevices", "org.projectluma.AudioDevices1"

OPEN_NAME, LOCKED_NAME, REFUSING_NAME = "Luma Test Speaker", "Luma Locked Speaker", "Luma Refusing Speaker"


def bus():
    return common.session_bus()


def call(method, parameters=None, reply="()", timeout=30000):
    result = bus().call_sync(BUS_NAME, PATH, INTERFACE, method, parameters, GLib.VariantType.new(reply),
                             Gio.DBusCallFlags.NONE, timeout, None)
    return result.unpack()


def call_error(method, parameters, timeout=30000) -> tuple[str | None, str]:
    try:
        call(method, parameters, "()", timeout)
        return None, ""
    except GLib.Error as error:
        remote = Gio.DBusError.get_remote_error(error)
        message = error.message
        if remote and message.startswith("GDBus.Error:"):
            message = message.split(": ", 1)[-1]
        return remote, message


def receivers() -> dict[str, dict]:
    (entries,) = call("GetAirPlayReceivers", None, "(aa{sv})")
    return {entry["name"]: entry for entry in entries}


def prop(name):
    value = bus().call_sync(BUS_NAME, PATH, "org.freedesktop.DBus.Properties", "Get",
                            GLib.Variant("(ss)", (INTERFACE, name)), GLib.VariantType.new("(v)"),
                            Gio.DBusCallFlags.NONE, 5000, None)
    return value.unpack()[0]


def airplay_nodes() -> set[str]:
    names = (((o.get("info") or {}).get("props") or {}).get("node.name", "") for o in common.dump())
    return {name for name in names if name.startswith("luma_airplay.")}


def host_step(name: str, timeout: float = 120) -> str:
    """Ask the host to do something on the receiver side and wait for it."""
    STEPS.mkdir(parents=True, exist_ok=True)
    done = STEPS / f"{name}.done"
    done.unlink(missing_ok=True)
    print(f"STEP {name}", flush=True)
    wait_for(done.exists, timeout, 0.25)
    return done.read_text(encoding="utf-8").strip() if done.exists() else ""


def tone(seconds: float = 3.0) -> Path:
    path = Path(tempfile.mkstemp(suffix=".wav")[1])
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(44100)
        frames = bytearray()
        for i in range(int(44100 * seconds)):
            sample = int(12000 * math.sin(2 * math.pi * 440 * i / 44100))
            frames += struct.pack("<hh", sample, sample)
        out.writeframes(bytes(frames))
    return path


def main() -> int:
    common.remove(common.SPEAKER)
    common.add(common.SPEAKER)
    speaker = common.SPEAKER[0]
    wait_for(lambda: default() == speaker, 5)

    service = common.Service()
    service.start()

    # A. Nothing is looked up or created until the picker opens.
    time.sleep(3)
    check(receivers() == {}, "no receivers are listed before the picker opens")
    check(not airplay_nodes(), "no AirPlay outputs exist before the picker opens")
    check(prop("AirPlayAvailable") is True, "Avahi is available to the service")

    # B. Browsing lists receivers and creates nothing.
    call("StartAirPlayBrowsing")
    check(prop("AirPlayBrowsing") is True, "browsing starts with the picker")
    found = wait_for(lambda: OPEN_NAME in receivers() and LOCKED_NAME in receivers(), 15)
    check(bool(found), f"browsing finds the receivers on the network ({sorted(receivers())})")
    listed = receivers()
    if LOCKED_NAME in listed:
        check(listed[LOCKED_NAME]["requires_password"] is True, "the locked receiver announces its password")
    if OPEN_NAME in listed:
        check(listed[OPEN_NAME]["state"] == "available" and not listed[OPEN_NAME]["remembered"],
              "an unchosen receiver is available, not remembered")
    check(not airplay_nodes(), "browsing alone creates no outputs")

    # C. Choosing the open receiver: one sink, used and remembered; audio arrives.
    open_id = listed.get(OPEN_NAME, {}).get("id", "")
    error, message = call_error("ConnectAirPlayReceiver", GLib.Variant("(ss)", (open_id, "")))
    check(error is None, f"connecting to {OPEN_NAME} succeeds ({error} {message})")
    node = f"luma_airplay.{open_id}"
    check(airplay_nodes() == {node}, f"exactly one AirPlay output exists ({airplay_nodes()})")
    check(wait_for(lambda: default() == node, 5) is True, "the chosen receiver becomes the output")
    entry = receivers().get(OPEN_NAME, {})
    check(entry.get("state") == "connected" and entry.get("remembered") is True, "it is connected and remembered")
    sample = tone(4.0)
    before = host_step("audio-bytes")
    player = subprocess.run(["pw-play", str(sample)], capture_output=True, text=True, timeout=30)
    time.sleep(2)
    after = host_step("audio-bytes")
    try:
        grew = int(after) - int(before)
    except ValueError:
        grew = -1
    check(player.returncode == 0 and grew > 44100 * 4, f"sound reached the receiver ({grew} bytes of PCM)")
    sample.unlink()

    # D. A receiver with a password.
    locked_id = listed.get(LOCKED_NAME, {}).get("id", "")
    error, message = call_error("ConnectAirPlayReceiver", GLib.Variant("(ss)", (locked_id, "")))
    check(error == f"{INTERFACE}.Error.PasswordRequired", f"no password: {error} “{message}”")
    error, message = call_error("ConnectAirPlayReceiver", GLib.Variant("(ss)", (locked_id, "not-it")))
    check(error == f"{INTERFACE}.Error.PasswordIncorrect", f"wrong password: {error} “{message}”")
    check(f"luma_airplay.{locked_id}" not in airplay_nodes(), "no output after a failed password")
    error, message = call_error("ConnectAirPlayReceiver", GLib.Variant("(ss)", (locked_id, "luma-test")))
    check(error is None, f"the right password connects ({error} {message})")
    check(wait_for(lambda: default() == f"luma_airplay.{locked_id}", 5) is True, "and it becomes the output")
    state_file = Path(os.environ["XDG_STATE_HOME"]) / "luma-audio-devices" / "state.json"
    check("luma-test" not in state_file.read_text(encoding="utf-8"), "the password is not in the state file")

    # E. Closing the picker stops browsing; remembered receivers stay listed.
    call("StopAirPlayBrowsing")
    check(prop("AirPlayBrowsing") is False, "browsing stops with the picker")
    check(set(receivers()) == {OPEN_NAME, LOCKED_NAME}, f"only remembered receivers remain ({sorted(receivers())})")

    # F. Back to the speaker; after a restart, remembered outputs return without taking the sound.
    choose(common.SPEAKER)
    check(wait_for(lambda: default() == speaker, 5) is True, "the person goes back to the speaker")
    service.stop()
    check(bool(wait_for(lambda: not airplay_nodes(), 5)), "outputs go away with the service")
    service.start()
    check(bool(wait_for(lambda: node in airplay_nodes(), 20)), "a remembered receiver's output returns by itself")
    time.sleep(2)
    check(default() == speaker, "a returning AirPlay output does not take the sound")
    keyring = os.environ.get("LUMA_TEST_KEYRING") == "1"
    locked_entry = receivers().get(LOCKED_NAME, {})
    if keyring:
        check(bool(wait_for(lambda: f"luma_airplay.{locked_id}" in airplay_nodes(), 20)),
              "the locked receiver returns with the keyring password")
    else:
        check(locked_entry.get("error") == "password-required" and f"luma_airplay.{locked_id}" not in airplay_nodes(),
              "without a keyring the locked receiver waits for its password")

    # G. The receiver leaves the network: its output goes; it comes back when the receiver does.
    host_step("stop-open-receiver")
    check(bool(wait_for(lambda: node not in airplay_nodes(), 60)), "the output leaves with the receiver")
    check(bool(wait_for(lambda: receivers().get(OPEN_NAME, {}).get("state") == "unavailable", 5)),
          f"and the receiver is listed as unavailable ({receivers().get(OPEN_NAME)})")
    host_step("start-open-receiver")
    check(bool(wait_for(lambda: node in airplay_nodes(), 60)), "the output returns with the receiver")
    check(default() == speaker, "still without taking the sound")

    # H. A receiver that accepts the check but refuses the stream.
    call("StartAirPlayBrowsing")
    refusing = wait_for(lambda: receivers().get(REFUSING_NAME), 15)
    check(bool(refusing), "the refusing receiver is found")
    if refusing:
        error, message = call_error("ConnectAirPlayReceiver", GLib.Variant("(ss)", (refusing["id"], "")))
        check(error is None, f"its connection check passes ({error} {message})")
        start = common.mark()
        sample = tone(2.0)
        try:
            # pw-play can wait forever on an output that just vanished.
            subprocess.run(["pw-play", str(sample)], capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            pass
        sample.unlink()
        failure = wait_for(lambda: [e for e in common.events()[start:]
                                    if e["event"] == "notify" and e["summary"].startswith("Can’t play to")], 15)
        check(bool(failure) and REFUSING_NAME in failure[0]["summary"], f"a failure notification is shown ({failure})")
        check(wait_for(lambda: default() == speaker, 10) is True, "sound falls back to the speaker")
        check(receivers().get(REFUSING_NAME, {}).get("error") == "failed", "the receiver shows the failure")
        call("ForgetAirPlayReceiver", GLib.Variant("(s)", (refusing["id"],)))
    call("StopAirPlayBrowsing")

    # I. Forget.
    call("ForgetAirPlayReceiver", GLib.Variant("(s)", (open_id,)))
    check(bool(wait_for(lambda: node not in airplay_nodes(), 10)), "forgetting removes the output")
    check(OPEN_NAME not in receivers(), "and the receiver is no longer listed")
    check(open_id not in state_file.read_text(encoding="utf-8"), "and no longer remembered")
    call("ForgetAirPlayReceiver", GLib.Variant("(s)", (locked_id,)))
    service.stop()

    failed = [text for ok, text in common.RESULTS if not ok]
    print(f"\n{len(common.RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
