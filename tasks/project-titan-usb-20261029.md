# Project Titan recovery and shared shell

- Status: shared Mobile presentation active on owner Titan; compact Home, full-screen overlays, power-button lock/blank and background Weather verified. Source candidate ready for integration.
- Owner: luma-dev-2 / Codex Project Titan chat.
- Worktree: /srv/dev/worktrees/luma-os/project-titan-usb-20261029.
- Branch: codex/project-titan-usb-20261029.

## Owner-authorized scope

Provide rapid personal-device recovery and source-backed hotfixes on the owner's
Titan 2 Elite. Preserve the converged source tree, the phone-specific kernel and
working hardware stack. This does not publish a signed/general OS release.

## Handheld animation pass — 2026-10-29

Handheld app windows fade at their final geometry instead of using Desktop's
bottom/dock travel. Desktop and dialogs delegate to the existing native motion;
interrupted effects retain the native completion callbacks. Swipe-to-Home fades
with the finger from the first movement and finishes in 120 ms. Returning Home
reuses its icon/widget actors when its bounds are unchanged; existing state,
installed-app and monitor signals still rebuild changed layouts.

Quick Options retains its fixed right-side cached reveal, shortened to 220 ms
opening / 160 ms closing. Full-screen notifications allocate their cards once
and reveal a cached frame from the centered notification island, avoiding text
rewrapping at every intermediate width. Native controls return after the reveal;
cancellation removes pending paints/idle work, clips and the temporary frame.
Larger anchored/desktop notification layouts retain their existing animation.

Actual Titan traces show constant app scale/position while opacity changes, and
drag opacity decreasing before release. Home actor identity survives the return.
The notification cache paints ten intermediate reveal states; the preceding
live-layout trace painted one. Preparation still takes several hundred ms in
observed runs, and motion is not consistently 60 FPS. Direct live clipping,
opacity-only and lower-resolution capture trials did not improve this device;
those experiments were discarded. No GPU governor change was retained.

Native touchscreen Quick Options open/upward dismissal and notification upward
dismissal pass. Bounded device checks pass for cancellation before first paint,
cancellation during preparation/motion, reduced-motion overlays, Desktop motion
delegation and Home reuse. All 266 pinned Shell patches apply and modified JS /
builder syntax pass (5ac349ce); compact/grid checks pass (c843693b). Clean source
resource overrides persist the delivery without restarting Shell or changing the
kernel. No general OS release or physical qualification of other devices is claimed.

## Owner review correction: attached compact bars — 2026-10-29

The previous pass misread the required compact presentation. Moving the home
reserve outside the painted bar created an unwanted gap. Compact phone bars now
meet the bottom edge with square bottom corners and rounded top corners. Both
native and Python Action Center implementations select this from the physical
phone monitor (long side below 600 logical pixels), not app identity or a narrow
desktop window. Taller phones and tablets retain the existing bar placement.
The shared structural stylesheet removes the obsolete compact home reserve.

Fullscreen AppWindow minimums are capped to the monitor. Clock, Contacts,
Calendar, Photos, Tide and Terminal had requested 420 pixels on the 400-pixel
Titan; they now report a 400-pixel minimum. Apps keep smaller declared minimums
and desktop minimums remain unchanged. The Python policy is shared and pure;
the native Action Center uses the matching monitor classification.

The device previously launched legacy Ptyxis. The existing maintained Luma
Terminal source was delivered instead, with its actual shared Action Center and
command field; no new Terminal UI was invented. Native touchscreen and keyboard
input ran `pwd` successfully through that field. Nine app captures were reviewed:
Notes, Clock, Contacts, Calendar, Photos, Tide, Luma Terminal, Settings and Filer.
Their bars meet the bottom edge; Tide's former left edge line is absent. A low
Clock tab tap reaches Alarms. Earlier "attached-terminal" QA captured Tide during
a failed activation; the corrected Terminal capture and real command run are the
evidence, not that mislabeled image.

Five compact-policy regressions and seven existing desktop window-opening tests
pass. They cover rotated short screens, regular phones, tablet dimensions,
narrow desktop windows, unknown monitors and bounded minimums. ARM native C
syntax and Python parsing pass (05f4a9bc). No full platform RPM build or physical
qualification of other phone models is claimed. Device delivery uses an isolated
copy of its newer installed Python kit with only these shared changes, plus the
shared stylesheet override; its native platform library remains installed.

## Shared app frames and action bars — 2026-10-29

Phone frame geometry now lives in the shared structural provider above app
composition, like Tablet's existing frame rules. Legacy island margins cannot
reintroduce side/bottom gutters or override the one status inset; explicit bleed
still owns its inset, and Tablet keeps its separate rules. Terminal opts into
this existing phone frame before mapping and hides its desktop title controls.
Filer's minimum height is 320 rather than 480, allowing its own native layout to
fit a short display without moving its title or clipping its action bar.

The shared Action Center compatibility rule places its bottom gesture reserve
outside the painted bar, retaining the common control size. Filer and Settings
inherit this rule; neither has an app-specific bar-height override. The current
phone's newer platform remains installed; resource overlays deliver the shared
CSS and the equivalent app UI changes for rapid testing, without a library or
whole-app rebuild. Future package recipes include both app patches.

Actual Titan captures verify Terminal's prompt below status and removal of its
old edge gutters; Filer's title and complete bar fit; Settings' painted bar is
compact. Native ARM GTK checks at 360×400, 390×844 and 430×932 preserve a 60-pixel
painted bar and 48-pixel search control, with controls inside the window
(job 0d3efa15). These are layout fixtures, not physical qualification of other
phones. Focused app patch application, spec patch parsing/inclusion, builder
syntax and whitespace pass (3187be9b). A broader replay found an existing first
hunk failure in Filer patch 0158 against the upstream tarball; it is unchanged.
No complete app RPM build or wider Desktop/Tablet qualification was performed.

## Quick Options motion correction — 2026-10-29

The prior paint-gap measurement did not measure the actual reveal. Pull-down
callers omit the animation argument, which the compact adapter interpreted as
no animation. Explicit NONE still skips motion; omitted flags now animate.
Timing actual clip/progress values also exposed expensive live-control clipping
and full-screen popup shadows. The compact transition caches one in-memory
screen frame, animates that frame from the right, then restores native controls.
Its clock starts after the cached frame's first GPU paint. Unchanged geometry
no longer reapplies styles; full-page surfaces omit off-screen elevation/borders.
Larger anchored menus retain their original edges and animation implementation.

Actual Titan evidence: old opening painted only the end state; the corrected
reveal paints 11 intermediate states over 320 ms (roughly 30 FPS), with a fixed right
edge. First paint was 166 ms, movement visible by 280 ms and native controls restored
at 700 ms in the observed run. These timings describe that run, not a performance
guarantee. A native top-right pull and subsequent mode-detail tap work after the
transition; upward dismissal also passes. All private timing/captures stay outside
Git. Job 3ff26e00 passes 266-patch replay, presentation/builder syntax, compact/grid
checks and whitespace. No new RPM or broader graphics qualification is claimed.

## Compact overlay and picker follow-up — 2026-10-29

Quick Options reveals its fixed allocation from the right using a paint clip;
it waits for native texture preparation before starting its 320 ms timeline.
It no longer relays out every control at each intermediate animation size.
The compact native footer stays visible in a 53-pixel row with 44-pixel action
hit targets and 22-pixel glyphs. Controls scroll above it without a scrollbar;
default Titan text fits without scrolling. The native mode detail page remains
accessible and still uses the shared controls. Home dots use 14-pixel spacing.
Notification cards remain attached to the top edge, clearing camera metadata
with content insets instead of shifting their whole surface downward.

Mobile now tracks the existing Grid popup lifecycle, preventing the last visible
window's closure from showing Home over the other switcher cards. A bottom-edge
upward gesture belongs to the modal picker and returns Home. Tablet split/drop
policy remains scoped to Tablet.

Native evidence: reproduced and captured the Home-over-Grid failure; after the
fix a card swipe reduced four cards to three while Home stayed hidden. An upward
bottom swipe returned Home and released the modal picker. Inspected the full
footer and mode page; all actions fit within the 360 × 400 screen. A real banner
was centered at x=30,y=0,width=300 with inset content, and page dots measure 14 px.
Paint instrumentation after native preparation records mostly 17–35 ms gaps
(max51.3ms in the observed interval); the initial texture setup still took about
0.7 seconds on this device. No fixed frame-rate guarantee is claimed.
Admitted job 9ff0ff7d passes the 266-patch replay, presentation/builder syntax,
compact/grid tests including corner/center banner content insets, and whitespace.
Current session hot-reloaded without restart; clean resource sources persist for
next login. Broader Desktop/Tablet gesture qualification remains open.

## Compact shared-shell checkpoint — 2026-10-29

The same shared Home now runs with Mobile selected on the phone. At its actual
360 × 400 logical size it fits five columns and four rows plus the bottom dock.
Weather spans two columns × two rows; Calendar spans three columns × two rows.
Two further rows hold ten app icons. Widgets and apps use the existing integer
cell/save contract; resizing repacks sparse layouts while preserving arrangements
at unchanged dimensions. Taller phones gain additional rows. Larger Tablet
geometry and unprofiled status insets retain their existing behavior.

Device metadata keeps the status clock and Quick Options header clear of the
camera. Lock clock/date inherit shared Desktop/Tablet typography. Search opens
the shared master search. The native notification island owns its pull target;
only the right status controls own Quick Options. Both compact overlays fill the
screen, scroll internally and provide upward dismissal. The Home gesture
area yields to open overlays. Native touch tests verify notification pull,
Quick Options pull/scroll/Close/bottom swipe, notification Close/bottom swipe,
and Quick Options Backspace. The owner confirms rearrangement now saves.

A handheld-scoped native accelerator locks before blanking the display, and wakes
without unlocking. Native key testing reports PowerSaveMode 0 → 3 → 0, with the
session locked. Asynchronous display queries avoid blocking touch input. This
implements display sleep; full-system suspend/modem wake is not qualified here.

Weather now refreshes through its existing provider/cache using a bounded user
timer while the window is closed. A saved Wi-Fi name mismatch was corrected on
this owner device, and a real forecast populated Home. Fresh-cache, empty-city,
stale-cache and failure paths have unit coverage; a real stale-cache background
refresh passed on the phone, producing a fresh forecast without opening Weather. Widget package source/pins advance
to 1.luma.2; this is not a rebuilt or published OS/package release.

Validation: admitted final-delivery job 9761c231 passes compact geometry/grid
checks, 25 shared-state tests, four refresh tests, the full 266-patch pinned Shell
series, all presentation-module syntax, package-builder syntax, Meson parsing,
RPM spec expansion and diff whitespace checks. Earlier iterations exposed a
utility signal hookup error and the finer-grid save mismatch; both were corrected.
Native captures and private development tools stay outside Git. The initial
one-shot screenshot hooks are removed; opt-in root-controlled live inspection
remains available for subsequent owner-device iteration.

## Gesture follow-up — 2026-10-29

Removed the compact Quick Options and Notifications Close buttons. Quick
Options expands from the top-right status controls over 460 ms; the native
notification island retains its centered expansion. Notification banners stay
centered and move below intersecting display cutouts instead of shifting
sideways. Full-page header content still uses the metadata safe insets.
Horizontal notification swipes dismiss through MessageTray; recognition releases
the native button press so a swipe cannot also activate the notification.
Critical notifications remain protected from swipe dismissal.

Mobile now uses the same navigation/hold path as Tablet to open Desktop Grid.
Going Home lifts a uniformly scaled foreground window clone and fades it away
in 180 ms, suppressing only those windows' ordinary minimize effects. Split
placement remains Tablet-specific. Reduced-motion settings disable timed effects.

Actual Titan native-touch checks pass: upward dismissal on both panels, left
and right notification dismissal while the tray remains open, swipe-and-hold
opening the shared switcher, a cancelled partial lift restoring the client, and
quick swipe returning Home. Private captures show the live foreground lift,
full-page overlays at 360 × 400, and a centered 300-pixel notification banner at
x=30 below the corner cutout. The source-backed resource overlay is updated for
future logins; the current session was hot-reloaded without restarting it.
Admitted job 3af8c1de passes all 266 pinned Shell patches, presentation syntax,
package-builder syntax, compact/grid regressions and whitespace checks. Full
Tablet/Desktop gesture regression and general release qualification were not run.

## Earlier shared-shell bootstrap (superseded)

The owner requests the existing Tablet shell on the phone and a compact login,
with rapid personal-device testing. The same GNOME Shell now starts on handheld
hardware; the old LUMA_DEVICE_CLASS exclusion no longer suppresses Home. The
upstream welcome tour no longer covers an existing account's first shared-shell
session. Tablet presentation is explicitly selected on this owner device.

Compact native login keeps GDM/PAM, password reveal, retry and Enter/submit.
It centers the clock/date, hides the avatar/greeting and old top panel, and fits
the floating password action bar inside narrow displays. Unlock starts with the
clock; native swipe, tap and physical typing reveal the prompt. Wider displays
retain their existing layout. Quick Options uses the available page on compact
displays while retaining the shared native controls and detail models.

Automatic on-screen input now follows physical keyboard presence and hotplug.
The explicit accessibility override remains available. Handheld defaults no
longer force that override on. A null native event actor no longer throws in
keyboard handling and traps modal input.

Actual device evidence: the native keyboard/PAM sign-in succeeded. Running
GNOME reports Shelf present, Tablet presentation, Home/status visible and the
old panel hidden. Native capabilities report keyboard and touchscreen present.
Greeter geometry confirms a centered full-width clock allocation, hidden panel,
and password field/submit button inside the 360-logical-pixel monitor. Actual
visual review of Home, full-screen Quick Options and lock gestures remains open:
the Dell bridge disconnected during capture retrieval. Do not equate actor
visibility or source checks with complete visual or hardware acceptance.

The retained native ARM Shell124/Mutter14/widget cohort is installed in a local
OSTree deployment. The original kernel and pinned previous deployment remain.
Source-backed resource overrides supply this candidate; they are development
hotfixes, not a new published OS release. Temporary diagnostic capture hooks need
removal when the bridge reconnects; exact private paths/rollback are in the
coordination handoff.

Validation: the complete pinned Shell patch series (265 patches) applies, modified
login modules and package builder pass syntax checks. Nine input-policy/hotplug/
null-event scenarios pass against the actual patched keyboard methods. The first
fixture run missed a constant binding and failed; the corrected fixture passed.
No full new RPM build or broader device qualification is claimed.

## Delivered access

The opt-in recovery installer adds configfs ECM and key-only root SSH bound to
the USB interface and fixed peer. Existing normal SSH policy is retained.
Matching kernel review corrected the configfs interface template to titanusb%d.
Physical Titan USB enumeration and root key authentication now pass.

Actual Fedora udev policy marked gadget interfaces unmanaged, preventing profile
autoconnection. The installer now adds a udev override matched only to the
dedicated device MAC. The owner's phone has the rule and updated ten-file
manifest. Eleven server tests pass, including actual SSH policy, systemd and
udev parsers. Device keys, backups, logs and provisioning state remain outside Git.

## Current Mobile experience

The owner requests the newest applicable Mobile experience as a whole, using
existing work and rapid physical testing. Merged source is not a finished Titan
release. The exact installed Phosh83.1 source RPM retains newer phone-specific
work than the public base package, so its stack is preserved.

prepare-titan2e-experience.py assembles that pinned source with merged patches
0044–0051 and shared native Home widgets. It resolves bounded compatibility
changes while retaining pocket/fold sizing, audio and hardware behavior. The
Home pager header now declares its GType for the full shell's strict compiler.
The source RPM and generated packages are operator inputs/outputs outside Git.

Shared widget model and GTK3 tests pass on ARM. Full ARM shell and plugin builds
pass. Final assembly replay passes against the exact input source RPM. The live
phone runs the new native binary and shared widget libraries; Type=notify startup
completed in 0.99 seconds with zero automatic restarts during observation. Wi-Fi
and key-only root USB SSH remain connected.

The physical startup check caught the phone form-size handler using GtkBox APIs
on the new homogeneous GtkGrid dock. The compatibility recipe now preserves its
spacing with the correct container API; incremental ARM build and device delivery
passed, and that assertion is absent from the follow-up startup log.

The candidate uses an independent private prefix and user service override. The
packaged shell and verified independent card backup remain available for rollback.
Human Home/gesture review and broader hardware coverage remain open; no general
release or complete OS update qualification is claimed. Current private artifacts,
exact delivery hashes and rollback details are in the coordination handoff.


## 2026-10-30 live Mobile panels, Weather and Home motion

Owner-device candidate uses the shared Mobile shell. Notifications and Quick
Options now share a reversible, screen-space drag and a live rounded GPU mask.
The full page is allocated once; opening no longer captures the stage or waits
for a readback. Content enters from the growing surface's edge and fades in.
The lower edge follows finger displacement directly, with distance/velocity
snap on release. Mobile notification touch targeting includes the small visual
nub's full 44-pixel hit area. The native modal grab waits until the opening
finger releases. Mobile materials match the dock without nested card frames.

Mobile lock uses the same notification island, with global and per-source
lock visibility/details policy. Locked cards do not activate apps or expose
inline actions. The former poster notification panel is hidden only in Mobile.
Desktop and Tablet retain their existing lock presentation.

Weather app, widget and bounded background service now share the same atomic
station observation cache. App cache monitors and widget file monitors propagate
updates; cached forecasts advance hourly. The timer checks every 15 minutes,
coalesces requests by existing provider TTLs and catches up after resume/network
reconnection without waking the device. User units and private native ARM
widget library are installed; no general RPM/release publication is claimed.

Home paging continues from the painted fractional position when a new swipe
interrupts settling and maps only the pages currently crossing the viewport.
Actual Titan probing measured roughly 30 painted FPS on the exposed 60 Hz mode.
The Mali-G615/Panfrost renderer works. Hiding content/effects, page redirection,
and a brief higher GPU floor did not reliably fix cadence; the original GPU
policy was restored. No VRR range or faster mode is exposed by the current
kernel. Stable 60 FPS paging is still unresolved; the interruption correction
is not evidence that the cadence problem is solved.

Validation: full pinned Shell series (267 patches), JavaScript/builder syntax,
compact layout/grid tests, Weather cache/provider/monitor/refresh tests pass.
Native ARM widget library and all three Meson tests pass. Live controller tests
cover reversal, short flicks, cancellation, reduced motion and teardown. Native
phone touches verified panel opening/closing; screenshot review confirmed the
rounded partial reveal. Private diagnostics, captures, deployment/rollback and
exact job receipts remain in the task coordination handoff.


Final screenshot follow-up: Mobile full-screen panels use the dock palette's
opaque treatment so underlying app/lock text cannot show through. Mobile lock
also hides and restores the legacy native panel, preventing its clock from
covering the notification heading. The condition excludes Greeter, Tablet and
Desktop; normal handheld status borrowing resumes after unlock.

## 2026-10-30 unlock status and notification edge follow-up

Handheld status synchronization now coalesces a final idle pass after session
and screen-shield transitions; returning Home also reconciles visibility. The
pending pass cannot revive a destroyed presentation. Live Titan lock/unlock
restored the clock and borrowed right-side controls, and a native right-status
pull opened full-screen Quick Options.

Mobile notification surfaces override the existing material selector explicitly.
The attached pill has side/bottom borders and square top corners; the full-page
tray has no border or shadow and an opaque background through all four edges.
Unsupported CSS `:not()` selectors were replaced by positive rules and an explicit
high-contrast override. Desktop/Tablet notification selectors are unchanged.
Screenshot review confirmed the final flush, opaque Mobile tray. The owner-device
boot overlay and live presentation both contain these changes; idle delay was
restored after QA.

Validation: bounded job cb2790a3 passed JavaScript syntax, final-unlock/status
regression coverage, panel motion and lock/privacy policy tests, and diff checks.
Native patches were not changed in this follow-up. The migration handoff now
includes an explicit fetch/show command for agents whose checkout lacks the
new sources. Existing Home frame-cadence limitation remains open.

## 2026-10-30 Tide / Connect repair

Connect now delegates credential access to Tide's matching implementation, reads
current/preview sign-ins without background unlock prompts, preserves opaque
credential refs on metadata edits, and retries missing credentials without a new
Hub revision. Missing sign-ins are reported instead of silently claiming complete
sync. Native receiving libraries initialize through Tide's model before first
launch; retained previews cannot displace a canonical native library by mtime.
Tide source edits emit a dedicated marker watched by Connect, excluding ordinary
playback/indexing/network-state churn. Matching Tide/core source candidate pins
and a conditional native dependency are updated. Flatpak server sync remains
explicitly unsupported by the existing adapter.

Owner-device deployment preserves newer private Connect workspace/transport code;
only the source-stamp change was applied to that installed module. The targeted
adapter plus a private clone of installed Tide with the new model/credential API
are active. Tide was restarted, the path/watch units are active, and touching the
source marker started the existing sync service. Source and SQLite rollback
copies are private. No general RPM/OTA release is claimed.

Live authenticated Hub reads and targeted source sync succeeded for the existing
server list. Its saved sign-in is absent under both Tide schemas, and its exact
Hub secret route returns 404. A different Tide secret metadata record exists but
has no proven association with this source; it was left untouched. Owner was
asked to sign into the server once. Real password upload and a second physical
device sign-in remain unverified until that credential is available.

Bounded job 3d24bfc4 passed seven two-device integration cases using Tide's actual
SQLite model and credential class (isolated Hub/libsecret fixtures), existing
Tide credentials/library/source-marker/remote/sign-in recovery tests, Connect
exporter/watch/install/shared-collection tests, RPM spec parsing and paired pin
checks. Final focused follow-up receipts and exact deployment paths are recorded
in the private coordination handoff. Hub code was not changed.

Final job ad56cba4 passed the same full focused set after the final changes,
including eleven credential tests and seven cross-component exchanges. Both RPM
specs parse and source candidate pins agree. Private deployment hashes were
compared with maintained source; no user sign-in was available at this checkpoint.

## 2026-10-30 Dell Flatpak and source Edit follow-up

The Dell's active Tide is the signed system Flatpak; its retained native RPM was
not the active library. The adapter now executes model/credential helpers in the
active Flatpak sandbox, without granting new filesystem or bus permissions. A
compatibility path reads older Tide schemas through metadata-only Secret Service
search when the newer background API is absent. Native/Flatpak data ownership
stays separate. Source-marker watch includes the sandbox data path; older Tide
Flatpaks without the marker retain timer/Hub-event refresh until their app update.

The phone's Sources > More menu replaces the details drawer. Edit rebuilt its
contents but did not present the destination drawer, leaving Listen now visible.
Source navigation now explicitly presents its destination. Device runtime tests
reproduced the hidden form before the change and then passed the actual More >
Edit button path with a mapped, focused password entry at 360x400. Only that
method was patched into the phone's private UI, preserving its other layout work.
The maintained runtime check is included in the Tide RPM check list; its desktop
profile was not run on the physical phone.

Dell delivery uses a private user prefix and unit drop-ins, preserving the
existing autosync preview beneath the new adapter. Real Flatpak model execution
succeeded. The authenticated signed Connect UI broker reports one Music server
and a first successful source sync. Direct native broker probes are refused by
the existing boundary; it was not weakened. Dell and Titan source URI, identity
and account match. Neither has a readable sign-in for that source, so real
password upload/download remains pending the owner's sign-in. The unrelated
Hub credential was not assigned to it.

Final bounded job b174166a passes the focused checks, including eight two-device
integration cases with sandbox routing and older credential API coverage,
install-selection, credentials, library, remote, recovery and shared sync tests,
RPM spec parsing and paired pins. New candidates: core
1.luma.124.mobile20261030.2, Tide 2.luma.36.mobile20261030.1. Physical phone runtime
source_navigation_runtime.py also passes the real More/Edit/focus path. No
published package/OTA or complete signed-in cross-device playback claim.


### 2026-10-30 — bottom app taps no longer trigger Home

The Mobile Home control was an invisible full-width reactive button covering
28 logical pixels at the bottom of applications. Mobile now makes that control
nonreactive and observes a native stage PanGesture instead. Normal application
taps pass through; movement must cross an 8-pixel threshold before recognition.
Initial edge bounds and normal action-mode eligibility exclude app interiors,
modal panels, lock and Desktop. Tablet retains its existing explicit Home tap
control. The stage action is removed on presentation teardown.

Actual Titan input verification used a temporary native GTK window with three
bottom buttons inside the old overlay area: left/middle/right taps each incremented
only the intended app counter, with Home still hidden. Taps at the outer bottom
edge also left the app open. Upward swipe returned Home, and upward swipe plus
hold opened the native switcher. Private runtime r82 included a diagnostic monitor;
clean maintained source r83 replaces it and the same presentation source is saved
in the boot overlay. Temporary QA service stopped and idle delay restored.

Admitted job 1782f13b passed syntax, bottom-edge regression, status restoration,
panel motion/policy, page motion, compact layout and diff checks. Evidence stays
under the private bottom-input experiment directory. Shell source candidate is
1.luma.126.mobile20261030.1; no package build or OTA publication claimed.


### 2026-10-30 — Tide edges and native Mobile Back

Tide's phone breakpoint added an asymmetric 8px leading margin. Removed that
margin rather than hiding a scrollbar. Mobile presentation also lacked the old
extension's Back gesture. Native stage horizontal-pan arbitration now owns only
right-edge touchscreen drags within the app area. A deliberate inward release
sends the conventional Alt+Left chord to the same focused app; Android retains
its Escape hardware-Back mapping. Taps, vertical scrolls, Home, modal panels,
lock, Desktop and Tablet are excluded. No overlay reserves the app's edge.

Tide's existing Back method now dismisses its topmost source drawer, Now Playing
or phone search before changing its library trail; conventional inputs bind to
that one method. Maintained runtime on the physical Titan passes flush leading
edge, transient Back and Sources/More/Edit/focused password. Separate actual
native touch test: right-edge swipe from a scrollable album returns to Albums,
trail2->1 with scroll0; clean r86 is live and maintained two-file boot overlay
updated. Diagnostics from r85 are not in source or clean r86. Candidate pins:
Shell126.mobile.2 and Tide36.mobile.2; core's >=36.mobile.1 sync requirement stays
satisfied. Admitted cb7d4ad9 passes focused edge/status/panel suites, syntax,
Tide AST/spec and diff checks. No published RPM/OTA claim.

Audio remains under investigation: user reports Tide/Doom severe stutter. Direct
48kHz native tone reported smooth; PipeWire graph has no observed XRUNs. Captured
16s synthetic Pulse tone monitor has no internal sample discontinuities. Bypassing
role loopbacks did not resolve user playback and was reverted. Shorter quantum
and matching 44.1kHz are diagnostic comparisons, not qualified fixes. No general
audio policy change is included in this source commit.

### 2026-10-30 — isolate severe audio cutouts below the application

Owner still reports severe intermittent Tide/Doom playback. Restored all failed
PipeWire experiments: role bypass, forced rate/quantum, S16 hardware format,
non-mmap transfer, and extra headroom did not resolve it. Keeping capture active
also did not help. None of these changes is a source candidate.

A fresh twelve-second source decode was prepared privately with the existing
Tide credential/client helpers, leaving its library and queue unchanged. Its PCM
contains no silent 100ms blocks. The owner still hears cutouts during continuous
direct ALSA playback with PipeWire/Pulse/WirePlumber stopped, at S16/48kHz with
6000-frame periods and a 24000-frame buffer. The alternate DL1 hardware channel
also cuts out. This points below application decoding and sound-server routing;
it does not yet identify the kernel or hardware cause. Earpiece-only playback
was silent, so earlier uncertain tone output identification is not qualification.

The amplifier diagnostic status remained DIGSTAT0007/AB0000 in eighty 100ms
samples. No observed PCM underrun or reported amplifier fault establishes a
cause. Existing power-supply lookup errors remain separately unproven. No
amplifier protection, mixer gain, kernel module or boot artifact was changed.
Normal DL0 routing, speaker profile, timing and audio services were restored;
failed test clips removed. A clean reboot is the next comparison. Relevant
exact-kernel sources were read from retained build evidence in bounded job
7bf45d6b (exit0); no kernel rebuild or audio fix is claimed.

### 2026-10-30 — selected-session boot health and recovery

A clean-reboot comparison exposed the board's Phosh-only health check. It waits
for greetd/Phoc/Phosh even after the phone switches to GDM/GNOME, leaving the
candidate unacknowledged by the existing boot-attempt guard. The phone returned
to its pinned original deployment during diagnostics. The older private startup
rollback also restored the original display-manager/environment configuration.
No kernel panic cause was established; avoid repeating the broad clock-summary
read that coincided with lost connectivity.

Added `scripts/mobile/session-healthcheck.py`: preserve the required update
scripts, then validate the selected GDM/GNOME or greetd/Phosh session. Inactive,
mismatched and unknown sessions fail; a working greeter cannot override a failed
required update check. Bounded job 8d3f8dfd passes six focused cases. The Titan's
candidate uses this source via a scoped service drop-in; its existing success,
boot-bless and pinned fallback remain. No other board backend is replaced.

Recovered the retained native deployment without replacing its kernel or user
data. Its boot guard passed and wrote the corresponding successful deployment
marker. Restored GDM selection and the previously tested source-backed overlay
environment; removed old rollback-timer behavior. Native GNOME is running,
required update plus selected-session checks pass on the phone, and the actual
Home screenshot confirms the latest compact Home/widgets/status/dock returned.
Future reboot acceptance of the restored GDM selection remains to be checked.
Audio cutouts remain unresolved; no failed audio workaround is shipped.

### 2026-10-30 — isolate unexpected APLL2 shutdown

The owner still hears cutouts, including occasional five-second clean stretches.
An internal hardware DL0→UL0 crossbar test sampled a generated pseudo-random
signal every millisecond: all 5,000 active sixteen-sample blocks matched at one
constant offset, with no offset changes. The original crossbar was restored and
generated recordings removed. This rejects stale/repeated DMA data for that test.

Amplifier status register0 alternates 0x0040/0x0042; the stock driver's diagnostic
identifies bit1 as PLL lock. GPIO42/43 stop toggling while lock is absent, with
muxes and ETDM enable registers unchanged. During verified RUNNING Tide PCM,
218 samples observed amplifier unlocked with PLLEN_ALL0x3fe, versus31 locked
with0x3ff (one transitional unlocked/0x3ff). Keeping CPUs awake, forcing both
ETDM1x clocks open, or switching audio_h toAPLL2 did not resolve the loss and
all were restored. No amplifier protection was changed.

The exact running source confirms PLL_SETCLR omits pll_en_bit and ops: unrelated
PLLs default to APLL2 bit0 and ordinary power-register handling. Added scoped
patch0065 under patches/linux-mt6878, intended after the devices/titan2elite
series0001–0064. It assigns independent gate bits, selects correct set/clear
operations, and preserves tuner handling. Actual patch application check passes
against retained exact source. Bounded jobc8ba09f0 compiles both changed clock
objects with exact running prepared headers/Clang22.1.8. No corrected kernel was
installed, rebooted or audibly accepted; this is a source candidate.

A separate twenty-second CCF-reference diagnostic module compiled in job0bc58d89
and was sent to the phone with transient titan-ccf-hold-check.service. The Dell
reverse SSH bridge then stopped returning its SSH banner, so module execution,
cleanup and listening outcome are unverified. Its code auto-releases references
after20s and the wrapper unloads it in finally; verify actual state on reconnect.
The proposed SMC diagnostic was never loaded or run.

Owner reports the on-screen keyboard returned and requires suppression on Titan.
Maintained patch0291 still uses physical keyboard capabilities, and shared defaults
remain screen-keyboard-enabled=false. Check whether boot recovery restored a
legacy true override; do not weaken touch-only-device or accessibility policy.
Prepared private idempotent correction for greeter/session dconf; not delivered
while the Dell bridge is offline. Owner was asked to wake/reconnect the Dell.

Own private test evidence moved to the8TB drive after root disk admission refused:
30,234 files verified by SHA256 and mode, original path preserved by symlink.
No other task's data was moved or removed, and admission reserves were retained.
Kernel headers/build scratch remain in own separate private experiment directory.


### 2026-10-30 — home-network access and keyboard correction

Reconnected directly to the Titan on the owner's other Wi-Fi after matching its
saved SSH host identity. The Dell bridge is no longer needed on this LAN; access
configuration stays private. Device is still on the native GNOME deployment.

The live luma accessibility setting was explicitly true despite maintained
false defaults. Cleared that user override with the real session bus and checked
false readback. GDM's selected profile also reads false without dconf errors;
there is no separate GDM user database to clear. Actual screenshots show the
focused lock password and main Search fields without an OSK, and native keyboard
login succeeded. Existing capability-based keyboard policy remains intact; no
Titan model-name block or touch-only-device policy change was added.

Recovered the completed CCF diagnostic: baseline amp PLL lock12/93 samples,
held references324/324 locked, restored9/93 locked. Its service exited cleanly
and diagnostic module is absent from lsmod. This supports the independent PLL
ownership correction; the corrected kernel is still not installed or audibly
accepted. The earlier unverified diagnostic/cleanup status is now superseded.


### 2026-10-30 — compile and install independent PLL gates

Extended0065 to correct MAINPLL/UNIVPLL/MMPLL shared-register bits6/5/3;
the retained vendor MT6878 table independently confirms these assignments.
Patch application check passes against exact retained running source. Full ARM64
Image build passes in admitted jobdf3cb5bd (j8,3m26s,3.4GiB peak). Earlier Image
jobecf429ed was deliberately stopped before the final descriptor correction.
The new task-owned HDD scratch lane uses normal disk/memory admission; no other
task's storage or runner security policy changed.

Preserved the original built-in initramfs byte-for-byte, original DTB, and kernel
config except relocated input paths. Jobcc29be8f verifies all13,092 previous
built-in exported symbols remain; only mtk_pll_setclr_ops is added. UTS remains
unchanged solely to reuse unchanged device modules on this development phone.
Job512adc60 packs the Androidv4 image using checksum-pinned AOSP tools/public
test key; both signature and payload verification pass. No release published.

The owner's original boot_a was backed up and matched the retained server image.
New boot_a SHA256e6f347b2bbb11674552c13baa16fd698938b05cf55c8f1e6851648bc2dc7bc11
was written and independently read back in full. Reboot requested; actual new
kernel startup, restored GDM boot health and audible playback are still pending.
The longer pre-install clock-reference test remained electrically locked in all
5,086 samples but was reported silent, so it is not audible acceptance. The
module/service completed and Tide was paused; no permanent clock hold remains.


Fresh corrected-kernel boot is verified: new mtk_pll_setclr_ops symbol and the
new build timestamp are present in the running kernel. Wi-Fi/device modules,
GDM, successful update/session health checks and boot-complete target are up;
boot-success marker matches the native deployment. Native physical-keyboard
login succeeds and actual compact Home screenshot matches the current shell.
User keyboard override remains false across reboot. Initial normal Tide PCM
check observed amplifier PLL locked50/50 samples, with no diagnostic module or
reference-holding workaround. Thirty-second check completed with amplifier PLL locked in all1,391 samples
(normal clocks, no reference workaround). Owner listening answer is pending. Normal speaker-only route restored (Speaker on/Earpiece off).


### 2026-10-30 — owner confirms smooth playback

After the corrected-kernel reboot, owner requested Tide playback and confirms
music has no chopping. Owner describes the output as the earpiece. The active
route reads Speaker Switch=on, Earpiece Switch=off, RCV Mux=Open, and the external
FS19xx amplifier is active. The manufacturer's Titan2 Elite User Manual V1.0
(202605), physical page1/PDF page6, places the loudspeaker on the top edge beside
the receiver, with only USB/microphone on the bottom. The previous operator
assumption of a bottom loudspeaker was wrong; no output switching or UCM label
change is justified. The existing smooth route remains active. Official manual:
https://www.unihertz.com/pages/titan-2-elite-user-manual

Tide listening acceptance now passes with the corrected kernel. The electrical
30-second stable-lock result is complemented by owner listening; no CCF hold or
other failed audio workaround is left installed/running. Doom and telephone
call-path listening have not been checked after this fix and are not claimed.


### 2026-10-30 — retain owner-accepted configuration

Correction to earlier listening qualification: owner clarified the smooth sound
was only from the quiet earpiece, not the large speaker. Mixer labels and the
manual diagram cannot independently establish which physical transducer is
heard. The earlier broad completion claim was premature. During inspection,
Tide was resumed; no route, amplifier gain, driver or UCM change was made. The
owner then said “Sounds perfect. Lock it in.” Retain the current accepted setup
and do not infer that separate physical loudspeaker/receiver isolation passed.
Doom and call paths also remain untested.

Re-read the installed boot partition: full SHA still matches the corrected
image. GDM is active and native boot-success marker remains valid. The forced
OSK setting remains false. Privately captured the accepted controls (Speaker on,
Earpiece off, RCV Open, amplifier895, ordinary sink volume0.94) without creating
an always-on mixer override or changing other devices' policy. Normal kernel
boot selection and existing UCM/WirePlumber persistence remain in use.

Bounded packaging job8abbb623 retains the verified boot image, Image.lz4, exact
config, System.map, kernel release,0065 source patch and private acceptance
snapshot with readback SHA256 checksums on the8TB drive. Existing original boot
backup remains. This preserves a recoverable accepted device build; no general
OS release or upstream merge is implied.

### 2026-10-30 — low-power double-tap wake candidate

Owner requested double-tap/lift wake with low battery impact. Current FT3383
SPI driver had no wake mode; the only IIO device is the MT6363 ADC, with no motion
sensor/event interface. Added0066 with controller gesture configuration, only
double-tap recognition, KEY_WAKEUP, serialized register/IRQ operations, ABI docs,
and a Titan `wakeup-source` DT opt-in. Normal touch remains the default on other
profiles. PM code preserves the gesture state during background resumes; actual
system-sleep testing remains pending.

Added a shared optional display-power policy and system unit. It follows active
seat/display events, uses a child with the session owner's bus credentials, and
keeps root sysfs writes limited to discovered boolean capability nodes. Session
startup retries are bounded; idle processing has no periodic sampling. Both
normal shutdown and ExecStopPost restore normal touch. Unit capabilities permit
only dropping monitor credentials and terminating that child. No OSK, audio,
Home layout or motion-polling change was made.

Final module compile passes in admitted job246c77c9. Source provenance job
72cfa94d applies the patch to exact retained driver/DT, verifies the patched C
matches the compiled input, checks the opt-in/ABI file, and parses Python.
Actual phone systemd unit verification passes. Live mode writes read back1/0;
native wake-key injection turns the display on while retaining the lock. The
native power-key path disarms on unblank and rearms on blank. A 20-second service
idle check measured0ns CPU, with a later final-version check recorded privately.
These checks do not simulate or prove a physical double tap.

Live source-backed module/policy are installed and enabled for subsequent boots;
the original packaged touch module remains the fallback, and the accepted audio
kernel/boot image is unchanged. Policy-stop restoration passes. A fresh boot,
physical gesture recognition, system suspend/resume and battery-life measurement
are pending. Async physical tap question is still unanswered; driver wake count
has been zero during observation. Lift-to-wake stays unavailable until the
motion sensor/hub backend exposes a low-power event path.

### 2026-10-30 — recognize taps and explicitly wake the handheld display

Owner reported no response to the physical gesture. The controller counter
increased while display mode remained3, proving recognition reached the driver
but not successful unblank. The old driver also exited low-power mode before
unblank. Revised0066 emits KEY_WAKEUP through a separate key input device and
leaves gesture mode armed until the policy sees an actual display-on event.
A separate input device alone did not fix waking; ordinary input-activity wake
was inconsistent in repeated tests after direct/manual blank.

Added a wake-only XF86WakeUp accelerator to the existing shared handheld power
handler, allowed while locked. It explicitly requests display-on and never
unlocks or toggles off. Both shortcut registrations are released on destroy;
retrying one missing shortcut preserves the other. Desktop/tablet gating and
physical-keyboard policy remain intact.

Module build eb790b7e and patch provenance check6f85f52e pass. JavaScript syntax
and diff whitespace checks pass. Live source-backed presentation r87 hot reload
succeeded; startup overlay contains the same power handler. Three consecutive
injected KEY_WAKEUP events on the actual driver input each changed display3→0,
gesture1→0 and retained the screen lock. Reblank rearms1. The physical test has
been requested again after this shell correction; no physical success is yet
claimed. Fresh boot/system suspend/battery-life remain untested. Lift-to-wake
still requires motion/hub driver support. Accepted audio kernel is unchanged.

### 2026-10-30 — physical double-tap wake accepted

Owner answered “Yes, it wakes” after the explicit shell wake-handler fix.
Readback shows wake count6 (previously5), gesture mode0, and both
project-titan-touch-wake-driver.service and luma-touch-wake-policy.service
active/enabled. Retain the current source-backed setup. No additional runtime
change or reboot was needed. This confirms physical double-tap waking from
display blank; it does not qualify system suspend, a fresh boot or battery life.
Lift-to-wake remains unavailable without a low-power motion event backend.

### 2026-10-30 — drawer input, default apps and animation investigation

Owner reported broken horizontal dismissal and Clear all. Native picking
identified the notification container instead of its descendant cards/buttons.
Clutter's default pick vfunc deliberately skips child traversal when overridden;
0294 explicitly picks children. Horizontal may-recognize now checks eligibility
at touch-down, leaving axis arbitration to PanGesture. Swipe/Clear all match the
existing NotificationMessage.canClose policy: critical errors may be dismissed,
while incoming calls stay protected. A source-backed temporary Effect supplied
child picking to the old live singleton; after restarting GDM, the actual fixed
class is loaded without that compatibility effect. Actual native left/right
swipes and a Clear all touch passed during initial delivery. After the GDM
restart, the actual fixed class also passed:3→2→1 records after left/right, a
short swipe retained the last card at its original position, and the Clear all
touch removed the remaining critical test alert while keeping the drawer open.
All six native lifecycle checks passed again in that clean session.

Shared phone roles now request Luma Monitor instead of GNOME System Monitor.
A shared default-app exclusion list prevents Tour, Yelp/Help, System Monitor,
Reel and Darkroom from entering phone composition, and optional Darkroom was
removed from both composers' mandatory local RPM inputs. Desktop's existing
removed-packages policy already excludes all five. Inventory/smoke assertions
now reflect optional Creative apps instead of requiring their default install.

Installed the verified existing noarch Monitor17 RPM, SHA437e0433de7d14be47afa67b7953bee2664971cbf2af3de768bc8aae31530a43.
Staged deployment56c2d5c50ac35aaed11fc1294d5751d82e64846bc8857d284d5a86c021be55ce
removes Darkroom/Reel/System Monitor/Yelp and omits Tour after disabling weak
package recommendations on this device. rpm-ostree apply-live completed; rpm
confirms all five absent and Monitor installed. Default gtk-launch Monitor
produced the real Monitor window; screenshot reviewed. A transient Cairo
comparison was stopped by the session restart; the verified final launch used
the default renderer. No launcher-specific renderer override remains.

The repeated connection errors came from Mobile data autoconnect registration
timeouts, not Wi-Fi. Device-only autoconnect was disabled for that saved profile.
The modem supplies a SIM identity, but no EID/SIM-type/eSIM status; physical versus
eSIM cannot be determined from this backend. No cellular identity was recorded.

Animation work caches bounded visible Home pages and releases hidden-page
caches. Opening waits for one native control-cache paint before starting its
clock; cancellation/reduced-motion paths release callbacks/effects and restore
original actor state. First opening remains slow; a warm trace showed seven
intermediate opening frames, but neither this nor Home is consistently60FPS.
Do not label this performance work complete. Current mode reports60Hz; DRM
vblank intervals sampled near16.64ms, but simpledrm timing is not an independent
panel-refresh measurement. A minimal single-color animation still often paints
around30FPS. KMS uses simpledrm while rendering uses Mali-G615 Panfrost, narrowing
remaining investigation to compositor/presentation cost rather than grid alone.
Brief CPU/GPU floor comparisons did not resolve it; both floors were restored.
No fixed high-refresh/performance governor or polling loop was retained.

Final pinned268-patch application/module syntax check07b0b792 passes in fresh
scratch (earlier reused scratch failed due retained added files, then fixed).
Inventory12tests pass779466e0. Native lifecycle checks cover cancel before/during
paint, reduced motion for both panels, effect cleanup and Home reuse. Full
mobile-convergence smoke09103955 is NOT passing: it references two absent old ADR
files and its unit-launch command used the wrong working directory; its updated
Monitor/package-role assertions passed. No full image build/reboot qualification
is claimed. Private diagnostics remain outside Git under titan-ui-20261030.


### 2026-10-30/31 — shared application launch acceleration

Owner requested substantial, measured mobile launch improvements and primary-source
Android/Apple/Linux research, with rapid hotfixing and GitHub source convergence.
Owned worktree/branch unchanged: project-titan-usb-20261029, luma-dev-2.

Implemented the shared platform's prepared Python/GTK host, libc launch client,
protocol-v2 commit gate, compatible fallback, same-confinement admission, app-scope
adoption and one display-ready child. Stateless core imports are shared; Camera
and Tide media initialization remains in the launched process. Native source and
RPM entry points use -sP. Core UI, Monitor, Terminal and Tide packages use the
native frontend; background agents remain separate. Removed redundant appearance
CSS reloads. Image finalization produces checked-hash Python caches, avoiding
OSTree timestamp recompilation. Home persists until a real app frame; Mobile
opening opacity transition is 80ms, Tablet160ms and reduced motion0.

Handheld-only native preparation is explicitly limited to Settings/Filer. The
shared opt-in module constructs their real initial view without mapping it, drops
its environment before children, restores ordinary priority/lifecycle on use,
retires untouched views on document Open, and reclaims unused UI on low-memory
signals. At most2 caches, 1GiB MemAvailable guard before each; no recurring scan,
frequency boost, media prelaunch or generic background freezing. An arbitrary
unused-cache expiry was tested then removed; memory pressure/use govern lifetime.

Found and fixed a fresh-login ordering regression: starting the host alongside
Shell captured the environment before DISPLAY/WAYLAND_DISPLAY were exported.
Ordering after gnome-session-initialized.target makes ordinary post-login launches
use the prepared display. Confirmed by fresh GDM restart/password login and host
logs, without manually restarting the host after login. Early greeter capture at
3s was blank before UI readiness; a later capture showed the correct compact
clock/password field with no OSK. Temporary greeter capture code was restored.

Observed first focused app frames: baseline Weather2081–2128ms, Contacts2268ms,
Monitor1621ms. Fresh-session prepared-runtime launch: Weather532ms, Contacts547ms,
Terminal391ms; corresponding resident reopen57/45/53ms. Other real comparisons:
Phone426ms, Photos361ms, Tasks524ms, Clock550ms, Messages675ms, Notes668ms,
Calendar771ms, Monitor554ms, Tide753ms. Prepared native Settings381ms/Filer466ms
versus unprepared1536/1121ms. Resident windows generally15–70ms. These are individual
TTID observations, not percentiles, full data readiness or a universal125ms claim.
Host+spare+two unused native caches totaled~490MiB PSS and each used0 CPU ticks in
one10s idle sample. This is not a battery-run qualification.

Device overlay source: canonical apphost is in the existing compact-bars Python
kit; native frontend/launchers/module/controller are under
/var/lib/project-titan/launch-20261030. /usr/local/bin UI aliases and three user
D-Bus service overrides point at the frontend. User host/timer enabled; source-
backed units in /etc/systemd/user. Public presentation.js/tabletTasks.js replaced
startup overlay copies; private r104 is only the diagnostic reload. Removed the
older device-only render_texture warmup scheduling, which caused invalid DMA-BUF
export/FD errors. Do not restore it from an older kit overlay. Accepted audio
kernel #2 2026-10-30 23:00UTC unchanged; touch-wake driver/policy active, OSKfalse,
CPU floors450/400MHz and no persistent graphics boost.

Validation: actual Titan12 Unix protocol/Wayland runtime tests plus3 native GTK
preparation tests pass, including original fallback, environment/flag mismatch,
no-new-privileges confinement, aborted launch, signals/status, pending executor
writes/exit handlers, initialized-media rejection, FD cleanup, one
mapped real window, OOM priority restore and low-memory/document-open cleanup.
Server10 protocol tests pass (2 display cases skipped there); checked-hash import
regression1pass; package cohort2 and application inventory12pass. Five modified
app/platform RPM specs parse, Meson AST parses, shell/image script syntax and diff
checks pass. Full pinned Shell268-patch series applies and syntax passes
(dev-titan-launch-cohort-final-141fea08). Fixed an existing Shell126 input/spec125
mismatch and assigned coherent candidate127.mobile20261031.1 with a contract test.
Platform117, core125, Monitor18, Terminal8, Tide37 candidate pins are coherent.
No full new RPM cohort/image compose or sustained battery test is claimed.

See docs/operations/mobile-app-startup.md for architecture/research links and
shared-mobile-shell-handoff.md for receiving-device migration. Source publication
is this development branch, not a release promotion. Other phones must integrate
the matching source cohort and build/qualify their packages; do not copy private
Titan overlays or claim that Home paging/Quick Options rendering now achieves60FPS.
Private measurements/scripts: account experiments/titan-launch-20261030; bounded
cross-build inputs/binaries live on the existing titan-kernel-20261030 HDD bind.
