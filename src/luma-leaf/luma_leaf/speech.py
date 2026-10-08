# SPDX-License-Identifier: Apache-2.0
"""Read aloud: speech-dispatcher, one sentence per utterance.

One sentence per utterance is what makes the rest work: pause and resume land
on a sentence rather than a guess, every word carries a mark to underline,
long chapters never reach an engine's utterance limit, and a change of speed
or voice takes effect at once by restarting the sentence being read.

This module only speaks. What to say next, and where it is on the page, is
the narrator's (narrator.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from gi.repository import GLib

try:
    import speechd
except ImportError:  # the application still reads without a voice
    speechd = None

SPEEDS = (0.75, 1.0, 1.25, 1.5, 2.0)
NEURAL_HINTS = ("piper", "neural", "premium", "enhanced", "natural", "mimic3")
NAMED_HINTS = ("daniel", "samantha", "alex", "karen", "moira", "serena", "tom", "fiona", "amy", "ryan", "jenny", "lessac")


@dataclass(frozen=True)
class Voice:
    module: str
    name: str          # speech-dispatcher synthesis voice name
    language: str
    variant: str

    @property
    def id(self) -> str:
        return f"{self.module}:{self.name}"

    @property
    def label(self) -> str:
        if self.variant and self.variant != "none":
            return self.variant
        return self.name

    @property
    def neural(self) -> bool:
        text = f"{self.module} {self.name} {self.variant}".lower()
        return any(hint in text for hint in NEURAL_HINTS)

    def rank(self) -> tuple[int, str]:
        text = f"{self.name} {self.variant}".lower()
        if self.neural:
            return 0, self.label
        if any(hint in text for hint in NAMED_HINTS):
            return 1, self.label
        return 2, self.label


def rate_for(speed: float) -> int:
    """speech-dispatcher's rate is −100…100 around the voice's own pace."""
    return max(-100, min(100, round((speed - 1.0) * 100)))


class Speaker:
    """Speaks one sentence at a time and reports words and completion on the main loop."""

    def __init__(self) -> None:
        self.client = None
        self.error: str | None = None
        self._token = 0
        self.voices: list[Voice] = []
        self.voice: Voice | None = None
        self.speed = 1.0
        if speechd is None:
            self.error = "Speech isn't available on this computer."
            return
        try:
            self.client = speechd.SSIPClient("leaf")
            self.client.set_data_mode(speechd.DataMode.SSML)
            self.refresh_voices()
        except Exception as error:  # speech-dispatcher not running or not installed
            self.client = None
            self.error = f"Speech isn't available ({error})."

    @property
    def available(self) -> bool:
        return self.client is not None

    @property
    def has_neural_voice(self) -> bool:
        return any(voice.neural for voice in self.voices)

    def refresh_voices(self, preferred_id: str | None = None) -> list[Voice]:
        """Discover actual SSIP voices after a managed installation/removal.

        Discovery temporarily changes output modules. Always restore the chosen
        voice afterwards so enumeration cannot leave reading on the last module.
        A person's existing choice wins; otherwise choose the best installed voice.
        """
        current_id = preferred_id or (self.voice.id if self.voice else None)
        if speechd is None:
            return self.voices
        if self.client is not None:
            try:
                self.client.list_output_modules()
            except Exception:
                # A managed voice installation may restart the native service.
                # Discard its old transport and reconnect through SSIP itself.
                self.close()
        if self.client is None:
            try:
                self.client = speechd.SSIPClient("leaf")
                self.client.set_data_mode(speechd.DataMode.SSML)
                self.error = None
            except Exception as error:
                self.client = None
                self.voices = []
                self.voice = None
                self.error = f"Speech isn't available ({error})."
                return self.voices
        self.voices = self._discover()
        chosen = next((voice for voice in self.voices if voice.id == current_id),
                      self.voices[0] if self.voices else None)
        self.set_voice(chosen)
        return self.voices

    def _discover(self) -> list[Voice]:
        found: list[Voice] = []
        try:
            modules = list(self.client.list_output_modules())
        except Exception:
            modules = []
        for module in modules or [None]:
            try:
                if module:
                    self.client.set_output_module(module)
                voices = self.client.list_synthesis_voices()
            except Exception:
                continue
            for name, language, variant in voices:
                if not (language or "").lower().startswith("en"):
                    continue
                voice = Voice(module or "", name, language, variant or "none")
                # espeak lists every English variant under every accent; offer
                # each accent once, plus the variants a person would recognise.
                if voice.variant != "none" and not voice.neural and not any(h in voice.variant.lower() for h in NAMED_HINTS):
                    continue
                found.append(voice)
        found.sort(key=Voice.rank)
        return found[:40]

    def set_voice(self, voice: Voice | None) -> None:
        self.voice = voice
        if self.client is None or voice is None:
            return
        try:
            if voice.module:
                self.client.set_output_module(voice.module)
            self.client.set_synthesis_voice(voice.name)
        except Exception:
            pass

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        if self.client is not None:
            try:
                self.client.set_rate(rate_for(speed))
            except Exception:
                pass

    def speak(self, words: list[str], *, on_word, on_done) -> None:
        """Speak these words as one utterance. `on_word(i)` and `on_done(finished)`
        run on the GLib main loop; a cancelled or superseded utterance reports
        nothing."""
        self.cancel()
        self._token += 1
        token = self._token
        if self.client is None:
            GLib.idle_add(lambda: on_done(False) and False)
            return
        ssml = "<speak>" + " ".join(f'<mark name="{index}"/>{escape(word)}' for index, word in enumerate(words)) + "</speak>"

        def callback(kind, index_mark=None):
            if token != self._token:
                return
            if kind == speechd.CallbackType.INDEX_MARK and index_mark is not None:
                try:
                    GLib.idle_add(_once, on_word, int(index_mark))
                except ValueError:
                    pass
            elif kind == speechd.CallbackType.END:
                GLib.idle_add(_guarded, self, token, on_done, True)
            elif kind == speechd.CallbackType.CANCEL:
                pass

        try:
            self.client.set_rate(rate_for(self.speed))
            self.client.speak(ssml, callback=callback, event_types=(
                speechd.CallbackType.INDEX_MARK, speechd.CallbackType.END, speechd.CallbackType.CANCEL))
        except Exception as error:
            self.error = str(error)
            GLib.idle_add(_guarded, self, token, on_done, False)

    def cancel(self) -> None:
        self._token += 1
        if self.client is not None:
            try:
                self.client.cancel()
            except Exception:
                pass

    def close(self) -> None:
        self.cancel()
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None


def _once(callback, *args):
    callback(*args)
    return False


def _guarded(speaker: Speaker, token: int, callback, *args):
    if token == speaker._token:
        callback(*args)
    return False
