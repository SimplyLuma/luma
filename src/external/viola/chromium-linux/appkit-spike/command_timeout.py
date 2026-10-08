# SPDX-License-Identifier: GPL-3.0-only
"""Contain operational command timeouts without replaying browser actions."""
from collections import deque
import time


class EngineCommandTimeout(TimeoutError):
    def __init__(self, method, timeout, callers):
        super().__init__(f'{method} did not answer within {timeout} seconds')
        self.method = method
        # Only source filenames/function names/line numbers, never expressions,
        # parameters, clipboard text or page URLs.
        self.callers = callers


class CommandTimeoutPolicy:
    def __init__(self, engine_alive, notify, record, clock=time.monotonic):
        self.engine_alive, self.notify, self.record = engine_alive, notify, record
        self.clock = clock
        self.ready = False
        self.count = 0
        self.recent = deque(maxlen=32)
        self.last_notice = float('-inf')

    def handle(self, exception):
        if not isinstance(exception, TimeoutError) or not self.ready or not self.engine_alive():
            return False
        self.count += 1
        item = {'method': getattr(exception, 'method', 'service-state-wait'),
                'callers': getattr(exception, 'callers', [])}
        self.recent.append(item)
        self.record(item)
        now = self.clock()
        if now - self.last_notice >= 60:
            self.last_notice = now
            self.notify()
        # Do not call success continuations or replay an action: the engine may
        # already have committed it despite not answering before the deadline.
        return True

    def snapshot(self):
        return {'count': self.count, 'recent': list(self.recent)}
