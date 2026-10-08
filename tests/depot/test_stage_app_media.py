# SPDX-License-Identifier: Apache-2.0
"""Publisher rejects corrupt or absent media before signing a release."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('stage_media', ROOT / 'scripts/depot/stage-app-media.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


def chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)


def png(width=1, height=1):
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00\xff')) + chunk(b'IEND', b''))


class MediaStage(unittest.TestCase):
    def test_complete_png_and_corruption(self):
        valid = png()
        self.assertEqual(media.png_structure(valid), (1, 1))
        damaged = bytearray(valid)
        damaged[45] ^= 1
        for value in (valid[:8], valid[:-1], valid + b'junk', bytes(damaged), png(8193, 1), png(8192, 8192)):
            with self.subTest(value=value[:32]), self.assertRaises(ValueError):
                media.png_structure(value)

    def test_exact_object_media_is_required_and_immutable(self):
        app = 'org.projectluma.Notes'
        image = png()
        sha = hashlib.sha256(image).hexdigest()
        url = f'https://dl.simplyluma.com/media/{app}/{sha}.png'
        def metainfo(images):
            return f'<component><id>{app}</id><screenshots>{images}</screenshots></component>'.encode()
        xml = metainfo(f'<screenshot><image width="1" height="1">{url}</image></screenshot>')
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / 'site'
            with patch.object(media, 'object_bytes', side_effect=[xml, image]):
                result = media.stage(Path(tmp), 'a' * 64, app, site, 'https://dl.simplyluma.com')
            self.assertEqual(result['images'][0]['sha256'], sha)
            self.assertFalse(result['public_sync_executed'])
            target = site / 'media' / app / (sha + '.png')
            self.assertEqual(target.read_bytes(), image)
            target.write_bytes(b'different')
            with patch.object(media, 'object_bytes', side_effect=[xml, image]), self.assertRaisesRegex(ValueError, 'different bytes'):
                media.stage(Path(tmp), 'a' * 64, app, site, 'https://dl.simplyluma.com')
            for invalid in (metainfo(''), xml.replace(b'width="1"', b'width="2"'), xml.replace(b'https://dl.simplyluma.com', b'https://evil.invalid')):
                with patch.object(media, 'object_bytes', side_effect=[invalid, image]), self.assertRaises(ValueError):
                    media.stage(Path(tmp), 'a' * 64, app, site, 'https://dl.simplyluma.com')
            self.assertEqual(target.read_bytes(), b'different')

if __name__ == '__main__':
    unittest.main()
