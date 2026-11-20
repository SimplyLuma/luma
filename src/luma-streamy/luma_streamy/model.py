# SPDX-License-Identifier: Apache-2.0
"""Streamy's scene model: scenes of widgets on a canvas, kept as JSON.

Pure Python: no GTK and no GStreamer, so the engine, the window and the
tests share one model. Positions and sizes are canvas pixels (1920 × 1080
for landscape, 1080 × 1920 for vertical).
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

SCHEMA = 1

#: Canvas formats: key -> (label, width, height).
FORMATS = {
    "landscape": ("Landscape 16:9", 1920, 1080),
    "vertical": ("Vertical 9:16", 1080, 1920),
}

#: Widget kinds: key -> (default name, Lucide icon, has picture, has sound).
KINDS = {
    "display": ("Display", "monitor", True, False),
    "window": ("Window", "app-window", True, False),
    "camera": ("Camera", "camera", True, False),
    "image": ("Image", "image", True, False),
    "microphone": ("Microphone", "mic", False, True),
    "desktop-audio": ("Desktop audio", "volume-2", False, True),
    # The apps the person picks in the AudioCapture portal (pipewiresrc on its remote).
    "app-audio": ("App audio", "audio-lines", False, True),
}

#: What a new widget of each kind starts as, in canvas pixels of a 1920 × 1080 canvas.
_DEFAULT_RECTS = {
    "display": (0.0, 0.0, 1.0, 1.0),
    "window": (0.1, 0.1, 0.6, 0.6),
    "camera": (0.7, 0.62, 0.26, 0.33),
    "image": (0.04, 0.06, 0.2, 0.2),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Effects:
    """How a picture widget is drawn: the lumaframe element's properties."""

    corner_radius: float = 0.0
    stroke_width: float = 0.0
    stroke_color: str = "FFFFFF"
    shadow_blur: float = 0.0
    shadow_x: float = 0.0
    shadow_y: float = 0.0
    shadow_opacity: float = 0.4
    opacity: float = 1.0
    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 0.0
    crop_bottom: float = 0.0

    def clamped(self) -> "Effects":
        c = copy.copy(self)
        c.corner_radius = max(0.0, c.corner_radius)
        c.stroke_width = max(0.0, c.stroke_width)
        c.shadow_blur = max(0.0, c.shadow_blur)
        c.shadow_opacity = _clamp(c.shadow_opacity, 0.0, 1.0)
        c.opacity = _clamp(c.opacity, 0.0, 1.0)
        for name in ("crop_left", "crop_top", "crop_right", "crop_bottom"):
            setattr(c, name, _clamp(getattr(c, name), 0.0, 0.49))
        color = "".join(ch for ch in c.stroke_color.upper() if ch in "0123456789ABCDEF")
        c.stroke_color = color if len(color) == 6 else "FFFFFF"
        return c

    @property
    def margin(self) -> float:
        """Room around the box the shadow needs (three sigmas, plus the offset)."""
        if self.shadow_blur <= 0 or self.shadow_opacity <= 0:
            return 0.0
        return float(int(self.shadow_blur * 1.5 + max(abs(self.shadow_x), abs(self.shadow_y)) + 1))


@dataclass
class Widget:
    id: str
    kind: str
    name: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    visible: bool = True
    locked: bool = False
    effects: Effects = field(default_factory=Effects)
    #: Sound: volume in dB and mute.
    volume_db: float = 0.0
    muted: bool = False
    #: Source settings: the portal's restore token and stream id, an image path, a device.
    source: dict = field(default_factory=dict)

    @property
    def has_picture(self) -> bool:
        return KINDS[self.kind][2]

    @property
    def has_sound(self) -> bool:
        return KINDS[self.kind][3]

    @property
    def icon(self) -> str:
        return KINDS[self.kind][1]


@dataclass
class Scene:
    id: str
    name: str
    #: Bottom to top.
    widgets: list[Widget] = field(default_factory=list)

    def widget(self, widget_id: str) -> Widget | None:
        return next((w for w in self.widgets if w.id == widget_id), None)


class ModelError(ValueError):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class Project:
    format: str = "landscape"
    scenes: list[Scene] = field(default_factory=list)
    active: str | None = None

    # ── Canvas ──

    @property
    def canvas(self) -> tuple[int, int]:
        _label, width, height = FORMATS[self.format]
        return width, height

    def set_format(self, key: str) -> None:
        """Change the canvas, keeping every widget where it was relative to the canvas."""
        if key not in FORMATS:
            raise ModelError(f"unknown canvas format {key!r}")
        if key == self.format:
            return
        old_w, old_h = self.canvas
        self.format = key
        new_w, new_h = self.canvas
        sx, sy = new_w / old_w, new_h / old_h
        for scene in self.scenes:
            for w in scene.widgets:
                if not w.has_picture:
                    continue
                # Keep the picture's proportions: scale by the smaller factor, keep the centre.
                s = min(sx, sy)
                cx, cy = (w.x + w.width / 2) * sx, (w.y + w.height / 2) * sy
                w.width, w.height = w.width * s, w.height * s
                w.x, w.y = cx - w.width / 2, cy - w.height / 2

    # ── Scenes ──

    def scene(self, scene_id: str | None) -> Scene | None:
        return next((s for s in self.scenes if s.id == scene_id), None)

    @property
    def active_scene(self) -> Scene | None:
        return self.scene(self.active)

    def add_scene(self, name: str | None = None) -> Scene:
        scene = Scene(new_id("scene"), name or self._next_scene_name())
        self.scenes.append(scene)
        if self.active is None:
            self.active = scene.id
        return scene

    def _next_scene_name(self) -> str:
        names = {s.name for s in self.scenes}
        n = len(self.scenes) + 1
        while f"Scene {n}" in names:
            n += 1
        return f"Scene {n}"

    def rename_scene(self, scene_id: str, name: str) -> None:
        name = name.strip()
        if not name:
            raise ModelError("a scene has a name")
        self._require_scene(scene_id).name = name

    def remove_scene(self, scene_id: str) -> tuple[int, Scene]:
        scene = self._require_scene(scene_id)
        if len(self.scenes) == 1:
            raise ModelError("the last scene stays")
        index = self.scenes.index(scene)
        self.scenes.remove(scene)
        if self.active == scene_id:
            self.active = self.scenes[min(index, len(self.scenes) - 1)].id
        return index, scene

    def restore_scene(self, index: int, scene: Scene) -> None:
        self.scenes.insert(min(index, len(self.scenes)), scene)

    def duplicate_scene(self, scene_id: str) -> Scene:
        source = self._require_scene(scene_id)
        copy_ = Scene(new_id("scene"), f"{source.name} copy",
                      [copy.deepcopy(w) for w in source.widgets])
        for w in copy_.widgets:
            w.id = new_id(w.kind)
        self.scenes.insert(self.scenes.index(source) + 1, copy_)
        return copy_

    def move_scene(self, scene_id: str, index: int) -> None:
        scene = self._require_scene(scene_id)
        self.scenes.remove(scene)
        self.scenes.insert(max(0, min(index, len(self.scenes))), scene)

    def activate(self, scene_id: str) -> Scene:
        scene = self._require_scene(scene_id)
        self.active = scene.id
        return scene

    def _require_scene(self, scene_id: str) -> Scene:
        scene = self.scene(scene_id)
        if scene is None:
            raise ModelError(f"no scene {scene_id!r}")
        return scene

    # ── Widgets ──

    def add_widget(self, scene_id: str, kind: str, *, name: str | None = None, source: dict | None = None,
                   aspect: float | None = None) -> Widget:
        """Add a widget on top; pictures get a sensible place for their kind."""
        if kind not in KINDS:
            raise ModelError(f"unknown widget kind {kind!r}")
        scene = self._require_scene(scene_id)
        w = Widget(new_id(kind), kind, name or self._next_widget_name(scene, kind), source=dict(source or {}))
        if w.has_picture:
            cw, ch = self.canvas
            fx, fy, fw, fh = _DEFAULT_RECTS[kind]
            width, height = fw * cw, fh * ch
            if aspect:
                # Fit the source's shape inside the default box.
                if width / height > aspect:
                    width = height * aspect
                else:
                    height = width / aspect
            w.x, w.y, w.width, w.height = fx * cw, fy * ch, width, height
            if kind == "camera":
                w.effects = Effects(corner_radius=24, stroke_width=4, shadow_blur=40, shadow_y=12,
                                    shadow_opacity=0.35)
        scene.widgets.append(w)
        return w

    def _next_widget_name(self, scene: Scene, kind: str) -> str:
        base = KINDS[kind][0]
        names = {w.name for w in scene.widgets}
        if base not in names:
            return base
        n = 2
        while f"{base} {n}" in names:
            n += 1
        return f"{base} {n}"

    def find_widget(self, widget_id: str) -> tuple[Scene, Widget]:
        for scene in self.scenes:
            w = scene.widget(widget_id)
            if w is not None:
                return scene, w
        raise ModelError(f"no widget {widget_id!r}")

    def remove_widget(self, widget_id: str) -> tuple[Scene, int, Widget]:
        scene, w = self.find_widget(widget_id)
        index = scene.widgets.index(w)
        scene.widgets.remove(w)
        return scene, index, w

    def restore_widget(self, scene_id: str, index: int, widget: Widget) -> None:
        scene = self._require_scene(scene_id)
        scene.widgets.insert(min(index, len(scene.widgets)), widget)

    def move_widget(self, widget_id: str, index: int) -> None:
        """Restack: index 0 is the bottom."""
        scene, w = self.find_widget(widget_id)
        scene.widgets.remove(w)
        scene.widgets.insert(max(0, min(index, len(scene.widgets))), w)

    def set_rect(self, widget_id: str, x: float, y: float, width: float, height: float) -> Widget:
        _scene, w = self.find_widget(widget_id)
        if width <= 0 or height <= 0:
            raise ModelError("a widget has a size")
        w.x, w.y, w.width, w.height = float(x), float(y), float(width), float(height)
        return w

    def align(self, widget_id: str, how: str) -> Widget:
        """Align to the canvas: left, center, right, top, middle, bottom, or fill."""
        _scene, w = self.find_widget(widget_id)
        cw, ch = self.canvas
        if how == "left":
            w.x = 0
        elif how == "center":
            w.x = (cw - w.width) / 2
        elif how == "right":
            w.x = cw - w.width
        elif how == "top":
            w.y = 0
        elif how == "middle":
            w.y = (ch - w.height) / 2
        elif how == "bottom":
            w.y = ch - w.height
        elif how == "fill":
            w.x, w.y, w.width, w.height = 0, 0, cw, ch
        else:
            raise ModelError(f"unknown alignment {how!r}")
        return w

    # ── Persistence ──

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, "format": self.format, "active": self.active,
                "scenes": [asdict(s) for s in self.scenes]}

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise ModelError("not a Streamy project this version reads")
        project = cls(format=data.get("format", "landscape") if data.get("format") in FORMATS else "landscape")
        effect_names = {f.name for f in fields(Effects)}
        widget_names = {f.name for f in fields(Widget)} - {"effects"}
        for raw in data.get("scenes", []):
            scene = Scene(str(raw["id"]), str(raw.get("name") or "Scene"))
            for rw in raw.get("widgets", []):
                if rw.get("kind") not in KINDS:
                    continue  # a kind from a newer version
                effects = Effects(**{k: v for k, v in (rw.get("effects") or {}).items() if k in effect_names})
                w = Widget(**{k: v for k, v in rw.items() if k in widget_names}, effects=effects.clamped())
                scene.widgets.append(w)
            project.scenes.append(scene)
        active = data.get("active")
        project.active = active if project.scene(active) else (project.scenes[0].id if project.scenes else None)
        return project

    @classmethod
    def starter(cls) -> "Project":
        project = cls()
        project.add_scene("Starting soon")
        return project


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class ProjectStore:
    """The project file under the app's data directory, written atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Project:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            project = Project.from_dict(data)
            if project.scenes:
                return project
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError):
            # A damaged file is kept aside, never overwritten silently.
            if self.path.exists():
                self.path.replace(self.path.with_suffix(".damaged.json"))
        return Project.starter()

    def save(self, project: Project) -> None:
        atomic_write(self.path, json.dumps(project.to_dict(), indent=1, sort_keys=True))
