<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Dock menu "Run in the Background" render

Headless GNOME Shell render of `patches/gnome-shell/0117-luma-dock-menu-background-activity.patch`
in the Shell render oracle container (Fedora 44, gnome-shell `50.3-1.luma.99.surfacepreview20260914.80`).

- `fake_background.py` serves `org.projectluma.Background1` with Messages
  (essential, on) and Weather (off by default).
- `dock-render.js` opens the dock menu for Messages, Weather and Photos,
  toggles the row through its own `toggled` path, and records the row's
  visibility, switch state and sentence in `render.json` with a screenshot per
  state.
- `run-bga.sh` runs the Shell with the patched `appMenu.js` as a resource
  overlay under its own runtime and home directories. `BGA_NO_SERVICE=1`
  renders without the service, where no row may appear.

Copy the directory to `/tmp/bga-oracle/` in the container, put the patched
`js/ui/appMenu.js` under `/tmp/bga-oracle/overlay/js/ui/`, then run
`ORACLE_THEME=light ORACLE_OUT=/tmp/bga-oracle/out-light bash /tmp/bga-oracle/run-bga.sh`.
