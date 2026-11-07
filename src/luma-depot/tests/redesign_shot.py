#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render the redesigned Depot to a PNG, for comparing with the approved screens (handoff 15.5).

    python3 src/luma-depot/tests/redesign_shot.py out.png [--appearance dark|light] [--size 1180x740]
        [--place apps] [--item app:tide] [--scroll 600] [--demo filters] [--query photo]

A developer and CI tool; nothing installs it. It uses the approved fixture
(tests/fixtures/depot-redesign/world.json) unless LUMA_DEPOT_FIXTURE says otherwise.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE.parent), str(ROOT / "src/luma-platform/appkit"), str(ROOT / "src/luma-installer")]

import gi  # noqa: E402


def _stub_luma_typelibs(appearance: str) -> None:
    """Off Luma (a developer's Mac) the C kit's typelibs are missing: stand in for the few calls the
    window makes, so a layout can be iterated on. Gate screenshots come from Luma (tools/lumaui-conform)."""
    import types
    try:
        gi.require_version("LumaAppearance", "1")
        return
    except ValueError:
        pass

    class Anything:
        def __init__(self, *_a, **_k): pass
        def __call__(self, *_a, **_k): return Anything()
        def __getattr__(self, name):
            if name == "get_surface": return lambda: appearance
            if name in ("get_has_selection",): return lambda: True
            if name.startswith(("get_", "is_")): return lambda *_a: False
            if name == "connect": return lambda *_a, **_k: 0
            return Anything()
        def __bool__(self): return False

    real = gi.require_version
    gi.require_version = lambda ns, v: None if ns in ("LumaAppearance", "LumaUI") else real(ns, v)
    import gi.repository as repository
    for name in ("LumaAppearance", "LumaUI"):
        module = types.ModuleType(f"gi.repository.{name}")
        module.__getattr__ = lambda attr: Anything()
        sys.modules[f"gi.repository.{name}"] = module
        setattr(repository, name, module)


gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402


def render(window: Gtk.Window, path: str) -> None:
    width, height = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    texture = window.get_renderer().render_texture(snapshot.to_node(), Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--appearance", default="dark", choices=("dark", "light"))
    parser.add_argument("--size", default="1180x740")
    parser.add_argument("--place", default="today")
    parser.add_argument("--item", default="")
    parser.add_argument("--scroll", type=float, default=0.0)
    parser.add_argument("--demo", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--settle", type=int, default=1500)
    options = parser.parse_args()
    os.environ.setdefault("LUMA_DEPOT_FIXTURE", str(ROOT / "tests/fixtures/depot-redesign/world.json"))
    os.environ["LUMA_APPEARANCE"] = options.appearance
    width, height = (int(v) for v in options.size.split("x"))

    _stub_luma_typelibs(options.appearance)
    from luma_appkit import install_lumaui
    from luma_depot.shell import DepotShell

    app = Adw.Application(application_id="org.projectluma.Depot.Shot")
    result = {"code": 1}

    def activate(_app) -> None:
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if options.appearance == "dark" else Adw.ColorScheme.FORCE_LIGHT)
        install_lumaui()
        window = DepotShell(app)
        window.set_default_size(width, height)
        window.present()

        def stage() -> bool:
            try:
                staged()
            except Exception:  # noqa: BLE001 - a failing state must not hang the run
                import traceback
                traceback.print_exc()
                app.quit()
            return False

        def staged() -> None:
            if options.place != "today":
                window.go(options.place)
            if options.item:
                kind, item_id = options.item.split(":", 1)
                window.open_item(kind, item_id)
            if options.query:
                window._search_changed(options.query)
            if options.demo:
                demo = getattr(window.current_page, f"demo_{options.demo.replace('-', '_')}", None)
                if demo is None:
                    print(f"no demo {options.demo!r} on this page", file=sys.stderr)
                else:
                    demo()
            GLib.timeout_add(options.settle, shoot)

        def shoot() -> bool:
            if options.scroll:
                window.scroller.get_vadjustment().set_value(options.scroll)
                GLib.timeout_add(300, finish)
            else:
                finish()
            return False

        def finish() -> bool:
            render(window, options.out)
            result["code"] = 0
            app.quit()
            return False

        GLib.timeout_add(400, stage)

    app.connect("activate", activate)
    app.run([])
    return result["code"]


if __name__ == "__main__":
    raise SystemExit(main())
