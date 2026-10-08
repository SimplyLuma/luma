#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real saved Opus samples versus the production playback filter, without devices.

This is a signal/engine boundary check, not proof of a physical microphone or
speaker. A deliberately injected click must fail the same continuity detector.
"""
from array import array
import math
from pathlib import Path
import tempfile
import time
import unittest
from prairie_apps import audio_backend as audio
from gi.repository import GLib


def decode(path):
    Gst = audio.gst()
    pipeline = Gst.parse_launch('uridecodebin name=input ! audioconvert ! audioresample '
        '! audio/x-raw,format=F32LE,rate=48000,channels=1 ! appsink name=output sync=false')
    pipeline.get_by_name('input').set_property('uri', path.as_uri())
    sink = pipeline.get_by_name('output')
    result = array('f')
    try:
        pipeline.set_state(Gst.State.PLAYING)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            sample = sink.emit('try-pull-sample', Gst.SECOND // 100)
            if sample:
                buffer = sample.get_buffer()
                result.frombytes(buffer.extract_dup(0, buffer.get_size()))
            elif sink.get_property('eos'):
                return result
            error = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if error:
                raise AssertionError(error.parse_error()[0].message)
        raise AssertionError('Saved audio did not decode to EOS')
    finally:
        pipeline.set_state(Gst.State.NULL)


def continuous_sine(samples):
    # Trim codec start/end, then detect a broadband impulse using curvature.
    data = samples[4800:-4800]
    assert len(data) > 12000, 'Insufficient active PCM for the continuity check'
    rms = math.sqrt(sum(v*v for v in data) / len(data))
    assert .45 < rms < .65, f'Silent or incorrect test signal: {rms}'
    curvature = max(abs(data[i] - 2 * data[i-1] + data[i-2]) for i in range(2, len(data)))
    assert curvature < .012, f'Unexpected impulse in playback PCM: {curvature}'
    return curvature


class SavedAudioTests(unittest.TestCase):
    def test_saved_pcm_and_production_playback_preserve_signal(self):
        Gst = audio.gst()
        with tempfile.TemporaryDirectory(prefix='memos-signal-') as directory:
            session = audio.start_recording(directory, source='audiotestsrc', source_label='Private test sine')
            time.sleep(1.3)
            path = session.stop()
            encoded = path.read_bytes()
            reference = decode(path)
            reference_curvature = continuous_sine(reference)
            # Prove the detector goes red for the artifact it is meant to catch.
            clicked = array('f', reference)
            clicked[len(clicked)//2] = -clicked[len(clicked)//2] + .6
            with self.assertRaisesRegex(AssertionError, 'Unexpected impulse'):
                continuous_sine(clicked)
            for rate in (1., 1.5):
                errors, ended = [], []
                player = audio.PlaybackSession(path, sink='appsink', on_error=errors.append,
                                               on_end=lambda: ended.append(True))
                sink = player.pipeline.get_property('audio-sink')
                sink.set_property('caps', Gst.Caps.from_string('audio/x-raw,format=F32LE,rate=48000,channels=1'))
                result = array('f')
                try:
                    player.set_rate(rate)
                    player.play()
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline and not ended:
                        sample = sink.emit('try-pull-sample', Gst.SECOND//100)
                        if sample:
                            buffer = sample.get_buffer()
                            result.frombytes(buffer.extract_dup(0, buffer.get_size()))
                        while GLib.MainContext.default().pending():
                            GLib.MainContext.default().iteration(False)
                    self.assertTrue(ended, 'Production playback never reached EOS')
                    self.assertFalse(errors)
                    curvature = continuous_sine(result)
                    self.assertAlmostEqual(len(result) * rate, len(reference), delta=1600)
                    if rate == 1.:
                        self.assertAlmostEqual(curvature, reference_curvature, delta=.001)
                    print(f'PASS saved PCM versus production playback rate={rate}: samples={len(result)}, curvature={curvature:.6f}')
                finally:
                    player.stop()
            self.assertEqual(path.read_bytes(), encoded)


if __name__ == '__main__':
    unittest.main(verbosity=2)
