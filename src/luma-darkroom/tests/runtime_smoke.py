# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from luma_darkroom.application import DarkroomApplication
from luma_darkroom.window import DarkroomWindow


def _icon_names(widget: Gtk.Widget) -> set[str]:
    names = set()
    if isinstance(widget, Gtk.Image) and widget.get_icon_name():
        names.add(widget.get_icon_name())
    child = widget.get_first_child()
    while child is not None:
        names |= _icon_names(child)
        child = child.get_next_sibling()
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--decorated", choices=("true", "false"), required=True)
    args = parser.parse_args()
    app = DarkroomApplication()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    window = DarkroomWindow(app)
    assert window.get_decorated() is (args.decorated == "true")
    window.set_default_size(500 if args.compact else 1024, 700)
    window.present()

    failure: list[BaseException] = []

    def inspect() -> bool:
        try:
            window._set_compact(args.compact)
            assert window.layout.get_visible_child_name() == ("compact" if args.compact else "wide")
            # The identity in the corner is the toolkit's: the window carries
            # its class and the application carries the menu.
            assert "luma-app-window" in window.get_css_classes()
            assert app.get_menubar() is not None
            assert window.histogram is not None
            assert window.curves is not None
            assert window.semantic_root is not None or window.document is None
            # Every glyph the workspace shows resolves in the icon theme, never
            # to the missing-image fallback.
            theme = Gtk.IconTheme.get_for_display(window.get_display())
            shown = _icon_names(window)
            assert "document-open-symbolic" in shown, sorted(shown)
            unresolved = sorted(name for name in shown if not theme.has_icon(name))
            assert not unresolved, f"icons that do not resolve: {unresolved}"
        except BaseException as error:  # a failure must end the run, not hang it
            failure.append(error)
        window.close()
        app.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(150, inspect)
    status = app.run([])
    if failure:
        raise failure[0]
    return status


if __name__ == "__main__":
    raise SystemExit(main())

