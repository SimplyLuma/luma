# SPDX-License-Identifier: Apache-2.0
"""Calendar-only month cells and time surfaces; shared controls come from LumaUI."""
from datetime import date, datetime, timedelta
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GObject, Gsk, Graphene, Gtk, Pango, GLib
from luma_appkit import PersonAvatar, CountBadge, apply_type, icons
from pathlib import Path
from .calendar_fixture import events_on, free_gaps, minute_text, month_days, overlap_lanes, duration_text
from .calendar_data import same_occurrence, participant_rows, contact_for_person


def label(text, role="body", *, wrap=False, weight=None, tone=None):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap, hexpand=True)
    apply_type(widget, role, weight=weight)
    if tone is not None:
        if tone not in ("primary","secondary","muted","faint"):
            raise ValueError("unknown Calendar text tone")
        widget.add_css_class("calendar-ink-"+tone)
    if not wrap:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
        widget.set_max_width_chars(1)
    return widget


def button(text, callback, *, icon=None, name=None, accessible_label=None):
    widget = Gtk.Button(hexpand=False)
    widget.add_css_class("calendar-control")
    row = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
    if icon:
        row.append(icons.image(icon))
    if text:
        name_label = label(text)
        name_label.set_max_width_chars(-1)
        name_label.set_hexpand(False)
        row.append(name_label)
    widget.set_child(row)
    description = accessible_label or text or icon or name
    widget.set_tooltip_text(description)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [description])
    if name:
        widget.set_name(name)
    widget.connect("clicked", lambda *_: callback())
    return widget


def category(source, fixture=None):
    if fixture:
        return {"work": "work", "personal": "play", "family": "create", "hol": "tools"}.get(source.uid, "work")
    return {"blue": "work", "green": "play", "violet": "media", "amber": "create"}.get(source.tone, "work")


def avatar(name,size,window, *, email=""):
    picture=None
    if window.fixture:
        key=next((key for key,person in window.fixture.people.items() if person["name"]==name),None)
        path=Path(window.fixture.path).parent / "calendar-v70" / f"face-{key}.jpg"
        if path.exists():
            picture=Gdk.Texture.new_from_filename(str(path))
    else:
        person=contact_for_person(window.people,name,email)
        if person and person.photo:
            try:
                picture=Gdk.Texture.new_from_bytes(GLib.Bytes.new(person.photo))
            except GLib.Error:
                pass
    return PersonAvatar("Nick" if window.fixture and name=="You" else name,size,picture=picture,hue=250 if window.fixture and name=="You" else None)


def event_category(event, window):
    source = next((s for s in window.sources if s.uid == event.source_uid), None)
    return category(source, window.fixture) if source else "work"


class CalendarCanvas(Gtk.Widget):
    """Allocate time blocks by minute and lane, clipping short events correctly."""
    __gtype_name__ = "LumaCalendarCanvas"

    def __init__(self, height=810, *, column_gap=0):
        super().__init__(hexpand=True, overflow=Gtk.Overflow.HIDDEN)
        self.height = height
        self.column_gap = column_gap
        self.items = []

    def add(self, widget, y, height, lane=0, lanes=1, inset=2):
        widget.set_parent(self)
        self.items.append((widget, y, height, lane, lanes, inset))

    def do_measure(self, orientation, for_size):
        return (0, 0, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (self.height, self.height, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        for child, y, h, lane, lanes, inset in self.items:
            cw = (width-self.column_gap*(lanes-1))/lanes
            left, right = inset if isinstance(inset, tuple) else (inset, inset)
            x = (cw+self.column_gap)*lane + left
            w = max(0, int(cw - left - right))
            point = Graphene.Point()
            point.init(x, y)
            child.allocate(w, int(h), -1, Gsk.Transform.new().translate(point))

    def do_snapshot(self, snapshot):
        for child, *_ in self.items:
            self.snapshot_child(child, snapshot)

    def clear(self):
        for widget, *_ in self.items:
            if isinstance(widget, CalendarCanvas):
                widget.clear()
            widget.unparent()
        self.items.clear()


class CalendarFlowColumn(Gtk.Widget):
    """Let Flow use the full island width without imposing a phone minimum."""
    __gtype_name__ = "LumaCalendarFlowColumn"

    def __init__(self, compact=False):
        super().__init__(hexpand=True)
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              margin_start=8 if compact else 20,
                              margin_end=12 if compact else 28)
        self.column.set_parent(self)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 0, -1, -1
        return self.column.measure(orientation, for_size)

    def do_size_allocate(self, width, height, baseline):
        self.column.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot):
        self.snapshot_child(self.column, snapshot)

    def clear(self):
        self.column.unparent()


class CalendarMonthGrid(Gtk.Widget):
    """Seven equal columns whose content cannot widen a phone's island."""
    __gtype_name__="LumaCalendarMonthGrid"

    def __init__(self,weeks):
        super().__init__(hexpand=True,vexpand=True,overflow=Gtk.Overflow.HIDDEN)
        self.weeks=weeks
        self.cells=[]

    def attach(self,cell,column,row,*_):
        cell.set_parent(self)
        self.cells.append((cell,column,row))

    def do_measure(self,orientation,for_size):
        return (0,0,-1,-1) if orientation==Gtk.Orientation.HORIZONTAL else (84*self.weeks,84*self.weeks,-1,-1)

    def do_size_allocate(self,width,height,baseline):
        cw=(width-24)/7
        rh=(height-4*(self.weeks-1))/self.weeks
        for cell,column,row in self.cells:
            # Like v70 cmFit: reserve the overflow line, then show at most
            # three events, reducing that number until the cell fits.
            chips, overflow, number, total = cell.calendar_contents
            available = max(0, int(rh) - 16)
            number_height = number.measure(Gtk.Orientation.VERTICAL, int(cw))[1]
            if getattr(cell, "calendar_width", None) != int(cw):
                for chip in chips:
                    chip.set_visible(True)
                overflow.set_visible(True)
                cell.calendar_heights = ([chip.measure(Gtk.Orientation.VERTICAL, max(int(cw)-16,chip.measure(Gtk.Orientation.HORIZONTAL,-1)[0]))[1] for chip in chips],
                                         overflow.measure(Gtk.Orientation.VERTICAL, int(cw)-16)[1])
                cell.calendar_width = int(cw)
            heights, more_height = cell.calendar_heights
            shown = len(chips)
            while shown > 0:
                count = 1 + shown + (total > shown)
                needed = number_height + sum(heights[:shown]) + (more_height if total > shown else 0) + 3 * (count - 1)
                if needed <= available + 1:
                    break
                shown -= 1
            for i, chip in enumerate(chips):
                chip.set_visible(i < shown)
            overflow.set_label(f"{total-shown} more")
            overflow.set_visible(total > shown)
            point=Graphene.Point().init(column*(cw+4),row*(rh+4))
            cell.allocate(max(0,int(cw)),max(0,int(rh)),-1,Gsk.Transform.new().translate(point))

    def do_snapshot(self,snapshot):
        for cell,*_ in self.cells:
            self.snapshot_child(cell,snapshot)

    def clear(self):
        for cell,*_ in self.cells:
            cell.unparent()
        self.cells.clear()


def event_chip(event, window, *, block=False, height=40):
    widget = Gtk.Button(overflow=Gtk.Overflow.HIDDEN)
    widget.set_name(f"cal-event-{event.uid}")
    widget.add_css_class("calendar-event-block" if block else "calendar-event-chip")
    if block and height<34:
        widget.add_css_class("tiny")
    widget.add_css_class(event_category(event, window))
    if event.all_day:
        widget.add_css_class("all-day")
    if not block:
        widget.set_size_request(-1,21 if event.all_day else 17)
    if window.selected_event and same_occurrence(window.selected_event,event):
        widget.add_css_class("selected")
    if block and (event.end.date()<window.today or (event.end.date()==window.today and event.end.hour*60+event.end.minute<window.now_minute())):
        widget.set_opacity(.5)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if block and height >= 34 else Gtk.Orientation.HORIZONTAL, spacing=8 if block and height<34 else 1 if block else 5)
    start = event.start.hour * 60 + event.start.minute
    end = event.end.hour * 60 + event.end.minute
    if block:
        title = label(event.summary, "meta", weight=600)
        title.add_css_class("calendar-event-name")
        body.append(title)
        when = minute_text(start)
        if height >= 34:
            when += " – " + minute_text(end)
        if event.location and height >= 64:
            when += " · " + event.location
        metadata = label(when, "caption", weight=400)
        metadata.add_css_class("calendar-event-meta")
        if height < 34:
            title.set_hexpand(False)
            title.set_max_width_chars(20)
            metadata.set_hexpand(False)
            metadata.set_max_width_chars(-1)
        body.append(metadata)
        if height >= 80:
            faces = Gtk.Box(spacing=0)
            faces.set_margin_top(4)
            for name, _, email in participant_rows(event):
                faces.append(avatar(name, 18,window,email=email))
            body.append(faces)
    else:
        if not event.all_day:
            when = label(minute_text(start).replace(" ",""),"caption",weight=400)
            when.set_ellipsize(Pango.EllipsizeMode.NONE)
            when.set_max_width_chars(-1)
            when.set_hexpand(False)
            body.append(when)
        title=label(event.summary,"caption",weight=600 if event.all_day else 400)
        title.add_css_class("calendar-event-name")
        body.append(title)
    widget.set_child(body)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [event.summary])
    widget.connect("clicked", lambda *_: window.open_event(event))
    return widget


class CalendarSurface(Gtk.Box):
    compact_flow = GObject.Property(type=bool, default=False)

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.window = window
        self.set_name("cal-surface")
        self.month_grid = None
        self.week_header = None
        self.week_header_canvas = None
        self.flow_headers = {}
        self.connect("notify::compact-flow", self._flow_width_changed)

    def _flow_width_changed(self, *_):
        if self.window.loaded:
            self.window.refresh()

    def clear(self):
        self.flow_headers.clear()
        if self.week_header is not None:
            self.week_header_canvas.clear()
            self.window.overlay.remove_overlay(self.week_header)
            self.week_header = self.week_header_canvas = None
        def dispose(widget):
            if isinstance(widget, CalendarFlowColumn):
                dispose(widget.column)
                widget.clear()
                return
            if isinstance(widget, (CalendarCanvas,CalendarMonthGrid)):
                widget.clear()
                return
            child = widget.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                dispose(child)
                child = following
        dispose(self)
        while child := self.get_first_child():
            self.remove(child)

    def refresh(self):
        self.clear()
        while child := self.get_first_child():
            self.remove(child)
        self.month_grid = None
        if self.window.view == "month" and getattr(self.window, "narrow", False):
            self._pmonth()
        elif self.window.view == "month":
            self._month()
        elif self.window.view == "week":
            self._week()
        else:
            self._flow()
        self.set_margin_top(self.get_margin_top() + self.window.status_inset)
        if self.week_header is not None:
            self.week_header.set_margin_top(self.week_header.get_margin_top() + self.window.status_inset)

    def _month(self):
        window = self.window
        self.set_margin_top(76)  # v71 keeps the head at the top at every width over 560
        self.set_margin_start(16)
        self.set_margin_end(16)
        self.set_margin_bottom(80)
        self.set_size_request(-1, 404)
        header = Gtk.Grid(column_homogeneous=True, column_spacing=4)
        for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            heading = label(name, "label")
            heading.set_margin_start(10)
            header.attach(heading, i, 0, 1, 1)
        header.set_margin_bottom(9)
        self.append(header)
        grid = self.month_grid = CalendarMonthGrid(len(month_days(window.anchor))//7)
        self.append(grid)
        for index, day in enumerate(month_days(window.anchor)):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, overflow=Gtk.Overflow.HIDDEN,
                           accessible_role=Gtk.AccessibleRole.BUTTON, focusable=True)
            cell.set_name(f"cal-day-{day.isoformat()}")
            cell.add_css_class("calendar-day")
            if day.month != window.anchor.month:
                cell.add_css_class("outside")
                cell.set_opacity(.4)
            if day == window.today:
                cell.add_css_class("today")
            if day == window.selected_day and window.selected_event is None:
                cell.add_css_class("selected")
            cell.update_property([Gtk.AccessibleProperty.LABEL], [day.strftime("%A, %B %-d")])
            cell.set_size_request(0, 0)
            number=label(str(day.day),"body",weight=650)
            number.set_size_request(-1,19)
            number.set_margin_bottom(3)
            if day < window.today:
                number.add_css_class("calendar-past-number")
            cell.append(number)
            events = events_on(window.events, day, window.hidden_sources)
            chips = [event_chip(event, window) for event in events[:3]]
            for chip in chips:
                cell.append(chip)
            # One line, clipped in a narrow cell (v71 .cmd em); a wrapped count crowded the chips out.
            overflow = label(f"{max(0,len(events)-3)} more", "caption",weight=400,tone="faint")
            overflow.set_size_request(-1,16)
            overflow.set_vexpand(True)
            overflow.set_valign(Gtk.Align.END)
            cell.append(overflow)
            cell.calendar_contents = (chips, overflow, number, len(events))
            click = Gtk.GestureClick()
            click.connect("released", lambda gesture, _n, _x, _y, d=day: window.select_day(d)
                          if gesture.get_current_event() is not None else None)
            cell.add_controller(click)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", lambda _c, key, _code, _state, d=day:
                         (window.select_day(d), True)[1] if key in (Gdk.KEY_Return, Gdk.KEY_space) else False)
            cell.add_controller(keys)
            grid.attach(cell, index % 7, index // 7, 1, 1)

    def _pmonth(self):
        """Narrow Month (v71 pmonthHTML): a compact month of dots, then the chosen day: what's in it,
        what's free, and a way to add to it. A sideways swipe on the month moves a month."""
        window = self.window
        self.set_size_request(-1, -1)
        self.set_margin_top(70)
        self.set_margin_start(14)
        self.set_margin_end(14)
        self.set_margin_bottom(150)
        days = month_days(window.anchor)
        if window.selected_day is None or (window.selected_day.year, window.selected_day.month) != (window.anchor.year, window.anchor.month):
            window.selected_day = window.today if (window.today.year, window.today.month) == (window.anchor.year, window.anchor.month) else window.anchor.replace(day=1)
        header = Gtk.Grid(column_homogeneous=True, margin_bottom=6)
        for i, name in enumerate("MTWTFSS"):
            heading = label(name, "small", weight=600, tone="muted")
            heading.set_xalign(.5)
            header.attach(heading, i, 0, 1, 1)
        self.append(header)
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=2)
        grid.set_name("cal-pmonth")
        grid.add_css_class("calendar-pmonth")
        for index, day in enumerate(days):
            events = events_on(window.events, day, window.hidden_sources)
            cell = Gtk.Button()
            cell.set_name(f"cal-day-{day.isoformat()}")
            cell.add_css_class("calendar-pday")
            for flag, name in ((day.month != window.anchor.month, "outside"), (day == window.today, "today"),
                               (day < window.today, "past"), (day == window.selected_day, "selected")):
                if flag:
                    cell.add_css_class(name)
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
            number = Gtk.Label(label=str(day.day), halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            number.add_css_class("calendar-pday-number")
            number.set_size_request(36, 36)
            column.append(number)
            dots = Gtk.Box(spacing=3, halign=Gtk.Align.CENTER)
            dots.set_size_request(-1, 5)
            seen = []
            for event in events:
                kind = event_category(event, window)
                if kind not in seen:
                    seen.append(kind)
            for kind in seen[:3]:
                dot = Gtk.Box(valign=Gtk.Align.CENTER)
                dot.set_size_request(5, 5)
                dot.add_css_class("calendar-pday-dot")
                dot.add_css_class(kind)
                dots.append(dot)
            column.append(dots)
            cell.set_child(column)
            cell.set_size_request(-1, 52)
            cell.update_property([Gtk.AccessibleProperty.LABEL],
                                 [day.strftime("%A, %B %-d") + (f", {len(events)} events" if events else ", nothing planned")])
            cell.connect("clicked", lambda _b, d=day: window.select_day(d))
            grid.attach(cell, index % 7, index // 7, 1, 1)
        swipe = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        def swiped(gesture, dx, dy):
            if abs(dx) > 50 and abs(dx) > abs(dy) * 1.5:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                GLib.idle_add(lambda: (window.step(1 if dx < 0 else -1), False)[1])
        swipe.connect("drag-end", swiped)
        grid.add_controller(swipe)
        self.append(grid)
        self._pday(window.selected_day)

    def _pday(self, day):
        window = self.window
        today = day == window.today
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=16)
        section.set_name("cal-pday")
        section.add_css_class("calendar-pday-agenda")
        head = Gtk.Box(spacing=8, margin_start=4, margin_end=4)
        title = label("Today" if today else day.strftime("%A"), "intro", weight=700)
        title.set_hexpand(False)
        title.set_max_width_chars(-1)
        head.append(title)
        date_text = label(day.strftime("%A, %B %-d") if today else day.strftime("%B %-d"), "lead", weight=400, tone="muted")
        date_text.set_valign(Gtk.Align.BASELINE_CENTER if hasattr(Gtk.Align, "BASELINE_CENTER") else Gtk.Align.BASELINE)
        title.set_valign(date_text.get_valign())
        head.append(date_text)
        for words in (title, date_text):
            words.set_ellipsize(Pango.EllipsizeMode.NONE)
        section.append(head)
        summary = label(window.day_sentence(day), "body", wrap=True, tone="muted")
        summary.set_margin_start(4)
        summary.set_margin_end(4)
        summary.set_margin_top(3)
        summary.set_margin_bottom(14)
        section.append(summary)
        entries = events_on(window.events, day, window.hidden_sources)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        now = window.now_minute()
        for event in entries:
            row = Gtk.Button()
            row.set_name(f"cal-pevent-{event.uid}")
            row.add_css_class("calendar-pevent")
            end = event.end.hour * 60 + event.end.minute
            if day < window.today or (today and not event.all_day and end < now):
                row.add_css_class("past")
            if window.selected_event is not None and same_occurrence(window.selected_event, event):
                row.add_css_class("selected")
            line = Gtk.Box(spacing=12)
            when = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=False)
            when.set_size_request(60, -1)
            if event.all_day:
                when.append(self._right(label("All day", "body", weight=650)))
            else:
                when.append(self._right(label(minute_text(event.start.hour * 60 + event.start.minute), "body", weight=650)))
                when.append(self._right(label(minute_text(end), "small", tone="muted")))
            line.append(when)
            bar = Gtk.Box()
            bar.set_size_request(4, -1)
            bar.add_css_class("calendar-event-stripe")
            bar.add_css_class(event_category(event, window))
            line.append(bar)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
            text.append(label(event.summary, "reading", weight=600))
            if event.location:
                place = Gtk.Box(spacing=5)
                metadata = window.fixture.metadata.get(event.uid, {}) if window.fixture else {}
                if metadata.get("video"):
                    place.append(icons.image("video", pixel_size=13))
                place.append(label(event.location, "meta", tone="muted"))
                text.append(place)
            line.append(text)
            faces = Gtk.Box(valign=Gtk.Align.CENTER, hexpand=False)
            for name, _, email in participant_rows(event):
                faces.append(avatar(name, 22, window, email=email))
            line.append(faces)
            row.set_child(line)
            row.update_property([Gtk.AccessibleProperty.LABEL], [event.summary])
            row.connect("clicked", lambda _b, e=event: window.open_event(e))
            rows.append(row)
        if entries:
            section.append(rows)
        free = free_gaps(day, entries, today=window.today, now_minute=now)
        if free:
            heading = label("Free", "meta", weight=600, tone="muted")
            heading.set_margin_start(4)
            heading.set_margin_top(20)
            heading.set_margin_bottom(8)
            section.append(heading)
            slots = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for start, end in free:
                when = f"{minute_text(start)} – {minute_text(end)}"
                slot = Gtk.Button()
                slot.add_css_class("calendar-free-row")
                slot.add_css_class("calendar-pfree")
                content = Gtk.Box(spacing=10)
                content.append(self._fixed(label(when, "body", tone="primary")))
                content.append(self._fixed(label(duration_text(end - start), "body", tone="muted")))
                content.append(Gtk.Box(hexpand=True))
                hold = self._fixed(label("Hold", "body", weight=600))
                hold.add_css_class("calendar-free-hold")
                content.append(hold)
                slot.set_child(content)
                slot.update_property([Gtk.AccessibleProperty.LABEL], [f"{when}, hold this time"])
                slot.connect("clicked", lambda _b, s=start, e=end: window.hold(day, s, e))
                slots.append(slot)
            section.append(slots)
        add = Gtk.Button(margin_top=14)
        add.set_name("cal-pday-add")
        add.add_css_class("calendar-padd")
        line = Gtk.Box(spacing=10)
        line.append(icons.image("plus", pixel_size=18))
        line.append(label("Add an event on " + ("today" if today else day.strftime("%A")), "reading", tone="secondary"))
        add.set_child(line)
        add.connect("clicked", lambda _b: window.new_event(day=day, start=600))
        section.append(add)
        self.append(section)

    @staticmethod
    def _right(widget):
        widget.set_xalign(1)
        widget.set_max_width_chars(-1)
        widget.set_ellipsize(Pango.EllipsizeMode.NONE)
        return widget

    @staticmethod
    def _fixed(widget):
        widget.set_hexpand(False)
        widget.set_max_width_chars(-1)
        widget.set_ellipsize(Pango.EllipsizeMode.NONE)
        return widget

    def _track(self, day, *, flow=False):
        window = self.window
        canvas = CalendarCanvas()
        canvas.set_overflow(Gtk.Overflow.VISIBLE)
        if not flow:
            canvas.add_css_class("calendar-track")
            if day == window.today:
                canvas.add_css_class("today")
        entries = events_on(window.events, day, window.hidden_sources)
        for hour in range(7, 23):
            rule = Gtk.Box()
            rule.add_css_class("calendar-hour-rule")
            canvas.add(rule, (hour - 7) * 54, 1, inset=0)
        lane_canvas = canvas
        if flow:
            lane_canvas = CalendarCanvas()
            canvas.add(lane_canvas, 0, 810, inset=(8, 0))
            for start, end in free_gaps(day, entries, today=window.today, now_minute=window.now_minute()):
                free = button(duration_text(end-start) + " free", lambda d=day, s=start, e=end: window.hold(d, s, e))
                content = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                summary = label(duration_text(end-start) + " free", "small",weight=500)
                summary.add_css_class("calendar-free-duration")
                summary.set_hexpand(False)
                # A centred box is measured for its height; an ellipsizing label then shows only "…".
                summary.set_ellipsize(Pango.EllipsizeMode.NONE)
                summary.set_max_width_chars(-1)
                content.append(summary)
                hint = label("Hold this time", "small",weight=600)
                hint.add_css_class("calendar-free-hint")
                hint.set_hexpand(False)
                hint.set_ellipsize(Pango.EllipsizeMode.NONE)
                hint.set_max_width_chars(-1)
                hint.set_opacity(0)
                content.append(hint)
                free.set_child(content)
                motion = Gtk.EventControllerMotion()
                motion.connect("enter", lambda _c, _x, _y, h=hint: h.set_opacity(1))
                motion.connect("leave", lambda _c, h=hint: h.set_opacity(0))
                free.add_controller(motion)
                free.add_css_class("calendar-free-region")
                lane_canvas.add(free, (start - 420) * .9, (end - start) * .9, inset=0)
        else:
            click = Gtk.GestureClick()
            def slot(_gesture, _presses, x, y):
                picked = canvas.pick(x, y, Gtk.PickFlags.DEFAULT)
                while picked is not None and picked != canvas:
                    if isinstance(picked, Gtk.Button):
                        return
                    picked = picked.get_parent()
                minute = min(1320, max(420, int((y / .9 + 420) / 30 + .5) * 30))
                window.new_event(day=day, start=minute)
            click.connect("released", slot)
            canvas.add_controller(click)
        for event, lane, lanes in overlap_lanes(entries):
            start = max(420, event.start.hour * 60 + event.start.minute)
            end = min(1320, event.end.hour * 60 + event.end.minute)
            if end <= start:
                continue
            h = max(18, (end - start) * .9)
            lane_canvas.add(event_chip(event, window, block=True, height=h), (start - 420) * .9, h, lane, lanes, inset=2 if flow else 3)
        minute = window.now_minute()
        if day == window.today and 420 < minute < 1320:
            line = Gtk.Box()
            line.add_css_class("calendar-now")
            canvas.add(line, (minute - 420) * .9, 2, inset=(-6, 0))
            marker = Gtk.Box()
            dot = Gtk.Box()
            dot.set_size_request(10, 10)
            dot.add_css_class("calendar-now-dot")
            marker.append(dot)
            marker.append(Gtk.Box(hexpand=True))
            canvas.add(marker, (minute - 420) * .9 - 5, 10, inset=(-11, 0))
        return canvas

    def _hours(self, day=None, *, narrow=False):
        canvas = CalendarCanvas()
        canvas.set_hexpand(False)
        compact = (day is not None and self.compact_flow) or narrow
        canvas.set_size_request(40 if day is not None and compact else 44 if narrow else 56, -1)
        minute = self.window.now_minute()
        now_shown = day == self.window.today and 420 < minute < 1320
        for hour in range(7, 23):
            # v71: today's hour label within 18 minutes of now gives way to the now label.
            if now_shown and abs(minute - hour * 60) < 18:
                continue
            text = label(minute_text(hour * 60), "caption",weight=400,tone="faint")
            text.set_xalign(1)
            canvas.add(text, (hour - 7) * 54 - 8, 16, inset=2 if compact else 4)
        if now_shown:
            # Narrow: one line, without AM/PM, on a masking pill.
            narrow = getattr(self.window, "narrow", False)
            text = label(minute_text(minute).removesuffix(" AM").removesuffix(" PM") if narrow else minute_text(minute), "caption",weight=700)
            if narrow:
                text.add_css_class("calendar-now-pill")
                text.set_ellipsize(Pango.EllipsizeMode.NONE)
                text.set_max_width_chars(-1)
            text.set_xalign(1)
            text.add_css_class("calendar-now-label")
            canvas.add(text, (minute - 420) * .9 - 9, 18, inset=2 if compact else 4)
        return canvas

    def _week(self):
        window = self.window
        narrow = getattr(window, "narrow", False)
        top = self._week_top()
        self.set_size_request(-1, -1)
        self.set_margin_top(top)
        self.set_margin_start(8)
        self.set_margin_end(16)
        self.set_margin_bottom(150 if narrow else 100)
        # Narrow: three days from the anchor (v71 weekHTML n = 3).
        days = 3 if narrow else 7
        start = window.anchor if narrow else window.anchor - timedelta(days=window.anchor.weekday())
        grid = Gtk.Grid(column_spacing=4, row_spacing=8, hexpand=True)
        self.append(grid)
        grid.attach(self._hours(narrow=narrow), 0, 1, 1, 1)
        group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        header = self.week_header = Gtk.Box(spacing=4, valign=Gtk.Align.START,
                                            margin_start=8, margin_end=16,
                                            margin_top=top)
        if narrow:
            header.add_css_class("calendar-week-pinned")
        gutter = Gtk.Box()
        gutter.set_size_request(44 if narrow else 56,-1)
        header.append(gutter)
        heads = []
        for i in range(days):
            day = start + timedelta(days=i)
            head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            head.add_css_class("calendar-week-head")
            head.append(label(day.strftime("%a"), "label"))
            number = label(str(day.day), "card-title")
            if day == window.today:
                head.add_css_class("calendar-week-today")
                number.add_css_class("calendar-today-label")
            head.append(number)
            for event in events_on(window.events, day, window.hidden_sources):
                if event.all_day:
                    head.append(event_chip(event, window))
            heads.append(head)
            track = self._track(day)
            if day < window.today:
                track.set_opacity(.75)
            group.add_widget(track)
            grid.attach(track, i + 1, 1, 1, 1)
        height = max(head.measure(Gtk.Orientation.VERTICAL,-1)[1] for head in heads)
        canvas = self.week_header_canvas = CalendarCanvas(height=height,column_gap=4)
        for i, head in enumerate(heads):
            canvas.add(head,0,height,i,days,inset=0)
        header.append(canvas)
        self.window.overlay.add_overlay(header)
        space = Gtk.Box()
        space.set_size_request(-1,height)
        grid.attach(space,0,0,days+1,1)

    def _week_top(self):
        # Narrow: the day header pins right under the title island (v71 .calnarrow .cwh padding-top 66).
        if getattr(self.window, "narrow", False):
            return 66
        return 76

    def track_scroll(self, adjustment):
        if self.week_header is not None:
            rest = self._week_top()
            pinned = rest if getattr(self.window, "narrow", False) else 64
            self.week_header.set_margin_top(max(pinned, int(rest-adjustment.get_value())))

    def _flow(self):
        window = self.window
        self.set_size_request(-1, -1)
        # v70's later .cflow/.cdh rules keep day headings in the scrolling stream.
        self.set_margin_top(74 if getattr(window, "narrow", False) else 70)
        self.set_margin_start(0)
        self.set_margin_end(0)
        self.set_margin_bottom(100)
        column = CalendarFlowColumn(self.compact_flow)
        self.append(column)
        content = column.column
        gutter = 44 if self.compact_flow else 60
        self.flow_tracks = {}
        self.flow_sections = {}
        base = window.fixture.base if window.fixture else window.anchor - timedelta(days=window.anchor.weekday())
        for i in range(14):
            day = base + timedelta(days=i)
            if i:
                night = Gtk.Box(spacing=8, margin_top=18, margin_bottom=10, margin_start=gutter)
                night.add_css_class("calendar-night")
                night.append(icons.image("moon",pixel_size=14))
                night.append(label("Night", "small",tone="faint"))
                content.append(night)
            section=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            if day < window.today:
                section.set_opacity(.6)
            self.flow_sections[day]=section
            content.append(section)
            heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4,margin_start=gutter,margin_top=22,margin_bottom=12)
            self.flow_headers[day] = heading
            title=Gtk.Box(spacing=12)
            weekday = label(day.strftime("%A"), "calendar_flow_day")
            weekday.set_hexpand(False)
            weekday.set_max_width_chars(-1)
            title.append(weekday)
            day_text=label(day.strftime("%B %-d") + (" · Today" if day==window.today else ""),weight=500,tone=None if day==window.today else "secondary")
            if day==window.today:
                day_text.add_css_class("calendar-today-label")
            title.append(day_text)
            heading.append(title)
            heading.append(label(window.day_sentence(day),"meta"))
            for event in events_on(window.events,day,window.hidden_sources):
                if event.all_day:
                    heading.append(event_chip(event,window))
            section.append(heading)
            row = Gtk.Box(spacing=4)
            row.append(self._hours(day))
            track=self._track(day, flow=True)
            self.flow_tracks[day]=track
            row.append(track)
            section.append(row)
