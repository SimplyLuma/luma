#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual Photos pointer crop, persisted Done, canceled draft and source bytes."""
import argparse,ctypes,ctypes.util,hashlib,os,tempfile,time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0');gi.require_version('GdkX11','4.0')
from gi.repository import Gdk,GLib,Graphene,Gtk
from photos_runtime_smoke import _png,settle,until
from prairie_apps.photos import PhotosApplication,PhotosWindow
from prairie_apps.photos_backend import PhotoLibrary
from prairie_apps.photos_adjustments import AdjustmentStore

def drag(window,start,end):
    x11,xtst=(ctypes.CDLL(ctypes.util.find_library(lib)) for lib in ('X11','Xtst'))
    x11.XOpenDisplay.restype=ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes=[ctypes.c_void_p];x11.XDefaultRootWindow.restype=ctypes.c_ulong
    x11.XTranslateCoordinates.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_int,ctypes.c_int,ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_ulong)]
    x11.XFlush.argtypes=[ctypes.c_void_p];x11.XCloseDisplay.argtypes=[ctypes.c_void_p]
    xtst.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
    xtst.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
    display=x11.XOpenDisplay(None);assert display
    try:
        x,y,child=ctypes.c_int(),ctypes.c_int(),ctypes.c_ulong()
        assert x11.XTranslateCoordinates(display,window.get_surface().get_xid(),x11.XDefaultRootWindow(display),0,0,ctypes.byref(x),ctypes.byref(y),ctypes.byref(child))
        tx,ty=window.get_surface_transform()
        def move(p):
            assert xtst.XTestFakeMotionEvent(display,-1,round(x.value+p[0]+tx),round(y.value+p[1]+ty),0)
            x11.XFlush(display);settle(.04)
        move(start);xtst.XTestFakeButtonEvent(display,1,True,0);x11.XFlush(display);settle(.06)
        for step in range(1,7):move(tuple(a+(b-a)*step/6 for a,b in zip(start,end)))
        xtst.XTestFakeButtonEvent(display,1,False,0);x11.XFlush(display);settle(.2)
    finally:x11.XCloseDisplay(display)

def point(window,u,v):
    view=window.viewport;x,y,w,h=view.get_content_bounds()
    ok,p=view.compute_point(window,Graphene.Point().init(x+u*w,y+v*h));assert ok
    return p.x,p.y

parser=argparse.ArgumentParser();parser.add_argument('--width',type=int,required=True);args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='luma-photos-free-crop-') as folder:
    root=Path(folder)
    for key in ('HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME'):
        p=root/key;p.mkdir();os.environ[key]=str(p)
    os.environ['LUMA_PHOTOS_NON_UNIQUE']='1';os.environ.pop('LUMA_PHOTOS_FIXTURE',None)
    source=root/'pictures';source.mkdir();image=source/'Generated.png';image.write_bytes(_png(900,600));before=image.read_bytes()
    library=PhotoLibrary(root/'catalog.sqlite3');s=library.add_source(source,'Private generated fixture');assert library.scan_source(s.id).added==1
    app=PhotosApplication();assert app.register(None);window=PhotosWindow(app,library=library);window.set_default_size(args.width,900);window.present()
    try:
        until(lambda:window.state and len(window.state.records)==1,'photo fixture')
        record=window.state.records[0];window.open_photo(record.id);until(lambda:hasattr(window,'viewport') and window.viewport.get_content_bounds()[2]>0,'photo decode')
        window._begin_edit();window._edit_mode('crop');until(lambda:window.viewport.get_content_bounds()[2]>0,'crop edit');settle(.5)
        # Start from the ordinary full-image frame, then resize with actual native
        # pointer input. The app must persist the widget callback into its draft.
        _,_,iw,ih=window.viewport.get_content_bounds()
        drag(window,point(window,1-4/iw,1-4/ih),point(window,.7,.7))
        rect=window.state.draft.get('crop_rect');assert rect is not None and rect[2]<.8 and rect[3]<.8,rect
        x,y,w,h=rect
        drag(window,point(window,x+w/2,y+h/2),point(window,x+w/2+.1,y+h/2+.08))
        moved=window.state.draft['crop_rect'];assert moved[0]>x+.05 and moved[1]>y+.03,(rect,moved)
        output=os.environ.get('LUMA_PHOTOS_FREE_CROP_OUTPUT')
        def capture(label):
            if output:
                p=Path(output);p.mkdir(parents=True,exist_ok=True);settle(.3)
                snap=Gtk.Snapshot();Gtk.WidgetPaintable.new(window).snapshot(snap,window.get_width(),window.get_height())
                node=snap.to_node();assert node is not None,'mapped Photos snapshot required'
                window.get_renderer().render_texture(node,Graphene.Rect().init(0,0,window.get_width(),window.get_height())).save_to_png(str(p/f'photos-{label}-{args.width}.png'))
        capture('edit')
        saved=tuple(moved);window._done();until(lambda:not window.state.editing,'persisted Done')
        values=AdjustmentStore(library.database).load(record.id);assert tuple(values['crop_rect'])==saved,values
        window._begin_edit();window._edit_mode('crop');until(lambda:window.viewport.get_content_bounds()[2]>0,'reopened crop');settle(.3)
        assert tuple(window.state.draft['crop_rect'])==saved
        x,y,w,h=saved;drag(window,point(window,x+w,y+h),point(window,x+w-.15,y+h-.1));assert tuple(window.state.draft['crop_rect'])!=saved
        window._cancel();until(lambda:not window.state.editing,'Cancel')
        assert tuple(AdjustmentStore(library.database).load(record.id)['crop_rect'])==saved
        assert image.read_bytes()==before,'editing must not rewrite the original image'
        until(lambda:window.viewport.get_content_bounds()[2]>0,'canceled draft photo decode');capture('saved')
        print('PASS actual Photos pointer resize/move/Done/reopen/Cancel/original',args.width,flush=True)
    finally:window.close();settle(.2)
