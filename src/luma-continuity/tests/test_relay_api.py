import json
import unittest
from unittest.mock import patch
import httpx
from luma_continuity.account import AccountAPI, AccountConfig, AccountError
from luma_continuity.relay_api import RelayAPI
from luma_continuity.relay_control import RelaySession
from test_relay_control import session, DEVICE, PAIR, FIRST, ORIGIN


class RelayAPITests(unittest.TestCase):
    def setUp(self):
        self.requests=[];self.allowed=True
        self.response=httpx.Response(200,json=session())
        def handle(request):self.requests.append(request);return self.response
        client=httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=False,trust_env=False)
        self.addCleanup(client.close)
        api=AccountAPI(AccountConfig('https://identity.example.test/realms/luma',ORIGIN,'native'),client)
        self.api=RelayAPI(api,DEVICE,bearer=lambda:'synthetic-token',authorized=lambda:self.allowed,now=lambda:1000)

    def test_ensure_uses_exact_body_and_no_token_url(self):
        result=self.api.ensure(PAIR)
        self.assertEqual(result.session_id,FIRST)
        request=self.requests[-1]
        self.assertEqual(str(request.url),ORIGIN+'/v1/relay-sessions')
        self.assertEqual(json.loads(request.content),dict(device_id=DEVICE,pair_id=PAIR))
        self.assertEqual(request.headers['authorization'],'Bearer synthetic-token')

    def test_cross_origin_ticket_never_opens_socket(self):
        data=session();data['url']='wss://attacker.test/v1/relay/'+FIRST
        self.response=httpx.Response(200,json=data)
        with self.assertRaises(ValueError):self.api.ensure(PAIR)
        self.allowed=False
        with self.assertRaises(PermissionError):self.api.ensure(PAIR)
        self.assertEqual(len(self.requests),1)

    def test_ticket_is_bound_one_use_and_ack_does_not_replay(self):
        current=RelaySession.parse(session(),ORIGIN)
        ticket=dict(ticket='synthetic-ticket',expires=1060,session_id=FIRST,url=current.url,header='X-Luma-Relay-Ticket',one_use=True)
        self.response=httpx.Response(200,json=ticket)
        with patch.object(self.api,'connector',side_effect=OSError('private exception')) as connect:
            with self.assertRaises(OSError):self.api.open(current)
            self.assertEqual(connect.call_count,1);self.assertEqual(len(self.requests),1)
        ticket['one_use']=False;self.response=httpx.Response(200,json=ticket)
        with patch.object(self.api,'connector') as connect:
            with self.assertRaises(PermissionError):self.api.open(current)
            connect.assert_not_called()

    def test_rate_limit_exposes_bounded_backoff_without_retry(self):
        self.response=httpx.Response(429,headers={'Retry-After':'120'})
        with self.assertRaises(AccountError) as caught:self.api.ensure(PAIR)
        self.assertEqual(caught.exception.retry_after,120);self.assertEqual(len(self.requests),1)

    def test_event_headers_and_incremental_decode(self):
        body=': keepalive\n\nevent: snapshot\nid: cursor\ndata: '+json.dumps(dict(device_id=DEVICE,sessions=[],reset=True))+'\n\n'
        self.response=httpx.Response(200,headers={'Content-Type':'text/event-stream'},content=body)
        events=list(self.api.events('previous'))
        self.assertEqual(len(events),1)
        self.assertEqual(self.requests[-1].headers['Last-Event-ID'],'previous')
        self.assertFalse(self.requests[-1].url.query)


if __name__=='__main__':unittest.main()
