#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""The playing list (org.projectluma.AudioDevices1 version 3) in a headless
session: one row per app, a row leaving ten seconds after the app's last
stream, volume and output coming back with the app, an output of an app's own
unplugged and plugged back in, an app with and without a media player, and
the other sounds.

Runs against real pipewire, wireplumber (luma/app-audio.lua), pipewire-pulse
and the installed service, like scenario_mixer.py."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenario_outputs import (  # noqa: E402
    DAC, RESULTS, SPEAKER, Service, add, check, default, dump, remove, run, wait_for,
)
from scenario_mixer import applications, call, linked_to, stop, stream_volume, tone  # noqa: E402

HERE = Path(__file__).resolve().parent


def outputs() -> dict[str, dict]:
    (rows,) = call("GetOutputs", None, "(aa{sv})")
    return {row["name"]: row for row in rows}


def row(name: str) -> dict:
    return applications().get(name, {})


def where(stream_name: str) -> str:
    """Which outputs an app's streams are linked to, and the default: for a failed check's line."""
    graph = dump()
    nodes = {o["id"]: (o.get("info") or {}).get("props") or {} for o in graph
             if o["type"] == "PipeWire:Interface:Node"}
    sources = {i for i, p in nodes.items() if p.get("application.name") == stream_name}
    sinks = sorted({nodes.get((o.get("info") or {}).get("input-node-id"), {}).get("node.name", "?")
                    for o in graph if o["type"] == "PipeWire:Interface:Link"
                    and (o.get("info") or {}).get("output-node-id") in sources})
    return f"{stream_name} on {sinks or 'nothing'}, default {default()}"


def near(value, target: float, tolerance: float = 0.01) -> bool:
    return isinstance(value, (int, float)) and abs(value - target) < tolerance


def main() -> int:
    add(SPEAKER)
    add(DAC)
    run("pw-metadata", "-n", "default", "0", "default.configured.audio.sink",
        json.dumps({"name": SPEAKER[0]}), "Spa:String:JSON")
    service = Service()
    service.start()
    first = tone("PlayerA", 440)
    second = tone("PlayerA", 660)
    quiet = tone("PlayerB", 880)
    player = None
    try:
        # One row per app.
        rows = wait_for(lambda: (lambda r: r if len(r.get("PlayerA", {}).get("streams", ())) == 2
                                 and "PlayerB" in r else None)(applications()), 10)
        check(bool(rows), "two streams of one app are one row")
        listed = [key for key, entry in applications().items() if entry["listed"]]
        check(sorted(listed) == ["PlayerA", "PlayerB"], f"the playing list holds only apps with a stream ({listed})")
        others = [key for key, entry in applications().items() if entry["other"]]
        check(others == ["@system", "org.projectluma.Messages", "org.projectluma.Phone"],
              f"the other sounds are present while silent ({others})")
        check(not any(applications()[key]["listed"] for key in others), "a silent other sound is not in the playing list")

        # An app without a media player is its own name, with no transport.
        entry = row("PlayerB")
        check(entry.get("player") == "" and entry.get("title") == "" and not entry.get("can_pause")
              and entry.get("name") == "PlayerB", "an app without a media player keeps its name and has no transport")
        try:
            call("ApplicationPlayPause", GLib.Variant("(s)", ("PlayerB",)))
            refused = False
        except GLib.Error:
            refused = True
        check(refused, "play/pause on an app without a media player is refused")

        # With one, the row takes its title, artist, artwork and transport.
        player = subprocess.Popen([sys.executable, str(HERE / "fake_player.py"), "PlayerA", "God Only Knows",
                                   "The Beach Boys"], stdout=subprocess.PIPE, text=True)
        check(bool(wait_for(lambda: row("PlayerA").get("title") == "God Only Knows", 5)),
              "an app's media player gives its row the title")
        entry = row("PlayerA")
        check(entry.get("artist") == "The Beach Boys" and entry.get("art", "").startswith("file://")
              and entry.get("can_pause") and entry.get("can_go_next") and not entry.get("can_go_previous"),
              "and the artist, the artwork and what the player can do")
        check(row("PlayerB").get("title") == "", "another app's player does not name this one")
        call("ApplicationPlayPause", GLib.Variant("(s)", ("PlayerA",)))
        check(bool(wait_for(lambda: row("PlayerA").get("status") == "paused" and not row("PlayerA").get("playing"), 5)),
              "play/pause reaches the player; a paused player's row is not playing")
        check(bool(wait_for(lambda: row("PlayerA").get("position") == 842_000_000, 3)),
              "a paused row says where it stopped")
        call("ApplicationPlayPause", GLib.Variant("(s)", ("PlayerA",)))
        wait_for(lambda: row("PlayerA").get("playing"), 5)

        # Volume and output come back with the app.
        call("SetApplicationVolume", GLib.Variant("(sd)", ("PlayerA", 0.5)))
        call("SetApplicationOutput", GLib.Variant("(ss)", ("PlayerA", DAC[0])))
        check(bool(wait_for(lambda: linked_to("PlayerA", DAC[0]) and near(stream_volume("PlayerA")[0], 0.125), 5)),
              "PlayerA is at 50% on the USB DAC")
        stop(first)
        stop(second)
        gone = time.monotonic()
        check(bool(wait_for(lambda: row("PlayerA").get("streams") == [], 3)) and row("PlayerA").get("listed") is True,
              "its row stays when its last stream closes")
        shown = row("PlayerA")
        check(near(shown.get("volume"), 0.5), f"and keeps showing its volume ({shown.get('volume')}, muted {shown.get('muted')})")
        time.sleep(max(0.0, 8.5 - (time.monotonic() - gone)))
        check(row("PlayerA").get("listed") is True, "the row is still there after 8.5 seconds")
        check(bool(wait_for(lambda: "PlayerA" not in applications(), 3.5)),
              f"and is removed ten seconds after the last stream ({time.monotonic() - gone:.1f} s)")
        check(time.monotonic() - gone >= 9.9, "not before ten seconds")
        first = tone("PlayerA", 440)
        check(bool(wait_for(lambda: near(stream_volume("PlayerA")[0], 0.125), 5)),
              "after a restart the app is at 50% again")
        check(bool(wait_for(lambda: linked_to("PlayerA", DAC[0]), 5)), "and on the USB DAC again")
        check(bool(wait_for(lambda: row("PlayerA").get("output") == DAC[0], 3)), "GetApplications reports its output")

        # Between two tracks the row never leaves.
        stop(first)
        seen = []
        deadline = time.monotonic() + 2.0
        first = None
        while time.monotonic() < deadline:
            seen.append("PlayerA" in applications())
            if first is None and time.monotonic() > deadline - 1.2:
                first = tone("PlayerA", 440)
            time.sleep(0.1)
        check(all(seen), "the row does not leave between two tracks")
        wait_for(lambda: linked_to("PlayerA", DAC[0]), 5)

        # Its output unplugged: it plays on the main output and keeps its choice.
        remove(DAC)
        check(bool(wait_for(lambda: linked_to("PlayerA", SPEAKER[0]), 5)),
              f"with its output unplugged the app plays on the main output ({where('PlayerA')})")
        check(bool(wait_for(lambda: row("PlayerA").get("output") == "" and
                            row("PlayerA").get("playing_on") == SPEAKER[0], 3)),
              "and reads as following the main output")
        check(near(stream_volume("PlayerA")[0], 0.125), "at its own volume")
        add(DAC)
        check(bool(wait_for(lambda: linked_to("PlayerA", DAC[0]), 6)),
              "plugged back in, the app returns to its output")
        check(linked_to("PlayerB", SPEAKER[0]) and not linked_to("PlayerB", DAC[0]),
              f"an app that follows the main output stays on it ({where('PlayerB')})")
        check(bool(wait_for(lambda: row("PlayerA").get("output") == DAC[0], 3)), "and reads as on its own output again")
        # Started while its output is away, it still returns when it comes back.
        remove(DAC)
        stop(first)
        first = tone("PlayerA", 440)
        check(bool(wait_for(lambda: linked_to("PlayerA", SPEAKER[0]), 5)),
              f"started while its output is away, the app plays on the main output ({where('PlayerA')})")
        add(DAC)
        check(bool(wait_for(lambda: linked_to("PlayerA", DAC[0]), 6)), "and moves to its output when it appears")
        # Set back to the main output, it no longer follows the DAC.
        call("SetApplicationOutput", GLib.Variant("(ss)", ("PlayerA", "")))
        check(bool(wait_for(lambda: linked_to("PlayerA", SPEAKER[0]), 5)),
              f"set back, it follows the main output ({where('PlayerA')})")
        remove(DAC)
        add(DAC)
        time.sleep(1.5)
        check(linked_to("PlayerA", SPEAKER[0]) and not linked_to("PlayerA", DAC[0]),
              "and stays there when the other output is plugged in again")

        # Other sounds hold their level while silent.
        call("SetApplicationVolume", GLib.Variant("(sd)", ("org.projectluma.Messages", 0.9)))
        check(bool(wait_for(lambda: near(row("org.projectluma.Messages").get("volume"), 0.9), 5)),
              "an other sound's level is kept while it is silent")
        service.stop()
        service.start()
        check(bool(wait_for(lambda: near(row("org.projectluma.Messages").get("volume"), 0.9), 5)),
              "and read back after the service restarts")

        # What outputs are called.
        named = outputs()
        check(named[SPEAKER[0]].get("label") == "Speakers" and named[SPEAKER[0]].get("kind") == "speakers",
              f"the built-in output is Speakers ({named[SPEAKER[0]].get('label')}, {named[SPEAKER[0]].get('detail')})")
        check(not any(word in named[DAC[0]].get("label", "Analog") for word in ("Analog", "USB Audio"))
              and named[DAC[0]].get("kind") == "usb" and named[DAC[0]].get("detail", "").startswith("USB"),
              f"a USB output is named by its product ({named[DAC[0]].get('label')}, {named[DAC[0]].get('detail')})")
        check(named[SPEAKER[0]].get("last_used", 0) > 0, "the output in use has a last-used time")
    finally:
        for process in (first, second, quiet, player):
            if process is not None and process.poll() is None:
                process.kill()
        service.stop()
        remove(DAC)

    failed = [text for ok, text in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
