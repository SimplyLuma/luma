#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Collect local audio routing/mixer evidence without changing audio settings.

No sudo, network access, playback, microphone recording or driver overrides.
The private JSON file is shared only if its owner chooses to share it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess


PROPERTIES = {
    "device.api", "device.name", "device.description", "device.id",
    "api.alsa.card", "api.alsa.card.name", "api.alsa.card.longname",
    "api.alsa.pcm.card", "api.alsa.pcm.device", "api.alsa.path",
    "media.class", "node.name", "node.description", "device.profile.name",
}
PARAMETERS = {
    "index", "direction", "name", "description", "available", "devices",
    "profile", "save", "mute", "volume", "channelVolumes", "channelMap",
}


def read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")[:131072].strip()
    except OSError:
        return None


def command(argv: list[str], limit: int = 262144) -> dict:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=12,
                                env={**os.environ, "LC_ALL": "C"})
        return {"status": result.returncode, "output": result.stdout[:limit].strip()}
    except FileNotFoundError:
        return {"status": "tool unavailable"}
    except subprocess.TimeoutExpired:
        return {"status": "timed out"}


def audio_graph(objects: list[dict]) -> dict:
    """Discard clients, application streams, serials and unrelated metadata."""
    ids = {str(o["id"]) for o in objects
           if o.get("info", {}).get("props", {}).get("device.api") == "alsa"}
    hardware = []
    names = set()
    for obj in objects:
        info = obj.get("info", {})
        props = info.get("props", {})
        cls = props.get("media.class", "")
        if str(obj.get("id")) not in ids and not (
            cls in ("Audio/Sink", "Audio/Source") and (
                str(props.get("device.id")) in ids or
                "api.alsa.pcm.card" in props)):
            continue
        if props.get("node.name"):
            names.add(props["node.name"])
        params = {}
        for key, values in info.get("params", {}).items():
            if key not in ("Props", "Route", "EnumRoute", "Profile", "EnumProfile"):
                continue
            params[key] = []
            for value in values:
                item = {k: v for k, v in value.items() if k in PARAMETERS}
                if isinstance(value.get("props"), dict):
                    item["props"] = {k: v for k, v in value["props"].items()
                                     if k in PARAMETERS}
                params[key].append(item)
        hardware.append({"id": obj.get("id"), "state": info.get("state"),
                         "properties": {k: v for k, v in props.items()
                                        if k in PROPERTIES}, "parameters": params})
    defaults = {}
    for obj in objects:
        if obj.get("props", {}).get("metadata.name") != "default":
            continue
        for item in obj.get("metadata", []):
            if item.get("key") not in ("default.audio.sink", "default.audio.source"):
                continue
            value = item.get("value", {})
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = {}
            name = value.get("name") if isinstance(value, dict) else None
            defaults[item["key"]] = name if name in names else "non-ALSA or unknown"
    return {"hardware": hardware, "defaults": defaults}


def collect(connection: str) -> dict:
    result = {"schema": "org.projectluma.audio-support-report/v1",
              "captured_utc": datetime.now(timezone.utc).isoformat(),
              "speaker_connection": connection, "kernel": platform.release(),
              "changed_audio_settings": False, "recorded_audio": False,
              "board": {name: read(Path("/sys/class/dmi/id") / name)
                        for name in ("sys_vendor", "product_name", "board_name", "bios_version")}}
    packages = command(["rpm", "-q", "alsa-lib", "alsa-ucm", "alsa-sof-firmware",
                        "pipewire", "wireplumber", "kernel-core"])
    result["packages"] = packages
    status = command(["rpm-ostree", "status", "--json"])
    try:
        result["booted_deployment"] = [
            {k: d.get(k) for k in ("version", "checksum")}
            for d in json.loads(status.get("output", ""))["deployments"] if d.get("booted")]
    except (ValueError, KeyError, TypeError):
        result["booted_deployment"] = {"status": status["status"]}
    result["cards"] = []
    for path in sorted(Path("/proc/asound").glob("card[0-9]*"))[:8]:
        if not re.fullmatch(r"card\d+", path.name):
            continue
        index = path.name[4:]
        result["cards"].append({"index": int(index), "id": read(path / "id"),
                               "mixer": command(["amixer", "-c", index, "contents"])})
    graph = command(["pw-dump"], limit=16 * 1024 * 1024)
    try:
        result["pipewire"] = audio_graph(json.loads(graph.get("output", "")))
    except (ValueError, TypeError, KeyError):
        result["pipewire"] = {"status": graph["status"], "graph_unavailable": True}
    result["es8336_quirk_override"] = read(
        Path("/sys/module/snd_soc_sof_es8336/parameters/quirk"))
    # Keep only audio-related kernel messages, without journal usernames/hostnames.
    kernel = command(["journalctl", "-k", "-b", "--no-pager", "-o", "cat",
                      "--grep", "(?i)sof[-_ ]|es83[0-9]|ESSX|snd_soc"])
    text = kernel.get("output", "")
    text = re.sub(r"/home/[^/\s]+|/var/home/[^/\s]+", "<home>", text)
    result["audio_kernel_messages"] = {"status": kernel["status"], "output": text}
    return result


def save(path: Path, report: dict) -> None:
    # Refuse overwrites and symlinks; keep the report private by default.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speaker-connection", choices=("hdmi", "3.5mm", "usb", "built-in", "unknown"),
                        default="unknown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path.cwd() / (
        "luma-audio-report-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    try:
        save(output, collect(args.speaker_connection))
    except OSError as error:
        parser.exit(1, f"Could not save the report: {error}\n")
    print(f"Saved {output}. No audio settings changed; nothing uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
