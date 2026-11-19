# SPDX-License-Identifier: Apache-2.0
"""Run Lua 5.4 source through the system's liblua (ctypes), for tests and timing.

libinput 1.31 links liblua 5.4, so any machine that runs the plugin has the
library. Returns None from available() where it doesn't (macOS, minimal CI).
"""

from __future__ import annotations

import ctypes
import ctypes.util

_NAMES = ("lua5.4", "lua-5.4", "lua54", "lua")
_PATHS = ("liblua5.4.so.0", "liblua-5.4.so", "liblua.so.5.4", "liblua5.4.so", "/usr/lib64/liblua-5.4.so",
          "/usr/lib/x86_64-linux-gnu/liblua5.4.so.0", "/usr/lib/aarch64-linux-gnu/liblua5.4.so.0")
_lib = None


def _load():
    global _lib
    if _lib is not None:
        return _lib or None
    for cand in [ctypes.util.find_library(n) for n in _NAMES] + list(_PATHS):
        if not cand:
            continue
        try:
            lib = ctypes.CDLL(cand)
            lib.luaL_newstate.restype = ctypes.c_void_p
            lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
            lib.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            lib.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_void_p]
            lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            lib.lua_tolstring.restype = ctypes.c_char_p
            lib.lua_close.argtypes = [ctypes.c_void_p]
            lib.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
            _lib = lib
            return lib
        except (OSError, AttributeError):
            continue
    _lib = False
    return None


def available() -> bool:
    return _load() is not None


class LuaError(RuntimeError):
    pass


def run(source: str) -> str:
    """Run a chunk that returns one string; return that string."""
    lib = _load()
    if lib is None:
        raise LuaError("liblua 5.4 not found")
    L = lib.luaL_newstate()
    try:
        lib.luaL_openlibs(L)
        if lib.luaL_loadstring(L, source.encode()) != 0:
            raise LuaError(lib.lua_tolstring(L, -1, None).decode(errors="replace"))
        if lib.lua_pcallk(L, 0, 1, 0, None, None) != 0:
            raise LuaError(lib.lua_tolstring(L, -1, None).decode(errors="replace"))
        out = lib.lua_tolstring(L, -1, None)
        return out.decode() if out is not None else ""
    finally:
        lib.lua_close(L)


def table(values) -> str:
    """A Python list of numbers/None/nested lists as a Lua table constructor (NaN as 0/0)."""
    if isinstance(values, (list, tuple)):
        return "{" + ",".join(table(v) for v in values) + "}"
    if values is None:
        return "nil"
    if isinstance(values, bool):
        return "true" if values else "false"
    if isinstance(values, str):
        return '"' + values.replace("\\", "\\\\").replace('"', '\\"') + '"'
    v = float(values)
    if v != v:
        return "(0/0)"
    if v in (float("inf"), float("-inf")):
        return "math.huge" if v > 0 else "-math.huge"
    return repr(v)
