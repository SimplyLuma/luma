#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Private generated-photo regression for the five manual Photos review findings.
Set LUMA_PHOTOS_REVIEW_OUTPUT to retain optional PNG evidence. No user store is used.
"""
import os,tempfile
from pathlib import Path
from photos_runtime_smoke import settle,until,grids,_png
from prairie_apps.photos import PhotosApplication,PhotosWindow
from prairie_apps.photos_backend import PhotoLibrary
from gi.repository import Gtk,Graphene
temporary=tempfile.TemporaryDirectory(prefix='photos-review-')
root=Path(temporary.name)
output=Path(os.environ.get('LUMA_PHOTOS_REVIEW_OUTPUT',str(root/'captures')))
output.mkdir(parents=True,exist_ok=True)
for key in ('HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME'):
 p=root/key;p.mkdir();os.environ[key]=str(p)
os.environ['LUMA_PHOTOS_NON_UNIQUE']='1'
os.environ.pop('LUMA_PHOTOS_FIXTURE',None)
pics=root/'pictures';pics.mkdir()
for n in range(8):(pics/f'Fixture {n}.png').write_bytes(_png(120,80))
lib=PhotoLibrary(root/'catalog.sqlite3');source=lib.add_source(pics,'Fixture');assert lib.scan_source(source.id).added==8
app=PhotosApplication();assert app.register(None)
w=PhotosWindow(app,library=lib);w.set_default_size(402,874);w.present()
until(lambda:w.state and len(w.state.records)==8,'fixture');settle(1)
def bounds(widget):
 ok,b=widget.compute_bounds(w);assert ok
 return [b.get_x(),b.get_y(),b.get_width(),b.get_height()]
def save(name):
 s=Gtk.Snapshot();Gtk.WidgetPaintable.new(w).snapshot(s,w.get_width(),w.get_height())
 w.get_renderer().render_texture(s.to_node(),Graphene.Rect().init(0,0,w.get_width(),w.get_height())).save_to_png(str(output/(name+'.png')))
for width in (320,340,360,380,402,425,500,559,560,600,720,1180,402,360):
 w.set_default_size(width,874);settle(.3)
 for size in (96,340):
  w._size_changed(size);settle(.25)
  for g in grids(w):
   b=bounds(g);sc=bounds(w.scroll)
   assert b[0]>=sc[0] and b[0]+b[2]<=sc[0]+sc[2],('grid overflow',width,size,b,sc)
   for t in g._flat:
    ok,tb=t.compute_bounds(g);assert ok
    assert tb.get_x()>=0 and tb.get_x()+tb.get_width()<=g.get_width(),('tile overflow',width,size)
  print('BOUNDS',width,size,b,flush=True)
 if width in (360,402,720,1180):
  save(f'library-{width}')
 if width in (360,402):
  c=w.action_center;base=bounds(c.bar)
  for key,fn in [('add',w._add_panel),('places',w._collections_panel),('zoom',w._zoom_panel)]:
   w._grow(key,fn,'ph-add' if key=='add' else 'ph-collection' if key=='places' else 'ph-zoom-menu');settle(.3)
   grown=bounds(c.bar);assert grown[0]==base[0] and grown[2]==base[2],('bar changed',width,key,base,grown)
   save(f'{key}-{width}');w._fold();settle(.2)
  w._open_search();settle(.3)
  assert w._search_panel.get_first_child().get_text()=='Search your photos by name or place.'
  save(f'search-{width}')
  w._search_field.set_text('Fixture');settle(.8)
  assert w.state.query=='Fixture'
  assert w._search_panel.get_first_child() is not None
  w._fold();settle(.5)
 if width in (360,402,720,1180):
  w.open_photo(w.state.records[0].id);settle(.7)
  save(f'viewer-{width}');w._close_viewer();settle(.4)
# Closing a phone viewer must remain closed across both responsive tiers.
for width in (1180,402,720,360):
 w.set_default_size(width,874);settle(.3)
 assert w.stack.get_visible_child_name()=='library'
 assert not w._island.get_visible(),('closed viewer header resurrected',width)
# Real place metadata makes the date heading wider than a phone. It must
# ellipsize without imposing that width on every photo beneath it.
for record in w.state.records:
 lib.set_metadata(record.id,place='A very long neighborhood name in San Francisco')
w._load();until(lambda:all(r.place for r in w.state.records),'place metadata');settle(.5)
for width in (320,360,402,720,1180):
 w.set_default_size(width,874);settle(.3)
 for g in grids(w):
  b=bounds(g);sc=bounds(w.scroll)
  assert b[0]+b[2]<=sc[0]+sc[2]-20,('long heading overflow',width,b,sc)
 save(f'long-heading-{width}')
w.close();temporary.cleanup();print('Photos review: continuous bounds, panel width, empty-place search and viewer PASS',flush=True)
