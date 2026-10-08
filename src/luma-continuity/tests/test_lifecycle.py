import unittest
from luma_continuity.lifecycle import Recovery


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.timers={};self.next=0;self.runs=0
        def later(delay,callback):
            self.next+=1;self.timers[self.next]=(delay,callback);return self.next
        def run():self.runs+=1;return True
        self.owner=Recovery(run,later=later,cancel=lambda key:self.timers.pop(key,None),now=lambda:100,monotonic=lambda:100,jitter=lambda *_:1)
    def fire(self):
        key=next(iter(self.timers));delay,callback=self.timers.pop(key);callback();return delay
    def test_no_retries_or_expiry_timers_during_sleep_and_one_resume(self):
        self.owner.request();self.fire()
        self.owner.completed({'account_status':'signed_in','service_status':'ready','valid_until':115})
        self.assertEqual(len(self.timers),1)
        self.owner.environment(awake=False);self.assertFalse(self.timers)
        self.owner.request();self.assertFalse(self.timers)
        self.owner.environment(awake=True);self.owner.environment(awake=True)
        self.assertEqual(len(self.timers),1);self.fire();self.assertEqual(self.runs,2)
    def test_backoff_is_bounded_and_offline_cancels_it(self):
        self.owner.request();self.fire();delays=[]
        for _ in range(5):
            self.owner.completed({'account_status':'signed_in','service_status':'unavailable','stale':True})
            delays.append(self.fire())
        self.owner.completed({'account_status':'signed_in','service_status':'unavailable','stale':True})
        self.assertEqual(delays,[2,4,8,16,32]);self.assertEqual(self.fire(),60)
        # A server recovers without any network-change event.
        self.owner.completed({'account_status':'signed_in','service_status':'ready','valid_until':115})
        self.assertEqual(self.fire(),12)
        self.owner.environment(online=False)
        self.owner.completed({'account_status':'signed_in','service_status':'unavailable','stale':True})
        self.owner.request();self.assertFalse(self.timers)
        self.owner.environment(online=True);self.assertEqual(len(self.timers),1)
    def test_lock_requires_event_and_close_suppresses_late_results(self):
        self.owner.request();self.fire();self.owner.completed({'account_status':'locked'})
        self.assertFalse(self.timers)
        self.owner.request();self.assertEqual(len(self.timers),1)
        self.owner.close();self.owner.completed({'account_status':'signed_in','valid_until':115,'service_status':'ready'})
        self.assertFalse(self.timers)
    def test_busy_events_coalesce_to_one_reconciliation(self):
        self.owner.request();self.fire()
        for _ in range(10):self.owner.request()
        self.assertFalse(self.timers)
        self.owner.completed({'account_status':'signed_in','service_status':'ready','valid_until':115})
        self.assertEqual(len(self.timers),1);self.fire();self.assertEqual(self.runs,2)

    def test_clock_rollback_does_not_extend_refresh_deadline(self):
        self.owner.now=lambda:-1000
        self.owner.completed({'account_status':'signed_in','service_status':'ready',
            'valid_until':115,'lease_deadline_monotonic':115})
        self.assertEqual(self.fire(),12)
