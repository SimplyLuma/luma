# SPDX-License-Identifier: GPL-3.0-only
"""Real descriptor/layout regressions for raw native frame copying."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from shared_frame import copy_pixels, validate_layout


def frame():
    return dict(kind='frame', version=2, transport='shm', fourcc='AR24', id=1,
                window_id=7, target_id='owned-page', width=2, height=2,
                stride=12, offset=4, region_size=28, content_x=0, content_y=0,
                content_width=2, content_height=2, color_primaries=1,
                transfer_characteristics=13, matrix_coefficients=0, full_range=1)


class SharedFrameTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='viola-shared-frame-')
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'pixels'
        self.body = b'HEAD' + bytes(range(24))
        self.path.write_bytes(self.body)
        self.fd = os.open(self.path, os.O_RDONLY)
        self.addCleanup(os.close, self.fd)

    def test_offset_stride_and_immutable_copy_survive_producer_reuse(self):
        pixels = copy_pixels(frame(), self.fd)
        self.assertEqual(pixels, self.body[4:])
        self.path.write_bytes(b'X' * len(self.body))
        self.assertEqual(pixels, self.body[4:])

    def test_writable_descriptor_is_rejected(self):
        fd = os.open(self.path, os.O_RDWR)
        try:
            with self.assertRaises(ValueError): copy_pixels(frame(), fd)
        finally: os.close(fd)

    def test_nonregular_descriptor_is_rejected(self):
        read, write = os.pipe()
        try:
            with self.assertRaises(ValueError): copy_pixels(frame(), read)
        finally: os.close(read); os.close(write)

    def test_truncated_backing_is_rejected_without_mapping(self):
        self.path.write_bytes(b'X')
        with self.assertRaises(ValueError): copy_pixels(frame(), self.fd)

    def test_short_read_and_concurrent_truncation_are_rejected(self):
        real = os.pread
        def truncated(fd, length, offset):
            self.path.write_bytes(b'X')
            return real(fd, length, offset)
        with patch('shared_frame.os.pread', side_effect=truncated):
            with self.assertRaises(ValueError): copy_pixels(frame(), self.fd)

    def test_protocol_pixel_format_and_identity_are_checked(self):
        for key, value in [('kind','cursor'),('version',1),('transport','dmabuf'),
                           ('fourcc','NV12'),('target_id',''),('target_id','x'*129),
                           ('id',True),('id',0),('window_id',0),('version',2.0),
                           ('color_primaries',2),('transfer_characteristics',2),
                           ('matrix_coefficients',1),('full_range',True)]:
            with self.subTest(key=key, value=value):
                metadata = frame(); metadata[key] = value
                with self.assertRaises(ValueError): validate_layout(metadata)

    def test_layout_overflow_padding_and_content_bounds_are_checked(self):
        for key, value in [('width',5121),('height',3201),('stride',7),
                           ('stride',2**63),('offset',5),('region_size',2**63),
                           ('content_x',1),('content_width',3),('width',True)]:
            with self.subTest(key=key, value=value):
                metadata = frame(); metadata[key] = value
                with self.assertRaises(ValueError): validate_layout(metadata)

    def test_rgba_order_is_retained_without_lossy_encoding(self):
        metadata = frame(); metadata['fourcc'] = 'AB24'
        self.assertEqual(copy_pixels(metadata, self.fd), self.body[4:])

if __name__ == '__main__':
    unittest.main()
