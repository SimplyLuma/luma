# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sqlite3
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402
from luma_appkit import IslandSplitView

try:
    gi.require_version("LumaSemantics", "1")
    from gi.repository import LumaSemantics  # type: ignore[attr-defined]
except (ImportError, ValueError):
    LumaSemantics = None

try:
    from luma_semantic_broker.client import SemanticPublisher
except (ImportError, ValueError):
    SemanticPublisher = None

from luma_appkit import (
    AppWindow, Command, CommandGroup, CommandRegistry, ConnectedButtonGroup, EmptyState, ListEmptyState,
    InlineRenameField, Island, command_actions, command_menu, command_popover,
    install_appkit, command_menu_model, add_style_sheet,
)
from .connect_notes import keep_other_copy
from .notes_backend import Folder, Note, NotesStore, _clean_note_title, _content
from .notes_editor import NotesFormattingToolbar
from .notes_export import export_markdown
from .notes_richtext import BLOCK_STYLES, INLINE_STYLES, NotePicture, RichTextPage
from .notes_fixture import source_from_environment


def _point_rectangle(x: float, y: float) -> Gdk.Rectangle:
    """A 1x1 rectangle at a point. Gdk.Rectangle(x, y, 1, 1) silently ignores its arguments."""
    rectangle = Gdk.Rectangle()
    rectangle.x, rectangle.y, rectangle.width, rectangle.height = int(x), int(y), 1, 1
    return rectangle


APP_ID = "org.projectluma.Notes"
ICON_NAME = APP_ID
# Saving is local and cheap, so it follows typing closely: once typing pauses,
# or at the latest every few seconds while it goes on. Syncing is paced
# separately and never per keystroke.
AUTOSAVE_DELAY_MS = 700
AUTOSAVE_MAX_WAIT_MS = 4000
# A change written to the library elsewhere (sync) is looked at this long
# after the first sign of it, so one sync pass is one refresh.
STORE_SETTLE_MS = 250
# Where the folder cannot be watched (no inotify instance left), the library
# is asked this often instead; asking is one PRAGMA, and writes nothing.
STORE_POLL_SECONDS = 2
SIDEBAR_WIDTH = 178
PRIMARY_ISLAND_GAP = 9
UNTITLED = "Untitled page"
PIN_ICON = "luma-pin-symbolic"


def _double_click_ms() -> int:
    settings = Gtk.Settings.get_default()
    value = settings.get_property("gtk-double-click-time") if settings is not None else 400
    return max(150, min(int(value or 400), 400))


class NoteRow(Gtk.ListBoxRow):
    def __init__(self, note: Note, window: "NotesWindow", *, nested: bool = False) -> None:
        super().__init__()
        self.note = note
        self.window = window
        self.add_css_class("notes-page-row")
        if nested:
            self.add_css_class("nested")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("notes-row-title")
        self.title_stack = Gtk.Stack()
        self.title_stack.add_named(self.title, "label")
        box.append(self.title_stack)
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.pin = Gtk.Image(icon_name=PIN_ICON, pixel_size=11)
        self.pin.add_css_class("notes-favorite-mark")
        meta.append(self.pin)
        self.stamp = Gtk.Label()
        self.stamp.add_css_class("notes-row-meta")
        meta.append(self.stamp)
        self.preview = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.preview.add_css_class("notes-row-preview")
        meta.append(self.preview)
        box.append(meta)
        self.set_child(box)
        self.update(note)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._context_menu)
        self.add_controller(click)
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._drag_prepare)
        source.connect("drag-begin", lambda source, _drag: self.window._sidebar_drag_begin(self, source))
        source.connect("drag-end", lambda *_: self.window._sidebar_drag_end(self))
        self.add_controller(source)
        # Double-click anywhere on the row renames it. The first click has
        # already opened the page without rebuilding the list, so this row is
        # still the one under the pointer when the second click lands.
        rename_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        rename_click.connect("pressed", lambda _g, count, _x, _y: self.begin_rename() if count == 2 else None)
        self.add_controller(rename_click)

    def update(self, note: Note) -> None:
        self.note = note
        self.title.set_label(note.display_title)
        self.pin.set_visible(note.favorite)
        self.stamp.set_label(_relative_date(note.modified_at))
        self.preview.set_label(note.preview)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"{note.display_title}. {'Pinned. ' if note.favorite else ''}"
             f"{_relative_date(note.modified_at)}. {note.preview}"],
        )

    def begin_rename(self) -> None:
        if existing := self.title_stack.get_child_by_name("rename"):
            self.title_stack.remove(existing)
        field = InlineRenameField(
            self.note.title,
            commit=lambda value: self.window._finish_note_rename(self.note, value),
            cancel=lambda: self.title_stack.set_visible_child_name("label"),
        )
        self.title_stack.add_named(field, "rename")
        self.title_stack.set_visible_child_name("rename")
        GLib.idle_add(lambda: (field.begin(), GLib.SOURCE_REMOVE)[1])

    def _drag_prepare(self, *_arguments) -> Gdk.ContentProvider:
        value = GObject.Value()
        value.init(GObject.TYPE_STRING)
        value.set_string(f"note:{self.note.id}")
        return Gdk.ContentProvider.new_for_value(value)

    def _context_menu(self, gesture: Gtk.GestureClick, _press: int, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.window.show_menu(self.window.note_commands(self.note), self, x, y)

    def _drag_enter(self, target: Gtk.DropTarget, x: float, y: float) -> Gdk.DragAction:
        return self.window._sidebar_drag_motion(self, x, y)

    def _drag_leave(self, _target: Gtk.DropTarget) -> None:
        self.window._clear_drop_hint()

    def _drop(self, _target: Gtk.DropTarget, value: object, x: float, y: float) -> bool:
        return self.window._sidebar_drop(self, value, x, y)


class FolderRow(Gtk.ListBoxRow):
    def __init__(self, folder: Folder, window: "NotesWindow") -> None:
        super().__init__()
        self.folder = folder
        self.window = window
        self.add_css_class("notes-folder-row")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Image(icon_name="luma-folder-symbolic", pixel_size=14)
        icon.add_css_class("notes-folder-icon")
        line.append(icon)
        self.label = Gtk.Label(label=folder.name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.label.add_css_class("notes-folder-label")
        self.label_stack = Gtk.Stack(hexpand=True)
        self.label_stack.add_named(self.label, "label")
        line.append(self.label_stack)
        if folder.favorite:
            pin = Gtk.Image(icon_name=PIN_ICON, pixel_size=11)
            pin.add_css_class("notes-favorite-mark")
            line.append(pin)
        count = len(window.store.list_notes(folder_id=folder.id))
        if count:
            tally = Gtk.Label(label=str(count))
            tally.add_css_class("notes-folder-count")
            line.append(tally)
        self.set_child(line)
        self.update_state([Gtk.AccessibleState.EXPANDED], [int(folder.expanded)])
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._context_menu)
        self.add_controller(click)
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._drag_prepare)
        source.connect("drag-begin", lambda source, _drag: self.window._sidebar_drag_begin(self, source))
        source.connect("drag-end", lambda *_: self.window._sidebar_drag_end(self))
        self.add_controller(source)
        # A double-click renames. The single click that opens and closes the
        # folder waits out the double-click time, so a double-click never
        # opens or closes it on the way to renaming it.
        rename_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        rename_click.connect("pressed", self._pressed)
        self.add_controller(rename_click)

    def _pressed(self, _gesture, count: int, _x: float, _y: float) -> None:
        if count == 2:
            self.window.cancel_folder_toggle()
            self.begin_rename()

    def begin_rename(self) -> None:
        if existing := self.label_stack.get_child_by_name("rename"):
            self.label_stack.remove(existing)
        field = InlineRenameField(
            self.folder.name,
            commit=lambda value: self.window._finish_folder_rename(self.folder, value),
            cancel=lambda: self.label_stack.set_visible_child_name("label"),
        )
        self.label_stack.add_named(field, "rename")
        self.label_stack.set_visible_child_name("rename")
        GLib.idle_add(lambda: (field.begin(), GLib.SOURCE_REMOVE)[1])

    def is_renaming(self) -> bool:
        return self.label_stack.get_visible_child_name() == "rename"

    def _drag_prepare(self, *_arguments) -> Gdk.ContentProvider:
        value = GObject.Value()
        value.init(GObject.TYPE_STRING)
        value.set_string(f"folder:{self.folder.id}")
        return Gdk.ContentProvider.new_for_value(value)

    def _drag_enter(self, target: Gtk.DropTarget, x: float, y: float) -> Gdk.DragAction:
        return self.window._sidebar_drag_motion(self, x, y)

    def _drop(self, _target: Gtk.DropTarget, value: object, x: float, y: float) -> bool:
        return self.window._sidebar_drop(self, value, x, y)

    def _context_menu(self, gesture: Gtk.GestureClick, _press: int, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.window.show_menu(self.window.folder_commands(self.folder), self, x, y)


class RootDropBoundary(Gtk.ListBoxRow):
    """An explicit root insertion target after an expanded folder or the list."""
    def __init__(self, window: "NotesWindow", after: str | None = None) -> None:
        super().__init__(selectable=False, activatable=False)
        self.set_focusable(False)
        self.root_after = after
        self.add_css_class("notes-root-boundary")
        self.set_child(Gtk.Box(height_request=8 if after else 24))
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Drop outside folder" if after else "Drop at end of list"])


class NotesWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self.store = source_from_environment()
        self.fixture = bool(getattr(self.store, "fixture", False))
        self.semantic_root = None
        self.semantic_publisher = None
        self.current: Note | None = None
        self.current_folder: str | None = None
        self.save_source = 0
        self._dirty_since = 0
        self._saved_state: tuple | None = None
        self.loading = False
        self.last_deleted_id: str | None = None
        self.search_visible = False
        self.note_rows: dict[str, NoteRow] = {}
        self.folder_rows: dict[str, FolderRow] = {}
        self.folder_children: dict[str, list[Gtk.ListBoxRow]] = {}
        self._renaming = False
        self._sidebar_reload_source = 0
        self._folder_toggle_source = 0
        self._store_monitor: Gio.FileMonitor | None = None
        self._store_check_source = 0
        self._store_poll_source = 0
        # "monitor" (the library's folder is watched) or "poll" (it could not be).
        self.store_watch = ""
        self._store_version = 0
        self._sidebar_seen: tuple | None = None
        # How many times a change written elsewhere was taken into this window.
        self.external_refreshes = 0
        commands = self._application_commands()
        super().__init__(
            application=application,
            app_id=APP_ID + ".Fixture" if self.fixture else APP_ID,
            title="Notes",
            icon_name=ICON_NAME,
            commands=commands,
            default_width=820,
            default_height=560,
            minimum_width=360,
            minimum_height=380,
        )
        self.add_css_class("luma-notes")
        self.toast_overlay = Adw.ToastOverlay()
        self.split = IslandSplitView(collapsed=False, show_content=False)
        self.split.set_sidebar_width_unit(Adw.LengthUnit.PX)
        self.split.set_min_sidebar_width(SIDEBAR_WIDTH + PRIMARY_ISLAND_GAP)
        self.split.set_max_sidebar_width(SIDEBAR_WIDTH + PRIMARY_ISLAND_GAP)
        sidebar = self._build_sidebar()
        sidebar.set_margin_end(PRIMARY_ISLAND_GAP)
        self._build_layout(sidebar, self._build_editor())
        self._install_keys()
        self._reload_sidebar()
        first = (self.store.get_note(os.environ.get("LUMA_NOTES_SELECTED", self.store.selected))
                 if self.fixture else next(iter(self.store.list_notes()), None))
        if first is not None:
            self._open_note(first)
        else:
            self._show_empty()
        self._refresh_semantics()
        if not self.fixture:
            self._watch_store()
            self.semantic_publisher = self._create_semantic_publisher(application)
        self.connect("close-request", self._close_notes)
        # Leaving the window writes what is pending, so another app (or a
        # sync) never reads a page that is a few keystrokes behind.
        self.connect("notify::is-active", lambda *_: None if self.is_active() else self._flush_save())

    def _build_layout(self, sidebar, editor):
        self.split.set_sidebar(Adw.NavigationPage(child=sidebar, title="Notes"))
        self.split.set_content(Adw.NavigationPage(child=editor, title="Page"))
        self.toast_overlay.set_child(self.split)
        self.set_body(self.toast_overlay)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560px"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)

    def _save_state(self):
        # A fixture never persists window state alongside a real Notes window.
        if not self.fixture:
            super()._save_state()

    def _semantic_application(self):
        if LumaSemantics is None:
            return None
        root = LumaSemantics.SemanticObject.new("notes", "application", "Notes")
        root.set_privacy(LumaSemantics.Privacy.PUBLIC)
        root.add_action(LumaSemantics.Action.new("notes.create", "Create note"))
        for note in self.store.list_notes():
            semantic_note = LumaSemantics.SemanticObject.new(
                f"note:{note.id}", "document", note.display_title
            )
            save = LumaSemantics.Action.new("notes.save", "Save note")
            save.set_parameter_type("s")
            semantic_note.add_action(save)
            delete = LumaSemantics.Action.new("notes.delete", "Move note to Trash")
            delete.set_risk(LumaSemantics.ActionRisk.DESTRUCTIVE)
            semantic_note.add_action(delete)
            root.add_child(semantic_note)
        return root

    def _create_semantic_publisher(self, application: Adw.Application):
        if SemanticPublisher is None or self.semantic_root is None:
            return None
        try:
            return SemanticPublisher(
                application,
                APP_ID,
                "notes",
                lambda: self.semantic_root,
                {
                    "notes.create": self._semantic_create,
                    "notes.save": self._semantic_save,
                    "notes.delete": self._semantic_delete,
                },
            )
        except (GLib.Error, OSError, RuntimeError, TypeError, ValueError):
            return None

    def _refresh_semantics(self) -> None:
        if self.fixture:
            return
        self.semantic_root = self._semantic_application()
        if self.semantic_publisher is not None:
            GLib.idle_add(self.semantic_publisher.update)

    def _semantic_note(self, object_id: str) -> Note:
        if not object_id.startswith("note:"):
            raise ValueError("semantic target is not a note")
        try:
            return self.store.get_note(object_id.removeprefix("note:"))
        except KeyError as error:
            raise ValueError("semantic note no longer exists") from error

    def _semantic_create(self, object_id: str, _parameter):
        if object_id != "notes":
            raise ValueError("create action belongs to the Notes application")
        self.create_note()
        if self.current is None:
            raise RuntimeError("note creation did not produce a current note")
        return {"object_id": f"note:{self.current.id}"}

    def _semantic_save(self, object_id: str, parameter):
        if not isinstance(parameter, str):
            raise ValueError("save action requires the complete note text")
        note = self._semantic_note(object_id)
        if not self._can_edit_note_content(note.id):
            raise PermissionError("This shared note is read-only")
        saved = self.store.update_note(
            note.id, title=note.title, body=parameter, runs=()
        )
        if self.current and self.current.id == note.id:
            self._open_note(saved)
        self._refresh_semantics()
        return {"object_id": object_id, "saved": True}

    def _semantic_delete(self, object_id: str, _parameter):
        note = self._semantic_note(object_id)
        self._delete_specific(note)
        self._refresh_semantics()
        return {"object_id": object_id, "deleted": True}

    def _application_commands(self) -> CommandRegistry:
        return CommandRegistry((
            CommandGroup(None, (
                Command("notes.new", "New page", self.create_note, "document-new-symbolic", shortcut=("Ctrl", "N")),
                Command("folders.new", "New folder", self.create_folder, "folder-new-symbolic", shortcut=("Ctrl", "Shift", "N")),
                Command("notes.search", "Search", self.toggle_search, "system-search-symbolic", shortcut=("Ctrl", "F")),
            ), quick_actions=True),
            CommandGroup("NOTES", (
                Command("notes.about", "About Notes", self._show_about, "help-about-symbolic"),
                Command("notes.quit", "Quit Notes", self.close, "log-out", shortcut=("Ctrl", "Q")),
            )),
        ))

    def _build_sidebar(self) -> Gtk.Widget:
        island = Island()
        island.add_css_class("notes-sidebar")
        island.set_size_request(SIDEBAR_WIDTH, -1)

        self.search = Gtk.SearchEntry(placeholder_text="Search notes")
        self.search.add_css_class("notes-search")
        self.search.set_visible(False)
        self.search.connect("search-changed", lambda *_: self._reload_sidebar())
        island.append(self.search)

        self.sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, vexpand=True)
        self.sidebar_list.add_css_class("notes-sidebar-list")
        self.sidebar_list.connect("row-activated", self._row_activated)
        blank_menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        blank_menu.connect("pressed", self._sidebar_context)
        self.sidebar_list.add_controller(blank_menu)
        root_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        root_target.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        root_target.connect("enter", self._root_drag_motion)
        root_target.connect("motion", self._root_drag_motion)
        root_target.connect("leave", lambda *_: self._clear_drop_hint())
        root_target.connect("drop", self._drop_to_root)
        self.sidebar_list.add_controller(root_target)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.sidebar_list)
        self.sidebar_scroll = scroll
        island.append(scroll)
        create = Gtk.Button()
        create.add_css_class("notes-new-button")
        create.update_property([Gtk.AccessibleProperty.LABEL], ["New note"])
        create_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        create_line.append(Gtk.Image(icon_name="luma-plus-symbolic", pixel_size=14))
        create_line.append(Gtk.Label(label="New note"))
        create.set_child(create_line)
        create.connect("clicked", lambda *_: self.create_note())
        island.append(create)
        return island

    def _build_editor(self) -> Gtk.Widget:
        island = Island()
        island.add_css_class("notes-editor-island")
        island.set_hexpand(True)
        toolbar = NotesFormattingToolbar(self.context, self._toggle_format, self._show_link_popover)
        self.format_toolbar = toolbar
        self.format_buttons = toolbar.format_buttons
        self.save_status = toolbar.save_status
        island.append(toolbar)

        canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        canvas.add_css_class("notes-canvas")
        # The title is one line. Its placeholder is only a placeholder: dim,
        # never text, and gone with the first letter typed.
        self.title_entry = Gtk.Entry(placeholder_text=UNTITLED)
        self.title_entry.set_property("truncate-multiline", True)
        self.title_entry.add_css_class("notes-editor-title")
        self.title_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Page title"])
        self.title_entry.connect("changed", self._content_changed)
        self.title_entry.connect("activate", lambda *_: self.focus_body_start())
        title_keys = Gtk.EventControllerKey()
        title_keys.connect("key-pressed", self._title_key)
        self.title_entry.add_controller(title_keys)
        canvas.append(self.title_entry)

        self.page = RichTextPage(
            on_changed=self._content_changed,
            on_caret=self._refresh_format_buttons,
            picture_commands=self._show_picture_bar,
            on_problem=lambda message: self.toast_overlay.add_toast(Adw.Toast(title=message)),
        )
        self.page.on_picture_menu = self._picture_menu
        if self.fixture:
            self.page.picture_paths = self.store.picture_paths
        else:
            self.page.watch_sync_status()
        self.buffer = self.page.buffer
        self.editor = self.page.view
        self.picture_bar: Gtk.Popover | None = None
        appearance = Adw.StyleManager.get_default()
        appearance.connect("notify::dark", self._appearance_changed)
        GLib.idle_add(self._sync_editor_tag_colors)
        # The text view already has the toolkit's menu — cut, copy, paste,
        # delete, select all, each enabled when it applies. Notes adds its own
        # commands to that menu rather than opening a second one over it.
        editor_commands = CommandRegistry((CommandGroup(None, (
            Command("editor.link", "Add link…", lambda: self._show_link_popover(self.editor), "insert-link-symbolic"),
            Command("editor.picture", "Add picture…", self._choose_picture_file, "image-x-generic-symbolic"),
        )),))
        self.editor.insert_action_group("editor", command_actions(editor_commands))
        self.editor.set_extra_menu(command_menu(editor_commands, prefix="editor"))
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class("notes-body-scroll")
        scroll.set_child(self.editor)
        self.body_scroll = scroll
        canvas.append(scroll)
        self.canvas = canvas
        island.append(canvas)
        self.empty_state = EmptyState("No notes yet",
            "Everything you write is saved as you go. Start with a page.",
            "luma-empty-notes-symbolic", primary=("New note", self.create_note))
        island.append(self.empty_state)
        self.empty_state.set_visible(False)
        return island

    # ----------------------------------------------------------- title

    def focus_body_start(self) -> None:
        """Enter in the title goes to the start of the page, as in every editor."""
        self.editor.grab_focus()
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.editor.scroll_mark_onscreen(self.buffer.get_insert())

    def _title_key(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Down and not state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK):
            self.focus_body_start()
            return True
        return False

    # --------------------------------------------------------- sidebar

    def _show_empty(self) -> None:
        self.current = None
        self.current_folder = None
        self.format_toolbar.set_visible(False)
        self.canvas.set_visible(False)
        self.empty_state.set_visible(True)

    def _reload_sidebar(self, select_id: str | None = None) -> None:
        self._clear_drop_hint()
        while child := self.sidebar_list.get_first_child():
            self.sidebar_list.remove(child)
        self.note_rows = {}
        self.folder_rows = {}
        self.folder_children = {}
        needle = self.search.get_text() if hasattr(self, "search") else ""
        selected = select_id or (self.current.id if self.current else None)
        if needle:
            for note in self.store.list_notes(search=needle):
                self._append_note_row(note, nested=False, selected=selected)
            return
        if not self.store.list_notes() and not self.store.list_folders():
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            row.set_child(ListEmptyState("No notes yet"))
            self.sidebar_list.append(row)
        root_notes = self.store.list_notes(folder_id="__root__")
        for item in self.store.root_items():
            if isinstance(item, Note):
                ids = self.store.descendants(item.id) | {item.id}
                self._append_note_tree(tuple(n for n in root_notes if n.id in ids), 0, selected)
                continue
            folder = item
            row = FolderRow(folder, self)
            self.folder_rows[folder.id] = row
            self.sidebar_list.append(row)
            children: list[Gtk.ListBoxRow] = []
            notes = self.store.list_notes(folder_id=folder.id)
            children.extend(self._append_note_tree(notes, 1, selected))
            if not notes:
                hint = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
                hint.set_markup('Empty. <a href="notes.new">New note</a>')
                hint.add_css_class("notes-folder-empty")
                hint.connect("activate-link", lambda _label, _uri, target=folder: (self._new_in_folder(target), True)[1])
                empty_row = Gtk.ListBoxRow(selectable=False, activatable=False)
                empty_row.add_css_class("notes-empty-folder-row")
                empty_row.set_child(hint)
                self.sidebar_list.append(empty_row)
                children.append(empty_row)
            boundary = RootDropBoundary(self, "folder:" + folder.id)
            self.sidebar_list.append(boundary)
            children.append(boundary)
            for child in children:
                child.set_visible(folder.expanded)
            self.folder_children[folder.id] = children
        self.root_end = RootDropBoundary(self)
        self.sidebar_list.append(self.root_end)

    def _append_note_tree(self, notes: tuple[Note, ...], depth: int, selected: str | None) -> list[NoteRow]:
        parents = self.store.note_parents()
        ids = {note.id for note in notes}
        children: dict[str | None, list[Note]] = {}
        for note in notes:
            parent = parents.get(note.id)
            children.setdefault(parent if parent in ids else None, []).append(note)
        rows: list[NoteRow] = []
        def append(parent: str | None, level: int) -> None:
            for note in children.get(parent, ()):
                row = self._append_note_row(note, nested=False, selected=selected)
                row.get_child().set_margin_start(min(level, 5) * 20)
                rows.append(row)
                append(note.id, level + 1)
        append(None, depth)
        return rows

    def _append_note_row(self, note: Note, *, nested: bool, selected: str | None) -> NoteRow:
        row = NoteRow(note, self, nested=nested)
        self.note_rows[note.id] = row
        self.sidebar_list.append(row)
        if note.id == selected:
            self.sidebar_list.select_row(row)
        return row

    def _select_row_for(self, note_id: str) -> None:
        row = self.note_rows.get(note_id)
        if row is None:
            self._reload_sidebar(select_id=note_id)
            return
        if not row.get_visible() and row.note.folder_id in self.folder_rows:
            self._set_folder_expanded(row.note.folder_id, True)
        self.sidebar_list.select_row(row)

    def _row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if hasattr(row, "note"):
            self._open_note(row.note)
        elif hasattr(row, "folder"):
            self.current_folder = row.folder.id
            self.cancel_folder_toggle()
            self._folder_toggle_source = GLib.timeout_add(
                _double_click_ms(), self._toggle_folder_now, row.folder.id)

    def cancel_folder_toggle(self) -> None:
        if self._folder_toggle_source:
            GLib.source_remove(self._folder_toggle_source)
            self._folder_toggle_source = 0

    def _toggle_folder_now(self, folder_id: str) -> bool:
        self._folder_toggle_source = 0
        row = self.folder_rows.get(folder_id)
        if row is not None and not row.is_renaming():
            self._set_folder_expanded(folder_id, not row.folder.expanded)
        return GLib.SOURCE_REMOVE

    def _set_folder_expanded(self, folder_id: str, expanded: bool) -> None:
        """Open or close a folder in place; the rows stay the same rows."""
        try:
            folder = self.store.set_folder_expanded(folder_id, expanded)
        except KeyError:
            return
        row = self.folder_rows.get(folder_id)
        if row is not None:
            row.folder = folder
            row.update_state([Gtk.AccessibleState.EXPANDED], [int(expanded)])
        for child in self.folder_children.get(folder_id, ()):
            child.set_visible(expanded)

    # ----------------------------------------------------------- pages

    def _open_note(self, note: Note) -> None:
        self._flush_save()
        self.loading = True
        try:
            self.current = self.store.get_note(note.id)
            self.empty_state.set_visible(False)
            self.format_toolbar.set_visible(True)
            self.canvas.set_visible(True)
            self.current_folder = self.current.folder_id
            # A title that is only the placeholder's words is no title.
            title = "" if self.current.title == UNTITLED else self.current.title
            self.title_entry.set_text(title)
            self.page.load(self.current.body, self.current.runs)
            self._sync_editor_tag_colors()
        finally:
            self.loading = False
        self._saved_state = self._page_state()
        self.save_status.saved(_last_saved_label(self.current.modified_at))
        self.split.set_show_content(True)
        self._select_row_for(note.id)
        self._refresh_format_buttons()

    def create_note(self) -> None:
        self._flush_save()
        note = self.store.create_note(folder_id=self.current_folder)
        self._reload_sidebar(select_id=note.id)
        self._open_note(note)
        self._refresh_semantics()
        self.title_entry.grab_focus()

    def create_folder(self) -> None:
        folder = self.store.create_folder()
        self._reload_sidebar()
        GLib.idle_add(self._begin_folder_rename, folder.id)

    def duplicate_note(self) -> None:
        if self.current is None:
            return
        self._duplicate_specific(self.current)

    def _duplicate_specific(self, source: Note) -> None:
        if self.current and self.current.id == source.id:
            self._flush_save()
            source = self.store.get_note(source.id)
        duplicate = self.store.create_note(
            folder_id=source.folder_id,
            title=f"{source.title} copy" if source.title else "",
        )
        duplicate = self.store.update_note(
            duplicate.id,
            title=duplicate.title,
            body=source.body,
            runs=source.runs,
        )
        self._reload_sidebar(select_id=duplicate.id)
        self._open_note(duplicate)
        self._refresh_semantics()

    def delete_current(self) -> None:
        if self.current is None:
            return
        self._delete_specific(self.current)

    def _delete_specific(self, note: Note) -> None:
        """Move a page to Recently Deleted, with Undo; nothing is destroyed."""
        was_current = self.current is not None and self.current.id == note.id
        if was_current:
            self._flush_save()
        try:
            self.store.soft_delete_note(note.id)
        except KeyError:
            return
        self.last_deleted_id = note.id
        if was_current:
            self.current = None
            self._reload_sidebar()
            replacement = next(iter(self.store.list_notes()), None)
            if replacement:
                self._open_note(replacement)
            else:
                self.loading = True
                self.title_entry.set_text("")
                self.page.clear()
                self.loading = False
                self._show_empty()
        else:
            self._reload_sidebar()
        toast = Adw.Toast(title=f"Deleted “{note.display_title}”", button_label="Undo", timeout=6)
        toast.connect("button-clicked", lambda *_: self._undo_delete(note.id))
        self.toast_overlay.add_toast(toast)
        self._refresh_semantics()

    def _undo_delete(self, note_id: str) -> None:
        try:
            note = self.store.restore_note(note_id)
        except KeyError:
            return
        self._reload_sidebar(select_id=note.id)
        self._open_note(note)
        self._refresh_semantics()

    def _sidebar_drag_begin(self, row: Gtk.ListBoxRow, source: Gtk.DragSource) -> None:
        self.cancel_folder_toggle()
        self._drag_value = (f"note:{row.note.id}" if hasattr(row, "note")
                            else f"folder:{row.folder.id}")
        source.set_icon(Gtk.WidgetPaintable.new(row.get_child()), 0, 0)
        row.add_css_class("dragging")
        self.sidebar_list.add_css_class("sidebar-dragging")

    def _sidebar_drag_end(self, row: Gtk.ListBoxRow) -> None:
        self._drag_value = None
        self._clear_drop_hint()
        row.remove_css_class("dragging")
        self.sidebar_list.remove_css_class("sidebar-dragging")

    def _clear_drop_hint(self) -> None:
        row = getattr(self, "_drop_hint_row", None)
        if row is not None:
            for name in ("drop-before", "drop-after", "drop-into"):
                row.remove_css_class(name)
        self._drop_hint_row = None

    def _root_plan(self, kind: str, item_id: str, anchor: str | None, after: bool) -> tuple:
        key = kind + ":" + item_id
        items = [self.store.root_key(item) for item in self.store.root_items()]
        if anchor == key:
            raise ValueError("Cannot drop an item on itself")
        items = [item for item in items if item != key]
        before = anchor
        if anchor is None:
            before = None
        elif after:
            index = items.index(anchor)
            before = items[index + 1] if index + 1 < len(items) else None
        return ("after" if after else "before", "root-" + kind, item_id, None, anchor, before)

    def _root_owner(self, row: Gtk.ListBoxRow) -> str:
        if hasattr(row, "folder"):
            return "folder:" + row.folder.id
        if row.note.folder_id is not None:
            return "folder:" + row.note.folder_id
        owner = row.note.id
        while parent := self.store.parent_of(owner):
            owner = parent
        return "note:" + owner

    def _sidebar_drop_plan(self, row: Gtk.ListBoxRow, value: object, y: float,
                           x: float | None = None) -> tuple | None:
        """Resolve one scope and position for both the indicator and the drop."""
        if not isinstance(value, str) or ":" not in value:
            return None
        kind, item_id = value.split(":", 1)
        gap = 4 if row.has_css_class("drop-before") or row.has_css_class("drop-after") else 0
        fraction = y / max(1, row.get_height() - gap)
        try:
            if kind == "folder":
                self.store.get_folder(item_id)
            elif kind == "note":
                if self.store.get_note(item_id).deleted_at is not None:
                    return None
            else:
                return None
            if isinstance(row, RootDropBoundary):
                return self._root_plan(kind, item_id, row.root_after, True)
            if kind == "folder":
                return self._root_plan(kind, item_id, self._root_owner(row), fraction >= .5)
            if hasattr(row, "folder"):
                if .25 <= fraction <= .75:
                    return ("into", kind, item_id, row.folder.id, None, None)
                return self._root_plan(kind, item_id, "folder:" + row.folder.id, fraction > .75)
            if not hasattr(row, "note") or row.note.id == item_id:
                return None
            if row.note.id in self.store.descendants(item_id, include_hidden=True):
                return None
            # In the outer gutter, a nested row targets its root block. The
            # explicit folder footer also offers this without precision aiming.
            nested = row.note.folder_id is not None or self.store.parent_of(row.note.id) is not None
            if nested and x is not None and x < 20:
                return self._root_plan(kind, item_id, self._root_owner(row), fraction >= .5)
            if .25 <= fraction <= .75:
                return ("into", kind, item_id, row.note.folder_id, row.note.id, None)
            parent = self.store.parent_of(row.note.id)
            if row.note.folder_id is None and parent is None:
                return self._root_plan(kind, item_id, "note:" + row.note.id, fraction > .75)
            moving = self.store.get_note(item_id)
            if moving.favorite != row.note.favorite:
                return None
            siblings = [n for n in self.store.list_notes(folder_id=row.note.folder_id or "__root__")
                        if n.id != item_id and n.favorite == moving.favorite
                        and self.store.parent_of(n.id) == parent]
            index = next(i for i, n in enumerate(siblings) if n.id == row.note.id)
            after = fraction > .75
            before = siblings[index + 1].id if after and index + 1 < len(siblings) else (
                None if after else row.note.id)
            return ("after" if after else "before", kind, item_id, row.note.folder_id, parent, before)
        except (KeyError, StopIteration, ValueError):
            return None

    def _sidebar_drag_motion(self, row: Gtk.ListBoxRow, x: float, y: float) -> Gdk.DragAction:
        plan = self._sidebar_drop_plan(row, getattr(self, "_drag_value", None), y, x)
        self._clear_drop_hint()
        if plan is None:
            return Gdk.DragAction(0)
        hint = row
        if plan[1].startswith("root-") and not isinstance(row, RootDropBoundary):
            anchor_kind, anchor_id = plan[4].split(":", 1)
            hint = self.folder_rows[anchor_id] if anchor_kind == "folder" else self.note_rows[anchor_id]
        if plan[0] == "after" and not isinstance(hint, RootDropBoundary):
            row = hint
            if hasattr(row, "folder"):
                children = [r for r in self.folder_children.get(row.folder.id, ()) if r.get_visible()]
            else:
                ids = self.store.descendants(row.note.id)
                children = [r for r in self.note_rows.values() if r.note.id in ids and r.get_visible()]
            if children:
                hint = children[-1]
        hint.add_css_class("drop-" + plan[0])
        self._drop_hint_row = hint
        return Gdk.DragAction.MOVE

    def _sidebar_drop(self, row: Gtk.ListBoxRow, value: object, x: float, y: float) -> bool:
        plan = self._sidebar_drop_plan(row, value, y, x)
        self._clear_drop_hint()
        if plan is None:
            return False
        _zone, kind, item_id, folder, parent, before = plan
        try:
            if kind.startswith("root-"):
                self._place_at_root(kind.removeprefix("root-"), item_id, before)
            elif kind == "folder":
                self.reorder_folder(item_id, before)
            else:
                self.move_note(item_id, folder, before, parent_id=parent)
        except (ValueError, sqlite3.Error) as error:
            self.toast_overlay.add_toast(Adw.Toast(title=f"Could not move: {error}"))
            return False
        return True

    def reorder_note(self, note_id: str, before_id: str | None) -> None:
        try:
            self.store.reorder_note(note_id, before_id)
        except KeyError:
            return
        self._reload_sidebar(select_id=note_id)

    def move_note(self, note_id: str, folder_id: str | None, before_id: str | None,
                  *, parent_id: str | None = None) -> None:
        moving_ids = self.store.descendants(note_id) | {note_id}
        if self.current and self.current.id in moving_ids:
            self._flush_save()
        try:
            self.store.move_note(note_id, folder_id, before_id=before_id, parent_id=parent_id)
        except KeyError:
            return
        if folder_id is not None:
            self.store.set_folder_expanded(folder_id, True)
        if self.current and self.current.id in moving_ids:
            self.current = self.store.get_note(self.current.id)
            self.current_folder = folder_id
        self._reload_sidebar(select_id=note_id)

    def reorder_folder(self, folder_id: str, before_id: str | None) -> None:
        try:
            self.store.reorder_folder(folder_id, before_id)
        except KeyError:
            return
        self._reload_sidebar()

    def _place_at_root(self, kind: str, item_id: str, before: str | None) -> None:
        self._flush_save()
        self.store.place_root(kind, item_id, before)
        if self.current:
            self.current = self.store.get_note(self.current.id)
            self.current_folder = self.current.folder_id
        self._reload_sidebar(select_id=item_id if kind == "note" else None)

    def _root_drop_row(self, x: float, y: float) -> tuple[Gtk.ListBoxRow, float, float]:
        row = self.sidebar_list.get_row_at_y(int(y))
        if not isinstance(row, (NoteRow, FolderRow, RootDropBoundary)):
            row = self.root_end
        ok, bounds = row.compute_bounds(self.sidebar_list)
        return row, x - bounds.get_x() if ok else x, y - bounds.get_y() if ok else 0

    def _root_drag_motion(self, _target, x: float, y: float) -> Gdk.DragAction:
        row, local_x, local_y = self._root_drop_row(x, y)
        return self._sidebar_drag_motion(row, local_x, local_y)

    def _drop_to_root(self, _target, value: object, x: float, y: float) -> bool:
        row, local_x, local_y = self._root_drop_row(x, y)
        return self._sidebar_drop(row, value, local_x, local_y)

    def _move_selected(self, delta: int) -> bool:
        row = self.sidebar_list.get_selected_row()
        if hasattr(row, "folder") or (hasattr(row, "note") and row.note.folder_id is None
                                         and self.store.parent_of(row.note.id) is None):
            kind = "folder" if hasattr(row, "folder") else "note"
            item_id = row.folder.id if kind == "folder" else row.note.id
            keys = [self.store.root_key(item) for item in self.store.root_items()]
            index = keys.index(kind + ":" + item_id)
            target = index + delta
            if not 0 <= target < len(keys):
                return False
            before = keys[target] if delta < 0 else (keys[target + 1] if target + 1 < len(keys) else None)
            self._place_at_root(kind, item_id, before)
            self.sidebar_list.select_row(self.folder_rows[item_id] if kind == "folder" else self.note_rows[item_id])
            return True
        if not hasattr(row, "note"):
            return False
        parent = self.store.parent_of(row.note.id)
        siblings = [n for n in self.store.list_notes(folder_id=row.note.folder_id or "__root__")
                    if self.store.parent_of(n.id) == parent and n.favorite == row.note.favorite]
        index = next((position for position, note in enumerate(siblings) if note.id == row.note.id), -1)
        target = index + delta
        if index < 0 or target < 0 or target >= len(siblings):
            return False
        if delta < 0:
            before_id = siblings[target].id
        else:
            before_id = siblings[target + 1].id if target + 1 < len(siblings) else None
        self.store.reorder_note(row.note.id, before_id)
        self._reload_sidebar(select_id=row.note.id)
        return True

    # ---------------------------------------------------------- saving

    def _page_state(self) -> tuple:
        body, runs = self.page.serialize()
        return (" ".join(self.title_entry.get_text().split()), body, runs)

    def _content_changed(self, *_args) -> None:
        if self.loading or self.current is None:
            return
        self.save_status.pending()
        now = GLib.get_monotonic_time()
        if not self._dirty_since:
            self._dirty_since = now
        if self.save_source:
            GLib.source_remove(self.save_source)
        waited = (now - self._dirty_since) // 1000
        delay = max(0, min(AUTOSAVE_DELAY_MS, AUTOSAVE_MAX_WAIT_MS - waited))
        self.save_source = GLib.timeout_add(delay, self._save)

    def _can_edit_note_content(self, note_id: str) -> bool:
        return not (self.current and self.current.id == note_id) or self.editor.get_editable()

    def _save(self) -> bool:
        self.save_source = 0
        self._dirty_since = 0
        if self.current is None:
            return GLib.SOURCE_REMOVE
        if not self._can_edit_note_content(self.current.id):
            return GLib.SOURCE_REMOVE
        state = self._page_state()
        if state == self._saved_state:
            # Nothing new since the last save: no write, and so nothing to sync.
            self.save_status.saved()
            return GLib.SOURCE_REMOVE
        title, body, runs = state
        try:
            self._keep_version_from_elsewhere(state)
            self.current = self.store.update_note(self.current.id, title=title, body=body, runs=runs)
        except (OSError, sqlite3.Error) as error:
            self.save_status.failed(str(error))
            self.save_source = GLib.timeout_add(1500, self._save)
            return GLib.SOURCE_REMOVE
        except KeyError:
            return GLib.SOURCE_REMOVE
        self._saved_state = state
        self.save_status.saved()
        row = self.note_rows.get(self.current.id)
        if row is not None:
            row.update(self.current)
        else:
            self._reload_sidebar_soon(select_id=self.current.id)
        self._refresh_semantics()
        return GLib.SOURCE_REMOVE

    def _flush_save(self) -> None:
        if self.save_source:
            GLib.source_remove(self.save_source)
            self.save_source = 0
            self._save()

    def _has_local_edits(self) -> bool:
        return bool(self.save_source) or (self.current is not None and self._page_state() != self._saved_state)

    def _keep_version_from_elsewhere(self, state: tuple) -> None:
        """Before a save writes over the open page, look at it in the library.

        If it changed elsewhere since it was opened here (sync took another
        device's version while the person was typing), their text is still
        what gets saved, and the other version is kept beside it as one
        "(other copy)". The copy goes through the guard sync uses, so a save
        retried after a failure never makes a second one.
        """
        base = self.current
        try:
            stored = self.store.get_note(base.id)
        except KeyError:
            return
        if _content(stored) == _content(base):
            return
        title, body, runs = state
        if stored.deleted_at is not None:
            # Deleted elsewhere while being written here: the page comes back.
            self.store.restore_note(base.id)
            print(f"prairie-notes: page {base.id} was deleted elsewhere while being edited here; "
                  "keeping it", file=sys.stderr)
            self._reload_sidebar_soon(select_id=base.id)
            return
        if (stored.title, stored.body, stored.runs) == (_clean_note_title(title), body, tuple(runs)):
            return  # The same change, made in both places.
        copy = keep_other_copy(self.store, stored)
        if copy is None:
            print(f"prairie-notes: the other version of page {base.id} is already kept", file=sys.stderr)
            return
        print(f"prairie-notes: page {base.id} changed elsewhere while being edited here; "
              f"kept that version as page {copy.id}", file=sys.stderr)
        self.toast_overlay.add_toast(Adw.Toast(
            title=f"This page also changed on another device. That version is kept as “{copy.title}”.",
            timeout=6))
        self._reload_sidebar_soon(select_id=base.id)

    # ------------------------------------------- changes made elsewhere

    def _watch_store(self) -> None:
        """Show what sync writes to the library while Notes is open.

        The library's folder is watched for the database and its write-ahead
        log. A change is taken only when SQLite says another connection
        committed one (PRAGMA data_version), so Notes' own saves never refresh
        the window, and a refresh, which writes nothing, never starts another.
        """
        self._store_version = self.store.data_version()
        self._sidebar_seen = self._sidebar_signature()
        path = self.store.path
        self._store_names = {path.name, f"{path.name}-wal"}
        try:
            monitor = Gio.File.new_for_path(str(path.parent)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error as error:
            print(f"prairie-notes: cannot watch the library's folder ({error.message}); "
                  f"looking for changes every {STORE_POLL_SECONDS} s instead", file=sys.stderr)
            self.store_watch = "poll"
            self._store_poll_source = GLib.timeout_add_seconds(STORE_POLL_SECONDS, self._store_poll)
            return
        monitor.set_rate_limit(STORE_SETTLE_MS)
        monitor.connect("changed", self._store_file_changed)
        self._store_monitor = monitor
        self.store_watch = "monitor"

    def _store_poll(self) -> bool:
        if getattr(self, "_closed", False):
            self._store_poll_source = 0
            return GLib.SOURCE_REMOVE
        if not self._store_check_source:
            self._store_check()
        return GLib.SOURCE_CONTINUE

    def _store_file_changed(self, _monitor, file: Gio.File, other: Gio.File | None, _event) -> None:
        names = {item.get_basename() for item in (file, other) if item is not None}
        if self._store_check_source or not names & self._store_names:
            return
        self._store_check_source = GLib.timeout_add(STORE_SETTLE_MS, self._store_check)

    def _store_check(self) -> bool:
        self._store_check_source = 0
        if getattr(self, "_closed", False):
            return GLib.SOURCE_REMOVE
        if self._renaming or self._rename_open():
            # Not under a name being typed; look again once it is done.
            self._store_check_source = GLib.timeout_add(STORE_SETTLE_MS, self._store_check)
            return GLib.SOURCE_REMOVE
        try:
            version = self.store.data_version()
        except sqlite3.Error:
            return GLib.SOURCE_REMOVE
        if version == self._store_version:
            return GLib.SOURCE_REMOVE
        self._store_version = version
        self.external_refreshes += 1
        self._take_change_from_elsewhere()
        return GLib.SOURCE_REMOVE

    def _rename_open(self) -> bool:
        return (any(row.title_stack.get_visible_child_name() == "rename" for row in self.note_rows.values())
                or any(row.is_renaming() for row in self.folder_rows.values()))

    def _take_change_from_elsewhere(self) -> None:
        """The list as the library now has it, and the open page too unless
        the person has changed it here since it was last saved."""
        current, gone = self.current, False
        if current is not None:
            try:
                stored = self.store.get_note(current.id)
            except KeyError:
                stored = None
            if _content(stored) == _content(current):
                self.current = stored
                self.current_folder = stored.folder_id
            elif self._has_local_edits():
                pass  # Their text stays; saving keeps the other version as a copy.
            elif stored is None or stored.deleted_at is not None:
                gone = True
                self.current = None
            else:
                self._reload_open_note(stored)
        self._refresh_sidebar_in_place()
        if gone:
            first = next(iter(self.store.list_notes()), None)
            if first is not None:
                self._open_note(first)
            else:
                self._show_empty()
        self._refresh_semantics()

    def _reload_open_note(self, stored: Note) -> None:
        """Show the page's new version where the old one was: same caret,
        same scroll, and nothing to save."""
        adjustment = self.body_scroll.get_vadjustment()
        scrolled = adjustment.get_value()
        caret = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        title_caret = self.title_entry.get_position()
        self.loading = True
        try:
            self.current = stored
            self.current_folder = stored.folder_id
            self.title_entry.set_text("" if stored.title == UNTITLED else stored.title)
            self.page.load(stored.body, stored.runs)
            self._sync_editor_tag_colors()
            self.buffer.place_cursor(self.buffer.get_iter_at_offset(min(caret, self.buffer.get_char_count())))
            self.title_entry.set_position(min(title_caret, len(self.title_entry.get_text())))
        finally:
            self.loading = False
        self._saved_state = self._page_state()
        self.save_status.saved(_last_saved_label(stored.modified_at))
        self._refresh_format_buttons()
        adjustment.set_value(scrolled)
        GLib.idle_add(lambda: (adjustment.set_value(scrolled), GLib.SOURCE_REMOVE)[1],
                      priority=GLib.PRIORITY_LOW)

    def _sidebar_signature(self) -> tuple:
        return (self.search.get_text(), self.store.list_notes(), self.store.list_folders(),
                tuple(sorted(self.store.note_parents().items())),
                tuple(sorted(self.store.folder_parents().items())),
                tuple(self.store.root_key(item) for item in self.store.root_items()))

    def _refresh_sidebar_in_place(self) -> None:
        """Rebuild the list only if what it shows changed; keep its scroll,
        the open page's row selected, and the keyboard where it was."""
        signature = self._sidebar_signature()
        if signature == self._sidebar_seen:
            return
        self._sidebar_seen = signature
        adjustment = self.sidebar_scroll.get_vadjustment()
        scrolled = adjustment.get_value()
        focused = self.sidebar_list.get_focus_child() is not None
        self._reload_sidebar()
        adjustment.set_value(scrolled)
        GLib.idle_add(lambda: (adjustment.set_value(scrolled), GLib.SOURCE_REMOVE)[1],
                      priority=GLib.PRIORITY_LOW)
        if focused and (row := self.sidebar_list.get_selected_row()) is not None:
            row.grab_focus()

    # ------------------------------------------------------ formatting

    def _toggle_format(self, style: str) -> None:
        """Apply a format, with or without a selection.

        A block style takes the caret's paragraph (or every selected one). An
        inline style changes the selection, or, with none, arms the button so
        that what is typed next carries it.
        """
        if self.current is None or not self.editor.get_editable():
            return
        if self.current is None:
            return
        if style in INLINE_STYLES:
            self.page.toggle_inline(style)
        elif style in BLOCK_STYLES:
            self.page.toggle_block(style)
        self._refresh_format_buttons()
        self.editor.grab_focus()

    def _refresh_format_buttons(self) -> None:
        """Light a button when its format is on the selection, at the caret,
        or armed for what gets typed next."""
        inline = self.page.inline_state()
        lines = self.page.selected_lines()
        blocks = [self.page.block_of_line(line) for line in lines]
        for style, button in self.format_buttons.items():
            if style in INLINE_STYLES:
                active = inline.get(style, False)
            else:
                active = bool(blocks) and all(style in block for block in blocks)
            if active:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")

    def _show_link_popover(self, button: Gtk.Widget) -> None:
        if self.current is None or not self.editor.get_editable():
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        box.add_css_class("notes-link-popover")
        display = None
        if not self.buffer.get_selection_bounds():
            display = Gtk.Entry(placeholder_text="Text to display")
            box.append(display)
        entry = Gtk.Entry(placeholder_text="https://example.com")
        box.append(entry)
        apply = Gtk.Button(label="Add link")
        apply.add_css_class("suggested-action")
        box.append(apply)
        popover = Gtk.Popover(child=box)
        popover.add_css_class("luma-dialog-popover")
        popover.set_parent(button)
        popover.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        apply.connect(
            "clicked",
            lambda *_: self._apply_link(
                entry.get_text(), popover, display.get_text() if display else None
            ),
        )
        entry.connect(
            "activate",
            lambda *_: self._apply_link(
                entry.get_text(), popover, display.get_text() if display else None
            ),
        )
        popover.popup(); (display or entry).grab_focus()

    def _apply_link(
        self, href: str, popover: Gtk.Popover, display_text: str | None = None
    ) -> None:
        if self.current is None or not self.editor.get_editable():
            return
        href = _normalize_href(href)
        if not href:
            self.toast_overlay.add_toast(
                Adw.Toast(title="Enter a valid web or email address")
            )
            return
        bounds = self.buffer.get_selection_bounds()
        if not bounds:
            text = (display_text or "").strip() or href
            start = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            offset = start.get_offset()
            self.buffer.insert(start, text)
            bounds = (
                self.buffer.get_iter_at_offset(offset),
                self.buffer.get_iter_at_offset(offset + len(text)),
            )
        tag = self.page.link_tag(href)
        self._apply_named_color(tag, "luma_blue")
        self.buffer.apply_tag(tag, *bounds)
        popover.popdown(); self.page.changed()

    def _appearance_changed(self, *_args) -> None:
        GLib.idle_add(self._sync_editor_tag_colors)

    def _apply_named_color(self, tag: Gtk.TextTag, name: str) -> None:
        found, color = self.get_style_context().lookup_color(name)
        if found:
            tag.set_property("foreground-rgba", color)

    def _sync_editor_tag_colors(self) -> bool:
        self._apply_named_color(self.page.tags["quote"], "luma_ink_secondary")
        self._apply_named_color(self.page.tags["checked"], "luma_muted")
        found, color = self.get_style_context().lookup_color("luma_amber")
        if found:
            self.page.tags["highlight"].set_property("background-rgba", color)
        for tag in self.page.link_tags:
            self._apply_named_color(tag, "luma_blue")
        self.editor.queue_draw()
        return GLib.SOURCE_REMOVE

    # -------------------------------------------------------- pictures

    def _choose_picture_file(self) -> None:
        if self.current is None or not self.editor.get_editable():
            return
        if self.fixture:
            return
        dialog = Gtk.FileDialog(title="Add Picture")
        images = Gtk.FileFilter(name="Pictures")
        images.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog.set_filters(filters)
        dialog.set_default_filter(images)
        dialog.open_multiple(self, None, self._picture_files_chosen)

    def _picture_files_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        if self.current is None or not self.editor.get_editable():
            return
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return
        self.page.import_files([files.get_item(index) for index in range(files.get_n_items())])

    def _picture_registry(self, picture: NotePicture) -> CommandRegistry:
        line = self.page.picture_line(picture)
        lines = range(line, line + 1) if line is not None else range(0)
        return CommandRegistry((
            CommandGroup(None, (
                Command("picture.left", "Align Left", lambda: self.page.set_alignment(lines, "")),
                Command("picture.center", "Align Center", lambda: self.page.set_alignment(lines, "align-center")),
                Command("picture.right", "Align Right", lambda: self.page.set_alignment(lines, "align-right")),
            )),
            CommandGroup(None, (
                Command("picture.full", "Fit to Text Width", lambda: self._resize_picture(picture, -1)),
                Command("picture.reset", "Reset Size", lambda: self._resize_picture(picture, 0)),
            )),
            CommandGroup(None, (
                Command("picture.copy", "Copy Picture", lambda: self._copy_picture(picture)),
                Command("picture.replace", "Replace Picture…", lambda: self._replace_picture(picture)),
            )),
            CommandGroup(None, (
                Command("picture.delete", "Delete", lambda: self.page.delete_picture(picture), destructive=True),
            )),
        ))

    def _picture_menu(self, picture: NotePicture, x: float, y: float) -> None:
        if self.picture_bar is not None:
            self.picture_bar.popdown()
        # Popovers hang from the text view: one parented to a widget anchored
        # in the text is never shown.
        ok, point = picture.compute_point(self.editor, Graphene.Point().init(x, y))
        self.show_menu(self._picture_registry(picture), self.editor, point.x if ok else x, point.y if ok else y)

    def _show_picture_bar(self, picture: NotePicture) -> None:
        """The layout bar under a chosen picture, as Write shows one."""
        if self.picture_bar is not None:
            self.picture_bar.popdown()
        line = self.page.picture_line(picture)
        lines = range(line, line + 1) if line is not None else range(0)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("notes-picture-bar")

        def group(choices):
            box = ConnectedButtonGroup(compact=True)
            for label, tooltip, action in choices:
                button = Gtk.Button(label=label, tooltip_text=tooltip)
                button.add_css_class("notes-picture-bar-button")
                button.connect("clicked", lambda _b, run=action: (run(), self.page.fit_pictures()))
                box.append(button)
            bar.append(box)

        group((("Left", "Align left", lambda: self.page.set_alignment(lines, "")),
               ("Center", "Align center", lambda: self.page.set_alignment(lines, "align-center")),
               ("Right", "Align right", lambda: self.page.set_alignment(lines, "align-right"))))
        group((("Full width", "Fit to the text width", lambda: self._resize_picture(picture, -1)),
               ("Reset size", "Show the picture at its own size, within the text width",
                lambda: self._resize_picture(picture, 0))))
        group((("Replace…", "Choose another picture", lambda: self._replace_picture(picture)),
               ("Delete", "Delete the picture", lambda: self.page.delete_picture(picture))))
        popover = Gtk.Popover(child=bar, has_arrow=False, position=Gtk.PositionType.BOTTOM)
        popover.add_css_class("notes-picture-popover")
        popover.set_parent(self.editor)
        ok, bounds = picture.compute_bounds(self.editor)
        if ok:
            # Under the picture, or over its lower edge when that is out of view.
            y = min(bounds.get_y() + bounds.get_height(), self.editor.get_height() - 44)
            popover.set_pointing_to(_point_rectangle(bounds.get_x() + bounds.get_width() / 2, y))
        popover.set_offset(0, 8)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, keyval, *_: self._picture_key(picture, keyval))
        popover.add_controller(keys)

        def closed(closing: Gtk.Popover) -> None:
            if self.picture_bar is closing:
                self.picture_bar = None
                if self.page.chosen is picture:
                    self.page.choose_picture(None)
            GLib.idle_add(lambda: (closing.unparent() if closing.get_parent() else None, False)[1])

        popover.connect("closed", closed)
        self.picture_bar = popover
        self.page.picture_bar = popover
        popover.popup()

    def _picture_key(self, picture: NotePicture, keyval: int) -> bool:
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace, Gdk.KEY_KP_Delete):
            self.page.delete_picture(picture)
            return True
        return False

    def _resize_picture(self, picture: NotePicture, width: int) -> None:
        if self.current is None or not self.editor.get_editable():
            return
        picture.width = 10_000 if width < 0 else width
        if width < 0:
            picture.width = max(1, picture.display_size()[0])
        picture.fit()
        self.page.changed()

    def _copy_picture(self, picture: NotePicture) -> None:
        if picture.texture is not None:
            self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(picture.texture))
            self.toast_overlay.add_toast(Adw.Toast(title="Picture copied", timeout=2))

    def _replace_picture(self, picture: NotePicture) -> None:
        if self.current is None or not self.editor.get_editable():
            return
        if self.fixture:
            return
        subject_id = self.current.id if self.current else None
        dialog = Gtk.FileDialog(title="Replace Picture")
        images = Gtk.FileFilter(name="Pictures")
        images.add_mime_type("image/*")
        dialog.set_default_filter(images)

        def chosen(source: Gtk.FileDialog, result) -> None:
            try:
                file = source.open_finish(result)
            except GLib.Error:
                return
            if (not self.get_mapped() or not self.current or not self.editor.get_editable()
                    or self.current.id != subject_id or picture not in self.page.pictures):
                return
            from .notes_attachments import PictureError, store_picture
            try:
                name = store_picture(Path(file.get_path()).read_bytes())
            except (OSError, PictureError, TypeError) as error:
                self.toast_overlay.add_toast(Adw.Toast(title=str(error) or "That picture could not be used."))
                return
            self.page.replace_picture(picture, name)

        dialog.open(self, None, chosen)

    # ------------------------------------------------------------ menus

    def show_menu(self, registry: CommandRegistry, parent: Gtk.Widget, x: float, y: float) -> None:
        popover = command_popover(registry)
        popover.set_parent(parent)
        popover.set_pointing_to(_point_rectangle(x, y))
        popover.popup()

    def toggle_search(self) -> None:
        self.search_visible = not self.search_visible
        self.search.set_visible(self.search_visible)
        if self.search_visible:
            self.search.grab_focus()
        else:
            self.search.set_text("")

    def note_commands(self, note: Note) -> CommandRegistry:
        destinations = []
        parent = self.store.parent_of(note.id)
        if parent is not None:
            destinations.append(Command(
                "note.move.out", "Move out of parent note",
                lambda: self.move_note(note.id, note.folder_id, parent,
                                       parent_id=self.store.parent_of(parent)),
                "go-up-symbolic"))
        excluded = self.store.descendants(note.id, include_hidden=True) | {note.id}
        parents = tuple(Command(
            f"note.nest.{target.id}", target.display_title,
            lambda target=target: self.move_note(note.id, target.folder_id, None, parent_id=target.id),
            "text-x-generic-symbolic")
            for target in self.store.list_notes() if target.id not in excluded)
        if parents:
            destinations.append(Command("note.nest", "Inside note", lambda: None,
                                        "text-x-generic-symbolic", children=parents))
        if note.folder_id is not None:
            destinations.append(Command(
                "note.move.root", "No Folder",
                lambda: self.move_note(note.id, None, None), "go-home-symbolic",
            ))
        destinations.extend(
            Command(
                f"note.move.{folder.id}", folder.name,
                lambda target=folder.id: self.move_note(note.id, target, None),
                "luma-folder-symbolic",
            )
            for folder in self.store.list_folders() if folder.id != note.folder_id
        )
        destinations.append(Command(
            "note.move.new", "New Folder…", lambda: self._move_to_new_folder(note), "folder-new-symbolic"))
        return CommandRegistry((
            CommandGroup(None, (
                Command("note.open", "Open", lambda: self._open_note(note), "document-open-symbolic"),
                Command("note.rename", "Rename", lambda: self._begin_note_rename(note.id),
                        "edit-rename-symbolic", shortcut=("F2",),
                        enabled=lambda: self._can_edit_note_content(note.id)),
                Command("note.duplicate", "Duplicate", lambda: self._duplicate_specific(note), "edit-copy-symbolic"),
                Command("note.pin", "Unpin" if note.favorite else "Pin",
                        lambda: self._favorite_note(note), PIN_ICON),
            )),
            CommandGroup(None, (
                Command("note.move", "Move to", lambda: None, "luma-folder-symbolic",
                        children=tuple(destinations)),
                Command("note.export", "Export as Markdown…", lambda: self._export_note(note),
                        "document-save-as-symbolic"),
            )),
            CommandGroup(None, (
                Command("note.delete", "Delete", lambda: self._delete_specific(note),
                        "user-trash-symbolic", destructive=True),
            )),
        ))

    def folder_commands(self, folder: Folder) -> CommandRegistry:
        return CommandRegistry((CommandGroup(None, (
            Command("folder.new-note", "New page here", lambda: self._new_in_folder(folder), "document-new-symbolic"),
            Command("folder.rename", "Rename", lambda: self._begin_folder_rename(folder.id),
                    "edit-rename-symbolic", shortcut=("F2",)),
            Command("folder.pin", "Unpin" if folder.favorite else "Pin",
                    lambda: self._favorite_folder(folder), PIN_ICON),
            Command("folder.delete", "Delete folder", lambda: self._delete_folder(folder), "user-trash-symbolic", destructive=True),
        )),))

    def _sidebar_context(self, gesture: Gtk.GestureClick, _press: int, x: float, y: float) -> None:
        row = self.sidebar_list.get_row_at_y(int(y))
        if row is not None and row.get_visible():
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        registry = CommandRegistry((CommandGroup(None, (
            Command("notes.new", "New page", self.create_note, "document-new-symbolic"),
            Command("folders.new", "New folder", self.create_folder, "folder-new-symbolic"),
        )),))
        self.show_menu(registry, self.sidebar_list, x, y)

    def _move_to_new_folder(self, note: Note) -> None:
        folder = self.store.create_folder()
        self.move_note(note.id, folder.id, None)
        GLib.idle_add(self._begin_folder_rename, folder.id)

    def _export_note(self, note: Note) -> None:
        if self.fixture:
            return
        if self.current and self.current.id == note.id:
            self._flush_save()
        note = self.store.get_note(note.id)
        name = "".join(char for char in note.display_title if char not in '/\\:*?"<>|').strip() or "Note"
        dialog = Gtk.FileDialog(title="Export as Markdown", initial_name=f"{name}.md")

        def chosen(source: Gtk.FileDialog, result) -> None:
            try:
                file = source.save_finish(result)
            except GLib.Error:
                return
            try:
                export_markdown(note, Path(file.get_path()))
            except (OSError, TypeError) as error:
                self.toast_overlay.add_toast(Adw.Toast(title=f"Could not export: {error}"))
                return
            self.toast_overlay.add_toast(Adw.Toast(title=f"Exported “{note.display_title}”", timeout=3))

        dialog.save(self, None, chosen)

    def _begin_note_rename(self, note_id: str) -> bool:
        if not self._can_edit_note_content(note_id):
            return GLib.SOURCE_REMOVE
        row = self.note_rows.get(note_id)
        if row is None:
            return GLib.SOURCE_REMOVE
        self.sidebar_list.select_row(row)
        row.begin_rename()
        return GLib.SOURCE_REMOVE

    def _finish_note_rename(self, note: Note, title: str) -> bool:
        if getattr(self, "_closed", False):
            return True
        if not self._can_edit_note_content(note.id):
            return True
        if not title.strip():
            return False
        if self._renaming:
            # Already committing this rename; see _finish_folder_rename.
            return True
        self._renaming = True
        try:
            if self.current and self.current.id == note.id:
                self._flush_save()
            updated = self.store.rename_note(note.id, title)
            if self.current and self.current.id == note.id:
                self.current = updated
                self.loading = True
                self.title_entry.set_text(updated.title)
                self.loading = False
                self._saved_state = self._page_state()
            self._refresh_semantics()
        finally:
            self._renaming = False
        self._reload_sidebar_soon(select_id=note.id)
        return True

    def _begin_folder_rename(self, folder_id: str) -> bool:
        row = self.folder_rows.get(folder_id)
        if row is None:
            return GLib.SOURCE_REMOVE
        self.sidebar_list.select_row(row)
        row.begin_rename()
        return GLib.SOURCE_REMOVE

    def _finish_folder_rename(self, folder: Folder, name: str) -> bool:
        """Rename a folder, without rebuilding the sidebar underneath itself.

        The rename field commits when it loses focus. Rebuilding the sidebar
        here destroys the row holding that field, which makes it lose focus
        again — and it commits again, and rebuilds again. That recursion is
        what locked the window up when a new folder was created, because a new
        folder opens straight into its rename. So the commit is made
        re-entrant, and the rebuild waits until the handler has returned.
        """
        if getattr(self, "_closed", False):
            return True
        if not name.strip():
            return False
        if self._renaming:
            return True
        self._renaming = True
        try:
            self.store.rename_folder(folder.id, name)
        finally:
            self._renaming = False
        self._reload_sidebar_soon()
        return True

    def _reload_sidebar_soon(self, select_id: str | None = None) -> None:
        """Rebuild the sidebar once the current event handler has finished."""
        if self._sidebar_reload_source:
            GLib.source_remove(self._sidebar_reload_source)
        self._sidebar_reload_source = GLib.idle_add(
            self._reload_sidebar_now, select_id, priority=GLib.PRIORITY_DEFAULT_IDLE
        )

    def _reload_sidebar_now(self, select_id: str | None) -> bool:
        self._sidebar_reload_source = 0
        self._reload_sidebar(select_id=select_id)
        return GLib.SOURCE_REMOVE

    def _favorite_note(self, note: Note) -> None:
        self.store.set_note_favorite(note.id, not note.favorite)
        if self.current and self.current.id == note.id:
            self.current = self.store.get_note(note.id)
        self._reload_sidebar(select_id=note.id)

    def _favorite_folder(self, folder: Folder) -> None:
        self.store.set_folder_favorite(folder.id, not folder.favorite); self._reload_sidebar()

    def _new_in_folder(self, folder: Folder) -> None:
        self.current_folder = folder.id; self.create_note()

    def _delete_folder(self, folder: Folder) -> None:
        self.store.delete_folder(folder.id)
        if self.current_folder == folder.id:
            self.current_folder = None
        self._reload_sidebar()

    def _show_about(self) -> None:
        about = Adw.AboutDialog(application_name="Notes", application_icon=ICON_NAME,
                                developer_name="Project Luma", version="0.2.0",
                                comments="A focused, responsive writing space built with the Luma Application Kit.")
        about.present(self)

    def _install_keys(self) -> None:
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

    def _in_body(self) -> bool:
        focus = self.get_focus()
        return focus is not None and (focus is self.editor or focus.is_ancestor(self.editor))

    def _key_pressed(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        key = Gdk.keyval_name(keyval) or ""
        if control and shift and key.lower() == "n":
            self.create_folder(); return True
        if control and key.lower() == "n": self.create_note(); return True
        if control and key.lower() == "f": self.toggle_search(); return True
        # Undo, formatting and links belong to the page; the title and the
        # search field keep their own editing keys.
        if self._in_body():
            if control and key.lower() in {"z", "y", "b", "i", "u", "k"} and not self.editor.get_editable():
                return True
            if control and key.lower() == "z" and hasattr(self.buffer, "undo"):
                if shift:
                    if self.buffer.get_can_redo(): self.buffer.redo()
                elif self.buffer.get_can_undo(): self.buffer.undo()
                return True
            if control and key.lower() in {"b", "i", "u"}:
                self._toggle_format({"b": "bold", "i": "italic", "u": "underline"}[key.lower()]); return True
            if control and key.lower() == "k":
                self._show_link_popover(self.editor); return True
        if control and shift and key in {"Up", "Down"}:
            return self._move_selected(-1 if key == "Up" else 1)
        if key == "F2":
            selected = self.sidebar_list.get_selected_row()
            if hasattr(selected, "note"):
                self._begin_note_rename(selected.note.id); return True
            if hasattr(selected, "folder"):
                self._begin_folder_rename(selected.folder.id); return True
        return False

    def _close_notes(self, _window) -> bool:
        # A rename still open is committed while the library is open.
        self.set_focus(None)
        self._flush_save()
        self._closed = True
        # Nothing scheduled may reach the library once it is closed.
        self.cancel_folder_toggle()
        self.page.stop_watching_sync_status()
        if self._store_monitor is not None:
            self._store_monitor.cancel()
            self._store_monitor = None
        for source in ("_store_check_source", "_store_poll_source"):
            if getattr(self, source):
                GLib.source_remove(getattr(self, source))
                setattr(self, source, 0)
        if self._sidebar_reload_source:
            GLib.source_remove(self._sidebar_reload_source)
            self._sidebar_reload_source = 0
        if self.semantic_publisher is not None:
            self.semantic_publisher.close()
            self.semantic_publisher = None
        self.store.close()
        return False


class NotesApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.connect("notify::active-window", self._active_window_changed)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        _install_notes_style()

    def do_activate(self) -> None:
        from .notes_lumaui import NotesLumaWindow
        window = next((window for window in self.get_windows() if isinstance(window, NotesWindow)), None)
        (window or NotesLumaWindow(self)).present()

    def do_open(self, files, _n_files, _hint) -> None:
        if os.environ.get("LUMA_NOTES_FIXTURE"):
            self.do_activate()
            return
        from .notes_file import TextFileWindow
        for file in files:
            window = next((window for window in self.get_windows()
                           if isinstance(window, TextFileWindow) and window.file.equal(file)), None)
            (window or TextFileWindow(self, file)).present()

    def _active_window_changed(self, *_arguments) -> None:
        window = self.get_active_window()
        if window is not None and hasattr(window, "commands"):
            self.set_menubar(command_menu_model(window.commands, self))


def _relative_date(value: str) -> str:
    try:
        stamp = datetime.fromisoformat(value).astimezone()
    except ValueError:
        return "Today"
    now = datetime.now().astimezone()
    if stamp.date() == now.date(): return "Today"
    return stamp.strftime("%b %-d")


def _last_saved_label(value: str) -> str:
    try:
        stamp = datetime.fromisoformat(value).astimezone()
    except ValueError:
        return "Last saved"
    return f"Last saved at {stamp.strftime('%-I:%M %p')}"


def _normalize_href(value: str) -> str:
    href = value.strip()
    if not href:
        return ""
    lowered = href.casefold()
    if lowered.startswith(("https://", "http://", "mailto:")):
        return href
    if ":" in href.split("/", 1)[0] or " " in href:
        return ""
    if "@" in href:
        return f"mailto:{href}"
    return f"https://{href}"


_notes_style_provider: Gtk.CssProvider | None = None


def _install_notes_style() -> None:
    """Load Notes' sheet through the kit, so it follows light and dark with it."""
    global _notes_style_provider
    display = Gdk.Display.get_default()
    if display is None:
        return
    path = os.environ.get("LUMA_NOTES_STYLE_PATH", "/usr/share/prairie-core/notes.css")
    if _notes_style_provider is not None:
        _notes_style_provider.load_from_path(path)
        return
    _notes_style_provider = add_style_sheet(path)


def main() -> int:
    return NotesApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
