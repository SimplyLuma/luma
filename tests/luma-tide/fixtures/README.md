# Tide gapless MP3 fixtures

These four small MP3 files exist because Fedora's GStreamer set has no MP3
*encoder* plugin that the package's own `Requires` pull in, so unlike the
FLAC/Opus/Vorbis fixtures (which `gapless_runtime.py` encodes on the fly with
`audiotestsrc` + the encoders already required) an MP3 fixture cannot be
generated during `%check`. Committing them keeps the check dependency-free:
nothing but `gstreamer1-plugins-base`/`-good` and an MP3 decoder is needed to
run it.

## Provenance

Generated with `regenerate_mp3_fixtures.py` on Fedora 44 (GStreamer 1.28.7,
`lame` 3.100). No third-party audio: every file is a synthetic sine tone from
GStreamer's own `audiotestsrc` (440 Hz for `_a`, 880 Hz for `_b`), 44100 Hz
mono, exactly 44032 samples (1.0 s) of source PCM, encoded at 64 kbit/s CBR.

| File | Encoded with | Gapless metadata |
| --- | --- | --- |
| `gapless_lame_a.mp3`, `gapless_lame_b.mp3` | `lame -b 64 -m m` | Xing/LAME header carrying encoder delay + padding |
| `gapless_itunsmpb_a.mp3`, `gapless_itunsmpb_b.mp3` | `lame -b 64 -m m -t` (no Xing tag), then an `iTunSMPB` ID3 `TXXX` frame added with mutagen | iTunes-style `iTunSMPB` only |

`gapless_mp3_fixtures.json` records the source sample count and what each file
actually decoded to when it was made. `gapless_runtime.py` asserts against
those numbers, so a change in GStreamer's behavior shows up as a test failure
rather than a silently different claim.

## What they measure

`mpegaudioparse` reads the Xing/LAME header, so the LAME pair decodes to
exactly its 44032 encoded samples and albums made of such files are
sample-accurate across a track boundary — that is the common real-world MP3
case (LAME, and anything using libmp3lame).

GStreamer has no iTunSMPB support at all, so the iTunSMPB pair decodes to
46080 samples: 576 of encoder delay plus 1472 of padding are left in, about
46 ms per track. Tide cannot trim that in-process — the clip has to happen
inside the decode chain, and PyGObject exposes no way to resize or replace a
buffer in a pad probe — so this is recorded as a known limitation and would be
fixed by teaching `mpegaudioparse` about iTunSMPB upstream.

## Regenerating

Needs `lame`, `python3-mutagen`, and GStreamer with `lamemp3enc`/an MP3
decoder (`gstreamer1-plugins-ugly-free`, `gstreamer1-plugin-mpg123`) — none of
which are package dependencies, which is exactly why the output is committed:

```sh
python3 tests/luma-tide/fixtures/regenerate_mp3_fixtures.py tests/luma-tide/fixtures
```
