# SPDX-License-Identifier: Apache-2.0
"""What a person called each arrangement, and which ones they chose not to set up."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import model


def _path() -> Path:
    if os.environ.get("FLATPAK_ID"):
        base = Path(os.environ["XDG_DATA_HOME"]) / "state"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "luma" / "displays.json"


def load() -> dict:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    names = value.get("names") if isinstance(value, dict) else None
    dismissed = value.get("dismissed") if isinstance(value, dict) else None
    labels = value.get("labels") if isinstance(value, dict) else None
    return {"names": names if isinstance(names, dict) else {},
            "labels": labels if isinstance(labels, dict) else {},
            "dismissed": [k for k in dismissed if isinstance(k, str)] if isinstance(dismissed, list) else []}


def save(value: dict) -> None:
    path = _path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def name_for(key: tuple[model.Spec, ...], fallback: str) -> str:
    return load()["names"].get(model.key_text(key)) or fallback


def rename(key: tuple[model.Spec, ...], name: str) -> None:
    value = load()
    name = name.strip()[:60]
    if name:
        value["names"][model.key_text(key)] = name
    else:
        value["names"].pop(model.key_text(key), None)
    save(value)


def dismissed(key: tuple[model.Spec, ...]) -> bool:
    return model.key_text(key) in load()["dismissed"]


def dismiss(key: tuple[model.Spec, ...]) -> None:
    value = load()
    if model.key_text(key) not in value["dismissed"]:
        value["dismissed"] = (value["dismissed"] + [model.key_text(key)])[-50:]
        save(value)


def _spec_text(spec: model.Spec) -> str:
    return f"{spec.vendor}:{spec.product}:{spec.serial}"


def remember_labels(labels: dict[model.Spec, str]) -> None:
    """Keep the friendly name of each display seen, for arrangements shown
    while it is unplugged: monitors.xml only records model numbers."""
    value = load()
    changed = False
    for spec, label in labels.items():
        if value["labels"].get(_spec_text(spec)) != label:
            value["labels"][_spec_text(spec)] = label
            changed = True
    if changed:
        save(value)


def label(spec: model.Spec) -> str:
    return load()["labels"].get(_spec_text(spec)) or model.spec_short_name(spec)
