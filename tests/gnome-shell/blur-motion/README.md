# Blur motion frame-time oracle

Measures compositor frame time while a Frost or Glass window moves, for the
Mutter background blur backdrop cache (`patches/mutter/0015`). Runs on the
build server in a Fedora container with the built Mutter, GNOME Shell and
LumaUI RPMs, with this directory copied to `/oracle/blur-motion/harness`.

- `blurwin.py`: a translucent GTK 4 window that requests a background blur
  through `LumaUI.SurfaceBackdrop`.
- `run.sh`: one headless Shell with virtual monitors at the owner's layout
  (2880x1800 at 1.25 plus 5120x1440). `M_THEME` picks the appearance mode,
  `M_CACHE=0` turns the backdrop cache off (`LUMA_MUTTER_BLUR_CACHE=0`), which
  is the previous code path.
- `eval.js`: three blurred windows (one maximized on the ultrawide, two
  overlapping on the panel). Records the update time of every frame, per view,
  while dragging a window across both monitors with a real pointer, resizing,
  maximizing and unmaximizing, snapping with Super+Left, and opening the
  overview.
- `compare.py`: the table, and the regression check. It fails if a Frost or
  Glass drag's mean or p95 frame time exceeds Light's by more than the margin
  (default 25% + 1 ms).
- `matrix.sh CONTAINER`: all five runs plus the comparison.

Software rendering (`M_SOFTWARE=1`, the default on the build server) makes
blur work show up as CPU time, so absolute numbers are higher than on the
owner's GPU; compare runs with each other, not with a real session.
`M_DEBUG=render` adds the per-frame backdrop log (cached or rebuilt, fresh
pixels).
