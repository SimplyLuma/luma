# SPDX-License-Identifier: Apache-2.0
"""Independent Undo windows for confirmed source removals."""


class DeferredRemovals:
    def __init__(self, schedule, cancel, commit, delay=4500):
        self.schedule, self.cancel, self.commit, self.delay = schedule, cancel, commit, delay
        self.pending = {}

    def add(self, identifier):
        if identifier in self.pending:
            return
        token = object()
        timer = self.schedule(self.delay, lambda: self._expire(identifier, token))
        self.pending[identifier] = timer, token

    def _expire(self, identifier, token):
        entry = self.pending.get(identifier)
        if entry is not None and entry[1] is token:
            self.pending.pop(identifier)
            self.commit(identifier)
        return False

    def undo(self, identifier):
        entry = self.pending.pop(identifier, None)
        if entry is not None:
            self.cancel(entry[0])

    def flush(self):
        for identifier, (timer, _token) in tuple(self.pending.items()):
            self.pending.pop(identifier)
            self.cancel(timer)
            self.commit(identifier)
