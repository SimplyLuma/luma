# SPDX-License-Identifier: Apache-2.0
"""Bounded admission and caller-owned request/approval state."""
import threading
from concurrent.futures import ThreadPoolExecutor

MAX_INPUT = 32768
MAX_OUTPUT = 4 * 1024 * 1024

class Busy(ValueError):
    pass

class Admission:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ari-host")
        self.slots = threading.BoundedSemaphore(4)
    def reserve(self):
        if not self.slots.acquire(blocking=False):
            raise Busy("Ari is busy. Try again when a current operation finishes.")
    def submit_reserved(self, work):
        def guarded():
            try: work()
            finally: self.slots.release()
        try: return self.pool.submit(guarded)
        except BaseException:
            self.slots.release()
            raise
    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

class Owners:
    def __init__(self):
        self.lock = threading.RLock()
        self.requests = {}
        self.approvals = {}
        self.steps = {}
    def add(self, request, sender):
        with self.lock:
            if len(self.requests) >= 4: raise Busy("Too many active operations.")
            self.requests[request] = sender
    def owns(self, kind, key, sender):
        with self.lock: return getattr(self, kind).get(key) == sender
    def event(self, request, event):
        with self.lock:
            sender = self.requests.get(request) or self.steps.get(event.get("step"))
            if not sender: return None
            if event.get("type") == "approval": self.approvals[event["approval"]] = sender
            if event.get("type") == "approval_settled": self.approvals.pop(event["approval"], None)
            if event.get("type") == "step":
                if len(self.steps) >= 1024: raise Busy("Too many unreviewed changes.")
                self.steps[event["step"]] = sender
            return sender
    def vanished(self, sender):
        with self.lock:
            requests = [k for k, v in self.requests.items() if v == sender]
            for group in (self.requests, self.approvals, self.steps):
                for key in [k for k,v in group.items() if v == sender]: group.pop(key)
            return requests
