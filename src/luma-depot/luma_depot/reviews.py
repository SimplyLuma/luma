# SPDX-License-Identifier: Apache-2.0
"""Ratings and reviews on an app's page, and the sheet for writing one.

Reading is anonymous. Writing goes through this computer's Luma Connect
enrolment; when there is none the section says so plainly and offers to open
Luma Connect, rather than a Write button that fails after the typing is done.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from luma_installer.depot_reviews import (  # noqa: E402
    BODY_MAX, MINIMUM_RATINGS, TITLE_MAX, Review, ReviewPage, ReviewsClient,
    ReviewsError, is_enrolled)

from .providers import ProviderError, run_async  # noqa: E402

CONNECT_DESKTOP_ID = "org.projectluma.Connect.desktop"


def star_row(value: float, size: int = 11) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
    row.add_css_class("dp-star-row")
    for index in range(5):
        filled = value >= index + 0.5
        image = Gtk.Image(icon_name="starred-symbolic" if filled else "non-starred-symbolic",
                          pixel_size=size)
        image.add_css_class("filled" if filled else "empty")
        row.append(image)
    row.update_property([Gtk.AccessibleProperty.LABEL], [f"{value:.1f} out of 5 stars"])
    return row


def ratings_line(average: float, count: int) -> str:
    if count >= MINIMUM_RATINGS:
        return f"{count:,} rating{'s' if count != 1 else ''}"
    if count:
        return "Not enough ratings"
    return "No ratings yet"


def _when(text: str) -> str:
    try:
        from datetime import datetime
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return moment.strftime("%-d %B %Y")
    except (ValueError, AttributeError):
        return ""


def open_connect(parent: Gtk.Widget) -> bool:
    info = Gio.DesktopAppInfo.new(CONNECT_DESKTOP_ID)
    if info is None:
        return False
    try:
        display = parent.get_display()
        return info.launch([], display.get_app_launch_context() if display else None)
    except GLib.Error:
        return False


class ReviewsStore:
    """Review pages the window has read, shared by every redraw of a page.

    The window redraws an app page on every progress tick of an install. The
    reviews must not be fetched again each time, so requests and their answers
    live here and a section only draws what the store already holds.
    """

    def __init__(self, on_change, client: ReviewsClient | None = None) -> None:
        self.client = client or ReviewsClient()
        self.on_change = on_change
        self.pages: dict[str, ReviewPage] = {}
        self.errors: dict[str, str] = {}
        self.loading: set[str] = set()
        self.more_errors: set[str] = set()

    def ensure(self, slug: str) -> None:
        if slug in self.pages or slug in self.errors or slug in self.loading:
            return
        self._load(slug, "")

    def retry(self, slug: str) -> None:
        self.errors.pop(slug, None)
        self.ensure(slug)
        self.on_change()

    def invalidate(self, slug: str) -> None:
        self.pages.pop(slug, None)
        self.errors.pop(slug, None)
        self.ensure(slug)
        self.on_change()

    def more(self, slug: str) -> None:
        page = self.pages.get(slug)
        if page is None or not page.next_cursor or slug in self.loading:
            return
        self.more_errors.discard(slug)
        self._load(slug, page.next_cursor)
        self.on_change()

    def _load(self, slug: str, cursor: str) -> None:
        self.loading.add(slug)

        def work():
            try:
                return self.client.reviews(slug, cursor)
            except ReviewsError as error:
                raise ProviderError(str(error), hint=str(error)) from error

        run_async(work, lambda result: self._loaded(slug, cursor, result))

    def _loaded(self, slug: str, cursor: str, result) -> None:
        self.loading.discard(slug)
        if not result.ok:
            if cursor:
                self.more_errors.add(slug)
            else:
                self.errors[slug] = result.error.hint or "Reviews could not be loaded."
        elif cursor and slug in self.pages:
            base = self.pages[slug]
            self.pages[slug] = ReviewPage(base.average, base.count, base.reviews + result.value.reviews,
                                          result.value.next_cursor, base.own_review)
        else:
            self.pages[slug] = result.value
        self.on_change()


class ReviewsSection(Gtk.Box):
    """The whole "Ratings and reviews" block for one app."""

    def __init__(self, window, app, *, version: str, installed: bool,
                 store: ReviewsStore | None = None, preview_page: ReviewPage | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add_css_class("dp-reviews")
        self.window = window
        self.app = app
        self.version = version
        self.installed = installed
        self.store = store
        self.preview_page = preview_page
        self.client = store.client if store is not None else ReviewsClient()
        if preview_page is not None:
            self._show(preview_page)
            return
        page = store.pages.get(app.slug)
        if page is not None:
            self._show(page)
        elif app.slug in store.errors:
            self._failed(store.errors[app.slug])
        else:
            self._loading()
            store.ensure(app.slug)

    def _failed(self, message: str) -> None:
        self._summary(self.app.rating, self.app.rating_count)
        line = Gtk.Label(label=message, xalign=0, wrap=True)
        line.add_css_class("dp-quiet-line")
        self.append(line)
        again = Gtk.Button(label="Try again", halign=Gtk.Align.START)
        again.add_css_class("luma-button")
        again.add_css_class("small")
        again.add_css_class("quiet")
        again.connect("clicked", lambda *_: self.store.retry(self.app.slug))
        self.append(again)

    # ── Drawing ──────────────────────────────────────────────────────────

    def _clear(self) -> None:
        while child := self.get_first_child():
            self.remove(child)

    def _loading(self) -> None:
        self._clear()
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
        spinner.set_size_request(14, 14)
        line.append(spinner)
        label = Gtk.Label(label="Reading reviews…", xalign=0)
        label.add_css_class("dp-quiet-line")
        line.append(label)
        self.append(line)

    def _summary(self, average: float, count: int) -> None:
        summary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        summary.add_css_class("dp-rating-summary")
        if count >= MINIMUM_RATINGS:
            big = Gtk.Label(label=f"{average:.1f}")
            big.add_css_class("dp-rating-big")
            summary.append(big)
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER)
            column.append(star_row(average, 13))
            caption = Gtk.Label(label=ratings_line(average, count), xalign=0)
            caption.add_css_class("dp-rating-count")
            column.append(caption)
            summary.append(column)
        else:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
            heading = Gtk.Label(label=ratings_line(average, count), xalign=0)
            heading.add_css_class("dp-rating-none")
            column.append(heading)
            detail = Gtk.Label(
                label=("An average appears once three people have rated it." if count
                       else "Be the first to say what it is like."), xalign=0, wrap=True)
            detail.add_css_class("dp-rating-count")
            column.append(detail)
            summary.append(column)
        spacer = Gtk.Box(hexpand=True)
        summary.append(spacer)
        self.action_slot = Gtk.Box(valign=Gtk.Align.CENTER)
        summary.append(self.action_slot)
        self.append(summary)

    def _show(self, page: ReviewPage) -> None:
        self._clear()
        self._summary(page.average, page.count)
        self._action(page)
        if not page.reviews:
            empty = Gtk.Label(label="No written reviews yet.", xalign=0)
            empty.add_css_class("dp-quiet-line")
            self.append(empty)
            return
        listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        listing.add_css_class("dp-review-list")
        for index, review in enumerate(page.reviews):
            if index:
                rule = Gtk.Box()
                rule.add_css_class("dp-acc-rule")
                listing.append(rule)
            listing.append(self._card(review))
        self.append(listing)
        if page.next_cursor and self.preview_page is None:
            busy = self.app.slug in self.store.loading
            failed = self.app.slug in self.store.more_errors
            more = Gtk.Button(label="Reading\u2026" if busy else ("Try again" if failed else "More reviews"),
                              halign=Gtk.Align.START, sensitive=not busy)
            more.add_css_class("luma-button")
            more.add_css_class("small")
            more.add_css_class("quiet")
            more.connect("clicked", lambda *_: self.store.more(self.app.slug))
            self.append(more)

    def _action(self, page: ReviewPage) -> None:
        if self.preview_page is not None:
            button = Gtk.Button(label="Write a review")
            button.add_css_class("luma-button")
            button.add_css_class("small")
            button.connect("clicked", lambda *_: self.window._report(
                "Preview only", "Reviews are not posted from the preview."))
            self.action_slot.append(button)
            return
        enrolled = (self.client.is_enrolled() if hasattr(self.client, 'is_enrolled')
                    else is_enrolled(self.client.hub))
        if not enrolled:
            button = Gtk.Button(label="Sign in to Luma to review")
            button.get_child().set_ellipsize(Pango.EllipsizeMode.END)
            button.add_css_class("luma-button")
            button.add_css_class("small")
            button.add_css_class("quiet")
            button.set_tooltip_text("Reviews are tied to your Luma account. Luma Connect signs this computer in.")

            def sign_in(*_):
                if not open_connect(self):
                    self.window._report("Luma Connect is not installed",
                                        "Sign in at hub.simplyluma.com, then review from the web.")
            button.connect("clicked", sign_in)
            self.action_slot.append(button)
            return
        own = page.own_review
        button = Gtk.Button(label="Edit your review" if own else "Write a review")
        button.add_css_class("luma-button")
        button.add_css_class("small")
        button.connect("clicked", lambda *_: ReviewComposer(
            self.window, self.app, self.client, own, self.version, self.installed,
            on_saved=self._saved).present(self.window))
        self.action_slot.append(button)

    def _saved(self) -> None:
        self.store.invalidate(self.app.slug)

    def _card(self, review: Review) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("dp-review")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.append(star_row(review.rating, 11))
        if review.title:
            title = Gtk.Label(label=review.title, xalign=0, hexpand=True, wrap=True)
            title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            title.add_css_class("dp-review-title")
            head.append(title)
        card.append(head)
        meta_parts = [review.author]
        if review.own:
            meta_parts[0] = "You"
        when = _when(review.updated_at or review.created_at)
        if when:
            meta_parts.append(when)
        if review.version:
            meta_parts.append(f"Version {review.version}")
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        line = Gtk.Label(label=" · ".join(meta_parts), xalign=0)
        line.set_ellipsize(Pango.EllipsizeMode.END)
        line.add_css_class("dp-review-meta")
        meta.append(line)
        if review.installed_on_luma:
            chip = Gtk.Label(label="Installed on Luma")
            chip.add_css_class("dp-chip")
            meta.append(chip)
        card.append(meta)
        if review.body:
            body = Gtk.Label(label=review.body, xalign=0, wrap=True)
            body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_selectable(True)
            body.add_css_class("dp-review-body")
            card.append(body)
        if review.reply is not None:
            reply = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            reply.add_css_class("dp-review-reply")
            who = Gtk.Label(label=f"Reply from {review.reply.author or 'the developer'}", xalign=0)
            who.add_css_class("dp-review-reply-author")
            reply.append(who)
            text = Gtk.Label(label=review.reply.body, xalign=0, wrap=True)
            text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            text.add_css_class("dp-review-body")
            reply.append(text)
            card.append(reply)
        accessible = f"{review.rating} stars. {review.title}. {review.body}"
        card.update_property([Gtk.AccessibleProperty.LABEL], [accessible[:500]])
        return card


class ReviewComposer(Adw.Dialog):
    """Write, edit or delete this account's review of one app."""

    def __init__(self, window, app, client: ReviewsClient, existing: Review | None,
                 version: str, installed: bool, *, on_saved) -> None:
        super().__init__(title=f"Review {app.name}", content_width=440)
        self.window = window
        self.app = app
        self.client = client
        self.existing = existing
        self.version = version
        self.installed = installed
        self.on_saved = on_saved
        self.rating = existing.rating if existing else 0

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("luma-button")
        cancel.add_css_class("small")
        cancel.add_css_class("quiet")
        cancel.set_valign(Gtk.Align.CENTER)
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.post = Gtk.Button(label="Update" if existing else "Post")
        self.post.add_css_class("luma-button")
        self.post.add_css_class("small")
        self.post.add_css_class("primary")
        self.post.set_valign(Gtk.Align.CENTER)
        self.post.connect("clicked", lambda *_: self._save())
        header.pack_end(self.post)
        view.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.add_css_class("dp-composer")
        body.set_margin_top(8)
        prompt = Gtk.Label(label="Your rating", xalign=0)
        prompt.add_css_class("dp-composer-label")
        body.append(prompt)
        self.stars = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.stars.add_css_class("dp-star-picker")
        self.star_buttons = []
        for value in range(1, 6):
            button = Gtk.Button()
            button.add_css_class("flat")
            button.set_child(Gtk.Image(icon_name="non-starred-symbolic", pixel_size=22))
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [f"{value} star{'s' if value > 1 else ''}"])
            button.connect("clicked", lambda _b, v=value: self._rate(v))
            self.star_buttons.append(button)
            self.stars.append(button)
        body.append(self.stars)

        self.title = Gtk.Entry(placeholder_text="Title (optional)", max_length=TITLE_MAX)
        self.title.add_css_class("dp-composer-entry")
        self.title.update_property([Gtk.AccessibleProperty.LABEL], ["Review title"])
        if existing:
            self.title.set_text(existing.title)
        body.append(self.title)

        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                 top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.text.update_property([Gtk.AccessibleProperty.LABEL], ["Review"])
        if existing:
            self.text.get_buffer().set_text(existing.body)
        self.text.get_buffer().connect("changed", lambda *_: self._validate())
        frame = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=132)
        frame.add_css_class("dp-composer-text")
        frame.set_child(self.text)
        body.append(frame)

        self.note = Gtk.Label(xalign=0, wrap=True)
        self.note.add_css_class("dp-get-note")
        self.note.set_label(
            "Posted with your Luma account. "
            + ("Marked “Installed on Luma”. " if installed else "")
            + (f"About version {version}." if version else ""))
        body.append(self.note)
        self.error = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.error.add_css_class("dp-failed")
        body.append(self.error)

        if existing:
            delete = Gtk.Button(label="Delete review", halign=Gtk.Align.START)
            delete.add_css_class("luma-button")
            delete.add_css_class("small")
            delete.add_css_class("quiet")
            delete.add_css_class("destructive")
            delete.connect("clicked", lambda *_: self._confirm_delete())
            body.append(delete)

        view.set_content(body)
        self.set_child(view)
        self._rate(self.rating)

    def _rate(self, value: int) -> None:
        self.rating = value
        for index, button in enumerate(self.star_buttons):
            image = button.get_child()
            image.set_from_icon_name("starred-symbolic" if index < value else "non-starred-symbolic")
            if index < value:
                button.add_css_class("chosen")
            else:
                button.remove_css_class("chosen")
        self._validate()

    def _body(self) -> str:
        buffer = self.text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def _validate(self) -> None:
        length = len(self._body().strip())
        self.post.set_sensitive(1 <= self.rating <= 5 and length <= BODY_MAX)
        if length > BODY_MAX:
            self._fail(f"Reviews can be up to {BODY_MAX:,} characters.")
        elif self.error.get_visible() and self.error.get_label().startswith("Reviews can be"):
            self.error.set_visible(False)

    def _fail(self, message: str) -> None:
        self.error.set_label(message)
        self.error.set_visible(True)

    def _busy(self, busy: bool) -> None:
        self.post.set_sensitive(not busy)
        self.post.set_label("Posting…" if busy else ("Update" if self.existing else "Post"))

    def _save(self) -> None:
        self._busy(True)
        title, body = self.title.get_text(), self._body()

        def work():
            try:
                return self.client.save(self.app.slug, rating=self.rating, title=title, body=body,
                                        version=self.version, installed_on_luma=self.installed)
            except ReviewsError as error:
                raise ProviderError(str(error), hint=str(error)) from error

        run_async(work, self._done)

    def _done(self, result) -> None:
        self._busy(False)
        if not result.ok:
            self._fail(result.error.hint or "The review was not posted.")
            return
        self.close()
        self.on_saved()

    def _confirm_delete(self) -> None:
        dialog = Adw.AlertDialog(heading="Delete your review?",
                                 body="Your rating and review of this app are removed from Luma.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_dialog, name):
            if name != "delete":
                return

            def work():
                try:
                    self.client.delete(self.app.slug)
                except ReviewsError as error:
                    raise ProviderError(str(error), hint=str(error)) from error
            run_async(work, self._done)
        dialog.connect("response", response)
        dialog.present(self)
