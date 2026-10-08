#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Write one file of every kind Quick View previews into the given folder."""
import io
import math
import pathlib
import shutil
import struct
import sys
import tarfile
import wave
import zipfile

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

root = pathlib.Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)


def picture(name, width, height, kind):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    stride = pixbuf.get_rowstride()
    data = bytearray(stride * height)
    for y in range(height):
        for x in range(width):
            u, v = x / width, y / height
            if kind == 'sunset':
                r, g, b = 250 - 90 * v, 140 + 60 * u - 80 * v, 90 + 120 * v
            else:
                r, g, b = 40 + 180 * u, 120 + 100 * math.sin(v * 6.3), 200 - 120 * u
            i = y * stride + x * 3
            data[i:i + 3] = bytes(max(0, min(255, int(c))) for c in (r, g, b))
    GdkPixbuf.Pixbuf.new_from_bytes(__import__('gi').repository.GLib.Bytes.new(bytes(data)),
                                    GdkPixbuf.Colorspace.RGB, False, 8, width, height, stride
                                    ).savev(str(root / name), 'png', [], [])


picture('01 Landscape photo.png', 1800, 1200, 'sunset')
picture('02 Tall screenshot.png', 824, 1866, 'waves')
(root / '03 Notes.txt').write_text('Quick View\n\n' + '\n'.join(
    f'{n:02d}. A plain line of text that wraps in a narrow preview.' for n in range(1, 60)))
(root / '04 Readme.md').write_text('# Project notes\n\nQuick View shows **rendered** Markdown '
                                   'with a source toggle.\n\n- Images\n- Documents\n- Archives\n\n'
                                   '```python\nprint("hello")\n```\n')
(root / '05 script.py').write_text('"""Code preview."""\n\n\ndef greet(name: str) -> str:\n'
                                   '    return f"Hello, {name}"\n\n\nprint(greet("Luma"))\n')


def pdf(path):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R '
               b'/Resources << /Font << /F1 5 0 R >> >> >>',
               None,
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    text = b'BT /F1 28 Tf 72 760 Td (Quarterly report) Tj ET'
    objects[3] = b'<< /Length %d >>\nstream\n' % len(text) + text + b'\nendstream'
    out = io.BytesIO(); out.write(b'%PDF-1.4\n'); offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(out.tell()); out.write(b'%d 0 obj\n' % n + body + b'\nendobj\n')
    xref = out.tell()
    out.write(b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1))
    for offset in offsets:
        out.write(b'%010d 00000 n \n' % offset)
    out.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref))
    path.write_bytes(out.getvalue())


pdf(root / '06 Report.pdf')
with wave.open(str(root / '07 Tone.wav'), 'wb') as sound:
    sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(22050)
    sound.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(2 * math.pi * 440 * i / 22050)))
                               for i in range(22050 * 3)))
fonts = sorted(pathlib.Path('/usr/share/fonts').rglob('*.ttf')) + sorted(pathlib.Path('/usr/share/fonts').rglob('*.otf'))
if fonts:
    shutil.copy(fonts[0], root / f'08 Font{fonts[0].suffix}')
with tarfile.open(root / '09 Source.tar.gz', 'w:gz') as archive:
    for name in ('03 Notes.txt', '04 Readme.md', '05 script.py'):
        archive.add(root / name, arcname=f'source/{name}')
folder = root / '10 Folder'
folder.mkdir(exist_ok=True)
for n in range(6):
    (folder / f'item {n}.txt').write_text('x' * (n * 400))
with zipfile.ZipFile(root / '11 Bundle.zip', 'w') as bundle:
    bundle.writestr('docs/a.txt', 'a' * 2000)
    bundle.writestr('docs/b.md', '# b')
