"""Portal permission lifecycle: no fix before grant, no leaks or late callback."""
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/luma-maps'))
from luma_maps.location import LocationFix, LocationRequest


class Portal:
    def __init__(self):
        self.stops = 0
        self.disconnected = []
        self.calls = []
    def connect(self, name, callback):
        self.updated = callback
        return 12
    def disconnect(self, token):
        self.disconnected.append(token)
    def location_monitor_start(self, *args):
        self.calls.append(args)
        self.started = args[-1]
    def location_monitor_start_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result
    def location_monitor_stop(self):
        self.stops += 1
    def fix(self, lat=41.88, lon=-87.63, accuracy=20):
        self.updated(self, lat, lon, 0, accuracy, 0, 0, '', 1234, 0)
    def finish(self, result):
        self.started(self, result)


class Loop:
    def __init__(self):
        self.timers = {}
        self.next = 0
    def timeout_add_seconds(self, seconds, callback):
        self.next += 1
        self.timers[self.next] = (seconds, callback)
        return self.next
    def source_remove(self, token):
        self.timers.pop(token)


class MapsLocationTests(unittest.TestCase):
    def setUp(self):
        self.portal, self.loop = Portal(), Loop()
        self.fixes, self.errors = [], []
        self.cancelled = False
        def cancel():
            self.cancelled = True
        self.cancel = SimpleNamespace(cancel=cancel)
        self.parent = object()
        self.request = LocationRequest(object(), self.fixes.append, self.errors.append,
            backend=lambda window: (self.portal, self.parent, self.cancel, self.loop, 'exact', 'none'))

    def test_native_parent_and_permission_confirmed_one_shot(self):
        self.request.start()
        self.request.start()
        self.assertEqual(len(self.portal.calls), 1)
        self.assertEqual(self.portal.calls[0][:5], (self.parent, 0, 0, 'exact', 'none'))
        self.assertFalse(self.loop.timers)  # Never timeout a permission decision.
        self.portal.fix()
        self.assertFalse(self.fixes)
        self.portal.finish(True)
        self.assertEqual(len(self.fixes), 1)
        self.assertFalse(self.request.active)
        self.assertEqual(self.portal.disconnected, [12])
        self.assertTrue(self.cancelled)
        self.portal.fix()
        self.assertEqual(len(self.fixes), 1)

    def test_denial_drops_early_fix_and_closes_monitor(self):
        self.request.start()
        self.portal.fix()
        self.portal.finish(False)
        self.assertFalse(self.fixes)
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.loop.timers)
        self.assertEqual(self.portal.stops, 1)

    def test_invalid_fix_never_moves_map_timeout_only_after_grant(self):
        self.request.start()
        self.portal.finish(True)
        self.portal.fix(lat=float('nan'))
        self.portal.fix(lon=181)
        self.portal.fix(accuracy=-1)
        self.assertFalse(self.fixes)
        seconds, callback = self.loop.timers[1]
        self.assertEqual(seconds, 30)
        self.assertFalse(callback())
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.request.active)

    def test_cancel_then_late_grant_has_no_effect(self):
        self.request.start()
        self.request.close()
        self.portal.finish(True)
        self.portal.fix()
        self.assertFalse(self.fixes)
        self.assertFalse(self.errors)
        self.assertFalse(self.loop.timers)

    def test_valid_post_grant_fix_removes_timeout(self):
        self.request.start()
        self.portal.finish(True)
        self.assertTrue(self.loop.timers)
        self.portal.fix()
        self.assertFalse(self.loop.timers)
        self.assertEqual(self.fixes[0], LocationFix(41.88, -87.63, 20))

    def test_provider_failure_keeps_manual_browsing_available(self):
        def unavailable(window):
            raise RuntimeError('portal is absent')
        request = LocationRequest(object(), self.fixes.append, self.errors.append, unavailable)
        request.start()
        self.assertFalse(request.active)
        self.assertIn('search and browse', self.errors[0])

    def test_city_precision_does_not_claim_street_zoom(self):
        self.assertLess(LocationFix(0, 0, 20000).zoom, LocationFix(0, 0, 10).zoom)
        self.assertIsNone(LocationFix.valid(100, 0, 10))
        self.assertIsNone(LocationFix.valid(0, float('inf'), 10))


if __name__ == '__main__':
    unittest.main()
