# SPDX-License-Identifier: GPL-3.0-only
"""Nonblocking, generation-scoped CDP requests for page attachment and sizing."""
from gi.repository import GLib


class PageRequests:
    def __init__(self, engine, submit, error):
        self.engine, self.submit, self.error = engine, submit, error
        self.pending = {}

    def clear(self):
        for timer in self.pending.values():
            GLib.source_remove(timer)
        self.pending.clear()

    def call(self, method, params, session, done):
        token = object()
        def settle(value=None, error=None, expired=False):
            timer = self.pending.pop(token, None)
            if timer is None:
                return False
            if not expired:
                GLib.source_remove(timer)
            if error is not None and not isinstance(error, TimeoutError) and not any(
                    text in str(error) for text in ('Session with given id not found',
                                                  'No target with given id', 'Target closed')):
                self.error(str(error))
            else:
                done(value)
            return False
        self.pending[token] = GLib.timeout_add(3000, lambda: settle(
            error=TimeoutError(method), expired=True))
        def received(response):
            try:
                value = response.result()
            except Exception as error:
                GLib.idle_add(settle, None, error)
            else:
                GLib.idle_add(settle, value)
        def write():
            # Only the pipe write occupies the executor, never the renderer's
            # response. Catch here so a closed target cannot fail its executor.
            if token not in self.pending:
                return
            try:
                self.engine.request(method, params, session).add_done_callback(received)
            except Exception as error:
                GLib.idle_add(settle, None, error)
        self.submit(write)
