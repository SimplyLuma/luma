"""Calls metadata reconciliation: lease refresh is not a new channel identity."""
import time
from .call_relay_api import CallRelaySession,PURPOSES
from .relay_control import identifier


class CallRelayDirectory:
    def __init__(self,device_id,pair_ids,origin,*,now=time.time):
        self.device_id=identifier(device_id);self.pair_ids=frozenset(map(identifier,pair_ids))
        self.origin,self.now=origin,now
        self.sessions={};self.cursor=None

    def apply(self,event,cursor,data):
        if not isinstance(cursor,str) or not 0<len(cursor)<=128 or any(ord(c)<33 or ord(c)>126 for c in cursor):
            raise ValueError('invalid call cursor')
        before=self.sessions
        after={key:s for key,s in before.items() if s.expires>self.now()}
        def add(raw):
            s=CallRelaySession.parse(raw,self.origin)
            if s.pair_id in self.pair_ids and s.expires>self.now():after[(s.pair_id,s.purpose)]=s
        if event=='snapshot':
            if (not isinstance(data,dict) or set(data)!={'device_id','sessions','reset'}
                    or data['device_id']!=self.device_id or type(data['reset']) is not bool
                    or not isinstance(data['sessions'],list) or len(data['sessions'])>100):
                raise ValueError('invalid call snapshot')
            after={};seen=set()
            for raw in data['sessions']:
                s=CallRelaySession.parse(raw,self.origin);key=(s.pair_id,s.purpose)
                if key in seen:raise ValueError('duplicate call purpose')
                seen.add(key);add(raw)
        elif event=='session_offered':add(data)
        elif event=='session_closed':
            if (not isinstance(data,dict) or set(data)!={'pair_id','session_id','generation','reason','purpose','attempt_id'}
                    or data['purpose'] not in PURPOSES or identifier(data['session_id'])!=data['generation']
                    or not isinstance(data['reason'],str) or not 0<len(data['reason'])<=64):
                raise ValueError('invalid call closure')
            key=(identifier(data['pair_id']),data['purpose']);attempt=identifier(data['attempt_id'])
            current=after.get(key)
            if current and (current.session_id,current.attempt_id)==(data['session_id'],attempt):after.pop(key)
        elif event=='revoked':
            if not isinstance(data,dict) or set(data)!={'pair_id','reason'}:raise ValueError('invalid call revocation')
            pair=identifier(data['pair_id']);after={key:s for key,s in after.items() if s.pair_id!=pair}
        else:raise ValueError('invalid call event')
        obsolete=[s for key,s in before.items() if key not in after or
            (s.session_id,s.attempt_id,s.url)!=(after[key].session_id,after[key].attempt_id,after[key].url)]
        self.sessions,self.cursor=after,cursor
        return obsolete
