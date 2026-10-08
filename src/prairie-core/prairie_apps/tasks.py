# SPDX-License-Identifier: Apache-2.0
"""Tasks v70 composition, one responsive source for desktop and phone.

AppWindow owns the frame; NavigationSidebar/SidebarFoot the destinations;
Island and ToastHost the work area; DetailsPane the task drawer; ActionCenter
quick entry; Menu the pickers; kit type and avatars compose Tasks-only rows.
EDS reads and existing writes run in one worker with generation tokens.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango
from luma_appkit import (
    ActionCenter, AddRow, AppWindow, BarAction, BarChip, BarEntry, Command,
    CommandGroup, CommandRegistry, CountBadge, DestructiveDialog, DetailsPane, EmptyState,
    HeroTitleField, Island, Menu, MenuSection, ModeSwitch, SidebarRow, RowLead, AvatarStack, NavigationSidebar, ParagraphField,
    ScrollView, SidebarFoot, SidebarToggle, TextField, TextButton, Toast, ToastHost, Selection, StackedButton, StackedButtons,
    add_style_sheet, apply_type, icons, install_appkit, install_lumaui,
)
from luma_appkit.action_center import make_control, SEPARATOR, SPACER
from luma_appkit.action_bubble import FloatingMenu, MenuItem
from luma_appkit import lumaui, ProgressLine
from luma_appkit.bar_panel import BarTile, BarTiles, PanelRow, panel_list
from luma_appkit.menus import bar_menu
from luma_appkit.rows_swipe import SwipeAction, SwipeRow
from .tasks_data import TasksData, VIEWS, PRIORITIES, day_label, in_view, task_groups, suggested_tasks, quick_chips, plan_tasks
from .tasks_backend import day
from .tasks_parse import Choice, parse_task
from .tasks_repeat import CHOICES as REPEAT_CHOICES, UNITS as REPEAT_UNITS, custom_rule, label as repeat_label
from .tasks_plan import PlanDeck, PlanButton
from .tasks_widgets import check, column, face, faces, line, list_dot, named, task_row, text

APP_ID = "org.projectluma.Tasks"
APP_ICON_ID = APP_ID  # The preview launcher changes the instance ID only.


def clear(widget):
    while (child := widget.get_first_child()) is not None: widget.remove(child)


class TasksWindow(AppWindow):
    def __init__(self, application):
        self.closed = self.busy = False
        self._after_busy = None
        self.generation = 0
        self._loading = self._reload_pending = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tasks-eds")
        self.source = TasksData(lambda: GLib.idle_add(self._reload))
        self.data = {"lists": [], "tasks": [], "people": {}}
        self.view = "today"
        self.selected = None
        self.details_wanted = True
        self.query = os.environ.get("LUMA_TASKS_QUERY", "") if self.source.fixture else ""
        self.draft = os.environ.get("LUMA_TASKS_DRAFT", "") if self.source.fixture else ""
        self.show_done = False
        self.adding = False
        self.suggestions = True
        self.undo_action = None
        self._pending_fields = None
        self._pending_original = {}
        self._step_drafts = {}
        self._comment_drafts = {}
        self._comment_to_reveal = None
        self.phone_panel = None
        self._details_revision = 0
        self._detail_restore_target = None
        self._detail_restore_callback = None
        self._comment_reveal_handlers = []
        self._comment_reveal_source = None
        self.fixture_dir = str(Path(os.environ["LUMA_TASKS_FIXTURE"]).with_suffix("")) if self.source.fixture else None
        super().__init__(application=application, app_id=APP_ID, title="Tasks", icon_name=APP_ICON_ID,
                         commands=CommandRegistry((CommandGroup("", (
                             Command("tasks.new", "New task", self._new, "plus", shortcut=("Ctrl", "N")),
                             Command("tasks.find", "Search tasks", self._find, "search", shortcut=("Ctrl", "F")),
                             Command("tasks.sidebar", "Show or hide sidebar", lambda:self.sidebar_toggle.toggle(), "panel-left"),
                             Command("tasks.quit", "Quit Tasks", self.close, "log-out", shortcut=("Ctrl", "Q")),
                         )),)), default_width=1180, default_height=740, minimum_width=360)
        self.sidebar = named(NavigationSidebar(variant="destinations"), "tk-sidebar")
        self.sidebar.list.connect("row-activated", self._view_activated)
        self.foot = named(SidebarFoot(search="Search tasks", on_search=self._search), "tk-foot")
        self.foot.entry.set_name("tk-search")
        self.foot.set_margin_bottom(0)
        self.sidebar.append_footer(self.foot)
        self.island = named(Island(), "tk-island")
        self.island.set_hexpand(True)
        self.page = named(column(), "tk-page")
        self.page.set_margin_top(34); self.page.set_margin_bottom(110)
        self.page.set_margin_start(34); self.page.set_margin_end(34)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=760, child=self.page)
        self.scroll = named(ScrollView(clamp), "tk-body")
        self.island.append(self.scroll)
        self.empty_state = EmptyState('No tasks yet', 'Add a task below to get started.',
                                      icons.icon_name('check-check'))
        self.empty_state.set_visible(False)
        self.island.append(self.empty_state)
        self.host = ToastHost(self.island)
        self.center = named(ActionCenter(), "tk-bar").attach(self.host)
        self.details = named(DetailsPane("Task", on_close=self._details_closed), "tk-details-slot")
        self.details.sheet.set_name("tk-details")
        self.details.close_button.set_name("tk-close-details")
        self.details.sheet.set_size_request(340, -1)
        self.details.sheet.set_margin_start(0)
        self.main_detail = Gtk.Box(hexpand=True)
        self.main_detail.append(self.host); self.main_detail.append(self.details)
        self.layout = Gtk.Box(hexpand=True, vexpand=True)
        self.layout.append(self.sidebar); self.layout.append(self.main_detail)
        self.sidebar_toggle = named(SidebarToggle(self.sidebar, drawer_below=901), "tk-sidebar-toggle")
        self.sidebar_toggle.set_control_visible(False)
        self.set_leading(self.sidebar_toggle)
        # Plan my day covers the whole window (v71 .tkplan, inset 0) over the three panes.
        self.cover = Gtk.Overlay(child=self.layout)
        self.set_body(self.cover)
        self.plan = None
        self._plan_queue = []
        self._plan_added = 0
        self.searching = False
        self._drawn_tier = "regular"
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 559px"))
        phone.add_setter(self.details.sheet, "margin-start", 0)
        self.add_breakpoint(phone)
        # v71: a window crossing 560 (or 900) redraws in the other shape.
        self.tier_watch.connect("tier-changed", lambda _watch, _tier: self._tier_check())
        self.connect("close-request", self._close)
        self._render_loading()
        self._reload()

    def _run(self, operation, callback):
        future = self.executor.submit(operation)
        def done(future):
            try: result, error = future.result(), None
            except Exception as failure: result, error = None, str(failure)
            def deliver():
                if not self.closed: callback(result, error)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(deliver)
        future.add_done_callback(done)

    def _reload(self):
        if self.closed: return GLib.SOURCE_REMOVE
        if self._loading:
            self._reload_pending = True
            return GLib.SOURCE_REMOVE
        self._loading = True
        generation = self.generation
        def loaded(result, error):
            self._loading = False
            if generation != self.generation:
                pass  # A write started after this read; only its later reload can publish.
            elif error:
                Toast.show(self.host, error, kind="error")
            else:
                self.data = result
                if self.selected and not self.task(self.selected): self.selected = None
                self._render()
            if self._reload_pending:
                self._reload_pending = False
                self._reload()
        self._run(self.source.load, loaded)
        return GLib.SOURCE_REMOVE

    def task(self, key=None):
        return next((t for t in self.data["tasks"] if t["id"] == (key or self.selected)), None)

    def source_list(self, key):
        return next((s for s in self.data["lists"] if s["id"] == key), None)

    def _name(self, key):
        return "You" if key == "me" else self.data["people"].get(key, {"n": key})["n"].split(" ")[0]

    def _render_loading(self):
        clear(self.page); self.page.append(text("Loading tasks…", "caption"))
        self._bar()

    def _view_activated(self, _list, row):
        if hasattr(row, "view_key"): self._select_view(row.view_key)

    def _select_view(self, key):
        if self._flush_fields(lambda: self._select_view(key)):
            return
        self.view = key
        first = next((t for t in self.data["tasks"] if in_view(t, key) and not t["done"]), None)
        self.selected = first["id"] if first else None
        self._render()

    def _render(self):
        self._apply_tier()
        self._sidebar(); self._main(); self._details(); self._bar()

    @property
    def phone(self):
        return self._drawn_tier == "phone"

    def _tier_check(self):
        tier = self.tier
        if tier == self._drawn_tier: return
        was_phone, self._drawn_tier = self.phone, tier
        if was_phone != self.phone:
            # The phone bar and the desktop bar are different shapes: drop what the other one had open.
            self.searching = False
            if self.adding: self.adding = False
            self.center.fold()
        self._render()

    def _apply_tier(self):
        """v71 @container win: the sidebar leaves at 900 and the task pane at 700; a phone (<560)
        has neither: the bar's place dropdown and the grown bar take their jobs."""
        self._drawn_tier = self.tier
        width = self.get_width()
        # Compact keeps navigation in the shared drawer; phone has its bar places.
        self.details.set_visible(not self.phone and not (0 < width <= 700))
        # The phone's bottom room is the kit's one safe area (automatic under 560).
        margin = (8, 16, 0) if self.phone else (34, 34, 110)
        self.page.set_margin_top(margin[0]); self.page.set_margin_start(margin[1]); self.page.set_margin_end(margin[1])
        self.page.set_margin_bottom(margin[2])
        if self.plan is not None: self.plan.set_compact(self.phone)

    def _sidebar(self):
        self.sidebar.clear()
        for key, title, glyph in VIEWS:
            count = sum(in_view(t, key) for t in self.data["tasks"])
            row = SidebarRow(title, lead=RowLead.icon(glyph), trail=count,
                             attention=key == "today" and any(not t["done"] and t.get("due") is not None and t["due"] < 0 for t in self.data["tasks"]))
            row.set_name("tk-view-" + key); row.view_key = key
            self.sidebar.append_row(row)
            if key == self.view: self.sidebar.list.select_row(row)
        self.sidebar.append_section("Lists", action=("plus", "New list", self._new_list))
        for source in self.data["lists"]:
            trailing = line(8)
            if source["ppl"]: trailing.append(faces(source["ppl"][:3], self.data["people"], size=18, fixture_dir=self.fixture_dir))
            trailing.append(CountBadge(sum(t["l"] == source["id"] and not t["done"] for t in self.data["tasks"])))
            row = SidebarRow(source["n"], lead=RowLead.dot(source.get("h", 250)), trail=trailing)
            row.view_key = source["id"]; row.set_name("tk-view-" + source["id"])
            context = Gtk.GestureClick(button=3, propagation_phase=Gtk.PropagationPhase.CAPTURE)
            context.connect("pressed", lambda gesture, _count, _x, _y, uid=source["id"], anchor=row:
                            self._list_context(gesture, anchor, uid))
            row.add_controller(context)
            self.sidebar.append_row(row)
            if source["id"] == self.view: self.sidebar.list.select_row(row)
        if not self.source.fixture:
            from .collaboration import CollaborationCache, CollaborationClient
            from .connect_sync import ConnectError
            try:
                client, cache = CollaborationClient(), CollaborationCache(read_only=True)
                try:
                    invitations = cache.db.execute("SELECT * FROM invitations WHERE hub=? AND device=? AND kind IN ('task','list')", (client.address, client.identity.device_id)).fetchall()
                finally:
                    cache.close()
            except (ConnectError, OSError):
                invitations = []
            if invitations:
                self.sidebar.append_section('Invitations')
                for invitation in invitations:
                    row = SidebarRow('Shared ' + invitation['kind'], lead=RowLead.icon('users'))
                    row.connect('activate', lambda _row, invitation=dict(invitation), anchor=row: self._menu(anchor, [
                        Command('collaboration.accept', 'Accept invitation', lambda: self._accept_collaboration(invitation, True), 'check'),
                        Command('collaboration.decline', 'Decline invitation', lambda: self._accept_collaboration(invitation, False), 'x'),
                    ]))
                    row.set_name('tk-collaboration-invitation')
                    self.sidebar.append_row(row)
        if self.foot.entry.get_text() != self.query: self.foot.entry.set_text(self.query)

    def _accept_collaboration(self, invitation, accept):
        def operation():
            from .collaboration import CollaborationClient
            from .tasks_collaboration import sync_tasks_collaboration
            CollaborationClient().operation(invitation['id'], 'accept', {'accept': accept})
            sync_tasks_collaboration()
        self._mutate(operation, 'Invitation accepted' if accept else 'Invitation declined')

    def _main(self):
        clear(self.page)
        self.scroll.set_visible(True)
        self.empty_state.set_visible(False)
        source = self.source_list(self.view)
        title = source["n"] if source else next((n for k, n, _ in VIEWS if k == self.view), "Tasks")
        heading = line(14)
        words = column(4); words.set_hexpand(True)
        words.append(text(title, "hero"))
        subtitle = self.source.today.strftime("%A, %B %-d") if self.view == "today" else (
            "Shared with " + ", ".join(self._name(k) for k in source["ppl"]) if source and source["ppl"] else
            "Only you" if source else "The next few weeks" if self.view == "upcoming" else "")
        if subtitle: words.append(text(subtitle, "body", muted=True, wrap=True))
        heading.append(words)
        if source:
            actions = line(8); actions.set_valign(Gtk.Align.END)
            if source["ppl"]:
                actions.append(faces(["me", *source["ppl"]], self.data["people"], size=26, fixture_dir=self.fixture_dir))
            share = make_control(BarAction("users", "Shared" if source["ppl"] else "Share",
                                          on_activate=lambda: self._share(share)))
            share.set_name("tk-share"); actions.append(share)
            heading.append(actions)
        self.page.append(heading)
        groups = task_groups(self.data["tasks"], self.data["lists"], self.view, self.query, self.source.today)
        shown = 0
        for name, tasks in groups:
            if not tasks and not (source and source.get("secs")): continue
            group = column(0); group.set_margin_top(18)
            if name:
                label_line = line(8); label_line.set_margin_start(10); label_line.set_margin_bottom(4)
                heading = text(name, "small", weight=650, muted=False); heading.add_css_class("tk-group-title")
                label_line.append(heading); label_line.append(CountBadge(len(tasks)))
                if name == "Overdue": heading.add_css_class("tk-overdue")
                group.append(label_line)
            task_list = Selection.apply(Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE))
            for task in tasks:
                item = Gtk.ListBoxRow(child=self._swipe(task, self._task_row(task)))
                item.add_css_class("tk-task-list-row")
                task_list.append(item); shown += 1
            group.append(task_list)
            if not tasks: group.append(text("Nothing here yet.", "body", muted=True, margin_start=12, margin_end=12, margin_top=4, margin_bottom=8))
            self.page.append(group)
        if not shown and not (source and source.get("secs")):
            self.empty_state.set_text('No matching tasks' if self.query else 'All clear' if self.view == 'today' and self.data['tasks'] else 'No tasks yet',
                'Try a different search.' if self.query else 'Add a task below to get started.')
            self.scroll.set_visible(False)
            self.empty_state.set_visible(True)
        planned = plan_tasks(self.data["tasks"]) if self.view == "today" else []
        if planned:
            self.scroll.set_visible(True)
            self.empty_state.set_visible(False)
            # v71 (changelog "Tasks: Plan my day", and v71's desktop Today as it opens): "Plan my day.
            # 6 things could fit today. Swipe through them." The deck works with a pointer and its buttons too.
            card = named(PlanButton(len(planned), self._plan_open), "tk-plan-day")
            card.set_margin_top(4)  # v71 .tkplanb margin: 4px 0 14px (the next group brings 18)
            self.page.append(card)
        suggestions = suggested_tasks(self.data["tasks"]) if self.view == "today" and self.suggestions else []
        if suggestions:
            group = column(4); group.set_margin_top(18)
            label_line = line(8)
            label_line.set_margin_start(10)
            suggested_title = text("Suggested for today", "small", weight=650, hexpand=True, muted=False)
            suggested_title.add_css_class("tk-suggested-title"); label_line.append(suggested_title)
            hide = named(make_control(BarAction("", "Hide", on_activate=self._hide_suggestions)), "tk-hide-suggestions")
            label_line.append(hide); group.append(label_line)
            for task in suggestions: group.append(self._task_row(task, suggested=True))
            self.page.append(group)
        done = [t for t in self.data["tasks"] if t["l"] == self.view and t["done"]] if source else []
        if done:
            reveal = named(make_control(BarAction("chevron-down" if self.show_done else "chevron-right", f"{len(done)} done", on_activate=self._show_done)), "tk-show-done")
            reveal.set_halign(Gtk.Align.START); reveal.set_margin_top(18); self.page.append(reveal)
            if self.show_done:
                for task in done: self.page.append(self._task_row(task))

    def _swipe(self, task, row):
        """v71 swipe rows (phone): right = Done (green), left = Tomorrow (orange); the toast confirms."""
        if task["done"]: return row
        done = SwipeAction("check", "green", lambda _r, k=task["id"], t=task["t"]: self._edit(k, {"done": True}, "Done: " + t), label="Done")
        later = SwipeAction("sunrise", "orange", lambda _r, k=task["id"]: self._edit(k, {"due": 1}, "Moved to tomorrow"), label="Tomorrow")
        return SwipeRow(row, start=done, end=later)

    def _task_row(self, task, suggested=False):
        row = task_row(task, self.source_list(task["l"]), self.data["people"], self.source.today,
                        self.view, lambda: self._pick(task["id"]), lambda: self._edit(task["id"], {"done": not task["done"]}, "Done: " + task["t"]),
                        selected=self.selected == task["id"], suggested=suggested,
                        on_today=lambda: self._edit(task["id"], {"due": 0}, "Added to today: " + task["t"]), fixture_dir=self.fixture_dir)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        def menu(gesture, _count, _x, _y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._menu(row, [Command('task.share', 'Share', lambda: self._share_task(task, row), 'share-2'),
                             Command('task.delete', 'Delete', lambda: self._delete_task(task['id']), 'trash-2', destructive=True)])
        click.connect('pressed', menu)
        row.add_controller(click)
        return row

    def _hide_suggestions(self): self.suggestions = False; self._main()
    def _show_done(self): self.show_done = not self.show_done; self._main()
    def _pick(self, key):
        if self._flush_fields(lambda: self._pick(key)):
            return
        if key != self.selected:
            self._comment_to_reveal = None
        self.selected = key; self.details_wanted = True; self._render()
    def _details_closed(self):
        if self._flush_fields(self._details_closed):
            if not self.busy: self._details()  # Keep invalid drafts editable.
            return
        self._comment_to_reveal = None
        self._cancel_detail_restore()
        self._detail_restore_target = None
        self.details_wanted = False; self._bar()

    def _details(self):
        task = self.task()
        previous = self.phone_panel if self.phone else self.details.scroller
        scroll_value = previous.get_vadjustment().get_value() if previous is not None and getattr(self, '_detail_key', None) == self.selected else 0
        if self._detail_restore_target and self._detail_restore_target[0] == self.selected:
            scroll_value = self._detail_restore_target[1]
        self._cancel_detail_restore()
        self._details_revision += 1
        self._detail_key = self.selected
        drafts = {}
        if task and self._pending_fields and self._pending_fields[0] == task["id"]:
            for field, value in self._pending_values(self._pending_fields).items():
                if value != self._pending_original.get(field): drafts[field] = value
        clear(self.details.body)
        if hasattr(self, "comment_footer") and self.comment_footer.get_parent() is not None:
            self.comment_footer.get_parent().remove(self.comment_footer)
        grown = self.phone
        self.phone_panel = None
        if not task or not self.details_wanted:
            self._detail_restore_target = None
            self.details.show(open=False, subject=None)
            return
        if grown: self.details.show(open=False, subject=None)
        # A phone grows the bar with the same content (v71 tkBarPhone: `.tkdet2` is #tk-det's body).
        pane = DetailsPane("Task") if grown else self.details
        self._fill_details(pane, task, drafts)
        if grown:
            content = pane.body
            pane.scroller.set_child(None)
            content.add_css_class("tk-details-grown")
            # v71 .tkphbar .fexp: up to 540 tall, scrolling; the comment field stays at its foot.
            # The comment field is the last thing in the panel and scrolls with it (v71 `.tkdet2 .tkcomp`).
            panel = Gtk.ScrolledWindow(child=content, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       propagate_natural_height=True, max_content_height=540)
            panel.set_name("tk-details-grown")
            self.phone_panel = panel
            self._restore_details_scroll(panel, task["id"], scroll_value)
            self._reveal_submitted_comment(task, panel)
            return
        self.details.show(open=True, subject=task["id"])
        self._restore_details_scroll(self.details.scroller, task["id"], scroll_value)
        self._reveal_submitted_comment(task, self.details.scroller)

    def _cancel_comment_reveal(self):
        if self._comment_reveal_source is not None:
            GLib.source_remove(self._comment_reveal_source)
            self._comment_reveal_source = None
        for owner, handler in self._comment_reveal_handlers:
            if isinstance(handler, Gtk.EventController): owner.remove_controller(handler)
            else: owner.disconnect(handler)
        self._comment_reveal_handlers = []

    def _stop_comment_reveal(self, *_):
        # Only a new user interaction acknowledges the submitted comment.
        # Provider readback and focus/allocation changes are not navigation.
        self._comment_to_reveal = None
        self._cancel_comment_reveal()
        return False

    def _cancel_detail_restore(self):
        self._cancel_comment_reveal()
        if self._detail_restore_callback is not None:
            widget, callback = self._detail_restore_callback
            widget.remove_tick_callback(callback)
            self._detail_restore_callback = None

    def _restore_details_scroll(self, scroller, key, value):
        # Rebuilding the body temporarily shrinks the adjustment to zero.
        # Restore after allocation, and also after a later provider readback.
        revision = self._details_revision
        self._detail_restore_target = (key, value)
        last_extent, stable_frames = None, 0
        def restore(_widget, _frame):
            nonlocal last_extent, stable_frames
            if self.closed or revision != self._details_revision or self.selected != key:
                return GLib.SOURCE_REMOVE
            if not scroller.get_mapped(): return GLib.SOURCE_CONTINUE
            adjustment = scroller.get_vadjustment()
            extent = (adjustment.get_upper(), adjustment.get_page_size())
            if extent[1] <= 0: return GLib.SOURCE_CONTINUE
            stable_frames = stable_frames + 1 if extent == last_extent else 0
            last_extent = extent
            adjustment.set_value(min(value, max(0, extent[0] - extent[1])))
            if stable_frames < 2: return GLib.SOURCE_CONTINUE
            self._detail_restore_target = None
            self._detail_restore_callback = None
            return GLib.SOURCE_REMOVE
        self._detail_restore_callback = (scroller, scroller.add_tick_callback(restore))

    def _reveal_submitted_comment(self, task, scroller):
        intent = self._comment_to_reveal
        if not intent or intent[0] != task["id"]: return
        if not any(activity[1] == "c" and activity[2] == intent[1] for activity in task["act"]): return
        revision = self._details_revision
        adjustment = scroller.get_vadjustment()
        def show_comment():
            self._comment_reveal_source = None
            current = self.phone_panel if self.phone else self.details.scroller
            if (self.closed or self.selected != task["id"] or revision != self._details_revision
                    or scroller is not current or self._comment_to_reveal != intent):
                return GLib.SOURCE_REMOVE
            if (not scroller.get_mapped() or adjustment.get_page_size() <= 0
                    or self._detail_restore_target is not None):
                self._comment_reveal_source = GLib.timeout_add(60, show_comment)
                return GLib.SOURCE_REMOVE
            # Keep focus with the composer as well as its scroll position:
            # GTK otherwise animates back to the top control focused on grow.
            if self._comment_entry.get_mapped(): self._comment_entry.grab_focus()
            adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
            return GLib.SOURCE_REMOVE
        def schedule(*_):
            if self._comment_reveal_source is None and self._comment_to_reveal == intent:
                self._comment_reveal_source = GLib.timeout_add(60, show_comment)
        # Late layout and the grown panel's automatic focus may move an already
        # revealed comment. Retain intent across those changes and EDS reloads.
        for signal in ("changed", "value-changed"):
            self._comment_reveal_handlers.append((adjustment, adjustment.connect(signal, schedule)))
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._stop_comment_reveal)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._stop_comment_reveal)
        press = Gtk.GestureClick()
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", self._stop_comment_reveal)
        for controller in (scroll, keys, press):
            scroller.add_controller(controller)
            self._comment_reveal_handlers.append((scroller, controller))
        schedule()

    def _fill_details(self, pane, task, drafts):
        """v71 "Tasks detail (2026-10-01)": the title, then grouped inset cards (when, priority, who,
        where), Notes, Steps and Comments, each under its label, 22 above. Phone and desktop alike."""
        source = self.source_list(task["l"])
        writable = source.get('writable', True)
        header = line(12); header.add_css_class("tk-detail-head")
        header.append(check(task["done"], task["pri"], lambda: self._edit(task["id"], {"done": not task["done"]}), "tk-detail-complete", big=True))
        header.get_first_child().set_sensitive(writable)
        # v70's editable title wraps; the paragraph part supplies that behavior.
        title = named(ParagraphField(drafts.get("t", task["t"]), placeholder="Task title", label="Task title"), "tk-title")
        apply_type(title, "assistant-title")
        title.set_editable(writable)
        title.set_hexpand(True)
        title_scroll = Gtk.ScrolledWindow(child=title, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         vscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_height=True, min_content_height=26,
                                         height_request=26, hexpand=True, valign=Gtk.Align.CENTER)
        def fit_title_height(editor=title, scroller=title_scroll):
            if not editor.get_mapped() or editor.get_width() <= 0:
                return GLib.SOURCE_REMOVE
            content = editor.text
            if len(content) <= 12 and "\n" not in content:
                needed = 26
            else:
                end = editor.get_buffer().get_end_iter()
                if end.backward_char():
                    last = editor.get_iter_location(end)
                    needed = min(120, max(26, last.y + last.height + 1))
                else:
                    needed = 26
            if needed != scroller.get_size_request()[1]:
                scroller.set_size_request(-1, needed)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(40, fit_title_height)
        title.connect("map", lambda *_: GLib.idle_add(fit_title_height))
        title.get_buffer().connect("changed", lambda *_: GLib.idle_add(fit_title_height))
        header.append(title_scroll)
        # v71 .tkdh .ib: a quiet flag, red when on (not a raised chip).
        flag = named(make_control(BarAction("flag", tooltip="Flag", danger=task.get("flag", False),
                                           on_activate=lambda: self._edit(task["id"], {"flag": not task.get("flag", False)})), size="tool"), "tk-flag")
        flag.set_valign(Gtk.Align.CENTER)
        header.append(flag)
        pane.add(header)
        actions = StackedButtons([
            StackedButton('share-2', 'Share', on_click=lambda: self._share_task(task, pane)),
            StackedButton('trash-2', 'Delete', danger=True, on_click=self._delete)], size='tile')
        actions.set_name('tk-detail-actions')
        actions.get_last_child().set_sensitive(writable)
        pane.add(actions)

        # Due, Priority, Assigned and List: one inset card, hairline rows, a 92 px label column.
        facts = named(column(0), "tk-props"); facts.add_css_class("tk-props")
        facts.set_sensitive(writable)
        due = day_label(task.get("due"), self.source.today)
        # v71 .tkprops dd wraps controls instead of widening the phone panel.
        due_tools = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                column_spacing=6, row_spacing=6,
                                min_children_per_line=1, max_children_per_line=2,
                                halign=Gtk.Align.START)
        due_tools.add_css_class("tk-fact-values")
        due_tools.insert(self._control("tk-due", due or "Add a date", lambda b: self._due(b),
                                       danger=bool(task.get("due") is not None and task["due"] < 0)), -1)
        if task.get("due") is not None or task.get('repeat') or task.get('recurring'):
            repeat = self._control('tk-repeat', '', self._repeat, 'repeat')
            repeat.set_tooltip_text(repeat_label(task.get('repeat')) if task.get('repeat') else 'Repeat')
            repeat.update_property([Gtk.AccessibleProperty.LABEL], ['Repeat'])
            due_tools.insert(repeat, -1)
        self._fact(facts, "calendar", "Due", due_tools)
        start_field = TextField('Start time', value=drafts.get('start_time', task.get('start_time', '')), placeholder='Add a start time',
                                on_activate=lambda _value: self._flush_fields())
        start_field.label.set_visible(False)
        start_field.set_name('tk-start-time')
        self._fact(facts, 'clock', 'Starts', start_field)
        time_field = TextField('End time', value=drafts.get('time', task.get('time', '')), placeholder='15:00 or 3:00 PM',
                               on_activate=lambda _value: self._flush_fields())
        time_field.label.set_visible(False)
        start_field.add_css_class('tk-inline-field')
        time_field.add_css_class('tk-inline-field')
        time_field.set_name('tk-time')
        time_focus = Gtk.EventControllerFocus()
        time_focus.connect('leave', lambda *_: GLib.idle_add(lambda: (self._flush_fields(), False)[1]))
        time_field.entry.add_controller(time_focus)
        start_focus = Gtk.EventControllerFocus()
        start_focus.connect('leave', lambda *_: GLib.idle_add(lambda: (self._flush_fields(), False)[1]))
        start_field.entry.add_controller(start_focus)
        self._fact(facts, 'clock', 'Ends', time_field)
        # Priority is a full-width row of four under its label.
        priority = ModeSwitch([(str(i), name) for i, name in enumerate(PRIORITIES)], fill=True, size="small",
                              current=str(task["pri"]), label="Priority",
                              on_change=lambda value: self._edit(task["id"], {"pri": int(value)}))
        for key, button in priority.buttons.items(): button.set_name("tk-priority-" + key)
        self._fact(facts, "flag", "Priority", priority, wide=True)
        assigned = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6,
                               max_children_per_line=4, halign=Gtk.Align.START)
        assigned.add_css_class("tk-assignees")
        for key in task["who"]:
            part = line(6); part.add_css_class("tk-person")
            part.append(face(key, self.data["people"], 22, self.fixture_dir)); part.append(text(self._name(key), "body", weight=550)); part.set_halign(Gtk.Align.START); assigned.append(part)
        add_person = self._control("tk-assign", "" if task["who"] else "Someone", lambda b: self._assign(b), "plus")
        add_person.set_halign(Gtk.Align.START); assigned.append(add_person)
        self._fact(facts, "user", "Assigned", assigned)
        # "Shared with Alex" sits under the list.
        list_words = source["n"] + (" › " + source["secs"][task.get("sec", 0)] if source.get("secs") else "")
        list_tools = column(6)
        list_tools.append(self._control("tk-move", list_words, lambda b: self._move(b)))
        if source["ppl"]:
            shared = line(5); shared.set_name("tk-shared-with"); shared.append(icons.image("users", pixel_size=13))
            shared.append(text("Shared with " + ", ".join(self._name(k) for k in source["ppl"]), "caption", muted=True, weight=400, wrap=True))
            list_tools.append(shared)
        self._fact(facts, "list", "List", list_tools)
        pane.add(facts)

        pane.add_section("Notes")
        notes = named(ParagraphField(drafts.get("notes", task["notes"]), placeholder="Anything worth remembering", label="Notes"), "tk-notes")
        notes.set_editable(writable)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self._flush_fields())
        notes.add_controller(focus)
        notes.set_size_request(-1, 24)
        # A wrapping text view measures one line until its layout is validated; measure again then.
        notes.connect("map", lambda w: GLib.timeout_add(60, lambda: (w.queue_resize(), False)[1]))
        well = column(); well.add_css_class("tk-note-well"); well.append(notes)
        pane.add(well)
        self._pending_fields = (task["id"], title, notes, time_field, start_field)
        self._pending_original = {"t": task["t"], "notes": task["notes"], "time": task.get("time", ""), 'start_time': task.get('start_time', '')}

        # Steps: the label with "n of m", a progress line, then one inset card with "Add a step" last.
        completed = sum(done for _title, done in task["subs"])
        pane.add_section("Steps", count=f"{completed} of {len(task['subs'])}" if task["subs"] else None)
        if task["subs"]:
            progress = named(ProgressLine(completed / len(task["subs"]), tone="good", label="Steps"), "tk-step-progress")
            progress.add_css_class("tk-step-progress"); pane.add(progress)
        steps = named(column(0), "tk-steps"); steps.add_css_class("tk-card")
        steps.set_sensitive(writable)
        for index, (title_text, done) in enumerate(task["subs"]):
            step = line(10); step.add_css_class("tk-step"); step.set_size_request(-1, 34)
            step.append(check(done, 0, lambda i=index: self._mutate(lambda: self.source.step(task["id"], i)), f"tk-step-{index}", step=True))
            label = text(title_text, "body", wrap=True)
            if done: label.add_css_class("tk-step-done")
            step.append(label); steps.append(step)
        field = TextField("Add a step", placeholder="Add a step",
                          value=self._step_drafts.get(task["id"], ""),
                          on_changed=lambda value: self._step_drafts.__setitem__(task["id"], value),
                          on_activate=lambda value: self._add_step(task["id"], value))
        field.label.set_visible(False)
        field.add_css_class("tk-inline-field")
        field.add_css_class("tk-step-entry")
        field.entry.set_name("tk-add-step"); field.set_hexpand(True)
        add_step = line(10); add_step.add_css_class("tk-step"); add_step.add_css_class("tk-activity")
        add_step.set_size_request(-1, 44)
        plus = icons.image("plus", pixel_size=16); plus.set_margin_start(1); plus.set_margin_end(1)
        plus.set_valign(Gtk.Align.CENTER)
        add_step.append(plus); add_step.append(field); steps.append(add_step)
        pane.add(steps)

        # Comments: their own inset card.
        pane.add_section("Comments")
        if task.get("comments_local"):
            pane.add(text("Comments stay on this computer", "caption", muted=True))
        talk = named(column(10), "tk-comments"); talk.add_css_class("tk-card"); talk.add_css_class("tk-talk")
        for activity in task["act"]: talk.append(self._activity(activity))
        if not task["act"]: talk.append(text("No activity yet.", "body", muted=True))
        talk.append(self._comment_footer(task, self.source_list(task['l'])))
        pane.add(talk)

    def _fact(self, parent, icon, name, control, *, wide=False):
        # A wide fact (Priority) puts its control on its own line under the label, the card's full width.
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if wide else Gtk.Orientation.HORIZONTAL, spacing=8 if wide else 12)
        row.add_css_class("tk-fact")
        if wide: row.add_css_class("wide")
        if parent.get_first_child() is not None: row.add_css_class("following")
        label = line(8); label.set_size_request(92, -1); label.add_css_class("tk-fact-label")
        label.append(icons.image(icon, pixel_size=16)); label.append(text(name, "meta"))
        label.set_valign(Gtk.Align.START if wide else Gtk.Align.CENTER)
        row.append(label); control.set_hexpand(True)
        if not wide: control.set_valign(Gtk.Align.CENTER)
        row.append(control); parent.append(row)

    def _control(self, name, label, callback, icon="", *, danger=False):
        button = make_control(BarAction(icon, label or None, tooltip=name if not label else None, danger=danger), size="bubble")
        button.set_name(name); button.set_halign(Gtk.Align.START); button.set_hexpand(False)
        button.connect("clicked", lambda *_: callback(button))
        return button

    def _activity(self, activity):
        who, kind = activity[:2]
        row = line(10 if kind == "c" else 8); row.set_margin_top(5); row.set_margin_bottom(5)
        row.append(face(who, self.data["people"], 26 if kind == "c" else 18, self.fixture_dir))
        if kind == "c":
            bubble = column(3); bubble.set_hexpand(True); bubble.add_css_class("tk-comment")
            if who == "me": bubble.add_css_class("mine")
            head = line(8)
            author = text(self._name(who), "small", weight=700, hexpand=True)
            head.append(author); head.append(text(activity[3], "caption", weight=400))
            bubble.append(head); bubble.append(text(activity[2], "body", wrap=True)); row.append(bubble)
        else:
            row.add_css_class("tk-activity")
            words = line(3); words.set_hexpand(True)
            words.append(text(self._name(who), "small", weight=600, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR))
            words.append(text(kind, "small", wrap=True, hexpand=True))
            row.append(words)
            row.append(text(activity[2], "caption", weight=400))
        return row

    def _comment_footer(self, task, source):
        # The composer belongs to Comments and scrolls with that section.
        footer = line(8); footer.add_css_class("tk-composer"); footer.set_name("tk-composer")
        footer.set_sensitive(source.get('commentable', True))
        author = face("me", self.data["people"], 26, self.fixture_dir)
        author.set_valign(Gtk.Align.CENTER)
        footer.append(author)
        field = TextField("Comment", placeholder="Write a comment. @ to mention someone" if source["ppl"] else "Add a comment for yourself",
                          value=self._comment_drafts.get(task["id"], ""),
                          on_changed=lambda value: self._comment_drafts.__setitem__(task["id"], value))
        field.label.set_visible(False)
        field.add_css_class("tk-inline-field")
        field.set_hexpand(True); field.entry.set_name("tk-comment")
        self._comment_entry = field.entry
        field.entry.connect("activate", lambda *_: self._comment(task["id"], field))
        footer.append(field)
        send = named(make_control(BarAction("plus", tooltip="Add comment", primary=True, on_activate=lambda: self._comment(task["id"], field)), size="tool"), "tk-send")
        send.set_valign(Gtk.Align.CENTER)
        footer.append(send); self.comment_footer = footer
        return footer

    _BAR_NAMES = {"plus": "tk-new", "calendar": "tk-bar-due", "list": "tk-bar-move", "ellipsis": "tk-more",
                  "search": "tk-search-toggle", "rotate-ccw": "tk-bar-undone", "sun": "tk-bar-today",
                  "sunrise": "tk-bar-tomorrow", "flag": "tk-bar-flag", "x": "tk-bar-close"}

    def _bar(self):
        if self.phone: return self._phone_bar()
        if self.adding:
            return
        items = []
        task = self.task()
        if task and self.details_wanted:
            items.extend((BarChip(task["t"], lead=list_dot(self.source_list(task["l"])), on_dismiss=self.details.close),
                          BarAction("calendar", tooltip="Due date", on_activate=lambda: self._due(self._bar_anchor("tk-bar-due"))),
                          BarAction("list", tooltip="Move to list", on_activate=lambda: self._move(self._bar_anchor("tk-bar-move"))),
                          BarAction("ellipsis", tooltip="More", on_activate=lambda: self._more(self._bar_anchor("tk-more"))), SEPARATOR))
        entry = self._add_entry()
        self.center.show_bar(items, entry=entry) if items else self.center.show_bar([entry])
        self.add_field.widget.text_widget.set_name('tk-add')
        self._draft_changed(self.draft)
        self._name_bar(self.center.bar_row)

    def _name_bar(self, row):
        child = row.get_first_child()
        while child:
            item = getattr(child, "bar_item", None)
            if isinstance(item, BarChip):
                child.set_name("tk-bar-task")
            elif isinstance(item, BarAction):
                if item.icon in self._BAR_NAMES: child.set_name(self._BAR_NAMES[item.icon])
                if item.icon == "check": child.set_name("tk-bar-complete")
            child = child.get_next_sibling()

    def _phone_bar(self):
        """v71 Tasks on a phone: two rows, like Calendar. Top: the view as a dropdown (it replaces the
        sidebar) and Search; under a hairline, "Add a task", always there, read as you type. A picked
        task grows the bar with everything about it; the row becomes Complete, Today, Tomorrow, Flag, ✕."""
        task = self.task()
        if task and self.details_wanted and self.phone_panel is not None:
            key = task["id"]
            primary = (BarAction("rotate-ccw", "Not done", keep_label=True, fill=True,
                                 on_activate=lambda: self._edit(key, {"done": False}, "Not done: " + task["t"]))
                       if task["done"] else
                       BarAction("check", "Complete", primary=True, keep_label=True, fill=True,
                                 on_activate=lambda: self._edit(key, {"done": True}, "Done: " + task["t"])))
            self.center.show_bar([
                primary,
                BarAction("sun", tooltip="Today", on_activate=lambda: self._edit(key, {"due": 0}, "Moved to today")),
                BarAction("sunrise", tooltip="Tomorrow", on_activate=lambda: self._edit(key, {"due": 1}, "Moved to tomorrow")),
                BarAction("flag", tooltip="Flag", active=task.get("flag", False),
                          on_activate=lambda: self._edit(key, {"flag": not task.get("flag", False)})),
                BarAction("x", tooltip="Close", on_activate=self._phone_close_task)], fill=True)
            self._name_bar(self.center.bar_row)
            self.center.grow("task", self.phone_panel)
            return
        source = self.source_list(self.view)
        name, glyph = (source["n"], "") if source else next(((n, g) for k, n, g in VIEWS if k == self.view), VIEWS[0][1:])
        places = BarAction(glyph, name, dropdown=True, lead=list_dot(source) if source else None,
                           panel=self._places_panel, key="places")
        search = BarAction("search", tooltip="Search", active=self.searching or bool(self.query), on_activate=self._phone_search)
        self.center.show_bar([places, SPACER, search], entry=self._add_entry())
        self._name_bar(self.center.bar_row)
        first = self.center.bar_row.get_first_child()
        if first is not None: first.set_name("tk-places")
        if self.add_field.widget is not None:
            self.add_field.widget.text_widget.set_name("tk-add")
            self._draft_changed(self.draft)
        if self.searching: self._open_search()

    def _add_entry(self):
        source = self.source_list(self.view)
        placeholder = f'Add to {source["n"]}' if source else "Add a task"
        self.add_field = BarEntry("quick", icon="plus", text=self.draft,
                                  placeholder=placeholder + ", like Call Theo tomorrow 3pm",
                                  on_change=self._draft_changed, on_submit=lambda _: self._add_task())
        return self.add_field

    def _phone_close_task(self):
        self._comment_to_reveal = None
        self._cancel_detail_restore()
        self._detail_restore_target = None
        self.details_wanted = False
        self.phone_panel = None
        self.center.fold()
        self._bar()

    def _open_search(self):
        self.center.search(self._search, placeholder="Search tasks", text=self.query, on_close=self._search_closed)

    def _phone_search(self):
        if self.searching:
            self.center.close_search()
        else:
            self.searching = True
            self._phone_bar()  # Search is the raised chip while the field is the bottom row

    def _search_closed(self):
        self.searching = False
        self._phone_bar()

    def _places_panel(self):
        """The views and lists, grown from the bar's place dropdown (v71: they replace the sidebar on a phone)."""
        count = lambda key: sum(in_view(t, key) for t in self.data["tasks"])
        pick = lambda key: (lambda: self._select_view(key))
        tiles = BarTiles([BarTile(glyph, f"{title} · {count(key)}" if count(key) else title, pick(key), on=key == self.view, name=title)
                          for key, title, glyph in VIEWS[:3]], columns=3)
        rows = [tiles]
        rows += [PanelRow(title, icon=glyph, count=count(key) or None, current=key == self.view, on_activate=pick(key))
                 for key, title, glyph in VIEWS[3:]]
        rows.append("Lists")
        for source in self.data["lists"]:
            left = sum(t["l"] == source["id"] and not t["done"] for t in self.data["tasks"])
            shared = "Shared with " + ", ".join(self._name(k) for k in source["ppl"]) if source["ppl"] else None
            rows.append(PanelRow(source["n"], lead=list_dot(source), subtitle=shared, count=left or None,
                                 current=source["id"] == self.view, on_activate=pick(source["id"])))
        rows.append(PanelRow("New list", icon="plus", on_activate=self._new_list))
        panel = panel_list(rows, label="Lists")
        panel.set_name("tk-places-panel")
        return panel

    def _bar_anchor(self, name):
        def find(widget):
            child = widget.get_first_child()
            while child:
                if child.get_name() == name and child.get_mapped(): return child
                found = find(child)
                if found: return found
                child = child.get_next_sibling()
        return find(self.center) or self.center

    def _menu(self, anchor, rows, *, label=""):
        if self.phone:  # v71: on a phone every menu rises from the bar
            items = [MenuItem(row.label, icon=row.icon, note=row.description or None, on_activate=row.execute,
                              selected=bool(row.checked and row.checked()), danger=row.destructive)
                     for row in rows if row.enabled()]
            # From inside the grown task it rises in the bar's frame over it (v71 tkPop on a phone), so the
            # task stays open behind the menu: anchor it on the island, not on a bar button.
            return bar_menu(self.island, ([label] if label else []) + items, label=label or "Menu")
        from dataclasses import replace
        rows = [replace(row, icon=icons.icon_name(row.icon) if row.icon else None) for row in rows]
        registry = CommandRegistry((CommandGroup(label, tuple(rows)),))
        menu = Menu(registry); menu.set_parent(anchor)
        if anchor.get_name() in ("tk-more", "tk-bar-due", "tk-bar-move"): menu.set_position(Gtk.PositionType.TOP)
        menu.connect("closed", lambda pop: GLib.idle_add(lambda: pop.unparent() or False))
        menu.popup()
        return menu

    def _due(self, anchor):
        task = self.task()
        if not task: return
        rows = [Command("due." + str(offset), label, lambda d=offset: self._edit(task["id"], {"due": d}), icon,
                        description=day_label(offset, self.source.today) if offset is not None else "")
                for offset, label, icon in ((0, "Today", "sun"), (1, "Tomorrow", "sunrise"),
                                            (3, "This weekend", "calendar"), (7, "Next week", "calendar"), (None, "No date", "x"))]
        self._menu(anchor, rows)

    def _save_time(self, key, value):
        from .tasks_parse import parse_clock
        try:
            if value.strip(): parse_clock(value)
        except ValueError as error:
            Toast.show(self.host, str(error), kind='warning')
            return
        self._edit(key, {'time': value.strip()}, 'Saved task time')

    def _repeat(self, anchor):
        task = self.task()
        if task is None:
            return
        rows = [Command('repeat.' + key, name,
                        lambda rule=rule: self._edit(task['id'], {'repeat': rule}, 'Saved repeat'),
                        'x' if not rule else 'repeat',
                        checked=lambda rule=rule: task.get('repeat', '') == rule and (bool(rule) or not task.get('recurring')))
                for key, name, rule in REPEAT_CHOICES]
        rows.append(Command('repeat.custom', 'Custom…', lambda: self._repeat_custom(anchor), 'sliders-horizontal'))
        self._repeat_menu = self._menu(anchor, rows, label='Repeat')

    def _repeat_custom(self, anchor):
        task = self.task()
        if task is None:
            return
        fields = dict(part.split('=', 1) for part in task.get('repeat', '').split(';') if '=' in part)
        frequency = fields.get('FREQ', 'WEEKLY')
        if frequency not in dict(REPEAT_UNITS):
            frequency = 'WEEKLY'
        interval = TextField('Repeat every', value=fields.get('INTERVAL', '1'))
        interval.set_name('tk-repeat-interval')
        interval.entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        unit = [frequency]
        units = ModeSwitch(REPEAT_UNITS, fill=True, size='small', current=frequency,
                           label='Repeat unit', on_change=lambda value: unit.__setitem__(0, value))
        units.set_name('tk-repeat-units')
        def save():
            try:
                rule = (task.get('repeat', '') if task.get('repeat') and interval.entry.get_text().strip() == fields.get('INTERVAL', '1') and unit[0] == frequency
                        else custom_rule(task.get('repeat', ''), interval.entry.get_text(), unit[0]))
            except ValueError as error:
                interval.entry.set_tooltip_text(str(error))
                interval.entry.grab_focus()
                Toast.show(self.host, str(error), kind='warning')
                return
            self._repeat_menu.close()
            self._edit(task['id'], {'repeat': rule}, 'Saved repeat')
        submit = TextButton('Save repeat', style='key', on_click=save)
        submit.set_name('tk-repeat-save')
        panel = column(8)
        panel.append(interval); panel.append(units); panel.append(submit)
        if self.phone:
            self._repeat_menu = bar_menu(self.island, [MenuSection(panel)], label='Custom repeat', title='Custom repeat')
        else:
            self._repeat_menu = FloatingMenu([MenuSection(panel)], label='Custom repeat').popup(anchor, align='start')

    def _assign(self, anchor):
        task = self.task(); source = self.source_list(task["l"])
        def assign(key):
            who = task["who"]
            changed = [p for p in who if p != key] if key in who else who + [key]
            self._edit(task["id"], {"who": changed})
        rows = [Command("assign." + key, "You" if key == "me" else self.data["people"][key]["n"],
                        lambda k=key: assign(k), "user",
                        description="Owner" if key == "me" else "@" + self.data["people"][key].get("u", key),
                        checked=lambda k=key: k in task["who"])
                for key in ["me", *source["ppl"]]]
        rows.append(Command("assign.add", "Invite someone to " + source["n"] + "…" if source["ppl"] else "Share " + source["n"] + " to assign people…", lambda: self._share(anchor), "user-plus", enabled=lambda: self.source.fixture or source.get("writable", False)))
        self._menu(anchor, rows, label="Assign to")

    def _move(self, anchor):
        task = self.task()
        if not task: return
        rows = [Command("move." + source["id"], source["n"], lambda s=source: self._move_to(task, s), "list") for source in self.data["lists"]]
        self._menu(anchor, rows)

    def _move_to(self, task, source):
        self._mutate(lambda: self.source.move(task["id"], source["id"]), "Moved to " + source["n"], deselect=True)

    def _share(self, anchor):
        source = self.source_list(self.view)
        if source is not None:
            self._share_records(anchor, source['n'], [t for t in self.data['tasks'] if t['l'] == source['id']], list_id=source['id'])
            return
        if self.task(): self._share_task(self.task(), anchor)
        return

    def _share_task(self, task, anchor):
        self._share_records(anchor, task['t'], [task])

    def _share_records(self, anchor, title, tasks, *, list_id=None):
        from .sharing_data import tasks_copy
        from .sharing_files import share_calendar_copy
        from .tasks_backend import Task
        from datetime import datetime, timedelta
        records = []
        for task in tasks:
            if not self.source.fixture:
                records.append(self.source.records[task['id']])
                records.extend(self.source.records['|'.join(key)] for key in task.get('step_keys', ()))
                continue
            due = self.source.today + timedelta(days=task['due']) if task.get('due') is not None else None
            if due and task.get('time'):
                from .tasks_parse import parse_clock
                due = datetime.combine(due, parse_clock(task['time'])).astimezone()
            records.append(Task(task['id'], task['l'], task['t'], due=due, completed=task['done'], notes=task['notes']))
        if not self.source.fixture:
            from .connect_sync import load_identity
            if load_identity() is not None:
                from .tasks_collaboration import SOURCE_PREFIX, task_content
                from .collaboration_ui import NativeCollaborationShare
                from luma_appkit import ShareResult
                kind = 'list' if list_id is not None else 'task'
                local_id = list_id if list_id is not None else tasks[0]['id']
                content = {'title': title, 'tasks': [task_content(record) for record in records]} if kind == 'list' else {'title': title, 'task': task_content(records[0]), 'steps': [task_content(record) for record in records[1:]]}
                if tasks and tasks[0]['l'].startswith(SOURCE_PREFIX):
                    snapshot = self.source.repository.shared[tasks[0]['l']]
                    kind, content, local_id = snapshot['kind'], snapshot['content'], tasks[0]['l']
                def copied(choice, value):
                    self._sharing_menu = share_calendar_copy(self, anchor, title, tasks_copy(records))
                    return ShareResult(False, 'Choose the destination for this task copy.')
                self._collaboration_share = NativeCollaborationShare(self, anchor, title=title, kind=kind,
                    local_id=local_id, content=content, copy_choice=copied,
                    on_changed=lambda _snapshot: self._reload())
                return
        self._sharing_menu = share_calendar_copy(self, anchor, title, tasks_copy(records))

    def _more(self, anchor):
        rows = [Command("task.duplicate", "Duplicate", lambda: self._fixture_action("Duplicate"), "copy", enabled=lambda: self.source.fixture),
                Command("task.move", "Move to list…", lambda: self._move(anchor), "list"),
                Command("task.list", "Turn into a list", lambda: self._fixture_action("Turn into a list"), "layers", enabled=lambda: self.source.fixture),
                Command("task.delete", "Delete", self._delete, "trash-2", destructive=True)]
        self._menu(anchor, rows)

    # ── Plan my day (phone) ──────────────────────────────────────────────

    def _plan_open(self):
        if self.plan is not None: return
        today = [t for t in self.data["tasks"] if not t["done"] and t.get("due") is not None and t["due"] <= 0]
        self._plan_added = 0
        self.plan = PlanDeck(plan_tasks(self.data["tasks"]), today, lists=self.data["lists"], people=self.data["people"],
                             today_date=self.source.today, fixture_dir=self.fixture_dir,
                             on_decide=self._plan_decide, on_close=self._plan_close)
        self.plan.set_compact(self.phone)
        self.cover.add_overlay(self.plan)
        if self.source.fixture and os.environ.get("LUMA_TASKS_PLAN_END"):
            self.plan.skip_to_end()  # fixture-only start-up mode: the deck's ending, nothing written

    def _plan_decide(self, task, decision):
        # Today puts it on today; Not today pushes a late or undated one to tomorrow; Skip leaves it.
        if decision > 0: self._plan_queue.append((task["id"], {"due": 0}))
        elif decision < 0 and (task.get("due") is None or task["due"] <= 0): self._plan_queue.append((task["id"], {"due": 1}))
        self._plan_pump()

    def _plan_pump(self):
        # One write at a time (the worker is serial); the deck has already moved on.
        if self.busy or not self._plan_queue: return
        key, changes = self._plan_queue.pop(0)
        task = self.task(key)
        if task is None or all(task.get(f) == v for f, v in changes.items()): return self._plan_pump()
        self._mutate(lambda: self.source.edit(key, changes), None, then=self._plan_pump)

    def _plan_close(self, added):
        if self.plan is None: return
        self.cover.remove_overlay(self.plan); self.plan = None
        if added: Toast.show(self.host, f"{added} added to today")

    def _fixture_action(self, kind):
        # v70's Duplicate/Turn into a list are notices too, not store operations.
        Toast.show(self.host, "Duplicated" if kind == "Duplicate" else "Converted into a list with 0 tasks")

    def _delete(self):
        if self._flush_fields(self._delete):
            return
        task = self.task()
        if not task: return
        self._mutate(lambda: self.source.delete(task["id"]), "Deleted: " + task["t"], deselect=True)

    def _delete_task(self, key):
        if self._flush_fields(lambda: self._delete_task(key)): return
        task = self.task(key)
        if task is None: return
        self._mutate(lambda: self.source.delete(key), 'Deleted: ' + task['t'], deselect=self.selected == key)

    def _edit(self, key, changes, message="Saved"):
        task = self.task(key)
        if not task: return
        changed = {field: value for field, value in changes.items() if task.get(field) != value or (field == 'repeat' and not value and task.get('recurring'))}
        if changed:
            if not self.source.fixture and set(changed) <= {"flag", "who"} and ("who" not in changed or not task['l'].startswith('luma-shared:')):
                message = "Saved on this computer"
            self._mutate(lambda: self.source.edit(key, changed), message)

    def _mutate(self, operation, message="Saved", deselect=False, then=None, on_result=None):
        if self.busy: return
        self.busy = True
        self.generation += 1  # an older read cannot overwrite a successful edit
        def finished(undo, error):
            self.busy = False
            if error:
                self._after_busy = None
                Toast.show(self.host, error, kind="error"); return
            if deselect: self.selected = None
            self.undo_action = undo
            if message: Toast.show(self.host, message, undo=(lambda: self._mutate(undo, "Undone")) if callable(undo) else None)
            if on_result is not None: on_result(undo)
            self._reload()
            if then is not None: then()
            waiting, self._after_busy = self._after_busy, None
            if waiting is not None: GLib.idle_add(lambda: (waiting(), False)[1])
        self._run(operation, finished)

    @staticmethod
    def _pending_values(pending):
        _key, title, notes, clock, start = pending
        return {"t": title.text.strip(), "notes": notes.text, "time": clock.entry.get_text().strip(), 'start_time': start.entry.get_text().strip()}

    def _flush_fields(self, then=None):
        pending = self._pending_fields
        if pending is None: return False
        key, title, notes, clock, start = pending
        task = self.task(key)
        if task is None: self._pending_fields = None; return False
        if self.busy:
            # A draft equal to the old baseline may revert the in-flight write.
            # Recheck it only after completion advances the saved baseline.
            if then is not None:
                self._after_busy = then
            elif self._after_busy is None:
                self._after_busy = lambda: self._flush_fields()
            return True
        changes = {field: value for field, value in self._pending_values(pending).items()
                   if self._pending_original.get(field, task.get(field, "")) != value}
        if not changes: return False
        if not changes.get("t", task["t"]):
            Toast.show(self.host, "Enter a task title.", kind="warning")
            title.grab_focus(); return True
        for time_key, field in (('time', clock), ('start_time', start)):
            if not changes.get(time_key): continue
            from .tasks_parse import parse_clock
            try: parse_clock(changes[time_key])
            except ValueError as error:
                Toast.show(self.host, str(error), kind='warning')
                field.entry.grab_focus()
                return True
        def saved():
            # The next close/navigation observes the saved values immediately;
            # the worker's later reload supplies the canonical store copy.
            task.update(changes)
            current = self._pending_fields
            if current and current[0] == key:
                # A worker save must not discard edits typed since submission.
                self._pending_original.update(changes)
                values = self._pending_values(current)
                if all(self._pending_original.get(field, task.get(field, "")) == value for field, value in values.items()):
                    self._pending_fields = None
            if then is not None: then()
        self._mutate(lambda: self.source.edit(key, changes), then=saved)
        return True

    def _new(self):
        if not self.data["lists"]: return
        if self.phone:  # the add field is always in the phone bar
            if self.searching: self._phone_search()
            if hasattr(self, "add_field"): self.add_field.focus()
            return
        self.adding = True
        source = self.source_list(self.view)
        placeholder = f'Add to {source["n"]}' if source else "Add a task"
        self.add_field = BarEntry("quick", icon="plus", text=self.draft,
                                  placeholder=placeholder + ', like “Call Theo tomorrow 3pm”',
                                  on_change=self._draft_changed, on_submit=lambda _: self._add_task(),
                                  on_close=self._finish_entry, close_label="Done")
        self.center.show_bar([self.add_field])
        self.add_field.widget.text_widget.set_name("tk-add")
        self._draft_changed(self.draft)
        self.add_field.focus()

    def _draft_changed(self, value):
        self.draft = value
        if hasattr(self, "add_field"):
            try:
                parsed = self._parse()
            except ValueError as error:
                self.add_field.set_chips((str(error),))
                return
            self.add_field.set_chips(quick_chips(value, parsed, self.source.today, self.data["lists"], self.data["people"]))

    def _parse(self):
        default = self.source_list(self.view) or next((s for s in self.data["lists"] if s.get("default")), next(iter(self.data["lists"]), None))
        return parse_task(self.draft, today=self.source.today,
                          lists=tuple(Choice(s["id"], s["n"]) for s in self.data["lists"]),
                          people=tuple(Choice(key, p["n"]) for key, p in self.data["people"].items()),
                          source_uid=default["id"] if default else "", view=self.view,
                          me="me" if self.source.fixture else self.source.repository.me)

    def _add_task(self):
        if self.busy: return  # Keep the draft until the current write finishes.
        try:
            parsed = self._parse()
        except ValueError as error:
            self.toast(str(error), kind='error')
            return
        if not parsed.title: return
        submitted = self.draft
        def reveal(key):
            self.selected = key
            self.details_wanted = True
            if self.query:
                self.query = ""; self.searching = False
                self.foot.entry.set_text("")
            # Smart views can hide a task with no due date, assignment, or flag.
            # Open its actual list so a successful Enter always has a visible result.
            due = parsed.due
            visible = (self.view == parsed.source_uid or
                       self.view == "today" and due is not None and day(due) <= self.source.today or
                       self.view == "upcoming" and due is not None and day(due) > self.source.today or
                       self.view == "mine" and parsed.assignee == ("me" if self.source.fixture else self.source.repository.me))
            if not visible: self.view = parsed.source_uid
        def saved():
            if self.draft == submitted:
                self.draft = ""; self.add_field.clear(); self.add_field.set_chips([])
        self._mutate(lambda: self.source.add(parsed), "Added to " + self.source_list(parsed.source_uid)["n"],
                     then=saved, on_result=reveal)

    def _finish_entry(self):
        self.adding = False
        self.draft = ""; self.center.fold(); self._bar()

    def _new_list(self):
        self._list_name_dialog(None)

    def _list_context(self, gesture, anchor, uid):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        source = self.source_list(uid)
        if source is None: return
        rows = [
            Command("list.rename", "Rename list…", lambda: self._list_name_dialog(uid), "pencil"),
            Command("list.color", "Change colour…", lambda: self._list_color_menu(anchor, uid), "palette"),
            Command("list.add", "New task in this list", lambda: self._new_in_list(uid), "plus"),
            Command('list.delete', 'Delete list…', lambda: self._delete_list_menu(anchor, uid), 'trash-2', destructive=True),
        ]
        self._menu(anchor, rows, label=source["n"])

    def _delete_list_menu(self, anchor, uid):
        source = self.source_list(uid)
        if source is None: return
        tasks = [t for t in self.data['tasks'] if t['l'] == uid]
        def confirm(destination=None):
            target = self.source_list(destination) if destination else None
            consequence = (f'Move {len(tasks)} tasks to {target["n"]}, then delete this list.' if target else
                           f'Delete this list and its {len(tasks)} tasks.' if tasks else 'Delete this empty list.')
            def removed(_option):
                def saved():
                    self.view = destination or 'today'
                    self.selected = None
                self._mutate(lambda: self.source.delete_list(uid, destination), 'List deleted', then=saved, deselect=True)
            DestructiveDialog.ask(anchor, title=f'Delete {source["n"]}?', body=consequence,
                                  action='Move and delete' if target else 'Delete list', on_confirm=removed)
        if not tasks:
            confirm(); return
        rows = ['Keep tasks by moving them to']
        rows += [Command('list.delete.move.' + s['id'], s['n'], lambda key=s['id']: confirm(key), 'list')
                 for s in self.data['lists'] if s['id'] != uid and s.get('writable', True)]
        rows += [None, Command('list.delete.all', 'Delete all tasks and list…', confirm, 'trash-2', destructive=True)]
        self._menu(anchor, rows, label='Delete ' + source['n'])

    def _new_in_list(self, uid):
        if self._flush_fields(lambda: self._new_in_list(uid)): return
        self._select_view(uid)
        self._new()

    def _list_name_dialog(self, uid):
        source = self.source_list(uid) if uid else None
        entry = Gtk.Entry(text=source["n"] if source else "")
        entry.set_activates_default(True)
        dialog = Adw.AlertDialog(heading="Rename list" if source else "New list")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Rename" if source else "Create")
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        def save(_dialog, response):
            if response != "save": return
            name = entry.get_text().strip()
            if not name:
                Toast.show(self.host, "Enter a list name.", kind="warning")
                return
            if source:
                if name == source["n"]: return
                self._mutate(lambda: self.source.update_list(uid, name=name), "List renamed")
            else:
                self._mutate(lambda: self.source.create_list(name), "List created")
        dialog.connect("response", save)
        dialog.present(self)

    def _list_color_menu(self, anchor, uid):
        colours = (("Blue", 210), ("Purple", 270), ("Pink", 330),
                   ("Red", 5), ("Orange", 30), ("Yellow", 50), ("Green", 145))
        rows = [Command("list.color." + name.lower(), name,
                        lambda hue=hue: self._mutate(
                            lambda: self.source.update_list(uid, hue=hue), "List colour changed"),
                        "circle", checked=lambda hue=hue: self.source_list(uid).get("h") == hue)
                for name, hue in colours]
        self._menu(anchor, rows, label="List colour")

    def _add_step(self, key, value):
        if not value.strip() or self.busy: return
        def saved():
            if self._step_drafts.get(key, "") == value:
                self._step_drafts.pop(key, None)
        self._mutate(lambda: self.source.add_step(key, value.strip()), "Step added", then=saved)

    def _comment(self, key, field):
        submitted = field.text.strip()
        if not submitted.strip() or self.busy: return
        def saved():
            self._comment_to_reveal = (key, submitted)
            if self._comment_drafts.get(key, "").strip() == submitted:
                self._comment_drafts.pop(key, None)
        self._mutate(lambda: self.source.comment(key, submitted), "Comment added", then=saved)

    def _search(self, value):
        self.query = value; self._main()

    def _find(self):
        if self.phone:
            if not self.searching: self._phone_search()
            return
        self.foot.entry.grab_focus()

    def _close(self, *_):
        if self.busy:
            self._after_busy = self.close
            Toast.show(self.host, "Finishing your change…", busy=True); return True
        if self._flush_fields(self.close): return True
        self.closed = True; self.generation += 1
        self._cancel_detail_restore()
        self.executor.submit(self.source.close); self.executor.shutdown(wait=False)
        return False


class TasksApplication(Adw.Application):
    def __init__(self): super().__init__(application_id=APP_ID)
    def do_startup(self):
        Adw.Application.do_startup(self); install_appkit(); install_lumaui()
        path = Path(__file__).resolve().parent.parent / "style/tasks.css"
        add_style_sheet(os.environ.get("LUMA_TASKS_STYLE_PATH", str(path if path.exists() else Path("/usr/share/prairie-core/tasks.css"))))
    def do_activate(self): (self.props.active_window or TasksWindow(self)).present()


def main(): return TasksApplication().run([])
if __name__ == "__main__": raise SystemExit(main())
