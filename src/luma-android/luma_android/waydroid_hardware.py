# SPDX-License-Identifier: GPL-3.0-or-later
"""Admit trusted Android callers before Waydroid's root hardware dispatch.

The provider owns the implementation and wire protocol. This process-local
adapter wraps its Binder service constructor without replacing provider files.
It is specific to the existing user-zero, non-UID-remapped Waydroid transport.
"""
from __future__ import annotations

INTERFACE = 'lineageos.waydroid.IHardware'
SERVICE_NAME = 'waydroidhardware'
PERMISSION_DENIED = -1


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
                return local.new_reply(), PERMISSION_DENIED
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


def main():
    import os
    import sys
    if os.getuid() != 0:
        raise SystemExit('The Android container service requires its system identity.')
    sys.path.insert(0, '/usr/lib/waydroid')
    import tools
    from tools.interfaces import IHardware
    install_guard(IHardware)
    sys.argv = ['waydroid', 'container', 'start']
    os.umask(0o022)
    return tools.main()
