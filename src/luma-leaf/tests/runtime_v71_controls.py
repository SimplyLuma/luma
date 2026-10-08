import os,time
os.environ['LUMA_LEAF_PREVIEW']='1'
from pathlib import Path
os.environ['LUMA_LEAF_FIXTURE']=str(Path(__file__).resolve().parents[3]/'tests/fixtures/leaf-v70.json')
os.environ['WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS']='1'
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib,Gtk
from luma_leaf.application import LeafApplication

def until(test):
 end=time.monotonic()+15
 while time.monotonic()<end:
  while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
  if test():return
  time.sleep(.01)
 raise AssertionError('did not settle')
app=LeafApplication();assert app.register(None);app.activate();w=app.window
for width in (360,402,720,1180):
 w.set_default_size(width,874);w.show_library()
 until(lambda:w.get_width()==width and w.library_view.phone==(width<560))
 until(lambda:not w.reader.action_center.card.get_visible())
 if width<560:
  assert w.library_view.action_center.get_mapped()
  assert w.library_view.grid.get_max_children_per_line()==3
 # Assert actual painted content against the window, not its oversized parent.
 until(lambda:w.library_view.grid.get_first_child().get_width()>0)
 for item in (w.library_view.content,w.library_view.hero_phone if width<560 else w.library_view.hero_desktop):
  ok,b=item.compute_bounds(w)
  assert ok and b.origin.x>=0 and b.origin.x+b.size.width<=width,(width,b)
 tile=w.library_view.grid.get_first_child()
 count=0
 while tile:
  ok,b=tile.compute_bounds(w)
  assert ok and b.origin.x>=0 and b.origin.x+b.size.width<=width,(width,count,b)
  count+=1;tile=tile.get_next_sibling()
 assert count==10
 w.app.open_book('totc',False)
 until(lambda:w.reader.ready)
 until(lambda:w.reader._phone==(width<560))
 w.reader.contents_button.set_active(True)
 until(lambda:w.reader.action_center.grown=='contents' if width<560 else w.reader.contents.get_mapped())
 assert w.reader.contents_list.get_first_child() is not None
 w.reader.contents_button.set_active(False)
 w.show_library();w.reader._refresh_actions()
 assert not w.reader.action_center.card.get_visible()
 print('PASS library/reader/contents',width,flush=True)
w.destroy();app.quit()
