"""Exercise real responsive library, inspector and rating controls against a fixture."""
import os,time
from pathlib import Path
os.environ['LUMA_DARKROOM_FIXTURE']=str((Path(__file__).resolve().parents[1] if (Path(__file__).resolve().parents[1]/'fixtures').is_dir() else Path(__file__).resolve().parents[3])/('fixtures/darkroom-v70.json' if (Path(__file__).resolve().parents[1]/'fixtures').is_dir() else 'tests/fixtures/darkroom-v70.json'))
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib,Gtk
from luma_darkroom.application import DarkroomApplication

def until(test):
 end=time.monotonic()+20
 while time.monotonic()<end:
  while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
  if test():return
  time.sleep(.01)
 raise AssertionError('layout did not settle')
def descendants(widget):
 child=widget.get_first_child()
 while child is not None:
  yield child
  yield from descendants(child)
  child=child.get_next_sibling()

def check_window_controls(window):
 # Count mapped controls, including native HeaderBar internals, after allocation.
 end=time.monotonic()+.3
 until(lambda:time.monotonic()>=end)
 native=[node for node in descendants(window.title_bar)
         if isinstance(node,Gtk.WindowControls) and node.get_mapped() and not node.get_empty()]
 kit=[node for node in descendants(window.title_bar)
      if isinstance(node,Gtk.Button) and node.has_css_class('lumaui-window-control') and node.get_mapped()]
 assert not native, f'duplicate native window controls: {len(native)}'
 assert len(kit)==(0 if window.phone else 3),(window.get_width(),window.phone,len(kit))
 assert not window.title_bar.get_show_start_title_buttons()
 assert not window.title_bar.get_show_end_title_buttons()

app=DarkroomApplication();assert app.register(None);app.activate();w=app.get_windows()[0]
until(lambda:w.snapshot is not None)
for width in (360,402,720,1180,402):
 w.set_default_size(width,874);w.set_mode('library')
 until(lambda:w.get_surface() is not None and w.get_surface().get_width()==width and w.phone==(width<=600))
 check_window_controls(w)
 if w.phone:
  assert w.action_center.head_row.get_visible()
  w._phone_info();assert w.action_center.grown=='information';w.action_center.fold_panel()
 for mode in ('adjust','crop','looks','mask','heal'):
  w.set_mode('edit');w.set_tool(mode)
  until(lambda:w.action_center.grown=='adjustments' if w.phone else w.right_panel.get_visible())
  assert w.inspector.get_parent() is not None
  if w.phone:
   until(lambda:w.action_center.panel.get_height()>0 and w.canvas.get_height()>0)
   ok,photo=w.canvas.compute_bounds(w);assert ok
   ok,panel=w.action_center.panel.compute_bounds(w);assert ok
   assert photo.size.width>100 and photo.size.height>100
   assert photo.origin.x>=0 and photo.origin.x+photo.size.width<=w.get_width()
   assert photo.origin.y+photo.size.height<=panel.origin.y,(width,mode,photo,panel)
   viewport=w.inspector.get_parent();scroller=viewport.get_parent()
   assert isinstance(scroller,Gtk.ScrolledWindow)
   assert scroller.get_height()<=300
   adj=scroller.get_vadjustment()
   adj.set_value(adj.get_upper());until(lambda:adj.get_value()>=max(0,adj.get_upper()-adj.get_page_size())-1)
   adj.set_value(0)
 w._set_rating(3);assert w.current_photo().stars==3
 w.set_mode('library')
 print('PASS Darkroom controls',width,flush=True)
w.destroy();app.quit()
