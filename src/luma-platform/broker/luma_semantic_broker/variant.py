# SPDX-License-Identifier: Apache-2.0
"""Strict conversion between bounded Python records and D-Bus variants."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402


def encode(value: Any) -> GLib.Variant:
    if isinstance(value, GLib.Variant):
        return value
    if value is None:
        # D-Bus does not permit the GVariant unit type on the wire.  The
        # canonical no-value semantic payload is therefore an empty vardict.
        return GLib.Variant("a{sv}", {})
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, str):
        return GLib.Variant("s", value)
    if isinstance(value, int) and not isinstance(value, bool):
        return GLib.Variant("x", value)
    if isinstance(value, float):
        return GLib.Variant("d", value)
    if isinstance(value, dict):
        return vardict(value)
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, str) for item in value):
            return GLib.Variant("as", list(value))
        if all(isinstance(item, dict) for item in value):
            return GLib.Variant(
                "aa{sv}",
                [{str(key): encode(item) for key, item in entry.items()} for entry in value],
            )
        return GLib.Variant("av", [encode(item) for item in value])
    raise TypeError(f"unsupported semantic variant value: {type(value).__name__}")


def vardict(value: dict[str, Any]) -> GLib.Variant:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("semantic variant dictionary keys must be strings")
    return GLib.Variant("a{sv}", {key: encode(item) for key, item in value.items()})


def unbox(value: Any) -> Any:
    if isinstance(value, GLib.Variant):
        return unbox(value.unpack())
    if isinstance(value, dict):
        return {str(key): unbox(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [unbox(item) for item in value]
    return value
