# Deploying a model in libinput

Checked on the XPS 14 (Luma nightly 20261117.1: libinput 1.31.3, mutter 50.4)
and on a kernel replay of the same touchpad (UHID, kernel 6.19, libinput 1.31.3).

## What libinput 1.31 offers

- **Lua plugins** (since 1.29). libinput is built with Lua 5.4. A plugin
  registers for `new-evdev-device`, then gets every `evdev-frame` of a device
  (CLOCK_MONOTONIC µs timestamp) before libinput's touchpad code sees it. It can
  return a modified frame, drop it, prepend or append frames, set one timer,
  and disable built-in features (`touchpad-palm-detection`, jump detection,
  hysteresis, debouncing). The sandbox has `math`, `string` and `table`; no
  `io` or `os`.
- **Loading.** The compositor must call `libinput_plugin_system_load_plugins()`.
  mutter 50 does (libmutter-18 imports it), with the default directories
  `/etc/libinput/plugins` and `/usr/lib64/libinput/plugins`. None exist on the
  XPS today.
- **The stock tools ignore `--set-plugin-path`.** `libinput-debug-events`
  1.31.3 imports only `append_default_paths`, so the only directories a stock
  tool reads are the compositor's. `touchlab/shadow.py` opens a private
  libinput context through `libinput.so` with one plugin directory instead;
  the recorder runs it as a second stream with `--shadow-plugins DIR`.
- **Marking a palm.** Setting `ABS_MT_TOOL_TYPE` to `MT_TOOL_PALM` on a slot,
  mid-touch, makes libinput log `palm detected (tool-palm)` and drop the touch
  from pointer motion, taps, clickfinger counts and gestures. This is the same
  path the firmware's Confidence flag already takes: hid-multitouch keeps a
  Confidence=0 contact and marks its slot `MT_TOOL_PALM` (it does not drop it).
  `plugins/shadow-probe.lua` shows the rewrite end to end through shadow.py.
- **This pad is a pressure pad.** libinput's Sensel quirk sets
  `INPUT_PROP_PRESSUREPAD`, so libinput ignores pressure for palm and thumb
  detection and uses area ("thumb detection (area)") and speed. A model that
  reads force has information libinput doesn't use.

## Recommendation

Ship the model as a generated Lua plugin from a Luma package:
`/usr/lib64/libinput/plugins/50-luma-touch.lua`, matched to the device
(bus I2C, 2C2F:0034) and to a model file version.

1. **Model:** gradient-boosted trees (about 100 trees, depth 4 to 5) per
   decision horizon, exported as plain Lua comparisons. Trees need no
   runtime, are easy to inspect and cost a few thousand comparisons. A small
   sequence model is possible later; trees first.
2. **Features in the plugin:** the same definitions as `features.py`, kept
   incrementally per slot (position, edge distances, force from
   `ABS_MT_PRESSURE`, motion, stillness, other slots, `MT_TOOL_PALM` from the
   kernel as the firmware flag, `BTN_LEFT`). Key timing comes from the plugin
   also attaching to the internal keyboard's frames; it reads only the time.
   A shared test feeds the same frames to `features.py` and to the Lua code
   and compares the vectors.
3. **Decision:** evaluate a contact at 25, 50 and 100 ms after touch-down and
   again at 200 ms; on "accidental", set `MT_TOOL_PALM` on its slot. Keep
   libinput's own palm detection on; the plugin only adds palms.
4. **Holding suspect contacts (optional):** for a contact that lands where
   mistakes happen (bottom or side band, within 500 ms of a key press), the
   plugin can hold its first frames until the 25 ms decision and then release
   them or mark the slot. Other contacts are never delayed.
5. **Rollout:** shadow first. The recorder's `--shadow-plugins` runs the
   plugin in a private context next to the real one and records both, so
   agreement and timing are measured on real use before anything changes.
   Recordings exported with `touchlab export-record` replay through any
   libinput build with `libinput replay` for regression tests. Then ship in
   the nightly with an off switch (the package, or a quirk the plugin reads).

Not recommended: a patched libinput (a fork to carry), or a uinput filter
daemon (a second device, a grab, added latency).

## Latency budget

| Item | Budget |
|---|---|
| Report interval | about 8.3 ms (to be confirmed from the first recordings: `report_ms` in the report) |
| Plugin callback, per frame | under 50 µs. The probe plugin measured 9 µs per frame in shadow.py on the build host |
| Tree evaluation | under 100 µs, only at the horizons above, not every frame |
| Added input latency | none for contacts it doesn't hold; at most 25 ms for held suspect contacts |
| Recorder overhead | 3.4% of one core while touching (recorder, libinput and the shadow context together); idle otherwise |

## What can be decided in time

| libinput behaviour | When it commits | Model in time? |
|---|---|---|
| Tap | after the tap timeout (about 180 ms) | Yes, at 50 to 100 ms |
| Clickfinger count (one finger left, two right) | at the button press | Yes when the stray contact landed 25 ms or more before the click, which a resting thumb or palm almost always did |
| Two-finger scroll, gestures | after a few frames of motion | Yes at 25 to 50 ms |
| Pointer motion from a stray contact | from its first frames, after hysteresis | Partly; holding suspect contacts covers it |
| A contact that starts as a finger and becomes a palm | any time | The 200 ms re-check, and the firmware flag |
| Disable-while-typing | already timing based | The model adds force and position to the keyboard timing |

## Phases 2 to 4

- **2, collect:** run the recorder, do a guided pass, use the laptop with the
  shortcuts for a week.
- **3, train:** copy sessions and labels to the build host's private storage
  (`/srv/dev-storage/projects/luma-touch-lab/private/`, mode 0700), run
  `touchlab features`, train per horizon, and check it against libinput and
  the firmware with the report's agreement table.
- **4, ship:** generate the Lua plugin, run it in shadow mode on the XPS, then
  package it.
