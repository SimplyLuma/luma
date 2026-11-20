# Streamy

Live streaming and recording for Luma. Scenes hold widgets (displays,
windows, cameras, images, a microphone, desktop audio) on a 1920 × 1080 or
1080 × 1920 canvas; each picture widget can have rounded corners, a stroke and
a shadow. The program goes to Twitch, YouTube or any RTMP(S) server, or to an
MKV or fragmented MP4 file.

## How it works

- `luma_streamy/model.py`: scenes and widgets, saved as JSON in
  `$XDG_DATA_HOME/streamy/project.json`.
- `luma_streamy/graph.py`: the GStreamer graph, described as data (tested
  without GStreamer).
- `luma_streamy/engine.py`: builds and edits the graph. Each picture widget is
  `source ! glupload ! lumaframe ! glvideomixer`; sound is `pulsesrc` or
  `pipewiresrc ! volume ! level ! audiomixer`. Only the active scene's visible
  widgets capture; the rest are set to NULL and removed, their portal
  sessions kept open. Recording and streaming are separate pipelines fed from
  appsink taps, created only while they run, so a dropped connection never
  stops the preview or a recording. Streams reconnect with backoff (2 s, ×1.5,
  up to 15 min).
- Encoders are picked at run time, hardware first, and checked by encoding a
  frame: `vah264lpenc`, `vah264enc`, `openh264enc` for H.264; `vaav1lpenc`,
  `vaav1enc` for AV1; `fdkaacenc` for AAC.
- `gst-lumaframe/`: the GL element that draws one layer in one pass
  (shadow, rounded mask, stroke, crop, opacity).
- `luma_streamy/portals.py`: ScreenCast (restore tokens, `pipewire-serial`)
  and Camera portals.
- The window is LumaUI: `NavigationSidebar` for scenes, `CreativeWorkspace`
  with docked `FloatingPanel`s (widgets, inspector, audio) and saved layouts,
  `ArrangeView` for direct manipulation, `AudioMeter`s, and the
  `ActionCenter` (Add widget, Record, Go live; a live `BarReadout`).

Stream keys are stored with libsecret. Per-app audio will come from the
per-app audio portal (`app-audio` widgets read a `pipewiresrc` fd it gives).

## Development

```sh
PYTHONPATH=src/luma-streamy python3 -m unittest discover -s src/luma-streamy/tests -p 'test_*.py'
```

`STREAMY_FAKE_SOURCES=1` replaces portal capture with drawn stand-ins and test
tones, for VMs and check runs. `tests/engine_smoke.py` records 10 s and
streams 10 s to an RTMP server headless:

```sh
STREAMY_FAKE_SOURCES=1 GST_PLUGIN_PATH=<lumaframe build> \
  python3 src/luma-streamy/tests/engine_smoke.py /tmp rtmp://127.0.0.1/live/test
```

`STREAMY_SHOT=out.png STREAMY_SHOT_WIDTH=360` renders the window to a PNG and
quits.
