# SPDX-License-Identifier: Apache-2.0
"""App-owned document canvas; chrome is composed separately from LumaUI."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Graphene, Gtk
from luma_appkit import lumaui_tokens
from luma_appkit.media_style import colour, rect, rounded


def document_colour(widget,role,fallback):
    # TODO(kit-request viewer-10): adopt R2-AT's exported Viewer role names.
    found,value=widget.get_style_context().lookup_color(role)
    return value if found else colour(widget,fallback)


class DocumentCanvas(Gtk.DrawingArea):
    def __init__(self, outlines, divider=None, **kwargs):
        super().__init__(**kwargs)
        self.outlines = outlines
        self.divider = divider

    def do_snapshot(self, snapshot):
        # Bounds belong to the displayed document, never its source pixels or
        # exported appearance. Resolve ink from the same media token as the kit.
        for x, y, width, height, kind in self.outlines(self.get_width(), self.get_height()):
            bounds = Graphene.Rect().init(x, y, width, height)
            radius = lumaui_tokens.MEDIA['tile']['radius'] if kind == 'image' else 0
            shape = rounded(bounds, radius)
            if kind == 'pdf':
                snapshot.append_outset_shadow(shape, colour(self, 'luma_media_shadow', .3), 0, 1, 0, 2)
                snapshot.append_outset_shadow(shape, colour(self, 'luma_media_shadow', .7), 0, 20, -24, 50)
            elif kind == 'image':
                snapshot.append_outset_shadow(shape, colour(self, 'luma_media_shadow', .6), 0, 18, -14, 44)
                snapshot.append_outset_shadow(shape, colour(self, 'luma_media_shadow', .25), 0, 0, 1, 0)
            else:
                snapshot.append_outset_shadow(shape, colour(self, 'luma_media_shadow', .8), 0, 20, -30, 60)
        Gtk.DrawingArea.do_snapshot(self, snapshot)
        if self.divider:
            position=self.divider(self.get_width(),self.get_height())
            if position:
                x,y,height=position
                face=document_colour(self,'luma_viewer_handle_face','lumaui_white')
                line=rounded(rect(x-1,y,2,height),0)
                snapshot.append_outset_shadow(line,colour(self,'luma_media_shadow',.5),0,0,0,10)
                snapshot.append_color(face,line.bounds)
                center=rounded(rect(x-17,y+height/2-17,34,34),17)
                snapshot.append_outset_shadow(center,colour(self,'luma_media_shadow',.4),0,4,0,12)
                snapshot.push_rounded_clip(center);snapshot.append_color(face,center.bounds);snapshot.pop()
