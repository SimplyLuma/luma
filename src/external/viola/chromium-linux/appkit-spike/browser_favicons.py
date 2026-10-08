# SPDX-License-Identifier: GPL-3.0-only
"""Bounded GTK icon cache over Chromium's existing favicon service."""
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from gi.repository import Gdk, GLib


class BrowserFavicons:
    def __init__(self, services):
        self.services = services
        self.cache = OrderedDict()
        self.pending = {}
        self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='viola-favicons')

    def request(self, url, callback, revision=None):
        if self.closed:
            return
        key = (url, revision)
        if key in self.cache:
            self.cache.move_to_end(key)
            callback(self.cache[key])
            return
        if key in self.pending:
            self.pending[key].append(callback)
            return
        if len(self.pending) >= 256:
            return
        self.pending[key] = [callback]
        future = self.pool.submit(self.services.favicon, url, revision)
        def completed(result):
            try:
                data = result.result()
            except Exception:
                data = None
            GLib.idle_add(self.received, key, data)
        future.add_done_callback(completed)

    def received(self, key, data):
        callbacks = self.pending.pop(key, [])
        if self.closed:
            return GLib.SOURCE_REMOVE
        texture = None
        try:
            if data and data.startswith('data:image/png;base64,') and len(data) <= 131072:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(
                    base64.b64decode(data.split(',', 1)[1], validate=True)))
        except Exception:
            pass
        self.cache[key] = texture
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        for callback in callbacks:
            callback(texture)
        return GLib.SOURCE_REMOVE

    def close(self):
        self.closed = True
        # Closing one window must not block every other GTK window while a
        # pending favicon fetch reaches its bounded browser-side timeout.
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.pending.clear()
        self.cache.clear()
