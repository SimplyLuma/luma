# SPDX-License-Identifier: Apache-2.0
"""LumaUI icons: Lucide glyphs under stable `lumaui-<name>-symbolic` names.

Parts and applications name a glyph by its Lucide name (`"share-2"`,
`"trash-2"`); `icon_name()` turns it into the theme name. The glyphs live in
the Prairie icon theme (assets/icon-theme/Prairie/symbolic/actions, imported
by tools/import-lumaui-icons.py) and, so the kit can travel without that
theme, in an `icons/` folder beside the package, which `ensure_icons()` adds
to the display's icon theme. tests/unit/test_lumaui_icons.py fails when the
kit, the gallery or an application names a glyph that was never imported.

Share is `share-2`, Export is `share`, Upload is `upload`.
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

PREFIX = "lumaui-"
SHARE = "share-2"
EXPORT = "share"
UPLOAD = "upload"

_PACKAGE = Path(__file__).resolve().parent
_added: set[int] = set()


def icon_name(lucide: str) -> str:
    """The theme name for a Lucide glyph: `icon_name("share-2")` is `lumaui-share-2-symbolic`."""
    if lucide.endswith("-symbolic"):
        return lucide
    return f"{PREFIX}{lucide}-symbolic"


def resolve(name: str) -> str:
    """A command's icon as the theme knows it: a bare Lucide name the kit imported
    ("trash-2") becomes its LumaUI glyph; any other name is left as it is."""
    if not name or name.endswith("-symbolic") or "." in name:
        return name
    glyph = icon_name(name)
    return glyph if any((path / f"{glyph}.svg").is_file() for path in search_paths()) else name


#: The name the package exports: `luma_appkit.lumaui_icon("share-2")`.
lumaui_icon = icon_name


def search_paths() -> list[Path]:
    """Folders that hold the kit's glyphs, nearest first; only those that exist."""
    candidates = [Path(p) for p in os.environ.get("LUMA_APPKIT_ICON_PATH", "").split(os.pathsep) if p]
    candidates.append(_PACKAGE / "icons")
    # Run from a source checkout: the Prairie theme's own folder.
    for parent in _PACKAGE.parents:
        theme = parent / "assets/icon-theme/Prairie/symbolic/actions"
        if theme.is_dir():
            candidates.append(theme)
            break
    return [path for path in candidates if path.is_dir()]


def ensure_icons(display: Gdk.Display | None = None) -> None:
    """Make the kit's glyphs resolvable on `display`, whatever icon theme is active."""
    display = display or Gdk.Display.get_default()
    if display is None or hash(display) in _added:
        return
    _added.add(hash(display))
    theme = Gtk.IconTheme.get_for_display(display)
    present = set(theme.get_search_path() or [])
    for path in search_paths():
        if str(path) not in present:
            theme.add_search_path(str(path))


def image(lucide: str, *, pixel_size: int | None = None) -> Gtk.Image:
    """A decorative Gtk.Image of a LumaUI glyph; the control around it carries the name."""
    picture = Gtk.Image(icon_name=icon_name(lucide), accessible_role=Gtk.AccessibleRole.PRESENTATION)
    if pixel_size:
        picture.set_pixel_size(pixel_size)
    return picture
