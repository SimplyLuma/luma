#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Supervise one bounded publication worker and restore explicitly paused timers.

Run the small supervisor outside the worker's memory cgroup. A worker success is
not a publication/qualification receipt. The caller admits their combined RAM,
containers, native processes and storage; this tool does not bypass that policy.
"""
import argparse
import fcntl
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys

CONFIG_ENV = ('LUMA_OS_ROOT', 'LUMA_OS_PROFILE', 'LUMA_TARGET_ARCHITECTURE',
              'LUMA_OS_KEYS', 'LUMA_OS_SECRETS', 'LUMA_OS_SLICE',
              'CONTAINERS_STORAGE_CONF', 'LUMA_OS_GRAPH_TOOLS_IMAGE', 'LUMA_OS_TOOLS_IMAGE_NAME')


def worker_command(unit, command, environment, memory_mib, seconds, extra_env=()):
    if not re.fullmatch(r'luma-os-publication-[A-Za-z0-9_.-]+\.service', unit):
        raise ValueError('a unique luma-os-publication-*.service unit is required')
    if not command or not 512 <= memory_mib <= 32768 or not 10 <= seconds <= 21600:
        raise ValueError('invalid worker command or admitted memory/runtime budget')
    names = set(CONFIG_ENV) | set(extra_env)
    for name in names:
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name) or name.endswith(('_FD', '_FDS')):
            raise ValueError('invalid environment name or non-transferable inherited descriptor')
    missing = set(extra_env) - environment.keys()
    if missing:
        raise ValueError('explicit worker environment is missing: ' + ', '.join(sorted(missing)))
    # Explicit values are passed to the service manager: shell inheritance does
    # not carry caller variables across systemd-run's new service boundary.
    return ['systemd-run', '--system', '--wait', '--collect', '--service-type=exec',
            '--unit=' + unit, '--slice=' + environment.get('LUMA_OS_SLICE', 'luma-os.slice'),
            '--property=PrivateMounts=yes', '--property=KillMode=control-group',
            f'--property=MemoryMax={memory_mib}M', '--property=MemorySwapMax=256M',
            '--property=CPUQuota=200%', f'--property=RuntimeMaxSec={seconds}',
            '--property=TimeoutStopSec=20', '--property=UMask=0077',
            *['--setenv=' + name + '=' + environment[name] for name in sorted(names) if name in environment],
            '--', *command]


class SystemControl:
    def state(self, unit):
        return subprocess.check_output(['systemctl', 'show', unit, '-p', 'ActiveState', '--value'],
                                       text=True, timeout=15).strip()

    def change(self, action, unit):
        subprocess.run(['systemctl', action, unit], check=True, timeout=30)

    def worker_absent(self, unit):
        result = subprocess.check_output(['systemctl', 'show', unit, '-p', 'LoadState', '--value'],
                                         text=True, timeout=15).strip()
        return result == 'not-found'

    def stop_worker(self, unit):
        if self.worker_absent(unit):
            return
        if self.state(unit) in ("inactive", "failed"):
            return
        self.change("stop", unit)
        if self.state(unit) not in ("inactive", "failed"):
            raise RuntimeError("worker is still active; timers cannot resume")

    def run(self, command):
        limits = [arg.split('=', 2)[-1] for arg in command if arg.startswith('--property=RuntimeMaxSec=')]
        timeout = int(limits[0]) + 60 if len(limits) == 1 else 7260
        return subprocess.run(command, check=False, timeout=timeout).returncode


def save(directory, name, document):
    # Exclusive immutable phase receipts make recovery facts durable even when
    # the outer supervisor is interrupted. Never overwrite an earlier attempt.
    target = directory / name
    temporary = directory / (name + '.partial')
    with temporary.open('x') as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fchmod(stream.fileno(), 0o400); os.fsync(stream.fileno())
    if target.exists():
        raise FileExistsError(target)
    temporary.rename(target)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)



def protected_directory(path):
    for member in (path, *path.parents):
        info = member.lstat()
        # A root-owned sticky /tmp can safely contain a root-owned private leaf.
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or
                (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
            raise ValueError('state directories require protected root-owned ancestors')


@contextmanager
def attempt_lock(directory):
    # Directory inode locking avoids an extra mutable lock path. Recovery may
    # inspect a retained attempt only after its supervisor has released this
    # lock (including kernel release after an uncatchable supervisor kill).
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('attempt is still supervised; recovery cannot run concurrently') from error
        yield
    finally:
        os.close(fd)


def supervise(control, unit, timers, command, directory, preflight=None):
    # Keep the lock across STARTED, timer changes, worker cleanup and RESTORED.
    # A later recovery must not race a live supervisor before worker creation.
    if not directory.is_absolute():
        raise ValueError('state directory must be absolute')
    if not control.worker_absent(unit):
        raise ValueError('worker unit already exists; use a fresh attempt name')
    if len(set(timers)) != len(timers) or any(not re.fullmatch(r'luma-[A-Za-z0-9_.@-]+\.timer', t) for t in timers):
        raise ValueError('explicit distinct Luma timer units required')
    before = {timer: control.state(timer) for timer in timers}
    if any(state not in ('active', 'inactive') for state in before.values()):
        raise ValueError('timer state is transitional or failed; no timer changed')
    if any(control.state(timer[:-6] + '.service') not in ('inactive', 'failed') for timer in timers):
        raise ValueError('a graph refresh service is active; no timer changed')
    protected_directory(directory.parent)
    directory.mkdir(mode=0o700)
    with attempt_lock(directory):
        return _supervise(control, unit, timers, command, directory, before, preflight)


def _supervise(control, unit, timers, command, directory, before, preflight=None):
    save(directory, 'STARTED.json', {'worker_unit': unit, 'timer_states': before,
                                   'result': 'PREPARED_NOT_EXECUTED'})
    result = None
    worker_attempted = False
    restored = {}
    try:
        for timer, state in before.items():
            if state == 'active':
                control.change('stop', timer)
                if control.state(timer) != 'inactive':
                    raise RuntimeError('timer did not stop: ' + timer)
        # Pausing timers cannot cancel a refresh that fired during preflight.
        if any(control.state(timer[:-6] + '.service') not in ('inactive', 'failed') for timer in timers):
            raise RuntimeError('graph service raced timer preflight; worker was not started')
        if preflight is not None:
            preflight()
        save(directory, 'HELD.json', {'worker_unit': unit, 'paused': [t for t in timers if before[t] == 'active']})
        worker_attempted = True
        result = control.run(command)
    finally:
        # A failed/interrupted systemd-run client does not necessarily stop its unit.
        # Quiesce the owned worker before allowing refresh timers to run again.
        if worker_attempted:
            control.stop_worker(unit)
        # Restore all originally active timers, including one whose stop raised
        # after changing state. An originally inactive timer stays untouched.
        errors = []
        for timer, state in before.items():
            if state == 'active':
                try:
                    control.change('start', timer)
                    restored[timer] = control.state(timer)
                    if restored[timer] != 'active':
                        raise RuntimeError('timer did not restart: ' + timer)
                except Exception as error:
                    errors.append(str(error))
        save(directory, 'RESTORED.json', {'worker_unit': unit, 'worker_exit': result,
             'timers': restored, 'errors': errors, 'result': 'TIMERS_RESTORED' if not errors else 'RESTORE_FAILED'})
        if errors:
            raise RuntimeError('; '.join(errors))
    return result



def recover(control, directory):
    protected_directory(directory)
    with attempt_lock(directory):
        return _recover(control, directory)


def _recover(control, directory):
    for path in (directory / 'STARTED.json', directory, *directory.parents):
        info = path.lstat()
        if info.st_uid != 0 or (info.st_mode & 0o022 and not (stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX)) or stat.S_ISLNK(info.st_mode):
            raise ValueError('recovery requires protected root-owned state')
    if (directory / 'RECOVERED.json').exists() or ((directory / 'RESTORED.json').exists() and json.loads((directory / 'RESTORED.json').read_text()).get('result') == 'TIMERS_RESTORED'):
        raise ValueError('supervision already closed; recovery is not needed')
    record = json.loads((directory / 'STARTED.json').read_text())
    unit = record['worker_unit']
    if not re.fullmatch(r'luma-os-publication-[A-Za-z0-9_.-]+\.service', unit):
        raise ValueError('invalid recorded worker unit')
    if control.state(unit) not in ('inactive', 'failed'):
        raise ValueError('recorded worker is active; recovery cannot resume timers')
    for timer, state in record['timer_states'].items():
        if not re.fullmatch(r'luma-[A-Za-z0-9_.@-]+\.timer', timer) or state not in ('active', 'inactive'):
            raise ValueError('invalid recorded timer state')
    restored = {}
    for timer, state in record['timer_states'].items():
        if state == 'active':
            control.change('start', timer)
            restored[timer] = control.state(timer)
            if restored[timer] != 'active':
                raise RuntimeError('timer recovery failed: ' + timer)
    save(directory, 'RECOVERED.json', {'worker_unit': unit, 'timers': restored,
         'result': 'RECOVERED_INTERRUPTED_SUPERVISION_NOT_PUBLICATION_PASS'})



def require_equal_files(pairs):
    """Bounded metadata preflight; signature verification belongs to preparation."""
    for left, right in pairs:
        values = []
        for name in (left, right):
            path = Path(name)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
                raise ValueError('predecessor metadata must be a small regular file')
            with path.open('rb') as stream:
                value = stream.read(16 * 1024 * 1024 + 1)
            if len(value) != info.st_size:
                raise ValueError('predecessor metadata changed while reading')
            values.append(value)
        if values[0] != values[1]:
            raise ValueError('staged predecessor differs from verified preparation; reconcile before publication')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit')
    parser.add_argument('--recover', type=Path)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--timer', action='append', default=[])
    parser.add_argument('--require-equal-files', nargs=2, action='append', default=[])
    parser.add_argument('--pass-env', action='append', default=[])
    parser.add_argument('--memory-mib', type=int, default=4096)
    parser.add_argument('--runtime-seconds', type=int, default=7200)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('publication supervision requires the authorized root build host')
    if args.recover is not None:
        if args.unit or args.state_dir or args.timer or args.command or args.require_equal_files:
            parser.error('recovery accepts only its protected state directory')
        recover(SystemControl(), args.recover)
        return 0
    if not args.unit or args.state_dir is None:
        parser.error('--unit and --state-dir are required')
    # A catchable supervisor interruption must run worker/timer cleanup. A hard
    # kill still needs the explicit protected-state recovery above.
    def interrupted(signum, frame):
        raise KeyboardInterrupt('publication supervisor interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    try:
        require_equal_files(args.require_equal_files)
        worker = worker_command(args.unit, command, os.environ, args.memory_mib,
                                args.runtime_seconds, args.pass_env)
        return supervise(SystemControl(), args.unit, args.timer, worker, args.state_dir,
                         lambda: require_equal_files(args.require_equal_files))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    sys.exit(main())
