"""Volatile, one-call audio consent bound to a server-minted attempt.

A peer offer alone never opens audio. Native preparation consumes a fresh
native call capability; the desktop starts only from an explicit UI action.
Call events invalidate preparations and active audio without polling the modem.
"""
from dataclasses import dataclass
import threading
import time


@dataclass
class PreparedAudio:
    pair_id:str
    attempt_id:str
    generation:str
    native_call:str|None
    expires:float
    closed:bool=False
    audio:object=None
    timer:object=None


class CallAudioOwner:
    def __init__(self,*,authorized,dispatch,later,cancel,now=time.time):
        self.authorized,self.dispatch,self.later,self.cancel,self.now=authorized,dispatch,later,cancel,now
        self.lock=threading.RLock();self.prepared={};self.closed=False

    def prepare(self,binding,generation,attempt,*,native_call=None):
        from .policy import IDENTIFIER
        from .relay_control import identifier
        if not isinstance(generation,str) or not IDENTIFIER.fullmatch(generation):raise ValueError('current call generation required')
        identifier(attempt)
        if not self.authorized(binding):raise PermissionError('call audio consent required')
        with self.lock:
            if self.closed or self.prepared:raise PermissionError('audio already prepared')
            value=PreparedAudio(binding.pair_id,attempt,generation,native_call,self.now()+120)
            self.prepared[(binding.pair_id,attempt)]=value
        def arm():
            def expired():
                value.timer=None
                with self.lock:
                    current=self.prepared.get((value.pair_id,value.attempt_id))
                if current is value and value.audio is None:self.invalidate(binding.pair_id)
                return False
            if not value.closed:value.timer=self.later(120,expired)
            return False
        self.dispatch(arm)
        return value

    def allowed(self,session):
        with self.lock:
            value=self.prepared.get((session.pair_id,session.attempt_id))
            return bool(not self.closed and value and not value.closed and
                (value.audio is not None or self.now()<value.expires))

    def factory(self,directory,session,binding,allowed,channel_factory,failed,changed=lambda:None):
        from .call_audio_session import CallAudioSession
        with self.lock:
            value=self.prepared.get((session.pair_id,session.attempt_id))
            if not value or not self.allowed(session) or not self.authorized(binding):return None
            def current():return value.generation if not value.closed else None
            audio=CallAudioSession(None,directory,binding.peer,account=binding.account,epoch=binding.epoch,
                call=value.generation,session=session.attempt_id.replace('-',''),incoming=binding.role=='receiver',
                current_call=current,consented=lambda:allowed() and self.authorized(binding) and not value.closed,
                dispatch=self.dispatch,failed=lambda:(self.invalidate(binding.pair_id),failed()),
                native_current_call=(lambda:value.native_call if not value.closed else None) if value.native_call else None,
                channel_factory=channel_factory,changed=changed)
            value.audio=audio
        owner=self
        class Channel:
            def start(self):audio.start(offer=binding.role=='requester')
            def close(self):
                owner.invalidate(value.pair_id);audio.close()
        return Channel()

    def reconcile(self,pair_id,active_generations):
        with self.lock:
            values=[value for value in self.prepared.values() if value.pair_id==pair_id]
        if any(value.generation not in active_generations for value in values):self.invalidate(pair_id)

    def invalidate(self,pair_id=None):
        with self.lock:
            values=[value for key,value in self.prepared.items() if pair_id is None or key[0]==pair_id]
            for value in values:
                value.closed=True;self.prepared.pop((value.pair_id,value.attempt_id),None)
        for value in values:
            if value.timer is not None:
                timer=value.timer;value.timer=None
                self.dispatch(lambda timer=timer:(self.cancel(timer),False)[1])
            if value.audio:self.dispatch(lambda value=value:(value.audio.close(),False)[1])

    def close(self):
        self.closed=True;self.invalidate()
