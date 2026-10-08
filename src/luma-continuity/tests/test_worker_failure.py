from concurrent.futures import Future
from types import SimpleNamespace
import unittest
from luma_continuity.daemon import Daemon
from luma_continuity.account import SecretTokens,AccountError


class WorkerFailureTests(unittest.TestCase):
    def test_exception_after_account_work_is_published_and_releases_busy(self):
        d=Daemon.__new__(Daemon);pending=[];published=[]
        d.closed=False;d.busy=False;d.latest={'generation':1};d.environment_generation=4
        d._signal=lambda:None
        d.GLib=SimpleNamespace(idle_add=lambda f,*a:pending.append((f,a)))
        def snapshot():raise RuntimeError('synthetic snapshot failure')
        d.model=SimpleNamespace(snapshot=snapshot)
        def submit(run):
            f=Future()
            try:run()
            except Exception as e:f.set_exception(e)
            else:f.set_result(None)
            return f
        d.worker=SimpleNamespace(submit=submit)
        def publish(state,error,generation):
            d.busy=False;published.append((state,error,generation))
        d._publish=publish
        self.assertTrue(d._schedule(lambda:None));self.assertTrue(d.busy)
        f,args=pending.pop();f(*args)
        self.assertFalse(d.busy);self.assertTrue(published[0][0]['stale'])
        self.assertEqual(published[0][1:],('state_collection_failed',4))

    def test_secret_owner_silence_is_cancelled(self):
        import time
        tokens=SecretTokens.__new__(SecretTokens)
        def unresponsive(cancellable):
            deadline=time.monotonic()+1
            while not cancellable.is_cancelled() and time.monotonic()<deadline:time.sleep(.005)
            raise RuntimeError('synthetic cancelled request')
        started=time.monotonic()
        with self.assertRaises(AccountError) as caught:tokens._secret_call(unresponsive,timeout=.025)
        self.assertEqual(caught.exception.code,'keyring_unavailable')
        self.assertLess(time.monotonic()-started,.5)
