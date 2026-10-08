# SPDX-License-Identifier: Apache-2.0
"""Camera RAW decoding through the distribution's required LibRaw library.

Only public C getters/setters and the documented processed-image record are
used; the version-dependent internal libraw_data_t layout stays opaque.
Reference: https://www.libraw.org/docs/API-C.html (LibRaw 0.22).
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
import io
import math
import os
import threading
from pathlib import Path

RAW_SUFFIXES = frozenset({".dng", ".nef", ".nrw", ".cr2", ".cr3", ".crw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".ori", ".rw2", ".pef", ".rwl", ".3fr", ".fff", ".iiq", ".kdc", ".dcr", ".mos", ".mrw", ".srw", ".x3f"})


class RawDecodeError(RuntimeError):
    pass


class _Image(C.Structure):
    _fields_ = [("type", C.c_int), ("height", C.c_ushort), ("width", C.c_ushort),
                ("colors", C.c_ushort), ("bits", C.c_ushort), ("data_size", C.c_uint),
                ("data", C.c_ubyte * 1)]


def _library():
    # Override is for development against an unpacked distribution RPM only.
    path = os.environ.get("LUMA_DARKROOM_LIBRAW") or ctypes.util.find_library("raw_r") or "libraw_r.so.25"
    try:
        library = C.CDLL(path)
        signatures = {
            "libraw_init": ([C.c_uint], C.c_void_p),
            "libraw_close": ([C.c_void_p], None),
            "libraw_open_file": ([C.c_void_p, C.c_char_p], C.c_int),
            "libraw_unpack_thumb": ([C.c_void_p], C.c_int),
            "libraw_dcraw_make_mem_thumb": ([C.c_void_p, C.POINTER(C.c_int)], C.POINTER(_Image)),
            "libraw_unpack": ([C.c_void_p], C.c_int),
            "libraw_dcraw_process": ([C.c_void_p], C.c_int),
            "libraw_set_output_color": ([C.c_void_p, C.c_int], None),
            "libraw_set_gamma": ([C.c_void_p, C.c_int, C.c_float], None),
            "libraw_set_no_auto_bright": ([C.c_void_p, C.c_int], None),
            "libraw_set_adjust_maximum_thr": ([C.c_void_p, C.c_float], None),
            "libraw_set_highlight": ([C.c_void_p, C.c_int], None),
            "libraw_get_rgb_cam": ([C.c_void_p, C.c_int, C.c_int], C.c_float),
            "libraw_get_pre_mul": ([C.c_void_p, C.c_int], C.c_float),
            "libraw_set_output_bps": ([C.c_void_p, C.c_int], None),
            "libraw_get_cam_mul": ([C.c_void_p, C.c_int], C.c_float),
            "libraw_set_user_mul": ([C.c_void_p, C.c_int, C.c_float], None),
            "libraw_dcraw_make_mem_image": ([C.c_void_p, C.POINTER(C.c_int)], C.POINTER(_Image)),
            "libraw_dcraw_clear_mem": ([C.POINTER(_Image)], None),
            "libraw_strerror": ([C.c_int], C.c_char_p),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        return library
    except (OSError, AttributeError) as error:
        raise RawDecodeError("Darkroom’s camera support needs repair. Reinstall or update Darkroom, then try again.") from error


_cache_lock = threading.Lock()
_cached_key = None
_cached_image = None


def decode_raw(path: Path):
    """Reuse one decoded source while adjusting; invalidate when its file changes."""
    global _cached_key, _cached_image
    info = path.stat()
    key = (str(path.resolve()), info.st_mtime_ns, info.st_size)
    with _cache_lock:
        if _cached_key == key and _cached_image is not None:
            return _cached_image
        _cached_key = _cached_image = None
        image = _decode_raw(path)
        # Bound retained camera samples to 192 MiB. No disk cache or original writes.
        if image.samples.nbytes <= 192 * 1024 * 1024:
            _cached_key, _cached_image = key, image
            return image
        return image


def _decode_raw(path: Path):
    """Demosaic to unclipped 16-bit linear camera RGB; defer the color matrix."""
    import numpy as np
    from .develop import CameraFrame
    library = _library()
    handle = library.libraw_init(0)
    if not handle:
        raise RawDecodeError("There isn’t enough memory to open this photo. Close another photo and try again.")
    bitmap = None

    def check(code):
        if code:
            reason = library.libraw_strerror(code)
            detail = reason.decode("utf-8", "replace") if reason else "Unreadable camera data"
            raise RawDecodeError(f"This camera file couldn’t be read. Try another copy of the original photo. ({detail})")

    try:
        check(library.libraw_open_file(handle, os.fsencode(path)))
        check(library.libraw_unpack(handle))
        library.libraw_set_output_color(handle, 0)  # Camera RGB: no gamut clipping.
        library.libraw_set_output_bps(handle, 16)
        library.libraw_set_gamma(handle, 0, 1.0)
        library.libraw_set_gamma(handle, 1, 1.0)
        library.libraw_set_no_auto_bright(handle, 1)
        library.libraw_set_adjust_maximum_thr(handle, 0.0)
        library.libraw_set_highlight(handle, 1)  # Normalize WB by its largest gain.

        multipliers = [library.libraw_get_cam_mul(handle, index) for index in range(4)]
        if all(math.isfinite(value) and value > 0 for value in multipliers[:3]):
            if not math.isfinite(multipliers[3]) or multipliers[3] <= 0: multipliers[3] = multipliers[1]
            for index, value in enumerate(multipliers): library.libraw_set_user_mul(handle, index, value)
        else:
            multipliers = [library.libraw_get_pre_mul(handle, index) for index in range(4)]
        positive = [value for value in multipliers[:3] if math.isfinite(value) and value > 0]
        if not positive: raise RawDecodeError("This camera has no usable white balance calibration")
        # Undo highlight=1's headroom-preserving normalization in floating point,
        # after demosaic. Doing it in uint16 (highlight=0) clips camera channels.
        check(library.libraw_dcraw_process(handle))
        normalized = [library.libraw_get_pre_mul(handle, index) for index in range(4)]
        normalized = [value for value in normalized if math.isfinite(value) and value > 0]
        if not normalized: raise RawDecodeError("This camera has no usable white balance calibration")
        scale = 1.0 / min(normalized)
        error = C.c_int()
        bitmap = library.libraw_dcraw_make_mem_image(handle, C.byref(error))
        check(error.value)
        if not bitmap:
            raise RawDecodeError("This camera file contains no readable image.")
        frame = bitmap.contents
        if frame.type != 2 or frame.bits != 16 or frame.colors not in (1, 3, 4):
            raise RawDecodeError("This camera’s image format isn’t supported yet.")
        expected = frame.width * frame.height * frame.colors * 2
        if not frame.width or not frame.height or expected != frame.data_size:
            raise RawDecodeError("This camera file is incomplete. Try another copy of the original photo.")
        pixels = C.string_at(C.addressof(frame) + _Image.data.offset, expected)
        samples = np.frombuffer(pixels, dtype=np.uint16).reshape(frame.height, frame.width, frame.colors)
        matrix = np.array([[library.libraw_get_rgb_cam(handle, row, col) for col in range(frame.colors)] for row in range(3)], dtype=np.float32)
        if frame.colors == 1: matrix = np.ones((3, 1), dtype=np.float32)
        if not np.isfinite(matrix).all() or not np.any(matrix):
            raise RawDecodeError("This camera has no usable color calibration")
        return CameraFrame(samples, matrix, scale)
    finally:
        if bitmap: library.libraw_dcraw_clear_mem(bitmap)
        library.libraw_close(handle)


def raw_thumbnail(path: Path):
    """Read the camera's embedded gallery preview, without demosaicing its RAW.

    This is only for gallery tiles. Editing and export always use decode_raw.
    An absent thumbnail is reported without launching a costly sensor decode.
    """
    from PIL import Image, ImageOps
    library = _library()
    handle = library.libraw_init(0)
    if not handle: raise RawDecodeError("Not enough memory to read this thumbnail")
    bitmap = None
    try:
        if library.libraw_open_file(handle, os.fsencode(path)) or library.libraw_unpack_thumb(handle):
            raise RawDecodeError("This photo has no readable embedded preview")
        error = C.c_int()
        bitmap = library.libraw_dcraw_make_mem_thumb(handle, C.byref(error))
        if error.value or not bitmap: raise RawDecodeError("This photo has no readable embedded preview")
        frame = bitmap.contents
        if not 0 < frame.data_size <= 64 * 1024 * 1024: raise RawDecodeError("Invalid thumbnail size")
        data = C.string_at(C.addressof(frame) + _Image.data.offset, frame.data_size)
        if frame.type == 1:
            with Image.open(io.BytesIO(data)) as image:
                return ImageOps.exif_transpose(image).convert("RGB")
        if frame.type == 2 and frame.bits == 8 and frame.colors in (1, 3) and frame.width * frame.height * frame.colors == len(data):
            return Image.frombytes("RGB" if frame.colors == 3 else "L", (frame.width, frame.height), data).convert("RGB")
        raise RawDecodeError("Unsupported embedded preview")
    finally:
        if bitmap: library.libraw_dcraw_clear_mem(bitmap)
        library.libraw_close(handle)
