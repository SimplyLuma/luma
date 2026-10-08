"""Typed ephemeral call RPC and native change hints on a paired channel.

Only one command may be outstanding. Disconnect means unconfirmed; neither
this owner nor a durable queue retries it. Incoming commands use CallReceiver's
existing grant transaction and native single-use call/dial capabilities.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from .call_channel import PinnedJSONChannel
from .call_receiver import CallReceiver,CallRequestDenied
from .policy import Journal,IDENTIFIER,Denied


class CallControlChannel:
    def __init__(self,stream,directory,peer,*,account,epoch,session,incoming,
                 authorized,dispatch,changed,failed,adapter=None,channel_factory=None,audio_changed=lambda generations:None):
        if (not all(isinstance(v,str) and IDENTIFIER.fullmatch(v) for v in (epoch,session))
                or not isinstance(account,str) or not 0<len(account)<=512 or type(incoming) is not bool
                or incoming!=(adapter is not None)):
            raise ValueError('explicit call control role required')
        self.directory,self.peer=Path(directory),peer
        self.binding={'account':account,'epoch':epoch,'session':session}
        self.incoming,self.authorized,self.dispatch=incoming,authorized,dispatch
        self.changed,self.failed,self.adapter=changed,failed,adapter
        self.audio_changed=audio_changed
        self.closed=False;self.pending=None;self.server_busy=False
        self.lock=threading.RLock();self.sequence=0;self.received=0
        factory=channel_factory or (lambda **kw:PinnedJSONChannel(stream,peer,**kw))
        self.channel=factory(check=self._check,receive=self._receive,failed=self._lost)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='connect-native-call-rpc')

    def _check(self):
        if self.closed or not self.authorized():raise PermissionError('call sharing unavailable')
        journal=Journal(self.directory/'continuity.db')
        try:
            row=journal.db.execute('SELECT account,epoch,grants,outgoing_grants,revoked FROM peers WHERE fingerprint=?',(self.peer,)).fetchone()
            if (not journal.enabled() or not row or row[4] or row[0]!=self.binding['account']
                    or row[1]!=self.binding['epoch'] or 'calls.read' not in json.loads(row[2] if self.incoming else row[3])):
                raise PermissionError('call read consent required')
        finally:journal.close()

    def start(self):self.channel.start()

    def _send(self,kind,body):
        with self.lock:
            self._check()
            self.channel.send(dict(self.binding,sequence=self.sequence,kind=kind,body=body))
            self.sequence+=1

    def request(self,request):
        if self.incoming:raise PermissionError('requester required')
        # Typed native receiver performs full envelope validation. Reject any
        # other capability before it can cross the requester boundary.
        if not isinstance(request,dict) or request.get('capability') not in {'calls.read','calls.control','calls.audio'}:
            raise ValueError('typed call request required')
        from .local import ScopedExchange
        admission=ScopedExchange(self.directory,self.peer,self.binding['epoch'],authorized=self.authorized)
        with admission._admit(request):pass
        waiting={'id':request.get('id'),'event':threading.Event(),'result':None}
        with self.lock:
            self._check()
            if self.pending is not None:raise PermissionError('call request already pending')
            self.pending=waiting
        try:
            self._send('request',request)
            if not waiting['event'].wait(10):self._lost();raise TimeoutError('call request unconfirmed')
            with admission._admit(request):pass
            if waiting['result'] is None:raise PermissionError('call request unconfirmed')
            if waiting['result']=={'state':'denied','result':None}:raise Denied('call request denied')
            return waiting['result']
        finally:
            with self.lock:
                if self.pending is waiting:self.pending=None

    def hint(self,incoming,active_generations=()):
        if not self.incoming or type(incoming) is not bool:raise ValueError('native call hint required')
        self._send('changed',{'incoming':incoming,'active_generations':list(active_generations)})

    def native_changed(self):
        # Native observation shares the control executor, so it cannot race
        # snapshot generation updates or consume a caller's one-shot token.
        def observe():
            try:
                self._check()
                generations=self.adapter.active_audio_generations()
                self.audio_changed(generations)
                self.hint(self.adapter.incoming(),generations)
            except Exception:self._lost()
        self.executor.submit(observe)

    def _receive(self,value):
        self._check()
        if (not isinstance(value,dict) or set(value)!=set(self.binding)|{'sequence','kind','body'}
                or any(value[k]!=v for k,v in self.binding.items())
                or type(value['sequence']) is not int or value['sequence']!=self.received
                or self.received>=2**53 or not isinstance(value['body'],dict)):
            raise ValueError('invalid call control envelope')
        self.received+=1;body=value['body'];kind=value['kind']
        if self.incoming:
            if kind!='request' or not isinstance(body.get('id'),str) or not IDENTIFIER.fullmatch(body['id']):
                raise ValueError('correlated call request required')
            with self.lock:
                if self.server_busy:raise ValueError('call request already pending')
                self.server_busy=True
            def handle():
                journal=Journal(self.directory/'continuity.db')
                try:
                    receiver=CallReceiver(journal,self.adapter,authorized=lambda:not self.closed and self.authorized())
                    try:result=receiver.handle(self.peer,body)
                    except CallRequestDenied:result={'state':'denied','result':None}
                    self._send('response',{'id':body['id'],'result':result})
                except Exception:self._lost()
                finally:
                    journal.close()
                    with self.lock:self.server_busy=False
            self.executor.submit(handle)
        elif kind=='response':
            with self.lock:
                pending=self.pending
                if (set(body)!={'id','result'} or not pending or body['id']!=pending['id']
                        or not isinstance(body['result'],dict)
                        or not (body['result']=={'state':'denied','result':None} or
                            set(body['result'])=={'state','result'} and body['result']['state']=='complete' and isinstance(body['result']['result'],dict))):
                    raise ValueError('unexpected call response')
                pending['result']=body['result'];pending['event'].set()
        elif kind=='changed':
            if (set(body)!={'incoming','active_generations'} or type(body['incoming']) is not bool
                    or not isinstance(body['active_generations'],list) or len(body['active_generations'])>8
                    or any(not isinstance(v,str) or not IDENTIFIER.fullmatch(v) for v in body['active_generations'])):
                raise ValueError('invalid call hint')
            def publish():
                try:self._check();self.audio_changed(body['active_generations']);self.changed(body['incoming'])
                except Exception:self._lost()
                return False
            self.dispatch(publish)
        else:raise ValueError('invalid call control frame')

    def _lost(self):
        if self.closed:return
        self.close();self.dispatch(lambda:(self.failed(),False)[1])

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True;self.channel.close()
            if self.pending:self.pending['event'].set()
        if self.adapter and hasattr(self.adapter,'invalidate'):self.adapter.invalidate()
        self.executor.shutdown(wait=False,cancel_futures=True)
