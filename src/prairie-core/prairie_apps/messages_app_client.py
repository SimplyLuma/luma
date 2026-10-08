# SPDX-License-Identifier: Apache-2.0
"""Sandbox mailbox client for the existing Messages Agent owner."""
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import tempfile
import uuid
from gi.repository import Gio, GLib
from .messages_accounts import Account, MediaPart, ACCOUNT_ID
from .messages_backend import (AttachmentRecord, MessageRecord, QuoteRecord,
    ReactionRecord, ThreadRecord, MAX_ATTACHMENT_BYTES, MessagingCapability, TransportMessage)
from .messages_mms import MmsCapability

RECORDS = {c.__name__: c for c in (Account, MediaPart, AttachmentRecord, MessageRecord,
    QuoteRecord, ReactionRecord, ThreadRecord, MessagingCapability, TransportMessage, MmsCapability)}


class HostServiceUnavailable(RuntimeError):
    """The approved host owner is absent; never fall back to sandbox secrets."""


def unpack(value):
    if isinstance(value, list): return tuple(unpack(v) for v in value)
    if isinstance(value, dict):
        if set(value) == {'$record', 'data'}:
            cls = RECORDS.get(value['$record'])
            if cls is None: raise ValueError('Unsupported mailbox record')
            data = {k: unpack(v) for k, v in value['data'].items()}
            if cls is MediaPart and isinstance(data.get('preview'), dict):
                data['preview'] = None
            return cls(**data)
        return {k: unpack(v) for k, v in value.items()}
    return value


def sandboxed():
    return Path('/.flatpak-info').is_file()


def cache_directory():
    path = Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache')) / 'prairie/messages/mailbox'
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


class ApplicationAccounts:
    def __init__(self, client):
        self.client = client
        self.root = cache_directory() / 'accounts'

    def list(self, *, include_pending=False):
        # Pending login state and keyring material never leave the host owner.
        return list(unpack(json.loads(self.client.call('ApplicationAccounts')[0])))

    def directory(self, account):
        if not ACCOUNT_ID.fullmatch(account): raise ValueError('Invalid account')
        return self.root / account


class ApplicationStore:
    def __init__(self, client, account):
        self.client, self.account = client, account
        # A private scratch path, never the host account's database filename.
        self.path = cache_directory() / hashlib.sha256(account.encode()).hexdigest() / 'mailbox'
        self.path.parent.mkdir(mode=0o700, exist_ok=True)

    def clone(self): return ApplicationStore(self.client, self.account)
    def close(self): pass

    def _call(self, operation, *args, **kwargs):
        from .messages_app_rpc import pack
        data = json.dumps({'args': pack(args), 'kwargs': pack(kwargs)}, allow_nan=False)
        reply = self.client.call('ApplicationStore', GLib.Variant('(sss)', (self.account, operation, data)))
        value = json.loads(reply[0])
        def previews(row):
            if isinstance(row, list): return [previews(r) for r in row]
            if isinstance(row, dict) and row.get('$record') == 'MediaPart':
                data = row['data']
                preview = data.get('preview')
                if isinstance(preview, dict) and preview.get('$preview'):
                    data['preview'] = str(self._descriptor_file(data['message_uid'], data['part'], cache_key=preview.get('revision', '')))
            return row
        result = unpack(previews(value))
        if operation == 'media_parts':
            from dataclasses import replace
            result = tuple(replace(r, preview=Path(r.preview)) if r.preview else r for r in result)
        elif operation == 'media_part' and result is not None and result.preview:
            from dataclasses import replace
            result = replace(result, preview=Path(result.preview))
        return result

    def __getattr__(self, name):
        # Keep this list aligned with the host's explicit public store API.
        # No dynamic attributes, SQL, connection handles or file paths exist.
        from .messages_app_rpc import READS, WRITES
        if name in READS | WRITES | {'authorize_send'}:
            return lambda *args, **kwargs: self._call(name, *args, **kwargs)
        raise AttributeError(name)

    def attach_file(self, address, source, *, name=None):
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ATTACHMENT_BYTES:
                raise ValueError('Choose a nonempty regular attachment under 25 MB')
            fds = Gio.UnixFDList.new(); index = fds.append(fd)
            result, _ = self.client.connection.call_with_unix_fd_list_sync(
                'org.projectluma.Messages.Agent', '/org/projectluma/Messages/Agent',
                'org.projectluma.Messages.Agent1', 'ApplicationImport',
                GLib.Variant('(sssh)', (self.account, address, str(name or Path(source).name), index)),
                GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NO_AUTO_START, 30000, fds, None)
            return unpack(json.loads(result.unpack()[0]))
        finally: os.close(fd)

    def attachment_path(self, attachment):
        return self._descriptor_file(attachment.uid, '', attachment.size, cache_key=attachment.storage_key)

    def _descriptor_file(self, uid, part, size=None, *, cache_key=''):
        # Select an attachment by its opaque ID. The server looks up its own
        # storage key; caller-provided filenames never select host files.
        if cache_key and not re.fullmatch('[0-9a-f]{64}', cache_key): raise ValueError('Invalid attachment revision')
        path = self.path.parent / hashlib.sha256((uid + ':' + part + ':' + cache_key).encode()).hexdigest()
        if cache_key:
            try: cached = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError: cached = None
            if cached is not None:
                try:
                    info = os.fstat(cached)
                    if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and 0 < info.st_size <= MAX_ATTACHMENT_BYTES and (size is None or info.st_size == size):
                        return path
                finally: os.close(cached)
        result, fds = self.client.connection.call_with_unix_fd_list_sync(
            'org.projectluma.Messages.Agent', '/org/projectluma/Messages/Agent',
            'org.projectluma.Messages.Agent1', 'ApplicationAttachment',
            GLib.Variant('(sss)', (self.account, uid, part)),
            GLib.VariantType.new('(h)'), Gio.DBusCallFlags.NO_AUTO_START, 10000, None, None)
        fd = fds.get(result.unpack()[0]); temporary = None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or (size is not None and info.st_size != size) or not 0 < info.st_size <= MAX_ATTACHMENT_BYTES:
                raise ValueError('Invalid attachment descriptor')
            out, temporary = tempfile.mkstemp(prefix='.attachment-', dir=self.path.parent)
            with os.fdopen(out, 'wb') as writer:
                offset = 0
                while chunk := os.pread(fd, min(1024*1024, MAX_ATTACHMENT_BYTES+1-offset), offset):
                    offset += len(chunk)
                    if offset > MAX_ATTACHMENT_BYTES: raise ValueError('Attachment exceeds 25 MB')
                    writer.write(chunk)
                if offset != info.st_size: raise ValueError('Attachment changed during preview')
                writer.flush(); os.fsync(writer.fileno())
            os.replace(temporary, path); temporary = None
            return path
        finally:
            os.close(fd)
            if temporary is not None: Path(temporary).unlink(missing_ok=True)


class NativeProvider:
    remote = True
    native = True
    requires_user_token = True
    def __init__(self, client):
        self.client = client
        self.status = {'state': 'ready'}
    def call(self, operation, *args):
        reply = self.client.call('ApplicationNative', GLib.Variant('(ss)',
            (operation, json.dumps({'args': args, 'kwargs': {}}, allow_nan=False))), timeout=60000)
        return unpack(json.loads(reply[0]))
    def start(self, notify): pass  # initial native discovery stays on the existing context worker
    def refresh(self): pass
    def close(self, **_kwargs): pass
    def send_message(self, uid, token): return self.call('send', uid, token)
    def retry_message(self, uid, token): return self.send_message(uid, token)
    def sms_transport(self):
        provider = self
        class Transport:
            def inspect(self): return provider.call('inspect')
            def snapshot(self): return provider.call('snapshot')
        return Transport()
    def mms_transport(self):
        provider = self
        class Transport:
            def inspect(self): return provider.call('mms_inspect')
            def start(self, _arrived, changed): changed(self.inspect())
            def stop(self): pass
            def mark_read(self, path): provider.call('mms_read', path)
            def delete(self, path): provider.call('mms_delete', path)
            def cached_message(self, _path): return None  # imported by its host owner
        return Transport()


class PhoneProvider(NativeProvider):
    native = False
    def __init__(self, client, identity):
        super().__init__(client)
        self.identity = identity
        self.status = {'state': 'connecting'}
    def call(self, operation, *args):
        reply = self.client.call('ApplicationPhone', GLib.Variant('(sss)',
            (self.identity, operation, json.dumps({'args': args, 'kwargs': {}}, allow_nan=False))), timeout=60000)
        return unpack(json.loads(reply[0]))
    def start(self, notify):
        self.notify = notify
        self.client.listen(self.identity, self._changed)
        self.refresh()
    def _changed(self, state):
        self.status = state; self.notify(state)
    def refresh(self): self.call('refresh')
    def close(self, **_kwargs): self.client.unlisten(self.identity, self._changed)


class ApplicationLogin:
    def __init__(self, client, network, on_step, account=None):
        self.client, self.network, self.on_step, self.account = client, network, on_step, account
        self.session = str(uuid.uuid4())
        self.subscription = client.connection.signal_subscribe('org.projectluma.Messages.Agent',
            'org.projectluma.Messages.Agent1', 'LoginStep', '/org/projectluma/Messages/Agent', None,
            Gio.DBusSignalFlags.NONE, self._step)
    def _step(self, _connection, _sender, _path, _interface, _signal, parameters, *_user):
        session, name, data = parameters.unpack()
        if session != self.session: return
        self.on_step(name, unpack(json.loads(data)))
        if name in {'login.done', 'login.error'}: self._unsubscribe()
    def _unsubscribe(self):
        if self.subscription:
            self.client.connection.signal_unsubscribe(self.subscription); self.subscription = 0
    def start(self, method):
        try:
            self.client.call('ApplicationLogin', GLib.Variant('(sss)', ('start', self.network,
                json.dumps({'method': method, 'account': self.account.id if self.account else '', 'session': self.session}))))
        except GLib.Error:
            self._unsubscribe(); self.on_step('login.error', {'message': 'Messages could not start sign-in. Try again.'})
    def submit(self, field, value):
        self.client.call('ApplicationLogin', GLib.Variant('(sss)', ('submit', self.session, json.dumps({'field': field, 'value': value}))))
    def cancel(self):
        if self.subscription:
            try: self.client.call('ApplicationLogin', GLib.Variant('(sss)', ('cancel', self.session, '{}')))
            except GLib.Error: pass
        self._unsubscribe()
