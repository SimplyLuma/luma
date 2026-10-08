# SPDX-License-Identifier: Apache-2.0
"""Native adaptive Darkroom workspace."""

from __future__ import annotations

import os
import copy
import threading
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("LumaSemantics", "1")
gi.require_version("LumaUI", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, LumaSemantics, LumaUI

from luma_appkit import AppContext, AppWindow, CommandRegistry, IconButton, Island, Toolbar, ConnectedButtonGroup, NumericField, add_style_sheet

from .editing import EditError, Editor
from .raw import RAW_SUFFIXES, raw_thumbnail
from .engine import ExportJob, ImagingUnavailable, RasterEngine, RenderError
from .model import Crop, DOCUMENT_SUFFIX, Document, DocumentError, DocumentStore, ExportPreset, Layer, file_path


def _point_rectangle(x: float, y: float) -> Gdk.Rectangle:
    """A 1x1 rectangle at a point. Gdk.Rectangle(x, y, 1, 1) silently ignores its arguments."""
    rectangle = Gdk.Rectangle()
    rectangle.x, rectangle.y, rectangle.width, rectangle.height = int(x), int(y), 1, 1
    return rectangle


APP_ID = "org.projectluma.Darkroom"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".gif", ".heif", ".heic", ".avif", ".dng", ".nef", ".cr2", ".cr3", ".arw", ".raf", ".orf", ".rw2"}
IMAGE_SUFFIXES |= RAW_SUFFIXES


def icon_button(icon: str, label: str, action: str | None = None) -> Gtk.Button:
    button = IconButton(icon, label, context=AppContext.from_environment(), quiet=True)
    if action:
        button.set_action_name(action)
    return button


def clear(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


class DarkroomWindow(AppWindow):
    def __init__(self, application) -> None:
        super().__init__(
            application=application, app_id=APP_ID, title="Darkroom", icon_name=APP_ID,
            commands=CommandRegistry(()), default_width=1380, default_height=880,
            minimum_width=360, minimum_height=294,
        )
        LumaUI.init()
        # The C kit's context answers the touch and decoration questions the
        # workspace asks; the window's own decision is the kit's.
        self.native_context = LumaUI.Context.new_from_environment()
        self.document: Document | None = None
        self.editor: Editor | None = None
        self.document_path: Path | None = None
        self.store = DocumentStore(Path(GLib.get_user_state_dir()) / "darkroom" / "recovery")
        self.dirty = False
        self._closing = False
        self.export_job: ExportJob | None = None
        self._compact = False
        self._fit_mode = True
        self._panels_hidden = False
        self._crop_entry = None
        self._adjustment_scales = {}
        self._adjustment_values = {}
        self._sessions = {}
        self._current_path = None
        self._thumbnail_cache = {}
        self._retouch_buttons = {}
        self._source_size = (0, 0)
        self._preview_scale = 1.0
        self._render_generation = 0
        self._render_pending = False
        self._interactive = False
        self._settle_source = 0
        self._prepared_key = None
        self._prepared_source = None
        self._prepared_small = None
        self._prepared_original = None
        self._autosave_source = 0
        self._updating = False
        self._recovery_offered = False
        self._filmstrip_paths: list[Path] = []
        self._tool_buttons: dict[str, Gtk.ToggleButton] = {}
        self._appearance = "system"
        self._clone_source: list[float] | None = None
        self._edited_preview = None
        self._crop_drag_handle: str | None = None
        self._crop_drag_start: Crop | None = None
        self.toast_overlay = Adw.ToastOverlay()
        self._build_ui()
        self._build_actions()
        self._load_style()
        self._refresh_all()
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 859px"))
        breakpoint.connect("apply", lambda *_: self._set_compact(True))
        breakpoint.connect("unapply", lambda *_: self._set_compact(False))
        self.add_breakpoint(breakpoint)
        self.connect("close-request", self._close_request)
        GLib.idle_add(self._apply_initial_width)

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.toast_overlay.set_child(root)
        self.set_body(self.toast_overlay)
        self.get_application().set_menubar(self._application_menu())
        # Canvas uses sibling AppKit islands; the photo owns its toolbar.
        self.tool_pane = Gtk.Box()
        self.tool_pane.append(self._build_tools())
        self.secondary_pane = Island()
        self.secondary_pane.set_size_request(240, -1)
        self.secondary_pane.set_hexpand(False)
        self.secondary_pane.append(self._build_secondary())
        self.canvas_pane = Island()
        self.canvas_pane.set_hexpand(True)
        self.canvas_pane.append(self._build_canvas())
        self.inspector_pane = Island()
        self.inspector_pane.set_size_request(308, -1)
        self.inspector_pane.set_hexpand(False)
        self.inspector_pane.append(self._build_inspector())
        self.workspace_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9, vexpand=True)
        for pane in (self.secondary_pane, self.canvas_pane, self.inspector_pane):
            self.workspace_row.append(pane)
        self.wide_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, vexpand=True)
        self.wide_box.append(self.workspace_row)
        self.filmstrip_pane = Island()
        self.filmstrip_pane.set_vexpand(False)
        self.filmstrip_pane.append(self._build_filmstrip())
        # The gallery belongs to the window, including the compact Edit page.
        self.secondary_pane.set_visible(False)
        self.compact_stack = Adw.ViewStack(vexpand=True)
        compact = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        compact.append(self.compact_stack)
        switcher = Adw.ViewSwitcherBar(stack=self.compact_stack)
        switcher.set_reveal(True)
        compact.append(switcher)
        self.layout = Gtk.Stack(vexpand=True, hhomogeneous=False, vhomogeneous=False)
        self.layout.add_named(self.wide_box, "wide")
        self.layout.add_named(compact, "compact")
        root.append(self.layout)
        root.append(self.filmstrip_pane)

    def _photo_toolbar(self) -> Gtk.Widget:
        bar = Toolbar()
        bar.add_css_class("darkroom-photo-toolbar")
        self.command_bar = bar
        bar.append(icon_button("document-open-symbolic", "Open image (Ctrl+O)", "win.open"))
        self.image_title = Gtk.Label(label="Darkroom", xalign=0, hexpand=True)
        self.image_title.set_ellipsize(3)
        self.image_title.set_width_chars(4)
        self.image_title.add_css_class("heading")
        self.image_subtitle = Gtk.Label()
        bar.append(self.image_title)
        self.undo_group = ConnectedButtonGroup(compact=True)
        self.undo_group.append(icon_button("edit-undo-symbolic", "Undo (Ctrl+Z)", "win.undo"))
        self.undo_group.append(icon_button("edit-redo-symbolic", "Redo (Ctrl+Shift+Z)", "win.redo"))
        bar.append(self.undo_group)
        self.compare_button = icon_button("view-dual-symbolic", "Compare with original (\\)", "win.before-after")
        bar.append(self.compare_button)
        self.zoom_label = Gtk.Label(label="Fit")
        self.zoom_menu = Gtk.MenuButton()
        self.zoom_menu.set_child(self.zoom_label)
        self.zoom_menu.set_tooltip_text("Zoom")
        zoom = Gio.Menu()
        for label, action in (("Fit photo", "fit"), ("100% · Actual pixels", "actual-pixels"), ("Zoom in", "zoom-in"), ("Zoom out", "zoom-out")):
            zoom.append(label, f"win.{action}")
        self.zoom_menu.set_menu_model(zoom)
        bar.append(self.zoom_menu)
        self.export_button = Gtk.Button(label="Export…", action_name="win.export")
        bar.append(self.export_button)
        bar.append(icon_button("document-properties-symbolic", "Show editing panel", "win.inspector"))
        return bar

    def _build_tools(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(5)
        box.set_margin_end(5)
        tools = (
            ("select", "object-select-symbolic", "Select and transform", "V"),
            ("crop", "luma-darkroom-crop-symbolic", "Crop", "C"),
            ("heal", "luma-darkroom-heal-symbolic", "Healing", "J"),
            ("clone", "edit-copy-symbolic", "Clone", "S"),
            ("brush-mask", "luma-darkroom-brush-symbolic", "Brush mask", "B"),
            ("linear-gradient", "luma-darkroom-linear-gradient-symbolic", "Linear gradient mask", "G"),
            ("radial-gradient", "media-record-symbolic", "Radial gradient mask", "Shift+G"),
            ("hand", "pan-down-symbolic", "Hand", "H"),
            ("zoom", "zoom-in-symbolic", "Zoom", "Z"),
            ("eyedropper", "color-select-symbolic", "Eyedropper", "I"),
        )
        first = None
        for key, icon, label, shortcut in tools:
            button = Gtk.ToggleButton()
            button.set_child(Gtk.Image.new_from_icon_name(icon))
            button.set_tooltip_text(f"{label} ({shortcut})")
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", self._tool_toggled, key)
            self._tool_buttons[key] = button
            box.append(button)
        scroll.set_child(box)
        return scroll

    def _build_compact_tools(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.add_css_class("darkroom-compact-tools")
        for key in ("select", "crop", "heal", "brush-mask", "linear-gradient", "hand", "zoom"):
            source = self._tool_buttons[key]
            button = Gtk.Button()
            image = source.get_child()
            icon = image.get_icon_name() if image else "image-x-generic-symbolic"
            button.set_child(Gtk.Image.new_from_icon_name(icon))
            button.set_tooltip_text(source.get_tooltip_text())
            button.update_property([Gtk.AccessibleProperty.LABEL], [source.get_accessible_role().value_nick if False else key.replace("-", " ")])
            button.connect("clicked", lambda _button, tool=key: self._select_tool(tool))
            box.append(button)
        scroll.set_child(box)
        return scroll

    def _build_secondary(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tabs.set_margin_start(6); tabs.set_margin_end(6); tabs.set_margin_top(6)
        self.secondary_stack = Gtk.Stack()
        self.secondary_stack.set_vexpand(True)
        self.layers_list = Gtk.ListBox()
        self.layers_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.layers_list.connect("row-selected", self._layer_selected)
        layer_scroll = Gtk.ScrolledWindow(); layer_scroll.set_child(self.layers_list)
        self.history_list = Gtk.ListBox(); self.history_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        history_scroll = Gtk.ScrolledWindow(); history_scroll.set_child(self.history_list)
        self.images_list = Gtk.ListBox(); self.images_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        images_scroll = Gtk.ScrolledWindow(); images_scroll.set_child(self.images_list)
        for name, title, child in (("layers", "Layers", layer_scroll), ("history", "History", history_scroll), ("images", "Images", images_scroll)):
            self.secondary_stack.add_named(child, name)
            button = Gtk.Button(label=title)
            button.set_hexpand(True)
            button.connect("clicked", lambda _b, page=name: self.secondary_stack.set_visible_child_name(page))
            tabs.append(button)
        box.append(tabs); box.append(self.secondary_stack)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        actions.add_css_class("darkroom-panel-actions")
        for icon, label, action in (("list-add-symbolic", "Add layer", "win.add-layer"), ("go-up-symbolic", "Move layer up", "win.layer-up"), ("go-down-symbolic", "Move layer down", "win.layer-down"), ("user-trash-symbolic", "Delete layer", "win.delete-layer")):
            actions.append(icon_button(icon, label, action))
        box.append(actions)
        return box

    def _build_canvas(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._photo_toolbar())
        self.open_notice = Adw.Banner(title="", revealed=False, button_label="Open another photo")
        self.open_notice.connect("button-clicked", self._open_notice_action)
        box.append(self.open_notice)
        self.context_controls = Toolbar()
        box.append(self.context_controls)
        self.canvas_overlay = Gtk.Overlay()
        self.canvas_overlay.set_vexpand(True)
        self.canvas_overlay.add_css_class("darkroom-canvas-dark")
        self.comparison = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.original_picture = Gtk.Picture()
        self.edited_picture = Gtk.Picture()
        self.comparison_edited_picture = Gtk.Picture()
        for picture in (self.original_picture, self.edited_picture, self.comparison_edited_picture):
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_can_shrink(True)
        for picture, title, side in ((self.original_picture, "Original", "start"), (self.comparison_edited_picture, "Edited", "end")):
            frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            label = Gtk.Label(label=title)
            label.add_css_class("darkroom-comparison-label")
            frame.append(label)
            picture.set_vexpand(True)
            frame.append(picture)
            getattr(self.comparison, f"set_{side}_child")(frame)
        self.comparison.set_resize_start_child(True)
        self.comparison.set_resize_end_child(True)
        self.canvas_stack = Gtk.Stack()
        self.canvas_stack.add_named(self.edited_picture, "edited")
        self.canvas_stack.add_named(self.comparison, "compare")
        self.canvas_scroller = Gtk.ScrolledWindow()
        self.canvas_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.canvas_scroller.set_child(self.canvas_stack)
        self.canvas_overlay.set_child(self.canvas_scroller)
        self.crop_overlay = Gtk.DrawingArea()
        self.crop_overlay.set_draw_func(self._draw_crop)
        self.crop_overlay.set_can_target(False)
        self.canvas_overlay.add_overlay(self.crop_overlay)
        crop_drag = Gtk.GestureDrag.new()
        crop_drag.connect("drag-begin", self._crop_drag_begin)
        crop_drag.connect("drag-update", self._crop_drag_update)
        crop_drag.connect("drag-end", self._crop_drag_end)
        self.crop_overlay.add_controller(crop_drag)
        self.canvas_empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.canvas_empty.add_css_class("darkroom-photo-empty")
        self.canvas_empty.set_halign(Gtk.Align.CENTER); self.canvas_empty.set_valign(Gtk.Align.CENTER)
        empty_icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic"); empty_icon.set_pixel_size(48)
        self.canvas_empty.append(empty_icon)
        empty_title = Gtk.Label(label="Make the photograph yours", wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=22); empty_title.add_css_class("title-2")
        self.canvas_empty.append(empty_title)
        hint = Gtk.Label(label="Light, color, and a fresh perspective.\nOpen or drop a photo to begin.")
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_wrap(True)
        self.canvas_empty.append(hint)
        open_button = Gtk.Button(label="Open Image…"); open_button.set_action_name("win.open"); open_button.add_css_class("suggested-action")
        self.canvas_empty.append(open_button)
        safe = Gtk.Label(label="Your original stays untouched.", wrap=True, css_classes=["dim-label"])
        self.canvas_empty.append(safe)
        self.canvas_overlay.add_overlay(self.canvas_empty)
        self.render_spinner = Gtk.Spinner(); self.render_spinner.set_halign(Gtk.Align.CENTER); self.render_spinner.set_valign(Gtk.Align.CENTER)
        self.canvas_overlay.add_overlay(self.render_spinner)
        box.append(self.canvas_overlay)

        # Canvas keeps zoom in the photo toolbar, not a second bottom strip.
        # Retain metadata labels as state for consumers; Info owns their display.
        self.color_status = Gtk.Label(label="")
        self.pixel_status = Gtk.Label(label="")

        click = Gtk.GestureClick.new()
        click.set_button(0)
        click.connect("pressed", self._canvas_pressed)
        self.canvas_overlay.add_controller(click)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._file_drop)
        self.canvas_overlay.add_controller(drop)
        zoom = Gtk.GestureZoom.new()
        zoom.connect("begin", lambda *_: setattr(self, "_pinch_zoom_start", self.document.workspace.zoom if self.document else 1.0))
        zoom.connect("scale-changed", lambda _gesture, scale: self._set_zoom(getattr(self, "_pinch_zoom_start", 1.0) * scale))
        self.canvas_scroller.add_controller(zoom)
        return box

    def _build_inspector(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("darkroom-inspector")
        tabs = Toolbar()
        tabs.add_css_class("darkroom-inspector-tabs")
        group = ConnectedButtonGroup(compact=False)
        group.set_hexpand(True)
        group.set_homogeneous(True)
        self.inspector_stack = Gtk.Stack(vexpand=True, hhomogeneous=False, vhomogeneous=False)
        self._page_buttons = {}
        first = None
        for name, title in (("adjust", "Adjust"), ("crop", "Crop"), ("retouch", "Retouch"), ("history", "History")):
            button = Gtk.ToggleButton(label=title, hexpand=True)
            if first is None: first = button
            else: button.set_group(first)
            button.connect("toggled", lambda button, page=name: self._show_page(page) if button.get_active() else None)
            group.append(button)
            self._page_buttons[name] = button
        tabs.append(group)
        panel.append(tabs)
        panel.append(self.inspector_stack)
        adjust = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        histogram_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        histogram_content.add_css_class("darkroom-photo-analysis")
        histogram_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        label = Gtk.Label(label="Histogram", xalign=0, hexpand=True)
        label.add_css_class("darkroom-section-caption")
        histogram_header.append(label)
        clipping = Gtk.ToggleButton(icon_name="dialog-warning-symbolic")
        clipping.add_css_class("flat")
        clipping.add_css_class("darkroom-small-control")
        clipping.set_tooltip_text("Highlight histogram clipping")
        clipping.connect("toggled", self._clipping_changed)
        histogram_header.append(clipping)
        histogram_content.append(histogram_header)
        self.histogram = LumaUI.Histogram.new()
        self.histogram.set_size_request(-1, 96)
        self.histogram.set_content_height(96)
        histogram_content.append(self.histogram)
        adjust.append(histogram_content)
        self.missing_notice = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        notice = Gtk.Label(label="Original missing. Relink the photo to continue.", wrap=True, xalign=0)
        self.missing_notice.append(notice)
        self.missing_notice.append(Gtk.Button(label="Relink…", action_name="win.relink"))
        adjust.append(self.missing_notice)
        looks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for name, recipe in (("Natural", {}), ("Warm", {"temperature": 18, "vibrance": 12}), ("Cool", {"temperature": -18, "vibrance": 8}), ("Vivid", {"contrast": 12, "vibrance": 28}), ("Soft", {"contrast": -15, "saturation": -10}), ("Monochrome", {"black-and-white": True, "contrast": 18})):
            button = Gtk.Button(label=name)
            button.connect("clicked", lambda _b, title=name, values=recipe: self._apply_look(title, values))
            looks.append(button)
        adjust.append(self._section("Looks", looks))
        self.bw_button = Gtk.CheckButton(label="Black & white")
        self.bw_button.connect("toggled", self._black_white_changed)
        looks.append(self.bw_button)
        self.adjustment_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        groups = (
            ("Light", (("exposure", "Exposure", -5, 5, .05), ("contrast", "Contrast", -100, 100, 1),
                       ("highlights", "Highlights", -100, 100, 1), ("shadows", "Shadows", -100, 100, 1),
                       ("whites", "Whites", -100, 100, 1), ("blacks", "Blacks", -100, 100, 1))),
            ("Color", (("temperature", "Warmth", -100, 100, 1), ("tint", "Tint", -100, 100, 1),
                       ("vibrance", "Vibrance", -100, 100, 1), ("saturation", "Saturation", -100, 100, 1))),
            ("Detail", (("texture", "Texture", -100, 100, 1), ("clarity", "Clarity", -100, 100, 1),
                        ("sharpening", "Sharpening", 0, 100, 1),
                        ("noise-reduction", "Noise reduction", 0, 100, 1))),
        )
        for title, controls in groups:
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for args in controls: content.append(self._adjustment_row(*args))
            self.adjustment_box.append(self._section(title, content, expanded=title == "Light"))
        adjust.append(self.adjustment_box)
        self.curves = LumaUI.CurveEditor.new()
        self.curves.connect("changed", self._curve_changed)
        adjust.append(self._section("Tone curve", self.curves))
        reset = Gtk.Button(label="Reset adjustments", action_name="win.reset-adjustments")
        reset.set_margin_start(12); reset.set_margin_end(12); reset.set_margin_top(12); reset.set_margin_bottom(12)
        adjust.append(reset)
        self.crop_controls = self._build_crop_controls()
        history = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        history.append(Gtk.Label(label="Your editing steps", xalign=0, css_classes=["heading"]))
        history.append(Gtk.Label(label="Undo and redo let you retrace your edits.\nYour original photo stays untouched.", wrap=True, xalign=0, css_classes=["dim-label"]))
        buttons = ConnectedButtonGroup()
        buttons.append(Gtk.Button(label="Undo", action_name="win.undo"))
        buttons.append(Gtk.Button(label="Redo", action_name="win.redo"))
        history.append(buttons)
        # This list has one parent; the older Layers panel remains optional.
        self.edit_history = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        history.append(self.edit_history)
        self.metadata_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.layer_controls = self._build_layer_controls()
        history.append(self._section("Photo information", self.metadata_box))
        retouch = self._build_retouch()
        for name, content in (("adjust", adjust), ("crop", self.crop_controls), ("history", history), ("retouch", retouch)):
            if name != "adjust":
                for side in ("top", "bottom", "start", "end"): getattr(content, f"set_margin_{side}")(12)
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(content)
            self.inspector_stack.add_named(scroll, name)
        self._page_buttons["adjust"].set_active(True)
        return panel

    def _section(self, title: str, child: Gtk.Widget, expanded: bool = False) -> Gtk.Widget:
        section = Gtk.Expander(label=title, expanded=expanded)
        section.add_css_class("darkroom-adjustment-section")
        child.set_margin_top(8)
        section.set_child(child)
        section.connect("notify::expanded", lambda widget, _p: widget.remove_css_class("collapsed") if widget.get_expanded() else widget.add_css_class("collapsed"))
        if not expanded: section.add_css_class("collapsed")
        return section

    def _show_page(self, page: str) -> None:
        self.inspector_stack.set_visible_child_name(page)
        if self.document:
            if page == "crop": self._select_tool("crop")
            elif page == "retouch": self._select_tool(next((key for key, button in self._retouch_buttons.items() if button.get_active()), "heal"))
            else: self._select_tool("select")

    def _adjustment_row(self, key: str, label_text: str, minimum: float, maximum: float, step: float) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("darkroom-adjustment-row")
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=label_text, xalign=0, hexpand=True)
        label.add_css_class("darkroom-field-label")
        value = NumericField("", minimum=minimum, maximum=maximum, step=step, digits=2 if key == "exposure" else 0)
        value.add_css_class("darkroom-adjustment-value")
        value.set_hexpand(False)
        value.entry.set_width_chars(5)
        value.entry.set_max_width_chars(6)
        value.entry.set_alignment(1)
        value.set_size_request(56, -1)
        value.entry.update_property([Gtk.AccessibleProperty.LABEL], [label_text])
        value.connect("value-changed", lambda field, number: self._adjustment_scales[key].set_value(number))
        self._adjustment_values[key] = value
        reset = icon_button("edit-undo-symbolic", f"Reset {label_text}")
        reset.add_css_class("flat")
        reset.add_css_class("darkroom-small-control")
        reset.connect("clicked", lambda *_: self._reset_adjustment(key))
        heading.append(label); heading.append(value); heading.append(reset)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, step)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_has_origin(False)
        scale.update_property([Gtk.AccessibleProperty.LABEL], [label_text])
        if key in {"temperature", "tint"}: scale.set_tooltip_text("Relative to the photo’s original white balance")
        scale.connect("value-changed", self._adjustment_changed, key)
        gesture = Gtk.GestureClick.new()
        gesture.connect("released", lambda *_: self.editor and self.editor.end_continuous_edit())
        scale.add_controller(gesture)
        self._adjustment_scales[key] = scale
        setattr(self, f"adjustment_{key.replace('-', '_')}", scale)
        box.append(heading); box.append(scale)
        return box

    def _build_crop_controls(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(Gtk.Label(label="Find your frame", xalign=0, css_classes=["heading"]))
        box.append(Gtk.Label(label="Drag an edge or corner on the photo. Drag inside the frame to reposition it.", wrap=True, xalign=0, css_classes=["dim-label"]))
        self.crop_spins = {}
        box.append(Gtk.Label(label="Frame preset", xalign=0))
        ratios = Gtk.DropDown.new_from_strings(["Free", "Original", "Square", "4 : 3", "3 : 2", "16 : 9", "4 : 5", "9 : 16"])
        ratios.update_property([Gtk.AccessibleProperty.LABEL], ["Crop aspect ratio"])
        ratios.connect("notify::selected", self._crop_ratio_changed)
        self.crop_ratios = ratios
        box.append(ratios)
        box.append(Gtk.Label(label="Straighten", xalign=0))
        spin = Gtk.SpinButton.new_with_range(-45, 45, .1)
        spin.set_digits(1)
        spin.update_property([Gtk.AccessibleProperty.LABEL], ["Straighten in degrees"])
        spin.connect("value-changed", self._crop_changed, "straighten")
        self.crop_spins["straighten"] = spin
        box.append(spin)
        rotate = ConnectedButtonGroup()
        for title, operation in (("Rotate left", "rotate"), ("Flip", "flip")):
            button = Gtk.Button(label=title)
            button.connect("clicked", lambda _b, op=operation: self._transform_crop(op))
            rotate.append(button)
        box.append(rotate)
        box.append(Gtk.Button(label="Reset crop", action_name="win.reset-crop"))
        done = Gtk.Button(label="Done")
        done.connect("clicked", lambda *_: self._page_buttons["adjust"].set_active(True))
        box.append(done)
        return box

    def _apply_look(self, name: str, recipe: dict) -> None:
        if not self.editor: return
        from .model import Adjustment, new_id
        keys = {"temperature", "vibrance", "contrast", "saturation", "black-and-white"}
        def apply():
            self.document.raw_development[:] = [a for a in self.document.raw_development if a.kind not in keys]
            for key, value in recipe.items():
                self.document.raw_development.append(Adjustment(new_id("adjustment"), key, value))
        self.editor.change(f"Apply {name.lower()} look", apply)
        self._mark_changed()

    def _build_retouch(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(Gtk.Label(label="Small touches", xalign=0, css_classes=["heading"]))
        box.append(Gtk.Label(label="Heal a spot, or copy a small detail from another part of the photo.", wrap=True, xalign=0, css_classes=["dim-label"]))
        group = ConnectedButtonGroup()
        first = None
        for key, title in (("heal", "Heal"), ("clone", "Clone")):
            button = Gtk.ToggleButton(label=title)
            if first is None: first = button
            else: button.set_group(first)
            button.connect("toggled", lambda button, tool=key: self._select_tool(tool) if button.get_active() else None)
            self._retouch_buttons[key] = button
            group.append(button)
        first.set_active(True)
        box.append(group)
        self.brush_size = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 4, 160, 1)
        self.brush_size.set_value(32)
        self.brush_size.set_draw_value(True)
        self.brush_size.update_property([Gtk.AccessibleProperty.LABEL], ["Retouch brush size"])
        box.append(Gtk.Label(label="Brush size", xalign=0))
        box.append(self.brush_size)
        box.append(Gtk.Label(label="Heal: click a spot to blend it with its surroundings.\n\nClone: click the source, then the spot to replace.\n\nUse Undo to remove a touch.", wrap=True, xalign=0, css_classes=["dim-label"]))
        box.append(Gtk.Button(label="Undo last edit", action_name="win.undo"))
        return box

    def _black_white_changed(self, button) -> None:
        if self._updating or not self.editor: return
        self.editor.set_adjustment("black-and-white", button.get_active())
        self._mark_changed()

    def _crop_ratio_changed(self, dropdown, _param) -> None:
        if self._updating or not self.editor or self._edited_preview is None: return
        index = dropdown.get_selected()
        if index == 0: return
        image_ratio = self._edited_preview.width / self._edited_preview.height
        ratio = [None, image_ratio, 1, 4/3, 3/2, 16/9, 4/5, 9/16][index] / image_ratio
        crop = copy.deepcopy(self.document.crop)
        width, height = (1.0, 1.0 / ratio) if ratio > 1 else (ratio, 1.0)
        crop.left, crop.right = (1-width)/2, (1+width)/2
        crop.top, crop.bottom = (1-height)/2, (1+height)/2
        self.editor.set_crop(crop, name="Change crop ratio")
        self._mark_changed()
        self.crop_overlay.queue_draw()

    def _transform_crop(self, operation: str) -> None:
        if not self.editor: return
        crop = copy.deepcopy(self.document.crop)
        if operation == "rotate": crop.rotation = (crop.rotation + 90) % 360
        else: crop.flip_horizontal = not crop.flip_horizontal
        self.editor.set_crop(crop, name="Rotate photo" if operation == "rotate" else "Flip photo")
        self._page_buttons["adjust"].set_active(True)
        self._mark_changed()

    def _action_reset_adjustments(self, *_args) -> None:
        if self.editor:
            self.editor.change("Reset adjustments", lambda: self.document.raw_development.clear())
            self._mark_changed()

    def _build_layer_controls(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.blend = Gtk.DropDown.new_from_strings(["Normal", "Multiply", "Screen", "Overlay", "Soft Light", "Hard Light", "Darken", "Lighten", "Difference", "Color", "Luminosity"])
        self.blend.connect("notify::selected", self._blend_changed); box.append(self.blend)
        self.opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1); self.opacity.set_value(100); self.opacity.set_draw_value(True)
        self.opacity.connect("value-changed", self._opacity_changed); box.append(self.opacity)
        masks = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        brush = Gtk.Button(label="Brush Mask"); brush.connect("clicked", lambda *_: self._add_mask("brush")); masks.append(brush)
        gradient = Gtk.Button(label="Radial Mask"); gradient.connect("clicked", lambda *_: self._add_mask("radial-gradient")); masks.append(gradient)
        box.append(masks)
        return box

    def _build_filmstrip(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Toolbar()
        self.gallery_toggle = Gtk.ToggleButton(label="Gallery", active=False)
        self.gallery_toggle.set_tooltip_text("Show or hide gallery (G)")
        self.gallery_toggle.connect("toggled", self._gallery_toggled)
        bar.append(self.gallery_toggle)
        self.gallery_count = Gtk.Label(label="No photos", xalign=0, hexpand=True)
        self.gallery_count.set_ellipsize(3)
        self.gallery_count.set_width_chars(1)
        self.gallery_count.add_css_class("darkroom-meta")
        bar.append(self.gallery_count)
        bar.append(icon_button("list-add-symbolic", "Add photos", "win.open"))
        bar.append(icon_button("folder-open-symbolic", "Browse a folder", "win.open-folder"))
        self.gallery_previous = icon_button("go-previous-symbolic", "Previous gallery page")
        self.gallery_next = icon_button("go-next-symbolic", "Next gallery page")
        self.gallery_previous.connect("clicked", lambda *_: self._gallery_page_changed(-1))
        self.gallery_next.connect("clicked", lambda *_: self._gallery_page_changed(1))
        bar.append(self.gallery_previous); bar.append(self.gallery_next)
        box.append(bar)
        self.gallery_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.gallery_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=170, max_content_height=220)
        self.filmstrip = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2, max_children_per_line=12, column_spacing=6, row_spacing=6)
        for side in ("start", "end", "top", "bottom"): getattr(self.filmstrip, "set_margin_" + side)(8)
        self.gallery_scroll.set_child(self.filmstrip)
        self.gallery_revealer.set_child(self.gallery_scroll)
        box.append(self.gallery_revealer)
        self._gallery_page = 0
        self._gallery_signature = None
        self._gallery_generation = 0
        self._gallery_worker_running = False
        self._gallery_pictures = {}
        self._gallery_placeholders = {}
        return box

    def _gallery_toggled(self, button):
        self.gallery_revealer.set_reveal_child(button.get_active())
        if button.get_active(): self._queue_gallery_thumbnail()

    def _open_gallery_photo(self, path):
        self.open_path(path, as_document=path.name.endswith(DOCUMENT_SUFFIX))
        if self._compact: self.compact_stack.set_visible_child_name("image")

    def _gallery_page_changed(self, delta):
        self._gallery_page = max(0, min((len(self._filmstrip_paths) - 1) // 24, self._gallery_page + delta))
        self._refresh_filmstrip()
        self.gallery_scroll.get_vadjustment().set_value(0)

    def _action_gallery(self, *_args):
        self.gallery_toggle.set_active(not self.gallery_toggle.get_active())

    def _action_open_folder(self, *_args):
        dialog = Gtk.FileDialog(title="Browse photo folder")
        dialog.select_folder(self, None, self._folder_finished)

    def _folder_finished(self, dialog, result):
        try: folder = dialog.select_folder_finish(result)
        except GLib.Error: return
        if folder.get_path(): self._browse_folder(Path(folder.get_path()))

    def _browse_folder(self, folder):
        # Read only this folder, never recursively scan the user's library.
        self._gallery_generation += 1
        generation = self._gallery_generation
        def worker():
            try:
                paths = sorted((p.resolve() for p in folder.iterdir() if p.is_file() and (p.suffix.lower() in IMAGE_SUFFIXES or p.name.endswith(DOCUMENT_SUFFIX))), key=lambda p: p.name.casefold())
                error = None
            except OSError as exc: paths, error = [], str(exc)
            GLib.idle_add(ready, paths, error)
        def ready(paths, error):
            if self._closing or generation != self._gallery_generation: return False
            if error: self._toast("Couldn’t read this folder: " + error); return False
            self._thumbnail_cache.clear()
            self._gallery_signature = None
            self._filmstrip_paths = paths
            self._gallery_page = 0
            self._refresh_filmstrip()
            self.gallery_toggle.set_active(True)
            if not paths: self._toast("No supported photos in this folder")
            return False
        threading.Thread(target=worker, name="darkroom-gallery-scan", daemon=True).start()

    def _build_actions(self) -> None:
        callbacks: dict[str, Callable] = {
            "gallery": self._action_gallery, "open-folder": self._action_open_folder, "open": self._action_open, "open-photos": self._action_open_photos, "save": self._action_save,
            "save-as": self._action_save_as, "save-version": self._action_save_version, "export": self._action_export,
            "undo": self._action_undo, "redo": self._action_redo, "before-after": self._action_before_after,
            "inspector": self._action_inspector, "panels": self._action_panels, "fit": lambda *_: self._fit_image(),
            "actual-pixels": lambda *_: self._set_zoom(1.0), "zoom-in": lambda *_: self._zoom(1.25),
            "zoom-out": lambda *_: self._zoom(.8), "add-layer": self._action_add_layer,
            "delete-layer": self._action_delete_layer, "layer-up": lambda *_: self._move_layer(-1),
            "layer-down": lambda *_: self._move_layer(1), "reset-crop": self._action_reset_crop, "reset-adjustments": self._action_reset_adjustments,
            "relink": self._action_relink, "snapshot": self._action_snapshot, "show-filer": self._action_show_filer,
            "shortcuts": self._action_shortcuts, "fullscreen": self._action_fullscreen,
            "layers": self._action_layers,
        }
        self.actions: dict[str, Gio.SimpleAction] = {}
        for name, callback in callbacks.items():
            action = Gio.SimpleAction.new(name, None); action.connect("activate", callback); self.add_action(action); self.actions[name] = action
        appearance = Gio.SimpleAction.new_stateful("appearance", GLib.VariantType.new("s"), GLib.Variant.new_string("system"))
        appearance.connect("activate", self._action_appearance); self.add_action(appearance); self.actions["appearance"] = appearance
        app = self.get_application()
        accelerators = {
            "gallery": ["g"], "open-folder": ["<Control><Shift>o"], "open": ["<Control>o"], "save": ["<Control>s"], "save-as": ["<Control><Shift>s"], "export": ["<Control>e"],
            "undo": ["<Control>z"], "redo": ["<Control><Shift>z"], "zoom-in": ["<Control>plus"], "zoom-out": ["<Control>minus"],
            "fit": ["0"], "actual-pixels": ["1"], "before-after": ["backslash"], "panels": ["<Control>backslash"], "fullscreen": ["f"],
        }
        for name, keys in accelerators.items(): app.set_accels_for_action(f"win.{name}", keys)
        key = Gtk.EventControllerKey.new(); key.connect("key-pressed", self._key_pressed); self.add_controller(key)

    def _application_menu(self) -> Gio.MenuModel:
        menu = Gio.Menu()
        quick = Gio.Menu(); quick.append("Add Photos…", "win.open"); quick.append("Browse Folder…", "win.open-folder"); quick.append("Gallery", "win.gallery"); quick.append("Open from Photos", "win.open-photos"); quick.append("Export…", "win.export"); menu.append_section(None, quick)
        file = Gio.Menu()
        for label, action in (("Save", "win.save"), ("Save As…", "win.save-as"), ("Save Version", "win.save-version"), ("Export…", "win.export"), ("Show Source in Filer", "win.show-filer")):
            file.append(label, action)
        menu.append_section("File", file)
        edit = Gio.Menu(); edit.append("Undo", "win.undo"); edit.append("Redo", "win.redo"); edit.append("Create Snapshot", "win.snapshot"); menu.append_section("Edit", edit)
        view = Gio.Menu(); view.append("Before and After", "win.before-after"); view.append("Inspector", "win.inspector"); view.append("Hide or Restore Panels", "win.panels"); view.append("Full-screen Image", "win.fullscreen"); view.append("Advanced Layers…", "win.layers"); menu.append_section("View", view)
        app = Gio.Menu(); app.append("Keyboard Shortcuts", "win.shortcuts"); app.append("About Darkroom", "app.about"); app.append("Quit Darkroom", "app.quit"); menu.append_section(None, app)
        return menu

    def _load_style(self) -> None:
        style = Path(os.environ.get("LUMA_DARKROOM_STYLE_PATH", "/usr/share/luma-darkroom/darkroom.css"))
        if not style.is_file(): style = Path(__file__).parents[1] / "style" / "darkroom.css"
        if style.is_file(): add_style_sheet(str(style))
        # Darkroom's own tool glyphs are installed into hicolor beside the
        # package's prefix; run from the source tree, they are found beside it.
        source_icons = Path(__file__).parents[1] / "data" / "icons"
        if (source_icons / "hicolor").is_dir():
            theme = Gtk.IconTheme.get_for_display(self.get_display())
            if str(source_icons) not in theme.get_search_path():
                theme.add_search_path(str(source_icons))

    def open_path(self, path: Path, *, as_document: bool = False) -> None:
        path = path.resolve()
        if self._settle_source:
            GLib.source_remove(self._settle_source); self._settle_source = 0
        self._interactive = False
        if self._current_path == path: return
        self.open_notice.set_revealed(False)
        try:
            session = self._sessions.get(path)
            if session:
                document, editor, document_path, dirty = session
            else:
                document = DocumentStore.load(path) if as_document else Document.new(path.as_uri(), raw=path.suffix.lower() in RAW_SUFFIXES)
                if not as_document and path.suffix.lower() not in IMAGE_SUFFIXES:
                    raise DocumentError(f"{path.name} is not a supported image")
                editor, document_path, dirty = Editor(document), path if as_document else None, False
        except (DocumentError, OSError) as error:
            self._error("Couldn’t open image", str(error)); return
        if self.document and self._current_path:
            self._autosave()
            self.editor.end_continuous_edit()
            self._sessions[self._current_path] = (self.document, self.editor, self.document_path, self.dirty)
        self._previous_path = self._current_path if self._edited_preview is not None else None
        self._opening_path = path
        self._shown_original = None
        self.document, self.editor, self.document_path, self.dirty = document, editor, document_path, dirty
        self._current_path = path
        self._edited_preview = None
        self._source_size = (0, 0)
        self._preview_scale = 1.0
        self._fit_image()
        self.canvas_stack.set_visible_child_name("edited")
        for picture in (self.edited_picture, self.original_picture, self.comparison_edited_picture): picture.set_paintable(None)
        if not self._filmstrip_paths and not as_document: self._browse_folder(path.parent)
        if path not in self._filmstrip_paths: self._filmstrip_paths.append(path)
        self.document.workspace.active_tool = "select"
        self._page_buttons["adjust"].set_active(True)
        self._refresh_all(); self._schedule_render()

    def _schedule_render(self) -> None:
        self._render_generation += 1; generation = self._render_generation
        if not self.document or self.document.source.missing:
            self.render_spinner.stop(); self.render_spinner.set_visible(False)
            self._refresh_all(); return
        if self._render_pending: return
        self._render_pending = True
        if self._edited_preview is None:
            self.render_spinner.start(); self.render_spinner.set_visible(True)
        self._render_document = self.document
        interactive = self._interactive
        self._render_interactive = interactive
        document = self.document.clone()
        maximum = 1800 if self._fit_mode else None

        def worker() -> None:
            try:
                engine = RasterEngine(document)
                # The crop tool frames the whole photo, as Lightroom does; the
                # crop applies again when another tool is chosen.
                framing = document.workspace.active_tool == "crop"
                source_path = file_path(document.source.uri)
                stat = source_path.stat()
                key = (str(source_path), stat.st_mtime_ns, stat.st_size, maximum)
                if key != self._prepared_key:
                    self._prepared_source = engine.open_source(maximum)
                    self._prepared_size = (document.source.width, document.source.height)
                    small = self._prepared_source[0].copy()
                    small.thumbnail((960, 960))
                    self._prepared_small = (small, self._prepared_source[1])
                    self._prepared_original = engine.render(original=True, prepared=self._prepared_source)
                    self._prepared_key = key
                document.source.width, document.source.height = self._prepared_size
                prepared = self._prepared_small if interactive else self._prepared_source
                edited = engine.render(crop=not framing, prepared=prepared)
                original = self._prepared_original
                result = (edited, original, None, self._prepared_size, prepared[0].width / self._prepared_size[0])
            except Exception as error:
                result = (None, None, str(error), (0, 0), 1.0)
            GLib.idle_add(self._render_ready, generation, result)
        threading.Thread(target=worker, name="darkroom-preview", daemon=True).start()

    def _render_ready(self, generation: int, result) -> bool:
        self._render_pending = False
        if self._closing: return GLib.SOURCE_REMOVE
        newer = generation != self._render_generation
        if self._render_document is not self.document:
            self._schedule_render(); return GLib.SOURCE_REMOVE
        # Present completed frames from this photo while a drag continues;
        # only the latest requested recipe is queued, never a backlog.
        edited, original, error, source_size, preview_scale = result
        self.render_spinner.stop(); self.render_spinner.set_visible(False)
        if error:
            if newer:
                self._schedule_render(); return GLib.SOURCE_REMOVE
            failed = getattr(self, "_opening_path", None)
            if failed:
                previous = getattr(self, "_previous_path", None)
                self.document = self.editor = self.document_path = self._current_path = None
                self.dirty = False
                self._sessions.pop(failed, None)
                if failed in self._filmstrip_paths: self._filmstrip_paths.remove(failed)
                if previous: self.open_path(previous, as_document=previous.name.endswith(DOCUMENT_SUFFIX))
                else: self._refresh_all()
                self.open_notice.set_title(f"Couldn’t open {failed.name}. Try another copy of the photo.")
            else:
                self.open_notice.set_title("This edit couldn’t be displayed. Undo it or open another photo.")
            self.open_notice.set_button_label("Dismiss" if self.document else "Open another photo")
            self.open_notice.set_revealed(True)
            self._opening_path = None
            return GLib.SOURCE_REMOVE
        self._opening_path = None
        edited_texture = self._texture(edited.image)
        self.edited_picture.set_paintable(edited_texture); self.comparison_edited_picture.set_paintable(edited_texture)
        if getattr(self, "_shown_original", None) is not original:
            self.original_picture.set_paintable(self._texture(original.image))
            self._shown_original = original
        self._edited_preview = edited.image
        dimensions_changed = self._source_size != source_size
        self._source_size = source_size
        self.document.source.width, self.document.source.height = source_size
        if self.document.source.raw: self.document.source.bit_depth = 16
        self._preview_scale = preview_scale
        if not self._fit_mode: self._apply_zoom_size()
        if dimensions_changed: self._refresh_metadata()
        try:
            channels = GLib.Variant("aad", [[float(value) for value in channel] for channel in edited.histogram])
            self.histogram.set_channels(channels)
        except (TypeError, GLib.Error):
            pass
        self.pixel_status.set_label(f"{round(edited.image.width / self._preview_scale):,} × {round(edited.image.height / self._preview_scale):,}")
        self.color_status.set_label(f"{edited.source_profile} → {edited.output_profile}")
        if self._current_path and (not self._render_interactive or not self._thumbnail_cache.get(self._current_path)):
            thumbnail = edited.image.copy()
            thumbnail.thumbnail((120, 76))
            self._thumbnail_cache[self._current_path] = self._texture(thumbnail)
            picture = self._gallery_pictures.get(self._current_path)
            if picture:
                picture.set_paintable(self._thumbnail_cache[self._current_path])
                self._gallery_placeholders[self._current_path].set_visible(False)
        self.canvas_empty.set_visible(False)
        if newer: self._schedule_render()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _texture(image):
        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        return Gdk.MemoryTexture.new(rgba.width, rgba.height, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(rgba.tobytes()), rgba.width * 4)

    def _refresh_all(self) -> None:
        document = self.document
        available = document is not None
        for name, action in self.actions.items() if hasattr(self, "actions") else []:
            if name not in {"open", "open-folder", "gallery", "open-photos", "appearance", "shortcuts"}: action.set_enabled(available)
        self.canvas_empty.set_visible(not available)
        self.inspector_stack.set_sensitive(available)
        self.missing_notice.set_visible(bool(document and document.source.missing))
        self._refresh_context_controls()
        if not available:
            self.set_identity_subtitle("")
            self.image_title.set_label("No image open"); self.image_subtitle.set_label("Open an image to begin")
            self._refresh_layers(); self._refresh_history(); self._refresh_metadata(); self._refresh_filmstrip(); self._rebuild_semantics(); return
        identity_subtitle = document.name + (" •" if self.dirty else "")
        self.set_identity_subtitle(identity_subtitle)
        self.actions["undo"].set_enabled(self.editor.can_undo)
        self.actions["redo"].set_enabled(self.editor.can_redo)
        self.image_title.set_label(identity_subtitle)
        self.image_subtitle.set_label(document.source.display_name)
        self.missing_notice.set_visible(document.source.missing)

        self._tool_buttons.get(document.workspace.active_tool, self._tool_buttons["select"]).set_active(True)
        self.crop_overlay.set_can_target(document.workspace.active_tool == "crop")
        self.zoom_label.set_label("Fit" if self._fit_mode else f"{document.workspace.zoom * 100:.0f}%")
        self._refresh_context_controls(); self._refresh_layers(); self._refresh_history(); self._refresh_adjustments(); self._refresh_metadata(); self._refresh_filmstrip(); self._apply_arrangement()
        self._rebuild_semantics()

    def _rebuild_semantics(self) -> None:
        root = LumaSemantics.SemanticObject.new("darkroom.application", "application", "Darkroom")
        if self.document:
            document = LumaSemantics.SemanticObject.new(self.document.id, "image-document", self.document.name)
            for layer in self.document.walk_layers():
                document.add_child(LumaSemantics.SemanticObject.new(layer.id, "image-layer", layer.name))
            root.add_child(document)
        self.semantic_root = root

    def _refresh_context_controls(self) -> None:
        tool = self.document.workspace.active_tool if self.document else "select"
        self.context_controls.set_visible(tool == "crop")
        if self.context_controls.get_first_child(): return
        hint = Gtk.Label(label="Drag to reframe", hexpand=True, xalign=0)
        self.context_controls.append(hint)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", self._cancel_crop)
        self.context_controls.append(cancel)
        done = Gtk.Button(label="Done")
        done.connect("clicked", lambda *_: self._page_buttons["adjust"].set_active(True))
        self.context_controls.append(done)

    def _cancel_crop(self, *_args) -> None:
        if self.editor and self._crop_entry:
            self.editor.set_crop(self._crop_entry, name="Cancel crop")
            self._mark_changed()
        self._page_buttons["adjust"].set_active(True)

    def _refresh_layers(self) -> None:
        clear(self.layers_list)
        if not self.document: return
        for layer in reversed(self.document.layers):
            row = Gtk.ListBoxRow(); row.layer_id = layer.id
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7); content.set_margin_top(6); content.set_margin_bottom(6); content.set_margin_start(6); content.set_margin_end(6)
            visible = Gtk.CheckButton(); visible.set_active(layer.visible); visible.set_tooltip_text("Layer visibility"); visible.connect("toggled", lambda button, lid=layer.id: self._toggle_layer(lid, button.get_active())); content.append(visible)
            icon = Gtk.Image.new_from_icon_name({"adjustment": "document-properties-symbolic", "group": "folder-symbolic", "text": "insert-text-symbolic"}.get(layer.kind, "image-x-generic-symbolic")); content.append(icon)
            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); name = Gtk.Label(label=layer.name); name.set_xalign(0); name.set_ellipsize(3); names.append(name)
            detail = Gtk.Label(label=f"{layer.kind.replace('-', ' ').title()} · {layer.opacity * 100:.0f}%"); detail.add_css_class("darkroom-meta"); detail.set_xalign(0); names.append(detail); names.set_hexpand(True); content.append(names)
            if layer.masks: content.append(Gtk.Label(label=f"◐ {len(layer.masks)}"))
            if layer.locked: content.append(Gtk.Image.new_from_icon_name("changes-prevent-symbolic"))
            row.set_child(content); self.layers_list.append(row)
            if layer.id == self.document.workspace.selected_layer_id: self.layers_list.select_row(row)

    def _refresh_history(self) -> None:
        clear(self.history_list)
        clear(self.edit_history)
        if not self.document: return
        if not self.document.history:
            self.edit_history.append(Gtk.Label(label="Original photo · no edits yet", xalign=0))
        for entry in reversed(self.document.history):
            self.edit_history.append(Gtk.Label(label=entry.name, xalign=0, margin_top=8, margin_bottom=8, wrap=True))
            row = Gtk.ListBoxRow(); label = Gtk.Label(label=entry.name); label.set_xalign(0); label.set_margin_top(7); label.set_margin_bottom(7); label.set_margin_start(8); label.set_margin_end(8); row.set_child(label); self.history_list.append(row)

    def _refresh_adjustments(self) -> None:
        if not self.document: return
        self._updating = True
        for key, scale in self._adjustment_scales.items():
            adjustment = next((item for item in self.document.raw_development if item.kind == key and item.enabled), None)
            value = float(adjustment.value) if adjustment else 0
            scale.set_value(value)
            self._adjustment_values[key].set_value(value)
        self.bw_button.set_active(any(a.kind == "black-and-white" and a.enabled and a.value for a in self.document.raw_development))
        crop = self.document.crop
        for key, spin in self.crop_spins.items(): spin.set_value(float(getattr(crop, key)))
        curve = next((item for item in self.document.raw_development if item.kind == "tone-curve" and item.enabled), None)
        points = curve.value if curve else [[0.0, 0.0], [1.0, 1.0]]
        self.curves.set_points(GLib.Variant("a(dd)", [(float(x), float(y)) for x, y in points]))
        layer = self.document.layer(self.document.workspace.selected_layer_id)
        if layer:
            modes = ["normal", "multiply", "screen", "overlay", "soft-light", "hard-light", "darken", "lighten", "difference", "color", "luminosity"]
            self.blend.set_selected(modes.index(layer.blend_mode) if layer.blend_mode in modes else 0); self.opacity.set_value(layer.opacity * 100)
        self._updating = False

    def _refresh_metadata(self) -> None:
        clear(self.metadata_box)
        if not self.document: return
        source = self.document.source
        values = (("Filename", source.display_name), ("Type", source.content_type or ("RAW image" if source.raw else "Image")), ("Dimensions", f"{self._source_size[0]:,} × {self._source_size[1]:,}" if self._source_size[0] else "Reading image…"), ("Precision", "16-bit RAW decode" if source.raw else f"{source.bit_depth}-bit"), ("Profile", "Camera calibration → sRGB" if source.raw else source.embedded_profile), ("Source", "Missing" if source.missing else "Available"))
        for key, value in values:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); label = Gtk.Label(label=key); label.add_css_class("darkroom-meta"); label.set_xalign(0); label.set_hexpand(True); result = Gtk.Label(label=value); result.set_xalign(1); result.set_selectable(True); result.set_ellipsize(3); row.append(label); row.append(result); self.metadata_box.append(row)

    def _refresh_filmstrip(self) -> None:
        self._gallery_page = max(0, min(self._gallery_page, (len(self._filmstrip_paths) - 1) // 24))
        paths = self._filmstrip_paths[self._gallery_page * 24:(self._gallery_page + 1) * 24]
        signature = (tuple(paths), self._current_path)
        count = len(self._filmstrip_paths)
        self.gallery_count.set_tooltip_text(f"Photos {self._gallery_page * 24 + 1}–{min(count, (self._gallery_page + 1) * 24)} of {count}" if count else "Add photos or browse a folder")
        self.gallery_count.set_label(f"{count} photo{'s' if count != 1 else ''}" if count else "Add photos or a folder")
        self.gallery_previous.set_sensitive(self._gallery_page > 0)
        self.gallery_next.set_sensitive((self._gallery_page + 1) * 24 < count)
        if signature == self._gallery_signature: return
        self._gallery_signature = signature
        clear(self.filmstrip); clear(self.images_list)
        self._gallery_pictures = {}
        self._gallery_placeholders = {}
        for path in paths:
            button = Gtk.ToggleButton(active=path == self._current_path)
            button.set_tooltip_text(path.name)
            button.update_property([Gtk.AccessibleProperty.LABEL], [path.name])
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            picture = Gtk.Picture.new_for_paintable(self._thumbnail_cache.get(path))
            picture.set_size_request(96, 64)
            picture.set_can_shrink(True)
            tile = Gtk.Overlay(child=picture)
            placeholder = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
            placeholder.set_visible(not bool(self._thumbnail_cache.get(path)))
            placeholder.set_can_target(False)
            tile.add_overlay(placeholder)
            self._gallery_placeholders[path] = placeholder
            content.append(tile)
            label = Gtk.Label(label=path.stem, max_width_chars=12, ellipsize=3)
            label.add_css_class("darkroom-meta")
            content.append(label)
            button.set_child(content)
            button.connect("clicked", lambda _b, p=path: self._open_gallery_photo(p))
            self.filmstrip.insert(button, -1)
            self._gallery_pictures[path] = picture
        self._queue_gallery_thumbnail()

    def _queue_gallery_thumbnail(self):
        if self._closing or self._gallery_worker_running or not self.gallery_toggle.get_active(): return
        path = next((p for p in self._gallery_pictures if p not in self._thumbnail_cache and p != self._current_path), None)
        if path is None: return
        self._gallery_worker_running = True
        def worker():
            try:
                document = DocumentStore.load(path) if path.name.endswith(DOCUMENT_SUFFIX) else Document.new(path.as_uri(), raw=path.suffix.lower() in RAW_SUFFIXES)
                source = file_path(document.source.uri)
                if source.suffix.lower() in RAW_SUFFIXES:
                    image = raw_thumbnail(source)
                    image.thumbnail((160, 160))
                else:
                    image, _profile = RasterEngine(document).open_source(160)
                error = None
            except Exception as exc: image, error = None, str(exc)
            GLib.idle_add(ready, image, error)
        def ready(image, error):
            self._gallery_worker_running = False
            if self._closing: return False
            texture = self._texture(image) if image is not None else None
            self._thumbnail_cache[path] = texture
            while len(self._thumbnail_cache) > 128: self._thumbnail_cache.pop(next(iter(self._thumbnail_cache)))
            picture = self._gallery_pictures.get(path)
            if picture:
                picture.set_paintable(texture)
                self._gallery_placeholders[path].set_visible(texture is None)
                if error: picture.set_tooltip_text("Open photo to view it")
            self._queue_gallery_thumbnail()
            return False
        threading.Thread(target=worker, name="darkroom-gallery-thumbnail", daemon=True).start()

    def _mark_changed(self) -> None:
        self.dirty = True; self._refresh_all(); self._schedule_render()
        if not self._autosave_source: self._autosave_source = GLib.timeout_add_seconds(4, self._autosave)

    def _autosave(self) -> bool:
        self._autosave_source = 0
        if self.document and self.dirty:
            try: self.store.autosave(self.document, self.document_path)
            except OSError as error: self._toast(f"Autosave failed: {error}")
        return GLib.SOURCE_REMOVE

    def _tool_toggled(self, button, tool: str) -> None:
        if button.get_active(): self._select_tool(tool)

    def _select_tool(self, tool: str) -> None:
        if not self.document: return
        if tool != "clone": self._clone_source = None
        framing_changed = (tool == "crop") != (self.document.workspace.active_tool == "crop")
        if framing_changed and tool == "crop":
            self._crop_entry = copy.deepcopy(self.document.crop)
            self._fit_image()
            self.canvas_stack.set_visible_child_name("edited")
        self.document.workspace.active_tool = tool
        if tool in self._retouch_buttons and not self._retouch_buttons[tool].get_active():
            self._retouch_buttons[tool].set_active(True)
        if tool in self._tool_buttons and not self._tool_buttons[tool].get_active(): self._tool_buttons[tool].set_active(True)
        self.crop_overlay.set_can_target(tool == "crop")
        self.crop_overlay.queue_draw(); self._refresh_context_controls()
        if tool == "crop" and not self._page_buttons["crop"].get_active(): self._page_buttons["crop"].set_active(True)
        if tool in {"heal", "clone"} and not self._page_buttons["retouch"].get_active(): self._page_buttons["retouch"].set_active(True)
        if tool == "select" and self._page_buttons["crop"].get_active(): self._page_buttons["adjust"].set_active(True)
        if framing_changed: self._schedule_render()

    def _image_rect(self) -> tuple[float, float, float, float]:
        """Where the photo itself is drawn, in the canvas overlay's coordinates.

        The picture keeps its aspect ratio inside the canvas, so a gray margin
        surrounds it on two sides. Crop handles, retouch points and the
        eyedropper map onto this rectangle, never onto the margin.
        """
        width, height = float(max(1, self.crop_overlay.get_width())), float(max(1, self.crop_overlay.get_height()))
        picture = self.comparison_edited_picture if self.canvas_stack.get_visible_child_name() == "compare" else self.edited_picture
        paintable = picture.get_paintable()
        found, bounds = picture.compute_bounds(self.crop_overlay)
        if paintable is None or not found:
            return 0.0, 0.0, width, height
        x, y, box_width, box_height = bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()
        natural_width, natural_height = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
        if natural_width <= 0 or natural_height <= 0 or box_width <= 0 or box_height <= 0:
            return x, y, box_width, box_height
        scale = min(box_width / natural_width, box_height / natural_height)
        shown_width, shown_height = natural_width * scale, natural_height * scale
        return x + (box_width - shown_width) / 2, y + (box_height - shown_height) / 2, shown_width, shown_height

    def _adjustment_changed(self, scale, key: str) -> None:
        if self._updating or not self.editor: return
        try:
            self.editor.set_adjustment(key, scale.get_value(), coalesce=True)
            self.dirty = True
            self._interactive = True
            self._updating = True
            self._adjustment_values[key].set_value(scale.get_value())
            self._updating = False
            self.actions["undo"].set_enabled(self.editor.can_undo)
            self.actions["redo"].set_enabled(self.editor.can_redo)
            self._schedule_render()
            if self._settle_source: GLib.source_remove(self._settle_source)
            self._settle_source = GLib.timeout_add(180, self._finish_adjustment)
            if not self._autosave_source: self._autosave_source = GLib.timeout_add_seconds(4, self._autosave)
        except (EditError, DocumentError) as error: self._toast(str(error))

    def _finish_adjustment(self):
        self._settle_source = 0
        self._interactive = False
        if self.editor: self.editor.end_continuous_edit()
        self._refresh_all()
        self._schedule_render()
        return GLib.SOURCE_REMOVE

    def _reset_adjustment(self, key: str) -> None:
        if not self.editor: return
        self.editor.reset_adjustment(key); self._mark_changed()

    def _curve_changed(self, curve) -> None:
        if self._updating or not self.editor: return
        points = [[float(x), float(y)] for x, y in curve.get_points().unpack()]
        self.editor.set_adjustment("tone-curve", points, coalesce=True); self._mark_changed()

    def _crop_changed(self, spin, key: str) -> None:
        if self._updating or not self.editor or not self.document: return
        crop = Crop(**{field: getattr(self.document.crop, field) for field in self.document.crop.__dataclass_fields__})
        setattr(crop, key, spin.get_value())
        try: self.editor.set_crop(crop, coalesce=True); self._mark_changed(); self.crop_overlay.queue_draw()
        except DocumentError: pass

    def _blend_changed(self, dropdown, _param) -> None:
        if self._updating or not self.editor or not self.document: return
        layer_id = self.document.workspace.selected_layer_id
        modes = ["normal", "multiply", "screen", "overlay", "soft-light", "hard-light", "darken", "lighten", "difference", "color", "luminosity"]
        if layer_id: self.editor.set_blend_mode(layer_id, modes[dropdown.get_selected()]); self._mark_changed()

    def _opacity_changed(self, scale) -> None:
        if self._updating or not self.editor or not self.document: return
        if self.document.workspace.selected_layer_id:
            self.editor.set_layer_opacity(self.document.workspace.selected_layer_id, scale.get_value() / 100, coalesce=True); self._mark_changed()

    def _layer_selected(self, _list, row) -> None:
        if not row or not self.document: return
        self.document.workspace.selected_layer_id = row.layer_id; self._refresh_adjustments()

    def _toggle_layer(self, layer_id: str, active: bool) -> None:
        if not self.editor or not self.document: return
        layer = self.document.layer(layer_id)
        if layer and layer.visible != active: self.editor.toggle_layer(layer_id); self._mark_changed()

    def _add_mask(self, kind: str) -> None:
        if not self.editor or not self.document or not self.document.workspace.selected_layer_id: return
        geometry = {"x": .5, "y": .5, "radius": .35} if kind == "radial-gradient" else {}
        self.editor.add_mask(self.document.workspace.selected_layer_id, kind, geometry=geometry); self._mark_changed()

    def _canvas_pressed(self, gesture, presses: int, x: float, y: float) -> None:
        if not self.document or not self.editor: return
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_SECONDARY:
            self._canvas_menu(x, y); return
        tool = self.document.workspace.active_tool
        left, top, width, height = self._image_rect()
        if tool in {"heal", "clone", "brush-mask", "linear-gradient", "radial-gradient", "eyedropper"} and \
                not (left <= x <= left + width and top <= y <= top + height):
            return  # the gray margin around the photo is not part of it
        point = [max(0.0, min(1.0, (x - left) / width)), max(0.0, min(1.0, (y - top) / height))]
        size = (getattr(self, "brush_size", None).get_value() if getattr(self, "brush_size", None) else 48) / max(width, height)
        if tool == "heal":
            self.editor.add_retouch("heal", [point], size=size); self._mark_changed()
        elif tool == "clone":
            if self._clone_source is None:
                self._clone_source = point; self._toast("Clone source set; choose a destination")
            else:
                self.editor.add_retouch("clone", [point], source=self._clone_source, size=size); self._clone_source = None; self._mark_changed()
        elif tool in {"brush-mask", "linear-gradient", "radial-gradient"} and self.document.workspace.selected_layer_id:
            kind = {"brush-mask": "brush", "linear-gradient": "linear-gradient", "radial-gradient": "radial-gradient"}[tool]
            geometry = {"points": [point], "size": size} if kind == "brush" else ({"start": max(0, point[0] - .25), "end": min(1, point[0] + .25)} if kind == "linear-gradient" else {"x": point[0], "y": point[1], "radius": .28})
            self.editor.add_mask(self.document.workspace.selected_layer_id, kind, geometry=geometry); self._mark_changed()
        elif tool == "eyedropper" and self._edited_preview is not None:
            px = min(self._edited_preview.width - 1, max(0, round(point[0] * (self._edited_preview.width - 1))))
            py = min(self._edited_preview.height - 1, max(0, round(point[1] * (self._edited_preview.height - 1))))
            red, green, blue, *_ = self._edited_preview.convert("RGBA").getpixel((px, py))
            value = f"#{red:02X}{green:02X}{blue:02X} · R {red} G {green} B {blue}"
            self.pixel_status.set_label(value); self._toast(value)
        elif tool == "zoom":
            self._zoom(1.25)

    def _canvas_menu(self, x: float, y: float) -> None:
        menu = Gio.Menu()
        menu.append("Fit Image", "win.fit"); menu.append("Actual Pixels", "win.actual-pixels")
        if self.document:
            menu.append("Before and After", "win.before-after"); menu.append("Show Source in Filer", "win.show-filer"); menu.append("Export…", "win.export")
        popover = Gtk.PopoverMenu.new_from_model(menu); popover.set_parent(self.canvas_overlay); popover.set_pointing_to(_point_rectangle(x, y)); popover.popup()

    def _draw_crop(self, _area, cr, _width: int, _height: int) -> None:
        if not self.document or self.document.workspace.active_tool != "crop": return
        origin_x, origin_y, width, height = self._image_rect()
        crop = self.document.crop
        left, top = origin_x + crop.left * width, origin_y + crop.top * height
        right, bottom = origin_x + crop.right * width, origin_y + crop.bottom * height
        # Everything stays on the photo: the frame, its handles and the shade
        # are clipped to it, so none of it spills onto the gray margin.
        cr.rectangle(origin_x, origin_y, width, height); cr.clip()
        cr.set_source_rgba(0, 0, 0, .52)
        cr.rectangle(origin_x, origin_y, width, top - origin_y); cr.rectangle(origin_x, bottom, width, origin_y + height - bottom)
        cr.rectangle(origin_x, top, left - origin_x, bottom - top); cr.rectangle(right, top, origin_x + width - right, bottom - top); cr.fill()
        if crop.overlay == "thirds":
            cr.set_source_rgba(1, 1, 1, .45); cr.set_line_width(1)
            for fraction in (1/3, 2/3):
                cr.move_to(left + (right - left) * fraction, top); cr.line_to(left + (right - left) * fraction, bottom)
                cr.move_to(left, top + (bottom - top) * fraction); cr.line_to(right, top + (bottom - top) * fraction)
            cr.stroke()
        # The frame sits inside the crop edge; corner and edge handles are
        # bars drawn inward from it, the way Lightroom marks a crop.
        cr.set_source_rgba(1, 1, 1, .9); cr.set_line_width(1)
        cr.rectangle(left + .5, top + .5, max(0, right - left - 1), max(0, bottom - top - 1)); cr.stroke()
        arm, thickness = min(18.0, (right - left) / 3, (bottom - top) / 3), 3.0
        cr.set_source_rgba(1, 1, 1, .98)
        for x, y, dx, dy in ((left, top, 1, 1), (right, top, -1, 1), (right, bottom, -1, -1), (left, bottom, 1, -1)):
            cr.rectangle(min(x, x + dx * arm), min(y, y + dy * thickness), arm, thickness)
            cr.rectangle(min(x, x + dx * thickness), min(y, y + dy * arm), thickness, arm)
        middle_x, middle_y = (left + right) / 2, (top + bottom) / 2
        cr.rectangle(middle_x - arm / 2, top, arm, thickness); cr.rectangle(middle_x - arm / 2, bottom - thickness, arm, thickness)
        cr.rectangle(left, middle_y - arm / 2, thickness, arm); cr.rectangle(right - thickness, middle_y - arm / 2, thickness, arm)
        cr.fill()

    def _crop_drag_begin(self, _gesture, x: float, y: float) -> None:
        if not self.document: return
        crop = self.document.crop; origin_x, origin_y, width, height = self._image_rect()
        x, y = x - origin_x, y - origin_y
        positions = {
            "top-left": (crop.left * width, crop.top * height), "top": ((crop.left + crop.right) * width / 2, crop.top * height),
            "top-right": (crop.right * width, crop.top * height), "right": (crop.right * width, (crop.top + crop.bottom) * height / 2),
            "bottom-right": (crop.right * width, crop.bottom * height), "bottom": ((crop.left + crop.right) * width / 2, crop.bottom * height),
            "bottom-left": (crop.left * width, crop.bottom * height), "left": (crop.left * width, (crop.top + crop.bottom) * height / 2),
        }
        handle, position = min(positions.items(), key=lambda item: (item[1][0] - x) ** 2 + (item[1][1] - y) ** 2)
        distance = (position[0] - x) ** 2 + (position[1] - y) ** 2
        self._crop_drag_handle = handle if distance <= 28 ** 2 else ("move" if crop.left*width < x < crop.right*width and crop.top*height < y < crop.bottom*height else None)
        if self._crop_drag_handle and self._crop_drag_handle != "move":
            self._updating = True
            self.crop_ratios.set_selected(0)
            self._updating = False
        self._crop_drag_start = Crop(**{field: getattr(crop, field) for field in crop.__dataclass_fields__})

    def _crop_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if not self.editor or not self.document or not self._crop_drag_handle or not self._crop_drag_start: return
        _origin_x, _origin_y, width, height = self._image_rect()
        crop = Crop(**{field: getattr(self._crop_drag_start, field) for field in self._crop_drag_start.__dataclass_fields__})
        if self._crop_drag_handle == "move":
            dx = max(-crop.left, min(1-crop.right, dx / width))
            dy = max(-crop.top, min(1-crop.bottom, dy / height))
            crop.left += dx; crop.right += dx; crop.top += dy; crop.bottom += dy
        if "left" in self._crop_drag_handle: crop.left = max(0, min(crop.right - .01, crop.left + dx / width))
        if "right" in self._crop_drag_handle: crop.right = min(1, max(crop.left + .01, crop.right + dx / width))
        if "top" in self._crop_drag_handle: crop.top = max(0, min(crop.bottom - .01, crop.top + dy / height))
        if "bottom" in self._crop_drag_handle: crop.bottom = min(1, max(crop.top + .01, crop.bottom + dy / height))
        self.editor.set_crop(crop, coalesce=True); self.dirty = True; self._refresh_adjustments(); self.crop_overlay.queue_draw()

    def _crop_drag_end(self, *_args) -> None:
        if self.editor and self._crop_drag_handle:
            self.editor.end_continuous_edit(); self._refresh_all(); self._schedule_render()
        self._crop_drag_handle = None; self._crop_drag_start = None

    def _arrangement_changed(self, dropdown, _param) -> None:
        if self._updating or not self.document: return
        self.document.workspace.arrangement = ["develop", "edit", "retouch", "review"][dropdown.get_selected()]; self._apply_arrangement()

    def _apply_arrangement(self) -> None:
        if self._compact: return
        self.secondary_pane.set_visible(False)
        self.inspector_pane.set_visible(not self._panels_hidden)
        self.filmstrip_pane.set_visible(True)

    def _clipping_changed(self, button) -> None:
        self.histogram.set_clipping_visible(button.get_active())
        if self.document: self.document.workspace.clipping_warnings = button.get_active()

    def _proof_changed(self, button) -> None:
        if self.document: self.document.workspace.soft_proof = button.get_active(); self._schedule_render()

    def _file_drop(self, _target, value, _x, _y) -> bool:
        files = value.get_files()
        if not files: return False
        paths = [Path(file.get_path()) for file in files if file.get_path()]
        if not paths: return False
        if len(paths) == 1 and paths[0].is_dir(): self._browse_folder(paths[0])
        else: self.open_paths(paths)
        return True

    def _place_image(self, path: Path) -> None:
        if self.editor: self.editor.add_layer("linked", path.stem, source_uri=path.resolve().as_uri()); self._mark_changed()

    def _open_notice_action(self, *_args) -> None:
        if self.document: self.open_notice.set_revealed(False)
        else: self._action_open()

    def _action_open(self, *_args) -> None:
        dialog = Gtk.FileDialog(title="Add Photos")
        supported = Gtk.FileFilter(name="Photos and Darkroom documents")
        for suffix in sorted(IMAGE_SUFFIXES | {DOCUMENT_SUFFIX}):
            supported.add_pattern("*" + suffix)
            supported.add_pattern("*" + suffix.upper())
        filters = Gio.ListStore.new(Gtk.FileFilter); filters.append(supported)
        dialog.set_filters(filters)
        dialog.open_multiple(self, None, self._open_finished)

    def _open_finished(self, dialog, result) -> None:
        try: files = dialog.open_multiple_finish(result)
        except GLib.Error: return
        paths = [Path(files.get_item(i).get_path()) for i in range(files.get_n_items()) if files.get_item(i).get_path()]
        self.open_paths(paths)

    def open_paths(self, paths):
        paths = [p.resolve() for p in paths if p.suffix.lower() in IMAGE_SUFFIXES or p.name.endswith(DOCUMENT_SUFFIX)]
        if len(paths) == 1 and not self._filmstrip_paths:
            self.open_path(paths[0], as_document=paths[0].name.endswith(DOCUMENT_SUFFIX))
            return
        for path in paths:
            if path not in self._filmstrip_paths: self._filmstrip_paths.append(path)
        if paths:
            self.open_path(paths[0], as_document=paths[0].name.endswith(DOCUMENT_SUFFIX))
            self._refresh_filmstrip()
            self.gallery_toggle.set_active(True)

    def _action_open_photos(self, *_args) -> None:
        app = Gio.DesktopAppInfo.new("org.projectluma.Photos.desktop")
        if app: app.launch([], None); self._toast("Choose Edit in Darkroom from Photos")
        else: self._toast("Photos is not installed")

    def _action_save(self, *_args) -> None:
        if not self.document: return
        if not self.document_path: self._action_save_as(); return
        self._save_to(self.document_path)

    def _action_save_as(self, *_args) -> None:
        if not self.document: return
        dialog = Gtk.FileDialog(title="Save Editable Darkroom Document", initial_name=f"{self.document.name}{DOCUMENT_SUFFIX}")
        dialog.save(self, None, self._save_finished)

    def _save_finished(self, dialog, result) -> None:
        try: file = dialog.save_finish(result)
        except GLib.Error: return
        if file.get_path():
            path = Path(file.get_path()); path = path if path.name.endswith(DOCUMENT_SUFFIX) else path.with_name(path.name + DOCUMENT_SUFFIX); self._save_to(path)

    def _save_to(self, path: Path) -> None:
        try: DocumentStore.save(self.document, path); self.document_path = path; self.dirty = False; self.store.discard_recovery(self.document.id); self._refresh_all(); self._toast("Editable document saved")
        except (OSError, DocumentError) as error: self._error("Save failed", str(error))

    def _action_save_version(self, *_args) -> None:
        if self.editor: snapshot = self.editor.create_snapshot(); self._mark_changed(); self._toast(f"Created {snapshot.name}")

    def _action_export(self, *_args) -> None:
        if not self.document: return
        dialog = Adw.Dialog(title="Export Image"); dialog.set_content_width(430); dialog.set_content_height(440)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12); box.set_margin_top(18); box.set_margin_bottom(18); box.set_margin_start(18); box.set_margin_end(18)
        format_box = Gtk.DropDown.new_from_strings(["JPEG", "PNG", "TIFF", "WEBP", "PDF"]); box.append(Gtk.Label(label="Format", xalign=0)); box.append(format_box)
        quality = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1); quality.set_value(90); quality.set_draw_value(True); box.append(Gtk.Label(label="Quality", xalign=0)); box.append(quality)
        metadata = Gtk.DropDown.new_from_strings(["Copyright only", "All metadata", "Strip all metadata"]); box.append(Gtk.Label(label="Metadata", xalign=0)); box.append(metadata)
        note = Gtk.Label(label="Export renders a delivery file. Your editable layers remain in the Darkroom document."); note.set_wrap(True); note.add_css_class("darkroom-meta"); box.append(note)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); cancel = Gtk.Button(label="Cancel"); cancel.connect("clicked", lambda *_: dialog.close()); buttons.append(cancel); choose = Gtk.Button(label="Choose Destination…"); choose.add_css_class("suggested-action"); choose.connect("clicked", lambda *_: self._choose_export(dialog, format_box, quality, metadata)); buttons.append(choose); box.append(buttons)
        dialog.set_child(box); dialog.present(self)

    def _choose_export(self, sheet, format_box, quality, metadata) -> None:
        formats = ["JPEG", "PNG", "TIFF", "WEBP", "PDF"]; fmt = formats[format_box.get_selected()]; extension = {"JPEG": ".jpg", "PNG": ".png", "TIFF": ".tif", "WEBP": ".webp", "PDF": ".pdf"}[fmt]
        preset = ExportPreset(format=fmt, quality=round(quality.get_value()), metadata=["copyright", "all", "none"][metadata.get_selected()])
        dialog = Gtk.FileDialog(title="Export Image", initial_name=f"{self.document.name}{extension}")
        dialog.save(self, None, lambda chooser, result: self._export_destination(chooser, result, sheet, preset, extension))

    def _export_destination(self, dialog, result, sheet, preset, extension) -> None:
        try: file = dialog.save_finish(result)
        except GLib.Error: return
        if not file.get_path(): return
        destination = Path(file.get_path()); destination = destination if destination.suffix else destination.with_suffix(extension); sheet.close()
        toast = Adw.Toast(title="Preparing export…", timeout=0); self.toast_overlay.add_toast(toast)
        self.export_job = ExportJob(self.document, destination, preset, overwrite=False, progress=lambda value, status: toast.set_title(f"{status} · {value*100:.0f}%"), completed=lambda path, error: self._export_complete(path, error, toast)); self.export_job.start()

    def _export_complete(self, path, error, toast) -> None:
        toast.dismiss(); self.export_job = None
        if error: self._error("Export failed", error)
        else: self._toast(f"Exported {path.name}")

    def _action_undo(self, *_args) -> None:
        if self.editor:
            try: name = self.editor.undo(); self.dirty = True; self._refresh_all(); self._schedule_render(); self._toast(f"Undid {name}")
            except EditError as error: self._toast(str(error))

    def _action_redo(self, *_args) -> None:
        if self.editor:
            try: name = self.editor.redo(); self.dirty = True; self._refresh_all(); self._schedule_render(); self._toast(f"Redid {name}")
            except EditError as error: self._toast(str(error))

    def _action_before_after(self, *_args) -> None:
        if not self.document: return
        comparing = self.canvas_stack.get_visible_child_name() != "compare"
        if self.document.workspace.active_tool == "crop": self._page_buttons["adjust"].set_active(True)
        self.document.workspace.before_after = "side-by-side" if comparing else "off"
        self._fit_image()
        self.canvas_stack.set_visible_child_name("compare" if comparing else "edited")
        self.comparison.set_position(max(1, self.canvas_overlay.get_width() // 2))
        self.compare_button.set_tooltip_text("Return to edited photo" if comparing else "Compare with original")

    def _action_inspector(self, *_args) -> None:
        if self._compact:
            self.compact_stack.set_visible_child_name("adjust" if self.compact_stack.get_visible_child_name() == "image" else "image")
        else:
            self._panels_hidden = self.inspector_pane.get_visible()
            self._apply_arrangement()

    def _action_panels(self, *_args) -> None:
        self._action_inspector()

    def _action_layers(self, *_args) -> None:
        dialog = Adw.Dialog(title="Layers", content_width=360, content_height=520)
        if self.secondary_pane.get_parent() is self.workspace_row:
            self.workspace_row.remove(self.secondary_pane)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.secondary_pane.set_visible(True)
        content.append(self.secondary_pane)
        content.append(self.layer_controls)
        dialog.set_child(content)
        def closed(*_args):
            content.remove(self.secondary_pane)
            content.remove(self.layer_controls)
            self.secondary_pane.set_visible(False)
            self.workspace_row.prepend(self.secondary_pane)
        dialog.connect("closed", closed)
        dialog.present(self)

    def _action_add_layer(self, *_args) -> None:
        if self.editor: self.editor.add_layer("adjustment", f"Adjustment {len(self.document.layers)}"); self._mark_changed()

    def _action_delete_layer(self, *_args) -> None:
        if self.editor and self.document and self.document.workspace.selected_layer_id:
            try: self.editor.remove_layer(self.document.workspace.selected_layer_id); self._mark_changed()
            except EditError as error: self._toast(str(error))

    def _move_layer(self, delta: int) -> None:
        if not self.editor or not self.document or not self.document.workspace.selected_layer_id: return
        layer = self.document.layer(self.document.workspace.selected_layer_id)
        if layer not in self.document.layers: return
        index = self.document.layers.index(layer)
        target = index - 1 if delta < 0 else index + 2
        target = max(0, min(len(self.document.layers), target))
        self.editor.reorder_layer(layer.id, target); self._mark_changed()

    def _action_reset_crop(self, *_args) -> None:
        if self.editor: self.editor.set_crop(Crop(), name="Reset crop"); self._mark_changed()

    def _action_relink(self, *_args) -> None:
        dialog = Gtk.FileDialog(title="Relink Original Image"); dialog.open(self, None, self._relink_finished)

    def _relink_finished(self, dialog, result) -> None:
        try: file = dialog.open_finish(result)
        except GLib.Error: return
        if file.get_path() and self.editor: self.editor.relink_source(Path(file.get_path()).resolve().as_uri()); self._mark_changed()

    def _action_snapshot(self, *_args) -> None:
        if self.editor: snapshot = self.editor.create_snapshot(); self._mark_changed(); self._toast(f"Created {snapshot.name}")

    def _action_show_filer(self, *_args) -> None:
        if not self.document: return
        file = Gio.File.new_for_uri(self.document.source.uri)
        try: Gio.AppInfo.launch_default_for_uri(file.get_parent().get_uri(), None)
        except GLib.Error as error: self._toast(f"Couldn’t open Filer: {error.message}")

    def _action_shortcuts(self, *_args) -> None:
        dialog = Adw.AlertDialog(heading="Darkroom Shortcuts", body="V Select · C Crop · J Heal · S Clone · B Brush mask · G Gradient · H Hand · Z Zoom · I Eyedropper\n0 Fit · 1 Actual pixels · \\ Before/after · Ctrl+\\ Panels · Ctrl+S Save · Ctrl+E Export")
        dialog.add_response("close", "Close"); dialog.present(self)

    def _action_fullscreen(self, *_args) -> None:
        if self.is_fullscreen(): self.unfullscreen()
        else: self.fullscreen()

    def _action_appearance(self, action, parameter) -> None:
        value = parameter.get_string(); action.set_state(parameter); self._appearance = value
        root = self.get_content()
        for name in ("light", "dark", "frost", "glass"): root.remove_css_class(f"darkroom-{name}")
        if value != "system": root.add_css_class(f"darkroom-{value}")
        style = Adw.StyleManager.get_default()
        if value == "light": style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif value == "dark": style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else: style.set_color_scheme(Adw.ColorScheme.DEFAULT)

    def _fit_image(self) -> None:
        self._fit_mode = True
        self.zoom_label.set_label("Fit")
        for picture in (self.edited_picture, self.original_picture, self.comparison_edited_picture): picture.set_size_request(-1, -1)

    def _set_zoom(self, value: float) -> None:
        if not self.document or self._edited_preview is None: return
        was_fit = self._fit_mode
        self._fit_mode = False
        self.document.workspace.zoom = max(.05, min(8, value))
        self.zoom_label.set_label(f"{self.document.workspace.zoom*100:.0f}%")
        self._apply_zoom_size()
        if was_fit: self._schedule_render()

    def _apply_zoom_size(self) -> None:
        for picture in (self.edited_picture, self.original_picture, self.comparison_edited_picture):
            texture = picture.get_paintable()
            if texture:
                scale = texture.get_intrinsic_width() / self._source_size[0] if picture is self.original_picture and self._source_size[0] else self._preview_scale
                factor = self.document.workspace.zoom / scale
                picture.set_size_request(round(texture.get_intrinsic_width()*factor), round(texture.get_intrinsic_height()*factor))

    def _zoom(self, factor: float) -> None:
        if not self.document: return
        value = self.document.workspace.zoom
        if self._fit_mode and self._edited_preview is not None:
            value = min(self.canvas_overlay.get_width()/self._edited_preview.width, self.canvas_overlay.get_height()/self._edited_preview.height) * self._preview_scale
        self._set_zoom(value * factor)

    def _key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        focus = self.get_focus()
        if focus and isinstance(focus, (Gtk.Editable, Gtk.TextView, Gtk.SpinButton)): return False
        key = Gdk.keyval_name(keyval) or ""
        shortcuts = {"v": "select", "c": "crop", "j": "heal", "s": "clone", "b": "brush-mask", "g": "linear-gradient", "h": "hand", "z": "zoom", "i": "eyedropper"}
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK): return False
        if key.lower() in shortcuts: self._select_tool(shortcuts[key.lower()]); return True
        if key == "Escape": self._select_tool("select"); return True
        if key in {"bracketleft", "bracketright"} and hasattr(self, "brush_size"):
            amount = 5 if key == "bracketright" else -5; self.brush_size.set_value(self.brush_size.get_value() + amount); return True
        return False

    def _width_changed(self, *_args) -> None:
        self._set_compact(self.get_width() < 860)

    def _apply_initial_width(self) -> bool:
        self._set_compact(self.get_width() < 860); return GLib.SOURCE_REMOVE

    def _set_compact(self, compact: bool) -> None:
        if self._compact == compact: return
        self._compact = compact
        self.undo_group.set_visible(not compact)
        self.pixel_status.set_visible(not compact)
        # Widgets cannot have two parents. Moving shared panes keeps one source
        # tree and one editor state while changing presentation only.
        if compact:
            for pane in (self.canvas_pane, self.secondary_pane, self.inspector_pane):
                if pane.get_parent() is self.workspace_row: self.workspace_row.remove(pane)
            for pane, name, title, icon in ((self.canvas_pane, "image", "Photo", "image-x-generic-symbolic"), (self.inspector_pane, "adjust", "Edit", "document-properties-symbolic")):
                pane.set_visible(pane is not self.secondary_pane)
                if self.compact_stack.get_page(pane) is None: self.compact_stack.add_titled_with_icon(pane, name, title, icon)
            self.layout.set_visible_child_name("compact")
        else:
            for pane in (self.canvas_pane, self.secondary_pane, self.inspector_pane):
                if pane.get_parent() is self.compact_stack: self.compact_stack.remove(pane)
            for pane in (self.secondary_pane, self.canvas_pane, self.inspector_pane):
                if pane.get_parent() is None: self.workspace_row.append(pane)
            self.layout.set_visible_child_name("wide")
            self._apply_arrangement()

    def offer_recovery(self) -> None:
        if self._recovery_offered: return
        self._recovery_offered = True
        recovered = list(self.store.recoverable())
        if not recovered: return
        path, document, _metadata = recovered[-1]
        dialog = Adw.AlertDialog(heading="Recover unsaved Darkroom work?", body=f"A recoverable edit of {document.source.display_name} was found.")
        dialog.add_response("discard", "Discard"); dialog.add_response("recover", "Recover"); dialog.set_response_appearance("recover", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, response: self._recover_response(response, path, document)); dialog.present(self)

    def _recover_response(self, response: str, path: Path, document: Document) -> None:
        if response == "recover": self.document = document; self.editor = Editor(document); self.document_path = None; self.dirty = True; self._refresh_all(); self._schedule_render()
        else: path.unlink(missing_ok=True)

    def _close_request(self, *_args) -> bool:
        for document, _editor, path, dirty in self._sessions.values():
            if dirty and document is not self.document:
                try: self.store.autosave(document, path)
                except OSError as error:
                    self._error("Couldn’t preserve edits", str(error)); return True
        if self.dirty:
            try: self.store.autosave(self.document, self.document_path)
            except OSError as error:
                self._error("Couldn’t preserve edits", str(error)); return True
        self._closing = True
        for name in ("_settle_source", "_autosave_source"):
            source = getattr(self, name)
            if source: GLib.source_remove(source); setattr(self, name, 0)
        return False

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message))

    def _error(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body); dialog.add_response("close", "Close"); dialog.present(self)
