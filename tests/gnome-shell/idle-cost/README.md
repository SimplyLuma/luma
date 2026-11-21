# Idle cost

What a desktop at rest costs the Shell. A headless Luma session with three
virtual monitors (1920x1200, 2560x1440 and 1920x1200 at scales 1, 1 and
1.25), the shelf, dock, clock and status cluster, and two still windows.
After the session settles and the pointer is parked, nothing on screen
changes, so the frame clock should not dispatch and the main thread should
sleep.

`probe.js` measures over `IDLE_MS` (10 s), then over 8 s in each of these
states: the pointer resting on the shelf, after a notification (once its
breaths are over), right after Quick Settings and the overview close, a
window repainting every frame on the first monitor, and after that window
closes. Per state:

- frame clock dispatches and paints per view per second (`before-update`,
  `after-paint`);
- context switches of the Shell's main thread per second (wakeups);
- the Shell's CPU time.

A state fails if any view dispatches more than `IDLE_MAX_DISPATCHES` (0.5)
times a second or the main thread wakes more than `IDLE_MAX_WAKEUPS` (15)
times a second; with the animating window, the other two monitors must stay
at rest. `MODE=idle` measures the resting desktop only.

To find what keeps a clock running, `HOLD=<label prefix>` stays in that state
for `HOLD_MS` after measuring it (writing `hold.ready`, to attach a profiler)
and `DIAG` runs tools there: `find` hides each piece of top chrome and each
window in turn, `transitions` lists transitions in flight, `clips` what each
painted frame repaints, `sources` the main-loop sources, laters and redraws
queued from JavaScript, `allocations` actors whose allocation keeps changing.

Run inside a container with the Shell installed (a Luma OS tree works), with
this directory at `/oracle/t`:

    bash /oracle/t/run.sh    # idle.json, scenarios.json, checks.json, shell.log

`ORACLE_OVERLAY=dir` loads `dir/js/ui` and `dir/data/theme` over the
installed Shell. `ORACLE_MONITORS` and `ORACLE_SCALES` change the layout.

Software rendering (llvmpipe) makes painting cost CPU and main-thread
context switches; compare dispatches and paints between runs, not CPU with a
real session.

Headless, aarch64 Luma tree 20261119.2 (Shell .185, Mutter .21), three
monitors at 1, 1 and 1.25:

| state | before: dispatches/s per view | after (Shell 0358 overlay, Mutter with 0037) |
|---|---|---|
| desktop at rest | 0.2, 0.2, 0.2 | 0.4, 0.2, 0.2 |
| after Quick Settings closes | 46, 43, 43 (278 wakeups/s) | 0, 0, 0 (0.1 wakeups/s) |
| after the overview closes | 45, 43, 43 (339 wakeups/s) | 0.2, 0.2, 0.2 (4.6 wakeups/s) |
| window animating on monitor 1 | 60, 58, 58 | 60, 0, 0 |
