from pathlib import Path
from types import SimpleNamespace
import unittest
from luma_continuity.call_audio_owner import CallAudioOwner


class AudioOwnerTests(unittest.TestCase):
    def setUp(self):
        self.allowed=True;self.now=1000;self.jobs=[];self.timers=[]
        self.binding=SimpleNamespace(pair_id='11111111-1111-4111-8111-111111111111')
        self.attempt='22222222-2222-4222-8222-222222222222'
        self.owner=CallAudioOwner(authorized=lambda _:self.allowed,dispatch=self.jobs.append,
            later=lambda delay,cb:self.timers.append(cb),cancel=lambda _:None,now=lambda:self.now)
        self.addCleanup(self.owner.close)
        self.session=SimpleNamespace(pair_id=self.binding.pair_id,attempt_id=self.attempt)

    def test_offer_requires_exact_explicit_attempt_and_expires(self):
        self.assertFalse(self.owner.allowed(self.session))
        self.owner.prepare(self.binding,'a'*32,self.attempt,native_call='synthetic')
        self.assertTrue(self.owner.allowed(self.session))
        self.assertFalse(self.owner.allowed(SimpleNamespace(pair_id=self.binding.pair_id,attempt_id=self.binding.pair_id)))
        self.now=1121;self.assertFalse(self.owner.allowed(self.session))

    def test_no_consent_or_changed_call_never_admits_audio(self):
        self.allowed=False
        with self.assertRaises(PermissionError):self.owner.prepare(self.binding,'a'*32,self.attempt)
        self.allowed=True;self.owner.prepare(self.binding,'a'*32,self.attempt)
        self.owner.invalidate(self.binding.pair_id)
        self.assertFalse(self.owner.allowed(self.session));self.assertEqual(self.owner.prepared,{})

    def test_no_overlapping_or_replayed_preparations(self):
        self.owner.prepare(self.binding,'a'*32,self.attempt)
        with self.assertRaises(PermissionError):self.owner.prepare(self.binding,'a'*32,self.attempt)

    def test_duplicate_active_hints_preserve_audio_but_end_or_replacement_closes(self):
        self.owner.prepare(self.binding,'a'*32,self.attempt)
        self.owner.reconcile(self.binding.pair_id,['a'*32])
        self.owner.reconcile(self.binding.pair_id,['a'*32])
        self.assertTrue(self.owner.allowed(self.session))
        self.owner.reconcile(self.binding.pair_id,['b'*32])
        self.assertFalse(self.owner.allowed(self.session))
        self.owner.prepare(self.binding,'a'*32,self.attempt)
        self.owner.reconcile(self.binding.pair_id,[])
        self.assertFalse(self.owner.allowed(self.session))
