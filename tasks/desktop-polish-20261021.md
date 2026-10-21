# Desktop updates — 2026-10-21

Status: source qualified; release pending drawer review. Owner: luma-dev-2. Source task: desktop-polish-20261021.

Nightly 20261020.1 is published. This is a separate update batch; no new image
has been published from these changes.

## Installer

Merged separately in PR 28. Forward the selected device map from the manual
screen through applyPlan into storage planning. Continue previously raised a
JavaScript TypeError resolving the EFI partition's parent disk. The real manual
screen regression reproduces the failure before the fix; four same/separate
disk and startup-order cases pass afterwards. Normal Atlas20 packaging passed
all 39 JavaScript tests, Python/viewer checks and bundle/RPM checks. Native
D-Bus transport is substituted in the regression; a new physical installation
or ISO is not established by these tests.

## Desktop implementation and evidence

- Shell120 (including the Shell119 fixes): solid login surfaces and even power-menu spacing; respect dock
  battery percentage; one-percent brightness keys and lock/idle input; Apps and
  Downloads shortcuts with a searchable drawer. Normal package checks and
  native Shell fixtures passed. Actual login, drawer and Downloads screenshots
  captured. Volume keys and repeat checked in the installed disposable desktop;
  physical battery/backlight hardware is not qualified by synthetic fixtures.
- Settings58: Night Light start/end time controls with manual/automatic state,
  12/24-hour editing and validation; live Applications/Downloads/visibility
  controls preserving explicit empty lists and custom pin order. Normal RPM
  stages and native component checks passed. Installed Wayland GUI passed all
  five shortcut saved-state cases with real VM pointer input. The outer GUI
  runner returned failure after its successful assertions because cleanup tried
  stopping an already-unloaded transient unit; cleanup is now idempotent.
- Filer74 and Terminal7: hover Open/Open With menus, directory-aware Terminal
  actions and D-Bus activation. Normal RPM/Flatpak builds and native actions
  passed. Installed Filer with the normally assembled and maintained-key-signed runtime
  populated Applications in 0.952 seconds including observation overhead, showed
  only Name, and survived all three focused-search navigations. Platform and
  Locale signatures were independently checked with the maintained remote key
  before normal offline installation; both retained the maintained origin.
- Platform109: disconnect search-control callbacks when their retired item is
  finalized. The native regression fails before and passes after the fix;
  normal platform checks passed. Runtime publishing also copies metadata before
  normalizing permissions; five permission/link tests passed.
- Tiling27: leaving a monitor retained the last cursor/activation state, so
  returning to the same point could skip rebuilding a closed selection and
  leave a zero-height preview. The native regression reproduced this before
  the fix; 128 drag/drop checks passed afterwards across four five-monitor
  geometry/scale combinations. Normal package checks passed. This qualifies
  the reproduced trigger, not every physical monitor configuration.
- Codec delivery: evidence-backed design proposal only. Native and isolated
  runtime decoding of the test sample worked with a separately obtained Cisco
  binary. Licensing, distribution and full nested-sandbox delivery remain
  explicit production gates; no codec integration is enabled.

## Remaining release gates

The Applications drawer still requires the user's screenshot appearance
confirmation. Retained WebKit RPMs were recovered and all four engine payloads match the
GPG-verified published runtime exactly. The normal exact-input collector passes with the pinned runtime and development
packages. Normal Platform109 and SDK builds, private maintained-key signing and
installed Filer qualification passed. Every assembled engine payload matches the
signed baseline, with no pin override. The earlier diagnostic runtime remains
ineligible; the new passing candidate uses the normal producer and publisher.
Reusing the published engine does not establish qualification of the separate
pending native-touch source changes. Final public OS/app update and image
qualification remain separate from source/package validation.

## Drawer review revision

Shell120 replaces the Applications and XDG Downloads shortcut marks with authored
blue artwork derived from Filer's plate, with raised grid and download symbols.
The Applications header uses one title line and more space before search. Equal
cell and spacer dimensions keep partial rows aligned. Custom folder icons and
keyboard/search behavior remain covered. The installed Shell119 regression
reproduced the header, artwork and partial-row failures; revised packaging and
final screenshot qualification passed. The final normal RPM build passed all
package checks, and its installed native fixture passed 47 checks in both
appearances. Actual 1280×800 desktop captures show all 28 installed apps,
including the final row labels. The RPM contains the artwork license and its
correct license expression. Updated appearance still awaits
the owner's confirmation before release.
