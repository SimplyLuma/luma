"""Ephemeral call request admission; never durable command queuing or replay."""
import json
import time
from .policy import Denied, IDENTIFIER
from . import transport


class CallRequestDenied(Denied):
    """A correlated request was rejected before native dispatch."""


class CallReceiver:
    def __init__(self,journal,adapter,*,authorized):
        self.journal,self.adapter,self.authorized=journal,adapter,authorized

    def handle(self,peer,request):
        # Serialize admission with local grant/revoke transactions, without
        # storing a command or cached call response in the durable journal.
        self.journal.db.execute('BEGIN IMMEDIATE')
        try:
            result=self._handle(peer,request)
            self.journal.db.execute('COMMIT')
            return result
        finally:
            if self.journal.db.in_transaction:self.journal.db.execute('ROLLBACK')

    def _handle(self,peer,request):
        row=self.journal.db.execute('SELECT epoch,account,grants,revoked FROM peers WHERE fingerprint=?',(peer,)).fetchone()
        if (not row or row[3] or not isinstance(request,dict) or row[0]!=request.get('epoch')
                or row[1]!=request.get('account') or not self.authorized()):
            raise Denied('call authority changed')
        if len(transport.encode(request))>8192:raise CallRequestDenied('call request bound')
        if (not isinstance(request,dict) or set(request)!={'version','account','epoch','id','capability','expires','payload'}
                or type(request['version']) is not int or request['version']!=1
                or request['capability'] not in {'calls.read','calls.control','calls.audio'}
                or not isinstance(request['id'],str) or not IDENTIFIER.fullmatch(request['id'])
                or type(request['expires']) is not int or not time.time()<request['expires']<=time.time()+15
                or not isinstance(request['payload'],dict)):
            raise CallRequestDenied('invalid call operation')
        if request['capability'] not in json.loads(row[2]):raise CallRequestDenied('call capability unavailable')
        try:result=self.adapter(request['capability'],request['payload'])
        except CallRequestDenied:raise Denied('native call result unconfirmed') from None
        if not self.authorized():raise Denied('call authorization lost')
        return {'state':'complete','result':result}
