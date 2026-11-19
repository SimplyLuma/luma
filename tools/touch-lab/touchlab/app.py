# SPDX-License-Identifier: Apache-2.0
"""Touch Lab: the 10-minute active session and a live view of every contact.

The session is a dozen short drills where the drill defines each contact's
role, a test round where live dots show the predicted role and the user
corrects misses, a retrain in seconds, and a follow-up drill aimed at the
least certain role. Reads the recorder's live stream (a socket in
$XDG_RUNTIME_DIR, owned by the user) and writes only labels and the
personal model. The typing box counts key presses for the prompt and keeps
nothing else: no text, no key names.
"""

from __future__ import annotations

import math
import os
import threading
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

from . import contacts, drills, libinput_log, roles  # noqa: E402
from .cli import read_status, sudo_command  # noqa: E402
from .labels import LabelLog, MARKS, data_root, now_ns  # noqa: E402

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
        self.header: dict = {}
        self.descriptor = b""
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
            self.header = rec[2]
            tp = rec[2].get("devices", {}).get("touchpad", {})
            desc = bytes.fromhex(tp.get("report_descriptor", ""))
            absinfo = tp.get("evdev_description", {}).get("absinfo")
            self.builder = (contacts.TrackBuilder(desc) if desc or not absinfo else
                            contacts.EvdevTrackBuilder(absinfo))
            self.descriptor = desc
        elif tag == "h" and self.builder:
            for kind, tr in self.builder.feed_hid(rec[0], bytes.fromhex(rec[2])):
                if kind == "down":
                    for fn in self.listeners:
                        fn(tr)
            if len(self.builder.tracks) > 400:
                del self.builder.tracks[:200]
        elif tag == "e" and self.builder:
            events = self.builder.feed_evdev(rec[0], rec[3])
            if isinstance(self.builder, contacts.EvdevTrackBuilder):
                for kind, tr in events or []:
                    if kind == "down":
                        for fn in self.listeners:
                            fn(tr)
                if len(self.builder.tracks) > 400:
                    del self.builder.tracks[:200]
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
        self.live = None                 # LiveRoles during the test round
        self.focus: str | None = None    # the contact a correction applies to
        self.surface_class = ""
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.add_css_class("touchlab-pad")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Live touchpad contacts"])
        apply_type(self, "caption")

    def do_measure(self, orientation, for_size):
        return (240, 520, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (120, 260, -1, -1)

    def _geometry(self):
        b = self.feed.builder
        if isinstance(b, contacts.EvdevTrackBuilder):
            pw, ph = b.pad_mm
        else:
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
        shown = list(b.active.values())
        if self.focus and self.live:
            shown += [t for t in b.tracks[-12:] if t.key == self.focus and t not in shown]
        for tr in shown:
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
            predicted, prob = self.live.role(tr.key) if self.live else (None, 0.0)
            if predicted:
                role = roles.COLOURS[predicted]
            faded = tr.t_up is not None
            x, y = ox + s.x * k, oy + s.y * k
            r = 7 * k                                   # a fingertip, about 14 mm across
            dot = style.rounded(style.rect(x - r, y - r, 2 * r, 2 * r), r)
            snap.push_rounded_clip(dot)
            snap.append_color(style.colour(self, role, 0.12 if faded else 0.30), style.rect(x - r, y - r, 2 * r, 2 * r))
            snap.pop()
            ring_r = r + 2 + s.force * 26 * k / 2
            ring = style.rounded(style.rect(x - ring_r, y - ring_r, 2 * ring_r, 2 * ring_r), ring_r)
            col = style.colour(self, role)
            snap.append_border(ring, [2, 2, 2, 2], [col, col, col, col])
            text = self.labels.get(tr.key) or {"palm": "palm", "thumb": "thumb", "thumb-suspect": "thumb?"}.get(verdict, "")
            if text in roles.ROLES:
                text = roles.name(text, self.surface_class)
            if predicted:
                text = f"{roles.name(predicted, self.surface_class)} {prob:.0%}"
                if tr.key == self.focus:
                    text = "Last: " + text
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
    """Idle → ten-minute active session (drills → learn → test round → learn → follow-up drill) or
    "just use it"."""

    def __init__(self, application) -> None:
        self.log = LabelLog()
        self.mode: str | None = None           # None, "session" or "free"
        self.phase: str | None = None          # drill, summary, learning, test
        self.trial = None
        self.run_id = None
        self.models = None
        self.labelled: list[tuple[str, object, str]] = []   # (drill, track, role)
        self.stats = drills.RoundStats()
        self.test = None
        commands = CommandRegistry((
            CommandGroup(None, (
                Command("lab.session", "Start the session", lambda: self.start_session(), shortcut=("Ctrl", "G")),
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
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        GLib.timeout_add(33, self._tick)
        GLib.timeout_add(2000, self._status)
        self._status()
        self._load_models()

    # surface and model -------------------------------------------------
    def surface(self) -> tuple[dict, str, tuple]:
        from .train import surface_from_meta
        if not self.feed.header:
            return {"pressure": 1, "force": 1, "confidence": 1, "button": 1, "keyboard": 1}, "force-pad", (149.6, 73.8)
        cached = getattr(self, "_surface", None)
        if cached and cached[0] is self.feed.header:
            mask, cls, pad = cached[1]
        else:
            mask, cls, pad = surface_from_meta(self.feed.header, self.feed.descriptor)
            self._surface = (self.feed.header, (mask, cls, pad))
        b = self.feed.builder
        if isinstance(b, contacts.EvdevTrackBuilder):
            pad = b.pad_mm
        elif b and b.layout and b.layout.ok:
            pad = (b.layout.x_mm or pad[0] or 100.0, b.layout.y_mm or pad[1] or 60.0)
        return mask, cls, (pad[0] or 100.0, pad[1] or 60.0)

    def models_dir(self) -> str:
        return os.path.join(data_root(), "models")

    def _load_models(self) -> None:
        from . import rolemodel
        for name in ("personal.json", "base.json"):
            path = os.path.join(self.models_dir(), name)
            if os.path.exists(path):
                try:
                    self.models = rolemodel.load(path)
                    return
                except (OSError, ValueError, KeyError):
                    continue

        def starter():
            # No model shipped yet: a small synthetic starter, replaced by the personal model.
            from . import synth_roles
            m = rolemodel.train(synth_roles.corpus(n_users=3, seconds=6, seed=2), {"rounds": 12})
            os.makedirs(self.models_dir(), mode=0o700, exist_ok=True)
            rolemodel.save(m, os.path.join(self.models_dir(), "base.json"), {"starter": "synthetic"})
            GLib.idle_add(lambda: setattr(self, "models", m) or False)
        threading.Thread(target=starter, daemon=True).start()

    def build(self) -> None:
        self.prompt = TypeLabel("Touch Lab", role="title-1", wrap=True)
        self.detail = TypeLabel("Teach the touchpad what each touch is: about ten minutes of short drills, then a "
                                "test round where you correct what it gets wrong.", role="body", wrap=True)
        self.progress = TypeLabel("", role="caption")
        self.sentence = TypeLabel("", role="title-3", wrap=True)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=20, margin_end=20, margin_top=16)
        for w in (self.prompt, self.detail, self.sentence, self.progress):
            head.append(w)
        self.sentence.set_visible(False)
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
        self.key = TypeLabel("Ring: force. Amber: the firmware calls it a palm. Violet: libinput is ignoring it. "
                             "Ticks along the top: key presses.", role="caption", wrap=True)
        body.append(self.key)
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
                BarAction("play", "Start the session", primary=True, sensitive=ready, on_activate=self.start_session),
                BarAction("hand", "Just use it", sensitive=ready, on_activate=self.start_free),
            ], more=[BarAction("bar-chart-3", "Open report", on_activate=self.open_report),
                     BarAction("copy", "Copy start command", on_activate=self.copy_command)])
        elif self.mode == "session" and self.phase == "test":
            _mask, cls, _pad = self.surface()
            picks = [BarAction("", f"{i + 1} {roles.name(r, cls)}", on_activate=lambda r=r: self.correct(r))
                     for i, r in enumerate(roles.ROLES)
                     if cls != "touchscreen" or r != "thumb-click"]
            self.bar.show_bar([BarAction("skip-forward", "Done", primary=True, on_activate=self.end_test)] + picks[:3],
                              more=picks[3:] + [BarAction("square", "Stop", on_activate=self.stop)])
        elif self.mode == "session":
            self.bar.show_bar([
                BarAction("skip-forward", "Skip", primary=True, sensitive=self.phase == "drill", on_activate=self.skip),
                BarAction("rotate-ccw", "Again", sensitive=self.phase == "drill", on_activate=self.redo),
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
        if self.mode is None and self.feed.header and not getattr(self, "_described", False):
            self._described = True
            mask, cls, pad = self.surface()
            self.pad.surface_class = cls
            has = [n for n, b in (("force", "force"), ("contact size", "size"), ("firmware palm flag", "confidence"),
                                  ("click", "button")) if mask.get(b)]
            self.progress.set_text(f"{surfaces_name(cls)}, {pad[0]:.0f} × {pad[1]:.0f} mm"
                                   + (f"; reports {', '.join(has)}" if has else ""))
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
        if self.phase == "drill" and self.trial:
            self._check_drill(now)
        elif self.phase == "test" and self.test and self.feed.builder:
            self.pad.live.update(self.feed.builder.tracks[-24:], self.feed.keys, now)
            done = [t for t in self.feed.builder.tracks[-12:] if t.t_up is not None and t.key in self.test["seen"]]
            if done:
                last = max(done, key=lambda t: t.t_up)
                self.pad.focus = last.key
            left = drills.TEST_ROUND_SECONDS - (now - self.test["t0"]) / 1e9
            self.progress.set_text(f"Test round · {max(0, left):.0f} s left · {self.test['corrected']} corrected")
            if left <= 0:
                self.end_test()
        return True

    def _key(self, _c, keyval, _code, _state) -> bool:
        if self.phase != "test":
            return False
        k = keyval - Gdk.KEY_1
        if 0 <= k < len(roles.ROLES):
            self.correct(roles.ROLES[k])
            return True
        return False

    # the session -------------------------------------------------------
    def start_session(self) -> None:
        mask, cls, _pad = self.surface()
        self.pad.surface_class = cls
        self.run_id = uuid.uuid4().hex[:12]
        self.plan = drills.plan(cls, mask)
        self.drill_i = -1
        self.labelled, self.stats, self.test = [], drills.RoundStats(), None
        self.mode = "session"
        self.log.append("run", run=self.run_id, phase="begin", mode="active", surface=cls, drills=len(self.plan),
                        mask=sorted(b for b, v in mask.items() if v))
        self.next_drill()

    def next_drill(self) -> None:
        self.drill_i += 1
        if self.drill_i >= len(self.plan):
            self.learn("drills")
            return
        self.begin_drill(self.plan[self.drill_i])

    def begin_drill(self, d, follow: bool = False) -> None:
        _mask, cls, _pad = self.surface()
        v = d.variant(cls)
        self.phase = "drill"
        self.pad.live, self.pad.focus = None, None
        seconds = drills.FOLLOW_UP_SECONDS if follow else d.seconds
        n = self.drill_i + 1 if not follow else len(self.plan) + 1
        self.trial = {"d": d, "v": v, "n": n, "t0": now_ns(), "tracks": [], "done": False, "seconds": seconds,
                      "follow": follow}
        self.log.append("scenario", run=self.run_id, phase="begin", scenario=d.id, trial=n, intent=d.category,
                        roles=list(v.roles), t0=self.trial["t0"], follow_up=follow)
        self.prompt.set_text(v.title)
        self.detail.set_text(v.text)
        self.sentence.set_visible(d.typing)
        if d.typing:
            self.sentence.set_text(drills.sentence())
        self.pad.labels = {}
        self.typing.set_visible(d.typing)
        self.typing.reset()
        if d.typing:
            self.typing.grab_focus()
        if d.targets:
            self.target.move()
            self.stage.set_visible_child_name("target")
        else:
            self.stage.set_visible_child_name("pad")
        self.show_bar()

    def contact_down(self, tr) -> None:
        if self.phase == "drill" and self.trial and not self.trial["done"]:
            self.trial["tracks"].append(tr)
        elif self.phase == "test" and self.test:
            self.test["seen"].add(tr.key)
            self.test["tracks"].append(tr)

    def _check_drill(self, now: int) -> None:
        tr = self.trial
        if tr["done"]:
            return
        d = tr["d"]
        elapsed = (now - tr["t0"]) / 1e9
        left = tr["seconds"] - elapsed
        what = "Follow-up" if tr["follow"] else f"Drill {tr['n']} of {len(self.plan)} · {d.category}"
        self.progress.set_text(f"{what} · {max(0, left):.0f} s left · {len(tr['tracks'])} touches")
        if d.targets and now >= tr.get("next_dot", 0):
            tr["next_dot"] = now + 2_500_000_000
            self.target.move()
        busy = bool(self.feed.builder and self.feed.builder.active)
        if left <= 0 and (not busy or left < -3):
            self.end_drill()

    def end_drill(self, outcome: str | None = None) -> None:
        tr = self.trial
        tr["done"] = True
        d, v = tr["d"], tr["v"]
        mask, cls, pad = self.surface()
        labels, why = drills.assign(v.rule, tr["tracks"], pad, cls) if outcome is None else ({}, outcome)
        for track in tr["tracks"]:
            role = labels.get(track.key)
            if role:
                track.role = role
                self.labelled.append((d.id, track, role))
                self.log.append("contact", run=self.run_id, scenario=d.id, trial=tr["n"], down=track.t_down,
                                contact_id=track.cid, label=role, role=role, how=f"drill:{v.rule}",
                                **{"class": roles.label_class(role)})
        self.pad.labels = labels
        self.log.append("scenario", run=self.run_id, phase="end", scenario=d.id, trial=tr["n"], outcome=why,
                        contacts=len(tr["tracks"]), labelled=len(labels), keys=self.typing.presses)
        counts: dict[str, int] = {}
        for r in labels.values():
            counts[r] = counts.get(r, 0) + 1
        got = ", ".join(f"{n} {roles.name(r, cls).lower()}" for r, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        self.phase = "summary"
        self.progress.set_text((f"Got {got}." if got else "No touches.") + ("" if why == "ok" else f" ({why})"))
        self.show_bar()
        GLib.timeout_add(1400, self._after_drill)

    def _after_drill(self) -> bool:
        if self.mode != "session":
            return False
        if self.trial and self.trial["follow"]:
            self.learn("final")
        else:
            self.next_drill()
        return False

    def skip(self) -> None:
        if self.phase == "drill" and self.trial and not self.trial["done"]:
            self.end_drill("skipped")

    def redo(self) -> None:
        if self.phase != "drill" or not self.trial:
            return
        tr = self.trial
        self.log.append("scenario", run=self.run_id, phase="discard", scenario=tr["d"].id, trial=tr["n"])
        tr["done"] = True
        self.begin_drill(tr["d"], tr["follow"])

    # learning ----------------------------------------------------------
    def _scenes(self):
        from .rfeatures import Scene, from_track
        mask, cls, pad = self.surface()
        if not self.labelled:
            return []
        t0 = min(t.t_down for _d, t, _r in self.labelled)
        keys = [(k - t0) / 1e6 for k in self.feed.keys]
        by: dict[str, list] = {}
        for d, t, r in self.labelled:
            c = from_track(t, t0)
            c.role = r
            by.setdefault(d, []).append(c)
        return [Scene(cs, keys, pad, mask, cls, f"personal:me:{d}") for d, cs in by.items()]

    def learn(self, stage: str) -> None:
        self.phase = "learning"
        self.typing.set_visible(False)
        self.sentence.set_visible(False)
        self.stage.set_visible_child_name("pad")
        self.prompt.set_text("Learning your hands")
        self.detail.set_text(f"{len(self.labelled)} labelled touches so far. This takes a few seconds.")
        self.progress.set_text("")
        self.show_bar()
        scenes = self._scenes()
        base = self.models

        def work():
            from . import rolemodel
            out, err = base, None
            try:
                if base is None:
                    raise RuntimeError("the starter model isn't ready yet")
                if scenes:
                    out = rolemodel.personalise(base, scenes)
                    os.makedirs(self.models_dir(), mode=0o700, exist_ok=True)
                    rolemodel.save(out, os.path.join(self.models_dir(), "personal.json"),
                                   {"personal": True, "touches": len(self.labelled)})
            except Exception as exc:  # noqa: BLE001 - shown to the user, the session goes on
                err = str(exc)
            GLib.idle_add(self._learned, out, stage, err)
        threading.Thread(target=work, daemon=True).start()

    def _learned(self, models, stage: str, err: str | None) -> bool:
        if self.mode != "session":
            return False
        self.models = models
        if err:
            Toast.show(self.host, f"Couldn't retrain: {err}", kind="error")
        if stage == "drills" and models is None:
            self.finish("Drills saved. The starter model was still being prepared; start again for the test round.")
        elif stage == "drills":
            self.start_test()
        elif stage == "test":
            mask, cls, _pad = self.surface()
            d = drills.follow_up(self.stats, cls, mask)
            if d is None:
                self.finish("Session done. Your touchpad model is saved.")
            else:
                self.begin_drill(d, follow=True)
                self.progress.set_text(f"One more: aimed at {', '.join(roles.name(r, cls).lower() for r in d.variant(cls).roles)}")
        else:
            self.finish("Session done. Your touchpad model is saved; it's used in the next test round.")
        return False

    # test round --------------------------------------------------------
    def start_test(self) -> None:
        from .live_roles import LiveRoles
        mask, cls, pad = self.surface()
        self.phase = "test"
        self.test = {"t0": now_ns(), "seen": set(), "tracks": [], "corrected": 0, "fixed": {}}
        self.pad.live = LiveRoles(self.models, pad, mask)
        self.pad.labels = {}
        self.prompt.set_text("Test round")
        self.detail.set_text("Use it any way you like. Each dot shows what Touch Lab thinks the touch is. When one "
                             "is wrong, press the number of the right one (or pick it in the bar); it applies to the "
                             "last touch.")
        self.log.append("scenario", run=self.run_id, phase="begin", scenario="test-round", trial=1, intent="test",
                        t0=self.test["t0"])
        self.show_bar()

    def correct(self, role: str) -> None:
        if self.phase != "test" or not self.pad.focus:
            return
        tr = next((t for t in self.test["tracks"] if t.key == self.pad.focus), None)
        if tr is None:
            return
        predicted, prob = self.pad.live.role(tr.key)
        self.test["fixed"][tr.key] = role
        self.test["corrected"] += 1
        tr.role = role
        self.labelled.append(("test-round", tr, role))
        self.log.append("contact", run=self.run_id, scenario="test-round", trial=1, down=tr.t_down,
                        contact_id=tr.cid, label=role, role=role, predicted=predicted, how="correction",
                        **{"class": roles.label_class(role)})
        _mask, cls, _pad = self.surface()
        Toast.show(self.host, f"Noted: {roles.name(role, cls).lower()}")

    def end_test(self) -> None:
        if self.phase != "test":
            return
        for tr in self.test["tracks"]:
            predicted, prob = self.pad.live.role(tr.key)
            if predicted:
                self.stats.add(predicted, prob, self.test["fixed"].get(tr.key))
        self.log.append("scenario", run=self.run_id, phase="end", scenario="test-round", trial=1,
                        contacts=len(self.test["tracks"]), corrected=self.test["corrected"], outcome="ok")
        self.pad.live, self.pad.focus = None, None
        self.learn("test")

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
        if self.mode == "session" and self.phase == "drill" and self.trial and not self.trial["done"]:
            self.log.append("scenario", run=self.run_id, phase="discard", scenario=self.trial["d"].id,
                            trial=self.trial["n"])
        self.finish("Stopped. What you did so far is saved.")

    def finish(self, message: str) -> None:
        if self.mode:
            self.log.append("run", run=self.run_id, phase="end", mode="active" if self.mode == "session" else self.mode)
        self.mode, self.phase, self.trial, self.test = None, None, None, None
        self.pad.live, self.pad.focus = None, None
        self.prompt.set_text("Touch Lab")
        self.detail.set_text(message)
        self.progress.set_text("")
        self.sentence.set_visible(False)
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


def surfaces_name(cls: str) -> str:
    return {"legacy-clickpad": "Clickpad (PS/2 or SMBus)", "ptp-clickpad": "Precision touchpad",
            "force-pad": "Force touchpad", "touchscreen": "Touchscreen"}.get(cls, "Touch surface")


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
