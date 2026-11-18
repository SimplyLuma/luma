# SPDX-License-Identifier: Apache-2.0
"""Per-contact tracks from the raw streams.

The HID reports are the reference: they include contacts the firmware flags
as not confident, the force of each contact and the device's own scan clock.
evdev slots are matched to HID contacts by time and position (hid-multitouch
passes positions through unscaled), and libinput's per-touch reasoning
(which names slots) is attached to the contact that held the slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import hid
from .libinput_log import Decision, SUPPRESSING_THUMB

ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x2F, 0x35, 0x36
ABS_MT_TOOL_TYPE, ABS_MT_TRACKING_ID, ABS_MT_PRESSURE = 0x37, 0x39, 0x3A
MT_TOOL_PALM = 2


@dataclass
class Sample:
    t: int            # ns, CLOCK_MONOTONIC (arrival)
    x: float          # mm from the left edge
    y: float          # mm from the top edge
    force: float      # 0..1 of the pressure range
    confident: bool | None
    scan: float       # device scan time, seconds, unwrapped
    button: bool


@dataclass
class Track:
    cid: int
    t_down: int
    t_up: int | None = None
    samples: list[Sample] = field(default_factory=list)
    slot: int | None = None
    tracking_id: int | None = None
    kernel_palm_at: int | None = None
    libinput: list[Decision] = field(default_factory=list)
    label: str | None = None           # gold label (guided sessions)
    label_class: str | None = None
    label_how: str | None = None
    marks: list[str] = field(default_factory=list)   # natural-use marks whose window covered this contact
    scenario: str | None = None

    @property
    def key(self) -> str:
        return f"{self.t_down}:{self.cid}"

    @property
    def duration_ms(self) -> float:
        end = self.t_up if self.t_up is not None else (self.samples[-1].t if self.samples else self.t_down)
        return (end - self.t_down) / 1e6

    def firmware_flagged(self) -> bool:
        return any(s.confident is False for s in self.samples)

    def first_unconfident_ms(self) -> float | None:
        for s in self.samples:
            if s.confident is False:
                return (s.t - self.t_down) / 1e6
        return None

    def libinput_verdict(self) -> str:
        """What libinput did with this contact: palm, thumb, thumb-suspect or finger."""
        verdict = "finger"
        for d in self.libinput:
            if d.kind == "palm":
                return "palm"
            if d.kind == "thumb" and d.detail.get("to") == "THUMB_STATE_SUPPRESSED":
                verdict = "thumb"
            elif d.kind == "thumb" and d.detail.get("to") in SUPPRESSING_THUMB and verdict == "finger":
                verdict = "thumb-suspect"
        return verdict

    def libinput_first_ms(self) -> float | None:
        for d in self.libinput:
            if d.kind == "palm" or (d.kind == "thumb" and d.detail.get("to") == "THUMB_STATE_SUPPRESSED"):
                return (d.t - self.t_down) / 1e6
        return None


class TrackBuilder:
    """Incremental: feed records in time order, read .tracks / .active."""

    def __init__(self, descriptor: bytes) -> None:
        self.layout = hid.TouchpadLayout(hid.parse_descriptor(descriptor)) if descriptor else None
        self.active: dict[int, Track] = {}
        self.tracks: list[Track] = []
        self._scan_last: int | None = None
        self._scan_base = 0.0
        self.scan_unit = self.layout.scan_time_unit() if self.layout and self.layout.ok else 1e-4
        self.button = False
        self.reports = 0
        # evdev slot state
        self._slot = 0
        self._slots: dict[int, dict] = {}
        self.slot_history: list[tuple[int, int | None, int, int, Track | None]] = []

    def mm(self, x: int, y: int) -> tuple[float, float]:
        lo = self.layout
        xs = (lo.x_mm or 1.0) / max(1, lo.x_range[1] - lo.x_range[0])
        ys = (lo.y_mm or 1.0) / max(1, lo.y_range[1] - lo.y_range[0])
        return (x - lo.x_range[0]) * xs, (y - lo.y_range[0]) * ys

    def feed_hid(self, t: int, raw: bytes) -> list[tuple[str, Track]]:
        if not self.layout or not self.layout.ok:
            return []
        rep = self.layout.decode(raw)
        if rep is None:
            return []
        self.reports += 1
        if rep.scan_time is not None:
            if self._scan_last is not None and rep.scan_time < self._scan_last:
                self._scan_base += 65536 * self.scan_unit
            self._scan_last = rep.scan_time
        scan = self._scan_base + (rep.scan_time or 0) * self.scan_unit
        self.button = bool(rep.buttons & 1)
        events = []
        seen = set()
        continuation = rep.contact_count == 0
        prange = max(1, self.layout.pressure_range[1])
        for c in rep.contacts:
            if continuation and not c.tip:
                continue
            seen.add(c.contact_id)
            tr = self.active.get(c.contact_id)
            if c.tip:
                if tr is None:
                    tr = Track(c.contact_id, t)
                    self.active[c.contact_id] = tr
                    self.tracks.append(tr)
                    events.append(("down", tr))
                x, y = self.mm(c.x, c.y)
                tr.samples.append(Sample(t, x, y, (c.pressure or 0) / prange, c.confidence, scan, self.button))
                events.append(("move", tr))
            elif tr is not None:
                tr.t_up = t
                del self.active[c.contact_id]
                events.append(("up", tr))
        if not continuation:
            for cid in [k for k in self.active if k not in seen]:
                tr = self.active.pop(cid)
                tr.t_up = t
                events.append(("up", tr))
        return events

    def feed_evdev(self, t: int, frame: list[list[int]]) -> None:
        for etype, code, value in frame:
            if etype != 3:
                continue
            if code == ABS_MT_SLOT:
                self._slot = value
                continue
            st = self._slots.setdefault(self._slot, {})
            if code == ABS_MT_TRACKING_ID:
                if value >= 0:
                    st.clear()
                    st.update(tid=value, start=t, x=None, y=None, track=None)
                else:
                    if st.get("tid") is not None:
                        self.slot_history.append((self._slot, st["tid"], st["start"], t, st.get("track")))
                    st.clear()
            elif code == ABS_MT_POSITION_X:
                st["x"] = value
            elif code == ABS_MT_POSITION_Y:
                st["y"] = value
            elif code == ABS_MT_TOOL_TYPE and value == MT_TOOL_PALM and st.get("track"):
                st["track"].kernel_palm_at = st["track"].kernel_palm_at or t
        for slot, st in self._slots.items():
            if st.get("tid") is not None and st.get("track") is None and st.get("x") is not None:
                st["track"] = self._match(st, t)
                if st["track"]:
                    st["track"].slot, st["track"].tracking_id = slot, st["tid"]

    def _match(self, st: dict, t: int) -> Track | None:
        lo = self.layout
        if not lo:
            return None
        best, dist = None, None
        for tr in list(self.active.values()) + self.tracks[-12:]:
            if tr.slot is not None or not tr.samples or abs(tr.t_down - st["start"]) > 60_000_000:
                continue
            s = min(tr.samples, key=lambda s: abs(s.t - t))
            x, y = self.mm(st["x"], st.get("y") or 0)
            d = (s.x - x) ** 2 + (s.y - y) ** 2
            if dist is None or d < dist:
                best, dist = tr, d
        return best if dist is not None and dist < 25.0 else None

    def finish(self, t_end: int) -> None:
        for slot, st in self._slots.items():
            if st.get("tid") is not None:
                self.slot_history.append((slot, st["tid"], st["start"], t_end, st.get("track")))
        for tr in self.active.values():
            tr.t_up = tr.t_up or (tr.samples[-1].t if tr.samples else t_end)

    def attach_libinput(self, decisions: list[Decision]) -> None:
        """Give each per-touch decision to the contact that held that slot then."""
        for d in decisions:
            if d.touch is None or d.touch < 0:
                continue
            for slot, _tid, start, end, track in self.slot_history:
                if slot == d.touch and track is not None and start - 2_000_000 <= d.t <= end + 60_000_000:
                    track.libinput.append(d)
                    break


def build(records: list[list], descriptor: bytes, decisions: list[Decision] | None = None) -> TrackBuilder:
    tb = TrackBuilder(descriptor)
    last = 0
    for r in records:
        if r[1] == "h":
            tb.feed_hid(r[0], bytes.fromhex(r[2]))
        elif r[1] == "e":
            tb.feed_evdev(r[0], r[3])
        last = r[0]
    tb.finish(last)
    if decisions:
        tb.attach_libinput(decisions)
    return tb
