"""The SSE iterator exclusively owns response close, even during cancellation."""
import threading
import unittest
from types import SimpleNamespace
from luma_continuity.relay_api import RelayAPI
from luma_continuity.call_relay_api import CallRelayAPI
from luma_continuity.daemon import wait_event_owners


class EventCancellationTests(unittest.TestCase):
    def exercise(self,kind,before_headers=False):
        blocked=threading.Event();release=threading.Event();closed=[];received=[];errors=[]
        class Response:
            status_code=200;headers={'content-type':'text/event-stream'}
            def __enter__(self):
                if before_headers:blocked.set();assert release.wait(2)
                return self
            def __exit__(self,*args):closed.append(threading.get_ident())
            def iter_bytes(self):
                blocked.set();assert release.wait(2)
                yield b'event: snapshot\nid: one\ndata: {}\n\n'
        api=SimpleNamespace(config=SimpleNamespace(api_origin='https://api.example'),
                            client=SimpleNamespace(stream=lambda *a,**kw:Response()))
        relay=kind(api,'11111111-1111-4111-8111-111111111111',bearer=lambda:'synthetic',authorized=lambda:True)
        def consume():
            try:received.extend(relay.events())
            except Exception as error:errors.append(type(error).__name__)
        owner=threading.Thread(target=consume);owner.start()
        self.assertTrue(blocked.wait(2))
        relay.cancel_events()
        self.assertEqual(closed,[]);self.assertTrue(owner.is_alive())
        release.set();owner.join(2)
        self.assertFalse(owner.is_alive());self.assertEqual(errors,[])
        self.assertEqual(received,[]);self.assertEqual(closed,[owner.ident])

    def test_blocked_calls_read(self):self.exercise(CallRelayAPI)
    def test_blocked_messages_read(self):self.exercise(RelayAPI)
    def test_calls_pending_headers(self):self.exercise(CallRelayAPI,True)
    def test_messages_pending_headers(self):self.exercise(RelayAPI,True)

    def test_old_iterator_cleanup_cannot_clear_new_cancel(self):
        relay=RelayAPI(SimpleNamespace(),'11111111-1111-4111-8111-111111111111',bearer=lambda:'synthetic',authorized=lambda:True)
        old=relay._begin_events();new=relay._begin_events()
        self.assertTrue(old.is_set());relay._end_events(old);relay.cancel_events()
        self.assertTrue(new.is_set())

    def test_shutdown_is_bounded_and_does_not_claim_stalled_owner_closed(self):
        release=threading.Event();owner=threading.Thread(target=lambda:release.wait(2))
        owner.start()
        daemon=SimpleNamespace(relay_runtime=SimpleNamespace(thread=owner),calls=SimpleNamespace(relay=None))
        try:self.assertFalse(wait_event_owners([daemon],timeout=.01))
        finally:release.set();owner.join(2)
        self.assertTrue(wait_event_owners([daemon],timeout=.01))
