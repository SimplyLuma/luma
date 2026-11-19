# Touch surfaces and what the model gets from each

One model family runs on every surface. A surface is classed by what its
hardware reports, never by vendor, and a feature mask says which signals
are real. A signal a surface doesn't report is NaN in the feature row; the
trees learned a default branch for "absent", and the mask bits (`m_*`) are
inputs too, so the same trees serve a Sensel force pad and a PS/2 Synaptics
pad without per-device code.

Code: `touchlab/surfaces.py` (probe, classes, quirks), `touchlab/rfeatures.py`
(features and masks). Run `touchlab probe` on a machine to see its surfaces.

## Classes

Mined from libinput's `test/litest-device-*.c` (34 touch devices, real axis
ranges, `touchlab/data/litest-surfaces.json`, refreshed with
`touchlab mine-litest <libinput source>`), with libinput's quirks applied:

| Class | Typical devices | litest devices |
|---|---|---|
| `legacy-clickpad` | PS/2 and SMBus/RMI4 Synaptics, Elan, ALPS | 10: synaptics-x1-carbon-3rd, t440, x220, rmi4, hover, st, elantech, alps-3fg, alps-dualpoint, alps-semi-mt |
| `ptp-clickpad` | I2C/USB HID Precision Touchpads with a mechanical click; Apple bcm5974 and Magic Trackpad | 11: atmel-hover, synaptics-i2c, synaptics-phantomclicks, panasonic-cf-sv1, magic-trackpad, bcm5974, … |
| `force-pad` | Sensel and Cirque (Dell XPS 14/16), Goodix and Synaptics pressure pads, Apple Force Touch, haptic Chromebook pads | 2: generic-pressurepad, synaptics-pressurepad; and 27 quirk entries that add `INPUT_PROP_PRESSUREPAD` |
| `touchscreen` | Phones (Fold, Pixel 9, Titan 2 Elite, FP6), tablets, laptop screens | 11: nexus4, generic-mt, protocol-a, calibrated, mt-tool, Wacom finger screens, … |

How the probe decides, in order: `INPUT_PROP_DIRECT` → touchscreen;
`INPUT_PROP_PRESSUREPAD` or a pressure axis with a resolution (libinput's
own test, `tp_init_pressurepad`) → force pad; i8042 or RMI bus, or no
firmware palm flag on a non-HID bus → legacy clickpad; an I2C/USB/BT/SPI
pad → PTP clickpad. Sources: sysfs bitmaps (no privileges), `EVIOCGABS`
when the node is readable, else the HID report descriptor (world-readable)
for ranges, units and the Confidence usage; or a recorder header.

## Signals per class

| Signal (mask bit) | legacy | PTP | force pad | touchscreen |
|---|---|---|---|---|
| Position in mm (`resolution`) | usually; semi-MT pads give a bounding box | yes | yes | often missing; assumed 70 × 150 mm |
| Pressure (`pressure`) | most, but it is capacitance, i.e. contact area | some, also area | yes, real force (`force`) | some, area |
| Contact size (`size`, `minor`) | rare (`ABS_TOOL_WIDTH` is per pad, not per contact) | about half, ellipse on some | rarely | most, ellipse on many |
| Firmware palm flag (`confidence`) | no (RMI4 has a tool type) | yes (HID Confidence → `MT_TOOL_PALM`) | yes on HID pads | few |
| Click (`button`, `haptic`) | mechanical | mechanical | haptic, triggered by force | none |
| Keyboard beside it (`keyboard`) | yes | yes (no for external pads) | yes | no |

## How the model makes up for what is missing

- **Force pad, no size (the XPS 14).** The force-over-time shape stands in
  for contact size: `p_area` (force integrated over the window), `p_rise`
  and `p_rise_ms` (how fast it loads), `p_settle_cv` (how still it is once
  loaded), `p_t_max_ms` and `p_drop`. A palm loads slowly to a high,
  wobbling plateau; a resting thumb settles to a low, very stable one; a
  pressing thumb rises sharply to the haptic threshold; a brush is a short
  low blip. `p_at_button` tells a thumb press from a finger press.
- **Capacitive pads (legacy, PTP).** `m_force = 0` tells the trees that
  `p_*` is area, so the same columns act as a size signal. No firmware flag
  on legacy pads: position, motion, timing and the other contacts carry it.
- **Size but no force (most touchscreens).** `maj_*`, `min_max` and
  `aspect` (a thumb or holding finger is elongated, a palm large); edge
  truncation is learned (a holding finger at the bezel reports a smaller
  blob than it is).
- **Everything, on every class.** The other contacts (Schwarz et al.'s
  strongest feature is the distance to the other touches): `nearest_mm`,
  `nearest_down_mm`, `nearest_dy_mm` (a thumb rests below the pointing
  finger), `nearest_speed`, `others_moving`/`others_still`, landing order
  and gaps, and in stage 2 what the model thinks the others are
  (`o_point`, `o_thumb`, `o_palm`, `nearest_role`). Keyboard timing (only
  press times) where there is a keyboard. Firmware confidence is an input,
  not a verdict.
- **Semi-MT pads.** Positions are a bounding box; the mask bit lets the
  model lean on counts and timing. They have at most two slots.

## Deployment consequences

- libinput 1.31 disables `ABS_MT_PRESSURE` on pressure pads before plugins
  see frames, so a force pad needs the generated quirk
  (`50-luma-touch-roles.quirks`: `-INPUT_PROP_PRESSUREPAD`,
  `AttrPressureRange=0:0`, palm and thumb pressure thresholds 0). libinput
  then still ignores pressure and only the plugin reads it. Without the
  quirk the plugin runs with `m_pressure = 0`.
- libinput only honours `MT_TOOL_PALM` when the device has that axis
  (`tp->palm.use_mt_tool`). `enable_evdev_usage` refuses `EV_ABS`, so the
  plugin adds the axis with `set_absinfo` when the device is created
  (before libinput configures it). Verify that on a PS/2 pad before the
  legacy class ships.
- On touchscreens libinput turns a slot that becomes `MT_TOOL_PALM` into a
  touch cancel (`evdev-fallback.c`), which is Chromium's "hold, then
  cancel" for free.
