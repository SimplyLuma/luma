# Memos v70 port

Memos uses the LumaUI window, islands, sidebar foot, corner pill, Share sheet,
menus, action centre and media transport. The kit's `AudioWaveform` draws both the deck and
the compact list waveforms from real peak values. Memos owns the timed transcript,
recording timer and in-memory Studio fixture.

The existing app ID, binary and `Music/Voice Memos` library stay in place.
Real mode reads the existing Ogg Opus recordings and sidecar cues. It retains
the established record, rename, Trash/Undo and byte-for-byte audio export
operations. The Share sheet's Copy action offers the selected audio file on
the clipboard. File reads and peak decoding run off the GTK thread. Favourites,
recently deleted, duplicate, trim and speech processing remain fixture-only
or hidden on real data until their storage and write paths are approved.
The real library has no link or sending service; those Share callbacks report
that limitation. A kit request tracks hiding those controls when unavailable.
The Share sheet's fixture people and cropped portraits are copies of v70's
sample Contacts assets. The fixture never opens the user's library or microphone.

## Checks

From the repository root:

```sh
python3 -m unittest tests/unit/test_memos_data.py tests/unit/test_memo_library.py tests/unit/test_prairie_audio_backend.py
MEMO_GST_TESTS=1 python3 tests/unit/test_prairie_audio_backend.py
python3 tests/memo/runtime.py
```

Run the last two on a host with GTK4 and GStreamer. The GStreamer check records
a short test tone through `audiotestsrc` into a temporary directory, pauses,
resumes, saves it, and checks the Ogg output. It never opens a microphone.
The runtime check constructs an unshown GTK window in fixture mode and exercises
search, playback state, speed, recording, pause, rename and Trash/Undo without
writing a real recording.

Visual acceptance is `bash ~/Documents/.luma-dev/bin/lumaui-conform memos --all`
from this worktree. The scenario includes the selected memo, search, music,
filter menu, favourites, deleted empty state, More, Share, speed and recording.
The four variant reports must all say PASS before a preview is installed.
