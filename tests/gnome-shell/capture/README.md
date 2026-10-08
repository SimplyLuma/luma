# Capture oracle

Evidence harness for GNOME Shell patch 0142 (Capture, the screenshot and
screen recording tool). Runs on the build server in a headless Shell with a
PipeWire session, driving Capture with virtual pointer and keyboard events.

- `run.sh` (inside the container), `go.sh` (one run from the host) and
  `matrix.sh` (every case, into `evidence/<run>/`). `C_OVERLAY=none` with a
  Fedora container that has the built RPMs installed tests the packages
  instead of a resource overlay.
- `eval.js` cases (`C_ONLY`): `bar` (each mode, the Options menu and a
  tooltip, measured against the dock island), `shortcuts` (Ctrl+Shift+1 to
  4), `selection`, `window`, `timer`, `thumbnail`, `record` (selection and
  whole-screen recordings stopped by Stop, the shortcut and Escape),
  `saveto` and `persist` (a second session on the same home), `search`,
  `keyboard` (with `C_A11Y=true`, `atspi.py` logs what a screen reader
  receives), `multimonitor`, and `paintprobe` (how Capture tells the
  screen's own paint from a screenshot's).
- `diff.py A B` compares a captured PNG with the same region of the screen.
- Recordings are checked by decoding frames with `gst-launch-1.0` and
  looking where the pill and outline were.
