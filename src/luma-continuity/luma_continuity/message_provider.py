"""Explicit selected-phone provider; no discovery, local modem or automatic grant.

The application owner injects a current verified account observation and a main
loop dispatcher. Each worker exchange rechecks account, epoch and directional
grants. Closing stops admission immediately; in-flight TLS has its own deadline.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
import stat
import sqlite3
from pathlib import Path
import threading
import time
from .client import QueuedMessages
from .local import PairedExchange
from .policy import DIGEST, IDENTIFIER, Journal


@dataclass(frozen=True)
class SelectedPhone:
    peer: str
    epoch: str
    account: str | None
    label: str
    address: str
    port: int
    carrier: str = 'lan'

    def __post_init__(self):
        import ipaddress
        if not DIGEST.fullmatch(self.peer) or not IDENTIFIER.fullmatch(self.epoch):
            raise ValueError('invalid selected phone')
        if self.carrier == 'companion':
            # ADR-021 companion phones pair without an account; the pairing's own
            # epoch and directional grants are the authorization.
            if self.account is not None:
                raise ValueError('companion phones are not account-bound')
        elif not isinstance(self.account, str) or not 0 < len(self.account) <= 512:
            raise ValueError('verified account binding required')
        if not isinstance(self.label, str) or not 0 < len(self.label) <= 128:
            raise ValueError('invalid device label')
        ipaddress.ip_address(self.address)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('invalid device port')
        if self.carrier not in {'lan','relay','companion'}: raise ValueError('invalid message carrier')


# Not-ready states are re-observed on their own: a Connect state change is not
# guaranteed after every recovery (a phone waking, a keyring unlocked at login
# before this window saw it), and a stuck status is worse than a cheap retry.
RETRY_DELAYS = (2, 4, 8, 15, 30, 60)
# PAM unlocks the login keyring moments after the session starts; an early
# 'locked' observation is reported as connecting rather than asking for Unlock.
STARTUP_GRACE = 15


def _start_timer(delay, callback):
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


class MessageProvider(QueuedMessages):
    def __init__(self, directory, selection, *, account_observation, store_factory,
                 dispatch, exchange_factory=PairedExchange, startup_grace=STARTUP_GRACE,
                 timer=_start_timer, monotonic=time.monotonic):
        self.selection = selection
        self.identity_directory = Path(directory)
        self.account_observation = account_observation
        self.dispatch = dispatch
        self.exchange_factory = exchange_factory
        self._lock = threading.Lock()
        self._ui_thread = threading.get_ident()
        self._permissions = {}
        self._account_locked = False
        self._account_offline = False
        self._generation = 0
        self._validation = None
        self._validation_pending = False
        self._grant_lock = threading.Lock()
        self._grant_db = None
        self._grant_inode = None
        self._disconnect = lambda: None
        self._closed = False
        self._timer = timer
        self._monotonic = monotonic
        self._grace_until = monotonic() + startup_grace
        self._retry = None
        self._retry_index = 0
        self._active = None
        self._notify = lambda _state: None
        self._mms_refresh = lambda: None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='connect-messages')
        self.status = {'state': 'idle', 'imported': 0, 'needs_review': 0}
        key = hashlib.sha256(((selection.account or 'companion:') + selection.peer + selection.epoch).encode()).hexdigest()
        super().__init__(self.identity_directory / 'message-caches' / key,
                         selection.peer, selection.epoch, label=selection.label,
                         approved=lambda: self.allowed('messages.read'),
                         capability_approved=self.allowed, account=selection.account,
                         store_factory=store_factory)

    def allowed(self, capability):
        if self._closed: return False
        if threading.get_ident() == self._ui_thread:
            return not self._validation_pending and self._permissions.get(capability, False)
        if not self.account_current(): return False
        # Keep one serialized read-only connection. Reopening the writable
        # Journal for every check creates/removes WAL files and invalidates our
        # own monitor even though no authorization changed.
        path = self.identity_directory / 'continuity.db'
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or path.parent.stat().st_mode & 0o077):
            raise PermissionError('private grant database required')
        inode = (info.st_dev, info.st_ino)
        with self._grant_lock:
            if self._grant_db is None or self._grant_inode != inode:
                if self._grant_db is not None: self._grant_db.close()
                self._grant_db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True,
                                                timeout=1, check_same_thread=False)
                self._grant_db.execute('PRAGMA query_only=ON')
                self._grant_inode = inode
            row = self._grant_db.execute('SELECT epoch,account,outgoing_grants,revoked FROM peers WHERE fingerprint=?',
                                        (self.peer,)).fetchone()
        allowed = bool(row and row[0] == self.epoch and row[1] == self.account
                       and not row[3] and capability in json.loads(row[2]))
        self._permissions[capability] = allowed
        return allowed

    def _close_grants(self):
        with self._grant_lock:
            if self._grant_db is not None:
                self._grant_db.close()
                self._grant_db = self._grant_inode = None

    def account_current(self):
        if self._closed: return False
        observation = self.account_observation()
        self._account_locked = isinstance(observation,dict) and observation.get('locked') is True
        self._account_offline = isinstance(observation,dict) and observation.get('offline') is True
        valid = (isinstance(observation, dict) and observation.get('account') == self.selection.account
                 and observation.get('signed_in') is True and observation.get('stale') is False)
        if not valid: self._permissions.clear()
        return valid

    def _denied_state(self):
        if self._account_locked:
            return 'connecting' if self._monotonic() < self._grace_until else 'locked'
        return 'offline' if self._account_offline else 'unavailable'

    def _publish_status(self, state):
        self.status = state
        self._mms_refresh()
        self._notify(dict(state))
        self._schedule_retry()

    def _schedule_retry(self):
        if self._closed: return
        if self.status['state'] in ('ready', 'attention'):
            self._retry_index = 0
            return
        if self._retry is not None: return
        delay = RETRY_DELAYS[min(self._retry_index, len(RETRY_DELAYS) - 1)]
        self._retry_index += 1
        self._retry = self._timer(delay, lambda: self.dispatch(self._retry_due))

    def _retry_due(self):
        self._retry = None
        if not self._closed and self.status['state'] not in ('ready', 'attention'): self.invalidate()
        return False

    def retry(self):
        """Explicit Retry/Unlock: re-observe the account and the phone now."""
        if self._closed: return
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None
        self._retry_index = 0
        self.invalidate()

    def invalidate(self, *_args):
        """Suspend admission immediately; validate noisy events off the UI thread."""
        if self._closed: return
        self._generation += 1
        self._permissions.clear()
        self._validation_pending = True
        self._mms_refresh()
        self._request_validation()

    def _request_validation(self):
        if self._closed or self._validation is not None: return
        self._validation = self._executor.submit(self._validate)

    def _validate(self):
        generation = self._generation
        try:
            permissions = {capability: self.allowed(capability)
                           for capability in ('messages.read', 'messages.send')}
        except Exception:
            permissions = {}
        def publish():
            self._validation = None
            if self._closed: return False
            if generation != self._generation:
                self._request_validation()
                return False
            self._validation_pending = False
            self._permissions = permissions
            if not permissions.get('messages.read', False):
                self._publish_status({'state': self._denied_state(), 'imported': 0, 'needs_review': 0})
            else:
                # A current account/grant survived the event. Fetch a fresh
                # result instead of publishing an older in-flight generation.
                self.refresh()
            return False
        self.dispatch(publish)

    def start(self, notify):
        self._notify = notify
        return self.refresh()

    def refresh(self):
        """Coalesce concurrent UI refreshes; never enqueue an unbounded backlog."""
        with self._lock:
            if self._closed: return None
            if self._active is not None and not self._active.done(): return self._active
            self._active = self._executor.submit(self._run)
            return self._active

    def _run(self):
        generation = self._generation
        state = {'state': 'offline', 'imported': 0, 'needs_review': 0}
        try:
            if not self.allowed('messages.read'): raise PermissionError('phone unavailable')
            exchange = self.exchange_factory(self.identity_directory, self.peer, self.epoch,
                self.selection.address, self.selection.port, timeout=5, authorized=self.account_current)
            if self.allowed('messages.send'): self.flush(exchange)
            result = self.sync_recent(exchange)
            from .queue import Outbox
            box = Outbox(self.outbox_path)
            try:
                box.pending(self.peer, self.epoch)  # settle expiration without retrying
                review = box.db.execute("""SELECT count(*) FROM outbox o WHERE state IN ('unknown','expired','revoked')
                    OR (state='complete' AND (json_type(result,'$.result.error') IS NOT NULL
                    OR (json_extract(result,'$.result.state')='unknown' AND NOT EXISTS
                        (SELECT 1 FROM reconciled_sends r WHERE r.id=o.id AND r.peer=o.peer AND r.epoch=o.epoch
                         AND r.remote=json_extract(o.result,'$.result.uid') AND r.state='sent'))))""").fetchone()[0]
            finally: box.close()
            state = {'state': 'attention' if review else 'ready', 'imported': result['imported'],
                     'needs_review': review, 'truncated': result['truncated']}
        except PermissionError: state['state'] = self._denied_state()
        except Exception: pass  # UI never receives raw network/private payload errors
        def publish():
            if self._closed: return False
            if generation != self._generation or self._validation_pending:
                self._request_validation()
                return False
            if not self.allowed('messages.read'):
                state.update(state=self._denied_state(), imported=0)
            self._publish_status(state)
            return False
        self.dispatch(publish)
        return state

    def send_message(self, uid):
        # Core calls this from its existing transmit worker, never the UI.
        if threading.get_ident() == self._ui_thread:
            raise RuntimeError("queue mutation requires the Messages transmit worker")
        result = super().send_message(uid)
        self.refresh()
        return result

    def retry_message(self, uid):
        if threading.get_ident() == self._ui_thread:
            raise RuntimeError("retry requires the Messages transmit worker")
        result = super().retry_message(uid)
        self.refresh()
        return result

    def sms_transport(self):
        transport = super().sms_transport()
        original = transport.inspect
        def inspect():
            from prairie_apps.messages_backend import MessagingCapability
            capability = original()
            reasons = {'ready': '' if capability.available else capability.reason,
                       'offline': 'Waiting for your phone… Messages stay queued.',
                       'connecting': 'Connecting to your phone…',
                       'locked': 'Unlock your login keyring in Luma Connect to reconnect to your phone.',
                       'unavailable': 'Phone access is unavailable.',
                       'attention': 'Some sends were not confirmed. Use Review send on the affected message.'}
            return MessagingCapability(capability.available,
                reasons.get(self.status['state'], capability.reason))
        transport.inspect = inspect
        return transport

    def mms_transport(self):
        transport = super().mms_transport()
        original_start = transport.start
        def start(on_message, on_capability):
            # Start can precede the first asynchronous authorization result.
            # Republish on the same main-loop path as SMS capability changes.
            self._mms_refresh = lambda: original_start(on_message, on_capability)
            self._mms_refresh()
        def stop():
            self._mms_refresh = lambda: None
        transport.start, transport.stop = start, stop
        return transport

    def close(self):
        with self._lock:
            if self._closed: return
            self._closed = True
        self._disconnect()
        if self._retry is not None: self._retry.cancel()
        self._mms_refresh = lambda: None
        for pending in (self._active, self._validation):
            if pending is not None: pending.cancel()
        self._executor.submit(self._close_grants)
        self._executor.shutdown(wait=False, cancel_futures=False)


def companion_exchange(directory, peer, epoch, address, port, *, timeout=10, authorized=lambda: True):
    """PairedExchange to a companion phone at its most recently seen route.

    A phone's address changes with networks; the companion registry records the
    route of its last authenticated connection, so the saved selection's address is
    only a fallback.
    """
    from .companion import Registry
    device = Registry(directory).active().get(peer) or {}
    return PairedExchange(directory, peer, epoch, device.get('host') or address, device.get('port') or port,
                          timeout=timeout, authorized=authorized)


def companion_observation(directory, peer):
    """Account-free observation for a companion selection: the Connect link is on and the phone is still paired."""
    def observe():
        from .companion import Registry
        journal = Journal(Path(directory) / 'continuity.db')
        try: enabled = journal.enabled()
        finally: journal.close()
        return {'account': None, 'locked': False, 'offline': not enabled,
                'signed_in': peer in Registry(directory).active(), 'stale': not enabled}
    return observe


def selected_provider(*, store_factory, dispatch, config_path=None):
    """Load an explicitly saved local selection; absent selection uses local SMS.

    An invalid existing selection raises instead of silently selecting a modem.
    The selection UI/CLI owner must save this private file after explicit consent.
    """
    import os
    config_path = Path(config_path or Path.home()/'.config/luma-connect/messages-phone.json')
    if not config_path.exists(): return None
    info = config_path.lstat()
    if (config_path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077
            or config_path.parent.stat().st_mode & 0o077 or info.st_size > 8192):
        raise PermissionError('private selected-phone configuration required')
    from .transport import _unique
    document = json.loads(config_path.read_text(), object_pairs_hook=_unique)
    if set(document) != {'version','identity_directory','phone'} or document['version'] != 1:
        raise ValueError('invalid selected-phone configuration')
    directory = Path(document['identity_directory'])
    if not directory.is_absolute() or directory.is_symlink():
        raise ValueError('invalid selected-phone identity directory')
    selection = SelectedPhone(**document['phone'])
    from .daemon import load_config, BUS, PATH
    if selection.carrier == 'companion':
        provider = MessageProvider(directory, selection, account_observation=companion_observation(directory, selection.peer),
                                   store_factory=store_factory, dispatch=dispatch, exchange_factory=companion_exchange)
        return _watch(provider, directory, config_path, BUS, PATH)
    from .account import authorization_current
    config = load_config()
    if config is None: raise PermissionError('Connect account configuration required')
    def observe():
        from gi.repository import Gio
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = connection.call_sync(BUS, PATH, BUS, 'GetState', None, None,
                                      Gio.DBusCallFlags.NONE, 1500, None)
        state = json.loads(result.unpack()[0])
        identity = state.get('identity') or {}
        return {'account': identity.get('account_id'),
                'locked': state.get('account_status') == 'locked',
                'offline': state.get('service_status') in ('offline','unavailable'),
                'signed_in': state.get('account_status') == 'signed_in' and identity.get('issuer') == config.issuer,
                'stale': state.get('enabled') is False or not authorization_current(state)}
    from .daemon_exchange import DaemonRelayExchange
    provider = MessageProvider(directory, selection, account_observation=observe,
                               store_factory=store_factory, dispatch=dispatch,
                               exchange_factory=DaemonRelayExchange if selection.carrier=='relay' else PairedExchange)
    return _watch(provider, directory, config_path, BUS, PATH)


IDENTITY_DIRECTORY = Path.home()/'.local/share/luma-connect/device'


def message_services(*, store_factory, dispatch, config_path=None, identity_directory=None, problem=lambda _error: None):
    """Every phone Messages shows beside this device's own messages (ADR-022).

    The saved account selection comes first, then each paired Android phone
    (ADR-021) whose pairing let this computer read messages and that has
    connected at least once. The pairing grant is the consent; revoking it on
    either device makes that phone's service unavailable without touching the
    others. A damaged saved selection is reported through ``problem`` and
    skipped rather than taking the other services down with it.
    """
    config_path = Path(config_path or Path.home()/'.config/luma-connect/messages-phone.json')
    providers = []
    try:
        selected = selected_provider(store_factory=store_factory, dispatch=dispatch, config_path=config_path)
    except (OSError, ValueError, TypeError, PermissionError) as error:
        problem(error); selected = None
    if selected is not None: providers.append(selected)
    directory = Path(identity_directory or IDENTITY_DIRECTORY)
    if not (directory/'continuity.db').is_file() or directory.is_symlink(): return providers
    from .companion import Registry
    try: devices = Registry(directory).active()
    except (sqlite3.Error, OSError) as error:
        problem(error); return providers
    from .daemon import BUS, PATH
    for fingerprint, device in sorted(devices.items(), key=lambda item: item[1]['paired_at']):
        if ('messages.read' not in device['outgoing'] or not device['host'] or not device['port']
                or any(provider.peer == fingerprint for provider in providers)):
            continue
        selection = SelectedPhone(fingerprint, device['epoch'], None, (device['name'] or 'Android phone')[:128],
                                  device['host'], device['port'], 'companion')
        provider = MessageProvider(directory, selection, account_observation=companion_observation(directory, fingerprint),
                                   store_factory=store_factory, dispatch=dispatch, exchange_factory=companion_exchange)
        providers.append(_watch(provider, directory, config_path, BUS, PATH))
    return providers


def _watch(provider, directory, config_path, BUS, PATH):
    from gi.repository import Gio
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    subscriptions = [connection.signal_subscribe(BUS, BUS, 'StateChanged', PATH, None,
        Gio.DBusSignalFlags.NONE, provider.invalidate),
        connection.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
        'NameOwnerChanged', '/org/freedesktop/DBus', BUS, Gio.DBusSignalFlags.NONE, provider.invalidate)]
    monitor = Gio.File.new_for_path(str(directory)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
    def changed(_monitor, file, other, _event):
        names = {item.get_basename() for item in (file, other) if item is not None}
        if names & {'continuity.db', 'continuity.db-wal'}: provider.invalidate()
    monitor.connect('changed', changed)
    selection_monitor = Gio.File.new_for_path(str(config_path)).monitor_file(Gio.FileMonitorFlags.NONE, None)
    selection_monitor.connect('changed', provider.invalidate)
    def disconnect():
        for subscription in subscriptions: connection.signal_unsubscribe(subscription)
        monitor.cancel(); selection_monitor.cancel()
    provider._disconnect = disconnect
    return provider
