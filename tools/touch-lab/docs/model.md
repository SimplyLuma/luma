# The contact-role model

**Status: trained and tested on synthetic data only.** The numbers below
show that the pipeline works end to end and which signals matter. They are
not a measure of accuracy on real hands; that comes from active sessions
recorded on the XPS 14 and the phones, which replace the synthetic data.

## Roles

`point` (pointing finger), `second` (a finger joining it: scroll, pinch,
two-finger tap, swipe), `thumb-rest` (resting thumb, or a holding finger on
a screen), `thumb-click` (thumb pressing the pad), `palm`, `brush`
(graze, knuckle, accidental). `touchlab/roles.py` maps roles to what
libinput does with them.

## Model

- Rows: one per contact per window (25, 50, 100, 200 ms after touch-down,
  or at lift), 71 features (`touchlab/rfeatures.py`, list and meanings in
  `FEATURES`), with NaN for signals the surface lacks and the mask bits as
  inputs.
- Stage 1: multi-class gradient-boosted trees (softmax, depth 4, histogram
  splits, learned default branch for NaN) on everything except the role
  context.
- Stage 2: the same plus the other contacts' stage-1 role probabilities,
  trained on out-of-fold stage-1 predictions (two folds by person).
- Decision: votes accumulate over the windows evaluated so far (weights
  0.6/0.8/1.0/1.2), as in Schwarz et al. (CHI'14).
- Personal retrain: a few depth-3 trees per stage fitted on one person's
  drill and correction data, on top of the shipped model.
- Export: plain Lua tables plus a dozen-line evaluator
  (`gbdt.to_lua`, `plugins/luma-touch-roles.lua.in`).

## Data

`touchlab train` builds:

- synthetic active sessions (`touchlab/synth_roles.py`): simulated people
  (force, size, speed, thumb posture) on a sampled device of every class
  (pad size, report rate and which signals it has), plus the XPS 14's
  exact signal set; every drill; a share of each role drawn from the hard
  end (thumbs landing high, small palms, still pointing fingers, late
  second fingers). Test people are held out from training.
- the phase 1 HID replay of the XPS 14 descriptor through the real
  decoders (`replay_xps_hid` in the report).
- recorded active sessions with `--recorded --root <private copy>`, on the
  build host only.

## Baseline

`rolemodel.baseline_suppress` is libinput 1.31's behaviour on the same rows:
the firmware palm flag, pressure and touch-size palm thresholds, side-edge
palms, disable-while-typing, and the thumb rules from
`evdev-mt-touchpad-thumb.c` (exclusion below 92%, pressure/size thumbs below
85%, speed-based and contextual thumbs). "Baseline, whole contact" also
lets it re-decide when later touches land, which is when libinput catches
a resting thumb. The comparison is the track/ignore call, since libinput
has no roles.

## Results (synthetic)

`touchlab train --users 12 --seconds 20 --rounds 40 --depth 3` on the build
host: 540 training scenes (53492 stage-1 rows), 180 test
scenes from people held out of training, 71 s to train both stages
(240 + 240 trees, 6618 nodes; the Lua plugin is
235 KB). Track/ignore is the call libinput can act on; the baseline is the
libinput-style rules above.

| Window | Contacts | Role (one window) | Role (votes so far) | Track/ignore | Baseline at window | Baseline, whole contact |
|---|---|---|---|---|---|---|
| 25 ms | 4830 | 89.7% | 89.7% | 95.2% | 71.6% | 77.7% |
| 50 ms | 4830 | 91.8% | 92.0% | 96.3% | 73.4% | 78.5% |
| 100 ms | 4830 | 95.3% | 94.5% | 97.6% | 74.4% | 79.1% |
| 200 ms | 4830 | 97.8% | 97.0% | 98.7% | 75.8% | 79.4% |

| Surface | 25 ms votes | 100 ms votes | 200 ms track/ignore | baseline |
|---|---|---|---|---|
| force-pad | 89.4% | 94.0% | 98.4% | 73.5% |
| force-pad:xps14 | 88.6% | 93.0% | 98.4% | 76.3% |
| legacy-clickpad | 86.9% | 93.0% | 98.4% | 77.2% |
| ptp-clickpad | 89.0% | 93.5% | 98.6% | 80.9% |
| touchscreen | 94.5% | 98.7% | 99.8% | 71.3% |

| At 100 ms | Model | Baseline at window | Baseline, whole contact |
|---|---|---|---|
| Resting thumb tracked (moves the pointer / breaks a scroll) | 0.4% | 90.8% | 63.7% |
| Second finger dropped (scroll or gesture breaks) | 0.0% | 2.0% | 2.2% |
| Pointing finger dropped | 4.5% | 5.3% | 7.5% |
| Clicking thumb tracked (jitters the pointer, turns a click right) | 0.0% | 43.2% | 23.2% |

Depth 4 with 80 rounds scores about one point higher on the synthetic test
(95.8% role and 98.4% track/ignore at 100 ms) at twice the size and cost.

### Transfer to other hands (the warning sign)

The phase 1 replay (a different hand generator, the real XPS 14
descriptor, the real HID decoders, 90 labelled contacts) is the only
data the model was not built around, and it scores much lower:

| Window | Role (votes) | Track/ignore | Baseline at window | Baseline, whole contact |
|---|---|---|---|---|
| 25 ms | 42.2% | 63.3% | 85.6% | 88.9% |
| 50 ms | 44.4% | 65.6% | 85.6% | 88.9% |
| 100 ms | 50.0% | 70.0% | 85.6% | 88.9% |
| 200 ms | 56.7% | 74.4% | 78.9% | 88.9% |

Across training runs this swings from about 63% to 93% track/ignore
(a smaller local run reached 91% at 100 ms; depth 4 reached
76%). The trees learn the generator's hands. This is what real
active sessions have to fix, and why the plugin stays in shadow mode until
the model is trained on them.

### Latency

| | Per evaluation (features + both stages) | Per contact (4 windows) |
|---|---|---|
| Lua 5.4 (liblua via ctypes, build host) | 126 µs | 505 µs |
| Python (app test round) | 149 µs median, 174 µs p95 | |

Evaluations happen only when a window ends (or at lift), not per frame.
A decision exists 25 ms after touch-down, before libinput commits to a tap
(about 180 ms) and, for a resting thumb, before the pointing finger lands.

### Personal retrain

synthetic person: heavy hand (force x1.4), thumb resting at 72% of the pad height: retrained on one session (330 labelled
contacts) in 0.52 s, tested on a second session:

| Window | Role before | Role after | Track/ignore before | after |
|---|---|---|---|---|
| 25 ms | 87.3% | 90.2% | 93.2% | 94.5% |
| 50 ms | 88.6% | 90.2% | 93.8% | 95.4% |
| 100 ms | 91.2% | 92.2% | 96.1% | 96.7% |
| 200 ms | 96.4% | 96.7% | 98.0% | 99.0% |
