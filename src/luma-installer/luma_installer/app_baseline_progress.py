# SPDX-License-Identifier: Apache-2.0
"""Progress of the first-boot app baseline for systemd and the signed-in person.

luma-app-baseline.service is Type=notify. It reports ready, which releases the
display manager ordered after it, when the handoff finishes or LOGIN_WAIT
seconds after it started, whichever is first. After that the import carries on
in the background at a lower priority than the session. Each step is written
to STATUS (world-readable) so the session can say that apps are still being
set up while the app grid fills in.
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

STATUS = Path('/run/luma/app-baseline/status.json')
SCHEMA = 'org.projectluma.app-baseline-progress/v1'
#: Seconds the display manager waits for the baseline at most.
LOGIN_WAIT = 90
INSTALLING, DONE, FAILED = 'installing', 'done', 'failed'


def sd_notify(message, environ=os.environ):
    address = environ.get('NOTIFY_SOCKET')
    if not address:
        return False
    if address.startswith('@'):
        address = '\0' + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        sock.sendall(message.encode())
    return True


def read(path=STATUS):
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (not isinstance(doc, dict) or doc.get('schema') != SCHEMA
            or doc.get('state') not in (INSTALLING, DONE, FAILED)):
        return None
    return doc


def publish(path, doc):
    """Replace the status atomically; it is informational, so errors are ignored."""
    try:
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.status-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(doc, stream, sort_keys=True)
                stream.write('\n')
                os.fchmod(stream.fileno(), 0o644)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError as error:
        print(f'App baseline status not written: {error}', file=sys.stderr)


class Progress:
    def __init__(self, path=STATUS, *, wait=LOGIN_WAIT, notify=sd_notify, clock=time.time):
        self.path, self.wait, self.notify, self.clock = path, wait, notify, clock
        self.lock = threading.Lock()
        self.released = False
        self.timer = None
        self.doc = None

    def arm(self):
        """Release the display manager after the bounded wait even if work remains."""
        self.timer = threading.Timer(self.wait, self.release, kwargs={'background': True})
        self.timer.daemon = True
        self.timer.start()

    def begin(self, total=0):
        self.doc = {'schema': SCHEMA, 'state': INSTALLING, 'apps_ready': 0, 'apps_total': total,
                    'started_unix': self.clock()}
        publish(self.path, self.doc)

    def step(self, ready, total):
        if self.doc is None:
            self.begin(total)
        self.doc.update(apps_ready=ready, apps_total=total)
        publish(self.path, self.doc)
        self.notify(f'STATUS={ready} of {total} apps set up')

    def release(self, background=False):
        with self.lock:
            if self.released:
                return
            self.released = True
        if self.timer is not None:
            self.timer.cancel()
        if background:
            # The session starts now; the rest of the import yields to it. On
            # Linux this sets the main thread, whose later children inherit it.
            try:
                os.setpriority(os.PRIO_PROCESS, os.getpid(), 10)
            except OSError:
                pass
        self.notify('READY=1')

    def finish(self, ok=True):
        if self.timer is not None:
            self.timer.cancel()
        if self.doc is not None:
            self.doc.update(state=DONE if ok else FAILED, finished_unix=self.clock())
            publish(self.path, self.doc)
        if ok:
            self.release()


def stopped(path=STATUS):
    """ExecStopPost: a handoff killed mid-import is reported, not left installing."""
    doc = read(path)
    if doc is not None and doc['state'] == INSTALLING:
        doc.update(state=FAILED, finished_unix=time.time())
        publish(path, doc)


def message(doc):
    if doc['state'] == DONE:
        return 'Your apps are ready', 'Every app that comes with Luma is installed.'
    if doc['state'] == FAILED:
        return 'Some apps are not set up yet', 'Luma finishes setting them up the next time it starts.'
    ready, total = doc.get('apps_ready', 0), doc.get('apps_total', 0)
    body = 'Apps that come with Luma appear as each one is ready.'
    if isinstance(ready, int) and isinstance(total, int) and 0 <= ready < total:
        body += f' {ready} of {total} so far.'
    return 'Setting up your apps', body


def desktop_notification(replaces, summary, body):
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    hints = {'desktop-entry': GLib.Variant('s', 'org.projectluma.Depot')}
    reply = bus.call_sync('org.freedesktop.Notifications', '/org/freedesktop/Notifications',
                          'org.freedesktop.Notifications', 'Notify',
                          GLib.Variant('(susssasa{sv}i)', ('Depot', replaces, 'org.projectluma.Depot',
                                                           summary, body, [], hints, -1)),
                          GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, 10000, None)
    return reply.unpack()[0]


def notice(path=STATUS, *, send=desktop_notification, sleep=time.sleep, poll=5):
    """At sign-in: say apps are still being set up, then say when they are."""
    doc = read(path)
    if doc is None or doc['state'] != INSTALLING:
        return
    identifier = send(0, *message(doc))
    while True:
        sleep(poll)
        doc = read(path)
        if doc is None:
            return
        if doc['state'] != INSTALLING:
            send(identifier, *message(doc))
            return


def main(argv=sys.argv[1:]):
    if argv == ['notice']:
        notice()
    elif argv == ['stopped']:
        stopped()
    else:
        raise SystemExit('usage: app_baseline_progress notice|stopped')


if __name__ == '__main__':
    main()
