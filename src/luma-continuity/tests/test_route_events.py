"""Interface hints must not repeatedly tear down a reachable encrypted route."""
from types import SimpleNamespace
import unittest
from luma_continuity.daemon import Daemon


class RouteEventTests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.probes=[]
        class Cancel:
            def __init__(self):self.cancelled=False
            def cancel(self):self.cancelled=True
        self.d=Daemon.__new__(Daemon);d=self.d
        d.closed=False;d.awake=True;d.online=True;d.route_check=None;d.environment_generation=0
        d.config=SimpleNamespace(api_origin='https://api.example')
        d.Gio=SimpleNamespace(Cancellable=Cancel,NetworkAddress=SimpleNamespace(parse_uri=lambda *a:a))
        d.GLib=SimpleNamespace(Error=RuntimeError)
        d.recovery=SimpleNamespace(environment=lambda **kw:self.events.append(kw))
        def pause():
            d.environment_generation+=1
            if d.route_check:d.route_check.cancel();d.route_check=None
            self.events.append('pause')
        d._pause_connection=pause
        self.monitor=SimpleNamespace(can_reach_async=lambda endpoint,cancel,callback:self.probes.append((cancel,callback)),
            can_reach_finish=lambda result:result)

    def test_reachable_hint_storm_coalesces_without_pause_or_refresh(self):
        for _ in range(100):self.d._network_changed(self.monitor,True)
        self.assertEqual(len(self.probes),1);self.assertEqual(self.events,[])
        self.probes[0][1](self.monitor,True)
        self.assertTrue(self.d.online);self.assertEqual(self.events,[])
        self.d._network_changed(self.monitor,False)  # no WAN, direct route remains
        self.probes[-1][1](self.monitor,True)
        self.assertEqual(self.events,[])

    def test_actual_route_loss_fences_once_then_recovery_once(self):
        self.d._network_changed(self.monitor,True);self.probes[-1][1](self.monitor,False)
        self.assertFalse(self.d.online);self.assertEqual(self.events,['pause',{'online':False}])
        self.d._network_changed(self.monitor,False);self.probes[-1][1](self.monitor,False)
        self.assertEqual(len(self.events),2)
        self.d._network_changed(self.monitor,True);self.probes[-1][1](self.monitor,True)
        self.assertTrue(self.d.online);self.assertEqual(self.events[-1],{'online':True})

    def test_sleep_or_close_cannot_publish_late_probe(self):
        self.d._network_changed(self.monitor,True);old=self.probes[-1]
        self.d.awake=False;self.d._pause_connection();old[1](self.monitor,True)
        self.assertTrue(old[0].cancelled);self.assertEqual(self.events,['pause'])
        self.d._network_changed(self.monitor,True);self.assertEqual(len(self.probes),1)
        self.d.awake=True;self.d._network_changed(self.monitor,True)
        self.d.closed=True;self.probes[-1][1](self.monitor,False)
        self.assertEqual(self.events,['pause'])
