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
    SidebarToggle, TextButton, Toast, ToastHost, TypeLabel, ValueSlider, VolumeSlider, add_style_sheet,
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



#: The Inspector's column: one width whatever it shows, so nothing beside it ever reflows.
INSPECTOR_WIDTH = 384  # the widest editor (Look's five presets), with the dock gap before it


class FixedColumn(Gtk.Widget):
    """Holds a pane at one width: its content's natural width never reaches the layout. Empty (the pane
    is a phone's drawer then) it takes no room."""

    __gtype_name__ = "StreamyFixedColumn"

    def __init__(self, child: Gtk.Widget, width: int) -> None:
        super().__init__(hexpand=False, vexpand=True)
        self._width = width
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        child = self.get_first_child()
        if child is None or not child.get_visible():
            return 0, 0, -1, -1
        if orientation == Gtk.Orientation.HORIZONTAL:
            from luma_appkit import lumaui_tokens

            root = self.get_root()
            window = (root.get_width() or root.get_default_size()[0]) if isinstance(root, Gtk.Window) else 0
            if 0 < window <= lumaui_tokens.PHONE_MAX_WIDTH:
                return 0, 0, -1, -1  # a phone: the pane is a bottom drawer
            minimum, natural, _b, _n = child.measure(orientation, -1)
            if natural == 0:
                return 0, 0, -1, -1  # its sheet is elsewhere (a drawer)
            # Never narrower than its content needs; once wider, it stays that wide (one reflow at most).
            self._width = max(self._width, minimum)
            return self._width, self._width, -1, -1
        minimum, natural, _b, _n = child.measure(orientation, max(for_size, self._width))
        return minimum, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        child = self.get_first_child()
        if child is not None:
            child.allocate(width, height, baseline, None)

    def do_dispose(self):
        child = self.get_first_child()
        if child is not None:
            child.unparent()

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
        self._meters: dict[str, object] = {}
        self._building = False
        self.widget_errors: dict[str, str] = {}
        #: Widgets whose stream is open but has sent no picture yet (not a failure).
        self.widget_waiting: set[str] = set()
        self._drag_start: dict[str, tuple] = {}
        self.history = History()
        self._level_words: dict[str, object] = {}
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
        self.engine.effects_of = lambda widget: self.project.effects_on(widget)
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
        self.engine.connect("preview-changed", lambda engine: self.picture.set_paintable(engine.paintable))

        self._build_sidebar()
        self._build_canvas()
        self._build_insets()
        self.set_body(self._body())
        self.refresh_all()
        self.connect("close-request", self._closing)
        self.connect("notify::is-active", lambda *_a: self._update_preview_running())
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
                Command("app.destinations", "Destinations…", self.edit_destination, icon="radio-tower"),
                Command("app.recordings", "Show recordings", self.show_recordings, icon="folder-open"),
            )),
            CommandGroup("Canvas", tuple(
                Command(f"view.{key}", label, lambda k=key: self.set_format(k),
                        icon="rectangle-horizontal" if key == "landscape" else "smartphone",
                        checked=lambda k=key: self.project.format == k)
                for key, (label, _w, _h) in FORMATS.items())),
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
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True)
        self.picture.add_css_class("streamy-program")
        self.arrange = ArrangeView(self.picture, canvas=self.project.canvas, on_select=self._canvas_selected,
                                   on_change=self._canvas_changed, on_commit=self._canvas_committed,
                                   on_activate=lambda wid: self._grow("look"),
                                   on_crop=self._canvas_cropped, on_crop_commit=self._canvas_crop_committed,
                                   on_drag_mode=self._drag_mode)
        # The video always has room around it, whatever the stage's size.
        self.arrange.set_fit_margins(STAGE_MARGIN, STAGE_MARGIN, STAGE_MARGIN, STAGE_MARGIN)
        self.arrange.update_property([Gtk.AccessibleProperty.LABEL], ["Scene"])
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", lambda g, _n, x, y: self._canvas_menu(x, y) and
                     g.set_state(Gtk.EventSequenceState.CLAIMED))
        self.arrange.add_controller(menu)
        hold = Gtk.GestureLongPress(touch_only=True)
        hold.connect("pressed", lambda g, x, y: self._canvas_menu(x, y) and g.set_state(Gtk.EventSequenceState.CLAIMED))
        self.arrange.add_controller(hold)

    def _build_insets(self) -> None:
        """The island: the preview as the bare stage; Widgets and Audio side by side under it, as tall
        as the taller one's content (capped; past that each scrolls in its inset)."""
        self.widget_nav = NavigationList("files")
        self.widget_list = self.widget_nav.list
        self.widget_list.connect("row-selected", self._widget_row_selected)
        add = IconOnlyButton("plus", "Add widget")
        add.connect("clicked", lambda *_a: self.center.grow("add", panel_list(self._add_menu())))
        self.audio_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=4,
                                     min_children_per_line=1, valign=Gtk.Align.START, column_spacing=24)
        self.audio_box.add_css_class("streamy-mixer")
        audio_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                          )
        audio_scroll.set_child(self.audio_box)
        self.audio_empty = ListEmptyState("No microphone or desktop audio")
        audio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        audio.append(self.audio_empty)
        audio.append(audio_scroll)
        # The preview is an inset like the others (same radius, gaps and surface), the canvas fitted
        # inside it with room on every side.
        # The canvases, in the Preview's header: on = composited and shown. Until both can be shown side
        # by side, turning one on shows it instead of the other; the last one on stays on.
        from luma_appkit import ToggleChip

        self.canvas_chips = {key: ToggleChip(FORMATS[key][0].split()[0], icon="rectangle-horizontal"
                                             if key == "landscape" else "smartphone",
                                             active=self.project.format == key,
                                             on_toggle=lambda on, k=key: self._canvas_chip(k, on))
                             for key in FORMATS}
        self.preview_tile = DockTile("preview", "Preview", "monitor", self.arrange, main=True,
                                     actions=list(self.canvas_chips.values()))
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
        hold = Gtk.GestureLongPress(touch_only=True)
        hold.connect("pressed", lambda _g, x, y: self.widget_list.get_row_at_y(int(y)) is None and
                     self._add_widget_menu(widgets, point=(x, y)))
        widgets.add_controller(hold)
        self.widgets_tile = DockTile("widgets", "Widgets", "layers", widgets, phone="drawer", actions=[add])
        self.audio_tile = DockTile("audio", "Audio", "volume-2", audio, phone="drawer")
        self.dock = Dock([self.preview_tile, self.widgets_tile, self.audio_tile], layout=self.layouts.current,
                         under_bar=True, on_layout=self.layouts.remember)
        self.dock.on_rearrange = self._panels_moved
        self.preview_tile.connect("notify::width", lambda *_a: GLib.idle_add(self._size_preview))

    def _size_preview(self) -> bool:
        x0, _y0 = self.arrange.canvas_to_view(0, 0)
        x1, _y1 = self.arrange.canvas_to_view(*self.project.canvas)
        if x1 - x0 > 8:
            # Device pixels at the surface's own (fractional) scale: the picture is drawn 1:1.
            surface = self.get_surface()
            scale = surface.get_scale() if surface is not None and hasattr(surface, "get_scale") else None
            self.engine.set_preview_size(round((x1 - x0) * (scale or self.get_scale_factor())), 0)
        return False

    def _body(self) -> Gtk.Widget:
        from luma_appkit import DetailsPane, Island

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
        # The Inspector: a fixed column beside the island, always there (a bottom drawer on a phone);
        # the selection's Source, Look and Arrange, or the scene's own settings when nothing is selected.
        self.inspector = DetailsPane("Inspector", main=True, raised=True)
        self._inspector_sections: dict[str, Gtk.Widget] = {}
        body.append(FixedColumn(self.inspector, INSPECTOR_WIDTH))
        self.inspector.show(open=True, subject="scene")
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
        try:
            self.engine.start(self.project)
        except EngineError as error:
            Toast.show(self.host, str(error), kind="error")
            return False
        self._ask_missing_sources()
        self._tick = GLib.timeout_add(1000, self._every_second)
        return False

    def _update_preview_running(self) -> None:
        """The program runs while the window is shown or something is sent; otherwise nothing captures."""
        visible = self.get_mapped() and not (self.get_surface() and
                                              self.get_surface().get_state() & Gdk.ToplevelState.MINIMIZED)
        if not visible and not self.engine.outputs and self.engine.running:
            self.engine.stop()
        elif visible and not self.engine.running:
            self._start_engine()

    def _fill_fake_sources(self) -> None:
        for scene in self.project.scenes:
            for w in scene.widgets:
                if w.kind in self._fake_images:
                    w.source["fake-image"] = self._fake_images[w.kind]

    def _source_for(self, widget: Widget):
        if self.portals is None:
            return None, None
        return self.portals.source_for(widget)

    def _ask_missing_sources(self) -> None:
        scene = self.project.active_scene
        if scene is None or self.fake or self.portals is None:
            return
        for w in scene.widgets:
            if w.kind in ("display", "window", "camera", "app-audio") and w.visible and \
                    w.id not in self.portals.captures and w.id not in self.widget_errors:
                self._choose_source(w, restore=True)

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
        if not w.stretched and w.height:
            # Fitted inside the box it had, centred: it never grows past where it was placed.
            cx, cy = w.x + w.width / 2, w.y + w.height / 2
            aspect = width / height
            bw, bh = (w.width, w.width / aspect) if w.width / w.height < aspect else (w.height * aspect, w.height)
            w.x, w.y, w.width, w.height = cx - bw / 2, cy - bh / 2, bw, bh
            self.engine.update_widget(w)
            self.arrange.set_item_rect(w.id, w.x, w.y, w.width, w.height)
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
            "missing": f"{name} stopped responding" if w.kind == "camera" else f"{name} is gone",
            "format": f"{name} sends a picture Streamy can't read",
            "stopped": f"{name} stopped with an error",
        }.get(reason, f"{name} stopped")

    def _widget_failed(self, _engine, widget_id: str, reason: str) -> None:
        """A source failed: say so on the widget, offer the fix, keep everything else running."""
        try:
            _scene, w = self.project.find_widget(widget_id)
        except ModelError:
            return
        from .engine import log

        log(f"{w.kind} widget {w.name!r}: {self.cause_line(w, reason)} ({reason})")
        self.widget_waiting.discard(widget_id)
        self.widget_errors[widget_id] = reason
        if self.portals is not None:
            self.portals.close(widget_id)
        self._show_scene()  # its card says so in its place
        if reason == "timeout":
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
            self.arrange.set_canvas_size(*self.project.canvas)
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
        self.arrange.clear()
        self.arrange.set_canvas_size(*self.project.canvas)
        if scene is None:
            return
        for w in scene.widgets:
            if not w.has_picture:
                continue
            add_arrange_item(self.arrange, ArrangeItem(w.id, w.name, w.x, w.y, w.width, w.height, locked=w.locked,
                                                       visible=w.visible, keeps_aspect=True,
                                                       croppable=True,
                                                       crop=(w.effects.crop_left, w.effects.crop_top,
                                                             w.effects.crop_right, w.effects.crop_bottom)))
        if self.selected and scene.widget(self.selected) and scene.widget(self.selected).has_picture:
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
        from luma_appkit.structure_adapt import is_phone

        if is_phone(self):
            # A phone: the Inspector is a drawer that opens when asked (a key in the bar), never over
            # the preview on its own.
            if not getattr(self, "_inspector_asked", False):
                pane.show(open=False, main=False)
        elif not pane.main:
            pane.show(open=True, main=True)
        w = self._selected_widget()
        self._inspector_for = w.id if w is not None else None
        if w is None:
            scene = self.project.active_scene
            pane.set_title(scene.name if scene else "Scene")
            if scene is not None:
                self._inspector_section("scene", "Scene", self._scene_panel(scene))
            return
        pane.set_title(w.name)
        self._inspector_section("source", "Source", self._source_panel(w))
        if w.has_picture:
            self._inspector_section("look", "Look", self._look_panel(w))
            self._inspector_section("arrange", "Arrange", self._arrange_panel(w))
        if scroll is not None and keep == self._inspector_for:
            GLib.idle_add(lambda: scroll.get_vadjustment().set_value(offset) and False)

    def _inspector_section(self, key: str, title: str, content: Gtk.Widget) -> None:
        heading = self.inspector.add_section(title)
        self.inspector.add(content)
        self._inspector_sections[key] = heading

    def _inspector_scroll(self) -> Gtk.ScrolledWindow | None:
        widget = self.inspector
        while widget is not None:
            child = widget.get_first_child()
            found = None
            stack = [self.inspector]
            while stack:
                node = stack.pop()
                if isinstance(node, Gtk.ScrolledWindow):
                    found = node
                    break
                c = node.get_first_child()
                while c is not None:
                    stack.append(c)
                    c = c.get_next_sibling()
            return found
        return None

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
            return f"{name} · {mode[1]}p {mode[2]}" if mode else name
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
        if cameras is None or not cameras.ready:
            return [ListEmptyState("Looking for cameras…")]
        if not cameras.cameras:
            return [ListEmptyState("No camera found")]
        current = self._camera_of(w)
        rows: list = [PanelHeading("Camera")]
        for camera in cameras.cameras:
            rows.append(PanelRow(camera.label, icon="camera", selected=current is not None and camera.name == current.name,
                                 closes=False, on_activate=lambda c=camera: self._choose_camera(w, c.name)))
        if current is not None and current.modes:
            mode = self._mode_of(w, current)
            choices = []
            for width, height, fps in current.modes[:6]:
                key = f"{width}x{height}@{fps}"
                choices.append((key, f"{height}p {fps}"))
            rows.append(PropertyChoice("Picture", choices,
                                       current=f"{mode[0]}x{mode[1]}@{mode[2]}" if mode else choices[0][0],
                                       on_change=lambda key: self._choose_mode(w, key)))
        rows.append(PanelSwitch("flip-horizontal-2", "Mirror", active=bool(w.source.get("mirror")),
                                on_toggle=lambda on: self._set_source(w, "mirror", on, "Mirror")))
        return rows

    def _device_rows(self, w: Widget, kind: str) -> list:
        current = w.source.get("device")
        heading = "Microphone" if kind == "source" else "Output heard"
        rows: list = [PanelHeading(heading),
                      PanelRow(self.devices.words(kind, None), icon="mic" if kind == "source" else "speaker",
                               selected=not current, closes=False,
                               on_activate=lambda: self._choose_device(w, None))]
        for device in self.devices.of(kind):
            rows.append(PanelRow(device.label, icon="mic" if kind == "source" else "speaker",
                                 selected=current == device.name, closes=False,
                                 on_activate=lambda d=device: self._choose_device(w, d.name)))
        if not self.devices.of(kind):
            rows.append(ListEmptyState("No microphone found" if kind == "source" else "No output found"))
        return rows

    def _monitor_rows(self, w: Widget) -> list:
        current = w.source.get("monitor-device")
        rows = [PropertyChoice("Monitor", [("off", "Off"), ("only", "Monitor"), ("both", "Monitor + output")],
                               current=w.monitor, segments=True, on_change=lambda mode: self._set_monitor(w, mode)),
                PanelRow(self.devices.words("sink", None), icon="headphones", selected=not current, closes=False,
                         on_activate=lambda: self._set_source(w, "monitor-device", None, "Monitor output"))]
        for device in self.devices.of("sink"):
            rows.append(PanelRow(device.label, icon="headphones", selected=current == device.name, closes=False,
                                 on_activate=lambda d=device: self._set_source(w, "monitor-device", d.name,
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

    @staticmethod
    def shadow_strength(fx) -> float:
        return round(min(100.0, max(fx.shadow_blur / 0.6, fx.shadow_opacity / 0.45 * 100 if fx.shadow_blur else 0)))

    def _set_shadow(self, w: Widget, strength: float) -> None:
        """One slider for the shadow: blur, drop and darkness grow together."""
        self.project.set_effect(w, "shadow_blur", strength * 0.6)
        self.project.set_effect(w, "shadow_y", round(strength * 0.2))
        self.project.set_effect(w, "shadow_opacity", 0.0 if strength <= 0 else 0.2 + 0.25 * strength / 100)
        self._effects_changed(w)

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
        self.arrange.set_item_rect(w.id, w.x, w.y, w.width, w.height)
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
        self._inspector_asked = True
        self.inspector.open()
        GLib.timeout_add(500, lambda: setattr(self, "_inspector_asked", False) or False)
        scroll = self._inspector_scroll()

        def reveal() -> bool:
            ok, bounds = heading.compute_bounds(scroll.get_child()) if scroll is not None else (False, None)
            if ok:
                scroll.get_vadjustment().set_value(bounds.get_y())
            return False

        GLib.idle_add(reveal)

    # ── Audio inset ──

    #: A strip's narrowest: under it the Audio inset scrolls, so it never pushes the Widgets/Audio seam.
    STRIP_WIDTH = 220

    def _refresh_audio(self) -> None:
        """A strip per sound, each a comfortable fixed width, in a row that wraps: the mute and monitor keys,
        the name over the device it hears, its live level, and the gain with the meter along it."""
        box = self.audio_box
        box.remove_all()
        self._meters.clear()
        self._level_words.clear()
        scene = self.project.active_scene
        sounds = [w for w in (scene.widgets if scene else []) if w.has_sound]
        self.audio_empty.set_visible(not sounds)
        box.get_parent().get_parent().set_visible(bool(sounds))
        for w in sounds:
            strip = Gtk.Box(hexpand=False, halign=Gtk.Align.START)
            strip.set_size_request(self.STRIP_WIDTH, -1)
            strip.add_css_class("streamy-strip")
            strip.update_property([Gtk.AccessibleProperty.LABEL], [w.name])
            keys = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
            mute = IconOnlyButton("volume-x" if w.muted else w.icon,
                                  f"Unmute {w.name}" if w.muted else f"Mute {w.name}", size="small")
            mute.connect("clicked", lambda *_a, wid=w: self._toggle_mute(wid))
            if w.muted:
                mute.add_css_class("streamy-muted")
            keys.append(mute)
            words = {"off": "Monitor off", "only": "Monitoring here only (not sent)",
                     "both": "Monitoring here and sending"}[w.monitor]
            monitor = IconOnlyButton("headphones" if w.monitor == "off" else "headphones", words, size="small")
            monitor.connect("clicked", lambda *_a, wid=w: self._set_monitor(
                wid, {"off": "only", "only": "both", "both": "off"}[wid.monitor]))
            if w.monitor != "off":
                monitor.add_css_class("streamy-monitoring")
            keys.append(monitor)
            strip.append(keys)
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            column.add_css_class("streamy-strip-body")
            strip.append(column)
            # The head names what it hears; a tap opens the device chooser in the bar.
            head = Gtk.Button(hexpand=True)
            head.add_css_class("streamy-strip-head-key")
            head.add_css_class("flat")
            head.update_property([Gtk.AccessibleProperty.LABEL], [f"{w.name}: choose what it hears"])
            head.connect("clicked", lambda *_a, wid=w: (self.select(wid.id), self._grow("source")))
            lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            line = Gtk.Box()
            line.add_css_class("streamy-strip-head")
            name = TypeLabel(w.name, role="row-title")
            name.set_hexpand(True)
            line.append(name)
            level = TypeLabel("Muted" if w.muted else "−∞", role="caption")
            level.add_css_class("streamy-strip-db")
            level.update_property([Gtk.AccessibleProperty.LABEL], ["Level"])
            line.append(level)
            lines.append(line)
            sub = Gtk.Box()
            sub.add_css_class("streamy-strip-head")
            device = TypeLabel(self.source_summary(w), role="caption")
            device.set_hexpand(True)
            device.label.set_ellipsize(3)
            sub.append(device)
            gain = TypeLabel(self._db_words(w), role="caption")
            gain.add_css_class("streamy-strip-db")
            sub.append(gain)
            lines.append(sub)
            head.set_child(lines)
            column.append(head)
            volume = VolumeSlider(-60, 12, value=w.volume_db, unity=0, unit="dB", meter=2, horizontal=True,
                                  label=f"{w.name} gain",
                                  on_move=lambda value, wid=w, words=gain: self._volume_moved(wid, value, words))
            volume.set_muted(w.muted)
            volume.set_hexpand(True)
            column.append(volume)
            box.append(strip)
            self._meters[w.id] = volume.get_meter()
            self._level_words[w.id] = level
        self.dock.refit()

    @staticmethod
    def _db_words(w: Widget) -> str:
        """The gain, said as gain (the live level beside it says how loud it is)."""
        gain = round(w.volume_db)
        return "Gain 0 dB" if gain == 0 else f"Gain {gain:+d} dB".replace("-", "−")

    @staticmethod
    def level_words(db: float) -> str:
        return "−∞" if db <= -59.5 else f"{round(db)} dB".replace("-", "−")

    def _volume_moved(self, w: Widget, value: float, words) -> None:
        self._checkpoint(f"Change {w.name} gain", key=(w.id, "gain"))
        self._set_volume(w, value)
        words.label.set_label(self._db_words(w))

    def _levels(self, _engine, widget_id: str, left: float, right: float) -> None:
        meter = self._meters.get(widget_id)
        if meter is not None and meter.get_mapped():
            meter.set_levels_db(left, right)
        words = self._level_words.get(widget_id)
        if words is not None and words.get_mapped():
            text = self.level_words(max(left, right))
            if words.label.get_label() not in (text, "Muted"):
                words.label.set_label(text)

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
        self._refresh_bar()
        self._regrow("source")

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
        items: list = []
        live = self.stream_state != "off"
        if live:
            self.readout = BarChip(self._stream_word(), meta=clock(time.monotonic() - (self.live_since or
                                                                                       time.monotonic())),
                                   detail=self._stream_words(), live=True)
            items.append(self.readout)
        elif self.recording_since is not None:
            self.readout = BarChip("Recording", meta=clock(time.monotonic() - self.recording_since), live=True)
            items.append(self.readout)
        else:
            self.readout = None
        w = self._selected_widget()
        if w is not None:
            if items:
                items.append(SEPARATOR)
            cause = self.cause_line(w, self.widget_errors.get(w.id))
            items.append(BarChip(w.name, detail=cause, on_dismiss=self.deselect) if cause else
                         BarChip(w.name, on_dismiss=self.deselect))
            # Source first: what makes the widget work, its summary as the key's words.
            # Editing is the Inspector's; the bar keeps the summary and the quick actions.
            items.append(BarAction(KINDS[w.kind][1], self.source_summary(w), on_activate=lambda: self._grow("source")))
            if w.has_picture:
                items.append(BarAction("crop", tooltip="Crop on the canvas",
                                       on_activate=lambda: self.arrange.set_cropping(w.id)))
            # A widget without its source ends the bar with the fix, as the bar's one key.
            fix = self.fix_words(w, self.widget_errors[w.id]) if w.id in self.widget_errors else None
            if fix:
                items.append(BarAction("monitor-up" if w.kind in ("display", "window") else "rotate-ccw", fix,
                                       primary=True, on_activate=lambda: self.retry_widget(w)))
            elif w.id in self.widget_waiting:
                # The stream is open and may yet start; Try again reconnects it, no picker.
                items.append(BarAction("rotate-ccw", "Try again", primary=True,
                                       on_activate=lambda: (self.widget_waiting.discard(w.id),
                                                            self._restart_widget(w))))
            # Rare and destructive ones wait under ⋯, so the chip keeps its name while an editor is open.
            if w.has_picture:
                switch = BarAction("eye" if not w.visible else "eye-off", "Show" if not w.visible else "Hide",
                                   on_activate=self.toggle_hidden)
            else:
                switch = BarAction("volume-2" if w.muted else "volume-x", "Unmute" if w.muted else "Mute",
                                   on_activate=lambda: self._toggle_mute(w))
            more = [switch, BarAction("pencil", "Rename", on_activate=lambda: self.rename_widget(w)),
                    BarAction("copy", "Duplicate", on_activate=self.duplicate_selected),
                    BarAction("trash-2", "Delete", danger=True, on_activate=self.delete_selected)]
            if live:
                more.append(BarAction("square", "End stream…", danger=True, on_activate=self.ask_end_stream))
            self.center.show_bar(items, more=more)
            return
        items.append(BarAction("plus", "Add widget", menu=self._add_menu))
        if self.recording_since is not None:
            items.append(BarAction("square", "Stop recording", record=True, on_activate=self.toggle_record))
        else:
            items.append(BarAction("circle", "Record", on_activate=self.toggle_record))
        if live:
            items.append(BarAction("square", "End stream", danger=True, on_activate=self.ask_end_stream))
        else:
            items.append(BarAction("radio-tower", "Go live", primary=True, on_activate=self.go_live))
        # The canvases are the Preview header's chips now.
        self._sync_canvas_chips()
        self.center.show_bar(items)

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
        stats = self.engine.stats("stream")
        if stats is None:
            return "Connecting"
        kbps = f"{round(stats.bitrate_kbps):,} kbps"
        dropped = "no dropped frames" if not stats.dropped else f"{stats.dropped:,} dropped"
        return f"{kbps} · {dropped}"

    def _every_second(self) -> bool:
        if self.readout is not None:
            if self.stream_state != "off" and self.live_since is not None:
                self.readout.update(self._stream_word(), meta=clock(time.monotonic() - self.live_since),
                                    detail=self._stream_words())
            elif self.recording_since is not None:
                self.readout.update(meta=clock(time.monotonic() - self.recording_since))
        self._update_thumbnail()
        return True

    def _update_thumbnail(self) -> None:
        """The active scene's sidebar picture, at most once a second and only while the sidebar shows."""
        paintable = self.engine.paintable
        scene = self.project.active_scene
        if paintable is None or scene is None or not self.sidebar.get_mapped():
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
        if widget_id == self.selected:
            return
        self.selected = widget_id
        w = self._selected_widget()
        if w is not None and w.has_picture:
            self.arrange.set_selected(widget_id)
        else:
            self.arrange.set_selected(None)
        self._building = True
        row = self._widget_rows.get(widget_id) if widget_id else None
        if row is not None:
            self.widget_list.select_row(row)
        else:
            self.widget_list.unselect_all()
        self._building = False
        self._refresh_inspector()
        self._refresh_bar()

    def deselect(self) -> None:
        self.select(None)

    def _canvas_selected(self, widget_id) -> None:
        if not self._building:
            self.select(widget_id)

    def _widget_row_selected(self, _list, row) -> None:
        if not self._building:
            self.select(getattr(row, "widget_id", None) if row is not None else None)

    # ── Direct manipulation ──

    def _set_rect(self, w: Widget, x, y, width, height, *, commit: bool) -> None:
        self.project.set_rect(w.id, x, y, width, height)
        self.engine.update_widget(w)
        self.arrange.set_item_rect(w.id, w.x, w.y, w.width, w.height)
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

    def _frame_shown(self, _engine, widget_id: str, x: float, y: float, width: float, height: float) -> None:
        """The preview on screen now draws ``widget_id`` here: a dragged widget's outline stays on its picture."""
        if widget_id not in self._following or not hasattr(self.arrange, "set_item_shown_rect"):
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
        w = self._selected_widget()
        scene = self.project.active_scene
        if w is None or scene is None:
            return
        import copy

        from .model import new_id

        self._checkpoint(f"Duplicate {w.name}")
        twin = copy.deepcopy(w)
        twin.id = new_id(w.kind)
        twin.name = ""
        twin.name = self.project._next_widget_name(scene, w.kind)
        twin.source.pop("restore-token", None)  # a second share: its own picker
        if w.has_picture:
            twin.x, twin.y = w.x + 40, w.y + 40
        scene.widgets.insert(scene.widgets.index(w) + 1, twin)
        self.selected = twin.id
        self._changed(structure=True)
        self._show_scene()
        self._ask_missing_sources()

    def toggle_hidden(self) -> None:
        w = self._selected_widget()
        if w is not None:
            self._set_visible(w, not w.visible)

    def _set_visible(self, w: Widget, visible: bool) -> None:
        self._checkpoint(("Show " if visible else "Hide ") + w.name)
        w.visible = visible
        self._show_scene()
        self._changed(structure=True)

    def toggle_lock(self) -> None:
        w = self._selected_widget()
        if w is not None:
            self._checkpoint(("Unlock " if w.locked else "Lock ") + w.name)
            w.locked = not w.locked
            self._changed(structure=True)

    def restack(self, direction: int) -> None:
        w = self._selected_widget()
        scene = self.project.active_scene
        if w is None or scene is None:
            return
        self._checkpoint("Bring to front" if direction > 0 else "Send to back")
        self.project.move_widget(w.id, len(scene.widgets) if direction > 0 else 0)
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
        self.selected = None
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

    def _canvas_chip(self, key: str, on: bool) -> bool:
        if not on:
            return False  # one canvas is always on
        if self.engine.outputs:
            Toast.show(self.host, "The canvas can't change while recording or live", kind="warning")
            return False
        self.set_format(key)
        return self.project.format == key

    def _sync_canvas_chips(self) -> None:
        for key, chip in getattr(self, "canvas_chips", {}).items():
            chip.set_active(self.project.format == key)
            chip.set_live(bool(self.engine.outputs) and self.project.format == key)

    def set_format(self, key: str) -> None:
        if key == self.project.format:
            return
        if self.engine.outputs:
            self.formats.set_current(self.project.format)
            Toast.show(self.host, "The canvas can't change while recording or live", kind="warning")
            return
        self._checkpoint("Change canvas")
        self.project.set_format(key)
        self.arrange.set_canvas_size(*self.project.canvas)
        self.engine.set_canvas(self.project)
        if self.engine.paintable is not None:
            self.picture.set_paintable(self.engine.paintable)
        self._changed(structure=True)

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
        if self.recording_since is not None:
            self.engine.stop_record()
            return
        folder = recordings_dir()
        folder.mkdir(parents=True, exist_ok=True)
        name = time.strftime("Streamy %Y-%m-%d %H.%M.%S.mkv")
        try:
            self.engine.start_record(str(folder / name), container="mkv")
        except EngineError as error:
            Toast.show(self.host, str(error), kind="error")
            return
        self.recording_path = folder / name

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
        if self.stream_state != "off":
            return
        server, key = self._destination()
        if not key and self.destination != "custom":
            self.edit_destination(then_go=True)
            return
        self._start_stream(server, key)

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
        self.engine.stop_stream()

    def _output_state(self, _engine, kind: str, state: str) -> None:
        if kind == "record":
            if state == "on":
                self.recording_since = time.monotonic()
            elif state == "off":
                if self.recording_since is not None:
                    Toast.show(self.host, "Recording saved", kind="saved")
                self.recording_since = None
        elif kind == "stream":
            if state == "off" and self.stream_state != "off":
                Toast.show(self.host, "Stream ended", kind="done")
                self.live_since = None
            self.stream_state = state
        self._refresh_bar()
