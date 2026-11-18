# Touch Lab

Data collection for a learned touchpad contact classifier: which contacts are
intended (pointing, scrolling, tapping, clicking, gestures) and which are
accidental (a resting thumb, a palm while typing, a graze). It records the
touchpad at the HID level, libinput's own decisions for the same contacts and
key-press timing, collects labels from everyday use and from guided
scenarios, and turns recordings into per-contact feature vectors.

Phase 1 of four: collect (this tool), record, train, ship the model in
libinput as a Lua plugin from a Luma package ([docs/deployment.md](docs/deployment.md)).

Recordings stay on the machine they were made on, or on private storage for
training. They never go into this repository.

## Parts

| Part | What it does |
|---|---|
| `touchlab/recorder.py` | Root-started recorder: hidraw reports, touchpad evdev frames, `libinput debug-events --verbose`, key-press times. Writes compressed chunks as the user. |
| `touchlab/cli.py` (`touchlab`) | User command: status, pause/resume/stop, `mark`, settings mirror, synthetic sessions, reports, features, `libinput record` export. |
| `touchlab/app.py` (Touch Lab) | LumaUI app: live view of every contact, a guided pass and a "just use it" mode. |
| `touchlab/report.py` | Self-contained HTML report: heat maps, force curves, contact timelines against the keyboard, firmware/libinput/label agreement, session stats. |
| `touchlab/features.py` | Per-contact feature vectors over the first N ms, and short sequences for a sequence model. |
| `touchlab/synth.py` | Synthetic sessions with the real report descriptor, for tests and the report before any recording exists. |
| `touchlab/shadow.py` | A private libinput context that loads Lua plugins from one directory, for shadow runs (`--shadow-plugins`). |
| `plugins/shadow-probe.lua` | A probe plugin that exercises the palm-marking path; not a classifier. |
| `e2e/uhid_touchpad.py` | Plays synthetic reports through a real kernel (UHID) to check the recorder, hid-multitouch and libinput end to end. |

## Privacy

- Keyboards: only the time of each key press, rounded to 10 ms, and which
  keyboard it came from. The key code is compared against a range and dropped
  in the same statement; it is never stored, sent to the writer or logged.
  libinput's key events are removed from its output before they leave the
  recorder. Releases and auto-repeat are not recorded.
- The typing box in the app counts presses for its prompt and keeps nothing.
- Nothing is sent anywhere. Data lives in `~/.local/share/luma-touch-lab`
  (mode 0700).

## Install (user only)

```sh
tools/touch-lab/install.sh            # copies to ~/.local/share/luma-touch-lab/app
tools/touch-lab/install.sh --remove   # removes the launcher and shortcuts, keeps data
```

The installer adds a launcher (Touch Lab), two shortcuts, and mirrors the
touchpad settings into `settings.json` so the recorder's libinput instance
behaves like the compositor's.

## Recording

The touchpad's hidraw and evdev nodes are root-only, so the recorder starts
as root in a transient unit, opens the devices, and forks a writer that drops
to the user before it touches any file. It does not grab or write to any
device; if it stops, input is unaffected. Print the exact command with:

```sh
~/.local/share/luma-touch-lab/app/bin/touchlab sudo-command
```

It runs `systemd-run --unit=luma-touch-lab` with a read-only filesystem except
the data directory, no network, a 256 MiB memory cap, a 20% CPU quota, idle
I/O priority and only the capabilities needed to drop privileges. Stop it
without sudo with `touchlab stop`; pause with `touchlab pause`.

Limits: chunks rotate at 48 MiB of JSON (about 5 MiB compressed) or 15 minutes;
recording pauses at 4 GiB of sessions or when the disk has under 20 GiB free.
A crash loses at most the last 5 seconds.

## Labels

| Shortcut | Meaning |
|---|---|
| Super+Alt+W | That was wrong: the last 3 seconds |
| Super+Alt+A | That was right, but almost wrong |

Both run `touchlab mark wrong|almost`, which stamps CLOCK_MONOTONIC before
anything else and shows a short notification.

The app's guided pass runs every scenario twice in random order (34 trials,
about five minutes) and names each contact by rule
([touchlab/scenarios.py](touchlab/scenarios.py)); "Again" discards a trial.
"Just use it" marks a stretch of normal use, where unmarked contacts count as
intended.

## Reports and features

```sh
touchlab synth                         # a synthetic session under ~/.local/share/luma-touch-lab/synthetic
touchlab report --root ~/.local/share/luma-touch-lab/synthetic
touchlab report                        # every recorded session
touchlab features --out DIR            # features-{25,50,100,200}ms.csv, sequences.jsonl, schema.json
touchlab export-record SESSION --out s.yml   # for `libinput replay`
```

Formats and every feature column: [docs/dataset.md](docs/dataset.md).
The XPS 14 touchpad's reports: [docs/xps14-touchpad.md](docs/xps14-touchpad.md).

## Tests

```sh
python3 -m unittest discover -s tools/touch-lab/tests
```

`e2e/uhid_touchpad.py` needs root and a machine with no graphical session;
it is run by hand when the recorder or the decoders change.
