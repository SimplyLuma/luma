# SPDX-License-Identifier: Apache-2.0
"""Notes composition: a navigation tree and an editable document island.

Existing save, sync, rename and drag handlers live in notes.py. Shared visual
parts come from LumaUI; the app keeps its document and hierarchy semantics.
"""
from __future__ import annotations

import os
import json
import uuid
import random
import sqlite3
from dataclasses import replace
from threading import Thread
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango
from luma_appkit import (
    ActionCenter, AddRow, AvatarStack, BarAction, BarMenu, Command, CommandGroup, CommandRegistry, ContentLitHeader,
    Collaborator, CornerPill, CountBadge, DetailsItem, EmptyState, FileCard, HeroTitleField, Island, Person,
    InlineRenameField, Mark, MarkButton, MarkValue, Menu, NoteCard, RowLead, SidebarRow, NavigationSidebar, PersonAvatar, SaveStatus, ScrollView,
    SEPARATOR, SelectionBubble, ShareSheet, ShareSubject, ShareResult, SidebarFoot, Toast, ToastHost, apply_type, icons,
    lumaui_tokens, type_font, type_metrics,
)
from luma_appkit.action_bubble import FloatingMenu, SearchMenu, MenuItem
from luma_appkit.rows_menu import RichMenuItem
from .notes import NotesWindow, NoteRow, FolderRow, UNTITLED, _last_saved_label
from luma_appkit.rows_navigation import append_section
from .notes_backend import Note, Folder
from .notes_richtext import RichTextPage, INLINE_STYLES
from .notes_phone import NotesPhone


# v71 phone: the note's title starts 20 under the title island (top 52, 48 tall).
PHONE_TITLE_TOP = 74


class _ToastBridge:
    """Keep existing event handlers while the kit owns every toast pixel."""
    def __init__(self, region):
        self.region = region

    def add_toast(self, toast):
        undo = (lambda: toast.emit("button-clicked")) if toast.get_button_label() else None
        Toast.show(self.region, toast.get_title(), kind="deleted" if undo else "warning", undo=undo)


class _SaveStatusBridge:
    """The existing save controller reports through Notes' visible kit parts."""
    def __init__(self, window):
        self.window, self.error = window, None

    def pending(self):
        if self.window.fixture and self.window.current:
            raw = self.window.store.raw_notes.get(self.window.current.id)
            if raw is not None:
                raw['at'] = 'Just now'
        if hasattr(self.window, 'edited_label'):
            self.window.edited_label.set_label('Edited just now')

    def saved(self, *_args):
        self.error = None

    def failed(self, message):
        if message != self.error:
            Toast.show(self.window.document_host, 'Could not save: ' + message, kind='error')
            self.error = message


class _NotesGrain(Gtk.Widget):
    """A fine, stable dither over the page light so its long fade stays smooth."""

    __gtype_name__ = "LumaNotesGrain"

    _grain = None

    def __init__(self):
        super().__init__(can_target=False, hexpand=True, valign=Gtk.Align.START,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)

    def do_measure(self, orientation, _for_size):
        return (380, 380, -1, -1) if orientation == Gtk.Orientation.VERTICAL else (0, 0, -1, -1)

    @classmethod
    def _grain_texture(cls):
        if cls._grain is None:
            rng = random.Random(0x6E6F7465)
            pixels = bytearray()
            for _ in range(64 * 64):
                light = rng.getrandbits(1)
                pixels.extend((255 if light else 0, 255 if light else 0,
                               255 if light else 0, 10))
            cls._grain = Gdk.MemoryTexture.new(64, 64, Gdk.MemoryFormat.R8G8B8A8,
                                               GLib.Bytes.new(bytes(pixels)), 64 * 4)
        return cls._grain

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        bounds = Graphene.Rect().init(0, 0, width, height)
        round_corner = Graphene.Size().init(12, 12)
        square_corner = Graphene.Size().init(0, 0)
        snapshot.push_rounded_clip(Gsk.RoundedRect().init(
            bounds, round_corner, round_corner, square_corner, square_corner))
        tile = Graphene.Rect().init(0, 0, 64, 64)
        snapshot.push_repeat(bounds, tile)
        snapshot.append_texture(self._grain_texture(), tile)
        snapshot.pop()
        snapshot.pop()


def _clear(box):
    while child := box.get_first_child():
        box.remove(child)


# The store retains numeric hues used by v70. The kit currently names its palette.
_HUES = {'blue': 220, 'teal': 175, 'green': 150, 'yellow': 60,
         'orange': 45, 'red': 20, 'pink': 330, 'purple': 285, 'violet': 260, 'grey': 0}


class _TreeRow(SidebarRow):
    """Kit pixels with the existing Notes rename, context and model handlers."""
    def __init__(self, item, window, *, draggable=True, **parts):
        self.window = window
        self.is_folder = isinstance(item, Folder)
        if self.is_folder:
            self.folder = item
        else:
            self.note = item
        super().__init__(item.name if self.is_folder else (item.title.strip() or "New note"),
                         drag=(('folder:' if self.is_folder else 'note:') + item.id) if draggable else None,
                         on_drop=(lambda value, where: window._tree_drop(self, value, where)) if draggable else None,
                         can_contain=True,
                         can_drop=lambda value, where: window._tree_can_drop(self, value, where), **parts)
        if self.depth == 0 and parts.get('expanded') is None and hasattr(self, 'twisty'):
            self.twisty.set_visible(False)
        # A nonvisual stack preserves inline editing without replacing the kit row.
        label_parent = self.title_label.get_parent()
        label_parent.remove(self.title_label)
        self.title_stack = Gtk.Stack(hexpand=True)
        self.title_stack.add_named(self.title_label, 'label')
        label_parent.prepend(self.title_stack)
        self.label_stack = self.title_stack
        self.title = self.title_label
        self.label = self.title_label
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect('pressed', self._context)
        self.add_controller(secondary)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect('pressed', lambda _g, count, _x, _y: self.begin_rename() if count == 2 else None)
        self.add_controller(click)

    def update(self, note):
        self.note = note
        self.set_title(note.title.strip() or "New note")

    def begin_rename(self):
        if self.is_folder:
            self.window.cancel_folder_toggle()
            FolderRow.begin_rename(self)
        else:
            if self.window._can_edit_note_content(self.note.id):
                NoteRow.begin_rename(self)

    def is_renaming(self):
        return self.title_stack.get_visible_child_name() == 'rename'

    def _context(self, gesture, _count, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        commands = (self.window.folder_commands(self.folder) if self.is_folder
                    else self.window.note_commands(self.note))
        self.window.show_menu(commands, self, x, y)


class NotesLumaWindow(NotesPhone, NotesWindow):
    def _application_commands(self):
        groups = []
        for group in super()._application_commands().groups:
            commands = []
            for command in group.commands:
                if command.id == 'notes.new':
                    command = replace(command, label='New note')
                elif command.id == 'folders.new':
                    command = replace(command, execute=self._new_folder_form, icon='folder-plus')
                commands.append(command)
            groups.append(replace(group, commands=tuple(commands)))
        groups.append(CommandGroup(None, (
            Command('notes.sidebar', 'Show or hide sidebar', lambda: self.sidebar_toggle.toggle(), 'panel-left'),
        )))
        return CommandRegistry(groups)

    def __init__(self, application):
        self.open_nodes = set()
        self.fixture_sharing = {}
        self._page_mark_subject_id = None
        self._document_selection = False
        self._ui_loading = True
        super().__init__(application)
        self._ui_loading = False
        if not self.fixture:
            try:
                from .collaboration import CollaborationCache
                cache = CollaborationCache()
                path = cache.path
                cache.close()
                self._collaboration_monitor = Gio.File.new_for_path(str(path.parent)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
                self._collaboration_monitor.set_rate_limit(100)
                def changed(_monitor, file, _other, _event):
                    if (file.get_basename() in (path.name, path.name + '-wal') and _event in (Gio.FileMonitorEvent.CHANGED, Gio.FileMonitorEvent.CHANGES_DONE_HINT) and not getattr(self, '_closed', False)):
                        self._reload_sidebar()
                        self._apply_collaboration_role()
                self._collaboration_monitor.connect('changed', changed)
            except (OSError, GLib.Error):
                pass
        if self.fixture:
            self.connect("map", lambda *_: GLib.idle_add(self._fixture_focus))

    def _close_notes(self, window):
        monitor = getattr(self, '_collaboration_monitor', None)
        if monitor is not None:
            monitor.cancel()
            self._collaboration_monitor = None
        return super()._close_notes(window)

    def _fixture_focus(self):
        if os.environ.get('LUMA_NOTES_QUERY'):
            self.toggle_search()
        elif os.environ.get('LUMA_NOTES_SELECTION'):
            start, end = (int(value) for value in os.environ['LUMA_NOTES_SELECTION'].split(':'))
            self.editor.grab_focus()
            self.buffer.select_range(self.buffer.get_iter_at_offset(start), self.buffer.get_iter_at_offset(end))
            self._document_selection = True
            self._refresh_format_buttons()
        else:
            self.set_focus(None)
            self._document_selection = False
            self._refresh_format_buttons()
        if self.phone:
            self._phone_fixture_state()
        if folder_id := os.environ.get('LUMA_NOTES_CONTEXT_FOLDER'):
            folder = self.store.get_folder(folder_id)
            self._menu(self.folder_commands(folder), self.folder_rows[folder_id])
        return False

    def _build_layout(self, sidebar, editor):
        # WindowIdentity already owns the application menu.
        self.set_show_menubar(False)
        # The lists: the folder tree on a computer, the cards on a phone (NotesPhone). ListFirst puts
        # them beside the note on a computer and pushes the note over them on a phone (v71).
        from luma_appkit import ListFirst
        self.lists = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.lists.append(self.sidebar)
        self.document_host.set_hexpand(True)
        self.layout = ListFirst(self.lists, self.document_host, push_on_activate=False)
        self.set_body(self.layout)
        self.toast_overlay = _ToastBridge(self.document_host)
        self.sidebar.set_size_request(284, -1)
        self.sidebar.set_margin_end(0)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 899px"))
        narrow.add_setter(self.sidebar, "width-request", 240)
        narrow.add_setter(self.canvas, "margin-start", 24)
        narrow.add_setter(self.canvas, "margin-end", 24)
        self.add_breakpoint(narrow)
        # F9 shows and hides the sidebar; v71 draws no toggle in a desktop title row (Filer, Notes).
        from luma_appkit import SidebarToggle
        self._sidebar_shown = True
        self.sidebar_toggle = SidebarToggle(self.sidebar, closed=self._sidebar_closed)
        self.sidebar_toggle.set_name('nt-sidebar-toggle')
        self.sidebar_toggle.set_control_visible(False)
        self.set_leading(self.sidebar_toggle)
        from luma_appkit.content_type import type_class
        page_title = GObject.Value(GObject.TYPE_STRV,
                                   [c for c in self.title_entry.get_css_classes() if c != type_class('document-title')]
                                   + [type_class('document-title-compact')])
        # v71 compact (720 and under): the sidebar folds away (NotesPhone._fold_compact); F9 brings it back.
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
        compact.add_setter(self.sidebar, "width-request", 240)
        for side in ("start", "end"):
            compact.add_setter(self.canvas, "margin-" + side, 18)
        compact.add_setter(self.canvas, "margin-top", 32)
        compact.add_setter(self.canvas, "margin-bottom", 130)
        compact.add_setter(self.title_entry, 'css-classes', page_title)
        self.add_breakpoint(compact)
        # v71 phone (under 560): the 16 gutter, the title 20 under the title island, and the
        # bottom room is the bar's safe area (LumaUI), not a guessed padding.
        # (The phone shape hides the sidebar itself: the toggle's active state at phone width is
        # its drawer, which v71 Notes does not have.)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 559px"))
        for side in ("start", "end"):
            phone.add_setter(self.canvas, "margin-" + side, 16)
        phone.add_setter(self.canvas, "margin-top", PHONE_TITLE_TOP)
        phone.add_setter(self.canvas, "margin-bottom", 0)
        phone.add_setter(self.title_entry, 'css-classes', page_title)
        self.add_breakpoint(phone)
        self._install_phone()

    def _sidebar_closed(self, shown):
        self._sidebar_shown = shown
        self._sidebar_room()

    def _build_sidebar(self):
        self.sidebar = NavigationSidebar(variant="tree")
        self.sidebar.set_name("nt-sidebar")
        self.sidebar_list = self.sidebar.list
        self.sidebar_list.connect("row-activated", self._row_activated)
        root_drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        root_drop.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        root_drop.connect('drop', self._blank_root_drop)
        self.sidebar_list.add_controller(root_drop)
        self.sidebar_scroll = self.sidebar_list.get_ancestor(Gtk.ScrolledWindow)
        self.foot = SidebarFoot(search="Search notes", on_search=lambda _text: self._reload_sidebar(),
                                add=("New note", "square-pen", self.create_note))
        self.foot.set_name("nt-foot")
        self.search = self.foot.entry
        self.search.connect('notify::has-focus', self._format_focus)
        search_keys = Gtk.EventControllerKey()
        search_keys.connect('key-pressed', self._search_key)
        self.search.add_controller(search_keys)
        self.search.set_name("nt-search")
        self.foot.add_button.set_name("nt-new")
        self.sidebar.append_footer(self.foot)
        if self.fixture:
            self.open_nodes = set(self.store.document["open"])
            selected = self.store.get_note(os.environ.get('LUMA_NOTES_SELECTED', self.store.selected))
            folder_id = selected.folder_id
            while folder_id:
                self.store.set_folder_expanded(folder_id, True)
                folder_id = self.store.folder_parents().get(folder_id)
            parent_id = self.store.parent_of(selected.id)
            while parent_id:
                self.open_nodes.add('n' + parent_id)
                parent_id = self.store.parent_of(parent_id)
        if self.fixture and os.environ.get('LUMA_NOTES_QUERY'):
            self.search.set_text(os.environ['LUMA_NOTES_QUERY'])
        return self.sidebar

    def _build_editor(self):
        island = Island()
        island.set_name("nt-island")
        self.document_host = ToastHost(island)
        self.light = ContentLitHeader(tone="create")
        self.light.set_name("nt-light")
        self.document_layers = Gtk.Overlay(hexpand=True, vexpand=True)
        island.append(self.document_layers)
        document = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.document_layers.set_child(document)
        self.document_layers.add_overlay(self.light)
        self.document_layers.set_measure_overlay(self.light, False)
        self.grain = _NotesGrain()
        self.document_layers.add_overlay(self.grain)
        self.document_layers.set_measure_overlay(self.grain, False)
        self.canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                              margin_top=32, margin_bottom=140, margin_start=40, margin_end=40)
        self.canvas.set_name("nt-page")
        self.canvas.add_css_class("lumaui-document-surface")
        # A 700 px pad includes its 40 px margins; text is at most 620 px.
        clamp = Adw.Clamp(maximum_size=700, tightening_threshold=700)
        clamp.set_child(self.canvas)
        self.meta = Gtk.Box(spacing=10, margin_bottom=14)
        self.meta.set_name("nt-meta")
        self.meta.set_size_request(-1, 26)
        self.canvas.append(self.meta)
        from inspect import signature
        self._page_mark_kind = 'kind' in signature(MarkButton.__init__).parameters
        self.mark = MarkButton(on_change=lambda value: self._save_mark("note", self._page_mark_subject_id, value),
                               label="Add icon", **({'kind': 'page'} if self._page_mark_kind else {}))
        self.mark.connect('clicked', self._page_mark_picker_opened)
        self.mark.set_halign(Gtk.Align.START)
        self.mark.set_name("nt-mark")
        self.mark.add_css_class("nt-mark")
        self.mark_slot = Gtk.Box(height_request=28, margin_bottom=6)
        self.mark_slot.append(self.mark)
        self.canvas.append(self.mark_slot)
        # The title is the document's first editable block. HeroTitleField's
        # editable mode is the framed Contacts rename field, not this surface.
        self.title_entry = Gtk.Entry(placeholder_text="Title")
        self.title_entry.add_css_class("nt-document-title")
        apply_type(self.title_entry, "document-title")
        self.title_entry.set_halign(Gtk.Align.FILL)
        self.title_entry.set_hexpand(True)
        self.title_entry.set_alignment(0)
        self.title_entry.set_width_chars(1)
        self.title_entry.set_name("nt-title")
        self.title_entry.set_margin_bottom(14)
        self.title_entry.connect("changed", self._content_changed)
        self.title_entry.connect("activate", lambda *_: self.focus_body_start())
        self.title_entry.connect('notify::has-focus', self._format_focus)
        title_keys = Gtk.EventControllerKey()
        title_keys.connect('key-pressed', self._title_key)
        self.title_entry.add_controller(title_keys)
        self.canvas.append(self.title_entry)
        self.page = RichTextPage(on_changed=self._content_changed, on_caret=self._refresh_format_buttons,
                                 picture_commands=self._show_picture_bar,
                                 on_problem=lambda text: Toast.show(self.document_host, text, kind="error"))
        self.page.file_widget_factory = self._file_widget
        if self.fixture:
            self.page.picture_paths = self.store.picture_paths
        else:
            self.page.watch_sync_status()
        self.buffer, self.editor = self.page.buffer, self.page.view
        self.editor.connect('notify::has-focus', self._format_focus)
        self.editor.set_name("nt-doc")
        apply_type(self.editor, "body")
        self.editor.set_vexpand(False)
        self.editor.set_bottom_margin(0)
        self.editor.set_hexpand(True)
        self.page.on_picture_menu = self._picture_menu
        self._apply_document_roles()
        slash_keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        slash_keys.connect("key-pressed", self._slash_key)
        self.editor.add_controller(slash_keys)
        self.slash_menu = None
        self.selection_style_picker = BarMenu("Text", self._style_menu, name="Paragraph style")
        self.selection_bubble = SelectionBubble(self.editor, [self.selection_style_picker, SEPARATOR] + [
            BarAction(icon, tooltip=label, on_activate=lambda s=style: self._tool(s))
            for style, icon, label in (("bold", "bold", "Bold"), ("italic", "italic", "Italic"),
                                       ("highlight", "highlighter", "Highlight"), ("link", "link", "Link"))
        ] + [SEPARATOR, BarAction("ellipsis", tooltip="More formatting", on_activate=self._more_formatting)])
        self.selection_bubble.set_name("nt-selection")
        self.selection_bubble.get_last_child().set_name('nt-selection-more')
        for button in _children(self.selection_bubble):
            if isinstance(button, Gtk.Button) and button.get_tooltip_text() in ('Bold', 'Italic', 'Highlight', 'Link'):
                button.set_name('nt-selection-' + button.get_tooltip_text().lower())
        self.picture_bar = None
        self.canvas.append(self.editor)
        self.kids = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=28)
        self.kids.set_name("nt-kids")
        self.canvas.append(self.kids)
        self._shared_comments = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self._shared_comments.set_name('nt-collaboration-comments')
        self.canvas.append(self._shared_comments)
        self._shared_comment_drafts = {}
        self.body_scroll = ScrollView(clamp, vexpand=True)
        self.body_scroll.set_name("nt-scroll")
        document.append(self.body_scroll)
        self.empty_state = EmptyState("No notes yet", "", "lumaui-file-text-symbolic",
                                      primary=("New note", self.create_note))
        self.empty_state.set_visible(False)
        document.append(self.empty_state)
        self.corner = None
        self.format_toolbar = ActionCenter().attach(self.document_host)
        self.format_toolbar.set_name("nt-bar")
        self.format_buttons = {}
        self.save_status = _SaveStatusBridge(self)
        self._show_tools()
        GLib.idle_add(self._sync_editor_tag_colors)
        return self.document_host

    def _slash_key(self, _controller, keyval, _code, state):
        if keyval != Gdk.KEY_slash or state & (Gdk.ModifierType.CONTROL_MASK |
                Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        if self.current is None or not self.editor.get_editable() or self.buffer.get_has_selection():
            return False
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        start = cursor.copy()
        start.set_line_offset(0)
        if self.buffer.get_text(start, cursor, True).replace('\u200b', '').strip():
            return False  # URLs and ordinary prose keep their literal slash.
        offset = cursor.get_offset()
        self.buffer.insert(cursor, '/')
        mark = self.buffer.create_mark(None, self.buffer.get_iter_at_offset(offset), True)
        subject = self.current.id
        def apply(action):
            try:
                if self.current is None or not self.editor.get_editable() or self.current.id != subject or mark.get_deleted():
                    return
                first = self.buffer.get_iter_at_mark(mark)
                last = first.copy()
                last.forward_char()
                if self.buffer.get_text(first, last, True) != '/':
                    return
                self.buffer.begin_user_action()
                try:
                    self.buffer.delete(first, last)
                    self.buffer.place_cursor(self.buffer.get_iter_at_mark(mark))
                    action()
                finally:
                    self.buffer.end_user_action()
                self.editor.grab_focus()
            finally:
                if not mark.get_deleted():
                    self.buffer.delete_mark(mark)
        self.slash_menu = SearchMenu([
            MenuItem(label, icon=icon, on_activate=lambda cb=callback: apply(cb))
            for label, icon, callback in (
                ('Image', 'image', self._choose_picture_file),
                ('H1 · Heading', 'heading', lambda: self._set_block_style('heading-1')),
                ('Bold', 'bold', lambda: self._tool('bold')),
                ('Numbered list', 'list-ordered', lambda: self._tool('numbered')),
                ('Bulleted list', 'list', lambda: self._tool('bulleted')),
                ('Checklist', 'list-checks', lambda: self._tool('checklist')),
                ('Link', 'link', lambda: self._tool('link')),
            )], label='Insert into note',
            on_cancel=lambda: self.buffer.delete_mark(mark) if not mark.get_deleted() else None)
        self.slash_menu.set_name('nt-slash-menu')
        self.slash_menu.popup(self.editor, align='start')
        return True

    def _apply_document_roles(self):
        """Only kit typography tokens determine document block fonts."""
        apply_type(self.editor, 'reading')
        apply_type(self.title_entry, 'document-title')
        self.page.tags['bold'].set_property('weight', type_metrics('assistant-title')['weight'])
        for style, role in (('heading-1', 'title-1'), ('heading', 'assistant-title'), ('quote', 'reading')):
            font = type_font(role)
            if style == 'quote':
                font.set_style(Pango.Style.ITALIC)
            tag = self.page.tags[style]
            tag.set_property('scale-set', False)
            tag.set_property('font-desc', font)
            tag.set_property('weight', type_metrics(role)['weight'])
            tracking = round(type_metrics(role)['tracking'] * Pango.SCALE)
            # GtkTextTag rejects negative spacing. Keep the requested negative
            # tracking open in notes-01; an invalid setter cannot render it.
            if tracking >= 0:
                tag.set_property('letter-spacing', tracking)
        # apply_type already supplies reading's line height to GtkTextView.
        # Extra wrap pixels would apply that spacing a second time.
        self.editor.set_pixels_inside_wrap(0)
        from luma_appkit import DocumentBlockLayout
        from .notes_richtext import level_of
        def block_layout(line):
            block = self.page.block_of_line(line)
            kind = 'heading' if 'heading-1' in block else next((kind for kind in ("heading", "quote", "checklist", "bulleted", "numbered") if kind in block), "paragraph")
            return kind, level_of(block)
        self.document_layout = DocumentBlockLayout(self.editor, block_layout)

    def _show_tools(self):
        self.style_picker = BarMenu("Text", self._style_menu, name="Paragraph style")
        items = [self.style_picker, SEPARATOR]
        for style, icon, label in (("bold", "bold", "Bold"), ("italic", "italic", "Italic"),
                                   ("strike", "strikethrough", "Strikethrough"),
                                   ("highlight", "highlighter", "Highlight"), ("link", "link", "Link")):
            items.append(BarAction(icon, tooltip=label, on_activate=lambda s=style: self._tool(s)))
        items.append(SEPARATOR)
        for style, icon, label in (("checklist", "list-checks", "Checklist"),
                                   ("bulleted", "list", "Bulleted list"),
                                   ("numbered", "list-ordered", "Numbered list")):
            items.append(BarAction(icon, tooltip=label, on_activate=lambda s=style: self._tool(s)))
        items.extend([SEPARATOR, BarAction("plus", tooltip="Insert", on_activate=self._insert_menu)])
        self.tool_items = items
        self._render_tools()

    def _render_tools(self):
        if self.current is None or not self.editor.get_editable():
            self.format_toolbar.hide_bar()
            return
        if getattr(self, 'phone', False):
            block = self.page.block_of_line(self.page.cursor_line()) if self._document_selection else set()
            self.phone_tool_items = self._phone_tool_items(block)
            self.format_toolbar.show_bar(self.phone_tool_items, toolbar=True)
            for child in _children(self.format_toolbar.bar_row):
                item = getattr(child, "bar_item", None)
                if item:
                    child.set_name("nt-tool-" + (item.tooltip or item.label).lower().replace(" ", "-"))
            return
        self.format_toolbar.show_bar(self.tool_items)
        self.style_picker.widget.set_name("nt-tool-text")
        for child in _children(self.format_toolbar.bar_row):
            item = getattr(child, "bar_item", None)
            if item:
                child.set_name("nt-tool-" + (getattr(item, "tooltip", None) or item.label).lower().replace(" ", "-"))

    def _tool(self, style):
        if self.current is None or not self.editor.get_editable():
            return
        if style == "link":
            self._show_link_popover(self.editor)
        elif style in INLINE_STYLES or style in ("bulleted", "numbered", "checklist"):
            self._toggle_format(style)
        else:
            self._pending_part("notes-01-document-surface.md")

    def _show_link_popover(self, anchor):
        if self.current is None or not self.editor.get_editable():
            return
        if self.fixture:
            # The v70 demo link action uses this URL, on the actual selection.
            bounds = self.buffer.get_selection_bounds()
            if bounds:
                self.buffer.apply_tag(self.page.link_tag('https://simplyluma.com'), *bounds)
                self.page.changed()
            return
        from luma_appkit import TextField
        from luma_appkit.action_center import make_control
        from luma_appkit.rows_menu import MenuSection
        subject_id = self.current.id
        bounds = self.buffer.get_selection_bounds()
        offsets = tuple(it.get_offset() for it in bounds) if bounds else None
        revision = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        display = None
        if offsets is None:
            display = TextField('Text to display',
                                placeholder='Text to display')
            form.append(display)
        href = TextField('Link address',
                         placeholder='https://example.com')
        href.entry.set_name('nt-link-address')
        form.append(href)
        def apply():
            if self.current is None or not self.editor.get_editable() or self.current.id != subject_id:
                menu.close()
                return
            if self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True) != revision:
                Toast.show(self.document_host, 'The note changed. Select the text again.', kind='warning')
                menu.close()
                return
            from .notes import _normalize_href
            value = _normalize_href(href.text)
            if not value:
                Toast.show(self.document_host, 'Enter a valid web or email address', kind='warning')
                return
            self.buffer.begin_user_action()
            try:
                if offsets is None:
                    text = display.text.strip() or value
                    start = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
                    self.buffer.insert(self.buffer.get_iter_at_offset(start), text)
                    selected = (start, start + len(text))
                else:
                    selected = offsets
                self.buffer.apply_tag(self.page.link_tag(value),
                                      *(self.buffer.get_iter_at_offset(i) for i in selected))
            finally:
                self.buffer.end_user_action()
            self.page.changed()
            menu.close()
            self.editor.grab_focus()
        button = make_control(BarAction('', 'Add link', primary=True, on_activate=apply))
        button.set_name('nt-link-apply')
        form.append(button)
        menu = FloatingMenu([MenuSection(form, label='Add link')], label='Add link')
        href.entry.connect('activate', lambda _field: apply())
        menu.popup(anchor)
        href.grab_focus()

    def _style_menu(self, anchor=None):
        menu = FloatingMenu(["Style"] + [
            MenuItem(label, on_activate=lambda s=style: self._set_block_style(s))
            for style, label in (("heading", "Heading"), ("text", "Text"), ("quote", "Quote"))
        ], label="Paragraph style")
        menu.set_name("nt-style-menu")
        menu.popup(anchor or self.style_picker.widget, align="start")
        self._label_menu_controls({"Heading": "nt-style-heading", "Text": "nt-style-text", "Quote": "nt-style-quote"})

    def _set_block_style(self, style):
        if self.current is None or not self.editor.get_editable():
            return
        if self.current is None:
            return
        self.buffer.begin_user_action()
        try:
            for line in self.page.selected_lines():
                block = self.page.block_of_line(line) - {"heading", "heading-1", "quote"}
                self.page.set_line_block(line, block | ({style} if style != "text" else set()))
        finally:
            self.buffer.end_user_action()
        self.page.changed()
        self._refresh_format_buttons()
        self.editor.grab_focus()

    def _insert_menu(self):
        if self.current is None or not self.editor.get_editable():
            return
        registry = CommandRegistry((CommandGroup(None, (
            Command("insert.image", "Image…", self._choose_picture_file, "image"),
            Command("insert.file", "File…", self._choose_note_file, "paperclip"),
            Command("insert.divider", "Divider", self._insert_divider, "minus"),
        )), CommandGroup(None, (
            Command("insert.note", "Note inside this one", self._new_inside, "file-text"),
        ))))
        menu = self._menu(registry, self.format_toolbar.bar_row.get_last_child())
        menu.set_name('nt-insert-menu')
        names = {'Image…': 'nt-insert-image', 'File…': 'nt-insert-file',
                 'Divider': 'nt-insert-divider', 'Note inside this one': 'nt-insert-note-inside'}
        self._label_menu_controls(names)
        return menu

    def _choose_picture_file(self):
        if self.current is None or not self.editor.get_editable():
            return
        if not self.fixture:
            return super()._choose_picture_file()
        # v70's Image command inserts this sample. Refer to the fixture's
        # read-only asset; never import it into the user's attachments store.
        from .notes_fixture import DocumentParser
        parser = DocumentParser(self.store.path.parent)
        parser.feed('<img src="notes-v70/life-sync-terrace.webp">')
        self.store.picture_paths.update(parser.pictures)
        name = next(iter(parser.pictures))
        self.editor.grab_focus()
        self.page.insert_picture(name, width=620)

    def _file_widget(self, metadata):
        from .notes_files import file_path
        path = (self.store.path.parent / 'notes-v70' / metadata['src']) if self.fixture else file_path(metadata['src'])
        available = not self.fixture and path.is_file()
        def open_file():
            if self.fixture or not available:
                Toast.show(self.document_host, 'File not available on this device', kind='warning')
            else:
                Gtk.UriLauncher.new(path.as_uri()).launch(self, None, self._file_opened)
        card = FileCard(str(path), name=metadata['name'], size=metadata['size'],
                        subtitle='Local attachment' if available else 'File not available on this device',
                        tone=None if available else 'warning', on_open=open_file)
        card.set_name('nt-file-' + metadata['src'][:12])
        return card

    def _file_opened(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error as error:
            Toast.show(self.document_host, str(error), kind='error')

    def _choose_note_file(self):
        if self.current is None or not self.editor.get_editable():
            return
        if self.fixture:
            Toast.show(self.document_host, 'Choose a file to attach')
            return
        if self.current is not None:
            note_id = self.current.id
            dialog = Gtk.FileDialog(title='Attach file')
            dialog.open(self, None, lambda chooser, result: self._note_file_chosen(chooser, result, note_id))

    def _note_file_chosen(self, dialog, result, note_id):
        try:
            chosen = dialog.open_finish(result)
        except GLib.Error:
            return
        path = chosen.get_path()
        if path is None:
            Toast.show(self.document_host, 'Choose a local file', kind='warning')
            return
        # Read/copy off the GTK thread. A completed chooser belongs to the
        # original document even if another note was opened meanwhile.
        def copy_file():
            from .notes_files import store_file
            try:
                metadata, error = store_file(path), None
            except (OSError, ValueError) as failure:
                metadata, error = None, str(failure)
            GLib.idle_add(self._note_file_ready, note_id, metadata, error)
        Thread(target=copy_file, daemon=True).start()

    def _note_file_ready(self, note_id, metadata, error):
        if not self.get_mapped():
            return False
        if error:
            Toast.show(self.document_host, error, kind='error')
            return False
        try:
            stored = self.store.get_note(note_id)
            if stored.deleted_at is not None or self._collaboration_role(note_id) not in ('owner', 'edit', 'revoked'):
                return False
            if self.current and self.current.id == note_id:
                self.page.insert_file(metadata)
                self._flush_save()
            else:
                prefix = stored.body + ('\n' if stored.body and not stored.body.endswith('\n') else '')
                run = dict(metadata, start=len(prefix), end=len(prefix) + 1, style='file')
                self.store.update_note(note_id, title=stored.title, body=prefix + '\ufffc\n',
                                       runs=stored.runs + (run,))
                self._reload_sidebar()
        except (KeyError, OSError, ValueError, sqlite3.Error) as failure:
            Toast.show(self.document_host, str(failure), kind='error')
        return False

    def _insert_divider(self):
        if self.current is None or not self.editor.get_editable():
            return
        if self.current is None:
            return
        self.buffer.begin_user_action()
        try:
            if self.buffer.get_has_selection():
                self.buffer.delete_selection(True, True)
            line = self.page.cursor_line()
            block = self.page.block_of_line(line)
            self.buffer.insert_at_cursor('\n\n', -1)
            if self.buffer.get_iter_at_mark(self.buffer.get_insert()).ends_line():
                block = frozenset()
            self.page.set_line_block(line + 1, frozenset({'divider'}))
            self.page.set_line_block(line + 2, block)
        finally:
            self.buffer.end_user_action()
        self.page.changed()
        self.editor.grab_focus()

    def _more_formatting(self):
        menu = FloatingMenu(['Format'] + [
            MenuItem(label, icon=icon, on_activate=lambda s=style: self._tool(s))
            for style, label, icon in (("underline", "Underline", "underline"),
                                       ("strike", "Strikethrough", "strikethrough"),
                                       ("bulleted", "Bulleted list", "list"),
                                       ("numbered", "Numbered list", "list-ordered"))
        ] + [MenuItem("Plain text", icon='type', on_activate=self._plain_text)], label="Format")
        menu.set_name('nt-format-menu')
        menu.popup(self.selection_bubble.get_last_child())
        self._label_menu_controls({label: 'nt-format-' + key for key, label in
                                   (('underline', 'Underline'), ('strike', 'Strikethrough'),
                                    ('bulleted', 'Bulleted list'), ('numbered', 'Numbered list'),
                                    ('plain', 'Plain text'))})
        return menu

    def _plain_text(self):
        if self.current is None or not self.editor.get_editable():
            return
        if self.current is None:
            return
        bounds = self.buffer.get_selection_bounds()
        if not bounds:
            # A collapsed selection changes the next typed characters, never
            # strips formatting from the rest of the saved document.
            self.page.pending.update({style: False for style in INLINE_STYLES})
            self.editor.grab_focus()
            self._refresh_format_buttons()
            return
        start, end = bounds
        self.buffer.begin_user_action()
        try:
            for style in INLINE_STYLES:
                self.buffer.remove_tag(self.page.tags[style], start, end)
        finally:
            self.buffer.end_user_action()
        self.page.changed()
        self.editor.grab_focus()

    def _menu(self, registry, anchor):
        rows, commands = [], []
        for group in registry.visible_groups():
            if rows:
                rows.append(None)
            if group.label:
                rows.append(group.label)
            for command in group.commands:
                rows.append(RichMenuItem(command.label, icon=command.icon,
                            danger=command.destructive,
                            checked=bool(command.checked and command.checked()),
                            on_activate=lambda cid=command.id: registry.invoke(cid)))
                commands.append(command)
        menu = FloatingMenu(rows)
        for button, command in zip(menu.buttons, commands):
            button.set_name('nt-command-' + command.id.replace('.', '-'))
            button.set_sensitive(bool(command.enabled()))
        menu.popup(anchor)
        self._label_menu_controls({command.label: 'nt-command-' + command.id.replace('.', '-')
                                   for command in commands})
        return menu

    def show_menu(self, registry, parent, _x=0, _y=0):
        # Tree and image context actions use the same public shared menu route
        # as Insert; the inherited command_popover is the legacy renderer.
        return self._menu(registry, parent)

    def _picture_registry(self, picture):
        registry = super()._picture_registry(picture)
        subject_id = self.current.id if self.current else None

        def active_subject():
            return bool(self.get_mapped() and self.current
                        and self.current.id == subject_id and picture in self.page.pictures)

        return CommandRegistry(replace(group, commands=tuple(
            replace(command, enabled=lambda enabled=command.enabled, cid=command.id:
                    active_subject() and (cid == 'picture.copy' or self.editor.get_editable()) and enabled())
            for command in group.commands)) for group in registry.groups)

    def _show_picture_bar(self, picture):
        if self.picture_bar is not None:
            self.picture_bar.popdown()
        menu = self._menu(self._picture_registry(picture), picture)
        menu.set_name('nt-picture-menu')
        # RichTextPage's existing controller expects a popdown method. This
        # nonvisual adapter preserves it while FloatingMenu owns presentation.
        menu.popdown = menu.close
        self.picture_bar = self.page.picture_bar = menu

    def _picture_menu(self, picture, _x, _y):
        self._show_picture_bar(picture)

    def _label_menu_controls(self, names):
        for widget in _descendants(self):
            # Shared phone drawers create fresh menu rows. Name those actual
            # rows without renaming the anchor or other same-label controls.
            if isinstance(widget, Gtk.Button) and widget.get_accessible_role() in (
                    Gtk.AccessibleRole.MENU_ITEM, Gtk.AccessibleRole.MENU_ITEM_CHECKBOX):
                labels = [child.get_text() for child in _descendants(widget) if isinstance(child, Gtk.Label)]
                for label in labels:
                    if label in names:
                        widget.set_name(names[label])
                        break

    def _label_mark_controls(self):
        from luma_appkit import MarkPicker
        for widget in _descendants(self):
            if isinstance(widget, MarkPicker):
                widget.set_name('nt-mark-picker')
                for child in tuple(_children(widget.body)):
                    if isinstance(child, Gtk.ScrolledWindow) and not isinstance(child, ScrollView):
                        content = child.get_child()
                        before = child.get_prev_sibling()
                        if isinstance(content, Gtk.Viewport):
                            content = content.get_child()
                            child.get_child().set_child(None)
                        child.set_child(None)
                        widget.body.remove(child)
                        scroll = ScrollView(content, fade_top=True, fade_bottom=True,
                                            propagate_natural_height=True, max_content_height=220)
                        scroll.add_css_class('notes-mark-scroll')
                        widget.body.insert_child_after(scroll, before)
                for kind, button in widget.tabs.items():
                    button.set_name('nt-mark-tab-' + kind)
                    if not getattr(button, '_notes_mark_named', False):
                        button._notes_mark_named = True
                        button.connect('toggled', lambda _b: self._label_mark_controls())
                for button in widget.grid_buttons:
                    key = button.get_label() if widget.tab == 'emoji' else button.get_tooltip_text().replace(' ', '-')
                    button.set_name('nt-mark-choice-' + key)
                for button in _descendants(widget):
                    if (isinstance(button, Gtk.Button)
                            and button.get_accessible_role() == Gtk.AccessibleRole.RADIO
                            and button.get_tooltip_text()):
                        button.set_name('nt-mark-hue-' + button.get_tooltip_text().lower())
        return False

    def _page_mark_picker_opened(self, _button):
        self._page_mark_subject_id = self.current.id if self.current else None
        self._label_mark_controls()

    def _label_share_controls(self):
        sheet = self.share_sheet
        if sheet.mode_switch is not None:
            for key, button in sheet.mode_switch.buttons.items():
                button.set_name('nt-share-' + key)
        if sheet.invite is not None:
            sheet.invite.set_name('nt-share-query')
        role = 0
        for widget in _descendants(sheet):
            if isinstance(widget, Gtk.Button):
                if widget.has_css_class('lumaui-share-role'):
                    widget.set_name('nt-share-role-' + str(role))
                    role += 1
                    if not getattr(widget, '_notes_menu_named', False):
                        widget._notes_menu_named = True
                        widget.connect('clicked', lambda _b: self._label_menu_controls(
                            {'Can edit': 'nt-share-permission-edit', 'Can comment': 'nt-share-permission-comment',
                             'Can view': 'nt-share-permission-view', 'Remove': 'nt-share-permission-remove'}))
                elif widget.has_css_class('lumaui-share-link-picker'):
                    widget.set_name('nt-share-link-picker')
                    if not getattr(widget, '_notes_menu_named', False):
                        widget._notes_menu_named = True
                        widget.connect('clicked', lambda _b: self._label_menu_controls(
                            {'Only people with access': 'nt-share-access-off',
                             'Anyone with the link can view': 'nt-share-access-view',
                             'Anyone with the link can edit': 'nt-share-access-edit'}))
                elif widget.has_css_class('lumaui-share-chip'):
                    widget.set_name('nt-share-copy-link')
                elif widget.has_css_class('lumaui-share-suggestion'):
                    widget.set_name('nt-share-suggestion')
                elif widget.has_css_class('lumaui-share-tile'):
                    for child in _descendants(widget):
                        if isinstance(child, Gtk.Label) and child.has_css_class('lumaui-share-tile-label'):
                            widget.set_name('nt-share-target-' + child.get_text().lower().replace(' ', '-'))
        for key, button in getattr(sheet, 'action_buttons', {}).items():
            button.set_name('nt-share-action-' + key)
        return False

    def _pending_part(self, request):
        # TODO(kit-request): replace with the coordinator's semantic API.
        Toast.show(self.document_host, "This control is not available yet", kind="warning")

    def _reload_sidebar(self, select_id=None):
        self._reload_tree(select_id)
        if getattr(self, 'phone', False) and self.phone_page == 'list':
            self._render_phone_list()

    def _reload_tree(self, select_id=None):
        self.sidebar.clear()
        self.note_rows, self.folder_rows, self.folder_children = {}, {}, {}
        notes = self.store.list_notes(search=self.search.get_text())
        # Newly created notes lead their scope, as v70 nNew/NOTES.unshift.
        # Existing favorite/manual ordering is otherwise retained unchanged.
        notes = tuple(sorted(notes, key=lambda n: (0, n.sort_order) if n.sort_order < 0 else (1, 0)))
        parents = self.store.note_parents()
        selected = select_id or (self.current.id if self.current else None)
        if self.search.get_text():
            for note in notes:
                self._append_note_row(note, nested=False, selected=selected)
            if not notes:
                hint = apply_type(Gtk.Label(label='No notes match.', xalign=0), 'body', muted=True)
                row = Gtk.ListBoxRow(selectable=False, activatable=False)
                row.set_child(hint)
                self.sidebar_list.append(row)
            return
        pinned = [n for n in notes if n.favorite]
        if pinned:
            append_section(self.sidebar, "Pinned")
            for note in pinned:
                self._append_note_row(note, nested=False, selected=selected, pinned=True)
        section = append_section(self.sidebar, "Notes", action=("plus", "New note", self.create_note))
        section.action_button.set_name('nt-new-note')
        folder_parents = self.store.folder_parents()
        def tree(folder_id, depth):
            items = self.store.root_items() if folder_id is None else (
                tuple(f for f in self.store.root_items() if isinstance(f, Folder) and folder_parents.get(f.id) == folder_id)
                + tuple(n for n in notes if n.folder_id == folder_id and n.id not in parents))
            for item in items:
                if isinstance(item, Folder):
                    if folder_parents.get(item.id) != folder_id:
                        continue
                    row = _TreeRow(item, self, depth=depth, folder=True, expanded=item.expanded,
                                   mark=self._mark_value("folder", item.id), trail=self._folder_count(item.id, notes),
                                   on_expand=lambda expanded, fid=item.id: self._set_folder_expanded(fid, expanded),
                                   on_mark=lambda value, fid=item.id: self._save_mark("folder", fid, value))
                    row.set_name("nt-folder-" + item.id)
                    row.mark_button.set_name('nt-folder-mark-' + item.id)
                    row.mark_button.connect('clicked', lambda _b: self._label_mark_controls())
                    self.folder_rows[item.id] = row
                    self.sidebar_list.append(row)
                    if item.expanded:
                        tree(item.id, depth + 1)
                elif item.id not in shared_ids and not (self.fixture and self.store.raw_notes.get(item.id, {}).get("from")):
                    leaf(item, depth)
        def leaf(note, depth):
            row = self._append_note_row(note, nested=False, selected=selected, depth=depth)
            if "n" + note.id in self.open_nodes or not self.fixture:
                for child in notes:
                    if parents.get(child.id) == note.id:
                        leaf(child, depth + 1)
        shared_ids, invitations = set(), []
        if not self.fixture:
            try:
                from .collaboration import CollaborationCache, CollaborationClient
                client, cache = CollaborationClient(), CollaborationCache(read_only=True)
                try:
                    shared_ids = {r['local_id'] for r in cache.mappings(client, 'note') if r['role'] not in ('owner', 'revoked')}
                    invitations = cache.db.execute('SELECT * FROM invitations WHERE hub=? AND device=? AND kind=?', (client.address, client.identity.device_id, 'note')).fetchall()
                finally:
                    cache.close()
            except Exception:
                pass
        tree(None, 0)
        if not notes:
            hint = Gtk.ListBoxRow(selectable=False, activatable=False)
            hint.set_name('nt-sidebar-empty')
            hint.set_child(apply_type(Gtk.Label(label='No notes yet', xalign=0), 'caption', muted=True))
            self.sidebar_list.append(hint)
        shared = [n for n in notes if n.id in shared_ids or self.fixture and self.store.raw_notes.get(n.id, {}).get("from")]
        if shared:
            append_section(self.sidebar, "Shared with me")
            for note in shared:
                leaf(note, 0)
        if invitations:
            append_section(self.sidebar, "Invitations")
            for invitation in invitations:
                row = SidebarRow("Shared " + invitation['kind'])
                row.connect('activate', lambda _row, invitation=dict(invitation), anchor=row: self._menu(CommandRegistry((CommandGroup(None, (
                        Command('collaboration.accept', 'Accept invitation', lambda: self._accept_collaboration(invitation, True), 'check'),
                        Command('collaboration.decline', 'Decline invitation', lambda: self._accept_collaboration(invitation, False), 'x'),
                    )),)), anchor))
                row.set_name('nt-collaboration-invitation')
                self.sidebar_list.append(row)

    def _accept_collaboration(self, invitation, accept):
        def worker():
            from .collaboration import CollaborationClient, sync_notes_collaboration
            try:
                CollaborationClient().operation(invitation['id'], 'accept', {'accept': accept})
                sync_notes_collaboration(store_path=self.store.path)
                error = None
            except Exception as failure:
                error = str(failure)
            GLib.idle_add(done, error)
        def done(error):
            if error:
                Toast.show(self.document_host, error, kind='error')
            else:
                self._reload_sidebar()
                self._take_change_from_elsewhere()
            return False
        Thread(target=worker, daemon=True, name='notes-accept-collaboration').start()

    def _collaboration_role(self, note_id):
        if self.fixture:
            return 'owner'
        from .connect_sync import connect_data_directory, load_identity
        try:
            from .collaboration import CollaborationCache, CollaborationClient
            # Personal and revoked recoverable copies remain editable offline.
            # Only a current shared mapping needs current account metadata.
            path = connect_data_directory() / 'collaboration.sqlite3'
            if not path.exists():
                return 'owner'
            local = CollaborationCache(read_only=True)
            try:
                shared = local.db.execute("SELECT 1 FROM documents WHERE kind='note' AND local_id=? AND role!='revoked' LIMIT 1", (note_id,)).fetchone()
            finally:
                local.close()
            if shared is None:
                return 'owner'
            if load_identity() is None:
                return 'unknown'
            client, cache = CollaborationClient(), CollaborationCache(read_only=True)
            try:
                return next((row['role'] for row in cache.mappings(client, 'note') if row['local_id'] == note_id), 'owner')
            finally:
                cache.close()
        except Exception:
            return 'unknown'

    def _can_edit_note_content(self, note_id):
        return (super()._can_edit_note_content(note_id)
                and self._collaboration_role(note_id) in ('owner', 'edit', 'revoked'))

    def _apply_collaboration_role(self):
        role = self._collaboration_role(self.current.id) if self.current else 'owner'
        editable = role in ('owner', 'edit', 'revoked')
        self.editor.set_editable(editable)
        self.title_entry.set_editable(editable)
        self.format_toolbar.set_sensitive(editable)
        self._render_tools()
        self._refresh_collaboration_comments()

    def _refresh_collaboration_comments(self):
        if self.fixture or not self.current or not hasattr(self, '_shared_comments'):
            return
        from .collaboration import CollaborationClient, CollaborationCache
        snapshot = None
        try:
            client, cache = CollaborationClient(), CollaborationCache(read_only=True)
            try:
                mapping = next((r for r in cache.mappings(client, 'note') if r['local_id'] == self.current.id and r['role'] != 'revoked'), None)
                row = cache.db.execute('SELECT snapshot FROM snapshots WHERE id=?', (mapping['id'],)).fetchone() if mapping else None
                snapshot = json.loads(row[0]) if row else None
            finally:
                cache.close()
        except Exception:
            pass
        self._shared_comments.set_visible(snapshot is not None)
        if snapshot is None:
            return
        signature = (self.current.id, snapshot['role'], json.dumps(snapshot['comments'], sort_keys=True))
        if signature == getattr(self, '_shared_comments_signature', None):
            return
        self._shared_comments_signature = signature
        while child := self._shared_comments.get_first_child():
            self._shared_comments.remove(child)
        from luma_appkit import TextField, TextButton
        from .collaboration import public_name
        heading = Gtk.Label(label='Comments', xalign=0)
        apply_type(heading, 'label')
        self._shared_comments.append(heading)
        members = {m['account']: public_name(m) for m in snapshot['members']}
        for comment in snapshot['comments']:
            author = members.get(comment['account'], 'Luma user')
            words = Gtk.Label(label=author + '\n' + comment['body'], xalign=0, wrap=True, selectable=True)
            self._shared_comments.append(words)
        note_id, document_id = self.current.id, snapshot['id']
        composer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        field = TextField('Comment', placeholder='Add a comment', value=self._shared_comment_drafts.get(note_id, ''),
                          on_changed=lambda value: self._shared_comment_drafts.__setitem__(note_id, value))
        field.set_hexpand(True)
        field.set_name('nt-shared-comment')
        def submit(*_):
            body = field.text.strip()
            if not body or getattr(self, '_shared_comment_sending', False):
                return
            self._shared_comment_sending = True
            def worker():
                try:
                    client = CollaborationClient()
                    client.operation(document_id, 'comments', {'id': str(uuid.uuid4()), 'body': body})
                    latest = client.read(document_id)
                    cache = CollaborationCache()
                    try: cache.remember_snapshot(client, latest)
                    finally: cache.close()
                    error = None
                except Exception as failure:
                    error = str(failure)
                GLib.idle_add(done, error)
            def done(error):
                self._shared_comment_sending = False
                if getattr(self, '_closed', False):
                    return False
                if error:
                    Toast.show(self.document_host, error, kind='error')
                else:
                    if self._shared_comment_drafts.get(note_id, '').strip() == body:
                        self._shared_comment_drafts.pop(note_id, None)
                    self._shared_comments_signature = None
                    self._refresh_collaboration_comments()
                return False
            Thread(target=worker, daemon=True, name='notes-shared-comment').start()
        field.entry.connect('activate', submit)
        composer.append(field)
        composer.append(TextButton('Send', on_click=submit))
        composer.set_sensitive(snapshot['role'] in ('owner', 'edit', 'comment'))
        self._shared_comments.append(composer)

    def _append_note_row(self, note, *, nested, selected, depth=0, pinned=False):
        children = bool(self.store.descendants(note.id)) and not pinned
        search = bool(self.search.get_text())
        raw = self.store.raw_notes.get(note.id, {}) if self.fixture else {}
        people = self._fixture_people() if self.fixture else {}
        shares = raw.get('share', [])
        row = _TreeRow(note, self, depth=depth, draggable=not search,
                       lead=RowLead.mark(MarkValue('dot', self.store.folder_meta.get(note.folder_id, {}).get('hue', 20))) if search and self.fixture else None,
                       mark=None if search else self._mark_value('note', note.id),
                       expanded=('n' + note.id in self.open_nodes) if children and not search else None,
                       on_expand=lambda expanded: self._set_note_expanded(note.id, expanded),
                       on_mark=lambda value: self._save_mark('note', note.id, value),
                       meta=raw.get('at', '').replace('Today, ', '') if search else '',
                       subtitle=(' '.join(note.body.replace('\ufffc', '').split())[:80] or 'No text yet') if search else '',
                       trail=AvatarStack([people[k] for k in shares], size='row') if shares else None)
        row.set_name("nt-note-" + note.id)
        if row.mark_button is not None:
            row.mark_button.set_name('nt-note-mark-' + note.id)
            row.mark_button.connect('clicked', lambda _b: self._label_mark_controls())
        self.note_rows[note.id] = row
        self.sidebar_list.append(row)
        if note.id == selected:
            self.sidebar_list.select_row(row)
        return row

    def _create_folder_from_values(self, name, hue, parent_id=None):
        # Called only by the new-folder form's Create action.
        if parent_id is not None:
            self.store.get_folder(parent_id)
        folder = self.store.create_folder(name.strip() or 'New folder', parent_id=parent_id,
                                          mark={'kind': 'dot', 'hue': hue})
        if parent_id is not None:
            self.store.set_folder_expanded(parent_id, True)
        self.store.set_folder_expanded(folder.id, True)
        self._reload_sidebar()
        Toast.show(self.document_host, f'Created “{folder.name}”')
        return folder

    def _folder_count(self, folder_id, notes):
        parents = self.store.folder_parents()
        def inside(fid):
            while fid:
                if fid == folder_id:
                    return True
                fid = parents.get(fid)
            return False
        return sum(inside(note.folder_id) for note in notes)

    def _set_note_expanded(self, note_id, expanded):
        (self.open_nodes.add if expanded else self.open_nodes.discard)('n' + note_id)
        self._reload_sidebar()

    def _mark_value(self, kind, item_id):
        saved = self.store.get_mark(kind, item_id)
        if not saved:
            hue = self.store.folder_meta.get(item_id, {}).get('hue', 220) if self.fixture and kind == 'folder' else ('grey' if kind == 'note' else 220)
            saved = {'kind': 'dot' if kind == 'folder' else 'icon', 'hue': hue, 'value': 'file-text'}
        hue = saved['hue']
        return MarkValue(saved['kind'], hue, icon=saved.get('value') if saved['kind'] == 'icon' else None,
                         emoji=saved.get('value') if saved['kind'] == 'emoji' else None)

    def _save_mark(self, kind, item_id, value):
        if item_id is None:
            return
        saved = dict(self.store.get_mark(kind, item_id) or {})
        hue = 'grey' if value.hue == 'grey' else _HUES.get(value.hue, value.hue)
        if kind == 'folder':
            # v70 keeps FOLD's last colour when the mark becomes Grey or emoji.
            # Retain it in the same backed-up mark metadata, across restarts.
            saved['folder_hue'] = hue if value.kind != 'emoji' and hue != 'grey' else self._folder_hue(item_id)
        saved.update(kind=value.kind, hue=hue, value=value.icon or value.emoji or '')
        try:
            self.store.set_mark(kind, item_id, saved)
        except (KeyError, ValueError, OSError, sqlite3.Error) as error:
            Toast.show(self.document_host, 'Could not save mark: ' + str(error), kind='error')
            self._reload_sidebar()
            self._render_subject()
            return
        self._reload_sidebar()
        self._render_subject()

    def _folder_hue(self, folder_id):
        saved = self.store.get_mark('folder', folder_id) if folder_id else None
        if saved:
            if 'folder_hue' in saved:
                return saved['folder_hue']
            if saved['hue'] != 'grey':
                return saved['hue']
        return self.store.folder_meta.get(folder_id, {}).get('hue', 220) if self.fixture else 220

    def _root_owner(self, row):
        owner = super()._root_owner(row)
        if owner.startswith('folder:'):
            folder_id = owner.split(':', 1)[1]
            parents = self.store.folder_parents()
            while parents.get(folder_id):
                folder_id = parents[folder_id]
            return 'folder:' + folder_id
        return owner

    def _tree_can_drop(self, row, value, where):
        if not isinstance(value, str) or ':' not in value:
            return False
        kind, moving_id = value.split(':', 1)
        target = row.folder if row.is_folder else row.note
        if kind == 'folder':
            if not row.is_folder or moving_id == target.id:
                return False
            try:
                self.store.get_folder(moving_id)
            except KeyError:
                return False
            parents = self.store.folder_parents()
            ancestor = target.id if where == 'inside' else parents.get(target.id)
            while ancestor:
                if ancestor == moving_id:
                    return False
                ancestor = parents.get(ancestor)
            return True
        fraction = {'before': .1, 'inside': .5, 'after': .9}[where]
        return self._sidebar_drop_plan(row, value, max(1, row.get_height()) * fraction, 30) is not None

    def _tree_drop(self, row, value, where):
        if not isinstance(value, str) or ":" not in value:
            return False
        if not self._can_leave_document():
            return False
        if value.startswith('folder:') and hasattr(row, 'folder'):
            folder_id = value.split(':', 1)[1]
            try:
                if where == 'inside':
                    self.store.move_folder(folder_id, row.folder.id, expand_parent=True)
                else:
                    parents = self.store.folder_parents()
                    parent = parents.get(row.folder.id)
                    siblings = [item for item in self.store.root_items()
                                if isinstance(item, Folder) and parents.get(item.id) == parent
                                and item.id != folder_id]
                    index = next(i for i, item in enumerate(siblings) if item.id == row.folder.id)
                    index += int(where == 'after')
                    before = self.store.root_key(siblings[index]) if index < len(siblings) else None
                    self.store.move_folder(folder_id, parent, root_before=before)
            except (KeyError, ValueError, OSError, sqlite3.Error) as error:
                Toast.show(self.document_host, 'Could not move folder: ' + str(error), kind='error')
                return False
            self._reload_sidebar()
            if self.current:
                self._render_subject()
            return True
        fraction = {'before': .1, 'inside': .5, 'after': .9}[where]
        return self._sidebar_drop(row, value, 30, max(1, row.get_height()) * fraction)

    def _sidebar_drop(self, row, value, x, y):
        try:
            return super()._sidebar_drop(row, value, x, y)
        except (KeyError, OSError) as error:
            Toast.show(self.document_host, 'Could not move: ' + str(error), kind='error')
            return False

    def _blank_root_drop(self, _target, value, _x, y):
        # Row drops belong to SidebarRow. Empty space retains the existing
        # top-level move behavior without adding a visual boundary row.
        row = self.sidebar_list.get_row_at_y(int(y))
        if isinstance(row, _TreeRow) or not isinstance(value, str):
            return False
        kind, separator, item_id = value.partition(':')
        if not separator or kind not in ('note', 'folder'):
            return False
        if not self._can_leave_document():
            return False
        try:
            if kind == 'folder':
                self.store.get_folder(item_id)
            else:
                self.store.get_note(item_id)
            self._place_at_root(kind, item_id, None)
        except (KeyError, ValueError, OSError, sqlite3.Error) as error:
            Toast.show(self.document_host, 'Could not move: ' + str(error), kind='error')
            return False
        return True

    def _place_at_root(self, kind, item_id, before):
        if not self._can_leave_document():
            return
        if kind == 'folder':
            self.store.move_folder(item_id, None, root_before=before)
            self._reload_sidebar()
        else:
            super()._place_at_root(kind, item_id, before)
        if self.current:
            self._render_subject()

    def move_note(self, note_id, folder_id, before_id, *, parent_id=None):
        moving_ids = self.store.descendants(note_id) | {note_id}
        if self.current and self.current.id in moving_ids and not self._can_leave_document():
            return
        super().move_note(note_id, folder_id, before_id, parent_id=parent_id)
        if self.current:
            self._render_subject()

    def _set_folder_expanded(self, folder_id, expanded):
        self.store.set_folder_expanded(folder_id, expanded)
        self._reload_sidebar()

    def _can_leave_document(self):
        self._flush_save()
        # A vanished row can fail without scheduling the ordinary disk retry.
        # Retry an unscheduled draft, then require actual saved-state equality.
        if self._has_local_edits() and not self.save_source:
            self._save()
        if self._has_local_edits():
            if self.save_status.error is None:
                self.save_status.failed('The note could not be saved. Your edits remain here.')
            return False
        return self.save_status.error is None

    def _open_note(self, note):
        if not self._can_leave_document():
            return False
        self._document_selection = False
        super()._open_note(note)
        self.body_scroll.set_visible(True)
        self._render_tools()
        self._render_subject()
        self._apply_collaboration_role()
        return True

    def _key_pressed(self, controller, keyval, keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            if self.current is not None and self._can_leave_document():
                Toast.show(self.document_host, 'Saved “' + self.current.display_title + '”', kind='saved')
            return True
        return super()._key_pressed(controller, keyval, keycode, state)

    def _delete_specific(self, note):
        if self.current and self.current.id == note.id and not self._can_leave_document():
            return
        super()._delete_specific(note)

    def _undo_delete(self, note_id):
        if not self._can_leave_document():
            return
        try:
            note = self.store.restore_note(note_id)
            folder_id = note.folder_id
            folder_parents = self.store.folder_parents()
            while folder_id:
                self.store.set_folder_expanded(folder_id, True)
                folder_id = folder_parents.get(folder_id)
            parent_id = self.store.parent_of(note_id)
            while parent_id:
                self.open_nodes.add('n' + parent_id)
                parent_id = self.store.parent_of(parent_id)
        except (KeyError, OSError, sqlite3.Error) as error:
            Toast.show(self.document_host, 'Could not restore note: ' + str(error), kind='error')
            return
        self.search.set_text('')
        self._reload_sidebar(select_id=note_id)
        self._open_note(note)
        self._refresh_semantics()
        Toast.show(self.document_host, 'Restored “' + note.display_title + '”', kind='undone')

    def _show_empty(self):
        super()._show_empty()
        # A hidden canvas does not hide its expanding scroller. Leaving the
        # scroller here steals the empty state's space and pushes it down.
        self.body_scroll.set_visible(False)
        self.format_toolbar.hide_bar()
        if self.corner is not None:
            self.document_host.remove_overlay(self.corner)
            self.corner = None
        self.light.set_visible(False)
        self.grain.set_visible(False)

    def note_commands(self, note):
        def current_action(callback):
            if self._open_note(self.store.get_note(note.id)):
                callback()
        return CommandRegistry((CommandGroup(None, (
            Command('note.pin', 'Unpin' if note.favorite else 'Pin',
                    lambda: self._favorite_note(note), 'pin'),
            Command('note.new-inside', 'New note inside',
                    lambda: current_action(self._new_inside), 'plus'),
            Command('note.share', 'Share…',
                    lambda: current_action(lambda: self._share(self.corner.controls['share'])), 'users'),
            Command('note.move', 'Move to folder', lambda: current_action(self._move_menu), 'file-text'),
            Command('note.duplicate', 'Duplicate', lambda: self._duplicate_specific(note), 'copy'),
        )), CommandGroup(None, (
            Command('note.delete', 'Delete', lambda: self._delete_specific(note), 'trash-2', destructive=True),
        ))))

    def folder_commands(self, folder):
        return CommandRegistry((CommandGroup(folder.name, (
            Command('folder.new-note', 'New note here', lambda: self._new_in_folder(folder), 'plus'),
        )),))

    def create_folder(self):
        # Legacy callers still reach this command; the current UI creates
        # pages, which can contain other pages when someone wants a group.
        self.create_note()

    def create_note(self):
        self._create_note_in_folder(None)

    def _new_in_folder(self, folder):
        self._create_note_in_folder(folder.id)

    def _create_note_in_folder(self, folder_id):
        if not self._can_leave_document():
            return
        try:
            note = self.store.create_note(folder_id=folder_id, first=True)
        except (KeyError, ValueError, OSError, sqlite3.Error) as error:
            Toast.show(self.document_host, 'Could not create page: ' + str(error), kind='error')
            return
        self.search.set_text('')
        self._reload_sidebar(select_id=note.id)
        self._open_note(note)
        if self.phone:
            self.phone_page = 'note'
            self._show_phone_page()
        self._refresh_semantics()
        self.title_entry.grab_focus()

    def _duplicate_specific(self, source, *, notify=True):
        if not self._can_leave_document():
            return
        source = self.store.get_note(source.id)
        parent_id = self.store.parent_of(source.id)
        title = f'{source.title} copy' if source.title else ''
        try:
            duplicate = self.store.create_note(folder_id=source.folder_id, title=title,
                                               parent_id=parent_id, first=True,
                                               body=source.body, runs=source.runs)
        except (KeyError, ValueError, OSError, sqlite3.Error) as error:
            Toast.show(self.document_host, 'Could not duplicate: ' + str(error), kind='error')
            return None
        if self.fixture:
            raw = dict(self.store.raw_notes.get(source.id, {}))
            raw.update(t=title, pinned=False, live=None, at='Just now')
            self.store.raw_notes[duplicate.id] = raw
        if parent_id is not None:
            self.open_nodes.add('n' + parent_id)
        self._reload_sidebar(select_id=duplicate.id)
        self._open_note(duplicate)
        if notify:
            Toast.show(self.document_host, 'Duplicated')
        return duplicate

    def _new_folder_form(self, parent_id=None):
        from luma_appkit import TextField
        from luma_appkit.action_center import make_control
        from luma_appkit.rows_menu import MenuSection
        field = TextField('Folder name', placeholder='Folder name',
                          value=os.environ.get('LUMA_NOTES_FOLDER_NAME', '') if self.fixture else '')
        hue = [150]
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        form.append(field)
        def create():
            try:
                self._create_folder_from_values(field.text, hue[0], parent_id)
            except (KeyError, ValueError, OSError, sqlite3.Error) as error:
                Toast.show(self.document_host, str(error), kind='error')
                return
            menu.close()
        actions = Gtk.Box(halign=Gtk.Align.END)
        create_button = make_control(BarAction('', 'Create', primary=True, on_activate=create))
        create_button.set_name('nt-folder-create')
        actions.append(create_button)
        form.append(actions)
        title = 'New folder' + (' in ' + self.store.get_folder(parent_id).name if parent_id else '')
        menu = FloatingMenu([MenuSection(form, label=title)], label=title)
        menu.set_name('nt-new-folder-menu')
        field.entry.set_name('nt-folder-name')
        field.entry.connect('activate', lambda _entry: create())
        menu.popup(self.foot)
        field.grab_focus()
        self.folder_form = menu

    def _reload_open_note(self, stored):
        super()._reload_open_note(stored)
        self._render_subject()

    def _render_subject(self):
        if self.current is not None:
            self.page.check_hue = self._folder_hue(self.current.folder_id)
        note = self.current
        if note is None:
            return
        self.light.set_visible(True)
        self.grain.set_visible(True)
        raw = self.store.raw_notes.get(note.id, {}) if self.fixture else {}
        if self.fixture:
            self.page.clear_collaborators()
            if peer := self.store.people.get(raw.get("live")):
                body = self.buffer.get_text(*self.buffer.get_bounds(), True).rstrip("\n")
                self.page.set_collaborator(raw["live"], name=peer["n"].split()[0],
                                           hue=peer["h"], offset=len(body))
        tone = {"launch": "create", "press": "create", "personal": "work", "recipes": "play", "ideas": "media"}.get(note.folder_id, "tools")
        if self.fixture:
            self.light.set_source(hue=self._folder_hue(note.folder_id) if note.folder_id else 20)
        else:
            mark = self.store.get_mark('folder', note.folder_id) if note.folder_id else None
            self.light.set_source(hue=self._folder_hue(note.folder_id)) if mark else self.light.set_source(tone=tone)
        self.mark.mark.set_value(self._mark_value("note", note.id))
        chosen = self.store.get_mark("note", note.id) is not None
        if self._page_mark_kind:
            self.mark.set_empty(not chosen)
        # v71 phone: no empty "Add icon" slot; a chosen icon still shows above the title.
        self.mark.set_visible(chosen if self.phone else self._page_mark_kind or chosen)
        self.mark_slot.set_visible(chosen or not self.phone)
        self.mark_slot.set_margin_bottom(8 if chosen else 6)
        # v71 phone: the title island says the folder and the edited time, so the meta row goes.
        self.meta.set_visible(not self.phone)
        _clear(self.meta)
        path = []
        folder_id = note.folder_id
        while folder_id:
            folder = self.store.get_folder(folder_id)
            path.insert(0, folder.name)
            folder_id = self.store.folder_parents().get(folder_id)
        from luma_appkit import FolderPath, TrailCrumb
        if path:
            chip = FolderPath(path, mark=self._mark_value('folder', note.folder_id),
                              on_activate=lambda: self._move_menu(chip))
            chip.set_name('nt-folder-path')
            self.meta.append(chip)
        elif raw.get("from"):
            self.meta.append(apply_type(Gtk.Label(label="From " + self.store.people[raw["from"]]["n"].split()[0]), "caption"))
        ancestors = []
        parent_id = self.store.parent_of(note.id)
        while parent_id:
            parent = self.store.get_note(parent_id)
            ancestors.insert(0, parent)
            parent_id = self.store.parent_of(parent_id)
        for parent in ancestors:
            crumb = TrailCrumb(parent.display_title, on_activate=lambda n=parent: self._open_note(n))
            crumb.button.set_name('nt-ancestor-' + parent.id)
            self.meta.append(crumb)
        stamp = raw.get("at", note.modified_at)
        if not raw:
            from datetime import datetime
            try:
                edited = datetime.fromisoformat(stamp).astimezone()
                now = datetime.now().astimezone()
                stamp = ('just now' if (now - edited).total_seconds() < 60 else
                         'today at ' + edited.strftime('%-I:%M %p') if edited.date() == now.date() else
                         edited.strftime('%b %-d'))
            except ValueError:
                pass
        elif raw.get('live') and os.environ.get('LUMA_NOTES_LIVE_SETTLED') == '1':
            stamp = 'just now'
        label = "Edited " + stamp.replace("Today, ", "today at ").replace("Yesterday", "yesterday")
        self.edited_label = apply_type(Gtk.Label(label=label, xalign=0), "caption")
        self.meta.append(self.edited_label)
        _clear(self.kids)
        children = [n for n in self.store.list_notes() if self.store.parent_of(n.id) == note.id]
        if children:
            self.kids.append(apply_type(Gtk.Label(label="Inside this note", xalign=0, margin_bottom=8), "label"))
            for child in children:
                snippet = ' '.join(child.plain_text.replace('\ufffc', '').split())[:90]
                preview = NoteCard(child.title, snippet,
                                      on_activate=lambda n=child: self._open_note(n))
                preview.set_name("nt-child-" + child.id)
                self.kids.append(preview)
        add = AddRow("New note inside", icon="plus", appearance="document", on_activate=self._new_inside)
        add.set_name("nt-new-inside")
        self.kids.append(add)
        if self.corner is not None:
            self.document_host.remove_overlay(self.corner)
        more = CommandRegistry((CommandGroup(None, (
            Command("note.move", "Move to folder", self._move_menu, "folder"),
            Command("note.duplicate", "Duplicate", self.duplicate_note, "copy"),
            Command("note.export", "Export as Markdown", lambda: self._export_note(self.current), "download"),
            Command("note.write", "Open in Write", self._open_in_write, "file-text"),
        )), CommandGroup(None, (Command("note.delete", "Delete note", self.delete_current, "trash-2", destructive=True),))))
        presence = None
        if self.fixture:
            people = self._fixture_people()
            keys = [raw['from']] if raw.get('from') else raw.get('share', [])
            if keys:
                from luma_appkit import PresenceChip
                label = ('Shared by ' + people[raw['from']].name if raw.get('from') else
                         'Shared with ' + ' and '.join(people[k].name.split()[0] for k in keys))
                status = people[raw['live']].name.split()[0] + ' is here' if raw.get('live') else None
                presence = PresenceChip([people[k] for k in keys], label=label, status=status,
                                        on_activate=self._share)
                presence.set_name('nt-presence')
        self.corner = CornerPill(people=presence, share=self._share,
                                 states=[("pin", "Unpin" if note.favorite else "Pin", note.favorite,
                                          lambda _active: self._favorite_note(self.current))], more=more, labelled=True)
        self.corner.set_name("nt-corner")
        # v71 phone: the title island replaces the corner (faces, Share, Pin and ⋯ live in it).
        self.corner.set_visible(not self.phone)
        self.corner.controls["more"].set_name("nt-more")
        self.corner.controls["more"].connect("clicked", lambda _b: self._label_menu_controls(
            {command.label: "nt-command-" + command.id.replace(".", "-")
             for group in more.visible_groups() for command in group.commands}))
        self.corner.controls["share"].set_name("nt-share")
        self.document_host.add_overlay(self.corner)
        self.document_host.set_measure_overlay(self.corner, False)
        self._render_phone_island()

    def _favorite_note(self, note):
        super()._favorite_note(note)
        if self.current:
            self._render_subject()

    def _delete_folder(self, folder):
        if not self._can_leave_document():
            return
        super()._delete_folder(folder)
        if self.current:
            self.current = self.store.get_note(self.current.id)
            self._render_subject()

    def _key_pressed(self, controller, keyval, keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            if self.current is not None and self._can_leave_document():
                Toast.show(self.document_host, 'Saved “' + self.current.display_title + '”', kind='saved')
            return True
        return super()._key_pressed(controller, keyval, keycode, state)

    def _delete_specific(self, note):
        if self.current and self.current.id == note.id and not self._can_leave_document():
            return
        leaving = self.phone and self.current is not None and self.current.id == note.id
        super()._delete_specific(note)
        if leaving:
            self.phone_page = 'list'
            self._show_phone_page()

    def _move_menu(self, anchor=None):
        """Move to folder (v71 `data-nmove`): the folders, indented, the current one checked."""
        if self.current is None:
            return None
        subject_id, current = self.current.id, self.current.folder_id
        parents = self.store.folder_parents()
        folders = []

        def tree(parent, depth):
            for folder in self.store.list_folders():
                if parents.get(folder.id) == parent:
                    folders.append((folder, depth))
                    tree(folder.id, depth + 1)
        tree(None, 0)
        move = lambda fid: self.move_note(subject_id, fid, None)
        if self.phone:
            # v71 phone: Move grows the writing bar into the folders.
            from luma_appkit import PanelRow, panel_list
            rows = []
            for folder, depth in folders:
                row = PanelRow(folder.name, lead=Mark(value=MarkValue('dot', self._folder_hue(folder.id))),
                               selected=folder.id == current, on_activate=lambda f=folder.id: move(f))
                row.set_name("nt-move-" + folder.id)
                row.set_margin_start(20 * depth)
                rows.append(row)
            back = PanelRow('Move to', icon='chevron-left', on_activate=self._phone_more, closes=False)
            panel = panel_list([back] + rows, label='Move to folder')
            panel.set_name('nt-move-menu')
            self.format_toolbar.grow('move', panel)
            return panel
        rows = ['Move to'] + [RichMenuItem(('\u2003' * depth) + folder.name, icon='folder', checked=folder.id == current,
                                           on_activate=lambda f=folder.id: move(f)) for folder, depth in folders]
        menu = FloatingMenu(rows, label='Move to folder')
        for button, (folder, _depth) in zip(menu.buttons, folders):
            button.set_name('nt-move-' + folder.id)
        menu.popup(anchor or self.corner.controls['more'])
        menu.set_name('nt-move-menu')
        return menu

    def _move_inside_page(self, note_id, parent):
        self.open_nodes.add('n' + parent.id)
        self.move_note(note_id, parent.folder_id, None, parent_id=parent.id)

    def _fixture_people(self):
        if hasattr(self, '_people'):
            return self._people
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        self._people = {}
        for key, raw in self.store.people.items():
            picture = None
            if face := raw.get('face'):
                asset, fx, fy, zoom = face
                path = self.store.path.parent / 'notes-v70' / (asset + '.webp')
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
                size = max(1, round(pixbuf.get_width() / zoom))
                x = max(0, min(pixbuf.get_width() - size, round(fx * pixbuf.get_width() - size / 2)))
                y = max(0, min(pixbuf.get_height() - size, round(fy * pixbuf.get_height() - size / 2)))
                picture = Gdk.Texture.new_for_pixbuf(pixbuf.new_subpixbuf(x, y, size, size))
            # v70's account is Nick (initial N); ShareSheet labels its owner
            # row "You" independently from the person's identity.
            name = 'Nick' if key == 'me' else raw['n']
            self._people[key] = Person(name, phone=raw.get('phone', ''), username=raw.get('u') or '',
                                       online=raw.get('on', False), hue=raw.get('h'), picture=picture)
        return self._people

    def _share(self, anchor):
        if self.current is None:
            return
        if not self._can_leave_document():
            return
        title = self.current.display_title
        subject = self.current
        if self.fixture:
            people = self._fixture_people()
            state = self.fixture_sharing.setdefault(self.current.id, {
                'collaborators': [Collaborator(people['PR'], 'edit'), Collaborator(people['NF'], 'comment')],
                'link_access': 'off', 'link_for': 'people',
            })
            count = len(state['collaborators'])
            recent = [people[k] for k in ('PR', 'NF', 'TH', 'SK', 'AR', 'AC', 'JK', 'ZM')]
            self.share_sheet = ShareSheet.present(
                anchor, document=ShareSubject(title, f'Notes · you and {count} others' if count else 'Notes · only you',
                                               kind='doc', icon='org.projectluma.Notes', share_link_available=True),
                targets=self._fixture_share_targets(title), people=recent, owner=people['me'], choices=('work-together', 'send-copy'),
                collaborators=state['collaborators'], link_access=state['link_access'], link_for=state['link_for'],
                suggest=lambda query: [p for p in recent if query.lower().lstrip('@') in (p.name + ' ' + p.username).lower()],
                on_choice=lambda choice, value: self._fixture_share_choice(state, choice, value, title=title))
        else:
            from .connect_sync import ConnectError, load_identity
            try:
                connected = load_identity() is not None
            except ConnectError:
                connected = False
                Toast.show(anchor, 'Live sharing is unavailable. Send a copy or retry when Connect is available.', kind='warning')
            if connected:
                from .collaboration import note_content
                from .collaboration_ui import NativeCollaborationShare
                try:
                    content = note_content(subject)
                except Exception as error:
                    Toast.show(anchor, str(error), kind='warning')
                else:
                    self._collaboration_share = NativeCollaborationShare(self, anchor, title=title, kind='note',
                        local_id=subject.id, content=content,
                        copy_choice=lambda choice, value: self._share_copy_choice(subject, choice, value))
                    return
            from luma_appkit import ShareTarget, installed_targets
            document = ShareSubject(title, 'Notes', kind='doc', icon='org.projectluma.Notes',
                                    mime_type='text/markdown', share_link_available=False)
            targets = [ShareTarget('notes', 'Notes', 'org.projectluma.Notes')]
            targets.extend(target for target in installed_targets(document) if target.key != 'notes')
            self.share_sheet = ShareSheet.present(
                anchor, document=document,
                choices=('send-copy',), targets=targets,
                on_choice=lambda choice, value: self._share_copy_choice(subject, choice, value))
        self.share_sheet.set_name('nt-share-sheet')
        self._label_share_controls()
        if self.share_sheet.mode_switch is not None:
            for button in self.share_sheet.mode_switch.buttons.values():
                button.connect('toggled', lambda _b: self._label_share_controls())
        if self.fixture and (query := os.environ.get('LUMA_NOTES_SHARE_QUERY')):
            self.share_sheet.invite.set_text(query)
            self._label_share_controls()

    @staticmethod
    def _fixture_share_targets(title):
        from luma_appkit.bar_share import default_targets
        return default_targets(ShareSubject(title, 'Notes', kind='doc', icon='org.projectluma.Notes'))

    def _fixture_share_choice(self, state, choice, value, *, title=None):
        title = title or self.current.display_title
        GLib.idle_add(self._label_share_controls)
        if choice == 'invite':
            # ShareSheet copies the input list. Persist its new collaborator
            # in fixture state once, so reopening the sheet retains the invite.
            if not any(c.person.name == value.person.name for c in state['collaborators']):
                state['collaborators'].append(value)
            return f'Invitation sent to @{value.person.username}'
        if choice == 'role':
            collaborator, role = value
            collaborator.role = role
        elif choice == 'remove':
            state['collaborators'] = [c for c in state['collaborators'] if c is not value]
        elif choice in ('link-access', 'link-for'):
            state[choice.replace('-', '_')] = value
        elif choice in ('copy', 'copy-link'):
            return 'Copied'
        elif choice == 'send-to':
            return f'Sent to {value.name.split()[0]} in Messages'
        elif choice == 'target':
            return {'messages': f'A new message with {title} attached',
                    'mail': f'A new email with {title} attached',
                    'notes': 'Added to a new note', 'write': 'Opened in Write',
                    'ari': 'Ari has it. Ask what you like.'}.get(value)
        elif choice == 'save':
            Toast.show(self.document_host, 'Choose where to save the copy')
        elif choice == 'print':
            Toast.show(self.document_host, f'Printing {title} on Brother HL-L2350DW')
        return None

    def _share_copy_choice(self, subject, choice, _value):
        if not self._can_leave_document():
            return None
        try:
            subject = self.store.get_note(subject.id)
        except KeyError:
            Toast.show(self.document_host, 'This note is no longer available', kind='warning')
            return None
        if choice == 'target':
            if _value == 'notes':
                if self._duplicate_specific(subject, notify=False) is not None:
                    Toast.show(self.document_host, 'Added to a new note')
                return None
            if _value == 'write':
                self._open_in_write(subject)
                return ShareResult(False)
            target = next((target for target in self.share_sheet.targets if target.key == _value), None)
            if target is None or not target.app_id:
                return ShareResult(False, 'This application is no longer available')
            self._send_copy_to_app(subject, target)
            return ShareResult(False)  # Completion is reported after actual export and launch.
        if choice == 'copy':
            if not self._can_leave_document():
                return None
            from .notes_export import to_markdown
            self.get_display().get_clipboard().set(to_markdown(subject)[0])
            return ShareResult(True, 'Copied')
        if choice == 'save':
            self._export_note(subject)
        elif choice == 'print':
            self._print_note(subject)
        elif choice in ('copy-link', 'send-to'):
            return ShareResult(False, 'This note does not have a shared link')
        return ShareResult(False)

    def _send_copy_to_app(self, note, target):
        from pathlib import Path
        from .notes_export import export_open_in_write
        from luma_appkit.application_directory import lookup, launch as launch_application
        receiver = lookup(target.app_id + '.desktop')
        if receiver is None or not (receiver.supports_files() or receiver.supports_uris()):
            Toast.show(self.document_host, 'This application cannot receive a note file', kind='warning')
            return
        directory = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share')) / 'luma/notes/shared-copies'
        def export():
            destination, error = None, None
            try:
                destination = export_open_in_write(note, data_directory=directory)
            except (OSError, ValueError, TypeError) as failure:
                error = str(failure)
            GLib.idle_add(launch, destination, error)
        def launch(destination, error):
            if getattr(self, '_closed', False):
                return GLib.SOURCE_REMOVE
            if error:
                Toast.show(self.document_host, 'Could not share: ' + error, kind='error')
                return GLib.SOURCE_REMOVE
            def accepted(ok, failure):
                if getattr(self, '_closed', False): return
                if ok:
                    Toast.show(self.document_host, 'Opened in ' + target.label, kind='sent')
                else:
                    Toast.show(self.document_host, f'{failure}. Copy kept at {destination}', kind='error')
            launch_application(receiver.get_id(), [Gio.File.new_for_path(str(destination))],
                               context=self.get_display().get_app_launch_context(), callback=accepted)
            return GLib.SOURCE_REMOVE
        Thread(target=export, daemon=True).start()

    def _export_note(self, note):
        if self.fixture:
            Toast.show(self.document_host, 'Exported as Markdown')
            return
        if self.current and self.current.id == note.id:
            if not self._can_leave_document():
                return
        note = self.store.get_note(note.id)
        name = ''.join(char for char in note.display_title if char not in '/\\:*?"<>|').strip() or 'Note'
        dialog = Gtk.FileDialog(title='Export as Markdown', initial_name=f'{name}.md')
        def chosen(source, result):
            try:
                target = source.save_finish(result).get_path()
            except GLib.Error:
                return
            if not target:
                Toast.show(self.document_host, 'Choose a local destination', kind='error')
                return
            def export():
                from pathlib import Path
                from .notes_export import export_markdown
                error = None
                try:
                    export_markdown(note, Path(target))
                except (OSError, ValueError, TypeError) as failure:
                    error = str(failure)
                GLib.idle_add(finished, error)
            Thread(target=export, daemon=True).start()
        def finished(error):
            if not getattr(self, '_closed', False):
                Toast.show(self.document_host, f'Could not export: {error}' if error else
                           f'Exported “{note.display_title}”', kind='error' if error else 'saved')
            return GLib.SOURCE_REMOVE
        dialog.save(self, None, chosen)

    def _open_in_write(self, note=None):
        note = note or self.current
        if note is None:
            return
        if self.fixture:
            Toast.show(self.document_host, 'Opened in Write')
            return
        try:
            app = Gio.DesktopAppInfo.new('org.projectluma.Write.desktop')
        except TypeError:
            # PyGObject raises when this nullable constructor returns NULL.
            app = None
        if app is None:
            try:
                context = self.get_display().get_app_launch_context()
                uri = 'luma-depot://install/org.projectluma.Write'
                try:
                    depot = Gio.DesktopAppInfo.new('org.projectluma.Depot.desktop')
                except TypeError:
                    depot = None
                opened = (depot.launch_uris([uri], context) if depot is not None else
                          Gio.AppInfo.launch_default_for_uri(uri, context))
            except GLib.Error:
                opened = False
            if opened:
                Toast.show(self.document_host, 'Write isn’t installed. Check its availability in Depot.')
            else:
                Toast.show(self.document_host, 'Write isn’t installed. Open Depot to check its availability.', kind='warning')
            return
        if not self._can_leave_document():
            return
        note = self.store.get_note(note.id)
        def export_copy():
            from .notes_export import export_open_in_write
            target, error = None, None
            try:
                target = export_open_in_write(note)
            except (OSError, ValueError, TypeError) as failure:
                error = str(failure)
            GLib.idle_add(launch_copy, target, error)
        def launch_copy(target, error):
            if getattr(self, '_closed', False):
                return GLib.SOURCE_REMOVE
            if error:
                Toast.show(self.document_host, error, kind='error')
                return GLib.SOURCE_REMOVE
            try:
                app.launch([Gio.File.new_for_path(str(target))], self.get_display().get_app_launch_context())
            except GLib.Error as failure:
                Toast.show(self.document_host, f'{failure}. Copy kept at {target}', kind='error')
            return GLib.SOURCE_REMOVE
        Thread(target=export_copy, daemon=True).start()

    def _close_notes(self, window):
        if not self._can_leave_document():
            return True
        return super()._close_notes(window)

    def _print_note(self, note=None):
        note = note or self.current
        if note is None or self.fixture:
            return
        if not self._can_leave_document():
            return
        note = self.store.get_note(note.id)
        from math import ceil
        from gi.repository import PangoCairo
        operation = Gtk.PrintOperation()
        operation.set_job_name(note.display_title)
        text = note.display_title + '\n\n' + note.plain_text.replace('\ufffc', '[Image]')
        state = {}
        def begin(op, context):
            layout = context.create_pango_layout()
            layout.set_font_description(type_font('reading'))
            layout.set_width(round(context.get_width() * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_text(text, -1)
            state['layout'] = layout
            op.set_n_pages(max(1, ceil(layout.get_pixel_size()[1] / context.get_height())))
        def draw(_op, context, page):
            cairo = context.get_cairo_context()
            cairo.save()
            cairo.rectangle(0, 0, context.get_width(), context.get_height())
            cairo.clip()
            cairo.translate(0, -page * context.get_height())
            PangoCairo.show_layout(cairo, state['layout'])
            cairo.restore()
        operation.connect('begin-print', begin)
        operation.connect('draw-page', draw)
        try:
            operation.run(Gtk.PrintOperationAction.PRINT_DIALOG, self)
        except GLib.Error as error:
            Toast.show(self.document_host, str(error), kind='error')

    def _new_inside(self):
        if self.current is None:
            return
        parent = self.current
        if not self._can_leave_document():
            return
        try:
            note = self.store.create_note(folder_id=parent.folder_id, parent_id=parent.id, first=True)
        except (KeyError, ValueError, OSError, sqlite3.Error) as error:
            Toast.show(self.document_host, 'Could not create note: ' + str(error), kind='error')
            return
        self.search.set_text('')
        self.open_nodes.add("n" + parent.id)
        self._reload_sidebar(select_id=note.id)
        self._open_note(note)
        self.title_entry.grab_focus()

    def _refresh_format_buttons(self):
        if hasattr(self, "format_toolbar") and hasattr(self, "page"):
            inline = self.page.inline_state()
            if hasattr(self, 'selection_bubble'):
                for style, icon in (('bold', 'bold'), ('italic', 'italic'), ('highlight', 'highlighter')):
                    self.selection_bubble.set_active(icon, bool(inline.get(style)))
            block = self.page.block_of_line(self.page.cursor_line()) if self._document_selection else set()
            if hasattr(self, 'style_picker'):
                label = next((name for style, name in (('heading-1', 'Heading 1'), ('heading', 'Heading'), ('quote', 'Quote'),
                               ('checklist', 'Checklist'), ('bulleted', 'List'), ('numbered', 'Numbers'))
                              if style in block), 'Text')
                self.style_picker.set_label(label)
                if hasattr(self, 'selection_style_picker'):
                    self.selection_style_picker.set_label(label)
            changed = False
            selected = self.buffer.get_has_selection()
            for item in getattr(self, 'tool_items', ()):
                if not isinstance(item, BarAction):
                    continue
                style = {'Bold': 'bold', 'Italic': 'italic', 'Strikethrough': 'strike',
                         'Checklist': 'checklist', 'Bulleted list': 'bulleted',
                         'Numbered list': 'numbered'}.get(item.tooltip)
                active = bool(style in block if style in ('checklist', 'bulleted', 'numbered')
                              else selected and inline.get(style)) if style else False
                if item.active != active:
                    item.active = active
                    changed = True
            if getattr(self, 'phone', False):
                if self.format_toolbar.grown:
                    return  # v71: the bar keeps the selection (and its Aa panel) while you tap
                check = next((i for i in getattr(self, 'phone_tool_items', ()) if isinstance(i, BarAction)
                              and i.tooltip == 'Checklist'), None)
                changed = check is not None and check.active != ('checklist' in block)
            if changed:
                self._render_tools()

    def _format_focus(self, widget, _property):
        if widget.has_focus():
            self._document_selection = widget is getattr(self, 'editor', None)
            self._refresh_format_buttons()

    def toggle_search(self):
        if not self.side_toggle.shown or not self.sidebar.get_visible():
            self.side_toggle.toggle()
        self.search.grab_focus()

    def _search_key(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape and self.search.get_text():
            self.search.set_text('')
            return True
        return False


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _descendants(widget):
    for child in _children(widget):
        yield child
        yield from _descendants(child)
