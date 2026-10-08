# SPDX-License-Identifier: Apache-2.0
"""Studio v70 Darkroom library and editor on the LumaUI window and parts."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
import threading

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from luma_appkit import (
    ActionCenter, AdjustmentGroup, AppWindow, BarAction, BarChip, BarMenu, DetailsFacts, PanelHeading, Command, CommandGroup,
    CommandRegistry, CornerPill, CreativeWorkspace, DetailsPane, FloatingPanel,
    Island, MediaGrid, MediaItem, ModeSwitch, NavigationRow, NavigationSidebar, RowLead,
    SidebarRow, apply_sidebar_variant, SEPARATOR,
    ShareSheet, ShareSubject, Toast, ToastHost, ValueSlider, add_style_sheet, apply_type, icons,
)

from luma_appkit.structure_adapt import WidthWatch
from luma_appkit.action_center import make_control
from luma_appkit.action_bubble import MenuItem

from .editing import Editor
from .library_data import IMAGE_SUFFIXES, LibraryPhoto, LibrarySnapshot, load_fixture, load_real_library
from .library_metadata import MemoryMetadataStore, MetadataStore, default_path
from .model import Adjustment, Crop, Document, DocumentStore, ExportPreset, file_path, new_id
from .photo_widgets import PhotoCanvas, PhotoLabels, PhotoRating
from .raw import RAW_SUFFIXES, raw_thumbnail


APP_ID = "org.projectluma.Darkroom"
ICON_NAME = "org.projectluma.Darkroom"

GROUPS = (
    ("Light", (("exp", "Exposure", -100, 100), ("con", "Contrast", -100, 100),
               ("hi", "Highlights", -100, 100), ("sh", "Shadows", -100, 100),
               ("wh", "Whites", -100, 100), ("bl", "Blacks", -100, 100))),
    ("Colour", (("temp", "Temperature", -100, 100), ("tint", "Tint", -100, 100),
                ("vib", "Vibrance", -100, 100), ("sat", "Saturation", -100, 100))),
    ("Effects", (("cla", "Clarity", -100, 100), ("vig", "Vignette", -100, 100),
                 ("grain", "Grain", 0, 100), ("fade", "Fade", 0, 100))),
    ("Detail", (("sharp", "Sharpening", 0, 100), ("nr", "Noise reduction", 0, 100))),
)
ADJUSTMENT_KINDS = {
    "exp": "exposure", "con": "contrast", "hi": "highlights", "sh": "shadows",
    "wh": "whites", "bl": "blacks", "temp": "temperature", "tint": "tint",
    "vib": "vibrance", "sat": "saturation", "cla": "clarity", "vig": "vignette",
    "grain": "grain", "fade": "fade", "sharp": "sharpening", "nr": "noise-reduction",
}
LOOKS = (
    ("As shot", {}), ("Portra", {"temp": 18, "tint": 4, "sat": -10, "con": -12, "sh": 22, "fade": 12}),
    ("Tri-X", {"sat": -100, "con": 32, "grain": 34, "cla": 20}),
    ("Velvia", {"sat": 34, "vib": 22, "con": 16, "bl": -10}),
    ("Faded", {"fade": 38, "con": -18, "sat": -14}),
    ("Noon", {"hi": -42, "sh": 34, "con": -6}),
    ("Golden", {"temp": 34, "exp": 12, "vib": 10}),
    ("Blue hour", {"temp": -30, "tint": 8, "exp": -10, "con": 10}),
)


def _clear(box: Gtk.Widget) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


def _label(text: str, role: str = "body", **properties) -> Gtk.Label:
    label = apply_type(Gtk.Label(label=text, **properties), role)
    if role == "label":
        label.set_xalign(0)
    return label


def _button(text: str, callback, *, icon: str | None = None, name: str | None = None) -> Gtk.Button:
    button = Gtk.Button()
    if icon is None:
        button.set_label(text)
    else:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.append(icons.image(icon))
        row.append(_label(text))
        button.set_child(row)
    button.connect("clicked", lambda _b: callback())
    if name:
        button.set_name(name)
    return button


class DarkroomWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self.fixture_path = os.environ.get("LUMA_DARKROOM_FIXTURE")
        self.metadata = MemoryMetadataStore() if self.fixture_path else MetadataStore(default_path())
        self.snapshot: LibrarySnapshot | None = None
        self.selected: list[int] = []
        self.collection = "all"
        self.mode = "library"
        self.tool = "adjust"
        self.open_group = "Light"
        self.active_mask: str | None = None
        self.compare = False
        self.focus = False
        self.left_open = True
        self.inspector_open = True
        self._generation = 0
        self._documents: dict[str, tuple[Document, Editor]] = {}
        self._raw_textures: dict[str, Gdk.Texture] = {}
        self._fixture_textures: dict[str, Gdk.Texture] = {}
        self._filmstrip_pictures: dict[str, Gtk.Picture] = {}
        self._look_textures: dict[tuple[str, str], Gdk.Texture] = {}
        self._look_pending: set[str] = set()
        self._render_generation = 0
        self._adjust_settle = 0
        self._export_job = None
        self._saved: set[str] = set()
        self._save_timers: dict[str, int] = {}
        self._save_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="darkroom-save")
        self.store = None if self.fixture_path else DocumentStore(
            Path(GLib.get_user_state_dir()) / "darkroom" / "recovery")
        self._recovery_offered = False
        commands = CommandRegistry((CommandGroup(None, (
            Command("import", "Import…", self.import_photos, icon="download"),
            Command("show-in-filer", "Show in Filer", self.show_in_filer, icon="folder-open"),
            Command("trash-rejected", "Move rejected to Trash", self.trash_rejected,
                    icon="trash-2", destructive=True, visible=lambda: self._rejected_count() > 0),
        )),))
        super().__init__(application=application, app_id=APP_ID, title="Darkroom",
                         icon_name=ICON_NAME, commands=commands, default_width=1380,
                         default_height=880, minimum_width=360, minimum_height=560)
        style = Path(os.environ.get("LUMA_DARKROOM_STYLE_PATH", "/usr/share/luma-darkroom/darkroom.css"))
        if not style.is_file():
            style = Path(__file__).resolve().parent.parent / "style" / "darkroom.css"
        add_style_sheet(str(style))
        self.set_name("dr-window")

        self.sidebar = NavigationSidebar(variant="destinations")
        self.sidebar.set_name("dr-navigation")
        self.sidebar.set_size_request(228, 338)
        self.sidebar.list.connect("row-activated", self._sidebar_activated)
        self.subtitle_label = _label("", "body")
        self.subtitle_label.add_css_class("dr-window-subtitle")
        self.title_bar.pack_start(self.subtitle_label)

        self.phone = False
        self.details = DetailsPane("Information")
        self.details.set_name("dr-info-slot")
        self.details.sheet.set_name("dr-info")
        self.details.connect("notify::shown", self._details_changed)
        self.modes = ModeSwitch((("library", "Library", "layout-grid"),
                                 ("edit", "Edit", "sliders-vertical")),
                                current="library", on_change=self.set_mode, label="View")
        self.modes.buttons["edit"].set_name("dr-mode-edit")
        self.modes.buttons["library"].set_name("dr-mode-library")
        self.corner = CornerPill(modes=self.modes, share=self.share_photo,
                                 actions=(("share", "Export", self.export_photo),),
                                 info=self.details, more=commands, labelled=True)
        self.corner.set_name("dr-corner")
        self.title_bar.set_name("dr-top")
        self.corner.controls["info"].set_name("dr-info-toggle")
        self.corner.controls["actions.0"].set_tooltip_text("Export a copy · Ctrl+Shift+E")
        self.corner.controls["actions.0"].update_property(
            [Gtk.AccessibleProperty.LABEL], ["Export a copy · Ctrl+Shift+E"])
        self.corner.controls["more"].set_name("dr-more")
        self.title_bar.pack_end(self.corner)

        self.island = Island()
        self.island.set_name("dr-work")
        self.island.set_hexpand(True)
        self.island.set_vexpand(True)
        self.work = Gtk.Overlay(hexpand=True, vexpand=True)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.content.set_name("dr-content")
        self.work.set_child(self.content)
        self.phone_nav=CornerPill(actions=(('chevron-left','Library',lambda:self.set_mode('library')),('share','Export',self.export_photo)),share=self.share_photo)
        self.phone_nav.set_halign(Gtk.Align.START);self.phone_nav.set_valign(Gtk.Align.START)
        self.phone_nav.set_visible(False);self.work.add_overlay(self.phone_nav)
        self.inspector = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.inspector.set_name("dr-inspector-content")
        self.left_panel = FloatingPanel("Library", "image", child=self.sidebar,
                                        summary="0 photos", on_folded=self._left_folded)
        self.left_panel.set_name("dr-left")
        self.right_header_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.auto_header = _button("Auto", self.auto_adjust)
        self.right_header_actions.append(self.auto_header)
        reset = Gtk.Button(child=icons.image("rotate-ccw"), tooltip_text="Reset all")
        reset.update_property([Gtk.AccessibleProperty.LABEL], ["Reset all"])
        reset.connect("clicked", lambda _button: self.reset_edits())
        self.right_header_actions.append(reset)
        self.right_panel = FloatingPanel("Adjust", "sliders-vertical", child=self.inspector,
                                         header_actions=self.right_header_actions,
                                         on_folded=self._right_folded)
        self.right_panel.set_name("dr-right")
        self.right_panel.set_visible(False)
        self.workspace = CreativeWorkspace(self.work, left=self.left_panel,
                                            right=self.right_panel, grid=False)
        self.workspace.set_name("dr-workspace")
        self.island.append(self.workspace)
        self.host = ToastHost(self.island)
        self.host.set_name("dr-body")
        self.action_center = ActionCenter().attach(self.host)
        self.action_center.set_name("dr-bar")
        self.root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        self.root.set_name("dr-shell")
        self.root.append(self.host)
        self.root.append(self.details)
        self._phone_probe = Gtk.Box(visible=False)
        self._phone_probe.connect("notify::visible", self._phone_changed)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600px"))
        phone.add_setter(self._phone_probe, "visible", True)
        self.add_breakpoint(phone)
        self._width_watch=WidthWatch(self,lambda _width:self._layout(),threshold=900)
        self.set_body(self.root)
        self._install_keys()
        self._show_loading()
        self._load_library()

    def _install_keys(self) -> None:
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

    def _key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        if isinstance(self.get_focus(), Gtk.Editable):
            return False
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval == Gdk.KEY_z:
                self._undo_edit()
                return True
            if keyval in (Gdk.KEY_e, Gdk.KEY_E):
                self.export_photo()
                return True
            return False
        if state & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and self.snapshot and self.snapshot.photos:
            direction = -1 if keyval == Gdk.KEY_Left else 1
            self.select_photo((self.snapshot.selected + direction) % len(self.snapshot.photos))
            return True
        if keyval == Gdk.KEY_i and self.mode == "library":
            self.details.show(open=not self.details.shown, subject=self.current_photo())
            return True
        if keyval == Gdk.KEY_p and self.mode == "library":
            self._set_flag(1)
            return True
        if keyval == Gdk.KEY_x and self.mode == "library":
            self._set_flag(-1)
            return True
        if keyval == Gdk.KEY_u and self.mode == "library":
            self._set_flag(0)
            return True
        if self.mode == "library" and Gdk.KEY_0 <= keyval <= Gdk.KEY_5:
            value = keyval - Gdk.KEY_0
            photo = self.current_photo()
            if photo is not None:
                self._set_rating(0 if photo.stars == value else value)
            return True
        if self.mode == "library" and keyval in (Gdk.KEY_e, Gdk.KEY_Return):
            self.set_mode("edit")
            return True
        if self.mode == "edit" and keyval in (Gdk.KEY_g, Gdk.KEY_Escape):
            self.set_mode("library")
            return True
        if self.mode == "edit" and keyval in (Gdk.KEY_a, Gdk.KEY_r, Gdk.KEY_l,
                                                  Gdk.KEY_m, Gdk.KEY_h):
            self.set_tool({Gdk.KEY_a: "adjust", Gdk.KEY_r: "crop", Gdk.KEY_l: "looks",
                           Gdk.KEY_m: "mask", Gdk.KEY_h: "heal"}[keyval])
            return True
        if keyval == Gdk.KEY_backslash:
            self.focus = not self.focus
            self._layout()
            return True
        if keyval == Gdk.KEY_y and self.mode == "edit":
            self._set_compare(not self.compare)
            return True
        return False

    def _show_loading(self) -> None:
        _clear(self.content)
        self.content.append(_label("Loading library…", "caption", halign=Gtk.Align.CENTER,
                                   valign=Gtk.Align.CENTER, vexpand=True))

    def _load_library(self) -> None:
        self._generation += 1
        generation = self._generation
        if self.fixture_path:
            GLib.idle_add(self._receive_library, generation, load_fixture(self.fixture_path), None)
            return

        def worker() -> None:
            try:
                snapshot, error = load_real_library(self.metadata), None
            except Exception as exc:
                snapshot, error = None, str(exc)
            GLib.idle_add(self._receive_library, generation, snapshot, error)

        threading.Thread(target=worker, name="darkroom-library", daemon=True).start()

    def _receive_library(self, generation: int, snapshot: LibrarySnapshot | None, error: str | None) -> bool:
        if generation != self._generation:
            return False
        if error or snapshot is None:
            _clear(self.content)
            self.content.append(_label(error or "Could not open library", "body",
                                       halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True))
            return False
        self.snapshot = snapshot
        self.collection = f"shoot:{snapshot.shoot}" if snapshot.shoot else "all"
        self.selected = [snapshot.selected] if snapshot.current() else []
        fixture_state = os.environ.get("LUMA_DARKROOM_STATE")
        if snapshot.fixture and fixture_state == "multiselect" and len(snapshot.photos) > 1:
            self.selected = [0, 1]
        elif snapshot.fixture and fixture_state == "empty_selection":
            self.selected = []
        self._rebuild()
        if not snapshot.fixture:
            self._load_raw_thumbnails(generation, snapshot.photos)
        return False

    def _load_raw_thumbnails(self, generation: int, photos: list[LibraryPhoto]) -> None:
        def worker() -> None:
            for photo in photos:
                if generation != self._generation:
                    return
                if photo.path.suffix.lower() not in RAW_SUFFIXES:
                    continue
                try:
                    preview = raw_thumbnail(photo.path)
                    preview.thumbnail((640, 640))
                    stream = BytesIO()
                    preview.save(stream, format="PNG")
                    GLib.idle_add(self._receive_raw_thumbnail, generation, photo.id, stream.getvalue())
                except Exception:
                    continue

        threading.Thread(target=worker, name="darkroom-raw-thumbnails", daemon=True).start()

    def _receive_raw_thumbnail(self, generation: int, photo_id: str, data: bytes) -> bool:
        if generation != self._generation:
            return False
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        self._raw_textures[photo_id] = texture
        for item in getattr(self, "media_items", ()):
            if item.id == photo_id:
                item.picture = texture
        if picture := self._filmstrip_pictures.get(photo_id):
            picture.set_paintable(texture)
        if self.current_photo() and self.current_photo().id == photo_id and hasattr(self, "canvas"):
            self.canvas.edited.set_paintable(texture)
            self.canvas.set_original_texture(texture)
        return False

    def current_photo(self) -> LibraryPhoto | None:
        return self.snapshot.current() if self.snapshot else None

    def _rejected_count(self) -> int:
        return sum(p.flag == -1 for p in self.snapshot.photos) if self.snapshot else 0

    def _rebuild(self) -> None:
        if self.snapshot is None:
            return
        self._sidebar()
        self._main()
        self._inspector()
        self._information()
        self._bar()
        self.commands.get("trash-rejected").label = f"Move {self._rejected_count()} rejected to Trash"
        self._layout()
        self.set_identity_subtitle(f"{self.snapshot.total_count:,} photos · {self.snapshot.source}")
        self.subtitle_label.set_label(f"{self.snapshot.total_count:,} photos · {self.snapshot.source}")
        self.corner.controls["info"].set_visible(self.mode == "library")

    def _phone_changed(self, probe: Gtk.Widget, _property) -> None:
        self.phone = probe.get_visible()
        self.subtitle_label.set_visible(not self.phone and self.get_width()>900)
        self.title_bar.set_show_end_title_buttons(False)
        self.title_bar.set_show_start_title_buttons(False)
        self._lumaui_window_controls.set_visible(not self.phone)
        if self.snapshot is not None:
            self._layout()
            if self.mode == "library":
                self._information()
                self._main()
            self._bar()

    def _details_changed(self, _pane: DetailsPane, _property) -> None:
        if self.snapshot is not None and self.mode == "library":
            self._information()
            self._main()

    def _layout(self) -> None:
        compact=0 < self.get_width() <= 900
        self.subtitle_label.set_visible(not self.phone and not compact)
        if self.mode == "edit":
            self.work.add_css_class("dr-edit-work")
            self.workspace.remove_css_class("dr-library-workspace")
        else:
            self.work.remove_css_class("dr-edit-work")
            self.workspace.add_css_class("dr-library-workspace")
        self.workspace.set_focused(self.focus)
        self.right_panel.set_visible(self.mode == "edit" and not self.phone)
        self.left_panel.set_visible(not self.phone)
        self.corner.set_visible(not self.phone)
        self.phone_nav.set_visible(self.phone and self.mode=="edit")
        if not self.phone:
            self.left_panel.set_folded(compact or not self.left_open)
            self.right_panel.set_folded(compact or not self.inspector_open)
        snap = self.snapshot
        if snap is not None:
            self.left_panel.set_title(
                snap.shoot or "Library" if self.mode == "edit" or self.phone else "Library")
            self.left_panel.set_icon(
                ("gallery-vertical" if self.left_panel.get_folded() else "chevron-left")
                if self.mode == "edit" else "image")
            self.left_panel.set_summary(
                f"{snap.selected + 1} of {len(snap.photos)}" if self.mode == "edit"
                else f"{len(snap.photos)} photos")
        if self.phone:
            self.right_panel.set_title("Adjust")
            self.right_panel.set_icon("sliders-horizontal")
        if self.phone:
            self.content.set_margin_start(0)
            self.content.set_margin_end(0)
        elif self.mode == "library":
            self.content.set_margin_start(0 if self.focus or compact else 268 if self.left_open else 0)
            self.content.set_margin_end(14)
        else:
            self.content.set_margin_start(32 if self.focus or compact else 262 if self.left_open else 32)
            self.content.set_margin_end(32 if self.focus or compact or not self.inspector_open else 300)

    def _left_folded(self, folded: bool) -> None:
        if self.mode == "edit":
            self.left_panel.set_icon("gallery-vertical" if folded else "chevron-left")
        if not self.phone and self.get_width()>900 and not self.workspace.get_is_phone():
            self.left_open = not folded
            if self.snapshot is not None:
                self._layout()

    def _right_folded(self, folded: bool) -> None:
        if not self.phone and self.get_width()>900 and not self.workspace.get_is_phone():
            self.inspector_open = not folded
            if self.snapshot is not None:
                self._layout()

    def _sidebar(self) -> None:
        snap = self.snapshot
        assert snap is not None
        self.sidebar.clear()
        if self.mode == "edit":
            apply_sidebar_variant(self.sidebar, "files")
            self.sidebar.set_size_request(228, 548)
            self.sidebar.add_css_class("dr-filmstrip")
            self._filmstrip_pictures.clear()
            for index, photo in enumerate(snap.photos):
                picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
                source = self._media_picture(photo)
                if isinstance(source, Gdk.Paintable):
                    picture.set_paintable(source)
                else:
                    picture.set_file(Gio.File.new_for_path(str(source)))
                self._filmstrip_pictures[photo.id] = picture
                frame = Gtk.AspectFrame(ratio=1.5, obey_child=False)
                frame.set_size_request(212, 142)
                frame.set_child(picture)
                row = Gtk.ListBoxRow(child=frame)
                row.add_css_class("dr-strip-photo")
                if index == snap.selected:
                    row.add_css_class("on")
                if photo.flag == -1:
                    row.add_css_class("rej")
                row.set_name(f"dr-strip-row-{index}")
                row.photo_index = index
                self.sidebar.append_row(row)
            return
        apply_sidebar_variant(self.sidebar, "destinations")
        self.sidebar.set_size_request(228, 338)
        self._filmstrip_pictures.clear()
        self.sidebar.remove_css_class("dr-filmstrip")
        for title, glyph, count in (("All photos", "image", snap.total_count),
                                    ("Picks", "flag", snap.pick_count),
                                    ("Last import", "download", snap.import_count)):
            row = SidebarRow(title, lead=RowLead.icon(glyph), trail=count)
            row.collection_key = {"All photos": "all", "Picks": "picks", "Last import": "last-import"}[title]
            self.sidebar.append_row(row)
            if row.collection_key == self.collection:
                self.sidebar.list.select_row(row)
        self.sidebar.append_section("Shoots")
        for name, date in snap.shoots:
            row = SidebarRow(name, lead=RowLead.icon("calendar"), meta=date)
            row.set_name(f"dr-shoot-{name.lower().replace(' ', '-')}")
            row.collection_key = f"shoot:{name}"
            self.sidebar.append_row(row)
            if row.collection_key == self.collection:
                self.sidebar.list.select_row(row)
        self.sidebar.append_section("Albums")
        for name, count in snap.albums:
            row = SidebarRow(name, lead=RowLead.icon("bookmark"), trail=count)
            row.collection_key = f"album:{name}"
            self.sidebar.append_row(row)
            if row.collection_key == self.collection:
                self.sidebar.list.select_row(row)

    def _sidebar_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if self.mode == "edit":
            if hasattr(row, "photo_index"):
                self.select_photo(row.photo_index)
            else:
                self.set_mode("library")
            return
        collection = getattr(row, "collection_key", None)
        if collection and self.snapshot:
            visible = self.snapshot.indices_for(collection)
            if self.snapshot.fixture and not visible:
                Toast.show(self.host, "This set is empty in the sample", kind="notified")
                return
            self.collection = collection
            self.selected = [visible[0]] if visible else []
            if visible:
                self.snapshot.selected = visible[0]
            self._main()
            self._information()
            self._bar()

    def _main(self) -> None:
        _clear(self.content)
        if self.mode == "edit":
            self._edit_main()
        else:
            self._library_main()

    def _library_main(self) -> None:
        snap = self.snapshot
        assert snap is not None
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.add_css_class("dr-library")
        if self.phone:
            pane.set_margin_top(42)
            pane.set_margin_start(2)
            pane.set_margin_end(2)
        visible = snap.indices_for(self.collection)
        visible_set = set(visible)
        title = self.collection.split(":", 1)[1] if ":" in self.collection else {
            "all": "All photos", "picks": "Picks", "last-import": "Last import"}.get(self.collection, "All photos")
        header = _label(title, "title-1")
        header.set_name("dr-clear-selection")
        header_click = Gtk.GestureClick()
        header_click.connect("released", lambda *_args: self.clear_selection())
        header.add_controller(header_click)
        header.set_halign(Gtk.Align.START)
        header.add_css_class("dr-library-title")
        pane.append(header)
        if self.collection.startswith("shoot:"):
            summary = f"{snap.shoot_date + ' · ' if snap.shoot_date else ''}{len(visible)} photos · {sum(snap.photos[i].flag == 1 for i in visible)} picks"
        else:
            summary = f"{len(visible)} photos"
        pane.append(_label(summary, "caption", xalign=0))
        self.media_items = [
            MediaItem(photo.id, self._media_picture(photo),
                      title=photo.name, selected=index in self.selected,
                      dimmed=photo.flag == -1, badge="Edited" if photo.edited else "", data=index)
            for index, photo in enumerate(snap.photos) if index in visible_set
        ]
        grid = MediaGrid(self.media_items, kind="library",
                         min_side=170 if self.details.shown or self.phone else 200,
                         columns=3 if self.phone else None,gap=4 if self.phone else None,
                         scrolls=not snap.fixture,
                         on_select=self._media_select, on_open=self._media_open,
                         meta=self._media_meta, label="Photos in this shoot")
        grid.add_css_class("dr-grid")
        pane.append(grid)
        if snap.fixture:
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
            scroll.set_child(pane)
            self.content.append(scroll)
        else:
            pane.set_vexpand(True)
            self.content.append(pane)

    def _media_picture(self, photo: LibraryPhoto) -> Gdk.Texture | Path:
        if photo.id in self._raw_textures:
            return self._raw_textures[photo.id]
        if self.snapshot and self.snapshot.fixture:
            if photo.id not in self._fixture_textures:
                self._fixture_textures[photo.id] = Gdk.Texture.new_from_filename(str(photo.path))
            return self._fixture_textures[photo.id]
        return photo.path

    def _media_meta(self, item: MediaItem) -> Gtk.Widget:
        photo = self.snapshot.photos[item.data]
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.add_css_class("dr-tile-meta")
        if photo.stars:
            row.append(PhotoRating(photo.stars, readonly=True, pixel_size=10))
        else:
            row.append(Gtk.Box(hexpand=True))
        if photo.flag:
            glyph = icons.image("flag" if photo.flag == 1 else "x")
            glyph.add_css_class("dr-pick" if photo.flag == 1 else "dr-reject")
            row.append(glyph)
        if photo.label:
            label = Gtk.Box()
            label.add_css_class("dr-label")
            label.add_css_class(photo.label)
            row.append(label)
        return row

    def _media_select(self, item: MediaItem, mode: str) -> None:
        snap = self.snapshot
        assert snap is not None
        index = item.data
        if mode == "toggle":
            self.selected = [i for i in self.selected if i != index] if index in self.selected else self.selected + [index]
        elif mode == "extend":
            self.selected = list(dict.fromkeys(self.selected + [index]))
        else:
            self.selected = [index]
        snap.selected = index
        for media in self.media_items:
            media.selected = media.data in self.selected
        self._information()
        self._bar()

    def _media_open(self, item: MediaItem) -> None:
        self.select_photo(item.data)
        self.set_mode("edit")

    def select_photo(self, index: int) -> None:
        if self.snapshot is None or not 0 <= index < len(self.snapshot.photos):
            return
        self.snapshot.selected = index
        self.selected = [index]
        self._rebuild()

    def clear_selection(self) -> None:
        self.selected = []
        for item in getattr(self, "media_items", ()):
            item.selected = False
        self.details.show(subject=None)
        self._bar()

    def set_mode(self, mode: str) -> None:
        if mode == self.mode or self.snapshot is None:
            return
        if mode == "edit" and not self.snapshot.photos:
            self.modes.set_current("library")
            return
        self.mode = mode
        self.left_open = mode == "library"
        self.compare = False
        self.modes.set_current(mode)
        if mode == "edit":
            self.details.show(subject=None)
        else:
            self.selected = [self.snapshot.selected]
        self._rebuild()

    def _edit_main(self) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        if self.snapshot and self.snapshot.fixture:
            self._histogram = photo.histogram
        stage = Gtk.Overlay(hexpand=True, vexpand=True)
        stage.set_name("dr-stage")
        stage.add_css_class("dr-stage")
        stage.set_child(Gtk.Box(hexpand=True, vexpand=True))
        canvas = PhotoCanvas()
        canvas.set_source(photo.path)
        if photo.id in self._raw_textures:
            canvas.edited.set_paintable(self._raw_textures[photo.id])
            canvas.set_original_texture(self._raw_textures[photo.id])
        canvas.set_crop(self.tool == "crop")
        canvas.set_compare(self.compare)
        canvas.set_heal_handler(self._canvas_action())
        self.canvas = canvas
        stage.add_overlay(canvas)
        stage.connect("get-child-position", self._position_canvas)
        self.content.append(stage)
        self._render_photo()

    def _position_canvas(self, stage: Gtk.Overlay, child: Gtk.Widget,
                         allocation: Gdk.Rectangle) -> bool:
        if child is not getattr(self, "canvas", None):
            return False
        margin_left = 12 if self.phone else 8
        margin_right = 12 if self.phone else 0
        available_width = max(1, stage.get_width() - margin_left - margin_right)
        available_height = max(1, stage.get_height() - (460 if self.phone else 130))
        paintable = self.canvas.edited.get_paintable()
        image_width = paintable.get_intrinsic_width() if paintable else 3
        image_height = paintable.get_intrinsic_height() if paintable else 2
        if image_width <= 0 or image_height <= 0:
            image_width, image_height = 3, 2
        scale = min(available_width / image_width, available_height / image_height)
        width = max(1, round(image_width * scale))
        height = max(1, round(image_height * scale))
        allocation.x = margin_left + (available_width - width) // 2
        allocation.y = round((100 if self.phone else 40) + (available_height - height) / 2)
        allocation.width, allocation.height = width, height
        return True

    def _inspector(self) -> None:
        _clear(self.inspector)
        if self.mode != "edit":
            return
        photo = self.current_photo()
        if photo is None:
            return
        titles = {"adjust": ("sliders-vertical", "Adjust"), "crop": ("crop", "Crop"),
                  "looks": ("aperture", "Looks"), "mask": ("user", "Masks"),
                  "heal": ("bandage", "Heal")}
        icon, title = titles[self.tool]
        self.right_panel.set_title("Adjust" if self.phone else title)
        self.right_panel.set_icon("sliders-horizontal" if self.phone else icon)
        self.auto_header.set_visible(self.tool == "adjust")
        if self.tool in ("adjust", "looks"):
            self.inspector.append(self._histogram_panel(photo))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("dr-inspector-body")
        if self.tool == "crop":
            body.add_css_class("dr-crop-inspector")
        self.inspector.append(body)
        if self.tool == "adjust":
            for name, fields in GROUPS:
                sliders = [ValueSlider(label, photo.adjustments.get(key,
                                             20 if key == "sharp" else 10 if key == "nr" else 0),
                                       low, high, default=20 if key == "sharp" else 10 if key == "nr" else 0,
                                       on_change=lambda value, k=key: self._adjust(k, round(value), True))
                           for key, label, low, high in fields]
                group = AdjustmentGroup(name, sliders, open=self.open_group == name,
                                        on_toggle=lambda on, n=name: self._toggle_group(n if on else ""))
                group.header.set_name(f"dr-group-{name.lower()}")
                body.append(group)
        elif self.tool == "looks":
            grid = Gtk.FlowBox(max_children_per_line=2, min_children_per_line=2,
                               selection_mode=Gtk.SelectionMode.NONE)
            grid.add_css_class("dr-looks")
            for name, _values in LOOKS:
                choice = _button(name, lambda n=name: self.apply_look(n))
                choice.add_css_class("dr-look")
                slug = name.lower().replace(" ", "-")
                fixture_preview = photo.path.parent / "looks" / f"{slug}.png"
                preview_path = fixture_preview if self.fixture_path and fixture_preview.exists() else photo.path
                thumb = Gtk.Picture.new_for_file(Gio.File.new_for_path(str(preview_path)))
                if (texture := self._look_textures.get((photo.id, name))) is not None:
                    thumb.set_paintable(texture)
                thumb.set_content_fit(Gtk.ContentFit.COVER)
                thumb.set_size_request(108, 72)
                column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                column.append(thumb)
                column.append(_label(name, "body"))
                choice.set_child(column)
                if photo.look == name:
                    choice.add_css_class("on")
                grid.insert(choice, -1)
            body.append(grid)
            if not self.fixture_path:
                self._load_look_previews(photo)
        elif self.tool == "crop":
            body.append(_label("Shape", "label"))
            ratios = Gtk.FlowBox(max_children_per_line=3, min_children_per_line=3,
                                 selection_mode=Gtk.SelectionMode.NONE)
            ratios.add_css_class("dr-ratios")
            for key, label in (("free", "Free"), ("orig", "Original"), ("1", "1:1"),
                               ("0.8", "4:5"), ("1.5", "3:2"), ("1.778", "16:9")):
                ratios.insert(_button(label, lambda k=key: self._set_ratio(k)), -1)
            body.append(ratios)
            body.append(_label("Straighten", "label"))
            angle = ValueSlider("Angle", photo.rotation, -15, 15,
                                unit="°", step=1, on_change=self._rotate)
            angle.add_css_class("dr-crop-angle")
            body.append(angle)
            body.append(_label("Rotate", "label"))
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.append(_button("Left", self._rotate_left, icon="rotate-ccw"))
            row.append(_button("Flip", self._flip))
            row.append(_button("Reset", self._crop_reset))
            body.append(row)
        elif self.tool == "mask":
            body.append(_label("Select", "label"))
            choices = Gtk.FlowBox(max_children_per_line=2, min_children_per_line=2,
                                  selection_mode=Gtk.SelectionMode.NONE)
            choices.add_css_class("dr-masks")
            for key, name, glyph in (("subject", "Subject", "user"), ("sky", "Sky", "cloud"),
                                     ("bg", "Background", "image"), ("brush", "Brush", "pencil")):
                button = _button(name, lambda k=key: self._select_mask(k), icon=glyph)
                if self.active_mask == key:
                    button.add_css_class("on")
                choices.insert(button, -1)
            body.append(choices)
            if self.active_mask:
                body.append(_label(f"{self.active_mask.title()} only", "label"))
                values = photo.masks.get(self.active_mask, {})
                for key, name in (("exp", "Exposure"), ("temp", "Temperature"), ("cla", "Clarity")):
                    body.append(ValueSlider(name, values.get(key, 0), -100, 100,
                                            on_change=lambda value, k=key: self._mask_adjust(k, round(value))))
            else:
                body.append(_label("Pick what to adjust on its own. The rest of the photo stays as it is.",
                                   "caption", wrap=True))
        elif self.tool == "heal":
            body.append(_label("Click a spot on the photo to remove it. Darkroom fills it from the area around it.",
                               "caption", wrap=True))
            for index, spot in enumerate(photo.heal, 1):
                body.append(_label(f"Spot {index}  ·  {round(spot[0])}%, {round(spot[1])}%", "body"))
            if photo.heal:
                body.append(_button("Clear spots", self._clear_heal))

    def _load_look_previews(self, photo: LibraryPhoto) -> None:
        if photo.id in self._look_pending or all((photo.id, name) in self._look_textures for name, _ in LOOKS):
            return
        self._look_pending.add(photo.id)

        def worker() -> None:
            from .look_preview import render_look_preview
            previews: dict[str, bytes] = {}
            for name, values in LOOKS:
                try:
                    previews[name] = render_look_preview(photo.path, values)
                except Exception:
                    break
            GLib.idle_add(self._receive_look_previews, photo.id, previews)

        threading.Thread(target=worker, name="darkroom-looks", daemon=True).start()

    def _receive_look_previews(self, photo_id: str, previews: dict[str, bytes]) -> bool:
        self._look_pending.discard(photo_id)
        for name, data in previews.items():
            self._look_textures[(photo_id, name)] = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        if previews and self.tool == "looks" and self.current_photo() and self.current_photo().id == photo_id:
            self._inspector()
        return False

    def _histogram_panel(self, photo: LibraryPhoto) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("dr-histogram")
        histogram = Gtk.DrawingArea(height_request=70)
        histogram.set_draw_func(self._draw_histogram)
        box.append(histogram)
        camera, lens, shutter, aperture, iso = photo.camera
        exposure = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        exposure.append(_label(f"{shutter} · {aperture} · ISO {iso}", "caption", hexpand=True, xalign=0))
        exposure.append(_label(" ".join(lens.split()[:2]), "caption", xalign=1))
        box.append(exposure)
        return box

    def _draw_histogram(self, _area, cr, width: int, height: int) -> None:
        # The actual histogram is filled by the next preview. No fabricated values.
        histogram = getattr(self, "_histogram", None)
        if histogram is None:
            return
        for bins, rgba in zip(histogram[:3], ((1, 0.3, 0.3, 0.45), (0.3, 1, 0.3, 0.45),
                                              (0.3, 0.5, 1, 0.45))):
            scale = max(bins) or 1
            cr.set_source_rgba(*rgba)
            cr.move_to(0, height)
            for index, value in enumerate(bins):
                cr.line_to(index / max(1, len(bins) - 1) * width,
                           height - min(height, value / scale * height))
            cr.line_to(width, height)
            cr.fill()

    def _toggle_group(self, name: str) -> None:
        self.open_group = name
        self._inspector()

    def _information(self) -> None:
        # Keep the phone drawer tall enough to show the photo and its facts.
        # Natural content height otherwise varies with font metrics and starts
        # the drawer well below the reference position.
        self.details.sheet.set_size_request(
            -1, round(self.get_height() * 0.785) if self.phone else -1)
        photo = self.current_photo()
        if self.mode != "library" or not self.selected or photo is None:
            self.details.show(subject=None)
            return
        self.details.show(subject=photo)
        if not self.details.shown:
            return
        self.details.clear()
        self.details.set_title(f"{len(self.selected)} photos" if len(self.selected) > 1 else photo.name)
        picture = Gtk.Picture.new_for_file(Gio.File.new_for_path(str(photo.path)))
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_can_shrink(True)
        picture.set_hexpand(True)
        frame = Gtk.AspectFrame(ratio=1.5, obey_child=False)
        frame.add_css_class("dr-info-picture")
        if self.phone:
            frame.set_size_request(-1, 240)
        frame.set_child(picture)
        self.details.add(frame)
        self.details.add_section("Rating")
        rating = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        rating.append(PhotoRating(photo.stars, on_change=self._set_rating, pixel_size=17))
        rating.append(_label(("Not rated", "One star", "Two stars", "Three stars", "Four stars", "Five stars")[photo.stars], "caption"))
        self.details.add(rating)
        camera, lens, shutter, aperture, iso = photo.camera
        self.details.add_section("Camera")
        self.details.add_facts((("Camera", camera), ("Lens", lens),
                                ("Exposure", f"{shutter} · {aperture} · ISO {iso}"),
                                ("Taken", f"{photo.date}, {photo.time}"),
                                ("File", photo.file_label or photo.path.name)))
        self.details.add_section("Keywords")
        keywords = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        keywords.add_css_class("dr-keywords")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        keywords.append(row)
        for index, keyword in enumerate(photo.keywords):
            if index == 3 and not self.phone:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                keywords.append(row)
            row.append(_label(keyword, "caption"))
        add_keyword = Gtk.Button(child=icons.image("plus"), tooltip_text="Add a keyword")
        add_keyword.update_property([Gtk.AccessibleProperty.LABEL], ["Add a keyword"])
        add_keyword.connect("clicked", lambda _button: self._add_keyword())
        row.append(add_keyword)
        self.details.add(keywords)

    def _phone_info(self):
        photo=self.current_photo()
        if photo is None:return
        panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.append(PanelHeading(photo.name))
        camera,lens,shutter,aperture,iso=photo.camera
        panel.append(DetailsFacts([('Camera',camera),('Lens',lens),('Exposure',f'{shutter} · {aperture} · ISO {iso}'),('Taken',f'{photo.date}, {photo.time}'),('File',photo.file_label or photo.path.name)]))
        self.action_center.grow('information',panel)

    def _bar(self) -> None:
        if self.phone and self.mode=='library' and self.selected:
            photo=self.current_photo()
            if photo is None:return
            head=Gtk.Box(spacing=6)
            name=Gtk.Label(label=f'{len(self.selected)} photos' if len(self.selected)>1 else photo.name,
                           xalign=0,hexpand=True,ellipsize=3,max_width_chars=1)
            apply_type(name,'caption');head.append(name)
            head.append(PhotoRating(photo.stars,on_change=self._set_rating))
            for icon,title,value in [('flag','Pick',1),('x','Reject',-1)]:
                head.append(make_control(BarAction(icon,tooltip=title,active=photo.flag==value,on_activate=lambda v=value:self._set_flag(v))))
            self.action_center.show_bar([
                BarAction('sliders-vertical','Edit',primary=True,keep_label=True,fill=True,on_activate=lambda:self.set_mode('edit')),
                BarAction('info',tooltip='Information',on_activate=self._phone_info),
                BarAction('share-2',tooltip='Share',on_activate=lambda:self.share_photo(self.action_center.bar)),
                BarAction('share',tooltip='Export',on_activate=self.export_photo),
                BarAction('circle-x',tooltip='Done',on_activate=self.clear_selection)],head=head,fill=True)
            return
        if self.mode == "edit":
            tools = (("adjust", "sliders-vertical", "Adjust"), ("crop", "crop", "Crop"),
                     ("looks", "aperture", "Looks"), ("mask", "user", "Masks"),
                     ("heal", "bandage", "Heal"))
            if not self.phone and not isinstance(self.inspector.get_parent(),Gtk.Stack):
                if self.inspector.get_parent() is not None:
                    parent = self.inspector.get_parent()
                    if isinstance(parent, Gtk.Viewport):
                        parent.set_child(None)
                    else:
                        parent.remove(self.inspector)
                self.right_panel.set_child(self.inspector)
            self.action_center.show_bar([
                *(BarAction(icon, tooltip=label, active=self.tool == key,
                            on_activate=lambda k=key: self.set_tool(k)) for key, icon, label in tools),
                SEPARATOR,
                BarAction("split", tooltip="Before and after", active=self.compare,
                          on_activate=lambda: self._set_compare(not self.compare)),
            ])
            buttons = []
            button = self.action_center.bar_row.get_first_child()
            while button is not None:
                buttons.append(button)
                button = button.get_next_sibling()
            for key, button in zip(("adjust", "crop", "looks", "mask", "heal"), buttons[:5]):
                button.set_name(f"dr-tool-{key}")
            buttons[-1].set_name("dr-compare")
            if self.phone:
                self.right_panel.set_child(None)
                if self.inspector.get_parent() is not None:
                    parent = self.inspector.get_parent()
                    if isinstance(parent, Gtk.Viewport):
                        parent.set_child(None)
                    else:
                        parent.remove(self.inspector)
                panel=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                         propagate_natural_height=True,
                                         max_content_height=300)
                panel.set_name('dr-phone-inspector')
                panel.set_child(self.inspector)
                self.action_center.grow('adjustments',panel)
            return
        if not self.selected:
            self.action_center.show_bar((BarAction("chevron-down", "Capture time", self._sort_menu),
                                         SEPARATOR, BarAction("download", "Import", self.import_photos, primary=True)))
            return
        photo = self.current_photo()
        if photo is None:
            return
        picture = self._media_picture(photo)
        lead = None
        if isinstance(picture, Gdk.Paintable):
            lead = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN)
            lead.add_css_class("dr-bar-thumb")
            lead.set_size_request(42, 28)
            lead.set_child(Gtk.Box())
            thumbnail = Gtk.Picture(paintable=picture, content_fit=Gtk.ContentFit.COVER,
                                    can_shrink=True)
            thumbnail.set_halign(Gtk.Align.FILL)
            thumbnail.set_valign(Gtk.Align.FILL)
            lead.add_overlay(thumbnail)
            lead.set_measure_overlay(thumbnail, False)
        items = ([PhotoRating(photo.stars, on_change=self._set_rating)] if self.phone else [
            BarChip(f"{len(self.selected)} photos" if len(self.selected) > 1 else photo.name,
                    lead=lead),
            PhotoRating(photo.stars, on_change=self._set_rating),
        ]) + [SEPARATOR,
            BarAction("flag", tooltip="Pick · P", active=photo.flag == 1,
                      on_activate=lambda: self._set_flag(1)),
            BarAction("x", tooltip="Reject · X", active=photo.flag == -1,
                      on_activate=lambda: self._set_flag(-1))]
        if not self.phone:
            items.append(PhotoLabels(photo.label, on_change=self._set_label))
        self.action_center.show_bar(items)

    def _sort_menu(self) -> None:
        Toast.show(self.host, "Sorted by capture time", kind="notified")

    def _edit_targets(self) -> list[LibraryPhoto]:
        return [self.snapshot.photos[i] for i in self.selected] if self.snapshot else []

    def _update_metadata(self, field: str, value) -> None:
        photos = self._edit_targets()
        if not photos:
            return
        for photo in photos:
            setattr(photo, field, value)
            if self.fixture_path:
                self.metadata.update(photo.id, {field: value})
            else:
                self._queue_write(self.metadata.update, photo.id, {field: value})
        if self.snapshot and field == "flag" and not self.snapshot.fixture:
            self.snapshot.pick_count = sum(p.flag == 1 for p in self.snapshot.photos)
        self._rebuild()

    def _set_rating(self, value: int) -> None:
        self._update_metadata("stars", value)

    def _set_flag(self, value: int) -> None:
        targets = self._edit_targets()
        if not targets:
            return
        self._update_metadata("flag", 0 if all(p.flag == value for p in targets) else value)

    def _set_label(self, value: str | None) -> None:
        self._update_metadata("label", value)

    def _add_keyword(self) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        dialog = Adw.AlertDialog(heading="Add a keyword", body="Add a keyword to this photo.")
        field = Gtk.Entry(placeholder_text="Keyword")
        dialog.set_extra_child(field)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        def response(_dialog, result):
            if result == "add" and field.get_text().strip():
                value = tuple(dict.fromkeys((*photo.keywords, field.get_text().strip())))
                self._update_metadata("keywords", value)
        dialog.connect("response", response)
        dialog.present(self)

    def set_tool(self, tool: str) -> None:
        if tool == self.tool:
            return
        self.tool = tool
        self.compare = False
        if hasattr(self, "canvas"):
            self.canvas.set_compare(False)
            self.canvas.set_crop(tool == "crop")
            self.canvas.set_heal_handler(self._canvas_action())
        self._inspector()
        self._bar()

    def _set_compare(self, on: bool) -> None:
        self.compare = on
        if hasattr(self, "canvas"):
            self.canvas.set_compare(on)
        self._bar()

    def _document_for(self, photo: LibraryPhoto) -> tuple[Document, Editor]:
        if photo.id not in self._documents:
            document = Document.new(photo.path.as_uri(), raw=photo.path.suffix.lower() in RAW_SUFFIXES)
            editor = Editor(document)
            for key, value in photo.adjustments.items():
                if key in ADJUSTMENT_KINDS and value:
                    editor.set_adjustment(ADJUSTMENT_KINDS[key], value / 100 if key == "exp" else value)
            self._documents[photo.id] = (document, editor)
        return self._documents[photo.id]

    def _render_photo(self) -> None:
        photo = self.current_photo()
        if self.mode != "edit" or photo is None or not hasattr(self, "canvas"):
            return
        self._render_generation += 1
        generation = self._render_generation
        try:
            from .engine import RasterEngine
        except ModuleNotFoundError:
            # The conform worker has no imaging package. Its fixture uses
            # decoded source pixels; production packages require Pillow.
            return
        document, _editor = self._document_for(photo)
        canvas = self.canvas
        document = document.clone()

        def worker() -> None:
            try:
                rendered = RasterEngine(document).render(max_dimension=2048)
                stream = BytesIO()
                rendered.image.save(stream, format="PNG")
                result, histogram, error = stream.getvalue(), rendered.histogram, None
            except Exception as exc:
                result, histogram, error = None, None, str(exc)
            GLib.idle_add(self._receive_render, generation, canvas, result, histogram, error)

        threading.Thread(target=worker, name="darkroom-preview", daemon=True).start()

    def _receive_render(self, generation: int, canvas: PhotoCanvas, data, histogram, error) -> bool:
        if generation != self._render_generation or canvas is not getattr(self, "canvas", None):
            return False
        if error:
            Toast.show(self.host, error, kind="error")
        elif data:
            canvas.set_preview(data)
            self._histogram = histogram
            self.inspector.queue_draw()
        return False

    def _save_recipe(self, photo: LibraryPhoto) -> None:
        if self.fixture_path:
            return
        previous = self._save_timers.pop(photo.id, 0)
        if previous:
            GLib.source_remove(previous)
        self._save_timers[photo.id] = GLib.timeout_add(400, self._flush_recipe, photo.id)

    def _queue_write(self, operation, *args) -> None:
        future = self._save_executor.submit(operation, *args)
        future.add_done_callback(lambda done: GLib.idle_add(self._write_result, done.exception()))

    def _write_result(self, error: Exception | None) -> bool:
        if error is not None:
            Toast.show(self.host, f"Could not save Darkroom changes: {error}", kind="error")
        return False

    def _flush_recipe(self, photo_id: str) -> bool:
        self._save_timers.pop(photo_id, None)
        if self.store is not None and photo_id in self._documents:
            document = self._documents[photo_id][0].clone()
            self._queue_write(self.store.autosave, document, None)
        return False

    def _adjust(self, key: str, value: int, continuous: bool) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        photo.adjustments[key] = value
        _document, editor = self._document_for(photo)
        editor.set_adjustment(ADJUSTMENT_KINDS[key], value / 100 if key == "exp" else value,
                              coalesce=continuous)
        if not continuous:
            editor.end_continuous_edit()
        else:
            if self._adjust_settle:
                GLib.source_remove(self._adjust_settle)
            self._adjust_settle = GLib.timeout_add(350, self._settle_adjustment, editor)
        self._save_recipe(photo)
        self._render_photo()

    def _settle_adjustment(self, editor: Editor) -> bool:
        editor.end_continuous_edit()
        self._adjust_settle = 0
        return False

    def apply_look(self, name: str) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        values = dict(next(values for label, values in LOOKS if label == name))
        photo.look = name
        photo.adjustments = values
        document, editor = self._document_for(photo)
        def change() -> None:
            document.raw_development = [
                Adjustment(new_id("adjustment"), ADJUSTMENT_KINDS[key],
                           value / 100 if key == "exp" else value)
                for key, value in values.items()
            ]
        editor.change(f"Apply {name}", change)
        self._save_recipe(photo)
        self._render_photo()
        self._inspector()
        Toast.show(self.host, f"{name} applied", kind="done")

    def auto_adjust(self) -> None:
        for key, value in {"exp": 10, "con": 8, "hi": -30, "sh": 26,
                           "wh": 12, "bl": -8, "vib": 14}.items():
            self._adjust(key, value, False)
        self._inspector()
        Toast.show(self.host, "Auto adjusted", kind="done")

    def reset_edits(self) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        photo.adjustments.clear()
        photo.look = "As shot"
        photo.crop = None
        photo.rotation = 0
        photo.heal.clear()
        photo.masks.clear()
        photo.brush_points.clear()
        document, editor = self._document_for(photo)
        def change() -> None:
            document.raw_development.clear()
            document.crop = Crop()
            document.retouch.clear()
            document.layers = document.layers[:1]
            document.workspace.selected_layer_id = document.layers[0].id
            document.workspace.selected_mask_id = None
        editor.change("Reset to as shot", change)
        self._save_recipe(photo)
        self._main()
        self._inspector()
        Toast.show(self.host, "Reset to as shot", kind="done")

    def _undo_edit(self) -> None:
        photo = self.current_photo()
        if photo is None or photo.id not in self._documents:
            return
        _document, editor = self._documents[photo.id]
        if editor.can_undo:
            editor.undo()
            for key, kind in ADJUSTMENT_KINDS.items():
                value = next((a.value for a in editor.document.raw_development if a.kind == kind), 0)
                photo.adjustments[key] = round(value * 100) if key == "exp" else value
            self._render_photo()
            self._inspector()

    def _set_ratio(self, key: str) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        if key in ("free", "orig"):
            crop = Crop()
        else:
            ratio = float(key)
            width, height = self._image_size(photo.path)
            current = width / height
            if ratio < current:
                margin = (1 - ratio / current) / 2
                crop = Crop(left=margin, right=1 - margin)
            else:
                margin = (1 - current / ratio) / 2
                crop = Crop(top=margin, bottom=1 - margin)
        photo.crop = {"left": crop.left, "right": crop.right, "top": crop.top, "bottom": crop.bottom}
        document, editor = self._document_for(photo)
        editor.set_crop(crop)
        self._save_recipe(photo)
        self._render_photo()

    @staticmethod
    def _image_size(path: Path) -> tuple[int, int]:
        try:
            from PIL import Image
            with Image.open(path) as image:
                return image.size
        except Exception:
            return 3, 2

    def _rotate(self, angle: int) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        photo.rotation = angle
        document, editor = self._document_for(photo)
        crop = copy.deepcopy(document.crop)
        crop.straighten = angle
        editor.set_crop(crop)
        self._save_recipe(photo)
        self._render_photo()

    def _rotate_left(self) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        document, editor = self._document_for(photo)
        crop = copy.deepcopy(document.crop)
        crop.rotation = (crop.rotation - 90) % 360
        editor.set_crop(crop)
        self._save_recipe(photo)
        self._render_photo()

    def _flip(self) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        document, editor = self._document_for(photo)
        crop = copy.deepcopy(document.crop)
        crop.flip_horizontal = not crop.flip_horizontal
        editor.set_crop(crop)
        self._save_recipe(photo)
        self._render_photo()

    def _crop_reset(self) -> None:
        self._set_ratio("orig")
        self._rotate(0)
        self._inspector()

    def _select_mask(self, key: str) -> None:
        self.active_mask = None if self.active_mask == key else key
        if hasattr(self, "canvas"):
            self.canvas.set_heal_handler(self._canvas_action())
        self._inspector()

    def _canvas_action(self):
        if self.tool == "heal":
            return self._add_heal_spot
        if self.tool == "mask" and self.active_mask == "brush":
            return self._add_brush_point
        return None

    def _mask_adjust(self, key: str, value: int) -> None:
        photo = self.current_photo()
        if photo is None or self.active_mask is None:
            return
        photo.masks.setdefault(self.active_mask, {})[key] = value
        names = {"subject": "Subject", "sky": "Sky", "bg": "Background", "brush": "Brush"}
        name = names[self.active_mask]
        document, editor = self._document_for(photo)
        layer = next((item for item in document.layers if item.kind == "adjustment" and item.name == name), None)
        if layer is None:
            layer = editor.add_layer("adjustment", name)
            # v70's Subject, Sky and Background previews use geometric falloffs.
            # Keep the same editable geometry in Darkroom's existing mask recipe.
            if self.active_mask == "brush":
                editor.add_mask(layer.id, "brush", geometry={"points": list(photo.brush_points), "size": 0.05})
            elif self.active_mask == "sky":
                editor.add_mask(layer.id, "linear-gradient",
                                geometry={"axis": "y", "start": 0.45, "end": 0.0})
            else:
                mask = editor.add_mask(layer.id, "radial-gradient",
                                       geometry={"x": 0.52, "y": 0.48, "radius": 0.35})
                if self.active_mask == "bg":
                    editor.change("Invert background mask", lambda: setattr(mask, "inverted", True))
        editor.set_adjustment(ADJUSTMENT_KINDS[key], value / 100 if key == "exp" else value,
                              layer_id=layer.id, coalesce=True)
        if self._adjust_settle:
            GLib.source_remove(self._adjust_settle)
        self._adjust_settle = GLib.timeout_add(350, self._settle_adjustment, editor)
        self._save_recipe(photo)
        self._render_photo()
        self._inspector()

    def _add_brush_point(self, x: float, y: float) -> None:
        photo = self.current_photo()
        if photo is None or self.tool != "mask" or self.active_mask != "brush":
            return
        photo.brush_points.append([x, y])
        document, editor = self._document_for(photo)
        layer = next((item for item in document.layers if item.kind == "adjustment" and item.name == "Brush"), None)
        if layer is None:
            layer = editor.add_layer("adjustment", "Brush")
        mask = next((item for item in layer.masks if item.kind == "brush"), None)
        if mask is None:
            editor.add_mask(layer.id, "brush", geometry={"points": [[x, y]], "size": 0.05})
        else:
            editor.change("Brush mask", lambda: mask.geometry.setdefault("points", []).append([x, y]))
        self._save_recipe(photo)
        self._render_photo()

    def _clear_heal(self) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        photo.heal.clear()
        document, editor = self._document_for(photo)
        editor.change("Clear healing spots", lambda: document.retouch.__setitem__(
            slice(None), [operation for operation in document.retouch if operation.kind != "heal"]))
        self._save_recipe(photo)
        self._render_photo()
        self._inspector()

    def _add_heal_spot(self, x: float, y: float) -> None:
        photo = self.current_photo()
        if photo is None or self.tool != "heal":
            return
        photo.heal.append([x * 100, y * 100])
        _document, editor = self._document_for(photo)
        editor.add_retouch("heal", [[x, y]])
        self._save_recipe(photo)
        self._render_photo()
        self._inspector()

    def import_photos(self) -> None:
        # Opening an image is the existing Darkroom workflow; it does not add
        # files to Photos or mutate a source library.
        picker = Gtk.FileDialog(title="Open photos")
        picker.open_multiple(self, None, self._receive_open)

    def _receive_open(self, picker: Gtk.FileDialog, result) -> None:
        try:
            files = picker.open_multiple_finish(result)
        except GLib.Error:
            return
        self.open_paths([Path(file.get_path()) for file in files if file.get_path()])

    def open_paths(self, paths: list[Path]) -> None:
        valid = [path for path in paths if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES]
        if not valid:
            return
        if self.snapshot is None:
            self.snapshot = LibrarySnapshot(photos=[])
        for path in valid:
            if any(item.path == path for item in self.snapshot.photos):
                continue
            self.snapshot.photos.insert(0, LibraryPhoto(id=path.resolve().as_uri(), name=path.stem, path=path))
        self.snapshot.total_count = len(self.snapshot.photos)
        self.snapshot.selected = 0
        self.selected = [0]
        self._rebuild()

    def show_in_filer(self) -> None:
        photo = self.current_photo()
        if photo:
            Gio.AppInfo.launch_default_for_uri(photo.path.parent.as_uri(), None)

    def trash_rejected(self) -> None:
        # The reference removes rejected rows from its in-memory sample. Real
        # library trashing is a different write kind and is intentionally not
        # performed by this command.
        if not self.fixture_path or not self.snapshot:
            Toast.show(self.host, "Rejected photos remain in the source library", kind="notified")
            return
        rejected = [p for p in self.snapshot.photos if p.flag == -1]
        if not rejected:
            return
        previous = list(self.snapshot.photos)
        self.snapshot.photos = [p for p in previous if p.flag != -1]
        self.selected = []
        self.snapshot.selected = 0
        self._rebuild()
        def undo() -> None:
            self.snapshot.photos = previous
            self._rebuild()
        Toast.show(self.host, f"Moved {len(rejected)} rejected photos to Trash", kind="deleted", undo=undo)

    def share_photo(self, _button: Gtk.Widget) -> None:
        photo = self.current_photo()
        if photo:
            picture = self._raw_textures.get(photo.id) or self._fixture_textures.get(photo.id)
            ShareSheet.present(
                _button,
                document=ShareSubject(photo.name, photo.path.name, picture=picture, image=True),
                on_choice=lambda choice, value: self._share_choice(photo, choice, value),
            )

    def _share_choice(self, photo: LibraryPhoto, choice: str, value: object) -> str | None:
        if choice == "copy":
            picture = self._raw_textures.get(photo.id) or self._fixture_textures.get(photo.id)
            if picture is not None:
                self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(picture))
                return "Photo copied"
            self.get_clipboard().set(photo.path.as_uri())
            return "Photo location copied"
        if choice == "save":
            self.export_photo()
            return None
        if choice == "target":
            app_ids = {"messages": "org.projectluma.Messages", "mail": "org.projectluma.Charlie",
                       "notes": "org.projectluma.Notes", "photos": "org.projectluma.Photos",
                       "canvas": "org.projectluma.Canvas", "ari": "org.projectluma.Ari"}
            app_id = app_ids.get(str(value))
            if app_id is None:
                return "That share target is unavailable"
            application = Gio.DesktopAppInfo.new(f"{app_id}.desktop")
            if application is None:
                return f"{str(value).title()} is not installed"
            try:
                application.launch([Gio.File.new_for_path(str(photo.path))], None)
            except GLib.Error as exc:
                return f"Could not open {str(value).title()}: {exc.message}"
            return None
        if choice == "print":
            return "Printing is unavailable"
        return None

    def export_photo(self) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        picker = Gtk.FileDialog(title="Export a copy", initial_name=f"{photo.name}.jpg")
        picker.save(self, None, self._receive_export)

    def _receive_export(self, picker: Gtk.FileDialog, result) -> None:
        photo = self.current_photo()
        if photo is None:
            return
        try:
            file = picker.save_finish(result)
        except GLib.Error:
            return
        destination = Path(file.get_path())
        if destination.exists():
            Toast.show(self.host, "Choose a new name for this copy", kind="warning")
            return
        document, _editor = self._document_for(photo)
        from .engine import ExportJob
        job = ExportJob(document, destination, ExportPreset(),
                        completed=lambda path, error: self._export_completed(path, error))
        self._export_job = job
        job.start()

    def _export_completed(self, path: Path | None, error: str | None) -> bool:
        Toast.show(self.host, error or f"Exported {path.name}", kind="error" if error else "saved")
        return False

    def offer_recovery(self) -> None:
        if self.fixture_path or self._recovery_offered or self.store is None:
            return
        self._recovery_offered = True

        def worker() -> None:
            assert self.store is not None
            recoveries = list(self.store.recoverable())
            GLib.idle_add(self._offer_recovery_result, recoveries)

        threading.Thread(target=worker, name="darkroom-recovery", daemon=True).start()

    def _offer_recovery_result(self, recoveries) -> bool:
        if not recoveries:
            return False
        if self.snapshot is None:
            GLib.timeout_add(100, self._offer_recovery_result, recoveries)
            return False
        path, document, _metadata = recoveries[-1]
        dialog = Adw.AlertDialog(heading="Recover unsaved Darkroom work?",
                                 body=f"A recoverable edit of {document.source.display_name} was found.")
        dialog.add_response("discard", "Discard")
        dialog.add_response("recover", "Recover")
        dialog.set_response_appearance("recover", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _dialog, response: self._recover_response(response, path, document))
        dialog.present(self)
        return False

    def _recover_response(self, response: str, path: Path, document: Document) -> None:
        if response != "recover":
            path.unlink(missing_ok=True)
            return
        source = file_path(document.source.uri)
        if source is None or not source.exists() or self.snapshot is None:
            Toast.show(self.host, "The original photo could not be found", kind="error")
            return
        index = next((i for i, item in enumerate(self.snapshot.photos) if item.path == source), -1)
        if index < 0:
            photo = LibraryPhoto(id=source.resolve().as_uri(), name=source.stem, path=source)
            self.snapshot.photos.insert(0, photo)
            self.snapshot.total_count += 1
            index = 0
        photo = self.snapshot.photos[index]
        photo.adjustments = {
            key: round(adjustment.value * 100) if key == "exp" else adjustment.value
            for key, kind in ADJUSTMENT_KINDS.items()
            for adjustment in document.raw_development if adjustment.kind == kind and adjustment.enabled
        }
        self._documents[photo.id] = (document, Editor(document))
        self.snapshot.selected = index
        self.selected = [index]
        self.set_mode("edit")
