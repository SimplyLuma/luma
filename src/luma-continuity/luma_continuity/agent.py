"""Luma Connect's background agent (ADR-033, communication category).

The Connect daemon is the agent: it keeps paired phones linked (their
notifications, calls and texts reach this computer with Luma Connect closed)
and the account session current. luma-background starts it at login as
``luma-connect-daemon --agent`` in the unit it generates; before that service
is installed the autostart entry does. It owns ``org.projectluma.Connect.Agent``
and publishes whether the link is on, how many phones are paired and reachable,
the account state and hub enrolment.

The daemon is also D-Bus activated for the Connect app, through
``luma-connect.service``. The two never run side by side: the on-demand daemon
owns org.projectluma.Connect1 so that it can be replaced, and the agent takes
the name over when it starts; the on-demand daemon then exits.

On resume and when the network returns it also gets Luma Hub sync going again
at once: the sync units' timers do not count time asleep and the long poll of
``luma-connect-sync watch`` can sit on a dead connection, so shared calendars,
contacts and notes would otherwise wait for their next timer.

The agent contract comes from prairie_apps.background_agent (prairie-core-apps,
which this package requires). An older prairie-core-apps without it leaves the
daemon exactly as it was: nothing here is required for Connect to work.
"""
import logging
from pathlib import Path

LOG = logging.getLogger(__name__)

APP_ID = 'org.projectluma.Connect'
ON_DEMAND_UNIT = 'luma-connect.service'
SYNC_UNIT = 'luma-connect-sync.service'
WATCH_UNIT = 'luma-connect-sync-watch.service'


def _contract():
    try:
        from prairie_apps import background_agent
    except ImportError:
        return None
    return background_agent if hasattr(background_agent, 'AgentPublisher') else None


def enrolled_with_hub(home=None):
    home = Path(home or Path.home())
    return (home/'.local/share/luma/connect/device.json').is_file()


def autostart_steps_aside(argv, connection):
    """The autostart entry is only the fallback: with luma-background installed, it starts the agent."""
    contract = _contract()
    if contract is None or '--autostart' not in argv[1:] or contract.is_managed():
        return False
    return contract.service_present(connection)


class SyncKick:
    """Start the hub sync now and restart its watcher, if this device is enrolled."""

    def __init__(self, call, *, enrolled=enrolled_with_hub):
        self.call, self.enrolled = call, enrolled

    def __call__(self):
        if not self.enrolled():
            return False
        for method, unit in (('StartUnit', SYNC_UNIT), ('TryRestartUnit', WATCH_UNIT)):
            try:
                self.call(method, unit)
            except Exception as error:
                LOG.info('%s %s failed (%s)', method, unit, type(error).__name__)
        return True


class ConnectAgent:
    """Publishes the daemon's state under the agent contract and handles wake events."""

    def __init__(self, daemon, connection, *, contract=None, kick=None):
        self.daemon, self.connection = daemon, connection
        self.contract = contract or _contract()
        self.publisher = None
        if self.contract is None:
            return
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib

        def call(method, unit):
            connection.call_sync('org.freedesktop.systemd1', '/org/freedesktop/systemd1',
                                 'org.freedesktop.systemd1.Manager', method, GLib.Variant('(ss)', (unit, 'replace')),
                                 None, Gio.DBusCallFlags.NONE, 10000, None)
        self.kick = kick or SyncKick(call)
        info = self.contract.AgentInfo(APP_ID, 'Luma Connect', 'communication', ('luma-connect-daemon', '--agent'),
                                       wake=('login', 'network', 'resume'),
                                       publishes=('enabled', 'phones', 'phones-reachable', 'account', 'hub-sync',
                                                  f'live-extension:{APP_ID}.Devices'),
                                       purpose='Stay linked to your phone when Luma Connect is closed')
        self.publisher = self.contract.AgentPublisher(info, wake=self.wake, lost=lambda _started: None)

    def start(self):
        if self.publisher is None:
            return
        self.publisher.own(self.connection)
        self.update()

    def wake(self, event):
        if event in {'resume', 'network'}:
            self.kick()

    def update(self):
        if self.publisher is None:
            return
        GLib = self.GLib
        state = self.daemon.latest
        devices = []
        companion = getattr(self.daemon, 'companion', None)
        if companion is not None:
            try:
                devices = companion.snapshot().get('companion_devices') or []
            except Exception:
                devices = []
        reachable = sum(1 for row in devices if row.get('reachable'))
        self.publisher.publish('enabled', bool(getattr(self.daemon, 'enabled', False)))
        self.publisher.publish('phones', len(devices))
        self.publisher.publish('phones-reachable', reachable)
        self.publisher.publish('account', str(state.get('account_status') or 'signed_out'))
        self.publisher.publish('hub-sync', enrolled_with_hub())
        self.publisher.set_state('running' if getattr(self.daemon, 'enabled', False) else 'idle',
                                 f'{len(devices)} phone{"s" if len(devices) != 1 else ""} paired')

    def close(self):
        if self.publisher is not None:
            self.publisher.release()


def agent_requested(argv):
    return '--agent' in argv[1:]
