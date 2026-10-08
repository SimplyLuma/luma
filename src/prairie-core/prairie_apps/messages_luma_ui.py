# SPDX-License-Identifier: Apache-2.0
"""Luma Messages screens (ADR-051): your username, QR code and link; starting a
conversation by username or link; a message request; safety numbers; reports.

Everything shown comes from the helper and, through it, the Hub: the username
and its suggestions, the profile link, the other person's name, the safety
number. Nothing here makes a value up; while an answer is on its way the
screen says so, and when it can't get one it says that.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import Avatar, TextButton  # noqa: E402

from .messages_accounts_ui import qr_widget  # noqa: E402
from .messages_luma import REPORT_REASONS, handle_from, safety_groups  # noqa: E402
from .messages_dialog import MessagesDialog

LOCK_ICON = "luma-lock-symbolic"
CHECK_DELAY_MS = 350


def _label(text: str, *classes: str, **kwargs) -> Gtk.Label:
    label = Gtk.Label(label=text, wrap=True, xalign=kwargs.pop("xalign", 0), **kwargs)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for name in classes:
        label.add_css_class(name)
    return label


def _pill(label: str, *, suggested: bool = False, destructive: bool = False) -> Gtk.Button:
    button = Gtk.Button(label=label, halign=Gtk.Align.CENTER)
    button.add_css_class("pill")
    if suggested:
        button.add_css_class("suggested-action")
    if destructive:
        button.add_css_class("destructive-action")
    return button


def encrypted_badge(text: str = "End-to-end encrypted") -> Gtk.Widget:
    """The lock and a word or two: how a Luma conversation says what it is."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, halign=Gtk.Align.START)
    box.add_css_class("messages-luma-badge")
    icon = Gtk.Image(icon_name=LOCK_ICON, pixel_size=12)
    icon.add_css_class("messages-luma-lock")
    box.append(icon)
    box.append(Gtk.Label(label=text))
    box.update_property([Gtk.AccessibleProperty.LABEL], [text])
    return box


class _Page:
    """A scrolled form page beneath the shared modal's page controls."""

    def __init__(self, title: str, tag: str, *, navigation: bool = True) -> None:
        self.toolbar = Adw.ToolbarView()
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.clamp = Adw.Clamp(maximum_size=420, tightening_threshold=340)
        for setter in (self.clamp.set_margin_start, self.clamp.set_margin_end, self.clamp.set_margin_top, self.clamp.set_margin_bottom):
            setter(20)
        self.scroll.set_child(self.clamp)
        self.toolbar.set_content(self.scroll)
        self.actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.actions.set_margin_start(20)
        self.actions.set_margin_end(20)
        self.actions.set_margin_top(8)
        self.actions.set_margin_bottom(20)
        self.actions.set_visible(False)
        self.toolbar.add_bottom_bar(self.actions)
        # A dialog with one screen holds the toolbar itself; one that pushes
        # screens holds it in a navigation page.
        self.page = Adw.NavigationPage(child=self.toolbar, title=title, tag=tag) if navigation else None

    def show(self, widget: Gtk.Widget) -> None:
        while child := self.actions.get_first_child():
            self.actions.remove(child)
        self.actions.set_visible(False)
        self.clamp.set_child(widget)

    def set_action(self, widget: Gtk.Widget) -> None:
        """Keep the page's commit action visible while its form scrolls."""
        self.actions.append(widget)
        self.actions.set_visible(True)


def _waiting(text: str) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, vexpand=True)
    box.set_margin_top(60)
    box.append(Adw.Spinner(halign=Gtk.Align.CENTER, width_request=28, height_request=28))
    box.append(_label(text, "dim-label", justify=Gtk.Justification.CENTER, xalign=0.5))
    return box


def _problem(text: str, retry=None) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER)
    box.set_margin_top(48)
    icon = Gtk.Image(icon_name="dialog-warning-symbolic", pixel_size=40)
    icon.add_css_class("dim-label")
    box.append(icon)
    box.append(_label(text, justify=Gtk.Justification.CENTER, xalign=0.5))
    if retry is not None:
        again = _pill("Try Again")
        again.connect("clicked", lambda *_: retry())
        box.append(again)
    return box


class LumaProfileDialog(MessagesDialog):
    """Your Luma username, its QR code and link, and messaging someone by theirs.

    Without a username yet it is the place to choose one; the Hub's suggestions
    are offered as they come, and each name is checked as it is typed.
    """

    def __init__(self, window, service, *, start_with: str = "") -> None:
        super().__init__(title="Luma", content_width=440, content_height=640, follows_content_size=False)
        self.add_css_class("messages-luma-dialog")
        self.window, self.service = window, service
        self.identity: dict | None = None
        self._check_source = 0
        self._check_serial = 0
        self._claiming = False
        self.start_with = start_with
        self.view = Adw.NavigationView()
        self.main = _Page("Luma", "profile")
        self.view.add(self.main.page)
        self.set_child(self.view)
        self.load()

    # Loading
    def load(self) -> None:
        self.main.show(_waiting("Getting your Luma profile…"))
        self.window.luma_call(self.service, "luma.identity", {}, self._loaded)

    def _loaded(self, result: dict | None, error) -> None:
        if error is not None:
            self.main.show(_problem(error.message or "Luma isn't connected right now.", self.load))
            return
        self.identity = result or {}
        if self.identity.get("handle"):
            self._show_profile()
        else:
            self._show_claim()

    # Choosing a username
    def _show_claim(self, *, changing: bool = False) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_name("luma-claim")
        box.append(_label("Change your username" if changing else "Choose your Luma username", "title-2"))
        rules = (self.identity or {}).get("rules") or {}
        changes = (self.identity or {}).get("changes_left")
        detail = "People can find you and message you by your username. It never shows your phone number or email."
        if changing and isinstance(changes, int):
            detail = (f"You can change it {changes} more time{'s' if changes != 1 else ''} this year. "
                      f"Your old username is kept for you for {rules.get('hold_days', 30)} days.")
        box.append(_label(detail, "dim-label"))
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        entry_row.add_css_class("messages-luma-handle-field")
        at = Gtk.Label(label="@")
        at.add_css_class("messages-luma-at")
        entry_row.append(at)
        self.handle_entry = Gtk.Entry(hexpand=True, placeholder_text="username", max_length=int(rules.get("max", 30)))
        self.handle_entry.add_css_class("flat")
        self.handle_entry.set_input_hints(Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.LOWERCASE)
        self.handle_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Username"])
        entry_row.append(self.handle_entry)
        box.append(entry_row)
        self.handle_status = _label(f"{rules.get('min', 3)} to {rules.get('max', 30)} letters, digits, dots or underscores.", "messages-luma-hint")
        box.append(self.handle_status)
        suggestions = [s for s in (self.identity or {}).get("suggestions") or [] if isinstance(s, str)][:5]
        if suggestions and not changing:
            box.append(_label("Suggestions", "heading"))
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=6, row_spacing=6)
            for suggestion in suggestions:
                chip = Gtk.Button(label=f"@{suggestion}")
                chip.add_css_class("messages-filter")
                chip.connect("clicked", lambda _b, s=suggestion: self.handle_entry.set_text(s))
                flow.append(chip)
            box.append(flow)
        self.claim_button = TextButton("Change Username" if changing else "Claim Username", style="key")
        self.claim_button.set_name("luma-claim-submit")
        self.claim_button.set_sensitive(False)
        self.claim_button.connect("clicked", lambda *_: self._claim())
        self.handle_entry.connect("changed", self._handle_changed)
        self.handle_entry.connect("activate", lambda *_: self.claim_button.get_sensitive() and self._claim())
        if changing:
            page = _Page("Username", "claim")
            page.show(box)
            page.set_action(self.claim_button)
            self.view.push(page.page)
        else:
            self.main.show(box)
            self.main.set_action(self.claim_button)
        self.handle_entry.grab_focus()

    def _handle_changed(self, entry: Gtk.Entry) -> None:
        self.claim_button.set_sensitive(False)
        if self._check_source:
            GLib.source_remove(self._check_source)
            self._check_source = 0
        # An empty field also invalidates a request already in flight.
        self._check_serial += 1
        text = entry.get_text().strip().removeprefix("@")
        if not text:
            self.handle_status.set_label("")
            return
        serial = self._check_serial
        self.handle_status.set_label("Checking…")
        self.handle_status.remove_css_class("error")
        self.handle_status.remove_css_class("success")

        def check() -> bool:
            self._check_source = 0
            self.window.luma_call(self.service, "luma.handle.check", {"handle": text},
                                  lambda result, error: self._checked(serial, text, result, error))
            return GLib.SOURCE_REMOVE
        self._check_source = GLib.timeout_add(CHECK_DELAY_MS, check)

    def _checked(self, serial: int, text: str, result: dict | None, error) -> None:
        if self._finished or self._claiming or serial != self._check_serial:
            return  # a later keystroke asked again
        if error is not None:
            self.handle_status.set_label(error.message or "Couldn't check that username.")
            return
        if result and result.get("available"):
            self.handle_status.set_label(f"@{result.get('handle') or text} is available.")
            self.handle_status.add_css_class("success")
            self.claim_button.set_sensitive(True)
        else:
            self.handle_status.set_label(str((result or {}).get("reason") or "That username isn't available."))
            self.handle_status.add_css_class("error")

    def _claim(self) -> None:
        if self._finished or self._claiming or not self.claim_button.get_sensitive():
            return
        handle = self.handle_entry.get_text().strip().removeprefix("@")
        self._claiming = True
        self.claim_button.set_sensitive(False)
        self.handle_entry.set_sensitive(False)
        self.handle_status.remove_css_class("success")
        self.handle_status.remove_css_class("error")
        self.handle_status.set_label("Claiming…")

        def done(result, error) -> None:
            if self._finished:
                return
            self._claiming = False
            self.handle_entry.set_sensitive(True)
            if error is not None:
                self.handle_status.set_label(error.message or "That username couldn't be claimed.")
                self.handle_status.add_css_class("error")
                self.claim_button.set_sensitive(True)
                return
            self.identity = {**(self.identity or {}), **(result or {})}
            self.view.pop_to_tag("profile")
            self._show_profile()
            self.window.luma_profile_changed(self.service)
        self.window.luma_call(self.service, "luma.handle.claim", {"handle": handle}, done)

    def close(self) -> None:
        if self._check_source:
            GLib.source_remove(self._check_source)
            self._check_source = 0
        self._check_serial += 1
        super().close()

    # Your profile
    def _show_profile(self) -> None:
        identity = self.identity or {}
        handle = str(identity.get("handle") or "")
        name = str(identity.get("name") or "")
        link = str(identity.get("profile_link") or "")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_name("luma-profile")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        head.append(Avatar(name or handle, medium=True))
        if name:
            head.append(_label(name, "title-3", justify=Gtk.Justification.CENTER, xalign=0.5))
        handle_label = _label(f"@{handle}", "messages-luma-handle", justify=Gtk.Justification.CENTER, xalign=0.5)
        head.append(handle_label)
        box.append(head)
        if link:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
            card.add_css_class("messages-luma-qr-card")
            card.append(qr_widget(link, f"QR code for @{handle}"))
            box.append(card)
            box.append(_label("Someone can scan this with their phone's camera to message you on Luma.", "dim-label",
                              justify=Gtk.Justification.CENTER, xalign=0.5))
            link_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            link_row.add_css_class("messages-luma-link-row")
            link_label = Gtk.Label(label=link.removeprefix("https://"), xalign=0, hexpand=True, selectable=True,
                                   ellipsize=Pango.EllipsizeMode.MIDDLE)
            link_label.add_css_class("messages-luma-link")
            link_row.append(link_label)
            copy = Gtk.Button(label="Copy Link")
            copy.add_css_class("flat")
            copy.connect("clicked", lambda *_: self._copy(link, copy))
            link_row.append(copy)
            box.append(link_row)
        start = Adw.PreferencesGroup(title="Message someone",
                                     description="Enter their username, or paste a Luma link someone shared with you.")
        self.start_entry = Adw.EntryRow(title="Username or link")
        self.start_entry.set_show_apply_button(True)
        self.start_entry.connect("apply", lambda *_: self._start())
        self.start_entry.connect("entry-activated", lambda *_: self._start())
        start.add(self.start_entry)
        box.append(start)
        self.start_status = _label("", "messages-luma-hint")
        box.append(self.start_status)
        settings = Adw.PreferencesGroup()
        change = Adw.ButtonRow(title="Change Username")
        change.connect("activated", lambda *_: self._show_claim(changing=True))
        change.set_sensitive(int(identity.get("changes_left") or 0) > 0)
        settings.add(change)
        box.append(settings)
        self.main.show(box)
        if self.start_with:
            self.start_entry.set_text(self.start_with)
            self.start_with = ""
            self._start()

    def _copy(self, text: str, button: Gtk.Button) -> None:
        Gdk.Display.get_default().get_clipboard().set(text)
        button.set_label("Copied")
        GLib.timeout_add(1600, lambda: (button.set_label("Copy Link"), GLib.SOURCE_REMOVE)[1])

    def _start(self) -> None:
        text = self.start_entry.get_text()
        handle = handle_from(text)
        if handle is None:
            self.start_status.set_label("That isn't a Luma username or link.")
            self.start_status.add_css_class("error")
            return
        self.start_status.remove_css_class("error")
        self.start_status.set_label(f"Looking up @{handle}…")

        def done(error) -> None:
            if error is None:
                self.close()
            else:
                self.start_status.set_label(error.message)
                self.start_status.add_css_class("error")
        self.window.start_luma_conversation(self.service, handle, done)


class SafetyNumberDialog(MessagesDialog):
    """The safety number of a conversation with one person, to compare in person."""

    def __init__(self, window, service, conversation: str, name: str) -> None:
        super().__init__(title="Safety Number", content_width=440, content_height=620, follows_content_size=False)
        self.add_css_class("messages-luma-dialog")
        self.window, self.service, self.conversation, self.name = window, service, conversation, name or "this person"
        self.page = _Page("Safety Number", "safety", navigation=False)
        self.set_child(self.page.toolbar)
        self.load()

    def load(self) -> None:
        self.page.show(_waiting("Working out the safety number…"))
        self.window.luma_call(self.service, "luma.safety", {"conversation": self.conversation}, self._loaded)

    def _loaded(self, result: dict | None, error) -> None:
        if error is not None:
            self.page.show(_problem(error.message or "The safety number isn't available right now.", self.load))
            return
        result = result or {}
        groups = safety_groups(str(result.get("digits") or ""))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_name("luma-safety")
        if result.get("changed"):
            warning = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            warning.add_css_class("messages-luma-warning")
            warning.append(Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.START))
            warning.append(_label(f"{self.name}'s safety number changed since you verified it. This happens when they add or "
                                  "remove a device. Compare it again before you share anything sensitive.", hexpand=True))
            box.append(warning)
        verified = bool(result.get("verified"))
        box.append(encrypted_badge("Verified" if verified else "Not verified"))
        box.append(_label(f"Compare these numbers with the ones on {self.name}'s device. If they match, your "
                          "conversation is encrypted between the two of you and no one else.", "dim-label"))
        grid = Gtk.Grid(column_spacing=18, row_spacing=10, halign=Gtk.Align.CENTER)
        grid.add_css_class("messages-luma-safety-grid")
        for index, group in enumerate(groups):
            cell = Gtk.Label(label=group)
            cell.add_css_class("messages-luma-safety-digits")
            grid.attach(cell, index % 4, index // 4, 1, 1)
        grid.update_property([Gtk.AccessibleProperty.LABEL], ["Safety number: " + " ".join(groups)])
        box.append(grid)
        qr = str(result.get("qr") or "")
        if qr:
            card = Gtk.Box(halign=Gtk.Align.CENTER)
            card.add_css_class("messages-luma-qr-card")
            card.append(qr_widget(qr, "Safety number code"))
            box.append(card)
        action = _pill("Clear Verification" if verified else "Mark as Verified", suggested=not verified)
        action.connect("clicked", lambda *_: self._verify(not verified))
        box.append(action)
        self.status = _label("", "messages-luma-hint", justify=Gtk.Justification.CENTER, xalign=0.5)
        box.append(self.status)
        self.page.show(box)

    def _verify(self, on: bool) -> None:
        def done(_result, error) -> None:
            if error is not None:
                self.status.set_label(error.message)
                return
            self.window.luma_conversation_changed(self.service, self.conversation)
            self.load()
        self.window.luma_call(self.service, "luma.verify", {"conversation": self.conversation, "verified": on}, done)


class ReportDialog(MessagesDialog):
    """Report someone to Luma. Only the messages the person chooses to include are sent."""

    def __init__(self, window, service, conversation: str, account: str, name: str, recent: list[tuple[str, str]]) -> None:
        super().__init__(title="Report", content_width=440, content_height=600, follows_content_size=False)
        self.add_css_class("messages-luma-dialog")
        self.window, self.service, self.conversation, self.account = window, service, conversation, account
        self.recent = recent  # (message id, text) of their latest messages, newest last
        page = _Page("Report", "report", navigation=False)
        self.set_child(page.toolbar)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_name("luma-report")
        box.append(_label(f"Report {name or 'this person'}", "title-2"))
        box.append(_label("Luma reviews reports. They won't know you reported them.", "dim-label"))
        reasons = Adw.PreferencesGroup(title="What's wrong?")
        self.reason = REPORT_REASONS[0][0]
        first = None
        for code, label in REPORT_REASONS:
            row = Adw.ActionRow(title=label)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if first is None:
                first = check
                check.set_active(True)
            else:
                check.set_group(first)
            check.connect("toggled", lambda c, code=code: c.get_active() and setattr(self, "reason", code))
            row.add_prefix(check)
            row.set_activatable_widget(check)
            reasons.add(row)
        box.append(reasons)
        options = Adw.PreferencesGroup()
        self.include = Adw.SwitchRow(title="Include their recent messages",
                                     subtitle=f"The last {len(recent)} they sent you. This is the only way Luma sees what anyone wrote."
                                     if recent else "They haven't sent you any messages.")
        self.include.set_active(bool(recent))
        self.include.set_sensitive(bool(recent))
        options.add(self.include)
        self.block = Adw.SwitchRow(title="Also block them", active=True)
        options.add(self.block)
        box.append(options)
        self.detail = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, height_request=72, top_margin=8, bottom_margin=8,
                                   left_margin=10, right_margin=10)
        self.detail.add_css_class("messages-luma-detail")
        self.detail.update_property([Gtk.AccessibleProperty.LABEL], ["Anything else Luma should know"])
        box.append(_label("Anything else? (optional)", "heading"))
        box.append(self.detail)
        self.send = _pill("Send Report", destructive=True)
        self.send.connect("clicked", lambda *_: self._send())
        box.append(self.send)
        self.status = _label("", "messages-luma-hint", justify=Gtk.Justification.CENTER, xalign=0.5)
        box.append(self.status)
        page.show(box)

    def _send(self) -> None:
        buffer = self.detail.get_buffer()
        args = {"conversation": self.conversation, "account": self.account, "reason": self.reason,
                "detail": buffer.get_text(*buffer.get_bounds(), False).strip()[:1000],
                "messages": [uid for uid, _text in self.recent] if self.include.get_active() else [],
                "block": self.block.get_active()}
        self.send.set_sensitive(False)
        self.status.set_label("Sending…")

        def done(_result, error) -> None:
            if error is not None:
                self.status.set_label(error.message or "The report couldn't be sent.")
                self.send.set_sensitive(True)
                return
            self.window.luma_conversation_changed(self.service, self.conversation)
            self.window.notice("Report sent. Thank you for telling us." + (" They're blocked." if args["block"] else ""))
            self.close()
        self.window.luma_call(self.service, "luma.report", args, done)


class RequestBar(Gtk.Box):
    """In place of the composer while a stranger's conversation waits for an answer."""

    def __init__(self, on_answer) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add_css_class("messages-luma-request")
        self.text = _label("", justify=Gtk.Justification.CENTER, xalign=0.5)
        self.text.add_css_class("messages-luma-request-text")
        self.append(self.text)
        self.detail = _label("They won't know you've seen this until you reply.", "dim-label",
                             justify=Gtk.Justification.CENTER, xalign=0.5)
        self.append(self.detail)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER, homogeneous=True)
        for label, answer, css in (("Block", "block", "destructive-action"), ("Delete", "declined", None),
                                   ("Accept", "accepted", "suggested-action")):
            button = Gtk.Button(label=label)
            button.add_css_class("pill")
            if css:
                button.add_css_class(css)
            button.connect("clicked", lambda _b, a=answer: on_answer(a))
            buttons.append(button)
        self.append(buttons)

    def show_for(self, name: str, handle: str, group: bool) -> None:
        who = name or handle or "Someone"
        tail = f" ({handle})" if handle and name and handle != name else ""
        self.text.set_label(f"{who}{tail} added you to this group." if group else f"{who}{tail} wants to message you.")


class SafetyNotice(Gtk.Box):
    """A conversation whose other side's devices changed."""

    def __init__(self, on_view) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.add_css_class("messages-luma-notice")
        self.icon = Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.CENTER)
        self.append(self.icon)
        self.text = _label("", hexpand=True)
        self.append(self.text)
        view = Gtk.Button(label="View", valign=Gtk.Align.CENTER)
        view.add_css_class("flat")
        view.connect("clicked", lambda *_: on_view())
        self.append(view)

    def show_for(self, name: str, flags: dict) -> bool:
        who = name or "This person"
        if flags.get("safety_changed"):
            self.add_css_class("strong")
            self.text.set_label(f"{who}'s safety number changed. You verified it before; compare it again.")
            return True
        self.remove_css_class("strong")
        if flags.get("devices_changed"):
            self.text.set_label(f"{who} signed in on a new device, or removed one.")
            return True
        return False
