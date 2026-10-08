"""Bounded Connect relay metadata; never native capability or pairing authority."""
from dataclasses import dataclass
import json
import time
from urllib.parse import urlsplit
from uuid import UUID


def identifier(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('invalid relay identifier')
    return value


@dataclass(frozen=True)
class RelaySession:
    session_id: str
    pair_id: str
    expires: int
    url: str
    max_frame_bytes: int
    max_total_bytes: int

    @classmethod
    def parse(cls, data, api_origin):
        if not isinstance(data, dict) or set(data) != {'session_id', 'generation', 'pair_id',
                'expires', 'url', 'max_frame_bytes', 'max_total_bytes', 'end_to_end_tls_required'}:
            raise ValueError('invalid relay session metadata')
        sid, pair = identifier(data['session_id']), identifier(data['pair_id'])
        if data['generation'] != sid or data['end_to_end_tls_required'] is not True:
            raise ValueError('invalid relay generation or encryption boundary')
        origin = urlsplit(api_origin)
        if origin.scheme != 'https' or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username or origin.password:
            raise ValueError('invalid relay API origin')
        if data['url'] != 'wss://' + origin.netloc + '/v1/relay/' + sid:
            raise ValueError('unapproved relay destination')
        for name, maximum in [('expires', 2**53), ('max_frame_bytes', 65536), ('max_total_bytes', 10485760)]:
            if type(data[name]) is not int or not 0 < data[name] <= maximum:
                raise ValueError('invalid relay bounds')
        return cls(sid, pair, data['expires'], data['url'], data['max_frame_bytes'], data['max_total_bytes'])


class RelayDirectory:
    """A snapshot reconciler scoped to existing local pair bindings.

    Callbacks may close obsolete sessions, but this object never changes local
    grants, account identity, queued operations or the pairing epoch.
    """
    def __init__(self, device_id, pair_ids, api_origin, *, now=time.time):
        self.device_id = identifier(device_id)
        self.pair_ids = frozenset(identifier(pair) for pair in pair_ids)
        self.api_origin, self.now = api_origin, now
        self.sessions = {}
        self.cursor = None

    def apply(self, event, cursor, data):
        if not isinstance(cursor, str) or not 0 < len(cursor) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in cursor):
            raise ValueError('invalid event cursor')
        before = dict(self.sessions)
        after = {p:s for p,s in before.items() if s.expires > self.now()}
        if event == 'snapshot':
            if (not isinstance(data, dict) or set(data) != {'device_id','sessions','reset'}
                    or data['device_id'] != self.device_id or type(data['reset']) is not bool
                    or not isinstance(data['sessions'], list) or len(data['sessions']) > 100):
                raise ValueError('invalid relay snapshot')
            after = {}
            seen = set()
            for item in data['sessions']:
                session = RelaySession.parse(item, self.api_origin)
                if session.pair_id in seen: raise ValueError('duplicate pair session')
                seen.add(session.pair_id)
                if session.pair_id in self.pair_ids and session.expires > self.now():
                    after[session.pair_id] = session
        elif event == 'session_offered':
            session = RelaySession.parse(data, self.api_origin)
            if session.pair_id in self.pair_ids and session.expires > self.now():
                after[session.pair_id] = session
        elif event in {'session_closed','revoked'}:
            fields = {'pair_id','reason'} | ({'session_id','generation'} if event == 'session_closed' else set())
            if not isinstance(data, dict) or set(data) != fields: raise ValueError('invalid relay closure')
            pair = identifier(data['pair_id'])
            if not isinstance(data['reason'],str) or not 0 < len(data['reason']) <= 64:
                raise ValueError('invalid closure reason')
            if event == 'session_closed':
                sid = identifier(data['session_id'])
                if data['generation'] != sid: raise ValueError('invalid closure generation')
                if pair in after and after[pair].session_id == sid: after.pop(pair)
            else:
                after.pop(pair, None)
        else:
            raise ValueError('unknown relay event')
        # Validate the whole event before changing checkpoint or active state.
        self.sessions, self.cursor = after, cursor
        return [session for pair,session in before.items() if after.get(pair) != session]


class EventDecoder:
    """Incremental SSE decoder with bounded lines/events; no credential logging."""
    def __init__(self):
        self.buffer = bytearray()
        self.lines = []
        self.size = 0

    def feed(self, chunk):
        if not isinstance(chunk, bytes) or len(chunk) > 65536: raise ValueError('event chunk bound')
        self.buffer.extend(chunk)
        result = []
        while b'\n' in self.buffer:
            raw, _, remainder = self.buffer.partition(b'\n')
            self.buffer = bytearray(remainder)
            self.size += len(raw) + 1
            if len(raw) > 65536 or self.size > 131072: raise ValueError('event size bound')
            line = raw.rstrip(b'\r').decode('utf-8', errors='strict')
            if not line:
                if self.lines:
                    fields = {}
                    for name,value in self.lines:
                        if name in fields: raise ValueError('duplicate event field')
                        fields[name] = value
                    if set(fields) != {'event','id','data'}: raise ValueError('incomplete relay event')
                    def unique(pairs):
                        obj = {}
                        for key,value in pairs:
                            if key in obj: raise ValueError('duplicate event JSON key')
                            obj[key] = value
                        return obj
                    def reject(_value): raise ValueError('nonfinite event JSON')
                    result.append((fields['event'],fields['id'],json.loads(fields['data'],object_pairs_hook=unique,parse_constant=reject)))
                self.lines, self.size = [], 0
            elif not line.startswith(':'):
                name, separator, value = line.partition(':')
                if not separator or name not in {'event','id','data'}: raise ValueError('invalid event field')
                self.lines.append((name,value[1:] if value.startswith(' ') else value))
        if len(self.buffer) > 65536: raise ValueError('event line bound')
        return result
