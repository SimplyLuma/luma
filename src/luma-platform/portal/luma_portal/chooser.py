# SPDX-License-Identifier: Apache-2.0
"""Open with — the application chooser.

A chooser has two questions to answer: which application, and once or always.
GNOME's answers neither — a flat alphabetical list with no default marked and
no notion of once. Windows answers both but makes Always the dominant button,
which walks people into setting defaults they never meant to set.

Here the once/always choice is two footer buttons and the low-commitment one is
the primary: Open Once is the default action, the Enter key and the right-hand
button. Always Open is its quieter neighbour. That ordering is the point of the
design and is not a detail to tidy up later.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import apps  # noqa: E402

RESPONSE_CANCEL = 1
RESPONSE_ONCE = 2
RESPONSE_ALWAYS = 3

DIALOG_WIDTH = 600
#: The spec asks for max-height 82vh. A natural height instead collapsed the
#: body to a single clipped row, because the list is the only thing that wants
#: to grow and nothing was telling the window it could.
DIALOG_HEIGHT_FRACTION = 0.82
DIALOG_HEIGHT_PREFERRED = 560
SEARCH_WIDTH = 178
FILE_TILE = 34
APP_TILE = 26
SEARCH_FOCUS_DELAY_MS = 50


def _tile(icon, size: int, css: str) -> Gtk.Widget:
    image = Gtk.Image(pixel_size=size)
    image.add_css_class(css)
    if icon is not None:
        image.set_from_gicon(icon)
    else:
        image.set_from_icon_name("application-x-executable")
    return image


class AppRow(Gtk.ListBoxRow):
    """[tile] name / one-line description ............... [DEFAULT]"""

    def __init__(self, application: apps.Application) -> None:
        super().__init__()
        self.application = application
        self.add_css_class("ow-row")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
        box.append(_tile(application.icon, APP_TILE, "ow-app-tile"))

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        name = Gtk.Label(label=application.name, xalign=0,
                         ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("ow-name")
        column.append(name)
        if application.description:
            # One line, always. A description that wraps turns a list of
            # applications into a page of prose.
            detail = Gtk.Label(label=application.description, xalign=0,
                               ellipsize=Pango.EllipsizeMode.END, lines=1)
            detail.add_css_class("ow-detail")
            column.append(detail)
        box.append(column)

        if application.is_default:
            chip = Gtk.Label(label="DEFAULT", valign=Gtk.Align.CENTER)
            chip.add_css_class("ow-chip")
            box.append(chip)

        self.set_child(box)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [application.accessible_name])

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        hay = f"{self.application.name} {self.application.description}".casefold()
        return needle.casefold() in hay


class Section(Gtk.Box):
    """A caption and its rows. Both disappear together when nothing matches."""

    def __init__(self, caption: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._caption = Gtk.Label(label=caption, xalign=0)
        self._caption.add_css_class("ow-caption")
        self.append(self._caption)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("ow-list")
        self.append(self.list)
        self.rows: list[AppRow] = []

    def populate(self, applications) -> None:
        for application in applications:
            row = AppRow(application)
            self.list.append(row)
            self.rows.append(row)
        self.set_visible(bool(self.rows))

    def filter(self, needle: str) -> int:
        shown = 0
        for row in self.rows:
            visible = row.matches(needle)
            row.set_visible(visible)
            shown += int(visible)
        # Never an empty heading: the caption is part of the section, not a
        # label that outlives it.
        self.set_visible(shown > 0)
        return shown


class OpenWithDialog(Adw.Window):
    """The dialog. Head fixed, body scrolling, foot fixed."""

    def __init__(self, *, application=None, file_name: str, content_type: str,
                 file_size: str = "", extra_ids=(), on_done=None) -> None:
        super().__init__(modal=True, title="Open with")
        if application is not None:
            self.set_application(application)
        self.add_css_class("ow-dialog")
        self.set_default_size(DIALOG_WIDTH, DIALOG_HEIGHT_PREFERRED)
        self._content_type = content_type
        self._on_done = on_done
        self._answered = False

        recommended, others, default_id = apps.for_content_type(
            content_type, extra_ids=extra_ids)
        self._default_id = default_id

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._build_head(file_name, content_type, file_size))
        root.append(self._build_body(recommended, others))
        root.append(self._build_foot())
        self.set_content(root)

        self._install_keys()
        self._select_initial(recommended, others)
        self.connect("map", lambda *_a: self._fit_to_monitor())
        GLib.timeout_add(SEARCH_FOCUS_DELAY_MS, self._focus_search)

    def _fit_to_monitor(self) -> None:
        """Never taller than most of the screen, never shorter than useful."""
        surface = self.get_surface()
        display = self.get_display()
        monitor = display.get_monitor_at_surface(surface) if surface is not None else None
        if monitor is None:
            monitors = display.get_monitors()
            monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor is None:
            return
        available = int(monitor.get_geometry().height * DIALOG_HEIGHT_FRACTION)
        _minimum, natural, _b, _e = self.measure(Gtk.Orientation.VERTICAL, DIALOG_WIDTH)
        self.set_default_size(DIALOG_WIDTH, max(320, min(available, max(natural, DIALOG_HEIGHT_PREFERRED))))

    # -- Bands ------------------------------------------------------------

    def _build_head(self, file_name, content_type, file_size) -> Gtk.Widget:
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        head.add_css_class("ow-head")
        icon = Gio.content_type_get_icon(content_type) if content_type else None
        head.append(_tile(icon, FILE_TILE, "ow-file-tile"))

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label=file_name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("ow-file-name")
        column.append(name)
        # The file gets a real identity here rather than a sentence in a title:
        # what kind of thing it is, then how big it is.
        kind = Gio.content_type_get_description(content_type) if content_type else ""
        detail = " · ".join(part for part in (kind, file_size) if part)
        if detail:
            label = Gtk.Label(label=detail, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            label.add_css_class("ow-file-kind")
            column.append(label)
        head.append(column)

        head.append(Gtk.Box(hexpand=True))
        self.search = Gtk.SearchEntry(placeholder_text="Search applications",
                                      valign=Gtk.Align.CENTER)
        self.search.add_css_class("ow-search")
        self.search.set_size_request(SEARCH_WIDTH, 32)
        self.search.connect("search-changed", lambda *_a: self._filter())
        self.search.connect("activate", lambda *_a: self._finish(RESPONSE_ONCE))
        head.append(self.search)

        close = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER)
        close.add_css_class("ow-close")
        close.set_tooltip_text("Cancel")
        close.connect("clicked", lambda *_a: self._finish(RESPONSE_CANCEL))
        head.append(close)
        return head

    def _build_body(self, recommended, others) -> Gtk.Widget:
        self.recommended = Section("RECOMMENDED FOR THIS KIND")
        self.recommended.populate(recommended)
        self.others = Section("ALL APPLICATIONS")
        self.others.populate(others)
        for section in (self.recommended, self.others):
            section.list.connect("row-selected", self._row_selected)
            section.list.connect("row-activated", lambda _l, _r: self._finish(RESPONSE_ONCE))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("ow-body")
        body.append(self.recommended)
        body.append(self.others)
        self.empty = Gtk.Label(label="")
        self.empty.add_css_class("ow-empty")
        self.empty.set_visible(False)
        body.append(self.empty)

        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=body)
        return self.scroller

    def _build_foot(self) -> Gtk.Widget:
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        foot.add_css_class("ow-foot")
        browse = Gtk.Button(label="Browse…", has_frame=False)
        browse.add_css_class("ow-browse")
        browse.connect("clicked", lambda *_a: self._browse())
        foot.append(browse)
        foot.append(Gtk.Box(hexpand=True))

        self.always = Gtk.Button(label="Always Open")
        self.always.add_css_class("ow-secondary")
        self.always.connect("clicked", lambda *_a: self._finish(RESPONSE_ALWAYS))
        foot.append(self.always)

        self.once = Gtk.Button(label="Open Once")
        self.once.add_css_class("ow-primary")
        self.once.connect("clicked", lambda *_a: self._finish(RESPONSE_ONCE))
        foot.append(self.once)
        # There is no Cancel button: the ✕, Escape and the scrim all cancel,
        # and a third footer button would compete with the two that decide.
        self.set_default_widget(self.once)
        return foot

    # -- Selection --------------------------------------------------------

    def _select_initial(self, recommended, others) -> None:
        for section in (self.recommended, self.others):
            for row in section.rows:
                if row.application.is_default:
                    section.list.select_row(row)
                    return
        # No default for this kind: nothing is preselected in the sense of
        # being blessed, but the first recommended row is where the keyboard
        # should start.
        if self.recommended.rows:
            self.recommended.list.select_row(self.recommended.rows[0])

    def _row_selected(self, listbox, row) -> None:
        if row is None:
            return
        for section in (self.recommended, self.others):
            if section.list is not listbox:
                section.list.unselect_all()
        self._update_actions()

    def selected(self):
        for section in (self.recommended, self.others):
            row = section.list.get_selected_row()
            if row is not None:
                return row.application
        return None

    def _update_actions(self) -> None:
        chosen = self.selected() is not None
        self.once.set_sensitive(chosen)
        # Always is refused without a kind to attach the choice to: there would
        # be nothing to write the default against.
        self.always.set_sensitive(chosen and bool(self._content_type))

    # -- Search -----------------------------------------------------------

    def _focus_search(self) -> bool:
        self.search.grab_focus()
        return GLib.SOURCE_REMOVE

    def _filter(self) -> None:
        needle = self.search.get_text().strip()
        shown = sum(section.filter(needle) for section in
                    (self.recommended, self.others))
        self.empty.set_label(f'No applications match "{needle}".')
        self.empty.set_visible(shown == 0)
        if shown and self.selected() is None:
            for section in (self.recommended, self.others):
                for row in section.rows:
                    if row.get_visible():
                        section.list.select_row(row)
                        return

    # -- Finishing --------------------------------------------------------

    def _install_keys(self) -> None:
        keys = Gtk.EventControllerKey()

        def pressed(_c, keyval, _code, _state):
            from gi.repository import Gdk
            if keyval == Gdk.KEY_Escape:
                self._finish(RESPONSE_CANCEL)
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self._finish(RESPONSE_ONCE)
                return True
            return False

        keys.connect("key-pressed", pressed)
        self.add_controller(keys)

    def _browse(self) -> None:
        """The escape hatch that keeps the list short.

        Without it the only way to reach something unlisted is to list every
        binary on the system, which is not a chooser.
        """
        dialog = Gtk.FileDialog(title="Choose an application")
        dialog.open(self, None, self._browsed)

    def _browsed(self, dialog, result) -> None:
        try:
            chosen = dialog.open_finish(result)
        except GLib.Error:
            return
        if chosen is None:
            return
        info = apps.from_commandline(chosen.get_path() or "")
        if info is None:
            return
        self._deliver(RESPONSE_ONCE, info.get_id() or chosen.get_path())

    def _finish(self, response: int) -> None:
        if response == RESPONSE_CANCEL:
            self._deliver(RESPONSE_CANCEL, "")
            return
        application = self.selected()
        if application is None:
            return
        if response == RESPONSE_ALWAYS:
            apps.set_default(application.desktop_id, self._content_type)
        self._deliver(response, application.desktop_id)

    def _deliver(self, response: int, desktop_id: str) -> None:
        if self._answered:
            return
        self._answered = True
        if self._on_done is not None:
            self._on_done(response, desktop_id)
        self.close()
