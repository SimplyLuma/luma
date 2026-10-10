# Sound input stays in Luma Settings

Run in a disposable Settings build environment, as an ordinary user:

```sh
sh tests/gnome-control-center/sound-input/run.sh /path/to/patched/settings/source /path/to/settings/build
```

The matching Meson build must already contain `test-luma-view`,
`test-luma-live-desk` and Gvc. The runner reuses those compiled view objects
and compiles the production sound adapter from the supplied source. It needs
PulseAudio, `pactl`, Xvfb, D-Bus, GCC, Ninja and the build's development libraries.

The test loads only a silent sink and two virtual microphones into a private
PulseAudio server. It opens the mapped Luma picker, selects a different input,
checks that no older panel opens, changes input volume, and checks the actual
server's default source, volume and unmute. It recreates the adapter and view
to verify retained settings, then rejects an input absent from the picker
without changing the selected source. The original adapter fails the
no-delegation assertion.

This verifies the shared Gvc/PulseAudio protocol and Settings boundary. It
does not prove microphone capture, speaker playback or a hardware codec fix.
The virtual sources have no hardware card; Gvc's missing-card warnings are
expected, while GTK/GLib warnings and critical errors remain fatal.
