# SPDX-License-Identifier: GPL-3.0-only
"""Coalesce adjacent unsent pointer events without crossing input boundaries."""
import threading


class PendingInput:
    def __init__(self, identity, method, params):
        self.identity, self.method = identity, method
        self.params = dict(params)
        self.started = False
        self.lock = threading.Lock()

    def merge(self, identity, method, params):
        with self.lock:
            if self.started or identity != self.identity or method != self.method:
                return False
            if method != 'Input.dispatchMouseEvent':
                return False
            kind = params.get('type')
            if kind not in ('mouseMoved', 'mouseWheel') or kind != self.params.get('type'):
                return False
            if any(params.get(key) != self.params.get(key) for key in ('button', 'buttons', 'modifiers')):
                return False
            if kind == 'mouseWheel':
                if any(params.get(key) != self.params.get(key) for key in ('x', 'y')):
                    return False
                self.params['deltaX'] += params.get('deltaX', 0)
                self.params['deltaY'] += params.get('deltaY', 0)
            else:
                self.params = dict(params)
            return True

    def take(self):
        with self.lock:
            self.started = True
            return dict(self.params)
