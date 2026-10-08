# SPDX-License-Identifier: Apache-2.0
"""Every user preference, through one catalogue (ADR-025 §1).

The catalogue is built from the installed GSettings schemas themselves: their
keys, types, allowed values and one-line summaries. A request like "show
seconds on the clock" or "make the mouse faster" becomes a search, a read and
a change with undo; no tool is written per setting. Schemas that decide
security, privacy or who may change what are not in the catalogue.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from ari.mcp import serve  # noqa: E402

DRY_RUN = os.environ.get("ARI_EVAL_DRY_RUN") == "1"
ALLOWED = (
    "org.gnome.desktop.interface", "org.gnome.desktop.peripherals.", "org.gnome.desktop.notifications",
    "org.gnome.desktop.sound", "org.gnome.desktop.wm.preferences", "org.gnome.desktop.calendar",
    "org.gnome.desktop.a11y", "org.gnome.desktop.session", "org.gnome.desktop.background",
    "org.gnome.desktop.datetime", "org.gnome.desktop.input-sources", "org.gnome.settings-daemon.plugins.power",
    "org.gnome.settings-daemon.plugins.color", "org.gnome.mutter", "org.gnome.shell.extensions.tilingshell",
    "org.project_luma.shell-state", "org.gnome.system.location", "org.gnome.shell.app-switcher",
)
# Never: the lock screen, privacy, proxies, lockdown, and Ari's own permissions.
DENIED = ("org.gnome.desktop.screensaver", "org.gnome.desktop.lockdown", "org.gnome.desktop.privacy",
          "org.gnome.system.proxy", "org.projectluma.Ari", "org.gnome.mutter.keybindings",
          "org.gnome.desktop.wm.keybindings", "org.gnome.settings-daemon.plugins.media-keys")
DENIED_KEYS = {"schema-version", "shelf-layout-version", "enabled-extensions", "disabled-extensions"}
STOP_WORDS = {"the", "a", "an", "my", "to", "of", "on", "off", "in", "and", "turn", "set", "make", "change",
              "show", "hide", "enable", "disable", "please", "is", "for", "be", "it"}


def _allowed(schema_id: str) -> bool:
    return schema_id.startswith(ALLOWED) and not schema_id.startswith(DENIED)


def _catalogue() -> list[tuple[str, str, Gio.SettingsSchemaKey]]:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None:
        return []
    non_relocatable, _relocatable = source.list_schemas(True)
    rows = []
    for schema_id in sorted(non_relocatable):
        if not _allowed(schema_id):
            continue
        schema = source.lookup(schema_id, True)
        for key in schema.list_keys():
            if key not in DENIED_KEYS:
                rows.append((schema_id, key, schema.get_key(key)))
    return rows


def _choices(key: Gio.SettingsSchemaKey) -> list[str]:
    kind, values = key.get_range().unpack()
    return [str(v) for v in values] if kind == "enum" else []


def _describe(schema_id: str, name: str, key: Gio.SettingsSchemaKey) -> dict:
    value = Gio.Settings.new(schema_id).get_value(name)
    return {"schema": schema_id, "key": name, "summary": key.get_summary() or "",
            "type": key.get_value_type().dup_string(), "value": value.print_(False),
            "choices": _choices(key), "writable": Gio.Settings.new(schema_id).is_writable(name)}


def find_setting(arguments: dict) -> dict:
    words = [w for w in re.findall(r"[a-z0-9]+", str(arguments.get("query", "")).lower()) if w not in STOP_WORDS]
    if not words:
        return {"ok": False, "summary": "Say what the setting is about, like 'clock seconds' or 'mouse speed'."}
    scored = []
    for schema_id, name, key in _catalogue():
        haystack_name = set(name.split("-")) | set(schema_id.rsplit(".", 1)[-1].split("-"))
        text = f"{key.get_summary() or ''} {key.get_description() or ''}".lower()
        score = sum(3 if w in haystack_name else 1 if re.search(rf"\b{re.escape(w)}", text) else 0 for w in words)
        if score:
            scored.append((score, schema_id, name, key))
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    matches = [_describe(s, n, k) for _score, s, n, k in scored[:8]]
    if not matches:
        return {"ok": True, "summary": "No setting matches that.", "data": {"matches": []}}
    lines = [f"{m['schema']} {m['key']} = {m['value']} ({m['summary']}"
             + (f"; one of {', '.join(m['choices'])}" if m["choices"] else "") + ")" for m in matches]
    return {"ok": True, "summary": "\n".join(lines), "data": {"matches": matches}}


def _parse(key: Gio.SettingsSchemaKey, raw: object) -> GLib.Variant:
    kind = key.get_value_type().dup_string()
    text = raw if isinstance(raw, str) else json.dumps(raw)
    text = text.strip()
    if kind == "b":
        lowered = text.lower()
        if lowered in ("true", "on", "yes", "enabled", "1"):
            return GLib.Variant("b", True)
        if lowered in ("false", "off", "no", "disabled", "0"):
            return GLib.Variant("b", False)
        raise ValueError("expected on or off")
    if kind == "s" and not (text.startswith("'") or text.startswith('"')):
        return GLib.Variant("s", text)
    return GLib.Variant.parse(GLib.VariantType.new(kind), text, None, None)


def change_setting(arguments: dict) -> dict:
    schema_id, name = str(arguments.get("schema", "")), str(arguments.get("key", ""))
    if not _allowed(schema_id) or name in DENIED_KEYS:
        return {"ok": False, "summary": "That setting isn't one I can change."}
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(schema_id, True) if source else None
    if schema is None or not schema.has_key(name):
        return {"ok": False, "summary": "That setting doesn't exist here. Search for it first."}
    key = schema.get_key(name)
    settings = Gio.Settings.new(schema_id)
    if not settings.is_writable(name):
        return {"ok": False, "summary": "That setting is locked on this machine."}
    try:
        value = _parse(key, arguments.get("value", ""))
    except (ValueError, GLib.Error):
        choices = _choices(key)
        return {"ok": False, "summary": f"{name} needs a {key.get_value_type().dup_string()} value"
                                        + (f": one of {', '.join(choices)}" if choices else "") + "."}
    if not key.range_check(value):
        return {"ok": False, "summary": f"{value.print_(False)} isn't allowed for {name}."
                                        + (f" Choose {', '.join(_choices(key))}." if _choices(key) else "")}
    before = settings.get_value(name)
    label = (key.get_summary() or name.replace("-", " ")).rstrip(".")
    if before.equal(value):
        return {"ok": True, "unchanged": True, "summary": f"{label} is already {value.print_(False)}."}
    summary = f"{label}: {before.print_(False)} → {value.print_(False)}"
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Change “{label}” to {value.print_(False)}",
                "detail": f"Now {before.print_(False)} · {schema_id}"}
    if not settings.set_value(name, value):
        return {"ok": False, "summary": f"{label} wasn't changed."}
    Gio.Settings.sync()
    return {"ok": True, "summary": summary,
            "undo": {"tool": "change_setting", "arguments": {"schema": schema_id, "key": name,
                                                             "value": before.print_(False)}}}


TOOLS = {
    "find_setting": ("Search every user preference on this computer (appearance, clock, mouse, touchpad, "
                     "keyboard, sound, notifications, power, windows, the shelf, tiling, accessibility). "
                     "Returns schema, key, current value and allowed values.",
                     {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                      "additionalProperties": False}, find_setting),
    "change_setting": ("Change a preference found with find_setting. value is the new value: true/false, a "
                       "number, one of the listed choices, or a GVariant text like ['a', 'b'].",
                       {"type": "object", "properties": {"schema": {"type": "string"}, "key": {"type": "string"},
                                                         "value": {"type": "string"},
                                                         "preview": {"type": "boolean"}},
                        "required": ["schema", "key", "value"], "additionalProperties": False}, change_setting),
}

if __name__ == "__main__":
    serve("ari-preferences", TOOLS)
