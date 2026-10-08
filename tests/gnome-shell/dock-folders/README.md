# Dock folders oracle

Shell patch 0180: a folder pinned to the Dock, its Stack and its Grid,
married and separated, and one broken out of the dock.

`folders.js` is a scenario for the movable-islands harness in
`tests/gnome-shell/shelf-arrange` (`run.sh OUT SCRIPT`), so it runs in the
same headless Shell with the same settings, overlay and Tiling Shell. It
makes a real `~/Downloads` and `~/Projects` first, because every count, size
and time the surface shows is read from the file system.

```
podman exec -e SA_OVERLAY=… -e SA_SCHEMA_DIR=… -e SA_MODE=dark \
  -e SA_EDGES=bottom luma-shell-oracle \
  bash /oracle/shelf-arrange/tests/run.sh /oracle/…/out/dark-bottom folders.js
```

- `SA_MODE` is the appearance mode: light, dark, frost or glass.
- `SA_EDGES` is a comma-separated list of edges to walk; each one is checked
  and photographed married, separated, as a Stack, as a Grid and broken out.

It checks that the rail is a part of the dock island when married and the
`folders` island when separated, that it is one rail either way, that the
surface hangs from its own tile away from the dock's edge on every edge,
that the arrangement is remembered per folder, that a detached folder
survives a click elsewhere and Escape closes it, that the count means files
that arrived since the folder was last opened, that the tile art and the
count follow a folder that changes underneath them, and that every part of
it is reachable from the keyboard.

This container has no GIO file monitor backend, so the scenario runs the
reload the directory watch would have run instead of waiting for it.
