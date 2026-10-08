# SPDX-License-Identifier: Apache-2.0
"""Viewer-only paper navigation (coordinator triage viewer-10)."""
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Gsk','4.0')
gi.require_version('Graphene','1.0')
from gi.repository import Gtk
from luma_appkit import apply_type
from luma_appkit.media_style import colour, rect, rounded
from .canvas import document_colour


class PaperFace(Gtk.Widget):
    def __init__(self, selected):
        super().__init__()
        self.selected=selected
        self.set_overflow(Gtk.Overflow.VISIBLE)

    def do_measure(self, orientation, for_size):
        size=54 if orientation==Gtk.Orientation.HORIZONTAL else 70
        return size,size,-1,-1

    def do_snapshot(self,snapshot):
        shape=rounded(rect(0,0,54,70),3)
        snapshot.append_outset_shadow(shape,colour(self,'luma_media_shadow',.35),0,2,0,6)
        if self.selected():
            snapshot.append_outset_shadow(shape,document_colour(self,'luma_viewer_page_ring','luma_accent_ink'),0,0,4,0)
            snapshot.append_outset_shadow(shape,colour(self,'luma_content'),0,0,2,0)
        snapshot.push_rounded_clip(shape)
        snapshot.append_color(document_colour(self,'luma_viewer_page_face','lumaui_white'),rect(0,0,54,70))
        for index in range(4):
            # Paper has a fixed white fallback in both appearances. Its ink
            # must also be fixed: a missing theme role inherits nearly white
            # window text in dark/HC. Final Viewer paper roles override this
            # published palette fallback when request10 lands.
            snapshot.append_color(document_colour(self,'luma_viewer_page_heading' if index==0 else 'luma_viewer_page_line','lumaui_black'),
                rect(8,10+index*7,22.8 if index==0 else 38,2))
        snapshot.pop()


class PageThumbnail(Gtk.Button):
    def __init__(self,number,selected,on_activate):
        super().__init__(halign=Gtk.Align.CENTER)
        # This Viewer-only button draws its own paper face. Gtk.Button supplies
        # native accessible activation and keyboard handling, without a second
        # generic button frame around the document thumbnail.
        self.set_layout_manager(None)
        self.set_overflow(Gtk.Overflow.VISIBLE)
        self.update_property([Gtk.AccessibleProperty.LABEL],[f'Page {number}'])
        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
        self.face=PaperFace(lambda:selected() or self.has_focus());content.append(self.face)
        content.append(apply_type(Gtk.Label(label=str(number)),'caption',weight=400))
        self.set_child(content);self.connect('clicked',lambda *_:on_activate())
        self.set_cursor_from_name('pointer')

    def do_measure(self,orientation,for_size):
        child=self.get_child()
        return child.measure(orientation,for_size) if child else (0,0,-1,-1)

    def do_size_allocate(self,width,height,baseline):
        child=self.get_child()
        if child:child.allocate(width,height,baseline,None)

    def do_snapshot(self,snapshot):
        child=self.get_child()
        if child:self.snapshot_child(child,snapshot)


# This app-owned paper control has its own CSS node. Native Gtk.Button padding
# and background otherwise add a second frame around its snapshot face.
PageThumbnail.set_css_name('viewer-page-thumbnail')
