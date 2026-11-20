# Recording other apps' sound

Luma apps record another app's sound (a game, a browser tab, a music player)
through the Audio Capture portal, `org.freedesktop.portal.AudioCapture`. The
app never gets the PipeWire socket. The person chooses in the Share sound
picker which apps it may hear, or all sound except its own; the app gets a
PipeWire remote that can see one node carrying that sound and nothing else.
A new stream of a chosen app (the next song, a restarted game) joins the node
by itself.

Use `luma_appkit.audio_capture`; it hides the portal's request and session
objects and stays stable if the interface changes upstream.

```python
from luma_appkit.audio_capture import ALL_SOUND, APPLICATIONS, request_audio_capture

def ready(capture, error):
    if error is not None:
        if not error.cancelled:
            show_error(str(error))
        return
    settings.set_string("audio-capture-token", capture.restore_token or "")
    label.set_text(capture.description)        # "Spotify, Firefox" or "All sound"
    source = capture.make_source()             # pipewiresrc, fd and target set
    pipeline.add(source)
    ...

request_audio_capture(None, ready,
                      types=APPLICATIONS | ALL_SOUND,
                      multiple=True,
                      restore_token=settings.get_string("audio-capture-token") or None)
```

- `capture.pipeline_description()` gives `pipewiresrc fd=N target-object=S
  do-timestamp=true` for `Gst.parse_launch`. The descriptor belongs to the
  capture: keep the capture until the pipeline is stopped.
- `capture.close()` ends the session and closes the remote. The portal may end
  it too (the person revoked it); set `capture.on_closed` to hear about that.
- `persist=True` (the default) asks for a restore token. Store it and pass it
  next time: the same choice applies without the picker, across restarts and
  sign-ins.
- `include_self=True` keeps the app's own sound in "All sound".
- The sound is each app's output as it plays, after its own volume.

## Packaging

A Flatpak needs no PipeWire or PulseAudio socket for this; the portal is
reached through the session bus like every portal. Do not add
`--filesystem=xdg-run/pipewire-0`.

## How it works

- `xdg-desktop-portal` (Luma patch 0004) owns sessions, restore tokens and the
  restricted remote (core, node factory and the capture node only).
- `luma-portal` shows the picker and asks `luma-audio-devices`
  (`org.projectluma.AudioDevices1.CreateCapture`) for the node.
- WirePlumber (`luma/app-audio.lua`) owns the node, links the chosen apps'
  streams into it as they appear, and links a portal remote's recording
  stream only to a capture issued to the same app.
