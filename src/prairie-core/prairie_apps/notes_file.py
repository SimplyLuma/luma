# SPDX-License-Identifier: Apache-2.0
"""Notes' independent file window: no library, indexing, recents or autosave."""
from __future__ import annotations

import time

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from luma_appkit import AppWindow, Command, CommandGroup, CommandRegistry, IconButton, Island, Toolbar
from luma_appkit import SaveChoice, UnsavedDocument, confirm_save
from .notes_document import MAX_TEXT_BYTES, TextDocument
from .notes_editor import NotesFormattingToolbar


class TextFileWindow(AppWindow):
    def __init__(self, application, file: Gio.File):
        self.file = file
        self.document = None
        self.etag = None
        self.saving = False
        self.closed = False
        self.close_after_save = False
        # The close prompt's Save, waiting for the write to finish.
        self.save_answer = None
        # When the first edit since the last save was made, for the prompt.
        self.unsaved_since = None
        self.io_cancel = Gio.Cancellable()
        self.stream = None
        self.contents = bytearray()
        self.pending_format = None
        commands = CommandRegistry((CommandGroup(None, (
            Command("file.save", "Save", self.save, "document-save-symbolic", shortcut=("Ctrl", "S")),
            Command("file.save-as", "Save As…", self.save_as, "document-save-as-symbolic", shortcut=("Ctrl", "Shift", "S")),
            Command("file.close", "Close File", self.close, shortcut=("Ctrl", "W")),
        )), CommandGroup("NOTES", (
            Command("notes.library", "Open Notes", application.activate, "org.projectluma.Notes"),
        ))))
        super().__init__(application=application, app_id=application.get_application_id() + ".FileWindow",
                         title="Notes", icon_name="org.projectluma.Notes", commands=commands,
                         default_width=760, default_height=560, minimum_width=320, minimum_height=300)
        self.add_css_class("luma-notes")
        self.add_css_class("notes-file-window")
        island = Island()
        island.add_css_class("notes-editor-island")
        self.toolbar = NotesFormattingToolbar(self.context, self._toggle_format, self._show_link_popover)
        self.format_buttons = self.toolbar.format_buttons
        self.status = self.toolbar.save_status
        self.status.set_label("Opening…")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        island.append(self.toolbar)
        canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, vexpand=True)
        canvas.add_css_class("notes-canvas")
        self.file_title = Gtk.Label(label=file.get_basename() or "Text file", xalign=0,
                                   ellipsize=Pango.EllipsizeMode.END)
        self.file_title.add_css_class("notes-editor-title")
        canvas.append(self.file_title)
        self.buffer = Gtk.TextBuffer()
        self.buffer.connect("modified-changed", self._modified)
        self.editor = Gtk.TextView(buffer=self.buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                   left_margin=0, right_margin=0, top_margin=0, bottom_margin=18,
                                   pixels_below_lines=3,
                                   editable=False, vexpand=True)
        self.editor.add_css_class("notes-editor")
        self.editor.update_property([Gtk.AccessibleProperty.LABEL], ["File contents"])
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.editor)
        canvas.append(scroll)
        island.append(canvas)
        self.set_body(island)
        self.connect("close-request", self._close_file)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._format_key)
        self.add_controller(keys)
        self._modified()
        # Reject oversized/nonregular files before loading; bounded reads also
        # bounds a file which grows between the metadata query and the read.
        file.query_info_async("standard::type,standard::size", Gio.FileQueryInfoFlags.NONE,
                              GLib.PRIORITY_DEFAULT, self.io_cancel, self._queried)

    def _queried(self, file, result):
        try:
            info = file.query_info_finish(result)
            if info.get_file_type() != Gio.FileType.REGULAR:
                raise ValueError("Choose a regular text file.")
            if info.get_size() > MAX_TEXT_BYTES:
                raise ValueError("This file is larger than Notes can edit (16 MB).")
            file.read_async(GLib.PRIORITY_DEFAULT, self.io_cancel, self._opened)
        except (GLib.Error, ValueError) as error:
            if not self.closed:
                self.status.set_label("Couldn’t open")
                self._error("Couldn’t open file", str(error))

    def _opened(self, file, result):
        try:
            self.stream = file.read_finish(result)
            self.stream.query_info_async("etag::value", GLib.PRIORITY_DEFAULT,
                                         self.io_cancel, self._stream_info)
        except GLib.Error as error:
            self._load_error(error)

    def _stream_info(self, stream, result):
        try:
            info = stream.query_info_finish(result)
            self.etag = info.get_attribute_string("etag::value")
            stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, self.io_cancel, self._chunk)
        except GLib.Error as error:
            self._load_error(error)

    def _chunk(self, stream, result):
        try:
            data = stream.read_bytes_finish(result).get_data()
            if len(self.contents) + len(data) > MAX_TEXT_BYTES:
                raise ValueError("This file is larger than Notes can edit (16 MB).")
            self.contents.extend(data)
            if data:
                stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, self.io_cancel, self._chunk)
            else:
                stream.query_info_async("etag::value", GLib.PRIORITY_DEFAULT,
                                         self.io_cancel, self._read_finished)
        except (GLib.Error, ValueError) as error:
            self._load_error(error)

    def _read_finished(self, stream, result):
        try:
            info = stream.query_info_finish(result)
            if info.get_attribute_string("etag::value") != self.etag:
                raise ValueError("The file changed while opening. Close this window and open it again.")
            document = TextDocument.decode(bytes(self.contents))
            self._close_stream()
            if self.closed:
                return
            self.document = document
            self.buffer.set_text(document.text)
            self.buffer.set_modified(False)
            self.buffer.set_enable_undo(True)
            self.editor.set_editable(True)
            self.status.set_label("Saved")
            self._modified()
            self.editor.grab_focus()
        except (GLib.Error, UnicodeError, ValueError) as error:
            self._load_error(error)

    def _close_stream(self):
        if self.stream is not None:
            self.stream.close_async(GLib.PRIORITY_DEFAULT, None, None)
            self.stream = None
        self.contents.clear()

    def _load_error(self, error):
        self._close_stream()
        if not self.closed:
            self.status.set_label("Couldn’t open")
            self._error("Couldn’t open file", str(error))

    def _modified(self, *_):
        dirty = self.buffer.get_modified()
        if not dirty:
            self.unsaved_since = None
        elif self.unsaved_since is None:
            self.unsaved_since = time.monotonic()
        self.set_title((self.file.get_basename() or "Text file") + (" •" if dirty else ""))
        self.file_title.set_label(self.file.get_basename() or "Text file")
        self.toolbar.set_sensitive(self.document is not None and not self.saving)
        if dirty and not self.saving:
            self.status.set_label("Unsaved changes")

    def _markdown_file(self):
        return (self.file.get_basename() or "").lower().endswith((".md", ".markdown", ".mdown"))

    def _format_or_convert(self, action):
        if self.document is None or self.saving:
            return
        if not self._markdown_file():
            # Formatting is explicit source syntax in a text document. A .txt
            # stays plain unless the user chooses a separate Markdown file.
            self.pending_format = action
            self.save_as(markdown=True)
            return
        action()

    def _toggle_format(self, style):
        self._format_or_convert(lambda: self._format_markdown(style))

    def _format_markdown(self, style):
        bounds = self.buffer.get_selection_bounds()
        start, end = bounds or (self.buffer.get_iter_at_mark(self.buffer.get_insert()),) * 2
        if style in {"heading", "quote", "bulleted", "numbered"}:
            start = start.copy()
            end = end.copy()
            start.set_line_offset(0)
            if end.starts_line() and end.compare(start) > 0:
                end.backward_char()
            if not end.ends_line():
                end.forward_to_line_end()
        text = self.buffer.get_text(start, end, True)
        if style in {"bold", "italic", "underline"}:
            left, right = {"bold": ("**", "**"), "italic": ("*", "*"), "underline": ("<u>", "</u>")}[style]
            remove = text.startswith(left) and text.endswith(right) and len(text) >= len(left + right)
            value = text[len(left):len(text)-len(right)] if remove else left + text + right
            cursor = len(value) if remove else len(left) + len(text)
        else:
            prefixes = {"heading": "# ", "quote": "> ", "bulleted": "- ", "numbered": "1. "}
            prefix = prefixes[style]
            lines = text.split("\n")
            remove = all(line.startswith(prefix) for line in lines)
            value = "\n".join(line[len(prefix):] if remove else prefix + line for line in lines)
            cursor = len(value)
        offset = start.get_offset()
        self.buffer.begin_user_action()
        self.buffer.delete(start, end)
        self.buffer.insert(self.buffer.get_iter_at_offset(offset), value)
        self.buffer.end_user_action()
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(offset + cursor))
        self.editor.grab_focus()

    def _show_link_popover(self, button):
        if self.document is None:
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        label = Gtk.Entry(placeholder_text="Text to display")
        href = Gtk.Entry(placeholder_text="https://example.com")
        apply = Gtk.Button(label="Add link")
        box.append(label); box.append(href); box.append(apply)
        popover = Gtk.Popover(child=box)
        popover.set_parent(button)
        def insert(*_):
            from .notes import _normalize_href
            target = _normalize_href(href.get_text())
            if not target:
                href.grab_focus()
                return
            bounds = self.buffer.get_selection_bounds()
            start, end = bounds or (self.buffer.get_iter_at_mark(self.buffer.get_insert()),) * 2
            text = self.buffer.get_text(start, end, True) or label.get_text() or target
            # Escape Markdown syntax in explicit link text/destinations.
            text = text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            target = target.replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")
            a, b = start.get_offset(), end.get_offset()
            def change():
                self.buffer.begin_user_action()
                self.buffer.delete(self.buffer.get_iter_at_offset(a), self.buffer.get_iter_at_offset(b))
                self.buffer.insert(self.buffer.get_iter_at_offset(a), f"[{text}]({target})")
                self.buffer.end_user_action()
                self.editor.grab_focus()
            popover.popdown()
            self._format_or_convert(change)
        apply.connect("clicked", insert)
        href.connect("activate", insert)
        popover.popup()
        label.grab_focus()

    def _format_key(self, _controller, keyval, _keycode, state):
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        key = (Gdk.keyval_name(keyval) or "").lower()
        if key in {"b", "i", "u"}:
            self._toggle_format({"b": "bold", "i": "italic", "u": "underline"}[key])
            return True
        if key == "k":
            self._show_link_popover(self.editor)
            return True
        return False

    def _text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)

    def save(self):
        if self.document is None or self.saving:
            return
        if not self.buffer.get_modified():
            self._answer_save(None)
            if self.close_after_save:
                self.close()
            return
        target = getattr(self, "markdown_destination", self.file)
        self._write(target, self.etag if target.equal(self.file) else None)

    def _write(self, destination, etag):
        text = self._text()
        data = self.document.encode(text)
        self.saving = True
        self.status.set_label("Saving…")
        self._modified()
        # GIO performs an atomic replacement, checks the loaded etag, and keeps
        # the existing file's permissions. External changes are never forced.
        destination.replace_contents_async(data, etag, False, Gio.FileCreateFlags.NONE,
                                           self.io_cancel, self._saved, (text, data))

    def _saved(self, destination, result, pending):
        self.saving = False
        try:
            _ok, etag = destination.replace_contents_finish(result)
            self.file, self.etag = destination, etag
            if hasattr(self, "markdown_destination"):
                del self.markdown_destination
            if self._text() == pending[0]:
                self.buffer.set_modified(False)
            self.status.set_label("Saved" if not self.buffer.get_modified() else "Unsaved changes")
            self._modified()
            self._answer_save(None)
            if self.close_after_save and not self.buffer.get_modified():
                self.close()
            self.close_after_save = False
        except GLib.Error as error:
            self.close_after_save = False
            self._modified()
            self.status.set_label("Couldn’t save")
            if self._answer_save(error.message):
                return
            self._error("Couldn’t save file", str(error) + "\nYour edits remain here. Use Save As to keep a separate copy.")

    def save_as(self, *, markdown=False):
        if self.document is None or self.saving:
            return
        name = self.file.get_basename() or "Untitled.txt"
        if markdown:
            name = name.rsplit(".", 1)[0] + ".md"
        dialog = Gtk.FileDialog(title="Save a Markdown Copy" if markdown else "Save File As", initial_name=name)
        dialog.save(self, self.io_cancel, self._save_as_chosen)

    def _save_as_chosen(self, dialog, result):
        try:
            destination = dialog.save_finish(result)
            if self.pending_format:
                action, self.pending_format = self.pending_format, None
                if not (destination.get_basename() or "").lower().endswith((".md", ".markdown", ".mdown")):
                    self._error("Choose a Markdown filename", "Use .md to preserve formatting. Your original file is unchanged.")
                    self.close_after_save = False
                    return
                # Keep the original .txt buffer intact until a separate copy is
                # selected. A failed write leaves the formatted buffer unsaved.
                self.markdown_destination = destination
                action()
            self._write(destination, self.etag if destination.equal(self.file) else None)
        except GLib.Error:
            self.pending_format = None
            self.close_after_save = False

    def _error(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.present(self)

    def _close_file(self, *_):
        if self.saving:
            self.close_after_save = True
            return True
        if self.buffer.get_modified():
            since = self.unsaved_since
            document = UnsavedDocument(self.file.get_basename() or "Text file", window=self, kind="document",
                                       unsaved_seconds=None if since is None else time.monotonic() - since)
            confirm_save(self, [document], self._close_answered)
            return True
        self.closed = True
        self.io_cancel.cancel()
        return False

    def _close_answered(self, response):
        if response.choice is SaveChoice.DISCARD:
            self.buffer.set_modified(False)
            GLib.idle_add(self._close_now)
        elif response.choice is SaveChoice.SAVE:
            self.save_answer = response
            self.close_after_save = True
            self.save()

    def _close_now(self):
        """Close after Don't Save.

        Once the sheet had been answered with Cancel, neither Gtk.Window.close()
        nor a close-request emitted from here reached this window's handler
        (reproduced under %check), so Don't Save left the window open. The
        answer is already final, so do what the handler would and go.
        """
        self.closed = True
        self.io_cancel.cancel()
        if hasattr(self, "_save_state"):
            self._save_state()
        self.destroy()
        return GLib.SOURCE_REMOVE

    def _answer_save(self, problem):
        """Tell a waiting close prompt how the save went; say whether one was waiting."""
        response, self.save_answer = self.save_answer, None
        if response is None:
            return False
        if problem:
            response.fail(f"Couldn’t save: {problem}. Your edits remain here.")
        else:
            response.done()
        return True
