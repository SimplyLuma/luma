#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Time from Monitor's window mapping to its first app rows being painted.

Runs the real MonitorWindow against a synthetic machine the size of a busy
desktop (about 1000 processes in 280 rows), through both the in-process
sampler and the host D-Bus broker, with and without a cached last state.
Sampling cost is reproduced as CPU work, so the numbers include the real
encode/decode, grouping and GTK layout cost. No real process list is used.

    first_entries.py [--source DIR] [--budget-ms 200] [--cold-budget-ms 400]

Scenarios: "direct" (no cache, in-process sampler as the RPM runs),
"host" (no cache, through the host broker as the Flatpak runs) and "warm"
(the host path again, over the list the previous launch left). The warm
launch is held to --budget-ms, the cold ones to --cold-budget-ms; a scenario
over budget is run once more and the better time counts.

--source is the directory holding luma_monitor (default: this checkout;
/usr/share/luma-monitor for the installed package). Needs GTK 4, libadwaita,
PyGObject, LumaUI (luma_appkit) and dbus-run-session; it runs itself on a
private session bus, under xvfb-run when that is installed. Prints one JSON
line and exits 0 on pass, 1 on fail.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
SCRIPT = str(Path(__file__).resolve())
CHECK = 'monitor-first-entries'

# Dell-class measurements (CPU seconds) of the host-side work per call.
COST = {'sample': .086, 'memory': .316, 'authenticate': .058}


def spin(seconds):
    """Spend thread CPU time, not wall time, so CPU limits apply to it."""
    end = time.thread_time() + seconds
    while time.thread_time() < end:
        pass


def synthetic(apps=36, app_processes=470, background=250, kernel=290, seed=7):
    """A process table shaped like a busy desktop: app trees, services, kthreads."""
    from luma_monitor.model import Identity, Process
    import random
    rng = random.Random(seed)
    processes = []
    pid = 3

    def add(name, identity, cgroup, ppid=1, uid=1000):
        nonlocal pid
        pid += 1
        processes.append(Process(pid, 1000 + pid, uid, name, 'S', rng.randint(1, 40), cgroup, identity,
                                 None, 100.0, None, None, None, None, ppid, rng.randint(1, 400) * 1024 ** 2))
        return pid

    for index in range(apps):
        identity = Identity(f'org.example.App{index:02d}', f'Example App {index:02d}',
                            'application-x-executable-symbolic', '')
        cgroup = f'/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.example.App{index:02d}.scope'
        leader = add(f'app{index:02d}', identity, cgroup)
        for child in range(app_processes // apps - 1):
            add(f'app{index:02d}-worker{child}', identity, cgroup, leader)
    for index in range(background):
        add(f'service-{index:03d}', None, f'/user.slice/user-1000.slice/user@1000.service/session.slice/s{index}.service')
    for index in range(kernel):
        add(f'kworker/{index}:0', None, '/', 2, 0)
    return processes


class SyntheticSampler:
    """Sampler's interface over a fixed synthetic table, with Dell-like CPU cost."""
    host = False

    def __init__(self, resolve=None, *, cost=1.0):
        self.processes = synthetic()
        self.previous = None
        self.users = {}
        self.cost = cost
        self.impacts = {}

    def reset(self):
        self.previous = None

    def sample(self, *, memory=False, now=None):
        import copy
        import random
        from luma_monitor.model import group_processes
        spin(COST['memory' if memory else 'sample'] * self.cost)
        measured = self.previous is not None
        processes = copy.deepcopy(self.processes)
        for p in processes:
            if measured:
                p.cpu = random.random() * 3
                p.reading = p.writing = 0.0
            if memory:
                p.memory = p.rss
        rows = group_processes([p for p in processes if p.pid != 2 and p.ppid != 2], self.users)
        for row in rows:
            row['impact'] = row['cpu']
        busy = 23.0 if measured else None
        apps_cpu = sum(r['cpu'] or 0 for r in rows if not r['background']) if measured else None
        self.previous = {'time': time.monotonic()}
        memory_info = {'MemTotal': 16 * 1024 ** 3, 'MemAvailable': 9 * 1024 ** 3, 'Cached': 3 * 1024 ** 3,
                       'SwapTotal': 8 * 1024 ** 3, 'SwapFree': 8 * 1024 ** 3, 'Committed_AS': 14 * 1024 ** 3}
        return {'rows': rows, 'processes': processes, 'uptime': 1000.0, 'cpu': busy,
                'apps_cpu': apps_cpu, 'system_cpu': max(0, busy - apps_cpu) if measured else None,
                'idle_cpu': 100 - busy if measured else None,
                'cores': [busy] * 8 if measured else [None] * 8, 'memory': memory_info,
                'disk_rates': (0.0, 0.0) if measured else (None, None),
                'network_rates': (0.0, 0.0) if measured else (None, None),
                'network_received': 10 ** 9, 'background_count': 540, 'time': time.monotonic()}


class QuietCatalog:
    entries = hidden_entries = {}
    def refresh_running(self): pass
    def close(self): pass
    def resolve(self, *_): return None


def broker_main(cost):
    """The real host broker, with the synthetic table and a stand-in caller check."""
    from luma_monitor import host_sampler, integration
    try:
        from luma_installer import app_data_broker
    except ImportError:  # the installer package is not needed for the stand-in
        import types
        package = types.ModuleType('luma_installer'); app_data_broker = types.ModuleType('luma_installer.app_data_broker')
        package.app_data_broker = app_data_broker
        sys.modules.update({'luma_installer': package, 'luma_installer.app_data_broker': app_data_broker})

    def authenticate(*_):
        spin(COST['authenticate'] * cost)
        return 'io.luma.Monitor'
    app_data_broker.authenticate = authenticate
    integration.DesktopCatalog = host_sampler.DesktopCatalog = QuietCatalog
    host_sampler.Sampler = lambda resolve=None: SyntheticSampler(resolve, cost=cost)
    host_sampler.battery_info = lambda: None
    host_sampler.performance_profile = lambda: None
    host_sampler.inhibitors = lambda: None
    host_sampler.Broker().run()


def scenario(name, cost):
    """Launch Monitor once and report window-map → first app rows painted."""
    import luma_monitor.application as application
    from luma_monitor import integration, model
    from luma_monitor.visuals import ActivityRow
    from gi.repository import GLib
    integration.battery_info = lambda: None
    integration.performance_profile = lambda: None
    if name == 'host':
        from luma_monitor import host_sampler
        integration.DesktopCatalog = host_sampler.HostCatalog
        model.Sampler = lambda resolve=None: host_sampler.HostSampler()
    else:
        integration.DesktopCatalog = QuietCatalog
        model.Sampler = lambda resolve=None: SyntheticSampler(resolve, cost=cost)
    app = application.MonitorApplication()
    result = {'scenario': name}
    marks = {}

    def rows(window):
        child = window.list.get_first_child()
        while child:
            if isinstance(child, ActivityRow) and child.get_mapped() and child.get_width() > 0:
                return True
            child = child.get_next_sibling()
        return False

    def live(window):
        return bool(getattr(window, 'live_rows', None))

    def added(_app, window):
        def mapped(*_):
            marks['map'] = time.monotonic()
            clock = window.get_frame_clock()

            def painted(*_):
                now = time.monotonic()
                marks.setdefault('paint', now)
                if 'first' not in marks and rows(window):
                    marks['first'] = now
                if 'live' not in marks and live(window) and rows(window):
                    marks['live'] = now
                if 'first' in marks and 'live' in marks and 'closing' not in marks:
                    # Closing the window (not quitting) keeps the last list for the next launch.
                    marks['closing'] = now
                    GLib.idle_add(window.close)
            clock.connect('after-paint', painted)
        window.connect('map', mapped)
    app.connect('window-added', added)

    def timeout():
        result['timeout'] = True
        app.quit()
        return GLib.SOURCE_REMOVE
    GLib.timeout_add_seconds(30, timeout)
    app.run([])
    for key in ('paint', 'first', 'live'):
        if key in marks and 'map' in marks:
            result[key + '_ms'] = round((marks[key] - marks['map']) * 1000, 1)
    return result


def child(arguments):
    sys.path.insert(0, arguments.source)
    if arguments.broker:
        broker_main(arguments.cost)
        return 0
    print(json.dumps(scenario(arguments.scenario, arguments.cost)), flush=True)
    return 0


def run_scenario(name, environment, arguments):
    common = ['--source', arguments.source, '--cost', str(arguments.cost)]
    broker = None
    try:
        if name == 'host':
            broker = subprocess.Popen([sys.executable, SCRIPT, '--broker', *common], env=environment)
            # On a desktop the service is D-Bus activated and ready before the
            # window shows; here it is started directly, so wait for its name.
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and broker.poll() is None:
                owned = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                      'NameHasOwner', GLib.Variant('(s)', ('org.projectluma.MonitorHost1',)),
                                      GLib.VariantType.new('(b)'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
                if owned:
                    break
                time.sleep(.05)
        output = subprocess.run([sys.executable, SCRIPT, '--scenario', name, *common],
                                env=environment, capture_output=True, text=True, timeout=90)
        lines = [line for line in output.stdout.splitlines() if line.startswith('{')]
        if output.returncode or not lines:
            return {'scenario': name, 'error': (output.stderr or output.stdout).strip()[-400:]}
        return json.loads(lines[-1])
    finally:
        if broker is not None:
            broker.terminate()
            broker.wait(timeout=10)


SCENARIOS = {'direct': ('direct', True), 'host': ('host', True), 'warm': ('host', False)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default=str(SOURCE))
    parser.add_argument('--budget-ms', type=float, default=200, help='warm launch, window map to first rows painted')
    parser.add_argument('--cold-budget-ms', type=float, default=400, help='launch without a cached list')
    parser.add_argument('--scenarios', default='direct,host,warm')
    parser.add_argument('--cost', type=float, default=1.0, help='scale the synthetic sampling CPU cost')
    parser.add_argument('--scenario')
    parser.add_argument('--broker', action='store_true')
    parser.add_argument('--wrapped', action='store_true')
    arguments = parser.parse_args()
    arguments.source = str(Path(arguments.source).resolve())
    if arguments.scenario or arguments.broker:
        return child(arguments)
    if not arguments.wrapped:
        # Always a private session bus: the stand-in broker must not meet a
        # real host service, and nothing here may reach the desktop's.
        command = ['dbus-run-session', '--']
        environment = dict(os.environ)
        if shutil.which('xvfb-run'):
            command += ['xvfb-run', '-a']
            environment.pop('WAYLAND_DISPLAY', None)
            environment['GDK_BACKEND'] = 'x11'
        missing = [tool for tool in ('dbus-run-session',) if not shutil.which(tool)]
        if not shutil.which('xvfb-run') and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            missing.append('xvfb-run')
        if missing:
            print(json.dumps({'check': CHECK, 'result': 'fail', 'detail': 'missing ' + ', '.join(missing)}))
            return 1
        # The private session's service chatter is not part of the result.
        return subprocess.call([*command, sys.executable, SCRIPT, '--wrapped', *sys.argv[1:]],
                               env=environment, stderr=subprocess.DEVNULL)
    results = []
    with tempfile.TemporaryDirectory(prefix='monitor-first-entries-') as home:
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [arguments.source, os.environ.get('PYTHONPATH')])),
                           GSK_RENDERER=os.environ.get('GSK_RENDERER', 'cairo'), GTK_A11Y='none',
                           GSETTINGS_BACKEND='memory', XDG_CACHE_HOME=f'{home}/cache',
                           XDG_CONFIG_HOME=f'{home}/config', XDG_STATE_HOME=f'{home}/state')
        environment.setdefault('LUMA_MONITOR_STYLE_PATH', str(Path(arguments.source) / 'data/monitor.css'))
        environment.pop('LUMA_MONITOR_FIXTURE', None)
        environment.pop('LUMA_MONITOR_PREVIEW', None)
        for name in arguments.scenarios.split(','):
            path, cold = SCENARIOS[name]
            budget = arguments.cold_budget_ms if cold else arguments.budget_ms
            best = None
            for attempt in range(2):
                if cold:
                    shutil.rmtree(f'{home}/cache', ignore_errors=True)
                result = dict(run_scenario(path, environment, arguments), scenario=name, budget=budget)
                if best is None or (result.get('first_ms') or 1e9) < (best.get('first_ms') or 1e9):
                    best = result
                if 'error' not in best and not best.get('timeout') and (best.get('first_ms') or 1e9) <= budget:
                    break
            results.append(best)
    failures = [r for r in results if 'error' in r or r.get('timeout') or r.get('first_ms') is None
                or r['first_ms'] > r['budget']]
    detail = '; '.join(
        f"{r['scenario']} " + (f"error {r['error']}" if 'error' in r else
                               f"first {r.get('first_ms', 'none')}ms live {r.get('live_ms', 'none')}ms (budget {r['budget']:g}ms)")
        for r in results)
    print(json.dumps({'check': CHECK, 'result': 'fail' if failures else 'pass', 'detail': detail}))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
