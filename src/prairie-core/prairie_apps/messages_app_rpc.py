# SPDX-License-Identifier: Apache-2.0
"""Messages' fixed host mailbox API; no SQL, account paths or keys cross it.

The existing Agent remains the network/MLS owner. A sandbox may edit that
owner's mailbox and pass explicit attachment descriptors, never open a helper
database or receive a Connect enrollment credential.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, is_dataclass
import inspect
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import threading
import uuid
import heapq

from gi.repository import Gio, GLib
from .messages_accounts import AccountStore, ACCOUNT_ID
from .messages_backend import MessageStore, MAX_ATTACHMENT_BYTES, ModemMessagingTransport, TransportMessage
from .messages_native_mailbox import NativeMailbox, import_mms
from .messages_mms import MmsMessagingTransport

MAX_INPUT = 131072
MAX_OUTPUT = 4 * 1024 * 1024
READS = frozenset({'message', 'thread', 'threads', 'draft', 'draft_attachments',
    'draft_reply', 'quote_for', 'reactions', 'incoming_revision', 'external_revision',
    'has_incoming', 'canonical_address', 'is_transport_deleted', 'sender', 'receipt',
    'luma_flags', 'luma_conversations', 'conversation_kind', 'conversation_participants',
    'conversation_phone', 'known_conversation', 'media_parts', 'media_part',
    'own_reaction', 'uid_for_transport', 'transport_for_uid'})
WRITES = frozenset({'add', 'set_display_name', 'set_draft', 'delete_draft',
    'remove_draft_attachment', 'set_draft_reply', 'update_state', 'delete_message',
    'delete_thread', 'mark_read', 'mark_received_read', 'mark_unread', 'recover_interrupted',
    'reconcile_outgoing', 'reconcile_phone_identities', 'mark_message_read', 'mark_read_through', 'ingest', 'set_reaction', 'resolve_sent_conversation'})

XML = '''
<method name="ApplicationAccounts"><arg type="s" direction="out"/></method>
<method name="ApplicationStore"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<method name="ApplicationAttachment"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="h" direction="out"/></method>
<method name="ApplicationImport"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="h" direction="in"/><arg type="s" direction="out"/></method>
<method name="ApplicationCloud"><arg type="s" direction="out"/></method>
<method name="ApplicationInfo"><arg type="s" direction="out"/></method>
<method name="ApplicationNative"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<method name="ApplicationPhones"><arg type="s" direction="out"/></method>
<method name="ApplicationPhone"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<method name="ApplicationOutbound"><arg type="s" direction="in"/><arg type="b" direction="in"/><arg type="s" direction="out"/></method>
<method name="ApplicationLogin"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<signal name="StoreChanged"/>
<signal name="MailboxError"><arg type="s"/></signal>
<signal name="LoginStep"><arg type="s"/><arg type="s"/><arg type="s"/></signal>
'''


def pack(value):
    if is_dataclass(value):
        data = {f.name: pack(getattr(value, f.name)) for f in fields(value)}
        return {'$record': type(value).__name__, 'data': data}
    if isinstance(value, Path):
        # Media previews use an explicit descriptor request. Host paths are not
        # an application capability and must not be returned as filenames.
        import re
        revision = value.name if re.fullmatch('[0-9a-f]{64}', value.name) else ''
        return {'$preview': True, 'revision': revision}
    if isinstance(value, (tuple, list)):
        return [pack(v) for v in value]
    if isinstance(value, dict):
        return {str(k): pack(v) for k, v in value.items()}
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError('Unsupported mailbox result')


def payload(value):
    result = json.dumps(pack(value), ensure_ascii=False, allow_nan=False)
    if len(result.encode()) > MAX_OUTPUT:
        raise ValueError('The mailbox result exceeds the application limit')
    return result


def arguments(text):
    if len(text.encode()) > MAX_INPUT:
        raise ValueError('Mailbox request is too large')
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {'args', 'kwargs'}:
        raise ValueError('Invalid mailbox request')
    if not isinstance(value['args'], list) or not isinstance(value['kwargs'], dict):
        raise ValueError('Invalid mailbox arguments')
    def bounded(v, depth=0):
        if depth > 8:
            raise ValueError('Mailbox arguments are too deeply nested')
        if isinstance(v, (list, dict)):
            if len(v) > 256:
                raise ValueError('Too many mailbox arguments')
            for item in (v.values() if isinstance(v, dict) else v): bounded(item, depth+1)
        elif v is not None and type(v) not in (str, int, bool, float):
            raise ValueError('Invalid mailbox argument')
        elif type(v) is float and not math.isfinite(v):
            raise ValueError('Invalid mailbox number')
    bounded(value)
    return value['args'], value['kwargs']


def authenticate_caller(connection, sender):
    """Native same-user clients retain native authority; sandboxes need signed identity."""
    def credential(method):
        return connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', method, GLib.Variant('(s)', (sender,)),
            GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    uid, pid = credential('GetConnectionUnixUser'), credential('GetConnectionUnixProcessID')
    if uid == 0 or uid != os.getuid(): raise PermissionError('Foreign Messages caller')
    from luma_installer.app_data_broker import _start
    before = _start(pid)
    try:
        Path(f'/proc/{pid}/root/.flatpak-info').stat()
    except FileNotFoundError:
        if _start(pid) != before: raise PermissionError('Messages caller changed')
        return
    ApplicationMailbox.authenticate(connection, sender)
    if _start(pid) != before: raise PermissionError('Messages caller changed')


class ApplicationMailbox:
    def __init__(self, agent):
        self.agent = agent
        # SQLite connections stay on their one owner thread. This also avoids
        # reopening/migrating a database on every read and generating a redraw
        # loop through the Agent's existing file monitors.
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='messages-mailbox')
        self.transport_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='messages-mailbox-transport')
        self.slots = threading.BoundedSemaphore(4)
        self.stores = {}
        self.delivery_stores = {}
        self.modem = ModemMessagingTransport()
        self.mms = MmsMessagingTransport()
        self.native_started = False
        self.logins = {}
        self.login_watches = {}
        self.phones = None
        self.native_pending = set()
        self.native_scan = None
        self.native_import_running = False
        self.native_lock = threading.Lock()
        self.closed = False
        self.native_error = False

    @staticmethod
    def authenticate(connection, sender):
        from luma_installer.app_data_broker import authenticate
        if authenticate(connection, sender) != 'org.projectluma.Messages':
            raise PermissionError('This mailbox belongs to Messages')

    def store(self, account, *, delivery=False):
        if account == 'native':
            path = self.agent._native_store_path()
            cls = NativeMailbox
        elif self.phones is not None and account in self.phones:
            path = self.phones[account].store_path
            cls = NativeMailbox
        elif ACCOUNT_ID.fullmatch(account):
            provider = self.agent.services.get(account)
            if provider is None:
                raise KeyError(account)
            path = provider.store_path
            cls = AccountStore
        else:
            raise ValueError('Invalid account identity')
        stores = self.delivery_stores if delivery else self.stores
        previous = stores.get(account)
        if previous is not None and previous.path != path:
            previous.close(); previous = None
        if previous is None:
            previous = stores[account] = cls(path)
        return previous

    @staticmethod
    def authorize_request(store, uid, *, kind='send', retry=False):
        # Only the fixed, authenticated person-facing RPC may mint a request;
        # this is not a network bearer token or an MLS credential.
        record = store.message(uid)
        if kind not in {'send', 'react'} or (kind == 'send' and record.direction != 'outgoing'):
            raise ValueError('Invalid user delivery request')
        if retry and record.state == 'sent':
            raise ValueError('A confirmed send cannot be retried')
        return store.authorize_send(uid, kind=kind, retry=retry)

    def execute(self, account, operation, text):
        args, kwargs = arguments(text)
        store = self.store(account)
        if operation == 'authorize_send':
            fn = lambda *a, **k: self.authorize_request(store, *a, **k)
        elif operation in READS | WRITES:
            fn = getattr(store, operation)
        else:
            raise ValueError('Unsupported mailbox operation')
        inspect.signature(fn).bind(*args, **kwargs)
        if operation == 'reconcile_outgoing':
            if account != 'native':
                raise ValueError('Network accounts cannot reconcile native modem records')
            args = [tuple(TransportMessage(**v['data']) for v in args[0])]
        if operation == 'ingest' and account != 'native':
            raise ValueError('Only the native mailbox ingests modem messages')
        return fn(*args, **kwargs)

    def attachment(self, account, uid, part):
        store = self.store(account)
        if part:
            row = store.media_part(uid, part)
            if row is None or row.preview is None:
                raise KeyError(uid)
            path = row.preview
        else:
            row = store._connection.execute('SELECT * FROM message_attachments WHERE uid=?', (uid,)).fetchone()
            if row is None: raise KeyError(uid)
            path = store.attachment_path(store._attachment(row))
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ATTACHMENT_BYTES:
            os.close(fd); raise ValueError('Invalid attachment')
        return fd

    def import_attachment(self, account, address, name, fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ATTACHMENT_BYTES:
            raise ValueError('Choose a nonempty regular attachment under 25 MB')
        store = self.store(account)
        with tempfile.TemporaryDirectory(prefix='.application-import-', dir=store.path.parent) as directory:
            path = Path(directory) / 'attachment'
            with path.open('xb') as output:
                count = 0
                while chunk := os.pread(fd, min(1024*1024, MAX_ATTACHMENT_BYTES+1-count), count):
                    count += len(chunk)
                    if count > MAX_ATTACHMENT_BYTES: raise ValueError('Attachment exceeds 25 MB')
                    output.write(chunk)
                after = os.fstat(fd)
                if count != info.st_size or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                    raise ValueError('The attachment changed while being imported')
                output.flush(); os.fsync(output.fileno())
            return store.attach_file(address, path, name=Path(name).name[:255])

    def start_native(self):
        self.native_started = True
        self.mms.start(self.native_arrived, lambda _cap: self.agent._schedule_count())

    def native_arrived(self, path, _properties):
        with self.native_lock:
            if self.closed: return
            if len(self.native_pending) < 64: self.native_pending.add(path)
            else: self.native_scan = ''
            if self.native_import_running: return
            self.native_import_running = True
        try: self.worker.submit(self.drain_native)
        except RuntimeError:
            with self.native_lock: self.native_import_running = False

    def drain_native(self):
        # At most one queued import batch. Overflow rescans the carrier owner's
        # existing authoritative cache eight paths at a time, rather than
        # allocating one future (or attachment payload) per signal.
        with self.native_lock:
            if self.closed:
                self.native_import_running = False; return
            paths = [self.native_pending.pop() for _ in range(min(8, len(self.native_pending)))]
            cursor = self.native_scan
        if not paths and cursor is not None:
            try: paths = heapq.nsmallest(8, (p for p in self.mms._messages if p > cursor))
            except RuntimeError: paths = []; cursor = ''
            with self.native_lock:
                if self.native_scan == cursor:
                    self.native_scan = paths[-1] if len(paths) == 8 else None
                elif not paths: self.native_scan = ''
        for path in paths:
            properties = self.mms.cached_message(path)
            if properties is None: continue
            try:
                import_mms(self.store('native'), path, properties)
                self.native_error = False
            except Exception:
                if not self.native_error and self.agent.agent is not None:
                    self.native_error = True
                    def notify_error():
                        self.agent.agent.connection.emit_signal(None, '/org/projectluma/Messages/Agent',
                            'org.projectluma.Messages.Agent1', 'MailboxError', GLib.Variant('(s)',
                            ('An MMS could not be imported. Its original remains in the carrier service.',)))
                        return GLib.SOURCE_REMOVE
                    GLib.idle_add(notify_error)
        GLib.idle_add(lambda: (self.agent._schedule_count(), False)[1])
        with self.native_lock:
            more = not self.closed and (self.native_pending or self.native_scan is not None)
            if not more: self.native_import_running = False
        if more:
            try: self.worker.submit(self.drain_native)
            except RuntimeError:
                with self.native_lock: self.native_import_running = False

    def native_send(self, uid, token, account='native'):
        store = self.store(account, delivery=True)
        record = store.message(uid)
        if record.direction != 'outgoing' or not isinstance(token, str) or len(token) != 32:
            raise ValueError('Invalid native delivery request')
        from .messages_outbound import disabled_reason
        if disabled_reason(self.agent.accounts.root):
            raise ValueError('Sending is turned off')
        # Claim before handing anything to the modem. Ambiguous carrier errors
        # cannot replay a consumed request; a person must explicitly retry.
        prior = store._connection.execute("SELECT 1 FROM send_tokens WHERE message_uid=? AND kind='send' AND state='claimed' LIMIT 1", (uid,)).fetchone()
        claim = store.claim_send(uid, token)
        if claim is None: return {'state': store.message(uid).state}
        try:
            if account != 'native':
                provider = self.phones[account]
                return (provider.retry_message if prior else provider.send_message)(uid)
            if record.attachments:
                with tempfile.TemporaryDirectory(prefix='.mms-send-', dir=store.path.parent) as temporary:
                    parts = [(p.uid, p.content_type, str(store.attachment_path(p))) for p in record.attachments]
                    body = record.wire_body or record.body
                    if body:
                        caption = Path(temporary) / 'message.txt'
                        caption.write_text(body, encoding='utf-8'); caption.chmod(0o600)
                        parts.insert(0, ('message.txt', 'text/plain', str(caption)))
                    carrier = self.mms.send((record.address,), parts)
                state, transport = 'queued', 'mmsd:' + carrier
            else:
                self.modem.send(record.address, record.wire_body or record.body)
                state, transport = 'sent', 'mm-outgoing:v1:' + uid
            store.update_state(uid, state, transport)
            return {'state': state}
        except Exception:
            store.update_state(uid, 'failed')
            return {'state': 'failed'}

    def native(self, operation, text):
        args, kwargs = arguments(text)
        if kwargs: raise ValueError('Invalid native arguments')
        if operation == 'inspect' and not args: return self.modem.inspect()
        if operation == 'snapshot' and not args: return self.modem.snapshot()
        if operation == 'mms_inspect' and not args: return self.mms.capability
        if operation == 'send' and len(args) == 2: return self.native_send(*args)
        if operation in {'mms_read', 'mms_delete'} and len(args) == 1:
            path = args[0]
            if not isinstance(path, str) or len(path) > 512 or path not in self.mms._messages:
                raise ValueError('Unknown carrier message')
            store = self.store('native', delivery=True)
            if not store.uid_for_transport('mmsd:' + path) and not store.is_transport_deleted('mmsd:' + path):
                raise ValueError('Carrier message is not in this mailbox')
            (self.mms.mark_read if operation == 'mms_read' else self.mms.delete)(path)
            return None
        raise ValueError('Unsupported native operation')

    def discover_phones(self):
        # Existing continuity owner owns grants, revocation, keyring and the
        # exactly-once outbox. The sandbox receives public labels, never pairing
        # secrets or an identity-directory mount.
        self.phones = {}
        try:
            from luma_continuity.message_provider import message_services
        except ImportError:
            return
        for provider in message_services(store_factory=MessageStore, dispatch=GLib.idle_add):
            identity = 'phone:' + provider.peer
            self.phones[identity] = provider
            self.agent._watch_store(provider.store_path.parent)
            provider.start(lambda state, identity=identity: (
                self.agent._emit_account(identity, state), self.agent._schedule_count()))

    def phone(self, account, operation, text):
        provider = self.phones[account]
        args, kwargs = arguments(text)
        if kwargs: raise ValueError('Invalid phone request')
        if operation == 'inspect' and not args: return provider.sms_transport().inspect()
        if operation == 'mms_inspect' and not args:
            transport = provider.mms_transport()
            capability = []
            transport.start(lambda *_: None, capability.append)
            transport.stop()
            return capability[0] if capability else transport.inspect()
        if operation == 'refresh' and not args: provider.refresh(); return None
        if operation == 'send' and len(args) == 2: return self.native_send(*args, account=account)
        raise ValueError('Unsupported phone operation')

    def cancel_login(self, sender):
        entry = self.logins.pop(sender, None)
        if entry: entry[1].cancel()
        watch = self.login_watches.pop(sender, 0)
        if watch: Gio.bus_unwatch_name(watch)

    def login(self, connection, sender, action, identity, text):
        from .messages_accounts import AccountLogin, network, available_networks
        data = json.loads(text)
        if not isinstance(data, dict): raise ValueError('Invalid sign-in request')
        if action == 'start':
            if set(data) != {'method', 'account', 'session'}: raise ValueError('Invalid sign-in request')
            item = network(identity)
            if item.automatic or item not in available_networks(self.agent.helper_dir) or data['method'] not in item.methods:
                raise ValueError('This network sign-in is unavailable')
            session = str(uuid.UUID(data['session']))
            existing = None
            if data['account']:
                existing = next((a for a in self.agent.accounts.list() if a.id == data['account'] and a.network == identity), None)
                if existing is None: raise ValueError('Unknown account')
            self.cancel_login(sender)
            if len(self.logins) >= 4: raise ValueError('Too many sign-ins')
            def step(name, value):
                if self.logins.get(sender, (None,))[0] != session: return
                if name == 'login.done': clean = {'account': value['account']}
                elif name == 'login.qr': clean = {'data': str(value.get('data', ''))[:65536]}
                elif name == 'login.code': clean = {k: str(value.get(k, ''))[:4096] for k in ('code', 'kind')}
                elif name == 'login.needs': clean = {k: str(value.get(k, ''))[:4096] for k in ('field', 'hint')}
                elif name == 'login.error': clean = {'message': str(value.get('message', ''))[:4096]}
                elif name in {'starting', 'browser.waiting'}: clean = {}
                else: return
                connection.emit_signal(sender, '/org/projectluma/Messages/Agent', 'org.projectluma.Messages.Agent1',
                    'LoginStep', GLib.Variant('(sss)', (session, name, payload(clean))))
                if name == 'login.done': self.agent.reload()
                if name in {'login.done', 'login.error'}: self.cancel_login(sender)
            owner = AccountLogin(identity, self.agent.accounts, secrets=self.agent.secrets,
                dispatch=GLib.idle_add, on_step=step, helper_dir=self.agent.helper_dir, account=existing)
            self.logins[sender] = (session, owner)
            self.login_watches[sender] = Gio.bus_watch_name_on_connection(connection, sender,
                Gio.BusNameWatcherFlags.NONE, None, lambda *_: self.cancel_login(sender))
            owner.start(data['method'])
            return session
        entry = self.logins.get(sender)
        if entry is None or entry[0] != identity: raise ValueError('Unknown sign-in session')
        if action == 'cancel' and not data: self.cancel_login(sender); return None
        if action == 'submit' and set(data) == {'field', 'value'}:
            if not isinstance(data['field'], str) or not 0 < len(data['field']) <= 64 or not isinstance(data['value'], str) or len(data['value'].encode()) > 65536:
                raise ValueError('Invalid sign-in field')
            entry[1].submit(data['field'], data['value']); return None
        raise ValueError('Unsupported sign-in action')

    def call(self, connection, sender, method, parameters, invocation):
        if not method.startswith('Application'): return False
        try:
            self.authenticate(connection, sender)
            if not self.slots.acquire(blocking=False):
                raise RuntimeError('The mailbox is busy; try again')
        except Exception:
            invocation.return_dbus_error('org.projectluma.Messages.Agent1.Error.Refused', 'The mailbox request was refused')
            return True
        values = parameters.unpack()
        incoming_fd = None
        try:
            self.agent._schedule_idle_exit()
            if method == 'ApplicationImport':
                incoming_fd = invocation.get_message().get_unix_fd_list().get(values[3])
            elif method not in {'ApplicationAccounts','ApplicationStore','ApplicationAttachment','ApplicationCloud', 'ApplicationInfo', 'ApplicationNative', 'ApplicationOutbound', 'ApplicationLogin', 'ApplicationPhones', 'ApplicationPhone'}:
                raise ValueError('Unsupported application method')
            if sum(len(v.encode()) for v in values if isinstance(v, str)) > MAX_INPUT:
                raise ValueError('Mailbox request is too large')
            if method == 'ApplicationNative' and not self.native_started:
                self.start_native()
            if method in {'ApplicationPhones', 'ApplicationPhone'} and self.phones is None:
                self.discover_phones()
            if method == 'ApplicationLogin':
                result = self.login(connection, sender, *values)
                invocation.return_value(GLib.Variant('(s)', (payload(result),)))
                self.slots.release()
                return True
        except Exception:
            if incoming_fd is not None: os.close(incoming_fd)
            self.slots.release()
            invocation.return_dbus_error('org.projectluma.Messages.Agent1.Error.InvalidInput', 'Invalid mailbox request')
            return True
        def work():
            descriptor = None
            try:
                # The process, installed commit and caller identity must still
                # be current after waiting for the bounded worker slot.
                self.authenticate(connection, sender)
                if method == 'ApplicationAccounts':
                    result = [a for a in self.agent.accounts.list() if a.id in self.agent.services]
                elif method == 'ApplicationStore':
                    result = self.execute(*values)
                elif method == 'ApplicationAttachment':
                    descriptor = self.attachment(*values); result = None
                elif method == 'ApplicationImport':
                    result = self.import_attachment(*values[:3], incoming_fd)
                elif method == 'ApplicationNative':
                    result = self.native(*values)
                elif method == 'ApplicationPhones':
                    result = [{'id': identity, 'label': provider.label} for identity, provider in self.phones.items()]
                elif method == 'ApplicationPhone':
                    result = self.phone(*values)
                elif method == 'ApplicationOutbound':
                    provider = self.agent.services[values[0]]
                    if values[1]: provider.resume_outbound()
                    result = provider.outbound_disabled()
                elif method == 'ApplicationInfo':
                    from .messages_accounts import available_networks
                    from .messages_luma import setup_problem
                    result = {'networks': [n.id for n in available_networks(self.agent.helper_dir)],
                              'luma_problem': setup_problem(helper_dir=self.agent.helper_dir)}
                else:
                    from .connect_messages import cloud_messages
                    result = cloud_messages()
                encoded = payload(result)
                error = ''
            except Exception:
                error = 'The mailbox operation failed'; encoded = ''
            finally:
                if incoming_fd is not None: os.close(incoming_fd)
            def finish():
                try:
                    if error:
                        invocation.return_dbus_error('org.projectluma.Messages.Agent1.Error.Failed', error)
                    elif descriptor is not None:
                        fds = Gio.UnixFDList.new(); handle = fds.append(descriptor)
                        invocation.return_value_with_unix_fd_list(GLib.Variant('(h)', (handle,)), fds)
                    else:
                        invocation.return_value(GLib.Variant('(s)', (encoded,)))
                finally:
                    if descriptor is not None: os.close(descriptor)
                    self.slots.release()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(finish)
        transport_call = method == 'ApplicationPhone' or (method == 'ApplicationNative' and values[0] != 'mms_inspect')
        executor = self.transport_worker if transport_call else self.worker
        try: executor.submit(work)
        except RuntimeError:
            if incoming_fd is not None: os.close(incoming_fd)
            self.slots.release()
            invocation.return_dbus_error('org.projectluma.Messages.Agent1.Error.Unavailable', 'The mailbox is closing')
        return True

    def close(self):
        with self.native_lock: self.closed = True
        self.mms.stop()
        for provider in (self.phones or {}).values(): provider.close()
        for sender in list(self.logins): self.cancel_login(sender)
        def close_stores():
            for store in self.stores.values(): store.close()
            self.stores.clear()
        try: self.worker.submit(close_stores)
        except RuntimeError: pass
        self.worker.shutdown(wait=False)
        def close_delivery():
            for store in self.delivery_stores.values(): store.close()
            self.delivery_stores.clear()
        try: self.transport_worker.submit(close_delivery)
        except RuntimeError: pass
        self.transport_worker.shutdown(wait=False)
