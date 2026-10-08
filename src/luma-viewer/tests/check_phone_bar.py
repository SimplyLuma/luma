# SPDX-License-Identifier: Apache-2.0
"""The closed information pane must not leave a blank frame at the right."""
import time

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib

from luma_viewer.application import ViewerApplication


def until(condition):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if condition():
            return
        time.sleep(.01)
    raise AssertionError('Viewer layout did not settle')


def right_edge(widget, ancestor):
    valid, bounds = widget.compute_bounds(ancestor)
    assert valid
    return bounds.origin.x + bounds.size.width


def children(widget):
    child=widget.get_first_child()
    while child:
        yield child
        child=child.get_next_sibling()


app = ViewerApplication()
assert app.register(None) and not app.get_is_remote()
app.activate()
window = app.get_windows()[0]
for width in (360,402,500):
    window.set_default_size(width,874)
    until(lambda: window.get_width()==width and window.loaded and window.phone)
    until(lambda: hasattr(window,'file_head') and window.file_head.get_mapped())
    assert not window.corner_slot.get_visible()
    assert not window.sidebar_toggle.get_visible()
    assert window.file_head.get_height()==48
    until(lambda: window.stage.get_height()==window.document_overlay.get_height())
    view=window._viewport()
    self_width=window.marks_area.get_width()
    image_width=window._page_size()[0]*view.scale
    assert abs(image_width-self_width)<=1,(width,image_width,self_width)
    assert window.file_head.thumbnail.get_width()==34
    window.file_head.emit('clicked')
    until(lambda: window.action_center.grown=='files')
    window.file_head.emit('clicked')
    until(lambda: window.action_center.grown is None)
    window._phone_information()
    until(lambda: window.action_center.grown=='information')
    window.action_center.fold_panel()
    window._set_mode('markup')
    until(lambda: not window.action_center.head_row.get_visible() and window.tools.get_mapped())
    assert window.tools.get_parent() is window.document_overlay
    assert window.surround.get_mapped()
    window.tool_buttons['text'].emit('clicked')
    assert window.tool=='text'
    assert window.tool_buttons['text'].has_css_class('primary')
    window._set_mode('adjust')
    until(lambda: window.adjust_panel.get_mapped())
    until(lambda: window.adjust_panel.get_width()==window.document_overlay.get_width()-32)
    valid,bounds=window.adjust_panel.compute_bounds(window.document_overlay)
    assert valid and bounds.origin.x==16
    assert abs(bounds.origin.y+bounds.size.height-(window.document_overlay.get_height()-104))<=1
    save=next(child.bar_item for child in children(window.action_center.bar_row) if getattr(getattr(child,'bar_item',None),'label','')=='Save')
    assert save.main.get_mapped() and save.more.get_mapped()
    window._set_mode('view')
    until(lambda: window.action_center.head_row.get_visible())
    print('PASS Viewer phone head',width)
window.destroy();app.quit()
