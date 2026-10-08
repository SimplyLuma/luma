# SPDX-License-Identifier: Apache-2.0
"""Unit transport declares the real native test process, never a sandbox."""
import os
from types import SimpleNamespace
class NativeSender:
    def call_sync(self, _name, _path, _interface, method, *args):
        if method not in {'GetConnectionUnixUser','GetConnectionUnixProcessID'}:
            raise AssertionError('Unexpected identity query')
        return SimpleNamespace(unpack=lambda:(os.getuid() if method=='GetConnectionUnixUser' else os.getpid(),))
