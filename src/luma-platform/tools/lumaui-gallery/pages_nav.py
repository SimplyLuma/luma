# SPDX-License-Identifier: Apache-2.0
"""LumaUI Gallery: the v71 navigation parts (K-NAV).

The gallery takes `PAGES` and `BUILDERS` from here; tools/lumaui-conform's
`nav-*` scenarios run `main()` (`bin/run --module pages_nav`) to show one part
alone in its window, measured against luma-next-71.html.

    python3 lumaui_gallery.py --page nav-island [--appearance dark] [--phone]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE.parents[1] / "appkit") not in sys.path:
    sys.path.insert(0, str(HERE.parents[1] / "appkit"))

from luma_appkit import TitleIsland  # noqa: E402

PAGES = [
    ("nav-island", "Title island", "panel-top", "structure", None),
]


def _ari_island() -> TitleIsland:
    """v71 ?app=ari under 560: lNavTtl(w, "Budget for Theo", "Today")."""
    return TitleIsland("Budget for Theo", "Today", lead="menu", phone_only=False)


def _notes_island() -> TitleIsland:
    """v71 Notes on a phone (#n-isl): ‹ | the folder, who's here and when it was edited."""
    return TitleIsland("Launch", "Priya is here · Edited just now", lead="back", phone_only=False,
                       grow=lambda: Gtk.Label(label="Pin  Copy link  Move  Duplicate"))


def page_island() -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    for margin in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{margin}")(24)
    lede = Gtk.Label(label="TitleIsland: ☰ or ‹, a full-height hairline, the title over its subtitle. "
                           "Tap it to grow into its places or its details.", wrap=True, xalign=0)
    box.append(lede)
    for island in (_ari_island(), _notes_island()):
        island.set_halign(Gtk.Align.START)
        box.append(island)
    return box


BUILDERS = {"nav-island": page_island}


# ── conform surfaces ───────────────────────────────────────────────────────
#
# LUMAUI_NAV_SURFACE names the part; it fills its window, which the harness
# sizes to the spec's element (the scenario's spec.window).

SURFACES = {"ari-island": _ari_island, "notes-island": _notes_island}

_CONFORM_CSS = """
window.lumaui-nav-conform box.lumaui-title-island.floating { margin: 0; }
window.lumaui-nav-conform { padding: 0; border-radius: 0; box-shadow: none;
  background-image: linear-gradient(to bottom, @luma_window_top, @luma_window_bottom); }
"""


def main() -> int:
    from luma_appkit import install_lumaui

    name = os.environ.get("LUMAUI_NAV_SURFACE", "ari-island")
    app = Gtk.Application(application_id="org.projectluma.LumaUINavConform")

    def activate(application: Gtk.Application) -> None:
        window = Gtk.ApplicationWindow(application=application, title=name)
        window.add_css_class("lumaui-nav-conform")
        window.set_decorated(False)
        window.add_css_class("luma-no-native-surfaces")
        install_lumaui(window.get_display())
        provider = Gtk.CssProvider()
        provider.load_from_string(_CONFORM_CSS)
        Gtk.StyleContext.add_provider_for_display(window.get_display(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        island = SURFACES[name]()
        island.set_name("nav-surface")
        island.set_halign(Gtk.Align.FILL)
        island.set_valign(Gtk.Align.FILL)
        window.set_child(island)
        window.present()

    app.connect("activate", activate)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
