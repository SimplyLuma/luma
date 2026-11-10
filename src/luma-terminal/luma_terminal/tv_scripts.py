# SPDX-License-Identifier: MPL-2.0
"""The saved scripts a TV may run: written on a computer, picked on the TV.

A TV has no keyboard worth the name, so Terminal on a TV never types: it runs
one of these, each a command and the folder it runs in. They are saved from
Terminal on a computer ("Save for TV"), and the TV can only pick from them,
never add. The first time, Luma's own read-only monitors are written there.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_SCRIPTS = (
    {"name": "System log", "command": "journalctl -f -n 300 -o short", "cwd": "~"},
    {"name": "Updates", "command": "rpm-ostree status; echo; journalctl -f -n 80 -u rpm-ostreed -o short",
     "cwd": "~"},
    {"name": "Disk and memory",
     "command": "while true; do clear; date; echo; df -h / /var; echo; free -h; sleep 5; done", "cwd": "~"},
)
NAME_LENGTH = 40


def scripts_path() -> Path:
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config) / "luma-terminal" / "tv-scripts.json"


def _valid(script: object) -> bool:
    return (isinstance(script, dict) and isinstance(script.get("name"), str) and script["name"].strip() != ""
            and isinstance(script.get("command"), str) and script["command"].strip() != ""
            and isinstance(script.get("cwd", "~"), str))


def load_scripts(path: Path | None = None) -> list[dict]:
    """The saved scripts, in order. A missing file is seeded with Luma's monitors;
    an unreadable one is left alone and the monitors are offered."""
    path = path or scripts_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _write(path, list(DEFAULT_SCRIPTS))
        return [dict(script) for script in DEFAULT_SCRIPTS]
    except (OSError, ValueError):
        return [dict(script) for script in DEFAULT_SCRIPTS]
    return [dict(script, cwd=script.get("cwd", "~")) for script in data if _valid(script)] \
        if isinstance(data, list) else [dict(script) for script in DEFAULT_SCRIPTS]


def script_name(command: str) -> str:
    """A name for a command, as the TV's rail shows it: the command, shortened."""
    text = " ".join(command.split())
    return text if len(text) <= NAME_LENGTH else text[:NAME_LENGTH - 1].rstrip() + "…"


def save_script(command: str, cwd: str, path: Path | None = None) -> dict:
    """Save a command for the TV (from Terminal on a computer). Saving one that is
    already there moves it to the end rather than listing it twice."""
    command = command.strip()
    if not command:
        raise ValueError("There is no command to save.")
    path = path or scripts_path()
    home = str(Path.home())
    if cwd == home or cwd.startswith(home + os.sep):
        cwd = "~" + cwd[len(home):]
    script = {"name": script_name(command), "command": command, "cwd": cwd or "~"}
    scripts = [s for s in load_scripts(path) if s["command"] != command] + [script]
    _write(path, scripts)
    return script


def _write(path: Path, scripts: list[dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}")
        temporary.write_text(json.dumps(scripts, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass
