"""Native relay API, using only the configured strict HTTPS account client."""
import time
import threading
from .account import AccountError
from .relay import connect_wss
from .relay_control import RelaySession, EventDecoder, identifier


class RelayAPI:
    def __init__(self, api, device_id, *, bearer, authorized, now=time.time, connector=connect_wss):
        self.api,self.device_id=api,identifier(device_id)
        self.bearer,self.authorized,self.now=bearer,authorized,now
        self.connector=connector
        self._event_lock=threading.Lock();self._event_cancel=None

    def _token(self):
        if not self.authorized(): raise PermissionError('account authorization unavailable')
        token=self.bearer()
        if not isinstance(token,str) or not token or '\r' in token or '\n' in token:
            raise PermissionError('account credential unavailable')
        return token

    def ensure(self, pair_id):
        pair_id=identifier(pair_id)
        result=self.api.request('POST','/v1/relay-sessions',self._token(),
            {'device_id':self.device_id,'pair_id':pair_id})
        session=RelaySession.parse(result,self.api.config.api_origin)
        if session.pair_id!=pair_id or not self.now()<session.expires<=self.now()+600 or not self.authorized():
            raise PermissionError('obsolete relay offer')
        return session

    def open(self, session):
        from dataclasses import asdict
        session=RelaySession.parse({**asdict(session),'generation':session.session_id,
            'end_to_end_tls_required':True},self.api.config.api_origin)
        token=self._token()
        if session.expires<=self.now(): raise PermissionError('relay session expired')
        result=self.api.request('POST','/v1/relay-sessions/'+identifier(session.session_id)+'/tickets',token,
            {'device_id':self.device_id})
        if (not isinstance(result,dict) or set(result)!={'ticket','expires','session_id','url','header','one_use'}
                or result['session_id']!=session.session_id or result['url']!=session.url
                or result['header']!='X-Luma-Relay-Ticket' or result['one_use'] is not True
                or type(result['expires']) is not int or not self.now()<result['expires']<=min(session.expires,self.now()+60)
                or not self.authorized()): raise PermissionError('invalid relay ticket binding')
        # connect_wss validates TLS and headers; it never writes ticket/token to disk.
        socket=self.connector(session.url,result['ticket'],bearer=token)
        if not self.authorized():
            socket.close();raise PermissionError('account authorization lost')
        return socket

    def close_session(self, session):
        return self.api.request('DELETE','/v1/relay-sessions/'+identifier(session.session_id),self._token(),
            {'device_id':self.device_id,'generation':session.session_id})

    def cancel_events(self):
        # The iterator owns TLS I/O and close. Cross-thread response.close can
        # release a descriptor while OpenSSL is still using it in recv().
        with self._event_lock:
            if self._event_cancel is not None:self._event_cancel.set()

    def _begin_events(self):
        cancelled=threading.Event()
        with self._event_lock:
            if self._event_cancel is not None:self._event_cancel.set()
            self._event_cancel=cancelled
        return cancelled

    def _end_events(self,cancelled):
        with self._event_lock:
            if self._event_cancel is cancelled:self._event_cancel=None

    def events(self, cursor=None):
        import httpx
        token=self._token()
        headers={'Authorization':'Bearer '+token,'Accept':'text/event-stream'}
        if cursor is not None:
            if not isinstance(cursor,str) or not 0<len(cursor)<=128 or any(ord(c)<33 or ord(c)>126 for c in cursor):
                raise ValueError('invalid event cursor')
            headers['Last-Event-ID']=cursor
        parser=EventDecoder()
        cancelled=self._begin_events()
        try:
            with self.api.client.stream('GET',self.api.config.api_origin+'/v1/devices/'+self.device_id+'/relay-events',
                    headers=headers,timeout=httpx.Timeout(30,connect=5)) as response:
                if cancelled.is_set():return
                if not self.authorized():raise PermissionError('account authorization lost')
                if response.status_code==429:
                    delay=response.headers.get('Retry-After','60')
                    raise AccountError('rate_limited',429,min(3600,max(1,int(delay))) if delay.isdecimal() else 60)
                if response.status_code!=200 or response.headers.get('content-type','').split(';')[0]!='text/event-stream':
                    raise AccountError('relay_events_unavailable',response.status_code)
                for chunk in response.iter_bytes():
                    if cancelled.is_set():return
                    if not self.authorized(): raise PermissionError('account authorization lost')
                    for event in parser.feed(chunk): yield event
        except httpx.HTTPError:
            raise AccountError('relay_events_unavailable') from None
        finally:
            self._end_events(cancelled)
