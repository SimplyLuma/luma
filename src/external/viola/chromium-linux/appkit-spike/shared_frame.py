# SPDX-License-Identifier: GPL-3.0-only
"""Bounded raw frame copies from an authenticated engine's read-only SHM FD.

The producer retains its writable mapping. The frame lease prevents pool reuse
until the consumer owns an immutable copy; F_SEAL_WRITE is not required.
"""
import fcntl
import os
import stat

MAX_FRAME_BYTES = 64 * 1024 * 1024


def _integer(metadata, name, minimum, maximum):
    value = metadata.get(name)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid shared frame ' + name)
    return value


def validate_layout(metadata):
    if (metadata.get('kind') != 'frame' or type(metadata.get('version')) is not int
            or metadata.get('version') != 2
            or metadata.get('transport') != 'shm'
            or metadata.get('fourcc') not in ('AR24', 'AB24')
            or not isinstance(metadata.get('target_id'), str)
            or not 1 <= len(metadata['target_id']) <= 128):
        raise ValueError('Unsupported shared frame protocol or pixel format')
    primaries = _integer(metadata, 'color_primaries', 1, 255)
    transfer = _integer(metadata, 'transfer_characteristics', 1, 255)
    _integer(metadata, 'matrix_coefficients', 0, 0)
    _integer(metadata, 'full_range', 0, 1)
    if primaries == 2 or transfer == 2:
        raise ValueError('Shared frame color parameters must be specified')
    _integer(metadata, 'id', 1, 2**31 - 1)
    _integer(metadata, 'window_id', 1, 2**31 - 1)
    width = _integer(metadata, 'width', 1, 5120)
    height = _integer(metadata, 'height', 1, 3200)
    stride = _integer(metadata, 'stride', width * 4, MAX_FRAME_BYTES)
    offset = _integer(metadata, 'offset', 0, MAX_FRAME_BYTES)
    region_size = _integer(metadata, 'region_size', 1, MAX_FRAME_BYTES)
    length = stride * height
    if length > MAX_FRAME_BYTES or offset + length > region_size:
        raise ValueError('Shared frame layout exceeds its region')
    x = _integer(metadata, 'content_x', 0, width - 1)
    y = _integer(metadata, 'content_y', 0, height - 1)
    content_width = _integer(metadata, 'content_width', 1, width)
    content_height = _integer(metadata, 'content_height', 1, height)
    if x + content_width > width or y + content_height > height:
        raise ValueError('Shared frame content exceeds its coded dimensions')
    return offset, length


def copy_pixels(metadata, descriptor):
    """Return immutable pixels; descriptor ownership and lease stay with caller."""
    offset, length = validate_layout(metadata)
    before = os.fstat(descriptor)
    if (not stat.S_ISREG(before.st_mode)
            or before.st_size != metadata['region_size']
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY):
        raise ValueError('Shared frame requires a correctly sized read-only regular FD')
    pixels = os.pread(descriptor, length, offset)
    after = os.fstat(descriptor)
    if (len(pixels) != length or
            (after.st_dev, after.st_ino, after.st_size) !=
            (before.st_dev, before.st_ino, before.st_size)):
        raise ValueError('Shared frame backing changed or was truncated during copy')
    return pixels
