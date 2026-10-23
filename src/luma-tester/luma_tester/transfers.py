# SPDX-License-Identifier: Apache-2.0
"""Measured transfer and verification progress shared by CLI and native UI."""
from dataclasses import dataclass
import hashlib
import time


def size_text(value):
    for divisor, unit in ((10**9, 'GB'), (10**6, 'MB'), (10**3, 'kB')):
        if value >= divisor:
            return f'{value / divisor:.1f} {unit}'
    return f'{value} B'


@dataclass(frozen=True)
class TransferProgress:
    phase: str
    received: int
    total: int

    @property
    def fraction(self):
        return min(1.0, max(0.0, self.received / self.total)) if self.total else 0.0

    @property
    def message(self):
        if self.phase == 'connecting':
            return 'Connecting to download…'
        if self.phase == 'ready':
            return 'Image verified.'
        title = {'downloading': 'Downloading', 'copying': 'Copying', 'verifying': 'Verifying'}[self.phase]
        return f'{title} · {self.fraction:.0%} · {size_text(self.received)} of {size_text(self.total)}'


class TransferReporter:
    def __init__(self, total, report=lambda message: None, progress=None):
        self.total, self.report, self.progress = total, report, progress
        self.phase, self.last = None, 0.0

    def update(self, phase, received, force=False):
        now = time.monotonic()
        if not force and phase == self.phase and now - self.last < .2:
            return
        self.phase, self.last = phase, now
        value = TransferProgress(phase, received, self.total)
        self.report(value.message)
        if self.progress:
            self.progress(value)

    def digest(self, path):
        self.update('verifying', 0, force=True)
        checksum, count = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while chunk := stream.read(1024**2):
                checksum.update(chunk)
                count += len(chunk)
                self.update('verifying', count)
        self.update('verifying', count, force=True)
        return checksum.hexdigest()
