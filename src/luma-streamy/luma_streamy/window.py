# SPDX-License-Identifier: Apache-2.0
"""Streamy's window: scenes in the sidebar, the canvas in an island, insets
for widgets, the inspector and audio, and the action bar as the action center.
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
    SEPARATOR, ActionCenter, ActionEditor, AlignmentActions, AppWindow, ArrangeItem, ArrangeView, AudioMeter,
    BarAction, BarChip, Command, CommandGroup, CommandRegistry, Dock, DockTile,
    DestructiveDialog, IconOnlyButton, Layer, LayerTree, ListEmptyState, ModeSwitch,
    NavigationSidebar, PropertyColor, PropertyNumber, PropertySection, RowLead, SidebarFoot, SidebarRow,
    SidebarToggle, TextButton, Toast, ToastHost, TypeLabel, ValueRange, ValueSlider, add_style_sheet,
)
from luma_appkit.action_bubble import MenuItem  # noqa: E402
from luma_appkit.creative_parts import add_arrange_item  # noqa: E402

from . import graph  # noqa: E402
from .engine import Engine, EngineError  # noqa: E402
from .layouts import PRESETS, LayoutStore  # noqa: E402
from .model import FORMATS, KINDS, ModelError, Project, ProjectStore, Widget  # noqa: E402

APP_ID = "org.projectluma.Streamy"
SECRET_KIND = "stream-key"


def data_dir() -> Path:
    return Path(os.environ.get("STREAMY_DATA_DIR") or Path(GLib.get_user_data_dir()) / "streamy")


def recordings_dir() -> Path:
    videos = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS) or str(Path.home() / "Videos")
    return Path(videos) / "Streamy"


def clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class StreamyWindow(AppWindow):
    def __init__(self, application, *, engine: Engine | None = None, portals=None) -> None:
        self.store = ProjectStore(data_dir() / "project.json")
        self.layouts = LayoutStore(data_dir() / "layouts.json")
        self.settings_path = data_dir() / "settings.json"
        self.project: Project = self.store.load()
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
        if self.portals is not None:
            self.portals.on_ready = lambda _widget_id: self._show_scene()
        self.engine.connect("levels", self._levels)
        self.engine.connect("problem", lambda _e, text: Toast.show(self.host, text, kind="warning"))
        self.engine.connect("output-state", self._output_state)
        self.engine.connect("widget-failed", self._widget_failed)

        self._build_sidebar()
        self._build_canvas()
        self._build_insets()
        self.set_body(self._body())
        self.refresh_all()
        self.connect("close-request", self._closing)
        self.connect("notify::is-active", lambda *_a: self._update_preview_running())
        GLib.idle_add(self._start_engine)

    # ── Commands ──

    def _commands(self) -> CommandRegistry:
        add = tuple(Command(f"widget.add-{kind}", f"Add {KINDS[kind][0].lower()}", lambda k=kind: self.add_widget(k),
                            icon=KINDS[kind][1]) for kind in ("display", "window", "camera", "image", "microphone",
                                                              "desktop-audio"))
        layouts = tuple(Command(f"layout.{key}", label, lambda k=key: self.use_layout(k), icon="layout-dashboard",
                                checked=lambda k=key: False) for key, (label, _l) in PRESETS.items())
        return CommandRegistry((
            CommandGroup(None, (
                Command("scene.new", "New scene", self.new_scene, icon="square-pen", shortcut=("Ctrl", "N")),
                Command("scene.duplicate", "Duplicate scene", self.duplicate_scene, icon="copy",
                        shortcut=("Ctrl", "Shift", "D")),
                Command("scene.rename", "Rename scene…", self.rename_scene, icon="pencil", shortcut=("F2",)),
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
                Command("layout.save", "Save layout…", self.save_layout, icon="save"),)),
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
        """The sidebar: scenes on top, the chosen scene's widgets under them, a seam between."""
        self.sidebar = NavigationSidebar(variant="files")
        self.sidebar.list.connect("row-activated", self._scene_activated)
        self.widget_store = Gio.ListStore.new(type(Layer("x", "x", "layers")))
        self.widget_tree = LayerTree(self.widget_store, on_select=self._tree_selected,
                                     on_visibility=self._tree_visibility, on_rename=self._tree_renamed,
                                     on_reorder=self._tree_reorder, on_context=self._tree_context)
        self.widgets_empty = ListEmptyState("No widgets in this scene")
        widgets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        widgets.append(self.widget_tree)
        widgets.append(self.widgets_empty)
        self.sidebar.append_pane("Widgets", widgets, share=self.settings.get("sidebar-share", 0.36),
                                 on_share=lambda share: self._setting("sidebar-share", round(share, 3)))
        self.sidebar.append_footer(SidebarFoot(search="Search scenes", on_search=self._search_scenes,
                                               add=("New scene", "square-pen", self.new_scene)))
        self._scene_filter = ""

    def _build_canvas(self) -> None:
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True)
        self.picture.add_css_class("streamy-program")
        self.arrange = ArrangeView(self.picture, canvas=self.project.canvas, on_select=self._canvas_selected,
                                   on_change=self._canvas_changed, on_commit=self._canvas_committed,
                                   on_activate=lambda wid: self._open_inspector())
        self.arrange.update_property([Gtk.AccessibleProperty.LABEL], ["Scene"])

    def _build_insets(self) -> None:
        """The island is tiled: the preview, audio and the inspector, each an inset with seams between."""
        self.inspector_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.inspector_box.add_css_class("streamy-inspector")
        inspector_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        inspector_scroll.set_child(self.inspector_box)
        self.audio_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=4,
                                     min_children_per_line=1, valign=Gtk.Align.START, column_spacing=24)
        self.audio_box.add_css_class("streamy-mixer")
        audio_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        audio_scroll.set_child(self.audio_box)
        self.audio_empty = ListEmptyState("No microphone or desktop audio")
        audio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        audio.append(self.audio_empty)
        audio.append(audio_scroll)
        self.canvas_facts = TypeLabel("", role="caption")
        self.canvas_facts.add_css_class("streamy-canvas-facts")
        self.preview_tile = DockTile("preview", "Preview", "monitor", self.arrange, main=True,
                                     actions=[self.canvas_facts])
        self.audio_tile = DockTile("audio", "Audio", "volume-2", audio)
        self.inspector_tile = DockTile("inspector", "Inspector", "sliders-horizontal", inspector_scroll)
        self.dock = Dock([self.preview_tile, self.audio_tile, self.inspector_tile], layout=self.layouts.current,
                         under_bar=True, on_layout=self.layouts.remember)
        self.preview_tile.connect("notify::width", lambda *_a: GLib.idle_add(self._size_preview))

    def _size_preview(self) -> bool:
        x0, _y0 = self.arrange.canvas_to_view(0, 0)
        x1, _y1 = self.arrange.canvas_to_view(*self.project.canvas)
        if x1 - x0 > 8:
            self.engine.set_preview_size((x1 - x0) * self.get_scale_factor(), 0)
        return False

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
        if self.engine.paintable is not None:
            self.picture.set_paintable(self.engine.paintable)
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
            if w.kind in ("display", "window", "camera") and w.visible and w.id not in self.portals.captures:
                self._choose_source(w, restore=True)

    def _choose_source(self, widget: Widget, *, restore: bool) -> None:
        if self.portals is None:
            return

        def done(capture) -> None:
            if capture.restore_token:
                widget.source["restore-token"] = capture.restore_token  # single-use: keep the new one
            if capture.aspect and not widget.source.get("sized"):
                widget.source["sized"] = True
                h = widget.width / capture.aspect
                self.project.set_rect(widget.id, widget.x, widget.y, widget.width, h)
            self._changed(structure=True)
            self._show_scene()

        def failed(reason: str) -> None:
            if reason == "cancelled":
                return
            self._widget_failed(None, widget.id, "nocamera" if reason == "no camera" else "missing")

        if widget.kind == "camera":
            self.portals.camera(widget.id, done=done, failed=failed)
        else:
            token = widget.source.get("restore-token") if restore else None
            self._with_parent(lambda parent: self.portals.screencast(
                widget.id, window=widget.kind == "window", restore_token=token, done=done, failed=failed,
                parent=parent))

    def _show_scene(self) -> None:
        self.engine.show_scene(self.project.active_scene, skip=set(self.widget_errors))

    @staticmethod
    def problem_words(w: Widget, reason: str) -> str:
        """What happened to a widget's source, and what fixes it, in a sentence or two."""
        thing = {"camera": "The camera", "display": "The shared display", "window": "The shared window",
                 "image": "The image", "microphone": "The microphone",
                 "desktop-audio": "Desktop audio"}.get(w.kind, w.name)
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
        if w.kind == "image":
            return "The image can't be opened. Choose another one."
        return f"{thing} stopped. Try again."

    def _widget_failed(self, _engine, widget_id: str, reason: str) -> None:
        """A source failed: say so on the widget, offer the fix, keep everything else running."""
        try:
            _scene, w = self.project.find_widget(widget_id)
        except ModelError:
            return
        self.widget_errors[widget_id] = reason
        if self.portals is not None:
            self.portals.close(widget_id)
        Toast.show(self.host, f"{w.name} couldn't start", kind="warning")
        self.refresh_all()

    def retry_widget(self, w: Widget) -> None:
        self.widget_errors.pop(w.id, None)
        if self.portals is not None:
            self.portals.close(w.id)
        self.refresh_all()
        if w.kind in ("display", "window", "camera") and not self.fake:
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
        for scene in self.project.scenes:
            if self._scene_filter and self._scene_filter not in scene.name.casefold():
                continue
            count = len(scene.widgets)
            row = SidebarRow(scene.name, lead=RowLead.thumbnail(self._thumbs.get(scene.id), kind="image"),
                             subtitle="Empty" if not count else f"{count} widget" + ("" if count == 1 else "s"))
            row.scene_id = scene.id
            self._scene_rows[scene.id] = row
            self.sidebar.append_row(row)
            if scene.id == self.project.active:
                self.sidebar.list.select_row(row)

    def _refresh_canvas(self) -> None:
        width, height = self.project.canvas
        self.canvas_facts.label.set_label(f"{width} × {height} · {graph.FPS} fps")
        scene = self.project.active_scene
        self.arrange.clear()
        self.arrange.set_canvas_size(*self.project.canvas)
        if scene is None:
            return
        for w in scene.widgets:
            if not w.has_picture:
                continue
            add_arrange_item(self.arrange, ArrangeItem(w.id, w.name, w.x, w.y, w.width, w.height, locked=w.locked,
                                                       visible=w.visible, keeps_aspect=w.kind in ("camera", "display")))
        if self.selected and scene.widget(self.selected) and scene.widget(self.selected).has_picture:
            self.arrange.set_selected(self.selected)

    def _refresh_widgets(self) -> None:
        scene = self.project.active_scene
        self.widget_store.remove_all()
        widgets = list(reversed(scene.widgets)) if scene else []
        for w in widgets:
            detail = "Unavailable" if w.id in self.widget_errors else "Locked" if w.locked else (
                None if w.has_picture else ("Muted" if w.muted else None))
            self.widget_store.append(Layer(w.id, w.name, w.icon, visible=w.visible, detail=detail))
        self.widget_tree.set_visible(bool(widgets))
        self.widgets_empty.set_visible(not widgets)
        self.widget_tree.set_selected([self.selected] if self.selected else [])

    def _selected_widget(self) -> Widget | None:
        scene = self.project.active_scene
        return scene.widget(self.selected) if scene and self.selected else None

    # ── Inspector ──

    def _refresh_inspector(self) -> None:
        box = self.inspector_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        w = self._selected_widget()
        if w is None:
            self.inspector_tile.set_title("Inspector")
            box.append(ListEmptyState("Select a widget to change it"))
            return
        self.inspector_tile.set_title(w.name)
        if w.has_picture:
            box.append(self._position_section(w))
            box.append(self._effects_sections(w))
        else:
            box.append(self._sound_section(w))
        box.append(self._source_section(w))

    def _number(self, label, value, on_change, *, unit=None, minimum=None, maximum=None, step=None, digits=0,
                name=None):
        return PropertyNumber(label, value, unit=unit, minimum=minimum, maximum=maximum, step=step, digits=digits,
                              name=name, compact=True, on_change=on_change)

    def _position_section(self, w: Widget) -> Gtk.Widget:
        def rect(field):
            def apply(value):
                x, y, width, height = w.x, w.y, w.width, w.height
                if field == "x":
                    x = value
                elif field == "y":
                    y = value
                elif field == "w":
                    width = max(8, value)
                else:
                    height = max(8, value)
                self._set_rect(w, x, y, width, height, commit=True)
            return apply

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(PropertySection(
            "Position",
            (self._number("X", w.x, rect("x"), unit="px"), self._number("Y", w.y, rect("y"), unit="px")),
            (self._number("W", w.width, rect("w"), unit="px", minimum=8),
             self._number("H", w.height, rect("h"), unit="px", minimum=8)),
            AlignmentActions(on_align=self._align)))
        crop = w.effects

        def cropper(name):
            def apply(value):
                setattr(w.effects, name, max(0.0, min(0.49, value / 100)))
                self._effects_changed(w)
            return apply

        box.append(PropertySection(
            "Crop",
            (self._number("Left", crop.crop_left * 100, cropper("crop_left"), unit="%", minimum=0, maximum=49),
             self._number("Right", crop.crop_right * 100, cropper("crop_right"), unit="%", minimum=0, maximum=49)),
            (self._number("Top", crop.crop_top * 100, cropper("crop_top"), unit="%", minimum=0, maximum=49),
             self._number("Bottom", crop.crop_bottom * 100, cropper("crop_bottom"), unit="%", minimum=0, maximum=49))))
        return box

    def _effects_sections(self, w: Widget) -> Gtk.Widget:
        fx = w.effects

        def setter(name, scale=1.0, low=0.0, high=None):
            def apply(value):
                value = value / scale
                if high is not None:
                    value = min(high, value)
                setattr(w.effects, name, max(low, value) if low is not None else value)
                self._effects_changed(w)
            return apply

        stroke_color = PropertyColor("Colour", fx.stroke_color)

        def color_changed(*_args):
            rgba = stroke_color.get_rgba()
            w.effects.stroke_color = "%02X%02X%02X" % (round(rgba.red * 255), round(rgba.green * 255),
                                                       round(rgba.blue * 255))
            self._effects_changed(w)

        stroke_color.connect("color-changed", color_changed)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("streamy-effects")
        box.append(PropertySection("Corners", self._number("Radius", fx.corner_radius, setter("corner_radius"),
                                                           unit="px", minimum=0, maximum=400)))
        box.append(PropertySection("Stroke", (self._number("Width", fx.stroke_width, setter("stroke_width"), unit="px",
                                                           minimum=0, maximum=64), None), stroke_color))
        box.append(PropertySection(
            "Shadow",
            (self._number("Blur", fx.shadow_blur, setter("shadow_blur"), unit="px", minimum=0, maximum=200),
             self._number("Opacity", fx.shadow_opacity * 100, setter("shadow_opacity", 100, 0, 1), unit="%",
                          minimum=0, maximum=100)),
            (self._number("X", fx.shadow_x, setter("shadow_x", low=None), unit="px", minimum=-200, maximum=200),
             self._number("Y", fx.shadow_y, setter("shadow_y", low=None), unit="px", minimum=-200, maximum=200))))
        box.append(PropertySection("Layer", (self._number("Opacity", fx.opacity * 100, setter("opacity", 100, 0, 1),
                                                          unit="%", minimum=0, maximum=100), None)))
        return box

    def _sound_section(self, w: Widget) -> Gtk.Widget:
        slider = ValueSlider("Volume", w.volume_db, -60, 6, unit="dB", default=0, step=1, layout="editor",
                             on_change=lambda value: self._set_volume(w, value))
        mute = TextButton("Unmute" if w.muted else "Mute", style="plain", on_click=lambda: self._toggle_mute(w))
        return PropertySection("Sound", slider, mute)

    def _source_section(self, w: Widget) -> Gtk.Widget:
        words = {
            "display": "A shared display",
            "window": "A shared window",
            "camera": "The camera",
            "image": Path(w.source.get("path", "")).name or "No image",
            "microphone": "The default microphone",
            "desktop-audio": "Everything this computer plays",
            "app-audio": "One app's sound",
        }[w.kind]
        items = [TypeLabel(words, role="body", wrap=True)]
        problem = self.widget_errors.get(w.id)
        if problem is not None:
            fix = "Share again…" if w.kind in ("display", "window") else (
                "Choose image…" if w.kind == "image" else "Try again")
            items = [TypeLabel(self.problem_words(w, problem), role="body", wrap=True),
                     TextButton(fix, style="plain", on_click=lambda: self.retry_widget(w))]
            return PropertySection("Source", *items)
        if w.kind in ("display", "window"):
            button = TextButton("Share another…" if w.kind == "display" else "Choose another window…", style="plain",
                                on_click=lambda: self._reshare(w))
            items.append(button)
        elif w.kind == "image":
            button = TextButton("Choose image…", style="plain", on_click=lambda: self._choose_image(w))
            items.append(button)
        return PropertySection("Source", *items)

    def _reshare(self, w: Widget) -> None:
        if self.portals is not None:
            self.portals.close(w.id)
            w.source.pop("restore-token", None)
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
            w.name = file.get_basename().rsplit(".", 1)[0][:40] or w.name
            if on_chosen is not None:
                on_chosen(True)
            self._restart_widget(w)

        dialog.open(self, None, chosen)

    def _restart_widget(self, w: Widget) -> None:
        self.engine._remove_branch(w.id)
        self._show_scene()
        self._changed(structure=True)

    def _open_inspector(self) -> None:
        """Effects: the inspector, in its tile (or its drawer on a phone)."""
        if self.dock.phone:
            self.dock.open_drawer("inspector")
        else:
            self.inspector_tile.more_button.grab_focus()

    # ── Audio inset ──

    def _refresh_audio(self) -> None:
        """A strip per sound: mute, name and level in dB, the meter, then the volume."""
        box = self.audio_box
        box.remove_all()
        self._meters.clear()
        scene = self.project.active_scene
        sounds = [w for w in (scene.widgets if scene else []) if w.has_sound]
        self.audio_empty.set_visible(not sounds)
        box.get_parent().get_parent().set_visible(bool(sounds))
        for w in sounds:
            # The mute key leads the strip; the name, meter and volume share one left edge beside it.
            strip = Gtk.Box()
            strip.add_css_class("streamy-strip")
            strip.update_property([Gtk.AccessibleProperty.LABEL], [w.name])
            mute = IconOnlyButton("volume-x" if w.muted else w.icon,
                                  f"Unmute {w.name}" if w.muted else f"Mute {w.name}", size="small")
            mute.set_valign(Gtk.Align.START)
            mute.connect("clicked", lambda *_a, wid=w: self._toggle_mute(wid))
            if w.muted:
                mute.add_css_class("streamy-muted")
            strip.append(mute)
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            column.add_css_class("streamy-strip-body")
            strip.append(column)
            line = Gtk.Box()
            line.add_css_class("streamy-strip-head")
            name = TypeLabel(w.name, role="row-title")
            name.set_hexpand(True)
            line.append(name)
            level = TypeLabel(self._db_words(w), role="caption")
            level.add_css_class("streamy-strip-db")
            line.append(level)
            column.append(line)
            meter = AudioMeter(2, horizontal=True, label=f"{w.name} level")
            meter.set_hexpand(True)
            column.append(meter)
            volume = ValueRange(w.volume_db, -60, 6, 1, label=f"{w.name} volume")
            volume.connect("value-changed", lambda _r, value, wid=w, words=level: self._volume_moved(wid, value, words))
            column.append(volume)
            box.append(strip)
            self._meters[w.id] = meter

    @staticmethod
    def _db_words(w: Widget) -> str:
        if w.muted:
            return "Muted"
        return "0 dB" if round(w.volume_db) == 0 else f"{round(w.volume_db):+d} dB".replace("-", "−")

    def _volume_moved(self, w: Widget, value: float, words) -> None:
        self._set_volume(w, value)
        words.label.set_label(self._db_words(w))

    def _levels(self, _engine, widget_id: str, left: float, right: float) -> None:
        meter = self._meters.get(widget_id)
        if meter is not None and meter.get_mapped():
            meter.set_levels_db(left, right)

    def _set_volume(self, w: Widget, value: float) -> None:
        w.volume_db = round(value)
        self.engine.update_widget(w)
        self._changed()

    def _toggle_mute(self, w: Widget) -> None:
        w.muted = not w.muted
        self.engine.update_widget(w)
        self._changed(structure=True)
        Toast.show(self.host, f"{w.name} muted" if w.muted else f"{w.name} unmuted", kind="done")

    # ── The bar ──

    def _add_menu(self) -> list:
        return [MenuItem(KINDS[kind][0], icon=KINDS[kind][1], on_activate=lambda k=kind: self.add_widget(k))
                for kind in ("display", "window", "camera", "image", "microphone", "desktop-audio")]

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
            items += [BarChip(w.name, on_dismiss=self.deselect),
                      BarAction("sliders-horizontal", "Effects", on_activate=self._open_inspector),
                      BarAction("layers", "Arrange", menu=self._arrange_menu),
                      BarAction("eye" if not w.visible else "eye-off", tooltip="Show" if not w.visible else "Hide",
                                on_activate=self.toggle_hidden),
                      BarAction("trash-2", tooltip="Delete", danger=True, on_activate=self.delete_selected)]
            more = [BarAction("plus", "Add widget", menu=self._add_menu)]
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
        # The canvas's shape starts the bar while nothing is selected or sent.
        modes = self.formats if not live and self.recording_since is None else None
        if modes is not None and modes.get_parent() is not None:
            modes.get_parent().remove(modes)
        self.center.show_bar(items, modes=modes)

    def _arrange_menu(self) -> list:
        return [MenuItem("Bring to front", icon="arrow-up", note="Ctrl+]", on_activate=lambda: self.restack(1)),
                MenuItem("Send to back", icon="arrow-down", note="Ctrl+[", on_activate=lambda: self.restack(-1)),
                MenuItem("Fill the canvas", icon="maximize-2", on_activate=lambda: self._align("fill")),
                MenuItem("Centre", icon="align-center-vertical",
                         on_activate=lambda: (self._align("center"), self._align("middle"))),
                MenuItem("Lock" if not (self._selected_widget() and self._selected_widget().locked) else "Unlock",
                         icon="lock", on_activate=self.toggle_lock)]

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
        """The picture at thumbnail size (42 × 34, cropped to fill), so a row never asks for 1920 pixels."""
        if image is None or image.get_intrinsic_width() == 0 or self.get_renderer() is None:
            return None
        from gi.repository import Graphene

        tw, th = 42, 34
        iw, ih = image.get_intrinsic_width(), image.get_intrinsic_height()
        scale = max(tw / iw, th / ih)
        snapshot = Gtk.Snapshot()
        snapshot.translate(Graphene.Point().init((tw - iw * scale) / 2, (th - ih * scale) / 2))
        image.snapshot(snapshot, iw * scale, ih * scale)
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
        self.widget_tree.set_selected([widget_id] if widget_id else [])
        self._refresh_inspector()
        self._refresh_bar()

    def deselect(self) -> None:
        self.select(None)

    def _canvas_selected(self, widget_id) -> None:
        if not self._building:
            self.select(widget_id)

    def _tree_selected(self, ids: list[str]) -> None:
        if not self._building:
            self.select(ids[0] if ids else None)

    # ── Direct manipulation ──

    def _set_rect(self, w: Widget, x, y, width, height, *, commit: bool) -> None:
        self.project.set_rect(w.id, x, y, width, height)
        self.engine.update_widget(w)
        self.arrange.set_item_rect(w.id, w.x, w.y, w.width, w.height)
        if commit:
            self._changed()

    def _canvas_changed(self, widget_id, x, y, width, height) -> None:
        _scene, w = self.project.find_widget(widget_id)
        self.project.set_rect(widget_id, x, y, width, height)
        self.engine.update_widget(w)

    def _canvas_committed(self, widget_id, x, y, width, height) -> None:
        self._canvas_changed(widget_id, x, y, width, height)
        if widget_id == self.selected:
            self._refresh_inspector()
        self._changed()

    def _align(self, how: str) -> None:
        w = self._selected_widget()
        if w is None or not w.has_picture:
            return
        self.project.align(w.id, how)
        self._set_rect(w, w.x, w.y, w.width, w.height, commit=True)
        self._refresh_inspector()

    def _effects_changed(self, w: Widget) -> None:
        self.engine.update_widget(w)
        self._changed()

    # ── Widgets ──

    def add_widget(self, kind: str) -> None:
        scene = self.project.active_scene
        if scene is None:
            return
        w = self.project.add_widget(scene.id, kind)
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
        if kind in ("display", "window", "camera") and not self.fake:
            self._choose_source(w, restore=False)
        Toast.show(self.host, f"{w.name} added", kind="added")

    def delete_selected(self) -> None:
        w = self._selected_widget()
        if w is None:
            return
        scene, index, removed = self.project.remove_widget(w.id)
        self.selected = None
        self.widget_errors.pop(removed.id, None)
        self._show_scene()
        if self.portals is not None:
            self.portals.close(removed.id)
        self._changed(structure=True)

        def undo() -> None:
            self.project.restore_widget(scene.id, index, removed)
            self.selected = removed.id
            self._changed(structure=True)
            self._show_scene()
            self._ask_missing_sources()

        Toast.show(self.host, f"{removed.name} deleted", kind="deleted", undo=undo)

    def toggle_hidden(self) -> None:
        w = self._selected_widget()
        if w is not None:
            self._set_visible(w, not w.visible)

    def _set_visible(self, w: Widget, visible: bool) -> None:
        w.visible = visible
        self._show_scene()
        self._changed(structure=True)

    def toggle_lock(self) -> None:
        w = self._selected_widget()
        if w is not None:
            w.locked = not w.locked
            self._changed(structure=True)

    def restack(self, direction: int) -> None:
        w = self._selected_widget()
        scene = self.project.active_scene
        if w is None or scene is None:
            return
        self.project.move_widget(w.id, len(scene.widgets) if direction > 0 else 0)
        self.engine.restack(scene)
        self._changed(structure=True)

    def _tree_visibility(self, layer) -> None:
        scene = self.project.active_scene
        w = scene.widget(layer.get_id()) if scene else None
        if w is not None:
            self._set_visible(w, layer.get_visible())

    def _tree_context(self, layer, *_args) -> None:
        from luma_appkit.action_bubble import FloatingMenu

        scene = self.project.active_scene
        w = scene.widget(layer.get_id()) if scene else None
        if w is None:
            return
        self.select(w.id)
        FloatingMenu([MenuItem("Rename", icon="pencil", on_activate=lambda: self.widget_tree.rename(layer.get_id())),
                      MenuItem("Unlock" if w.locked else "Lock", icon="lock-open" if w.locked else "lock",
                               on_activate=self.toggle_lock),
                      MenuItem("Show" if not w.visible else "Hide", icon="eye" if not w.visible else "eye-off",
                               on_activate=self.toggle_hidden),
                      None,
                      MenuItem("Delete", icon="trash-2", danger=True, on_activate=self.delete_selected)],
                     label=w.name).popup(self.widget_tree)

    def _tree_renamed(self, layer) -> None:
        try:
            _scene, w = self.project.find_widget(layer.get_id())
        except ModelError:
            return
        name = layer.get_name().strip()
        if name:
            w.name = name
            self._changed(structure=True)

    def _inspector_renamed(self, name: str) -> None:
        w = self._selected_widget()
        if w is not None and name.strip():
            w.name = name.strip()
            self._changed(structure=True)

    def _tree_reorder(self, layer, _parent, index: int) -> None:
        scene = self.project.active_scene
        if scene is None:
            return
        # The tree lists the top first; the model stacks from the bottom.
        self.project.move_widget(layer.get_id(), len(scene.widgets) - 1 - index)
        self.engine.restack(scene)
        self._changed(structure=True)

    # ── Scenes ──

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
        scene = self.project.add_scene()
        self.switch_scene(scene.id)
        Toast.show(self.host, f"{scene.name} added", kind="added")

    def duplicate_scene(self) -> None:
        scene = self.project.active_scene
        if scene is not None:
            copy = self.project.duplicate_scene(scene.id)
            self.switch_scene(copy.id)

    def rename_scene(self) -> None:
        scene = self.project.active_scene
        if scene is None:
            return
        entry = Gtk.Entry(text=scene.name, hexpand=True)
        editor = ActionEditor("Rename scene", "pencil", fields=[("Name", entry)], body=Gtk.Box(visible=False),
                              primary=BarAction("check", "Rename", on_activate=lambda: done()))

        def done() -> None:
            try:
                self.project.rename_scene(scene.id, entry.get_text())
            except ModelError:
                return
            self.center.fold()
            self._changed(structure=True)

        entry.connect("activate", lambda *_a: done())
        self.center.set_editor(editor)
        self.center.grow()
        entry.grab_focus()

    def delete_scene(self) -> None:
        scene = self.project.active_scene
        if scene is None or len(self.project.scenes) < 2:
            return
        index, removed = self.project.remove_scene(scene.id)
        if self.portals is not None:
            for w in removed.widgets:
                self.portals.close(w.id)
        self.switch_scene(self.project.active)

        def undo() -> None:
            self.project.restore_scene(index, removed)
            self.switch_scene(removed.id)

        Toast.show(self.host, f"{removed.name} deleted", kind="deleted", undo=undo)

    # ── Canvas format and layouts ──

    def set_format(self, key: str) -> None:
        if key == self.project.format:
            return
        if self.engine.outputs:
            self.formats.set_current(self.project.format)
            Toast.show(self.host, "The canvas can't change while recording or live", kind="warning")
            return
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
        entry.grab_focus()

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
