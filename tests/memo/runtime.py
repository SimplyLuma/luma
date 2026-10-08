#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Isolated native Memos interaction smoke test; never touches recordings."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src/luma-platform/appkit"), str(REPO / "src/prairie-core")]
temporary = tempfile.TemporaryDirectory(prefix="memos-v70-runtime-")
root = Path(temporary.name)
os.environ.update({
    "XDG_MUSIC_DIR": str(root / "Music"),
    "XDG_STATE_HOME": str(root / "state"),
    "XDG_DATA_HOME": str(root / "data"),
    "GSETTINGS_BACKEND": "memory",
    "LUMA_MEMOS_FIXTURE": str(REPO / "tests/fixtures/memos-v70.json"),
    "LUMA_MEMO_STYLE_PATH": str(REPO / "src/prairie-core/style/memo.css"),
})

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402
from prairie_apps.voice_memos import VoiceMemosApplication, VoiceMemosWindow  # noqa: E402

app = VoiceMemosApplication()
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
app.register(None)
window = VoiceMemosWindow(app)

assert window.fixture and window.root is None
assert len(window.memos) == 5 and window.current_id == "dry"
assert len(window._share_people()) == 8 and all(person.picture for person in window._share_people()[:5])
assert not window.transcript.get_visible()  # fixture speech is illustrative, not a real transcript
assert window.foot.add_button is not None
window._search("notification lip")
assert [memo.id for memo in window._visible()] == ["dry"]
window._search("")
window._set_speed(1.5)
assert window.speed == 1.5 and window.deck.transport.speed == 1.5
window._play()
assert window.playing
window._seek(20)
assert window.play_position == 20
window._play()
assert not window.playing

window._record()
assert window.recording and window.session is None
window._pause_recording()
assert window.record_paused
window._pause_recording()
assert not window.record_paused
window._record()
assert not window.recording and window.current_id == "fixture-5"
window._rename("New title")
assert window._find().title == "New title"
window._delete()
assert window.deleted[0].title == "New title"
window._restore_last()
assert window._find().title == "New title"

window.close()
app.quit()
assert not (root / "Music").exists()
temporary.cleanup()
print("Memos fixture runtime checks passed")
