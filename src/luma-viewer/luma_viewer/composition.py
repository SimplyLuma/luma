# SPDX-License-Identifier: Apache-2.0
"""Viewer composition: LumaUI chrome around app-owned document content."""
from pathlib import Path
import os
import math
import threading

import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from luma_appkit import (
    ActionCenter, AdjustmentPanel, ValueSlider, ZoomControl, SplitAction, ImageViewport, ToolPalette, FileSummaryRow, PanelRow, PanelHeading, DetailsFacts,
    ShareSheet, ShareSubject, ShareTarget, Person, SidebarRow, RowLead, BarChip, BarAction, Card, ColorSwatch, Command, CommandGroup,
    CommandRegistry, ConnectedButtonGroup, CornerPill, DetailsPane,
    EmptyState, IconButton, Island, ListEmptyState, Menu, MenuSection, ModeSwitch,
    NavigationRow, NavigationSidebar, OpenInMenu, SEPARATOR, SidebarFoot, SidebarToggle,
    TextField, Toast, ToastHost, apply_type, type_metrics, icons, lumaui_tokens,
)
from luma_appkit.action_bubble import FloatingMenu, MenuItem, float_at, rect_in
from luma_appkit.action_center import make_control
from luma_appkit.bar_items import zoom_text
from luma_appkit.menus import bar_menu
from luma_appkit.structure_adapt import WidthWatch
from .annotations import History, Mark
from .fixture import FixtureRecents
from .recents import Recents, format_size

INKS=('#ff5a4f','#ffb020','#3ccf7c','#3d8bff','#111111')
TOOLS=(('select','mouse-pointer-2','Select and move'),('ink','pencil','Pen'),
       ('highlight','highlighter','Highlighter'),('box','square','Rectangle'),
       ('ellipse','circle','Ellipse'),('arrow','arrow-up-right','Arrow'),
       ('text','type','Text'),('step','list-ordered','Numbered steps'),
       ('blur','droplets','Blur'),('redact','eye-off','Redact (removed for good when you save)'),
       ('sign','signature','Signature'))
ADJUSTMENTS=(('exp','Exposure','Light',100),('con','Contrast','Light',100),
             ('hi','Highlights','Light',100),('sh','Shadows','Light',100),
             ('sat','Saturation','Color',100),('vib','Vibrance','Color',100),
             ('warm','Warmth','Color',100),('tint','Tint','Color',100),
             ('str','Straighten','',15))


class _ViewerAdjustmentPanel(AdjustmentPanel):
    def __init__(self, *args, avoid, bar, **kwargs):
        self._avoid = avoid
        self._bar = bar
        super().__init__(*args, **kwargs)

    def _position(self, overlay, child, allocation):
        placed = super()._position(overlay, child, allocation)
        if placed:
            if overlay.get_width() > lumaui_tokens.PHONE_MAX_WIDTH:
                corner = self._avoid.get_child()
                valid, bounds = corner.compute_bounds(overlay) if corner is not None else (False, None)
                floor = int(bounds.origin.y + bounds.size.height) + 8 if valid else self._avoid.get_height() + 8
                shift = max(0, floor - allocation.y)
                allocation.y += shift
                allocation.height = max(0, allocation.height - shift)
        return placed


def label(text, role='body', **kwargs):
    return apply_type(Gtk.Label(label=text, **kwargs),role)


class ViewerUI:
    def __init__(self, application, opening=''):
        fixture=os.environ.get('LUMA_VIEWER_FIXTURE')
        self.fixture=bool(fixture)
        self.recents=FixtureRecents(fixture) if fixture else Recents()
        self.facts=None;self.history=History();self.sessions={}
        self.source_bytes=None;self.zoom=1.;self.zoom_fitted=True;self.pan=(0,0);self.stroke_width=6.
        self.pending=self.selected=self.drag_origin=self.drag_viewport=None
        self.loaded=self.saving=self.guard_open=False;self.last_saved=''
        self.mode=os.environ.get('LUMA_VIEWER_MODE','view') if self.fixture else 'view';self.tool='ink';self.ink=INKS[0]
        self.rotation=self.page=0;self.page_count=1
        self.document=self.pixbuf=self.texture=None;self.form_values={};self.saved_changes={}
        self.drag_from=self.drag_to=None;self.file_rows={};self.load_token=0;self.thumbnail_cache={};self.thumbnail_pending=set()
        self.adjustments={};self.crop=None;self.cropping=False;self.flip=False;self.hold=False
        self.live=False;self.text_regions=[];self.share_people=[];self.comparison_selection=set();self.compare_pixbufs={};self.compare=None;self.compare_mode='swipe';self.compare_fraction=.5
        self._building=False
        self.phone=False
        self._process_lock=threading.Lock()
        self._process_pending=None
        self._process_running=False
        self._process_full_source=0
        super().__init__(application=application,app_id=application.get_application_id(),
            title='Viewer',icon_name='org.projectluma.Viewer',commands=self._application_commands(),
            default_width=1180,default_height=740,minimum_width=360,minimum_height=420)
        self.set_name('vw-window')
        self.set_phone_bleed(True)
        mode_keys=Gtk.EventControllerKey()
        mode_keys.set_name('vw-mode-dismiss')
        mode_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        mode_keys.connect('key-pressed',self._mode_key)
        self.add_controller(mode_keys)
        file_keys=Gtk.EventControllerKey()
        file_keys.set_name('vw-file-navigation')
        file_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        file_keys.connect('key-pressed',self._file_key)
        self.add_controller(file_keys)
        self.layout=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar=self._build_sidebar()
        self.layout.append(self.sidebar)
        self.info=self._build_info()
        # A closed DetailsPane hides its sheet but leaves its slot visible. In a
        # Gtk.Box that still leaves an extra inter-pane gap at the right edge.
        self.info.set_visible(False)
        self.info.connect('notify::shown',lambda pane,_prop:pane.set_visible(pane.shown))
        self.main=self._build_main()
        self.layout.append(self.main);self.layout.append(self.info)
        self.set_body(self.layout)
        self.sidebar_toggle = SidebarToggle(self.sidebar, drawer_below=721)
        self.sidebar_toggle.set_name("vw-sidebar-toggle")
        self.sidebar_toggle.set_control_visible(False)
        self.title_bar.pack_start(self.sidebar_toggle)
        self._width_watch=WidthWatch(self,self._presentation_width,threshold=559)
        self.connect('close-request',self._close_requested)
        self._refresh_files();self._refresh_info();self._refresh_actions()
        if self.fixture:
            self._load_fixture_people()
            uid=os.environ.get('LUMA_VIEWER_SELECTED',self.recents.selected)
            opening=str(self.recents.samples[uid]['path'])
        elif not opening and self.recents.entries:opening=self.recents.entries[0].path
        if opening:self.open_path(opening)
        else:self.stage.append(EmptyState('Nothing open','Open a file, or drop one here.',
            icons.icon_name('folder-open'),primary=('Open…',self._choose_file)))

    def _presentation_width(self,width):
        phone=0 < width < 560
        if phone==self.phone:return
        self.phone=phone
        self._refresh_surround()
        preview = getattr(self, "_document_preview", None)
        if preview is not None:
            preview.set_margin_top(76 + (int(lumaui_tokens.PHONE_FRAME["status"]) if phone else 0))
        if self.document and hasattr(self,"pdf_pages"):
            self.pdf_pages.set_margin_top(76 + (int(lumaui_tokens.PHONE_FRAME["status"]) if phone else 0))
        self.action_center.fold_panel()
        self._refresh_actions()
        self._resize_canvas()

    def _refresh_surround(self):
        kind = self.facts.kind if self.facts is not None else None
        self.surround.set_visible(self.phone and kind not in ('EPUB book', '3D model'))

    def _phone_files(self):
        panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for _heading,entries in self.recents.grouped():
            for entry in entries:
                kind='missing' if not entry.exists else 'document' if 'PDF' in entry.kind else 'image'
                panel.append(PanelRow(entry.name,subtitle='Moved or deleted' if not entry.exists else entry.size_text,
                    lead=RowLead.thumbnail(self.thumbnail_cache.get(entry.path),kind=kind),
                    current=entry.path==str(self.facts.path),sensitive=entry.exists,
                    on_activate=lambda p=entry.path:self.open_path(p)))
        panel.append(PanelRow('Open a file',icon='folder-open',on_activate=self._choose_file))
        self.action_center.grow('files',panel,anchor=self.file_head,
            on_fold=lambda:self.file_head.set_expanded(False))
        self.file_head.set_expanded(self.action_center.grown=='files')

    def _phone_information(self):
        f=self.facts
        panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.append(PanelHeading(f.name))
        summary=f.summary
        if self.pixbuf:
            w,h=self.pixbuf.get_width(),self.pixbuf.get_height();summary=f'{w*h/1e6:.1f} MP · {w} × {h}'
        elif self.document:summary=f'PDF · {self.page_count} pages'
        panel.append(label(summary,'caption',xalign=0))
        rows=[('Kind',f.extra.get('kind',f.kind)),('Size',f.extra.get('size_text',format_size(f.size)))]
        if self.document:rows.append(('Pages',f'{self.page_count} · US Letter' if f.extra.get('pages') else str(self.page_count)))
        elif f.dimensions:rows.append(('Dimensions',f.dimensions))
        if f.extra.get('modified'):rows.append(('Modified',f.extra['modified']))
        elif f.created:rows.append(('Modified',f.created.strftime('%-d %B %Y')))
        rows.append(('Where',f.where))
        panel.append(DetailsFacts([(name,str(value)) for name,value in rows if value]))
        if f.extra.get('camera'):
            panel.append(PanelHeading('Camera'))
            panel.append(DetailsFacts(f.extra['camera']))
        if f.extra.get('uid')=='receipt':
            panel.append(PanelHeading('Text in this image'))
            panel.append(label('17 lines found, including a date, a phone number and a total. Turn on Text to select them.','caption',wrap=True,xalign=0))
        self.action_center.grow('information',panel)

    def _show_phone_bar(self):
        f=self.facts
        self.file_head=FileSummaryRow(f.name,f.extra.get('size_text',format_size(f.size)),
            picture=self.thumbnail_cache.get(str(f.path)),kind='document' if self.document else 'image',on_open=self._phone_files)
        head=Gtk.Box(spacing=6)
        head.append(self.file_head)
        info=make_control(BarAction('info',tooltip='Information',on_activate=self._phone_information))
        head.append(info)
        items=[BarAction('signature' if self.document else 'pen-line',tooltip='Sign and mark' if self.document else 'Mark up',
                         on_activate=lambda:self._set_mode('markup'),fill=True)]
        if not self.document:items.append(BarAction('sliders-horizontal',tooltip='Adjust',on_activate=lambda:self._set_mode('adjust'),fill=True))
        if self.facts.extra.get('uid')=='receipt' or self.text_regions:
            items.append(BarAction('scan-text',tooltip='Text',on_activate=self._live_text,active=self.live,fill=True))
        items.extend([BarAction('square-arrow-out-up-right',tooltip='Open in',on_activate=lambda:self._open_in(self.file_head),fill=True),
                      BarAction('share-2',tooltip='Share',on_activate=lambda:self._share(self.file_head),fill=True)])
        self.action_center.show_bar(items,head=head,fill=True)

    def _build_sidebar(self):
        sidebar=NavigationSidebar(variant='files',width='narrow');sidebar.set_name('vw-sidebar')
        self.files_box=sidebar.list;self.files_box.connect('row-activated',self._activate_recent)
        self.foot=SidebarFoot(search='Search',on_search=lambda _text:self._refresh_files(),
            add=('Open a file','folder-open',self._choose_file))
        self.foot.set_name('vw-foot');self.search=self.foot.entry
        sidebar.append_footer(self.foot)
        self.files_count=Gtk.Label() # retained backend state, never displayed
        return sidebar

    def _toggle_search(self):
        self._show_recents(search=True)

    def _file_key(self,_controller,key,_code,_state):
        if self.saving or self.guard_open or not self.loaded or not self.facts:return False
        if _state & (Gdk.ModifierType.SHIFT_MASK|Gdk.ModifierType.CONTROL_MASK|
                     Gdk.ModifierType.ALT_MASK|Gdk.ModifierType.META_MASK|
                     Gdk.ModifierType.SUPER_MASK):return False
        direction=1 if key in (Gdk.KEY_Down,Gdk.KEY_KP_Down) or \
            (key in (Gdk.KEY_Right,Gdk.KEY_KP_Right) and self.mode=='view') else \
            -1 if key in (Gdk.KEY_Up,Gdk.KEY_KP_Up) or \
            (key in (Gdk.KEY_Left,Gdk.KEY_KP_Left) and self.mode=='view') else 0
        if not direction:return False
        focus=self.get_focus()
        while focus:
            if isinstance(focus,(Gtk.Editable,Gtk.TextView,Gtk.Range)):return False
            if focus is getattr(self, '_document_preview', None):return False
            if focus is getattr(self,'marks_area',None) and self.mode=='markup' and self.selected is not None:
                return False
            focus=focus.get_parent()
        current=str(self.facts.path)
        order=getattr(self,'_navigation_order',None)
        if not order or current not in order:
            order=[str(entry.path) for _heading,entries in self.recents.grouped()
                for entry in entries if entry.exists]
            self._navigation_order=order
        if current not in order:return False
        index=max(0,min(len(order)-1,order.index(current)+direction))
        target=order[index]
        if target!=current:self.open_path(target,navigation=True)
        return True

    def _show_recents(self,search=False):
        if not self.sidebar_toggle.shown:
            self.sidebar_toggle.toggle(initial_focus=self.search if search else None)
        elif search:self.search.grab_focus()

    def _refresh_files(self):
        if not hasattr(self,'files_box'):return
        self.sidebar.clear();self.file_rows.clear()
        self.files_box.set_selection_mode(Gtk.SelectionMode.MULTIPLE if self.comparison_selection else Gtk.SelectionMode.SINGLE)
        selected=self.comparison_selection or ({str(self.facts.path)} if self.facts else set())
        groups=self.recents.grouped(self.search.get_text())
        for heading,entries in groups:
            self.sidebar.append_section(heading)
            for entry in entries:
                row=self._file_row(entry);self.sidebar.append_row(row)
                if entry.path in selected:self.files_box.select_row(row)
        if not groups:
            self.files_box.append(Gtk.ListBoxRow(selectable=False,activatable=False,
                child=ListEmptyState('No matching files' if self.search.get_text() else 'No recent files')))
        self.files_count.set_text('')

    def _file_row(self,entry):
        cached=self.recents.cached_thumbnail(entry.path)
        kind='missing' if not entry.exists else 'document' if 'PDF' in entry.kind else 'image'
        image=RowLead.thumbnail(self.thumbnail_cache.get(entry.path),kind=kind)
        has_thumbnail=kind=='image' and (cached or (self.fixture and self.recents.sample(entry.path).get('generated')))
        if has_thumbnail and entry.path not in self.thumbnail_cache and entry.path not in self.thumbnail_pending:
            self.thumbnail_pending.add(entry.path);self._load_thumbnail(entry.path,cached)
        count=len(self.sessions.get(entry.path,(History(),{}))[0].marks)
        if self.facts and str(self.facts.path)==entry.path:count=len(self.marks)
        sub='Moved or deleted' if not entry.exists else entry.size_text
        if count:sub+=f' · {count} mark'+('s' if count!=1 else '')
        row=SidebarRow(entry.name,lead=image,subtitle=sub,missing=not entry.exists)
        row.set_name('vw-file-'+getattr(entry,'uid',str(len(self.file_rows))))
        row.viewer_path=entry.path
        row.set_tooltip_text(entry.name)
        gesture=Gtk.GestureClick(button=3)
        gesture.connect('pressed',lambda _g,_n,x,y:self._row_menu(entry,row,x,y));row.add_controller(gesture)
        compare_click=Gtk.GestureClick(button=1)
        compare_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        def compare_pressed(gesture,_n,_x,_y):
            if gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK and self.facts and self.pixbuf is not None and entry.exists and (entry.kind=='Image' or 'image' in entry.kind.lower()):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                selection=set(self.comparison_selection or {str(self.facts.path)})
                if entry.path in selection:selection.remove(entry.path)
                else:selection.add(entry.path)
                self.comparison_selection=selection
                if len(selection)==2:self._begin_compare(sorted(selection))
                else:
                    self.compare=None;self.compare_pixbufs={}
                    self._processing_changed();self._refresh_files()
        compare_click.connect('pressed',compare_pressed);row.add_controller(compare_click)
        self.file_rows[entry.path]=row
        return row

    def _build_info(self):
        pane=DetailsPane('Information');pane.set_name('vw-information')
        self.info_body=pane.body
        return pane

    def _build_main(self):
        island=Island();island.set_name('vw-island');island.set_hexpand(True)
        overlay=Gtk.Overlay(hexpand=True,vexpand=True)
        self.stage=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True,vexpand=True)
        self.stage.set_name('vw-stage')
        self.stage_scroll=Gtk.ScrolledWindow(hexpand=True,vexpand=True,child=self.stage,
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.stage_scroll.connect('notify::width',lambda *_:self._resize_canvas())
        self.stage_scroll.connect('notify::height',lambda *_:self._resize_canvas())
        self.surround=ImageViewport()
        self.surround.set_visible(self.phone)
        overlay.set_child(self.surround)
        overlay.add_overlay(self.stage_scroll)
        self.document_overlay=overlay
        self.compare_labels=Gtk.Fixed(hexpand=True,vexpand=True,can_target=False)
        overlay.add_overlay(self.compare_labels)
        self.live_layer=Gtk.Fixed(hexpand=True,vexpand=True,can_target=False)
        overlay.add_overlay(self.live_layer)
        self.corner_slot=Adw.Bin(halign=Gtk.Align.END,valign=Gtk.Align.START)
        overlay.add_overlay(self.corner_slot)
        self.tools=self._build_tools().attach(overlay)
        self.adjust_panel=self._build_adjustments().attach(overlay)
        island.append(overlay)
        self.host=ToastHost(island)
        self.action_center=ActionCenter().attach(self.host,safe_area=False);self.action_center.set_name('vw-action-center')
        self.document_bar=self.corner_slot
        # Backend information is held here without adding a second header/status bar.
        self.title_label=Gtk.Label();self.status_path=Gtk.Label();self.status_detail=Gtk.Label()
        self.prev_button=Gtk.Button();self.next_button=Gtk.Button()
        self.save_button=Gtk.Button();self.rotate_button=Gtk.Button()
        self.zoom_label=Gtk.Label();self.clear_button=Gtk.Button()
        return self.host

    def _build_tools(self):
        items=[BarAction(icon,tooltip=title,on_activate=lambda k=key:self._select_tool(k)) for key,icon,title in TOOLS]
        items.append(BarAction('chevron-down',tooltip='Color and width',
            on_activate=lambda:self._style_menu(self.tools_box.get_last_child())))
        palette=ToolPalette(items)
        palette.set_name('vw-palette')
        self.tools_box=palette.row
        self.tool_buttons={key:button for (key,_icon,_title),button in zip(TOOLS,palette.buttons)}
        for key,button in self.tool_buttons.items():button.set_name('vw-tool-'+key)
        palette.buttons[-1].set_name('vw-style')
        return palette

    def _select_tool(self,key):
        self._cancel_drag();self.tool=key;self.selected=None;self._refresh_actions()

    def _style_menu(self,anchor):
        # Shared menu framing preserves the desktop card and phone bar presentation.
        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        colours=ConnectedButtonGroup();self.ink_buttons={}
        for colour,title in zip(INKS,('Red','Gold','Green','Blue','Black')):
            swatch=ColorSwatch(colour,title,context=self.context);swatch.set_active(colour==self.ink)
            swatch.connect('toggled',self._ink_toggled,colour);colours.append(swatch);self.ink_buttons[colour]=swatch
        content.append(colours)
        if not self.document:
            width=ConnectedButtonGroup()
            for value,title in ((3,'Thin'),(6,'Medium'),(12,'Thick')):
                b=Gtk.Button(label=title);apply_type(b,'caption')
                b.connect('clicked',lambda _b,v=value:setattr(self,'stroke_width',v));width.append(b)
            content.append(width)
        self.style_menu=bar_menu(anchor,[MenuSection(content)],label="Color and width")

    def _build_adjustments(self):
        sections=[];self.adjust_controls={}
        for key,title,section,limit in ADJUSTMENTS:
            if not sections or sections[-1][0]!=(section or None):sections.append((section or None,[]))
            slider=ValueSlider(title,minimum=-limit,maximum=limit,layout='stacked',
                on_change=lambda value,k=key:self._adjust_changed(k,value))
            slider.set_name('vw-adjust-'+key)
            sections[-1][1].append(slider);self.adjust_controls[key]=slider
        panel=_ViewerAdjustmentPanel(sections,on_reset=self._reset_adjustments,phone_layout="floating",
                                     avoid=self.corner_slot,bar=lambda:getattr(self,'action_center',None))
        panel.set_name('vw-adjustments');self.reset_button=panel.reset_button
        return panel

    def _adjust_changed(self,key,value):
        if self._building:return
        self.adjustments[key]=round(value)
        self._processing_changed(preview=True)

    def _reset_adjustments(self):
        self.adjustments={};self._sync_adjustments();self._processing_changed()

    def _sync_adjustments(self):
        self._building=True
        for key,slider in self.adjust_controls.items():slider.set_value(self.adjustments.get(key,0))
        self.reset_button.set_sensitive(any(self.adjustments.values()));self._building=False

    def _processing_changed(self, *, preview=False):
        if self._process_full_source:
            GLib.source_remove(self._process_full_source)
            self._process_full_source=0
        self._update_processed_image(preview=preview)
        self._sync_compare_labels()
        self._refresh_info();self._refresh_actions()
        if hasattr(self,'marks_area'):self.marks_area.queue_draw()

    def _finish_adjustment_preview(self):
        self._process_full_source=0
        self._update_processed_image(preview=False)
        return False

    def _refresh_actions(self):
        if not hasattr(self,'action_center'):return
        if self.facts is None:
            self.corner_slot.set_child(None)
            self.adjust_panel.set_shown(False);self.action_center.hide_bar();return
        modes=[('view','View','eye'),('markup','Sign and mark' if self.document else 'Mark up',
            'signature' if self.document else 'pen-line')]
        if self.facts.kind in ('Audio', 'EPUB book', '3D model'):
            modes = [('view', 'View', 'eye')]
        if not self.document and self.facts.kind=='Image':modes.append(('adjust','Adjust','sliders-horizontal'))
        if self.mode not in {m[0] for m in modes}:self.mode='view'
        switch=ModeSwitch(modes,current=self.mode,on_change=self._set_mode) if len(modes) > 1 else None
        self.mode_buttons=switch.buttons if switch is not None else {}
        for k,b in self.mode_buttons.items():b.set_name('vw-mode-'+k)
        corner=CornerPill(modes=switch,open_in=self._open_in,share=self._share,info=self.info)
        corner.set_name('vw-corner')
        for k,b in corner.controls.items():b.set_name('vw-'+k.replace('_','-'))
        self.corner_slot.set_child(corner)
        if self.facts.kind in ('Audio', 'EPUB book', '3D model'):
            self.tools.set_shown(False)
            self.adjust_panel.set_shown(False)
            self.action_center.hide_bar()
            self.corner_slot.set_visible(True)
            return
        pdf=self.document is not None
        for key,b in self.tool_buttons.items():
            b.set_visible(not pdf or key in ('select','highlight','text','sign','redact','ink'))
            b.set_sensitive(self.loaded and not self.saving)
            if key==self.tool:
                b.add_css_class('on')
            else:
                b.remove_css_class('on')
            b.update_state([Gtk.AccessibleState.PRESSED], [int(Gtk.AccessibleTristate.TRUE if key==self.tool else Gtk.AccessibleTristate.FALSE)])
        order=('select','ink','highlight','text','redact','sign') if pdf else tuple(key for key,_icon,_title in TOOLS)
        previous=None
        for key in order:
            self.tools_box.reorder_child_after(self.tool_buttons[key],previous);previous=self.tool_buttons[key]
        self.tools.set_shown(self.mode=='markup' and not self.compare)
        self.tools.set_current(self.tool_buttons.get(self.tool))
        self.adjust_panel.set_shown(self.mode=='adjust' and not self.cropping and not self.compare)
        self._sync_adjustments()
        if self.compare:
            names=' · '.join(self.recents.find(path).name.rsplit('.',1)[0] for path in self.compare)
            items=[BarChip('Comparing',icon='columns-2',meta=names),ModeSwitch(
                [('swipe','Swipe'),('side','Side by side'),('diff','Difference')],
                current=self.compare_mode,on_change=self._compare_mode,labels_only=True)]
            items += [BarAction('arrow-left-right',tooltip='Swap',on_activate=self._swap_compare),
                      BarAction('x',tooltip='Done',on_activate=self._end_compare)]
            self.corner_slot.set_visible(False)
        elif self.mode=='markup':
            items=[]
            if self.selected is not None:items.append(BarAction('trash-2',tooltip='Delete mark',on_activate=self._delete_mark))
            items += [BarAction('undo',tooltip='Undo',on_activate=self._undo_mark,sensitive=self.history.index>0),
                      BarAction('redo',tooltip='Redo',on_activate=self._redo_mark,sensitive=self.history.index<len(self.history.states)-1),
                      SplitAction('Save',self._save_copy,self._save_menu,sensitive=bool(self.marks or self.form_values))]
        elif self.mode=='adjust':
            if self.cropping:
                items=[ModeSwitch([('free','Free'),('orig','Original'),('square','Square'),('16:9','16:9'),('4:5','4:5')],
                    current=getattr(self,'crop_aspect','free'),on_change=self._crop_aspect,labels_only=True,ellipsize=True),
                    BarAction('','Cancel',on_activate=self._cancel_crop),BarAction('','Crop',on_activate=self._finish_crop,primary=True)]
            else:
                items=[BarAction('crop','Crop',on_activate=self._start_crop),
                    BarAction('rotate-ccw',tooltip='Rotate',on_activate=self._rotate),
                    BarAction('arrow-left-right',tooltip='Flip',on_activate=self._flip),SEPARATOR,
                    BarAction('wand-sparkles','Auto',on_activate=self._auto),
                    BarAction('eye',tooltip='Hold to see the original',on_activate=self._original),
                    SplitAction('Save',self._save_copy,self._save_menu,primary=True)]
        elif pdf:items=[BarChip(f'{self.page_count} pages')]
        else:
            actual=self._viewport().scale if self.pixbuf is not None else 1.
            fit=actual/self.zoom
            items=[ZoomControl(actual,lambda value:self._set_zoom(value/fit),on_fit=self._fit_window)]
            if self.facts.extra.get('uid')=='receipt' or self.text_regions:items += [SEPARATOR,BarAction('scan-text','Text',on_activate=self._live_text,active=self.live)]
        self.corner_slot.set_visible(not self.compare and not self.phone)
        if self.phone and self.mode=='view' and not self.compare:
            self._show_phone_bar()
            return
        if self.phone and self.cropping:
            items=[item for item in items if not isinstance(item,BarAction) or item.label!='Cancel']
        if self.phone and not self.compare:
            items.insert(0,BarAction('x',tooltip='Close',on_activate=lambda:self._set_mode('view')))
        mode_head=None
        if self.phone and self.cropping:
            mode_head=next(item for item in items if isinstance(item,ModeSwitch))
            items.remove(mode_head)
            mode_head=make_control(mode_head)
            mode_head.set_hexpand(True)
            for item in items:item.fill=True
        if self.phone and self.compare:
            mode_head=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6)
            mode_head.append(make_control(items[0]))
            items[1].set_hexpand(True)
            mode_head.append(make_control(items[1]))
            items=items[2:]
            for item in items:item.fill=True
        self.action_center.show_bar(items,fill=self.phone,head=mode_head)
        names={'Zoom out':'vw-zoom-out','Zoom in':'vw-zoom-in','Fit to window':'vw-zoom',
            'More ways to save':'vw-save-menu','Undo':'vw-undo','Redo':'vw-redo'}
        child=self.action_center.bar_row.get_first_child()
        while child:
            item=getattr(child,'bar_item',None)
            if isinstance(item,ZoomControl):
                item.out_key.set_name('vw-zoom-out');item.in_key.set_name('vw-zoom-in');item.fit_key.set_name('vw-zoom')
            elif isinstance(item,SplitAction):
                item.main.set_name('vw-action-save');item.more.set_name('vw-save-menu')
            elif isinstance(item,BarAction):
                child.set_name(names.get(item.tooltip or item.label,'vw-action-'+(item.label or item.tooltip or '').lower().replace(' ','-')))
                if item.tooltip=='Hold to see the original':
                    held=Gtk.GestureClick(button=1)
                    held.connect('pressed',lambda *_:self._hold_original(True))
                    held.connect('released',lambda *_:self._hold_original(False))
                    held.connect('cancel',lambda *_:self._hold_original(False));child.add_controller(held)
            child=child.get_next_sibling()

    def _set_mode(self,mode):
        self._cancel_drag();self.mode=mode;self.selected=None;self.cropping=False;self.live=False
        if self.document is not None and mode=="markup":self.tool="sign"
        for text in getattr(self,'pdf_text_labels',()):text.set_can_target(mode=='view')
        self._refresh_actions();self._resize_canvas()

    def _fit_window(self):
        fit=self._viewport().scale/self.zoom if self.pixbuf is not None else 1.
        if self.zoom_fitted:self._set_zoom(1/max(fit,.0001))
        else:self._reset_fit()

    def _reset_fit(self):
        self.zoom=1.;self.zoom_fitted=True;self.pan=(0,0)
        self._resize_canvas();self._refresh_actions()

    def _zoom_text(self):
        if self.pixbuf is not None and hasattr(self,'marks_area'):
            return zoom_text(self._viewport().scale)
        return '100%'

    def _refresh_info(self):
        if not hasattr(self,'info'):return
        self.info.clear();f=self.facts
        if f is None:self.info.show(subject=None);return
        sample=f.extra
        sub=f.summary
        if self.pixbuf:
            w,h=self.pixbuf.get_width(),self.pixbuf.get_height();sub=f'{w*h/1e6:.1f} MP · {w} × {h}'
        elif self.document:sub=f'PDF · {self.page_count} pages'
        self.info.add_hero(f.name,sub)
        facts=[('Kind',sample.get('kind',f.kind)),('Size',sample.get('size_text',format_size(f.size)))]
        if self.document:facts.append(('Pages',f'{self.page_count} · US Letter' if sample.get('pages') else str(self.page_count)))
        elif f.dimensions:facts.append(('Dimensions',f.dimensions))
        if sample.get('modified'):facts.append(('Modified',sample['modified']))
        elif f.created:facts.append(('Modified',f.created.strftime('%-d %B %Y')))
        facts.append(('Where',f.where));self.info.add_facts(facts)
        if sample.get('camera'):self.info.add_section('Camera');self.info.add_facts(sample['camera'])
        if sample.get('uid')=='receipt':
            self.info.add_section('Text in this image')
            self.info.add(label('17 lines found, including a date, a phone number and a total. Turn on Text to select them.','caption',wrap=True,xalign=0))
        self.info.show(subject=f)

    def _toggle_info(self):
        if self.phone:self._phone_information()
        else:self.info.show(open=not self.info.shown,subject=self.facts)
    def _open_in(self,anchor):
        if not self.facts:return
        edited=bool(self.marks or any(self.adjustments.values()) or self.crop or self.form_values or self.rotation or self.flip)
        guarded=self.fixture or edited
        message='Fixture is read-only' if self.fixture else 'Save a copy first to open your changes in another app'
        OpenInMenu(str(self.facts.path),content_type='application/pdf' if self.document else None,
            on_open=(lambda _app:Toast.show(self.host,message)) if guarded else None,
            on_other=(lambda:Toast.show(self.host,message)) if guarded else None).popup(anchor)
    def _load_fixture_people(self):
        self._fixture_people_ready=False
        import threading
        def work():
            from gi.repository import GdkPixbuf
            portraits=[]
            for person in self.recents.people:
                try:image=GdkPixbuf.Pixbuf.new_from_file(str(person['path'])) if person['path'] else None
                except GLib.Error:image=None
                portraits.append((person,image))
            def ready():
                self.share_people=[Person(person['name'],username=person['username'],hue=person['hue'],
                    picture=Gdk.Texture.new_for_pixbuf(image) if image else None) for person,image in portraits]
                self._fixture_people_ready=True
                return False
            GLib.idle_add(ready)
        threading.Thread(target=work,daemon=True).start()

    def _share(self,anchor):
        if not self.facts:return
        subject=ShareSubject(self.facts.name,f'{self.facts.extra.get("kind",self.facts.kind)} · {self.facts.extra.get("size_text",format_size(self.facts.size))}',
            kind='file',picture=self.thumbnail_cache.get(str(self.facts.path)),
            image=self.facts.kind=='Image',
            mime_type=Gio.content_type_guess(self.facts.path.name, None)[0])
        from luma_appkit.bar_share import default_targets
        targets=default_targets(subject) if self.fixture else None
        people=self.share_people if self.fixture else []
        def choice(action,value):
            if self.fixture:return 'Fixture is read-only'
            if action=='copy':return self._copy_document()
            elif action=='save':self._save_copy()
            elif action=='print':self._print_document()
            elif action=='target':
                target=next((target for target in sheet.targets if target.key==value),None)
                if target is None:return 'This app is unavailable'
                if self.marks or any(self.adjustments.values()) or self.crop or self.form_values or self.rotation or self.flip:
                    return 'Save a copy first to share your changes'
                from luma_appkit.application_directory import launch
                from luma_appkit import ShareResult
                def accepted(ok,error):
                    if getattr(self,'_closed',False):return
                    Toast.show(self.layer_host,'Opened in '+target.label if ok else error or 'Could not share',
                               kind='sent' if ok else 'error')
                launch(target.app_id+'.desktop',[Gio.File.new_for_path(str(self.facts.path))],
                       context=self.get_display().get_app_launch_context(),callback=accepted)
                return ShareResult(False)
            else:return 'This destination is unavailable for this local file'
        sheet=ShareSheet.present(anchor,document=subject,people=people,targets=targets,on_choice=choice,
            choices=('send-copy',), show_link=False,
            copy_actions=('copy',) if self.facts.kind in ('Audio', 'EPUB book', '3D model') else ('copy', 'save', 'print'))

    def _print_document(self):
        if self.fixture or not self.loaded:return
        import cairo
        from .drawing import draw_marks
        from .export import compose_image
        document=self.document;marks=tuple(self.marks)
        image=None if document else compose_image(getattr(self,'processed',self.pixbuf),marks,
            crop=self.crop,rotation=self.rotation,flip=self.flip,straighten=self.adjustments.get('str',0))
        operation=Gtk.PrintOperation(n_pages=document.get_n_pages() if document else 1)
        def draw(_operation,context,number):
            if document:
                page=document.get_page(number);width,height=page.get_size()
                # Rasterize the whole page so a redacted word never enters the spool.
                sheet=cairo.ImageSurface(cairo.FORMAT_RGB24,round(width*2),round(height*2))
                painter=cairo.Context(sheet);painter.scale(2,2);painter.set_source_rgb(1,1,1);painter.paint()
                page.render(painter);draw_marks(painter,[mark for mark in marks if mark.page==number],page.render)
            else:sheet=image; width,height=sheet.get_width(),sheet.get_height()
            cr=context.get_cairo_context();scale=min(context.get_width()/sheet.get_width(),context.get_height()/sheet.get_height())
            cr.scale(scale,scale);cr.set_source_surface(sheet);cr.paint()
        operation.connect('draw-page',draw)
        try:operation.run(Gtk.PrintOperationAction.PRINT_DIALOG,self)
        except GLib.Error as error:Toast.show(self.host,str(error),kind='error')

    def _copy_document(self):
        if not self.loaded:return
        if self.facts.kind in ('Audio', 'EPUB book', '3D model'):
            from luma_appkit import ShareResult
            payload = (self.facts.path.resolve().as_uri() + '\r\n').encode()
            copied = self.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes('text/uri-list', GLib.Bytes.new(payload)))
            if not copied:
                Toast.show(self.host, 'Could not copy this file', kind='error')
                return ShareResult(False)
            Toast.show(self.host, 'File copied', kind='copied')
            return ShareResult(True, 'File copied')
        if self.document:
            token=self.load_token;original=self.source_bytes;marks=tuple(self.marks);fields=dict(self.form_values)
            import threading
            def work_pdf():
                from .export import compose_pdf
                try:data=compose_pdf(original,marks,fields=fields)
                except Exception as error:
                    message=str(error)
                    GLib.idle_add(lambda:Toast.show(self.host,message,kind='error') if token==self.load_token else None);return
                def ready_pdf():
                    if token==self.load_token:
                        provider=Gdk.ContentProvider.new_for_bytes('application/pdf',GLib.Bytes.new(data))
                        self.get_clipboard().set_content(provider);Toast.show(self.host,'Copied',kind='copied')
                    return False
                GLib.idle_add(ready_pdf)
            threading.Thread(target=work_pdf,daemon=True).start();return
        token=self.load_token;source=self.pixbuf;marks=self.marks
        values=dict(self.adjustments);geometry=dict(crop=self.crop,rotation=self.rotation,flip=self.flip,straighten=values.get('str',0))
        import threading
        def work():
            from .processing import adjust_pixels
            from .export import compose_image
            from gi.repository import GdkPixbuf
            pixels=adjust_pixels(source.get_pixels(),source.get_width(),source.get_height(),source.get_rowstride(),source.get_n_channels(),values)
            image=GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels),source.get_colorspace(),source.get_has_alpha(),8,source.get_width(),source.get_height(),source.get_rowstride())
            import io
            output=io.BytesIO();compose_image(image,marks,**geometry).write_to_png(output)
            data=output.getvalue()
            def ready():
                if token==self.load_token:
                    texture=Gdk.Texture.new_from_bytes(GLib.Bytes.new(data));self.get_clipboard().set(texture);Toast.show(self.host,'Copied',kind='copied')
                return False
            GLib.idle_add(ready)
        threading.Thread(target=work,daemon=True).start()
    def _save_menu(self,anchor=None):
        if anchor is None:
            child=self.action_center.bar_row.get_last_child()
            item=getattr(child,"bar_item",None)
            anchor=item.more if isinstance(item,SplitAction) else child
        FloatingMenu([
            MenuItem('Save as…',icon='copy-plus',on_activate=self._save_copy),
            MenuItem('Share…',icon='share-2',on_activate=lambda:self._share(anchor)),
            MenuItem('Copy to clipboard',icon='clipboard',on_activate=self._copy_document),
        ],label='More ways to save').popup(anchor)
    def _page_menu(self):
        anchor=self.action_center.bar_row.get_first_child()
        registry=CommandRegistry((CommandGroup('',tuple(Command(f'page.{i}',str(i+1),lambda n=i:self._go_page(n)) for i in range(self.page_count))),))
        menu=Menu(registry);menu.set_parent(anchor);menu.connect('closed',lambda p:GLib.idle_add(p.unparent));menu.popup()
    def _go_page(self,page):self._step_page(page-self.page)
    def _live_text(self):
        self.live=not self.live;self._refresh_actions();self._sync_live_text()
        if hasattr(self,'marks_area'):self.marks_area.queue_draw()
        if self.live:Toast.show(self.host,'Select text in the image, or tap a highlighted item')
    def _flip(self):self.flip=not self.flip;self._processing_changed()
    def _auto(self):self.adjustments=dict(exp=12,con=18,sat=14,warm=6,str=0);self._sync_adjustments();self._processing_changed()
    def _original(self):
        # GtkButton emits clicked on release, before or after our gesture's
        # released signal. A completed pointer hold must not start a second
        # timed preview. Keep the brief preview for standalone activation.
        if getattr(self,'_original_press_seen',False):
            self._original_press_seen=False;return
        self.hold=True;self._resize_canvas()
        def finish():
            self._original_timeout=0;self.hold=False;self._resize_canvas();return False
        previous=getattr(self,'_original_timeout',0)
        if previous:GLib.source_remove(previous)
        self._original_timeout=GLib.timeout_add(180,finish)
    def _hold_original(self,active):
        serial=getattr(self,'_original_gesture_serial',0)+1
        self._original_gesture_serial=serial
        if active:
            previous=getattr(self,'_original_timeout',0)
            if previous:GLib.source_remove(previous);self._original_timeout=0
            self._original_press_seen=True
        else:
            def clear():
                if self._original_gesture_serial==serial:self._original_press_seen=False
                return False
            GLib.idle_add(clear)
        self.hold=active;self._resize_canvas()
    def _start_crop(self):
        # TODO(kit-request viewer-03-adjust-controls.md): attach the shared CropOverlay.
        w,h=self._page_size();self.crop_box=(w*.08,h*.08,w*.84,h*.84);self.crop_aspect='free';self.cropping=True;self._processing_changed()
    def _crop_aspect(self,aspect):
        self.crop_aspect=aspect
        if aspect=='free':self._refresh_actions();return
        w,h=self._page_size();ratio={'orig':w/h,'square':1,'16:9':16/9,'4:5':4/5}[aspect]
        cw=min(w*.9,h*.9*ratio);ch=cw/ratio
        self.crop_box=((w-cw)/2,(h-ch)/2,cw,ch);self._processing_changed()
    def _cancel_crop(self):self.cropping=False;self._processing_changed()
    def _mode_key(self,_controller,key,_code,_state):
        if self.saving or key!=Gdk.KEY_Escape:return False
        if self.cropping:self._cancel_crop();return True
        if self.compare:self._end_compare();return True
        return False
    def _finish_crop(self):
        self.crop=self.crop_box;self.cropping=False;self.zoom=1.;self.zoom_fitted=True;self.pan=(0,0);self._processing_changed()
    def _compare_mode(self,mode):self.compare_mode=mode;self._processing_changed()
    def _swap_compare(self):self.compare.reverse();self._processing_changed()
    def _end_compare(self):
        self.compare=None;self.comparison_selection.clear();self._processing_changed();self._refresh_files()
    def _text_entry(self,point):
        # TODO(kit-request viewer-08-inline-text.md): unlabeled inline field variant.
        previous=getattr(self,'_finish_text',None)
        if previous:previous(True)
        from gi.repository import Graphene
        viewport=self._viewport();x,y=viewport.to_view(point)
        local=Graphene.Point();local.init(x,y)
        ok,at=self.marks_area.compute_point(self.layer_host,local)
        if not ok:return
        field=TextField('Text to place',placeholder='Type here')
        field.set_name('vw-text-entry');field.entry.set_name('vw-text-entry-input')
        field.set_halign(Gtk.Align.START);field.set_valign(Gtk.Align.START)
        field.set_margin_start(max(0,round(at.x-6)));field.set_margin_top(max(0,round(at.y-16)))
        field.set_size_request(200,-1);self.layer_host.add_overlay(field)
        token=self.load_token;page=self.page;ink=self.ink
        width=self.stroke_width*(.5 if self.document else max(1,self._page_size()[0]/900))
        closed=False
        def finish(keep):
            nonlocal closed
            if closed:return
            closed=True;self._finish_text=None
            value=field.text.strip()
            if field.get_parent() is self.layer_host:self.layer_host.remove_overlay(field)
            if keep and value and token==self.load_token:
                self.history.add(Mark('text',ink,point,point,text=value,width=width,page=page));self._edits_changed()
        self._finish_text=finish
        keys=Gtk.EventControllerKey()
        def key(_c,keyval,_code,_state):
            if keyval not in (Gdk.KEY_Return,Gdk.KEY_Escape):return False
            finish(keyval==Gdk.KEY_Return);return True
        keys.connect('key-pressed',key);field.add_controller(keys)
        focus=Gtk.EventControllerFocus();focus.connect('leave',lambda *_:finish(True));field.entry.add_controller(focus)
        field.entry.grab_focus()

    def _fixture_state(self):
        if not self.fixture or getattr(self,'_fixture_applied',False):return
        self._fixture_applied=True
        state=os.environ.get('LUMA_VIEWER_STATE','view')
        self._set_mode(os.environ.get('LUMA_VIEWER_MODE','view'))
        if self.document and self.mode=='markup':self.tool='sign'
        if state=='information':
            GLib.timeout_add(250,lambda:(self._toggle_info(),False)[1])
        elif state=='sidebar':
            GLib.timeout_add(250,lambda:((self._phone_files() if self.phone else self.sidebar_toggle.toggle()),False)[1])
        elif state=='pdf-filled':
            for key,value in {'tenant':'Nick Field','orig':'01/01/2026','date':'09/25/2026'}.items():
                self.form_entries[key].set_text(value)
        elif state=='crop':self._start_crop()
        elif state=='inline-editor':
            self._select_tool('text')
            def edit():
                if self.marks_area.get_width()<=0:return True
                w,h=self._page_size();self._text_entry((w*.2,h*.2))
                return False
            GLib.timeout_add(250,edit)
        elif state=='text' or state.startswith('text-'):
            self._live_text()
            if state.startswith('text-'):
                kind=state.removeprefix('text-')
                def detected():
                    anchor=self.detected_labels.get(kind)
                    if anchor:self._detector_menu(anchor,kind,anchor.get_label())
                    return False
                GLib.timeout_add(250,detected)
        elif state.startswith('compare'):
            self._begin_compare([str(self.recents.samples[k]['path']) for k in ('v6','v7')])
            self.compare_mode={'compare-side':'side','compare-diff':'diff'}.get(state,'swipe')
        elif state=='sidebar':pass  # The sidebar is always visible.
        elif state=='save-menu':
            w,h=self._page_size();self.tool='box';self.history.add(Mark('box',self.ink,(w*.2,h*.2),(w*.4,h*.4),width=6*max(1,w/900)))
            self._edits_changed();GLib.timeout_add(250,lambda:(self._save_menu(),False)[1])
        elif state in ('style','open-in','share'):
            def show():
                if state=='share' and not self._fixture_people_ready:return True
                if state=='style':self._style_menu(self.tools_box.get_last_child())
                else:
                    anchor=self.corner_slot.get_child().controls['open_in' if state=='open-in' else 'share']
                    (self._open_in if state=='open-in' else self._share)(anchor)
                return False
            GLib.timeout_add(250,show)
        self._refresh_actions()

    def _begin_compare(self,paths):
        self.compare=list(paths);self.comparison_selection=set(paths);self.compare_pixbufs={};self._refresh_files()
        token=self.load_token;comparison=self.compare;source_paths=tuple(paths)
        import threading
        from . import formats
        def work():
            try:images={path:formats.load_image(Path(path))[0] for path in source_paths}
            except Exception as error:
                def failed(reason=str(error)):
                    if self.load_token==token and self.compare is comparison:self._render_failed(token,reason)
                    return False
                GLib.idle_add(failed);return
            def ready():
                # Swapping mutates this session's order; starting again,
                # switching documents or Done retires the actual session.
                if self.load_token==token and self.compare is comparison:
                    self.compare_pixbufs=images;self._processing_changed()
                return False
            GLib.idle_add(ready)
        threading.Thread(target=work,daemon=True).start();self._refresh_actions()

    def _update_processed_image(self, *, preview=False):
        if self.pixbuf is None:return
        self._process_token=getattr(self,'_process_token',0)+1
        token=self._process_token;source=self.pixbuf;values=dict(self.adjustments)
        if not any(values.values()):
            self.processed=source
            self._processed_quality='source'
            self._processed_token=token
            if hasattr(self,'marks_area'):self.marks_area.queue_draw()
            return
        with self._process_lock:
            viewport=self._viewport()
            preview_limit=max(720,round(max(viewport.rotated_size)*viewport.scale)) if preview else None
            self._process_pending=(token,source,values,preview,preview_limit)
            if self._process_running:return
            self._process_running=True
        threading.Thread(target=self._process_worker,daemon=True).start()

    def _process_worker(self):
        from gi.repository import GdkPixbuf
        from .processing import adjust_pixels
        while True:
            with self._process_lock:
                job=self._process_pending
                self._process_pending=None
                if job is None:
                    self._process_running=False
                    return
            token,source,values,preview,preview_limit=job
            try:
                working=source
                if preview:
                    scale=min(1.,preview_limit/max(source.get_width(),source.get_height()))
                    if scale<1:
                        working=source.scale_simple(max(1,round(source.get_width()*scale)),
                                                    max(1,round(source.get_height()*scale)),
                                                    GdkPixbuf.InterpType.BILINEAR)
                pixels=adjust_pixels(working.get_pixels(),working.get_width(),working.get_height(),
                                     working.get_rowstride(),working.get_n_channels(),values,
                                     cancelled=lambda:token!=self._process_token or source is not self.pixbuf)
                if pixels is None:continue
                rendered=GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels),working.get_colorspace(),
                    working.get_has_alpha(),8,working.get_width(),working.get_height(),working.get_rowstride())
            except Exception as error:
                def failed(error=error):
                    if token==self._process_token:
                        Toast.show(self.host,'Image adjustment failed: '+str(error),kind='error')
                    return False
                GLib.idle_add(failed)
                continue
            def ready(token=token,source=source,rendered=rendered,preview=preview):
                if token==self._process_token and source is self.pixbuf:
                    self.processed=rendered
                    self._processed_quality='preview' if preview else 'full'
                    self._processed_token=token
                    if hasattr(self,'marks_area'):self.marks_area.queue_draw()
                    if preview:
                        # Finish at source resolution only after the sharp
                        # interactive frame has reached the screen.
                        self._process_full_source=GLib.timeout_add(100,self._finish_adjustment_preview)
                return False
            GLib.idle_add(ready)

    def _canvas_outlines(self,width,height):
        if not self.loaded or self.cropping:return ()
        if not self.compare:
            vp=self._viewport();x,y=vp.offset
            w,h=(dimension*vp.scale for dimension in vp.rotated_size)
            return ((x,y,w,h,'image'),)
        from .annotations import Viewport
        x,y,w,h=40,56,max(1,width-80),max(1,height-152)
        outlines=[]
        for i,path in enumerate(self.compare if self.compare_mode=='side' else self.compare[:1]):
            image=self.compare_pixbufs.get(path)
            if image is None:continue
            side=self.compare_mode=='side';cw=w/2-10 if side else w
            ch=max(1,h-8-type_metrics('small')['line_height']) if side else h
            vp=Viewport(image.get_width(),image.get_height(),cw,ch)
            ox,oy=vp.offset
            outlines.append((x+(i*(w/2+10) if side else 0)+ox,y+oy,
                image.get_width()*vp.scale,image.get_height()*vp.scale,'comparison'))
        return outlines

    def _canvas_divider(self,width,height):
        if self.compare and len(self.compare_pixbufs)==2 and self.compare_mode=='swipe':
            return 40+max(1,width-80)*self.compare_fraction,56,max(1,height-152)
        return None

    def _draw_compare(self,cr,width,height):
        if not self.compare or len(self.compare_pixbufs)!=2:return
        images=[self.compare_pixbufs[path] for path in self.compare]
        from .annotations import Viewport
        def paint(image,x,y,w,h,operator=None):
            cr.save();cr.rectangle(x,y,w,h);cr.clip();cr.translate(x,y)
            vp=Viewport(image.get_width(),image.get_height(),w,h)
            vp.transform(cr)
            if operator is not None:cr.set_operator(operator)
            Gdk.cairo_set_source_pixbuf(cr,image,0,0);cr.paint();cr.restore()
        x,y,w,h=40,56,max(1,width-80),max(1,height-152)
        if self.compare_mode=='side':
            image_height=max(1,h-8-type_metrics('small')['line_height'])
            for i,image in enumerate(images):paint(image,x+i*(w/2+10),y,w/2-10,image_height)
        else:
            paint(images[0],x,y,w,h)
            if self.compare_mode=='diff':
                import cairo
                paint(images[1],x,y,w,h,cairo.OPERATOR_DIFFERENCE)
            else:
                cr.save();cr.rectangle(x+w*self.compare_fraction,y,w*(1-self.compare_fraction),h);cr.clip();paint(images[1],x,y,w,h);cr.restore()

    def _sync_live_text(self):
        self.detected_labels={}
        self.detected_bounds=[]
        child=self.live_layer.get_first_child()
        while child:
            following=child.get_next_sibling();self.live_layer.remove(child);child=following
        self.live_layer.set_can_target(self.live)
        if not self.live or not self.loaded or self.document:return
        from .fixture import RECEIPT_LINES
        import cairo
        from gi.repository import Pango
        vp=self._viewport();cr=cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1))
        for text,y,size,align,bold in RECEIPT_LINES if self.facts.extra.get('uid')=='receipt' else []:
            cr.select_font_face('monospace',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL);cr.set_font_size(size)
            width=cr.text_extents(text).x_advance
            x=710-width if align=='right' else 450-width/2 if align=='center' else 190
            source_bounds=(x,y-size*.82,width,size*1.05)
            x,yv=vp.to_view(source_bounds[:2])
            span=Gtk.Label(label=text,selectable=True,xalign=0)
            span.add_css_class('vw-live-text')
            kind={'118 Alameda Ave, Studio 4':'addr','Oakland CA 94607':'addr','(510) 555-0183':'tel',
                  'Sep 18, 2026  10:14 AM':'date','$62.00':'money'}.get(text)
            if kind:
                span.add_css_class('vw-detected-text');span.set_name('vw-detected-'+kind)
                self.detected_bounds.append(source_bounds)
                self.detected_labels.setdefault(kind,span)
                self._connect_detector(span,kind,text)
            attrs=Pango.AttrList();attrs.insert(Pango.attr_family_new('monospace'));attrs.insert(Pango.attr_size_new_absolute(round(size*vp.scale*Pango.SCALE)))
            span.set_attributes(attrs);self.live_layer.put(span,x,yv)
        for region in self.text_regions:
            from .ocr import detection_kind
            x,y=vp.to_view((region.x,region.y));span=Gtk.Label(label=region.text,selectable=True,xalign=0)
            span.add_css_class('vw-live-text');span.set_size_request(round(region.width*vp.scale),round(region.height*vp.scale))
            kind=detection_kind(region.text)
            if kind:
                span.add_css_class('vw-detected-text');self._connect_detector(span,kind,region.text)
                self.detected_bounds.append((region.x,region.y,region.width,region.height))
            attrs=Pango.AttrList();attrs.insert(Pango.attr_size_new_absolute(round(region.height*vp.scale*Pango.SCALE)))
            span.set_attributes(attrs);self.live_layer.put(span,x,y)

    def _connect_detector(self,span,kind,text):
        click=Gtk.GestureClick(button=1)
        def tapped(_g,_n,_x,_y):
            selected,*_bounds=span.get_selection_bounds()
            if not selected:self._detector_menu(span,kind,text)
        click.connect('released',tapped);span.add_controller(click)

    def _detector_menu(self,anchor,kind,text):
        choices={'date':(('calendar','Add to Calendar','calendar'),),
            'tel':(('phone','Call','call'),('users','Add to Contacts','contact')),
            'money':(('wallet','Convert currency','convert'),),
            'addr':(('map-pin','Open in Maps','maps'),)}
        if not self.fixture:
            # A registry keeps unavailable integrations disabled in both the
            # desktop menu and its phone drawer, including keyboard activation.
            commands=[]
            for icon,title,key in choices[kind]:
                scheme={'call':'tel','maps':'geo'}.get(key)
                commands.append(Command(key,title,lambda key=key:self._detector_action(key,text),icon=icon,
                    enabled=lambda scheme=scheme:bool(scheme and Gio.AppInfo.get_default_for_uri_scheme(scheme))))
            commands.append(Command('copy',f'Copy “{text}”',lambda:self._detector_action('copy',text),icon='copy'))
            menu=Menu(CommandRegistry((CommandGroup(None,tuple(commands)),)),variant='app')
            menu.set_parent(anchor);menu.popup();return
        rows=[MenuItem(title,icon=icon,on_activate=lambda key=key:self._detector_action(key,text))
              for icon,title,key in choices[kind]]
        rows.append(MenuItem(f'Copy “{text}”',icon='copy',on_activate=lambda:self._detector_action('copy',text)))
        menu=FloatingMenu(rows,label='Detected text')
        menu.popup(anchor)
        if self.get_width()>lumaui_tokens.PHONE_MAX_WIDTH:
            host=menu.get_parent()
            float_at(host,menu,rect_in(host,anchor),prefer='below',align='start',offset=6)

    def _detector_action(self,action,text):
        if self.fixture:Toast.show(self.host,'Fixture is read-only');return
        if action=='copy':
            self.get_clipboard().set(text);Toast.show(self.host,'Copied');return
        if action in ('call','maps'):
            from urllib.parse import quote
            uri='tel:'+''.join(c for c in text if c.isdigit() or c=='+') if action=='call' else 'geo:0,0?q='+quote(text)
            try:Gio.AppInfo.launch_default_for_uri(uri,self.get_display().get_app_launch_context())
            except GLib.Error as error:Toast.show(self.host,str(error))
        else:
            # No new Contacts/Calendar writes or invented exchange-rate service.
            Toast.show(self.host,{'calendar':'Adding events from Viewer is unavailable',
                'contact':'Adding contacts from Viewer is unavailable',
                'convert':'Currency conversion is unavailable'}[action])

    def _change_snapshot(self):
        return dict(adjustments=dict(self.adjustments),crop=self.crop,rotation=self.rotation,flip=self.flip,form_values=dict(self.form_values))

    def _revert(self):
        self.history.apply(());self.adjustments={};self.crop=None;self.rotation=0;self.flip=False;self.form_values={}
        if self.document is not None and self.source_bytes:
            import threading
            original=self.source_bytes;token=self.load_token
            def restore():
                gi.require_version('Poppler','0.18')
                from gi.repository import Poppler
                try:document=Poppler.Document.new_from_bytes(GLib.Bytes.new(original),None)
                except Exception as error:GLib.idle_add(self._render_failed,token,str(error));return
                from .pdf_text import document_text
                regions=document_text(document)
                GLib.idle_add(self._pdf_ready,token,document,document.get_n_pages(),original,regions)
            threading.Thread(target=restore,daemon=True).start()
        self._processing_changed();self._refresh_files();self._resize_canvas()

    def _load_text_regions(self):
        self.text_regions=[]
        if self.fixture or not self.pixbuf:return
        source=self.pixbuf;token=self.load_token
        import threading
        def work():
            from .ocr import recognize
            try:
                ok,data=source.save_to_bufferv('png',[],[])
                regions=recognize(data) if ok else []
            except Exception:regions=[]
            def ready():
                if token==self.load_token:
                    self.text_regions=regions;self._refresh_actions()
                return False
            GLib.idle_add(ready)
        threading.Thread(target=work,daemon=True).start()

    def _load_thumbnail(self,path,cached):
        import threading
        from . import formats
        def work():
            try:
                pixbuf=self.recents.pixbuf(path) if self.fixture and not cached else None
                if pixbuf is None:pixbuf=formats.load_image(Path(cached))[0]
                ratio=max(42/pixbuf.get_width(),34/pixbuf.get_height())
                scaled=pixbuf.scale_simple(max(42,round(pixbuf.get_width()*ratio)),max(34,round(pixbuf.get_height()*ratio)),2)
                cropped=scaled.new_subpixbuf((scaled.get_width()-42)//2,(scaled.get_height()-34)//2,42,34).copy()
            except Exception:cropped=None
            def ready():
                self.thumbnail_pending.discard(path)
                if cropped:
                    texture=Gdk.Texture.new_for_pixbuf(cropped);self.thumbnail_cache[path]=texture
                    self._refresh_files()
                return False
            GLib.idle_add(ready)
        threading.Thread(target=work,daemon=True).start()

    def _sync_compare_labels(self):
        child=self.compare_labels.get_first_child()
        while child:
            following=child.get_next_sibling();self.compare_labels.remove(child);child=following
        if not self.compare or not self.loaded:return
        width,height=self.marks_area.get_width(),self.marks_area.get_height()
        x,y,w,h=40,56,max(1,width-80),max(1,height-152)
        def name(path):
            f=self.recents.sample(path) if self.fixture else None
            return f['name'] if f else Path(path).name
        def overlay_label(text):
            result=label(text,'small');result.add_css_class('vw-comparison-label')
            return result
        if self.compare_mode=='diff':
            self.compare_labels.put(overlay_label('Only what changed is bright'),x,y-34)
        elif self.compare_mode=='swipe':
            left,right=(overlay_label(name(path)) for path in self.compare)
            self.compare_labels.put(left,x,y-34)
            _minimum,natural,_a,_b=right.measure(Gtk.Orientation.HORIZONTAL,-1)
            self.compare_labels.put(right,x+w-natural,y-34)
            from gi.repository import Graphene, Gsk
            glyph=icons.image('chevrons-up-down',pixel_size=16)
            glyph.add_css_class('vw-comparison-glyph');glyph.set_name('vw-compare-handle-icon')
            self.compare_labels.put(glyph,0,0)
            self.compare_labels.set_child_transform(glyph,Gsk.Transform().translate(
                Graphene.Point().init(x+w*self.compare_fraction+8,y+h/2-8)).rotate(90))
        elif len(self.compare_pixbufs)==2:
            caption_height=type_metrics('small')['line_height']
            image_height=max(1,h-8-caption_height)
            for i,path in enumerate(self.compare):
                image=self.compare_pixbufs[path];cell=w/2-10
                scale=min(cell/image.get_width(),image_height/image.get_height())
                caption=label(name(path),'small')
                caption.set_size_request(-1,math.ceil(caption_height))
                _minimum,natural,_a,_b=caption.measure(Gtk.Orientation.HORIZONTAL,-1)
                self.compare_labels.put(caption,x+i*(w/2+10)+(cell-natural)/2,y+(image_height+image.get_height()*scale)/2+8)
