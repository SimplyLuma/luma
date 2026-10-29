# Project Titan recovery and shared shell

- Status: shared GNOME Tablet Home and compact native login running on owner Titan; final visual/Quick Options checks pending bridge recovery.
- Owner: luma-dev-2 / Codex Project Titan chat.
- Worktree: /srv/dev/worktrees/luma-os/project-titan-usb-20261029.
- Branch: codex/project-titan-usb-20261029.

## Owner-authorized scope

Provide rapid personal-device recovery and source-backed hotfixes on the owner's
Titan 2 Elite. Preserve the converged source tree, the phone-specific kernel and
working hardware stack. This does not publish a signed/general OS release.

## Shared shell on Titan

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
