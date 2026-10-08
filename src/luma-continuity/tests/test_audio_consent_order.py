"""Delayed native consent must not prevent the pinned signaling handshake."""
import unittest
from unittest.mock import Mock,patch
from luma_continuity.call_audio_session import CallAudioSession

class ConsentOrderTests(unittest.TestCase):
    def setUp(self):
        self.allowed=True;self.events=[]
        self.signals=Mock();self.engine=Mock();self.lease=Mock()
        self.signals.start.side_effect=lambda:self.events.append('signaling')
        self.lease.start.side_effect=lambda *_:self.events.append('consent')
        self.engine.start.side_effect=lambda **_:self.events.append('media')
        self.patches=[patch('luma_continuity.call_audio_session.'+name,return_value=value) for name,value in
            [('PairedAudioSignals',self.signals),('WebRTCAudio',self.engine)]]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.audio=CallAudioSession(None,'.','peer',account='account',epoch='epoch',call='call',session='session',
            incoming=True,current_call=lambda:'call',consented=lambda:self.allowed,dispatch=lambda f:f(),
            failed=Mock(),native_current_call=lambda:'native',lease_factory=lambda **_:self.lease)
        self.addCleanup(self.audio.close)

    def test_signaling_connects_while_native_consent_is_pending(self):
        self.audio.start(offer=False)
        self.assertEqual(self.events,['signaling','consent'])
        self.engine.start.assert_not_called()
        offer={'type':'offer','sdp':'synthetic'}
        self.audio._receive_signal(offer)
        self.engine.receive.assert_not_called()
        self.audio._native_ready(self.lease)
        self.assertEqual(self.events,['signaling','consent','media'])
        self.assertEqual(self.engine.start.call_args.kwargs['endpoint'],('native',self.lease.uplink,self.lease.downlink))
        self.signals.start.assert_called_once();self.engine.receive.assert_called_once_with(offer)
        self.assertEqual(self.audio.pending_signals,[])

    def test_revocation_during_consent_never_starts_media(self):
        self.audio.start(offer=False);self.audio._receive_signal({'type':'offer'})
        self.allowed=False;self.audio._native_ready(self.lease)
        self.engine.start.assert_not_called()
        self.assertTrue(self.audio.closed);self.assertEqual(self.audio.pending_signals,[])

    def test_denial_discards_pending_signals(self):
        self.audio.start(offer=False);self.audio._receive_signal({'type':'offer'})
        self.audio._lost()
        self.assertTrue(self.audio.closed);self.assertEqual(self.audio.pending_signals,[])
        self.engine.start.assert_not_called()

    def test_pending_signals_are_bounded(self):
        self.audio.start(offer=False)
        for _ in range(34):self.audio._receive_signal({'type':'ice'})
        with self.assertRaises(ValueError):self.audio._receive_signal({'type':'ice'})
        self.engine.start.assert_not_called()

if __name__=='__main__':unittest.main()
