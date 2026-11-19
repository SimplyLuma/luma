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

## The plugin

`touchlab export-plugin` (or `touchlab train`) writes
`50-luma-touch-roles.lua` for `/usr/lib64/libinput/plugins/`, generated from
`plugins/luma-touch-roles.lua.in` and a model file, and
`50-luma-touch-roles.quirks` for the force pads.

1. **Model:** two stages of multi-class boosted trees (six roles) as plain
   Lua tables; a dozen-line evaluator walks them. The window (25, 50, 100,
   200 ms) is an input, so one model covers all four.
2. **Features in the plugin:** `contact_features` is the Python
   `rfeatures.contact_features` ported line for line; the tests run both on
   the same contacts through liblua and compare every value, and compare
   the model's probabilities. The surface mask comes from the device's
   axes, its udev properties and the list of force pads taken from
   libinput's own quirks. Keyboards are attached for press times only.
3. **Decision:** each window is evaluated when it ends (a timer covers
   contacts that send no frames) or at lift. Stage 2 gets the other
   contacts' stage-1 opinions; votes accumulate. Roles that libinput must
   not track (resting thumb, clicking thumb, palm, brush) get
   `MT_TOOL_PALM` on their slot; libinput drops the touch from motion,
   taps, gestures and the clickfinger count, and `max(1, n)` keeps a lone
   pressing thumb a left click. A held slot returns to `MT_TOOL_FINGER`
   only when "track" leads by a margin, and libinput takes the touch up
   again (tool-palm is reversible). The firmware's own palm flag always
   wins, and a new contact in a slot doesn't inherit the previous
   contact's tool type.
4. **Force pads:** libinput 1.31 disables `ABS_MT_PRESSURE` on pressure
   pads (`tp_init_pressurepad`) before any plugin sees a frame, so without
   help the plugin never sees force on the XPS. The generated quirk takes
   `INPUT_PROP_PRESSUREPAD` off the known pressure pads and turns
   libinput's own pressure use off (`AttrPressureRange=0:0`, palm and
   thumb pressure thresholds 0). Checked against libinput's source, not
   yet on the XPS.
5. **Pads without a tool-type axis** (most PS/2): the plugin adds one with
   `set_absinfo` when the device is created, so libinput honours the palm
   flag. To be checked on a PS/2 pad.
6. **Rollout:** shadow first (`--shadow-plugins`), then the nightly with an
   off switch. Recordings exported with `touchlab export-record` replay
   through any libinput build for regression tests.

Not recommended: a patched libinput (a fork to carry), or a uinput filter
daemon (a second device, a grab, added latency).

## Latency budget

| Item | Budget |
|---|---|
| Report interval | about 8.3 ms (to be confirmed from the first recordings: `report_ms` in the report) |
| Plugin callback, per frame | under 50 µs. The probe plugin measured 9 µs per frame in shadow.py on the build host |
| Evaluation (features + both stages) | 126 µs in Lua 5.4 per window for the depth-3 model (docs/model.md), only when a window ends, not every frame |
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
