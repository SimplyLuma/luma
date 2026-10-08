# SPDX-License-Identifier: Apache-2.0
"""Decode and play a real saved audio file through Viewer's native media control.

Run in an isolated display/audio session; never opens a user's microphone.
"""
import hashlib
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
import wave

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
from gi.repository import Gio, GLib, Gst, Gtk
Gst.init(None)
from luma_viewer.application import ViewerApplication, ViewerWindow


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        if predicate():
            return True
        time.sleep(.01)
    return False


def find(widget, name):
    if widget.get_name() == name:
        return widget
    child = widget.get_first_child()
    while child:
        result = find(child, name)
        if result:
            return result
        child = child.get_next_sibling()


class AudioPreview(unittest.TestCase):
    def test_real_saved_audio_plays_and_stops_without_changing_source(self):
        app = ViewerApplication()
        app.set_application_id('org.projectluma.Viewer.AudioTest')
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        with tempfile.TemporaryDirectory(prefix='viewer-audio-') as directory:
            path = Path(directory) / 'tone.wav'
            with wave.open(str(path), 'wb') as output:
                output.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                output.writeframes(b''.join(struct.pack('<h', round(12000 * math.sin(2 * math.pi * 440 * n / 48000))) for n in range(96000)))
            paths = [path]
            for extension, encoder in (('mp3', 'lamemp3enc ! id3v2mux'), ('flac', 'flacenc')):
                encoded = Path(directory) / ('tone.' + extension)
                pipeline = Gst.parse_launch(f'filesrc location="{path}" ! wavparse ! audioconvert ! {encoder} ! filesink location="{encoded}"')
                try:
                    pipeline.set_state(Gst.State.PLAYING)
                    message = pipeline.get_bus().timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
                    self.assertIsNotNone(message, 'audio fixture encoding timed out')
                    self.assertEqual(message.type, Gst.MessageType.EOS, 'audio fixture encoding failed')
                finally:
                    pipeline.set_state(Gst.State.NULL)
                self.assertGreater(encoded.stat().st_size, 0)
                paths.append(encoded)
            for path in paths:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                for width in (360, 500, 1024, 1440):
                    with self.subTest(format=path.suffix, width=width):
                        print(f"Viewer audio: {path.suffix} at {width}px", flush=True)
                        window = ViewerWindow(app, opening=str(path))
                        try:
                            window.set_default_size(width, 740)
                            window.present()
                            media = window._preview_media
                            self.assertTrue(until(lambda: media.is_prepared() or media.get_error()), 'media never prepared')
                            self.assertIsNone(media.get_error())
                            self.assertTrue(media.has_audio())
                            if path.suffix != '.mp3':
                                self.assertGreater(media.get_duration(), 1_900_000)
                            # ID3-tagged variable-rate MP3 can have no declared duration.
                            # Verify actual output at the Pulse monitor, not just a timer.
                            capture = subprocess.Popen(['parec', '--device=viewer_fixture.monitor',
                                '--format=s16le', '--rate=48000', '--channels=1', '--latency-msec=20'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                            try:
                                find(window, 'vw-audio-transport').play_key.emit('clicked')
                                self.assertTrue(until(lambda: media.get_timestamp() > 600_000), 'saved audio did not advance')
                            finally:
                                capture.terminate()
                                pcm, capture_error = capture.communicate(timeout=3)
                            self.assertGreater(len(pcm), 1000, 'no decoded audio reached the output sink: ' + capture_error.decode(errors='replace'))
                            samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm[:len(pcm) // 2 * 2])
                            self.assertGreater(max(abs(sample) for sample in samples), 1000,
                                'saved playback output was silent')
                            print(f'OUTPUT {path.suffix} {width}px: {len(pcm)} PCM bytes, '
                                f'peak={max(abs(sample) for sample in samples)}, source={digest}', flush=True)
                            media.set_playing(False)
                            self.assertFalse(media.get_playing())
                            self.assertEqual(media._tick_id, 0)
                            if path.suffix == '.wav' and width == 360:
                                media.seek(1_000_000)
                                self.assertTrue(until(lambda: media.get_timestamp() >= 950_000), 'native seek did not move')
                            window._clear_stage()
                            self.assertFalse(media.get_playing())
                            self.assertIsNone(media.get_file())
                            self.assertIsNone(window._preview_media)
                            self.assertIsNone(window._preview_media_handler)
                            self.assertIsNone(media._pipeline)
                            self.assertEqual(media._tick_id, 0)
                            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
                        finally:
                            window.close()
                            until(lambda: not window.get_visible(), 1)
            broken = Path(directory) / 'broken.mp3'
            broken.write_bytes(b'not an audio stream')
            from luma_viewer.audio_preview import AudioPreview as NativeAudioPreview
            media = NativeAudioPreview(Gio.File.new_for_path(str(broken)))
            try:
                self.assertTrue(until(lambda: media.get_error()), 'invalid stream did not report an error')
                self.assertFalse(media.get_playing())
                self.assertEqual(broken.read_bytes(), b'not an audio stream')
            finally:
                media.clear()
        app.run_dispose()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='viewer-audio-session-') as directory:
        root = Path(directory)
        for variable in ('XDG_RUNTIME_DIR', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
            location = root / variable.lower()
            location.mkdir(mode=0o700)
            os.environ[variable] = str(location)
        children = []
        try:
            for command in ('pipewire', 'pipewire-pulse', 'wireplumber'):
                children.append(subprocess.Popen([command], stdout=(root / (command + '.log')).open('w'), stderr=subprocess.STDOUT))
                time.sleep(.4)
            subprocess.run(['pactl', 'load-module', 'module-null-sink', 'sink_name=viewer_fixture', 'rate=48000', 'channels=1'], check=True)
            subprocess.run(['pactl', 'set-default-sink', 'viewer_fixture'], check=True)
            unittest.main(verbosity=2)
        finally:
            for child in reversed(children):
                child.terminate()
            for child in children:
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
