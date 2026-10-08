# SPDX-License-Identifier: Apache-2.0
"""Shared, data-bound story/arrangement timeline (MD3).

Times passed to callbacks are seconds. Clip DTOs carry integer frame positions;
the kit never edits them or reads a media file. Applications own edits/undo.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from .icons import icon_name
from .lumaui_tokens import MEDIA

__all__ = ["Timeline"]

TIMELINE = MEDIA["timeline"]
RULER_HEIGHT = TIMELINE["ruler"]
LANE_HEIGHT = {"over": TIMELINE["over_lane"], "main": TIMELINE["main_lane"],
               "audio": TIMELINE["audio_lane"]}


def _number(value, label: str, *, positive: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def _frame_value(clip, key: str) -> int:
    value = getattr(clip, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"clip {key} must be a nonnegative integer frame count")
    return value


class Timeline(Gtk.Box):
    """Kit-owned header, ruler, lanes, clip actions, playhead and export slot.

    ``callbacks`` accepts seek/skim(seconds), select(tuple of ids),
    move(id, start_seconds), trim(id, duration_seconds), split(id, seconds),
    transition_remove(id), tool(name), snapping(bool), zoom(float), plus
    gesture_begin(id, action), gesture_commit(id, action), gesture_cancel(id,
    action). All edit callbacks are intents; refresh with ``set_content`` after
    the application commits a change. Drag end emits at most one edit intent.
    """

    __gtype_name__ = "LumaUIMediaTimeline"

    def __init__(self, lanes: Sequence = (), duration: float = 0, fps: float = 24,
                 position: float = 0, selected: Sequence[str] = (), tool: str = "select",
                 snapping: bool = True, zoom: float = 1, callbacks: Mapping | None = None,
                 markers: Sequence[int] = ()) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-media-timeline")
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_focusable(True)
        self.set_accessible_role(Gtk.AccessibleRole.GROUP)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Timeline"])
        self.callbacks = dict(callbacks or {})
        self.duration = _number(duration, "duration")
        self.fps = _number(fps, "fps", positive=True)
        self.position = _number(position, "position")
        self.zoom = _number(zoom, "zoom", positive=True)
        self.tool = tool
        self.snapping = bool(snapping)
        self.selected = tuple(selected)
        self.markers = tuple(markers)
        self.lanes = tuple(lanes)
        self._pps = 20.0
        self._clip_buttons: dict[str, Gtk.Button] = {}
        self._drag_state = None
        self._export_status: tuple[str, float | None] | None = None

        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.header.set_name("timeline-header")
        self.header.add_css_class("timeline-header")
        self.append(self.header)
        self.info = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.info.set_name("timeline-info")
        self.info.add_css_class("timeline-info")
        self.info.set_hexpand(True)
        self.header.append(self.info)
        self.export_progress = Gtk.ProgressBar()
        self.export_progress.set_name("timeline-export-progress")
        self.export_progress.set_visible(False)
        self.header.append(self.export_progress)
        self.tools = Gtk.Box(spacing=2)
        self.tools.add_css_class("timeline-tools")
        self.header.append(self.tools)
        self._tool_buttons = {}
        for name, glyph, label in (("select", "mouse-pointer-2", "Select"),
                                   ("blade", "scissors", "Blade")):
            button = self._icon_button(glyph, label, lambda _b, name=name: self.set_tool(name, emit=True))
            self.tools.append(button)
            self._tool_buttons[name] = button
        self.snap_button = self._icon_button("magnet", "Snapping", lambda *_: self.set_snapping(not self.snapping, emit=True))
        self.tools.append(self.snap_button)
        self.zoom_controls = Gtk.Box(spacing=2)
        self.header.append(self.zoom_controls)
        self.zoom_controls.append(self._icon_button("zoom-out", "Zoom out", lambda *_: self.set_zoom(self.zoom / 1.25, emit=True)))
        self.zoom_controls.append(self._icon_button("zoom-in", "Zoom in", lambda *_: self.set_zoom(self.zoom * 1.25, emit=True)))

        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.set_name("timeline-scroll")
        self.scroller.add_css_class("timeline-scroll")
        self.scroller.set_hexpand(True)
        self.scroller.set_propagate_natural_height(False)
        self.append(self.scroller)
        self.surface = Gtk.Fixed()
        self.surface.set_name("timeline-lanes")
        self.surface.add_css_class("timeline-lanes")
        self.scroller.set_child(self.surface)
        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self._on_skim)
        motion.connect("leave", lambda *_: self._emit("skim", None))
        self.surface.add_controller(motion)
        self._render()

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    @staticmethod
    def _icon_button(glyph: str, label: str, action) -> Gtk.Button:
        button = Gtk.Button()
        button.set_child(Gtk.Image.new_from_icon_name(icon_name(glyph)))
        button.set_tooltip_text(label)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.add_css_class("timeline-key")
        button.connect("clicked", action)
        return button

    def _emit(self, name: str, *args) -> None:
        callback = self.callbacks.get(name)
        if callback is not None:
            callback(*args)

    def set_content(self, lanes: Sequence, *, duration: float | None = None,
                    fps: float | None = None, markers: Sequence[int] | None = None,
                    selected: Sequence[str] | None = None) -> None:
        """Replace immutable DTO projections without touching app state."""
        self.lanes = tuple(lanes)
        if duration is not None:
            self.duration = _number(duration, "duration")
        if fps is not None:
            self.fps = _number(fps, "fps", positive=True)
        if markers is not None:
            self.markers = tuple(markers)
        if selected is not None:
            self.selected = tuple(selected)
        self._render()

    def set_position(self, seconds: float) -> None:
        self.position = min(self.duration, _number(seconds, "position"))
        if hasattr(self, "playhead"):
            self.surface.move(self.playhead, int(round(self.position * self._pps)), 0)

    def set_selected(self, ids: Sequence[str]) -> None:
        self.selected = tuple(ids)
        for identifier, button in self._clip_buttons.items():
            self._class(button, "selected", identifier in self.selected)

    def set_tool(self, name: str, *, emit: bool = False) -> None:
        if name not in ("select", "blade"):
            raise ValueError("tool must be select or blade")
        self.tool = name
        for key, button in self._tool_buttons.items():
            self._class(button, "on", key == name)
        if emit:
            self._emit("tool", name)

    def set_snapping(self, enabled: bool, *, emit: bool = False) -> None:
        self.snapping = bool(enabled)
        self._class(self.snap_button, "on", self.snapping)
        if emit:
            self._emit("snapping", self.snapping)

    def set_zoom(self, value: float, *, emit: bool = False) -> None:
        self.zoom = min(6.0, max(1.0, _number(value, "zoom", positive=True)))
        self._render()
        if emit:
            self._emit("zoom", self.zoom)

    def set_export_status(self, label: str | None, progress: float | None = None) -> None:
        """Display real job status; ``None`` restores the duration readout."""
        if label is None:
            self._export_status = None
            self.export_progress.set_visible(False)
            self._render_info()
            return
        self._export_status = (str(label), progress)
        self.info.set_text(str(label))
        if progress is not None:
            fraction = min(1.0, max(0.0, _number(progress, "progress")))
            self.export_progress.set_fraction(fraction)
            self.export_progress.set_text(f"{round(fraction * 100)}%")
            self.export_progress.set_show_text(True)
            self.export_progress.set_visible(True)
        else:
            self.export_progress.set_visible(False)

    @staticmethod
    def _class(widget: Gtk.Widget, name: str, enabled: bool) -> None:
        if enabled:
            widget.add_css_class(name)
        else:
            widget.remove_css_class(name)

    def _render_info(self) -> None:
        minutes, seconds = divmod(int(self.duration), 60)
        self.info.set_text(f"{minutes}:{seconds:02d} · {self.fps:g}p")

    def _clear_surface(self) -> None:
        child = self.surface.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.surface.remove(child)
            child = next_child

    def _render(self) -> None:
        self._clear_surface()
        self._clip_buttons.clear()
        # Studio fits a short story into the desktop lane viewport; compact
        # windows retain that readable scale and scroll horizontally.
        self._pps = max(8.0, 760.0 / max(self.duration, 10.0) * self.zoom)
        width = max(520, int(self.duration * self._pps) + 40)
        height = RULER_HEIGHT + sum(LANE_HEIGHT.get(getattr(lane, "kind", "audio"), 36)
                                      for lane in self.lanes)
        self.surface.set_size_request(width, height)
        self.scroller.set_size_request(-1, min(220, max(120, height)))
        if self._export_status is None:
            self._render_info()
        else:
            self.set_export_status(*self._export_status)
        self.set_tool(self.tool)
        self.set_snapping(self.snapping)

        ruler = Gtk.Button(label="")
        ruler.set_name("timeline-ruler")
        ruler.add_css_class("timeline-ruler")
        ruler.update_property([Gtk.AccessibleProperty.LABEL], ["Timeline ruler; seek"])
        ruler.set_size_request(width, RULER_HEIGHT)
        self.surface.put(ruler, 0, 0)
        ruler_click = Gtk.GestureClick()
        ruler_click.connect("pressed", lambda _g, _n, x, _y: self._emit("seek", min(self.duration, max(0, x / self._pps))))
        ruler.add_controller(ruler_click)
        every = 1 if self._pps > 40 else 5 if self._pps > 18 else 10
        for second in range(0, int(self.duration) + 1, every):
            tick = Gtk.Label(label=f"{second // 60}:{second % 60:02d}")
            tick.add_css_class("timeline-tick")
            tick.set_can_target(False)
            self.surface.put(tick, round(second * self._pps), 0)
        for frame in self.markers:
            marker = Gtk.Label(label="◆")
            marker.add_css_class("timeline-marker")
            marker.set_can_target(False)
            self.surface.put(marker, round(frame / self.fps * self._pps), 12)

        top = RULER_HEIGHT
        for lane in self.lanes:
            kind = getattr(lane, "kind", "audio")
            lane_height = LANE_HEIGHT.get(kind, 36)
            band = Gtk.Box()
            band.set_size_request(width, lane_height)
            band.add_css_class("timeline-lane")
            band.add_css_class(kind if kind in LANE_HEIGHT else "audio")
            band.set_name(f"timeline-lane-{lane.id}")
            self.surface.put(band, 0, top)
            transitions = []
            for clip in lane.clips:
                if clip.id in self._clip_buttons:
                    raise ValueError(f"duplicate timeline clip id: {clip.id}")
                start = _frame_value(clip, "start_frame") / self.fps
                length = _frame_value(clip, "duration_frames") / self.fps
                if length <= 0:
                    raise ValueError("clip duration_frames must be positive")
                clip_width = max(6, round(length * self._pps) - 2)
                button = Gtk.Button(label=str(getattr(clip, "text", "") or clip.name))
                button.set_name(f"timeline-clip-{clip.id}")
                button.add_css_class("timeline-clip")
                button.add_css_class(clip.kind if clip.kind in ("video", "title", "audio", "music", "voice") else "video")
                button.set_size_request(clip_width, lane_height - 6)
                button.set_tooltip_text(f"{clip.name}, {start:g} seconds, {length:g} seconds")
                button.update_property([Gtk.AccessibleProperty.LABEL], [f"{clip.name}, {length:g} seconds"])
                self._class(button, "selected", clip.id in self.selected)
                self.surface.put(button, round(start * self._pps), top + 3)
                self._clip_buttons[clip.id] = button
                button.connect("clicked", lambda _b, c=clip, s=start: self._clip_click(c, s))
                if not getattr(lane, "locked", False):
                    drag = Gtk.GestureDrag()
                    drag.connect("drag-begin", lambda g, x, y, c=clip, s=start, d=length, w=clip_width: self._drag_begin(g, x, c, s, d, w))
                    drag.connect("drag-end", lambda g, dx, dy, c=clip: self._drag_end(g, dx, c))
                    button.add_controller(drag)
                if getattr(clip, "transition_after", None):
                    transitions.append((clip, start + length))
            for clip, seam in transitions:
                transition = self._icon_button("blend", f"Remove {clip.transition_after} transition",
                                               lambda _b, identifier=clip.id: self._emit("transition_remove", identifier))
                transition.add_css_class("timeline-transition")
                transition.set_size_request(20, 20)
                self.surface.put(transition, round(seam * self._pps) - 10, top + 20)
            top += lane_height

        self.playhead = Gtk.Box()
        self.playhead.set_name("timeline-playhead")
        self.playhead.add_css_class("timeline-playhead")
        self.playhead.set_can_target(False)
        self.playhead.set_size_request(2, height)
        self.surface.put(self.playhead, round(min(self.position, self.duration) * self._pps), 0)

    def _clip_click(self, clip, start: float) -> None:
        if self.tool == "blade":
            self._emit("split", clip.id, start)
            return
        self.set_selected((clip.id,))
        self._emit("select", (clip.id,))

    def _on_skim(self, _controller, x: float, _y: float) -> None:
        self._emit("skim", min(self.duration, max(0.0, x / self._pps)))

    def _drag_begin(self, gesture, x: float, clip, start: float, length: float, width: int) -> None:
        action = "trim" if x >= width - 9 else "move"
        self._drag_state = (gesture, clip.id, action, start, length)
        self._emit("gesture_begin", clip.id, action)

    def _drag_end(self, gesture, dx: float, clip) -> None:
        state = self._drag_state
        if state is None or state[0] is not gesture:
            return
        _gesture, identifier, action, start, length = state
        self._drag_state = None
        if abs(dx) < 3:
            self._emit("gesture_cancel", identifier, action)
            return
        seconds = round((dx / self._pps) * self.fps) / self.fps
        if action == "move":
            self._emit("move", identifier, max(0.0, start + seconds))
        else:
            self._emit("trim", identifier, max(1 / self.fps, length + seconds))
        self._emit("gesture_commit", identifier, action)

    def _on_key(self, _controller, keyval, _keycode, _state) -> bool:
        key = Gdk.keyval_name(keyval)
        if key in ("a", "A"):
            self.set_tool("select", emit=True)
        elif key in ("b", "B"):
            self.set_tool("blade", emit=True)
        elif key in ("n", "N"):
            self.set_snapping(not self.snapping, emit=True)
        elif key in ("Left", "Right"):
            step = (1 if key == "Right" else -1) / self.fps
            self._emit("seek", min(self.duration, max(0, self.position + step)))
        else:
            return False
        return True
