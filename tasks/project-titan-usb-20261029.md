# Project Titan recovery and shared shell

- Status: shared Mobile presentation active on owner Titan; compact Home, full-screen overlays, power-button lock/blank and background Weather verified. Source candidate ready for integration.
- Owner: luma-dev-2 / Codex Project Titan chat.
- Worktree: /srv/dev/worktrees/luma-os/project-titan-usb-20261029.
- Branch: codex/project-titan-usb-20261029.

## Owner-authorized scope

Provide rapid personal-device recovery and source-backed hotfixes on the owner's
Titan 2 Elite. Preserve the converged source tree, the phone-specific kernel and
working hardware stack. This does not publish a signed/general OS release.

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
