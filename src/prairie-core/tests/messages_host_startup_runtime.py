#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A sandboxed Messages window is on screen before it asks the agent anything.

The agent is replaced at the connection boundary by one that answers from real
stores, slowly (COST per call, half on the agent's main thread and half on its
single mailbox worker), and refuses a fifth concurrent mailbox request, as
messages_app_rpc does. The window must present without a single agent call on
its main thread, then list every conversation of every account, with the first
fill answered from what was fetched in parallel off the main thread.

    COST=0.37 VERBOSE=1 messages_host_startup_runtime.py   # timing of each call
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import GLib

COST = float(os.environ.get('COST', '0.05'))
ROOT = Path(tempfile.mkdtemp(prefix='messages-host-startup-'))
os.environ.update(HOME=str(ROOT), XDG_DATA_HOME=str(ROOT / 'data'), XDG_CONFIG_HOME=str(ROOT / 'config'),
                  XDG_CACHE_HOME=str(ROOT / 'cache'))

from prairie_apps.messages_accounts import Account, AccountStore  # noqa: E402
from prairie_apps.messages_backend import MessagingCapability  # noqa: E402
from prairie_apps.messages_mms import MmsCapability  # noqa: E402
from prairie_apps.messages_app_rpc import arguments, payload  # noqa: E402
from prairie_apps.messages_agent import AgentClient  # noqa: E402
from prairie_apps.messages import MessagesApplication, MessagesWindow  # noqa: E402

MAIN = threading.main_thread()
LUMA, GOOGLE = 'luma-' + 'a' * 16, 'gmessages-' + 'b' * 16
ACCOUNTS = [Account(LUMA, 'luma', 'Me', '@me', 1, False), Account(GOOGLE, 'gmessages', 'Me', '+15550100', 1, False)]
STORES = {}
# The agent's one mailbox worker owns its SQLite stores, as in messages_app_rpc.
WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='stand-in-agent')


def seed():
    for key, count in (('native', 5), (LUMA, 3), (GOOGLE, 6)):
        STORES[key] = AccountStore(ROOT / f'{key}.db')
        for index in range(count):
            STORES[key].add(f'@friend{index}' if key == LUMA else f'+1555010{index:04d}', f'hello {index}',
                            direction='incoming', timestamp=1_700_000_000 + index)
    return sum(len(store.threads()) for store in STORES.values())


calls = []
agent_main, slots = threading.Lock(), threading.BoundedSemaphore(4)


def answer(method, values):
    if method == 'ApplicationAccounts': return payload(ACCOUNTS)
    if method in {'ApplicationPhones', 'ApplicationCloud'}: return payload([])
    if method == 'ApplicationInfo': return payload({'networks': ['luma', 'gmessages'], 'luma_problem': ''})
    if method == 'ApplicationStore':
        account, operation, text = values
        args, kwargs = arguments(text)
        return payload(getattr(STORES[account], operation)(*args, **kwargs))
    if method == 'ApplicationNative':
        return payload(MmsCapability() if values[0] == 'mms_inspect' else
                       MessagingCapability(True, '') if values[0] == 'inspect' else ())
    if method == 'Accounts':
        return json.dumps([{'id': a.id, 'state': 'ready', 'network_state': 'connected', 'detail': ''} for a in ACCOUNTS])
    if method == 'Luma': return json.dumps({'ok': True, 'result': {'handle': 'me'}})
    return payload(None)


class SlowAgent(AgentClient):
    """AgentClient over a stand-in agent with the real one's cost and limits."""

    def __init__(self):
        self.connection = None
        self._listeners = {}
        self._reads, self._reads_lock, self._reads_generation, self._mailbox_generations = {}, threading.Lock(), 0, {}
        self.on_vanished = lambda: None
        self.on_store_changed = lambda: None
        self.on_mailbox_error = lambda _message: None

    def _request(self, method, parameters=None, timeout=10_000):
        values = parameters.unpack() if parameters is not None else ()
        limited = method.startswith('Application')
        if limited and not slots.acquire(blocking=False):
            raise GLib.Error('The mailbox request was refused')
        start = time.monotonic()
        try:
            with agent_main: time.sleep(COST / 2)
            reply = WORKER.submit(lambda: (time.sleep(COST / 2), answer(method, values))[1]).result()
        finally:
            if limited: slots.release()
        calls.append((method, values[1] if method == 'ApplicationStore' else '', threading.current_thread() is MAIN,
                      start, time.monotonic()))
        return (reply,)

    def call_async(self, method, parameters=None):
        changes = self._read_key(method, parameters)[1]

        def send():
            self._request(method, parameters)
            if changes != '': self.forget_reads(changes)
        threading.Thread(target=send, daemon=True).start()

    def close(self): pass


def main():
    expected = WORKER.submit(seed).result()
    fill = []
    loaded = MessagesWindow._host_services_loaded

    def host_services_loaded(window, *args):
        first = len(calls)
        try:
            return loaded(window, *args)
        finally:
            fill.extend(call for call in calls[first:] if call[2])
    with patch.object(MessagesWindow, '_host_services_loaded', host_services_loaded), \
         patch('prairie_apps.messages_app_client.sandboxed', return_value=True), \
         patch.object(MessagesWindow, '_connect_agent', side_effect=lambda _app: SlowAgent()), \
         patch('prairie_apps.messages.load_contacts', return_value=()):
        app = MessagesApplication(); assert app.register(None)
        started = time.monotonic()
        app.activate()  # constructs and presents the window
        presented = time.monotonic() - started
        window = app._window()
        assert window is not None and not getattr(window, '_service_unavailable', False)
        early = [call for call in calls if call[2]]
        assert not early, f'the window asked the agent {len(early)} times before it was presented: {early}'
        stall, last = [0.0], [time.monotonic()]

        def tick():
            now = time.monotonic(); stall[0] = max(stall[0], now - last[0]); last[0] = now
            return GLib.SOURCE_CONTINUE
        GLib.timeout_add(5, tick)
        filled, seen, quiet = None, 0, time.monotonic()
        deadline = time.monotonic() + 60 * max(COST, 0.05) / 0.05
        while time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.002)
            if filled is None and len(window.surface._entry_by_id) >= expected:
                filled = time.monotonic() - started
            if len(calls) != seen:
                seen, quiet = len(calls), time.monotonic()
            if filled is not None and time.monotonic() - quiet > max(30 * COST, 0.5):
                break
        assert filled is not None, f'listed {len(window.surface._entry_by_id)} of {expected} conversations'
        assert {service.id for service in window.services} == {'native', f'account:{LUMA}', f'account:{GOOGLE}'}
        assert window.service.id != 'native', 'a device opens on its network accounts, as before'
        assert window.surface.current is not None, 'the newest conversation is open, as before'
        on_main = [call for call in calls if call[2]]
        assert not fill, f'the first fill asked the agent on the main thread: {fill}'
        print(f'cost per call {COST:.2f} s; presented after {presented:.2f} s with 0 agent calls; '
              f'{expected} conversations after {filled:.2f} s; {len(calls)} calls, {len(on_main)} on the main thread '
              f'({sum(c[4] - c[3] for c in on_main):.2f} s); longest main-loop stall {stall[0]:.2f} s')
        if os.environ.get('VERBOSE'):
            for method, operation, main_thread, start, end in calls:
                print(f'  {start - started:6.2f} {end - start:5.2f} {"main" if main_thread else "    "} {method} {operation}')
        window.close()
        print('PASS sandboxed Messages presents before any agent call and fills in from parallel replies')


if __name__ == '__main__':
    main()
    sys.stdout.flush()
    os._exit(0)  # the stand-in agent's daemon threads need no orderly exit
