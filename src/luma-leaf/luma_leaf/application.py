# SPDX-License-Identifier: Apache-2.0
"""Leaf: one window, two rooms — the library and the page."""
from __future__ import annotations

import os
import ctypes
import threading
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("LumaUI", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, LumaUI

from luma_appkit import AppWindow, Command, CommandGroup, CommandRegistry, SidebarToggle, add_style_sheet
from luma_appkit import icons

from . import importer
from .data_paths import data_directory
from .fixture import FixtureLibrary, SilentSpeaker
from .library_view import LibraryView
from .mpris import MprisService
from .position import Locator
from .speech import Speaker
from .store import Library
from .watch import FolderWatch

APP_ID = "org.projectluma.Leaf.LumaUIPreview" if os.environ.get("LUMA_LEAF_PREVIEW") else "org.projectluma.Leaf"
ICON_NAME = "org.projectluma.Leaf"
VERSION = "0.1.0"


def _data(*parts: str) -> Path:
    return data_directory().joinpath(*parts)


def _register_reading_fonts() -> None:
    """Make Leaf's book faces available to Pango without changing the OS."""
    folder = _data("reader", "fonts")
    try:
        fontconfig = ctypes.CDLL("libfontconfig.so.1")
        fontconfig.FcConfigGetCurrent.restype = ctypes.c_void_p
        fontconfig.FcConfigAppFontAddFile.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
        fontconfig.FcConfigAppFontAddFile.restype = ctypes.c_int
        config = fontconfig.FcConfigGetCurrent()
        for name in ("Literata.ttf", "Literata-Italic.ttf", "Newsreader.ttf", "Newsreader-Italic.ttf",
                     "AtkinsonHyperlegible-Regular.ttf", "AtkinsonHyperlegible-Bold.ttf"):
            fontconfig.FcConfigAppFontAddFile(config, os.fsencode(folder / name))
    except (OSError, AttributeError):
        # EPUBs remain readable with their usual serif or sans fallback.
        pass


class LeafWindow(AppWindow):
    def __init__(self, application: "LeafApplication") -> None:
        super().__init__(application=application, app_id=APP_ID, title="Leaf", icon_name=ICON_NAME,
                         commands=self._commands(application), subtitle="",
                         default_width=1160, default_height=760, minimum_width=360, minimum_height=460)
        self.app = application
        self.add_css_class("leaf-window")
        application.set_menubar(application.menu())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, hexpand=True, vexpand=True)
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        self.library_view = LibraryView(application.library, application.locator)
        self.sidebar_toggle = SidebarToggle(self.library_view.sidebar,drawer_below=901)
        self.sidebar_toggle.set_control_visible(False)
        self.set_leading(self.sidebar_toggle)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 559px"))
        phone.add_setter(self.sidebar_toggle,"visible",False)
        phone.add_setter(self.library_view.hero_desktop,"visible",False)
        phone.add_setter(self.library_view.hero_phone,"visible",True)
        for side,value in (("top",52),("start",16),("end",16),("bottom",150)):
            phone.add_setter(self.library_view.content,f"margin-{side}",value)
        self.add_breakpoint(phone)
        self.library_view.connect("open-book", lambda _v, book_id, listen: application.open_book(book_id, listen))
        self.library_view.connect("add-books", lambda *_: application.add_books())
        self.library_view.connect("add-library", lambda *_: application.add_library())
        self.library_view.connect("keep-book", lambda _v, book_id: application.keep_book(book_id))
        self._reader = None
        self.stack.add_named(self.library_view, "library")
        self.stack.connect("notify::visible-child-name", lambda *_: application.sync_actions())
        overlay = Gtk.Overlay()
        overlay.set_child(self.stack)
        self.toast = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.NONE, halign=Gtk.Align.CENTER,
                                  valign=Gtk.Align.END, margin_bottom=40)
        self.toast_label = Gtk.Label()
        self.toast_label.add_css_class("leaf-toast")
        self.toast.set_child(self.toast_label)
        overlay.add_overlay(self.toast)
        self.set_body(overlay)
        files_drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        files_drop.connect("drop", self._drop_books)
        self.add_controller(files_drop)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._closing)
        # Only needed when a folder could not be watched; see FolderWatch.
        self.connect("notify::is-active", lambda window, _pspec: window.is_active() and application.check_folders())

    def _commands(self, application) -> CommandRegistry:
        return CommandRegistry((
            CommandGroup("Library", (
                Command("leaf.add-books", "Add books", application.add_books, "lumaui-plus-symbolic"),
                Command("leaf.add-library", "Connect a library", application.add_library, "lumaui-library-symbolic"),
                Command("leaf.all-books", "All books", lambda: application._library_view("all"), "lumaui-book-open-symbolic"),
                Command("leaf.reading-now", "Reading now", lambda: application._library_view("reading"), "lumaui-bookmark-symbolic"),
                Command("leaf.sidebar", "Show or hide sidebar", lambda: self.sidebar_toggle.toggle(), "lumaui-panel-left-symbolic"),
            )),
            CommandGroup("Reading", (
                Command("leaf.contents", "Contents", lambda: self.reader.toggle_contents(), "lumaui-list-symbolic", enabled=lambda: self.reading),
                Command("leaf.bookmark", "Bookmark this page", lambda: self.reader.toggle_bookmark(), "lumaui-bookmark-symbolic", enabled=lambda: self.reading),
                Command("leaf.read-aloud", "Read aloud", application._read_aloud, "lumaui-volume-2-symbolic"),
            )),
            CommandGroup("", (
                Command("leaf.about", "About Leaf", application._about, "lumaui-info-symbolic"),
                Command("leaf.quit", "Quit Leaf", application._quit, "lumaui-log-out-symbolic", shortcut=("Ctrl", "Q")),
            )),
        ))

    @property
    def reader(self):
        # The library must paint without starting a WebKit process or opening
        # the speech service. The actual reader is created only when needed.
        if self._reader is None:
            from .reader_view import ReaderView
            self._reader = ReaderView(self.app.library, self.app.locator, self.app.speaker)
            self._reader.connect("library", lambda *_: self.show_library())
            self.stack.add_named(self._reader, "page")
            self.app._start_mpris()
        return self._reader

    @property
    def reading(self) -> bool:
        stack = getattr(self, "stack", None)
        return bool(stack and stack.get_visible_child_name() == "page")

    def show_library(self) -> None:
        self.set_phone_bleed(False)
        # Reading aloud continues while the library is showing.
        self.stack.set_visible_child_name("library")
        if self._reader is not None:
            self._reader.action_center.hide_bar()
            self._reader.contents_button.set_active(False)
        self.set_identity_subtitle("")
        self.app.check_folders()
        self.library_view.queue_render()

    def show_page(self, title: str) -> None:
        self.set_phone_bleed(True)
        self.stack.set_visible_child_name("page")
        self.set_identity_subtitle(title)
        self.reader._refresh_actions()

    def _drop_books(self, _target, file_list, _x: float, _y: float) -> bool:
        files = tuple(file for file in file_list.get_files()
                      if (path := file.get_path()) and path.lower().endswith(importer.EXTENSIONS))
        if not files:
            return False
        self.app.do_open(files, len(files), "")
        return True

    def _key(self, _controller, keyval, _code, state) -> bool:
        focus = self.get_focus()
        typing = isinstance(focus, (Gtk.Text, Gtk.Entry, Gtk.SearchEntry, Gtk.TextView))
        if keyval == Gdk.KEY_Escape and self.reading:
            return self.reader.close_transient()
        modifiers = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK)
        if typing or modifiers or not self.reading:
            return False
        if keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.reader.toggle_listen()
            return True
        from gi.repository import WebKit
        if isinstance(focus, WebKit.WebView):
            return False   # the page handles its own turning keys
        if keyval in (Gdk.KEY_Right, Gdk.KEY_Page_Down, Gdk.KEY_space):
            self.reader.run("leaf.next()")
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Page_Up):
            self.reader.run("leaf.prev()")
            return True
        return False

    def _closing(self, *_args) -> bool:
        # Only closing the window, or ✕ in the player, stops reading aloud.
        if self._reader is not None:
            self._reader.narrator.stop()
        return False


class LeafApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.window: LeafWindow | None = None
        self.library: Library | None = None
        self.mpris: MprisService | None = None
        self._speaker = None
        self.books_watch: FolderWatch | None = None
        self.library_watch: FolderWatch | None = None
        self._pending_scan = 0
        self.fixture_mode = bool(os.environ.get("LUMA_LEAF_FIXTURE"))
        GLib.set_application_name("Leaf")

    # startup
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        LumaUI.init()
        _register_reading_fonts()
        icons = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icons.add_search_path(str(_data("icons")))
        add_style_sheet(str(_data("leaf.css")))
        self.library = (FixtureLibrary(os.environ["LUMA_LEAF_FIXTURE"])
                        if self.fixture_mode else Library())
        self.locator = Locator()
        self._install_actions()
        self._external = 0
        if not self.fixture_mode:
            self._scan()
            # Luma Connect writes reading from other devices into the same library.
            self.library_watch = FolderWatch(self.library.path.parent, self._library_file_changed)
            # Server libraries: listed at start, and checked for reach every five minutes.
            if self.library.sources():
                GLib.idle_add(lambda: self._refresh_sources() and False)
            GLib.timeout_add_seconds(300, self._refresh_sources)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = LeafWindow(self)
            if self.fixture_mode:
                state = os.environ.get("LUMA_LEAF_STATE", "library")
                if state == "want":
                    self.window.library_view.set_view("want")
                elif state == "title-sort":
                    self.window.library_view.sort = "title"
                    self.window.library_view.sort_switch.set_current("title")
                    self.window.library_view.set_view("all")
                elif state == "highlights":
                    self.window.library_view.set_view("hl")
                elif state in ("reader", "contents", "type-page", "listening", "selection"):
                    self.open_book("totc", False)
                    if state == "contents":
                        self.window.reader.toggle_contents()
                    elif state == "type-page":
                        GLib.idle_add(lambda: (self.window.reader.show_type_panel(), False)[1])
                    elif state == "listening":
                        narrator = self.window.reader.narrator
                        narrator.state.active = True
                        narrator.state.playing = True
                        narrator.state.book_id = "totc"
                        narrator.state.title = "A Tale of Two Cities"
                        narrator.state.chapter_label = "The Mail"
                        narrator.state.minutes_left = 11 * 60
                        narrator._notify()
            self.sync_actions()
        self.window.present()

    @property
    def speaker(self):
        if self._speaker is None:
            self._speaker = SilentSpeaker() if self.fixture_mode else Speaker()
        return self._speaker

    def _start_mpris(self) -> None:
        if self.mpris is not None or self.fixture_mode or self.window is None or self.window._reader is None:
            return
        try:
            self.mpris = MprisService(self.window._reader.narrator, raise_window=self.activate,
                                      quit_application=self.quit, cover_for=self._cover_for)
        except GLib.Error:
            self.mpris = None

    def do_open(self, files, _count, _hint) -> None:
        self.activate()
        if self.fixture_mode:
            opened = None
            for file in files:
                path = file.get_path()
                if path and path.lower().endswith(importer.EXTENSIONS):
                    opened = importer.import_file(self.library, Path(path)) or opened
            if opened:
                self._scanned()
                self.open_book(opened, False)
            return

        def work():
            opened = None
            worker = Library(self.library.path)
            for file in files:
                path = file.get_path()
                if path and path.lower().endswith(importer.EXTENSIONS):
                    opened = importer.import_file(worker, Path(path)) or opened
            GLib.idle_add(self._scanned)
            if opened:
                GLib.idle_add(self.open_book, opened, False)

        threading.Thread(target=work, daemon=True).start()

    def add_books(self) -> None:
        dialog = Gtk.FileDialog(title="Add books")
        selection = Gtk.FileFilter(name="EPUB books")
        selection.add_pattern("*.epub")
        dialog.set_default_filter(selection)
        dialog.open_multiple(self.window, None, self._books_chosen)

    def _books_chosen(self, dialog, result) -> None:
        try:
            chosen = dialog.open_multiple_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                self._toast(f"Books weren’t added: {error.message}")
            return
        files = [chosen.get_item(index) for index in range(chosen.get_n_items())]
        if files:
            self.do_open(files, len(files), "")

    def do_shutdown(self) -> None:
        if self.mpris is not None:
            self.mpris.close()
        if self._speaker is not None:
            self._speaker.close()
        Adw.Application.do_shutdown(self)

    # the library folder
    def _scan(self) -> None:
        folder = importer.default_folder()

        def work():
            # SQLite connections are per thread: the scan writes through its
            # own, and the window re-renders from the shared file when it ends.
            importer.scan(Library(self.library.path), folder)
            GLib.idle_add(self._scanned)

        threading.Thread(target=work, daemon=True).start()
        if folder.is_dir():
            self.books_watch = FolderWatch(folder, self._folder_changed, flags=Gio.FileMonitorFlags.WATCH_MOVES,
                                           recursive=True)

    def check_folders(self) -> None:
        """Look for changes a folder watch could not report (see FolderWatch)."""
        for watch in (self.books_watch, self.library_watch):
            if watch is not None:
                watch.check()

    def _folder_changed(self, *_args) -> None:
        if self._pending_scan:
            GLib.source_remove(self._pending_scan)
        self._pending_scan = GLib.timeout_add_seconds(2, self._rescan)

    def _rescan(self) -> bool:
        self._pending_scan = 0

        def work():
            importer.scan(Library(self.library.path))
            GLib.idle_add(self._scanned)

        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _scanned(self) -> bool:
        self.library._changed("books")
        return GLib.SOURCE_REMOVE

    def _library_file_changed(self, *_args) -> None:
        if self._external:
            GLib.source_remove(self._external)
        self._external = GLib.timeout_add(800, self._library_changed_outside)

    def _library_changed_outside(self) -> bool:
        self._external = 0
        self.library._changed("external")
        return GLib.SOURCE_REMOVE

    def _cover_for(self, book_id: str) -> str | None:
        book = self.library.book(book_id)
        return book.cover if book else None

    # opening
    def open_book(self, book_id: str, listen: bool = False) -> bool:
        book = self.library.book(book_id)
        if book is None or self.window is None:
            return False
        if not book.on_device:
            if book.remote_url:
                self._stream(book, listen)
            else:
                self.window.library_view.queue_render()
            return False
        self.window.reader.open(book, listen=listen)
        self.window.show_page(book.short_title)
        self.sync_actions()
        return False

    # libraries on a server
    def _stream(self, book, listen: bool) -> None:
        from . import opds
        source = next((s for s in self.library.sources() if s.id == book.source), None)
        self._toast(f"Opening “{book.short_title}” from {source.name if source else 'its library'}…")

        def work():
            try:
                opened = opds.stream(Library(self.library.path), book.id)
            except opds.SourceError as error:
                GLib.idle_add(self._toast, str(error))
                return
            GLib.idle_add(self._scanned)
            GLib.idle_add(self.open_book, opened, listen)

        threading.Thread(target=work, daemon=True).start()

    def keep_book(self, book_id: str) -> None:
        from . import opds
        book = self.library.book(book_id)
        if book is None:
            return
        self._toast(f"Downloading “{book.short_title}”…")

        def work():
            try:
                opds.keep(Library(self.library.path), book_id, importer.default_folder())
                GLib.idle_add(self._toast, f"“{book.short_title}” is on this device")
            except (opds.SourceError, OSError) as error:
                GLib.idle_add(self._toast, str(error))
            GLib.idle_add(self._scanned)

        threading.Thread(target=work, daemon=True).start()

    def _toast(self, message: str) -> bool:
        if self.window is not None:
            self.window.toast_label.set_label(message)
            self.window.toast.set_reveal_child(True)
            if getattr(self, "_toast_source", 0):
                GLib.source_remove(self._toast_source)
            self._toast_source = GLib.timeout_add_seconds(4, self._hide_toast)
        return False

    def _hide_toast(self) -> bool:
        self._toast_source = 0
        if self.window is not None:
            self.window.toast.set_reveal_child(False)
        return GLib.SOURCE_REMOVE

    def _refresh_sources(self) -> bool:
        from . import opds

        def work():
            worker = Library(self.library.path)
            for source in worker.sources():
                try:
                    opds.refresh(worker, source)
                except opds.SourceError:
                    pass
            GLib.idle_add(self._scanned)
            GLib.idle_add(self._sources_refreshed)

        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _sources_refreshed(self) -> bool:
        self.library._changed("sources")
        return GLib.SOURCE_REMOVE

    def add_library(self) -> None:
        from . import opds
        dialog = Adw.AlertDialog(heading="Connect a library",
                                 body="Read the books on a Calibre content server or an OPDS catalogue. Its books "
                                      "appear under Where, and stay listed when the server is out of reach.")
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        kind = Gtk.DropDown(model=Gtk.StringList.new(["Calibre content server", "OPDS catalogue"]))
        name = Gtk.Entry(placeholder_text="Name, like Home library")
        address = Gtk.Entry(placeholder_text="Address, like http://192.168.1.20:8080", input_purpose=Gtk.InputPurpose.URL)
        username = Gtk.Entry(placeholder_text="User name (if the library asks for one)")
        password = Gtk.PasswordEntry(placeholder_text="Password", show_peek_icon=True)
        for widget in (kind, name, address, username, password):
            form.append(widget)
        dialog.set_extra_child(form)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Connect")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")

        def respond(_dialog, response):
            if response != "connect" or not address.get_text().strip():
                return
            source = self.library.add_source("calibre" if kind.get_selected() == 0 else "opds",
                                             name.get_text().strip() or urlparse_host(address.get_text()),
                                             address.get_text().strip(), username.get_text().strip())
            if password.get_text():
                opds.store_password(source.id, password.get_text())
            self._toast(f"Reading {source.name}…")

            def work():
                worker = Library(self.library.path)
                try:
                    count = opds.refresh(worker, source)
                    GLib.idle_add(self._toast, f"{source.name}: {count} book" + ("" if count == 1 else "s"))
                except opds.SourceError as error:
                    GLib.idle_add(self._toast, f"{source.name}: {error}")
                GLib.idle_add(self._scanned)

            threading.Thread(target=work, daemon=True).start()

        dialog.connect("response", respond)
        dialog.present(self.window)

    # menu and actions
    def menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        library = Gio.Menu()
        library.append("All books", "app.all-books")
        library.append("Reading now", "app.reading-now")
        library.append("Show library", "app.show-library")
        menu.append_section("Library", library)
        reading = Gio.Menu()
        reading.append("Contents", "app.contents")
        reading.append("Bookmark this page", "app.bookmark")
        reading.append("Larger text", "app.larger")
        reading.append("Smaller text", "app.smaller")
        menu.append_section("Reading", reading)
        listen = Gio.Menu()
        listen.append("Read aloud", "app.read-aloud")
        listen.append("Faster", "app.faster")
        listen.append("Slower", "app.slower")
        self.voice_menu = Gio.Menu()
        self.sleep_menu = Gio.Menu()
        listen.append_submenu("Voice", self.voice_menu)
        listen.append_submenu("Sleep timer", self.sleep_menu)
        menu.append_section("Listen", listen)
        leaf = Gio.Menu()
        leaf.append("About Leaf", "app.about")
        leaf.append("Quit Leaf", "app.quit")
        menu.append_section("Leaf", leaf)
        return menu

    def _install_actions(self) -> None:
        def add(name, callback, accels=(), parameter=None):
            action = Gio.SimpleAction.new(name, parameter)
            action.connect("activate", lambda _a, value: callback(value) if parameter else callback())
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", list(accels))
            return action

        self.actions = {
            "all-books": add("all-books", lambda: self._library_view("all"), ["<Primary>1"]),
            "reading-now": add("reading-now", lambda: self._library_view("reading"), ["<Primary>2"]),
            "show-library": add("show-library", lambda: self.window and self.window.show_library(), ["<Primary>l"]),
            "contents": add("contents", lambda: self.window.reader.toggle_contents(), ["<Primary>t"]),
            "bookmark": add("bookmark", lambda: self.window.reader.toggle_bookmark(), ["<Primary>d"]),
            "larger": add("larger", lambda: self.window.reader.change_size(1), ["<Primary>plus", "<Primary>equal", "<Primary>KP_Add"]),
            "smaller": add("smaller", lambda: self.window.reader.change_size(-1), ["<Primary>minus", "<Primary>KP_Subtract"]),
            "read-aloud": add("read-aloud", self._read_aloud),
            "faster": add("faster", lambda: self.window.reader.narrator.faster()),
            "slower": add("slower", lambda: self.window.reader.narrator.slower()),
            "voice": add("voice", lambda v: self._voice(v.get_int32()), parameter=GLib.VariantType.new("i")),
            "sleep": add("sleep", lambda v: self.window.reader.narrator.set_sleep(v.get_string()),
                         parameter=GLib.VariantType.new("s")),
            "about": add("about", self._about),
            "quit": add("quit", self._quit, ["<Primary>q"]),
        }

    def sync_actions(self) -> None:
        reading = bool(self.window and self.window.reading)
        for name in ("contents", "bookmark", "larger", "smaller"):
            self.actions[name].set_enabled(reading)
        if self.window is not None and self._speaker is not None and not self.voice_menu.get_n_items():
            from .narrator import SLEEP_CHOICES
            for index, voice in enumerate(self.speaker.voices[:12]):
                self.voice_menu.append(voice.label, f"app.voice({index})")
            for key, label in SLEEP_CHOICES:
                self.sleep_menu.append(label, f"app.sleep('{key}')")

    def _library_view(self, view: str) -> None:
        if self.window is None:
            return
        self.window.show_library()
        self.window.library_view.set_view(view)

    def _read_aloud(self) -> None:
        """From the library, Read aloud opens the current book and starts reading."""
        if self.window is None:
            return
        if self.window.reading:
            self.window.reader.toggle_listen()
            return
        books = [b for b in self.library.books() if b.shelf == "reading" and b.on_device]
        books.sort(key=lambda b: -(b.position_at or 0))
        if books:
            self.open_book(books[0].id, True)

    def _voice(self, index: int) -> None:
        voices = self.speaker.voices
        if self.window and 0 <= index < len(voices):
            self.window.reader.narrator.set_voice(voices[index])
            self.window.reader.voice_drop.set_selected(index)

    def _about(self) -> None:
        if self.window:
            Adw.AboutDialog(application_name="Leaf", application_icon=ICON_NAME, version=VERSION,
                            developer_name="Project Luma", license_type=Gtk.License.APACHE_2_0,
                            comments="A reader for people who want the book.").present(self.window)

    def _quit(self) -> None:
        if self.window and self.window._reader is not None:
            self.window._reader.narrator.stop()
        self.quit()


def urlparse_host(address: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(address if "//" in address else "http://" + address)
    return parsed.hostname or "Library"


def main(argv: list[str] | None = None) -> int:
    return LeafApplication().run(argv)
