# SPDX-License-Identifier: Apache-2.0
"""Photos on LumaUI: composition, commands and asynchronous I/O.

AppWindow / NavigationSidebar / SidebarToggle / Island / ToastHost form the
window. ActionCenter grows into ActionEditor for reversible photo edits.
On a computer CornerPill holds the library's view (Years to All, photo size)
and the photo's actions; DetailsPane owns Information. On a phone (v71) there
is one bar: the collection and zoom as dropdowns, then Search and Add; viewing
a photo, Edit, Share, Favorite, Details and More, with the title island, a
filmstrip and the photo edge to edge. Photos owns only its own surfaces: the
photo grid, the Memories row and player, and the filmstrip.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import gi

gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Adw,Gdk,Gio,GLib,Gtk,Pango
import luma_appkit as kit
from luma_appkit import (AppWindow,Island,NavigationSidebar,SidebarToggle,
                        ActionCenter,ActionEditor,BarAction,SEPARATOR,SPACER,CornerPill,
                        DetailsPane,Toast,ToastHost,EmptyState,ListEmptyState,CountBadge,
                        Command,CommandGroup,CommandRegistry,TextField,StatusPill,
                        ScrollView,apply_type,install_appkit,install_lumaui,bind_navigation_input,
                        add_style_sheet,icons,lumaui_tokens)
from luma_appkit.action_center import make_control
from luma_appkit.action_bubble import FloatingMenu
from .photos_backend import APPLICATION_ID,IMAGE_SUFFIXES,PhotoLibrary,_walk_files
from .photos_content import decode_photo,decode_face,decode_square,prepare_print_pdf,decode_export_photo,edited_photo_node
from .photos_data import NAVIGATION,groups_for,memories_for
from .photos_state import PAGE_SIZE,PhotosState,source_from_environment

WIDGET_NAMES=('ph-sidebar','ph-island','ph-title','ph-body','ph-action-center','ph-corner',
              'ph-details','ph-editor','ph-image','ph-more','ph-import','ph-new-album')


# The shared preview launcher overrides this module-level application ID.
APP_ID=APPLICATION_ID
# LumaUI is mandatory: a successful module import means the platform is present.
# Keep the existing runtime/packaging capability contract for callers.
LUMA_PLATFORM_AVAILABLE=True


def _part(name):
    """Fail explicitly until the coordinator's published shared API is present."""
    try:
        return getattr(kit,name)
    except AttributeError:
        raise RuntimeError(f'LumaUI part {name} is pending (Photos kit requests 01–04)') from None


def _typed(text,role,*,fallback='body',weight=None,**properties):
    # TODO(kit-request photos-01-photo-grid.md, TY1): photo group and year roles.
    label=Gtk.Label(label=text,**properties)
    roles={key.replace('_','-') for key in lumaui_tokens.TYPE_SCALE}
    return apply_type(label,role if role in roles else fallback,weight=weight)


def _clear(box):
    while (child:=box.get_first_child()) is not None:
        box.remove(child)


def _phone_gutter():
    # v71: one side gutter on phones (K-NAV publishes it as GUTTER['phone']).
    return getattr(lumaui_tokens,'GUTTER',{}).get('phone',16)


def _viewer_insets(phone,editing):
    """Top, side, bottom, side room around the photo. On a phone it runs
    edge to edge on black, between the title island and the filmstrip."""
    if phone:
        return 170,0,(360 if editing else 168),0
    return 88,32,(290 if editing else 96),32


# Sizes v71 lets the photo grid take (P.size): the phone's slider and pinch
# reach a little further than the desktop island's.
SIZE_MIN,SIZE_MAX,SIZE_MAX_PHONE=96,300,340
ZOOMS=(('years','Years','calendar-range'),('months','Months','calendar-days'),
       ('days','Days','calendar'),('all','All photos','layout-grid'))
EDIT_MODES=(('looks','Looks','palette'),('light','Light','sun'),('colour','Color','droplet'),('crop','Crop','crop'))
MEMORY_SECONDS=3.2


class _MemoriesSection(Gtk.Widget):
    """The Memories row: each card is 72% of the row's own width (v71 `.pmemc`)."""

    def __init__(self):
        super().__init__()
        self._box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._box.set_parent(self)
        self.cards=[]
        self._width=0

    def append(self,child):
        self._box.append(child)

    def do_measure(self,orientation,for_size):
        return self._box.measure(orientation,for_size)

    def do_size_allocate(self,width,height,baseline):
        if width!=self._width:
            self._width=width
            for frame in self.cards:frame.set_width(max(160,round(width*.72)))
        self._box.allocate(width,height,baseline,None)

    def do_dispose(self):
        if self._box is not None:
            self._box.unparent();self._box=None


class _MemoryFrame(Gtk.Widget):
    """A Memory card's box: exactly its width and 190 tall, whatever the photo's size."""

    def __init__(self,child,height=190):
        super().__init__()
        self._width,self._height=240,height
        self.set_overflow(Gtk.Overflow.HIDDEN)
        child.set_parent(self)
        self._child=child

    def set_width(self,width):
        if width!=self._width:
            self._width=width
            self.queue_resize()

    def do_measure(self,orientation,_for_size):
        size=self._width if orientation==Gtk.Orientation.HORIZONTAL else self._height
        return size,size,-1,-1

    def do_size_allocate(self,width,height,baseline):
        self._child.allocate(width,height,baseline,None)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent();self._child=None


class PhotosWindow(AppWindow):
    def __init__(self,application,*,library=None):
        self.state=None
        self._supplied_source=library
        self.library=None
        self.executor=ThreadPoolExecutor(max_workers=4,thread_name_prefix='photos-decode')
        self.catalog_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='photos-catalog')
        self.scan_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='photos-scan')
        self._generation=0
        self._image_generation=0
        self._closed=False
        self._pending_open_path=None
        self._busy=False
        self._scan_generation=0
        self._monitors=[]
        self._monitor_timeout=0
        self._initial_scan_timer=0
        self._fixture_actions_applied=False
        self._edit_session=0
        self._slider_rows=[]
        self._sliders={}
        super().__init__(application=application,app_id=APPLICATION_ID,title='Photos',icon_name=APPLICATION_ID,
                         commands=self._commands(),default_width=1180,default_height=740,
                         minimum_width=360,minimum_height=420)
        self.content=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,hexpand=True,vexpand=True)
        self.sidebar=NavigationSidebar(variant='destinations')
        self.sidebar.set_size_request(212,-1)
        self.sidebar.set_name('ph-sidebar')
        self.sidebar.list.connect('row-activated',self._destination_activated)
        self.content.append(self.sidebar)
        self.island=Island()
        self.island.set_name('ph-island')
        self.island.set_hexpand(True); self.island.set_vexpand(True)
        self.page=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True,vexpand=True)
        self.header=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        self.header_words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True)
        self.header.append(self.header_words)
        # TODO(v71-kit PageHeader): the phone stack (30px title, meta on its
        # own line) comes from K-NAV's PageHeader once it lands.
        self.header.set_name('ph-header-content')
        self.header_slot=Adw.Bin(child=self.header)
        self.header_slot.set_name('ph-title')
        for side,value in (('top',20),('start',24),('end',24),('bottom',10)):
            self.header.set_property(f'margin-{side}',value)
        self.title=_typed('Library','title-1',xalign=0)
        self.meta=kit.NavigationTrailBar(kit.NavigationTrail(kit.Place('library','Library')),title=False)
        self.meta.set_margin_top(4)
        self.header_words.append(self.title); self.header_words.append(self.meta)
        self.photo_body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.photo_body.set_name('ph-photo-groups')
        self.photo_body.set_margin_start(20); self.photo_body.set_margin_end(20); self.photo_body.set_margin_bottom(96)
        self.photo_body.append(ListEmptyState('Loading photos…'))
        self.scroll=ScrollView(self.photo_body)
        self.scroll.set_name('ph-body')
        self.scroll.set_vexpand(True)
        self._resize_gestures(self.scroll)
        self.page.append(self.header_slot); self.page.append(self.scroll)
        self.library_empty=EmptyState('No photos yet',
            'Import photos to start your library.', 'lumaui-image-symbolic',
            primary=('Import photos',self._import_folder))
        self.library_empty.set_name('ph-library-empty')
        self.library_empty.set_visible(False)
        self.page.append(self.library_empty)
        self.stack=Gtk.Stack(hexpand=True,vexpand=True)
        self.stack.add_named(self.page,'library')
        self.viewer_slot=Adw.Bin(hexpand=True,vexpand=True)
        self.stack.add_named(self.viewer_slot,'viewer')
        self.overlay=Gtk.Overlay(child=self.stack)
        # Top right: the library's view island (desktop) or the photo's corner.
        self.corner_slot=Adw.Bin(halign=Gtk.Align.END,valign=Gtk.Align.START)
        self.overlay.add_overlay(self.corner_slot)
        # Phone viewer: the filmstrip above the bar (the title island floats on the window).
        self.strip_slot=Adw.Bin(halign=Gtk.Align.FILL,valign=Gtk.Align.END,visible=False)
        self.strip_slot.set_name('ph-strip-slot')
        self.overlay.add_overlay(self.strip_slot)
        self.island.append(self.overlay)
        self.host=ToastHost(self.island)
        self.host.set_hexpand(True); self.host.set_vexpand(True)
        self.content.append(self.host)
        self.details=DetailsPane('Information',on_close=self._details_closed)
        self.details.set_name('ph-details')
        self.details.connect('notify::shown',self._details_visibility_changed)
        self.content.append(self.details)
        self.action_center=ActionCenter().attach(self.host)
        self.action_center.bar.set_name('ph-action-center')
        # v71 desktop keeps the sidebar button in the title row; a phone has
        # none (the collection dropdown in the bar is how you move).
        self.toggle=SidebarToggle(self.sidebar)
        self.toggle.set_name('ph-sidebar-toggle')
        self.toggle.set_control_visible(False)
        self.title_bar.pack_start(self.toggle)
        # The Memories player covers the whole window, over every island.
        self.root=Gtk.Overlay(child=self.content)
        self.set_body(self.root)
        self._phone=False
        self._immersive=False
        self._search_panel=None
        self._places=[]
        self._breakpoints()
        keys=Gtk.EventControllerKey()
        keys.connect('key-pressed',self._key_pressed)
        self.add_controller(keys)
        bind_navigation_input(self,self._go_back,lambda:False)
        self.connect('close-request',self._close_requested)
        self._load()

    def _commands(self):
        return CommandRegistry((CommandGroup('',(
            Command('photos.import','Import',self.request_import,'upload'),
            Command('photos.new-album','New album',self.request_new_album,'plus',shortcut=('Ctrl','N')),
            Command('photos.search','Search photos',self.focus_search,'search',shortcut=('Ctrl','F')),
            Command('photos.sidebar','Show or hide sidebar',lambda:self.toggle.toggle(), 'panel-left'),
            Command('photos.quit','Quit Photos',self.close, "log-out", shortcut=('Ctrl','Q')),
        )),))

    def _breakpoints(self):
        # v71 tiers: a phone under 560 (the kit's PHONE_MAX_WIDTH), compact to
        # 900. Crossing the phone width redraws the bars in the other shape.
        phone=Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f'max-width: {lumaui_tokens.PHONE_MAX_WIDTH}px'))
        phone.connect('apply',lambda *_:self._tier_changed(True))
        phone.connect('unapply',lambda *_:self._tier_changed(False))
        gutter=_phone_gutter()
        # v71 Photos keeps its body at 20 on a phone (.iwin .pbody); the title takes the 16 gutter.
        phone.add_setter(self.photo_body,'margin-start',20); phone.add_setter(self.photo_body,'margin-end',20)
        phone.add_setter(self.header,'margin-start',gutter); phone.add_setter(self.header,'margin-end',gutter)
        phone.add_setter(self.sidebar,'visible',False)
        phone.add_setter(self.toggle,'visible',False)
        self.phone_breakpoint=phone
        self.add_breakpoint(phone)

    def _tier_changed(self,phone):
        if phone==self._phone:return
        self._phone=phone
        self._search_panel=None
        self._immersive=False
        self._set_viewer_fit_insets(phone)
        if self.state is None:return
        if self.state.current:self._render_viewer()
        else:self._render_library()
        if phone and getattr(self,'_fixture_pending',None):
            GLib.timeout_add(300,lambda:(self._fixture_panel(),False)[1])

    def _set_viewer_fit_insets(self,phone):
        for editing,(_,viewport,_,_) in getattr(self,'_viewer_surfaces',{}).items():
            viewport.set_fit_insets(*_viewer_insets(phone,editing))

    def _load(self,then=None):
        self._generation+=1
        generation=self._generation
        snapshot=self.state.query_snapshot() if self.state else None
        # Capture the query generation: obsolete catalog work is interrupted.
        def read():
            if self.state is None:
                source=self._supplied_source or source_from_environment()
                state=PhotosState(source)
                if state.fixture:
                    state.view=os.environ.get('LUMA_PHOTOS_VIEW','library')
                    state.zoom=os.environ.get('LUMA_PHOTOS_ZOOM','days')
                    state.query=os.environ.get('LUMA_PHOTOS_QUERY','')
                page=state.read_page(cancelled=lambda:self._closed or generation!=self._generation)
                # Search suggestions come from the library even when the first
                # page opens with a query or a different collection.
                records=page.records
                if state.query or state.view!='library':
                    records,_total=source.page(query='',collection='library' if state.fixture else 'all',
                                               limit=PAGE_SIZE,cancelled=lambda:self._closed or generation!=self._generation)
                places=list(dict.fromkeys(p.place for p in records if p.place))
                return state,page,places
            return self.state,self.state.read_page(snapshot=snapshot,cancelled=lambda:self._closed or generation!=self._generation),None
        future=self.catalog_executor.submit(read)
        future.add_done_callback(lambda future:GLib.idle_add(self._loaded,generation,future,then))

    def _loaded(self,generation,future,then):
        if self._closed or generation!=self._generation:
            return GLib.SOURCE_REMOVE
        try:
            self.state,page,places=future.result()
            if places is not None:self._places=places
            self.library=self.state.source
            self.state.apply_page(page)
            if not self.state.query and self.state.view=='library':
                # Search's place chips are the library's places, not the matches'.
                self._places=list(dict.fromkeys(p.place for p in self.state.records if p.place))
            self._render_sidebar()
            if self.state.current:self._render_viewer()
            else:self._render_library()
            if not self.state.fixture and not getattr(self,'_scan_started',False):
                self._scan_started=True
                self._begin_scan()
                self._watch_drives()
            if then:
                then()
            elif self.state.fixture and not self._fixture_actions_applied:
                self._fixture_actions_applied=True
                self._apply_fixture_state()
            elif self._pending_open_path:
                self._resolve_pending_open()
        except Exception as failure:
            self.library_empty.set_visible(False)
            self.scroll.set_visible(True)
            _clear(self.photo_body)
            self.photo_body.append(ListEmptyState(str(failure)))
            # A missing kit part remains visibly an error until it lands; it
            # never becomes a silent empty collection or a fake PASS.
            self.last_error=str(failure)
        return GLib.SOURCE_REMOVE

    def _render_sidebar(self):
        self.sidebar.clear()
        self.sidebar.append_section('Library')
        for key,label,icon in NAVIGATION[:4]:
            self._sidebar_row(key,label,icon,self.state.library_count if key=='library' else None)
        section=kit.append_section(self.sidebar,'Albums',action=('plus','New album',self.request_new_album))
        self.new_album_button=section.action_button
        self.new_album_button.set_name('ph-new-album')
        for key,label,icon in self._albums():
            self._sidebar_row(key,label,icon)
        if self.state.fixture and not hasattr(self,'sync_status'):
            self.sync_status=StatusPill('online',label=self.state.fixture.sync)
            self.sync_status.set_tooltip_text('Photos from this computer and your phone stay in step')
            self.sidebar.append_footer(self.sync_status)

    def _albums(self):
        """v71's album order: Launch, Wallpapers, the rest, then Deleted."""
        rows=[(album.id,album.name,'star') for album in self.state.albums if album.name=='Launch']
        rows.append(('walls','Wallpapers','grid-2x2'))
        rows+=[(album.id,album.name,'image') for album in self.state.albums
               if album.name!='Launch' and album.name.casefold()!='wallpapers']
        rows.append(('deleted','Deleted','trash-2'))
        return rows

    def _count(self,key):
        state=self.state
        if key=='library':return state.library_count
        if key=='fav':return state.counts.get('favorites')
        if key=='deleted':return state.counts.get('deleted')
        if key=='walls':
            if state.fixture:return len(state.fixture.rows('walls'))
            album=next((a for a in state.albums if a.name.casefold()=='wallpapers'),None)
        else:
            album=next((a for a in state.albums if a.id==key),None)
        return album.asset_count if album else None

    def _sidebar_row(self,key,label,icon,count=None):
        row=kit.SidebarRow(label,lead=kit.RowLead.icon(icon),trail=count)
        row.destination=key
        row.set_name(f'ph-view-{key}')
        self.sidebar.append_row(row)
        if self.state.view==key:
            self.sidebar.list.select_row(row)

    def _destination_activated(self,_list,row):
        if self.state and hasattr(row,'destination'):
            self._choose_collection(row.destination)

    def _choose_collection(self,key):
        self._fold()
        if self.state is None:return
        self.state.switch_view(key)
        self._load()

    def _render_library(self):
        self._image_generation+=1
        self._leave_viewer_chrome()
        kit.media_context(self.action_center,False)
        self.stack.set_visible_child_name('library')
        self.details.show(open=False,subject=None)
        _clear(self.photo_body)
        state=self.state
        empty_library=not state.records and state.view=='library' and not state.query and not state.library_count
        self.scroll.set_visible(not empty_library)
        self.library_empty.set_visible(empty_library)
        title=self._collection_label(state.view)
        self.title.set_label(title)
        if state.total:
            self.meta.set_meta([f'{state.total} photos',f'{state.place_count} places',state.month])
        else:
            self.meta.set_meta(['Nothing here'])
        # v71 Memories: the library's top (not while searching, not in Years). The
        # simulator shows the row on a computer too, so every width has it.
        if state.view=='library' and not state.query and state.zoom!='years':
            memories=memories_for(state.records,state.now,state.fixture)
            if memories:self.photo_body.append(self._memories_row(memories))
        if not state.records:
            text='Nothing was deleted in the last 30 days.' if state.view=='deleted' else 'No photos in this album yet.' if state.view not in {key for key,_,_ in NAVIGATION} else 'No photos match.'
            empty=ListEmptyState(text)
            empty.set_margin_top(30)
            empty.set_xalign(0);empty.set_justify(Gtk.Justification.LEFT)
            self.photo_body.append(empty)
        elif state.zoom=='years':
            self._render_years()
        else:
            minimum=int(round(state.size*1.55)) if state.zoom=='months' else state.size
            for index,group in enumerate(groups_for(state.records,state.zoom,state.now,state.fixture)):
                if group.title:
                    line=Gtk.Box(spacing=10)
                    line.set_margin_top(6 if self.photo_body.get_first_child() is None else 18); line.set_margin_bottom(10)
                    line.set_margin_start(2); line.set_margin_end(2)
                    line.append(_typed(group.title,'section-day',fallback='title-2',xalign=0,valign=Gtk.Align.BASELINE))
                    subtitle=_typed(group.subtitle,'body',xalign=0,valign=Gtk.Align.BASELINE,
                                    ellipsize=Pango.EllipsizeMode.END,hexpand=True)
                    subtitle.add_css_class('ph-day-meta')
                    line.append(subtitle)
                    self.photo_body.append(line)
                items=[kit.MediaItem(p.id,str(p.path),title=p.display_name,favourite=p.favorite,data=p)
                       for p in group.records]
                grid=kit.MediaGrid(items,kind='photo',min_side=minimum,scrolls=False,
                                   on_activate=lambda item:self.open_photo(item.id),label=group.title or title)
                grid.set_name('ph-grid')
                drag=Gtk.DragSource(actions=Gdk.DragAction.COPY)
                drag.connect('prepare',self._prepare_grid_drag,grid)
                grid.add_controller(drag)
                self.photo_body.append(grid)
            self._page_navigation()
        self._library_bar()
        self._view_island()

    def _collection_label(self,key):
        label=next((label for k,label,_ in NAVIGATION if k==key),None)
        return label or next((album.name for album in self.state.albums if album.id==key),'Library')

    def _collection_icon(self,key):
        return next((icon for k,_label,icon in NAVIGATION if k==key),'image')

    def _page_navigation(self):
        state=self.state
        if state.total<=PAGE_SIZE:return
        first=state.page_offset+1
        last=state.page_offset+len(state.records)
        row=Gtk.Box(spacing=12,halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
        row.set_margin_top(24);row.set_margin_bottom(12)
        previous=Gtk.Button(label='Newer photos')
        previous.set_sensitive(state.page_offset>0)
        previous.connect('clicked',lambda *_:self._change_page(-1))
        next_page=Gtk.Button(label='Older photos')
        next_page.set_sensitive(last<state.total)
        next_page.connect('clicked',lambda *_:self._change_page(1))
        row.append(previous)
        row.append(_typed(f'{first:,}–{last:,} of {state.total:,}','meta'))
        row.append(next_page)
        row.set_name('ph-page-navigation')
        self.photo_body.append(row)

    def _change_page(self,direction):
        destination=self.state.page_offset+direction*PAGE_SIZE
        if not 0<=destination<self.state.total:return
        self.state.page_offset=destination
        self._load(then=lambda:self.scroll.get_vadjustment().set_value(0))

    def _prepare_grid_drag(self,_source,x,y,grid):
        tile=grid.pick(x,y,Gtk.PickFlags.DEFAULT)
        while tile is not None and tile.get_parent() is not grid:
            tile=tile.get_parent()
        child=grid.get_first_child()
        position=0
        while child is not None:
            if child is tile:
                item=grid.model.get_item(position)
                return self._prepare_file_drag(None,x,y,item.id) if item else None
            child=child.get_next_sibling()
            position+=1
        return None

    def _prepare_file_drag(self,_source,_x,_y,identifier):
        # Only local, available originals can leave Photos. Fixture actions
        # never offer files to another application.
        if self.state is None or self.state.fixture:return None
        record=next((p for p in self.state.records if p.id==identifier),None)
        if record is None or not record.available:return None
        file=Gio.File.new_for_path(str(record.path))
        return Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([file])),
            Gdk.ContentProvider.new_for_bytes('text/uri-list',GLib.Bytes.new((file.get_uri()+'\r\n').encode())),
        ])

    def _render_years(self):
        state=self.state
        cards=state.years
        if not hasattr(self,'_year_grid'):
            self._year_grid=Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,homogeneous=True,
                                       column_spacing=10,row_spacing=10,min_children_per_line=3,max_children_per_line=3)
            self.phone_breakpoint.add_setter(self._year_grid,'min-children-per-line',1)
            self.phone_breakpoint.add_setter(self._year_grid,'max-children-per-line',1)
            if self.get_current_breakpoint()==self.phone_breakpoint:
                self._year_grid.set_min_children_per_line(1);self._year_grid.set_max_children_per_line(1)
            self._year_grid.add_css_class('ph-year-grid')
            self._year_grid.set_hexpand(True)
            self._year_grid.set_halign(Gtk.Align.FILL)
        flow=self._year_grid
        columns=1 if self.get_current_breakpoint()==self.phone_breakpoint else min(3,max(1,len(cards)))
        flow.set_min_children_per_line(columns)
        flow.set_max_children_per_line(columns)
        _clear(flow)
        self.photo_body.append(flow)
        for card in cards:
            record=state.source.asset(card['photo'])
            button=kit.MediaCollectionCard(record.path, title=str(card['year']),
                detail=f"{card['count']:,} photos", aspect=2.5 if len(cards)==1 else 1.6,
                on_activate=lambda year=card['year']:self._open_year(year))
            flow.append(button)

    def _open_year(self,year):
        # Year summaries cover the full catalog even when the grid is paged.
        # Start at the page containing this year's first photo.
        preceding=sum(card['count'] for card in self.state.years if str(card['year'])>str(year))
        self.state.page_offset=(preceding//PAGE_SIZE)*PAGE_SIZE
        self.state.zoom='months'
        self._load(then=lambda:self.scroll.get_vadjustment().set_value(0))

    # ── the library's bar and view ─────────────────────────────────────────

    def _library_bar(self):
        if self._phone:
            self._library_bar_phone()
            return
        # v71: the bar holds what you do every time, search and import. How the
        # library is laid out is a view choice: it floats top right (_view_island).
        focused=bool(getattr(self,'search_item',None) and self.search_item.entry and self.search_item.entry.has_focus())
        self.search_item=kit.BarSearch('Search places, people',span='narrow',text=self.state.query,label='Search photos',on_change=self._search_changed)
        self.action_center.show_bar([self.search_item,SEPARATOR,
                                     BarAction('upload','Import',primary=True,on_activate=self.request_import)])
        self._name_bar_action('Import','ph-import')
        if focused:self.search_item.focus()

    def _view_island(self):
        """Desktop: Years · Months · Days · All, then the photo size (not at Years)."""
        self.size_control=None
        if self._phone or self.state is None or self.state.current is not None:
            if self.state is None or self.state.current is None:self.corner_slot.set_child(None)
            return
        modes=kit.ModeSwitch([('years','Years'),('months','Months'),('days','Days'),('all','All')],
                             current=self.state.zoom,label='Zoom',on_change=self._set_zoom)
        modes.set_name('ph-zoom')
        options={'modes':modes}
        if self.state.zoom!='years':
            self.size_control=kit.ZoomControl(self.state.size/100,self._zoom_size_changed,kind='range',
                                              minimum=SIZE_MIN/100,maximum=SIZE_MAX/100,label='Photo size')
            size=make_control(self.size_control)
            size.set_name('ph-size')
            options['zoom']=size  # CornerPill folds it away in a window 720 or narrower
        island=CornerPill(**options)
        island.set_name('ph-view')
        self.corner_slot.set_child(island)

    def _library_bar_phone(self):
        """v71 phone: one bar. Where you are and how you're looking, as two
        dropdowns, then Search and Add as small icons on the right. Every
        choice grows the bar, never a drawer."""
        state=self.state
        self.corner_slot.set_child(None)
        if self._panel=='search' and getattr(self,'_search_panel',None) is not None:
            self._fill_search_panel()  # typing: the count and chips follow, the field keeps focus
            return
        zoom=next(label for key,label,_ in ZOOMS if key==state.zoom).replace(' photos','')
        regrow=self.action_center.grown if self.action_center.grown in ('zoom',) else None
        self.action_center.show_bar([
            BarAction(self._collection_icon(state.view),self._collection_label(state.view),dropdown=True,dropdown_size="compact",
                      panel=self._collections_panel,key='places'),
            BarAction('',zoom,dropdown=True,dropdown_size='compact',panel=self._zoom_panel,key='zoom'),
            SPACER,
            BarAction('search',tooltip='Search',active=bool(state.query),on_activate=self._open_search),
            BarAction('plus',tooltip='Add photos',panel=self._add_panel,key='add')],fill=True)
        for label,name in ((self._collection_label(state.view),'ph-collection'),(zoom,'ph-zoom-menu'),
                           ('Search','ph-search'),('Add photos','ph-add')):
            self._name_bar_action(label,name)
        if regrow:
            # v71 keeps Show by open while you pick (the zoom changes under it).
            self.action_center.grow(regrow,self._zoom_panel,anchor=self._bar_anchor('ph-zoom-menu'))

    def _name_bar_action(self,label,name):
        child=self.action_center.bar_row.get_first_child()
        while child is not None:
            item=getattr(child,'bar_item',None)
            if item and (getattr(item,'label',None)==label or getattr(item,'tooltip',None)==label):
                child.set_name(name)
                return
            child=child.get_next_sibling()

    # ── panels that grow the bar (phone) ──────────────────────────────────

    def _grow(self,key,widget,anchor_name=None,**options):
        """Grow the bar into `widget` (tapping the same key again folds it)."""
        self._dismiss_menu()
        self.action_center.grow(key,widget,anchor=self._bar_anchor(anchor_name) if anchor_name else None,**options)

    def _fold(self):
        """Close whatever grew: a panel, a menu."""
        self._dismiss_menu()
        if self.action_center.grown is not None:
            self.action_center.fold_panel()

    @property
    def _panel(self):
        return self.action_center.grown if hasattr(self,'action_center') else None

    @_panel.setter
    def _panel(self,_value):
        pass  # the action center keeps what is grown

    def _thumbnail(self,path,side=36):
        picture=Gtk.Picture(content_fit=Gtk.ContentFit.COVER,can_shrink=True)
        picture.set_size_request(side,side)
        picture.set_overflow(Gtk.Overflow.HIDDEN)
        picture.add_css_class('ph-thumb')
        self._decode_into(path,picture,side*2,square=True,keep=True)
        return picture

    def _collections_panel(self):
        """Library, Favorites, People, Places as tiles; Albums with covers and
        counts, New album; then Deleted."""
        state=self.state
        panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.set_name('ph-collections')
        panel.append(kit.BarTiles([kit.BarTile(icon,label,lambda k=key:self._choose_collection(k),on=state.view==key)
                                   for key,label,icon in NAVIGATION[:4]],columns=4))
        heading=kit.PanelHeading('Albums', action=BarAction('plus','New album',
                                                           on_activate=self._new_album_panel))
        heading.action_button.set_name('ph-panel-new-album')
        panel.append(heading)
        for key,label,icon in self._albums():
            cover=state.covers.get('walls' if key=='walls' else key) if key!='deleted' else None
            options={'lead':self._thumbnail(cover)} if cover else {'icon':icon}
            count=self._count(key)
            panel.append(kit.PanelRow(label,count=None if count is None else f'{count:,}',current=state.view==key,
                                      on_activate=lambda k=key:self._choose_collection(k),**options))
        return panel

    def _zoom_panel(self):
        """Show by: Years, Months, Days, All photos; then the photo size."""
        panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.set_name('ph-show-by')
        panel.append(kit.PanelHeading('Show by'))
        panel.append(kit.BarTiles([kit.BarTile(icon,label,lambda k=key:self._set_zoom(k),on=self.state.zoom==key,closes=False)
                                   for key,label,icon in ZOOMS],columns=4))
        self.size_control=None
        if self.state.zoom!='years':
            panel.append(kit.PanelHeading('Photo size'))
            self.size_control=kit.ZoomControl(self.state.size/100,self._zoom_size_changed,kind='range',
                                              minimum=SIZE_MIN/100,maximum=SIZE_MAX_PHONE/100,
                                              label='Photo size',steps=True)
            panel.append(make_control(self.size_control))
        return panel

    def _add_panel(self):
        """Add photos grows only the sources that exist. From Files, always;
        Paste, only with a photo on the clipboard; a drive or card, only when
        one is plugged in. A phone is never offered a phone."""
        rows=['Add photos',kit.PanelRow('From Files',icon='folder-open',submenu=True,on_activate=self._add_from_files)]
        clip=self._clipboard_photo()
        if clip:
            lead=self._thumbnail(self.state.source.asset(str(clip)).path) if self.state.fixture else None
            rows.append(kit.PanelRow('Paste',subtitle='The photo you copied',on_activate=self._paste,
                                     **({'lead':lead} if lead else {'icon':'clipboard-paste'})))
        drive=self._drive()
        if drive:
            name,count,_root=drive
            rows.append(kit.PanelRow(name,icon='hard-drive',subtitle=f'{count:,} new photos',submenu=True,
                                     on_activate=self._import_drive))
        panel=kit.panel_list(rows,label='Add photos')
        panel.set_name('ph-add-panel')
        return panel

    def request_add(self):
        self._grow('add',self._add_panel,'ph-add')

    def _new_album_panel(self):
        """New album grows a name field (the panel's 48px well) and Create album."""
        panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.set_name('ph-album-panel')
        panel.append(kit.PanelHeading('New album'))
        field=kit.PanelField('folder-plus','Album name',on_submit=self._create_album)
        field.set_name('ph-album-name')
        panel.append(field)
        create=make_control(BarAction('','Create album',primary=True,on_activate=lambda:self._create_album(field.text)))
        create.set_name('ph-album-create')
        panel.append(create)
        self.action_center.fold_panel()
        self._grow('album',panel,'ph-collection')
        GLib.idle_add(lambda:(field.grab_focus(),False)[1])

    def _open_search(self):
        """Search turns the row into the field with ✕; above it, place chips,
        then a live photo count while you type."""
        self._search_panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._search_panel.set_name('ph-search-panel')
        self._fill_search_panel()
        field=kit.PanelField('search','Search',text=self.state.query,on_change=self._search_changed)
        field.set_name('ph-search-field')
        self._search_field=field
        self._grow('search',self._search_panel,'ph-search',entry=field,on_fold=self._close_search)

    def _fill_search_panel(self):
        state,panel=self.state,self._search_panel
        _clear(panel)
        places=self._places[:8]
        if not state.query and not places:
            panel.append(ListEmptyState('Search your photos by name or place.'))
            return
        heading=f"{state.total:,} {'photo' if state.total==1 else 'photos'}" if state.query else 'Places'
        panel.append(kit.PanelHeading(heading))
        on=next((p for p in places if p.casefold()==state.query.casefold()),None)
        if places:
            panel.append(kit.PanelChoices([(p,p,'map-pin') for p in places],selected=on,on_choose=self._search_place))

    def _close_search(self):
        self._search_panel=None
        self._search_field=None
        if self.state.query:
            self._search_changed('')
        else:
            self._library_bar()

    def _search_place(self,place):
        query='' if self.state.query.casefold()==place.casefold() else place
        field=getattr(self,'_search_field',None)
        if field is not None:
            field.set_text(query)  # its on_change searches
        else:
            self._search_changed(query)

    def _search_changed(self,text):
        self.state.query=text
        self.state.page_offset=0
        self._load()

    def _set_zoom(self,zoom):
        self.state.zoom=zoom
        if zoom=='years' and not self.state.fixture:
            self._load(then=self._scroll_library_to_top)
        else:
            self._render_library()
            GLib.idle_add(self._scroll_library_to_top)

    def _scroll_library_to_top(self):
        self.scroll.get_vadjustment().set_value(0)
        return GLib.SOURCE_REMOVE

    def _zoom_size_changed(self,value):
        # v70's range steps by four pixels. Keep the drag's widget and focus.
        self._size_changed(96+round((value*100-96)/4)*4)
        if self.size_control:self.size_control.set_value(self.state.size/100)

    def _size_changed(self,value):
        self.state.size=max(SIZE_MIN,min(SIZE_MAX_PHONE if self._phone else SIZE_MAX,int(value)))
        minimum=self.state.size*1.55 if self.state.zoom=='months' else self.state.size
        child=self.photo_body.get_first_child()
        while child:
            if isinstance(child,kit.MediaGrid):child.set_min_side(minimum)
            child=child.get_next_sibling()

    def _resize_gestures(self,widget):
        """v71 phone: pinch the grid (or Ctrl-scroll) to make photos bigger or
        smaller, in step with the size slider."""
        pinch=Gtk.GestureZoom()
        pinch.connect('begin',lambda *_:setattr(self,'_pinch_from',self.state.size if self.state else 168))
        pinch.connect('scale-changed',self._pinched)
        widget.add_controller(pinch)
        scroll=Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect('scroll',self._scrolled)
        widget.add_controller(scroll)

    def _pinched(self,_gesture,scale):
        if not self._phone or self.state is None or self.state.zoom=='years':return
        self._size_changed(round(getattr(self,'_pinch_from',self.state.size)*scale))
        if self.size_control:self.size_control.set_value(self.state.size/100)

    def _scrolled(self,controller,_dx,dy):
        if not self._phone or self.state is None or self.state.zoom=='years' or not dy:return False
        if not controller.get_current_event_state()&Gdk.ModifierType.CONTROL_MASK:return False
        self._size_changed(round(self.state.size*(1.06 if dy<0 else 1/1.06)))
        if self.size_control:self.size_control.set_value(self.state.size/100)
        return True

    # ── Memories (phone) ───────────────────────────────────────────────────

    def _memories_row(self,memories):
        """Big swipeable cards, 72% wide and 190 tall; a tap plays one."""
        section=_MemoriesSection()
        section.set_name('ph-memories')
        section.set_margin_bottom(18)
        heading=_typed('Memories','collection-heading',xalign=0)
        heading.set_margin_bottom(10)
        section.append(heading)
        row=Gtk.Box(spacing=10)
        for index,memory in enumerate(memories):
            card=Gtk.Overlay()
            picture=Gtk.Picture(content_fit=Gtk.ContentFit.COVER,can_shrink=True)
            card.set_child(picture)
            self._decode_into(memory.records[0].path,picture,640)
            shade=Gtk.Box(can_target=False)
            shade.add_css_class('ph-memory-shade')
            card.add_overlay(shade)
            words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,valign=Gtk.Align.END,can_target=False)
            words.set_margin_start(14);words.set_margin_end(14);words.set_margin_bottom(12)
            title=_typed(memory.title,'section-title',weight=750,xalign=0,ellipsize=3)
            title.add_css_class('ph-memory-ink')
            words.append(title)
            caption=_typed(f'{memory.when} · {len(memory.records)} photos','meta',xalign=0)
            caption.add_css_class('ph-memory-ink')
            words.append(caption)
            card.add_overlay(words)
            frame=_MemoryFrame(card)
            section.cards.append(frame)
            button=Gtk.Button(child=frame)
            button.add_css_class('ph-memory')
            button.set_overflow(Gtk.Overflow.HIDDEN)
            button.set_name(f'ph-memory-{index}')
            button.update_property([Gtk.AccessibleProperty.LABEL],[f'{memory.title}, {memory.when}, {len(memory.records)} photos'])
            button.connect('clicked',lambda _b,m=memory:self.play_memory(m))
            row.append(button)
        scroller=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,vscrollbar_policy=Gtk.PolicyType.NEVER,
                                    propagate_natural_height=True,child=row)
        scroller.set_margin_bottom(4)
        section.append(scroller)
        self._memories=memories
        return section

    def play_memory(self,memory):
        """Full screen: the photos cross-fade every 3.2 seconds under a
        segmented progress bar; the title and place sit large at the foot.
        Tap the left or right side to step; ✕ closes."""
        self.close_memory()
        player=Gtk.Overlay()
        player.set_name('ph-memory-player')
        player.add_css_class('ph-memory-player')
        stack=Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,transition_duration=800)
        for index,record in enumerate(memory.records):
            picture=Gtk.Picture(content_fit=Gtk.ContentFit.COVER,can_shrink=True)
            self._decode_into(record.path,picture,1600,keep=True)
            stack.add_named(picture,str(index))
        player.set_child(stack)
        bar=Gtk.Box(spacing=4,valign=Gtk.Align.START,can_target=False)
        bar.set_margin_start(16);bar.set_margin_end(16);bar.set_margin_top(50)
        segments=[]
        for _record in memory.records:
            segment=kit.ProgressLine(0,size='tile',tone='neutral')
            segment.set_hexpand(True)
            bar.append(segment);segments.append(segment)
        kit.media_context(bar)
        player.add_overlay(bar)
        words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,valign=Gtk.Align.END,can_target=False)
        words.set_margin_start(20);words.set_margin_end(20);words.set_margin_bottom(60)
        title=_typed(memory.title,'hero',weight=750,xalign=0,wrap=True)
        title.add_css_class('ph-memory-ink')
        words.append(title)
        caption=_typed(f'{memory.place} · {len(memory.records)} photos','lead',xalign=0)
        caption.add_css_class('ph-memory-ink')
        words.append(caption)
        player.add_overlay(words)
        close=make_control(BarAction('x',tooltip='Close',on_activate=self.close_memory))
        close.set_halign(Gtk.Align.END);close.set_valign(Gtk.Align.START)
        close.set_margin_top(62);close.set_margin_end(12)
        close.set_name('ph-memory-close')
        kit.media_context(close)
        player.add_overlay(close)
        # Tap the left or right side to step (v71 .pmemtap: 40% each side).
        for side,label,delta in ((Gtk.Align.START,'Previous',-1),(Gtk.Align.END,'Next',1)):
            tap=Gtk.Button(halign=side,valign=Gtk.Align.FILL,opacity=0)
            tap.update_property([Gtk.AccessibleProperty.LABEL],[label])
            tap.set_tooltip_text(label)
            tap.set_margin_top(90);tap.set_margin_bottom(120)
            tap.connect('clicked',lambda _b,d=delta:self._memory_step(d))
            player.add_overlay(tap)
            taps=getattr(self,'_memory_taps',[]);taps.append(tap);self._memory_taps=taps
        GLib.idle_add(self._size_memory_taps,player)
        self.root.add_overlay(player)
        self._memory={'player':player,'stack':stack,'segments':segments,'index':-1,'timer':0,'tick':0,'started':0}
        self._memory_step(1)

    def _size_memory_taps(self,player):
        width=player.get_width() or self.get_width()
        for tap in getattr(self,'_memory_taps',[]):tap.set_size_request(int(width*.4),-1)
        return GLib.SOURCE_REMOVE

    def _memory_step(self,delta):
        memory=getattr(self,'_memory',None)
        if not memory:return GLib.SOURCE_REMOVE
        count=len(memory['segments'])
        index=(memory['index']+delta)%count
        memory['index']=index
        memory['stack'].set_visible_child_name(str(index))
        for position,segment in enumerate(memory['segments']):
            segment.set_fraction(1 if position<index else 0)
        for key in ('timer','tick'):
            if memory[key]:GLib.source_remove(memory[key])
        memory['started']=GLib.get_monotonic_time()
        memory['timer']=GLib.timeout_add(int(MEMORY_SECONDS*1000),self._memory_advance)
        memory['tick']=GLib.timeout_add(50,self._memory_progress)
        return GLib.SOURCE_REMOVE

    def _memory_advance(self):
        memory=getattr(self,'_memory',None)
        if memory:
            memory['timer']=0  # this source ends by returning; _memory_step clears the tick
            self._memory_step(1)
        return GLib.SOURCE_REMOVE

    def _memory_progress(self):
        memory=getattr(self,'_memory',None)
        if not memory:return GLib.SOURCE_REMOVE
        elapsed=(GLib.get_monotonic_time()-memory['started'])/1e6
        memory['segments'][memory['index']].set_fraction(min(1,elapsed/MEMORY_SECONDS))
        return GLib.SOURCE_CONTINUE

    def close_memory(self):
        memory=getattr(self,'_memory',None)
        if not memory:return
        self._memory=None
        self._memory_taps=[]
        for key in ('timer','tick'):
            if memory[key]:GLib.source_remove(memory[key])
        self.root.remove_overlay(memory['player'])

    def open_photo(self,identifier):
        if self.state is None:
            return
        self.state.open_photo(str(identifier))
        identifier=self.state.viewer
        self._work(lambda:self.state.load_adjustments(identifier),lambda _:self._render_viewer() if self.state.viewer==identifier else None)

    def _viewer_surface(self,editing):
        # Breakpoints retain their setter targets. Keep two fixed layouts so
        # stepping photos or adjusting sliders never retains discarded stages.
        if not hasattr(self,'_viewer_surfaces'):self._viewer_surfaces={}
        if editing in self._viewer_surfaces:return self._viewer_surfaces[editing]
        stage=Gtk.Overlay()
        stage.add_css_class('ph-viewer')
        # The shared viewport owns the black surround and fits within these
        # insets; its allocation remains the whole stage in every appearance.
        viewport=_part('ImageViewport')(surround='black')
        viewport.set_name('ph-image')
        viewport.set_fit_insets(*_viewer_insets(self._phone,editing))
        stage.set_child(viewport)
        titlebox=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,halign=Gtk.Align.START,valign=Gtk.Align.START)
        titlebox.set_margin_start(32); titlebox.set_margin_top(24)
        # v71 phone: the title island names the photo; the stage's own title goes.
        self.phone_breakpoint.add_setter(titlebox,'visible',False)
        titlebox.set_visible(not self._phone)
        title=_typed('','photo-title',fallback='title-2',xalign=0)
        title.add_css_class('ph-viewer-title')
        meta=_typed('','page-meta',fallback='meta',xalign=0)
        meta.add_css_class('ph-viewer-meta')
        meta.set_margin_top(3)
        titlebox.append(title); titlebox.append(meta); stage.add_overlay(titlebox)
        if not editing:
            # v71 phone: swipe to step, tap to hide all the chrome.
            drag=Gtk.GestureDrag()
            drag.connect('drag-end',self._stage_dragged)
            stage.add_controller(drag)
        self._viewer_surfaces[editing]=(stage,viewport,title,meta)
        return self._viewer_surfaces[editing]

    def _render_viewer(self):
        self._image_generation+=1
        state=self.state
        record=state.current
        if record is None:
            self._render_library()
            return
        labels=state.labels(record)
        self.set_phone_bleed(self._phone)
        # v71 phone: the viewer's bar is the ordinary bar (its key stays the filled primary).
        kit.media_context(self.action_center,not state.editing and not self._phone)
        self.stack.set_visible_child_name('viewer')
        stage,self.viewport,title,meta=self._viewer_surface(state.editing)
        title.set_label(record.display_name)
        meta.set_label(' · '.join(filter(None,(record.place,labels.get('day_subtitle'),labels.get('time')))))
        self.viewer_slot.set_child(stage)
        self.viewport.set_paintable(None)
        self.viewport.set_crop_editing(state.editing and state.edit_mode == 'crop',
            on_change=lambda rect: self._adjustment_changed('crop_rect', rect))
        self._decode_into(record.path,self.viewport,2048,values=state.draft if state.editing else state.adjustments.get(record.id,{}))
        if self._phone:
            self._title_island(record,labels)
        if state.editing:
            self.corner_slot.set_child(None)
            self.strip_slot.set_visible(False)
            self.details.show(open=False,subject=record.id)
            self._render_editor()
            return
        self._information(record)
        if self._phone:
            self.corner_slot.set_child(None)
            self._prefetch_share_people()
            self._filmstrip(record)
            self._viewer_bar_phone(record)
            self._apply_immersive()
            if self.state.info:
                self.state.info=False
                GLib.idle_add(lambda:(self._toggle_details(),False)[1])
            return
        self._corner(record)
        index=next(i for i,p in enumerate(state.records) if p.id==record.id)
        position=state.page_offset+index
        pager=kit.BarReadout(f'{position+1} of {state.total}',on_previous=lambda:self._step(-1),on_next=lambda:self._step(1),tone='media')
        self.action_center.show_bar([BarAction('chevron-left',self.title.get_label(),tooltip=f'Back to {self.title.get_label()}',
                                               on_activate=self._close_viewer),pager])
        pager.set_bounds(position>0,position<state.total-1)

    def _leave_viewer_chrome(self):
        """The phone viewer's own chrome goes when the library shows."""
        self.set_phone_bleed(False)
        self._immersive=False
        if getattr(self,'_island',None) is not None:self._island.set_visible(False)
        self.strip_slot.set_child(None);self.strip_slot.set_visible(False)
        self._strip=None
        self.action_center.set_visible(True)

    def _title_island(self,record,labels):
        """v71 phone viewer header: ‹ | the photo's title, "place · day, time".
        It replaces the old "‹ Library" pill and the title under it."""
        when=', '.join(filter(None,(labels.get('day_subtitle'),labels.get('time'))))
        subtitle=' · '.join(filter(None,(record.place,when)))
        island=getattr(self,'_island',None)
        if island is None:
            island=kit.TitleIsland(record.display_name,subtitle,lead='back',
                                   lead_label=f'Back to {self.title.get_label()}',on_lead=self._go_back)
            island.set_name('ph-title-island')
            island.float_over(self)
            self._island=island
        else:
            island.set_title(record.display_name,subtitle)
            island.set_lead('back',f'Back to {self.title.get_label()}')
        island.set_visible(not self._immersive)
        return island

    def _viewer_bar_phone(self,record):
        """Edit (primary, labelled, first), Share, Favorite (red when on),
        Details, More. No pager and no corner island: swipe or the filmstrip."""
        favourite=record.favorite
        self.action_center.show_bar([
            BarAction('sliders-horizontal','Edit',primary=True,keep_label=True,fill=True,on_activate=self._begin_edit),
            BarAction(icons.SHARE,tooltip='Share',panel=self._share_panel,key='share'),
            BarAction('heart',tooltip='Remove from Favorites' if favourite else 'Add to Favorites',
                      on_activate=lambda:self._favourite(record.id,not favourite)),
            BarAction('info',tooltip='Details',panel=self._details_panel,key='info'),
            BarAction('ellipsis',tooltip='More',panel=self._more_panel,key='more')],fill=True)
        for label,name in (('Edit','ph-edit'),('Share','ph-share'),('Details','ph-info'),('More','ph-more')):
            self._name_bar_action(label,name)
        self._name_bar_action('Remove from Favorites' if favourite else 'Add to Favorites','ph-favorite')
        heart=self._bar_anchor('ph-favorite')
        heart.add_css_class('ph-favorite')  # v71 .faved: the heart turns red; the button is not raised
        if favourite:heart.add_css_class('ph-faved')

    def _details_panel(self):
        """Details grows the photo's card and its facts."""
        record=self.state.current
        labels=self.state.labels(record)
        index=next((i for i,p in enumerate(self.state.records) if p.id==record.id),0)+self.state.page_offset
        rows=[kit.PanelRow(record.display_name,lead=self._thumbnail(record.path,56),closes=False,
                           subtitle=' · '.join(filter(None,(record.place,f'{index+1} of {self.state.total}'))))]
        for key,value in self._facts(record,labels):
            rows.append(kit.PanelRow(key,detail=value,closes=False))
        panel=kit.panel_list(rows,label='Details')
        panel.set_name('ph-details-panel')
        return panel

    def _toggle_details(self):
        if self._phone:
            self._grow('info',self._details_panel,'ph-info')
            return
        record=self.state.current
        if record is None:return
        self.details.show(open=not self.details.shown,subject=record.id)
        self.state.info=self.details.shown

    def _share_panel(self):
        """Share grows people, then Messages, Email, Copy, Nearby."""
        people=getattr(self,'_share_people',None)
        if people is None:
            people=[kit.Person(raw['name'],username=raw['username'],hue=raw['hue'])
                    for raw in self._share_people_raw()]
        targets=(('message-square','Messages','messages'),('mail','Email','mail'),('copy','Copy','copy'),
                 ('radio-tower','Nearby','nearby'))
        panel=kit.SharePanel(people=people,on_choice=self._share_panel_choice,targets=targets)
        panel.set_name('ph-share-panel')
        return panel

    def _share_people_raw(self):
        # The fixture's five recent faces (v71 Photos: Priya, Nora, Sam, Theo, Dad).
        return self.state.fixture.document.get('share_panel_people',[]) if self.state.fixture else []

    def _prefetch_share_people(self):
        """Decode the share panel's faces once, off the main loop."""
        if getattr(self,'_share_people_asked',False) or not self._share_people_raw():return
        self._share_people_asked=True
        fixture=self.state.fixture
        rows=self._share_people_raw()
        def read():
            result=[]
            for raw in rows:
                face=raw.get('face');picture=None
                if face:
                    path=(fixture.asset_root/face['image']).resolve()
                    if path.is_relative_to(fixture.asset_root):
                        picture=decode_face(path,face['x'],face['y'],face['zoom'])
                result.append((raw,picture))
            return result
        def done(result):
            self._share_people=[kit.Person(raw['name'],username=raw['username'],hue=raw['hue'],
                                           picture=Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf else None)
                                for raw,pixbuf in result]
        self._work(read,done,on_error=lambda _m:True)

    def _share_panel_choice(self,choice,value):
        record=self.state.current
        if record is None:return ''
        if choice=='copy':
            if not self.state.fixture:
                self.get_clipboard().set(Gdk.FileList.new_from_list([Gio.File.new_for_path(str(record.path))]))
            return 'Photo copied'
        if self.state.fixture:
            if choice=='send-to':return f'Sent to {value.name.split()[0]}'
            return {'messages':'Opening Messages','mail':'Opening a new email','nearby':'Looking for devices nearby'}.get(choice,'')
        app={'messages':'org.projectluma.Messages','mail':'org.projectluma.Charlie'}.get(choice)
        if choice=='send-to':app='org.projectluma.Messages'
        info=next((a for a in Gio.AppInfo.get_all() if app and a.get_id()==app+'.desktop'),None)
        if info is None:return 'That app is not available'
        try:info.launch([Gio.File.new_for_path(str(record.path))],self.get_display().get_app_launch_context())
        except GLib.Error as failure:return failure.message
        return ''

    def _filmstrip(self,record):
        """A filmstrip above the bar scrubs; the current photo is larger and outlined."""
        records=self.state.records
        index=next(i for i,p in enumerate(records) if p.id==record.id)
        strip=getattr(self,'_strip',None)
        if strip is None or strip['records'] is not records:
            row=Gtk.Box(spacing=2,valign=Gtk.Align.CENTER)
            tiles={}
            for photo in records:
                picture=Gtk.Picture(content_fit=Gtk.ContentFit.COVER,can_shrink=True)
                button=Gtk.Button(child=picture,valign=Gtk.Align.CENTER)
                button.add_css_class('ph-strip-photo')
                button.set_overflow(Gtk.Overflow.HIDDEN)
                button.update_property([Gtk.AccessibleProperty.LABEL],[photo.display_name])
                button.connect('clicked',lambda _b,i=photo.id:self.open_photo(i))
                row.append(button)
                tiles[photo.id]=(button,picture)
            scroller=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,vscrollbar_policy=Gtk.PolicyType.NEVER,
                                        child=row,height_request=48)
            scroller.set_name('ph-strip')
            scroller.set_margin_bottom(104)
            strip={'records':records,'row':row,'tiles':tiles,'scroller':scroller,'decoded':set()}
            self._strip=strip
            self.strip_slot.set_child(scroller)
        # Decode what is near the current photo; the rest as it comes near.
        for photo in records[max(0,index-24):index+25]:
            if photo.id not in strip['decoded']:
                strip['decoded'].add(photo.id)
                self._decode_into(photo.path,strip['tiles'][photo.id][1],96,square=True,keep=True)
        for identifier,(button,_picture) in strip['tiles'].items():
            on=identifier==record.id
            button.set_size_request(48 if on else 30,48 if on else 40)
            button.set_margin_start(4 if on else 0);button.set_margin_end(4 if on else 0)
            if on:button.add_css_class('on')
            else:button.remove_css_class('on')
        self.strip_slot.set_visible(not self._immersive)
        GLib.idle_add(self._centre_strip,record.id)

    def _centre_strip(self,identifier):
        strip=getattr(self,'_strip',None)
        if not strip or identifier not in strip['tiles']:return GLib.SOURCE_REMOVE
        scroller,row=strip['scroller'],strip['row']
        width=scroller.get_width()
        if width<=0:return GLib.SOURCE_REMOVE
        # Room either side so the first and last photos can sit in the middle.
        row.set_margin_start(max(0,width//2-20));row.set_margin_end(max(0,width//2-20))
        button=strip['tiles'][identifier][0]
        found,bounds=button.compute_bounds(row)
        if found:
            scroller.get_hadjustment().set_value(row.get_margin_start()+bounds.get_x()-width/2+bounds.get_width()/2)
        return GLib.SOURCE_REMOVE

    def _stage_dragged(self,_gesture,dx,dy):
        if not self._phone or self.state is None or self.state.current is None or self.state.editing:return
        if abs(dx)>40 and abs(dx)>abs(dy):
            self._fold()
            self._step(1 if dx<0 else -1)
        elif abs(dx)<8 and abs(dy)<8:
            if self._panel or self.details.shown:
                self._fold()
                if self.details.shown:self._toggle_details()
            else:
                self._immersive=not self._immersive
                self._apply_immersive()

    def _apply_immersive(self):
        """Tapping the photo hides all the chrome; another tap brings it back."""
        hidden=self._immersive and self._phone
        if getattr(self,'_island',None) is not None:self._island.set_visible(not hidden)
        self.strip_slot.set_visible(not hidden and self.strip_slot.get_child() is not None)
        self.action_center.set_visible(not hidden)

    def _corner(self,record):
        self.corner=CornerPill(share=self._share,states=[('heart','Remove from Favorites' if record.favorite else 'Add to Favorites',record.favorite,
                                                          lambda active:self._favourite(record.id,active))],
                               info=self.details,actions=[('pencil','Edit',self._begin_edit)],primary='Edit',more=self._more_commands())
        self.corner.set_name('ph-corner')
        self.corner.controls['more'].set_name('ph-more')
        self.corner_slot.set_child(self.corner)

    def _information(self,record):
        labels=self.state.labels(record)
        self.details.clear()
        self.details.add_hero(record.display_name)
        self.details.add_facts(self._facts(record,labels))
        wanted=self.state.info
        if self._phone:
            # A phone's Details grows the bar (_details_panel); the pane stays home.
            self.details.show(open=False,subject=record.id)
            self.state.info=wanted
            return
        self.details.show(open=wanted,subject=record.id)
        self.state.info=wanted

    def _facts(self,record,labels):
        facts=[('Taken',f"{labels.get('day_subtitle','')}, {labels.get('time','')}")]
        if record.place: facts.append(('Place',record.place))
        for key in ('camera','stored','backup'):
            value=labels.get(key)
            if key=='stored' and self._phone and self.state.fixture and value=='This computer':
                value='This phone'  # the fixture is v71's sample data, seen on its phone
            if value: facts.append((key.title(),value))
        if self.state.adjustments.get(record.id):
            facts.append(('Edits','Yes · original kept' if not self._phone else 'Original kept'))
        return facts

    def _details_visibility_changed(self,pane,_property):
        # Edit temporarily hides Information; v70 retains its open wish.
        if self.state and not self.state.editing:
            self.state.info=pane.shown

    def _details_closed(self):
        if self.state and not self.state.editing:
            self.state.info=False

    def _step(self,delta):
        destination=self.state.step_destination(delta)
        if destination is None:return
        offset,index=destination
        if offset==self.state.page_offset:
            if self.state.step(delta):self.open_photo(self.state.viewer)
            return
        wanted=self.state.info
        self.state.page_offset=offset
        def opened():
            if index<len(self.state.records):
                self.state.info=wanted
                self.open_photo(self.state.records[index].id)
        self._load(then=opened)

    def _close_viewer(self):
        self._image_generation+=1
        self._fold()
        self.state.close_viewer()
        self._render_library()
        self._load()

    def _go_back(self):
        if getattr(self,'_memory',None):
            self.close_memory();return True
        if not self.state or not self.state.current:return False
        if self.state.editing:self._cancel()
        else:self._close_viewer()
        return True

    def _more_commands(self):
        return CommandRegistry((CommandGroup('',(
            Command('photos.open-in','Open in…',self._open_in,'app-window'),
            Command('photos.add-to-album','Add to album…',self._add_to_album,'folder-plus'))),
            CommandGroup('',(Command('photos.delete','Delete',self._delete,'trash-2',destructive=True),))))

    def _more_anchor(self):
        corner=getattr(self,'corner',None)
        if not self._phone and corner is not None and corner.get_root() is not None:
            return corner.controls['more']
        return self._bar_anchor('ph-more')

    def _more_panel(self):
        """⋯ grows Add to album, Use as wallpaper, Duplicate, Save to Files and
        Delete photo (red, which confirms in the bar)."""
        rows=[kit.PanelRow('Add to album',icon='folder-plus',on_activate=self._add_to_album)]
        if self.state.fixture:
            # v71's two new writes (a desktop background, a second copy in the
            # library) exist against the fixture only, until Nick says yes.
            rows+=[kit.PanelRow('Use as wallpaper',icon='wallpaper',on_activate=lambda:Toast.show(self.host,'Set as your wallpaper')),
                   kit.PanelRow('Duplicate',icon='copy',on_activate=lambda:Toast.show(self.host,'Duplicated'))]
        rows+=[kit.PanelRow('Save to Files',icon='download',on_activate=self._save_to_files),
               kit.PanelRow('Delete photo',icon='trash-2',danger=True,closes=False,on_activate=self._confirm_delete)]
        panel=kit.panel_list(rows,label='More')
        panel.set_name('ph-more-panel')
        return panel

    def _more_phone(self):
        self._grow('more',self._more_panel,'ph-more')

    def _share_phone(self):
        self._grow('share',self._share_panel,'ph-share')

    def _save_to_files(self):
        record=self.state.current
        if record is None:return
        if self.state.fixture:
            Toast.show(self.host,'Saved to Files',kind='saved')
            return
        self._save_copy(record)

    def _confirm_delete(self):
        """Phone: Delete photo confirms in the bar (v71 LumaUI 9)."""
        kit.DestructiveDialog.in_bar(self.action_center,title='Delete this photo?',
                                     body='It stays in Deleted for 30 days, then it’s gone from every device.',
                                     action='Delete',on_confirm=self._delete,on_cancel=self._more_phone,
                                     anchor=self._bar_anchor('ph-more'),key='delete')

    def _favourite(self,identifier,active):
        def saved(_):
            if self.state.viewer==identifier:
                self._render_viewer()
            Toast.show(self.host,'Added to Favorites' if active else 'Removed from Favorites',kind='favourite' if active else 'unfavourite',
                       undo=None if active else lambda:self._favourite(identifier,True))
        self._work(lambda:self.state.set_favourite(identifier,active),saved)

    def _begin_edit(self):
        if self.state.begin_edit():
            self._edit_session+=1
            self._render_viewer()

    def _edit_controls(self):
        mode=self.state.edit_mode
        if mode in ('light','colour'):
            keys=(('e','Exposure'),('c','Contrast'),('hi','Highlights'),('sh','Shadows')) if mode=='light' else (('w','Warmth'),('sat','Saturation'))
            # Retain the controls so breakpoint setters and range drags retain
            # their targets across mode changes and asynchronous image loads.
            if not hasattr(self,'_slider_groups'):self._slider_groups={}
            if mode not in self._slider_groups:
                group=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
                group.set_margin_start(18);group.set_margin_end(18)
                group.set_margin_top(12);group.set_margin_bottom(12)
                for start in range(0,len(keys),2):
                    row=Gtk.Box(spacing=20,homogeneous=True)
                    self.phone_breakpoint.add_setter(row,'orientation',Gtk.Orientation.VERTICAL)
                    self.phone_breakpoint.add_setter(row,'spacing',12)
                    if self.get_current_breakpoint()==self.phone_breakpoint:
                        row.set_orientation(Gtk.Orientation.VERTICAL);row.set_spacing(12)
                    for key,label in keys[start:start+2]:
                        slider=kit.ValueSlider(label,layout='editor',on_change=lambda value,k=key:self._adjustment_changed(k,value))
                        slider.set_name(f'ph-adjust-{key}');slider.set_hexpand(True)
                        self._sliders[key]=slider;row.append(slider)
                    group.append(row)
                self._slider_groups[mode]=group
            for key,_ in keys:self._sliders[key].set_value(self.state.draft.get(key,0))
            return self._slider_groups[mode]
        if mode=='crop':
            # The shared viewport owns the direct crop frame and handles.
            group=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
            group.set_margin_start(18);group.set_margin_end(18)
            group.set_margin_top(8);group.set_margin_bottom(8)
            choices=(('rotate-ccw','Rotate',self._rotate),('flip-horizontal-2','Flip',self._flip),
                     ('crop','Crop',self._toggle_crop_shapes))
            if self._phone:
                # v71 phone: three large tiles, an icon over the word.
                tools=kit.BarTiles([kit.BarTile(icon,label,callback,closes=False,name=f'ph-crop-{label.lower()}')
                                    for icon,label,callback in choices],columns=3,size='compact')
            else:
                tools=Gtk.Box(spacing=8,homogeneous=True)
                for icon,label,callback in choices:
                    tool=make_control(BarAction(icon,label,on_activate=callback))
                    tool.set_name(f'ph-crop-{label.lower()}')
                    tools.append(tool)
            group.append(tools)
            if getattr(self,'_crop_shapes',False):
                ratios=Gtk.Box(spacing=8,homogeneous=True)
                for value,label in (('original','Original'),('square','Square'),('4:3','4:3'),('16:9','16:9')):
                    ratios.append(make_control(BarAction('',label,active=self.state.draft.get('crop','original')==value,
                                                         on_activate=lambda chosen=value:self._choose_crop(chosen))))
                group.append(ratios)
            group.add_css_class('ph-crop-actions')
            return group
        if not hasattr(self,'_preset_grid'):
            self._preset_grid=Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,min_children_per_line=6,max_children_per_line=6,column_spacing=8,row_spacing=8,homogeneous=True)
            self.phone_breakpoint.add_setter(self._preset_grid,'min-children-per-line',3)
            self.phone_breakpoint.add_setter(self._preset_grid,'max-children-per-line',3)
            if self.get_current_breakpoint()==self.phone_breakpoint:
                self._preset_grid.set_min_children_per_line(3);self._preset_grid.set_max_children_per_line(3)
            self._preset_grid.add_css_class('ph-preset-grid')
            self._preset_grid.set_margin_start(18)
            self._preset_grid.set_margin_end(18)
            self._preset_grid.set_margin_top(8)
            self._preset_grid.set_margin_bottom(8)
        row=self._preset_grid
        _clear(row)
        for key,label in (('','Original'),('vivid','Vivid'),('warm','Warm'),('cool','Cool'),('mono','Mono'),('fade','Fade')):
            tile=kit.MediaTile(self.state.current.path,title=label,caption=label,selected=self.state.draft.get('pre','')==key,
                               on_activate=lambda k=key:self._choose_look(k))
            tile.set_name(f'ph-look-{key or "original"}')
            row.append(tile)
        return row

    def _choose_look(self,key):
        self._adjustment_changed('pre',key)
        self._render_editor()

    def _render_editor(self):
        """Edit turns the bar into the editor (LumaUI action center). Desktop:
        Edit · Looks/Light/Color/Crop · Auto over the tools, then Revert to
        original · Cancel · Done. Phone (the bar grown, LumaUI 15): Cancel ·
        Edit · Done, the tools, then the four tools and Auto, Revert at the foot."""
        old=getattr(self,'editor',None)
        if old is not None:old.scroller.set_child(None)
        tools=self._edit_controls()
        from .photos_adjustments import KEYS
        changed=any(key in self.state.draft for key in KEYS)
        auto=BarAction('wand-sparkles','Auto',tooltip='Let Photos adjust it',on_activate=self._auto)
        revert=BarAction('rotate-ccw','Revert to original',sensitive=changed,on_activate=self._revert)
        options=dict(body=tools,modes=list(EDIT_MODES),mode=self.state.edit_mode,on_mode=self._edit_mode,
                     primary=BarAction('','Done',primary=True,on_activate=self._done),on_discard=self._cancel)
        options.update(tools=[auto],revert=revert)
        self.editor=ActionEditor('Edit','sliders-horizontal',**options)
        self.editor.set_name('ph-editor')
        self.action_center.set_editor(self.editor)
        self.action_center.grow()

    def _adjustment_changed(self,key,value):
        from .photos_adjustments import apply_changes
        self.state.draft=apply_changes(self.state.draft,{key:value})
        self.viewport.set_adjustments(self.state.draft)

    def _edit_mode(self,mode):
        self.state.edit_mode=mode
        self.viewport.set_crop_editing(mode == 'crop',
            on_change=lambda rect: self._adjustment_changed('crop_rect', rect))
        self._render_editor()

    def _revert(self):
        self.state.revert_edit()
        self._render_viewer()

    def _rotate(self):
        self._adjustment_changed('rot',(self.state.draft.get('rot',0)-90)%360)

    def _flip(self):
        self._adjustment_changed('flip',not self.state.draft.get('flip',False))

    def _toggle_crop_shapes(self):
        self._crop_shapes=not getattr(self,'_crop_shapes',False)
        self._render_editor()

    def _choose_crop(self,value):
        self._adjustment_changed('crop_rect',None)
        self._adjustment_changed('crop',value)
        self._render_editor()

    def _auto(self):
        self.state.auto_adjust()
        self._render_viewer()

    def _cancel(self):
        self._edit_session+=1
        self.state.cancel_edit()
        self.action_center.fold()
        self._render_viewer()

    def _done(self):
        identifier=self.state.viewer
        session=self._edit_session
        before,after=dict(self.state.baseline),dict(self.state.draft)
        def saved(values):
            self.state.adjustments[identifier]=values
            if self.state.viewer!=identifier or session!=self._edit_session:return
            self.state.editing=False
            self.action_center.fold()
            self._render_viewer()
            Toast.show(self.host,'Saved. Revert to the original any time from Edit.',kind='saved')
        self._work(lambda:self.state.save_edit(identifier,before,after),saved)

    def request_import(self):
        if self.state is None:return
        registry=CommandRegistry((CommandGroup('',(
            Command('photos.import-folder','From a folder…',self._import_folder,'folder'),
            Command('photos.import-card','From a memory card',lambda:None,'hard-drive',enabled=lambda:False))),))
        sections=[]
        if self.state.fixture:
            sections.append(_part('MenuSection')(self._import_card()))
        self._show_menu(self._bar_anchor('ph-import'),registry,sections=sections)

    def _import_card(self):
        data=self.state.fixture.document['import']
        column=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,halign=Gtk.Align.START)
        column.set_size_request(288,-1)
        column.set_margin_start(6);column.set_margin_end(6);column.set_margin_bottom(4)
        heading=_typed('Import','label',xalign=0)
        heading.set_margin_start(4);heading.set_margin_end(4)
        heading.set_margin_top(4);heading.set_margin_bottom(8)
        column.append(heading)
        row=Gtk.Box(spacing=10)
        row.set_margin_start(4);row.set_margin_end(4)
        row.append(icons.image('smartphone',pixel_size=20))
        words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        words.append(_typed(data['device'],'body',weight=700,xalign=0))
        words.append(_typed(f"{data['count']} new photos since {data['since']}",'caption',weight=400,xalign=0))
        row.append(words); column.append(row)
        strip=Gtk.Box(spacing=4,homogeneous=True)
        strip.set_margin_top(10);strip.set_margin_bottom(10)
        for identifier in data['photos']:
            picture=Gtk.Picture(content_fit=Gtk.ContentFit.COVER,can_shrink=True)
            picture.add_css_class('ph-import-thumb')
            # This fixture-only menu has four bounded 54 px crops. Decode
            # them before showing the menu so a capture never sees empty
            # cells while the real library's background decodes are busy.
            path=self.state.source.asset(identifier).path
            if path.is_file():
                picture.set_paintable(Gdk.Texture.new_for_pixbuf(decode_square(path,54)))
            strip.append(Gtk.AspectFrame(ratio=1,obey_child=False,child=picture))
        more=_typed(f"+{data['remaining']}",'meta',weight=600,xalign=.5,yalign=.5)
        more.set_size_request(54,54)
        more.add_css_class('ph-import-more')
        strip.append(more)
        column.append(strip)
        column.append(make_control(BarAction('',f"Import all {data['count']}",primary=True,
                        on_activate=self._import_all_fixture)))
        return column

    def _import_all_fixture(self):
        if not self.state.fixture:return
        self._dismiss_menu()
        data=self.state.fixture.document['import']
        Toast.show(self.host,f"Importing {data['count']} photos from {data['device']}")

    def _clipboard_photo(self):
        if self.state.fixture:
            return self.state.fixture.document.get('clipboard')
        formats=self.get_clipboard().get_formats()
        return formats.contain_gtype(Gdk.FileList) or formats.contain_mime_type('text/uri-list')

    def _drive(self):
        """An attached drive or card with a camera folder: (name, photos, DCIM)."""
        if self.state.fixture:
            raw=self.state.fixture.document.get('drive')
            return (raw['name'],raw['count'],None) if raw else None
        return getattr(self,'_attached_drive',None)

    def _watch_drives(self):
        if self.state is None or self.state.fixture or hasattr(self,'_volume_monitor'):return
        self._volume_monitor=Gio.VolumeMonitor.get()
        for signal in ('mount-added','mount-removed','mount-changed'):
            self._volume_monitor.connect(signal,lambda *_:self._find_drive())
        self._find_drive()

    def _find_drive(self):
        """Read-only: count the pictures under a removable mount's DCIM."""
        roots=[]
        for mount in self._volume_monitor.get_mounts():
            root=mount.get_root().get_path()
            if root and (mount.can_eject() or mount.can_unmount()) and (Path(root)/'DCIM').is_dir():
                roots.append((mount.get_name(),Path(root)/'DCIM'))
        def count():
            for name,root in roots:
                total=sum(1 for path in _walk_files(root,lambda:self._closed) if path.suffix.casefold() in IMAGE_SUFFIXES)
                if total:return name,total,root
            return None
        def found(drive):
            self._attached_drive=drive
        self._work(count,found,on_error=lambda _message:True)

    def _import_drive(self):
        self._fold()
        drive=self._drive()
        if drive is None:return
        name,count,root=drive
        if self.state.fixture or root is None:
            Toast.show(self.host,f'Adding {count} photos from {name}',kind='added')
            return
        self._import_paths(lambda:_walk_files(root,lambda:self._closed),f'from {name}')

    def _paste(self):
        self._fold()
        if self.state.fixture:
            Toast.show(self.host,'Added the photo you copied',kind='added')
            return
        def read(clipboard,result):
            try:files=clipboard.read_value_finish(result)
            except GLib.Error as error:
                Toast.show(self.host,error.message,kind='error');return
            paths=[Path(f.get_path()) for f in files.get_files() if f.get_path()]
            if paths:self._import_paths(lambda:iter(paths),'you copied')
        self.get_clipboard().read_value_async(Gdk.FileList,GLib.PRIORITY_DEFAULT,None,read)

    def _add_from_files(self):
        """From Files: the system picker asks for Photos ("Add photos",
        starting in Pictures, images only); the outcome is a toast here. The
        picker's phone layout is Filer's portal chooser (kit-requests/rows-01)."""
        self._fold()
        if self.state.fixture:
            # Fixture mode never opens a real chooser; it records the request.
            self._fixture_files_requested=True
            return
        kit.ask_for_file(self,kit.FileRequest('Add photos',start='pictures',show='images',many=True),self._files_chosen)

    def _files_chosen(self,files):
        if not files:return  # Cancel: back to Photos, nothing said
        paths=[Path(f.get_path()) for f in files if f.get_path()]
        if paths:self._import_paths(lambda:iter(paths),'')

    def _import_paths(self,paths,where):
        """The existing import write (copies into the library), nothing new."""
        def imported(result):
            self._load()
            count=len(result.completed)
            text=f"Added {count} {'photo' if count==1 else 'photos'}"+(f' {where}' if where else '')
            if result.errors:text+=' · '+'; '.join(result.errors)
            Toast.show(self.host,text,kind='error' if result.errors else 'added')
        self._work(lambda:self.state.source.import_files(paths()),imported)

    def request_new_album(self):
        if self.state is None:return
        if self._phone:
            self._new_album_panel()
            return
        field=TextField('Album name',placeholder='Name',on_activate=self._create_album)
        body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8,halign=Gtk.Align.START)
        body.set_size_request(228,-1)
        body.set_margin_start(6);body.set_margin_end(6);body.set_margin_bottom(2)
        heading=_typed('New album','label',xalign=0)
        heading.set_margin_start(2);heading.set_margin_end(2);heading.set_margin_top(2)
        body.append(heading);body.append(field)
        create=make_control(BarAction('','Create',primary=True,on_activate=lambda:self._create_album(field.text)))
        body.append(create)
        self._show_menu(self.new_album_button,CommandRegistry(),sections=[_part('MenuSection')(body)])

    def _create_album(self,name):
        name=name.strip() or 'New album'
        def created(album):
            self._dismiss_menu()
            self.state.switch_view(album.id)
            self._load()
        self._work(lambda:self.state.source.create_album(name),created)

    def _add_to_album(self):
        identifier=self.state.viewer
        if self.state.fixture:
            launch=next(album for album in self.state.albums if album.name=='Launch')
            self.state.source.add_to_album(launch.id,(identifier,))
            Toast.show(self.host,'Added to Launch',kind='added')
            return
        commands=[Command(f'album.{album.id}',album.name,lambda a=album:self._work(
            lambda:self.state.source.add_to_album(a.id,(identifier,)),lambda _:Toast.show(self.host,f'Added to {a.name}',kind='added')), 'image') for album in self.state.albums]
        commands.append(Command('album.new','New album',self.request_new_album,'plus'))
        self._show_menu(self._more_anchor(),CommandRegistry((CommandGroup('',tuple(commands)),)))

    def _delete(self):
        identifier=self.state.viewer
        state=self.state
        # Existing reversible trash path: no permanent delete, no original
        # pixel write. The Undo callback restores exactly the affected copies.
        remaining=[p.id for p in state.records if p.id!=identifier]
        index=next((i for i,p in enumerate(state.records) if p.id==identifier),0)
        successor=remaining[min(index,len(remaining)-1)] if remaining else None
        def deleted(result):
            state.close_viewer()
            self._load(then=lambda:self.open_photo(successor) if successor and any(p.id==successor for p in state.records) else None)
            Toast.show(self.host,'; '.join(result.errors) if result.errors else 'Moved to Deleted',
                       kind='error' if result.errors else 'deleted',undo=self._undo_delete if state._undo else None)
        self._work(lambda:state.trash(identifier),deleted)

    def _undo_delete(self):
        def failed(message):
            if self._closed:return True
            return Toast.show(self.host,message,kind='error',undo=self._undo_delete if self.state._undo else None)
        self._work(self.state.undo_trash,lambda identifier:self._load(then=lambda:self.open_photo(identifier)) if identifier else None,on_error=failed)


    def _open_in(self):
        record=self.state.current
        # The shared OpenInMenu presents ranked owning apps and their icons.
        options={}
        if self.state.fixture:
            options=dict(on_open=lambda app:Toast.show(self.host,f'Opening {record.display_name} in {app.get_name()}',kind='opening'),
                         on_other=lambda:Toast.show(self.host,'Choose an app to open it with'))
        self.open_in_menu=_part('OpenInMenu')(str(record.path),**options)
        self.open_in_menu.popup(self._more_anchor())

    def _share(self,button):
        record=self.state.current
        if record is None:return
        fixture=self.state.fixture
        def read():
            photo=decode_photo(record.path,128)
            people=[]
            for raw in fixture.document.get('share_people',[]) if fixture else ():
                face=raw.get('face')
                picture=None
                if face:
                    path=(fixture.asset_root/face['image']).resolve()
                    if not path.is_relative_to(fixture.asset_root):raise ValueError('Fixture face is outside its asset root')
                    picture=decode_face(path,face['x'],face['y'],face['zoom'])
                people.append((raw,picture))
            return photo,people
        generation=self._image_generation
        future=self.executor.submit(read)
        def ready(future):
            try:result,error=future.result(),''
            except Exception as failure:result,error=None,str(failure)
            GLib.idle_add(self._share_ready,generation,button,record,result,error)
        future.add_done_callback(ready)

    def _share_ready(self,generation,button,record,result,error):
        if self._closed or generation!=self._image_generation:return GLib.SOURCE_REMOVE
        if error:
            Toast.show(self.host,error,kind='error');return GLib.SOURCE_REMOVE
        photo,rows=result
        people=[kit.Person(raw['name'],username=raw['username'],hue=raw['hue'],
                           picture=Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf else None) for raw,pixbuf in rows]
        self._share_record=record
        document=kit.ShareSubject(self.title.get_label(),'Photos',kind='file',image=True,
                                   mime_type=record.mime_type if not self.state.fixture else None,
                                   picture=Gdk.Texture.new_for_pixbuf(photo))
        from luma_appkit.bar_share import default_targets
        self.share_sheet=kit.ShareSheet.present(button,document=document,people=people,
            targets=default_targets(document) if self.state.fixture else None,
            show_link=self.state.fixture,on_choice=self._share_choice)
        return GLib.SOURCE_REMOVE

    def _share_choice(self,choice,value):
        record=self._share_record
        fixture=self.state.fixture
        if choice=='copy':
            if not fixture:
                self.get_clipboard().set(Gdk.FileList.new_from_list([Gio.File.new_for_path(str(record.path))]))
            return f'{self.title.get_label()} copied. Paste it into any folder or message.'
        if fixture:
            if choice=='send-to':return f'Sent to {value.name.split()[0]} in Messages'
            if choice=='copy-link':return 'Link copied'
            if choice=='save':return 'Choose where to save the copy'
            if choice=='print':return f'Printing {self.title.get_label()} on Brother HL-L2350DW'
            if choice=='target':return {'messages':'A new message with Library attached','mail':'A new email with Library attached',
                                        'notes':'Added to a new note','photos':'Added to Photos','canvas':'Placed on a new board in Canvas',
                                        'ari':'Ari has it. Ask what you like.'}.get(value,'')
            return ''
        if choice=='save':self._save_copy(record);return ''
        if choice=='print':self._print_photo(record);return ''
        if choice=='target':
            target=next((target for target in self.share_sheet.targets if target.key==value),None)
            app=next((app for app in Gio.AppInfo.get_all() if target and app.get_id()==target.app_id+'.desktop'),None)
            if app is None:return kit.ShareResult(False,'This app is not available')
            try:app.launch([Gio.File.new_for_path(str(record.path))],self.get_display().get_app_launch_context())
            except GLib.Error as failure:return kit.ShareResult(False,failure.message)
            return kit.ShareResult(True)
        if choice=='copy-link':return kit.ShareResult(False,'This photo does not have a sharing link')
        return kit.ShareResult(False)

    def _print_photo(self,record):
        dialog=Gtk.PrintDialog(title=record.display_name)
        def prepared(dialog,result):
            try:setup=dialog.setup_finish(result)
            except GLib.Error:return
            page=setup.get_page_setup()
            width=page.get_paper_width(Gtk.Unit.POINTS)
            height=page.get_paper_height(Gtk.Unit.POINTS)
            temporary=tempfile.TemporaryDirectory(prefix='photos-print-')
            def printed(dialog,result):
                try:
                    dialog.print_file_finish(result)
                    if not self._closed:Toast.show(self.host,'Sent to printer')
                except GLib.Error as failure:
                    if not self._closed:Toast.show(self.host,failure.message,kind='error')
                finally:temporary.cleanup()
            def ready(path):
                if self._closed:temporary.cleanup();return
                dialog.print_file(self,setup,Gio.File.new_for_path(str(path)),None,printed)
            def failed(message):
                temporary.cleanup()
            self._work(lambda:prepare_print_pdf(record.path,Path(temporary.name)/'photo.pdf',width,height),ready,on_error=failed)
        dialog.setup(self,None,prepared)

    def _save_copy(self,record):
        dialog=Gtk.FileDialog(title='Save a copy')
        def chosen(dialog,result):
            try:
                folder=dialog.select_folder_finish(result).get_path()
                if folder is None:return
            except GLib.Error:return
            def saved(result):
                Toast.show(self.host,'; '.join(result.errors) if result.errors else 'Saved a copy',
                           kind='error' if result.errors else 'saved')
            self._work(lambda:self.library.export_assets((record.id,),Path(folder),cancelled=lambda:self._closed,
                                                        render_photo=self._render_export_copy),saved)
        dialog.select_folder(self,None,chosen)

    def _render_export_copy(self,path,values):
        """Worker decode, then the same actual GTK renderer that owns preview."""
        pixbuf=decode_export_photo(path)
        finished=threading.Event()
        outcome={}
        deadline=time.monotonic()+30
        def render():
            if finished.is_set():return GLib.SOURCE_REMOVE
            try:
                if self._closed or time.monotonic()>=deadline:
                    raise RuntimeError('Photos closed or image export timed out')
                node,bounds=edited_photo_node(pixbuf,values)
                outcome['texture']=self.get_renderer().render_texture(node,bounds)
            except Exception as error:outcome['error']=error
            finally:finished.set()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(render)
        while not finished.wait(.1):
            if self._closed or time.monotonic()>=deadline:
                finished.set()
                raise RuntimeError('Photos closed or image export timed out')
        if 'error' in outcome:raise outcome['error']
        if self._closed:raise RuntimeError('Photos closed while exporting')
        # Gdk.Texture is immutable/threadsafe. Encoding stays in this catalog
        # worker rather than blocking GTK during a large lossless PNG write.
        return bytes(outcome['texture'].save_to_png_bytes().get_data())

    def _show_menu(self,anchor,registry,*,sections=()):
        self._dismiss_menu()
        rows=list(sections)
        enabled=[]
        for group in registry.visible_groups():
            if rows:rows.append(None)
            if group.label:rows.append(group.label)
            for command in group.commands:
                if not command.visible():continue
                row=kit.RichMenuItem(command.label,icon=command.icon,danger=command.destructive,
                                     on_activate=lambda c=command:registry.invoke(c.id))
                rows.append(row);enabled.append(command.enabled())
        if self._phone:
            # v71: every menu rises from the bar on a phone.
            self.menu=None
            kit.bar_menu(anchor,rows)
            return
        self.menu=FloatingMenu(rows)
        for button,active in zip(self.menu.buttons,enabled):button.set_sensitive(active)
        self.menu.popup(anchor)

    def _dismiss_menu(self):
        menu=getattr(self,'menu',None)
        if menu is not None and hasattr(menu,'close'):menu.close()

    def _bar_anchor(self,name):
        child=self.action_center.bar_row.get_first_child()
        while child:
            if child.get_name()==name:return child
            child=child.get_next_sibling()
        return self.action_center.bar

    def _import_folder(self):
        self._dismiss_menu()
        if self.state.fixture:
            # Studio hands this action to its separate Picker window. Photos
            # returns to the library; fixture mode records the request without
            # opening a real chooser or adding a Photos toast absent in v70.
            self._fixture_folder_requested=True
            return
        dialog=Gtk.FileDialog(title='Import photos')
        dialog.select_folder(self,None,self._import_chosen)

    def _import_chosen(self,dialog,result):
        try:
            folder=dialog.select_folder_finish(result)
            path=folder.get_path()
            if path is None:raise ValueError('Choose a local folder')
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(),Gtk.DialogError.DISMISSED):
                Toast.show(self.host,error.message,kind='error')
            return
        except ValueError as error:
            Toast.show(self.host,str(error),kind='error')
            return
        def imported(result):
            self._load()
            text=f'Imported {len(result.completed)} photos'
            if result.errors:text+=' · '+ '; '.join(result.errors)
            Toast.show(self.host,text,kind='error' if result.errors else 'added')
        self._work(lambda:self.state.source.import_files(_walk_files(Path(path),lambda:self._closed)),imported)

    def focus_search(self):
        if self.state and self.state.viewer:self._close_viewer()
        if getattr(self,'search_item',None):self.search_item.focus()

    def request_open_path(self,path):
        self._pending_open_path=Path(path).resolve()
        if self.state:self._resolve_pending_open()

    def _resolve_pending_open(self):
        path=self._pending_open_path
        if path is None:return
        def found(offset):
            if offset is None:
                Toast.show(self.host,'This photo is not in the library',kind='warning')
                self._pending_open_path=None
                return
            self.state.view='library';self.state.query='';self.state.page_offset=offset
            def opened():
                record=next((p for p in self.state.records if p.path==path),None)
                self._pending_open_path=None
                if record:self.open_photo(record.id)
            self._load(then=opened)
        self._work(lambda:self.state.source.page_offset_for_path(path),found)

    def _decode_into(self,path,widget,side,*,values=None,square=False,keep=False):
        # keep: a picture that outlives the view it was asked for (the
        # filmstrip, the Memories player) takes its pixels whenever they come.
        generation=None if keep else self._image_generation
        future=self.executor.submit(decode_square if square else decode_photo,path,side)
        def decoded(future):
            try:pixbuf,error=future.result(),''
            except Exception as failure:pixbuf,error=None,str(failure)
            GLib.idle_add(self._image_loaded,generation,widget,pixbuf,error,values)
        future.add_done_callback(decoded)

    def _image_loaded(self,generation,widget,pixbuf,error,values):
        if self._closed or (generation is not None and generation!=self._image_generation):return GLib.SOURCE_REMOVE
        if pixbuf is None:
            widget.set_tooltip_text(error)
            return GLib.SOURCE_REMOVE
        widget.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        # Sliders can change the draft while this decode is in flight. Its
        # texture belongs to the current generation, but its settings snapshot
        # may be older than the preview already shown by the slider callback.
        if widget is getattr(self,'viewport',None) and self.state is not None and self.state.current is not None:
            values=self.state.draft if self.state.editing else self.state.adjustments.get(self.state.viewer,{})
        if values is not None and hasattr(widget,'set_adjustments'):
            widget.set_adjustments(values)
        return GLib.SOURCE_REMOVE

    def _work(self,operation,done,*,on_error=None):
        if self._closed:
            if on_error:on_error('Photos closed')
            return
        self._busy=True
        future=self.catalog_executor.submit(operation)
        def complete(future):
            try:value,error=future.result(),''
            except Exception as failure:value,error=None,str(failure)
            GLib.idle_add(self._worked,value,error,done,on_error)
        future.add_done_callback(complete)

    def _worked(self,value,error,done,on_error=None):
        self._busy=False
        if self._closed:
            if on_error:on_error('Photos closed')
            return GLib.SOURCE_REMOVE
        if error:
            handled=on_error(error) if on_error else False
            if not handled:Toast.show(self.host,error,kind='error')
        else:done(value)
        return GLib.SOURCE_REMOVE

    def _apply_fixture_state(self):
        env=os.environ
        if env.get('LUMA_PHOTOS_SIDEBAR')=='0':self.toggle.toggle()
        if env.get('LUMA_PHOTOS_NEW_ALBUM'):
            album=self.state.fixture.create_album(env['LUMA_PHOTOS_NEW_ALBUM'])
            self.state.switch_view(album.id);self.state.apply_page(self.state.read_page())
            self._render_sidebar();self._render_library()
        identifier=env.get('LUMA_PHOTOS_VIEWER')
        if identifier:
            self.state.open_photo(identifier)
            self.state.load_adjustments(identifier)
            self.state.info=env.get('LUMA_PHOTOS_INFO')=='1'
            mode=env.get('LUMA_PHOTOS_EDIT')
            if mode:
                self.state.edit_mode=mode;self.state.begin_edit()
                if env.get('LUMA_PHOTOS_AUTO')=='1':self.state.auto_adjust()
                if env.get('LUMA_PHOTOS_ROTATE')=='1':self.state.draft['rot']=270
                if env.get('LUMA_PHOTOS_FLIP')=='1':self.state.draft['flip']=True
                if env.get('LUMA_PHOTOS_REVERT')=='1':self.state.revert_edit()
                if env.get('LUMA_PHOTOS_PRESET'):self.state.draft['pre']=env['LUMA_PHOTOS_PRESET']
            action=env.get('LUMA_PHOTOS_FAVOURITE_ACTION')
            if action:self.state.set_favourite(identifier,action=='add')
            if env.get('LUMA_PHOTOS_SAVE_AUTO')=='1' or env.get('LUMA_PHOTOS_CANCEL_AUTO')=='1':
                self.state.begin_edit();self.state.auto_adjust()
                if env.get('LUMA_PHOTOS_SAVE_AUTO')=='1':
                    self.state.adjustments[identifier]=self.state.save_edit(identifier,self.state.baseline,self.state.draft)
                self.state.cancel_edit()
            if env.get('LUMA_PHOTOS_DELETE')=='1':
                self.state.trash(identifier)
                self.state.apply_page(self.state.read_page())
                if env.get('LUMA_PHOTOS_UNDO_DELETE')=='1':
                    restored=self.state.undo_trash();self.state.apply_page(self.state.read_page());self.state.open_photo(restored)
                elif self.state.records:self.state.open_photo(self.state.records[0].id)
                self._render_sidebar()
            self._render_viewer()
            if env.get('LUMA_PHOTOS_SAVE_AUTO')=='1':Toast.show(self.host,'Saved. Revert to the original any time from Edit.',kind='saved')
            if env.get('LUMA_PHOTOS_DELETE')=='1' and env.get('LUMA_PHOTOS_UNDO_DELETE')!='1':Toast.show(self.host,'Moved to Deleted',kind='deleted',undo=self._undo_delete)
            if action:
                Toast.show(self.host,'Added to Favorites' if action=='add' else 'Removed from Favorites',
                           kind='favourite',undo=None if action=='add' else lambda:self._favourite(identifier,True))
        menu=env.get('LUMA_PHOTOS_MENU')
        later=lambda callback:GLib.timeout_add(300,lambda:(callback(),False)[1])
        if menu=='share':later(lambda:self._share_phone() if self._phone else self._share(self.corner.controls['share']))
        elif menu=='more':later(lambda:self._more_phone() if self._phone else self.corner.controls['more'].activate())
        elif menu=='open-in':later(self._open_in)
        elif menu=='add-to-album':later(self._add_to_album)
        elif menu=='import':later(self.request_import)
        elif menu=='new-album':later(self.request_new_album)
        elif menu=='import-all':later(self._import_all_fixture)
        elif menu=='import-folder':later(self._import_folder)
        elif menu=='crop-guidance':later(self._toggle_crop_shapes)
        # v71 phone states: a grown panel, the hidden chrome, a Memory playing.
        panel=env.get('LUMA_PHOTOS_PANEL')
        if panel:
            # A phone state: wait until the window has taken its phone shape.
            self._fixture_pending=panel
            GLib.timeout_add(400,lambda:(self._fixture_panel(),False)[1])
        memory=env.get('LUMA_PHOTOS_MEMORY')
        if memory is not None and memory!='':
            GLib.timeout_add(400,lambda:(getattr(self,'_memories',None)
                                         and self.play_memory(self._memories[int(memory)]),False)[1])

    def _fixture_panel(self):
        panel=getattr(self,'_fixture_pending',None)
        if not panel or not self._phone:return  # phone states: a computer shows its plain view
        self._fixture_pending=None
        actions={'collections':lambda:self._grow('places',self._collections_panel,'ph-collection'),
                 'zoom':lambda:self._grow('zoom',self._zoom_panel,'ph-zoom-menu'),
                 'search':self._open_search,'add':self.request_add,'album':self._new_album_panel,
                 'details':self._toggle_details,'share':self._share_phone,
                 'more':self._more_phone,'delete':self._confirm_delete,
                 'immersive':lambda:(setattr(self,'_immersive',True),self._apply_immersive())}
        actions[panel]()

    def _key_pressed(self,_controller,keyval,_code,_modifiers):
        if not self.state:return False
        if keyval==Gdk.KEY_Escape:
            if getattr(self,'_memory',None):self.close_memory()
            elif self.state.editing:self._cancel()
            elif self._panel:self._fold()
            elif self._immersive:
                self._immersive=False;self._apply_immersive()
            elif self.details.shown:self.details.close()
            elif self.state.viewer is not None:self._close_viewer()
            else:return False
            return True
        if self.state.viewer is not None and not self.state.editing:
            if keyval in (Gdk.KEY_Left,Gdk.KEY_Right):
                self._step(-1 if keyval==Gdk.KEY_Left else 1);return True
        return False

    def _begin_scan(self):
        if self.state is None or self.state.fixture or self._closed:return
        self._scan_generation+=1
        generation=self._scan_generation
        def scan():
            results=self.library.scan_all(cancelled=lambda:self._closed or generation!=self._scan_generation)
            return results,self.library.sources()
        if not self.state.records and not self._initial_scan_timer:
            self._initial_scan_timer=GLib.timeout_add_seconds(1,self._show_first_indexed_page)
        future=self.scan_executor.submit(scan)
        def scanned(future):
            try:result,error=future.result(),''
            except Exception as failure:result,error=None,str(failure)
            GLib.idle_add(self._scan_loaded,generation,result,error)
        future.add_done_callback(scanned)

    def _show_first_indexed_page(self):
        if self._closed or self.state.records:
            self._initial_scan_timer=0
            return GLib.SOURCE_REMOVE
        self._load()
        return GLib.SOURCE_CONTINUE

    def _scan_loaded(self,generation,result,error):
        if self._closed or generation!=self._scan_generation:return GLib.SOURCE_REMOVE
        if self._initial_scan_timer:
            GLib.source_remove(self._initial_scan_timer);self._initial_scan_timer=0
        if error:Toast.show(self.host,error,kind='error')
        else:
            _results,sources=result
            self._install_source_monitors(sources)
            self._load()
        return GLib.SOURCE_REMOVE

    def _install_source_monitors(self,sources):
        for monitor in self._monitors:monitor.cancel()
        self._monitors=[]
        for source in sources:
            if not source.reachable:continue
            try:
                monitor=Gio.File.new_for_path(str(source.root)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES,None)
            except GLib.Error:
                continue
            monitor.connect('changed',self._source_changed)
            self._monitors.append(monitor)

    def _source_changed(self,*_):
        if self._closed:return
        if self._monitor_timeout:GLib.source_remove(self._monitor_timeout)
        self._monitor_timeout=GLib.timeout_add(750,self._monitor_rescan)

    def _monitor_rescan(self):
        self._monitor_timeout=0
        self._begin_scan()
        return GLib.SOURCE_REMOVE

    def _close_requested(self,*_):
        self._closed=True;self._generation+=1;self._image_generation+=1;self._scan_generation+=1
        if self._monitor_timeout:GLib.source_remove(self._monitor_timeout)
        if self._initial_scan_timer:GLib.source_remove(self._initial_scan_timer)
        for monitor in self._monitors:monitor.cancel()
        self.executor.shutdown(wait=False,cancel_futures=True)
        self.catalog_executor.shutdown(wait=False,cancel_futures=True)
        self.scan_executor.shutdown(wait=False,cancel_futures=True)
        return False


class PhotosApplication(Adw.Application):
    def __init__(self):
        flags=Gio.ApplicationFlags.HANDLES_OPEN
        if os.environ.get('LUMA_PHOTOS_FIXTURE') or os.environ.get('LUMA_PHOTOS_NON_UNIQUE')=='1':
            flags|=Gio.ApplicationFlags.NON_UNIQUE
        default_app_id=APP_ID
        if flags&Gio.ApplicationFlags.NON_UNIQUE and not default_app_id.endswith('.LumaUIPreview'):
            default_app_id+='.LumaUIPreview'
        app_id=os.environ.get('LUMA_PHOTOS_APP_ID',default_app_id)
        super().__init__(application_id=app_id,flags=flags)

    def do_startup(self):
        Adw.Application.do_startup(self)
        install_appkit();install_lumaui()
        add_style_sheet(os.environ.get('LUMA_PHOTOS_STYLE_PATH','/usr/share/prairie-core/photos.css'))

    def do_activate(self):
        (self.props.active_window or PhotosWindow(self)).present()

    def do_open(self,files,_count,_hint):
        self.do_activate()
        for file in files:
            if file.get_path():
                self.props.active_window.request_open_path(Path(file.get_path()))
                break


def main(argv=None):
    return PhotosApplication().run(sys.argv if argv is None else argv)

if __name__=='__main__':
    raise SystemExit(main())
