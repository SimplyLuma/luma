"""Purpose-bound Calls namespace; no Messages transport or native grants change."""
import asyncio
from dataclasses import asdict,dataclass
import ssl
from urllib.parse import urlsplit
from .account import AccountError
from .relay_api import RelayAPI
from .relay_control import RelaySession,EventDecoder,identifier

PURPOSES=frozenset({'call-control','audio-signaling'})


@dataclass(frozen=True)
class CallRelaySession(RelaySession):
    purpose:str
    attempt_id:str

    @classmethod
    def parse(cls,data,origin):
        value=dict(data);purpose=value.pop('purpose',None);attempt=identifier(value.pop('attempt_id',None))
        if purpose not in PURPOSES:raise ValueError('explicit call purpose required')
        sid=identifier(value.get('session_id'));parts=urlsplit(origin)
        expected='wss://'+parts.netloc+'/v1/call-relay/'+purpose+'/'+sid
        if value.get('url')!=expected:raise ValueError('unapproved call relay destination')
        # Reuse the strict shared bounds after verifying the Calls-only path.
        value['url']='wss://'+parts.netloc+'/v1/relay/'+sid
        base=RelaySession.parse(value,origin)
        return cls(**dict(asdict(base),url=expected),purpose=purpose,attempt_id=attempt)


class CallRelayAPI(RelayAPI):
    def admission_status(self,pair_id):
        pair_id=identifier(pair_id)
        result=self.api.request('GET','/v1/devices/'+self.device_id+'/call-relay-admission/'+pair_id,self._token())
        if (not isinstance(result,dict) or set(result) not in ({'recovery'},{'recovery','normal_capacity_available'})
                or 'normal_capacity_available' in result and type(result['normal_capacity_available']) is not bool
                or not self.authorized()):
            raise PermissionError('invalid call admission')
        grant=result['recovery']
        if grant is None:return dict(recovery=None,normal_capacity_available=result.get('normal_capacity_available'))
        if (not isinstance(grant,dict) or set(grant)!={'recovery_id','expires','purposes'}
                or type(grant['expires']) is not int or not self.now()<grant['expires']<=self.now()+901
                or not isinstance(grant['purposes'],list) or len(grant['purposes'])>2
                or any(not isinstance(p,str) or p not in PURPOSES for p in grant['purposes'])
                or len(set(grant['purposes']))!=len(grant['purposes'])):
            raise PermissionError('invalid call admission grant')
        identifier(grant['recovery_id'])
        return dict(recovery=grant,normal_capacity_available=result.get('normal_capacity_available'))

    def admission(self,pair_id):
        return self.admission_status(pair_id)['recovery']

    def mint(self,pair_id,purpose,recovery_id=None):
        if purpose not in PURPOSES:raise ValueError('explicit call purpose required')
        pair_id=identifier(pair_id)
        body={'device_id':self.device_id,'pair_id':pair_id,'purpose':purpose}
        if recovery_id is not None:body['recovery_id']=identifier(recovery_id)
        result=self.api.request('POST','/v1/call-relay-attempts',self._token(),body)
        if (set(result)!={'attempt_id','pair_id','purpose','start_deadline'} or result['pair_id']!=pair_id
                or result['purpose']!=purpose or type(result['start_deadline']) is not int
                or not self.now()<result['start_deadline']<=self.now()+125 or not self.authorized()):
            raise PermissionError('invalid call attempt')
        identifier(result['attempt_id'])
        return result

    def ensure_call(self,pair_id,attempt_id,purpose):
        if purpose not in PURPOSES:raise ValueError('explicit call purpose required')
        result=self.api.request('POST','/v1/call-relay-sessions',self._token(),
            {'device_id':self.device_id,'pair_id':identifier(pair_id),'attempt_id':identifier(attempt_id),'purpose':purpose})
        session=self._session(result)
        if (session.pair_id,session.attempt_id,session.purpose)!=(pair_id,attempt_id,purpose):
            raise PermissionError('call session binding mismatch')
        return session

    def _session(self,result):
        session=CallRelaySession.parse(result,self.api.config.api_origin)
        if not self.now()<session.expires<=self.now()+605 or not self.authorized():
            raise PermissionError('obsolete call session')
        return session

    def renew(self,session):
        result=self.api.request('POST','/v1/call-relay-sessions/'+session.session_id+'/renew',self._token(),
            {'device_id':self.device_id,'generation':session.session_id,'purpose':session.purpose})
        updated=self._session(result)
        if any(getattr(updated,k)!=getattr(session,k) for k in ('session_id','pair_id','attempt_id','purpose','url')):
            raise PermissionError('call renewal changed identity')
        return updated

    def close_call(self,session):
        return self.api.request('DELETE','/v1/call-relay-sessions/'+session.session_id,self._token(),
            {'device_id':self.device_id,'generation':session.session_id,'purpose':session.purpose})

    async def open_async(self,session):
        from websockets.asyncio.client import connect
        self._session(dict(asdict(session),generation=session.session_id,end_to_end_tls_required=True))
        def ticket():
            token=self._token()
            result=self.api.request('POST','/v1/call-relay-sessions/'+session.session_id+'/tickets',token,
                {'device_id':self.device_id,'purpose':session.purpose})
            if (set(result)!={'ticket','expires','session_id','url','header','one_use','purpose'}
                    or result['session_id']!=session.session_id or result['url']!=session.url
                    or result['purpose']!=session.purpose or result['header']!='X-Luma-Relay-Ticket'
                    or result['one_use'] is not True or type(result['expires']) is not int
                    or not self.now()<result['expires']<=min(session.expires,self.now()+60)
                    or not isinstance(result['ticket'],str) or not result['ticket']
                    or any(c in result['ticket'] for c in '\r\n') or not self.authorized()):
                raise PermissionError('invalid purpose-bound call ticket')
            return token,result['ticket']
        token,credential=await asyncio.to_thread(ticket)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.load_default_certs()
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        ws=await connect(session.url,ssl=context,proxy=None,additional_headers={
            'Authorization':'Bearer '+token,'X-Luma-Relay-Ticket':credential},max_size=65536,
            max_queue=4,compression=None,open_timeout=20,close_timeout=3)
        if not self.authorized():await ws.close();raise PermissionError('call authorization lost')
        return ws

    def events(self,cursor=None):
        import httpx
        headers={'Authorization':'Bearer '+self._token(),'Accept':'text/event-stream'}
        if cursor is not None:
            if not isinstance(cursor,str) or not 0<len(cursor)<=128 or any(ord(c)<33 or ord(c)>126 for c in cursor):
                raise ValueError('invalid call event cursor')
            headers['Last-Event-ID']=cursor
        parser=EventDecoder()
        cancelled=self._begin_events()
        try:
            with self.api.client.stream('GET',self.api.config.api_origin+'/v1/devices/'+self.device_id+'/call-relay-events',
                    headers=headers,timeout=httpx.Timeout(30,connect=5)) as response:
                if cancelled.is_set():return
                if not self.authorized():raise PermissionError('call authorization lost')
                if response.status_code==429:
                    delay=response.headers.get('Retry-After','60')
                    raise AccountError('rate_limited',429,min(3600,max(1,int(delay))) if delay.isdecimal() else 60)
                if response.status_code!=200 or response.headers.get('content-type','').split(';')[0]!='text/event-stream':
                    raise AccountError('call_events_unavailable',response.status_code)
                for chunk in response.iter_bytes():
                    if cancelled.is_set():return
                    if not self.authorized():raise PermissionError('call authorization lost')
                    yield from parser.feed(chunk)
        except httpx.HTTPError:raise AccountError('call_events_unavailable') from None
        finally:
            self._end_events(cancelled)
