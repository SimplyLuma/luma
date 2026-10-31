#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded, read-only runtime/PSS sampling in a disposable Android test VM.

Reports observed cgroup processes and only the synthetic fixture's attribution.
PSS includes each shared page proportionally; memory.current also includes cache
and kernel memory. This tool does not measure presented frames or battery power.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import time

FIXTURE = b'org.projectluma.androidtest'


def start_time(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def counters(path):
    return {key: int(value) for key, value in
            (line.split() for line in path.read_text().splitlines())}


def pss(pid):
    for line in Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines():
        if line.startswith('Pss:'):
            return int(line.split()[1])
    raise ValueError('Missing PSS counter')


def processes(group):
    found = set()
    count = 0
    for directory, _, files in os.walk(group):
        count += 1
        if count > 10000:
            raise RuntimeError('Cgroup directory bound exceeded')
        if 'cgroup.procs' in files:
            found.update(int(value) for value in (Path(directory) / 'cgroup.procs').read_text().split())
        if len(found) > 8192:
            raise RuntimeError('Cgroup process bound exceeded')
    return sorted(found)


def sample(group):
    total, fixture, missing = 0, 0, 0
    pids = processes(group)
    fixture_count = 0
    for pid in pids:
        try:
            before = start_time(pid)
            value = pss(pid)
            # Inspect only the command identity; never record arguments or data.
            with Path(f'/proc/{pid}/cmdline').open('rb') as stream:
                name = stream.read(256).split(b'\0', 1)[0]
            if start_time(pid) != before:
                raise ProcessLookupError('Process changed while sampling')
            total += value
            if name == FIXTURE or name.startswith(FIXTURE + b':'):
                fixture += value
                fixture_count += 1
        except (OSError, ValueError, IndexError):
            missing += 1
    return {'monotonic_ns': time.monotonic_ns(),
            'cpu': counters(group / 'cpu.stat'),
            'memory_current_bytes': int((group / 'memory.current').read_text()),
            'frozen': bool(int((group / 'cgroup.freeze').read_text())),
            'observed_processes': len(pids), 'unreadable_or_changed_processes': missing,
            'observed_runtime_pss_kib': total, 'fixture_processes': fixture_count,
            'fixture_pss_kib': fixture}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run in the disposable VM as its administrator for smaps access')
    if not (1 <= args.seconds <= 300 and .5 <= args.interval <= 30):
        parser.error('Use 1..300 seconds and a 0.5..30 second interval')
    init = int(subprocess.check_output(
        ['lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'],
        timeout=5, text=True).strip())
    if init <= 1:
        raise SystemExit('Android has no valid host init PID')
    generation = start_time(init)
    entries = [line[3:] for line in Path(f'/proc/{init}/cgroup').read_text().splitlines()
               if line.startswith('0::')]
    if len(entries) != 1 or entries[0] == '/':
        raise SystemExit('No distinct unified Android cgroup')
    root = Path('/sys/fs/cgroup').resolve()
    group = (root / entries[0].lstrip('/')).resolve()
    if not group.is_relative_to(root) or group == root:
        raise SystemExit('Invalid Android cgroup path')
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(json.dumps({'kind': 'metadata', 'architecture': platform.machine(),
            'kernel': platform.release(), 'scope': 'observed Android cgroup; synthetic fixture attribution',
            'limits': 'not first-frame, isolation or physical battery qualification'}) + '\n')
        started = time.monotonic()
        first = last = None
        while True:
            if start_time(init) != generation:
                raise RuntimeError('Android runtime generation changed during sampling')
            last = sample(group)
            if start_time(init) != generation:
                raise RuntimeError('Android runtime changed while reading its counters')
            if first is None:
                first = last
            stream.write(json.dumps({'kind': 'sample', **last}) + '\n')
            stream.flush()
            remaining = args.seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(args.interval, remaining))
        elapsed = (last['monotonic_ns'] - first['monotonic_ns']) / 1e9
        usage = last['cpu']['usage_usec'] - first['cpu']['usage_usec']
        stream.write(json.dumps({'kind': 'summary', 'actual_seconds': elapsed,
            'cgroup_cpu_microseconds': usage,
            'cpu_percent_of_one_core': usage / (elapsed * 10000) if elapsed else None}) + '\n')
    print(args.output)


if __name__ == '__main__':
    main()
