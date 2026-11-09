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
            # Preinstalled images are replaced by Luma's verified host update
            # path. Guest-supplied image paths never authorize host-root writes.
            image_replacement = self.interface == INTERFACE and code in (5, 6)
            if image_replacement or not admitted(request, self.allowed_uids):
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


MIRRORED_LIMITS = ('memory.high', 'memory.max', 'pids.max')
PAGE = 4096


def profile_limits(environ, meminfo=Path('/proc/meminfo')):
    """The hardware profile's ceilings for the payload cgroup LXC creates.

    LXC puts Android in lxc.payload.waydroid at the top of the hierarchy,
    outside waydroid-container.service, so the MemoryHigh=, MemoryMax= and
    TasksMax= the policy generator sets on the unit never bounded Android.
    The generator passes the same profile values in the environment; the
    unit's cgroup files are not readable in the container's SELinux domain.
    """
    total = 0
    for line in meminfo.read_text().splitlines():
        fields = line.split()
        if fields[:1] == ['MemTotal:'] and len(fields) > 1 and fields[1].isdigit():
            total = int(fields[1]) * 1024
    limits = {}
    for name, key in (('memory.high', 'LUMA_ANDROID_MEMORY_HIGH_PERCENT'),
                      ('memory.max', 'LUMA_ANDROID_MEMORY_MAX_PERCENT')):
        value = environ.get(key, '')
        if total and value.isdigit() and 0 < int(value) <= 100:
            limits[name] = str(total * int(value) // 100 // PAGE * PAGE)
    tasks = environ.get('LUMA_ANDROID_TASKS_MAX', '')
    if tasks.isdigit() and int(tasks) > 0:
        limits['pids.max'] = tasks
    return limits


def container_defines(limits):
    # Android's libprocessgroup puts every service and app in its own
    # uid_N/pid_N cgroup and kills that cgroup to end them. On a read-only
    # mount it cannot, so a framework restart left every app of the previous
    # generation running. The container's cgroup namespace keeps the
    # writable view inside its own payload cgroup.
    defines = ['-s', 'lxc.mount.auto=cgroup:rw:force']
    for name in MIRRORED_LIMITS:
        if name in limits:
            defines += ['-s', f'lxc.cgroup2.{name}={limits[name]}']
    return defines


def install_container_policy(run, defines):
    """Add Luma's container settings to the provider's lxc-start command."""
    original = run.user
    current = getattr(original, 'luma_container_defines', None)
    if current is not None:
        if current != tuple(defines):
            raise RuntimeError('Android container policy changed during startup')
        return

    def user(args, command, *rest, **kwargs):
        if isinstance(command, list) and command[:1] == ['lxc-start'] and '--' in command:
            split = command.index('--')
            command = command[:split] + list(defines) + command[split:]
        return original(args, command, *rest, **kwargs)

    user.luma_container_defines = tuple(defines)
    run.user = user


def main():
    import os
    import sys
    if os.getuid() != 0:
        raise SystemExit('The Android container service requires its system identity.')
    from .kernel import require_android_page_size
    require_android_page_size()
    verify_confinement(Path('/sys/fs/selinux/enforce'), Path('/proc/self/attr/current'))
    sys.path.insert(0, '/usr/lib/waydroid')
    import tools
    import tools.helpers.run
    from tools.interfaces import IHardware
    install_guard(IHardware)
    install_container_policy(tools.helpers.run, container_defines(profile_limits(os.environ)))
    sys.argv = ['waydroid', 'container', 'start']
    os.umask(0o022)
    return tools.main()


def disable_legacy_clipboard(provider):
    # Luma's matched image serves HIDL clipboard inside its native Wayland
    # composer. Do not also publish the unused Python/AIDL host clipboard.
    if not hasattr(provider, 'canClip'):
        raise RuntimeError('Unsupported legacy Android clipboard provider')
    provider.canClip = False
