# SPDX-License-Identifier: Apache-2.0
"""QR code modules for pairing codes, with no hard Python dependency.

python3-qrcode is used when installed. Otherwise libqrencode, which Luma images
already carry for GNOME Settings, is called directly. Both produce the same
module matrix: rows of booleans including a four-module quiet zone.
"""
import ctypes
import ctypes.util

QUIET_ZONE = 4
_LEVEL_M = 1
_MODE_8 = 2


class _QRcode(ctypes.Structure):
    _fields_ = [('version', ctypes.c_int), ('width', ctypes.c_int), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _python_qrcode(text):
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    code = qrcode.QRCode(border=QUIET_ZONE, error_correction=ERROR_CORRECT_M)
    code.add_data(text)
    code.make(fit=True)
    return [list(map(bool, row)) for row in code.get_matrix()]


def _libqrencode(text, library=None):
    name = library or ctypes.util.find_library('qrencode') or 'libqrencode.so.4'
    lib = ctypes.CDLL(name)
    lib.QRcode_encodeString.restype = ctypes.POINTER(_QRcode)
    lib.QRcode_encodeString.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.QRcode_free.argtypes = [ctypes.POINTER(_QRcode)]
    code = lib.QRcode_encodeString(text.encode('utf-8'), 0, _LEVEL_M, _MODE_8, 1)
    if not code:
        raise ValueError('text does not fit in a QR code')
    try:
        width = code.contents.width
        data = code.contents.data
        size = width + 2 * QUIET_ZONE
        matrix = [[False] * size for _ in range(size)]
        for y in range(width):
            for x in range(width):
                matrix[y + QUIET_ZONE][x + QUIET_ZONE] = bool(data[y * width + x] & 1)
        return matrix
    finally:
        lib.QRcode_free(code)


def matrix(text):
    """The QR modules for ``text``, or None when neither encoder is available."""
    try:
        return _python_qrcode(text)
    except ImportError:
        pass
    try:
        return _libqrencode(text)
    except OSError:
        return None
