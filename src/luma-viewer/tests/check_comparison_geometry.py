# SPDX-License-Identifier: Apache-2.0
"""Compare outlines with native pixels; prove the old geometry fails."""
import inspect
import textwrap
from types import SimpleNamespace

import cairo
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf
from luma_viewer.composition import ViewerUI

red=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,1200,1200)
red.fill(0xff0000ff)
blue=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,1200,1200)
blue.fill(0x0000ffff)
window=SimpleNamespace(loaded=True,cropping=False,compare=['red','blue'],
    compare_pixbufs={'red':red,'blue':blue},compare_mode='side')

def check(outline_callback,width,height):
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
    ViewerUI._draw_compare(window,cairo.Context(surface),width,height)
    surface.flush();data=bytes(surface.get_data());stride=surface.get_stride()
    outlines=outline_callback(window,width,height)
    assert len(outlines)==2
    for index,(x,y,w,h,kind) in enumerate(outlines):
        occupied=[]
        for py in range(height):
            for px in range(width):
                at=py*stride+px*4;b,g,r,a=data[at:at+4]
                # Full-resolution samples are downscaled like the real Filer
                # screenshots; their filtered edge occupies at most one pixel.
                if a and ((index==0 and r and not b) or (index==1 and b and not r)):
                    occupied.append((px,py))
        assert occupied,(width,height,index,'no raster pixels')
        bounds=(min(px for px,py in occupied),min(py for px,py in occupied),
            max(px for px,py in occupied)+1,max(py for px,py in occupied)+1)
        for observed,edge in zip(bounds,(x,y,x+w,y+h)):
            assert abs(observed-edge)<=1,(width,height,index,bounds,(x,y,w,h))
        assert kind=='comparison'
        print('OUTLINE MATCHES NATIVE RASTER',width,height,index,bounds,flush=True)

for dimensions in ((280,352),(390,740),(1180,740)):
    check(ViewerUI._canvas_outlines,*dimensions)

# A memory-only copy of the previously shipped callback proves the check
# catches the regression, without editing the source or weakening tolerance.
source=textwrap.dedent(inspect.getsource(ViewerUI._canvas_outlines))
current="ch=max(1,h-8-type_metrics('small')['line_height']) if side else h"
assert source.count(current)==1
namespace=dict(ViewerUI._canvas_outlines.__globals__)
exec(source.replace(current,'ch=h-36 if side else h'),namespace)
try:
    check(namespace['_canvas_outlines'],280,352)
except AssertionError as error:
    print('NEGATIVE CONTROL: old outline geometry rejected',error,flush=True)
else:
    raise AssertionError('Old outline geometry incorrectly passed')
