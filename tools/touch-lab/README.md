# Touch Lab

A learned contact-role classifier for every touch surface Luma runs on. Each
contact gets a role: pointing finger, second finger, resting thumb, clicking
thumb, palm or brush. What libinput does with it follows from the role: a
resting thumb neither moves the pointer nor breaks a scroll, and a pressing
thumb still clicks (left, not right).

The tool records the surface (HID reports where there are any, evdev
frames otherwise), libinput's own decisions and key-press timing; labels
contacts through a 10-minute active session; trains a small two-stage tree
model; and exports it as a libinput 1.31 Lua plugin
([docs/deployment.md](docs/deployment.md)). Surface classes and what the
model gets from each: [docs/surfaces.md](docs/surfaces.md). The model and
its (so far synthetic) accuracy: [docs/model.md](docs/model.md).

Recordings stay on the machine they were made on, or on private storage for
training. They never go into this repository.

## Parts

| Part | What it does |
|---|---|
| `touchlab/recorder.py` | Root-started recorder: hidraw reports, touchpad evdev frames, `libinput debug-events --verbose`, key-press times. Writes compressed chunks as the user. |
| `touchlab/cli.py` (`touchlab`) | User command: status, pause/resume/stop, `mark`, settings mirror, synthetic sessions, reports, features, `libinput record` export. |
| `touchlab/app.py` (Touch Lab) | LumaUI app: the active session (drills, test round with live role dots and corrections, retrain, follow-up drill), a live view of every contact and a "just use it" mode. |
| `touchlab/surfaces.py` (`touchlab probe`) | Surface classes, capability probe, feature mask; libinput test devices and quirks mined into `data/litest-surfaces.json`. |
| `touchlab/drills.py` | The 12 drills, per-class text, the rules that give each contact its role, and the follow-up choice. |
| `touchlab/rfeatures.py` | Role features at 25/50/100/200 ms with masks; mirrored line for line in the plugin. |
| `touchlab/gbdt.py`, `touchlab/rolemodel.py` (`touchlab train`) | Multi-class boosted trees (pure Python), the two-stage model, votes, the libinput-style baseline, evaluation, personal retrain. |
| `touchlab/synth_roles.py` | Synthetic active sessions on every surface class, with gold roles. |
| `touchlab/plugin.py` (`touchlab export-plugin`) | Generates `50-luma-touch-roles.lua` and its quirks from a model. |
| `plugins/luma-touch-roles.lua.in` | The plugin template: features, tree evaluator, slot handling. |
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

## The active session

About ten minutes, in the app ("Start the session"):

1. **Drills** (12, 30 to 60 s each): pointing slowly and on targets; thumb
   resting while pointing and while tapping (the right thumb down, index or
   middle finger pointing); thumb click; thumb holds the click while a
   finger drags; two-finger scroll; scroll with the thumb down; typing a
   prompted sentence (press timing only, never keys); heel of the hand;
   knuckle brushes; reaching across and gripping. The drill defines the
   roles, and a rule ([touchlab/drills.py](touchlab/drills.py)) tells apart
   the few it allows, so every contact is labelled without the user doing
   anything. "Again" discards a drill; contacts a rule can't place stay
   unlabelled.
2. **Learn**: a few personal trees on top of the shipped model (seconds,
   pure Python), saved to `models/personal.json`.
3. **Test round** (60 s): live dots coloured by predicted role with its
   confidence. Keys 1 to 6 (or the bar) correct the last touch.
4. **Learn** again with the corrections, then a 30 s **follow-up drill**
   aimed at the most confused pair of roles or the least certain role.

Drill text adapts per class: touchscreens get holding, reaching and edge
drills instead of clicks and typing. "Just use it" marks a stretch of normal
use, where unmarked contacts count as intended.

## Reports and features

```sh
touchlab synth                         # a synthetic session under ~/.local/share/luma-touch-lab/synthetic
touchlab report --root ~/.local/share/luma-touch-lab/synthetic
touchlab report                        # every recorded session
touchlab features --out DIR            # features-{25,50,100,200}ms.csv, sequences.jsonl, schema.json
touchlab export-record SESSION --out s.yml   # for `libinput replay`
touchlab probe                         # this machine's touch surfaces: class, signals, what's missing
touchlab train --out DIR               # synthetic + replay (add --recorded for active sessions under --root)
touchlab export-plugin --model DIR/roles-model.json --out DIR/plugin
```

Recorded sessions stay on the machine or in the private store on the build
host; `touchlab train --recorded --root <private copy>` runs there.

Formats and every feature column: [docs/dataset.md](docs/dataset.md).
The XPS 14 touchpad's reports: [docs/xps14-touchpad.md](docs/xps14-touchpad.md).

## Tests

```sh
python3 -m unittest discover -s tools/touch-lab/tests
```

The Lua tests (feature and model parity with the Python side, and the
plugin's slot handling) run where liblua 5.4 is installed and skip
elsewhere. `e2e/uhid_touchpad.py` needs root and a machine with no graphical session;
it is run by hand when the recorder or the decoders change.
