# Atlas: Luma's installer front end

Atlas is the interface of Luma's installer. It asks Fedora 44's eight
installer questions in the same order, one per screen, in front of Anaconda's
own D-Bus modules, with a *Get more apps* step (ADR-028, Depot, section 14)
before the review:

| # | Step | Anaconda owner |
|---|---|---|
| 1 | What language should Luma speak? | Localization, Boss |
| 2 | Where in the world are you? | Timezone |
| 3 | Where should Luma live? | Storage (disk selection, scenarios, partitioning) |
| 4 | Should we lock this drive? (or, by hand, *Which partition goes where?*) | Storage (automatic request, LUKS2) |
| 5 | Who is using this computer? | Users |
| 6 | Would you like more apps? | none: recorded for Depot's first-boot provisioning |
| 7 | Here is what will happen. | Storage device tree of the applied plan |
| 8 | Setting up Luma. | Boss installation task |
| 9 | Luma is ready. | (after the medium is ejected) |

Atlas uses Luma's native installation design. The
[public build guide](../../BUILDING.md) explains how its source and RPMs enter
installation-media composition.

## What is reused and what is Luma's

Atlas is built on [anaconda-webui](https://github.com/rhinstaller/anaconda-webui)
release **68** (commit `a8745f07`), the release Fedora 44 pairs with
anaconda-core 44.30. Exact inputs are in [`upstream/inputs.env`](upstream/inputs.env).

Reused from anaconda-webui 68 (LGPL-2.1-or-later), with their copyright
headers kept:

- `src/apis/` – every D-Bus API client, unchanged (`payload_source.js` is Luma's addition).
- `src/actions/`, `src/reducer.js`, `src/helpers/` – the store and helpers,
  unchanged.
- `src/hooks/Storage.jsx` – the storage-plan model and partitioning creation
  (one import path changed).
- `src/scenarios/` – scenario availability checks; the Cockpit storage editor
  scenario is dropped, the free-space scenario reports a shortfall instead of
  opening a reclaim dialog, and a Luma system counts for *Reinstall*.
- `runtime/` – `cockpit-coproc-wrapper.sh` and `webui-cockpit-ws.service`,
  unchanged; `webui-desktop` with two changes marked *Luma change*: the viewer
  runs as the unprivileged installer user, and in the lorax-built runtime the
  installer's Wayland and Xwayland sockets are private to root, so it opened
  no window. The script grants that user the Wayland socket with ACLs and
  passes its absolute path, `GDK_BACKEND=wayland` and a runtime directory.
  And the viewer is started through `libexec/atlas-viewer` (below).
- `src/contexts/Common.jsx` – the same contexts without PatternFly.

Luma's own:

- `src/atlas/` – the room and its components (`install-option`,
  `install-switchrow`, `install-field`, `install-warn`, `install-summary`,
  `install-meter`, `install-phase`, the dots).
- `src/steps/` – the eight steps, the manual mount point step, the error room.
- `src/model/` – plain-language rules with unit tests: the Layout line from
  the real plan, drive sentences, *Detected*, review warnings, field messages,
  progress phases, the clock line, low battery.
- `libexec/atlas-probe` – read-only facts D-Bus does not give (installer
  medium, firmware language, battery, whether stage 2 runs from memory, what
  the OSTree payload is and whether it configures zram, the medium's channel
  and payload source, network connectivity).
- `libexec/atlas-media` – eject the medium before step 8; save logs after a
  failure.
- On installation failure, **Details** expands the human error returned by
  Anaconda's `Task.Finish`, without needing another USB drive. The primary
  failure message, **Save the log**, and **Restart** remain available. Details
  redact credentials and exclude full tracebacks; complete diagnostics remain
  in Anaconda's logs. The sanitized message and failure phase survive a viewer
  reload because Anaconda consumes a stopped task's saved exception when
  `Finish` raises it. This reports the backend error, rather than guessing
  why a particular computer failed.
- `libexec/atlas-viewer` – starts and watches slitherer (a Qt Quick window
  with a Qt WebEngine view). It renders in software by default, restarts the
  viewer in safe mode when Atlas draws no first frame (`src/system/viewer.js`
  writes a heartbeat from drawn animation frames), and otherwise shows
  `libexec/atlas-viewer-fallback`, a GTK error screen in Atlas's style with the
  log locations, *Try again*, *Save the logs* and *Restart the computer*. Boot
  options `inst.luma.viewer=software|gpu|safe|fallback` and
  `inst.luma.viewer.first-frame=SECONDS`; log `/tmp/luma-atlas-viewer.log`.
  Tests: `test/viewer/test-atlas-viewer.sh`.
- `config/90-luma-atlas.conf` – Anaconda drop-in: no geolocation, slitherer.
- `src/data/app-collections.json` – the launch collections in catalog schema
  4's collection shape, a stand-in until the seed catalog carries them; also
  installed to `/usr/share/luma-installer-atlas/` for the kickstart to validate
  choices against.

No PatternFly and no Sass. React 18.3.1 is the only runtime dependency.

## Honesty rules the code keeps

- Nothing before step 6 writes to a drive: steps 1–5 only set Anaconda module
  properties and build a plan in memory. Anaconda writes when Boss runs the
  installation task.
- *Detected* appears only for a language derived from the firmware's
  `PlatformLang`; Anaconda's default language is not called detected.
- The installer medium is shown, disabled, as *this is your installer*.
- The Layout line lists the applied plan's mount points and btrfs subvolume
  names; a swap partition appears only if one is planned; zram is mentioned
  only when the payload contains `/usr/lib/systemd/zram-generator.conf`.
- A logind inhibitor for sleep and the lid switch protects the install without
  making a close-lid promise. The duration says
  *usually under ten minutes*, because no payload-size estimate exists.
- The last step appears only after `atlas-media eject` reports the medium
  gone, which it refuses to do while stage 2 still reads from the medium.
- The review names what is installed, its channel and where it comes from
  (*from this USB drive* or *downloaded as it installs*). A download without
  the internet is blocked on the review step. The completed install shows only the USB-removal instruction and restart
  action. Preview enrollment remains a separate Depot/update concern.
- *Get more apps* preselects nothing and downloads nothing. The installer
  kickstart writes `/etc/luma/first-boot-apps.json` (mode 0644) with only
  collection ids the packaged catalog knows.

## Build and test

```sh
./fetch-build-inputs.sh          # Cockpit pkg/lib and Figtree, pinned and verified
npm ci
npm test                         # model unit tests
node build.js                    # product bundle in dist/
ATLAS_PREVIEW=1 node build.js    # mocked backend in dist-preview/ (never packaged)
```

Visual walk of every step against the mock, and axe-core on every step
(`test/visual/axe-walk.mjs`); `test/visual/vm-walk.mjs` walks a real installer
in a VM. Visual walk (Paper and Ink, 1440×900 and
1024×640, plus error, offline, low-battery, contrast and no-inhibitor states):

```sh
python3 -m http.server 8741 --bind 127.0.0.1 --directory dist-preview &
PLAYWRIGHT_CHROMIUM=/path/to/chromium node test/visual/capture.mjs /tmp/atlas-shots
```

The package is built by `scripts/packages/build-luma-installer-atlas.sh`; the
installer ISO by `scripts/install/atlas-iso/build-installer-iso.sh`
(see the [installation-media build guide](../../BUILDING.md)).

## Surfaces

Ink is the default. Paper follows an explicit `prefers-color-scheme: light`; Slate at full
contrast follows `prefers-contrast: more`. The boot option
`inst.luma.surface=paper|ink|slate|contrast` overrides both.
