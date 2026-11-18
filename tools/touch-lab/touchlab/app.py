# SPDX-License-Identifier: Apache-2.0
"""Touch Lab: guided touchpad sessions and a live view of every contact.

Reads the recorder's live stream (a socket in $XDG_RUNTIME_DIR, owned by the
user) and writes only labels. The typing box counts key presses for the
prompt and keeps nothing else: no text, no key names.
"""

from __future__ import annotations

import math
import os
import random
import socket
import subprocess
import sys
import time
import uuid
import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, AppWindow, BarAction, Command, CommandGroup, CommandRegistry, EmptyState, Island,
    Toast, ToastHost, TypeLabel, add_style_sheet, apply_type, install_appkit, lumaui_icon,
)
from luma_appkit import media_style as style  # noqa: E402

from . import contacts, libinput_log  # noqa: E402
from .cli import read_status, sudo_command  # noqa: E402
from .labels import CLASSES, LabelLog, MARKS, data_root, now_ns  # noqa: E402
from .scenarios import SCENARIOS, assign  # noqa: E402

APP_ID = "org.projectluma.TouchLab"
HERE = os.path.dirname(os.path.abspath(__file__))


# ── Live stream ─────────────────────────────────────────────────────────────

class LiveFeed:
    """The recorder's records, as they happen."""

    def __init__(self, on_change) -> None:
        self.on_change = on_change
        self.sock: socket.socket | None = None
        self.buf = b""
        self.builder: contacts.TrackBuilder | None = None
        self.keys: list[int] = []
        self.events: list[tuple[int, str]] = []      # libinput's recent interpretation
        self.listeners: list = []
        self.connected = False
        self._watch = 0
        self.demo: list[list] = []
        if os.environ.get("TOUCHLAB_DEMO"):
            self._start_demo()
            return
        GLib.timeout_add(1500, self._reconnect)
        self._reconnect()

    def _start_demo(self) -> None:
        """Replay a synthetic session as if it were live (no recorder needed)."""
        from . import synth
        from .session import Session
        import tempfile
        root = tempfile.mkdtemp(prefix="touchlab-demo-")
        s = Session(synth.build_session(root, trials_per=1, free_minutes=0.2))
        recs = s.records()
        base = recs[0][0]
        offset = now_ns() - base
        self.feed([0, "H", s.meta])
        self.demo = [[r[0] + offset] + r[1:] for r in recs]
        self.connected = True
        GLib.timeout_add(16, self._demo_tick)

    def _demo_tick(self) -> bool:
        now = now_ns()
        while self.demo and self.demo[0][0] <= now:
            self.feed(self.demo.pop(0))
        self.on_change()
        return bool(self.demo)

    @property
    def path(self) -> str:
        return os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                            "luma-touch-lab", "live.sock")

    def _reconnect(self) -> bool:
        if self.sock is None:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.path)
                s.setblocking(False)
                self.sock = s
                self.buf = b""
                self._watch = GLib.io_add_watch(s.fileno(), GLib.PRIORITY_DEFAULT,
                                                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._read)
                self.connected = True
                self.on_change()
            except OSError:
                self.connected = False
        return True

    def _drop(self) -> None:
        if self._watch:
            GLib.source_remove(self._watch)
            self._watch = 0
        if self.sock:
            self.sock.close()
        self.sock = None
        self.connected = False
        self.on_change()

    def _read(self, _fd, cond) -> bool:
        try:
            data = self.sock.recv(1 << 16)
        except BlockingIOError:
            return True
        except OSError:
            data = b""
        if not data:
            self._drop()
            return False
        self.buf += data
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            self.feed(rec)
        self.on_change()
        return True

    def feed(self, rec: list) -> None:
        tag = rec[1]
        if tag == "H":
            desc = bytes.fromhex(rec[2].get("devices", {}).get("touchpad", {}).get("report_descriptor", ""))
            self.builder = contacts.TrackBuilder(desc)
        elif tag == "h" and self.builder:
            for kind, tr in self.builder.feed_hid(rec[0], bytes.fromhex(rec[2])):
                if kind == "down":
                    for fn in self.listeners:
                        fn(tr)
            if len(self.builder.tracks) > 400:
                del self.builder.tracks[:200]
        elif tag == "e" and self.builder:
            self.builder.feed_evdev(rec[0], rec[3])
        elif tag == "k":
            self.keys.append(rec[0])
            self.keys = self.keys[-200:]
        elif tag == "l":
            d = libinput_log.parse_line(rec[0], rec[2])
            if not d:
                return
            if d.touch is not None and self.builder:
                tr = self.builder._slots.get(d.touch, {}).get("track")
                if tr:
                    tr.libinput.append(d)
            text = describe(d)
            if text:
                if self.events and self.events[-1][1] == text and rec[0] - self.events[-1][0] < 400_000_000:
                    self.events[-1] = (rec[0], text)
                else:
                    self.events.append((rec[0], text))
                self.events = self.events[-8:]


def describe(d: libinput_log.Decision) -> str | None:
    if d.kind == "motion":
        return "Pointer"
    if d.kind == "scroll":
        return "Scroll"
    if d.kind == "button":
        b = d.detail.get("button", "")
        name = {"BTN_LEFT": "Click", "BTN_RIGHT": "Right click", "BTN_MIDDLE": "Middle click"}.get(b, b)
        return name if d.detail.get("pressed") else None
    if d.kind == "gesture" and d.detail.get("phase") == "begin":
        g = d.detail.get("gesture")
        if g == "hold":
            return None
        return f"{g.capitalize()} · {d.detail.get('fingers')} fingers"
    if d.kind == "palm":
        return f"Palm: touch {d.touch} ({d.detail.get('reason')})"
    if d.kind == "thumb" and d.detail.get("to") == "THUMB_STATE_SUPPRESSED":
        return f"Thumb: touch {d.touch} ignored"
    return None


# ── The pad ────────────────────────────────────────────────────────────────

class PadView(Gtk.Widget):
    """The touchpad at its own proportions: each contact, its force as a ring,
    the firmware's confidence and libinput's reading of it."""

    def __init__(self, feed: LiveFeed) -> None:
        super().__init__()
        self.feed = feed
        self.labels: dict[str, str] = {}
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.add_css_class("touchlab-pad")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Live touchpad contacts"])
        apply_type(self, "caption")

    def do_measure(self, orientation, for_size):
        return (240, 520, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (120, 260, -1, -1)

    def _geometry(self):
        b = self.feed.builder
        pw, ph = ((b.layout.x_mm or 149.6), (b.layout.y_mm or 73.8)) if b and b.layout and b.layout.ok else (149.6, 73.8)
        w, h = self.get_width(), self.get_height()
        scale = min((w - 8) / pw, (h - 8) / ph)
        ox, oy = (w - pw * scale) / 2, (h - ph * scale) / 2
        return pw, ph, scale, ox, oy

    def do_snapshot(self, snap: Gtk.Snapshot) -> None:
        if self.get_width() < 24 or self.get_height() < 24:
            return
        pw, ph, k, ox, oy = self._geometry()
        pad = style.rounded(style.rect(ox, oy, pw * k, ph * k), 14)
        snap.push_rounded_clip(pad)
        snap.append_color(style.colour(self, "luma_well"), style.rect(ox, oy, pw * k, ph * k))
        snap.append_inset_shadow(pad, style.colour(self, "luma_well_shade"), 0, 1, 0, 3)
        snap.pop()
        b = self.feed.builder
        if not b:
            return
        now = now_ns()
        ink = style.colour(self, "luma_ink")
        for tr in list(b.active.values()):
            if not tr.samples:
                continue
            s = tr.samples[-1]
            # Trail: the last 300 ms.
            tail = [p for p in tr.samples if p.t > s.t - 300_000_000]
            trail = style.colour(self, "luma_ink", 0.25)
            for a, c in zip(tail, tail[1:]):
                x0, y0, x1, y1 = ox + a.x * k, oy + a.y * k, ox + c.x * k, oy + c.y * k
                n = max(1, int(math.hypot(x1 - x0, y1 - y0) / 3))
                for i in range(n):
                    snap.append_color(trail, style.rect(x0 + (x1 - x0) * i / n - 1, y0 + (y1 - y0) * i / n - 1, 2, 2))
            verdict = tr.libinput_verdict()
            role = "luma_accent"
            if s.confident is False:
                role = "luma_amber"
            if verdict in ("palm", "thumb"):
                role = "luma_violet"
            x, y = ox + s.x * k, oy + s.y * k
            r = 7 * k                                   # a fingertip, about 14 mm across
            dot = style.rounded(style.rect(x - r, y - r, 2 * r, 2 * r), r)
            snap.push_rounded_clip(dot)
            snap.append_color(style.colour(self, role, 0.30), style.rect(x - r, y - r, 2 * r, 2 * r))
            snap.pop()
            ring_r = r + 2 + s.force * 26 * k / 2
            ring = style.rounded(style.rect(x - ring_r, y - ring_r, 2 * ring_r, 2 * ring_r), ring_r)
            col = style.colour(self, role)
            snap.append_border(ring, [2, 2, 2, 2], [col, col, col, col])
            text = self.labels.get(tr.key) or {"palm": "palm", "thumb": "thumb", "thumb-suspect": "thumb?"}.get(verdict, "")
            if s.confident is False:
                text = (text + " · " if text else "") + "firmware: palm"
            if s.button:
                text = (text + " · " if text else "") + "click"
            if text:
                layout = self.create_pango_layout(text)
                snap.save()
                snap.translate(Graphene.Point().init(x - r, y + ring_r + 4))
                snap.append_layout(layout, ink)
                snap.restore()
        # Keys: a short tick along the top edge for each press in the last 2 s.
        for t in self.feed.keys:
            age = (now - t) / 1e9
            if 0 <= age < 2:
                snap.append_color(style.colour(self, "luma_ink", 0.5 * (1 - age / 2)),
                                  style.rect(ox + pw * k * (1 - age / 2), oy - 6, 2, 5))


class TypingBox(Gtk.Box):
    """Somewhere to type. It counts key presses for the prompt; it stores nothing."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.presses = 0
        self.set_focusable(True)
        self.add_css_class("touchlab-typing")
        self.title = TypeLabel("Type here", role="label")
        self.note = TypeLabel("Nothing you type is kept.", role="caption")
        self.dots = TypeLabel("", role="body")
        for w in (self.title, self.dots, self.note):
            self.append(w)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.grab_focus())
        self.add_controller(click)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Typing box; nothing typed is kept"])

    def _key(self, _c, keyval, _code, state) -> bool:
        if keyval in (Gdk.KEY_Escape, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) or state & Gdk.ModifierType.CONTROL_MASK:
            return False
        self.presses += 1                         # only the count survives this line
        self.dots.set_text("•" * min(40, self.presses))
        return True

    def reset(self) -> None:
        self.presses = 0
        self.dots.set_text("")


class Target(Gtk.Widget):
    """A dot to put the pointer on (the precision scenario)."""

    def __init__(self) -> None:
        super().__init__()
        self.pos = (0.5, 0.5)
        self.hit = False
        self.set_hexpand(True)
        self.set_vexpand(True)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        self.add_controller(motion)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Pointer target"])

    def move(self) -> None:
        self.pos = (random.uniform(0.15, 0.85), random.uniform(0.2, 0.8))
        self.hit = False
        self.queue_draw()

    def _motion(self, _c, x, y) -> None:
        cx, cy = self.pos[0] * self.get_width(), self.pos[1] * self.get_height()
        hit = math.hypot(x - cx, y - cy) < 7
        if hit != self.hit:
            self.hit = hit
            self.queue_draw()

    def do_snapshot(self, snap) -> None:
        cx, cy = self.pos[0] * self.get_width(), self.pos[1] * self.get_height()
        role = "luma_green" if self.hit else "luma_accent"
        for r, a in ((12, 0.25), (5, 1.0)):
            snap.push_rounded_clip(style.rounded(style.rect(cx - r, cy - r, 2 * r, 2 * r), r))
            snap.append_color(style.colour(self, role, a), style.rect(cx - r, cy - r, 2 * r, 2 * r))
            snap.pop()


# ── The window ─────────────────────────────────────────────────────────────

class ShotBin(Gtk.Widget):
    """Holds the body and keeps the last frame it drew, for TOUCHLAB_SCREENSHOT."""

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.set_layout_manager(Gtk.BinLayout())
        child.set_parent(self)
        self.child = child
        self.node = None

    def do_snapshot(self, snap: Gtk.Snapshot) -> None:
        inner = Gtk.Snapshot()
        self.snapshot_child(self.child, inner)
        self.node = inner.to_node()
        if self.node is not None:
            snap.append_node(self.node)


class TouchLabWindow(AppWindow):
    REPEATS = 2

    def __init__(self, application) -> None:
        self.log = LabelLog()
        self.mode: str | None = None           # None, "guided" or "free"
        self.plan: list[tuple] = []
        self.trial = None
        self.run_id = None
        commands = CommandRegistry((
            CommandGroup(None, (
                Command("lab.guided", "Guided pass", lambda: self.start_guided(), shortcut=("Ctrl", "G")),
                Command("lab.free", "Just use it", lambda: self.start_free(), shortcut=("Ctrl", "U")),
                Command("lab.stop", "Stop", lambda: self.stop(), enabled=lambda: self.mode is not None),
                Command("lab.report", "Open report", lambda: self.open_report()),
            )),
            CommandGroup(None, (Command("app.quit", "Quit Touch Lab", application.quit, shortcut=("Ctrl", "Q")),)),
        ))
        super().__init__(application=application, app_id=APP_ID, title="Touch Lab", icon_name="input-touchpad",
                         commands=commands, default_width=980, default_height=700,
                         minimum_width=360, minimum_height=420)
        self.feed = LiveFeed(self.refresh)
        self.feed.listeners.append(self.contact_down)
        self.build()
        GLib.timeout_add(33, self._tick)
        GLib.timeout_add(2000, self._status)
        self._status()

    def build(self) -> None:
        self.prompt = TypeLabel("Touch Lab", role="title-1", wrap=True)
        self.detail = TypeLabel("Teach the touchpad what you meant. A guided pass takes about five minutes; "
                                "or just use the laptop and mark mistakes as they happen.", role="body", wrap=True)
        self.progress = TypeLabel("", role="caption")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=20, margin_end=20, margin_top=16)
        head.append(self.prompt)
        head.append(self.detail)
        head.append(self.progress)
        self.pad = PadView(self.feed)
        self.target = Target()
        self.typing = TypingBox()
        self.stage = Gtk.Stack(vexpand=True, hexpand=True)
        self.stage.add_named(self.pad, "pad")
        overlay = Gtk.Overlay(child=self.target)
        self.stage.add_named(overlay, "target")
        self.libinput_line = TypeLabel("", role="caption")
        self.empty = EmptyState("Not recording", "Copy the start command and run it in a terminal; it asks for "
                                "your password once. Touch Lab picks it up by itself.", lumaui_icon("circle-alert"))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=20, margin_end=20,
                       margin_bottom=96)
        body.append(self.stage)
        body.append(self.typing)
        body.append(self.libinput_line)
        key = TypeLabel("Ring: force. Amber: the firmware calls it a palm. Violet: libinput is ignoring it. "
                        "Ticks along the top: key presses.", role="caption", wrap=True)
        body.append(key)
        self.pages = Gtk.Stack(vexpand=True)
        self.pages.add_named(body, "live")
        self.pages.add_named(self.empty, "empty")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        column.append(head)
        column.append(self.pages)
        island = Island()
        island.append(column)
        self.host = ToastHost(island)
        self.recording = TypeLabel("", role="caption")
        self.set_trailing(self.recording)
        self.shot = ShotBin(self.host)
        self.set_body(self.shot)
        self.bar = ActionCenter()
        self.bar.attach(self.host)
        self.typing.set_visible(False)
        self.show_bar()

    # bar ---------------------------------------------------------------
    def show_bar(self) -> None:
        ready = self.feed.connected
        self._bar_ready = ready
        if self.mode is None and not ready:
            self.bar.show_bar([BarAction("copy", "Copy start command", primary=True, on_activate=self.copy_command)],
                              more=[BarAction("bar-chart-3", "Open report", on_activate=self.open_report)])
        elif self.mode is None:
            self.bar.show_bar([
                BarAction("play", "Guided pass", primary=True, sensitive=ready, on_activate=self.start_guided),
                BarAction("hand", "Just use it", sensitive=ready, on_activate=self.start_free),
            ], more=[BarAction("bar-chart-3", "Open report", on_activate=self.open_report),
                     BarAction("copy", "Copy start command", on_activate=self.copy_command)])
        elif self.mode == "guided":
            self.bar.show_bar([
                BarAction("skip-forward", "Skip", primary=True, on_activate=self.skip),
                BarAction("rotate-ccw", "Again", on_activate=self.redo),
                BarAction("square", "Stop", on_activate=self.stop),
            ])
        else:
            self.bar.show_bar([
                BarAction("triangle-alert", "That was wrong", primary=True, on_activate=lambda: self.mark("wrong")),
                BarAction("circle-help", "Almost wrong", on_activate=lambda: self.mark("almost")),
                BarAction("square", "Stop", on_activate=self.stop),
            ])

    # status ------------------------------------------------------------
    def _status(self) -> bool:
        st = read_status(data_root())
        running = bool(st and st["running"]) and self.feed.connected or bool(self.feed.demo)
        paused = st.get("paused") if st else None
        self.recording.set_text("Recording" + (f" · paused ({paused})" if paused else "") if running else "Not recording")
        self.pages.set_visible_child_name("live" if running else "empty")
        return True

    def refresh(self) -> None:
        if not hasattr(self, "recording"):
            return
        if self.mode is None and getattr(self, "_bar_ready", None) != self.feed.connected:
            self.show_bar()

    def _tick(self) -> bool:
        self.pad.queue_draw()
        now = now_ns()
        recent = [text for t, text in self.feed.events if now - t < 1_500_000_000]
        self.libinput_line.set_text("libinput: " + (" → ".join(recent[-4:]) if recent else "–"))
        if self.trial:
            self._check_trial(now)
        return True

    # guided pass -------------------------------------------------------
    def start_guided(self) -> None:
        self.run_id = uuid.uuid4().hex[:12]
        self.plan = [(s, n + 1) for s in SCENARIOS for n in range(self.REPEATS)]
        self.retried = set()
        random.shuffle(self.plan)
        self.total = len(self.plan)
        self.mode = "guided"
        self.log.append("run", run=self.run_id, phase="begin", mode="guided", trials=self.total)
        self.show_bar()
        self.next_trial()

    def next_trial(self) -> None:
        if not self.plan:
            self.finish("Guided pass done. Thank you.")
            return
        scenario, n = self.plan.pop(0)
        self.begin(scenario, n)

    def begin(self, scenario, n) -> None:
        self.trial = {"s": scenario, "n": n, "t0": now_ns(), "tracks": [], "last_up": None, "done": False}
        self.log.append("scenario", run=self.run_id, phase="begin", scenario=scenario.id, trial=n,
                        intent=scenario.intent, t0=self.trial["t0"])
        self.prompt.set_text(scenario.title)
        self.detail.set_text(scenario.detail)
        self.progress.set_text(f"{self.total - len(self.plan)} of {self.total}")
        self.pad.labels = {}
        self.typing.set_visible(scenario.typing)
        self.typing.reset()
        if scenario.typing:
            self.typing.grab_focus()
        if scenario.id == "precision":
            self.target.move()
            self.stage.set_visible_child_name("target")
        else:
            self.stage.set_visible_child_name("pad")

    def contact_down(self, tr) -> None:
        if self.trial and not self.trial["done"]:
            self.trial["tracks"].append(tr)

    def _check_trial(self, now: int) -> None:
        tr = self.trial
        if tr["done"]:
            return
        s = tr["s"]
        b = self.feed.builder
        active = bool(b and b.active)
        elapsed = (now - tr["t0"]) / 1e9
        if active:
            tr["last_up"] = None
        elif tr["tracks"] and tr["last_up"] is None:
            tr["last_up"] = now
        quiet = tr["last_up"] is not None and (now - tr["last_up"]) / 1e9 > (1.2 if s.typing else 0.7)
        if s.typing:
            finished = quiet and self.typing.presses >= 4 or elapsed > s.seconds + 6
        else:
            finished = quiet or elapsed > s.seconds + (6 if not tr["tracks"] else 0)
        if finished:
            self.end_trial()

    def end_trial(self, outcome: str | None = None) -> None:
        tr = self.trial
        tr["done"] = True
        s = tr["s"]
        labels, why = assign(s.rule, tr["tracks"]) if outcome is None else ({}, outcome)
        for track in tr["tracks"]:
            label = labels.get(track.key)
            if label:
                self.log.append("contact", run=self.run_id, scenario=s.id, trial=tr["n"], down=track.t_down,
                                contact_id=track.cid, label=label, how=f"rule:{s.rule}",
                                **{"class": CLASSES[label]})
        self.pad.labels = labels
        self.log.append("scenario", run=self.run_id, phase="end", scenario=s.id, trial=tr["n"], outcome=why,
                        contacts=len(tr["tracks"]), labelled=len(labels), keys=self.typing.presses)
        self.progress.set_text(f"{self.total - len(self.plan)} of {self.total} · "
                                + ("got it" if why == "ok" else f"{why}; it'll come round again"))
        if why not in ("ok", "skipped") and (s.id, tr["n"]) not in self.retried:
            self.retried.add((s.id, tr["n"]))
            self.plan.insert(min(len(self.plan), 3), (s, tr["n"]))
        GLib.timeout_add(900, self._after_trial)

    def _after_trial(self) -> bool:
        if self.mode == "guided":
            self.next_trial()
        return False

    def skip(self) -> None:
        if self.trial and not self.trial["done"]:
            self.end_trial("skipped")

    def redo(self) -> None:
        if not self.trial:
            return
        tr = self.trial
        self.log.append("scenario", run=self.run_id, phase="discard", scenario=tr["s"].id, trial=tr["n"])
        tr["done"] = True
        self.begin(tr["s"], tr["n"])

    # just use it -------------------------------------------------------
    def start_free(self) -> None:
        self.run_id = uuid.uuid4().hex[:12]
        self.mode = "free"
        self.log.append("run", run=self.run_id, phase="begin", mode="free")
        self.prompt.set_text("Just use it")
        self.detail.set_text("Use the laptop as usual. When the touchpad gets it wrong, press Super+Alt+W "
                              "(or Super+Alt+A when it nearly did). This window can stay in the background.")
        self.progress.set_text("")
        self.typing.set_visible(False)
        self.stage.set_visible_child_name("pad")
        self.show_bar()

    def mark(self, which: str) -> None:
        self.log.mark(which, source="app")
        Toast.show(self.host, f"Marked: {MARKS[which].lower()}")

    # common ------------------------------------------------------------
    def stop(self) -> None:
        if self.mode == "guided" and self.trial and not self.trial["done"]:
            self.log.append("scenario", run=self.run_id, phase="discard", scenario=self.trial["s"].id,
                            trial=self.trial["n"])
        self.finish("Stopped. What you did so far is saved.")

    def finish(self, message: str) -> None:
        if self.mode:
            self.log.append("run", run=self.run_id, phase="end", mode=self.mode)
        self.mode, self.trial, self.plan = None, None, []
        self.prompt.set_text("Touch Lab")
        self.detail.set_text(message)
        self.progress.set_text("")
        self.typing.set_visible(False)
        self.stage.set_visible_child_name("pad")
        self.show_bar()

    def copy_command(self) -> None:
        self.get_clipboard().set(sudo_command())
        Toast.show(self.host, "Command copied")

    def open_report(self) -> None:
        out = os.path.join(data_root(), "reports", "latest.html")
        try:
            subprocess.run([sys.executable, "-m", "touchlab.cli", "report", "--out", out], check=True,
                           cwd=os.path.dirname(HERE), capture_output=True, timeout=300)
            Gtk.FileLauncher.new(Gio.File.new_for_path(out)).launch(self, None, None, None)
        except (subprocess.SubprocessError, OSError) as exc:
            Toast.show(self.host, f"Couldn't build the report: {exc}", kind="error")


class TouchLabApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_activate(self) -> None:
        install_appkit()
        add_style_sheet(os.path.join(HERE, "data", "touchlab.css"))
        win = self.get_active_window() or TouchLabWindow(self)
        win.present()
        shot = os.environ.get("TOUCHLAB_SCREENSHOT")
        if shot:
            tries = [0]

            def take() -> bool:
                tries[0] += 1
                if save_screenshot(win, shot) or tries[0] > 20:
                    self.quit()
                    return False
                return True
            GLib.timeout_add(int(float(os.environ.get("TOUCHLAB_SCREENSHOT_AFTER", "2.5")) * 1000),
                             lambda: GLib.timeout_add(250, take) and False)


def save_screenshot(widget: Gtk.Widget, path: str) -> bool:
    """Render the window to a PNG (TOUCHLAB_SCREENSHOT, for checks without a screen)."""
    bin_ = widget.shot
    w, h = bin_.get_width(), bin_.get_height()
    node = bin_.node
    if node is None:
        bin_.queue_draw()
        return False
    renderer = widget.get_native().get_renderer()
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
    texture.save_to_png(path)
    return True


def main() -> int:
    return TouchLabApplication().run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
