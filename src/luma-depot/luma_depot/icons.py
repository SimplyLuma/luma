# SPDX-License-Identifier: Apache-2.0
"""Drawing the icon `luma_installer.depot_icons` chose, at the display's scale.

Two toolkit details decide whether an icon is right or wrong here.

An icon theme name that the theme does not have raises nothing: the image
draws the toolkit's own missing-image glyph. So the theme is asked first, by
`depot_icons.choose`, through `theme_has` below.

`Gtk.Image.new_from_file` makes one texture at the file's natural size and
then squeezes it into the widget, which on a fractional display is the wrong
number of pixels and looks soft. A `Gio.FileIcon` handed to `Gtk.Image` goes
through the icon theme's loader instead, which is asked for the size *and* the
widget's scale factor, so a 40pt tile on a 1.25x display gets the pixels it
actually needs. Every icon here is drawn that way, file or theme name alike.
"""
from __future__ import annotations

from gi.repository import Gio, GLib, Gtk

from luma_installer import depot_icons

#: A symbolic or monogram placeholder sits inside a tinted tile, so every
#: placeholder has the same silhouette. Real app artwork already has its own
#: shape (a rounded square, a circle, a mark) and is drawn at the tile's full
#: size with no tile behind it, the way an app store shows an app's icon.
INSET = 0.72
ARTWORK_INSET = 1.0


def theme_predicate(widget) -> callable:
    """Ask the display's icon theme whether it really has a name."""
    try:
        theme = Gtk.IconTheme.get_for_display(widget.get_display())
    except (AttributeError, TypeError):
        return lambda name: False
    return theme.has_icon


def choose_for(widget, app, media) -> depot_icons.Choice:
    """The icon decision for `app` on `widget`'s display."""
    media_path = ''
    if app.icon_sha256:
        media_path = media.path(app.icon_url, app.icon_sha256)
    appstream_path = ''
    name = (app.icon_name or '').strip()
    # A path in `icon_name` is a file the AppStream cache reader already
    # picked the size of; anything else is a name for the theme to answer.
    if depot_icons.looks_like_a_path(name):
        appstream_path, name = name, ''
    return depot_icons.choose(
        name=app.name,
        media_path=media_path,
        appstream_path=appstream_path,
        icon_name=name,
        theme_has=theme_predicate(widget),
    )


def image(choice: depot_icons.Choice, size: int) -> Gtk.Widget:
    """A widget drawing `choice` at `size` logical points.

    A file and a theme name both become a `Gio.Icon` so the icon theme loads
    them at `size` times the widget's scale factor. A placeholder is the
    application's monogram in the tile it would have filled.
    """
    pixels = max(1, int(round(size * INSET)))
    if choice.kind in ('file', 'themed'):
        gicon = _gicon(choice)
        if gicon is not None:
            artwork = Gtk.Image(gicon=gicon, pixel_size=max(1, int(round(size * ARTWORK_INSET))))
            artwork.add_css_class('dp-artwork')
            return artwork
    label = Gtk.Label(label=choice.value or '?')
    label.add_css_class('dp-monogram')
    if size >= 70:
        label.add_css_class('large')
    return label


def _gicon(choice: depot_icons.Choice):
    try:
        if choice.kind == 'file':
            return Gio.FileIcon.new(Gio.File.new_for_path(choice.value))
        return Gio.ThemedIcon.new(choice.value)
    except (GLib.Error, TypeError, ValueError):
        return None
