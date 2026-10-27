#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Export one SVG master into native macOS, Windows and Linux app-icon assets.

Build dependency: Pillow and either rsvg-convert or PyGObject/librsvg.
Generated assets belong in an artifact directory, never source Git.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
from PIL import Image

SIZES=(16,20,24,32,40,48,64,96,128,256,512,1024)
WINDOWS_SIZES=(16,20,24,32,40,48,64,96,128,256)

def render(source,output):
    converter=shutil.which('rsvg-convert')
    if converter:
        subprocess.run([converter,'-w','1024','-h','1024','-o',str(output),str(source)],check=True)
    else:
        import gi
        gi.require_version('Rsvg','2.0')
        from gi.repository import Rsvg
        handle=Rsvg.Handle.new_from_file(str(source))
        pixbuf=handle.get_pixbuf()
        if (pixbuf.get_width(),pixbuf.get_height())!=(1024,1024):
            raise ValueError('The SVG master must be 1024x1024')
        pixbuf.savev(str(output),'png',[],[])

def export(source,output):
    output.mkdir(parents=True,exist_ok=False)
    render(source,output/'master.png')
    master=Image.open(output/'master.png').convert('RGBA')
    images={size:master.resize((size,size),Image.Resampling.LANCZOS) for size in SIZES}
    iconset=output/'macos/Try Luma.iconset';iconset.mkdir(parents=True)
    for size in (16,32,128,256,512):
        for scale in (1,2):
            images[size*scale].save(iconset/('icon_'+str(size)+'x'+str(size)+('@2x' if scale==2 else '')+'.png'))
    iconutil=shutil.which('iconutil')
    icns=output/'macos/Try Luma.icns'
    if iconutil:
        subprocess.run([iconutil,'-c','icns','-o',str(icns),str(iconset)],check=True)
    else:
        # Portable ICNS export for design review; the native Mac packager uses iconutil.
        master.save(icns,format='ICNS')
    windows=output/'windows';windows.mkdir()
    master.save(windows/'Try Luma.ico',format='ICO',sizes=[(x,x) for x in WINDOWS_SIZES])
    with Image.open(windows/'Try Luma.ico') as result:
        assert result.ico.sizes()=={(x,x) for x in WINDOWS_SIZES}
    svg=output/'linux/hicolor/scalable/apps/io.luma.Tester.svg'
    svg.parent.mkdir(parents=True);shutil.copy2(source,svg)
    for size,image in images.items():
        target=output/('linux/hicolor/'+str(size)+'x'+str(size)+'/apps/io.luma.Tester.png')
        target.parent.mkdir(parents=True);image.save(target)
    assert Image.open(icns).size==(1024,1024)
    return output

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();export(a.source.resolve(),a.output.resolve())

if __name__=='__main__':main()
