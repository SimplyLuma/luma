# SPDX-License-Identifier: Apache-2.0
"""Export a session's touchpad evdev stream in `libinput record` format.

`libinput replay FILE` then plays it through a uinput device, so a recording
can be run through any libinput build, with or without a plugin, on a test
machine. Only the touchpad is exported; key presses never are.
"""

from __future__ import annotations

from .session import Session


def export(session: Session, out_path: str) -> None:
    tp = session.meta.get("devices", {}).get("touchpad", {})
    desc = tp.get("evdev_description", {})
    ident = desc.get("id", {})
    frames = session.by_tag("e")
    lines = ["version: 1", "ndevices: 1", "libinput:", '  version: "1.31.3"', "system:",
             f'  kernel: "{session.meta.get("host", {}).get("kernel", "")}"',
             f'  dmi: "{session.meta.get("host", {}).get("product", "")}"', "devices:",
             f"- node: {tp.get('evdev', '/dev/input/event0')}", "  evdev:",
             f"    # Name: {desc.get('name', tp.get('name', ''))}",
             f"    name: \"{desc.get('name', tp.get('name', ''))}\"",
             f"    id: [{ident.get('bustype', 0)}, {ident.get('vendor', 0)}, {ident.get('product', 0)}, "
             f"{ident.get('version', 0)}]", "    codes:", "      0: [0, 1, 3, 4]"]
    for ev, codes in sorted(desc.get("codes", {}).items(), key=lambda kv: int(kv[0])):
        lines.append(f"      {ev}: [{', '.join(str(c) for c in codes)}]")
    lines.append("    absinfo:")
    for code, a in sorted(desc.get("absinfo", {}).items(), key=lambda kv: int(kv[0])):
        lines.append(f"      {code}: [{a['minimum']}, {a['maximum']}, {a['fuzz']}, {a['flat']}, {a['resolution']}]")
    lines.append(f"    properties: [{', '.join(str(p) for p in desc.get('properties', []))}]")
    lines += ["  hid: []", "  udev:", "    properties:"]
    for k, v in sorted(tp.get("udev", {}).items()):
        lines.append(f"    - {k}={v}")
    lines += ["  quirks: []", "  events:"]
    t0 = frames[0][2] if frames else 0
    for _t, _tag, kernel_us, events in frames:
        rel = max(0, kernel_us - t0)
        sec, usec = divmod(rel, 1_000_000)
        lines.append("  - evdev:")
        for etype, code, value in events:
            lines.append(f"    - [{sec:3d}, {usec:6d}, {etype:2d}, {code:3d}, {value:6d}]")
        lines.append(f"    - [{sec:3d}, {usec:6d}, {0:2d}, {0:3d}, {0:6d}]")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
