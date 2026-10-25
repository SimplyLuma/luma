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

Integrated companion a9e0beae9 plus installer73 packaging5b4f0be61. Both user
and system services run Type=exec, so boot/login targets do not wait for cleanup;
RuntimeMaxSec2min bounds it, with five-minute failure retries. Actual isolated
Flatpak uninstall a64d0807 preserves unrelated deployment and real private data
marker; 14 root baseline boundary tests7ad83258 passed. The new baseline is23
apps plus3 runtimes, derived from the validated selection, never an old24-app
baseline relabeled.

Root integration checks6f8843d9 passed78 public contracts,10 profile checks,
20 device tests,6 retirement policy tests and4 registry tests. That wrapper
then failed honestly on a nonexistent test path (zero tests, exit5). Continuation
2a457207 runs the actual three baseline inventory tests plus release/unit checks;
consult its actual result before claiming success. Earlier wrappere4234cd5
stopped at missing private jsonschema dependency; installed only into an isolated
venv for6f8843d9. No shared Python or test assertions changed.

Release-side withdrawal now uses scripts/depot/retire-remote-apps.py from the
sealed signer before update-remote regenerates AppStream, summary and descriptors.
It marks every existing Displays architecture/branch EOL with “Display controls
are in Settings.” Each new signed commit preserves the previous commit as its
parent and its exact application tree; unrelated refs are checked unchanged.
Repeated execution is a no-op. No live remote was changed by this source work.
An isolated real repository test covers both CPUs, multiple branches, signed
EOL commits, history/payload preservation, unrelated refs and descriptor/index
withdrawal; actual installed-image wake/upgrade and final published AppStream
checks remain required.
Actual normal job403c6dfb passed all11 remote-generation, signed retirement and
remote synchronization tests. The retirement fixture verifies the new signatures
against an isolated remote keyring; no production keys or remote are used.
Additional actual31355ffc passed the same signed retirement with real AppStream
generation before and after: Displays appears before withdrawal on both CPUs,
disappears afterward, and unrelated Viola remains advertised. The first two
fixture attempts expected compressed XML in appstream2 and failed; inspection
confirmed Flatpak writes uncompressed appstream.xml there. Only the fixture
reader changed; EOL policy and production assertions stayed intact.

Native CI exposed two Shell packaging issues missed by the earlier relaxed
source-application fixture: duplicate Patch1274 declarations, and zero-context
main.js hunks rejected by RPM's plain Git application. Both are corrected.
Actual6115f685 parsed the full patched Fedora spec; d9126da9 reproduced the
production prep failure, and ae8f5d83 passed the complete real rpmbuild %prep
after normal-context regeneration. No activation remains in the resulting
main.js; Settings' preview helper and call remain.

The maintained component workflow now builds installer73 on its canonical x86
builder, and Ari20/policy29 on both native CPUs. Installer and Ari admit the
already checked matching toolkit, rather than rebuilding it. These three
packages have explicit noarch identity, checksum and required-payload checks.
Actual2e15fd02 passed12 matrix tests,3 real isolated RPM checker tests and YAML
guards. Checker-only changes select only their five consuming native jobs;
their uploaded artifacts omit duplicate toolkit packages. Actual new product
package builds remain pending CI, with installed-image checks still required.
