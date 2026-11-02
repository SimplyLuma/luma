# SPDX-License-Identifier: Apache-2.0
"""Pre-scaled pictures on disk, shared by every LumaUI app (`TextureLoader`'s second level).

A cover is decoded from its original once per drawn size; the scaled copy is
kept under `$XDG_CACHE_HOME/luma-artwork/v1/` so the next start, and the next
time it scrolls into view, reads a small file instead of decoding a large one.

- An entry's key is the source's path, modification time and size and the
  drawn side: a changed original (a cover revalidated by its app) misses and
  is made again; nothing is ever served stale.
- Opaque pictures are stored as JPEG, others as PNG.
- Use refreshes an entry's time; past the byte cap the least recently used
  entries go, down to 90% of it.
- Each source's dominant colour is kept in a small index, so a cover can paint
  its placeholder colour before any decoding (`colour()`).
- Works offline and never touches the network. Only pictures: no audio,
  video or documents are ever written here.

Thread-safe; every file operation runs on the loader's workers, never on the
GTK main loop (`colour()` is a dictionary read).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

__all__ = ["ArtworkDiskCache"]

VERSION = "v1"
DEFAULT_CAP = 384 * 1024 * 1024
JPEG_QUALITY = "88"


def _default_root() -> Path:
    return Path(GLib.get_user_cache_dir()) / "luma-artwork" / VERSION


class ArtworkDiskCache:
    """Scaled copies of pictures by (source, side); see the module docstring."""

    def __init__(self, root: Path | None = None, *, cap_bytes: int = DEFAULT_CAP) -> None:
        self.root = Path(root) if root is not None else _default_root()
        self.cap = max(1, cap_bytes)
        self._lock = threading.Lock()
        self._colours: dict[str, tuple[int, int, int]] | None = None
        self._colours_dirty = 0
        self._colours_saved = 0.0
        self._bytes: int | None = None  # known total, after the first scan

    # ── keys ──────────────────────────────────────────────────────────────

    @staticmethod
    def _stat_key(source: str) -> str | None:
        try:
            info = os.stat(source)
        except OSError:
            return None
        return f"{os.path.realpath(source)}\0{info.st_mtime_ns}\0{info.st_size}"

    def _path(self, stat_key: str, side: int, ext: str) -> Path:
        digest = hashlib.sha1(f"{stat_key}\0{side}".encode()).hexdigest()
        return self.root / digest[:2] / f"{digest}.{ext}"

    # ── pictures ──────────────────────────────────────────────────────────

    def load(self, source: str, side: int) -> GdkPixbuf.Pixbuf | None:
        """The cached copy of `source` drawn at `side`, or None (worker threads only)."""
        stat_key = self._stat_key(source)
        if stat_key is None:
            return None
        for ext in ("jpg", "png"):
            path = self._path(stat_key, side, ext)
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
            except GLib.Error:
                continue
            try:
                os.utime(path)  # recently used
            except OSError:
                pass
            return pixbuf
        return None

    def store(self, source: str, side: int, pixbuf: GdkPixbuf.Pixbuf) -> None:
        """Keep `pixbuf` as `source` drawn at `side` (worker threads only); failures are ignored."""
        stat_key = self._stat_key(source)
        if stat_key is None:
            return
        opaque = not pixbuf.get_has_alpha()
        path = self._path(stat_key, side, "jpg" if opaque else "png")
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            partial = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.part")
            if opaque:
                pixbuf.savev(str(partial), "jpeg", ["quality"], [JPEG_QUALITY])
            else:
                pixbuf.savev(str(partial), "png", ["compression"], ["3"])
            os.replace(partial, path)
            written = path.stat().st_size
        except (OSError, GLib.Error):
            return
        with self._lock:
            if self._bytes is not None:
                self._bytes += written
            over = self._bytes is None or self._bytes > self.cap
        if over:
            self.trim()

    def trim(self, target: int | None = None) -> None:
        """Remove least recently used entries until the cache is under `target` (90% of the cap)."""
        entries = []
        total = 0
        try:
            folders = list(os.scandir(self.root))
        except OSError:
            folders = []
        for folder in folders:
            if not folder.is_dir(follow_symlinks=False):
                continue
            try:
                for entry in os.scandir(folder.path):
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.name.startswith("."):
                        # a write that never finished (crash) is junk after an hour
                        if info.st_mtime < time.time() - 3600:
                            _unlink(entry.path)
                        continue
                    entries.append((info.st_mtime, info.st_size, entry.path))
                    total += info.st_size
            except OSError:
                continue
        limit = int(self.cap * 0.9) if target is None else target
        if total > self.cap or target is not None:
            entries.sort()
            for _mtime, size, path in entries:
                if total <= limit:
                    break
                if _unlink(path):
                    total -= size
        with self._lock:
            self._bytes = total

    # ── dominant colours ──────────────────────────────────────────────────

    def _colour_file(self) -> Path:
        return self.root / "colours.json"

    def _ensure_colours(self) -> dict[str, tuple[int, int, int]]:
        with self._lock:
            if self._colours is not None:
                return self._colours
        loaded: dict[str, tuple[int, int, int]] = {}
        try:
            raw = json.loads(self._colour_file().read_text())
            if isinstance(raw, dict):
                for source, value in raw.items():
                    if isinstance(value, str) and len(value) == 6:
                        loaded[source] = (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
        except (OSError, ValueError):
            pass
        with self._lock:
            if self._colours is None:
                self._colours = loaded
            return self._colours

    def colour(self, source: str) -> tuple[int, int, int] | None:
        """The dominant colour last measured for `source` (any size), if any."""
        colours = self._colours
        if colours is None:
            colours = self._ensure_colours()
        return colours.get(source)

    def set_colour(self, source: str, colour: tuple[int, int, int]) -> None:
        colours = self._ensure_colours()
        with self._lock:
            if colours.get(source) == colour:
                return
            colours[source] = colour
            self._colours_dirty += 1
            due = self._colours_dirty >= 64 or time.monotonic() - self._colours_saved > 5
        if due:
            self.save_colours()

    def save_colours(self) -> None:
        with self._lock:
            if not self._colours_dirty or self._colours is None:
                return
            # bounded: the colours of sources still present, newest first, at most 20000
            data = {source: "%02x%02x%02x" % value for source, value in list(self._colours.items())[-20000:]}
            self._colours_dirty = 0
            self._colours_saved = time.monotonic()
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            partial = self._colour_file().with_name(f".colours.{os.getpid()}.part")
            partial.write_text(json.dumps(data, separators=(",", ":")))
            os.replace(partial, self._colour_file())
        except OSError:
            pass


def dominant_colour(pixbuf: GdkPixbuf.Pixbuf) -> tuple[int, int, int]:
    """The picture's average colour, from a 4x4 box-filtered copy."""
    small = pixbuf.scale_simple(4, 4, GdkPixbuf.InterpType.TILES)
    data = small.read_pixel_bytes().get_data()
    stride, channels = small.get_rowstride(), small.get_n_channels()
    r = g = b = n = 0
    for y in range(4):
        for x in range(4):
            i = y * stride + x * channels
            r, g, b, n = r + data[i], g + data[i + 1], b + data[i + 2], n + 1
    return r // n, g // n, b // n


def _unlink(path: str) -> bool:
    try:
        os.unlink(path)
        return True
    except OSError:
        return False
