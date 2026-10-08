# SPDX-License-Identifier: Apache-2.0
"""Cooperative account commits shared by native UI, mail agent and MailHost.

Locks contain no credentials. Stable bounded stripes coordinate processes;
thread-local reentrancy permits transport refresh inside an account operation.
"""
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import threading
import time

_STRIPES = tuple(threading.RLock() for _ in range(64))
_HELD = threading.local()
_DESCRIPTORS = set()
_DESCRIPTOR_LOCK = threading.Lock()


def _after_fork():
    global _STRIPES, _HELD, _DESCRIPTORS, _DESCRIPTOR_LOCK
    # A child must neither inherit reentrancy authority nor keep the parent's
    # flock open-file-description alive after its parent releases it.
    for descriptor in _DESCRIPTORS:
        try: os.close(descriptor)
        except OSError: pass
    _STRIPES = tuple(threading.RLock() for _ in range(64))
    _HELD = threading.local()
    _DESCRIPTORS = set()
    _DESCRIPTOR_LOCK = threading.Lock()


def _before_fork(): _DESCRIPTOR_LOCK.acquire()
def _parent_after_fork(): _DESCRIPTOR_LOCK.release()


os.register_at_fork(before=_before_fork,
                    after_in_parent=_parent_after_fork,
                    after_in_child=_after_fork)


def stripe(identifier):
    return int.from_bytes(hashlib.sha256(identifier.encode('utf-8')).digest()[:2], 'big') % 64


def _waiting(cancelled, deadline):
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError('Account operation cancelled.')
    if time.monotonic() >= deadline:
        raise TimeoutError('Account operation is busy.')


@contextmanager
def _local_stripe(index, cancelled, deadline):
    lock = _STRIPES[index]
    while not lock.acquire(timeout=0.05): _waiting(cancelled, deadline)
    try:
        _waiting(cancelled, deadline)
        yield
    finally: lock.release()


@contextmanager
def _stripe_lock(store_path, index, cancelled, deadline):
    directory = Path(store_path).parent / '.account-lifecycle'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('Account lifecycle directory is unsafe.')
    key = (str(directory.resolve()), index)
    with _local_stripe(index, cancelled, deadline):
        held = getattr(_HELD, 'locks', None)
        if held is None: held = _HELD.locks = set()
        if key in held:
            yield
            return
        born = os.getpid()
        with _DESCRIPTOR_LOCK:
            descriptor = os.open(directory / f'{index:02d}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            _DESCRIPTORS.add(descriptor)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise PermissionError('Account lifecycle file is unsafe.')
            while True:
                _waiting(cancelled, deadline)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError: time.sleep(0.05)
            held.add(key)
            try: yield
            finally:
                if os.getpid() == born:
                    held.remove(key); fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            if os.getpid() == born:
                with _DESCRIPTOR_LOCK:
                    _DESCRIPTORS.remove(descriptor); os.close(descriptor)


@contextmanager
def account_locks(store_path, identifiers, *, cancelled=None):
    deadline = time.monotonic() + 60
    with ExitStack() as stack:
        for index in sorted({stripe(identifier) for identifier in identifiers}):
            stack.enter_context(_stripe_lock(store_path, index, cancelled, deadline))
        yield


def account_lock(store_path, identifier):
    return account_locks(store_path, (identifier,))
