"""Call channel lifetime and paired lease renewal on the daemon's main context.

SSE reconnect is not a grant. Existing channels survive it only while explicit
local authorization and the effective paired lease remain valid. Snapshot
absence, generation change and actual authority loss close immediately.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import time
import logging
from .account import AccountError


@dataclass
class ActiveCallChannel:
    session:object
    channel:object
    deadline:object=None
    renewal:object=None
    renewing:bool=False
    again:bool=False


class CallLeaseOwner:
    def __init__(self,api,directory,*,authorized,factory,dispatch,later,cancel,now=time.time,executor=None):
        self.api,self.directory,self.authorized,self.factory=api,directory,authorized,factory
        self.dispatch,self.later,self.cancel,self.now=dispatch,later,cancel,now
        self.active={};self.closed=False
        self.executor=executor or ThreadPoolExecutor(max_workers=1,thread_name_prefix='connect-call-lease')
        self.owns_executor=executor is None

    def valid(self,session):
        return not self.closed and session.expires>self.now() and self.authorized(session)

    def apply(self,event,cursor,data):
        if self.closed:return
        obsolete=self.directory.apply(event,cursor,data)
        for session in obsolete:self.end(session.session_id)
        for session in self.directory.sessions.values():
            if not self.valid(session):self.end(session.session_id);continue
            active=self.active.get(session.session_id)
            if active:
                active.session=session;self._deadline(active)
            else:
                # Factory may decline an audio offer without a matching
                # explicit per-call preparation; metadata never creates consent.
                active=ActiveCallChannel(session,None)
                self.active[session.session_id]=active
                try:
                    channel=self.factory(session,lambda a=active:self.active.get(a.session.session_id) is a and self.valid(a.session))
                    active.channel=channel
                    if channel is None or not self._channel_valid(session.session_id):
                        self.end(session.session_id);continue
                    channel.start();self._deadline(active);self._schedule_renew(active,60)
                except Exception as error:
                    logging.getLogger(__name__).warning('Calls channel construction failed (%s)',type(error).__name__)
                    self.end(session.session_id)

    def _channel_valid(self,sid):
        active=self.active.get(sid)
        return bool(active and self.valid(active.session))

    def _deadline(self,active):
        if active.deadline is not None:self.cancel(active.deadline)
        def expired():
            active.deadline=None
            if self.active.get(active.session.session_id) is not active:return False
            if not self.valid(active.session):self.end(active.session.session_id)
            else:self._deadline(active)
            return False
        active.deadline=self.later(max(.001,active.session.expires-self.now()),expired)

    def _schedule_renew(self,active,delay):
        if active.renewal is not None:self.cancel(active.renewal)
        def renew():
            active.renewal=None;self.renew(active.session.session_id);return False
        active.renewal=self.later(max(.001,delay),renew)

    def renew(self,sid):
        active=self.active.get(sid)
        if not active:return
        if not self.valid(active.session):self.end(sid);return
        if active.renewing:active.again=True;return
        if active.renewal is not None:self.cancel(active.renewal);active.renewal=None
        active.renewing=True;session=active.session
        def work():
            result=error=None
            try:
                if not self.valid(session):raise PermissionError('call authorization lost')
                result=self.api.renew(session)
            except Exception as failure:error=failure
            def publish():
                if self.closed or self.active.get(sid) is not active:return False
                active.renewing=False
                if not self.valid(active.session):self.end(sid);return False
                if error is not None:
                    if isinstance(error,PermissionError) or isinstance(error,AccountError) and error.status in (401,403,410):
                        self.end(sid);return False
                    delay=getattr(error,'retry_after',None) or 5
                    self._schedule_renew(active,delay)  # Hard deadline remains armed.
                    return False
                if (not self.valid(result) or any(getattr(result,k)!=getattr(session,k)
                        for k in ('session_id','pair_id','purpose','attempt_id','url'))):
                    self.end(sid);return False
                active.session=result
                key=(result.pair_id,result.purpose)
                current=self.directory.sessions.get(key)
                if current and current.session_id==sid:self.directory.sessions[key]=result
                self._deadline(active)
                again=active.again;active.again=False
                self._schedule_renew(active,.001 if again else 60)
                return False
            self.dispatch(publish)
        self.executor.submit(work)

    def account_refreshed(self):
        """Refresh the WSS guard bearer promptly after normal token rotation."""
        for sid in list(self.active):self.renew(sid)

    def authority_changed(self):
        for sid,active in list(self.active.items()):
            if not self.valid(active.session):self.end(sid)

    def events_disconnected(self):
        self.authority_changed()  # No blanket grace and no blanket teardown.

    def end(self,sid):
        active=self.active.pop(sid,None)
        if not active:return
        for timer in (active.deadline,active.renewal):
            if timer is not None:self.cancel(timer)
        if active.channel is not None:active.channel.close()

    def close(self):
        if self.closed:return
        self.closed=True
        for sid in list(self.active):self.end(sid)
        if self.owns_executor:self.executor.shutdown(wait=False,cancel_futures=True)
