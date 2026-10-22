# Luma Tester — Linux first

- Owner: Codex, luma-dev-2, current tester chat.
- Status: active — prior Mobile qualification withdrawn after user review; correcting phone provenance and desktop graphics.
- Branch: codex/luma-tester, based on origin/main 925db44.
- Worktree: /srv/dev/worktrees/luma-os/codex-luma-tester-20261017.
- Scope: LumaUI Linux launcher, mobile-first and desktop profiles, x86_64 and
  aarch64 guest definitions, verified image ingestion, private persistent sessions,
  localhost SSH, source sync/app restart workflow, image/agent documentation.
- Owned: src/luma-tester, docs/developer-platform/tester*.md, README/BUILDING
  navigation, this task record. No app-porting source lanes modified.
- No publication, OS deployment, signing, physical device operations, or user data
  changes. Host platform packages and integration checkout remain unchanged.

## Current handoff

The user explicitly rejected a generic GTK final interface. The revised GUI
requires real LumaUI: AppWindow/frame, Island, Card, type roles, icons and shared
palette. Flow: Try Luma / Develop for Luma → Mobile / Desktop → Open Luma.
An available target is selected automatically. Architecture is chosen automatically. Development
controls appear only on Develop. Image import and workspace diagnostics live in
Advanced. Imports persist locally; packaging may supply LUMA_TESTER_CATALOG.

Backend accepts trusted local manifests referencing HTTPS or local standalone
qcow2 files, checks SHA-256/size, maintains private overlays/keys/seeds and locks,
chooses KVM or TCG, exposes localhost-only SSH, QMP controls/screenshots, source
sync and a foreground app restart watcher. CLI remains independent of GTK/LumaUI.
The wheel is a launcher package, not a self-contained QEMU/LumaUI/guest bundle.

Current Linux x86_64 candidate is **Desktop only**, with automatic VirGL,
software fallback, and a frame-clock-limited private Unix VNC viewer. Actual
packaged guest checks verified VirGL, app source edit/restart, accelerated
screenshot capture and graceful shutdown. Final AppImage entry passed as well.
Latest source host/guest1deb3d1b. Mobile selection is unavailable in this
catalog until its actual phone shell/profile is qualified. Earlier r10 Mobile
claims below are withdrawn; portrait GNOME is not product parity.

Latest Desktop candidate (display refinement):
- /srv/dev/accounts/second/experiments/luma-tester-20261017/appimage-display-r15/Luma-Tester-0.1.0-linux-x86_64.AppImage
- SHA256 b46a3ccf8c7e245e89144ed49e7d9bf901f3701ee8cbef16e8f31e277e79929d;
  1688381944 bytes. Receipt and checksum beside it.
- Portable: /srv/dev/accounts/second/experiments/luma-tester-20261017/Luma-Tester-display-r15.
- Guest: desktop-image-r15; SHA25658526b0dc6041300379f7dac665efa5beb0104264012143839476eb135d571f0.
- Actual compositor reports Luma Desktop, preferred/current1920x1080, scale1.0.
  Uses a local LMA EDID firmware identity and guest-only hwdb entry; no physical
  device profile, OS package cohort or QEMU implementation changed.
- Full native/packaged workflow PASS dev-luma-tester-display-full-r15-1b08a476;
  actual AppImage entry PASS dev-luma-tester-display-appimage-entry-r15b-73d156bd.
  Core39 checks PASS dev-luma-tester-display-core-2bc8c978.
- Dell display update delivered on2026-10-22, exact checksum verified by
  dev-luma-tester-dell-display-delivery-1022-f8a613c2. Launched through the real
  graphical session as luma-preview-tester-display-20261022-a4654df3.service,
  with isolated tester-display-20261017 config/data/cache/workspace. No existing
  tester/QEMU process was running before launch. No Dell automated tests ran.
  Earlier graphics-r13 delivery completed after the user reported it online. Exact candidate
  checksum verified by dev-luma-tester-dell-graphics-delivery-r4-45557a0e.
  Launcher active in the real graphical session as
  luma-preview-tester-graphics-20261017-bd66b286.service, using isolated
  /home/nick/.local/share/luma-previews/tester-graphics-20261017 storage.
  No guest auto-started and no Dell automated app/UI tests ran. Manual review
  feedback: user says Desktop works great. New display candidate is separate;
  do not interrupt any running guest or discard its writable state.

Current phone component job is dev-luma-tester-phone-phosh-oracle-df2fc3e4 in the
separate owned luma-tester-phone-20261017 experiment. Phoc12 passed19 normal
checks. Phosh73 compiled but its normal check found an obsolete hardcoded dark
color oracle; it now reads the independent shared AppKit gradient-top reference,
using shell-state's existing exact-one-declaration check mechanism. Palette and
renderer payload source are unchanged. All other assertions/suites preserved.
Normal Phosh build/check passed all13 suites, scale3 Library composition and
all7 drawer traces; component-artifacts-r4.tar retains RPM/SRPM/QA. Kernel input
checks verified real multitouch; actual phone shell gesture integration remains
pending. Phone UI, current shared app/platform cohort, ARM execution and
macOS/Windows remain unqualified.
No phone was touched, and no release/push/publication occurred.

Phone builder state moved offline to
/mnt/luma-secondary/dev/luma-dev-2/luma-tester-phone-20261017/builder-state.
File hashes and qemu-img guest-visible comparisons passed before removal of the
old root-filesystem copy (dev-luma-tester-phone-state-migration-r2-8ab9b000;
user worker luma-tester-user-dbe0a8be33). Existing drivers hardcode the old path:
update them before reuse. Secondary-disk jobs require the bounded
root-user-worker.py controller because ordinary devctl workers make that mount
read-only. Next Mobile work: matched GTK4/libadwaita SDK, current shared
platform/apps, native phone profile, then actual shell/app gesture QA.

Historical r10 outputs (outside Git, retained review evidence):
- AppImage: /srv/dev/accounts/second/experiments/luma-tester-20261017/appimage-final/Luma-Tester-0.1.0-linux-x86_64.AppImage
- Size1558088184 bytes; SHA2569ff6c36c9c83e18c6816de6c2895a7e5f08eecce5c2c85aaf4ada59add9180eb.
- SHA256SUMS and appimage-receipt.json are beside the AppImage.
- Portable directory: /srv/dev/accounts/second/experiments/luma-tester-20261017/Luma-Tester-0.1.0-linux-x86_64.
- Guest/catalog/receipt: /srv/dev/accounts/second/experiments/luma-tester-20261017/converged-image-r10.
- Guest SHA25698191da5951d6006d7e9110c4df311cf623143b5ec880f22fc5432c2d750ba7a,
  1440481280 bytes;29 pinned inputs plus source-built Luma dock14, shared apps104,
  Shell112/Mutter13/platform107. Public build identity is readable in the guest.

Historical r10 boot/workflow evidence (Mobile parity withdrawn):
- 33 backend tests PASS, dev-luma-tester-relocation-check-92596565; covers real
  QEMU/QMP fixture controls, identity/integrity, private sessions and URL relocation
  preserving the original key/overlay. A fixture OS boot is not implied.
- Final packaged Mobile PASS, dev-luma-tester-final-mobile-df04aef7,46.74s,
  3409 live frames. Actual handheld extension ACTIVE, portrait home, SDK CLI,
  source sync + two app PIDs after edit, visible hot-fix text, graceful poweroff.
- Final packaged Desktop PASS, dev-luma-tester-final-desktop-4a6b3adc,42.78s,
  1950 frames. Separate desktop profile, same base digest, native app edit/restart
  and graceful poweroff. Home/hot-fix screenshots inspected for both targets.
- Saved Mobile relaunch PASS, dev-luma-tester-final-reopen-ed2660e5: project text
  and SSH key/host pin preserved, painted session ready, real guest pause/resume,
  second graceful poweroff. Same final private state, no re-pairing bypass.
- Final bundled LumaUI flow/narrow/dark check and AppImage assembly PASS,
  dev-luma-tester-appimage-final-r2-db54d408. The first attempt used a test that
  expects an empty catalog against the real bundled catalog; corrected the test
  environment to explicitly supply its empty catalog, without a product change.
- Actual AppImage entry PASS, dev-luma-tester-appimage-entry-9072aedd: extract/run,
  real LumaUI welcome window, keyboard quit, CLI doctor finding all tools inside
  the extracted bundle. Screenshot appimage-welcome.png in that job's artifacts.
- Full original shared-app RPM checks passed in r3; later builds reuse the exact
  unchanged cohort with receipt/image checks. Logs/source RPMs retained outside
  Git. No generated guest/image/package/key is committed.

Job evidence directories are /srv/dev/accounts/second/artifacts/UNIT_WITHOUT_SERVICE.
Owned failed/disposable builder caches and obsolete test sessions were retired
only after checking no process used them; logs/receipts were retained. Protected
OS stores and unrelated agents' outputs were not changed. Root free was27GiB
at final checks, above the20GiB reserve.

Git fetch encountered a pre-existing missing object under a Codex turn-diff
capture ref in the shared repository. Existing origin/main resolves to 925db44;
used that source without altering shared refs or unrelated worktrees.

## Validation and artifacts

- `devctl run luma-tester-source-final ... --memory-gib 1 --wait -- env
  PYTHONPATH=src/luma-tester python3 -m unittest discover -s
  src/luma-tester/tests -v`: 30 tests passed. Unit
  dev-luma-tester-source-final-6f51d1c9.service, artifacts
  /srv/dev/accounts/second/artifacts/dev-luma-tester-source-final-6f51d1c9.
  Includes real x86 QEMU/TCG startup, CLI-held disk lease, termination forwarding,
  QMP pause/resume and PPM capture; fixture disks intentionally have no OS.
- Checked-in `src/luma-tester/tests/ui_smoke.py` under Xvfb with real LumaUI:
  passed Try/Develop navigation, mobile default, target toggle, developer
  disclosure, Advanced, keyboard focus, narrow/dark rendering. Unit
  dev-luma-tester-lumaui-flow-82566647.service; screenshots/logs at
  /srv/dev/accounts/second/artifacts/dev-luma-tester-lumaui-flow-82566647.
  Native library inputs reused read-only from the existing Calendar-r9 platform
  bundle; only libluma libraries copied into this task's job artifact directory
  because secondary storage is noexec. AppKit source came from this worktree.
  No host library installation. Earlier native-runtime attempts failed due to
  noexec mapping and were corrected; final flow passed.
- Real LumaUI GUI → catalog selection → CLI → QEMU GTK display → pause/resume →
  screenshot → clean child exit passed with a no-OS fixture. Unit
  dev-luma-tester-lumaui-session-cc2dd125.service; artifacts
  /srv/dev/accounts/second/artifacts/dev-luma-tester-lumaui-session-cc2dd125.
  This is launcher integration evidence, not a Luma guest acceptance result.
- Final wheel built and installed into an isolated artifact directory; module
  entry point and desktop file checked. Unit
  dev-luma-tester-package-final-26beb3f3.service; wheel and installation under
  /srv/dev/accounts/second/artifacts/dev-luma-tester-package-final-26beb3f3.
  Wheel SHA-256 d24d76cd4a97441e8ce56f2f9344afdf56c54e5ff682744d0c9040c0ad80ecde.
- `git diff --check` passed. Generated media, disks, keys and packages are excluded
  from Git. All owned validation jobs have exited; no persistent tester service.

## History

- 2026-10-17: read server/project instructions, task ownership, existing twin
  scripts, QEMU documentation and Try Omarchy upstream. Created isolated task
  worktree and branch. Implemented backend, CLI, documentation and package.
- 2026-10-17: user clarified final LumaUI and simple beautiful UX. Replaced generic
  GTK preferences UI with actual shared LumaUI and intent-first flow. Ran actual
  native flow and QEMU integration checks. Guest-image milestone remains explicit.

- 2026-10-17 continuation: user authorized proceeding with guest images. Fedora44
  x86_64 Cloud base downloaded (583729152 bytes), SHA-256 and Fedora44 cleartext
  checksum signature verified against the installed Fedora public key. Reusing
  existing pinned native RPMs; current shared apps .104 will be built from source.
  Guest builder/profile files and optional desktop session capability now in scope.

- Builder r1 booted under KVM and cloud-init completed at 7.53 seconds. Direct
  authenticated SSH succeeded, but the admitted job's inherited SSH config made
  its client exit255 before network connect. An isolated `ssh -F /dev/null`
  succeeded inside the same admission/sandbox policy. Corrected Session SSH to
  ignore host-specific client config and retain an actionable last wait error.
  Interrupted only this owned builder safely; r1 preserved, r2 will use the new
  committed source identity rather than silently rebasing its build receipt.

- Builder r2 passed isolated SSH and cloud-init readiness, then exposed dnf5's
  command-specific `--allowerasing` placement. Corrected the guest recipe to
  `install --allowerasing`; also made the simulator's greetd selection explicit
  if a package preset installed GDM. r2's failure and disk are retained. r3 will
  use a fresh, separately identified builder rather than change prior receipts.

- Active builder r3: source1491e70, unit
  dev-luma-tester-mobile-image-r3-ee9966d4.service, 6 GiB / 12 GiB admitted.
  Output /srv/dev/accounts/second/experiments/luma-tester-20261017/mobile-image-r3.
  Isolated SSH/cloud-init readiness passed; real 549-package Fedora/Luma install
  transaction is running, followed by normal .104 app RPM build/checks. Only this
  owned disposable VM is active for the task. Do not mistake a builder boot for
  final-image graphical acceptance.

- Embedded GTK4 display implemented using GVnc1.5 over a private QEMU Unix
  socket (no TCP listener). Pixel-for-pixel parity with QMP's real screenshot,
  coordinate mapping and input dispatch passed in
  dev-luma-tester-live-display-39d2d2aa.service. The GUI now hosts this viewer in
  the LumaUI frame; source sync example added under examples/tester-hello.
  End-to-end full guest GUI qualification remains pending the actual image.

- r3 completed normally (exit0, 12m35s, 4 GiB peak). Shared apps .104 built from
  source with full normal RPM `%check`, then installed. Exported 1466236928-byte
  mobile base SHA2564a2a394f87b167f5daa420d8e0f0dec671ffc81c4344a3690b8eb1ca973b069f.
  Receipt /srv/dev/accounts/second/experiments/luma-tester-20261017/mobile-image-r3/build-receipt.json;
  package log SHA256d6de30cac03f32843d489914dccefe3f4d63e6a9b9a374c07265e553e9114384.
  No package checks skipped. The build alone is not graphical guest qualification.
- Shared-image profile selection now implemented: mobile/desktop catalogs share
  one base digest, target marker in per-instance NoCloud seed, profile service
  runs before greetd. Profile-only derivation checks the parent image/cohort and
  unchanged app/platform source. GUI/dev watcher now wait for a real Wayland
  session before exposing the display or launching a graphical app.

- Converged r4 build completed: sourcea97d1c6, both profiles in one standalone
  image/catalog, all26 parent component NEVRAs verified inside its builder.
  Actual fresh-session shell/app qualification is next. Added an OS-aware
  shutdown request through authenticated guest systemd because the handheld
  session intentionally intercepts the virtual ACPI power button as sleep/wake.
- Fresh r4 graphical diagnosis reached the actual shell, but showed landscape;
  handheld activates only on portrait monitors. Initial GUI qualification failed
  on unreadable source metadata (0600) and also exposed the VT too early.
  Added tester-only first-paint/portrait extension, public metadata mode0644,
  and nonblocking systemd poweroff to avoid racing SSH teardown. QA failure
  cleanup now first requests graceful shutdown. 32 backend checks passed in
  dev-luma-tester-first-frame-source-077505bd. Graphical fixes await fresh r5.
  Diagnostic screenshots/logs: dev-luma-tester-mobile-diagnosis-fresh-b5189f0a;
  that diagnostic exited1 on the old blocking poweroff transport race.
- r5 built successfully from source1db084a; mobile QA held loading as intended.
  Diagnosis found QEMU's implicit VGA plus explicit virtio GPU (two monitors),
  and private umask permissions on copied extension source directories. Fixed
  single GPU argv and installed extension permissions. r5 QA was stopped after
  requesting guest graceful poweroff; it is not a pass. Preparing r6.
- r6 booted one portrait GPU; first-paint marker worked. Actual mobile QA found
  handheld State ERROR: missing org.gnome.shell.extensions.dash-to-dock schema.
  Completing the shared extension dependencies (pinned Luma dock built from its
  checked upstream SRPM/patches, tiling toggle/shell) and SDK in r7. The app
  watcher also exposed a systemd transient WorkingDirectory shorthand rejection;
  now resolves HOME inside the authenticated guest shell with quoted argv.
  32 backend checks passed dev-luma-tester-app-launch-check-bb7f77b9. r6 is not
  accepted. Diagnostic reboot preserved its SSH host pin; graceful poweroff
  succeeded in dev-luma-tester-handheld-errors-8128afe5.
- r7 installed all29 pinned inputs and built/installed Luma dock14 normally.
  Export failed on a producer-only duplicate subprocess `check` argument while
  probing retained dock artifacts; corrected the probe to call subprocess
  directly and improved builder error cleanup to request systemd poweroff.
  No r7 image accepted/exported. Retrying from verified r6 as r8.
- r8 stopped during SRPM extraction with SIGPIPE141 under pipefail. Switched
  rpm2cpio/cpio to the same intermediate payload-file pattern used by the normal
  repository dock builder. Retrying as r9. No r8 image exported.
- r9 built successfully (sourceb1ec3ba,29 pinned RPMs + source-built dock14).
  Mobile full launcher/guest/app edit/clean shutdown PASS in
  dev-luma-tester-mobile-qualification-r9-78dc75cf (46.7s,739 live frames).
  Actual screenshot inspected: handheld home grid/dock and restarted app text.
  SDK CLI0.1.0 verified. Example minimum width460 exceeded the432 mobile viewport;
  lowered its declared minimum to280 before final packaged qualification.
  Added self-contained Linux directory producer, relocatable catalog/runtime,
  bundled distribution tools and notices; packaging validation is next.
- Portable r1 assembled (1.7GiB), but packaged QEMU boot failed before firmware:
  omitted Fedora ipxe/qemu ROM aliases. Added dereferenced ROMs into the bundle's
  QEMU firmware search directory, with package/license provenance. Native r9
  mobile pass remains valid; portable r1 is not accepted. QA now reports an
  early launcher exit immediately, rather than waiting for its full deadline.
- Portable r2 exposed a relocation guard issue when reusing r1's unbooted
  workspace: URL changes rejected even though digest/size/boot identity matched.
  Guard now allows only URL relocation; all other identity fields remain pinned.
  New meaningful relocation test retains the overlay inode/key; all33 checks
  passed in dev-luma-tester-relocation-check-92596565. AppImage bootstrap uses
  the original executable path for stable CLI commands/catalog identity.
- Portable r3 mobile PASS: dev-luma-tester-packaged-mobile-r3-7c1a3f95, actual
  bundled module path asserted, bundled QEMU/firmware/SSH/rsync booted guest,
  app source edit restarted successfully, graceful shutdown saved overlay.
  Screenshots inspected; example now fills portrait viewport. One first-login
  GDM lock hint covered the home screen, despite this preview's explicit unlocked
  profile. Session wrapper now records that configured preview state before
  Shell starts, using upstream's one-time hint marker. Preparing final r10.

- 2026-10-17, later user request “Launch it here”: Dell desktop route verified
  active (nick on nicks-xps, existing Wayland session). Copied the exact qualified
  AppImage into /home/nick/.local/share/luma-previews/tester-20261017 and verified
  SHA256SUMS successfully. Launched through the registered dell-preview helper
  with separate config/data/cache/workspace directories, using extract-and-run.
  Unit luma-preview-tester-hotfix-20261017-d04c82d2.service reports active/running.
  This is the real logged-in Dell desktop, not a server virtual display. No Dell
  automated app/UI tests ran; no production app/profile or phone was changed.
  Manual review feedback is pending. The launcher is left open; no guest was
  started automatically on the Dell.

- User review rejected the displayed Mobile UI. Prior boot/app checks did not
  establish product parity: the guest selected the GNOME handheld prototype
  instead of the actual Luma Phosh/Phoc phone session. Prior Mobile acceptance
  claims are withdrawn. The user also reported unusable desktop window dragging;
  current QEMU uses the software-only 2D virtio GPU and forced software blur.
  Corrective work traces the current phone source branch/package cohort and
  qualifies VirGL rendering plus a lighter nonaccelerated fallback. No updated
  candidate will be called Luma Mobile based only on portrait dimensions or an
  enabled extension. Existing Dell preview has exited; no replacement launched.

- Graphics correction in progress: auto VirGL with a bounded EGL startup probe,
  explicit software/required-VirGL controls, fallback receipt, and bundled Mesa
  driver/module closure. Removed forced software blur. The embedded viewer now
  batches texture copies on the GTK frame clock and prefers uncompressed local
  transport. 39 backend checks passed dev-luma-tester-graphics-checks-73fef8a7;
  exact viewer/QMP pixel parity and input passed
  dev-luma-tester-viewer-clock-r2-54a6a592. Native guest EGL reported VirGL backed
  by server RTX3090/NVK. Motion diagnostic observed375 frames/7.23s and visible
  window movement, but that diagnostic's exit failed on expected shutdown
  disconnect handling; do not call the full job a pass. Harness correction and
  before/after measurements remain in progress. No new Dell candidate delivered.
- Producer now exports Desktop only and refuses the rejected GNOME handheld
  prototype. Mobile readiness requires the actual Phosh process/native frame
  marker. Current phone source was located and pinned externally at branch
  fp6-25, commit2bf0a1db172b47308b1439f5b73b3e6e20cf62c3 (Phosh73, Phoc12,
  shared platform114, shell-state36). Private source stays outside public Git.
  Qualified keyring3 x86 artifacts located; real phone stack still needs native
  x86 component builds, complete normal checks and actual UI parity review.
- Retired7 owned obsolete generated artifacts in
  dev-luma-tester-retire-obsolete-r2-caefe132 after process/backing checks;
  receipts, current r10 baseline and original AppImage review evidence retained.

- r11 Desktop image built normally from r10's exact receipt/cohort in
  dev-luma-tester-desktop-r11-5ab3e282; sourcea9a3c22a, image SHA256
  85635f37e85459583e826759611950238e4c388b3e6a570af14411c899023180,
  1438449664 bytes. Only Desktop catalog emitted. Producer removes the earlier
  handheld prototype and forced blur. Retired stopped r10/r11 builder-state
  copies after backing/process checks in dev-luma-tester-retire-builders-afb1368b.
- Packaged accelerated desktop/app restart reached visible hot fix and verified
  guest VirGL, but dev-luma-tester-packaged-accelerated-c8f9b00b failed because
  QEMU's GL scanout has no QMP screendump surface. Added a GUI-independent GVnc
  full-frame PPM fallback; exact fallback/QMP pixel parity and input passed
  dev-luma-tester-rfb-capture-958668a0;39 core checks passed
  dev-luma-tester-capture-core-8b70727a. Repackaging and complete qualification next.
- Dell readonly command receipt confirms the user's original runs were fully
  local: KVM, host CPU,4 vCPUs,4096MiB,2D virtio GPU for both targets. No streaming.
  Server diagnostic frame counters are not Dell benchmarks; legacy viewer counts
  include multiple intermediate texture copies per displayed frame and must not
  be compared as visible frame rates.
- Actual phone component build admitted as
  dev-luma-tester-phone-components-138328bf (6GiB memory/5GiB disk; timeout1700s).
  Separate owned experiment: /srv/dev/accounts/second/experiments/luma-tester-phone-20261017.
  Immutable upstream tar and explicit x86/VM-runner adaptation receipt retained
  outside Git. Normal payload/check code is preserved. Starts from verified
  Fedora44 Cloud, never a user/phone disk. Qualified keyring3 x86 RPM/SRPM copies
  matched retained receipt hashes; shell-state36/Phoc12/Phosh73 builds pending.

- Corrected GPU package passed the full launcher/guest/app workflow with no
  errors in dev-luma-tester-accelerated-full-r2-b97212e1: VirGL mode and native
  guest VirGL renderer verified, source edit changed app PID2559→2868, screenshot
  fallback worked, graceful shutdown succeeded. Guest r11 digest unchanged.
  Portable source603b4258, output Luma-Tester-graphics-r12. AppImage SHA256
  b8c99359b0235ba39b124726399fcb084d228c51ab533396d15590108a975b8c;
  actual AppImage entry PASS dev-luma-tester-appimage-graphics-entry-89e29000.
  Not delivered: final availability UX polish selects Desktop and disables the
  unavailable Mobile choice until a native phone image is imported. Existing
  native flow/narrow/dark checks PASS dev-luma-tester-available-choice-ui-54262d4b.
- Phone source component attempt built shell-state36 successfully and Phoc12
  x86 RPM/SRPM with all19 normal Meson checks OK. Phoc wrapper exited1 on its
  stale ARM filename expectation after successful rpmbuild, so the overall job
  is not a pass. Source/check evidence retained in component-artifacts.tar.
  Resume dev-luma-tester-phone-components-r2-20d3bb70 validates all19 check
  results, exact x86 headers, retains QA/SRPM and continues normal Phosh73 build.
  This is component evidence only; native phone UI parity still unqualified.

- Actual phone renderer component build completed successfully in
  dev-luma-tester-phone-phosh-oracle-df2fc3e4. All13 required normal suites passed,
  plus scale3 Library composition and all7 scale3 drawer traces, negative provider
  control and installed source/payload assertions. RPM/SRPM/normal QA retained in
  component-artifacts-r4.tar, outside Git. Phoc12's19 checks remain passed.
  This does not yet qualify a booted phone image or the newer shared apps/SDK.
- Touch foundation delivered genuine Linux EV_ABS multitouch positions, tracking
  ID begin/end and BTN_TOUCH state in dev-luma-tester-kernel-touch-d9fa9e09.
  Mobile viewer now maps press/drag/release/scroll to that API; native shell
  gesture checks remain pending.39 source checks passed
  dev-luma-tester-touch-source-checks-4dee468d; existing viewer/QMP/capture pixel
  parity and input passed dev-luma-tester-touch-viewer-4175f787.
- Dell transfer attempt initially hit the known system SSH config ownership
  issue inside devctl; explicit own SSH config resolves that. Actual route then
  timed out before creating/copying the new preview. Normal dell-preview status
  also timed out, confirming delivery remains pending. No new app/VM launched
  on Dell; wake/connect async access question remains pending.

- After successful r13 delivery, user confirmed Desktop works great and asked
  to replace the Red Hat17-inch label and reduce the cramped default view.
  r15 now has a proper virtual EDID, guest hwdb identity and1080p default.
  r14's live check caught LMA Desktop rather than Luma Desktop: editing pnp.ids
  alone does not populate GNOME's udev hardware database. Fixed that explicitly
  with guest-only systemd-hwdb input; r15 passed the exact label/mode/scale check,
  native VirGL, app sync/edit/restart, accelerated capture and shutdown.
- AppImage entry first exceeded its unchanged45s startup bound under a2GiB
  allocation while extracting into temporary memory-backed storage. The same
  exact AppImage passed in12.7s with4GiB and a disk-backed task TMPDIR. No timeout
  or assertion was weakened. Retained both results. Failed/obsolete r14 exports,
  disposable builder disks, final disposable QA state and squashfs staging were
  retired after process/backing checks; receipts, packaged images and check
  screenshots/reports retained. Disk reserve was preserved.
- dev-luma-tester-dell-display-delivery-r15-9ea583bc failed on SSH banner timeout
  before remote mkdir/copy. No display update delivered or launched; r13 remains
  the last delivered candidate. Retry /srv/dev/accounts/second/artifacts/luma-tester-deliver-display.py
  when the tunnel returns, then use dell-preview with a separate
  tester-display-20261017 workspace. Preserve any running r13 guest/user work.

- 2026-10-22: user requested latest tester launch. Dell graphical session2
  available, no tester/QEMU process running. Delivered the previously qualified
  r15 AppImage with SHA256b46a3ccf8c7e245e89144ed49e7d9bf901f3701ee8cbef16e8f31e277e79929d,
  verified remote checksum and launched the launcher using the registered helper.
  Unit luma-preview-tester-display-20261022-a4654df3.service; workspace
  /home/nick/.local/share/luma-previews/tester-display-20261017/workspace.
  Existing older preview data preserved. This is delivery for manual review,
  not a new automated qualification or an OS release.
