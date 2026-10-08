# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: file card, Open button and Open in.

FileCard — one look for a file anywhere (a message, an email, a download,
a details pane): the file's own face (its thumbnail, or the icon of the app
that opens it) never larger than a 44 px square, its name cut in the middle
so the extension stays ("press-rel…ase.write"), size · kind, and one Open
that names and wears the default app's icon. States go in the second line
(`subtitle=`, `tone="danger"`), progress under it (`extra=`), and the Open
can be replaced by the card's own small actions (`actions=`: Pause, Cancel,
Remove). `compact=True` is the attachment size. v70 `lFile`, `lMid`.

    FileCard("~/Downloads/launch-deck.stage")
    FileCard(uri, subtitle="38 s left", extra=progress, actions=[BarAction("pause", tooltip="Pause", …)])
    FileCard(path, subtitle="Failed: the server stopped responding", tone="danger")

OpenButton — "Open" wearing the default app's icon, named "Open in <App>"
(v70 `lOpenBtn`).

OpenInMenu — the apps that can open it, with their real icons: the best Luma
app first, the default marked, then "Other app…", which asks the desktop's
chooser (the Luma portal's) through Gtk.FileLauncher (v70 `lOpenIn`).

    OpenInMenu(file).popup(button)

Everything reads the file through GIO: its display name, size, content type,
cached thumbnail, and the applications registered for its type.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .action_center import BarAction, make_control  # noqa: E402
from . import application_directory  # noqa: E402

__all__ = ["file_size", "file_kind", "FileCard", "OpenButton", "OpenInMenu", "split_name", "rank_apps", "LUMA_APP_PREFIX"]

#: Desktop ids of Luma's own applications start with this.
LUMA_APP_PREFIX = "org.projectluma."
#: ...except the launchers Luma writes for packages it installed (a .deb/.rpm app, ADR-050).
_INSTALLED_PREFIX = "org.projectluma.Installed."


def is_luma_app(app_id: str) -> bool:
    """Whether a desktop id is one of Luma's own applications (not a package Luma installed)."""
    return app_id.startswith(LUMA_APP_PREFIX) and not app_id.startswith(_INSTALLED_PREFIX)

_NAME = re.compile(r"^(.+?)(.{0,5}\.[A-Za-z0-9]{1,6})$", re.S)
_ATTRIBUTES = ",".join(("standard::type", "standard::display-name", "standard::size", "standard::content-type",
                        "standard::fast-content-type", "thumbnail::path", "thumbnail::is-valid"))


def split_name(name: str) -> tuple[str, str]:
    """A file name as (what may be cut, what always shows): the extension and a few letters before it."""
    match = _NAME.match(name)
    return (match.group(1), match.group(2)) if match else (name, "")


def rank_apps(apps: Iterable, default_id: str | None) -> list:
    """Apps (Gio.AppInfo-like) in menu order: Luma's first, the default first within its group, then by name."""
    seen, unique = set(), []
    for app in apps:
        key = app.get_id()
        if key and key not in seen:
            seen.add(key)
            unique.append(app)

    def key(app) -> tuple:
        return (not is_luma_app(app.get_id()), app.get_id() != default_id,
                (app.get_name() or "").casefold())

    return sorted(unique, key=key)


def _gfile(file: Gio.File | str) -> Gio.File:
    if isinstance(file, Gio.File):
        return file
    text = str(file)
    return Gio.File.new_for_uri(text) if "://" in text else Gio.File.new_for_path(os.path.expanduser(text))


def file_size(size: int) -> str:
    """A file's size as v71 writes it (lFile): "412 KB", "18.4 MB", "1.2 GB" (decimal units, as GLib's)."""
    if size < 1000:
        return f"{size} byte{'s' if size != 1 else ''}"
    for unit, scale in (("KB", 1e3), ("MB", 1e6), ("GB", 1e9), ("TB", 1e12)):
        value = size / scale
        if value < 999.5 or unit == "TB":
            if unit == "KB":
                return f"{round(value)} KB"
            text = f"{value:.1f}".removesuffix(".0")
            return f"{text} {unit}"
    return f"{size} bytes"


#: v71 LFK: the words a file's kind is known by, for the kinds Luma names itself.
_KIND_WORDS = {
    "application/x-luma-stage": "Stage presentation", "application/pdf": "PDF document",
    "application/x-luma-sheet": "Grid spreadsheet", "application/zip": "ZIP archive",
    "text/markdown": "Markdown", "application/x-luma-write": "Write document",
    "application/x-iso9660-image": "Disk image", "application/x-luma-note": "Note",
}
_KIND_BY_SUFFIX = {".stage": "Stage presentation", ".pdf": "PDF document", ".sheet": "Grid spreadsheet",
                   ".zip": "ZIP archive", ".md": "Markdown", ".iso": "Disk image"}
_KIND_BY_FAMILY = {"image": "Image", "audio": "Audio", "video": "Video"}


def file_kind(content_type: str | None, name: str = "") -> str:
    """What a file is, in words: v71's for Luma's kinds, else the desktop's description."""
    if content_type in _KIND_WORDS:
        return _KIND_WORDS[content_type]
    suffix = ("." + name.rsplit(".", 1)[-1].casefold()) if "." in name else ""
    if suffix in _KIND_BY_SUFFIX:
        return _KIND_BY_SUFFIX[suffix]
    family = (content_type or "").split("/", 1)[0]
    if family in _KIND_BY_FAMILY:
        return _KIND_BY_FAMILY[family]
    described = Gio.content_type_get_description(content_type) if content_type else ""
    # An unregistered type describes itself as its own name: say "File" rather than "application/x-…".
    unknown = not described or "/" in described or described == content_type or described.casefold() == "unknown"
    return "File" if unknown else described


def _default_app(content_type: str | None) -> Gio.AppInfo | None:
    if not content_type:
        return None
    try:
        if application_directory.sandboxed():
            return next((app for app in application_directory.applications(content_type) if app.default), None)
        return Gio.AppInfo.get_default_for_type(content_type, False)
    except TypeError:
        return None


def _best_app(content_type: str | None) -> Gio.AppInfo | None:
    """The app a file opens in: the first in Open-in order (the best Luma app first), not only Gio's default."""
    default = _default_app(content_type)
    try:
        found = application_directory.applications(content_type) if content_type else []
    except TypeError:
        found = []
    ranked = rank_apps([a for a in found if a.should_show()], default.get_id() if default is not None else None)
    return ranked[0] if ranked else default


def _launch(widget: Gtk.Widget, file: Gio.File | None, app: Gio.AppInfo | None, name: str) -> None:
    """Open `file` in `app` (or the default), telling the person if it could not be done."""
    try:
        if app is not None and file is not None:
            context = widget.get_display().get_app_launch_context()
            def finished(accepted, error):
                if not accepted:
                    from .action_toast import Toast
                    Toast.show(widget, error or f"Couldn’t open {name}", kind="error")
            application_directory.launch(app.get_id(), [file], context=context, callback=finished)
        elif file is not None:
            root = widget.get_root()
            Gtk.FileLauncher.new(file).launch(root if isinstance(root, Gtk.Window) else None, None, None, None)
    except GLib.Error:
        from .action_toast import Toast
        Toast.show(widget, f"Couldn’t open {name}", kind="error")


class _CappedLayout(Gtk.BoxLayout):
    """A box layout whose natural width is the part's design width: narrower only when it must be.

    v70's `width: 300px; max-width: 100%`: the card asks for its width, and a
    tighter place shrinks it (the name gives way first).
    """

    def __init__(self, orientation: Gtk.Orientation, width: int) -> None:
        super().__init__(orientation=orientation)
        self.width = width

    def do_measure(self, widget: Gtk.Widget, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        minimum, natural, min_base, nat_base = Gtk.BoxLayout.do_measure(self, widget, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            natural = max(minimum, self.width)
        return minimum, natural, min_base, nat_base


def cap_width(box: Gtk.Box, width: int) -> None:
    """Give `box` a design width it keeps unless the place it sits in is narrower."""
    box.set_layout_manager(_CappedLayout(box.get_orientation(), width))


class _Face(Gtk.Widget):
    """A picture cropped to a fixed square with rounded corners. Never larger than its square."""

    __gtype_name__ = "LumaUIFileFace"

    def __init__(self, size: int, radius: float) -> None:
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.size, self.radius = size, radius
        self.paintable: Gdk.Paintable | None = None
        self.add_css_class("lumaui-file-face")

    def set_paintable(self, paintable: Gdk.Paintable | None) -> None:
        self.paintable = paintable
        self.queue_draw()

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        paintable = self.paintable
        if paintable is None:
            return
        size = self.size
        bounds = Graphene.Rect().init(0, 0, size, size)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(bounds, self.radius)
        snapshot.push_rounded_clip(clip)
        width = paintable.get_intrinsic_width() or size
        height = paintable.get_intrinsic_height() or size
        scale = max(size / width, size / height)  # cover: fill the square, crop the rest
        drawn_w, drawn_h = width * scale, height * scale
        snapshot.save()
        snapshot.translate(Graphene.Point().init((size - drawn_w) / 2, (size - drawn_h) / 2))
        paintable.snapshot(snapshot, drawn_w, drawn_h)
        snapshot.restore()
        snapshot.pop()


def _app_icon(app: Gio.AppInfo | None, content_type: str | None) -> Gio.Icon | None:
    if app is not None and app.get_icon() is not None:
        return app.get_icon()
    if content_type:
        return Gio.content_type_get_icon(content_type)
    return None


class OpenButton(Gtk.Button):
    """Open, wearing the default app's icon: "Open in Stage"."""

    __gtype_name__ = "LumaUIOpenButton"

    def __init__(self, file: Gio.File | str | None = None, *, content_type: str | None = None,
                 label: str = "Open", small: bool = False, on_open: Callable[[], None] | None = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER)
        self.add_css_class("lumaui-open-button")
        if small:
            self.add_css_class("small")
        self.file = _gfile(file) if file is not None else None
        if content_type is None and self.file is not None:
            content_type, _uncertain = Gio.content_type_guess(self.file.get_basename(), None)
        self.content_type = content_type
        self.app = _best_app(content_type)
        line = Gtk.Box()
        line.add_css_class("lumaui-open-content")
        gicon = self.app.get_icon() if self.app is not None else None
        if gicon is not None:
            image = Gtk.Image.new_from_gicon(gicon)
            image.add_css_class("lumaui-app-icon")
        else:
            image = icons.image("square-arrow-out-up-right")
        line.append(image)
        line.append(Gtk.Label(label=label))
        self.set_child(line)
        name = f"Open in {self.app.get_name()}" if self.app is not None else label
        self.app_name = self.app.get_name() if self.app is not None else None
        self.set_tooltip_text(name)
        self.update_property([Gtk.AccessibleProperty.LABEL], [name])
        self._on_open = on_open
        self.connect("clicked", self._clicked)

    def _clicked(self, _button: Gtk.Button) -> None:
        if self._on_open is not None:
            self._on_open()
        else:
            _launch(self, self.file, self.app, self.file.get_basename() if self.file else "it")


class FileCard(Gtk.Box):
    """One file, one look. See the module docstring."""

    __gtype_name__ = "LumaUIFileCard"

    def __init__(self, file: Gio.File | str, *, name: str | None = None, size: int | None = None,
                 kind: str | None = None, content_type: str | None = None, subtitle: str | None = None,
                 tone: str | None = None, extra: Gtk.Widget | None = None,
                 actions: Sequence[BarAction] | None = None, on_open: Callable[[], None] | None = None,
                 thumbnail: Gdk.Paintable | str | None = None, compact: bool = False, selected: bool = False) -> None:
        if tone not in (None, "danger", "warning"):
            raise ValueError("a file card's tone is danger or warning")
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self.add_css_class("lumaui-file-card")
        lumaui.set_css_class(self, "compact", compact)
        lumaui.set_css_class(self, "selected", selected)
        metrics = tokens.FILE_CARD
        cap_width(self, metrics["mini_width"] if compact else metrics["width"])
        self.file = _gfile(file)
        info = self._query()
        self.name = name or (info.get_display_name() if info else None) or self.file.get_basename() or ""
        if content_type is None:
            content_type = (info.get_content_type() if info else None) or Gio.content_type_guess(self.name, None)[0]
        self.content_type = content_type
        if size is None and info is not None and info.get_file_type() == Gio.FileType.REGULAR:
            size = info.get_size()
        self.size = size
        self.kind = kind or file_kind(content_type, self.name)
        self.app = _best_app(content_type)

        # The face: a picture cropped into its square, or the app's icon.
        face_size = metrics["mini_face"] if compact else metrics["face"]
        slot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        slot.add_css_class("lumaui-file-slot")
        self.face = _Face(face_size, metrics["face_radius"])
        self.face_icon = Gtk.Image()
        self.face_icon.add_css_class("lumaui-file-icon")
        self.face_icon.set_pixel_size(metrics["mini_face"] if compact else metrics["face_icon"])
        gicon = _app_icon(self.app, content_type)
        if gicon is not None:
            self.face_icon.set_from_gicon(gicon)
        slot.append(self.face_icon)
        slot.append(self.face)
        self.face.set_visible(False)
        self.append(slot)
        self._load_face(thumbnail, info)

        # Name and second line.
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        text.add_css_class("lumaui-file-text")
        head, tail = split_name(self.name)
        name_row = Gtk.Box()
        name_row.add_css_class("lumaui-file-name")
        self.name_head = Gtk.Label(label=head, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.name_head.set_width_chars(1)
        name_row.append(self.name_head)
        if tail:
            self.name_tail = Gtk.Label(label=tail, xalign=0)
            name_row.append(self.name_tail)
        name_row.set_tooltip_text(self.name)
        text.append(name_row)
        second = " · ".join(part for part in ((file_size(size) if size is not None else None),
                                               subtitle or self.kind) if part)
        self.second_line = Gtk.Label(label=second, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.second_line.add_css_class("lumaui-file-meta")
        if tone:
            self.second_line.add_css_class(tone)
        text.append(self.second_line)
        if extra is not None:
            text.append(extra)
        self.append(text)

        # Open, or the card's own actions.
        if actions:
            for action in actions:
                button = make_control(action, size="file")
                self.append(button)
            self.open_button = None
        else:
            self.open_button = OpenButton(self.file, content_type=content_type, small=True,
                                          on_open=on_open or (lambda: _launch(self, self.file, self.app, self.name)))
            self.append(self.open_button)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{self.name}, {second}"])

    def _query(self) -> Gio.FileInfo | None:
        try:
            return self.file.query_info(_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            return None

    def _load_face(self, thumbnail: Gdk.Paintable | str | None, info: Gio.FileInfo | None) -> None:
        if isinstance(thumbnail, Gdk.Paintable):
            self._show_face(thumbnail)
            return
        path = thumbnail
        if path is None and info is not None and info.get_attribute_boolean("thumbnail::is-valid"):
            path = info.get_attribute_byte_string("thumbnail::path")
        if path is None and self.content_type and Gio.content_type_is_mime_type(self.content_type, "image/*") \
                and self.file.get_path() and (self.size or 0) < 40_000_000:
            path = self.file.get_path()
        if not path:
            return
        scale = 2 * tokens.FILE_CARD["face"]

        def load() -> None:
            try:
                gi.require_version("GdkPixbuf", "2.0")
                from gi.repository import GdkPixbuf
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, scale, scale, True)
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            except (GLib.Error, ImportError, ValueError):
                return
            GLib.idle_add(lambda: (self._show_face(texture), False)[1])

        threading.Thread(target=load, daemon=True).start()

    def _show_face(self, paintable: Gdk.Paintable) -> None:
        self.face.set_paintable(paintable)
        self.face.set_visible(True)
        self.face_icon.set_visible(False)
        self.add_css_class("picture")


class OpenInMenu:
    """The apps that can open a file: best Luma app first, the default marked, "Other app…" last."""

    def __init__(self, file: Gio.File | str | None = None, *, content_type: str | None = None,
                 on_open: Callable[[Gio.AppInfo], None] | None = None,
                 on_other: Callable[[], None] | None = None) -> None:
        self.file = _gfile(file) if file is not None else None
        if content_type is None and self.file is not None:
            content_type = Gio.content_type_guess(self.file.get_basename(), None)[0]
        self.content_type = content_type
        self.on_open, self.on_other = on_open, on_other
        default = _default_app(content_type)
        self.default_id = default.get_id() if default is not None else None
        found = application_directory.applications(content_type) if content_type else []
        self.apps = rank_apps([a for a in found if a.should_show()], self.default_id)
        self.menu = None
        if content_type:
            self._discovery = application_directory.discover(content_type, self._discovered)

    def _discovered(self, apps, error):
        if error:
            return
        self.default_id = next((app.get_id() for app in apps if getattr(app, 'default', False)), self.default_id)
        self.apps = rank_apps([app for app in apps if app.should_show()], self.default_id)
        if self.menu is not None and self.menu.get_mapped():
            self.menu.close()
            self.menu = None
            self.popup(self._anchor)

    def rows(self) -> list:
        from .action_bubble import MenuItem
        rows: list = ["Open in"]
        for app in self.apps:
            rows.append(MenuItem(app.get_name(), gicon=app.get_icon(),
                                 note="Default" if app.get_id() == self.default_id else None,
                                 on_activate=lambda a=app: self._open(a)))
        if self.file is not None or self.on_other is not None:
            rows.append(None)
            rows.append(MenuItem("Other app…", icon="app-window", on_activate=self._other))
        return rows

    def popup(self, anchor: Gtk.Widget) -> "OpenInMenu":
        from .action_bubble import FloatingMenu
        self._anchor = anchor
        self.menu = FloatingMenu(self.rows(), label="Open in")
        self.menu.add_css_class("open-in")
        self.menu.popup(anchor)
        return self

    def _open(self, app: Gio.AppInfo) -> None:
        if self.on_open is not None:
            self.on_open(app)
        else:
            _launch(self._anchor, self.file, app, self.file.get_basename() if self.file else "it")

    def _other(self) -> None:
        if self.on_other is not None:
            self.on_other()
            return
        # Always ask: the desktop's chooser (Luma's portal) lists every app and can set the default.
        launcher = Gtk.FileLauncher.new(self.file)
        launcher.set_always_ask(True)
        root = self._anchor.get_root()
        launcher.launch(root if isinstance(root, Gtk.Window) else None, None, None, None)
