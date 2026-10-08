#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real ordered audio continuation and recoverable failure boundaries."""
from array import array
import errno
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prairie_apps import audio_backend as audio


def tone(path, frequency):
    Gst = audio.gst()
    pipeline = Gst.parse_launch(f'audiotestsrc wave=sine freq={frequency} num-buffers=24 samplesperbuffer=480 '
        '! audio/x-raw,rate=48000,channels=1 ! audioconvert ! opusenc ! oggmux ! filesink name=output')
    pipeline.get_by_name('output').set_property('location', str(path))
    try:
        pipeline.set_state(Gst.State.PLAYING)
        message = pipeline.get_bus().timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if message is None or message.type != Gst.MessageType.EOS:
            raise AssertionError(message.parse_error()[0].message if message else 'Synthetic audio did not finish')
    finally:
        pipeline.set_state(Gst.State.NULL)


def pcm(path):
    Gst = audio.gst()
    pipeline = Gst.parse_launch('uridecodebin name=source ! audioconvert ! audioresample '
        '! audio/x-raw,format=F32LE,rate=8000,channels=1 ! appsink name=output sync=false')
    pipeline.get_by_name('source').set_property('uri', path.as_uri())
    sink = pipeline.get_by_name('output')
    data = array('f')
    try:
        pipeline.set_state(Gst.State.PLAYING)
        for _ in range(100):
            sample = sink.emit('try-pull-sample', Gst.SECOND // 10)
            if sample:
                buffer = sample.get_buffer()
                data.frombytes(buffer.extract_dup(0, buffer.get_size()))
            elif sink.get_property('eos'):
                return data
            error = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if error:
                raise AssertionError(error.parse_error()[0].message)
        raise AssertionError('Synthetic combined audio did not decode completely')
    finally:
        pipeline.set_state(Gst.State.NULL)


def energy(samples, frequency):
    step = 2 * math.pi * frequency / 8000
    return abs(sum(value * complex(math.cos(i * step), math.sin(i * step)) for i, value in enumerate(samples)))


class ContinuationTests(unittest.TestCase):
    def test_join_keeps_both_parts_and_reloads_ordered_sound_and_transcript(self):
        with tempfile.TemporaryDirectory(prefix='memo continuation ') as directory:
            root = Path(directory)
            first, second = root / 'Meeting first.ogg', root / 'Meeting second.ogg'
            tone(first, 440)
            tone(second, 880)
            before = [p.read_bytes() for p in (first, second)]
            for path, text in ((first, 'First part'), (second, 'Next part')):
                audio.atomic_json(audio.sidecar(path), {'version': 1, 'source': 'Synthetic test',
                    'transcript_state': 'complete', 'transcript': [{'start': 0, 'end': .1, 'text': text}]})
            result = audio.continue_recording(first, second, root)
            self.assertEqual([p.read_bytes() for p in (first, second)], before)
            decoded = pcm(result)
            self.assertAlmostEqual(len(decoded) / 8000, .48, delta=.025)
            self.assertGreater(energy(decoded[160:1600], 440), energy(decoded[160:1600], 880) * 5)
            self.assertGreater(energy(decoded[-1600:-160], 880), energy(decoded[-1600:-160], 440) * 5)
            record = next(record for record in audio.list_recordings(root) if record.path == result)
            self.assertAlmostEqual(record.duration, .48, delta=.025)
            self.assertEqual([cue['text'] for cue in record.transcript], ['First part', 'Next part'])
            self.assertAlmostEqual(record.transcript[1]['start'], .24, delta=.025)
            self.assertEqual(audio.read_json(audio.sidecar(result))['parts'], [first.name, second.name])

    def test_failed_publication_keeps_each_source_and_removes_incomplete_join(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'First.ogg', root / 'Second.ogg'
            tone(first, 440)
            tone(second, 880)
            before = [p.read_bytes() for p in (first, second)]
            with patch.object(audio, '_publish', side_effect=OSError(errno.ENOSPC, 'No space left')):
                with self.assertRaises(OSError):
                    audio.continue_recording(first, second, root)
            self.assertEqual([p.read_bytes() for p in (first, second)], before)
            self.assertEqual(set(root.glob('*.ogg')), {first, second})
            self.assertFalse(list(root.glob('*.partial')))

    def test_rejects_same_part_and_symlink_without_touching_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'First.ogg'
            source.write_bytes(b'OggS')
            linked = root / 'Link.ogg'
            linked.symlink_to(source)
            for second in (source, linked):
                with self.assertRaises(ValueError):
                    audio.continue_recording(source, second, root)
            self.assertEqual(source.read_bytes(), b'OggS')


if __name__ == '__main__':
    unittest.main(verbosity=2)
