# SPDX-License-Identifier: Apache-2.0
"""Stand-in pictures for STREAMY_FAKE_SOURCES=1.

Portal capture can't be clicked through in a VM or a test, so a display,
a window and a camera are drawn once (cairo) and fed through the same
pipeline as a real capture: lumaframe, the mixer, the encoders.
"""
from __future__ import annotations

import math
from pathlib import Path

import cairo


def _rounded(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _lines(cr: cairo.Context, x: float, y: float, w: float, rows: int, ink: tuple, step: float = 26) -> None:
    for i in range(rows):
        width = w * (0.55 + 0.4 * ((i * 37) % 10) / 10)
        _rounded(cr, x, y + i * step, width, 10, 5)
        cr.set_source_rgba(*ink)
        cr.fill()


def _window(cr: cairo.Context, x: float, y: float, w: float, h: float, accent: tuple) -> None:
    cr.save()
    _rounded(cr, x + 6, y + 14, w, h, 18)
    cr.set_source_rgba(0, 0, 0, 0.22)
    cr.fill()
    _rounded(cr, x, y, w, h, 18)
    cr.set_source_rgb(0.97, 0.97, 0.98)
    cr.fill()
    cr.rectangle(x + 18, y + 64, 210, h - 82)
    cr.set_source_rgb(0.93, 0.94, 0.95)
    cr.fill()
    for i, dot in enumerate(((0.93, 0.36, 0.33), (0.96, 0.75, 0.27), (0.35, 0.78, 0.42))):
        cr.arc(x + w - 70 + i * 22, y + 30, 6, 0, 2 * math.pi)
        cr.set_source_rgb(*dot)
        cr.fill()
    _rounded(cr, x + 24, y + 22, 160, 16, 8)
    cr.set_source_rgb(0.80, 0.82, 0.85)
    cr.fill()
    _lines(cr, x + 32, y + 88, 170, 8, (0.78, 0.80, 0.83, 1))
    _rounded(cr, x + 256, y + 88, w - 290, 150, 14)
    cr.set_source_rgb(*accent)
    cr.fill()
    _lines(cr, x + 256, y + 268, w - 300, max(3, int((h - 300) / 30)), (0.82, 0.84, 0.86, 1), 30)
    cr.restore()


def display(path: Path) -> Path:
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, 1920, 1080)
    cr = cairo.Context(s)
    g = cairo.LinearGradient(0, 0, 1920, 1080)
    g.add_color_stop_rgb(0, 0.16, 0.30, 0.55)
    g.add_color_stop_rgb(0.55, 0.42, 0.36, 0.66)
    g.add_color_stop_rgb(1, 0.93, 0.56, 0.47)
    cr.set_source(g)
    cr.paint()
    cr.rectangle(0, 0, 1920, 34)
    cr.set_source_rgba(0, 0, 0, 0.35)
    cr.fill()
    _window(cr, 140, 120, 1040, 700, (0.30, 0.52, 0.90))
    _window(cr, 1010, 300, 760, 620, (0.95, 0.62, 0.32))
    _rounded(cr, 720, 990, 480, 64, 22)
    cr.set_source_rgba(1, 1, 1, 0.25)
    cr.fill()
    for i in range(7):
        _rounded(cr, 744 + i * 64, 1000, 44, 44, 12)
        cr.set_source_rgba(1, 1, 1, 0.75)
        cr.fill()
    s.write_to_png(str(path))
    return path


def window(path: Path) -> Path:
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1280, 800)
    cr = cairo.Context(s)
    cr.set_source_rgb(0.11, 0.12, 0.14)
    cr.paint()
    _window(cr, 0, 0, 1280, 800, (0.36, 0.70, 0.55))
    s.write_to_png(str(path))
    return path


def camera(path: Path) -> Path:
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, 1280, 720)
    cr = cairo.Context(s)
    g = cairo.LinearGradient(0, 0, 1280, 720)
    g.add_color_stop_rgb(0, 0.36, 0.33, 0.31)
    g.add_color_stop_rgb(1, 0.18, 0.17, 0.17)
    cr.set_source(g)
    cr.paint()
    # A lamp's glow and a shelf behind.
    glow = cairo.RadialGradient(1060, 160, 10, 1060, 160, 380)
    glow.add_color_stop_rgba(0, 1, 0.82, 0.55, 0.55)
    glow.add_color_stop_rgba(1, 1, 0.82, 0.55, 0)
    cr.set_source(glow)
    cr.paint()
    cr.rectangle(80, 250, 360, 16)
    cr.set_source_rgb(0.45, 0.33, 0.24)
    cr.fill()
    for i, colour in enumerate(((0.70, 0.30, 0.25), (0.25, 0.45, 0.60), (0.85, 0.70, 0.35), (0.35, 0.55, 0.40))):
        cr.rectangle(100 + i * 52, 150 + (i % 2) * 18, 40, 100 - (i % 2) * 18)
        cr.set_source_rgb(*colour)
        cr.fill()
    # Shoulders and head.
    cr.save()
    cr.translate(640, 760)
    cr.scale(1.0, 0.62)
    cr.arc(0, 0, 330, math.pi, 2 * math.pi)
    cr.restore()
    cr.set_source_rgb(0.20, 0.33, 0.52)
    cr.fill()
    cr.arc(640, 330, 118, 0, 2 * math.pi)
    cr.set_source_rgb(0.86, 0.66, 0.52)
    cr.fill()
    cr.arc(640, 296, 122, math.pi * 1.02, math.pi * 1.98)
    cr.set_source_rgb(0.22, 0.16, 0.12)
    cr.fill()
    s.write_to_png(str(path))
    return path


def images(folder: Path) -> dict[str, str]:
    """Draw the stand-ins once; returns kind -> PNG path."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for kind, draw in (("display", display), ("window", window), ("camera", camera)):
        path = folder / f"{kind}.png"
        if not path.exists():
            draw(path)
        out[kind] = str(path)
    return out


def seed(project, pictures: dict[str, str]) -> None:
    """A believable project for check runs: three scenes with every kind of widget."""
    if any(scene.widgets for scene in project.scenes):
        return
    first = project.scenes[0]
    first.name = "Gameplay"
    project.add_widget(first.id, "display", source={"fake-image": pictures["display"]})
    camera = project.add_widget(first.id, "camera", source={"fake-image": pictures["camera"]}, aspect=16 / 9)
    camera.name = "Face camera"
    project.add_widget(first.id, "microphone")
    project.add_widget(first.id, "desktop-audio")
    chat = project.add_scene("Just chatting")
    big = project.add_widget(chat.id, "camera", source={"fake-image": pictures["camera"]}, aspect=16 / 9)
    project.set_rect(big.id, 96, 90, 1120, 630)
    window = project.add_widget(chat.id, "window", source={"fake-image": pictures["window"]}, aspect=1.6)
    project.set_rect(window.id, 1280, 210, 560, 350)
    window.effects.corner_radius = 18
    window.effects.shadow_blur = 36
    window.effects.shadow_y = 10
    project.add_widget(chat.id, "microphone")
    project.add_scene("Be right back")


class FakeCameras:
    """Check runs' cameras (STREAMY_FAKE_CAMERAS: labels joined by "|"; "" for none)."""

    def __init__(self, labels: list[str]) -> None:
        from .portals import Camera

        modes = ((1920, 1080, 30), (1280, 720, 30), (1280, 720, 60), (640, 480, 30))
        self.cameras = [Camera(f"fake-{i}", f"fake_camera_{i}", label, modes) for i, label in enumerate(labels)]
        self.ready = True

    def find(self, name):
        if name:
            return next((c for c in self.cameras if c.name == name), None)
        return self.cameras[0] if self.cameras else None


class FakePortals:
    """What the window asks of Portals, for check runs with STREAMY_FAKE_SOURCES=1: no D-Bus."""

    def __init__(self) -> None:
        import os

        labels = os.environ.get("STREAMY_FAKE_CAMERAS", "Built-in Front Camera|USB Camera")
        self.cameras = FakeCameras([label for label in labels.split("|") if label])
        self.captures: dict = {}
        self.on_ready = self.on_ended = self.on_cameras = None

    def audio_capture_available(self) -> bool:
        return True

    def close(self, widget_id: str) -> None:
        self.captures.pop(widget_id, None)

    def source_for(self, _widget):
        return None, None
