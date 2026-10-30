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
