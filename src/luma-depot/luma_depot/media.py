# SPDX-License-Identifier: Apache-2.0
"""Images for the window, fetched off the main thread and verified by digest.

The window asks for a path and gets one only once a verified file exists. A
request that is still downloading returns nothing and the window draws what it
would draw without the image; when the file lands, the window is told once.

A failure is never final. The first thing a new computer does is open Depot on
whatever network it has, and a download that failed then would otherwise stay
failed for as long as the window stayed open: the icon would be missing until
the person closed Depot and opened it again. So a failed digest is forgotten
after a short wait, and at once when the machine reports a usable route again,
and the window redraws when the bytes arrive.
"""

from __future__ import annotations

from functools import lru_cache
import time

from gi.repository import Gio, GLib

from luma_installer import depot_media

from .providers import ProviderError, run_async

#: How long a failed image is left alone before it is worth trying again.
#: Short enough that a slow first boot fills in while somebody is still
#: looking at the shelf, long enough that a genuinely absent image is not
#: re-fetched on every redraw.
RETRY_SECONDS = 45


class MediaLoader:
    def __init__(self, on_ready, *, monitor=None, clock=time.monotonic) -> None:
        self.on_ready = on_ready
        self.pending: set[str] = set()
        #: digest -> when it failed, so it can be retried rather than dropped.
        self.failures: dict[str, float] = {}
        self.clock = clock
        self._notify = 0
        self._monitor = monitor if monitor is not None else Gio.NetworkMonitor.get_default()
        self._monitor_handler = 0
        if self._monitor is not None:
            self._monitor_handler = self._monitor.connect('network-changed', self._network_changed)

    # ── What the window asks for ─────────────────────────────────────────

    def path(self, url: str, sha256: str) -> str:
        if not url or not sha256:
            return ''
        found = depot_media.cached(sha256)
        if found is not None:
            return str(found)
        if sha256 in self.pending or self._waiting(sha256):
            return ''
        self.failures.pop(sha256, None)
        self.pending.add(sha256)

        def work():
            try:
                return str(depot_media.fetch(url, sha256))
            except depot_media.MediaError as error:
                raise ProviderError(str(error), hint=str(error)) from error

        run_async(work, lambda result, digest=sha256: self._done(digest, result))
        return ''

    def failed_for(self, sha256: str) -> bool:
        """True while this image is not worth asking about again yet."""
        return self._waiting(sha256)

    def retry_now(self) -> None:
        """Forget every failure: something changed that could fix them."""
        if self.failures:
            self.failures.clear()
            self._schedule()

    def close(self) -> None:
        if self._monitor_handler and self._monitor is not None:
            self._monitor.disconnect(self._monitor_handler)
            self._monitor_handler = 0

    # ── Inside ───────────────────────────────────────────────────────────

    def _waiting(self, sha256: str) -> bool:
        failed_at = self.failures.get(sha256)
        if failed_at is None:
            return False
        if self.clock() - failed_at >= RETRY_SECONDS:
            del self.failures[sha256]
            return False
        return True

    def _network_changed(self, _monitor, available) -> None:
        if available:
            self.retry_now()

    def _done(self, sha256, result) -> None:
        self.pending.discard(sha256)
        if not result.ok:
            self.failures[sha256] = self.clock()
        self._schedule()

    def _schedule(self) -> None:
        if not self._notify:
            # Several images usually land together; draw once for all of them.
            self._notify = GLib.timeout_add(120, self._flush)

    def _flush(self):
        self._notify = 0
        self.on_ready()
        return GLib.SOURCE_REMOVE


#: Screenshots are drawn at most this wide (a 246 px frame at up to 4x scale):
#: decoding a 1600 px original for a thumbnail strip only costs memory.
SHOT_TEXTURE_WIDTH = 984


@lru_cache(maxsize=64)
def texture(path: str, width: int = SHOT_TEXTURE_WIDTH):
    """A picture file as a plain RGBA texture every GSK renderer can draw.

    The catalogue's screenshots are WebP. GTK decodes those through glycin
    into textures some renderers cannot upload (the Broadway renderer draws
    nothing for them), so they are decoded here with GdkPixbuf, scaled to
    what the strip needs, and handed over as an ordinary memory texture.
    None when the file cannot be read; the frame then stays empty.
    """
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, GdkPixbuf
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, -1, True)
    except GLib.Error:
        return None
    memory = Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(), memory,
                                 pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())
