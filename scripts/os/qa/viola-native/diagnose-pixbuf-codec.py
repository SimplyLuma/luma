#!/usr/bin/python3
"""Report installed PNG encoder/decoder behavior without touching user files."""
import hashlib
import json
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

report = {'formats': [{'name': f.get_name(), 'writable': f.is_writable()}
                      for f in GdkPixbuf.Pixbuf.get_formats()]}
try:
    pixel = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1)
    pixel.fill(0x13579BFF)
    encoded = pixel.save_to_bufferv('png', [], [])
    success, data = encoded
    assert success and bytes(data).startswith(b'\x89PNG\r\n\x1a\n')
    report['encode'] = {'ok': True, 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest()}
    try:
        decoder = GdkPixbuf.PixbufLoader.new_with_type('png')
        decoder.write(data)
        decoder.close()
        image = decoder.get_pixbuf()
        assert image.get_width() == image.get_height() == 1
        report['decode'] = {'ok': True, 'width': 1, 'height': 1}
    except Exception as error:
        report['decode'] = {'ok': False, 'type': type(error).__name__, 'error': str(error)}
except Exception as error:
    report['encode'] = {'ok': False, 'type': type(error).__name__, 'error': str(error)}
print(json.dumps(report, sort_keys=True))
