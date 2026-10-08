import json
import unittest
from luma_continuity.relay_control import RelayDirectory, EventDecoder, RelaySession

DEVICE='11111111-1111-4111-8111-111111111111'
PAIR='22222222-2222-4222-8222-222222222222'
FIRST='33333333-3333-4333-8333-333333333333'
SECOND='44444444-4444-4444-8444-444444444444'
ORIGIN='https://api.example.test'


def session(sid=FIRST):
    return dict(session_id=sid,generation=sid,pair_id=PAIR,expires=1600,
        url=ORIGIN.replace('https:','wss:')+'/v1/relay/'+sid,
        max_frame_bytes=65536,max_total_bytes=10485760,end_to_end_tls_required=True)


class RelayControlTests(unittest.TestCase):
    def setUp(self): self.directory=RelayDirectory(DEVICE,[PAIR],ORIGIN,now=lambda:1000)

    def test_reconnect_snapshot_replaces_orphan_and_stale_close_cannot_close_new(self):
        d=self.directory
        d.apply('session_offered','one',session())
        closed=d.apply('snapshot','two',dict(device_id=DEVICE,sessions=[session(SECOND)],reset=True))
        self.assertEqual([s.session_id for s in closed],[FIRST])
        d.apply('session_closed','three',dict(pair_id=PAIR,session_id=FIRST,generation=FIRST,reason='server_restart'))
        self.assertEqual(d.sessions[PAIR].session_id,SECOND)
        d.apply('revoked','four',dict(pair_id=PAIR,reason='authorization_lost'))
        self.assertEqual(d.sessions,{})

    def test_invalid_snapshot_does_not_advance_cursor_or_partially_replace(self):
        d=self.directory;d.apply('session_offered','one',session())
        bad=session(SECOND);bad['url']='wss://attacker.test/v1/relay/'+SECOND
        with self.assertRaises(ValueError):
            d.apply('snapshot','two',dict(device_id=DEVICE,sessions=[bad],reset=False))
        self.assertEqual(d.cursor,'one');self.assertEqual(d.sessions[PAIR].session_id,FIRST)
        for data in [dict(device_id=FIRST,sessions=[],reset=False),dict(device_id=DEVICE,sessions=[session(),session()],reset=False)]:
            with self.assertRaises(ValueError): d.apply('snapshot','two',data)

    def test_metadata_never_adds_a_local_pair_and_expiry_removes(self):
        d=RelayDirectory(DEVICE,[],ORIGIN,now=lambda:1000)
        d.apply('session_offered','one',session());self.assertEqual(d.sessions,{})
        d=self.directory;d.apply('session_offered','one',session());d.now=lambda:1600
        self.assertEqual(len(d.apply('snapshot','two',dict(device_id=DEVICE,sessions=[session()],reset=False))),1)
        self.assertEqual(d.sessions,{})

    def test_session_rejects_downgrade_or_unbounded_fields(self):
        for field,value in [('end_to_end_tls_required',False),('generation',SECOND),('expires',True),('max_total_bytes',10485761),('url',ORIGIN+'/v1/relay/'+FIRST)]:
            data=session();data[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError): RelaySession.parse(data,ORIGIN)

    def test_incremental_events_and_comments(self):
        body=('event: session_offered\nid: abc\ndata: '+json.dumps(session())+'\n\n').encode()
        parser=EventDecoder();events=[]
        for byte in b': heartbeat\n\n'+body: events.extend(parser.feed(bytes([byte])))
        self.assertEqual(events,[('session_offered','abc',session())])

    def test_event_memory_bounds_duplicate_keys_and_invalid_utf8(self):
        for content in [b'x'*65537,b'event: x\nid: y\ndata: {"a":1,"a":2}\n\n',
                        b'event: x\nid: y\ndata: NaN\n\n',b'event: x\nid: y\ndata: {}\nid: z\n\n',b'\xff\n']:
            with self.subTest(content=content[:40]),self.assertRaises((ValueError,UnicodeError)): EventDecoder().feed(content)


if __name__=='__main__': unittest.main()
