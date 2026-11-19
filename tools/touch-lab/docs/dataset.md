# Recording and dataset format

Version `luma-touch-lab/1`. All times are CLOCK_MONOTONIC nanoseconds on the
recording machine; `boot_id` scopes them.

## Directory

```
~/.local/share/luma-touch-lab/
  sessions/<YYYYMMDD-HHMMSS>-<boot8>/
    session.json                 header (also the first line of every chunk)
    stream-000001.jsonl.gz       records, one JSON array per line
  labels/<YYYYMMDD>.jsonl        labels, written by the user side only
  control/status.json            recorder heartbeat; control/pause, control/stop
  settings.json                  touchpad settings libinput mirrors
```

A new session starts with each recorder start and each new day.

## Records

| Tag | Shape | Meaning |
|---|---|---|
| `H` | `[t, "H", {...}]` | Header: devices, report descriptor, feature reports, evdev absinfo, libinput flags, clock offsets |
| `h` | `[t, "h", "<hex>"]` | One hidraw report, raw (report ID first) |
| `e` | `[t, "e", kernel_us, [[type, code, value], ...]]` | One touchpad evdev frame up to SYN_REPORT; `kernel_us` is the kernel timestamp on CLOCK_MONOTONIC |
| `l` | `[t, "l", "text"]` | A `libinput debug-events --verbose` line, key events removed |
| `p` | `[t, "p", "text"]` | The same from a libinput running shadow plugins (`--shadow-plugins DIR`) |
| `k` | `[t, "k", n]` | A key press on keyboard `n` (header `devices.keyboards[n]`, with `internal`), `t` rounded to 10 ms |
| `s` | `[t, "s", {"event": ...}]` | Recorder status: libinput start/exit, evdev drops, pause/resume, device changes, stop |

`t` is when the recorder read the record. HID reports also carry the device's
scan time (100 µs units, wraps every 6.5 s); `contacts.py` unwraps it.

## Labels

One JSON object per line: `{"v": 1, "t": ns, "boot_id": ..., "kind": ...}`.

| kind | Fields |
|---|---|
| `mark` | `mark` (`wrong` or `almost`), `window_ns` (3 s), `source` (`shortcut` or `app`) |
| `run` | `run`, `phase` (`begin`/`end`), `mode` (`guided` or `free`) |
| `scenario` | `run`, `scenario`, `trial`, `phase` (`begin`/`end`/`discard`), `intent`, `outcome` |
| `contact` | `run`, `scenario`, `trial`, `down` (touch-down ns), `contact_id` (HID contact ID), `label`, `class`, `how` (`rule:<rule>`, `drill:<rule>` or `correction`), and from the active session `role` (and `predicted` for a correction) |

A `discard` cancels every record of that trial written before it. In the
active session (`run` with `mode: active`, `surface`, `mask`), `scenario`
is the drill id (or `test-round`) and `intent` its category. Roles
(`touchlab/roles.py`): `point`, `second`, `thumb-rest`, `thumb-click`,
`palm`, `brush`; phase 1 labels map onto them (`roles.FROM_LABEL`).

Contact labels (`touchlab/labels.py`): intended `pointer`, `precision`,
`scroll`, `pinch`, `tap`, `tap-2`, `click`, `click-thumb`, `drag-thumb`,
`drag-finger`, `swipe-3`, `swipe-4`; accidental `thumb-rest`, `palm-typing`,
`thumb-drop`, `graze`, `palm-edge`, `hand-rest`.

## Contacts

`contacts.py` builds one track per HID contact (contact ID from tip-down to
lift), with samples `(t, x mm, y mm, force 0..1, firmware confidence, scan
time, button)`. The HID stream is the reference because it includes contacts
the firmware flags and the force of each. Each track is matched to its evdev
slot (same position at the same time; hid-multitouch passes positions
through unscaled), which links libinput's per-touch log lines (they name
slots) and the kernel's MT_TOOL_PALM to the contact.

`dataset.join()` adds labels: guided contact labels by contact ID and
touch-down time (within 40 ms), scenario windows, and marks to every contact
on the pad during a mark's window.

## Features

`features.py` writes one row per contact per horizon N (25, 50, 100, 200 ms).
A row at horizon N uses only data that existed N ms after touch-down: this
contact's samples, other contacts' samples and key presses up to then. That
makes a model trained on horizon N runnable at time N inside libinput.

| Group | Columns |
|---|---|
| Position | `x0_mm`, `y0_mm`, `x0_rel`, `y0_rel`, `edge_{left,right,top,bottom,min}_mm` |
| Time | `n_samples`, `elapsed_ms`, `lifted`, `dt_mean_ms` |
| Force | `f_first`, `f_max`, `f_mean`, `f_last`, `f_std`, `f_rise_per_s`, `f_t_max_ms`, `f_drop` |
| Motion | `path_mm`, `net_mm`, `max_disp_mm`, `straightness`, `speed_{max,mean,last}_mm_s`, `dir_deg`, `still_frac`, `jitter_mm` |
| Firmware | `conf_first`, `conf_all`, `conf_frac`, `conf_first_false_ms` |
| Button | `button`, `button_at_ms` |
| Other contacts | `others_at_down`, `others_max`, `others_landed_since`, `first_on_pad`, `since_prev_down_ms`, `since_prev_up_ms`, `nearest_other_mm`, `others_speed_mean_mm_s`, `rel_y_to_others_mm`, `moving_while_others_still`, `still_while_others_move` |
| Keyboard | `since_key_ms`, `since_key_internal_ms`, `keys_1s`, `keys_300ms`, `keys_window` |

Outcome columns, never inputs: `li_verdict` (libinput: `palm`, `thumb`,
`thumb-suspect`, `finger`), `li_first_ms`, `kernel_palm`, `duration_ms`,
`label`, `label_class`, `label_source` (`guided`, `mark-wrong`,
`mark-almost`, `free-unmarked`, `none`), `scenario`. `schema.json` repeats
all of this with a sentence per column.

`sequences.jsonl` holds each contact's first 32 reports as relative arrays
(`t_ms`, `dx_mm`, `dy_mm`, `force`, `confident`) with the same outcomes, for a
small sequence model.

Training weights are a phase 3 decision. A sensible start: guided labels
weight 1; `free-unmarked` as intended at a low weight; `mark-wrong` contacts
as candidates to review in the report, not as labels.
