# SPDX-License-Identifier: Apache-2.0
"""The "What's new" sheet: one release's notes, inside Depot.

Every place Depot offers a system release's notes opens this sheet -- the
update card while an update is offered or waiting for a restart, the "This
computer" facts, and the preview fixture -- so the notes read the same way
wherever they are reached. Nothing here opens a browser: when the notes cannot
be fetched or verified the sheet says so in place and offers Retry.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from . import release_notes  # noqa: E402
from .providers import ProviderError, run_async  # noqa: E402
from .release_notes import NotesError, ReleaseNotes  # noqa: E402

# One quiet mark of colour per type, so a long list can be scanned by kind.
TONES = {"feature": "violet", "improvement": "blue", "fix": "green", "security": "amber"}


def _label(text: str, style: str, *, wrap: bool = True, selectable: bool = False,
           heading: bool = False) -> Gtk.Label:
    kwargs = {"accessible_role": Gtk.AccessibleRole.HEADING} if heading else {}
    label = Gtk.Label(label=text, xalign=0, wrap=wrap, selectable=selectable, **kwargs)
    if wrap:
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.add_css_class(style)
    return label


def _button(text: str, *styles: str) -> Gtk.Button:
    button = Gtk.Button(label=text)
    button.add_css_class("luma-button")
    button.add_css_class("small")
    for style in styles:
        button.add_css_class(style)
    button.set_valign(Gtk.Align.CENTER)
    return button


class ReleaseNotesSheet(Adw.Dialog):
    """Loads a release's notes and shows them; the only notes view in Depot."""

    def __init__(self, *, name: str, url: str = "",
                 loader: Callable[[], ReleaseNotes] | None = None) -> None:
        super().__init__(title="What’s New", content_width=560, content_height=680,
                         follows_content_size=False)
        self.add_css_class("dp-rn-sheet")
        self.name = name
        self.url = url
        self.loader = loader or (lambda: release_notes.fetch(url, fallback_name=name))

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        done = _button("Done")
        done.connect("clicked", lambda *_: self.close())
        header.pack_end(done)
        view.add_top_bar(header)
        self.set_default_widget(done)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=150, vexpand=True)
        self.stack.add_named(self._loading_page(), "loading")
        self.error_page, self.error_text = self._error_page()
        self.stack.add_named(self.error_page, "error")
        view.set_content(self.stack)
        self.set_child(view)
        self.load()

    # ── Loading and failure ──────────────────────────────────────────────

    def load(self) -> None:
        cached = release_notes.CACHE.get(self.url) if self.url else None
        if cached is not None:
            self._show(cached)
            return
        self.stack.set_visible_child_name("loading")
        release_notes.journal("Depot: fetching release notes", url=self.url or "preview")

        def work() -> ReleaseNotes:
            try:
                return self.loader()
            except NotesError as error:
                raise ProviderError(error.detail, hint=error.message) from error

        run_async(work, self._loaded)

    def _loaded(self, result) -> None:
        if not result.ok:
            hint = result.error.hint or "These release notes could not be read."
            release_notes.journal(f"Depot: release notes unavailable: {result.error}", 4,
                                  url=self.url or "preview", error=str(result.error), shown=hint)
            self.error_text.set_label(hint)
            self.stack.set_visible_child_name("error")
            return
        notes = result.value
        if self.url:
            release_notes.CACHE.put(self.url, notes)
        release_notes.journal("Depot: release notes shown", url=self.url or "preview",
                              build=notes.build_id, changes=notes.count, verified=notes.verified)
        self._show(notes)

    def _loading_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        page.add_css_class("dp-rn-state")
        spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
        spinner.set_size_request(24, 24)
        spinner.set_halign(Gtk.Align.CENTER)
        page.append(spinner)
        words = _label("Loading release notes…", "dp-rn-state-detail", wrap=False)
        words.set_halign(Gtk.Align.CENTER)
        page.append(words)
        return page

    def _error_page(self) -> tuple[Gtk.Widget, Gtk.Label]:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        page.add_css_class("dp-rn-state")
        mark = Gtk.Image(icon_name="dialog-information-symbolic", pixel_size=20,
                         halign=Gtk.Align.CENTER)
        mark.add_css_class("dp-rn-state-mark")
        page.append(mark)
        title = _label("Release notes aren’t available", "dp-rn-state-title", heading=True)
        title.set_xalign(0.5)
        title.set_justify(Gtk.Justification.CENTER)
        page.append(title)
        text = _label("", "dp-rn-state-detail")
        text.set_xalign(0.5)
        text.set_justify(Gtk.Justification.CENTER)
        text.set_max_width_chars(44)
        page.append(text)
        retry = _button("Retry")
        retry.set_halign(Gtk.Align.CENTER)
        retry.set_margin_top(10)
        retry.connect("clicked", lambda *_: self.load())
        page.append(retry)
        return page, text

    # ── The notes ────────────────────────────────────────────────────────

    def _show(self, notes: ReleaseNotes) -> None:
        old = self.stack.get_child_by_name("notes")
        if old is not None:
            self.stack.remove(old)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("dp-rn")

        title = _label(notes.display_name or self.name or "Luma", "dp-rn-title", heading=True)
        body.append(title)
        count = f"{notes.count} change{'s' if notes.count != 1 else ''}" if notes.count else ""
        meta = " · ".join(part for part in (notes.date, count) if part)
        if meta:
            body.append(_label(meta, "dp-rn-meta"))

        if not notes.sections:
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            empty.add_css_class("dp-rn-empty")
            empty.append(_label("This update has no listed changes.", "dp-rn-details"))
            body.append(empty)
        for section in notes.sections:
            body.append(self._section(section))

        technical = self._technical(notes)
        if technical is not None:
            body.append(technical)

        scroller.set_child(body)
        self.stack.add_named(scroller, "notes")
        self.stack.set_visible_child_name("notes")

    def _section(self, section) -> Gtk.Widget:
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        block.add_css_class("dp-rn-section")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.add_css_class("dp-rn-sectionhead")
        dot = Gtk.Box(valign=Gtk.Align.CENTER)
        dot.add_css_class("dp-rn-dot")
        dot.add_css_class("tone-" + TONES.get(section.key, "muted"))
        head.append(dot)
        head.append(_label(section.label, "dp-rn-section-title", wrap=False, heading=True))
        amount = _label(str(len(section.notes)), "dp-rn-section-count", wrap=False)
        head.append(amount)
        block.append(head)

        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        items.add_css_class("dp-uplist")
        for index, note in enumerate(section.notes):
            if index:
                rule = Gtk.Box()
                rule.add_css_class("dp-acc-rule")
                items.append(rule)
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            row.add_css_class("dp-rn-item")
            row.append(_label(note.summary, "dp-rn-headline", selectable=True))
            if note.details:
                row.append(_label(note.details, "dp-rn-details", selectable=True))
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                [f"{note.summary}. {note.details}".strip()[:600]])
            items.append(row)
        block.append(items)
        return block

    def _technical(self, notes: ReleaseNotes) -> Gtk.Widget | None:
        facts = [(key, value) for key, value in (
            ("Build", notes.build_id), ("Version", notes.version),
            ("Channel", notes.channel.capitalize()),
            ("Signature", "Verified" if notes.verified else "")) if value]
        if not facts and not notes.packages:
            return None
        expander = Gtk.Expander(label="Technical details")
        expander.add_css_class("dp-rn-tech")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner.add_css_class("dp-rn-tech-body")
        for key, value in facts:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            name = _label(key, "dp-fact-key", wrap=False)
            name.set_size_request(96, -1)
            row.append(name)
            row.append(_label(value, "dp-fact-value", selectable=True))
            inner.append(row)
        if notes.packages:
            heading = _label(f"{len(notes.packages)} package"
                             f"{'s' if len(notes.packages) != 1 else ''} changed", "dp-fact-key")
            heading.set_margin_top(8)
            inner.append(heading)
            lines = []
            for change in notes.packages:
                if change.before and change.after:
                    lines.append(f"{change.name}  {change.before} → {change.after}")
                elif change.after:
                    lines.append(f"{change.name}  added {change.after}")
                else:
                    lines.append(f"{change.name}  removed")
            # Wrapped, never wider than the sheet: a long NEVRA must not widen it.
            inner.append(_label("\n".join(lines), "dp-rn-packages", selectable=True))
        expander.set_child(inner)
        return expander
