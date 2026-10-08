"""Register the kit's licensed document faces in this process only.

The source/preview tree and installed platform carry the same font files. No
user font directory or system Fontconfig configuration is changed.
"""
import ctypes
import ctypes.util
from pathlib import Path

_registered = False


def ensure_document_fonts():
    global _registered
    if _registered:
        return True
    roots = (Path(__file__).resolve().parent.parent / 'fonts',
             Path('/usr/share/luma-appkit/fonts'))
    root = next((p for p in roots if (p / 'Newsreader.ttf').is_file()), None)
    library = ctypes.util.find_library('fontconfig')
    if root is None or library is None:
        return False
    fc = ctypes.CDLL(library)
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    fc.FcConfigAppFontAddFile.restype = ctypes.c_int
    results = [fc.FcConfigAppFontAddFile(None, str(root / name).encode())
               for name in ('Newsreader.ttf', 'Newsreader-Italic.ttf')]
    if not all(results):
        return False
    from gi.repository import PangoCairo
    PangoCairo.FontMap.get_default().changed()
    _registered = True
    return True
