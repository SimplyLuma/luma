# SPDX-License-Identifier: Apache-2.0
"""Session-only document annotations, history, and canonical coordinate mapping.

Coordinates are in the displayed source page's points (PDF) or oriented pixels
(image), before any viewing rotation. No signature library or recovery cache.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import math

Point = tuple[float, float]


@dataclass(frozen=True)
class Mark:
    tool: str
    ink: str
    start: Point
    end: Point
    text: str = ""
    width: float = 2.5
    points: tuple[Point, ...] = ()
    page: int = 0

    def moved(self, dx: float, dy: float) -> Mark:
        def move(p):
            return p[0] + dx, p[1] + dy
        return replace(self, start=move(self.start), end=move(self.end),
                       points=tuple(move(p) for p in self.points))

    def bounds(self) -> tuple[float, float, float, float]:
        if self.tool == "step":
            radius=self.width*4.3
            return self.start[0]-radius,self.start[1]-radius,self.start[0]+radius,self.start[1]+radius
        points = self.points if self.tool in ("ink","highlight","sign") and self.points else (self.start, self.end)
        xs, ys = zip(*points)
        pad = max(self.width * 2, 14 if self.tool == "arrow" else 2)
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        if self.tool == "text":
            # Exact Pango extents are supplied by drawing.mark_bounds at export.
            x2 = max(x2, x1 + max(1, len(self.text)) * self.width * 5)
            y2 = max(y2, y1 + self.width * 9)
        return x1-pad, y1-pad, x2+pad, y2+pad


class History:
    def __init__(self):
        self.states: list[tuple[Mark, ...]] = [()]
        self.index = 0
        self.saved: tuple[Mark, ...] = ()

    @property
    def marks(self):
        return self.states[self.index]

    @property
    def dirty(self):
        return self.marks != self.saved

    def apply(self, marks):
        marks = tuple(marks)
        if marks == self.marks:
            return
        del self.states[self.index + 1:]
        self.states.append(marks)
        self.index += 1

    def add(self, mark):
        self.apply((*self.marks, mark))

    def undo(self):
        self.index = max(0, self.index - 1)

    def redo(self):
        self.index = min(len(self.states) - 1, self.index + 1)

    def mark_saved(self):
        self.saved = self.marks


@dataclass(frozen=True)
class Viewport:
    page_width: float
    page_height: float
    width: float
    height: float
    rotation: int = 0
    padding_x: float = 0
    padding_y: float = 0
    zoom: float = 1
    max_scale: float = float("inf")

    @property
    def rotated_size(self):
        return ((self.page_height, self.page_width) if self.rotation % 180
                else (self.page_width, self.page_height))

    @property
    def scale(self):
        rw, rh = self.rotated_size
        return max(0.001, min((self.width-self.padding_x) / max(rw, 1),
                              (self.height-self.padding_y) / max(rh, 1), self.max_scale) * self.zoom)

    @property
    def offset(self):
        rw, rh = self.rotated_size
        return ((self.width-rw*self.scale)/2, (self.height-rh*self.scale)/2)

    def to_view(self, point):
        x, y = point
        w, h = self.page_width, self.page_height
        r = self.rotation % 360
        if r == 90:
            x, y = h-y, x
        elif r == 180:
            x, y = w-x, h-y
        elif r == 270:
            x, y = y, w-x
        ox, oy = self.offset
        return ox+x*self.scale, oy+y*self.scale

    def to_page(self, point, clamp=False):
        ox, oy = self.offset
        x, y = ((point[0]-ox)/max(self.scale, 1e-9),
                (point[1]-oy)/max(self.scale, 1e-9))
        w, h = self.page_width, self.page_height
        r = self.rotation % 360
        if r == 90:
            x, y = y, h-x
        elif r == 180:
            x, y = w-x, h-y
        elif r == 270:
            x, y = w-y, x
        if not clamp and not (0 <= x <= w and 0 <= y <= h):
            return None
        return max(0, min(w, x)), max(0, min(h, y))

    def transform(self, cr):
        ox, oy = self.offset
        cr.translate(ox, oy)
        cr.scale(self.scale, self.scale)
        r = self.rotation % 360
        if r == 90:
            cr.translate(self.page_height, 0)
        elif r == 180:
            cr.translate(self.page_width, self.page_height)
        elif r == 270:
            cr.translate(0, self.page_width)
        cr.rotate(math.radians(r))


@dataclass(frozen=True)
class DocumentViewport:
    """Map edited image display coordinates back to the unchanged source."""
    base: Viewport
    source_width: float
    source_height: float
    crop: tuple | None = None
    flip: bool = False
    straighten: float = 0
    pan: tuple[float, float] = (0, 0)

    @property
    def scale(self):return self.base.scale
    @property
    def offset(self):return tuple(a+b for a,b in zip(self.base.offset,self.pan))
    @property
    def rotated_size(self):return self.base.rotated_size
    def transform(self,cr):
        cr.translate(*self.pan);self.base.transform(cr)

    def _turn(self,point,angle):
        x,y=point;cx,cy=self.source_width/2,self.source_height/2
        co,si=math.cos(math.radians(angle)),math.sin(math.radians(angle))
        return cx+(x-cx)*co-(y-cy)*si,cy+(x-cx)*si+(y-cy)*co

    def to_view(self,point):
        x,y=self._turn(point,self.straighten)
        cx,cy,w,h=self.crop or (0,0,self.source_width,self.source_height)
        x,y=x-cx,y-cy
        if self.flip:x=w-x
        x,y=self.base.to_view((x,y))
        return x+self.pan[0],y+self.pan[1]

    def to_page(self,point,clamp=False):
        point=self.base.to_page((point[0]-self.pan[0],point[1]-self.pan[1]),clamp)
        if point is None:return None
        x,y=point;cx,cy,w,h=self.crop or (0,0,self.source_width,self.source_height)
        if self.flip:x=w-x
        return self._turn((x+cx,y+cy),-self.straighten)
