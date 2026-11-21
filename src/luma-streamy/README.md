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
- `luma_streamy/portals.py`: ScreenCast (restore tokens with a single
  fallback to the picker, `pipewire-serial`), Camera, and app sound through
  `luma_appkit.audio_capture`; the host app id is registered with the portal
  at startup.
- The window is LumaUI: `NavigationSidebar` for scenes with the scene's
  widgets in its pane, a `Dock` with the preview as its bare stage and Audio
  as an inset that fits its strips, `ArrangeView` for direct manipulation,
  `AudioMeter`s, and the `ActionCenter` (Add widget, Record, Go live; a live
  chip; a selection's editors grow from the bar).

Stream keys are stored with libsecret. App audio widgets carry the apps (or
all sound but Streamy's own) the person picks in the Share sound picker,
through `luma_appkit.audio_capture`. A widget keeps one capture while its
scene is shown and closes it off-scene; its restore token brings it back
without the picker. The Add menu offers App audio only where the portal is.

## Destinations, Stats and Chat

- `luma_streamy/outputs.py` (shapes in `output_graph.py`): one encode per
  canvas and preset, teed to every destination row that shares it. Each row
  is its own pipeline fed by appsrc (`flvmux`/`eflvmux ! rtmp2sink`,
  `mpegtsmux ! srtsink`, `whipclientsink`, `matroskamux`/`mp4mux ! filesink`),
  so one dropped connection reconnects alone and joins on the next key frame.
  A recording drains its encoder before its muxer finishes the file.
  Recordings pick hardware AV1, then hardware H.264, then SVT-AV1, then
  OpenH264.
- `luma_streamy/destinations_panel.py`: the rows (official logos in
  `data/logos`), their editors in the action bar, and Go live. Keys and
  sign-ins are in the keyring (Secret schema
  `org.projectluma.Streamy.Destination`, attributes `account` = row id and
  `kind`).
- `luma_streamy/accounts.py`: Twitch sign-in by device code, YouTube by the
  installed-app loopback flow with PKCE; both then read the account's name,
  ingest and stream key. Client ids come from `$STREAMY_TWITCH_CLIENT_ID`,
  `$STREAMY_YOUTUBE_CLIENT_ID` and `$STREAMY_YOUTUBE_CLIENT_SECRET`, or
  `oauth.json` in `$XDG_CONFIG_HOME/streamy/` or `/etc/luma-streamy/`:

  ```json
  {"twitch": {"client_id": "…"}, "youtube": {"client_id": "…", "client_secret": "…"},
   "kick": {"pusher_key": "…", "pusher_cluster": "us2"}}
  ```

  Twitch needs an application of client type Public (scopes
  `channel:read:stream_key user:read:chat user:write:chat`); YouTube needs a
  Google Cloud OAuth client of type Desktop app with the YouTube Data API v3
  enabled. Without ids the flows run against a placeholder, the platform
  refuses it, and the editor says so; a pasted stream key still works.
- `luma_streamy/stats.py`, `stats_panel.py`: CPU from `/proc/stat`, GPU and
  video engine busy from DRM fdinfo (i915, xe, amdgpu), memory, and the
  recording disk's write rate and free space; sampled at most once a second,
  only while the panel is shown or something is live.
- `luma_streamy/chat.py`, `chat_panel.py`: a merged feed from pasted links,
  read-only: Twitch anonymous IRC over WebSocket, Kick's public Pusher socket.
  Sockets are open only while the panel is shown or something is live.

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

`tests/outputs_smoke.py` sends one encode to several RTMP and SRT rows and two
recordings (run it against a local mediamtx). `tests/panels_shot.py` renders
the three panels with fixture rows (`PANELS_SHOT`, `PANELS_WIDTH`,
`PANELS_OPEN`) and `tests/panels_conform.py` checks its boxes.

`STREAMY_SHOT=out.png STREAMY_SHOT_WIDTH=360` renders the window to a PNG and
quits, writing the measured boxes to `out.json`. `tests/conform.py out.json`
checks them against LumaUI's rules (row insets and heights, glyph centres,
inset headers, the audio inset's slack, the preview's breathing room, the
bar chip), and `tests/conform.py --same rest.json editing.json` checks that
an open editor moved nothing.
