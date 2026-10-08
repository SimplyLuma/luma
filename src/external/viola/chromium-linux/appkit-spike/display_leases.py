# SPDX-License-Identifier: GPL-3.0-only
"""Bounded sender-owned lifetime of Viola's existing private compositor."""
import threading
import uuid


class DisplayLeases:
    def __init__(self, factory, capacity=4):
        self.factory = factory
        self.capacity = capacity
        self.lock = threading.Lock()
        self.leases = {}
        self.occupied = 0

    def reserve(self, owner):
        with self.lock:
            if self.occupied >= self.capacity:
                raise RuntimeError('The browser has too many private displays.')
            handle = uuid.uuid4().hex
            self.leases[handle] = (owner, None)
            self.occupied += 1
            return handle

    def create(self, owner, handle):
        display = None
        try:
            with self.lock:
                if self.leases.get(handle) != (owner, None):
                    raise RuntimeError('The browser connection closed before startup.')
            display = self.factory()
            with self.lock:
                if self.leases.get(handle) != (owner, None):
                    raise RuntimeError('The browser connection closed during startup.')
                self.leases[handle] = (owner, display)
            return display
        except Exception:
            with self.lock:
                if self.leases.get(handle) == (owner, None):
                    del self.leases[handle]
            try:
                if display is not None:
                    display.close()
            finally:
                with self.lock:
                    self.occupied -= 1
            raise

    def release(self, owner, handle):
        with self.lock:
            lease = self.leases.get(handle)
            if lease is None or lease[0] != owner:
                raise PermissionError('The private display belongs to another connection.')
            del self.leases[handle]
        if lease[1] is not None:
            self.close_detached(lease[1])

    def detach(self, owner):
        with self.lock:
            displays = [display for sender, display in self.leases.values()
                        if sender == owner and display is not None]
            self.leases = {handle: lease for handle, lease in self.leases.items()
                           if lease[0] != owner}
        return displays

    def close_detached(self, display):
        # A display being stopped still occupies its slot. New startup cannot
        # overtake cleanup and create an unbounded number of native processes.
        display.close()
        with self.lock:
            self.occupied -= 1

    def disconnect(self, owner):
        for display in self.detach(owner):
            self.close_detached(display)

    def empty(self):
        with self.lock:
            return self.occupied == 0
