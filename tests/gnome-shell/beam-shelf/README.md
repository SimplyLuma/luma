# Beam and shelf surface oracle

Evidence harness for GNOME Shell patches 0131 (one placement rule for shelf
surfaces), 0132 (Beam) and 0133 (silent volume slider), gnome-settings-daemon
patch 0001 and `luma-sound-theme`. Runs on the build server; see
`docs/changes/2026-09-16-beam-shelf-placement-silent-volume.md` for results.

- `run.sh`, `go.sh`, `eval.js`, `matrix.sh`: a headless Shell per case (dock
  edge, scale, appearance mode). `eval.js` opens every shelf surface with real
  pointer clicks, measures the painted island edge to the surface edge, and
  crops screenshots; it also covers Beam's levels, the no-shelf cases, motion
  and Quick Options dismissal. `B_CONTAINER` selects the container (the shared
  `luma-shell-oracle` with an overlay, or a Fedora container with the built
  RPMs installed and `B_OVERLAY_DEFAULT=none`). `atspi.py` logs what a screen
  reader receives (`B_A11Y=true`).
- `sound/`: a PipeWire session (null sinks, `parec` capture, timestamped
  `pw-mon`) with gnome-shell and `gsd-media-keys`. `probe.js` moves the Quick
  Options volume slider, presses the volume keys (plain, Shift, Alt, mute) and
  plays other event sounds; `analyze.py` reports, per action, the captured
  level and the event ids that reached PipeWire. `make-tone.py` writes the
  tone used for the "something is playing" case.
