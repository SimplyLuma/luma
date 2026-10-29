# Project Titan recovery and Mobile experience

- Status: USB access delivered; newest merged Mobile shell ARM build in progress.
- Owner: luma-dev-2 / Codex Project Titan chat.
- Worktree: /srv/dev/worktrees/luma-os/project-titan-usb-20261029.
- Branch: codex/project-titan-usb-20261029.

## Owner-authorized scope

Provide rapid personal-device recovery and source-backed hotfixes on the owner's
Titan 2 Elite. Preserve the converged source tree, the phone-specific kernel and
working hardware stack. This does not publish a signed/general OS release.

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

Shared widget model and GTK3 tests pass on ARM. Full native ARM shell compilation
is running; no live Mobile experience update or release qualification is claimed
until actual deployment/readback. The independent card backup is verified.
Current private units, artifacts and delivery actions are in the coordination
handoff. Keep the original packaged shell for immediate rollback.
