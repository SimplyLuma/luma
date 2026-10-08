"""Native Advanced drawer keeps its full ticket and opens from the visible control."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

private = tempfile.TemporaryDirectory(prefix="valet-advanced-")
for kind in ("DATA", "CONFIG", "CACHE", "STATE"):
    folder = Path(private.name) / kind.lower()
    folder.mkdir()
    os.environ[f"XDG_{kind}_HOME"] = str(folder)
os.environ["LUMA_VALET_PREVIEW"] = "1"
os.environ["LUMA_VALET_FIXTURE"] = str(Path(__file__).resolve().parents[3] / "tests/fixtures/valet-v70.json")
os.environ["GTK_A11Y"] = "none"

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402
from luma_installer.installer import InstallerApplication  # noqa: E402

app = InstallerApplication()
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
failures = []
state = {"opened": False, "checked": False}


def verify() -> bool:
    try:
        window = app.get_active_window()
        if window is None:
            return GLib.SOURCE_CONTINUE
        ticket = window.ticket
        if ticket.get_width() < 500:
            return GLib.SOURCE_CONTINUE
        if not state["opened"]:
            state["opened"] = True
            assert ticket.advanced.get_sensitive()
            _ok, install = ticket.primary.compute_bounds(ticket)
            _ok, cancel = ticket.secondary.get_child().compute_bounds(ticket)
            gap = cancel.get_x() - (install.get_x() + install.get_width())
            assert 12 <= gap <= 26, f"Install/Cancel label gap is {gap}px"
            ticket.advanced.emit("clicked")
            assert ticket.advanced.get_active()
            GLib.timeout_add(500, verify)
            return GLib.SOURCE_REMOVE
        assert ticket.advanced_panel.get_child_revealed()
        assert ticket.panels.get_visible(), "base ticket must keep Advanced measured"
        assert ticket.advanced_panel.get_width() >= ticket.get_width() - 2
        assert ticket.advanced_panel.get_height() >= ticket.get_height() - 2
        ticket.advanced.emit("clicked")
        assert not ticket.advanced.get_active()
        state["checked"] = True
    except BaseException as error:
        failures.append(error)
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(150, verify)
status = app.run([])
private.cleanup()
if failures:
    raise failures[0]
assert state["checked"]
print("valet: Advanced opens and closes at full ticket size")
raise SystemExit(status)
