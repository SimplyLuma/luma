# Retire standalone Displays

Owner: luma-dev-2 / Codex root, arm-desktop integration worktree.
Status: source implementation and focused package checks; image delivery pending.

User requests removal of the standalone Displays application across desktop,
ARM and mobile, including existing installs, with display controls in Settings.

Shell patch0280 removes monitor-arrival/startup activation, including the wake
path, and retains Settings' explicit display preview ownership. The old UI RPM
is replaced by luma-display-config: only model/displayconfig and package init
remain, preserving Ari's API without a launcher, D-Bus activation or UI files.
The new backend obsoletes all historical0.1 Displays RPMs. Ari depends on the
backend. Shared desktop pins/compose inputs and launcher policy omit Displays.

Actual evidence,2026-10-25UTC:
- 675ba279: backend RPM built, installed backend imports and model suite passed;
  payload checked for absence of UI/activation; an isolated RPM database upgrade
  from synthetic old Displays removes old package. Fixture uses justdb/nodeps/
  ignoresize only for its isolated database, not actual OS installation.
- 771f4e25: pinned Fedora SRPM verified, complete258 declared Luma Shell patches
  applied successfully; resulting Settings helper executed for absent, retired
  Displays and Settings windows; startup/monitor activation absent.
- Launcher GIO visibility positive/negative checks and shell syntax passed.
- Earlier7cf21c64 built the backend but failed its synthetic RPM database size
  check; retained failure, corrected fixture675 without reducing real reserves.

Companion catalog/Flatpak retirement belongs to arm-server-qualification agent.
It must uninstall only exact retired app refs, preserve user data, run for
system installations and per-user login without requiring Depot to open, and
retry failures. Integrate reviewed source before final build.

Remaining: final installer/user/system unit integration, relevant package builds,
actual upgraded image removal/wake checks and new nightly publication. No running
user installation changed, no new nightly claimed by these component results.
