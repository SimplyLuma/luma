# SPDX-License-Identifier: GPL-3.0-or-later
"""Admit trusted Android callers before Waydroid's root hardware dispatch.

The provider owns the implementation and wire protocol. This process-local
adapter wraps its Binder service constructor without replacing provider files.
It is specific to the existing user-zero, non-UID-remapped Waydroid transport.
"""
from __future__ import annotations

from pathlib import Path

INTERFACE = 'lineageos.waydroid.IHardware'
SERVICE_NAME = 'waydroidhardware'
EX_SECURITY = -1
EX_UNSUPPORTED_OPERATION = -7


def exception_reply(local, code, message):
    reply = local.new_reply()
    # python-gbinder accepts an unsigned word even for AIDL signed int32.
    reply.append_int32(code & 0xffffffff)
    reply.append_string16(message)
    reply.append_int32(0)  # No remote stack trace payload.
    return reply, 0


def admitted(request) -> bool:
    try:
        uid = request.sender_euid()
    except Exception:
        return False
    # These are kernel Binder credentials, not a parcel field or property.
    return type(uid) is int and uid in (0, 1000)


class GuardedServiceManager:
    def __init__(self, backend, *args, **kwargs):
        self.manager = backend.ServiceManager(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.manager, name)

    def new_local_object(self, interface, handler):
        if interface != INTERFACE:
            raise RuntimeError('Unexpected Android hardware service interface')

        def guarded(request, code, flags):
            if not admitted(request):
                # AIDL SecurityException, including the empty remote-stack
                # header. Raw transport errors are normalized by libgbinder
                # and cannot reliably distinguish permission from absence.
                return exception_reply(local, EX_SECURITY, 'Android hardware access denied')
            if code not in range(1, 8):
                return exception_reply(local, EX_UNSUPPORTED_OPERATION,
                                       'Unsupported Android hardware operation')
            return handler(request, code, flags)

        local = self.manager.new_local_object(interface, guarded)
        return local


class GuardedGbinder:
    def __init__(self, backend):
        self.backend = backend

    def ServiceManager(self, *args, **kwargs):
        return GuardedServiceManager(self.backend, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.backend, name)


def install_guard(hardware):
    if hardware.INTERFACE != INTERFACE or hardware.SERVICE_NAME != SERVICE_NAME:
        raise RuntimeError('Unsupported Android hardware transport')
    if not isinstance(hardware.gbinder, GuardedGbinder):
        hardware.gbinder = GuardedGbinder(hardware.gbinder)


def verify_confinement(enforce: Path, current: Path):
    if enforce.exists():
        context = current.read_text().strip().split(':')
        if len(context) < 4 or context[2] != 'waydroid_t':
            raise RuntimeError('Android container is outside its Waydroid SELinux domain')


def main():
    import os
    import sys
    if os.getuid() != 0:
        raise SystemExit('The Android container service requires its system identity.')
    verify_confinement(Path('/sys/fs/selinux/enforce'), Path('/proc/self/attr/current'))
    sys.path.insert(0, '/usr/lib/waydroid')
    import tools
    from tools.interfaces import IHardware
    install_guard(IHardware)
    sys.argv = ['waydroid', 'container', 'start']
    os.umask(0o022)
    return tools.main()
