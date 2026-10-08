# SPDX-License-Identifier: GPL-3.0-only
"""Observe native video presentation without copying or encoding frames."""
from collections import deque
import time
import gi
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GObject


class MediaPaintable(GObject.Object, Gdk.Paintable):
    def __init__(self, source):
        super().__init__()
        self.source = source
        self.serial = 0
        self.presentations = deque(maxlen=600)
        self.handlers = [source.connect('invalidate-contents', self.changed),
                         source.connect('invalidate-size', lambda *_: self.invalidate_size())]

    def changed(self, *_):
        self.serial += 1
        self.invalidate_contents()

    def do_snapshot(self, snapshot, width, height):
        self.source.snapshot(snapshot, width, height)
        self.presentations.append((time.monotonic(), self.serial))

    def do_get_intrinsic_width(self):
        return self.source.get_intrinsic_width()

    def do_get_intrinsic_height(self):
        return self.source.get_intrinsic_height()

    def do_get_intrinsic_aspect_ratio(self):
        return self.source.get_intrinsic_aspect_ratio()

    def do_get_flags(self):
        return self.source.get_flags()

    def close(self):
        for handler in self.handlers:
            self.source.disconnect(handler)
        self.handlers = []


def cadence(samples, now, seconds=3):
    recent = [(stamp, identity) for stamp, identity in samples if stamp >= now-seconds]
    unique = []
    for sample in recent:
        if not unique or sample[1] != unique[-1][1]:
            unique.append(sample)
    if len(unique) < 2:
        return dict(fps=0, frames=len(unique), seconds=0, max_gap_ms=0)
    elapsed = unique[-1][0]-unique[0][0]
    return dict(fps=(len(unique)-1)/elapsed if elapsed else 0, frames=len(unique),
                seconds=elapsed, max_gap_ms=max(b[0]-a[0] for a,b in zip(unique,unique[1:]))*1000)
