# SPDX-License-Identifier: Apache-2.0
"""Voice enumeration must preserve the user's actual output module."""
import unittest
from unittest.mock import patch

from luma_leaf import speech


class Dispatcher:
    def __init__(self):
        self.modules = {"piper": [("ljspeech", "en-US", "none")],
                        "espeak": [("English", "en", "none")]}
        self.module = "espeak"
        self.voice = None
        self.closed = False

    def set_data_mode(self, _mode): pass
    def list_output_modules(self): return list(self.modules)
    def set_output_module(self, module): self.module = module
    def list_synthesis_voices(self): return self.modules[self.module]
    def set_synthesis_voice(self, name): self.voice = name
    def close(self): self.closed = True


class SpeechVoices(unittest.TestCase):
    def speaker(self):
        # Exercise the real Speaker, substituting only the SSIP transport.
        self.client = Dispatcher()
        class Protocol:
            class DataMode: SSML = "ssml"
            SSIPClient = lambda *_args: self.client
        transport = patch.object(speech, "speechd", Protocol)
        transport.start()
        self.addCleanup(transport.stop)
        return speech.Speaker()

    def test_default_restores_neural_module_after_enumeration(self):
        speaker = self.speaker()
        self.assertEqual(speaker.voice.id, "piper:ljspeech")
        self.assertEqual((self.client.module, self.client.voice), ("piper", "ljspeech"))

    def test_saved_basic_voice_preserved(self):
        speaker = self.speaker()
        speaker.refresh_voices("espeak:English")
        self.assertEqual(speaker.voice.id, "espeak:English")
        self.assertEqual((self.client.module, self.client.voice), ("espeak", "English"))

    def test_installed_voice_appears_without_restarting_leaf(self):
        speaker = self.speaker()
        del self.client.modules["piper"]
        speaker.refresh_voices()
        self.assertFalse(speaker.has_neural_voice)
        self.client.modules["piper"] = [("ljspeech", "en-US", "none")]
        speaker.refresh_voices()
        self.assertTrue(speaker.has_neural_voice)
        # The previously chosen basic voice remains a person's explicit choice.
        self.assertEqual(speaker.voice.id, "espeak:English")

    def test_removed_preferred_voice_falls_back_to_actual_installed_voice(self):
        speaker = self.speaker()
        del self.client.modules["piper"]
        speaker.refresh_voices("piper:ljspeech")
        self.assertEqual(speaker.voice.id, "espeak:English")

    def test_empty_registry_clears_removed_voice(self):
        speaker = self.speaker()
        self.client.modules.clear()
        speaker.refresh_voices()
        self.assertEqual(speaker.voices, [])
        self.assertIsNone(speaker.voice)

    def test_missing_dispatcher_has_no_fake_voice(self):
        with patch.object(speech, "speechd", None):
            speaker = speech.Speaker()
        self.assertEqual(speaker.refresh_voices(), [])
        self.assertFalse(speaker.available)
        self.assertFalse(speaker.has_neural_voice)

    def test_restarted_service_reconnects_to_real_registry(self):
        speaker = self.speaker()
        old = self.client
        old.list_output_modules = lambda: (_ for _ in ()).throw(OSError("service restarted"))
        replacement = Dispatcher()
        class Protocol:
            class DataMode: SSML = "ssml"
            SSIPClient = lambda *_args: replacement
        with patch.object(speech, "speechd", Protocol):
            speaker.refresh_voices("piper:ljspeech")
        self.assertTrue(old.closed)
        self.assertIs(speaker.client, replacement)
        self.assertEqual((replacement.module, replacement.voice), ("piper", "ljspeech"))

    def test_unavailable_service_retries_after_install(self):
        with patch.object(speech, "speechd", None):
            speaker = speech.Speaker()
        replacement = Dispatcher()
        class Protocol:
            class DataMode: SSML = "ssml"
            SSIPClient = lambda *_args: replacement
        with patch.object(speech, "speechd", Protocol):
            speaker.refresh_voices()
        self.assertTrue(speaker.available)
        self.assertIsNone(speaker.error)
        self.assertEqual(speaker.voice.id, "piper:ljspeech")
