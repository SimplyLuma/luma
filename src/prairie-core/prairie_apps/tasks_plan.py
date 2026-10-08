# SPDX-License-Identifier: Apache-2.0
"""Tasks: Plan my day (v71, "Tasks: Plan my day", phone).

A Tasks-only surface, like a dial pad is Phone's: the week's loose tasks as a
stack of cards. Swipe right (or Today) puts the top one on today; swipe left
(or Not today) pushes it out; Skip leaves it as it was. The card tilts with
the finger and stamps TODAY or NOT TODAY. It ends on what today now holds.

The window decides what a decision writes (`on_decide(task, 1 | -1 | 0)`);
the deck keeps its own copy of each task's day so the ending is right even
while the writes are still on their way.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Graphene, Gtk, Pango  # noqa: E402

from luma_appkit import BarAction, icons  # noqa: E402
from luma_appkit.action_center import make_control  # noqa: E402

from .tasks_data import day_label  # noqa: E402
from .tasks_widgets import column, face, line, list_dot, named, text  # noqa: E402

COMMIT = 100      # px: past this a release decides (v71 `Math.abs(dx) > 100`)
STAMP = 60        # px: the stamp shows past this (v71 `dx > 60`)
TILT = 18         # px of drag per degree of tilt (v71 `rotate(dx / 18 deg)`)
FLY = 480         # px the card flies off by (v71 `translateX(d * 480px)`)
STACK_STEP = 14   # each card under the top one sits 14 px lower...
STACK_SCALE = .05  # ...and 5% smaller (v71 .tkpc --k)


class PlanButton(Gtk.Button):
    """The warm card on Today: "Plan my day. 6 things could fit today. Swipe through them." """

    def __init__(self, count: int, on_activate) -> None:
        super().__init__(hexpand=True, vexpand=False)
        self.add_css_class("tk-plan-button")
        row = line(12)
        tile = column(0); tile.add_css_class("tk-plan-glyph"); tile.set_valign(Gtk.Align.CENTER)
        glyph = icons.image("layers", pixel_size=20); glyph.set_vexpand(True); tile.append(glyph)
        row.append(tile)
        words = column(2); words.set_hexpand(True)
        words.append(text("Plan my day", "section-day", weight=700))
        words.append(text(f"{count} thing{'s' if count != 1 else ''} could fit today. Swipe through them.", "meta", wrap=True))
        row.append(words)
        chevron = icons.image("chevron-right", pixel_size=18); chevron.add_css_class("tk-plan-chevron")
        row.append(chevron)
        self.set_child(row)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Plan my day"])
        self.connect("clicked", lambda *_: on_activate())


class _Tilt(Gtk.Widget):
    """One card, drawn moved and turned: `dx` follows the finger, `depth` is its place in the stack."""

    __gtype_name__ = "TasksPlanTilt"

    def __init__(self, child: Gtk.Widget, depth: int) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.child, self.depth, self.dx = child, depth, 0.0
        child.set_parent(self)

    def set_dx(self, dx: float) -> None:
        self.dx = dx
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.child.measure(orientation, for_size)[:2] + (-1, -1)

    def do_size_allocate(self, width, height, baseline):
        self.child.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        scale = 1 - self.depth * STACK_SCALE
        snapshot.save()
        snapshot.translate(Graphene.Point().init(width / 2 + self.dx, height / 2 + self.depth * STACK_STEP))
        if self.dx: snapshot.rotate(self.dx / TILT)
        if scale != 1: snapshot.scale(scale, scale)
        snapshot.translate(Graphene.Point().init(-width / 2, -height / 2))
        self.snapshot_child(self.child, snapshot)
        snapshot.restore()

    def do_dispose(self):
        if self.child is not None and self.child.get_parent() is self:
            self.child.unparent()
        self.child = None


class PlanDeck(Gtk.Box):
    """The full-window stack. `tasks` are the candidates in order; `today` is what's on today already."""

    def __init__(self, tasks, today, *, lists, people, today_date, fixture_dir, on_decide, on_close) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.set_name("tk-plan")
        self.add_css_class("tk-plan")
        self.tasks, self.lists, self.people = list(tasks), lists, people
        self.today_date, self.fixture_dir = today_date, fixture_dir
        self.on_decide, self.on_close = on_decide, on_close
        self.index, self.yes, self.no = 0, [], []
        self.due = {t["id"]: t.get("due") for t in [*tasks, *today]}
        self.today = {t["id"]: t for t in today}
        self._busy = False
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        width = Adw.Clamp(maximum_size=480, tightening_threshold=480, child=self.content)
        self.height_clamp = Adw.Clamp(orientation=Gtk.Orientation.VERTICAL,
                                     maximum_size=600, tightening_threshold=600,
                                     vexpand=True, child=width)
        self.append(self.height_clamp)
        self._draw()

    def set_compact(self, compact):
        self.height_clamp.set_maximum_size(10000 if compact else 600)
        self.height_clamp.set_tightening_threshold(10000 if compact else 600)

    # ── what is shown ──────────────────────────────────────────────────────

    def _head(self, counter: str | None) -> Gtk.Widget:
        head = line(10); head.add_css_class("tk-plan-head")
        head.append(text("Plan my day", "card-title", weight=750))
        tally = text(counter or "", "body", muted=True, hexpand=True)
        head.append(tally)
        close = named(make_control(BarAction("x", tooltip="Close", on_activate=self.finish), size="tool"), "tk-plan-close")
        head.append(close)
        return head

    def _draw(self) -> None:
        while (child := self.content.get_first_child()) is not None:
            self.content.remove(child)
        if self.index >= len(self.tasks):
            self._ending(); return
        self.content.append(self._head(f"{self.index + 1} of {len(self.tasks)}"))
        stack = Gtk.Overlay(vexpand=True); stack.add_css_class("tk-plan-stack")
        cards = self.tasks[self.index:self.index + 3]
        base = Gtk.Box(vexpand=True)  # the overlay's own size: the space the cards fill
        stack.set_child(base)
        self.top = None
        for depth in reversed(range(len(cards))):
            card = _Tilt(self._card(cards[depth], depth), depth)
            card.set_margin_bottom(24)
            if depth:
                card.set_opacity(1 - depth * .25); card.set_can_target(False)
            else:
                self.top = card
            stack.add_overlay(card)
        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._dragged)
        drag.connect("drag-end", self._released)
        self.top.add_controller(drag)
        self.content.append(stack)
        acts = Gtk.Box(homogeneous=False, spacing=10); acts.add_css_class("tk-plan-actions")
        acts.append(self._act("tk-plan-no", None, "Not today", -1))
        acts.append(self._act("tk-plan-skip", None, "Skip", 0))
        acts.append(self._act("tk-plan-yes", None, "Today", 1))
        self.content.append(acts)

    def _act(self, name, icon, label, decision, lead=False) -> Gtk.Button:
        button = named(Gtk.Button(hexpand=decision != 0), name)
        button.add_css_class("tk-plan-act")
        row = line(8); row.set_halign(Gtk.Align.CENTER)
        glyph = icons.image(icon, pixel_size=18) if icon else None
        if glyph and lead: row.append(glyph)
        row.append(text(label, "body", weight=650))
        if glyph and not lead: row.append(glyph)
        button.set_child(row)
        button.connect("clicked", lambda *_: self.decide(decision))
        return button

    def _card(self, task, depth) -> Gtk.Widget:
        source = next((s for s in self.lists if s["id"] == task["l"]), {"n": "", "h": 250})
        card = column(12); card.add_css_class("tk-plan-card")
        card.set_overflow(Gtk.Overflow.HIDDEN)
        where = line(6); where.append(list_dot(source)); where.append(text(source["n"], "caption", muted=True))
        card.append(where)
        card.append(text(task["t"], "page-title", weight=750, wrap=True))
        meta = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8,
                           max_children_per_line=4, halign=Gtk.Align.START)
        meta.add_css_class("tk-plan-meta")
        due = task.get("due")
        def chip(icon, words, late=False):
            part = line(5); part.add_css_class("tk-plan-chip")
            if late: part.add_css_class("late")
            if icon: part.append(icons.image(icon, pixel_size=13))
            part.append(text(words, "caption", muted=False)); meta.append(part)
        if due is not None and due < 0: chip("calendar", "Was due " + day_label(due, self.today_date).lower(), late=True)
        elif due is not None: chip("calendar", day_label(due, self.today_date))
        else: chip(None, "No date")
        if task.get("time"): chip("clock", task["time"])
        if task["subs"]: chip("list", f'{sum(s[1] for s in task["subs"])}/{len(task["subs"])} steps')
        card.append(meta)
        if task["notes"]:
            notes = text(task["notes"], "body", muted=False, wrap=True); notes.add_css_class("tk-plan-notes"); card.append(notes)
        others = [k for k in task["who"] if k != "me"]
        filler = Gtk.Box(vexpand=True); card.append(filler)
        if others:
            with_line = line(6)
            for key in others: with_line.append(face(key, self.people, 26, self.fixture_dir))
            names = ", ".join(self.people.get(k, {"n": k})["n"].split(" ")[0] for k in others)
            with_line.append(text("With " + names, "caption", muted=True)); card.append(with_line)
        if depth: return card
        holder = Gtk.Overlay(child=card)
        self.stamps = {}
        for side, words, align in ((1, "Today", Gtk.Align.END), (-1, "Not today", Gtk.Align.START)):
            stamp = text(words.upper(), "title-2", weight=750)
            stamp.add_css_class("tk-plan-stamp"); stamp.add_css_class("yes" if side > 0 else "no")
            stamp.set_halign(align); stamp.set_valign(Gtk.Align.START); stamp.set_opacity(0); stamp.set_can_target(False)
            holder.add_overlay(stamp); self.stamps[side] = stamp
        self.card_face = card
        return holder

    def _ending(self) -> None:
        self.content.append(self._head(None))
        end = column(10); end.add_css_class("tk-plan-end")
        end.set_vexpand(True); end.set_valign(Gtk.Align.CENTER); end.set_halign(Gtk.Align.FILL)
        tile = column(0); tile.add_css_class("tk-plan-end-glyph"); tile.set_halign(Gtk.Align.CENTER)
        glyph = icons.image("sunrise", pixel_size=30); glyph.set_vexpand(True); tile.append(glyph)
        end.append(tile)
        held = {**self.today, **{x["id"]: x for x in self.tasks if x["id"] in self.yes}}
        today = [t for t in held.values() if self.due.get(t["id"]) is not None and self.due[t["id"]] <= 0]
        title = text(f"Today holds {len(today)} thing{'s' if len(today) != 1 else ''}", "title-1", weight=700); title.set_xalign(.5)
        title.set_halign(Gtk.Align.CENTER); end.append(title)
        said = text(f"You added {len(self.yes)} and moved {len(self.no)} to later.", "body", muted=True); said.set_xalign(.5)
        said.set_halign(Gtk.Align.CENTER); end.append(said)
        rows = column(6); rows.add_css_class("tk-plan-today"); rows.set_halign(Gtk.Align.CENTER)
        for task in today[:6]:
            source = next((s for s in self.lists if s["id"] == task["l"]), {"h": 250})
            row = line(8); row.add_css_class("tk-plan-today-row")
            row.append(list_dot(source)); row.append(text(task["t"], "body", ellipsize=Pango.EllipsizeMode.END, hexpand=True)); rows.append(row)
        end.append(rows)
        go = named(make_control(BarAction("", "Start the day", primary=True, on_activate=self.finish)), "tk-plan-start")
        go.add_css_class("tk-plan-start"); go.set_halign(Gtk.Align.CENTER)
        end.append(go)
        self.content.append(end)

    # ── deciding ───────────────────────────────────────────────────────────

    def _dragged(self, _gesture, dx, _dy) -> None:
        if self._busy or self.top is None: return
        self.top.set_dx(dx)
        for side, stamp in getattr(self, "stamps", {}).items():
            stamp.set_opacity(1 if dx * side > STAMP else 0)
        if hasattr(self, "card_face"):
            for side, css in ((1, "going-yes"), (-1, "going-no")):
                (self.card_face.add_css_class if dx * side > STAMP else self.card_face.remove_css_class)(css)

    def _released(self, _gesture, dx, _dy) -> None:
        if self._busy or self.top is None: return
        if abs(dx) > COMMIT:
            self.decide(1 if dx > 0 else -1); return
        self._animate(self.top.dx, 0, lambda: None)
        for stamp in getattr(self, "stamps", {}).values(): stamp.set_opacity(0)

    def decide(self, decision: int) -> None:
        """Today (1), Not today (-1) or Skip (0) for the top card, which then leaves."""
        if self._busy or self.index >= len(self.tasks): return
        task = self.tasks[self.index]
        if decision > 0:
            self.due[task["id"]] = 0; self.yes.append(task["id"])
        elif decision < 0:
            if self.due.get(task["id"]) is None or self.due[task["id"]] <= 0: self.due[task["id"]] = 1
            self.no.append(task["id"])
            self.today.pop(task["id"], None)
        self.on_decide(task, decision)
        def advance():
            self._busy = False; self.index += 1; self._draw()
        self._busy = True
        if self.top is None: advance(); return
        target = decision * FLY if decision else self.top.dx
        if decision == 0: self.top.set_opacity(.5)
        self._animate(self.top.dx, target, advance)

    def _animate(self, start, end, done) -> None:
        card = self.top
        target = Adw.CallbackAnimationTarget.new(lambda value: card.set_dx(value))
        animation = Adw.TimedAnimation.new(card, start, end, 240 if start != end else 1, target)
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        animation.connect("done", lambda *_: done())
        self._animation = animation
        animation.play()

    def skip_to_end(self) -> None:
        """Skip every card at once (the fixture's ending state): nothing is decided."""
        self.index = len(self.tasks); self._busy = False; self._draw()

    def finish(self) -> None:
        self.on_close(len(self.yes))


__all__ = ["PlanButton", "PlanDeck"]
