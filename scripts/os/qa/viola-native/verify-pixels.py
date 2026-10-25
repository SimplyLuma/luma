#!/usr/bin/python3
"""Verify compositor screenshot pixels in an independently bound page rectangle.

This is a diagnostic component, not native-window/target identity admission.
The caller must bind the supplied rectangle to the exact proven GTK page and
compositor window; finding arbitrary matching pixels is intentionally unsupported.
"""
import argparse
import hashlib
import json
import math
import struct
from pathlib import Path

PALETTES = (
    ((231,41,83),(19,173,109),(43,83,229),(241,181,29)),
    ((29,191,211),(197,47,181),(89,211,47),(229,101,23)),
    ((117,53,223),(223,211,37),(31,151,179),(211,61,43)),
)

def verify(width, height, pixel, rectangle, phase):
    if phase not in range(3) or len(rectangle) != 4:
        raise ValueError('invalid phase or rectangle')
    x,y,w,h = rectangle
    if any(not math.isfinite(v) for v in rectangle) or w < 80 or h < 80 or x < 0 or y < 0 or x+w > width or y+h > height:
        raise ValueError('page rectangle outside screenshot or too small')
    samples = []
    for index, expected in enumerate(PALETTES[phase]):
        cx = x + w * (0.25 if index % 2 == 0 else 0.75)
        cy = y + h * (0.25 if index < 2 else 0.75)
        for dx,dy in ((-4,-4),(0,0),(4,4),(-4,4),(4,-4)):
            px,py = int(cx+dx),int(cy+dy)
            actual = tuple(pixel(px,py)[:3])
            ok = len(actual) == 3 and all(abs(a-b) <= 12 for a,b in zip(actual,expected))
            samples.append(dict(x=px,y=py,expected=expected,actual=actual,ok=ok))
    return dict(ok=all(item['ok'] for item in samples),phase=phase,rectangle=rectangle,samples=samples)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('screenshot',type=Path)
    parser.add_argument('--phase',type=int,required=True)
    parser.add_argument('--rectangle',nargs=4,type=float,required=True)
    args = parser.parse_args()
    path=args.screenshot
    before=path.stat()
    if not path.is_file() or path.is_symlink() or before.st_size > 16*1024*1024:
        raise ValueError('invalid screenshot file')
    data=path.read_bytes()
    if len(data)<33 or data[:8]!=b'\x89PNG\r\n\x1a\n' or data[12:16]!=b'IHDR' or struct.unpack('>I',data[8:12])[0]!=13:
        raise ValueError('invalid PNG header')
    width,height=struct.unpack('>II',data[16:24])
    if width<1 or height<1 or width*height>16_777_216:
        raise ValueError('PNG dimensions exceed bound')
    import gi
    gi.require_version('GdkPixbuf','2.0')
    from gi.repository import GdkPixbuf
    loader=GdkPixbuf.PixbufLoader.new_with_type('png')
    loader.write(data); loader.close()
    image=loader.get_pixbuf()
    if image.get_width()*image.get_height() > 16_777_216 or image.get_bits_per_sample()!=8:
        raise ValueError('unsupported screenshot geometry')
    pixels=image.get_pixels(); stride=image.get_rowstride(); channels=image.get_n_channels()
    report=verify(image.get_width(),image.get_height(),lambda x,y:pixels[y*stride+x*channels:y*stride+x*channels+3],args.rectangle,args.phase)
    after=path.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise ValueError('screenshot changed during verification')
    report.update(screenshot=str(path),sha256=hashlib.sha256(data).hexdigest(),scope='pixel proof only; caller must separately prove window/target/SHM/input identity')
    print(json.dumps(report,sort_keys=True))
    raise SystemExit(0 if report['ok'] else 1)

if __name__=='__main__': main()
