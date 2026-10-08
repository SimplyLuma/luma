# SPDX-License-Identifier: Apache-2.0
"""Native adaptive GTK/libadwaita application for Tide."""
from __future__ import annotations

import concurrent.futures
import contextlib
import gettext
import logging
import json
import weakref
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("LumaUI", "1")
gi.require_version("LumaSemantics", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, LumaSemantics, LumaUI

from luma_appkit import (
    AppWindow, Command, CommandGroup, CommandRegistry, EmptyState, ListEmptyState, Island, Menu,
    IconButton, NavigationRow, PresentationMode, SectionLabel, Toolbar, add_style_sheet, attach_context_menu,
    command_popover,
)

try:
    from luma_appkit.navigation import NavigationTrail, Place, bind_navigation_input
except ImportError:  # An AppKit from before the shared trail; drop with it.
    from ._navigation import NavigationTrail, Place, bind_navigation_input

from .credentials import CredentialUnavailable, SecretServiceStore
from .identity import APP_ID, validate_application_id
from .credit_markup import artist_index, build_credit_markup, escape_markup, parse_link_uri
from .indexer import AUDIO_EXTENSIONS, LibraryIndexer
from .model import (
    LibraryStore, OfflineState, RepeatMode, SearchResults, Source, SourceState, Track, TrackCopy,
)
from .mpris import MprisService
from .playback import GStreamerEngine, PlaybackController, PlaybackSnapshot, PlaybackState
from .preview_import import import_preview_library
from .remote import KIND as REMOTE_KIND, RemoteSources, accepts_plaintext
from .startup import open_library_store
from .subsonic import (
    AuthenticationFailed, Cancelled, CertificateError, InvalidAddress, SubsonicError, Unreachable,
    has_scheme, is_encrypted, normalize_address,
)

log = logging.getLogger("tide")

VIEW_TITLES = {
    "albums": "Albums", "songs": "Songs", "artists": "Artists",
    "recent": "Recently added", "sources": "Sources", "search": "Search",
    "search-albums": "Albums", "search-artists": "Artists", "search-songs": "Songs",
    "album": "Album", "artist": "Artist", "now-playing": "Now Playing",
}
# A server can hold tens of thousands of songs. Lists and grids build only the
# rows on screen; these bound the few places that still build every row.
QUEUE_ROWS = 50
RECENT_LIMIT = 500
LIBRARY_REFRESH_SECONDS = 8
# Views that the source filter applies to; playlists, albums opened from them,
# Now Playing and Sources itself are not filtered.
SEARCH_VIEWS = {"search", "search-albums", "search-artists", "search-songs"}
FILTERED_VIEWS = {"albums", "songs", "artists", "recent"} | SEARCH_VIEWS


def _format_time(nanoseconds: int) -> str:
    seconds = max(0, round(nanoseconds / 1_000_000_000))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _file_path(uri: str | None) -> Path | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = Path(unquote(parsed.path))
    return path if path.is_file() else None


def _accessible(widget: Gtk.Widget, label: str) -> Gtk.Widget:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return widget


def _on_main(future: concurrent.futures.Future, callback: Callable[[concurrent.futures.Future], None]) -> None:
    """Deliver a worker's result to the GTK main context."""
    def deliver() -> bool:
        callback(future)
        return GLib.SOURCE_REMOVE

    future.add_done_callback(lambda _future: GLib.idle_add(deliver))


_ = gettext.gettext
ngettext = gettext.ngettext


def _count(count: int, singular: str, plural: str) -> str:
    """A count read the way it is said: "1 copy", "2 copies", "1,569 songs".
    `singular` and `plural` carry a `{count}` placeholder."""
    return ngettext(singular, plural, count).format(count=f"{count:,}")


def _songs(count: int) -> str:
    return _count(count, "{count} song", "{count} songs")


def _tracks(count: int) -> str:
    return _count(count, "{count} track", "{count} tracks")


# Track lists are columns. Each list kind shows the columns that tell the
# person something there: an album's own songs need neither its artist nor
# its name on every row.
TRACK_COLUMNS = {
    "library": ("number", "title", "artist", "album", "year", "source", "time"),
    "album": ("number", "title", "year", "source", "time"),
}
COLUMN_TITLES = {"number": "#", "title": "Title", "artist": "Artist", "album": "Album",
                 "year": "Year", "source": "Plays from", "time": "Time"}
COLUMN_WIDTHS = {"number": 20, "year": 52, "artist": 180, "album": 200, "source": 150, "time": 48}


class SourceMonitor:
    """GIO change monitors trigger bounded rescans; there is no polling loop."""

    def __init__(self, source: Source, callback) -> None:
        self.source = source
        self.callback = callback
        self.monitors: list[Gio.FileMonitor] = []
        self.pending = 0
        parsed = urlparse(source.uri)
        if source.local and parsed.scheme == "file":
            root = Path(unquote(parsed.path))
            if root.is_dir():
                for directory, names, _files in os.walk(root, followlinks=False):
                    names[:] = [name for name in names if not name.startswith(".")]
                    try:
                        monitor = Gio.File.new_for_path(directory).monitor_directory(
                            Gio.FileMonitorFlags.WATCH_MOVES, None
                        )
                    except GLib.Error:
                        continue
                    monitor.connect("changed", self._changed)
                    self.monitors.append(monitor)

    def _changed(self, *_args: object) -> None:
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add_seconds(2, self._dispatch)

    def _dispatch(self) -> bool:
        self.pending = 0
        self.callback(self.source)
        return GLib.SOURCE_REMOVE

    def close(self) -> None:
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        for monitor in self.monitors:
            monitor.cancel()
        self.monitors.clear()


from .ui import TideWindow


class TideApplication(Adw.Application):
    def __init__(self, *, application_id: str = APP_ID) -> None:
        super().__init__(application_id=validate_application_id(application_id),
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.store: LibraryStore
        self.indexer: LibraryIndexer
        self.controller: PlaybackController
        self.window: TideWindow | None = None
        self.mpris: MprisService | None = None
        self.monitors: dict[str, SourceMonitor] = {}
        self._actions: dict[str, Gio.SimpleAction] = {}
        self.remote: RemoteSources
        self._syncing: set[str] = set()
        self._checking: set[str] = set()
        self._download_refresh = 0
        self._network_timer = 0
        self._network_handler = 0

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        LumaUI.init()
        from luma_appkit import install_lumaui
        install_lumaui()
        self._load_css()
        # Opening the library runs its schema migration; a failure there is
        # logged with its traceback and still fails startup, never silently.
        self.store = open_library_store(log=log)
        # A dev-preview Tide's library (its sources, songs, history and
        # server sign-in reference) comes along once, before anything reads
        # the library. It logs and carries on if it can't.
        import_preview_library(self.store, log=log)
        self.indexer = LibraryIndexer(self.store)
        # The keyring is opened on the remote worker the first time a password
        # is needed, never here on the GTK thread.
        self.remote = RemoteSources(self.store, SecretServiceStore)
        self.controller = PlaybackController(self.store, GStreamerEngine(), self._resolve_copy)
        self._install_actions()
        self._install_semantics()
        try:
            # True once for a library whose tracks predate multi-artist
            # credits: every source is read again, even if unchanged.
            reindex = self.store.consume_pending_artist_reindex()
            if not self.store.sources():
                music = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC) or Path.home() / "Music")
                self.store.add_source("This device", "local-folder", music, local=True)
            # Directory monitors cannot report moves made while Tide was closed.
            # Reconcile every persisted local source through the normal async
            # indexer, and reconnect remote sources, which sync only if their
            # server changed.
            for source in self.store.sources():
                if source.kind == REMOTE_KIND:
                    if reindex:
                        with contextlib.suppress(KeyError):
                            self.store.set_sync_marker(source.id, None)
                    self.scan_source(source, quiet=True)
                elif source.local:
                    self.scan_source(source, quiet=True, force=reindex)
            self._rebuild_monitors()
        except Exception:
            log.exception("Tide startup failed while preparing the library's sources")
            raise
        # Remote sources follow the network's own change events; nothing polls.
        network = Gio.NetworkMonitor.get_default()
        self._network_handler = network.connect("network-changed", self._network_changed)
        self.controller.subscribe(self._playback_for_sources)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = TideWindow(self)
            self.window.connect("close-request", self._window_closed)
        if self.mpris is None:
            self.mpris = MprisService(
                self.controller,
                application_id=self.get_application_id(),
                raise_window=self.activate,
                quit_application=self._quit,
            )
        self.window.present()

    def do_open(self, files: list[Gio.File], _n_files: int, _hint: str) -> None:
        self.activate()
        opened: dict[Path, list[Path]] = {}
        for file in files:
            if path := file.get_path():
                local_path = Path(path).resolve()
                opened.setdefault(local_path.parent, []).append(local_path)
        for parent, paths in opened.items():
            source = self.store.add_source(parent.name or "Music", "local-folder", parent, local=True)
            self.scan_source(source, refresh_paths=paths)

    def _window_closed(self, _window: TideWindow) -> bool:
        if self.controller.snapshot.state in (PlaybackState.PLAYING, PlaybackState.LOADING):
            self.window.set_visible(False)
            return True
        return False

    def _load_css(self) -> None:
        from .resources import stylesheet_path
        add_style_sheet(str(stylesheet_path()))

    def _action(self, name: str, callback, *, state=None, parameter=None) -> Gio.SimpleAction:
        if state is None:
            action = Gio.SimpleAction.new(name, parameter)
        else:
            action = Gio.SimpleAction.new_stateful(name, parameter, state)
        action.connect("activate", callback)
        self.add_action(action)
        self._actions[name] = action
        return action

    def _install_actions(self) -> None:
        self._action("play-pause", lambda *_: self.controller.toggle())
        self._action("next", lambda *_: self.controller.next())
        self._action("previous", lambda *_: self.controller.previous())
        self._action("stop", lambda *_: self.controller.stop())
        self._action("add-music", lambda *_: self._choose_music())
        self._action("add-server", lambda *_: self.show_server_dialog())
        self._action("add-source", lambda *_: self.window and self.window.present_source_menu())
        self._action("new-playlist", lambda *_: self._new_playlist())
        self._action("search", lambda *_: self.window and self.window.open_search())
        self._action("show-queue", self._toggle_queue, state=GLib.Variant("b", True))
        self._action("shuffle", self._toggle_shuffle, state=GLib.Variant("b", self.controller.snapshot.shuffle))
        self._action("repeat", self._cycle_repeat, state=GLib.Variant("s", self.controller.snapshot.repeat.value))
        self._action("select-copy", self._select_copy, parameter=GLib.VariantType.new("s"))
        self._action("shortcuts", lambda *_: self._show_shortcuts())
        self._action("about", lambda *_: self._show_about())
        self._action("quit", lambda *_: self._quit())
        self.set_accels_for_action("app.play-pause", ["AudioPlay"])
        self.set_accels_for_action("app.next", ["<Primary>Right", "AudioNext"])
        self.set_accels_for_action("app.previous", ["<Primary>Left", "AudioPrev"])
        self.set_accels_for_action("app.search", ["<Primary>f"])
        self.set_accels_for_action("app.new-playlist", ["<Primary>n"])
        self.set_accels_for_action("app.add-music", ["<Primary>o"])
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def _install_semantics(self) -> None:
        self.semantic_root = LumaSemantics.SemanticObject.new("tide", "application", "Tide")
        library = LumaSemantics.SemanticObject.new("tide-library", "music-library", "Library")
        playback = LumaSemantics.SemanticObject.new("tide-playback", "playback-session", "Playback")
        self.semantic_root.add_child(library)
        self.semantic_root.add_child(playback)
        for identifier, label in (
            ("playback.play-pause", "Play or pause"),
            ("playback.next", "Next track"),
            ("playback.previous", "Previous track"),
            ("library.add-source", "Add music source"),
            ("library.add-server", "Add music server"),
            ("source.remove", "Remove music server"),
            ("track.make-offline", "Download for offline listening"),
            ("track.remove-offline", "Remove download"),
            ("playlist.create", "Create playlist"),
        ):
            action = LumaSemantics.Action.new(identifier, label)
            risk = {
                "library.add-source": LumaSemantics.ActionRisk.CONSEQUENTIAL,
                # Adding a server stores an account password in the keyring.
                "library.add-server": LumaSemantics.ActionRisk.SECURITY_SENSITIVE,
                "source.remove": LumaSemantics.ActionRisk.DESTRUCTIVE,
                "track.make-offline": LumaSemantics.ActionRisk.CONSEQUENTIAL,
                "track.remove-offline": LumaSemantics.ActionRisk.DESTRUCTIVE,
            }.get(identifier)
            if risk is not None:
                action.set_risk(risk)
            self.semantic_root.add_action(action)
        self.live_extension = LumaSemantics.LiveExtension.new(
            "tide-media", self.get_application_id(), LumaSemantics.LiveCategory.MEDIA, "Tide"
        )
        self.live_extension.set_privacy(LumaSemantics.Privacy.PRIVATE)
        for identifier, label in (
            ("playback.previous", "Previous"),
            ("playback.play-pause", "Play or pause"),
            ("playback.next", "Next"),
        ):
            self.live_extension.add_action(LumaSemantics.Action.new(identifier, label))

    def _toggle_queue(self, action: Gio.SimpleAction, _parameter: object) -> None:
        visible = not action.get_state().get_boolean()
        action.set_state(GLib.Variant("b", visible))
        if self.window:
            self.window.set_queue_visible(visible)

    def _toggle_shuffle(self, action: Gio.SimpleAction, _parameter: object) -> None:
        enabled = not action.get_state().get_boolean()
        action.set_state(GLib.Variant("b", enabled))
        self.controller.set_shuffle(enabled)

    def _cycle_repeat(self, action: Gio.SimpleAction, _parameter: object) -> None:
        modes = [RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE]
        mode = modes[(modes.index(self.controller.snapshot.repeat) + 1) % len(modes)]
        action.set_state(GLib.Variant("s", mode.value))
        self.controller.set_repeat(mode)

    def _select_copy(self, _action: Gio.SimpleAction, parameter: GLib.Variant) -> None:
        self.controller.switch_copy(parameter.get_string())

    def _choose_music(self) -> None:
        if not self.window:
            return
        dialog = Gtk.FileDialog(title="Add a music folder", modal=True)
        dialog.select_folder(self.window, None, self._folder_selected)

    def _folder_selected(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = Path(folder.get_path())
        source = self.store.add_source(path.name or "Music", "local-folder", path, local=True)
        self.scan_source(source)
        self._rebuild_monitors()

    def _new_playlist(self, track_ids: list[str] | None = None) -> None:
        if not self.window:
            return
        entry = Gtk.Entry(placeholder_text="Playlist name", activates_default=True)
        dialog = Adw.AlertDialog(heading="New Playlist", body="Create an ordered collection in your Tide library.")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.connect("response", self._playlist_response, entry, track_ids or [])
        dialog.present(self.window)

    def _playlist_response(self, _dialog: Adw.AlertDialog, response: str, entry: Gtk.Entry, track_ids: list[str]) -> None:
        if response != "create":
            return
        try:
            playlist_id = self.store.create_playlist(entry.get_text())
            if track_ids:
                self.store.set_playlist_tracks(playlist_id, track_ids)
        except (ValueError, __import__("sqlite3").IntegrityError) as error:
            self.notify_error(str(error))
        if self.window:
            self.window.refresh_library()

    def scan_source(self, source: Source, *, refresh_paths: list[Path] | None = None,
                    quiet: bool = False, force: bool = False) -> None:
        """Bring one source up to date. For a server this also retries a
        connection, so it is what "Try Again" does."""
        if source.kind == REMOTE_KIND:
            self.sync_remote(source, quiet=quiet)
            return
        if self.window:
            self.window.set_status(f"Scanning {source.name}…")

        def progress(done: int, total: int) -> None:
            GLib.idle_add(self._scan_progress, source.name, done, total)

        future = self.indexer.scan_async(
            source.id, progress, refresh_paths=refresh_paths or (), force=force
        )
        future.add_done_callback(lambda result: GLib.idle_add(self._scan_finished, source, result))

    def _scan_progress(self, name: str, done: int, total: int) -> bool:
        if self.window:
            self.window.set_status(f"Scanning {name} · {done:,} of {total:,}")
        return GLib.SOURCE_REMOVE

    def _scan_finished(self, source: Source, future) -> bool:
        try:
            result = future.result()
        except Exception as error:
            log.exception("Scanning %s failed", source.id)
            with contextlib.suppress(KeyError):
                self.store.set_source_state(source.id, SourceState.ERROR)
            self.notify_error(f"Could not scan {source.name}: {error}")
        else:
            self.controller.refresh_metadata()
            if result.failed:
                self.notify_error(f"{result.failed} files in {source.name} could not be indexed.")
        if self.window:
            self.window.set_status(None)
            self.window.refresh_library()
        self._rebuild_monitors()
        return GLib.SOURCE_REMOVE

    def _rebuild_monitors(self) -> None:
        for monitor in self.monitors.values():
            monitor.close()
        self.monitors = {
            source.id: SourceMonitor(source, self.scan_source)
            for source in self.store.sources()
            if source.local
        }

    # -- remote sources ---------------------------------------------------------

    def _resolve_copy(self, copy: TrackCopy) -> str:
        return copy.uri if copy.source_local else self.remote.resolve(copy)

    def show_server_dialog(self, source: Source | None = None) -> None:
        if not self.window:
            self.activate()
        if self.window:
            if source is not None:
                with contextlib.suppress(KeyError):
                    source = self.store.source(source.id)  # the latest account name
            self.window.open_sources()
            if source is None:
                self.window._add_source()
            else:
                self.window._open_source(source.id, edit=True)

    def source_added(self, source: Source, *, signed_in_again: bool = False) -> None:
        """A server was added, or signed in to again: sync it now. Signing in
        again leaves the person where they were."""
        log.info("Remote source %s %s", source.id, "signed in again" if signed_in_again else "added")
        if self.window:
            if not signed_in_again:
                self.window.show_view("sources")
            self.window.refresh_library()
        self._syncing.discard(source.id)
        self.sync_remote(source)

    def sync_remote(self, source: Source, *, quiet: bool = False) -> None:
        if source.id in self._syncing:
            return
        self._syncing.add(source.id)
        if self.window:
            self.window.set_status(f"Connecting to {source.name}…")
            self.window._populate_sources()

        def progress(phase: str, done: int, total: int) -> None:
            GLib.idle_add(self._sync_progress, source.name, phase, done, total)

        _on_main(self.remote.sync(source.id, progress),
                 lambda future: self._sync_finished(source, future, quiet))

    def _sync_progress(self, name: str, phase: str, done: int, total: int) -> bool:
        if not self.window:
            return GLib.SOURCE_REMOVE
        if phase == "songs":
            self.window.set_status(f"Syncing {name} · {done:,} of {total:,}")
            self.window.library_changed()
        else:
            if done == 0:
                # Songs are in; let them appear while artwork arrives.
                self.window.library_changed()
            self.window.set_status(None if total and done >= total else
                                   f"Fetching artwork from {name} · {done:,} of {total:,}")
        return GLib.SOURCE_REMOVE

    def _sync_finished(self, source: Source, future: concurrent.futures.Future, quiet: bool) -> None:
        self._syncing.discard(source.id)
        try:
            future.result()
        except (concurrent.futures.CancelledError, Cancelled):
            pass  # removed, or Tide is quitting
        except Unreachable as error:
            if not quiet:
                self.notify_error(f"{source.name} is out of reach. {error}")
        except (AuthenticationFailed, CredentialUnavailable) as error:
            # The library shows a Sign In banner; a notification is only for
            # something the person just asked for.
            if not quiet or not self.window:
                self.notify_error(f"Sign in to {source.name} again. {error}")
        except SubsonicError as error:
            self.notify_error(f"Couldn’t sync {source.name}. {error}")
        except KeyError:
            pass  # removed while it synced
        except Exception:
            log.exception("Syncing remote source %s failed", source.id)
            with contextlib.suppress(KeyError):
                self.store.set_source_state(source.id, SourceState.ERROR)
            self.notify_error(f"Couldn’t sync {source.name}.")
        else:
            self.controller.refresh_metadata()
        self.remote.pump_downloads(self._downloads_changed)
        if self.window:
            self.window.set_status(None)
            self.window.library_changed(soon=True)

    def _network_changed(self, _monitor: Gio.NetworkMonitor, _available: bool) -> None:
        if self._network_timer:
            GLib.source_remove(self._network_timer)
        self._network_timer = GLib.timeout_add_seconds(2, self._network_settled)

    def _network_settled(self) -> bool:
        self._network_timer = 0
        available = Gio.NetworkMonitor.get_default().get_network_available()
        for source in self.remote.sources():
            if not available:
                self.remote.mark_offline(source.id)
            elif not self.remote.connected(source.id) and source.state is not SourceState.AUTH_REQUIRED:
                self.sync_remote(source, quiet=True)
        if self.window:
            self.window.refresh_library()
        return GLib.SOURCE_REMOVE

    def _playback_for_sources(self, snapshot: PlaybackSnapshot) -> None:
        # A stream that fails may mean its server went away: check, so the
        # library shows the truth instead of offering songs that cannot play.
        copy = snapshot.copy
        if (snapshot.state is PlaybackState.ERROR and copy is not None and not copy.here
                and copy.source_id not in self._checking and self.remote.connected(copy.source_id)):
            self._checking.add(copy.source_id)

            def checked(_future: concurrent.futures.Future) -> None:
                self._checking.discard(copy.source_id)
                if self.window:
                    self.window.refresh_library()

            GLib.idle_add(lambda: _on_main(self.remote.connect(copy.source_id), checked) or GLib.SOURCE_REMOVE)

    def can_make_offline(self, track_ids: list[str]) -> bool:
        return bool(self.store.offline_candidates(track_ids))

    def has_offline(self, track_ids: list[str]) -> bool:
        return any(
            copy.offline_state is not None
            for track_id in track_ids for copy in self.store.copies_for_track(track_id)
        )

    def make_offline(self, track_ids: list[str]) -> None:
        requested = self.remote.request_offline(track_ids, self._downloads_changed)
        if requested:
            self._download_notice_tracks = getattr(self, "_download_notice_tracks", set()) | set(track_ids)
            self._download_notice("Download queued", "Music is being saved for offline playback.")
        else:
            self._download_notice("No download needed", "This music is already on this device or already queued.")
        if self.window:
            self.window.refresh_offline_state()

    def remove_offline(self, track_ids: list[str]) -> None:
        self.remote.remove_offline(track_ids)
        if self.window:
            self.window.refresh_library()
            self.window.refresh_offline_state()

    def retry_downloads(self, source: Source) -> None:
        failed = self.store.offline_items(states=(OfflineState.FAILED,), source_id=source.id)
        self.store.request_offline(item.copy_id for item in failed)
        self.remote.pump_downloads(self._downloads_changed)
        if self.window:
            self.window.refresh_offline_state()

    def confirm_remove_downloads(self, source: Source) -> None:
        if not self.window:
            return
        summary = self.store.offline_summary(source.id)
        dialog = Adw.AlertDialog(
            heading="Remove Downloads?",
            body=f"{_songs(summary.complete)} ({GLib.format_size(summary.bytes)}) downloaded from "
                 f"{source.name} will be deleted from this device, and waiting downloads cancelled. "
                 "You can still stream them while the server is in reach.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove Downloads")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: response == "remove" and self.remove_offline(
            [item.track_id for item in self.store.offline_items(source_id=source.id)]
        ))
        dialog.present(self.window)

    def confirm_remove_source(self, source: Source) -> None:
        if not self.window:
            return
        songs = len(self.store.tracks(source_id=source.id))
        summary = self.store.offline_summary(source.id)
        downloads = (
            f" deletes {_songs(summary.complete)} ({GLib.format_size(summary.bytes)}) downloaded to this device,"
            if summary.complete else ""
        )
        dialog = Adw.AlertDialog(
            heading=f"Remove {source.name}?",
            body=f"Tide forgets the {_songs(songs)} it lists from this server,{downloads} and "
                 "removes the saved password from the keyring. Songs in your playlists stay "
                 "there as unavailable. Nothing on the server changes.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove Server")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: response == "remove" and self._remove_source(source))
        dialog.present(self.window)

    def _remove_source(self, source: Source) -> None:
        current = self.controller.snapshot.copy
        if current is not None and current.source_id == source.id:
            self.controller.stop()

        def removed(future: concurrent.futures.Future) -> None:
            try:
                future.result()
            except CredentialUnavailable as error:
                self.notify_error(f"{source.name} was removed, but its password is still in the keyring. {error}")
            except Exception:
                self.notify_error(f"Couldn’t remove {source.name}.")
            self.controller.reload_queue()
            if self.window:
                self.window.refresh_library()

        _on_main(self.remote.remove_source(source.id), removed)

    def _downloads_changed(self) -> None:
        GLib.idle_add(self._schedule_download_refresh)

    def _schedule_download_refresh(self) -> bool:
        if not self._download_refresh:
            self._download_refresh = GLib.timeout_add(700, self._refresh_downloads)
        return GLib.SOURCE_REMOVE

    def _download_notice(self, title: str, body: str) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.send_notification("downloads", notification)

    def _refresh_downloads(self) -> bool:
        self._download_refresh = 0
        requested = getattr(self, "_download_notice_tracks", set())
        states = [copy.offline_state for track_id in requested
                  for copy in self.store.copies_for_track(track_id) if copy.offline_state is not None]
        if requested and not any(state in (OfflineState.QUEUED, OfflineState.DOWNLOADING) for state in states):
            failed = OfflineState.FAILED in states
            self._download_notice("Downloads need attention" if failed else "Download complete",
                                  "Open Sources to retry failed downloads." if failed else "Your music is available offline in Tide.")
            self._download_notice_tracks = set()
        if self.window:
            if self.store.offline_summary().pending:
                self.window.refresh_offline_state()
            else:
                self.window.refresh_library()
                self.window.refresh_offline_state()
        return GLib.SOURCE_REMOVE

    def notify_error(self, message: str) -> None:
        notification = Gio.Notification.new("Tide needs attention")
        notification.set_body(message)
        notification.set_priority(Gio.NotificationPriority.NORMAL)
        self.send_notification("playback-error", notification)

    def _show_about(self) -> None:
        if self.window:
            Adw.AboutDialog(
                application_name="Tide",
                application_icon=APP_ID,
                version="0.1.0",
                developer_name="Project Luma",
                license_type=Gtk.License.APACHE_2_0,
                comments="A source-aware native music library and player.",
            ).present(self.window)

    def _show_shortcuts(self) -> None:
        if not self.window:
            return
        overlay = Gtk.ShortcutsWindow(transient_for=self.window, modal=True)
        section = Gtk.ShortcutsSection(section_name="general", title="Tide")
        group = Gtk.ShortcutsGroup(title="Playback and library")
        for title, accelerator in (
            ("Play or pause", "space"), ("Next", "<Primary>Right"),
            ("Previous", "<Primary>Left"), ("Search", "<Primary>f"),
            ("Add music", "<Primary>o"), ("New playlist", "<Primary>n"),
        ):
            group.add_shortcut(Gtk.ShortcutsShortcut(title=title, accelerator=accelerator))
        section.add_group(group)
        overlay.add_section(section)
        overlay.present()

    def _quit(self) -> None:
        self.controller.stop()
        self.quit()

    def do_shutdown(self) -> None:
        if self.window:
            self.window.close_resources()
        if self.mpris:
            self.mpris.close()
        for monitor in self.monitors.values():
            monitor.close()
        if self._network_handler:
            Gio.NetworkMonitor.get_default().disconnect(self._network_handler)
        for timer in (self._network_timer, self._download_refresh):
            if timer:
                GLib.source_remove(timer)
        self.indexer.close()
        self.remote.close()
        self.controller.close()
        self.store.close()
        Adw.Application.do_shutdown(self)


def main(argv: list[str] | None = None, *, application_id: str = APP_ID) -> int:
    # A D-Bus-activated service has no terminal; journald stamps each line, so
    # the message alone is logged, at INFO, to stderr.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return TideApplication(application_id=application_id).run(argv)
