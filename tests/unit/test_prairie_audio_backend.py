#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Memos audio file safety and optional isolated GStreamer recording tests."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src/prairie-core"))
from prairie_apps import audio_backend as audio  # noqa: E402


class AudioBackendTests(unittest.TestCase):
    def test_rename_preserves_recording_and_existing_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "Recording.ogg"
            original.write_bytes(b"OggS")
            audio.sidecar(original).write_text('{"source":"Studio"}')
            audio.peaks_path(original).write_bytes(b"peaks")
            renamed = audio.rename_recording(original, "Interview", root)
            self.assertEqual(renamed.name, "Interview.ogg")
            self.assertEqual(renamed.read_bytes(), b"OggS")
            self.assertEqual(audio.sidecar(renamed).read_text(), '{"source":"Studio"}')
            self.assertEqual(audio.peaks_path(renamed).read_bytes(), b"peaks")
            self.assertFalse(original.exists())
            self.assertFalse(audio.sidecar(original).exists())

    def test_rename_rejects_outside_symlinks_and_collisions(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory)
            original = root / "Recording.ogg"
            original.write_bytes(b"OggS")
            outside = Path(other) / "Outside.ogg"
            outside.write_bytes(b"OggS")
            (root / "Link.ogg").symlink_to(outside)
            with self.assertRaises(ValueError):
                audio.rename_recording(outside, "No", root)
            with self.assertRaises(ValueError):
                audio.rename_recording(root / "Link.ogg", "No", root)
            with self.assertRaises(ValueError):
                audio.rename_recording(original, "/", root)
            (root / "Interview.ogg").write_bytes(b"reserved")
            with self.assertRaises(FileExistsError):
                audio.rename_recording(original, "Interview", root)

    @unittest.skipUnless(os.environ.get("MEMO_GST_TESTS") == "1",
                         "set MEMO_GST_TESTS=1 for the isolated audiotestsrc run")
    def test_pause_resume_and_save_test_tone(self):
        # GStreamer state and GI namespace caches are process globals. Run the
        # real native pipeline without inheriting other backend mock fixtures.
        if __name__ != "__main__":
            result = subprocess.run([sys.executable, __file__], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = audio.start_recording(root, source="audiotestsrc", source_label="Test tone")
            time.sleep(0.2)
            session.pause()
            self.assertTrue(session.paused)
            time.sleep(0.1)
            session.resume()
            self.assertFalse(session.paused)
            time.sleep(0.2)
            saved = session.stop()
            self.assertTrue(saved.is_file())
            self.assertGreater(saved.stat().st_size, 100)
            self.assertEqual(saved.read_bytes()[:4], b"OggS")
            self.assertTrue(audio.sidecar(saved).is_file())
            player = audio.start_playback(saved, root, sink="fakesink")
            try:
                for rate in (0.75, 1.25, 1.5, 2.0, 1.0):
                    player.set_rate(rate)
                    self.assertEqual(player.rate, rate)
            finally:
                player.stop()


if __name__ == "__main__":
    unittest.main()
