# SPDX-License-Identifier: Apache-2.0
"""Streamy's window: scenes and their widgets in the sidebar, the preview as the island's
stage with Audio beside it, and the action bar as the action center, where a selection's
editors (Position, Style, Crop, Volume, Source) grow in place.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    SEPARATOR, ActionCenter, BarTile, BarTiles, ColorPicker, Disclosure, PanelHeading,
    InlineRenameField, OverrideDot, PanelRow, PanelSwitch, SyncKey, ActionEditor, AlignmentActions, AppWindow, ArrangeItem, ArrangeView,
    BarAction, BarChip, Command, CommandGroup, CommandRegistry, Dock, DockTile,
    DestructiveDialog, IconOnlyButton, ListEmptyState, ModeSwitch,
    NavigationList, NavigationSidebar, PropertyChoice, PropertyColor, PropertyNumber, PropertySection, RowAction, RowLead, SidebarFoot, SidebarRow,
    MixerRow, MixerStrip, SidebarToggle, TextButton, Toast, ToastHost, TypeLabel, ValueSlider, add_style_sheet,
)
from luma_appkit.action_bubble import MenuItem  # noqa: E402
from luma_appkit.bar_panel import panel_list  # noqa: E402
from luma_appkit.creative_parts import add_arrange_item  # noqa: E402

from . import graph  # noqa: E402
from .engine import Engine, EngineError  # noqa: E402
from .devices import AudioDevices  # noqa: E402
from .layouts import PRESETS, LayoutStore  # noqa: E402
from .undo import History  # noqa: E402
from .model import FORMATS, KINDS, ModelError, Project, ProjectStore, Widget  # noqa: E402


CANVAS_GLYPHS = {"landscape": "rectangle-horizontal", "vertical": "smartphone"}


def canvas_name(fmt: str) -> str:
    """"Landscape", "Vertical": a canvas by its short name."""
    return FORMATS[fmt][0].split()[0]


def lumaui_set_class(widget: Gtk.Widget, name: str, on: bool) -> None:
    (widget.add_css_class if on else widget.remove_css_class)(name)

APP_ID = "org.projectluma.Streamy"
STAGE_MARGIN = 16
SECRET_KIND = "stream-key"


def data_dir() -> Path:
    return Path(os.environ.get("STREAMY_DATA_DIR") or Path(GLib.get_user_data_dir()) / "streamy")


def recordings_dir() -> Path:
    videos = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS) or str(Path.home() / "Videos")
    return Path(videos) / "Streamy"


def clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"



#: The Inspector's width: one width whatever it shows, so nothing beside it ever reflows.
INSPECTOR_WIDTH = 384  # the widest editor (Look's five presets)


class StreamyWindow(AppWindow):
    def __init__(self, application, *, engine: Engine | None = None, portals=None) -> None:
        self.store = ProjectStore(data_dir() / "project.json")
        self.layouts = LayoutStore(data_dir() / "layouts.json")
        self.settings_path = data_dir() / "settings.json"
        self.project: Project = self.store.load()
        self._repair_project()
        self.settings = self._load_settings()
        self.fake = bool(int(os.environ.get("STREAMY_FAKE_SOURCES", "0")))
        self.portals = portals
        self.selected: str | None = None
        self.live_since: float | None = None
        self.recording_since: float | None = None
        self.stream_state = "off"
        self.destination = "twitch"
        self._save_timer = 0
        self._tick = 0
        self._thumbs: dict[str, Gdk.Paintable] = {}
        self._building = False
        self.widget_errors: dict[str, str] = {}
        #: Widgets whose stream is open but has sent no picture yet (not a failure).
        self.widget_waiting: set[str] = set()
        self._drag_start: dict[str, tuple] = {}
        self.history = History()
        self._monitor_last: dict[str, str] = {}
        self.devices = AudioDevices(on_changed=self._devices_changed)
        self._app_audio: bool | None = None

        commands = self._commands()
        super().__init__(application=application, app_id=APP_ID, title="Streamy", icon_name=APP_ID,
                         commands=commands, default_width=1280, default_height=800,
                         minimum_width=360, minimum_height=480)
        self.commands = commands
        style = Path(__file__).resolve().parent.parent / "style" / "streamy.css"
        if not style.is_file():
            style = Path("/usr/share/luma-streamy/streamy.css")
        if style.is_file():
            add_style_sheet(str(style))

        if self.fake:
            from .fake import images

            from .fake import seed

            self._fake_images = images(data_dir() / "fake")
            seed(self.project, self._fake_images)
            self._fill_fake_sources()

        self.engine = engine or Engine(sources=self._source_for)
        self.engine.view_of = lambda widget, fmt: self.project.view_on(widget, fmt)
        if self.portals is not None:
            self.portals.on_cameras = self._cameras_changed
            self.portals.on_ready = lambda _widget_id: self._show_scene()
            self.portals.on_ended = lambda widget_id: self._widget_failed(None, widget_id, "ended")
        self.engine.connect("levels", self._levels)
        self.engine.connect("problem", lambda _e, text: Toast.show(self.host, text, kind="warning"))
        self.engine.connect("output-state", self._output_state)
        self.engine.connect("widget-failed", self._widget_failed)
        self.engine.connect("widget-waiting", self._widget_waiting)
        self.engine.connect("widget-ready", self._widget_ready)
        self.engine.connect("source-size", self._source_size)
        self.engine.connect("frame-shown", self._frame_shown)
        #: Widgets whose outline follows the picture on screen (a drag, until the picture catches up):
        #: widget id -> the committed rect, or None while the drag goes on.
        self._following: dict[str, tuple | None] = {}
        self.engine.connect("preview-changed", lambda engine, fmt: self.pictures[fmt].set_paintable(
            engine.paintable_of(fmt)))

        self._build_sidebar()
        self._build_canvas()
        self._build_insets()
        self.set_body(self._body())
        self._build_outputs()
        self._watch_lock()
        self.refresh_all()
        # Esc clears the selection first (before the Inspector's own Esc closes it), unless a field or the
        # bar's editor has it.
        escape = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        escape.connect("key-pressed", self._escape)
        self.add_controller(escape)
        self.connect("close-request", self._closing)
        self.connect("notify::is-active", lambda *_a: self._update_preview_running())
        self.connect("realize", self._watch_surface)
        self.connect("unmap", lambda *_a: self._update_preview_running())
        GLib.idle_add(self._start_engine)

    def _repair_project(self) -> None:
        """Image sizes an older project lacks, shapes an older Streamy squashed, id-like names."""
        for scene in self.project.scenes:
            for w in scene.widgets:
                if w.kind == "image" and not w.source.get("size") and w.source.get("path"):
                    size = self._image_size(w.source["path"])
                    if size:
                        w.source["size"] = list(size)
        self.project.restore_shapes()
        self.project.tidy_names()

    @staticmethod
    def _image_size(path: str) -> tuple[int, int] | None:
        try:
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf

            info = GdkPixbuf.Pixbuf.get_file_info(path)
            if info is not None and info[0] is not None and info[1] > 0 and info[2] > 0:
                return info[1], info[2]
        except (ValueError, GLib.Error):
            pass
        return None

    # ── Commands ──

    def _commands(self) -> CommandRegistry:
        add = tuple(Command(f"widget.add-{kind}", f"Add {KINDS[kind][0].lower()}", lambda k=kind: self.add_widget(k),
                            icon=KINDS[kind][1], enabled=lambda k=kind: k in self._addable())
                    for kind in ("display", "window", "camera", "image", "microphone", "desktop-audio", "app-audio"))
        layouts = tuple(Command(f"layout.{key}", label, lambda k=key: self.use_layout(k), icon="layout-dashboard")
                        for key, (label, _l) in PRESETS.items())
        self._undo_command = Command("edit.undo", "Undo", self.undo, icon="undo-2", shortcut=("Ctrl", "Z"),
                                     enabled=lambda: self._can_undo())
        self._redo_command = Command("edit.redo", "Redo", self.redo, icon="redo", shortcut=("Ctrl", "Shift", "Z"),
                                     enabled=lambda: self._can_redo())
        return CommandRegistry((
            CommandGroup(None, (self._undo_command, self._redo_command)),
            CommandGroup(None, (Command("edit.redo-y", "Redo", self.redo, shortcut=("Ctrl", "Y"),
                                        enabled=lambda: self._can_redo()),), in_menu=False),
            CommandGroup(None, (
                Command("scene.new", "New scene", self.new_scene, icon="square-pen", shortcut=("Ctrl", "N")),
                Command("scene.duplicate", "Duplicate scene", self.duplicate_scene, icon="copy",
                        shortcut=("Ctrl", "D")),
                Command("scene.rename", "Rename", self.rename_focused, icon="pencil", shortcut=("F2",)),
                Command("scene.delete", "Delete scene", self.delete_scene, icon="trash-2", destructive=True,
                        enabled=lambda: len(self.project.scenes) > 1),
            )),
            CommandGroup("Widgets", add, in_menu=False),
            CommandGroup(None, (
                Command("output.record", "Record", self.toggle_record, icon="circle", shortcut=("Ctrl", "R")),
                Command("output.live", "Go live", self.go_live, icon="radio-tower", shortcut=("Ctrl", "L")),
                Command("output.end", "End stream…", self.ask_end_stream, icon="square",
                        enabled=lambda: self.stream_state != "off"),
                Command("app.destinations", "Destinations", lambda: self._toggle_panel("destinations"),
                        icon="radio-tower", checked=lambda: self._is_open("destinations")),
                Command("app.recordings", "Show recordings", self.show_recordings, icon="folder-open"),
            )),
            CommandGroup("Canvases", tuple(
                Command(f"view.{key}", label, lambda k=key: self._canvas_command(k),
                        icon="rectangle-horizontal" if key == "landscape" else "smartphone",
                        checked=lambda k=key: k in getattr(self, "canvas_on", ()))
                for key, (label, _w, _h) in FORMATS.items())),
            CommandGroup("Panels", (
                Command("panel.inspector", "Inspector", self.toggle_inspector, icon="settings-2",
                        shortcut=("Ctrl", "I"), checked=lambda: self._inspector_shown()),
                *(Command(f"panel.{key}", label, lambda k=key: self._toggle_panel(k), icon=icon,
                          checked=lambda k=key: hasattr(self, "dock") and self._is_open(k))
                  for key, label, icon in (("stats", "Stats", "activity"), ("chat", "Chat", "message-circle"))))),
            CommandGroup("Layouts", layouts + (
                Command("layout.save", "Save layout…", self.save_layout, icon="download"),)),
            CommandGroup(None, (
                Command("widget.delete", "Delete widget", self.delete_selected, icon="trash-2",
                        shortcut=("Delete",), enabled=lambda: self.selected is not None),
                Command("widget.hide", "Hide widget", self.toggle_hidden, icon="eye-off", shortcut=("Ctrl", "H"),
                        enabled=lambda: self.selected is not None),
                Command("widget.front", "Bring to front", lambda: self.restack(1), icon="arrow-up",
                        shortcut=("Ctrl", "bracketright"), enabled=lambda: self.selected is not None),
                Command("widget.back", "Send to back", lambda: self.restack(-1), icon="arrow-down",
                        shortcut=("Ctrl", "bracketleft"), enabled=lambda: self.selected is not None),
            ), in_menu=False),
        ))

    # ── Building ──

    def _build_sidebar(self) -> None:
        """The sidebar: the scenes, one list that scrolls as one, its + on the Scenes heading and search
        pinned at its foot."""
        self.sidebar = NavigationSidebar(variant="files")
        self.sidebar.list.connect("row-activated", self._scene_activated)
        self.sidebar.append_footer(SidebarFoot(search="Search scenes", on_search=self._search_scenes))
        self._scene_filter = ""

    def _build_canvas(self) -> None:
        """A preview per canvas (Landscape, Vertical), each its own panel; an edit applies to the canvas
        it is made on, which becomes the one edited (``project.format``)."""
        self.pictures: dict[str, Gtk.Picture] = {}
        self.arranges: dict[str, ArrangeView] = {}
        for fmt, (_label, cw, ch) in FORMATS.items():
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True)
            picture.add_css_class("streamy-program")
            on = self._on_canvas
            arrange = ArrangeView(picture, canvas=(cw, ch), on_select=on(fmt, self._canvas_selected),
                                  on_change=on(fmt, self._canvas_changed), on_commit=on(fmt, self._canvas_committed),
                                  on_activate=on(fmt, lambda wid: self._grow("look")),
                                  on_crop=on(fmt, self._canvas_cropped),
                                  on_crop_commit=on(fmt, self._canvas_crop_committed),
                                  on_drag_mode=self._drag_mode,
                                  **self._group_handlers(fmt))
            # The video always has room around it, whatever the stage's size.
            arrange.set_fit_margins(STAGE_MARGIN, STAGE_MARGIN, STAGE_MARGIN, STAGE_MARGIN)
            arrange.update_property([Gtk.AccessibleProperty.LABEL], [f"{FORMATS[fmt][0].split()[0]} canvas"])
            menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            menu.connect("pressed", lambda g, _n, x, y, f=fmt: self._on_canvas(f, self._canvas_menu)(x, y) and
                         g.set_state(Gtk.EventSequenceState.CLAIMED))
            arrange.add_controller(menu)
            hold = Gtk.GestureLongPress(touch_only=True)
            hold.connect("pressed", lambda g, x, y, f=fmt: self._on_canvas(f, self._canvas_menu)(x, y) and
                         g.set_state(Gtk.EventSequenceState.CLAIMED))
            arrange.add_controller(hold)
            # A press anywhere on a canvas makes it the one edited (before its own gestures run).
            press = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
            press.connect("pressed", lambda *_a, f=fmt: self._edit_canvas(f))
            arrange.add_controller(press)
            self.pictures[fmt], self.arranges[fmt] = picture, arrange

    def _group_handlers(self, fmt: str) -> dict:
        """Several widgets at once (LumaUI ArrangeView multi-selection), where this LumaUI has it."""
        import inspect

        params = inspect.signature(ArrangeView.__init__).parameters
        if "on_selection" not in params:
            return {}
        on = self._on_canvas
        return {"on_selection": on(fmt, self._canvas_selection), "on_group_change": on(fmt, self._group_changed),
                "on_group_commit": on(fmt, self._group_committed)}

    def _canvas_selection(self, ids: list) -> None:
        self.selection = list(ids)
        if len(self.selection) == 1:
            self.select(self.selection[0])
        elif not self.selection:
            self.select(None)
        else:
            self.selected = self.selection[-1]
            self._building = True
            self.widget_list.unselect_all()
            for widget_id in self.selection:
                row = self._widget_rows.get(widget_id)
                if row is not None:
                    self.widget_list.select_row(row)
            self._building = False
            self._refresh_inspector()
            self._refresh_bar()

    def _group_changed(self, rects: dict) -> None:
        """A group move or scale, live: every widget's box on the canvas edited (crop and Look kept)."""
        if not getattr(self, "_group_checkpoint", False):
            self._checkpoint(f"Move {len(rects)} widgets")
            self._group_checkpoint = True
        for widget_id, (x, y, width, height) in rects.items():
            try:
                _scene, w = self.project.find_widget(widget_id)
            except ModelError:
                continue
            self.project.set_rect(widget_id, x, y, width, height)
            self.engine.update_widget(w)
            for fmt, arrange in self.arranges.items():
                if fmt != self.project.format:
                    view = self.project.view_on(w, fmt)
                    arrange.set_item_rect(w.id, view.x, view.y, view.width, view.height)

    def _group_committed(self, rects: dict) -> None:
        if not getattr(self, "_group_checkpoint", False):
            self._checkpoint(f"Move {len(rects)} widgets")  # a nudge: one step
        self._group_checkpoint = True
        self._group_changed(rects)
        self._group_checkpoint = False
        self._changed()

    def _targets(self) -> list[Widget]:
        """What a command acts on: every selected widget, in the scene's order (top last)."""
        scene = self.project.active_scene
        ids = set(self._selected_ids())
        return [w for w in scene.widgets if w.id in ids] if scene is not None else []

    @staticmethod
    def _count(ws: list, one: str, several: str) -> str:
        return f"{one} {ws[0].name}" if len(ws) == 1 else f"{several} {len(ws)} widgets"

    def _selected_ids(self) -> list[str]:
        ids = [i for i in getattr(self, "selection", []) if self.project.active_scene and
               self.project.active_scene.widget(i)]
        return ids if len(ids) > 1 else ([self.selected] if self.selected else [])

    @property
    def arrange(self) -> ArrangeView:
        """The canvas edited."""
        return self.arranges[self.project.format]

    @property
    def picture(self) -> Gtk.Picture:
        return self.pictures[self.project.format]

    def _on_canvas(self, fmt: str, callback):
        """``callback`` for a gesture on canvas ``fmt``: that canvas is the one edited first."""
        def run(*args):
            self._edit_canvas(fmt)
            return callback(*args)
        return run

    def _edit_canvas(self, fmt: str) -> None:
        """Edits go to canvas ``fmt`` (the panel last clicked): placements switch losslessly; the
        selection moves to its preview and the Inspector names it."""
        if fmt == self.project.format or fmt not in FORMATS:
            return
        self.project.set_format(fmt)
        for key, arrange in self.arranges.items():
            arrange.set_selected(self.selected if key == fmt and self._selected_picture() else None)
        for key, tile in getattr(self, "preview_tiles", {}).items():
            lumaui_set_class(tile, "streamy-editing", key == fmt)
        self._refresh_inspector()
        self._refresh_bar()

    def _selected_picture(self) -> bool:
        scene = self.project.active_scene
        w = scene.widget(self.selected) if scene is not None and self.selected else None
        return w is not None and w.has_picture

    def _sync_rect(self, w: Widget) -> None:
        """Every preview's box for ``w`` where its canvas has it (a linked placement follows)."""
        for fmt, arrange in self.arranges.items():
            view = self.project.view_on(w, fmt)
            arrange.set_item_rect(w.id, view.x, view.y, view.width, view.height)

    def _build_insets(self) -> None:
        """The island: the preview as the bare stage; Widgets and Audio side by side under it, as tall
        as the taller one's content (capped; past that each scrolls in its inset)."""
        self.widget_nav = NavigationList("files")
        self.widget_list = self.widget_nav.list
        # Several at once: Shift+click extends a range, Ctrl+click adds or removes one (GtkListBox).
        self.widget_list.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.widget_list.connect("selected-rows-changed", self._widget_rows_changed)
        add = IconOnlyButton("plus", "Add widget")
        add.connect("clicked", lambda *_a: self.center.grow("add", panel_list(self._add_menu())))
        # The mixer: a channel strip per sound (LumaUI MixerStrip), left to right, scrolling sideways.
        self.mixer_row = MixerRow(label="Audio")
        self._strips: dict[str, object] = {}
        self.audio_empty = ListEmptyState("No microphone or desktop audio")
        self._wrap_labels(self.audio_empty)  # its words never widen the panel (panels keep their sizes)
        audio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # The empty words never set the panel's width (panels keep the sizes the layout gives them).
        empty = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                   vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        empty.set_child(self.audio_empty)
        self.audio_empty.connect("notify::visible", lambda e, _p, box=empty: box.set_visible(e.get_visible()))
        audio.append(empty)
        # The strips never set the panel's height (a scene's sounds never move a seam); a short panel scrolls.
        strips = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        strips.set_child(self.mixer_row)
        self.mixer_row.connect("notify::visible", lambda row, _p, box=strips: box.set_visible(row.get_visible()))
        audio.append(strips)
        # The preview is an inset like the others (same radius, gaps and surface), the canvas fitted
        # inside it with room on every side.
        # The canvases: on = composited and shown. Each preview panel has the dock's own header (glyph and
        # title, ⋯ and × at the right); on and off live in its ⋯ and in app menu › Canvases, and "Live"
        # follows the title while an output takes the canvas. The last one on stays on.
        saved = self.settings.get("canvases")
        if os.environ.get("STREAMY_CANVASES"):  # check runs: which canvases start on
            saved = os.environ["STREAMY_CANVASES"].split(",")
        self.canvas_on: set[str] = {f for f in saved if f in FORMATS} if isinstance(saved, list) and saved else \
            set(FORMATS)
        self.preview_tiles: dict[str, DockTile] = {}
        for fmt in FORMATS:
            tile = DockTile(fmt, canvas_name(fmt), CANVAS_GLYPHS[fmt], self.arranges[fmt], main=True,
                            more=lambda f=fmt: self._canvas_more(f), **self._closable())
            tile.add_css_class("streamy-preview")
            tile.connect("notify::width", lambda *_a, f=fmt: GLib.idle_add(self._size_preview, f))
            tile.connect("notify::height", lambda *_a, f=fmt: GLib.idle_add(self._size_preview, f))
            self.preview_tiles[fmt] = tile
        self.preview_tile = self.preview_tiles["landscape"]
        # The panels belong to the app, not the scene: their sizes are the saved layout's (a person's seam
        # drags), never their content's, so switching scenes moves nothing; long lists scroll inside.
        # On a phone each is a key over the preview and a bottom drawer, so the preview keeps the screen.
        widgets = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        widgets.set_child(self.widget_nav)
        # Blank space under the rows: right click or long press adds a widget.
        blank = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        blank.connect("pressed", lambda _g, _n, x, y: self.widget_list.get_row_at_y(int(y)) is None and
                      self._add_widget_menu(widgets, point=(x, y)))
        widgets.add_controller(blank)
        # A click on the blank space under the rows clears the selection.
        clear = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        clear.connect("pressed", lambda _g, _n, _x, y: self.widget_list.get_row_at_y(int(y)) is None and
                      self.deselect())
        widgets.add_controller(clear)
        hold = Gtk.GestureLongPress(touch_only=True)
        hold.connect("pressed", lambda _g, x, y: self.widget_list.get_row_at_y(int(y)) is None and
                     self._add_widget_menu(widgets, point=(x, y)))
        widgets.add_controller(hold)
        self.widgets_tile = DockTile("widgets", "Widgets", "layers", widgets, phone="drawer", actions=[add])
        # Its size is the layout's (a scene's sounds or their absence never move it); strips past its width
        # scroll sideways.
        self.audio_tile = DockTile("audio", "Audio", "volume-2", audio, phone="drawer")
        # Destinations, Stats and Chat: their content comes once the bar exists (their editors grow it).
        self.panel_boxes = {key: Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
                            for key in ("destinations", "stats", "chat")}
        self.panel_tiles = {key: DockTile(key, title, icon, self.panel_boxes[key], phone="drawer", **self._closable())
                            for key, title, icon in (("destinations", "Destinations", "radio-tower"),
                                                     ("stats", "Stats", "activity"),
                                                     ("chat", "Chat", "message-circle"))}
        # The Inspector: a dock panel (a bottom drawer on a phone) showing the selection's Source, Look and
        # Arrange, or the scene's own settings when nothing is selected. Closable (×, app menu › Panels,
        # Ctrl+I); closed, it stays closed (in the layout) until asked for: Source, Look, Arrange or Ctrl+I
        # open it on that section. Selecting or switching scenes never opens it.
        from luma_appkit import DetailsPane

        self.inspector = DetailsPane("Inspector", main=True, embedded=True)
        self.inspector.header.set_visible(False)  # the panel's own header names it
        self.inspector.show(open=True, subject="scene")
        self.inspector.set_size_request(INSPECTOR_WIDTH, -1)
        self._inspector_sections: dict[str, Gtk.Widget] = {}
        self.inspector_tile = DockTile("inspector", "Inspector", "settings-2", self.inspector, fit=True,
                                       closable=True, phone="drawer")
        self.dock = Dock([*self.preview_tiles.values(), self.widgets_tile, self.audio_tile, *self.panel_tiles.values(),
                          self.inspector_tile],
                         layout=self.layouts.current, under_bar=True, on_layout=self.layouts.remember)
        self.dock.on_rearrange = self._panels_moved
        if hasattr(self.dock, "on_close"):
            self.dock.on_close = self._panel_closing
        for fmt in FORMATS:
            if fmt not in self.canvas_on and self._is_open(fmt):
                self.dock.close_tile(fmt)
        if self.settings.get("inspector") == "closed":  # closed as a column before it was a panel
            self.dock.close_tile("inspector")
            self._setting("inspector", None)  # the layout keeps it closed from now on
        lumaui_set_class(self.preview_tiles[self.project.format], "streamy-editing", True)
        self._sync_canvas_state()

    @staticmethod
    def _closable() -> dict:
        import inspect

        return {"closable": True} if "closable" in inspect.signature(DockTile.__init__).parameters else {}

    def _is_open(self, key: str) -> bool:
        dock = getattr(self, "dock", None)
        return dock.is_open(key) if dock is not None and hasattr(dock, "is_open") else True

    @staticmethod
    def _wrap_labels(widget: Gtk.Widget) -> None:
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                child.set_wrap(True)
                child.set_justify(Gtk.Justification.CENTER)
            StreamyWindow._wrap_labels(child)
            child = child.get_next_sibling()

    def _size_preview(self, fmt: str = "landscape") -> bool:
        arrange = self.arranges[fmt]
        _label, cw, ch = FORMATS[fmt]
        x0, _y0 = arrange.canvas_to_view(0, 0)
        x1, _y1 = arrange.canvas_to_view(cw, ch)
        if x1 - x0 > 8:
            # Device pixels at the surface's own (fractional) scale: the picture is drawn 1:1.
            surface = self.get_surface()
            scale = surface.get_scale() if surface is not None and hasattr(surface, "get_scale") else None
            self.engine.set_preview_size(round((x1 - x0) * (scale or self.get_scale_factor())), 0, fmt=fmt)
        return False

    # ── Destinations, Stats, Chat ──

    def _build_outputs(self) -> None:
        """The outputs fan-out (one encode per canvas and preset, a pipeline per destination row) and its
        panels; the engine feeds it from the canvases' taps while a row is live."""
        from .chat_panel import ChatPanel
        from .destinations import DestinationStore
        from .destinations_panel import DestinationsPanel
        from .outputs import Outputs
        from .stats_panel import StatsPanel

        self.outputs = Outputs()
        self.dest_store = DestinationStore.load(data_dir() / "destinations.json")
        self.dest_panel = DestinationsPanel(self.dest_store, center=self.center, host=self.host, outputs=self.outputs,
                                            on_changed=self._sync_canvas_state)
        self.stats_panel = StatsPanel(outputs=self.outputs, store=self.dest_store)
        self.chat_panel = ChatPanel(center=self.center, host=self.host, path=data_dir() / "chat.json",
                                    outputs=self.outputs)
        for key, panel in (("destinations", self.dest_panel), ("stats", self.stats_panel), ("chat", self.chat_panel)):
            self.panel_boxes[key].append(panel)
            for action in getattr(panel, "actions", []):
                self.panel_tiles[key].actions.append(action)
        self.engine.fanout = self.outputs
        self.outputs.connect("row-state", self._row_state)
        self._sync_stats_canvases()

    def _row_state(self, _outputs, _row_id: str, _state: str, _words: str) -> None:
        """A destination row changed: taps open or close, chips show what is live, the bar follows."""
        self.engine.sync_taps()
        self._sync_canvas_state()
        for r in self.dest_store.rows:
            row = self.outputs.rows.get(r.id)
            if not r.is_stream and row is not None:
                self._record_target = row.target
        states = {r.id: (self.outputs.stats(r.id).state if self.outputs.stats(r.id) else "off")
                  for r in self.dest_store.rows}
        streams = [states[r.id] for r in self.dest_store.rows if r.is_stream and r.id in self.outputs.rows]
        stream = "on" if "live" in streams else "reconnecting" if "reconnecting" in streams else \
            "starting" if "connecting" in streams else "off"
        if stream != self.stream_state:
            if stream != "off" and self.live_since is None:
                self.live_since = time.monotonic()
            self._output_state(None, "stream", stream)
        recording = any(r.id in self.outputs.rows for r in self.dest_store.rows if not r.is_stream)
        if recording != (self.recording_since is not None):
            self._output_state(None, "record", "on" if recording else "off")

    def _sync_stats_canvases(self) -> None:
        panel = getattr(self, "stats_panel", None)
        if panel is None:
            return
        for fmt in FORMATS:
            on = fmt in self.engine.programs
            panel.set_canvas(fmt, fps=float(graph.FPS) if on else None, frame_ms=None)

    def _toggle_panel(self, key: str) -> None:
        """App menu › Panels: show a closed panel (where it was), or close an open one."""
        if not hasattr(self.dock, "open_tile"):
            return
        if self._is_open(key):
            self.dock.close_tile(key)
        else:
            self.dock.open_tile(key)
        self.layouts.remember(self.dock.layout())

    # ── Canvases on and off ──

    def _shown_canvases(self) -> list[str]:
        """The canvases composited for the screen: on, with their panel open (the edited one first)."""
        shown = [f for f in FORMATS if f in self.canvas_on and self._is_open(f)]
        shown.sort(key=lambda f: f != self.project.format)
        return shown

    def _sync_canvases(self) -> None:
        self.engine.set_canvases(self._shown_canvases())
        self._sync_stats_canvases()
        if self.project.format not in self.canvas_on or not self._is_open(self.project.format):
            other = next((f for f in self._shown_canvases()), None)
            if other is not None:
                self._edit_canvas(other)
        self._sync_canvas_state()

    def _canvas_used(self, fmt: str) -> list[str]:
        """What sends canvas ``fmt`` now, by name."""
        names = []
        for kind, output in self.engine.outputs.items():
            if output.fmt == fmt:
                names.append("the recording" if kind == "record" else "the stream")
        fanout = getattr(self, "outputs", None)
        if fanout is not None:
            names += [r.label for r in self.dest_store.rows if r.canvas == fmt and r.id in fanout.rows]
        return names

    def _canvas_chip(self, key: str, on: bool) -> bool:
        if on:
            self.canvas_on.add(key)
            if not self._is_open(key) and hasattr(self.dock, "open_tile"):
                self._open_canvas_panel(key)
            self._setting("canvases", sorted(self.canvas_on))
            self._sync_canvases()
            return True
        if len(self.canvas_on - {key}) == 0:
            Toast.show(self.host, "One canvas stays on", kind="info")
            return False
        used = self._canvas_used(key)
        if used:
            label = FORMATS[key][0].split()[0]
            self.center.grow("canvas-off", panel_list([
                PanelHeading(f"Turning {label} off ends {', '.join(used)}"),
                PanelRow(f"End and turn {label} off", icon="square", destructive=True,
                         on_activate=lambda: self._canvas_off(key, end=True)),
                PanelRow("Keep it on", icon="x")]))
            return False
        self._canvas_off(key)
        return True

    def _canvas_command(self, key: str) -> None:
        """App menu › Canvases: a canvas on with its panel closed shows its panel again; else on ↔ off."""
        if key in self.canvas_on and not self._is_open(key) and hasattr(self.dock, "open_tile"):
            self._open_canvas_panel(key)
            self._sync_canvases()
        else:
            self._canvas_chip(key, key not in self.canvas_on)
        self._sync_canvas_state()

    def _canvas_off(self, key: str, *, end: bool = False) -> None:
        if end:
            for kind, output in list(self.engine.outputs.items()):
                if output.fmt == key:
                    (self.engine.stop_stream if kind == "stream" else self.engine.stop_record)()
            for row in self.dest_store.rows:
                if row.canvas == key and row.id in self.outputs.rows:
                    self.outputs.stop(row.id)
        self.canvas_on.discard(key)
        if self._is_open(key) and hasattr(self.dock, "close_tile"):
            self.dock.close_tile(key)
        self._setting("canvases", sorted(self.canvas_on))
        self._sync_canvases()

    def _open_canvas_panel(self, key: str) -> None:
        """A canvas's panel opens beside the other preview: Vertical to its right at 9:16 of its height."""
        other = next((f for f in FORMATS if f != key and self._is_open(f)), None)
        size = None
        if other is not None:
            height = self.preview_tiles[other].get_height() - 40  # less its header
            _l, cw, ch = FORMATS[key]
            size = max(160, int(height * cw / ch) + 2 * STAGE_MARGIN)
        self.dock.open_tile(key, beside=other, edge="right" if key == "vertical" else "left", size=size)

    def _panel_closing(self, key: str):
        """Closing a preview panel hides it; the canvas stays on (and composited while sent)."""
        GLib.idle_add(lambda: self._sync_canvases() and False)
        return True

    def _body(self) -> Gtk.Widget:
        from luma_appkit import Island

        self.island = Island()
        self.island.append(self.dock)
        self.host = ToastHost(self.island)
        self.center = ActionCenter().attach(self.host)
        self.formats = ModeSwitch([(key, label, "rectangle-horizontal" if key == "landscape" else "smartphone")
                                   for key, (label, _w, _h) in FORMATS.items()],
                                  current=self.project.format, on_change=self.set_format, label="Canvas")
        body = Gtk.Box(hexpand=True, vexpand=True)
        body.append(self.sidebar)
        body.append(self.host)
        # The toggle (F9) moves the sidebar into a drawer below its width, so it comes after the frame.
        self.sidebar_toggle = SidebarToggle(self.sidebar, drawer_below=901)
        # The title row is the app menu alone; the toggle shows only where the sidebar is a drawer
        # (narrow windows and phones), which needs an opener. F9 still hides it on a desktop.
        self.sidebar_toggle.connect("notify::css-classes", lambda t, _p: t.set_visible(t.has_css_class("drawer-mode")))
        self.sidebar_toggle.set_visible(self.sidebar_toggle.has_css_class("drawer-mode"))
        if not self.sidebar_toggle.has_css_class("drawer-mode") and not self.sidebar_toggle.get_active():
            self.sidebar_toggle.set_active(True)  # no desktop key hides it, so it starts shown
        # The island's width changes when the sidebar comes or goes: the insets share it again.
        for signal in ("map", "unmap"):
            self.sidebar.connect(signal, lambda *_a: GLib.timeout_add(50, lambda: self.dock.refit() and False))
        self.set_leading(self.sidebar_toggle)
        return body

    # ── Settings ──

    def _load_settings(self) -> dict:
        import json

        try:
            data = json.loads(self.settings_path.read_text())
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _setting(self, key: str, value) -> None:
        import json

        from .model import atomic_write

        self.settings[key] = value
        atomic_write(self.settings_path, json.dumps(self.settings, indent=1))

    # ── Engine and sources ──

    def _start_engine(self) -> bool:
        self.engine.set_canvases(self._shown_canvases())
        try:
            self.engine.start(self.project)
        except EngineError as error:
            Toast.show(self.host, str(error), kind="error")
            return False
        self._ask_missing_sources()
        if not getattr(self, "_tick", 0):  # once: the engine restarts each time the window is shown again
            self._tick = GLib.timeout_add(1000, self._every_second)
        return False

    def _on_screen(self) -> bool:
        """Mapped, and neither minimized nor suspended (another workspace, covered: nothing of it shows)."""
        surface = self.get_surface()
        hidden = Gdk.ToplevelState.MINIMIZED | getattr(Gdk.ToplevelState, "SUSPENDED", 0)
        return self.get_mapped() and not (surface is not None and surface.get_state() & hidden)

    def _watch_surface(self, *_args) -> None:
        surface = self.get_surface()
        if surface is not None and getattr(self, "_watched_surface", None) is not surface:
            self._watched_surface = surface
            surface.connect("notify::state", lambda *_a: self._update_preview_running())

    def _update_preview_running(self) -> None:
        """The program runs while the window is shown or something is sent; otherwise nothing captures."""
        visible = self._on_screen()
        if not visible and not self.engine.outputs and self.engine.running:
            self.engine.stop()
        elif visible and not self.engine.running and getattr(self, "_tick", 0):
            self._start_engine()  # shown again (the first start is _start_engine's own, at idle)

    def _fill_fake_sources(self) -> None:
        for scene in self.project.scenes:
            for w in scene.widgets:
                if w.kind in self._fake_images:
                    w.source["fake-image"] = self._fake_images[w.kind]

    def _source_for(self, widget: Widget):
        if self.portals is None:
            return None, None
        if widget.kind == "camera" and not widget.source.get("mode"):
            from .portals import default_mode

            camera = self._camera_of(widget)
            mode = default_mode(camera.modes) if camera is not None else None
            if mode is not None:
                widget.source["mode"] = list(mode)  # a new camera starts at 1080p30, not its largest mode
                if mode[1] > 1080:
                    widget.source["scaled"] = [1920, 1080]  # a camera with nothing smaller: 1080p, scaled
        return self.portals.source_for(widget)

    def _ask_missing_sources(self) -> None:
        """The scene's sources without a capture reconnect, one at a time: each from its restore token,
        silently. One that brought up a picker (it took a person's answer) stops the walk; the rest wait
        for "Share" in the bar. Nothing is asked while the session is locked."""
        scene = self.project.active_scene
        if scene is None or self.fake or self.portals is None or getattr(self, "locked", False):
            return
        wanted = [w for w in scene.widgets if w.kind in ("display", "window", "camera", "app-audio") and w.visible
                  and w.id not in self.portals.captures and w.id not in self.widget_errors]
        # Only what restores without a picker starts by itself (a camera, a share with its restore token);
        # the rest say "Needs sharing again" and wait for Share in the bar: no picker pops up on its own.
        silent = [w for w in wanted if w.kind == "camera" or w.source.get("restore-token")]
        later = [w for w in wanted if w not in silent]
        for w in later:
            self.widget_errors[w.id] = "reshare"
        wanted = silent
        if later:
            self._share_queue = [w.id for w in later] + [i for i in getattr(self, "_share_queue", [])
                                                          if i not in {w.id for w in later}]
            self._show_scene()
            self.refresh_all()
            GLib.idle_add(lambda: self._offer_share_again() and False)
        queued = [i for i in getattr(self, "_share_queue", []) if i not in {w.id for w in wanted}]
        self._auto_queue = [w.id for w in wanted]
        self._share_queue = queued
        if not getattr(self, "_sharing", None) and self._auto_queue:
            self._share_next(auto=True)

    def _share_next(self, *, auto: bool) -> None:
        queue = getattr(self, "_auto_queue", []) if auto else getattr(self, "_share_queue", [])
        scene = self.project.active_scene
        while queue:
            widget_id = queue.pop(0)
            if not auto:
                self.widget_errors.pop(widget_id, None)  # its picker now
            w = scene.widget(widget_id) if scene is not None else None
            if w is None or w.id in self.portals.captures or not w.visible:
                continue
            self._sharing = (w.id, time.monotonic(), auto)
            self._choose_source(w, restore=True)
            return
        self._sharing = None

    def _shared_one(self, widget_id: str) -> None:
        """A queued source answered (shared or not): the next one, unless that took a picker."""
        sharing = getattr(self, "_sharing", None)
        if sharing is None or sharing[0] != widget_id:
            return
        _wid, started, auto = sharing
        self._sharing = None
        if not auto:
            # One picker at a time, walked by the person's Share: the next follows this one's answer; a
            # Cancel stops the walk (the rest keep their Share).
            if self.widget_errors.get(widget_id) == "unshared":
                for other in getattr(self, "_share_queue", []):
                    self.widget_errors[other] = "reshare"
                self._show_scene()
                self._offer_share_again()
            elif getattr(self, "_share_queue", []):
                self._share_next(auto=False)
            return
        if auto and time.monotonic() - started > 2.0 and getattr(self, "_auto_queue", []):
            # A picker came up: the rest wait for the person.
            self._share_queue = self._auto_queue + [i for i in getattr(self, "_share_queue", [])
                                                    if i not in self._auto_queue]
            self._auto_queue = []
            for widget_id in self._share_queue:
                self.widget_errors[widget_id] = "reshare"
            self._show_scene()
            self._offer_share_again()
            return
        self._share_next(auto=auto)

    def _offer_share_again(self) -> None:
        scene = self.project.active_scene
        waiting = [w for w in (scene.widgets if scene else []) if w.id in self._share_queue]
        if not waiting:
            return
        kinds = {w.kind for w in waiting}
        what = "windows" if kinds == {"window"} else "sources"
        words = f"Share {len(waiting)} {what} again" if len(waiting) > 1 else f"Share {waiting[0].name} again"
        self.center.grow("share-again", panel_list([
            PanelRow(words, icon="monitor-up", on_activate=lambda: self._share_next(auto=False))]))

    # ── The session's lock ──

    def _watch_lock(self) -> None:
        """Locked, nothing asks for a share (a picker can't be answered over the lock screen): sources that
        end are paused, and come back after unlock (silently from their tokens, else one at a time)."""
        self.locked = False
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        self._lock_bus = bus
        bus.signal_subscribe(None, "org.gnome.ScreenSaver", "ActiveChanged", "/org/gnome/ScreenSaver", None,
                             Gio.DBusSignalFlags.NONE, lambda *a: self._lock_changed(bool(a[5].unpack()[0])))

        def answered(source, result) -> None:
            try:
                self._lock_changed(bool(source.call_finish(result).unpack()[0]))
            except GLib.Error:
                pass

        bus.call("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "GetActive", None,
                 GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 2000, None, answered)
        # logind's LockedHint too, for a session without the screen saver's interface.
        try:
            system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return
        self._logind_bus = system

        def changed(*args) -> None:
            props = args[5].unpack()[1] if args[5] is not None else {}
            if "LockedHint" in props:
                self._lock_changed(bool(props["LockedHint"]))

        try:
            session = system.call_sync("org.freedesktop.login1", "/org/freedesktop/login1",
                                       "org.freedesktop.login1.Manager", "GetSession", GLib.Variant("(s)", ("auto",)),
                                       GLib.VariantType.new("(o)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        except GLib.Error:
            return
        system.signal_subscribe("org.freedesktop.login1", "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                session, None, Gio.DBusSignalFlags.NONE, changed)

    def _lock_changed(self, locked: bool) -> None:
        if locked == self.locked:
            return
        self.locked = locked
        from .engine import log

        log("session locked: shares wait" if locked else "session unlocked")
        if not locked:
            paused = [wid for wid, reason in self.widget_errors.items() if reason == "paused"]
            for widget_id in paused:
                self.widget_errors.pop(widget_id, None)
            self.refresh_all()
            GLib.timeout_add(1000, lambda: self._ask_missing_sources() and False)

    def _choose_source(self, widget: Widget, *, restore: bool) -> None:
        if self.portals is None or self.fake:
            return

        def done(capture) -> None:
            self.widget_errors.pop(widget.id, None)
            if capture.restore_token:
                widget.source["restore-token"] = capture.restore_token  # single-use: keep the new one
            if widget.kind == "app-audio" and capture.description:
                widget.source["heard"] = capture.description
                if not widget.source.get("named"):
                    widget.name = capture.description[:40]
            if capture.size:
                widget.source["size"] = list(capture.size)
            if capture.aspect and not widget.source.get("sized"):
                # Its first picture: the source's own shape, about half the canvas (a display: all of it).
                widget.source["sized"] = True
                self.project.place_natural(widget.id)
            self._changed(structure=True)
            self._show_scene()
            self._shared_one(widget.id)

        def failed(reason: str) -> None:
            # Every ending leaves the widget saying what happened and offering the fix; nothing
            # asks again by itself (a scene switch must not keep raising the picker).
            if reason == "no camera":
                reason = "nocamera"
            elif reason == "cancelled":
                # Declining the camera's permission answers AccessCamera the way Cancel answers a picker.
                reason = "denied" if widget.kind == "camera" else "unshared"
            elif reason not in ("timeout", "unavailable", "ended"):
                reason = "missing"
            widget.source.pop("restore-token", None)  # spent or refused: the next share picks afresh
            self._widget_failed(None, widget.id, reason)

        if widget.kind == "camera":
            def opened(capture) -> None:
                camera = getattr(capture, "camera", None)
                if camera is not None:
                    widget.source["camera"] = camera.name  # remembered by its stable node name
                    widget.source["device-name"] = camera.label
                done(capture)

            self.portals.camera(widget.id, done=opened, failed=failed, name=widget.source.get("camera"))
            return
        token = widget.source.get("restore-token") if restore else None
        if widget.kind == "app-audio":
            self._with_parent(lambda parent: self.portals.app_audio(
                widget.id, restore_token=token, done=done, failed=failed, parent=parent))
            return
        self._with_parent(lambda parent: self.portals.screencast(
            widget.id, window=widget.kind == "window", restore_token=token, done=done, failed=failed,
            parent=parent, cursor=widget.source.get("cursor", True)))

    def _show_scene(self) -> None:
        states = {wid: "waiting" for wid in self.widget_waiting}
        states.update({wid: "choose" for wid, reason in self.widget_errors.items() if reason in ("unshared", "nocamera")})
        states.update({wid: "Paused while locked" for wid, reason in self.widget_errors.items() if reason == "paused"})
        states.update({wid: "Needs sharing again" for wid, reason in self.widget_errors.items() if reason == "reshare"})
        self.engine.show_scene(self.project.active_scene, skip=set(self.widget_errors), states=states)
        self._close_offscene_sound()

    def _close_offscene_sound(self) -> None:
        """An app-audio capture lives only while its scene is shown; its restore token brings it back."""
        if self.portals is None:
            return
        scene = self.project.active_scene
        shown = {w.id for w in scene.widgets} if scene else set()
        for other in self.project.scenes:
            for w in other.widgets:
                if w.kind == "app-audio" and w.id not in shown and w.id in self.portals.captures:
                    self.portals.close(w.id)

    @staticmethod
    def problem_words(w: Widget, reason: str) -> str:
        """What happened to a widget's source, and what fixes it, in a sentence or two."""
        thing = {"camera": "The camera", "display": "The shared display", "window": "The shared window",
                 "image": "The image", "microphone": "The microphone",
                 "desktop-audio": "Desktop audio", "app-audio": "The apps' sound"}.get(w.kind, w.name)
        if reason == "unshared":
            what = {"display": "a display", "window": "a window", "app-audio": "the apps to hear"}.get(w.kind, "it")
            return f"Nothing is shared yet. Choose {what} to show it here."
        if reason == "ended":
            if w.kind == "app-audio":
                return "Sharing this sound was stopped. Choose apps to hear them again."
            return f"{thing} is no longer shared (it was stopped or closed). Share it again."
        if reason == "missing":
            if w.kind == "camera":
                # The node vanished under the stream: the camera (or its driver) stopped answering.
                return "The camera stopped responding. Try again once it's working; Streamy won't retry on its own."
            return f"{thing} is gone: it was closed, unplugged or removed. Choose it again."
        if reason == "format":
            return f"{thing} sends pictures in a format Streamy can't read."
        if reason == "timeout":
            return "The picker stopped waiting before anything was chosen. Share again when you're ready."
        if reason == "unavailable":
            return "This Luma doesn't offer app sound yet. Desktop audio carries everything this computer plays."
        if reason == "busy":
            return f"{thing} is in use by another app. Close it there, then try again."
        if reason == "denied":
            return f"Streamy isn't allowed to use {thing.lower()}. Allow it in Settings, then try again."
        if w.kind == "camera":
            if reason == "nocamera":
                return "No camera was found. Connect one, then try again."
            return "The camera didn't start. Another app may be using it; close that app, then try again."
        if w.kind in ("display", "window"):
            return f"{thing} stopped before its picture arrived. Share it again."
        if w.kind == "app-audio":
            return f"{thing} stopped. Choose the apps again."
        if w.kind == "image":
            return "The image can't be opened. Choose another one."
        return f"{thing} stopped. Try again."

    def _show_problem(self, w: Widget) -> None:
        self.select(w.id)
        self._grow("source")

    @staticmethod
    def fix_words(w: Widget, reason: str) -> str | None:
        """The key that fixes a widget's problem, or None when nothing here can."""
        if reason == "unavailable":
            return None
        if w.kind == "app-audio":
            return "Choose apps"
        if w.kind in ("display", "window"):
            return "Share" if reason == "unshared" else "Share again"
        if w.kind == "image":
            return "Choose image"
        return "Try again"

    def _widget_waiting(self, _engine, widget_id: str) -> None:
        self.widget_waiting.add(widget_id)
        self.refresh_all()
        self._show_scene()

    def _widget_ready(self, _engine, widget_id: str) -> None:
        if widget_id in self.widget_waiting:
            self.widget_waiting.discard(widget_id)
            self.refresh_all()

    def _source_size(self, _engine, widget_id: str, width: int, height: int) -> None:
        """The picture's own size (a window's, inside its cast): the widget keeps its place and width and
        takes that shape, unless it was stretched on purpose."""
        try:
            _scene, w = self.project.find_widget(widget_id)
        except ModelError:
            return
        if not width or not height or w.source.get("size") == [width, height]:
            return
        w.source["size"] = [width, height]
        # Fitted inside the box it had on each canvas, centred: it never grows past where it was placed.
        if self.project.reshape(w, fit=True):
            self.engine.update_widget(w)
            self._sync_rect(w)
        self._changed()
        self._refresh_bar()

    def source_name(self, w: Widget) -> str:
        """What a widget shows or hears, by name: the camera's own name, else the kind's."""
        named = w.source.get("device-name")
        if named:
            return named
        return {"camera": "The camera", "display": "The display", "window": "The window", "image": "The image",
                "microphone": "The microphone", "desktop-audio": "Desktop audio",
                "app-audio": "The apps' sound"}.get(w.kind, w.name)

    def cause_line(self, w: Widget, reason: str | None) -> str | None:
        """The one short line beside Try again: what went wrong, by name."""
        name = self.source_name(w)
        if reason is None:
            return f"{name} isn't sending a picture" if w.id in self.widget_waiting else None
        return {
            "unshared": "Nothing shared yet",
            "ended": "Sharing was stopped",
            "timeout": "The picker stopped waiting",
            "unavailable": "App sound isn't available",
            "busy": f"{name} is in use by another app",
            "denied": f"Streamy isn't allowed to use {name[0].lower() + name[1:] if name.startswith('The ') else name}",
            "nocamera": "No camera found",
            "paused": "Paused while locked",
            "reshare": "Needs sharing again",
            "missing": f"{name} stopped responding" if w.kind == "camera" else f"{name} is gone",
            "format": f"{name} sends a picture Streamy can't read" if w.has_picture else
                      f"{name} sends sound Streamy can't read",
            "stopped": f"{name} stopped with an error",
        }.get(reason, f"{name} stopped")

    def _widget_failed(self, _engine, widget_id: str, reason: str) -> None:
        """A source failed: say so on the widget, offer the fix, keep everything else running."""
        try:
            _scene, w = self.project.find_widget(widget_id)
        except ModelError:
            return
        from .engine import log

        if getattr(self, "locked", False) and reason in ("ended", "stopped", "missing", "timeout", "unshared"):
            reason = "paused"  # it comes back after unlock
        log(f"{w.kind} widget {w.name!r}: {self.cause_line(w, reason)} ({reason})")
        self.widget_waiting.discard(widget_id)
        self.widget_errors[widget_id] = reason
        if self.portals is not None:
            self.portals.close(widget_id)
        self._show_scene()  # its card says so in its place
        self._shared_one(widget_id)
        if reason == "paused":
            pass
        elif reason == "timeout":
            Toast.show(self.host, "The picker stopped waiting. Share again when you're ready.", kind="warning")
        elif reason != "unshared":  # a person who pressed Cancel knows
            Toast.show(self.host, f"{w.name} couldn't start", kind="warning")
        self.refresh_all()

    def retry_widget(self, w: Widget) -> None:
        self.widget_errors.pop(w.id, None)
        if self.portals is not None:
            self.portals.close(w.id)
        self.refresh_all()
        if w.kind in ("display", "window", "camera", "app-audio") and not self.fake:
            # Share again: the picker, not the old choice that just failed.
            self._choose_source(w, restore=w.kind == "camera")
        elif w.kind == "image" and not self.fake:
            self._choose_image(w)
        else:
            self._show_scene()

    def _with_parent(self, then) -> None:
        """The window's exported handle for a portal dialog ("wayland:…" or "x11:…"), or ""."""
        surface = self.get_surface()
        try:
            gi.require_version("GdkWayland", "4.0")
            from gi.repository import GdkWayland

            if isinstance(surface, GdkWayland.WaylandToplevel):
                def exported(toplevel, handle, *_args) -> None:
                    then(f"wayland:{handle}")
                    GLib.timeout_add_seconds(60, lambda: (toplevel.drop_exported_handle(handle)
                                                          if hasattr(toplevel, "drop_exported_handle")
                                                          else toplevel.unexport_handle()) and False)
                if surface.export_handle(exported):
                    return
        except (ValueError, ImportError, AttributeError):
            pass
        try:
            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11

            if isinstance(surface, GdkX11.X11Surface):
                then(f"x11:{surface.get_xid():x}")
                return
        except (ValueError, ImportError, AttributeError):
            pass
        then("")

    # ── Undo ──

    def _checkpoint(self, label: str, key=None) -> None:
        """Before an edit: the project as it is, so Ctrl+Z gives it back (one step per drag or scrub)."""
        if self._building or getattr(self, "_restoring", False):
            return
        if self.history.checkpoint(label, self._undo_state(), key=key):
            self._label_history()

    def _label_history(self) -> None:
        self._undo_command.label = f"Undo {self.history.undo_label}" if self.history.undo_label else "Undo"
        self._redo_command.label = f"Redo {self.history.redo_label}" if self.history.redo_label else "Redo"

    def _editing_text(self) -> Gtk.Widget | None:
        focus = self.get_focus()
        while focus is not None:
            if isinstance(focus, (Gtk.Text, Gtk.TextView)) or isinstance(focus, Gtk.Editable):
                return focus
            focus = focus.get_parent()
        return None

    def _can_undo(self) -> bool:
        return bool(self.history.undos) or self._editing_text() is not None

    def _can_redo(self) -> bool:
        return bool(self.history.redos) or self._editing_text() is not None

    def undo(self) -> None:
        text = self._editing_text()
        if text is not None:  # a field being typed in keeps its own undo
            text.activate_action("text.undo", None)
            return
        step = self.history.undo(self._undo_state())
        if step is not None:
            self._restore(step.project)
            Toast.show(self.host, f"Undid {step.label}", kind="done")

    def redo(self) -> None:
        text = self._editing_text()
        if text is not None:
            text.activate_action("text.redo", None)
            return
        step = self.history.redo(self._undo_state())
        if step is not None:
            self._restore(step.project)
            Toast.show(self.host, f"Redid {step.label}", kind="done")

    def _undo_state(self, panels=None) -> dict:
        """An undo step's state: the project, and the app's panel arrangement (never the scene's)."""
        data = self.project.to_dict()
        data["_panels"] = panels if panels is not None else self.dock.layout()
        return data

    def _panels_moved(self, previous, _new) -> None:
        """A panel docked somewhere else by drag: one undo step."""
        if self.history.checkpoint("Move panel", self._undo_state(previous)):
            self._label_history()

    def _restore(self, data: dict) -> None:
        """The project as a step had it: same ids, places, Looks and sources. Captures of widgets that
        went away close; ones that came back reconnect from their restore tokens. A step that only
        moved panels moves them back and leaves the project (and its captures) alone."""
        panels = data.pop("_panels", None)
        if panels is not None and panels != self.dock.layout():
            self.dock.set_layout(panels)
            self.layouts.remember(self.dock.layout())
        if data == self.project.to_dict():
            return
        self._restoring = True
        try:
            before = {w.id for s in self.project.scenes for w in s.widgets}
            self.project = Project.from_dict(data)
            if self.fake:
                self._fill_fake_sources()
            after = {w.id for s in self.project.scenes for w in s.widgets}
            for gone in before - after:
                self.widget_errors.pop(gone, None)
                self.widget_waiting.discard(gone)
                if self.portals is not None:
                    self.portals.close(gone)
            for widget_id in list(self.engine.branches):
                self.engine._remove_branch(widget_id)  # rebuilt with the step's settings
            if self.selected and self.selected not in after:
                self.selected = None
            self.formats.set_current(self.project.format)
            self._label_history()
            self._changed(structure=True)
            self._show_scene()
            self._ask_missing_sources()
        finally:
            self._restoring = False

    # ── Saving ──

    def _changed(self, *, structure: bool = False) -> None:
        if structure:
            self.refresh_all()
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(400, self._save)

    def _save(self) -> bool:
        self._save_timer = 0
        stored = Project.from_dict(self.project.to_dict())
        for scene in stored.scenes:
            for w in scene.widgets:
                w.source.pop("fake-image", None)
                w.source.pop("fake-silent", None)
        self.store.save(stored)
        return False

    def _closing(self, *_args) -> bool:
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save()
        if self._tick:
            GLib.source_remove(self._tick)
        self.engine.stop()
        return False

    # ── Refresh ──

    def refresh_all(self) -> None:
        self._building = True
        try:
            self._refresh_scenes()
            self._refresh_canvas()
            self._refresh_widgets()
            self._refresh_inspector()
            self._refresh_audio()
            self._refresh_bar()
        finally:
            self._building = False

    def _refresh_scenes(self) -> None:
        self.sidebar.clear()
        self._scene_rows = {}
        self.sidebar.append_section("Scenes", action=("plus", "New scene", self.new_scene))
        for scene in self.project.scenes:
            if self._scene_filter and self._scene_filter not in scene.name.casefold():
                continue
            count = len(scene.widgets)
            row = SidebarRow(scene.name, lead=RowLead.thumbnail(self._thumbs.get(scene.id), kind="image", fit="contain",
                                                                     aspect=self.project.canvas[0] / self.project.canvas[1]),
                             subtitle="Empty" if not count else f"{count} widget" + ("" if count == 1 else "s"))
            row.scene_id = scene.id
            click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            click.connect("pressed", lambda _g, _n, x, y, sid=scene.id, r=row: self._scene_menu(sid, r, x, y))
            row.add_controller(click)
            hold = Gtk.GestureLongPress(touch_only=True)
            hold.connect("pressed", lambda _g, x, y, sid=scene.id, r=row: self._scene_menu(sid, r, x, y))
            row.add_controller(hold)
            twice = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
            twice.connect("pressed", lambda _g, n, _x, _y, sid=scene.id: n == 2 and self.rename_scene(sid))
            row.add_controller(twice)
            self._scene_rows[scene.id] = row
            self.sidebar.append_row(row)
            if scene.id == self.project.active:
                self.sidebar.list.select_row(row)

    def _refresh_canvas(self) -> None:
        scene = self.project.active_scene
        for fmt, arrange in self.arranges.items():
            arrange.clear()
            arrange.set_canvas_size(*FORMATS[fmt][1:])
            if scene is None:
                continue
            for w in scene.widgets:
                if not w.has_picture:
                    continue
                v = self.project.view_on(w, fmt)
                add_arrange_item(arrange, ArrangeItem(w.id, w.name, v.x, v.y, v.width, v.height, locked=w.locked,
                                                      visible=w.visible, keeps_aspect=True, croppable=True,
                                                      crop=(v.effects.crop_left, v.effects.crop_top,
                                                            v.effects.crop_right, v.effects.crop_bottom)))
        if scene is not None and self.selected and scene.widget(self.selected) and \
                scene.widget(self.selected).has_picture:
            self.arrange.set_selected(self.selected)

    def _refresh_widgets(self) -> None:
        """The scene's widgets in the sidebar, top first: a row each; Hide, Lock and the rest are in its
        context menu and the bar."""
        scene = self.project.active_scene
        self.widget_nav.clear()
        self._widget_rows = {}
        widgets = list(reversed(scene.widgets)) if scene else []
        for w in widgets:
            # Hidden and muted are the trailing key's to say; the meta names what it can't.
            problem = self.widget_errors.get(w.id) or ("waiting" if w.id in self.widget_waiting else None)
            # The trailing key states the widget's one switch: a picture's eye, a sound's speaker. Off
            # (hidden, muted) always shows; on waits for the row (hover, selection, focus), so the list
            # reads calm and every row keeps one height.
            if problem:
                # A widget without its source says so at its end; the key opens Source, which
                # explains and offers the fix.
                words = {"unshared": "Not shared", "waiting": "No picture yet"}.get(problem, "Unavailable")
                key = RowAction("circle-alert", f"{w.name}: {words}", lambda wid=w: self._show_problem(wid))
            elif w.has_picture:
                key = RowAction("eye" if w.visible else "eye-off", f"Hide {w.name}" if w.visible else f"Show {w.name}",
                                lambda wid=w: self._set_visible(wid, not wid.visible), quiet=w.visible)
            else:
                key = RowAction("volume-x" if w.muted else "volume-2", f"Unmute {w.name}" if w.muted else f"Mute {w.name}",
                                lambda wid=w: self._toggle_mute(wid), quiet=not w.muted)
            row = SidebarRow(w.name, lead=RowLead.icon(w.icon), title_icon="lock" if w.locked else None, trail=key,
                             muted=not w.visible or bool(problem))
            row.widget_id = w.id
            click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            click.connect("pressed", lambda _g, _n, x, y, wid=w.id, r=row: self._widget_menu(wid, r, x, y))
            row.add_controller(click)
            hold = Gtk.GestureLongPress(touch_only=True)
            hold.connect("pressed", lambda _g, x, y, wid=w.id, r=row: self._widget_menu(wid, r, x, y))
            row.add_controller(hold)
            twice = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
            twice.connect("pressed", lambda _g, n, _x, _y, wid=w: n == 2 and self.rename_widget(wid))
            row.add_controller(twice)
            self.widget_list.append(row)
            self._widget_rows[w.id] = row
            if w.id == self.selected:
                self.widget_list.select_row(row)
        if not widgets:
            from luma_appkit import ListEmptyState as Empty

            empty = Gtk.ListBoxRow(selectable=False, activatable=False)
            empty.set_child(Empty("No widgets in this scene"))
            self.widget_list.append(empty)

    def _selected_widget(self) -> Widget | None:
        scene = self.project.active_scene
        return scene.widget(self.selected) if scene and self.selected else None

    # ── Inspector ──

    def _refresh_inspector(self) -> None:
        """The Inspector shows the selection (Source, then Look and Arrange for a picture) or, with nothing
        selected, the scene. It is rebuilt only when the selection or the structure changes, never while
        a field in it is being dragged."""
        pane = getattr(self, "inspector", None)
        if pane is None:
            return
        scroll = self._inspector_scroll()
        offset = scroll.get_vadjustment().get_value() if scroll is not None else 0
        keep = getattr(self, "_inspector_for", None)
        pane.clear()
        self._inspector_sections = {}
        w = self._selected_widget()
        several = [i for i in getattr(self, "selection", []) if self.project.active_scene and
                   self.project.active_scene.widget(i)]
        if len(several) > 1:
            self._inspector_for = None
            self.inspector_tile.set_title(f"{len(several)} widgets")
            pictures = [w for w in self._targets() if w.has_picture]
            if len(pictures) > 1:
                self._inspector_section("look", "Look", self._group_look_panel(pictures))
            self._inspector_section("arrange", "Arrange", self._group_panel(several))
            return
        self._inspector_for = w.id if w is not None else None
        # With both canvases on, the Inspector names the one its edits go to.
        on = f" · {FORMATS[self.project.format][0].split()[0]}" if len(getattr(self, "canvas_on", ())) > 1 else ""
        if w is None:
            scene = self.project.active_scene
            self.inspector_tile.set_title((scene.name if scene else "Scene") + on)
            if scene is not None:
                self._inspector_section("scene", "Scene", self._scene_panel(scene))
            return
        self.inspector_tile.set_title(w.name + (on if w.has_picture else ""))
        self._inspector_section("source", "Source", self._source_panel(w))
        if w.has_picture:
            self._inspector_section("look", "Look", self._look_panel(w))
            self._inspector_section("arrange", "Arrange", self._arrange_panel(w))
        if scroll is not None and keep == self._inspector_for:
            GLib.idle_add(lambda: scroll.get_vadjustment().set_value(offset) and False)

    def _group_look_panel(self, ws: list[Widget]) -> Gtk.Widget:
        """Several pictures' Look: a value they share shows; where they differ the row says Mixed, and a
        change applies to all (one undo step per drag)."""
        current = self._shared_look(ws)
        tiles = BarTiles([BarTile(icon, label, lambda k=key: self.apply_look_all(k), on=key == current)
                          for key, (label, icon, _v) in self.LOOKS.items()], columns=5, size="compact")
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rows.add_css_class("streamy-rows")
        effects = [self.project.effects_on(w) for w in ws]
        ids = tuple(w.id for w in ws)

        def mixed(values) -> Gtk.Widget | None:
            if len({round(v, 2) for v in values}) <= 1:
                return None
            word = TypeLabel("Mixed", role="caption")
            word.set_tooltip_text("Their values differ; a change applies to all")
            return word

        def setter(name: str, label: str):
            def apply(v: float) -> None:
                self._checkpoint(f"Change {label.lower()}", key=(ids, name))
                for w in ws:
                    if name == "shadow":
                        self._shadow_to(w, v)
                    else:
                        self.project.set_effect(w, name, v)
                    self.engine.update_widget(w)
                self._changed()
            return apply

        for name, label, low, high, unit, values in (
                ("corner_radius", "Corners", 0, 200, "px", [fx.corner_radius for fx in effects]),
                ("stroke_width", "Border", 0, 40, "px", [fx.stroke_width for fx in effects]),
                ("shadow", "Shadow", 0, 100, "%", [self.shadow_strength(fx) for fx in effects])):
            rows.append(self._slider(label, values[-1], low, high, setter(name, label), unit=unit, trail=mixed(values)))
        return self._panel(tiles, rows)

    def _group_panel(self, ids: list[str]) -> Gtk.Widget:
        """Several widgets: align and distribute them within their box (LumaUI align_rects), and what applies
        to all of them (order, hide, lock, duplicate, delete)."""
        ws = self._targets()
        rows = []
        if sum(1 for w in ws if w.has_picture) > 1:
            for how, label, icon in self.GROUP_ALIGN:
                rows.append(PanelRow(label, icon=icon, closes=False, on_activate=lambda h=how: self._align_selection(h)))
            if len(ids) > 2:
                rows.append(PanelRow("Distribute across", icon="between-horizontal-end", closes=False,
                                     on_activate=lambda: self._align_selection("", distribute="h")))
                rows.append(PanelRow("Distribute down", icon="between-vertical-end", closes=False,
                                     on_activate=lambda: self._align_selection("", distribute="v")))
            rows += [PanelRow("Bring forward", icon="arrow-up", closes=False, on_activate=lambda: self.restack_all_by(1)),
                     PanelRow("Send backward", icon="arrow-down", closes=False, on_activate=lambda: self.restack_all_by(-1))]
            shown = any(w.visible for w in ws if w.has_picture)
            rows.append(PanelRow("Hide all" if shown else "Show all", icon="eye-off" if shown else "eye", closes=False,
                                 on_activate=self.toggle_hidden))
        locked = all(w.locked for w in ws)
        rows += [PanelRow("Unlock all" if locked else "Lock all", icon="lock-open" if locked else "lock", closes=False,
                          on_activate=self.toggle_lock),
                 PanelRow(f"Duplicate {len(ids)} widgets", icon="copy", closes=False, on_activate=self.duplicate_selected),
                 PanelRow(f"Delete {len(ids)} widgets", icon="trash-2", danger=True, on_activate=self.delete_selected)]
        return self._panel(*rows)

    def _inspector_section(self, key: str, title: str, content: Gtk.Widget) -> None:
        heading = self.inspector.add_section(title)
        self.inspector.add(content)
        self._inspector_sections[key] = heading

    def _inspector_scroll(self) -> Gtk.ScrolledWindow | None:
        tile = getattr(self, "inspector_tile", None)
        return tile.scroller if tile is not None else None

    def _scene_panel(self, scene) -> Gtk.Widget:
        """With nothing selected: the scene's own settings."""
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rows.add_css_class("streamy-rows")
        pictures = sum(1 for w in scene.widgets if w.has_picture)
        sounds = len(scene.widgets) - pictures
        cw, ch = self.project.canvas
        rows.append(PanelRow(scene.name, icon="clapperboard", subtitle=f"{pictures} picture{'s' if pictures != 1 else ''}"
                             f" · {sounds} sound{'s' if sounds != 1 else ''} · {cw}×{ch}", closes=False,
                             on_activate=lambda: self.rename_scene(scene.id)))
        rows.append(PanelSwitch("star", "Opens at startup", active=self.project.startup == scene.id,
                                on_toggle=lambda _on: self.set_startup(scene.id)))
        if self.project.format != self.project.primary:
            rows.append(PanelRow(f"Reset to match {self._primary_words()}", icon="link-2", closes=False,
                                 on_activate=self.reset_secondary))
        rows.append(PanelRow("Add widget", icon="plus", closes=False,
                             on_activate=lambda: self._add_widget_menu(self.inspector)))
        return rows

    def _number(self, label, value, on_change, *, unit=None, minimum=None, maximum=None, step=None, digits=0,
                name=None):
        return PropertyNumber(label, value, unit=unit, minimum=minimum, maximum=maximum, step=step, digits=digits,
                              name=name, compact=True, on_change=on_change)

    def _panel(self, *sections: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("streamy-editor")
        for section in sections:
            if section is not None:
                box.append(section)
        return box

    #: Every slider row ends in the same slot (the Border row's swatch), so all tracks share their edges.
    ROW_SLOT = 28

    def _slider(self, label: str, value: float, low: float, high: float, on_change, *, unit: str = "px",
                key=None, default: float = 0.0, trail: Gtk.Widget | None = None,
                own: tuple | None = None) -> Gtk.Widget:
        """One editor row: a name, a slider and its value (LumaUI's ValueSlider, one line), then its slot."""
        def changed(v: float) -> None:
            if not self._building:
                on_change(v)

        slider = ValueSlider(label, value, low, high, unit=unit, default=default, step=1, layout="row",
                             on_change=changed)
        slider.set_hexpand(True)
        row = Gtk.Box(spacing=8)
        row.add_css_class("streamy-row")
        if own is not None:
            # (overridden, reset): this canvas's own value; a click on the dot follows the primary again.
            dot = OverrideDot(active=own[0], tooltip=f"Its own value · Click to reset to {self._primary_words()}")
            if own[0]:
                reset = Gtk.GestureClick()
                reset.connect("released", lambda *_a: own[1]())
                dot.add_controller(reset)
            row.append(dot)
        row.append(slider)
        slot = trail or Gtk.Box()
        slot.set_size_request(self.ROW_SLOT, -1)
        slot.set_valign(Gtk.Align.CENTER)
        row.append(slot)
        row.slider = slider
        return row

    # ── Source: what makes a widget work ──

    def source_summary(self, w: Widget) -> str:
        """What the Source key says at rest: "Built-in Front Camera · 1080p 30", "System default (…)"."""
        if w.kind == "camera":
            camera = self._camera_of(w)
            name = camera.label if camera else w.source.get("device-name") or "Camera"
            mode = self._mode_of(w, camera)
            scaled = w.source.get("scaled")
            height = scaled[1] if scaled else mode[1] if mode else None
            return f"{name} · {height}p{mode[2]}" if mode else name
        if w.kind == "microphone":
            return self.devices.words("source", w.source.get("device"))
        if w.kind == "desktop-audio":
            return self.devices.words("sink", w.source.get("device"))
        if w.kind == "app-audio":
            return w.source.get("heard") or "Choose apps"
        if w.kind == "image":
            return Path(w.source.get("path", "")).name or "Choose image"
        size = w.source.get("size")
        what = "Display" if w.kind == "display" else "Window"
        return f"{what} · {size[0]}×{size[1]}" if size else what

    def _camera_of(self, w: Widget):
        cameras = self.portals.cameras if self.portals is not None else None
        return cameras.find(w.source.get("camera")) if cameras is not None else None

    @staticmethod
    def _mode_of(w: Widget, camera) -> tuple | None:
        mode = w.source.get("mode")
        if isinstance(mode, (list, tuple)) and len(mode) == 3:
            return tuple(mode)
        return camera.modes[0] if camera is not None and camera.modes else None

    def _source_panel(self, w: Widget) -> Gtk.Widget:
        problem = self.widget_errors.get(w.id)
        parts: list = []
        if problem is not None or w.id in self.widget_waiting:
            parts.append(TypeLabel(self.problem_words(w, problem) if problem else
                                   f"{self.source_name(w)} is connected but hasn't sent a picture yet.",
                                   role="body", wrap=True))
        if w.kind == "camera":
            parts += self._camera_rows(w)
        elif w.kind in ("display", "window"):
            parts += [PanelRow("Change…", icon="monitor-up" if w.kind == "display" else "app-window",
                               subtitle="Choose another " + ("display" if w.kind == "display" else "window"),
                               on_activate=lambda: self._reshare(w)),
                      PanelSwitch("mouse-pointer-2", "Show the pointer",
                                  active=w.source.get("cursor", True),
                                  on_toggle=lambda on: self._set_cursor(w, on))]
        elif w.kind in ("microphone", "desktop-audio"):
            parts += self._device_rows(w, "source" if w.kind == "microphone" else "sink")
            parts.append(self._gain_slider(w))
            parts.append(PanelHeading("Monitor output"))
            parts += self._monitor_rows(w)
        elif w.kind == "app-audio":
            parts += [TypeLabel(w.source.get("heard") or "The sound of the apps you choose", role="body", wrap=True),
                      PanelRow("Choose other apps…", icon="audio-lines", on_activate=lambda: self._reshare(w)),
                      self._gain_slider(w)]
        elif w.kind == "image":
            parts.append(PanelRow("Choose image…", icon="image", on_activate=lambda: self._choose_image(w)))
        return self._panel(*parts)

    def _camera_rows(self, w: Widget) -> list:
        cameras = self.portals.cameras if self.portals is not None else None
        if self.portals is None:
            return [ListEmptyState("No camera found")]
        if cameras is None or not cameras.ready:
            error = getattr(self, "_camera_error", None)
            if error is not None:
                return [ListEmptyState(self.CAMERA_ERRORS.get(error, error))]
            self._look_for_cameras()
            return [ListEmptyState("Looking for cameras…")]
        if not cameras.cameras:
            return [ListEmptyState(cameras.error or "No camera found")]
        current = self._camera_of(w)
        rows: list = [PanelHeading("Camera")]
        for camera in cameras.cameras:
            rows.append(PanelRow(camera.label, icon="camera", selected=current is not None and camera.name == current.name,
                                 closes=False, on_activate=lambda c=camera: self._choose_camera(w, c.name)))
        if current is not None and current.modes:
            from .portals import rates, resolutions

            mode = self._mode_of(w, current) or current.modes[0]
            scaled = w.source.get("scaled")
            size_key = f"{scaled[0]}x{scaled[1]}:scaled" if scaled else f"{mode[0]}x{mode[1]}"
            rows.append(PropertyChoice("Resolution", resolutions(current.modes), current=size_key,
                                       on_change=lambda key: self._choose_size(w, key)))
            fps = [(str(r), f"{r} fps") for r in rates(current.modes, mode[:2])]
            if len(fps) > 1:
                rows.append(PropertyChoice("Frame rate", fps, current=str(mode[2]),
                                           on_change=lambda key: self._choose_rate(w, int(key))))
        rows.append(PanelSwitch("flip-horizontal-2", "Mirror", active=bool(w.source.get("mirror")),
                                on_toggle=lambda on: self._set_source(w, "mirror", on, "Mirror")))
        return rows

    def _device_rows(self, w: Widget, kind: str) -> list:
        """The Source's devices, inline, the chosen one marked."""
        current = w.source.get("device")
        icon = "mic" if kind == "source" else "speaker"
        default = self.devices.default(kind)
        rows: list = [PanelRow("System default", icon=icon, subtitle=default.label if default else None,
                               selected=not current, closes=False, on_activate=lambda: self._choose_device(w, None))]
        for device in self.devices.of(kind):
            rows.append(PanelRow(device.label, icon=icon, subtitle=self.devices.subtitle(device),
                                 selected=current == device.name, closes=False,
                                 on_activate=lambda d=device: self._choose_device(w, d.name)))
        if not self.devices.of(kind):
            rows.append(ListEmptyState("No microphone found" if kind == "source" else "No output found"))
        return rows

    def _monitor_rows(self, w: Widget) -> list:
        """Monitoring: Off, Monitor, Monitor + output; while on, one "Hear it on" choice."""
        rows = [PropertyChoice("Monitor", [("off", "Off"), ("only", "Monitor"), ("both", "Monitor + output")],
                               current=w.monitor, segments=True, on_change=lambda mode: self._set_monitor(w, mode))]
        if w.monitor != "off":
            default = self.devices.default("sink")
            choices = [("", f"System default ({default.label})" if default else "System default")]
            for device in self.devices.of("sink"):
                extra = self.devices.subtitle(device)
                choices.append((device.name, f"{device.label} · {extra}" if extra else device.label))
            current = w.source.get("monitor-device") or ""
            if current not in {key for key, _l in choices}:
                current = ""
            rows.append(PropertyChoice("Hear it on", choices, current=current,
                                       on_change=lambda key: self._set_source(w, "monitor-device", key or None,
                                                                              "Monitor output")))
        return rows

    def _gain_slider(self, w: Widget) -> Gtk.Widget:
        return self._slider("Gain", w.volume_db, -60, 12, lambda v: self._set_volume(w, v), unit="dB",
                            key=(w.id, "gain"))

    def _set_source(self, w: Widget, key: str, value, label: str) -> None:
        self._checkpoint(f"Change {label.lower()}")
        if value in (None, False):
            w.source.pop(key, None)
        else:
            w.source[key] = value
        self.engine.update_widget(w)
        self._changed()
        self._regrow("source")

    def _choose_camera(self, w: Widget, name: str) -> None:
        if name == w.source.get("camera"):
            return
        self._checkpoint("Change camera")
        w.source["camera"] = name
        w.source.pop("mode", None)
        w.source.pop("sized", None)
        self._reconnect(w)

    def _choose_size(self, w: Widget, key: str) -> None:
        """A camera's resolution, live (the capture reconnects; the camera stays chosen), undoable. A scaled
        size captures the smallest of the camera's own sizes that holds it and scales on the GPU."""
        from .portals import rates, source_for_scaled

        camera = self._camera_of(w)
        if camera is None:
            return
        size, _sep, scaled = key.partition(":")
        width, height = (int(v) for v in size.split("x"))
        native = source_for_scaled(camera.modes, (width, height)) if scaled else (width, height)
        if native is None:
            return
        current = self._mode_of(w, camera)
        fps_options = rates(camera.modes, native)
        fps = min(fps_options, key=lambda r: abs(r - (current[2] if current else 30))) if fps_options else 30
        self._checkpoint("Change resolution")
        w.source["mode"] = [native[0], native[1], fps]
        if scaled:
            w.source["scaled"] = [width, height]
        else:
            w.source.pop("scaled", None)
        self._reconnect(w)

    def _choose_rate(self, w: Widget, fps: int) -> None:
        mode = self._mode_of(w, self._camera_of(w))
        if mode is None or mode[2] == fps:
            return
        self._checkpoint("Change frame rate")
        w.source["mode"] = [mode[0], mode[1], fps]
        self._reconnect(w)

    def _choose_mode(self, w: Widget, key: str) -> None:
        size, fps = key.split("@")
        width, height = size.split("x")
        self._checkpoint("Change picture")
        w.source["mode"] = [int(width), int(height), int(fps)]
        self._reconnect(w)

    def _choose_device(self, w: Widget, name: str | None) -> None:
        if name == w.source.get("device"):
            return
        self._checkpoint("Change device")
        if name:
            w.source["device"] = name
        else:
            w.source.pop("device", None)
        self._reconnect(w)

    def _set_cursor(self, w: Widget, on: bool) -> None:
        self._checkpoint("Change pointer")
        w.source["cursor"] = bool(on)
        if self.portals is not None:
            self.portals.close(w.id)
            self._choose_source(w, restore=True)  # the same choice again, with or without the pointer

    def _set_monitor(self, w: Widget, mode: str) -> None:
        self._checkpoint("Change monitor")
        w.monitor = mode
        self.engine.update_widget(w)
        self._changed(structure=True)
        self._feedback_warning(w)

    def _feedback_warning(self, w: Widget) -> None:
        """Monitoring a built-in microphone on built-in speakers feeds back: say so once."""
        if w.monitor == "off" or w.kind != "microphone":
            return
        mic = self.devices.find("source", w.source.get("device"))
        out = self.devices.find("sink", w.source.get("monitor-device"))
        if mic is not None and out is not None and mic.internal and out.internal:
            Toast.show(self.host, "Built-in speakers will feed back into the microphone. Use headphones.",
                       kind="warning")

    def _reconnect(self, w: Widget) -> None:
        """A source setting changed: its stream starts again with it."""
        if self.portals is not None and w.kind == "camera":
            self.portals.close(w.id)
            self.widget_errors.pop(w.id, None)
            self._restart_widget(w)
            self._choose_source(w, restore=True)
        else:
            self._restart_widget(w)
        self._regrow("source")

    def _regrow(self, _key: str) -> None:
        """The Inspector shows what just changed (rebuilt in place, at the same scroll)."""
        self._refresh_inspector()

    # ── Look: how it is drawn ──

    LOOKS = {
        "none": ("None", "square-dashed", dict(corner_radius=0, stroke_width=0, shadow_blur=0, shadow_x=0,
                                                shadow_y=0, shadow_opacity=0.0)),
        "rounded": ("Rounded", "rectangle-horizontal", dict(corner_radius=24, stroke_width=0, shadow_blur=0, shadow_y=0,
                                                 shadow_x=0, shadow_opacity=0.0)),
        "card": ("Card", "app-window", dict(corner_radius=20, stroke_width=0, shadow_blur=40, shadow_y=12,
                                             shadow_x=0, shadow_opacity=0.35)),
        "outline": ("Outline", "square", dict(corner_radius=16, stroke_width=4, stroke_color="FFFFFF",
                                               shadow_blur=0, shadow_y=0, shadow_x=0, shadow_opacity=0.0)),
        "floating": ("Floating", "layers", dict(corner_radius=28, stroke_width=4, stroke_color="FFFFFF",
                                                 shadow_blur=60, shadow_y=20, shadow_x=0, shadow_opacity=0.45)),
    }

    def _look_of(self, w: Widget) -> str | None:
        fx = self.project.effects_on(w)
        for key, (_label, _icon, values) in self.LOOKS.items():
            if all(abs(getattr(fx, k) - v) < 0.01 if isinstance(v, (int, float)) else getattr(fx, k) == v
                   for k, v in values.items()):
                return key
        return None

    def apply_look(self, w: Widget, key: str) -> None:
        self._checkpoint(f"Look {self.LOOKS[key][0]}")
        for name, value in self.LOOKS[key][2].items():
            self.project.set_effect(w, name, value)
        self._effects_changed(w)
        self._regrow("look")

    def _shared_look(self, ws: list[Widget]) -> str | None:
        looks = {self._look_of(w) for w in ws}
        return looks.pop() if len(looks) == 1 else None

    def apply_look_all(self, key: str) -> None:
        ws = [w for w in self._targets() if w.has_picture]
        if not ws:
            return
        self._checkpoint(f"Look {self.LOOKS[key][0]}")
        for w in ws:
            for name, value in self.LOOKS[key][2].items():
                self.project.set_effect(w, name, value)
            self.engine.update_widget(w)
        self._changed()
        self._regrow("look")

    @staticmethod
    def shadow_strength(fx) -> float:
        return round(min(100.0, max(fx.shadow_blur / 0.6, fx.shadow_opacity / 0.45 * 100 if fx.shadow_blur else 0)))

    def _set_shadow(self, w: Widget, strength: float) -> None:
        self._shadow_to(w, strength)
        self._effects_changed(w)

    def _shadow_to(self, w: Widget, strength: float) -> None:
        """One slider for the shadow: blur, drop and darkness grow together."""
        self.project.set_effect(w, "shadow_blur", strength * 0.6)
        self.project.set_effect(w, "shadow_y", round(strength * 0.2))
        self.project.set_effect(w, "shadow_opacity", 0.0 if strength <= 0 else 0.2 + 0.25 * strength / 100)

    def _look_panel(self, w: Widget) -> Gtk.Widget:
        fx = self.project.effects_on(w)
        current = self._look_of(w)
        tiles = BarTiles([BarTile(icon, label, lambda k=key: self.apply_look(w, k), on=key == current)
                          for key, (label, icon, _v) in self.LOOKS.items()], columns=5, size="compact")
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rows.add_css_class("streamy-rows")

        def fx_setter(name: str, label: str, scale: float = 1.0):
            def apply(v: float) -> None:
                self._checkpoint(f"Change {label.lower()}", key=(w.id, name))
                self.project.set_effect(w, name, v / scale)
                self._effects_changed(w)
            return apply

        secondary = self.project.format != self.project.primary
        own_now = self.project.own_look(w) if secondary else {}

        def own(*names):
            if not secondary:
                return None
            return (any(n in own_now for n in names), lambda: self._reset_look_field(w, names))

        rows.append(self._slider("Corners", fx.corner_radius, 0, 200, fx_setter("corner_radius", "Corners"),
                                 own=own("corner_radius")))
        swatch = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text="Border colour")
        swatch.add_css_class("lumaui-creative-swatch")
        swatch.update_property([Gtk.AccessibleProperty.LABEL], ["Border colour"])
        paint = Gtk.DrawingArea(content_width=18, content_height=18, can_target=False)

        def draw(_area, cr, width, height) -> None:
            rgba = Gdk.RGBA()
            rgba.parse("#" + self.project.effects_on(w).stroke_color)
            Gdk.cairo_set_source_rgba(cr, rgba)
            cr.arc(width / 2, height / 2, min(width, height) / 2, 0, 6.2832)
            cr.fill()

        paint.set_draw_func(draw)
        swatch.set_child(paint)
        swatch.connect("clicked", lambda *_a: self._pick_border(w))
        rows.append(self._slider("Border", fx.stroke_width, 0, 40, fx_setter("stroke_width", "Border"), trail=swatch,
                                 own=own("stroke_width", "stroke_color")))
        rows.append(self._slider("Shadow", self.shadow_strength(fx), 0, 100,
                                 lambda v: (self._checkpoint("Change shadow", key=(w.id, "shadow")),
                                            self._set_shadow(w, v)), unit="%",
                                 own=own("shadow_blur", "shadow_y", "shadow_opacity")))
        more = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        more.add_css_class("streamy-rows")
        more.append(self._slider("Blur", fx.shadow_blur, 0, 200, fx_setter("shadow_blur", "Blur"), own=own("shadow_blur")))
        more.append(self._slider("Offset X", fx.shadow_x, -200, 200, fx_setter("shadow_x", "Offset"), own=own("shadow_x")))
        more.append(self._slider("Offset Y", fx.shadow_y, -200, 200, fx_setter("shadow_y", "Offset"), own=own("shadow_y")))
        more.append(PropertyChoice("Border", [("inside", "Inside"), ("center", "Centre"), ("outside", "Outside")],
                                   current=fx.stroke_position, segments=True,
                                   on_change=lambda pos: (self._checkpoint("Change border position"),
                                                          self.project.set_effect(w, "stroke_position", pos),
                                                          self._effects_changed(w))))
        rows.append(Disclosure("More", content=more))
        return self._panel(tiles, rows, self._look_sync_row(w))

    def _look_sync_row(self, w: Widget) -> Gtk.Widget:
        """The Look's sync key (LumaUI SyncKey): whether the other canvas follows this Look, and the way back."""
        other = next(f for f in FORMATS if f != self.project.primary)
        primary, secondary = (FORMATS[f][0].split()[0] for f in (self.project.primary, other))
        own = self.project.own_look(w, other)
        if self.project.format == self.project.primary:
            return SyncKey(primary, secondary, synced=not own, primary=True, changes=len(own))
        return SyncKey(primary, secondary, synced=not own, changes=len(own),
                       on_unsync=lambda: self.unsync_look(w), on_sync=lambda: self.sync_look(w))

    def unsync_look(self, w: Widget) -> None:
        self._checkpoint("Own look")
        self.project.unsync_look(w)
        self._effects_changed(w)
        self._regrow("look")

    def _reset_look_field(self, w: Widget, names: tuple) -> None:
        self._checkpoint(f"Reset to {self._primary_words()}")
        for name in names:
            self.project.sync_look(w, name=name)
        self._effects_changed(w)
        self._regrow("look")

    def sync_look(self, w: Widget) -> None:
        self._checkpoint("Sync look")
        self.project.sync_look(w)
        self._effects_changed(w)
        self._regrow("look")

    def _primary_words(self) -> str:
        return FORMATS[self.project.primary][0].split()[0]

    def _link_row(self, w: Widget) -> Gtk.Widget | None:
        """On the secondary canvas: whether this widget's place follows the primary canvas, and the way back."""
        if self.project.format == self.project.primary:
            return None
        primary = self._primary_words()
        if w.own.get("placement"):
            return PanelRow(f"Own place · Match {primary}", icon="link", closes=False,
                            on_activate=lambda: self.match_primary(w))
        return PanelRow(f"Linked to {primary}", icon="link-2", sensitive=False)

    def match_primary(self, w: Widget) -> None:
        self._checkpoint(f"Match {self._primary_words()}")
        self.project.match_primary(w.id)
        self.engine.update_widget(w)
        self._sync_rect(w)
        self._changed()
        self._regrow("arrange")

    def reset_secondary(self) -> None:
        scene = self.project.active_scene
        if scene is None:
            return
        self._checkpoint(f"Reset to match {self._primary_words()}")
        self.project.reset_secondary(scene.id)
        self._changed(structure=True)
        self._show_scene()

    def _pick_border(self, w: Widget) -> None:
        rgba = Gdk.RGBA()
        rgba.parse("#" + self.project.effects_on(w).stroke_color)
        state = {"started": False}

        def changed(color) -> None:
            if not state["started"]:
                state["started"] = True
                self._checkpoint("Change border colour")
            self.project.set_effect(w, "stroke_color", "%02X%02X%02X" % (
                round(color.red * 255), round(color.green * 255), round(color.blue * 255)))
            self._effects_changed(w)

        picker = ColorPicker(rgba, with_alpha=False, on_change=changed,
                             on_commit=lambda _c: state.__setitem__("started", False))
        self.center.grow("colour", picker)

    # ── Arrange: where it sits ──

    def _arrange_panel(self, w: Widget) -> Gtk.Widget:
        def rect(field):
            def apply(value):
                self._checkpoint("Move " + w.name, key=(w.id, "rect"))
                x, y, width, height = w.x, w.y, w.width, w.height
                if field == "x":
                    x = value
                elif field == "y":
                    y = value
                elif field == "w":
                    width = max(8, value)
                    if not w.stretched and w.width:
                        height = width * w.height / w.width
                else:
                    height = max(8, value)
                    if not w.stretched and w.height:
                        width = height * w.width / w.height
                self._set_rect(w, x, y, width, height, commit=True)
            return apply

        crop = w.effects

        def cropper(name):
            def apply(value):
                self._checkpoint("Crop " + w.name, key=(w.id, "crop"))
                setattr(w.effects, name, max(0.0, min(0.49, value / 100)))
                self._effects_changed(w)
            return apply

        position = PropertySection(
            "Position",
            (self._number("X", w.x, rect("x"), unit="px"), self._number("Y", w.y, rect("y"), unit="px")),
            (self._number("W", w.width, rect("w"), unit="px", minimum=8),
             self._number("H", w.height, rect("h"), unit="px", minimum=8)),
            AlignmentActions(on_align=self._align))
        cropping = PropertySection(
            "Crop",
            (self._number("Left", crop.crop_left * 100, cropper("crop_left"), unit="%", minimum=0, maximum=49),
             self._number("Right", crop.crop_right * 100, cropper("crop_right"), unit="%", minimum=0, maximum=49)),
            (self._number("Top", crop.crop_top * 100, cropper("crop_top"), unit="%", minimum=0, maximum=49),
             self._number("Bottom", crop.crop_bottom * 100, cropper("crop_bottom"), unit="%", minimum=0, maximum=49)))
        order = BarTiles([BarTile("bring-to-front", "Front", lambda: self.restack(1)),
                          BarTile("arrow-up", "Forward", lambda: self.restack_by(w, 1)),
                          BarTile("arrow-down", "Backward", lambda: self.restack_by(w, -1)),
                          BarTile("layers", "Back", lambda: self.restack(-1)),
                          BarTile("scaling", "Reset aspect", self.reset_aspect)], columns=5, size="compact")

        def opacity(v: float) -> None:
            self._checkpoint("Change opacity", key=(w.id, "opacity"))
            self.project.set_effect(w, "opacity", v / 100)
            self._effects_changed(w)

        layer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        link = self._link_row(w)
        if link is not None:
            layer.append(link)
        layer.append(PanelHeading("Layer"))
        layer.append(order)
        layer.append(self._slider("Opacity", w.effects.opacity * 100, 0, 100, opacity, unit="%", default=100))
        return self._panel(position, cropping, layer)

    def _reshare(self, w: Widget) -> None:
        """Change…: a new pick in the portal's picker, replacing the saved choice (display, window, app sound)."""
        if self.portals is None:
            return
        self._checkpoint(f"Change {w.name} source")
        self.portals.close(w.id)
        w.source.pop("restore-token", None)
        w.source.pop("sized", None)
        self.widget_errors.pop(w.id, None)
        self.widget_waiting.discard(w.id)
        self.engine._remove_branch(w.id)
        self.center.fold()
        self._choose_source(w, restore=False)

    def _choose_image(self, w: Widget, on_chosen=None) -> None:
        dialog = Gtk.FileDialog(title="Choose an image", modal=True)
        images = Gtk.FileFilter(name="Images")
        images.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog.set_filters(filters)

        def chosen(source, result) -> None:
            try:
                file = source.open_finish(result)
            except GLib.Error:
                if on_chosen is not None:
                    on_chosen(False)
                return
            w.source["path"] = file.get_path()
            scene, _w = self.project.find_widget(w.id)
            w.name = ""
            w.name = self.project.human_name(scene.id, "image", file.get_basename().rsplit(".", 1)[0])
            size = self._image_size(w.source["path"])
            if size:
                w.source["size"] = list(size)
                self.project.place_natural(w.id)
            if on_chosen is not None:
                on_chosen(True)
            self._restart_widget(w)

        dialog.open(self, None, chosen)

    def _restart_widget(self, w: Widget) -> None:
        self.engine._remove_branch(w.id)
        self._show_scene()
        self._changed(structure=True)

    def _grow(self, key: str) -> None:
        """Show one of the selection's Inspector sections (Source, Look, Arrange): scrolled to, and on a
        phone the Inspector's drawer opened."""
        if self._inspector_for != self.selected:
            self._refresh_inspector()
        heading = self._inspector_sections.get(key)
        if heading is None:
            return
        self._open_inspector()
        scroll = self._inspector_scroll()

        def reveal() -> bool:
            ok, bounds = heading.compute_bounds(scroll.get_child()) if scroll is not None else (False, None)
            if ok:
                scroll.get_vadjustment().set_value(bounds.get_y())
            return False

        GLib.idle_add(reveal)

    # ── Audio inset ──

    def _refresh_audio(self) -> None:
        """A channel strip per sound in the scene: its name over the device it hears, the fader with its
        meter, the level, and Mute and Monitor. A click on the strip selects the widget."""
        for strip in list(self._strips.values()):
            self.mixer_row.remove(strip)
        self._strips.clear()
        scene = self.project.active_scene
        sounds = [w for w in (scene.widgets if scene else []) if w.has_sound]
        self.audio_empty.set_visible(not sounds)
        self.mixer_row.set_visible(bool(sounds))
        for w in sounds:
            strip = MixerStrip(w.name, self.source_summary(w), db=w.volume_db, min_db=-60, max_db=12,
                               muted=w.muted, monitoring=w.monitor != "off",
                               on_change=lambda value, wid=w: self._volume_moved(wid, value),
                               on_mute=lambda muted, wid=w: muted != wid.muted and self._toggle_mute(wid),
                               on_monitor=lambda on, wid=w: self._monitor_toggled(wid, on))
            pick = Gtk.GestureClick()
            pick.connect("released", lambda *_a, wid=w: self.select(wid.id))
            strip.add_controller(pick)
            self.mixer_row.append(strip)
            self._strips[w.id] = strip
        self.dock.refit()

    def _volume_moved(self, w: Widget, value: float) -> None:
        self._checkpoint(f"Change {w.name} gain", key=(w.id, "gain"))
        self._set_volume(w, value)

    def _monitor_toggled(self, w: Widget, on: bool) -> None:
        """A strip's Monitor key: on hears it here as it was last heard ("only" or "both"); off stops."""
        if on == (w.monitor != "off"):
            return
        if on:
            self._set_monitor(w, self._monitor_last.get(w.id, "both"))
        else:
            self._monitor_last[w.id] = w.monitor
            self._set_monitor(w, "off")

    def _levels(self, _engine, widget_id: str, left: float, right: float) -> None:
        strip = self._strips.get(widget_id)
        if strip is not None and strip.get_mapped():
            strip.set_levels_db(left, right)

    def _set_volume(self, w: Widget, value: float) -> None:
        w.volume_db = round(value)
        self.engine.update_widget(w)
        self._changed()

    def _toggle_mute(self, w: Widget) -> None:
        self._checkpoint(("Unmute " if w.muted else "Mute ") + w.name)
        w.muted = not w.muted
        self.engine.update_widget(w)
        self._changed(structure=True)

    def _devices_changed(self) -> None:
        """A microphone or output came or went, or the default changed: strips and an open chooser follow."""
        if self.project.active_scene and any(w.has_sound for w in self.project.active_scene.widgets):
            self._refresh_audio()
            self._refresh_bar()
        self._regrow("source")

    def _cameras_changed(self) -> None:
        self._camera_error = None
        self._refresh_bar()
        self._regrow("source")

    #: Why the camera list is empty, in words.
    CAMERA_ERRORS = {"denied": "Camera access is turned off for Streamy", "failed": "The camera service did not answer",
                     "timeout": "No camera found", "no camera": "No camera found"}

    def _look_for_cameras(self) -> None:
        """Ask the camera portal for the list (once); after 3 s without an answer, say so instead of waiting."""
        if getattr(self, "_camera_asked", False):
            return
        self._camera_asked = True

        def failed(reason: str) -> None:
            self._camera_asked = False
            self._camera_error = reason
            self._regrow("source")

        def late() -> bool:
            cameras = self.portals.cameras
            if cameras is None or not cameras.ready:
                self._camera_asked = cameras is not None  # a watch on its way keeps going; else ask again later
                self._camera_error = "timeout"
                self._regrow("source")
            return False

        self.portals.watch_cameras(self._cameras_changed, failed)
        GLib.timeout_add_seconds(3, late)

    # ── The bar ──

    def _addable(self) -> tuple[str, ...]:
        kinds = ("display", "window", "camera", "image", "microphone", "desktop-audio")
        # App audio needs Luma's AudioCapture portal; asked once, after the window is up.
        if self._app_audio is None and self.portals is not None:
            self._app_audio = self.portals.audio_capture_available()
        return kinds + (("app-audio",) if self._app_audio or self.fake else ())

    def _add_menu(self) -> list:
        return [MenuItem(KINDS[kind][0], icon=KINDS[kind][1], on_activate=lambda k=kind: self.add_widget(k))
                for kind in self._addable()]

    def _refresh_bar(self) -> None:
        """The bar: the selection (its chip and quick actions) on the left when there is one, then always
        the two outputs. Record becomes "Stop recording" with its running time while recording; Go live
        becomes "End stream" with the LIVE time and bitrate while live (or "Add a destination" when no
        stream row would go live). Each runs on its own; both can run together."""
        items: list = []
        live = self.stream_state != "off"
        w = self._selected_widget()
        more = None
        several = self._targets() if len(self._selected_ids()) > 1 else []
        if several:
            # Several: what applies to all of them; Look and Arrange open in the Inspector.
            pictures = [x for x in several if x.has_picture]
            items.append(BarChip(f"{len(several)} widgets", on_dismiss=self.deselect))
            if pictures:
                items += [BarAction("squircle", "Look", on_activate=lambda: self._grow("look")),
                          BarAction("align-center-horizontal", "Arrange", on_activate=lambda: self._grow("arrange"))]
                shown = any(x.visible for x in pictures)
                more = [BarAction("eye-off" if shown else "eye", "Hide all" if shown else "Show all",
                                  on_activate=self.toggle_hidden)]
            more = (more or []) + [BarAction("copy", "Duplicate", on_activate=self.duplicate_selected),
                                   BarAction("trash-2", f"Delete {len(several)}", danger=True,
                                             on_activate=self.delete_selected)]
            items.append(SEPARATOR)
        elif w is not None:
            cause = self.cause_line(w, self.widget_errors.get(w.id))
            items.append(BarChip(w.name, detail=cause, on_dismiss=self.deselect) if cause else
                         BarChip(w.name, on_dismiss=self.deselect))
            # Source first: what makes the widget work, its summary as the key's words.
            items.append(BarAction(KINDS[w.kind][1], self.source_summary(w), on_activate=lambda: self._grow("source")))
            if w.has_picture:
                items.append(BarAction("crop", tooltip="Crop on the canvas",
                                       on_activate=lambda: self.arrange.set_cropping(w.id)))
            fix = self.fix_words(w, self.widget_errors[w.id]) if w.id in self.widget_errors else None
            if fix:
                items.append(BarAction("monitor-up" if w.kind in ("display", "window") else "rotate-ccw", fix,
                                       on_activate=lambda: self.retry_widget(w)))
            elif w.id in self.widget_waiting:
                items.append(BarAction("rotate-ccw", "Try again",
                                       on_activate=lambda: (self.widget_waiting.discard(w.id),
                                                            self._restart_widget(w))))
            if w.has_picture:
                switch = BarAction("eye" if not w.visible else "eye-off", "Show" if not w.visible else "Hide",
                                   on_activate=self.toggle_hidden)
            else:
                switch = BarAction("volume-2" if w.muted else "volume-x", "Unmute" if w.muted else "Mute",
                                   on_activate=lambda: self._toggle_mute(w))
            more = [switch, BarAction("pencil", "Rename", on_activate=lambda: self.rename_widget(w)),
                    BarAction("copy", "Duplicate", on_activate=self.duplicate_selected),
                    BarAction("trash-2", "Delete", danger=True, on_activate=self.delete_selected)]
            items.append(SEPARATOR)
        else:
            items.append(BarAction("plus", "Add widget", menu=self._add_menu))
        self.readouts = {}
        if self.recording_since is not None:
            self.readouts["record"] = BarChip("Recording", meta=clock(time.monotonic() - self.recording_since),
                                              live=True)
            items.append(self.readouts["record"])
            items.append(BarAction("square", "Stop recording", record=True, on_activate=self.stop_recording))
        else:
            items.append(BarAction("circle", "Record", on_activate=self.toggle_record))
        if live:
            self.readouts["stream"] = BarChip(self._stream_word(), meta=clock(time.monotonic() - (
                self.live_since or time.monotonic())), detail=self._stream_words(), live=True)
            items.append(self.readouts["stream"])
            items.append(BarAction("square", "End stream", danger=True, primary=True, on_activate=self.ask_end_stream))
        elif self._streams_ready():
            items.append(BarAction("radio-tower", "Go live", primary=True, on_activate=self.go_live))
        else:
            items.append(BarAction("plus", "Add a destination", primary=True, on_activate=self.add_destination))
        self._sync_canvas_state()
        self._sync_output_title()
        if more is not None:
            self.center.show_bar(items, more=more)
        else:
            self.center.show_bar(items)

    def _streams_ready(self) -> bool:
        """Some stream destination goes live with Go live."""
        store = getattr(self, "dest_store", None)
        return store is not None and any(r.is_stream and r.enabled for r in store.rows)

    def add_destination(self) -> None:
        """The Destinations panel, shown, with its + menu open."""
        if not self._is_open("destinations") and hasattr(self.dock, "open_tile"):
            self.dock.open_tile("destinations")
        if hasattr(self.dock, "show_tile"):
            self.dock.show_tile("destinations")
        self.dest_panel.add_menu()

    def _sync_output_title(self) -> None:
        """The window says what it sends: "LIVE" and "● Recording" by the app's name and in its title."""
        words = []
        if self.stream_state != "off":
            words.append("LIVE" if self.stream_state == "on" else self._stream_word())
        if self.recording_since is not None:
            words.append("● Recording")
        text = " · ".join(words)
        if text == getattr(self, "_output_title", None):
            return
        self._output_title = text
        if hasattr(self, "set_identity_subtitle"):
            self.set_identity_subtitle(text)
        self.set_title(f"{text} — Streamy" if text else "Streamy")

    def _arrange_menu(self) -> list:
        return [MenuItem("Bring to front", icon="arrow-up", note="Ctrl+]", on_activate=lambda: self.restack(1)),
                MenuItem("Send to back", icon="arrow-down", note="Ctrl+[", on_activate=lambda: self.restack(-1)),
                MenuItem("Fill the canvas", icon="maximize-2", on_activate=lambda: self._align("fill")),
                MenuItem("Centre", icon="align-center-vertical",
                         on_activate=lambda: (self._align("center"), self._align("middle"))),
                MenuItem("Reset aspect", icon="scaling", on_activate=self.reset_aspect),
                MenuItem("Lock" if not (self._selected_widget() and self._selected_widget().locked) else "Unlock",
                         icon="lock", on_activate=self.toggle_lock)]

    def reset_aspect(self) -> None:
        w = self._selected_widget()
        if w is None or w.natural_aspect is None:
            return
        self._checkpoint("Reset aspect")
        self.project.reset_aspect(w.id)
        self._set_rect(w, w.x, w.y, w.width, w.height, commit=True)

    def _stream_word(self) -> str:
        return {"reconnecting": "Reconnecting", "starting": "Connecting"}.get(self.stream_state, "Live")

    def _stream_words(self) -> str:
        live = [self.outputs.stats(r.id) for r in self.dest_store.rows if r.is_stream and r.id in self.outputs.rows]
        live = [st for st in live if st is not None and st.state == "live"]
        if live:
            kbps = f"{round(sum(st.bitrate_kbps for st in live)):,} kbps"
            dropped = sum(st.dropped_network + st.dropped_encoder for st in live)
            return f"{kbps} · " + ("no dropped frames" if not dropped else f"{dropped:,} dropped")
        stats = self.engine.stats("stream")
        if stats is None:
            return "Connecting"
        kbps = f"{round(stats.bitrate_kbps):,} kbps"
        dropped = "no dropped frames" if not stats.dropped else f"{stats.dropped:,} dropped"
        return f"{kbps} · {dropped}"

    def _every_second(self) -> bool:
        readouts = getattr(self, "readouts", {})
        if "stream" in readouts and self.live_since is not None:
            readouts["stream"].update(self._stream_word(), meta=clock(time.monotonic() - self.live_since),
                                      detail=self._stream_words())
        if "record" in readouts and self.recording_since is not None:
            readouts["record"].update(meta=clock(time.monotonic() - self.recording_since))
        # The sidebar picture every 3 s: rendering it costs the window's thread a texture import.
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 3 == 1:
            self._update_thumbnail()
        return True

    def _update_thumbnail(self) -> None:
        """The active scene's sidebar picture, every few seconds and only while the sidebar shows."""
        paintable = self.engine.paintable_of(self.project.primary) or self.engine.paintable
        scene = self.project.active_scene
        if paintable is None or scene is None or not self.sidebar.get_mapped() or not self._on_screen():
            return
        if not any(w.has_picture and w.visible and w.id not in self.widget_errors for w in scene.widgets):
            if self._thumbs.pop(scene.id, None) is not None:
                self._refresh_scenes()
            return  # nothing to show: the row keeps its glyph, not a black square
        image = self._small(paintable.get_current_image())
        if image is None:
            return
        self._thumbs[scene.id] = image
        row = self._scene_rows.get(scene.id)
        picture = self._find_picture(row) if row is not None else None
        if picture is not None:
            picture.set_paintable(image)
        else:
            self._refresh_scenes()  # its first picture: the row's lead becomes a thumbnail

    def _small(self, image) -> Gdk.Texture | None:
        """The whole canvas at thumbnail size, never cropped: contained in the row's 42 × 34 slot at its
        own aspect (16:9 lies across it, 9:16 stands centred in it), so the scene's layout reads."""
        if image is None or image.get_intrinsic_width() == 0 or self.get_renderer() is None:
            return None
        from gi.repository import Graphene

        tw, th = 42, 34
        iw, ih = image.get_intrinsic_width(), image.get_intrinsic_height()
        scale = min(tw / iw, th / ih)
        w, h = iw * scale, ih * scale
        snapshot = Gtk.Snapshot()
        snapshot.translate(Graphene.Point().init((tw - w) / 2, (th - h) / 2))
        image.snapshot(snapshot, w, h)
        node = snapshot.to_node()
        if node is None:
            return None
        return self.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, tw, th))

    @staticmethod
    def _find_picture(widget: Gtk.Widget) -> Gtk.Picture | None:
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Picture):
                return child
            found = StreamyWindow._find_picture(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    # ── Selection ──

    def select(self, widget_id: str | None) -> None:
        if widget_id == self.selected and len(getattr(self, "selection", [])) <= 1:
            return
        self.selected = widget_id
        self.selection = [widget_id] if widget_id else []
        w = self._selected_widget()
        for fmt, arrange in self.arranges.items():
            arrange.set_selected(widget_id if w is not None and w.has_picture and fmt == self.project.format
                                 else None)
        self._building = True
        row = self._widget_rows.get(widget_id) if widget_id else None
        self.widget_list.unselect_all()
        if row is not None:
            self.widget_list.select_row(row)
        self._building = False
        self._refresh_inspector()
        self._refresh_bar()

    def deselect(self) -> None:
        self.select(None)

    def _inspector_shown(self) -> bool:
        return getattr(self, "dock", None) is not None and self._is_open("inspector") and (
            not self._phone() or self.inspector_tile.get_mapped())

    def _open_inspector(self) -> None:
        if not self._is_open("inspector"):
            self.dock.open_tile("inspector")
        if self._phone():
            self.dock.open_drawer("inspector")

    def toggle_inspector(self) -> None:
        """App menu › Panels › Inspector, Ctrl+I: open (on the selection's Source) or close."""
        if self._inspector_shown() and not self._phone():
            self.dock.close_tile("inspector")
        elif self._selected_widget() is not None:
            self._grow("source")
        else:
            self._open_inspector()

    def _phone(self) -> bool:
        from luma_appkit.structure_adapt import is_phone

        return is_phone(self)

    def _escape(self, _controller, keyval: int, _code: int, state) -> bool:
        if keyval != Gdk.KEY_Escape or state & Gtk.accelerator_get_default_mod_mask():
            return False
        if self.selected is None and not getattr(self, "selection", []):
            return False
        if isinstance(self.get_focus(), Gtk.Text) or self.center.grown or self.center.state in ("editor", "split"):
            return False  # a field's or the bar's own Esc first
        self.deselect()
        return True

    def _canvas_selected(self, widget_id) -> None:
        if not self._building:
            self.select(widget_id)

    def _widget_rows_changed(self, listbox) -> None:
        if self._building:
            return
        rows = sorted(listbox.get_selected_rows(), key=lambda r: r.get_index())
        ids = [r.widget_id for r in rows if getattr(r, "widget_id", None)]
        focus = listbox.get_focus_child()
        primary = getattr(focus, "widget_id", None) if focus in rows else None
        self.set_selection(ids, primary=primary)

    def set_selection(self, ids: list[str], primary: str | None = None) -> None:
        """Select several widgets (the list, the canvas and the Inspector agree); ``primary`` (else the last)
        is the one the canvas's handles and the bar name."""
        scene = self.project.active_scene
        ids = [i for i in dict.fromkeys(ids) if scene is not None and scene.widget(i) is not None]
        if len(ids) <= 1:
            self.select(ids[0] if ids else None)
            return
        if primary in ids:
            ids = [i for i in ids if i != primary] + [primary]
        self.selection, self.selected = ids, ids[-1]
        self._show_selection()
        self._refresh_inspector()
        self._refresh_bar()

    def _show_selection(self) -> None:
        """The list's rows and the edited canvas's boxes show ``self.selection``."""
        self._building = True
        self.widget_list.unselect_all()
        for widget_id in self.selection:
            row = self._widget_rows.get(widget_id)
            if row is not None:
                self.widget_list.select_row(row)
        self._building = False
        scene = self.project.active_scene
        pictures = [i for i in self.selection if scene is not None and (w := scene.widget(i)) and w.has_picture]
        for fmt, arrange in self.arranges.items():
            if fmt != self.project.format:
                arrange.set_selected(None)
            elif hasattr(arrange, "set_selection"):
                arrange.set_selection(pictures or None)
            else:
                arrange.set_selected(pictures[-1] if pictures else None)

    # ── Direct manipulation ──

    def _set_rect(self, w: Widget, x, y, width, height, *, commit: bool) -> None:
        self.project.set_rect(w.id, x, y, width, height)
        self.engine.update_widget(w)
        self._sync_rect(w)
        if commit:
            self._changed()

    def _canvas_changed(self, widget_id, x, y, width, height) -> None:
        _scene, w = self.project.find_widget(widget_id)
        if widget_id not in self._drag_start:
            self._checkpoint(f"Move {w.name}")
            self._drag_start[widget_id] = (w.x, w.y, w.width, w.height)
        self.project.set_rect(widget_id, x, y, width, height)
        self._following[widget_id] = None
        self.engine.update_widget(w)
        for fmt, arrange in self.arranges.items():
            if fmt != self.project.format:  # the other canvas's box follows a linked placement
                view = self.project.view_on(w, fmt)
                arrange.set_item_rect(w.id, view.x, view.y, view.width, view.height)

    def _frame_shown(self, _engine, fmt: str, widget_id: str, x: float, y: float, width: float,
                     height: float) -> None:
        """The preview on screen now draws ``widget_id`` here: a dragged widget's outline stays on its picture."""
        if fmt != self.project.format or widget_id not in self._following or \
                not hasattr(self.arrange, "set_item_shown_rect"):
            return
        self.arrange.set_item_shown_rect(widget_id, x, y, width, height)
        final = self._following[widget_id]
        if final is not None and all(abs(a - b) < 0.5 for a, b in zip(final, (x, y, width, height))):
            del self._following[widget_id]  # caught up: the live rect again
            self.arrange.clear_item_shown_rect(widget_id)

    def _canvas_committed(self, widget_id, x, y, width, height) -> None:
        self._canvas_changed(widget_id, x, y, width, height)
        _scene, w = self.project.find_widget(widget_id)
        self._following[widget_id] = (float(w.x), float(w.y), float(w.width), float(w.height))
        start = self._drag_start.pop(widget_id, None)
        if start is not None and start[2] and start[3] and w.height:
            before, after = start[2] / start[3], w.width / w.height
            if abs(after - before) / before > 0.01:
                # Every handle keeps the shape unless Shift frees it: a new shape is on purpose.
                w.stretched = True
        if widget_id == self.selected:
            self._refresh_inspector()
        self._changed()

    def _canvas_cropped(self, widget_id, left, top, right, bottom) -> None:
        _scene, w = self.project.find_widget(widget_id)
        self._checkpoint(f"Crop {w.name}", key=(widget_id, "crop"))
        w.effects.crop_left, w.effects.crop_top, w.effects.crop_right, w.effects.crop_bottom = left, top, right, bottom
        self.engine.update_widget(w)

    def _canvas_crop_committed(self, widget_id, left, top, right, bottom) -> None:
        self._canvas_cropped(widget_id, left, top, right, bottom)
        self.history.end_coalescing()
        self._changed()

    #: The bar's quiet hint while a handle is held: what each modifier does now.
    DRAG_HINTS = {"resize": "Alt from centre · Shift stretch · Ctrl crop",
                  "resize-centre": "From centre · Shift stretch · Ctrl crop",
                  "stretch": "Stretching · Alt from centre", "stretch-centre": "Stretching from centre",
                  "crop": "Cropping · Alt from centre", "crop-centre": "Cropping from centre"}

    def _drag_mode(self, mode: str) -> None:
        hint = self.DRAG_HINTS.get(mode)
        w = self._selected_widget()
        if hint and w is not None:
            size = w.source.get("size")
            facts = f"{size[0]}×{size[1]} · {self._ratio_words(size)}" if size else None
            self.center.show_bar([BarChip(w.name, meta=facts, detail=hint)])
        else:
            self._refresh_bar()

    @staticmethod
    def _ratio_words(size) -> str:
        from math import gcd

        w, h = int(size[0]), int(size[1])
        known = {(16, 9): "16:9", (9, 16): "9:16", (32, 9): "32:9", (21, 9): "21:9", (16, 10): "16:10",
                 (4, 3): "4:3", (1, 1): "1:1", (3, 2): "3:2"}
        for (a, b), words in known.items():
            if abs(w / h - a / b) < 0.02:
                return words
        g = gcd(w, h) or 1
        return f"{w // g}:{h // g}"

    def _align(self, how: str) -> None:
        w = self._selected_widget()
        if w is None or not w.has_picture:
            return
        self._checkpoint({"fill": "Fill canvas", "fit": "Fit in canvas", "fill-width": "Fill width",
                          "fill-height": "Fill height", "original": "Original size"}.get(how, "Align"))
        self.project.align(w.id, how)
        self._set_rect(w, w.x, w.y, w.width, w.height, commit=True)
        self._refresh_inspector()

    def _effects_changed(self, w: Widget) -> None:
        self.engine.update_widget(w)
        self._changed()

    # ── Widgets ──

    def add_widget(self, kind: str, at: tuple[float, float] | None = None) -> None:
        scene = self.project.active_scene
        if scene is None:
            return
        self._checkpoint(f"Add {KINDS[kind][0].lower()}")
        w = self.project.add_widget(scene.id, kind)
        if at is not None and w.has_picture:
            # Centred on the point it was asked for (a right click on the canvas).
            w.x, w.y = at[0] - w.width / 2, at[1] - w.height / 2
        if self.fake and kind in getattr(self, "_fake_images", {}):
            w.source["fake-image"] = self._fake_images[kind]
        if kind == "image" and not self.fake:
            def chosen(ok: bool) -> None:
                if not ok:
                    self.project.remove_widget(w.id)
                    self._changed(structure=True)
            self._changed(structure=True)
            self._choose_image(w, chosen)
            return
        self.selected = w.id
        self._changed(structure=True)
        self._show_scene()
        if kind in ("display", "window", "camera", "app-audio") and not self.fake:
            self._choose_source(w, restore=False)
        Toast.show(self.host, f"{w.name} added", kind="added")

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if len(ids) > 1:
            self._checkpoint(f"Delete {len(ids)} widgets")
            for widget_id in ids:
                self.project.remove_widget(widget_id)
                self.widget_errors.pop(widget_id, None)
                self.widget_waiting.discard(widget_id)
                if self.portals is not None:
                    self.portals.close(widget_id)
            self.selected, self.selection = None, []
            self._show_scene()
            self._changed(structure=True)
            Toast.show(self.host, f"Deleted {len(ids)} widgets", kind="deleted", undo=self.undo)
            return
        w = self._selected_widget()
        if w is None:
            return
        self._checkpoint(f"Delete {w.name}")
        _scene, _index, removed = self.project.remove_widget(w.id)
        self.selected = None
        self.widget_errors.pop(removed.id, None)
        self.widget_waiting.discard(removed.id)
        self._show_scene()
        if self.portals is not None:
            self.portals.close(removed.id)  # its restore token stays in the undo step
        self._changed(structure=True)
        Toast.show(self.host, f"Deleted {removed.name}", kind="deleted", undo=self.undo)

    def duplicate_selected(self) -> None:
        ws = self._targets()
        scene = self.project.active_scene
        if not ws or scene is None:
            return
        import copy

        from .model import new_id

        self._checkpoint(self._count(ws, "Duplicate", "Duplicate"))
        twins = []
        for w in ws:
            twin = copy.deepcopy(w)
            twin.id = new_id(w.kind)
            twin.name = ""
            twin.name = self.project._next_widget_name(scene, w.kind)
            twin.source.pop("restore-token", None)  # a second share: its own picker
            if w.has_picture:
                twin.x, twin.y = w.x + 40, w.y + 40
            scene.widgets.insert(scene.widgets.index(w) + 1, twin)
            twins.append(twin.id)
        self.selected, self.selection = twins[-1], twins
        self._changed(structure=True)
        self._show_scene()
        self._ask_missing_sources()
        if len(twins) > 1:
            self.set_selection(twins)

    def toggle_hidden(self) -> None:
        """Hide them all (any shown), or show them all."""
        ws = [w for w in self._targets() if w.has_picture]
        if ws:
            self._set_visible_all(ws, not any(w.visible for w in ws))

    def _set_visible(self, w: Widget, visible: bool) -> None:
        self._set_visible_all([w], visible)

    def _set_visible_all(self, ws: list[Widget], visible: bool) -> None:
        self._checkpoint(self._count(ws, "Show" if visible else "Hide", "Show" if visible else "Hide"))
        for w in ws:
            w.visible = visible
        self._show_scene()
        self._changed(structure=True)

    def toggle_lock(self) -> None:
        """Lock them all (any unlocked), or unlock them all."""
        ws = self._targets()
        if not ws:
            return
        lock = not all(w.locked for w in ws)
        self._checkpoint(self._count(ws, "Lock" if lock else "Unlock", "Lock" if lock else "Unlock"))
        for w in ws:
            w.locked = lock
        self._changed(structure=True)

    def restack(self, direction: int) -> None:
        """To the front (or back), keeping their order among themselves."""
        ws = self._targets()
        scene = self.project.active_scene
        if not ws or scene is None:
            return
        self._checkpoint("Bring to front" if direction > 0 else "Send to back")
        for w in (ws if direction > 0 else reversed(ws)):
            self.project.move_widget(w.id, len(scene.widgets) if direction > 0 else 0)
        self.engine.restack(scene)
        self._changed(structure=True)

    def restack_all_by(self, step: int) -> None:
        """One place forward (or backward) each, past the next widget that is not selected."""
        ws = self._targets()
        scene = self.project.active_scene
        if not ws or scene is None:
            return
        self._checkpoint("Bring forward" if step > 0 else "Send backward")
        chosen = {w.id for w in ws}
        for w in (reversed(ws) if step > 0 else ws):
            index = scene.widgets.index(w)
            target = index + (1 if step > 0 else -1)
            if 0 <= target < len(scene.widgets) and scene.widgets[target].id not in chosen:
                self.project.move_widget(w.id, target)
        self.engine.restack(scene)
        self._changed(structure=True)

    def _widget_menu(self, widget_id: str, anchor: Gtk.Widget, x: float | None = None, y: float | None = None) -> None:
        """A widget's menu (right click or long press, on the canvas or its row), at the pointer: the same
        commands as the bar and the shortcuts; Order, Size, Align and Look cascade (drill in on a phone)."""
        from luma_appkit.action_bubble import FloatingMenu

        scene = self.project.active_scene
        w = scene.widget(widget_id) if scene else None
        if w is None:
            return
        if len(self._selected_ids()) > 1 and w.id in self._selected_ids():
            self._selection_menu(anchor, x, y)
            return
        self.select(w.id)
        rows: list = [MenuItem("Change source…", icon="monitor-up", on_activate=lambda: self._grow("source"))]
        if w.has_picture:
            order = [MenuItem("Bring to front", icon="bring-to-front", shortcut="Ctrl+]", on_activate=lambda: self.restack(1)),
                     MenuItem("Bring forward", icon="arrow-up", on_activate=lambda: self.restack_by(w, 1)),
                     MenuItem("Send backward", icon="arrow-down", on_activate=lambda: self.restack_by(w, -1)),
                     MenuItem("Send to back", icon="send-to-back", shortcut="Ctrl+[", on_activate=lambda: self.restack(-1))]
            size = [MenuItem("Fill canvas", icon="maximize-2", on_activate=lambda: self._align("fill")),
                    MenuItem("Fit in canvas", icon="minimize-2", on_activate=lambda: self._align("fit")),
                    MenuItem("Fill width", icon="move-horizontal", on_activate=lambda: self._align("fill-width")),
                    MenuItem("Fill height", icon="move-vertical", on_activate=lambda: self._align("fill-height")),
                    MenuItem("Original size", icon="scan", on_activate=lambda: self._align("original")),
                    MenuItem("Reset aspect", icon="ratio", on_activate=self.reset_aspect)]
            align = [MenuItem("Left", icon="align-start-vertical", on_activate=lambda: self._align("left")),
                     MenuItem("Centre", icon="align-center-vertical", on_activate=lambda: self._align("center")),
                     MenuItem("Right", icon="align-end-vertical", on_activate=lambda: self._align("right")),
                     None,
                     MenuItem("Top", icon="align-start-horizontal", on_activate=lambda: self._align("top")),
                     MenuItem("Middle", icon="align-center-horizontal", on_activate=lambda: self._align("middle")),
                     MenuItem("Bottom", icon="align-end-horizontal", on_activate=lambda: self._align("bottom"))]
            current = self._look_of(w)
            look = [MenuItem(label, icon=icon, selected=key == current, on_activate=lambda k=key: self.apply_look(w, k))
                    for key, (label, icon, _v) in self.LOOKS.items()]
            rows += [MenuItem("Order", icon="layers", submenu=order), MenuItem("Size", icon="scaling", submenu=size),
                     MenuItem("Align", icon="align-center-horizontal", submenu=align),
                     MenuItem("Look", icon="squircle", submenu=look)]
        if w.has_picture and self.project.format != self.project.primary:
            primary = self._primary_words()
            rows += [None,
                     MenuItem(f"Match {primary}", icon="link-2", on_activate=lambda: self.match_primary(w)),
                     MenuItem(f"Reset scene to match {primary}", icon="link-2", on_activate=self.reset_secondary)]
        rows.append(None)
        if w.has_picture:
            rows.append(MenuItem("Show" if not w.visible else "Hide", icon="eye" if not w.visible else "eye-off",
                                 shortcut="Ctrl+H", on_activate=self.toggle_hidden))
        else:
            rows.append(MenuItem("Unmute" if w.muted else "Mute", icon="volume-2" if w.muted else "volume-x",
                                 on_activate=lambda: self._toggle_mute(w)))
        rows += [MenuItem("Rename", icon="pencil", shortcut="F2", on_activate=lambda: self.rename_widget(w)),
                 MenuItem("Duplicate", icon="copy", on_activate=self.duplicate_selected),
                 MenuItem("Unlock" if w.locked else "Lock", icon="lock-open" if w.locked else "lock",
                          on_activate=self.toggle_lock),
                 None,
                 MenuItem("Delete", icon="trash-2", danger=True, shortcut="Delete", on_activate=self.delete_selected)]
        FloatingMenu(rows, label=w.name, title=w.name).popup(anchor, x=x, y=y)
        self._last_menu = rows  # tests activate each row

    def _selection_menu(self, anchor: Gtk.Widget, x: float | None = None, y: float | None = None) -> None:
        """Several widgets' menu: what applies to all of them at once (one undo step each)."""
        from luma_appkit.action_bubble import FloatingMenu

        ws = self._targets()
        pictures = [w for w in ws if w.has_picture]
        title = f"{len(ws)} widgets"
        rows: list = []
        if pictures:
            order = [MenuItem("Bring to front", icon="bring-to-front", shortcut="Ctrl+]", on_activate=lambda: self.restack(1)),
                     MenuItem("Bring forward", icon="arrow-up", on_activate=lambda: self.restack_all_by(1)),
                     MenuItem("Send backward", icon="arrow-down", on_activate=lambda: self.restack_all_by(-1)),
                     MenuItem("Send to back", icon="send-to-back", shortcut="Ctrl+[", on_activate=lambda: self.restack(-1))]
            align = [MenuItem(label, icon=icon, on_activate=lambda h=how: self._align_selection(h))
                     for how, label, icon in self.GROUP_ALIGN]
            current = self._shared_look(pictures)
            look = [MenuItem(label, icon=icon, selected=key == current, on_activate=lambda k=key: self.apply_look_all(k))
                    for key, (label, icon, _v) in self.LOOKS.items()]
            rows += [MenuItem("Order", icon="layers", submenu=order),
                     MenuItem("Align", icon="align-center-horizontal", submenu=align),
                     MenuItem("Look", icon="squircle", submenu=look), None]
            shown = any(w.visible for w in pictures)
            rows.append(MenuItem("Hide" if shown else "Show", icon="eye-off" if shown else "eye", shortcut="Ctrl+H",
                                 on_activate=self.toggle_hidden))
        locked = all(w.locked for w in ws)
        rows += [MenuItem("Duplicate", icon="copy", on_activate=self.duplicate_selected),
                 MenuItem("Unlock" if locked else "Lock", icon="lock-open" if locked else "lock", on_activate=self.toggle_lock),
                 None,
                 MenuItem(f"Delete {len(ws)} widgets", icon="trash-2", danger=True, shortcut="Delete",
                          on_activate=self.delete_selected)]
        FloatingMenu(rows, label=title, title=title).popup(anchor, x=x, y=y)
        self._last_menu = rows  # tests activate each row

    #: Several widgets aligned within their box (LumaUI align_rects).
    GROUP_ALIGN = (("left", "Align left", "align-start-vertical"), ("centre", "Align centres", "align-center-vertical"),
                   ("right", "Align right", "align-end-vertical"), ("top", "Align tops", "align-start-horizontal"),
                   ("middle", "Align middles", "align-center-horizontal"), ("bottom", "Align bottoms", "align-end-horizontal"))

    def _align_selection(self, how: str, distribute: str | None = None) -> None:
        from luma_appkit import align_rects, distribute_rects

        rects = {w.id: (w.x, w.y, w.width, w.height) for w in self._targets() if w.has_picture}
        if len(rects) < 2:
            return
        moved = distribute_rects(rects, distribute) if distribute else align_rects(rects, how, canvas=self.project.canvas)
        self._group_committed(moved)
        self._refresh_inspector()

    def _canvas_menu(self, x: float, y: float) -> bool:
        """Right click (or long press) on the canvas: the menu of the widget under it, topmost first."""
        scene = self.project.active_scene
        if scene is None:
            return False
        x0, y0 = self.arrange.canvas_to_view(0, 0)
        x1, y1 = self.arrange.canvas_to_view(*self.project.canvas)
        if x1 == x0 or y1 == y0:
            return False
        cx = (x - x0) / (x1 - x0) * self.project.canvas[0]
        cy = (y - y0) / (y1 - y0) * self.project.canvas[1]
        for w in reversed(scene.widgets):
            if w.has_picture and w.x <= cx <= w.x + w.width and w.y <= cy <= w.y + w.height:
                self._widget_menu(w.id, self.arrange, x, y)
                return True
        if 0 <= cx <= self.project.canvas[0] and 0 <= cy <= self.project.canvas[1]:
            self._add_widget_menu(self.arrange, at=(cx, cy), point=(x, y))  # blank canvas: add one there
            return True
        return False

    def _add_widget_menu(self, anchor: Gtk.Widget, at: tuple[float, float] | None = None,
                         point: tuple[float, float] | None = None) -> None:
        """Blank space's menu (the canvas, the Widgets panel): the + menu's kinds, from the same commands."""
        from luma_appkit.action_bubble import FloatingMenu

        rows: list = ["Add widget"]
        for kind in self._addable():
            command = self.commands.get(f"widget.add-{kind}")
            rows.append(MenuItem(command.label.replace("Add ", "").capitalize(), icon=command.icon,
                                 on_activate=lambda k=kind: self.add_widget(k, at=at)))
        x, y = point or (None, None)
        FloatingMenu(rows, label="Add widget", title="Add widget").popup(anchor, x=x, y=y)

    def rename_widget(self, w: Widget) -> None:
        """Rename in the widget's row: the name selected; Enter or a click away keeps it, Esc cancels."""
        row = self._widget_rows.get(w.id)

        def commit(name: str) -> None:
            self._checkpoint(f"Rename {w.name}")
            w.name = name
            w.source["named"] = True  # an app-audio widget keeps a name the person gave it
            self._changed(structure=True)

        if row is not None:
            self._inline_rename(row, w.name, commit)

    def _inline_rename(self, row, text: str, commit) -> None:
        """Swap a row's title for an InlineRenameField until it is done."""
        label = getattr(row, "title_label", None)
        box = label.get_parent() if label is not None else None
        if box is None or getattr(row, "renaming", False):
            return
        row.renaming = True
        finished = []

        def close(*_a) -> None:
            finished.append(True)  # removing the field moves focus, which would commit it again
            row.renaming = False

            def remove() -> bool:  # after the focus change that may have finished it is done
                if field.get_parent() is box:
                    box.remove(field)
                label.set_visible(True)
                return False

            GLib.idle_add(remove, priority=GLib.PRIORITY_DEFAULT)

        def done(value: str) -> bool:
            if finished:
                return True
            value = value.strip()
            close()
            if value and value != text:
                # The rows are rebuilt after this (default priority: a busy preview never starves it).
                GLib.idle_add(lambda: commit(value) and False, priority=GLib.PRIORITY_DEFAULT)
            return True

        field = InlineRenameField(text, commit=done, cancel=close)
        field.set_hexpand(True)
        field.update_property([Gtk.AccessibleProperty.LABEL], [f"Rename {text}"])
        box.insert_child_after(field, label)
        label.set_visible(False)
        # After the click, key or closing menu that asked for it has settled where focus goes.
        GLib.timeout_add(60, lambda: (not finished and field.begin()) and False)

    def rename_focused(self) -> None:
        """F2: the selected widget when the widget list or the canvas has focus, otherwise the scene."""
        focus = self.get_focus()
        if self.selected and focus is not None and (focus.is_ancestor(self.widget_list) or focus is self.widget_list
                                                    or focus.is_ancestor(self.arrange) or focus is self.arrange):
            w = self.project.active_scene.widget(self.selected) if self.project.active_scene else None
            if w is not None:
                self.rename_widget(w)
                return
        self.rename_scene()

    def restack_by(self, w: Widget, step: int) -> None:
        scene = self.project.active_scene
        if scene is None:
            return
        if len(self._selected_ids()) > 1 and w.id in self._selected_ids():
            self.restack_all_by(step)
            return
        index = scene.widgets.index(w)
        self._checkpoint("Bring forward" if step > 0 else "Send backward")
        self.project.move_widget(w.id, max(0, index + step))
        self.engine.restack(scene)
        self._changed(structure=True)

    # ── Scenes ──

    def _scene_menu(self, scene_id: str, anchor: Gtk.Widget, x: float | None = None, y: float | None = None) -> None:
        """A scene's menu (right click or long press), at the pointer: the scene commands, with their keys.
        It acts on that scene without switching to it, so the row it opened from stays."""
        from luma_appkit.action_bubble import FloatingMenu

        scene = self.project.scene(scene_id)
        if scene is None:
            return
        index = self.project.scenes.index(scene)
        startup = self.project.startup == scene_id
        rows = [MenuItem("Rename", icon="pencil", shortcut="F2", on_activate=lambda: self.rename_scene(scene_id)),
                MenuItem("Duplicate", icon="copy", shortcut="Ctrl+D", on_activate=lambda: self.duplicate_scene(scene_id)),
                MenuItem("Opens at startup" if startup else "Set as startup scene", icon="star", selected=startup,
                         on_activate=lambda: self.set_startup(scene_id))]
        if index > 0:
            rows.append(MenuItem("Move up", icon="arrow-up", on_activate=lambda: self.move_scene(scene_id, -1)))
        if index < len(self.project.scenes) - 1:
            rows.append(MenuItem("Move down", icon="arrow-down", on_activate=lambda: self.move_scene(scene_id, 1)))
        if len(self.project.scenes) > 1:
            rows += [None, MenuItem("Delete", icon="trash-2", danger=True,
                                    on_activate=lambda: self.delete_scene(scene_id))]
        if anchor.get_root() is None:
            anchor, x, y = self.sidebar, None, None
        FloatingMenu(rows, label=scene.name, title=scene.name).popup(anchor, x=x, y=y)
        self._last_menu = rows  # tests activate each row

    def set_startup(self, scene_id: str) -> None:
        self._checkpoint("Set startup scene")
        self.project.startup = None if self.project.startup == scene_id else scene_id
        self._changed(structure=True)

    def move_scene(self, scene_id: str, step: int) -> None:
        index = [s.id for s in self.project.scenes].index(scene_id)
        self._checkpoint("Move scene")
        self.project.move_scene(scene_id, index + step)
        self._changed(structure=True)

    def _scene_activated(self, _list, row) -> None:
        scene_id = getattr(row, "scene_id", None)
        if scene_id and scene_id != self.project.active:
            self.switch_scene(scene_id)

    def switch_scene(self, scene_id: str) -> None:
        self.project.activate(scene_id)
        self.select(None)  # a selection belongs to its scene
        self._show_scene()
        self._ask_missing_sources()
        self._changed(structure=True)

    def _search_scenes(self, text: str) -> None:
        self._scene_filter = text.strip().casefold()
        self._refresh_scenes()

    def new_scene(self) -> None:
        """A new "Scene N", shown, with its name ready to type over."""
        self._checkpoint("New scene")
        scene = self.project.add_scene()
        self.switch_scene(scene.id)
        self.rename_scene()

    def duplicate_scene(self, scene_id: str | None = None) -> None:
        scene = self.project.scene(scene_id) if scene_id else self.project.active_scene
        if scene is not None:
            self._checkpoint(f"Duplicate {scene.name}")
            copy = self.project.duplicate_scene(scene.id)
            self.switch_scene(copy.id)

    def rename_scene(self, scene_id: str | None = None) -> None:
        """Rename in the scene's row: the name selected; Enter or a click away keeps it, Esc cancels."""
        scene = self.project.scene(scene_id) if scene_id else self.project.active_scene
        row = self._scene_rows.get(scene.id) if scene is not None else None
        if row is None:
            return

        def commit(name: str) -> None:
            try:
                self._checkpoint(f"Rename {scene.name}")
                self.project.rename_scene(scene.id, name)
            except ModelError:
                return
            self._changed(structure=True)

        self._inline_rename(row, scene.name, commit)

    def delete_scene(self, scene_id: str | None = None) -> None:
        scene = self.project.scene(scene_id) if scene_id else self.project.active_scene
        if scene is None or len(self.project.scenes) < 2:
            return
        self._checkpoint(f"Delete {scene.name}")
        index, removed = self.project.remove_scene(scene.id)
        if self.portals is not None:
            for w in removed.widgets:
                self.portals.close(w.id)
        self.switch_scene(self.project.active)
        Toast.show(self.host, f"Deleted {removed.name}", kind="deleted", undo=self.undo)

    # ── Canvas format and layouts ──

    def _canvas_more(self, fmt: str) -> list:
        """A canvas panel's ⋯: turn it off, and the other canvas on or off ("· Off" while it is off)."""
        from luma_appkit import MenuItem

        rows = []
        for key in sorted(FORMATS, key=lambda k: k != fmt):
            name = canvas_name(key)
            if key in self.canvas_on:
                rows.append(MenuItem(f"Turn off {name}", icon="eye-off",
                                     on_activate=lambda k=key: self._canvas_chip(k, False)))
            else:
                rows.append(MenuItem(f"{name} · Off", icon=CANVAS_GLYPHS[key], note="Turn on",
                                     on_activate=lambda k=key: self._canvas_chip(k, True)))
        return rows

    def _sync_canvas_state(self) -> None:
        """Canvas state as words: "Live" after a canvas panel's title while an output takes it."""
        for fmt, tile in getattr(self, "preview_tiles", {}).items():
            tile.set_status("Live" if self._canvas_used(fmt) else None, tone="live")

    def set_format(self, key: str) -> None:
        """Edit canvas ``key`` (turning it on, its panel open, when it was off)."""
        if key not in self.canvas_on or not self._is_open(key):
            self._canvas_chip(key, True)
        self._edit_canvas(key)

    def use_layout(self, key: str) -> None:
        layout = self.layouts.get(key)
        self.dock.set_layout(layout)
        self.layouts.remember(self.dock.layout())

    def save_layout(self) -> None:
        entry = Gtk.Entry(placeholder_text="Layout name", hexpand=True)

        def done() -> None:
            try:
                self.layouts.save_as(entry.get_text(), self.dock.layout())
            except ValueError as error:
                Toast.show(self.host, str(error), kind="warning")
                return
            self.center.fold()
            Toast.show(self.host, "Layout saved", kind="saved")

        entry.connect("activate", lambda *_a: done())
        self.center.set_editor(ActionEditor("Save layout", "layout-dashboard", fields=[("Name", entry)],
                                            body=Gtk.Box(visible=False), primary=BarAction("check", "Save",
                                                                              on_activate=done)))
        self.center.grow()

    def show_recordings(self) -> None:
        folder = recordings_dir()
        folder.mkdir(parents=True, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(folder))).launch(self, None, None)

    # ── Recording ──

    def toggle_record(self) -> None:
        """Record, or stop recording: the Recording destination row (its canvas, container and folder)."""
        if self.recording_since is not None:
            self.stop_recording()
            return
        problem = self.dest_panel.record(True)
        if problem:
            Toast.show(self.host, problem, kind="error")
        self._refresh_bar()

    def stop_recording(self) -> None:
        self.dest_panel.record(False)
        self._refresh_bar()

    def _show_file(self, path: str) -> None:
        file = Gio.File.new_for_path(path)
        launcher = Gtk.FileLauncher.new(file)
        launcher.open_containing_folder(self, None, None)

    # ── Going live ──

    def _secret_store(self):
        from luma_appkit.platform import secret_store

        return secret_store(APP_ID)

    def _destination(self) -> tuple[str, str]:
        """(server, key) for the chosen destination, from settings and the keyring."""
        import json

        try:
            settings = json.loads(self.settings_path.read_text())
        except (FileNotFoundError, ValueError):
            settings = {}
        self.destination = settings.get("destination", self.destination)
        server = settings.get("server") or graph.DESTINATIONS[self.destination][1]
        try:
            key = self._secret_store().lookup(self.destination, SECRET_KIND) or ""
        except (GLib.Error, OSError):
            key = ""  # no keyring reachable: ask for the key
        return server, key

    def go_live(self) -> None:
        """Every stream row that goes live with Go live starts; with none, the Destinations panel opens
        on its + menu."""
        if self.stream_state != "off":
            return
        if self._streams_ready():
            self.dest_panel.go_live(record=None if self.dest_panel.record_with_live() else False)
            return
        self.add_destination()

    def _start_stream(self, server: str, key: str) -> None:
        try:
            url = graph.stream_url(server, key)
            _label, _ingest, codec, video, audio = graph.DESTINATIONS[self.destination]
            self.engine.start_stream(url, codec=codec, video_kbps=video, audio_kbps=audio)
        except (EngineError, ValueError) as error:
            Toast.show(self.host, str(error), kind="error")
            return
        self.stream_state = "starting"
        self.live_since = time.monotonic()
        self._refresh_bar()

    def edit_destination(self, then_go: bool = False) -> None:
        import json

        server, key = self._destination()
        server_entry = Gtk.Entry(text=server, hexpand=True, placeholder_text="rtmps://…")
        key_entry = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True)
        key_entry.set_text(key)
        key_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Stream key"])
        modes = [(k, label, "radio-tower") for k, (label, *_rest) in graph.DESTINATIONS.items()]

        def save() -> None:
            dest = self.destination
            chosen_server = server_entry.get_text().strip() if dest == "custom" else graph.DESTINATIONS[dest][1]
            try:
                graph.stream_url(chosen_server, "x")
            except ValueError as error:
                Toast.show(self.host, str(error), kind="warning")
                return
            try:
                self._secret_store().store(dest, SECRET_KIND, key_entry.get_text().strip(),
                                           label=f"Streamy stream key ({graph.DESTINATIONS[dest][0]})")
            except (GLib.Error, OSError):
                Toast.show(self.host, "The stream key couldn't be saved in the keyring", kind="warning")
            from .model import atomic_write

            atomic_write(self.settings_path, json.dumps({"destination": dest, "server": chosen_server
                                                         if dest == "custom" else ""}))
            self.center.fold()
            if then_go:
                self._start_stream(chosen_server, key_entry.get_text().strip())
            else:
                Toast.show(self.host, "Destination saved", kind="saved")

        def mode(key_: str) -> None:
            self.destination = key_
            server_entry.set_sensitive(key_ == "custom")
            if key_ != "custom":
                server_entry.set_text(graph.DESTINATIONS[key_][1])

        server_entry.set_sensitive(self.destination == "custom")
        body = Gtk.Box(visible=False)  # fields only: no text to write
        editor = ActionEditor(graph.DESTINATIONS[self.destination][0], "radio-tower", modes=modes,
                              mode=self.destination, on_mode=mode,
                              summary="Stream keys are kept in your keyring",
                              fields=[("Server", server_entry), ("Stream key", key_entry)], body=body,
                              primary=BarAction("radio-tower" if then_go else "check",
                                                "Go live" if then_go else "Save", on_activate=save))
        self.center.set_editor(editor)
        self.center.grow()
        key_entry.grab_focus()

    def ask_end_stream(self) -> None:
        DestructiveDialog.in_bar(self.center, title="End the stream?",
                                 body="Viewers see the stream end. A recording keeps going.",
                                 action="End stream", on_confirm=self._end_stream)

    def _end_stream(self) -> None:
        self.dest_panel.end()
        self.engine.stop_stream()

    def _output_state(self, _engine, kind: str, state: str) -> None:
        if kind == "record":
            if state == "on":
                self.recording_since = time.monotonic()
            elif state == "off":
                if self.recording_since is not None:
                    path = getattr(self, "_record_target", None)
                    if path and os.path.exists(path):
                        Toast.show(self.host, "Saved", kind="saved", action=("Show file", lambda p=path: self._show_file(p)))
                    else:
                        Toast.show(self.host, "Recording saved", kind="saved")
                self.recording_since = None
        elif kind == "stream":
            if state == "off" and self.stream_state != "off":
                Toast.show(self.host, "Stream ended", kind="done")
                self.live_since = None
            self.stream_state = state
        self._refresh_bar()
