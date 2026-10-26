# SPDX-License-Identifier: Apache-2.0
"""Unchanged source/marks are retained; active ink stays outside the layer."""
from types import SimpleNamespace, MethodType
import unittest
import cairo
import gi
gi.require_version('GdkPixbuf','2.0')
from gi.repository import GdkPixbuf
from luma_viewer.application import ViewerWindow
from luma_viewer.annotations import Mark
from luma_viewer.drawing import draw_marks

class ImageLayerTests(unittest.TestCase):
    def window(self):
        image=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,320,240)
        image.fill(0x7396b2ff)
        marks=(Mark('ink','#112233',(20,20),(100,70),points=((20,20),(30,50),(70,30),(100,70)),width=3),
               Mark('highlight','#ffe011',(10,100),(130,100),points=((10,100),(130,100)),width=15),
               Mark('blur','#112233',(80,10),(120,50)),Mark('redact','#112233',(150,20),(190,60)))
        w=SimpleNamespace(pixbuf=image,processed=image,hold=False,page=0,marks=marks,document=None)
        for name in ('_paint_image','_paint_source','_paint_image_layer'):
            setattr(w,name,MethodType(getattr(ViewerWindow,name),w))
        return w
    def render(self,w,cached):
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,480,360);cr=cairo.Context(surface);cr.scale(1.5,1.5)
        if cached:w._paint_image_layer(cr)
        else:
            w._paint_image(cr);draw_marks(cr,w.marks,w._paint_source)
        surface.flush();return bytes(surface.get_data())
    def test_retained_layer_matches_source_ink_highlight_blur_and_redaction(self):
        w=self.window();direct=self.render(w,False);cached=self.render(w,True)
        delta = [abs(a-b) for a,b in zip(direct,cached)]
        # Retaining the group adds one Cairo compositing/rounding step.
        # Every channel must remain within two 8-bit levels of direct painting.
        print('retained layer pixel delta:', max(delta), 'changed channels:', sum(d != 0 for d in delta))
        self.assertLessEqual(max(delta), 2)
    def test_repaint_reuses_one_layer_and_invalidates_on_content_changes(self):
        w=self.window();self.render(w,True);first=w._image_layer
        for _ in range(10):self.render(w,True);self.assertIs(w._image_layer,first)
        w.marks=(*w.marks,Mark('box','#1100ff',(20,50),(40,80)))
        self.render(w,True);second=w._image_layer;self.assertIsNot(second,first)
        w.processed=w.pixbuf.copy();self.render(w,True);third=w._image_layer;self.assertIsNot(third,second)
        w.hold=True;self.render(w,True);self.assertIsNot(w._image_layer,third)
