# SPDX-License-Identifier: Apache-2.0
"""The library room: sidebar, Continue reading, the shelf, the status bar.

Every count and every status line is derived from the book records on each
render. Books in a place that is out of reach are dimmed and never hidden:
hiding them teaches a person that their book is gone.
"""
from __future__ import annotations

import threading
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, GObject, Gtk, Pango

from luma_appkit import ActionCenter, BarAction, BarMenu, BarSearch, EmptyState, ToastHost, Card, Island, ModeSwitch, NavigationSidebar, ProgressLine, ScrollView, SidebarFoot, SidebarRow, TextButton, apply_type
from luma_appkit import icons
from luma_appkit import lumaui_tokens as tokens

from luma_appkit.structure_adapt import WidthWatch
from luma_appkit.action_bubble import MenuItem

from . import sentences
from .covers import AvailabilityGlyph, book_cover
from .fixture import FixtureLibrary
from .library_data import counts, matches, on_shelf, ordered
from .position import Locator, Stats
from .store import Book, Library


def _label(text: str = "", *classes: str, xalign: float = 0, **kwargs) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign, **kwargs)
    for name in classes:
        label.add_css_class(name)
    return label


def _icon(name: str, size: int = 14) -> Gtk.Image:
    return icons.image(name, pixel_size=size)


class BookFacts:
    """What the library says about one book, derived from its record."""

    def __init__(self, book: Book, library: Library, locator: Locator, *, sources=None) -> None:
        self.book = book
        self.sample = library.sample(book.id) if isinstance(library, FixtureLibrary) else None
        self.stats = Stats.from_json(book.stats)
        self.place = (_FixturePlace(self.sample) if self.sample and self.sample["p"] else
                      locator.place(book.path, self.stats, book.position) if book.path and book.position else None)
        source = next((s for s in (sources if sources is not None else library.sources()) if s.id == book.source), None)
        self.source_name = source.name if source else ""
        # A disc is this device's; a ring is a server's, read by streaming;
        # a slashed ring is a server out of reach.
        if self.sample is not None:
            self.where = "here"
        elif book.source == "device" and book.on_device:
            self.where = "here"
        elif source is not None and source.reachable:
            self.where = "stream"
        elif book.on_device and book.source == "device":
            self.where = "here"
        else:
            self.where = "gone"

    @property
    def in_progress(self) -> bool:
        # Reading now is the shelf. A book on it with no position yet (opened
        # before this version saved one for a first page without text) is
        # reading at its start: never New, and counted, shown and flagged the
        # same way everywhere.
        return self.book.shelf == "reading" and (self.book.on_device or self.sample is not None)

    @property
    def local(self) -> bool:
        return self.sample is not None or self.book.source == "device"

    @property
    def percent(self) -> int:
        return self.place.percent if self.place else 0

    def status(self) -> str:
        if self.sample is not None and self.sample.get("at"):
            return f"{self.sample['at']} · {self.percent}%"
        if not self.local and not (self.book.on_device and self.book.position):
            return f"On {self.source_name}" if self.where == "stream" else f"{self.source_name or 'Its library'} is offline"
        if self.book.shelf == "finished":
            return "Finished"
        if self.place is not None:
            return self.place.status()
        if self.book.shelf == "reading":
            return "Not started"
        return "Want to read" if self.book.shelf == "want" else "New"

    def short_status(self) -> str:
        if not self.local and not (self.book.on_device and self.book.position):
            return self.status()
        if self.book.shelf == "finished":
            return "Finished"
        if self.in_progress:
            return f"{self.percent}%"
        return "Not started" if self.book.shelf == "want" else "New"

    def spoken(self) -> str:
        return f"{self.book.short_title} by {self.book.author}, {self.status()}"

    def listen_time(self) -> str:
        if self.sample is not None:
            return self.sample.get("left", "").removeprefix("About ")
        words = self.stats.total_words - (self.place.words_before if self.place else 0)
        return sentences.hours_and_minutes(words, sentences.LISTENING_WPM)


class _FixturePlace:
    """Only the progress facts v70 supplies; no EPUB or user file is opened."""

    def __init__(self, sample: dict) -> None:
        self.fraction = float(sample["p"])
        self.percent = round(self.fraction * 100)

    def status(self) -> str:
        return f"{self.percent}%"

    def minutes_left_in_chapter(self) -> int:
        return 0


class LibraryView(Gtk.Box):
    """Emits `open-book(id, listen)`."""

    __gsignals__ = {
        "open-book": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        "add-books": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "add-library": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "keep-book": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, library: Library, locator: Locator) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.library = library
        self.locator = locator
        self.phone = False
        self.view = "reading"
        self.sort = "recent"
        self.query = ""
        self._render_source = 0
        self._render_generation = 0
        self.add_css_class("leaf-library")

        # The frame sidebar, its foot, and the island are kit parts.
        self.sidebar = NavigationSidebar(variant="destinations", width="narrow")
        self.sidebar.set_name("lf-sidebar")
        self.sidebar.list.connect("row-activated", self._row_activated)
        # TODO(kit-request leaf-07-sidebar-foot-search-role): expose the searchbox in conform.
        self.foot = SidebarFoot(search="Search your books", on_search=self._search_changed,
                                add=("Add books", "plus", lambda: self.emit("add-books")))
        self.foot.set_name("lf-foot")
        self.sidebar.append_footer(self.foot)
        self.search = self.foot.entry
        self.append(self.sidebar)

        # the shelf island
        island = Island()
        island.set_hexpand(True)
        island.set_name("lf-island")
        island.add_css_class("leaf-shelf-island")
        scroller = ScrollView()
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.add_css_class("leaf-shelf-content")
        self.content.set_margin_top(36)
        self.content.set_margin_start(40)
        self.content.set_margin_end(40)
        self.content.set_margin_bottom(110)
        scroller.set_child(self.content)
        self.shelf_scroll = scroller
        island.append(scroller)

        # Keep the inactive layout unmapped. Gtk.Stack still measured the tall
        # desktop grid at phone width, leaving a 300 px hole after the cover.
        self.hero_layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        self.hero_desktop = Adw_bin()
        self.hero_phone = Adw_bin()
        self.hero_phone.set_visible(False)
        self.hero_layout.append(self.hero_desktop)
        self.hero_layout.append(self.hero_phone)
        self.content.append(self.hero_layout)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.add_css_class("leaf-shelf-head")
        self.shelf_title = _label("Your books", "leaf-shelf-title")
        self.shelf_title.set_wrap(True)
        self.shelf_title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.shelf_title_slot = Gtk.Box()
        self.shelf_title_slot.append(self.shelf_title)
        self.shelf_count = _label("", "leaf-shelf-count", hexpand=True)
        head.append(self.shelf_title_slot)
        head.append(self.shelf_count)
        self.sort_switch = ModeSwitch((("recent", "Recent"), ("title", "Title"),
                                       ("author", "Author")), current=self.sort,
                                      on_change=self._sort_changed, label="Sort")
        self.sort_switch.set_hexpand(False)
        self.sort_switch.set_tooltip_text("Sort")
        head.append(self.sort_switch)
        self.content.append(head)

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                column_spacing=26, row_spacing=34, valign=Gtk.Align.START)
        self.grid.add_css_class("leaf-grid")
        self.grid.set_activate_on_single_click(True)
        self.content.append(self.grid)
        self.highlights_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.START)
        self.highlights_box.set_name("lf-highlights")
        self.content.append(self.highlights_box)
        self.empty = EmptyState("No books yet", "Add a book to start reading.", "lumaui-book-open-symbolic",
                                primary=("Add books", lambda: self.emit("add-books")))
        island.append(self.empty)

        self.bar_host=ToastHost(island)
        self.append(self.bar_host)
        self.action_center=ActionCenter().attach(self.bar_host)
        self.action_center.set_name("lf-action-center")
        self._width_watch=WidthWatch(self,self._presentation_width)

        self._grid_width = 0
        self._has_rendered = False
        tick = self.grid.add_tick_callback(self._track_width)
        self._tick = tick
        library.subscribe(lambda *_: self.queue_render())
        self.render()

    def _presentation_width(self,width):
        phone=0 < width < 560
        if phone==self.phone:return
        self.phone=phone
        self.sort_switch.set_visible(not phone and self.view!='hl')
        self._grid_width=0
        self._refresh_bar()
        self.queue_render()

    def _shelf_menu(self):
        rows=[MenuItem(title,on_activate=lambda k=key:self.set_view(k)) for key,title in
              [('reading','Reading now'),('want','Want to read'),('finished','Finished'),('all','All books'),('hl','Highlights and notes')]]
        rows += ['Collections']+[MenuItem(c.name,on_activate=lambda k=c.id:self.set_view('col:'+k)) for c in self.library.collections()]
        rows += ['Sort by']+[MenuItem(title,on_activate=lambda k=key:self._sort_changed(k)) for key,title in [('recent','Recent'),('title','Title'),('author','Author')]]
        return rows

    def _refresh_bar(self):
        if not hasattr(self,'action_center'):return
        if not self.phone:
            self.action_center.hide_bar();return
        title='Reading now' if self.view=='reading' else self._view_name()
        self.action_center.show_bar([BarMenu(title,self._shelf_menu(),icon='book-open'),
            BarSearch('Search',label='Search your books',text=self.query,collapsed=True,on_change=self._search_changed),
            BarAction('plus',tooltip='Add books',on_activate=lambda:self.emit('add-books'))],fill=True)

    # layout: repeat(auto-fill, minmax(136px, 1fr)) with 26px between columns
    def _track_width(self, widget, _clock) -> bool:
        width = widget.get_width()
        if width and width != self._grid_width:
            self._grid_width = width
            columns = 3 if self.phone else max(1, (width + 26) // (136 + 26))
            self.grid.set_column_spacing(12 if self.phone else 26)
            self.grid.set_row_spacing(16 if self.phone else 34)
            self.grid.set_min_children_per_line(columns)
            self.grid.set_max_children_per_line(columns)
            # The extra hero rail needs more room than the book shelf. At
            # ordinary desktop widths the same books remain in the shelf.
            if getattr(self, "_also", None) is not None:
                self._also.set_visible(width >= 1320)
        return GLib.SOURCE_CONTINUE

    def queue_render(self) -> None:
        # A hidden library is not rebuilt on every page turn; it renders when shown.
        if not self.get_mapped():
            self._dirty = True
            return
        if not self._render_source:
            self._render_source = GLib.idle_add(self._render_idle)

    def do_map(self) -> None:
        Gtk.Box.do_map(self)
        if getattr(self, "_dirty", False):
            self._dirty = False
            self.queue_render()

    def _render_idle(self) -> bool:
        self._render_source = 0
        self.render()
        return GLib.SOURCE_REMOVE

    # interaction
    def _search_changed(self, text: str) -> None:
        self.query = text.strip().casefold()
        self.render(sidebar=False)

    def _sort_changed(self, key: str) -> None:
        self.sort = key
        self.render(sidebar=False)

    def _row_activated(self, _list, row) -> None:
        view = getattr(row, "leaf_view", None)
        if view == "new-collection":
            self._new_collection()
            return
        if view == "add-library":
            self.emit("add-library")
            return
        if view:
            self.set_view(view)

    def set_view(self, view: str) -> None:
        self.view = view
        self._refresh_bar()
        self.render()

    def _new_collection(self) -> None:
        dialog = Gtk.Window(transient_for=self.get_root(), modal=True, title="New Collection", resizable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18,
                      margin_start=18, margin_end=18)
        entry = Gtk.Entry(placeholder_text="Collection name", activates_default=True)
        box.append(_label("Name the collection", "heading"))
        box.append(entry)
        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        create = Gtk.Button(label="Create")
        create.add_css_class("suggested-action")
        actions.append(cancel)
        actions.append(create)
        box.append(actions)
        dialog.set_child(box)
        dialog.set_default_widget(create)

        def done(*_):
            name = entry.get_text().strip()
            dialog.close()
            if name:
                collection = self.library.add_collection(name)
                self.set_view(f"col:{collection.id}")

        cancel.connect("clicked", lambda *_: dialog.close())
        create.connect("clicked", done)
        entry.connect("activate", done)
        dialog.present()

    # rendering
    def _facts(self) -> list[BookFacts]:
        return [BookFacts(book, self.library, self.locator) for book in self.library.books()]

    def _matches(self, facts: BookFacts) -> bool:
        book, view = facts.book, self.view
        if view in ("reading", "want", "finished", "new") and not on_shelf(book, view):
            return False
        if view.startswith("col:") and view[4:] not in book.collections:
            return False
        if view == "where:device" and not facts.local:
            return False
        if view.startswith("where:") and view != "where:device" and book.source != view[6:]:
            return False
        # The full stored title and both forms of each name are searched.
        if not matches(book, self.query):
            return False
        return True

    def _view_name(self) -> str:
        names = {"all": "All books", "reading": "Your books", "want": "Want to read", "new": "New",
                 "finished": "Finished", "hl": "Highlights and notes", "where:device": "This device"}
        if self.view in names:
            return names[self.view]
        if self.view.startswith("col:"):
            return next((c.name for c in self.library.collections() if c.id == self.view[4:]), "Collection")
        if self.view.startswith("where:"):
            return next((s.name for s in self.library.sources() if s.id == self.view[6:]), "Library")
        return "All books"

    def render(self, *, sidebar: bool = True) -> None:
        self._render_generation += 1
        generation = self._render_generation
        if isinstance(self.library, FixtureLibrary):
            self._render_facts(self._facts(), sidebar=sidebar)
            return
        # SQLite rows are quick to snapshot on the main loop. EPUB position
        # lookup opens archives, so work on an isolated Locator and discard
        # results made obsolete by a search, sort or library change.
        books, sources = self.library.books(), self.library.sources()
        if not self._has_rendered:
            self.empty.set_text("Loading books…", "")
            self.empty.actions.set_visible(False)
            self.empty.set_visible(True)
            self.shelf_scroll.set_visible(False)

        def work() -> None:
            locator = Locator()
            facts = [BookFacts(book, self.library, locator, sources=sources) for book in books]
            GLib.idle_add(self._render_ready, generation, facts, sidebar)

        threading.Thread(target=work, daemon=True, name="leaf-library-facts").start()

    def _render_ready(self, generation: int, everything: list[BookFacts], sidebar: bool) -> bool:
        if generation == self._render_generation:
            self._render_facts(everything, sidebar=sidebar)
        return GLib.SOURCE_REMOVE

    def _render_facts(self, everything: list[BookFacts], *, sidebar: bool) -> None:
        self._has_rendered = True
        if sidebar:
            self._render_sidebar(everything)
        name = self._view_name()
        self.shelf_title.set_label(name)
        shown = [] if self.view == "hl" else [facts for facts in everything if self._matches(facts)]
        if self.view == "reading" and not self.query:
            shown = list(everything)  # v70: the hero sits above the complete "Your books" shelf
        order = {book.id: index for index, book in enumerate(ordered((fact.book for fact in shown), self.sort))}
        shown.sort(key=lambda fact: order[fact.book.id])
        highlights = self.library.all_highlights() if self.view == "hl" else []
        self.shelf_count.set_label(f"{len(highlights)} saved" if self.view == "hl" else
                                   f"{len(shown)} book" + ("" if len(shown) == 1 else "s"))
        self.sort_switch.set_visible(self.view != "hl" and not self.phone)
        self.grid.set_visible(self.view != "hl")
        self.highlights_box.set_visible(self.view == "hl")
        self._render_hero(everything)
        if self.view == "hl":
            self._render_highlights(highlights)
            self.empty.set_text("No highlights yet", "Highlight words while you read. They’ll collect here.")
            self.empty.actions.set_visible(False)
            empty = not highlights
        else:
            self._render_grid(shown)
            self.empty.set_text("No books found" if self.query else "No books yet",
                                f"No books match “{self.search.get_text().strip()}”." if self.query else
                                "Add a book to start reading.")
            self.empty.actions.set_visible(not self.query)
            empty = not shown
        self.empty.set_visible(empty)
        self.shelf_scroll.set_visible(not empty)

    def _render_highlights(self, highlights) -> None:
        while child := self.highlights_box.get_first_child():
            self.highlights_box.remove(child)
        if not highlights:
            empty = apply_type(_label("Select words while you read to highlight them. They'll collect here."),
                               "body", muted=True)
            empty.set_margin_top(12)
            self.highlights_box.append(empty)
            return
        for highlight in highlights:
            book = self.library.book(highlight.book_id)
            if book is None:
                continue
            card = Card()
            card.add_css_class("leaf-highlight-card")
            quote = apply_type(Gtk.Label(label=f"“{highlight.text}”", xalign=0, wrap=True), "body")
            card.append(quote)
            if highlight.note:
                card.append(apply_type(Gtk.Label(label=highlight.note, xalign=0, wrap=True), "caption"))
            card.append(apply_type(Gtk.Label(label=book.short_title, xalign=0), "caption"))
            button = Gtk.Button(child=card)
            button.update_property([Gtk.AccessibleProperty.LABEL], [f"Open {book.short_title}: {highlight.text}"])
            button.connect("clicked", lambda _button, book_id=book.id: self.emit("open-book", book_id, False))
            self.highlights_box.append(button)

    def _render_sidebar(self, everything: list[BookFacts]) -> None:
        self.sidebar.clear()
        tally = counts((fact.book for fact in everything), len(self.library.all_highlights()))
        self.sidebar.append_section("Library")
        rows = [
            ("reading", "Reading now", "book-open"),
            ("want", "Want to read", "bookmark"),
            ("finished", "Finished", "check"),
            ("all", "All books", "library-big"),
            ("hl", "Highlights and notes", "pencil"),
        ]
        selected = None
        for view, title, icon in rows:
            row = SidebarRow(title, lead=icons.image(icon, pixel_size=16), trail=tally[view] or None)
            row.leaf_view = view
            self.sidebar.append_row(row)
            if view == self.view:
                selected = row
        self.sidebar.append_section("Collections")
        fixture_counts = ({entry["name"]: entry["count"] for entry in self.library.document.get("collections", ())}
                          if isinstance(self.library, FixtureLibrary) else {})
        collections = self.library.collections()
        if isinstance(self.library, FixtureLibrary):
            rank = {entry["name"]: index for index, entry in enumerate(self.library.document.get("collections", ())) }
            collections.sort(key=lambda item: rank.get(item.name, len(rank)))
        for collection in collections:
            members = fixture_counts.get(collection.name, len(collection.books))
            row = SidebarRow(collection.name, lead=icons.image("layers", pixel_size=16), trail=members or None)
            row.leaf_view = f"col:{collection.id}"
            self.sidebar.append_row(row)
            if row.leaf_view == self.view:
                selected = row
        add = SidebarRow("New collection", lead=icons.image("plus", pixel_size=16))
        add.leaf_view = "new-collection"
        self.sidebar.append_row(add)
        if selected is not None:
            self.sidebar.list.select_row(selected)

    def _render_hero(self, everything: list[BookFacts]) -> None:
        self._also = None
        self.hero_desktop.set_child(None)
        self.hero_phone.set_child(None)
        if self.view != "reading" or self.query:
            return
        progress = sorted((f for f in everything if f.in_progress), key=lambda f: -(f.book.last_read or 0))
        if not progress:
            return
        facts, others = progress[0], progress[1:4]
        book = facts.book
        hero = Gtk.Grid(column_spacing=40)
        hero.add_css_class("leaf-hero")
        hero.set_name("lf-hero")

        cover_button = Gtk.Button()
        cover_button.add_css_class("leaf-hero-cover")
        cover_button.set_child(book_cover(title=book.short_title, author=book.author, image=book.cover, width=210,
                                     edition=facts.sample))
        cover_button.set_size_request(210, 315)
        cover_button.set_valign(Gtk.Align.START)
        cover_button.update_property([Gtk.AccessibleProperty.LABEL], [f"Continue {book.short_title}"])
        cover_button.set_tooltip_text(f"Continue {book.short_title}")
        cover_button.connect("clicked", lambda *_: self.emit("open-book", book.id, False))
        hero.attach(cover_button, 0, 0, 1, 1)

        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        middle.append(_label("CONTINUE READING", "leaf-eyebrow"))
        title = _label(book.short_title, "leaf-hero-title", wrap=True)
        if book.short_title != book.title:
            title.set_tooltip_text(book.title)
        title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.set_max_width_chars(1)
        middle.append(title)
        middle.append(_label(book.author, "leaf-hero-by"))
        if isinstance(self.library, FixtureLibrary):
            quote = _label(f"“{self.library.document.get('hero_quote', '')}”", "leaf-hero-quote", wrap=True)
            quote.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            middle.append(quote)
        place = facts.place
        chapter = facts.sample.get("at", "") if facts.sample else (place.chapter_label if place else "")
        chapter = chapter.split(", ")[-1]
        remaining = facts.listen_time().replace(" hours", " h").replace(" hour", " h")
        meta = " · ".join(part for part in (chapter, f"{facts.percent}%", remaining) if part)
        middle.append(_label(meta, "leaf-hero-meta", ellipsize=Pango.EllipsizeMode.END))
        bar = ProgressLine(facts.place.fraction if facts.place else 0, size="tile", tone="neutral",
                           label=f"{book.short_title} progress")
        bar.set_hexpand(True)
        bar.add_css_class("leaf-hero-progress")
        middle.append(bar)
        actions = Gtk.Box(spacing=8)
        read = Gtk.Button()
        read.set_child(_button_content("book-open", "Continue"))
        read.add_css_class("luma-button")
        read.add_css_class("primary")
        read.connect("clicked", lambda *_: self.emit("open-book", book.id, False))
        listen = Gtk.Button()
        listen.set_child(_button_content("headphones", "Listen"))
        listen.add_css_class("luma-button")
        listen.connect("clicked", lambda *_: self.emit("open-book", book.id, True))
        actions.append(read)
        actions.append(listen)
        middle.append(actions)
        hero.attach(middle, 1, 0, 1, 1)

        if others:
            # The right rail starts level with the featured cover. Its books
            # sit at the foot of that rail, with the divider spanning it.
            also = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.START)
            also.add_css_class("leaf-also")
            also.set_size_request(250, 315)
            also.set_hexpand(False)
            also.append(Gtk.Box(vexpand=True))
            also.append(_label("ALSO READING", "leaf-eyebrow"))
            for other in others:
                row = Gtk.Button()
                row.add_css_class("leaf-also-row")
                row.set_size_request(221, 85)
                line = Gtk.Grid(column_spacing=11, row_spacing=1)
                small = book_cover(title=other.book.short_title, author=other.book.author, image=other.book.cover, width=46,
                              edition=other.sample)
                small.set_size_request(46, 69)
                line.attach(small, 0, 0, 1, 2)
                small_title = _label(other.book.short_title, "leaf-also-title", ellipsize=Pango.EllipsizeMode.END, hexpand=True)
                small_title.set_width_chars(1)
                small_title.set_max_width_chars(18)
                line.attach(small_title, 1, 0, 1, 1)
                line.attach(_label(other.status(), "leaf-also-status"), 1, 1, 1, 1)
                row.set_child(line)
                row.update_property([Gtk.AccessibleProperty.LABEL], [other.spoken()])
                row.connect("clicked", lambda _b, i=other.book.id: self.emit("open-book", i, False))
                also.append(row)
            hero.attach(also, 2, 0, 1, 1)
            self._also = also
            also.set_visible(self._grid_width >= 1320)
        self.hero_desktop.set_child(hero)
        phone = Gtk.Grid(column_spacing=16,row_spacing=16)
        phone.set_vexpand(False)
        phone.add_css_class("leaf-hero")
        phone.add_css_class("leaf-phone-hero")
        phone.set_name("lf-hero")
        phone_cover = Gtk.Button()
        phone_cover.add_css_class("leaf-hero-cover")
        phone_cover.set_child(book_cover(title=book.short_title, author=book.author, image=book.cover,
                                         width=110, edition=facts.sample))
        phone_cover.set_size_request(110, 165)
        phone_cover.set_halign(Gtk.Align.START)
        phone_cover.set_valign(Gtk.Align.START)
        phone_cover.set_vexpand(False)
        phone_cover.update_property([Gtk.AccessibleProperty.LABEL], [f"Continue {book.short_title}"])
        phone_cover.set_tooltip_text(f"Continue {book.short_title}")
        phone_cover.connect("clicked", lambda *_: self.emit("open-book", book.id, False))
        # The painted cover floats over a fixed-size layout slot. Measuring
        # its tile at the full phone column otherwise reserves 543 px for a
        # 240 px picture. Gtk.Overlay excludes overlays from its size request.
        cover_slot = Gtk.Overlay(halign=Gtk.Align.START, valign=Gtk.Align.START)
        cover_placeholder = Gtk.Box()
        cover_placeholder.set_size_request(110, 165)
        cover_slot.set_child(cover_placeholder)
        cover_slot.add_overlay(phone_cover)
        cover_slot.set_measure_overlay(phone_cover, False)
        phone.attach(cover_slot,0,0,1,1)
        phone_middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        phone_middle.set_vexpand(False)
        phone_middle.set_hexpand(True)
        phone_middle.add_css_class("leaf-phone-hero-middle")
        phone_middle.append(_label("CONTINUE READING", "leaf-eyebrow", wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR))
        phone_middle.append(_label(book.short_title, "leaf-hero-title", "leaf-phone-hero-title", wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=1))
        phone_middle.append(_label(book.author, "leaf-hero-by", wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=1))
        phone_middle.append(_label(meta, "leaf-hero-meta", ellipsize=Pango.EllipsizeMode.END))
        phone_bar = ProgressLine(facts.place.fraction if facts.place else 0, size="tile", tone="neutral",
                                 label=f"{book.short_title} progress")
        phone_bar.set_hexpand(True)
        phone_bar.add_css_class("leaf-hero-progress")
        phone_middle.append(phone_bar)
        phone_actions = Gtk.Box(spacing=8)
        phone_read = TextButton("Continue", icon="book-open", style="key",
                                on_click=lambda: self.emit("open-book", book.id, False))
        phone_listen = TextButton("Listen", icon="headphones", style="raised",
                                  on_click=lambda: self.emit("open-book", book.id, True))
        phone_actions.append(phone_read)
        phone_actions.append(phone_listen)
        phone_actions.set_homogeneous(True)
        # Keep both labeled actions available even at 360px. The reference's
        # narrow right column cannot hold them without pushing the shelf out.
        phone.attach(phone_actions,0,1,2,1)
        phone.attach(phone_middle,1,0,1,1)
        self.hero_phone.set_child(phone)

    def _render_grid(self, shown: list[BookFacts]) -> None:
        self.grid.remove_all()
        for facts in shown:
            self.grid.append(self._tile(facts))

    def _tile(self, facts: BookFacts) -> Gtk.Widget:
        book = facts.book
        button = Gtk.Button()
        button.add_css_class("leaf-book")
        if facts.where == "gone" or (not book.on_device and facts.where != "stream"):
            button.add_css_class("gone")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cover = book_cover(title=book.short_title, author=book.author, image=book.cover, width=150, flexible=True,
                      edition=facts.sample)
        cover.set_hexpand(True)
        cover.set_valign(Gtk.Align.START)
        cover.set_size_request(-1, -1)
        # v70 keeps 13 px between the painted cover and the title's ink.
        # The label itself contributes roughly 1 px above its ink.
        cover.set_margin_bottom(12)
        box.append(cover)
        title = _label(book.short_title, "leaf-book-title", wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END)
        title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.set_max_width_chars(1)
        box.append(title)
        box.append(_label(book.author, "leaf-book-by", ellipsize=Pango.EllipsizeMode.END))
        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if facts.in_progress else Gtk.Orientation.HORIZONTAL,
                       spacing=7)
        meta.add_css_class("leaf-book-meta")
        if not facts.local and not facts.in_progress:
            meta.append(AvailabilityGlyph(facts.where, size=12))
            meta.append(_label(facts.status()))
        elif book.shelf == "finished":
            meta.append(_label("Finished"))
        elif facts.in_progress:
            bar = ProgressLine(facts.place.fraction if facts.place else 0, size="tile", tone="neutral",
                               label=f"{book.short_title} progress")
            bar.set_hexpand(True)
            meta.append(bar)
            meta.append(_label(f"{facts.percent}%"))
        else:
            status = facts.short_status()
            label = _label(status)
            if status == "New":
                label.add_css_class("leaf-book-new")
            meta.append(label)
        box.append(meta)
        button.set_child(box)
        button.update_property([Gtk.AccessibleProperty.LABEL], [facts.spoken()])
        if book.short_title != book.title:
            button.set_tooltip_text(book.title)
        button.connect("clicked", lambda *_: self.emit("open-book", book.id, False))
        menu = Gtk.GestureClick(button=3)
        menu.connect("pressed", lambda _g, _n, x, y: self._book_menu(button, facts, x, y))
        button.add_controller(menu)
        return button

    def _book_menu(self, anchor: Gtk.Widget, facts: BookFacts, x: float, y: float) -> None:
        book = facts.book
        popover = Gtk.Popover(has_arrow=False)
        popover.set_parent(anchor)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("leaf-book-menu")

        def item(label, callback):
            entry = Gtk.Button(label=label)
            entry.add_css_class("flat")
            entry.get_child().set_xalign(0)
            entry.connect("clicked", lambda *_: (popover.popdown(), callback()))
            box.append(entry)

        if not facts.local:
            item("Download to keep", lambda: self.emit("keep-book", book.id))
        if book.shelf != "want":
            item("Want to read", lambda: self.library.set_shelf(book.id, "want"))
        if book.shelf != "finished":
            item("Mark as finished", lambda: self.library.set_shelf(book.id, "finished"))
        elif book.shelf == "finished":
            item("Mark as not finished", lambda: self.library.set_shelf(book.id, "reading" if book.position else "new"))
        for collection in self.library.collections():
            member = book.id in collection.books
            item(("Remove from " if member else "Add to ") + collection.name,
                 lambda c=collection, m=member: self.library.set_in_collection(c.id, book.id, not m))
        popover.set_child(box)
        area = Gdk_rect(x, y)
        popover.set_pointing_to(area)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()


def _button_content(icon: str, text: str) -> Gtk.Widget:
    box = Gtk.Box(spacing=7, halign=Gtk.Align.CENTER)
    box.append(_icon(icon))
    box.append(Gtk.Label(label=text))
    return box


def Gdk_rect(x: float, y: float):
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    return rect


def Adw_bin():
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    return Adw.Bin()
