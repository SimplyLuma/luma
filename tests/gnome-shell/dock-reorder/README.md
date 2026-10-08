# Dock reorder oracle

Shell patch 0188: a dock icon is pressed, dragged and dropped in a new
place; a gap opens where it will land and follows the pointer; the order
is `favorite-apps` and survives a restart; a running app that is not
pinned is pinned where it is dropped.

`reorder.js` and `restart.js` are scenarios for the movable-islands
harness in `tests/gnome-shell/shelf-arrange` (`run.sh OUT SCRIPT`). Run
both halves, the second after a Shell restart with the same settings:

```
podman exec -e SA_RESTART=1 -e SA_SCRIPT2=../dock-reorder/restart.js \
  <oracle container> bash <tests>/shelf-arrange/run.sh <out> ../dock-reorder/reorder.js
```

It drags with the virtual pointer the way a hand does, about 4 px every
40 ms (slower, and 400 ms held still is arrange mode by design), and stops
when the gap is where a person would stop: C before A (saved as C, A, B,
...), A after D, an icon back on its own place (no change), a pinned icon
past the separator (stays pinned, last), a running app in among the pinned
ones (pinned there), an icon on the folders rail (refused, order kept) and
across the rail and back (dropped in the gap), holding an icon still
(arrange mode, no reorder), and a reorder after arrange mode. `restart.js`
fails unless the first run changed the order, so an order nobody managed
to change cannot pass as persistence.

On Shell .146 it fails 10 of 18 (no gap, every drop snaps back); with 0188
it passes 18 of 18 and 3 of 3 after the restart.

Touch is not asserted: the headless virtual touchscreen does not deliver
the lift a drag in progress sees. With 0188 a touch drag starts and the
gap follows the finger; on .146 no gap opens.
