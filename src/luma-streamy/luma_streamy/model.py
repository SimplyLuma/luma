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


STROKE_POSITIONS = ("inside", "center", "outside")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fit_box(canvas: tuple[int, int], aspect: float, share: float = 0.5) -> tuple[float, float, float, float]:
    """A box of ``aspect`` that fits ``share`` of the canvas on both axes, centred (x, y, width, height)."""
    cw, ch = canvas
    bw, bh = cw * share, ch * share
    if bw / bh > aspect:
        width, height = bh * aspect, bh
    else:
        width, height = bw, bw / aspect
    return (cw - width) / 2, (ch - height) / 2, width, height


def derive_placement(rect: tuple[float, float, float, float], source: tuple[int, int],
                     target: tuple[int, int]) -> tuple[float, float, float, float]:
    """A placement on another canvas, derived from one on ``source``: the whole source canvas scaled
    to fit the target (landscape across a vertical canvas, centred) and the widget with it."""
    sw, sh = source
    tw, th = target
    s = min(tw / sw, th / sh)
    ox, oy = (tw - sw * s) / 2, (th - sh * s) / 2
    x, y, w, h = rect
    return ox + x * s, oy + y * s, w * s, h * s


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
    #: Where the border sits: over the picture's edge ("inside"), across it ("center") or around it.
    stroke_position: str = "inside"

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
        if c.stroke_position not in STROKE_POSITIONS:
            c.stroke_position = "inside"
        return c

    @property
    def stroke_reach(self) -> float:
        """How far the border reaches past the box (lumaframe's stroke-position)."""
        return {"inside": 0.0, "center": self.stroke_width / 2, "outside": self.stroke_width}.get(
            self.stroke_position, 0.0)

    @property
    def margin(self) -> float:
        """Room around the box for a border outside it and the shadow (three sigmas, plus the offset):
        lumaframe's ceil(reach + 1.5 blur + max offset)."""
        import math

        reach = self.stroke_reach
        if self.shadow_blur <= 0 or self.shadow_opacity <= 0:
            return float(math.ceil(reach))
        return float(math.ceil(reach + self.shadow_blur * 1.5 + max(abs(self.shadow_x), abs(self.shadow_y))) + 1)


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
    #: Sound: volume in dB, mute, and the monitor ("off", "only": heard here and not sent, "both").
    volume_db: float = 0.0
    muted: bool = False
    monitor: str = "off"
    #: Source settings: the portal's restore token and stream id, an image path, a device,
    #: and "size" ([width, height] in source pixels) once known.
    source: dict = field(default_factory=dict)
    #: Distorted on purpose (a Shift-drag): its box keeps a shape other than the source's.
    stretched: bool = False
    #: The placement (x, y, width, height) on each canvas format it has been shown in; the
    #: current format's lives in x/y/width/height and is copied here when the format changes.
    placements: dict = field(default_factory=dict)
    #: Groups that are this widget's own on the secondary canvas ("placement": True). A group not
    #: listed is linked: the secondary canvas derives it from the primary one.
    own: dict = field(default_factory=dict)
    #: The secondary canvas's own Look values ({"vertical": {"stroke_width": 12}}): a property listed
    #: there is its own; any other follows the primary canvas's Look (``effects``).
    look: dict = field(default_factory=dict)

    @property
    def natural_aspect(self) -> float | None:
        size = self.source.get("size")
        if isinstance(size, (list, tuple)) and len(size) == 2 and size[0] and size[1]:
            return size[0] / size[1]
        return None

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
    #: The canvas the other one follows (Landscape unless changed in Settings).
    primary: str = "landscape"
    #: The scene Streamy opens on (None: the one last shown).
    startup: str | None = None

    # ── Canvas ──

    @property
    def canvas(self) -> tuple[int, int]:
        _label, width, height = FORMATS[self.format]
        return width, height

    def set_format(self, key: str) -> None:
        """Change the canvas, losslessly both ways. The primary canvas keeps its placements; on the
        other one a widget's placement is linked (derived from the primary: the whole primary canvas
        scaled to fit, centred) until it is moved, resized or cropped there, which makes it its own."""
        if key not in FORMATS:
            raise ModelError(f"unknown canvas format {key!r}")
        if key == self.format:
            return
        old = self.format
        self.format = key
        for scene in self.scenes:
            for w in scene.widgets:
                if not w.has_picture:
                    continue
                w.placements[old] = [w.x, w.y, w.width, w.height]
                self._place(w)

    def _place(self, w: Widget) -> None:
        """Put ``w`` where the current format has it: kept (primary, or its own), or derived (linked)."""
        kept = w.placements.get(self.format)
        own = self.format == self.primary or w.own.get("placement")
        if own and isinstance(kept, list) and len(kept) == 4:
            w.x, w.y, w.width, w.height = (float(v) for v in kept)
            return
        source = w.placements.get(self.primary)
        if isinstance(source, list) and len(source) == 4 and self.format != self.primary:
            _l, pw, ph = FORMATS[self.primary]
            w.x, w.y, w.width, w.height = derive_placement(tuple(float(v) for v in source), (pw, ph), self.canvas)
        elif isinstance(kept, list) and len(kept) == 4:
            w.x, w.y, w.width, w.height = (float(v) for v in kept)
        else:
            # First time on this canvas and nothing to follow (added on the other one): derived once.
            other = next((f for f in FORMATS if f != self.format and isinstance(w.placements.get(f), list)), None)
            if other is not None:
                _l, ow, oh = FORMATS[other]
                w.x, w.y, w.width, w.height = derive_placement(tuple(float(v) for v in w.placements[other]),
                                                               (ow, oh), self.canvas)

    def placement_on(self, w: Widget, fmt: str) -> tuple[float, float, float, float] | None:
        """Where ``w`` is on canvas ``fmt`` (the current one, kept, or derived from the primary)."""
        if fmt == self.format:
            return w.x, w.y, w.width, w.height
        kept = w.placements.get(fmt)
        if fmt == self.primary or w.own.get("placement"):
            return tuple(float(v) for v in kept) if isinstance(kept, list) and len(kept) == 4 else None
        source = (w.x, w.y, w.width, w.height) if self.format == self.primary else w.placements.get(self.primary)
        if source is None:
            return None
        _l, pw, ph = FORMATS[self.primary]
        return derive_placement(tuple(float(v) for v in source), (pw, ph), FORMATS[fmt][1:])

    def match_primary(self, widget_id: str) -> Widget:
        """Link a widget's placement on the secondary canvas to the primary again."""
        _scene, w = self.find_widget(widget_id)
        w.own.pop("placement", None)
        if self.format != self.primary:
            self._place(w)
        return w

    # ── Look per canvas ──

    def effects_on(self, w: Widget, fmt: str | None = None) -> Effects:
        """The Look ``w`` has on canvas ``fmt`` (default: the current one): the primary's, with the
        secondary canvas's own values over it."""
        fmt = fmt or self.format
        own = w.look.get(fmt) if fmt != self.primary else None
        if not own:
            return w.effects
        fx = copy.copy(w.effects)
        names = {f.name for f in fields(Effects)}
        for name, value in own.items():
            if name in names:
                setattr(fx, name, value)
        return fx.clamped()

    def set_effect(self, w: Widget, name: str, value) -> None:
        """Change one Look property where the person is: on the primary canvas the base value (which the
        other canvas follows unless it has its own), on the secondary its own value for that property only."""
        if self.format == self.primary:
            setattr(w.effects, name, value)
        else:
            w.look.setdefault(self.format, {})[name] = value

    def own_look(self, w: Widget, fmt: str | None = None) -> dict:
        fmt = fmt or (self.format if self.format != self.primary else next(f for f in FORMATS if f != self.primary))
        return dict(w.look.get(fmt) or {})

    def sync_look(self, w: Widget, fmt: str | None = None, name: str | None = None) -> None:
        """The secondary canvas follows the primary's Look again (every property, or ``name``)."""
        fmt = fmt or (self.format if self.format != self.primary else next(f for f in FORMATS if f != self.primary))
        own = w.look.get(fmt)
        if not own:
            return
        if name is None:
            w.look.pop(fmt, None)
        else:
            own.pop(name, None)
            if not own:
                w.look.pop(fmt, None)

    #: The Look's properties (the crop is Arrange's).
    LOOK = ("corner_radius", "stroke_width", "stroke_color", "stroke_position", "shadow_blur", "shadow_x",
            "shadow_y", "shadow_opacity", "opacity")

    def unsync_look(self, w: Widget, fmt: str | None = None) -> None:
        """The secondary canvas keeps the Look it has now as its own: later primary edits stop there."""
        fmt = fmt or (self.format if self.format != self.primary else next(f for f in FORMATS if f != self.primary))
        fx = self.effects_on(w, fmt)
        w.look[fmt] = {name: getattr(fx, name) for name in self.LOOK}

    def reset_secondary(self, scene_id: str) -> None:
        """Every widget of a scene follows the primary canvas again."""
        for w in self._require_scene(scene_id).widgets:
            self.match_primary(w.id)

    def reset_aspect(self, widget_id: str) -> Widget:
        """The source's own shape again, keeping the widget's width and centre."""
        _scene, w = self.find_widget(widget_id)
        aspect = w.natural_aspect
        if aspect is None or not w.has_picture:
            return w
        cx, cy = w.x + w.width / 2, w.y + w.height / 2
        w.height = w.width / aspect
        w.x, w.y = cx - w.width / 2, cy - w.height / 2
        w.stretched = False
        return w

    def place_natural(self, widget_id: str) -> Widget:
        """A new widget's place once its source's size is known: a display fits the whole canvas,
        anything else about half of it; centred, in the source's shape."""
        _scene, w = self.find_widget(widget_id)
        aspect = w.natural_aspect
        if aspect is None or not w.has_picture:
            return w
        w.x, w.y, w.width, w.height = fit_box(self.canvas, aspect, 1.0 if w.kind == "display" else 0.5)
        w.stretched = False
        return w

    def restore_shapes(self) -> list[Widget]:
        """Widgets squashed by an older Streamy (not stretched by the person) get their source's shape."""
        fixed = []
        for scene in self.scenes:
            for w in scene.widgets:
                aspect = w.natural_aspect
                if not w.has_picture or w.stretched or aspect is None or not w.height:
                    continue
                if abs(w.width / w.height - aspect) / aspect > 0.01:
                    cx, cy = w.x + w.width / 2, w.y + w.height / 2
                    w.height = w.width / aspect
                    w.x, w.y = cx - w.width / 2, cy - w.height / 2
                    w.placements.clear()
                    fixed.append(w)
        return fixed

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

    def human_name(self, scene_id: str, kind: str, stem: str | None) -> str:
        """A file's name when it reads as one ("Logo"), else the kind's ("Image 2"): never an id."""
        import re

        stem = (stem or "").strip()
        if stem and not re.search(r"\d{6,}|[0-9a-f]{12,}", stem, re.IGNORECASE):
            return stem[:40]
        return self._next_widget_name(self._require_scene(scene_id), kind)

    def tidy_names(self) -> None:
        """Image widgets named after an id-like file name ("image-1793375857294") get the kind's name."""
        import re

        for scene in self.scenes:
            for w in scene.widgets:
                if w.kind == "image" and not w.source.get("named") and re.search(r"\d{6,}", w.name):
                    w.name = ""
                    w.name = self._next_widget_name(scene, w.kind)

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
        if self.format != self.primary and w.has_picture:
            w.own["placement"] = True  # moved on the secondary canvas: this widget's own there now
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
        elif how in ("fill", "fit", "fill-width", "fill-height", "original"):
            # Sizes keep the widget's shape (a stretched one keeps its own): never a distortion.
            aspect = w.width / w.height if w.width and w.height else 16 / 9
            cx, cy = w.x + w.width / 2, w.y + w.height / 2
            if how in ("fill", "fit"):
                wider = aspect > cw / ch
                cover = how == "fill"
                if wider == cover:  # height decides
                    w.height, w.width = ch, ch * aspect
                else:
                    w.width, w.height = cw, cw / aspect
                cx, cy = cw / 2, ch / 2
            elif how == "fill-width":
                w.width, w.height = cw, cw / aspect
                cx = cw / 2
            elif how == "fill-height":
                w.height, w.width = ch, ch * aspect
                cy = ch / 2
            else:
                size = w.source.get("size")
                if not (isinstance(size, (list, tuple)) and len(size) == 2 and size[0] and size[1]):
                    return w
                w.width, w.height = float(size[0]), float(size[1])
                w.stretched = False
            w.x, w.y = cx - w.width / 2, cy - w.height / 2
        else:
            raise ModelError(f"unknown alignment {how!r}")
        return w

    # ── Persistence ──

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, "format": self.format, "primary": self.primary, "startup": self.startup,
                "active": self.active,
                "scenes": [asdict(s) for s in self.scenes]}

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise ModelError("not a Streamy project this version reads")
        project = cls(format=data.get("format", "landscape") if data.get("format") in FORMATS else "landscape",
                      primary=data.get("primary") if data.get("primary") in FORMATS else "landscape")
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
        project.startup = data.get("startup") if project.scene(data.get("startup")) else None
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
                if project.startup:
                    project.active = project.startup  # opens on its startup scene
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
