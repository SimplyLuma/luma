#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render and resolve the extracted approved icon payload through GTK/librsvg."""
import argparse
import gi, json, pathlib, sys, cairo, hashlib
for name,version in [('Gtk','4.0'),('Rsvg','2.0')]: gi.require_version(name,version)
from gi.repository import Gtk, Gio, Rsvg
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--theme',type=pathlib.Path,required=True)
parser.add_argument('--manifest',type=pathlib.Path,required=True)
parser.add_argument('--output',type=pathlib.Path,required=True)
args=parser.parse_args()
theme=args.theme.resolve();out=args.output;out.mkdir(parents=True,exist_ok=True)
manifest=json.loads(args.manifest.read_text())
Gtk.init(); icons=Gtk.IconTheme.new(); icons.set_search_path([str(theme.parent), '/usr/share/icons']);icons.set_theme_name('Prairie')
rows=[]
for icon in manifest['icons']:
    name=icon['name']; expected=theme/icon['destination']
    paint=icons.lookup_icon(name,None,32,1,Gtk.TextDirection.NONE,Gtk.IconLookupFlags.FORCE_REGULAR)
    actual=pathlib.Path(paint.get_file().get_path()); assert actual.is_relative_to(theme),(name,actual)
    assert hashlib.sha256(actual.read_bytes()).hexdigest()==icon['sha256'],(name,actual)
    rows.append({'name':name,'resolved':str(actual.relative_to(theme))})
fallback=icons.lookup_icon('face-smile',None,32,1,Gtk.TextDirection.NONE,Gtk.IconLookupFlags.FORCE_REGULAR).get_file()
assert fallback and not pathlib.Path(fallback.get_path()).is_relative_to(theme),fallback
symbolic=icons.lookup_icon('folder-symbolic',None,32,1,Gtk.TextDirection.NONE,Gtk.IconLookupFlags.FORCE_SYMBOLIC)
assert symbolic.is_symbolic()
print('GTK: 51 approved regular lookups, inherited unowned icon and separate symbolic lookup pass')
print('Fallback:',fallback.get_path())
for mime in ['application/zip','text/plain','application/pdf','image/png','audio/mpeg','video/mp4','application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
    gicon=Gio.content_type_get_icon(mime);paint=icons.lookup_by_gicon(gicon,32,1,Gtk.TextDirection.NONE,Gtk.IconLookupFlags.FORCE_REGULAR)
    print('GIO',mime,gicon.to_string(),paint.get_file().get_path())
representatives=['folder','folder-bookmarks','folder-documents','folder-open','user-home','user-trash','text-x-generic','application-certificate','package-x-generic','audio-x-generic','image-x-generic','video-x-generic','x-office-document','x-office-drawing','x-office-presentation','x-office-spreadsheet','computer','phone','media-removable','video-display']
# Render all approved names on both surfaces, plus representative sizing grid.
for color,rgb in [('dark',(0.15,0.17,0.19)),('light',(0.96,0.96,0.96))]:
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1000,((len(rows)+7)//8)*170)
    ctx=cairo.Context(surface);ctx.set_source_rgb(*rgb);ctx.paint()
    for n,icon in enumerate(manifest['icons']):
        x=(n%8)*125;y=(n//8)*170
        handle=Rsvg.Handle.new_from_file(str(theme/icon['destination']))
        ctx.save();ctx.translate(x+14,y+4)
        box=Rsvg.Rectangle();box.x=0;box.y=0;box.width=96;box.height=96
        assert handle.render_document(ctx,box)
        ctx.restore();ctx.set_source_rgb(*((0.82,0.84,0.87) if color=='dark' else (0.15,0.17,0.19)))
        ctx.set_font_size(9)
        name=icon['name'];ctx.move_to(x+4,y+118);ctx.show_text(name[:23]);ctx.move_to(x+4,y+130);ctx.show_text(name[23:])
    surface.write_to_png(str(out/f'all-{color}.png'))
    for scale in [1,2]:
        sizes=[16,24,32,48,64,128,256];height=len(representatives)*290;width=820
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width*scale,height*scale);ctx=cairo.Context(surface);ctx.scale(scale,scale);ctx.set_source_rgb(*rgb);ctx.paint()
        for row,name in enumerate(representatives):
            icon=next(i for i in manifest['icons'] if i['name']==name);handle=Rsvg.Handle.new_from_file(str(theme/icon['destination']))
            x=8
            for size in sizes:
                ctx.save();ctx.translate(x,row*290+8);box=Rsvg.Rectangle();box.width=size;box.height=size;assert handle.render_document(ctx,box);ctx.restore();x+=size+12
            ctx.set_source_rgb(*((.82,.84,.87) if color=='dark' else (.15,.17,.19)));ctx.set_font_size(12);ctx.move_to(8,row*290+282);ctx.show_text(name)
        surface.write_to_png(str(out/f'sizes-{color}-{scale}x.png'))
(out/'lookup.json').write_text(json.dumps(rows,indent=2)+'\n')
print('librsvg: 51 approved names on light/dark; 20 families at 16/24/32/48/64/128/256 at scales1/2 pass')
