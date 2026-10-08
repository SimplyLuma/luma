# SPDX-License-Identifier: Apache-2.0

"""Viewer — open a file, fast, and look at it.

Three panes: the files you have opened, the document, and what is known about
it. The document is the only thing in the window with colour.

Two promises are kept in code rather than in prose. Opening a file never writes
to it. Marks live in Viewer until they are explicitly saved, and the
main action bar holds the editing and save actions.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import sys
import threading
from dataclasses import replace
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import (
    add_style_sheet, apply_type,
    AppWindow, Command, CommandGroup, CommandRegistry, ConnectedButtonGroup,
    ColorSwatch, EmptyState, ListEmptyState, IconButton, Island, NavigationRow, NavigationSidebar,
    SectionLabel, StatusBar, Toolbar,
    install_appkit, command_popover, icons, Toast, DestructiveDialog, lumaui_tokens,
)

from .composition import ViewerUI
from .fixture import FixtureRecents
from . import formats
from .annotations import History, Mark, Viewport, DocumentViewport
from .drawing import draw_mark, draw_marks, mark_bounds
from .canvas import DocumentCanvas
from .page_navigation import PageThumbnail
from .document_fields import DocumentField
from .export import save_image, save_pdf
from .formats import FileFacts, TIER_ONE, TIER_TWO, TIER_THREE
from .recents import Recents, format_size


def _point_rectangle(x: float, y: float) -> Gdk.Rectangle:
    """Build the menu anchor; boxed Rectangle constructor keywords are ignored."""
    rectangle = Gdk.Rectangle()
    rectangle.x, rectangle.y, rectangle.width, rectangle.height = int(x), int(y), 1, 1
    return rectangle


APP_ID = "org.projectluma.Viewer.LumaUIPreview" if os.environ.get("LUMA_VIEWER_FIXTURE") or os.environ.get("LUMA_VIEWER_PREVIEW") else "org.projectluma.Viewer"
ICON_NAME = "org.projectluma.Viewer"
SIDEBAR_WIDTH = 178
INFO_WIDTH = 278
ISLAND_GAP = 8


def scroll_zoom_multiplier(dy: float, natural_scroll: bool) -> float:
    """Use the active device's scroll direction for Ctrl+wheel zoom."""
    if dy == 0:
        return 1.0
    zoom_in = (dy > 0) if natural_scroll else (dy < 0)
    return 1.1 if zoom_in else 1 / 1.1


def scroll_zoom_schema(device) -> str:
    touchpad=device is not None and device.get_source()==Gdk.InputSource.TOUCHPAD
    return ('org.gnome.desktop.peripherals.touchpad' if touchpad
            else 'org.gnome.desktop.peripherals.mouse')



class ViewerWindow(ViewerUI, AppWindow):

    # ── Commands ─────────────────────────────────────────────────────────

    def _application_commands(self) -> CommandRegistry:
        return CommandRegistry((
            CommandGroup("", (
                Command("viewer.save-copy", "Save annotated copy…", self._save_copy,
                        icons.icon_name("copy-plus"), shortcut=("Ctrl", "S")),
                Command("viewer.undo", "Undo", self._undo_mark,
                        icons.icon_name("undo-2"), shortcut=("Ctrl", "Z")),
                Command("viewer.redo", "Redo", self._redo_mark,
                        icons.icon_name("redo"), shortcut=("Ctrl", "Shift", "Z")),
                Command("viewer.delete", "Delete selected mark", self._delete_mark,
                        icons.icon_name("trash-2")),
                Command("viewer.recents", "Recent files", self._show_recents,
                        icons.icon_name("history")),
                Command("viewer.search", "Search recent files", self._toggle_search,
                        icons.icon_name("search"), shortcut=("Ctrl", "F")),
                Command("viewer.info", "Information", self._toggle_info,
                        icons.icon_name("info")),
                Command("viewer.zoom-in", "Zoom in", lambda: self._set_zoom(self.zoom*1.25),
                        icons.icon_name("zoom-in")),
                Command("viewer.zoom-out", "Zoom out", lambda: self._set_zoom(self.zoom/1.25),
                        icons.icon_name("zoom-out")),
                Command("viewer.fit", "Fit page", self._reset_fit,
                        icons.icon_name("scan")),
                Command("viewer.open", "Open a file…", self._choose_file,
                        icons.icon_name("folder-open"), shortcut=("Ctrl", "O")),
            )),
            CommandGroup("", (
                Command("viewer.clear-recents", "Clear Recents", self._clear_recents,
                        icons.icon_name("trash-2")),
            )),
            CommandGroup("", (
                Command("viewer.about", "About Viewer", self._show_about,
                        icons.icon_name("info")),
                Command("viewer.quit", "Quit Viewer", self.close,
                        icons.icon_name("log-out"), shortcut=("Ctrl", "Q")),
            )),
        ))

    # ── Sidebar: Recents ─────────────────────────────────────────────────


    def _activate_recent(self, _list, row):
        # A native row selects itself before activation. Keep the current
        # document selected while a switch can still be cancelled or refused.
        current = self.file_rows.get(str(self.facts.path)) if self.facts else None
        self.files_box.select_row(current)
        self.open_path(row.viewer_path)




    def _row_menu(self, entry, row: Gtk.Widget, x: float, y: float) -> None:
        registry = CommandRegistry((CommandGroup("", (
            Command("viewer.recent-open", "Open", lambda: self.open_path(entry.path), icons.icon_name("folder-open")),
            Command("viewer.recent-copy-path", "Copy path", lambda: self._copy_path(entry.path), icons.icon_name("copy")),
            Command("viewer.recent-remove", "Remove from Recents", lambda: self._forget(entry.path), icons.icon_name("trash-2")),
        )),))
        popover = command_popover(registry)
        popover.set_parent(row)
        popover.set_pointing_to(_point_rectangle(x, y))
        popover.connect("closed", lambda widget: widget.unparent())
        popover.popup()

    def _copy_path(self, path: str) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(path)

    def _forget(self, path: str) -> None:
        self.recents.forget(path)
        self._refresh_files()

    def _clear_recents(self) -> None:
        self.recents.clear()
        self._refresh_files()

    # ── Main: the document ───────────────────────────────────────────────



    # ── Opening ──────────────────────────────────────────────────────────

    @property
    def marks(self):
        return self.history.marks

    def open_path(self, path: str, *, navigation=False) -> None:
        if self.saving or self.guard_open:
            return
        target = Path(path).expanduser().absolute()
        if self.facts and target == self.facts.path:
            return
        if self.fixture and self.recents.sample(target) is None:
            Toast.show(self.host,"Fixture is read-only");return
        if self.fixture and self.recents.sample(target).get("gone"):
            Toast.show(self.host,self.recents.sample(target)["name"]+" was moved or deleted. Filer can look for it");return
        # Keep arrow traversal stable as opening a real file moves its history
        # entry to the front. A manual choice starts a new traversal order.
        if not navigation:self._navigation_order=None
        self._open_path(target)

    def _open_path(self, target) -> None:
        finish=getattr(self,"_finish_text",None)
        if finish:finish(True)
        self.comparison_selection.clear()
        self._cancel_drag()
        self.live=False;self._sync_live_text()
        if self.facts is not None:
            self.sessions[str(self.facts.path)]=(self.history,dict(self._change_snapshot(),saved_changes=dict(self.saved_changes)))
            self.recents.remember_position(str(self.facts.path), page=self.page)
        self.load_token += 1  # invalidate every outstanding file callback, even missing files
        self.facts = self.recents.facts_for(target) if self.fixture else formats.inspect(target)
        self.history = History()
        self.rotation, self.page, self.page_count = 0, 0, 1
        self.adjustments={};self.crop=None;self.flip=False;self.compare=None;self.live=False;self.cropping=False;self.form_values={};self.saved_changes={}
        if str(target) in self.sessions:
            self.history,changes=self.sessions[str(target)]
            for key,value in changes.items():setattr(self,key,value)
        self._process_token=getattr(self,"_process_token",0)+1
        self.zoom = 1.0;self.zoom_fitted=True;self.pan=(0,0)
        self.document = self.pixbuf = self.texture = self.source_bytes = None
        self.loaded = False
        self.last_saved = ""
        self.title_label.set_label(self.facts.name)
        self.set_identity_subtitle(self.facts.name)
        if (self.fixture and self.facts.extra.get("gone")) or (not self.fixture and not target.is_file()):
            self._show_missing(self.facts)
        else:
            entry = self.recents.record(str(target), kind=self.facts.kind)
            self.page = max(entry.page, 0)
            self._render()
        self._refresh_pager()
        self._refresh_files()
        self._refresh_info()
        self._refresh_actions()

    def _show_missing(self, facts: FileFacts) -> None:
        self._clear_stage()
        self.stage.append(EmptyState(
            "That file has moved",
            f"{facts.name} is no longer at {facts.where}. "
            "It stays in Recents until you remove it.",
            icons.icon_name("image-off"),
        ))
        self.status_path.set_label(str(facts.path))
        self.status_detail.set_label("Missing")

    def _clear_stage(self) -> None:
        self._stop_media_preview()
        preview = getattr(self, '_document_preview', None)
        if preview is not None:
            self._document_preview = None
            preview.clear()
        while child := self.stage.get_first_child():
            self.stage.remove(child)

    def _stop_media_preview(self):
        media = getattr(self, "_preview_media", None)
        if media is not None:
            self._preview_media = None
            handler = getattr(self, "_preview_media_handler", None)
            if handler is not None:
                media.disconnect(handler)
                self._preview_media_handler = None
            media.set_playing(False)
            media.clear()

    def _render_audio(self, facts, token):
        from luma_appkit import MediaTransport
        from .audio_preview import AudioPreview
        media = AudioPreview(Gio.File.new_for_path(str(facts.path)))
        self._preview_media = media
        transport = MediaTransport("deck", label=f"Preview {facts.name}", volume=1.0,
            on_play=media.set_playing, on_seek=lambda seconds: media.seek(int(seconds * 1_000_000)),
            on_volume=media.set_volume)
        transport.set_name("vw-audio-transport")
        transport.attach_shortcuts(transport)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                         valign=Gtk.Align.CENTER, hexpand=True, vexpand=True)
        column.append(apply_type(Gtk.Label(label=facts.name, wrap=True), "title-1"))
        column.append(transport)
        column.append(transport.volume_control)
        self.stage.append(Adw.Clamp(maximum_size=560, child=column))
        def changed(*_):
            if token != self.load_token or self._preview_media is not media:
                return
            if media.get_error():
                self._render_failed(token, media.get_error().message)
                return
            transport.set_duration(media.get_duration() / 1_000_000)
            transport.set_position(media.get_timestamp() / 1_000_000)
            transport.set_playing(media.get_playing())
            self.loaded = media.is_prepared()
        self._preview_media_handler = media.connect("changed", changed)
        changed()
        self._refresh_status()

    def _render(self) -> None:
        facts = self.facts
        if facts is None:
            return
        self._clear_stage()
        self._refresh_surround()
        self.load_token += 1
        token = self.load_token

        if facts.kind == "Audio":
            self._render_audio(facts, token)
            return

        if facts.kind in ('EPUB book', '3D model'):
            self._render_document_preview(facts, token)
            return

        if facts.tier == TIER_THREE:
            self._render_unsupported(facts)
            return
        if facts.tier == TIER_TWO:
            self._render_handoff(facts)
            return
        if facts.kind == "Image":
            self._render_image_async(facts, token)
        elif facts.kind == "PDF document":
            self._render_pdf_async(facts, token)
        elif facts.kind == "Table":
            self._render_readable_async(facts,token)
        else:
            self._render_readable_async(facts,token)

    def _render_document_preview(self, facts, token):
        self.stage.append(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                                     valign=Gtk.Align.CENTER, vexpand=True))
        def work():
            try:
                if facts.kind == 'EPUB book':
                    from .book_preview import open_book
                    result = open_book(facts.path)
                else:
                    from .mesh import load_mesh
                    result = load_mesh(facts.path)
            except Exception as error:
                GLib.idle_add(self._render_failed, token, str(error), priority=GLib.PRIORITY_DEFAULT)
                return
            def ready():
                if token != self.load_token or self.facts is not facts:
                    if facts.kind == 'EPUB book':
                        result.zip.close()
                    return False
                self._clear_stage()
                def prepared():
                    if token == self.load_token and self.facts is facts:
                        self.loaded = True
                        self._refresh_status()
                        self._refresh_info()
                if facts.kind == 'EPUB book':
                    from .book_preview import create_preview
                    self.page_count = len(result.sections)
                    facts.summary = result.metadata.title
                    def section_changed(section):
                        if token != self.load_token or self.facts is not facts:
                            return
                        self.page = section
                        self._refresh_pager()
                        self._refresh_status()
                    try:
                        preview = create_preview(result, section_changed, prepared,
                            lambda reason: self._render_failed(token, reason))
                    except Exception as error:
                        result.zip.close()
                        self._render_failed(token, str(error))
                        return False
                else:
                    from .mesh_preview import MeshPreview
                    preview = MeshPreview(result)
                    facts.summary = f'{len(result.triangles):,} triangles'
                    facts.dimensions = ' × '.join(f'{value:g}' for value in result.dimensions)
                # Reserve the existing document controls above read-only pages,
                # as for PDFs, so book headings cannot sit under the corner pill.
                preview.set_margin_top(76 + (int(lumaui_tokens.PHONE_FRAME["status"]) if self.phone else 0))
                self._document_preview = preview
                self.stage.append(preview)
                if facts.kind == 'EPUB book':
                    preview.show_section(self.page)
                else:
                    prepared()
                self._refresh_actions()
                self._refresh_pager()
                return False
            GLib.idle_add(ready, priority=GLib.PRIORITY_DEFAULT)
        threading.Thread(target=work, daemon=True).start()

    def _render_readable_async(self,facts,token):
        self.stage.append(Gtk.Spinner(spinning=True,halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,vexpand=True))
        def work():
            try:data=formats.read_table(facts.path) if facts.kind=='Table' else formats.read_text(facts.path)
            except Exception as error:
                GLib.idle_add(self._render_failed,token,str(error));return
            def ready():
                if token!=self.load_token or self.facts is not facts:return False
                self._clear_stage()
                if facts.kind=='Table':self._render_table(facts,data)
                else:self._render_text(facts,data)
                return False
            GLib.idle_add(ready)
        threading.Thread(target=work,daemon=True).start()

    def _stage_frame(self, child: Gtk.Widget) -> Gtk.Widget:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("vw-canvas")
        frame.set_halign(Gtk.Align.CENTER)
        frame.set_valign(Gtk.Align.CENTER)
        frame.append(child)
        return frame

    def _render_image_async(self, facts: FileFacts, token: int) -> None:
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER, vexpand=True)
        self.stage.append(spinner)

        def work() -> None:
            try:
                pixbuf = self.recents.pixbuf(facts.path) if self.fixture else None
                if pixbuf is None:
                    pixbuf, size = formats.load_image(facts.path)
                else:
                    size = pixbuf.get_width(), pixbuf.get_height()
            except Exception as error:  # a hostile or broken file must not crash
                GLib.idle_add(self._render_failed, token, str(error))
                return
            GLib.idle_add(self._image_ready, token, pixbuf, size)

        threading.Thread(target=work, daemon=True).start()

    def _image_ready(self, token: int, pixbuf, size) -> bool:
        if token != self.load_token or self.facts is None:
            return False
        self.pixbuf = pixbuf
        self.processed = pixbuf
        self.texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        self.facts.dimensions = f"{size[0]} × {size[1]}"
        megapixels = (size[0] * size[1]) / 1_000_000
        self.facts.summary = f"{megapixels:.0f} MP · {size[0]} × {size[1]}"
        self._clear_stage()
        self.loaded = True
        self._update_processed_image()
        self._build_canvas()
        self.page_count = 1
        self._refresh_pager()
        self._refresh_status()
        self._store_thumbnail(pixbuf)
        self._load_text_regions()
        self._refresh_info()
        self._refresh_actions()
        self._fixture_state()
        return False

    def _store_thumbnail(self, pixbuf) -> None:
        if self.facts is None:
            return
        try:
            width, height = pixbuf.get_width(), pixbuf.get_height()
            scale = 76 / max(width, height, 1)
            small = pixbuf.scale_simple(max(int(width * scale), 1),
                                        max(int(height * scale), 1), 2)
            if self.recents.store_thumbnail(str(self.facts.path), small) is not None:
                # The row was drawn before the thumbnail existed; draw it again
                # now that it does, rather than leaving a generic icon.
                self._refresh_files()
        except Exception:
            pass

    def _render_pdf_async(self, facts: FileFacts, token: int) -> None:
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER, vexpand=True)
        self.stage.append(spinner)

        def work() -> None:
            try:
                gi.require_version("Poppler", "0.18")
                from gi.repository import Poppler
                # Snapshot the bytes used for this session; save never rereads a
                # changed/deleted source and combines it with stale annotations.
                source_bytes = self.recents.pdf_bytes(facts.path) if self.fixture else formats.read_pdf_bytes(facts.path)
                document = Poppler.Document.new_from_bytes(GLib.Bytes.new(source_bytes), None)
                count = document.get_n_pages()
                from .pdf_text import document_text
                regions=document_text(document)
            except Exception as error:
                GLib.idle_add(self._render_failed, token, str(error))
                return
            GLib.idle_add(self._pdf_ready, token, document, count, source_bytes, regions)

        threading.Thread(target=work, daemon=True).start()

    def _pdf_ready(self, token: int, document, count: int, source_bytes, regions=()) -> bool:
        if token != self.load_token or self.facts is None:
            return False
        self.document = document
        if not self.fixture and self.form_values:
            for number in range(count):
                for mapping in document.get_page(number).get_form_field_mapping():
                    field=mapping.field
                    if str(field.get_id()) in self.form_values:field.text_set_text(self.form_values[str(field.get_id())])
        self.source_bytes = source_bytes
        self.loaded = True
        self.page_count = max(count, 1)
        self.page = min(self.page, self.page_count - 1)
        self.facts.summary = f"{self.page_count} page" if self.page_count == 1 \
            else f"{self.page_count} pages"
        self.pdf_text_regions=regions
        self._draw_pdf_page()
        self._refresh_actions()
        self._refresh_pager()
        self._refresh_status()
        self._refresh_info()
        self._fixture_state()
        return False

    def _draw_pdf_page(self) -> None:
        self._clear_stage()
        layout=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,hexpand=True,vexpand=True)
        thumbs=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14,width_request=96)
        thumbs.set_margin_top(76);thumbs.set_halign(Gtk.Align.START)
        pages=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=26,halign=Gtk.Align.CENTER)
        from luma_appkit import lumaui_tokens
        pages.set_margin_top(76 + (int(lumaui_tokens.PHONE_FRAME["status"]) if self.phone else 0));pages.set_margin_bottom(110)
        self.pdf_pages=pages
        self.pdf_scroll=Gtk.ScrolledWindow(child=pages,hexpand=True,vexpand=True,hscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.pdf_areas=[];self.pdf_text_labels=[];self.pdf_thumbnails=[];self.form_entries={}
        for number in range(self.page_count):
            page=self.document.get_page(number);w,h=page.get_size()
            thumb=PageThumbnail(number+1,lambda n=number:self.page==n,lambda n=number:self._go_page(n))
            thumb.set_name('vw-page-thumbnail-'+str(number+1));thumbs.append(thumb);self.pdf_thumbnails.append(thumb)
            area=DocumentCanvas(lambda width,height:((0,0,width,height,'pdf'),),
                content_width=int(w),content_height=int(h),focusable=True)
            area.set_overflow(Gtk.Overflow.VISIBLE)
            area.viewer_page=number;area.viewer_width=w;area.viewer_height=h
            area.set_name('vw-page-'+str(number+1));area.set_draw_func(self._draw_pdf_canvas)
            overlay=Gtk.Overlay(child=area);fields=Gtk.Fixed();overlay.add_overlay(fields)
            for region in self.pdf_text_regions[number] if number<len(self.pdf_text_regions) else ():
                text=Gtk.Label(label=region.text,selectable=True,xalign=0,yalign=0)
                text.add_css_class('vw-live-text');text.set_can_target(self.mode=='view')
                attrs=Pango.AttrList();attrs.insert(Pango.attr_size_new_absolute(round(region.size*Pango.SCALE)))
                text.set_attributes(attrs);fields.put(text,region.x,region.y);self.pdf_text_labels.append(text)
            pages.append(overlay);self.pdf_areas.append(area)
            definitions=[]
            if self.fixture:
                definitions=[(key,placeholder,x,y,fw,None) for n,key,placeholder,x,y,fw
                    in self.recents.pdf_field_positions if n==number]
            else:
                gi.require_version('Poppler','0.18')
                from gi.repository import Poppler
                for mapping in page.get_form_field_mapping():
                    field=mapping.field
                    if field.get_field_type()==Poppler.FormFieldType.TEXT and not field.is_read_only():
                        rect=mapping.area
                        definitions.append((str(field.get_id()),field.get_name() or 'Text',rect.x1,h-rect.y2,rect.x2-rect.x1,field))
            for key,placeholder,x,y,fw,field in definitions:
                entry=DocumentField(placeholder_text=placeholder,text=self.form_values.get(key,(field.text_get_text() or '') if field else ''))
                apply_type(entry,'meta',weight=600)
                entry.set_size_request(round(fw),24);entry.set_name('vw-form-'+key);entry.add_css_class('vw-document-field')
                entry.update_property([Gtk.AccessibleProperty.LABEL],[placeholder])
                def changed(edit,k=key,f=field):
                    self.form_values[k]=edit.get_text()
                    if f:f.text_set_text(edit.get_text())
                    self._refresh_info();self._refresh_actions()
                entry.connect('changed',changed);fields.put(entry,x,y)
                self.form_entries[key]=entry
            drag=Gtk.GestureDrag(button=1)
            def begin(g,x,y,a=area,n=number):
                self.page=n;self.marks_area=a;self._mark_begin(g,x,y)
            drag.connect('drag-begin',begin);drag.connect('drag-update',self._mark_update);drag.connect('drag-end',self._mark_end);area.add_controller(drag)
            keys=Gtk.EventControllerKey();keys.connect('key-pressed',self._canvas_key);area.add_controller(keys)
        self.marks_area=self.pdf_areas[self.page]
        layout.append(thumbs);layout.append(self.pdf_scroll);self.stage.append(layout)

    def _draw_pdf_canvas(self,area,cr,width,height):
        number=area.viewer_page;cr.save();cr.scale(width/area.viewer_width,height/area.viewer_height)
        cr.set_source_rgb(1,1,1);cr.paint();self.document.get_page(number).render(cr)
        draw_marks(cr,[m for m in self.marks if m.page==number],self.document.get_page(number).render)
        if self.pending and self.pending.page==number:draw_mark(cr,self.pending)
        cr.restore()

    def _page_size(self):
        if self.document is not None:
            return self.document.get_page(self.page).get_size()
        if self.pixbuf is not None:
            return self.pixbuf.get_width(), self.pixbuf.get_height()
        return 1, 1

    def _build_canvas(self):
        self._clear_stage()
        area = DocumentCanvas(self._canvas_outlines, self._canvas_divider, hexpand=True, vexpand=True, focusable=True)
        area.set_name('vw-canvas')
        area.update_property([Gtk.AccessibleProperty.LABEL],
                             ["Document canvas. Choose Draw or sign to write; select a mark to move or delete it. In Select, N cycles marks, arrows move, Delete removes, Escape deselects."])
        area.set_draw_func(self._draw_canvas)
        self.marks_area = area
        self.stage.set_vexpand(True)
        self.stage.append(area)
        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", self._mark_begin)
        drag.connect("drag-update", self._mark_update)
        drag.connect("drag-end", self._mark_end)
        drag.connect("cancel", lambda *_: self._cancel_drag())
        area.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._canvas_key)
        area.add_controller(keys)
        scroll=Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        def wheel(_controller,_dx,dy):
            return self._wheel_zoom(_controller,dy)
        scroll.connect("scroll",wheel);area.add_controller(scroll)
        area.connect("resize", self._canvas_resized)
        self._resize_canvas()

    def _wheel_zoom(self, controller, dy):
        state=controller.get_current_event_state()
        if self.pixbuf is None or self.compare or not state & (Gdk.ModifierType.CONTROL_MASK|Gdk.ModifierType.META_MASK):
            return False
        device=controller.get_current_event_device()
        settings=getattr(self,'_zoom_scroll_settings',None)
        if settings is None:
            settings=self._zoom_scroll_settings={}
        schema=scroll_zoom_schema(device)
        if schema not in settings:settings[schema]=Gio.Settings.new(schema)
        natural=settings[schema].get_boolean('natural-scroll')
        factor=scroll_zoom_multiplier(dy,natural)
        if factor!=1:self._set_zoom(self.zoom*factor)
        return True

    def _canvas_resized(self,*_args):
        self._cancel_drag();self._sync_live_text();self._sync_compare_labels()
        shown=self._zoom_text()
        if shown!=getattr(self,'_last_zoom_text',None):
            self._last_zoom_text=shown;self._refresh_actions()

    def _resize_canvas(self):
        if not self.loaded or not hasattr(self, "marks_area"):
            return
        if self.document is not None:
            for area in getattr(self,"pdf_areas",[]):area.queue_draw()
            return
        w = max(100, self.stage_scroll.get_width()-32)
        h = max(100, self.stage_scroll.get_height()-32)
        self.marks_area.set_content_width(0)
        self.marks_area.set_content_height(0)
        self.marks_area.queue_draw()
        self._sync_live_text()

    def _viewport(self):
        w, h = self._page_size()
        if self.document is not None:
            return Viewport(w,h,max(1,self.marks_area.get_width()),max(1,self.marks_area.get_height()))
        sw,sh=w,h
        if self.crop and not self.hold:w,h=self.crop[2:]
        base=Viewport(w,h,max(1,self.marks_area.get_width()),max(1,self.marks_area.get_height()),self.rotation,0 if self.phone else 120,0 if self.phone else 150,self.zoom,1.5)
        return DocumentViewport(base,sw,sh,self.crop if not self.hold else None,self.flip if not self.hold else False,self.adjustments.get('str',0) if not self.hold else 0,self.pan)

    def _draw_canvas(self, area, cr, width, height):
        if not self.loaded:
            return
        if self.compare:
            self._draw_compare(cr,width,height)
            return
        viewport = self._viewport()
        cr.save()
        # Round only the displayed sheet; stored coordinates and exports keep
        # the complete source rectangle, including its corner pixels.
        x, y = viewport.offset
        rw, rh = (dimension * viewport.scale for dimension in viewport.rotated_size)
        from luma_appkit import lumaui_tokens
        radius = 0 if self.cropping else min(lumaui_tokens.MEDIA['tile']['radius'], rw / 2, rh / 2)
        for cx, cy, start in ((x+rw-radius, y+radius, -math.pi/2),
                              (x+rw-radius, y+rh-radius, 0),
                              (x+radius, y+rh-radius, math.pi/2),
                              (x+radius, y+radius, math.pi)):
            cr.arc(cx, cy, radius, start, start+math.pi/2)
        cr.close_path()
        cr.clip()
        viewport.transform(cr)
        pw, ph = self._page_size()
        if self.crop and not self.hold:
            cx,cy,cw,ch=self.crop;cr.rectangle(0,0,cw,ch);cr.clip()
        else:cx=cy=0;cw,ch=pw,ph;cr.rectangle(0, 0, pw, ph);cr.clip()
        if self.flip and not self.hold:cr.translate(cw,0);cr.scale(-1,1)
        cr.translate(-cx,-cy)
        if self.adjustments.get('str') and not self.hold:
            cr.translate(pw/2,ph/2);cr.rotate(math.radians(self.adjustments['str']));cr.translate(-pw/2,-ph/2)
        if self.document is not None:
            cr.set_source_rgb(1, 1, 1)
            cr.paint()
            self.document.get_page(self.page).render(cr)
        elif self.pixbuf is not None:
            self._paint_image(cr)
        for index, mark in enumerate(self.marks):
            if mark.page != self.page:
                continue
            shown = self.pending if index == self.selected and self.tool == "select" and self.pending else mark
            draw_marks(cr,[shown],self._paint_source)
            if index == self.selected and self.mode == "markup":
                x1,y1,x2,y2 = mark_bounds(cr, shown)
                from luma_appkit.media_style import colour
                ink=colour(area,'luma_blue')
                cr.set_source_rgba(ink.red,ink.green,ink.blue,ink.alpha)
                cr.set_line_width(1/max(viewport.scale,0.001))
                cr.set_dash([4/max(viewport.scale,0.001)])
                cr.rectangle(x1,y1,x2-x1,y2-y1)
                cr.stroke()
                cr.set_dash([])
        if self.pending and self.tool != "select":
            draw_marks(cr,[self.pending],self._paint_source)
        if self.live and not self.document:
            from luma_appkit.media_style import colour
            ink=colour(area,'luma_blue',.85)
            cr.set_source_rgba(ink.red,ink.green,ink.blue,ink.alpha)
            # v70's detector underline is three source-image pixels, so it
            # scales with the document. It is a viewing overlay, never exported.
            for dx,dy,dw,dh in getattr(self,'detected_bounds',()):
                cr.rectangle(dx,dy+dh-3,dw,3)
            cr.fill()
        cr.restore()
        if self.cropping and self.pixbuf is not None:
            self._draw_crop_overlay(cr, viewport)

    def _paint_image(self, cr):
        image = self.pixbuf if self.hold else getattr(self, 'processed', self.pixbuf)
        cr.save()
        # The interactive adjustment frame can match the displayed viewport
        # instead of the source resolution. Scale once in Cairo; an intermediate
        # bilinear enlargement made every slider change visibly soft.
        cr.scale(self.pixbuf.get_width()/image.get_width(), self.pixbuf.get_height()/image.get_height())
        Gdk.cairo_set_source_pixbuf(cr, image, 0, 0)
        cr.paint()
        cr.restore()

    def _draw_crop_overlay(self, cr, viewport):
        cx,cy,cw,ch=self.crop_box
        points=(viewport.to_view((cx,cy)),viewport.to_view((cx+cw,cy)),
                viewport.to_view((cx,cy+ch)),viewport.to_view((cx+cw,cy+ch)))
        left=min(p[0] for p in points);right=max(p[0] for p in points)
        top=min(p[1] for p in points);bottom=max(p[1] for p in points)
        ox,oy=viewport.offset
        rw,rh=(d*viewport.scale for d in viewport.rotated_size)
        cr.save()
        cr.rectangle(ox,oy,rw,rh);cr.clip()
        cr.rectangle(ox,oy,rw,rh);cr.rectangle(left,top,right-left,bottom-top)
        cr.set_fill_rule(1)  # even-odd: dim only the unselected picture
        cr.set_source_rgba(0,0,0,.55);cr.fill()
        cr.set_source_rgba(1,1,1,.9);cr.set_line_width(1)
        cr.rectangle(left+.5,top+.5,right-left-1,bottom-top-1);cr.stroke()
        cr.set_source_rgba(1,1,1,.4)
        for part in (1,2):
            x=left+(right-left)*part/3;y=top+(bottom-top)*part/3
            cr.move_to(x,top);cr.line_to(x,bottom)
            cr.move_to(left,y);cr.line_to(right,y)
        cr.stroke()
        cr.set_source_rgba(1,1,1,1);cr.set_line_width(5)
        for x,side in ((left,1),(right,-1)):
            for y,vertical in ((top,1),(bottom,-1)):
                cr.move_to(x,y+vertical*22);cr.line_to(x,y);cr.line_to(x+side*22,y)
        cr.stroke();cr.restore()

    def _paint_source(self,cr):
        if self.document:self.document.get_page(self.page).render(cr)
        elif self.pixbuf:
            self._paint_image(cr)

    def _render_text(self, facts: FileFacts, text: str) -> None:
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        view.add_css_class("vw-mono")
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(text)
        view.set_hexpand(True)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroll.set_child(view)
        scroll.set_size_request(-1, 400)
        self.stage.append(self._stage_frame(scroll))
        lines = text.count("\n") + 1
        facts.summary = f"{lines} lines"
        self._refresh_pager()
        self._refresh_status()

    def _render_table(self, facts: FileFacts, data) -> None:
        header,rows=data
        grid = Gtk.Grid(column_spacing=0, row_spacing=0)
        grid.add_css_class("vw-sheet")
        for column, name in enumerate(header):
            label = Gtk.Label(label=name, xalign=0)
            label.add_css_class("vw-sheet-head")
            grid.attach(label, column, 0, 1, 1)
        for index, row in enumerate(rows[:200], start=1):
            for column, value in enumerate(row):
                label = Gtk.Label(label=value, xalign=0 if column == 0 else 1)
                label.add_css_class("vw-sheet-cell")
                label.set_ellipsize(Pango.EllipsizeMode.END)
                grid.attach(label, column, index, 1, 1)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroll.set_child(grid)
        self.stage.append(self._stage_frame(scroll))
        facts.summary = f"{len(rows)} rows · {len(header)} columns"
        self._refresh_pager()
        self._refresh_status()

    def _render_handoff(self, facts: FileFacts) -> None:
        """Tier 2: readable, read-only, and honest about who owns the file."""
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        notice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        notice.add_css_class("vw-handoff")
        text = Gtk.Label(label=facts.owner_note, xalign=0, hexpand=True, wrap=True)
        text.add_css_class("vw-handoff-text")
        notice.append(text)
        open_there = Gtk.Button(label=f"Open in {facts.owner}")
        open_there.add_css_class("luma-button")
        open_there.connect("clicked", lambda *_: self._open_elsewhere())
        notice.append(open_there)
        column.append(notice)
        column.append(EmptyState(
            "Preview not built yet",
            "Viewer will render this read-only through the document engine. "
            "That path is not wired up in this build, so nothing is shown "
            "rather than something wrong.",
            facts.icon,
        ))
        self.stage.append(column)
        self._refresh_pager()
        self._refresh_status()

    def _render_unsupported(self, facts: FileFacts) -> None:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         valign=Gtk.Align.CENTER, vexpand=True)
        column.append(EmptyState(facts.kind, facts.owner_note, facts.icon))
        if facts.owner:
            button = Gtk.Button(label=f"Open in {facts.owner}")
            button.add_css_class("luma-button")
            button.set_halign(Gtk.Align.CENTER)
            button.connect("clicked", lambda *_: self._open_elsewhere())
            column.append(button)
        self.stage.append(column)
        self._refresh_pager()
        self._refresh_status()

    def _render_failed(self, token: int, reason: str) -> bool:
        if token != self.load_token:
            return False
        self.loaded = False
        self._refresh_actions()
        self._clear_stage()
        self.stage.append(EmptyState(
            "That file could not be opened",
            ("The image decoder could not start safely in this environment. "
             "The file has not been changed." if "bwrap" in reason else
             f"{reason}. The file has not been changed."),
            icons.icon_name("triangle-alert"),
        ))
        self._refresh_status()
        return False

    def _open_elsewhere(self) -> None:
        if self.facts is None:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(self.facts.path.as_uri(), None)
        except GLib.Error:
            pass

    # ── Marks ────────────────────────────────────────────────────────────

    def _cancel_drag(self):
        self.pending = self.drag_origin = self.drag_viewport = None
        self._crop_drag = None
        self.pan_origin=None
        if hasattr(self, "marks_area"):
            self.marks_area.queue_draw()

    def _mark_begin(self, gesture, x, y):
        if self.cropping and self.pixbuf is not None and not self.saving:
            viewport=self._viewport()
            point=viewport.to_page((x,y))
            if point is None:return
            cx,cy,cw,ch=self.crop_box
            corners={'nw':(cx,cy),'ne':(cx+cw,cy),'sw':(cx,cy+ch),'se':(cx+cw,cy+ch)}
            handle=next((key for key,corner in corners.items()
                if max(abs(a-b) for a,b in zip(viewport.to_view(corner),(x,y)))<=24),None)
            if handle is None and cx<=point[0]<=cx+cw and cy<=point[1]<=cy+ch:handle='move'
            if handle is not None:self._crop_drag=(handle,point,self.crop_box,viewport)
            return
        if self.compare and self.compare_mode=="swipe":
            self.compare_fraction=max(0,min(1,(x-40)/max(1,self.marks_area.get_width()-80)))
            self.drag_origin=(x,y);self._sync_compare_labels();self.marks_area.queue_draw();return
        if self.mode=="view" and self.loaded and self.pixbuf is not None and not self.compare:
            if self.zoom>1:self.pan_origin=self.pan
            return
        if self.mode != "markup" or not self.loaded or self.saving:
            return
        viewport = self._viewport()
        point = viewport.to_page((x, y))
        if point is None:
            return
        self.marks_area.grab_focus()
        self.drag_origin, self.drag_viewport = (x, y), viewport
        self.selected = None
        scale=.5 if self.document else max(1,self._page_size()[0]/900)
        width=self.stroke_width*scale
        if self.tool=='step':
            self.history.add(Mark('step',self.ink,point,point,text=str(sum(m.tool=='step' for m in self.marks)+1),width=width,page=self.page))
            self._cancel_drag();self._edits_changed();return
        if self.tool=='sign' and self.fixture:
            size=1.1 if self.document else scale*2.4
            start=(point[0]-20,point[1]-36)
            self.history.add(Mark('fixture-sign','#1b2a6b',start,(start[0]+140*size,start[1]+40*size),width=size,page=self.page))
            self._cancel_drag();self._edits_changed();return
        if self.tool == "select":
            import cairo
            cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1))
            for index in range(len(self.marks)-1, -1, -1):
                mark = self.marks[index]
                if mark.page != self.page:
                    continue
                l,t,r,b = mark_bounds(cr, mark)
                if l <= point[0] <= r and t <= point[1] <= b:
                    self.selected = index
                    break
        elif self.tool == "text":
            self._cancel_drag()
            self._text_entry(point)
        else:
            self.pending = Mark(self.tool, self.ink, point, point,
                                text=str(sum(m.tool=="step" for m in self.marks)+1) if self.tool=="step" else "",
                                width=width*3.2 if self.tool=="highlight" else width, points=(point,), page=self.page)
        self._refresh_actions()
        self.marks_area.queue_draw()

    def _mark_update(self, gesture, dx, dy):
        if getattr(self,'_crop_drag',None) is not None:
            handle,start,(x,y,w,h),viewport=self._crop_drag
            origin=viewport.to_view(start)
            point=viewport.to_page((origin[0]+dx,origin[1]+dy),clamp=True)
            if point is None:return
            delta_x,delta_y=point[0]-start[0],point[1]-start[1]
            page_w,page_h=self._page_size()
            if handle=='move':
                x=max(0,min(page_w-w,x+delta_x));y=max(0,min(page_h-h,y+delta_y))
            else:
                left=x+delta_x if 'w' in handle else x
                right=x+w+delta_x if 'e' in handle else x+w
                top=y+delta_y if 'n' in handle else y
                bottom=y+h+delta_y if 's' in handle else y+h
                left=max(0,min(right-40,left));right=min(page_w,max(left+40,right))
                top=max(0,min(bottom-40,top));bottom=min(page_h,max(top+40,bottom))
                x,y,w,h=left,top,right-left,bottom-top
                ratio={'orig':page_w/page_h,'square':1,'16:9':16/9,'4:5':4/5}.get(self.crop_aspect)
                if ratio:
                    if abs(delta_x)>=abs(delta_y)*ratio:h=w/ratio
                    else:w=h*ratio
                    anchor_x=right if 'w' in handle else left
                    anchor_y=bottom if 'n' in handle else top
                    w=min(w,anchor_x if 'w' in handle else page_w-anchor_x,
                          (anchor_y if 'n' in handle else page_h-anchor_y)*ratio)
                    h=w/ratio
                    x=anchor_x-w if 'w' in handle else anchor_x
                    y=anchor_y-h if 'n' in handle else anchor_y
            self.crop_box=(x,y,w,h)
            self.marks_area.queue_draw()
            return
        if getattr(self,"pan_origin",None) is not None:
            self.pan=(self.pan_origin[0]+dx,self.pan_origin[1]+dy);self._resize_canvas();return
        if self.compare and self.compare_mode=="swipe" and self.drag_origin:
            self.compare_fraction=max(0,min(1,(self.drag_origin[0]+dx-40)/max(1,self.marks_area.get_width()-80)))
            self._sync_compare_labels();self.marks_area.queue_draw();return
        if self.drag_origin is None or self.drag_viewport is None:
            return
        point = self.drag_viewport.to_page((self.drag_origin[0]+dx,
                                            self.drag_origin[1]+dy), clamp=True)
        if self.tool == "select" and self.selected is not None:
            start = self.drag_viewport.to_page(self.drag_origin, clamp=True)
            mark = self.marks[self.selected]
            # Keep moved content within its source page.
            l,t,r,b = mark.bounds()
            w,h = self._page_size()
            mx = max(-l, min(w-r, point[0]-start[0]))
            my = max(-t, min(h-b, point[1]-start[1]))
            self.pending = mark.moved(mx, my)
        elif self.pending:
            if self.pending.tool in ('box','ellipse') and gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK:
                sx,sy=self.pending.start
                point=(point[0],sy+math.copysign(abs(point[0]-sx),point[1]-sy or 1))
            points = self.pending.points
            if self.pending.tool in ("ink","highlight","sign") and point != points[-1]:
                points = (*points, point)
            self.pending = replace(self.pending, end=point, points=points)
        self.marks_area.queue_draw()

    def _mark_end(self, gesture, dx, dy):
        if getattr(self,'_crop_drag',None) is not None:
            self._mark_update(gesture,dx,dy)
            self._crop_drag=None
            return
        self._mark_update(gesture, dx, dy)
        mark = self.pending
        if mark:
            if self.tool == "select" and self.selected is not None:
                marks = list(self.marks)
                marks[self.selected] = mark
                self.history.apply(marks)
            elif mark.tool in ("ink","highlight","sign","step") or abs(mark.end[0]-mark.start[0])>=4 or abs(mark.end[1]-mark.start[1])>=4:
                self.history.add(mark)
        self._cancel_drag()
        self._edits_changed()


    def _edits_changed(self):
        self._refresh_files()
        self._refresh_info()
        self._refresh_actions()
        if hasattr(self, "marks_area"):
            self.marks_area.queue_draw()

    def _undo_mark(self):
        if self.saving:
            return
        self._cancel_drag()
        self.history.undo()
        self.selected = None
        self._edits_changed()

    def _redo_mark(self):
        if self.saving:
            return
        self._cancel_drag()
        self.history.redo()
        self.selected = None
        self._edits_changed()

    def _clear_marks(self):
        if self.saving:
            return
        self._cancel_drag()
        self.history.apply(m for m in self.marks if m.page != self.page)
        self.selected = None
        self._edits_changed()

    def _delete_mark(self):
        if self.saving or self.selected is None:
            return
        self.history.apply(m for i,m in enumerate(self.marks) if i != self.selected)
        self.selected = None
        self._edits_changed()

    def _canvas_key(self, controller, key, code, state):
        if self.saving:
            return False
        if key == Gdk.KEY_Escape:
            self._cancel_drag()
            self.selected = None
            self._edits_changed()
            return True
        if key in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self._delete_mark()
            return True
        if self.tool == "select" and key == Gdk.KEY_n:
            choices = [i for i,m in enumerate(self.marks) if m.page == self.page]
            if choices:
                pos = choices.index(self.selected) if self.selected in choices else -1
                self.selected = choices[(pos+1)%len(choices)]
                self._edits_changed()
                return True
        directions = {Gdk.KEY_Left:(-1,0), Gdk.KEY_Right:(1,0),
                      Gdk.KEY_Up:(0,-1), Gdk.KEY_Down:(0,1)}
        if self.selected is not None and key in directions:
            dx,dy = directions[key]
            mark = self.marks[self.selected]
            moved = mark.moved(dx,dy)
            l,t,r,b = moved.bounds()
            w,h = self._page_size()
            if l >= 0 and t >= 0 and r <= w and b <= h:
                marks = list(self.marks)
                marks[self.selected] = moved
                self.history.apply(marks)
                self._edits_changed()
            return True
        return False


    def _guard_unsaved(self, action):
        self.guard_open=True
        def cancel():self.guard_open=False
        def discard(_option):
            self.guard_open=False;self.history.mark_saved()
            for history,_changes in self.sessions.values():history.mark_saved()
            self.adjustments={};self.crop=None;self.rotation=0;self.flip=False
            self.sessions.clear();action()
        DestructiveDialog.ask(self,title='Discard unsaved changes?',
            body='Save a copy of each edited file before closing Viewer.',
            action='Discard and close',on_confirm=discard,on_cancel=cancel)

    def _close_requested(self, *_):
        if self.saving or self.guard_open:return True
        finish=getattr(self,"_finish_text",None)
        if finish:finish(True)
        def pending(changes):
            return any(changes.get('adjustments',{}).values()) or changes.get('crop') or changes.get('rotation') or changes.get('flip') or changes.get('form_values')
        current=self._change_snapshot()
        changed=self.history.dirty or (current!=self.saved_changes and bool(pending(current)))
        changed=changed or any(h.dirty or ({k:v for k,v in c.items() if k!='saved_changes'}!=c.get('saved_changes',{}) and bool(pending(c))) for h,c in self.sessions.values())
        if changed:
            self._guard_unsaved(self.close);return True
        self._stop_media_preview()
        preview = getattr(self, '_document_preview', None)
        if preview is not None:
            self._document_preview = None
            preview.clear()
        self.load_token += 1
        return False

    def _error(self, heading, reason):
        Toast.show(self.host,heading+': '+str(reason),kind='error')

    def _save_copy(self, after=None):
        if not self.loaded or self.saving or self.facts is None:
            return
        if self.facts.kind not in ('Image', 'PDF document'):
            return
        if self.fixture:
            Toast.show(self.host, "Fixture is read-only")
            return
        self._cancel_drag()
        token = self.load_token
        chooser = Gtk.FileDialog(title="Save a copy")
        suffix = ".pdf" if self.document is not None else ".png"
        stem = self.facts.path.stem
        if formats.pdf_compression(self.facts.path):
            stem = Path(stem).stem  # report.pdf.gz -> report
        chooser.set_initial_name(stem + " annotated" + suffix)
        chooser.set_initial_folder(Gio.File.new_for_path(str(self.facts.path.parent)))
        def chosen(dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return
            if token != self.load_token or not file:
                return
            path = file.get_path()
            if not path:
                self._error("Copy could not be saved", "Choose a local destination supported by the file portal.")
                return
            if Path(path).suffix.lower() != suffix:
                self._error("Choose the copy format", f"This copy needs a {suffix} filename.")
                return
            self._export_to(Path(path), after)
        chooser.save(self, None, chosen)

    def _export_to(self, path, after=None):
        if self.fixture:
            raise PermissionError("Fixture mode cannot export to disk.")
        if self.saving or not self.loaded:
            return
        self.saving = True
        marks = self.marks
        token,history=self.load_token,self.history
        facts, data, pixbuf = self.facts, self.source_bytes, self.pixbuf
        adjustments=dict(self.adjustments);crop=self.crop;rotation=self.rotation;flip=self.flip;fields=dict(self.form_values)
        snapshot=self._change_snapshot()
        self._refresh_actions()
        def work():
            error = None
            try:
                if data is not None:
                    save_pdf(facts.path, path, data, marks, fields=fields)
                else:
                    from .processing import adjust_pixels
                    from gi.repository import GdkPixbuf
                    pixels=adjust_pixels(pixbuf.get_pixels(),pixbuf.get_width(),pixbuf.get_height(),pixbuf.get_rowstride(),pixbuf.get_n_channels(),adjustments)
                    adjusted=GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels),pixbuf.get_colorspace(),pixbuf.get_has_alpha(),8,pixbuf.get_width(),pixbuf.get_height(),pixbuf.get_rowstride())
                    save_image(facts.path,path,adjusted,marks,crop=crop,rotation=rotation,flip=flip,straighten=adjustments.get('str',0))
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(done, error)
        def done(error):
            self.saving = False
            if token!=self.load_token or self.facts is not facts:
                if error:
                    self._error("Copy could not be saved", error + f" Changes to {facts.name} remain in Viewer.")
                else:
                    history.saved=marks
                    session=self.sessions.get(str(facts.path))
                    if session and session[0] is history:
                        session[1]['saved_changes']=snapshot
                    if self.history is history:
                        self.saved_changes=snapshot
                    Toast.show(self.host,"Saved copy: " + path.name)
                self._refresh_actions()
                return False
            if error:
                self._error("Copy could not be saved", error + " Your annotations are still here.")
            else:
                self.history.saved = marks
                self.saved_changes=snapshot
                self.last_saved = str(path)
                self.status_detail.set_label("Saved copy: " + path.name)
                Toast.show(self.host, "Saved copy: " + path.name)
            self._edits_changed()
            if not error and callable(after):
                after()
            return False
        threading.Thread(target=work, daemon=True).start()


    # ── Information ──────────────────────────────────────────────────────



    # ── State ────────────────────────────────────────────────────────────

    def _refresh_pager(self) -> None:
        multi = self.page_count > 1
        self.prev_button.set_sensitive(multi and self.page > 0)
        self.next_button.set_sensitive(multi and self.page < self.page_count - 1)

    def _refresh_status(self) -> None:
        facts = self.facts
        if facts is None:
            self.status_path.set_label("")
            self.status_detail.set_label("")
            return
        self.status_path.set_label(f"{facts.where}/{facts.name}")
        parts = [format_size(facts.size)]
        if facts.dimensions:
            parts.append(facts.dimensions)
        if self.page_count > 1:
            parts.append(f"page {self.page + 1} of {self.page_count}")
        self.status_detail.set_label(" · ".join(parts))

    def _step_page(self, direction: int) -> None:
        if self.page_count <= 1:
            return
        if self.saving:
            return
        self._cancel_drag()
        self.selected = None
        self.page = max(0, min(self.page + direction, self.page_count - 1))
        preview = getattr(self, '_document_preview', None)
        if self.facts is not None and self.facts.kind == 'EPUB book' and preview is not None:
            preview.show_section(self.page)
        if self.facts is not None:
            self.recents.remember_position(str(self.facts.path), page=self.page)
        if self.document is not None and hasattr(self,"pdf_scroll"):
            area=self.pdf_areas[self.page]
            valid,bounds=area.compute_bounds(self.pdf_pages)
            if valid:
                self.pdf_scroll.get_vadjustment().set_value(max(0,self.pdf_pages.get_margin_top()+bounds.origin.y-60))
            self.marks_area=area
            for thumb in self.pdf_thumbnails:thumb.face.queue_draw()
        self._refresh_pager()
        self._refresh_status()
        self._refresh_actions()

    def _rotate(self) -> None:
        self._cancel_drag()
        self.rotation = (self.rotation - 90) % 360
        self._processing_changed();self._resize_canvas()

    def _set_zoom(self, zoom):
        self._cancel_drag()
        fit=self._viewport().scale/self.zoom if self.pixbuf is not None else 1.
        self.zoom = max(0.1,min(8.0,zoom*fit))/max(fit,.0001)
        self.zoom_fitted=False
        self._resize_canvas()
        self._refresh_actions()

    def _mode_toggled(self, button: Gtk.ToggleButton, value: str) -> None:
        if button.get_active():
            self._cancel_drag()
            self.mode = value
            for name, other in self.mode_buttons.items():
                if name != value and other.get_active():
                    other.set_active(False)
        elif value == self.mode:
            button.set_active(True)

    def _tool_toggled(self, button: Gtk.ToggleButton, value: str) -> None:
        if button.get_active():
            self._cancel_drag()
            self.tool = value
            for name, other in self.tool_buttons.items():
                if name != value and other.get_active():
                    other.set_active(False)
        elif value == self.tool:
            button.set_active(True)

    def _ink_toggled(self, button: Gtk.ToggleButton, value: str) -> None:
        if button.get_active():
            self.ink = value
            for name, other in self.ink_buttons.items():
                if name != value and other.get_active():
                    other.set_active(False)
        elif value == self.ink:
            button.set_active(True)


    def _choose_file(self) -> None:
        if self.fixture:
            Toast.show(self.host, "Fixture is read-only")
            return
        chooser = Gtk.FileDialog()
        chooser.set_title("Open a file")

        def chosen(dialog, result) -> None:
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            if file is not None and file.get_path():
                self.open_path(file.get_path())

        chooser.open(self, None, chosen)

    def _show_about(self) -> None:
        about = Adw.AboutDialog(application_name="Viewer", application_icon=ICON_NAME,
                                developer_name="Project Luma")
        about.present(self)


class ViewerApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.opening = ""

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        from luma_appkit import install_lumaui
        install_lumaui()
        _install_viewer_style()
        _install_icon_path()

    def do_open(self, files, count, hint) -> None:
        if files:
            path = files[0].get_path()
            if path:
                self.opening = path
        self.do_activate()

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = ViewerWindow(self, self.opening)
        elif self.opening:
            window.open_path(self.opening)
        self.opening = ""
        window.present()


def _install_viewer_style() -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    # The kit owns this sheet, so it is reloaded when the surface treatment
    # changes. A provider of its own was not, so the application kept the
    # palette it started with while the windows beside it followed.
    add_style_sheet(
        os.environ.get("LUMA_VIEWER_STYLE_PATH", "/usr/share/luma-viewer/viewer.css")
    )


def _install_icon_path() -> None:
    """The mark-up glyphs Viewer ships, because no installed theme has them."""
    display = Gdk.Display.get_default()
    if display is None:
        return
    path = os.environ.get("LUMA_VIEWER_ICON_PATH", "/usr/share/luma-viewer/icons")
    if os.path.isdir(path):
        Gtk.IconTheme.get_for_display(display).add_search_path(path)


def main() -> int:
    return ViewerApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
