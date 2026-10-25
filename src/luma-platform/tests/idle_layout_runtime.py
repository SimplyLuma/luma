# SPDX-License-Identifier: Apache-2.0
"""Real GTK quiescence, resize, remap and virtual-list checks on an isolated display."""
import json
from pathlib import Path
import sys
import time
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import install_appkit, install_lumaui, add_style_sheet, MediaTransport
from luma_appkit.structure_adapt import _LayoutWatch
from luma_tide.fixture import FixtureLibrary
from luma_tide.ui import TideWindow
from luma_tide.ui_parts import SongList

errors=[]
def exception(kind,value,tb):
    errors.append(str(value));sys.__excepthook__(kind,value,tb)
sys.excepthook=exception

def pump(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        # A busy frame clock must not hide the deadline or hang a failed test.
        for _ in range(100):
            if not GLib.MainContext.default().pending():break
            GLib.MainContext.default().iteration(False)
        time.sleep(.003)

def quiet(widget,label):
    pump(.8)
    clock=widget.get_frame_clock();assert clock is not None
    start=clock.get_frame_counter();pump(1.1)
    frames=clock.get_frame_counter()-start
    assert frames<=3,(label,'permanent frame requests',frames)
    print('PASS idle frames',label,frames,flush=True)

app=Adw.Application(application_id='org.projectluma.IdleLayoutTest',flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit();install_lumaui()
source=Path(__file__).resolve().parents[3]
add_style_sheet(str(source/'src/luma-tide/data/tide.css'))
# The passive observer adapts on actual allocation and reconnects on remap.
box=Gtk.Box();seen=[]
watch=_LayoutWatch(box,lambda w,_clock:seen.append((w.get_width(),w.get_height())))
window=Gtk.Window(application=app,child=box,default_width=640,default_height=400)
window.present();pump(.4);assert seen and seen[-1][0]>=640
quiet(box,'observer')
window.set_default_size(820,480);pump(.5);assert seen[-1][0]>=820,seen[-1]
window.set_visible(False);pump(.15);assert watch.clock is None
window.present();pump(.4);assert watch.clock is not None
quiet(box,'remapped observer');window.close();pump(.1);assert watch.clock is None

# Paused media controls must adapt without keeping the frame clock alive.
for variant in ('attached','waveform'):
    transport=MediaTransport(variant,duration=120,playing=False)
    viewport=Gtk.Overlay(child=Gtk.Box(hexpand=True,vexpand=True),hexpand=True)
    viewport.set_overflow(Gtk.Overflow.HIDDEN)
    viewport.add_overlay(transport)
    viewport.set_measure_overlay(transport,False)
    window=Gtk.Window(application=app,child=viewport,default_width=800,default_height=300)
    window.present();pump(.5)
    quiet(window,'paused '+variant)
    window.set_default_size(390,300);pump(.5)
    expected = viewport.get_width() <= 559 if variant == 'waveform' else viewport.get_width() < transport._full_width
    assert transport.has_css_class('phone') == expected, (variant,viewport.get_width())
    quiet(window,'narrow paused '+variant)
    window.set_default_size(1000,300);pump(.5)
    assert not transport.has_css_class('phone')
    window.close();pump(.1)

# Details drawers must stay idle and follow height changes while open.
from luma_appkit.structure_details import DetailsPane
from luma_appkit.structure_layers import LayerHost
pane=DetailsPane()
for i in range(40):pane.body.append(Gtk.Label(label='Detail '+str(i)))
row=Gtk.Box();row.append(Gtk.Box(hexpand=True));row.append(pane)
host=LayerHost(row,name='window')
window=Gtk.Window(application=app,child=host,default_width=390,default_height=700)
window.present();pump(.4);pane.show(open=True,subject='fixture');pump(.6)
assert pane.is_drawer
quiet(window,'details drawer')
maximum=pane.scroller.get_max_content_height()
window.set_default_size(390,500);pump(.6)
assert pane.scroller.get_max_content_height()<maximum
quiet(window,'resized details drawer')
window.set_visible(False);pump(.2);window.present();pump(.7)
assert pane.is_drawer
quiet(window,'remapped details drawer')
window.set_default_size(1000,700);pump(.7);assert not pane.is_drawer
quiet(window,'details side pane')
window.set_default_size(390,700);pump(.7);assert pane.is_drawer
pane.close();pump(.7);assert not pane.is_drawer
quiet(window,'closed details drawer');window.close();pump(.2)

# Exercise Depot's real shelf reflow without starting package/network providers.
sys.path[:0]=[str(source/'src/luma-depot'),str(source/'src/luma-installer')]
from types import SimpleNamespace
from luma_depot.window import DepotWindow
cards=[Gtk.Button(label='App '+str(i)) for i in range(9)]
shelf=DepotWindow._cards(SimpleNamespace(_card=lambda index:cards[index]),range(len(cards)))
window=Gtk.Window(application=app,child=shelf,default_width=960,default_height=500)
window.present();pump(.6)
assert shelf.query_child(cards[1])[0]==1
quiet(window,'Depot shelf')
assert cards[1].grab_focus()
assert window.get_focus() is cards[1]
window.set_default_size(390,500);pump(.7)
assert shelf.query_child(cards[1])[:2]==(0,1),shelf.query_child(cards[1])
assert window.get_focus() is cards[1]
quiet(window,'narrow Depot shelf')
window.set_default_size(1200,500);pump(.6)
assert shelf.query_child(cards[1])[0]==1
quiet(window,'wide Depot shelf')
window.set_visible(False);pump(.2);window.present();pump(.5)
quiet(window,'remapped Depot shelf');window.close();pump(.2)

# Preview-only update copy shares the same passive size adaptation.
from luma_depot.os_updates import OsState
window=Gtk.Window(application=app,default_width=1000,default_height=600)
owner=SimpleNamespace(get_width=window.get_width,os_state=OsState(enrolled=True,staged_version='test'),
                      preview_staged=False,preview_checked='now',show_release_notes=lambda **_kw:None)
block=DepotWindow._preview_os_block(owner);window.set_child(block)
window.present();pump(.6)
assert block.get_orientation()==Gtk.Orientation.HORIZONTAL
quiet(window,'Depot update preview')
window.set_default_size(390,600);pump(.6)
assert block.get_orientation()==Gtk.Orientation.VERTICAL
quiet(window,'narrow Depot update preview')
window.set_default_size(1000,600);pump(.6)
assert block.get_orientation()==Gtk.Orientation.HORIZONTAL
quiet(window,'wide Depot update preview');window.close();pump(.2)

fixture=FixtureLibrary(source/'tests/fixtures/tide-v70.json')
w=TideWindow(app,fixture);w.set_default_size(1180,874);w.present();pump(1.2)
assert w.library.albums
quiet(w,'Tide desktop')
for width in (720,390,1024):
    w.set_default_size(width,874);pump(.6)
    assert w.phone==(w.get_width()<560),(width,w.get_width(),w.phone)
    if not w.phone:
        assert w.deck.get_allocated_width()==min(880,max(250,w.layer.get_width()-96))+36
    quiet(w,'Tide width '+str(width))
w.open_search();pump(.4);assert w.search.get_mapped()
w.search_item.entry.set_text('love');pump(.5);assert w.query=='love' and w.view=='search'
quiet(w,'Tide search');w.close_search();w.show_view('songs');pump(.5)
quiet(w,'Tide song list');w.close();pump(.15)

# Actual scrolling must still populate the far end without a resident tick.
page=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
scroll=Gtk.ScrolledWindow(child=page,overlay_scrolling=False)
made=[]
def make(_album,index):
    made.append(index);return Gtk.Label(label=str(index),height_request=44)
def bind(row,_album,index):row.set_label(str(index))
listing=SongList([(None,i) for i in range(20000)],make,bind,scroll.get_vadjustment(),page,lambda _rows:None,row_height=44)
page.append(listing)
window=Gtk.Window(application=app,child=scroll,default_width=640,default_height=500)
window.present();pump(.8)
adj=scroll.get_vadjustment();adj.set_value(adj.get_upper()-adj.get_page_size());pump(.8)
assert 19999 in listing.rows,(listing.span,adj.get_upper(),adj.get_value())
assert len(made)<160,len(made)
quiet(window,'20k virtual songs')
window.close();pump(.2);assert not errors,errors
print('PASS passive layout, Tide resize/search and bounded virtual scrolling',flush=True)
