# SPDX-License-Identifier: GPL-3.0-only
"""Latest-state-wins scheduling of browser state renders.

Each published state is complete, so an intermediate state never needs its
own render. One pending callback carries the newest state; a state equal to
the last rendered one is dropped before any widget work; renders are spaced
at least one frame interval apart.
"""
import math
import threading
import time

FRAME_INTERVAL = 1 / 60


class StateRenders:
    def __init__(self, render, idle_add, timeout_add, *, interval=FRAME_INTERVAL, clock=time.monotonic):
        self.render = render
        self.idle_add = idle_add
        self.timeout_add = timeout_add
        self.interval = interval
        self.clock = clock
        self.lock = threading.Lock()
        self.pending = None
        self.scheduled = False
        self.rendered = None
        self.last_render = -math.inf
        self.metrics = {'received': 0, 'coalesced': 0, 'unchanged': 0, 'rendered': 0}

    def push(self, state):
        """Accept a state from any thread; schedules at most one callback."""
        with self.lock:
            self.metrics['received'] += 1
            if self.pending is not None:
                self.metrics['coalesced'] += 1
            self.pending = state
            if self.scheduled:
                return
            self.scheduled = True
            delay = self.last_render + self.interval - self.clock()
        if delay > 0:
            self.timeout_add(max(1, math.ceil(delay * 1000)), self.flush)
        else:
            self.idle_add(self.flush)

    def flush(self):
        """Main-loop callback: render the newest state if it changed."""
        with self.lock:
            state, self.pending, self.scheduled = self.pending, None, False
            if state is None or state == self.rendered:
                if state is not None:
                    self.metrics['unchanged'] += 1
                return False
            self.rendered = state
            self.last_render = self.clock()
            self.metrics['rendered'] += 1
        self.render(state)
        return False
