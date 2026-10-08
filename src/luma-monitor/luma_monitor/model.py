# SPDX-License-Identifier: Apache-2.0
"""Unprivileged kernel samples. Missing and reset counters never become zero."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import pwd
import re
import time
from collections import deque


def fields(text):
    result = {}
    for line in text.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            result[key] = value.strip()
    return result


def number(value, default=None):
    try:
        return int(value.split()[0])
    except (AttributeError, IndexError, TypeError, ValueError):
        return default


def read(path):
    try:
        return Path(path).read_text(errors='replace')
    except (OSError, ValueError):
        return None


def stat_record(text):
    # comm can contain spaces and parentheses. Fields after its last ')' have
    # stable indices; splitting the entire stat line would silently shift them.
    close = text.rfind(')')
    if close < 0:
        raise ValueError('Malformed process stat')
    rest = text[close + 2:].split()
    return {'pid': int(text.split(' ', 1)[0]), 'comm': text[text.index('(') + 1:close],
            'state': rest[0], 'ppid': int(rest[1]),
            'ticks': int(rest[11]) + int(rest[12]), 'start': int(rest[19])}


def unescape_unit(value):
    return re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m[1], 16)), value)


def application_unit(cgroup):
    for segment in reversed(cgroup.split('/')):
        if segment.startswith('app-') and segment.endswith(('.scope', '.service')):
            return segment
    return None


def identity_candidates(cgroup):
    # Session D-Bus activation uses a systemd template rather than an app scope.
    # This identity survives closing the last window (for example Tide playback).
    for segment in reversed(cgroup.split('/')):
        match = re.fullmatch(r'dbus-:[0-9]+\.[0-9]+-(.+)@[0-9]+\.service', segment)
        if match:
            return (unescape_unit(match[1]),)
    unit = application_unit(cgroup)
    if not unit:
        snap = next((part for part in cgroup.split('/') if part.startswith('snap.') and part.endswith('.scope')), '')
        match = re.match(r'snap\.([^.]+)\.(.+?)-[0-9a-f-]+\.scope$', snap)
        return (f'{match[1]}_{match[2]}',) if match else ()
    name = unescape_unit(unit.rsplit('.', 1)[0]).removeprefix('app-').split('@', 1)[0]
    for prefix in ('flatpak-', 'gnome-', 'KDE-'):
        name = name.removeprefix(prefix)
    # App IDs can contain dashes. Match the longest known desktop ID first,
    # then remove only launch-instance suffixes while trying candidates.
    return tuple(dict.fromkeys([name] + [name.rsplit('-', n)[0] for n in range(1, name.count('-') + 1)]))


@dataclass(frozen=True)
class Identity:
    id: str
    name: str
    icon: str = 'application-x-executable-symbolic'
    search: str = ''


@dataclass
class Process:
    pid: int
    start: int
    uid: int
    name: str
    state: str
    threads: int
    cgroup: str
    identity: Identity | None
    cpu: float | None
    age: float
    memory: int | None
    cached: int | None
    reading: float | None
    writing: float | None

    ppid: int = 0
    rss: int | None = None

    @property
    def key(self):
        return f'pid:{self.pid}:{self.start}'


def complete_sum(values):
    values = list(values)
    return sum(values) if values and all(x is not None for x in values) else None


def rate(now, before, elapsed):
    if now is None or before is None or now < before or elapsed <= 0:
        return None
    return (now - before) / elapsed


class Sampler:
    def __init__(self, proc='/proc', sys='/sys', resolve=None):
        self.proc, self.sys = Path(proc), Path(sys)
        self.resolve = resolve or (lambda group, pid, command: None)
        self.hz = os.sysconf('SC_CLK_TCK')
        self.previous = None
        self.users = {}
        self.impacts = {}

    def reset(self):
        self.previous = None

    def sample(self, *, memory=False, now=None):
        now = time.monotonic() if now is None else now
        previous = self.previous or {}
        elapsed = now - previous.get('time', now)
        stat = read(self.proc / 'stat') or ''
        cpus = {}
        for line in stat.splitlines():
            if re.match(r'^cpu\d* ', line):
                parts = line.split()
                # guest and guest_nice are already included in user/nice.
                ticks = list(map(int, parts[1:9]))
                cpus[parts[0]] = (sum(ticks), ticks[3] + (ticks[4] if len(ticks) > 4 else 0))
        total_delta = cpus.get('cpu', (0, 0))[0] - previous.get('cpus', {}).get('cpu', (0, 0))[0]
        cpu_values = {}
        for name, (total, idle) in cpus.items():
            before = previous.get('cpus', {}).get(name)
            delta = total - before[0] if before else 0
            cpu_values[name] = (max(0, min(100, 100 * (1 - (idle - before[1]) / delta)))
                                if before and delta > 0 and idle >= before[1] else None)
        uptime = float((read(self.proc / 'uptime') or '0').split()[0])
        mem = {k: number(v, 0) * 1024 for k, v in fields(read(self.proc / 'meminfo') or '').items()}
        processes, counters = [], {}
        for directory in self.proc.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                record = stat_record((directory / 'stat').read_text())
                status = fields((directory / 'status').read_text())
                uid = number(status.get('Uid'))
                if uid is None:
                    continue
                cgroups = (directory / 'cgroup').read_text().splitlines()
                cgroup = next((line.split(':', 2)[2] for line in cgroups if line.startswith('0::')), '')
                if not cgroup:
                    cgroup = next((line.split(':', 2)[2] for line in cgroups if ':name=systemd:' in line), '')
                command = (directory / 'cmdline').read_bytes().split(b'\0')
                command = [x.decode(errors='replace') for x in command if x]
                identity = self.resolve(cgroup, record['pid'], command)
                io = fields(read(directory / 'io') or '')
                reading, writing = number(io.get('read_bytes')), number(io.get('write_bytes'))
                key = (record['pid'], record['start'])
                before = previous.get('processes', {}).get(key)
                cpu = (max(0, 100 * (record['ticks'] - before[0]) / total_delta)
                       if before and total_delta > 0 and record['ticks'] >= before[0] else None)
                pss = None
                if memory:
                    rollup = fields(read(directory / 'smaps_rollup') or '')
                    pss = number(rollup.get('Pss'))
                    pss = None if pss is None else pss * 1024
                cached = None  # RSS file pages are not the cgroup page cache.
                rss = number(status.get('VmRSS'))
                rss = rss * 1024 if rss is not None else None
                # Reject a PID reused during this sample rather than combining
                # the old process's counters with the new process's identity.
                if stat_record((directory / 'stat').read_text())['start'] != record['start']:
                    continue
                processes.append(Process(record['pid'], record['start'], uid, record['comm'],
                    record['state'], number(status.get('Threads'), 0), cgroup, identity, cpu,
                    max(0, uptime - record['start'] / self.hz), pss,
                    None if cached is None else cached * 1024,
                    rate(reading, before[1], elapsed) if before else None,
                    rate(writing, before[2], elapsed) if before else None, record['ppid'], rss))
                counters[key] = (record['ticks'], reading, writing)
            except (OSError, ValueError, IndexError, ProcessLookupError):
                continue
        by_pid = {p.pid: p for p in processes}
        for process in processes:
            ancestor, visited = process, set()
            while process.identity is None and ancestor.ppid in by_pid and ancestor.ppid not in visited:
                visited.add(ancestor.ppid)
                ancestor = by_pid[ancestor.ppid]
                if ancestor.uid != process.uid:
                    break
                process.identity = ancestor.identity
        rows = group_processes([p for p in processes if p.pid != 2 and p.ppid != 2], self.users)
        active = {r['id'] for r in rows if not r['background']}
        self.impacts = {k: v for k, v in self.impacts.items() if k in active}
        for row in rows:
            if row['background']:
                continue
            history = self.impacts.setdefault(row['id'], deque())
            history.append((now, row['cpu']))
            while history and history[0][0] <= now - 60:
                history.popleft()
            values = [v for _, v in history if v is not None]
            row['impact'] = sum(values) / len(values) if values else None
            groups = {p.cgroup for p in row['members']}
            # Parent cgroup counters already include descendants.
            groups = {group for group in groups if not any(group.startswith(parent.rstrip('/') + '/') for parent in groups if parent != group)}
            cache = []
            for group in groups:
                if not application_unit(group):
                    cache.append(None)
                    continue
                # memory.stat is whitespace-separated, not proc's colon format.
                pairs = dict(line.split() for line in (read(self.sys / 'fs/cgroup' / group.lstrip('/') / 'memory.stat') or '').splitlines() if len(line.split()) == 2)
                cache.append(number(pairs.get('file')))
            row['cached'] = complete_sum(cache)

        network = {}
        for interface in (self.sys / 'class/net').glob('*'):
            if interface.name == 'lo' or not (interface / 'device').exists():
                continue
            rx = number(read(interface / 'statistics/rx_bytes'))
            tx = number(read(interface / 'statistics/tx_bytes'))
            if rx is not None and tx is not None:
                network[interface.name] = (rx, tx)
        # Use physical/block top-level disks only; counting partitions and their
        # parent doubles IO. Device-mapper is excluded when backed by slaves.
        disks = {}
        for line in (read(self.proc / 'diskstats') or '').splitlines():
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            block = self.sys / 'class/block' / name
            if (block / 'partition').exists() or name.startswith(('loop', 'ram', 'zram')):
                continue
            if (block / 'slaves').exists() and any((block / 'slaves').iterdir()):
                continue
            disks[name] = (int(parts[5]) * 512, int(parts[9]) * 512)
        def device_rates(current, label):
            before = previous.get(label, {})
            # New/reset devices have no measured delta for this interval.
            return tuple(complete_sum(rate(pair[i], before.get(name, (None, None))[i], elapsed)
                         for name, pair in current.items()) for i in (0, 1))
        busy = cpu_values.get('cpu')
        app_cpu = complete_sum(row['cpu'] for row in rows if not row['background'])
        if not any(not row['background'] for row in rows) and previous:
            app_cpu = 0.0
        system_cpu = max(0, busy - app_cpu) if busy is not None and app_cpu is not None else None
        snapshot = {'rows': rows, 'processes': processes, 'uptime': uptime, 'cpu': busy,
                    'apps_cpu': app_cpu, 'system_cpu': system_cpu,
                    'idle_cpu': 100 - busy if busy is not None else None,
                    'cores': [cpu_values[k] for k in sorted(cpu_values, key=lambda x: int(x[3:] or -1)) if k != 'cpu'],
                    'memory': mem, 'disk_rates': device_rates(disks, 'disks'),
                    'network_rates': device_rates(network, 'network'), 'network_received': complete_sum(v[0] for v in network.values()), 'background_count': sum(p.identity is None for p in processes), 'time': now}
        self.previous = {'time': now, 'cpus': cpus, 'processes': counters, 'network': network, 'disks': disks}
        return snapshot


def group_processes(processes, users):
    groups = {}
    for process in processes:
        key = 'app:' + process.identity.id if process.identity else process.key
        groups.setdefault(key, []).append(process)
    rows = []
    for key, members in groups.items():
        leader = min(members, key=lambda p: p.start)
        identity = leader.identity
        for process in members:
            if process.uid not in users:
                try:
                    users[process.uid] = pwd.getpwuid(process.uid).pw_name
                except KeyError:
                    users[process.uid] = str(process.uid)
        rows.append({'id': key, 'name': identity.name if identity else leader.name, 'search': identity.search if identity else leader.name, 'icon': identity.icon if identity else 'utilities-terminal-symbolic',
                     'background': identity is None, 'members': members,
                     'cpu': complete_sum(p.cpu for p in members),
                     'memory': complete_sum(p.memory if p.memory is not None or identity else p.rss for p in members),
                     'cached': complete_sum(p.cached for p in members) if identity else None,
                     'reading': complete_sum(p.reading for p in members),
                     'writing': complete_sum(p.writing for p in members),
                     'age': max(p.age for p in members) if identity else None,
                     'pid': leader.pid, 'user': ', '.join(sorted({users[p.uid] for p in members})),
                     'threads': sum(p.threads for p in members),
                     'receiving': None, 'sending': None, 'impact': None, 'inhibits': None})
    return rows


def mount_unescape(text):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), text)


def filesystems(proc='/proc', sys='/sys'):
    result = []
    for line in (read(Path(proc) / 'self/mountinfo') or '').splitlines():
        try:
            before, after = line.split(' - ', 1)
            info, detail = before.split(), after.split()
            device, mount = info[2], mount_unescape(info[4])
            if mount not in ('/', '/home', '/var/home', '/boot/efi') and not mount.startswith(('/run/media/', '/media/')):
                continue
            if detail[0] in ('squashfs', 'overlay', 'tmpfs') or not (Path(sys) / 'dev/block' / device).exists():
                continue
            stats = os.statvfs(mount)
            total, free = stats.f_blocks * stats.f_frsize, stats.f_bavail * stats.f_frsize
            used = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
            result.append({'id': f'mount:{info[0]}', 'name': {'/': 'System', '/home': 'Home', '/var/home': 'Home', '/boot/efi': 'Boot'}.get(mount, Path(mount).name),
                           'mount': mount, 'type': detail[0], 'total': total,
                           'free': free, 'used': used / total * 100 if total else 0})
        except (ValueError, IndexError, OSError):
            continue
    return result
