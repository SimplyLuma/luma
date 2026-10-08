# SPDX-License-Identifier: Apache-2.0
"""GTK-free MONITOR data for the v70 port.

Values use the spec's units: memory in MiB, transfer rates in MB/s, CPU
in percent. Fixture mutations are isolated in memory and never dispatch
signals, desktop actions, profile changes, or writes to a user's store.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

RESOURCES = ('cpu', 'mem', 'disk', 'net', 'en')


def _rounded(value: float) -> int:
    # Match JavaScript Math.round for these nonnegative measurements.
    return math.floor(value + .5)


def format_value(value: float | None, resource: str) -> str:
    if resource not in RESOURCES:
        raise ValueError(f'Unknown resource: {resource}')
    if value is None or not math.isfinite(value):
        return '—'
    value = max(0, value)
    if resource == 'cpu':
        return f'{_rounded(value)}%' if value >= 10 else f'{value:.1f}%'
    if resource == 'mem':
        return f'{value / 1024:.1f} GB' if value >= 1024 else f'{_rounded(value)} MB'
    if resource == 'en':
        return 'High' if value >= 30 else 'Moderate' if value >= 10 else 'Low' if value >= 2 else 'Very low'
    return f'{value:.1f} MB/s' if value >= 1 else f'{_rounded(value * 1000)} KB/s' if value > .004 else '—'


def activity_matches(app: dict, query: str) -> bool:
    needle = query.strip().casefold()
    text = ' '.join((app['n'], *(p[0] for p in app.get('procs', ())))).casefold()
    return not needle or needle in text


def sorted_apps(apps, resource: str, *, key: str = 'v', descending: bool = True, query: str = '') -> list[dict]:
    if resource not in RESOURCES or key not in ('n', 'v'):
        raise ValueError('Unknown resource or sort key')
    shown = [a for a in apps if activity_matches(a, query)]
    if key == 'n':
        return sorted(shown, key=lambda a: a['n'].casefold(), reverse=descending)
    # Missing readings stay unavailable and sort after measured values,
    # including genuine zero. Do not conflate unavailable with idle.
    measured = [a for a in shown if a.get(resource) is not None]
    unavailable = [a for a in shown if a.get(resource) is None]
    return sorted(measured, key=lambda a: a[resource], reverse=descending) + unavailable


class FixtureSource:
    """Load one explicit fixture or fail. Never fall back to host data."""

    def __init__(self, path: str | Path):
        data = json.loads(Path(path).read_text())
        required = {'resources', 'apps', 'groups', 'totals', 'priorities'}
        if not isinstance(data, dict) or not required <= data.keys():
            raise ValueError('Incomplete MONITOR fixture')
        if (not isinstance(data['apps'], list) or not data['apps']
                or not isinstance(data['groups'], list)
                or tuple(r[0] for r in data['resources']) != RESOURCES):
            raise ValueError('Invalid MONITOR fixture inventory')
        if any(not {'id', 'n', 'sub', 'procs'} <= a.keys() for a in data['apps']):
            raise ValueError('Incomplete MONITOR app fixture')
        ids = [a['id'] for a in data['apps']]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate MONITOR fixture app')
        self.resources = copy.deepcopy(data['resources'])
        self.apps = copy.deepcopy(data['apps'])
        self.groups = copy.deepcopy(data['groups'])
        self.totals = copy.deepcopy(data['totals'])
        self.priorities = copy.deepcopy(data['priorities'])
        self.priority: dict[str, str] = {}
        self.stopped: set[str] = set()
        self.removed: set[str] = set()
        self._original = {a['id']: copy.deepcopy(a) for a in self.apps}

    def _app(self, identifier: str) -> dict:
        return next((a for a in self.apps if a['id'] == identifier), None) or self._unknown(identifier)

    @staticmethod
    def _unknown(identifier):
        raise KeyError(identifier)

    def visible_apps(self) -> list[dict]:
        return [a for a in self.apps if a['id'] not in self.removed]

    def quit(self, identifier: str) -> None:
        self._app(identifier)
        self.removed.add(identifier)

    def reopen(self, identifier: str) -> None:
        self._app(identifier)
        self.removed.discard(identifier)

    def pause(self, identifier: str) -> None:
        app = self._app(identifier)
        self.stopped.add(identifier)
        for resource in ('cpu', 'disk', 'net', 'en'):
            app[resource] = 0

    def resume(self, identifier: str) -> None:
        app = self._app(identifier)
        self.stopped.discard(identifier)
        for resource in ('cpu', 'disk', 'net', 'en'):
            app[resource] = self._original[identifier][resource]

    def set_priority(self, identifier: str, priority: str) -> None:
        self._app(identifier)
        if priority not in {p[0] for p in self.priorities}:
            raise ValueError('Unknown priority')
        self.priority[identifier] = priority

    def _process(self, pid):
        for app in self.apps:
            if any(p[1] == pid for p in app['procs']):return
        for group in self.groups:
            if any(p[1] == pid for p in group['p'] + filler_processes(group)):
                if group.get('kernel'):raise ValueError('Kernel threads cannot be controlled')
                return
        raise KeyError(pid)

    def pause_process(self, pid):
        self._process(pid);self.stopped.add('p'+str(pid))

    def resume_process(self, pid):
        self._process(pid);self.stopped.discard('p'+str(pid))

    def set_process_priority(self, pid, priority):
        self._process(pid)
        if priority not in {p[0] for p in self.priorities}:raise ValueError('Unknown priority')
        self.priority['p'+str(pid)]=priority


def fixture_totals(source: FixtureSource) -> dict:
    apps=source.visible_apps()
    def total(key):
        # v70 uses JavaScript reduce, whose sequential IEEE additions differ
        # from Python 3.12+'s compensated sum at the network rounding boundary.
        value=0
        for app in apps:value+=app.get(key) or 0
        return value
    return {'cpu':total('cpu')+6,'appsCpu':total('cpu'),'sysCpu':6,
            'mem':total('mem')+2150,'appsMem':total('mem'),'sysMem':2150,
            'cache':3100,'read':8+total('disk')*.15,'write':total('disk'),
            'down':total('net'),'up':.12+total('net')*.05,'en':total('en')}


def fixture_properties(source, app):
    pid=app['procs'][0][1];cpu_time=math.floor(app['th']*7+app['cpu']*11+.5)
    priority=source.priority.get(app['id'],'0');priority_label=dict(source.priorities)[priority]
    return [('Status','Stopped (SIGSTOP)' if app['id'] in source.stopped else 'Running'),
            ('Main process',f"{app['procs'][0][0]} · PID {pid}"),('User','nick'),
            ('Started',f'Today at 9:{10+pid%40:02d} AM'),('CPU time',f'{cpu_time//60} min {cpu_time%60} s'),
            ('Memory',f"{format_value(app['mem'],'mem')} resident · {format_value(app['mem']*.16,'mem')} shared"),
            ('Priority',f'{priority_label} · nice {priority}'),('Threads',app['th']),
            ('Sandbox','None · host access' if app['id']=='terminal' else 'Flatpak · files, network, sound'),
            ('Command line',f"/usr/bin/luma-{app['id']}"+(' --restore-session' if app['id']=='viola' else ' --tab=2' if app['id']=='terminal' else '')),
            ('Control group',f"app-flatpak-org.luma.{app['id'].capitalize()}-{pid}.scope")]


def fixture_process_properties(source, process, group):
    name,pid,user,_cpu,mem,threads=process
    kernel=group.get('kernel',False);key='p'+str(pid);priority=source.priority.get(key,'0')
    status='Sleeping (kernel thread)' if kernel else 'Stopped (SIGSTOP)' if key in source.stopped else 'Sleeping'
    return [('Status',status),('PID',pid),('User',user),
            ('Priority',f'{dict(source.priorities)[priority]} · nice {priority}'),('Threads',threads),
            ('Memory',format_value(mem,'mem')+' resident' if mem else '—'),
            ('Command line',f'[{name}]' if kernel else f"/usr/{'bin' if group['id']=='sys' else 'libexec'}/{name}"),
            ('Control group','kthreadd' if kernel else 'session.slice' if group['id']=='sys' else 'system.slice')]


def filler_processes(group):
    """v70's synthetic inventory, confined to fixture use."""
    pre={'sys':['gsd-','gvfsd-','at-spi-','dbus-','luma-'],
         'bg':['systemd-','gvfs-','flatpak-','ibus-','evolution-'],
         'kern':['kworker/','irq/','ksoftirqd/','cpuhp/','migration/']}[group['id']]
    suffix=['helper','daemon','monitor','agent','worker','broker','launcher']
    result=[]
    for i in range(group['total']-len(group['p'])):
        kernel=group.get('kernel',False)
        name=f'{pre[i%5]}{i%8}:{i*7%3}'+('H' if i%2 else '') if kernel else pre[i%5]+suffix[i*3%7]+('-'+str(i%9) if i>6 else '')
        result.append([name,4000+i*3+group['total'],'root' if kernel or not i%3 else 'nick',0,0 if kernel else 3+i*13%40,1+i%6])
    return result
