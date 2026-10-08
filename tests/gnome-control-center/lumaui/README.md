# Settings LumaUI port

0057 exports upstream `0522ad1b92956d800e5726f0d0ce32e8edbdfb51`.
An actual empty native PermissionStore now shows "No apps have asked for access
yet" inside the shared details group. The private-bus native view test checks
that message, zero application controls/writes, both desktop and phone widths,
and closed-window root release. Guarded build and both permission targets PASS
under fatal warnings. This is a source-level empty state fix, not a conform
comparison or package/VM acceptance claim. Logs:
`reports/settings-second-account-0057-{build,focused}.log`.
All 31 appended patches through0057 replay to exact upstream tree
`c8cfd9acfec02988036aa3edd44bd92e89e7b2d3`; the scenario lists all57
active patches and omits inactive `0024-users`.

0056 exports upstream `f750cebcb34caf786c24402d5f6e20b421fae1b6`.
The actual upstream Camera and Location pages and a new Microphone Privacy
route bind to the native PermissionStore observer and scoped preference writer.
They use the production GNOME settings schemas, an asynchronous session bus,
application metadata, and shared LumaUI details rows; fixture startup remains
separate. PermissionStore owner changes, external edits, failed writes, late
replies after destruction, and global camera/microphone/location switches are
covered with a private bus and memory settings. Focused native rebuild and the
seven adapter cases plus desktop/phone live view and upstream route cases PASS
with `G_DEBUG=fatal-warnings`. Three existing Privacy/Screen lock view cases and
the installed-spec hostname, TrackPoint, and Scroll Speed runtime assertions
also PASS. The thirty appended patches through0056 replay to the identical
upstream source tree `379c932796726c2db96819e18afc9a786788809a`.
At the 0056 checkpoint, the scenario listed the installed spec's 26 active patches plus0027–0056
explicitly, excluding inactive `0024-users`; canonical source selection is
landed, but installed server deployment and a new full conform gate remain
unverified. Other live routes, package RPM/VM, full inventory, and visual
acceptance are still open. Logs: `reports/settings-second-account-{permission-build,permission-focused,view-regressions,package-runtime,0056-replay}.log`
under `~/Documents/LumaDesign/`.

0055 exports `5820e1f2679ef5642964f687f05b5833cf72b248` and connects the
actual upstream CcScreenPage to a live Screen lock view. It accepts dedicated
native preference observers, loads no fixture model, follows external edits,
preserves uncommon existing durations and uses the scoped backup/edit adapter.
The navigation trail returns through the existing upstream navigation action.
Guarded build07 completed0; focused03 completed0/full log inspected:
live view at1180/390, actual CcScreenPage route/Back, Privacy fixture regression,
eight preference cases, preview identity, chooser and retained package assertions
all PASS. Pending work does not retain roots past the unchanged100ms assertion.
Replay matches tree `a57588b7b1532b751cf2454f44081c232626b47e` exactly.
Logs: `~/Documents/LumaDesign/reports/settings-daytime-live-lock-{build,focused}-20260926-{07,03}.log`.
Earlier build01/02/06 and focused01/02 failures remain preserved; corrected
test composition installs the window host after setting its child and verifies
the host allocation before testing desktop menu/phone drawer selection.
No host preference write, preview, full-suite or conform PASS is claimed.
Remaining routes, production window chrome and measured parity remain open.

0054 exports tested upstream `040c63250730ae438b3cf4167db19f2aebd830f7`.
Its PermissionStore adapter reads the existing native owner without activation
and changes only an existing selected application permission after durable
backup and a fresh read. Other applications, opaque entry data and location
timestamps remain intact. Competing edits and unknown values fail explicitly.
Cancellation before dispatch prevents writes; after dispatch the bounded reply
determines success, and an error can leave the write outcome unknown.
Seven private-bus cases PASS, including timeout, owner loss, native refusal,
corrupt recovery and cancellation after dispatch. Preferences, search edits,
chooser routing, preview identity, both-width Privacy callbacks and retained
package assertions also PASS. Full inspected log:
`~/Documents/LumaDesign/reports/settings-daytime-permissions-focused-20260926-02.log`.
Independent replay matches tree `8acf54c5ae0dcb519f979af62129bbf3b629fccd`.
The check entry point now retains all original targets plus permissions (12).
No new full-suite/server PASS is claimed: the previous shared Displays failure
and missing Session artwork remain open. Production view binding remains open.

Settings stays the patched GNOME Control Center 50.4 application. Work is
exported as append-only patches; upstream backends and existing stored values
remain owned by GNOME Control Center.

## Current checkpoint evidence, 2026-09-26

The preceding tested app checkpoint is `d88b358f071dc0491173247de3db752c84d57881`.
Append-only 0050 exports Apps fixture source `3308b1c`; it adds the Apps list and
all 18 detail routes, validated category/permission/storage metadata, real public
navigation and destructive dialogs, and scoped fixture actions. Focused Apps
check 1052, all 38 desktop/phone native smokes 1055, and the retained installed
spec runtime checks 1056 completed with return code 0 and inspected full logs.
Fresh daytime verification against the compiled 0050 source also completed
with return code 0: exact retained package runtime assertions and all 38 native
Apps desktop/phone mapped-artwork, width and closed-window smokes. The full log
is `~/Documents/LumaDesign/reports/settings-daytime-apps-checkpoint-20260926-01.log`.
Independent replay through 0050 has the exact source tree
`7887e343370fd35670d87610c3d147adedfc6e9e`, matching upstream `3308b1c`.
Accessibility is exported separately as0051, described below. The daytime continuation supersedes the
historical executor expiry and submission pause; guarded direct helpers resume.
These checks do not establish live application launch, permissions, cache removal,
uninstall, RPM `%check`, VM launch or visual parity.

Full unskipped native check 1080 returned 1: Apps passed at both widths, but the
widget target subsequently failed at the shared text-only Displays action API.
The overall suite passed only 10/11 targets. Earlier full runs 1054 and 1068
failed the original 100ms Apps root-release assertion intermittently; failure-only
observations preserve that assertion and do not establish a fix. All failures
remain acceptance failures. The authorized server attempt 0938 returned 3 before
capture because inactive 0024 was applied before active 0025; request 05 tracks
the missing active-series contract. No four-variant app PASS or safe installed
preview has been completed. Inventory and full live adapter wiring remain open.

0051 exports accessibility source `8c7661d8fff057401afcdde41da189816dcaa8e1`.
All six routes and 31 scoped controls are compiled. Guarded builds completed0,
23 pure fixture cases and both-width accessibility widget regression PASS,
all five native property/service cases PASS, and all12 accessibility native
desktop/phone smokes plus exact retained package runtime assertions PASS.
The native Orca action uses its existing ShowPreferences contract, with
cancellation, a five-second bound and no automatic service activation; private
bus tests cover success, cancellation, late replies, timeout and native errors.
Original 100ms root-release assertions are unchanged. The expanded28-case
widget runner has an explicit180-second execution budget: the previous default
30-second run timed out; the full rerun reached Apps/accessibility PASS and
then failed the required shared Displays action assertion at37.83 seconds.
All11 targets still run, with10PASS/1FAIL; no assertion or gate threshold changed.
Logs: `reports/settings-daytime-accessibility-{build,focused,full}-20260926-01.log`
and `reports/settings-daytime-accessibility-{build,native}-20260926-02.log`
under `~/Documents/LumaDesign/`. Independent0051 replay matches tested source
tree `110d9478b883fc155460de180b9ee238ab0636bb` exactly. These are partial
fixture views; live page/write bindings and exact Flash overlay paint remain
open, and no conform or production completion is implied.

0052 exports native preference edit source `76b825236b1c59826267ef74634659866d5261d9`.
It adds explicit asynchronous edits only for the observer's selected native keys,
validates schema types/ranges/writable flags, refuses fixture mode and pending
delayed observers, and changes only the edited key in the same backend.
Immutable durable private backups preserve both effective values and whether
an original user override existed; relocatable paths identify separate records.
The extracted backup helper preserves the existing Tracker recovery format.
Native callers are restricted to `~/.local/share/luma/settings/backups`; tests
use actual injected memory backends and temporary directories. No user store
was edited and no production view binding is claimed.
Guarded build completed0; all seven preference cases and the search edit case
PASS, covering reset, unrelated edits, cancellation, corruption, permissions,
identity mismatch, symlinks, locked keys and non-memory backend boundaries.
Retained package runtime assertions PASS. Full unskipped native check completed1:
10/11 targets PASS, Apps/accessibility PASS, then Displays aborts at36.75s on
request10's shared text-only action contract. Logs under LumaDesign/reports:
`settings-daytime-preference-build-20260926-03.log`,
`settings-daytime-preference-focused-20260926-02.log`,
`settings-daytime-preference-full-20260926-01.log` and
`settings-daytime-preference-package-20260926-01.log`.
Replay0052 exactly matches tested tree `d3bf69e1cb0ddebbbdbf606949a4e7fae8d4b10b`.
Full inventory, visual acceptance and production bindings remain open.

0053 exports Privacy source `792b708bf4d9a3ddf71df1ee5ab6a5f21250acaa`.
Eight routes use shared parts, scoped boolean/picker/radio controls, conditional
permission lists and file-history/trash confirmations. Removed controls and
stale dialog callbacks cannot edit a newer page. Sample security facts and
cleanup feedback remain fixture-only; production store/service binding is open.
Actual GNOME lock/privacy/location schema construction, exact types and read
snapshots pass using the isolated memory backend; no real settings write occurs.
Guarded build06 and models01 completed0:24 fixture cases/eight preferences PASS.
Focused Privacy widget04 PASS at both widths8.45s, including the shared phone
drawer and unchanged100ms root teardown. Native window-host composition must
exist before first-menu allocation, as LumaApplicationWindow supplies it.
All16 native smokes ran:14PASS; p-mic desktop/phone fail missing canonical
Session luma-v3-daw.svg(request11). Weather's existing canonical asset is staged.
Full native01 completed1:10/11 targets PASS, Apps/accessibility/Privacy PASS,
then Displays fails shared text-only action assertion at44.25s(request10).
Retained package runtime01 completed0; all full logs under
`~/Documents/LumaDesign/reports/settings-daytime-privacy-*-20260926-*.log`.
Replay0053 matches tested tree `2998c89907a38595eb3109d9a060a0d7ca25a038`.
No full server/conform, actual RPM%check, VM, live-page or production PASS claim.

## Historical integration check status, 2026-09-26

The last verified push ends at 0047 (`6de06805`). Append-only 0048 exports
the tested native source checkpoint `6b9a262`: guarded build 0908 and installed
spec runtime checks 0910 completed with return code 0; full native suite 0912
completed with return code 1, passing 10 of 11 targets. The widget target is
blocked by shared text-only display actions; diagnostic Sound asset setup and
immediate opening portal cancellation remain failures. Foundation `ab06da5e`
was integrated wholesale. Direct capsule SSH/new conform remain paused under
0431; the approved existing host queue supports builds, checks and scoped Git.
Actual RPM %check and VM launch remain unverified. INT needs tested pushed
state by 09:15 UTC; freeze is 09:30. Staging does not establish production DONE.

0049 exports native source `8dbb0c0`: approved Reel artwork lookup and the
supported Night Light toast kind now pass the actual controls. Six actual
native Wi-Fi, Volume by App and Keyboard desktop/phone smokes PASS, including
exact native surface 1180×740 / 390×740, mapped loaded artwork and closed
windows. Fedora Xvfb defaults to 640×480; the private smoke display explicitly
uses 1280×1024, and surface dimensions include the frame which widget content
dimensions omit. Assertions remain strict. The full unskipped native suite
still passes 10/11 with the shared text-only display action API fatal critical.

`verify-app.sh <meson-build-dir> <settings-v70.json>` is a proposed package-check
entry point after `host-build`: it never compiles, runs all app suites serially
inside a private bus/Xvfb display, uses memory settings and refuses missing
build metadata, fixture or native binary. It includes the private native
identity and chooser tests; both PASS on the private bus/display. The copied upstream Fedora
spec contains no `%check`, but the installed `0000-luma-fedora-spec.patch`
adds `%meson_test test-hostname`, generated TrackPoint/Scroll Speed UI/desktop
assertions and a standalone scroll-speed test requiring exactly three `ok`
results. That original check does not include the new LumaUI suites. INT needs
to wire the new entry point into its generated package check after applying
the complete active series; original patch bytes and Release remain unchanged.
Do not report this proposal as an observed package `%check` or VM smoke PASS.

`smoke-app.sh <native-binary> <settings-v70.json> <page> <desktop|phone>` is
the native fixture runtime command, suitable inside the build/VM test
environment. It uses a private bus/display and memory settings. The native
fixture smoke path requires five consecutive mapped/allocated observations
with picture assets loaded, then checks that its application windows close.
It fails on missing assets, unported pages, timeout or a remaining window.
It cannot enable smoke mode for a real-data application. Desktop and 390px
phone runs are both required; this proves neither live behavior nor conform.
`verify-smoke.py <meson-build-dir> <page> [page...]` stages the exact declared
scenario assets in a temporary directory and runs both presentations for every
selected page, recording all failures rather than omitting later phone runs.
Actual VM launch remains unverified by this task.

## Surface to part mapping

- Window and content islands: LumaApplicationWindow, LumaPane; adaptive navigation uses stock nonvisual Adw helpers.
- Sidebar account frame: LumaAccountCard; sidebar foot: LumaSidebarFoot; F9/drawer: LumaSidebarToggle.
- Page heading and captions: LumaTypeLabel; background wash: LumaContentLitHeader.
- Section surfaces: LumaCard; values/copy: LumaFactRow; navigation and Add: LumaDetailsRow and LumaAddRow.
- Page modes and segmented choices: LumaModeSwitch; counts/categories/status: LumaCountBadge, LumaCategoryPill, LumaStatusPill.
- Editable row text: LumaTextField. Switch/radio/range/picker rows need a semantic control API or an app-only composition using kit tokens; never restyle kit parts.
- Inline forms and display keep/revert: LumaActionEditor and LumaActionCenter.
- Picker menus: LumaFloatingMenu/MenuDrawer; destructive confirmation: LumaDestructiveDialog; results/Undo: LumaToast.
- Settings-only diagrams: network signal/QR, display arrangement, wallpaper/shelf/window previews, battery history, pointing-device/keyboard demos, screen-time chart. Compose in Settings using shared parts and tokens; never copy a shared part.

## Reproducible source preparation

Tarball: GNOME Control Center 50.4, SHA256
`5856c73999bedf45e74f73bac3d61e2b5ec1689f8bcf1943737baeee8204fb11`.
The installed `.29` spec patch (`0000`) lists 26 active source patches.
Apply those in spec order with `git am`; wrap plain diffs as temporary emails
and supply the missing author email on the old `0007` mailbox. Existing patch
bytes stay unchanged. `0024-users` is an inactive duplicate; `0025-users` is
active. `0000` is packaging metadata, not an upstream application patch.
The prepared tree is tagged `luma-settings-installed-29`.

## Fixture and capture contract

`tests/fixtures/settings-v70.json` is extracted from Studio's plain literals by
`extract-v70-fixture.py`; it records the spec digest. The extractor cannot run
JavaScript. It includes all 53 static pages and the 18 app IDs. The scenario
covers all pages plus initial Wi-Fi interactions and search (79 states, each
also selected for phone capture).

Fixture startup contract: `LUMA_SETTINGS_FIXTURE=<JSON>`,
`LUMA_SETTINGS_PAGE=<page ID>`, `LUMA_SETTINGS_VARIANT=<state>`, and
`LUMA_SETTINGS_QUERY=<text>`. These startup selectors are planned; their runtime
implementation is not complete. Fixture mode must bypass every real backend,
including NetworkManager, accounts, system services and persistent GSettings;
invalid fixture input must terminate, never fall back to real Settings.

The conform coordinator supplies C binary launch/capture support. Until then,
`gtk.module=settings` identifies the route, not a prairie Python module.
Never use the installed Settings binary as a capture fallback.

No preview or parity claim is made before all four conform variants pass.

The native launcher draft is `run-preview.sh`, not the prairie Python launcher.
It passes `LUMA_SETTINGS_APPLICATION_ID=org.gnome.Settings.LumaUIPreview`
explicitly through toolbox's `env`. CcApplication uses this exact value for its
GApplication identity before registration; other nonempty IDs, the installed
ID and an empty override fail closed. With no override, production retains
the configured `APPLICATION_ID`; fixture capture retains its separate ID.
`luma-settings-identity` exercises the actual native CcApplication on a private
test bus while the production name is occupied, verifies it is not remote,
creates no windows and releases the preview name after destruction. This new
check built and PASS: production owner receives zero activations, the actual
preview application is not remote, its name is released and child reaped. No
real-data preview is installed or launched until that evidence and PASS exist.

## Native build and fixture view (0029)

The toolbox dependencies now resolve. Set Meson's persistent
`-Dpkg_config_path=<private-prefix>/lib64/pkgconfig`; changing only the shell
environment does not replace its cached dependency path. Build with `host-build`.

0029 forces the memory settings backend before fixture startup, uses
`org.luma.Settings.Fixture` with NON_UNIQUE, and bypasses GNOME panel loading.
The initial `wifi`, `wifi-net`, `wifi-known`, and `hotspot` views are partial;
other pages fail with an explicit unported-page error. They do not use live
service adapters. No visual parity is claimed.

Run the model and widget tests inside the toolbox in a private Xvfb display:
`LUMA_SETTINGS_TEST_FIXTURE=<worktree>/tests/fixtures/settings-v70.json` and
`LD_LIBRARY_PATH=<private-prefix>/lib64 meson test -C <upstream>/_build
luma-settings-fixture luma-settings-view --print-errorlogs`. The widget suite
checks radio dependencies through actual switches, fixture isolation, and
destruction with a queued redraw.

## Network and Wi-Fi actions (0030–0031)

0030 adds the Network page, FloatingMenu choices, hotspot field editing,
fixture-only connect/forget operations, ActionCenter hidden-network/share
forms and DestructiveDialog confirmation. After the shared toolbox repair,
0030 compiled and its seven model/five widget tests passed. 0031 adds nearby
network connection, inline password entry, Wi-Fi Turn on, and Forget Undo.
The build and eight model/seven widget tests pass. Pending redraw and
connection callbacks are cancelled on destruction; radio-off cancels joining.
Undo restores only the forgotten network and preserves later connections
and other saved networks. All mutations remain in the supplied fixture.

`CFWIFI` carries the exact sample share artwork and password from v70.
The generator mirrors cfQR's binary64 arithmetic without executing JS. This
artwork belongs only to fixture mode; production Wi-Fi QR data must continue
to use upstream's actual credentials encoder. Fixture regeneration and
nonliteral-input rejection pass. The artwork is sample data, not app CSS.

All seven background-agent unit tests pass in a separate private bus in
`luma-dev-f44`. Their documented process/bus isolation avoids the failures
seen when combining them with GTK tests on the host's normal session bus.

0032 implements all six Wi-Fi VARIANT selectors after the view is allocated.
Unknown or mismatched page/state pairs fail explicitly. Private Xvfb tests
open the real ActionEditor, share-code picture, picker and DestructiveDialog,
and confirm/undo through the actual kit signals (eight widget tests pass).

0033 adds the Bluetooth fixture page and a GTK-free device model. Discovery
appears after 1.4 seconds, then every 2.2 seconds; pairing uses the spec
delays and keyboard code. Connect/disconnect, Forget confirmation with Undo
and radio-off cancellation are bound. Nine model/ten widget tests pass,
including delayed discovery/pairing in a private window. The page still
needs exact hero drawing, adaptive layout, and its live BlueZ adapter.

0034 isolates Bluetooth view lifecycle in its own page module, with shared
composition helpers in a private header. All app patches through 0034 replay
with git am to a second baseline checkout, producing identical source.
Settings type/control semantics are tracked in kit request 02; the existing
30px numeric role is no longer used for IP addresses or passwords.

0035 validates Windows & Workspaces choices, number bounds and preview
geometry in the pure model. Tests cover all legal gap sizes and reject
invalid edits without mutation; the Windows page itself remains unported.
0036 uses kit Copy actions for IPv4/IPv6 and the new C mono role for addresses
and the share password. Clipboard and orphan-control checks pass in Xvfb.
Ten model/eleven widget tests pass. Native scenario build/binary metadata is
now supplied; active patch ordering is tracked in Settings kit request 05.

Required kit units: run `check-kit-units.py --logs <directory>` inside a
private Xvfb display with the appkit PYTHONPATH and memory GSettings. Each
file uses a fresh session bus/process, and zero tests or all-skipped runs
fail. Foundation row size failures and a repeated-run composer-size failure
are recorded in Settings kit request 04, with full logs.

0037 decodes fixture artwork on a worker thread and discards results after
a page change or destruction. Missing assets produce an explicit failure,
with no user or installed-data fallback. Ten model/thirteen widget tests pass,
including successful decoding, missing assets and superseded work.

0038 composes Windows & Workspaces. Gap sliders retain their widget during
continuous changes; fixed workspace counts and display/app scope are bound.
Only staged artwork is loaded, normalized through LumaApplicationIcon for
application art. Ten model/fourteen widget tests pass. Compact controls and
precise preview surfaces remain pending parity; live window-manager writes
are not connected. This is fixture-only implementation.

0039 validates the eight Shelf presets from the fixture catalog, performs
scoped arrangement updates, and matches manual edits to the first applicable
preset as v70 does (excluding Split and Free). Eleven model tests pass,
including malformed-catalog rejection before mutation and preservation of
hiding behavior, icon size and unrelated connection settings.

0040 composes the Shelf page and its seven-app preview. Eleven model/fifteen
widget tests pass. Private Xvfb runtime smoke passes for Wi-Fi, Bluetooth,
Windows and Shelf; missing background artwork fails explicitly with exit 1.
Exact preview styling/clock determinism and Free-island dragging remain open.
Latest required kit units: 218 measured, 215 pass, three base row-size failures;
all 32 composer tests pass in this run. Request 04 records these results.

0041 validates the Appearance wallpaper/accent catalogs and scoped edits.
Twelve model tests pass, including traversal/unknown-hue rejection and
malformed-catalog rejection before mutation. Shared ColorSwatch/fixture accent
semantics are tracked in request 06; the complete wallpaper catalog in 03.

0042 adds the Appearance draft: Light/Dark selection through LumaSurfacePolicy,
wallpaper chooser/Add toast and tint switch. Twelve model/sixteen widget tests
pass. The view refuses theme writes without the memory backend. Its test-only,
noninstalled schema matches the kit policy fixture (luma-test.gschema.xml from
src/luma-platform/tests/appearance-schemas; Apache-2.0 kit provenance).
The native runtime still needs the canonical shell-state schema staged by the
kit/gate (request 06). Opposite-theme miniatures and Accent swatches are OPEN;
Blue fog artwork is OPEN in request 03. No Appearance parity claim is made.

0043 adds Notifications/Search pure helpers. Thirteen model tests pass, with
all 100 provider move positions checked for preserved enablement and stable
relative order. Duplicate/unknown providers and invalid durations are rejected.
CFAPPINFO derives 33 app names/artwork identifiers from literal dock markup;
fixture regeneration and executable-input rejection pass. Earlier fixture
values are unchanged. Discovery view composition remains in progress.

0044 adds Notifications, Search and Folders to search. Thirteen model/seventeen
widget tests pass, including a real GtkDropTarget drop, scoped notification
choices, provider switches, folder toggles and cross-page counts. v70 preview
notification text is fixture data; fixture regeneration passes. Nonempty QUERY
fails explicitly until sidebar search lands. Native Tasks art parity is open
in request 07. Live GNOME adapters are not yet wired into these views.

0045 applies the v70 phone padding through AdwBreakpoint setters and restores
desktop padding when widening. Unported links now report NOT_SUPPORTED while
preserving the current page; retained orphan links disconnect safely. The build
and 13 model/19 widget checks pass, including actual 390→1180→390 allocations.

0046 starts the live adapter migration with asynchronous, read-only libnm
metadata snapshots and weak generation-scoped completion. No secrets or
persistent writes occur; the production window is not connected to it yet.
Three private-bus lifecycle checks pass; the fourth successful-service check
is currently FAIL because the toolbox lacks python3-dbus. The coordinator
inbox records it; upstream network-panel also lacks python-dbusmock.
0047 adds validated Displays geometry and a scoped 15-second Keep/Go back
draft. Fourteen pure model checks pass, including 60 orientation/scale/side
combinations, timer restart, primary exclusivity and unrelated edit retention.

The feature inventory names all 71 pages, all 79 scenario states and 15
shared behaviors. `check-feature-inventory.py --scope-only` verifies declared
scope against fixture/scenario; it is not a completion check. The default
check currently exits 1 with every page open: partial fixture composition
does not count as completed live behavior or measured parity.
