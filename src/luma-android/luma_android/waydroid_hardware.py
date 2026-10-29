# SPDX-License-Identifier: GPL-3.0-or-later
"""Admit trusted Android callers before Waydroid host-service dispatch.

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


def admitted(request, allowed_uids=(0, 1000)) -> bool:
    try:
        uid = request.sender_euid()
    except Exception:
        return False
    # These are kernel Binder credentials, not a parcel field or property.
    return type(uid) is int and uid in allowed_uids


class GuardedServiceManager:
    def __init__(self, backend, *args, interface=INTERFACE, allowed_uids=(0, 1000),
                 last_operation=7, **kwargs):
        self.interface = interface
        self.allowed_uids = allowed_uids
        self.last_operation = last_operation
        self.manager = backend.ServiceManager(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.manager, name)

    def new_local_object(self, interface, handler):
        if interface != self.interface:
            raise RuntimeError('Unexpected Android host service interface')

        def guarded(request, code, flags):
            if not admitted(request, self.allowed_uids):
                # AIDL SecurityException, including the empty remote-stack
                # header. Raw transport errors are normalized by libgbinder
                # and cannot reliably distinguish permission from absence.
                return exception_reply(local, EX_SECURITY, 'Android host service access denied')
            if code not in range(1, self.last_operation + 1):
                return exception_reply(local, EX_UNSUPPORTED_OPERATION,
                                       'Unsupported Android host operation')
            return handler(request, code, flags)

        local = self.manager.new_local_object(interface, guarded)
        return local


class GuardedGbinder:
    def __init__(self, backend, *, interface=INTERFACE, allowed_uids=(0, 1000),
                 last_operation=7):
        self.backend = backend
        self.policy = dict(interface=interface, allowed_uids=allowed_uids,
                           last_operation=last_operation)

    def ServiceManager(self, *args, **kwargs):
        return GuardedServiceManager(self.backend, *args, **self.policy, **kwargs)

    def __getattr__(self, name):
        return getattr(self.backend, name)


def install_guard(hardware, *, interface=INTERFACE, service_name=SERVICE_NAME,
                  allowed_uids=(0, 1000), last_operation=7):
    if hardware.INTERFACE != interface or hardware.SERVICE_NAME != service_name:
        raise RuntimeError('Unsupported Android host transport')
    policy = dict(interface=interface, allowed_uids=allowed_uids,
                  last_operation=last_operation)
    if isinstance(hardware.gbinder, GuardedGbinder):
        if hardware.gbinder.policy != policy:
            raise RuntimeError('Android host admission policy changed during startup')
    else:
        hardware.gbinder = GuardedGbinder(hardware.gbinder, **policy)


def install_session_guards(clipboard, user_monitor):
    for provider, descriptor, name in (
        (clipboard, 'lineageos.waydroid.IClipboard', 'waydroidclipboard'),
        (user_monitor, 'lineageos.waydroid.IUserMonitor', 'waydroidusermonitor'),
    ):
        # These clients run in system_server. APKs use Android clipboard/focus
        # and package APIs, which enforce the application's own permissions.
        install_guard(provider, interface=descriptor, service_name=name,
                      allowed_uids=(1000,), last_operation=2)


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
