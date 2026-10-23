# Luma Tester — Linux first

- Owner: Codex, luma-dev-2, current tester chat.
- Status: active — Tester0.7.0 Mac r2 launched; Linux r32 snapshot update qualified and registered without stopping the user's guest. Published ARM Mobile payload verified; virtual boot adapter build underway, not yet boot-qualified.
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

2026-10-23 update: Mac r2 is delivered and launched. The finish controller's
mounted-DMG verification and Linux r32 build/UI/delivery stages all passed;
private `artifacts/tester-mac-live-finish.json` records the completed stages.
Linux r32 registration preserves the existing workspace and running guest;
`gui_reopened:false` is intentional. The current source suite has89 passing
tests, including actual Gio desktop launch argv/state preservation.

Current priority is Mobile ARM discovery/boot on Mac. The public Mobile file
exists and was downloaded with its published SHA256 verified. The Hub catalog
currently lists only x86 desktop ISOs; Tester also filters to ISO media and
suppresses published Desktop entries in Mobile. No qualified ARM Mobile profile
was bundled with Mac r2. This is a Tester integration gap, not a missing ARM
userspace or a reason to substitute a plain Fedora/desktop twin.

Owned HDD `mobile-arm-alpha1` retains the pristine compressed public source,
strictly decoded4096-sector GPT raw image, separate reflink `adapter.raw`,
inspection reports and `virtual-boot/{vmlinuz,initramfs.img,kernel-inputs.json}`.
Published OS is genuine Luma Handheld20261015.21, OSTree deployment97268a77…,
Fairphone kernel7.1.2. A generic7.2.11 Fedora ARM kernel/module cohort is added
only to the disposable derivative. Receipt hashes verified; RPM checks reported
header/payload digests OK, not release-signing qualification. Native dracut via
qemu-user with the published OS's ostree module passed in initrd-r2-07d8a0aa;
first attempt stopped after missing private /dev/fd builder setup was found.

Private helpers under account artifacts: `tester-arm-mobile-*.py`. Root workers
are bounded/privately mounted via `tester-arm-mobile-root-job.py`; read-only
inspection units passed. Current configure job6f10ba01 only reads private
helpers and modifies the disposable derivative; it does not lease/read source.
Next: inspect configure result and normal-policy labeling, then run prepared
native Mac worker/controller through normal admission. Ports10851/22251 are
reserved in those helpers only; no service has been started on them yet.
Native boot, rendering, developer bootstrap, final UEFI/profile packaging and
catalog integration remain UNVERIFIED. Do not publish/register this prototype
as a runnable Tester image yet. No Dell guest or physical phone was touched.

Earlier delivery milestones below are historical; the current ones above and
the final dated history supersede their versions and pending-stage statements.

Follow-up: generic initrd and configuration stages passed. Native M4 test r1
stopped at EDK2's narrow432-pixel graphics-console assertion. r2 used864×1920
at scale2 and reached genuine ARM Luma20261015.21, SSH and Phosh. r3 additionally
waited for full Home rendering (actual screenshot inspected) and verified a
graceful guest shutdown. Native unit `tester-arm-mobile-native-r3-f47c19ec`,
private HDD `mobile-arm-alpha1/native-r3`, all passed. Disk was temporarily served
over encrypted NBD10851; guest CPU ran on M4/HVF. No final local-disk or Tester
GUI qualification is claimed yet. All scoped QEMU/NBD/tunnel processes exited.

Tester0.7.1 source now supports catalog `tester_image` qcow2 entries with validated
boot profiles, matching experience/CPU selection and authoritative artifact
bytes. Phone flashing packages remain excluded. Optional bounded boot display
and control capabilities prevent older images receiving unsupported Home or
resize commands. Public Hub schema/artifact agreement remains separate; the
current endpoint has no virtual Mobile entry.94 source tests PASS in
`tester-mobile-profile-checks-7d0f356e` / root7422c2efea (journal confirms94/OK).
Next: finish self-booting private ARM image and per-session bootstrap, then build
and qualify the Mac app with that local image. No generated package is in Git.

The interface requires real LumaUI. Current source uses compact SB1 SidebarRow
with RowLead.icon in the destinations NavigationSidebar, the shared toolbar
Menu for build choices, and Card + LayerHost for saving presets. Welcome leads
to Try/Develop's shared simulator workspace. All device destinations can be
selected; unavailable ones show Soon and an explicit no-build state. Running
sessions show a LumaUI Toast when a different device is requested. No device
switch is silently represented by a different renderer or CPU architecture.

Backend accepts trusted local manifests referencing HTTPS or local standalone
qcow2 files, checks SHA-256/size, maintains private overlays/keys/seeds and locks,
chooses KVM or TCG, exposes localhost-only SSH, QMP controls/screenshots, source
sync and a foreground app restart watcher. CLI remains independent of GTK/LumaUI.
The wheel is a launcher package, not a self-contained QEMU/LumaUI/guest bundle.

Latest delivered app is **Tester0.2.0 r22**, source8a8bee7d, with anonymous Hub
attribution. AppImage283728376 bytes, SHA256
005dee354faca7512513be2c008a2167a94bdb068e44625a44a88440ef59b9dd.
Artifact: owned HDD appimage-tickets-r22; server packaged QA7a0cffba and live Hub
21d9f1d7 passed. Dell delivery95f663b6 verified; launcher aa71da66 is the latest.
See final history and docs/developer-platform/tester-hub-handoff.md. The following
r21 milestone describes the unchanged genuine Mobile/Desktop guest assets.

Current delivered Linux x86_64 candidate is combined **Desktop + genuine native
Mobile r21**, with a bottom LumaUI ActionCenter and framebuffer silhouette shadow.
AppImage is283720184 bytes; images are separate sidecars. Exact AppImage SHA256
is e235a784e939504e7fc8acda5a2b1fea4c61e8b13aa35b2c8540a21c9c9582fc;
source5240249d. Artifact: owned HDD appimage-native-light-r21. AppImage entry
cdae71ad and full packaged native Mobile18344c7b PASSED. The latter exercised
Home/Library touch, native OSK search, Home return, visible app hotfix and graceful
shutdown. Mobile QA used software graphics; Mobile GPU and physical phone/ARM
qualification are not claimed. Desktop uses the unchanged qualified r15 disk.

Deliveryfa42204d verified the app and both image checksums on Dell. The new app
is in tester-native-light-r21-20261022. Launcher
luma-preview-tester-native-light-20261022-bcefd27e.service was active; registration
luma-preview-tester-native-light-register-20261022-59f80fef.service exited0.
Existing tester-display-20261017 config/data/cache/workspace preserved. No guest
was running before launch; no guest auto-started. All automated tests remain on
this64GiB server; QEMU runs locally on Dell when Nick starts a guest there.
No Dell automated testing. Tablet/TV/smart-device images remain unavailable.

Hub ticket integration is implemented and live-qualified in0.2.0 from the supplied Hub
handoff and the exact API.md on nmcmil/luma-website work/download-tickets. The
live catalog currently offers installer ISOs, not ready-to-run developer disks.
Do not use those ISOs as cloud-init qcow2 inputs or attribute private disks to an
unrelated public ISO. Source/private previews use source:test; only the release
producer opt-in changes staged attribution to tester. Automatic update prompts,
ISO installation, public developer-disk/release indexes remain separate work.

Qualified Desktop guest / earlier display candidate:
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

- 2026-10-22: removed welcome subtitle, footer and both choice descriptions per
  user feedback. Menu content centers vertically, retaining scrolling at narrow
  sizes. Added an original vector icon using existing Prairie gradient/metal
  styling, desktop/phone motif, wired through frame, desktop entry, wheel and
  portable/AppImage icon paths. Native screenshots inspected and packaged
  screenshot text confirmed only the greeting and two choices. New launcher
  delivered/opened for manual review; no guest or other app interrupted.

- 2026-10-22 spacing follow-up: card child lacked vertical expansion, leaving
  extra space below its content. Actual GTK allocations now show46px above and
  below the84px icon/label group in each176px card; icon/label gap16px. Horizontal
  insets24px, inter-card gap16px, heading gap32px. Native screenshots inspected,
  keyboard/navigation/narrow/dark checks passed. Replaced the earlier device
  artwork with a simple orange dot in the canonical wordmark's #FE6819.
- Packaging at2GiB was killed by systemd-oomd; retry with admitted4GiB completed
  in10.8s. Actual AppImage entry passed15.2s. Removed stopped QA extraction and
  the superseded r15 AppImage to preserve disk reserve; r15 portable, guest,
  receipts, checksums and Dell copy retained, as is the complete r16 AppImage.
  Failed/completed intermediate squashfs files removed after worker exit.
- User saw the old Dell launcher while packaging was still in progress; stated
  that explicitly. After exact r17b checksum verification, stopped only the
  previous tester launcher (no QEMU running), reopened r17b with existing private
  config/data/workspace. New manual preview unit58e1db70, above. No automated
  app/UI tests ran on Dell and no guest/app work was interrupted.

- 2026-10-22 target-page/menu/dock follow-up: source8ce5a6f0 passed39 core
  checks, native flow/selection matrix/menu/hover checks and actual AppImage
  entry. r18 AppImage SHA256
  081eae9be76443b5c0de73335f5131b0f0ef06659b610f0a60ff57e24c57850b
  delivered with verified remote hash. Registered io.luma.Tester.desktop and
  canonical icon using install-desktop (registration unit524372b4, launcher
  unit082fa5d0). Guest remains qualified Desktop r15. No Dell automated tests.
- Native phone component progression: GTK21 normal build/checks passed in
  dev-luma-tester-phone-gtk4-r2-a90db9d5. Adwaita55 normal build/checks passed
  in21790213; that combined job failed afterward on missing platform git
  dependency. Platform/SDK114 full normal check passed in2ed65fa8. Native
  Stevia/LatinIME x86 keyboard r3c48cecc1 passed all9 normal suites. Native
  archives/receipts live under the owned phone experiment and secondary task
  subtree. Core117 exact normal-qualified noarch RPM/SRPM reused from retained
  native build, copied with receipts. Actual native phone guest not yet booted
  or qualified. Mobile profile scripts in working tree are unfinished.
- During UI QA, an initial one-off hover script set a server dconf value before
  activating memory backend; corrected the script. Prior server preference
  unknown, so no blind reset. Dell preferences were not changed by this script.
- User expanded workspace direction: persistent device sidebar shared by Try
  and Develop, compact run/pause/shutdown, image selection, future presets and
  hardware controls. Workspace implementation in progress; initial native
  welcome/navigation regression4ab44b1a passed. Tablet/TV/Smart Device must
  remain unavailable until dedicated real profiles exist. No fake phone or
  architecture equivalence. Actual Mobile boot remains the next required gate.
- Workspace UI now uses shared NavigationSidebar and IslandOverlaySplitView.
  A narrow-window allocation failure in initial49f84b90 was fixed by the kit's
  overlay split; inspected dark screenshots now have the proper frame surface.
  Final native flow3ca47bd8 and actual no-OS QEMU workspace controls/image
  choice/Try-Develop/narrow-overlay checka3cd9a3c passed.39 core checks passed
  b854d363. Native phone producer/configuration added as a candidate; syntax
  checks passed but composition/boot still pending. Keyboard corresponding
  modified source/notices collected at exact pinned commits in07fa6fd0.
- Named image/profile/mode presets added. Actual native UI save/restore plus
  QEMU controls passed7fd34a05;3 persistence/stale-nightly/name-integrity cases
  passed7b45ca19. Native phone r2 compositionb16afd2c passed after the first
  attempt correctly rejected a missing exact Fedora Phosh0.55 provider. Pinned
  stock Phosh/libphosh downloaded from official Koji with RPM digest checks.
  Native UI→real Phosh→app edit/PID restart→shutdown passedd5f2e5f7 in45s.
  ImageSHA b3915990578cfb7d4226f94b9e3abf280fa991b545f2303e62af903c59a2aa51.
  Screenshot review caught missing shared/handheld defaults (empty black Home).
  Correcting input cohort to include exact phone source presentation profiles;
  r2 is not a finished Mobile candidate. Gestures/keyboard still pending.
- r3 exact phone profiles compose14d11307 passed, but stricter Home/Library
  touch qualification61ea3b06 failed before gestures: a real native lockscreen
  still covered Home. Diagnostics89188ab4/629d9e8b confirmed matching defaults,
  wallpaper and native /opt library/session. Exact pinned upstream main.c shows
  unlocked is an explicit flag: omitting --locked does not start an unrecognized
  display manager unlocked. Preview override corrected to --unlocked; physical
  --locked service unchanged. No Mobile candidate delivered yet.
- Explicit --unlocked r4 boots genuine populated phone Home and real Library;
  guest VirGL verified. Strict touch QA initially expected a literal Library
  heading, but actual source UI uses an alphabetic list and Search apps; that
  oracle was corrected without changing payload. Downward Library drag also
  folds the outer launcher; the actual Home bar is the source-defined Home
  action and will be checked explicitly. Search tap focuses its entry but OSK
  remains hidden in0f9c07a7. Phone native IM routing now being corrected from
  desktop-base IBus selection to Wayland. Separate real issue found: fallback
  screenshot GVnc entered the GUI's GLib context from a worker, causing coroutine
  warnings. Capture now isolated in a subprocess; native qualification pending.
- R5 native IM image composea5607f07 passed.42 core/preset checks passed
  bb706b75, including isolated capture entry. Keyboard suppression identified
  from exact Stevia schema: ignore-hw-keyboards defaults false, and QEMU has
  hardware keyboards. Actual0212398d shows native OSK visible after enabling
  that scoped setting in QA. Added the same simulator-only default to source.
  Native Home bar return after OSK still folds the launcher; keyboard navigation
  and typing now under qualification. Graphics probe moved after UI actions to
  avoid contaminating the navigation state. Earlier failed Home assertions and
  screenshots retained. No Mobile image delivered yet.
- Corresponding source collectionc331ab42 passed,71MiB under owned HDD artifacts.
  Archive worker0783d104 hit its1GiB memory limit while moving immutable old
  phone exports; originals retained until exact verification. Retryfd1042cc
  uses3GiB and verifies any existing copy before replacing a source with a
  symlink. Stopped r18 AppImage QA extraction retired in f2eb4c41; root reserve
  preserved. Never move live guest backings.
- R6 composecff60807 passed with keyboard default in its system profile. Native
  touch typing filters Library to Notes and presents LatinIME suggestions;
  screenshot visually inspected (345135b8 failed only on case-sensitive OCR).
  Keyboard/Home return still exposes a native fold/state issue; added explicit
  Home API/CLI/toolbar using Phosh's real overview property and keyboard bindings.
  This preserves the phone renderer and provides a recovery/navigation action.
  Startup uses the same native action before showing Mobile. Qualification
  pending; do not claim Home issue fixed from syntax checks.
- Architecture selection/real LumaUI navigation b9453626 passed including
  Windows-style ARM64 spelling; desktop native preference and ARM phone
  preference/fallback checked. Source remains Linux host only. Old immutable
  phone exports successfully archived with exact SHA checks in fd1042cc; failed
  1GiB archival attempt remains recorded. Root26GiB/secondary22GiB at last check.
- Home keyboard-binding qualification73934907 still failed returning from OSK:
  OverviewActive alone does not prove the Home page is visible. Replacing that
  approximation with an optional GTK3 automation module loaded solely into
  the tester's actual Phosh process. It dispatches the existing native Home
  action (including after IME layout settles); no alternative phone UI or
  private renderer source is copied into public Git. Module compile/native
  qualification pending. Prior Phosh RPM and its full normal checks unchanged.
- First concurrent Home API attempt17adefe3 hit same Application ID on a shared
  test D-Bus and exited before qualification; isolated each task UI run with
  dbus-run-session in the owned external wrapper. It did not pass. Host control
  layout/actual QEMU lifecycle check aa21afd4 passed.

- 2026-10-22: r7 native control module compiled39ebcaa3 and image70440a9c
  composed;8eebf3d8 and paired host/viewer587ed1fd still show blank Home after
  keyboard dismissal. Repeated-state48c19f50 rejected changed SSH host key;
  retained pin/evidence. Scoped cloud-init ssh_deletekeys:false added to prevent
  regenerating an established preview identity; no host-key checking weakened.
- r8 module synchronization compile8d64571c and image8a17fe35 passed, source
  ff34ca84, imageSHA d05c666504252d598cc0f3e9975896299a801309a85fbe2d496f45d275d04c1e.
  Native8d8167cb hit a real Nouveau/Zink MMU fault and VK_ERROR_DEVICE_LOST;
  worker shutdown terminated stuck QEMU. No completed native qualification.
  Software-host retryaa2bd6dd reached genuine phone but initial Home remained
  blank. Do not describe r8 as qualified or deliver it as finished Mobile.
- Native debug probes9d7e1ff2/fbebf949/af3682b0/1ebf6e6d/f0d50a21/d49d6bf0
  trace a transient phosh toplevel created after Home unfolds; its arrival folds
  Home, then it disappears. GTK reports an unmapped subsurface parent. Disabling
  tooltips or GDK GL did not resolve it. Debug modules/settings only in retired
  QA overlay; public renderer and exported image unchanged. Home issue remains.
- Immutable old final/r13 AppImages archived to owned HDD with exact existing
  SHA256SUMS verified in0123524d; original paths preserved as symlinks. Temporary
  root-space pressure during stuck renderer cleared with worker shutdown.
- Preparing a Desktop-only sidebar preview for Dell while genuine Mobile stays
  gated. New bundle must not claim phone qualification. Bound mksquashfs memory
  to1GiB in addition to two CPUs, instead of its host-relative default.

- Desktop sidebar r19 built886f19db, actual AppImage entry692652dd and packaged
  QEMU/no-OS control UIce20d968 passed. Delivered0a655b06 with exact checksum
  e58651276a3297167293937560ef377b5340ebd7fd2060e10c6f0dc98a273a67;
  launched on Dell as luma-preview-tester-workspace-20261022-e5e74cd3.service.
  Register4c8b3984 exited0. User rejected large legacy rows as non-LumaUI and
  reported inert device destinations. Correcting source now; r19 is superseded
  visually, not an approved final design.
- Native trace5c5e319b definitively identifies the phantom window as
  GtkTooltipWindow (real Gdk owner and stack evidence), not GL or a real app.
  Deprecated gtk-enable-tooltips setting was ineffective. New module11229180
  clears pointer tooltips recursively in the touch simulator shell. Normal
  compile950baa93 passed. Three repeated actual native Home actions643ffcc8
  now retain the populated real Home; screenshot inspected. Keyboard return
  and an exported image with this module still need full qualification.
- Corrected UI uses actual SB1 destination glyphs/36px rows, shared Menu build
  picker and native LayerHost/Card preset modal; removed sidebar form stacks
  and full-width footer buttons. Unavailable device selection and live-session
  Toast feedback implemented. Native menu, preset, row metrics, empty-device
  state, real no-OS QEMU lifecycle and narrow sidebar passed b81dfe4d. Earlier
 665346aa exposed an invalid type role and e00c392a exposed a capture helper
  selecting a destroyed popup XID; both corrected and retained as failures.
- Older immutable phone exports r5–r7 archived with exact manifest SHA and
  standalone/backing checks in d2f42178; original paths retained as symlinks.

- Compact r20 sourcec3aff3c3 built737a9342, actual AppImage entryf401d84d passed.
  Packaged UI0dfb5714 collided with concurrent Mobile QA on localhost2228;
  it did not pass. Sequential retry8515ff0c passed menu, modal, row metrics,
  no-build state, busy-session notice, real no-OS QEMU controls and narrow view.
  Desktop and Mobile GUI guest tests must be serialized even with isolated D-Bus.
  Delivered328f287b and launched on Dell4b1d959f; normal icon registration0b2f5dc5
  exited0, no QEMU was running before launch. User preview remains Desktop only.
- Phone r9 image3d931df7 built from exact native input bundle and tooltip module;
  imageSHA e4403e122bff15084d7b63c06209a9b88cf1d71a960c9f4eb9c19a74b1558dab.
  Initialefbe9a33 Home passed but synthetic swipe was held too long on the
  software renderer and did not open Library. Bounded eight-update quick swipe
  uses the real native gesture; sparse UI OCR11 reads actual labels more reliably.
  Native workflow323f85cc PASSED: genuine Home, touch Library, native Stevia OSK,
  on-screen Notes typing/filtering, Home after keyboard dismissal, actual app
  source sync/PID restart, visibly rendered Hot fix is live, graceful shutdown.
  This QA used software graphics because host Nouveau had faulted; do not set
  guest_virgl_renderer_verified or claim Mobile GPU/physical Fairphone qualification.
- Persistence6d45d783 PASSED on the same r9 overlay: project and SSH key/host pin
  preserved, real pause/resume and second graceful shutdown. Exported r9 has no
  debug module or runtime debug settings from the old r8 QA overlay.
- Combined Desktop+native Mobile r21 packaging now planned, with exact native
  corresponding sources, interaction/persistence receipt bindings and explicit
  software-host qualification metadata. r20 Desktop disk/graphics remain unchanged.

- Bottom ActionCenter and framebuffer silhouette shadow implemented at user request.
  Shared native bar controls are hosted in a ToastHost beside the sidebar; guest
  area reserves the bar's space. Shadow uses LumaUI float-shadow colours around
  the actual contained screen bounds; framebuffer contents are never blurred.
  Actual native Mobile workflow e488cff8 PASSED again with the new host UI,
  including keyboard/Home and visible app hotfix. Source compact UI464dc11b
  PASSED after final modal/focus/nowrap composition; native controls remain used.
- Lightweight packaging defaults to HTTPS image downloads, with explicit
  --offline and --sidecar-images producers. Sidecars use checksum filenames and
  stay outside the AppImage; source cache/workspaces keep their existing identity.
  Asset checks8066a390 and47 core/preset/asset tests6644ed0c PASSED, including
  download fallback, metadata immutability and path/symlink containment.
  No published image feed or automatic latest-version prompt is configured;
  do not present that future service as implemented. Private r21 uses separately
  delivered qualified images; no public image upload or release authorization.

- r21 build827a8915, actual AppImage entrycdae71ad and packaged native Mobile
  18344c7b PASSED; verified deliveryfa42204d and Dell GUI launchbcefd27e.
  AppImage283720184 bytes, SHA e235a784e939504e7fc8acda5a2b1fea4c61e8b13aa35b2c8540a21c9c9582fc.
  Mobile is actual native phone source2bf0a1db with qualified r9 disk; hardware
  GPU qualification remains open. New host UI and both guest sidecars delivered.
- Initial Hub/core/preset/asset58 checks b54c47f5 PASSED. Resumable anonymous
  downloads, source:test default, native-arch catalog sorting, ARM emulation notice,
  and exact published artifact associations implemented; final checks pending.
- Disk admission refused scratch cleanup estimates; manual bounded removal only
  retired verified passed/stopped owned AppImage extraction TMP forcb51e3e1ae,
  dcda5d907a and1bf80d616a. Logs, screenshots, packages and receipts retained.
  r18 archival firstc5ff4207 failed because ordinary runner makes HDD read-only;
  retry39ac8a94 uses the owned HDD storage helper. Full live7GB ISO analytics
  qualification still subject to ordinary reserve/admission, no bypass requested.

- Final60 component checks, packager compile checks and native workspace/no-OS
  QEMU lifecycle0898fed4 PASSED. Simulated ARM-host UIbc3c9ebb PASSED: absent
  ARM gives a coming-soon notice plus explicit Start with Emulation; adding an
  ARM profile selects it automatically. This is selection/UI evidence, not ARM
  execution qualification. r18 archive39ac8a94 PASSED with every file SHA and
  symlink target verified; original artifact path preserved as a symlink.
- Live ticket f75f75ff recorded a real403 failure: CDN rejects Python-urllib UA
  (Cloudflare1010). Added honest Luma-Tester/version UA; same anonymous test
  ticket retained. Full live retry21d9f1d7 downloading actual published7GB ISO;
  completion/failure verification pending. Production source never used in QA.

- Tester0.2.0 source8a8bee7d built in6f63b51b; first build attemptd5694db8
  exited75 on the shared artifact-path lease while live analytics QA ran. Retry
  used this source worktree's lease, with no source edits during the build.
  AppImage005dee354faca7512513be2c008a2167a94bdb068e44625a44a88440ef59b9dd,
  283728376 bytes, owned HDD appimage-tickets-r22. Guest assets unchanged.
- Live Hub retry21d9f1d7 PASSED: actual7,198,212,096-byte published ISO download,
  exactSHA/size, completion, idempotent second report counted:false, cached reuse
  issued no ticket, separate forced network failure retained as incomplete.
  All source:test. Same ticket from real403 failure recovered successfully.
  Private evidence root-jobs/luma-tester-user-7d1f4b7843/qualification.json.
- Packaged0.2.0 QA7a0cffba PASSED actual AppImage entry/tool discovery and full
  genuine native Mobile workflow including touch, OSK typing, populated Home
  return, visible app source hotfix and graceful shutdown; software-host QA.
  Evidence root-jobs/luma-tester-user-de63b71795/qualification.json.
- Delivered95f663b6 verified app plus both existing sidecars. Initially preserved
  the running Dell Desktop guest. When metadata showed that guest and r21 app had
  stopped, launched0.2.0 via luma-preview-tester-tickets-20261022-aa71da66.service.
  Registrationluma-preview-tester-tickets-register-20261022-c1d23768.service
  exited0. Candidate tester-tickets-r22-20261022, existing isolated workspace kept.
  No Dell automated tests. No production analytics, public upload or signing.
- Read-only Dell runtime metadata before its Desktop guest stopped showed
  software fallback with an empty GPU initialization failure detail. Do not claim
  that running session was VirGL. Server QMP-stdio probe fee2a635 returned valid
  status5/5; it does not reproduce that Dell GPU failure. GPU fallback diagnosis
  remains open; no speculative fix or Dell automated graphics probe performed.
- Hub handoff docs/developer-platform/tester-hub-handoff.md records concrete
  integration and remaining catalog agreement. Public IDs currently name ISOs;
  instant-start developer disks need distinct experience/architecture artifacts.
  Current manifests cannot honestly attribute private qcow2 disks to those ISOs.
  Automatic update prompts, signed public developer/release feed and non-Linux
  host qualification remain open. Next work: graphics fallback diagnosis and
  qualified developer-disk publication through the authorized integrator.

- 2026-10-22 next UX iteration IN PROGRESS, uncommitted source: user requests
  live Try/Developer sidebar toggle; remove central Project card; expose SSH,
  copy-agent prompt, folders/audio/mic/camera; real disk snapshots and graceful
  close/reopen; align empty state, unclipped softer shadows, remove hover tooltip,
  icon-labelled native controls, top-left VM resources/settings and image states.
  Owned additions luma_tester/vm.py and tests/test_vm.py. Native UI firstbbffbb20
  failed because QA used programmatic ModeSwitch.set_current (intentionally silent);
  actual toggle retry6ccf9169 PASSED. Screenshot review found collapsed settings
  scroll height; fixed propagate-natural-height, pending repeat. Added SSH Switch.
  Backend66 checks f043446e PASSED, including actual qemu-io mutation/snapshot/restore.
- Rendering diagnosis17276534 and direct QMP comparebfb280a0 completed: native
  compositor grim screenshots have0 black pixels; direct QMP/VNC output has black
  tile regions (QMP capture confirmed works directly). This is software virtual
  scanout/damage corruption, not an ARM guest: private preview Mobile is x86_64.
  Scoped Phoc PHOC_DEBUG=damage-whole trial currently running in existing disposable
  phone-qa-r9 overlay, job pending. It does not change base disk or physical phones.
  Source Phoc flags confirmed from exact retained0.55.1 SRPM. Do not ship debug
  setting or claim rendering fixed until repeated capture evidence is clean.
- Retired only stopped/passed r22 qualification entry/tmp extraction to restore
  reserve; packages, native corresponding sources, screenshots/reports retained.
  Dell r22 Desktop guest running at last check; preserve it during delivery.

- UX0.3.0 source now implemented; final checks747dc27d PASSED67 unit checks,
  compile/shell checks and public native UI smoke (mode disclosure, no-image,
  keyboard focus, settings sheet and narrow/dark allocation). Detailed UI1e6e195c
  PASSED real8GiB setting application, fixed visible Save footer and device sheet.
- Source actual Mobile/share runf3165e94 failed reading a share because host/guest
  UIDs differ. Added guest-only bindfs identity bridge; retry1d1ecf50 PASSED genuine
  touch/OSK/Home, shared edits both ways, agent SSH key enable/revoke/re-enable,
  real SDK hotfix visible and graceful shutdown. Guest helper install does not
  chmod/chown host projects. New phone image will include bindfs for offline use.
- Repaint trial9e21c47a failed after restarting greetd (autologin only once per
  boot); cold retryf008f88a functional workflow passed but black tiles remained.
  Pixman trial11efd511 hit the same restart gate. Cold renderer diagnosticddf78308
  confirmed actual WLR_RENDERER=pixman and0 black pixels in direct QMP output.
  QA overlay startup script restored after that diagnostic. Exported r9 unchanged.
- New mobile-session selects native pixman only when negotiated VirtIO GPU bit0
  (VIRGL) is absent; normal renderer remains on3D hardware. Linux bitstring order
  and GPU feature0 verified from upstream virtio.c and installed virtio_gpu.h.
  Full new-image export/repeated visual qualification remains pending; do not
  claim GPU-only effects or Mobile VirGL/ARM/physical-device qualification.
- Current source adds PipeWire/SPA client closure and USB host module to portable
  producer; actual portable/audio packaging validation remains pending. Camera
  passthrough requires explicit selection/host permission; real camera and speaker
  hardware have not been tested on the server. Existing Dell guest must be kept.
- 2026-10-22 workspace follow-up: discover only existing qualified or explicitly
  preserved predecessor workspaces, scoped by target/architecture/checksum;
  retain project disks and SSH identities across app upgrades. Known r9 virtual
  phone launchers receive an owned software-renderer drop-in and a verified full
  reboot; custom launchers and physical-device profiles are excluded. First
  upgrade08ae0e42 failed on SSH disconnect during reboot; retry1a7aeff6 PASSED
  actual workflow with clean Home/returned-Home captures (0 black pixels each).
- Mobile r10 exportd4a061f2 completed: SHA256
  bd88d4af0c5e1749cf30ef87f98551d2697bfca60f9a42130211a6853faa54e9,
  1898708992 bytes; bindfs included offline; renderer policy follows VirtIO VIRGL
  feature. Native source qualificationee1f58dd PASSED touch/library/OSK/typed
  Notes/Home, verified actual pixman, clean Home captures, live Try/Developer
  switching without replacing QEMU, bidirectional shares, SSH key revocation,
  visible SDK hotfix and graceful shutdown. CPU rendering changes GPU effects;
  no claim of VirGL/ARM/physical-phone parity.
- GUI reopenf00d462f PASSED restored existing workspace/project/SSH identity and
  actual window-close graceful shutdown (30.1 seconds total). Source final
  checks3f64f26a PASSED67 units, compileall, shell syntax and public native UI
  smoke. New portable audio/USB closure and packaged0.3.0/Dell delivery pending.
- Tester0.3.0/e5abd564 built61722fb8 using the public portable producer and
  qualified r22 native runtime; includes PipeWire/SPA client closure, USB host
  module and exact bindfs corresponding source RPM. Owned HDD output
  appimage-workspace-r23/Luma-Tester-0.3.0-linux-x86_64.AppImage,
  SHA256 f472e4cf079beedb69035659dce822894cf0f642c8ebb2b26bd58bb659dd7ede,
  283511288 bytes. Sidecars: Desktop r15 unchanged; Mobile r10 above. Private
  source:test attribution retained. No public signing/publication or ARM build.
- Packaged qualification06fa8c3a: actual AppImage entry PASSED; actual bundled
  Python/UI/QEMU full Mobile workflow PASSED including live mode switch, clean
  Home/returned-Home captures, SSH revocation, shares and visible hotfix. Aggregate
  job failed only on the separate audio QA fixture's >108-byte Unix socket path.
  Audio fixture retrye1b05b90 then lacked optional host pw-dump; final18caca8c
  PASSED packaged PipeWire module loading and QMP readiness against an isolated
  PipeWire server. Physical speakers/microphones/cameras remain untested.
  Evidence root-jobs/luma-tester-user-410c93e7c0/{entry,mobile}/qualification.json
  and root-jobs/luma-tester-user-9df6000911/audio-qualification.json.
- Deliverydff81185 PASSED app and both guest SHA checks on Dell. Desktop asset
  reused by verified hardlink; new Mobile copied separately. Candidate folder
  /home/nick/.local/share/luma-previews/tester-workspace-r23-20261022.
  Confirmed no running QEMU immediately before launch; old r22 app already stopped.
  Launched luma-preview-tester-workspace-20261022-2d620868.service using unchanged
  tester-display-20261017/{workspace,config,data,cache}; read-only metadata confirms
  active/running, ExecMainStatus0. Desktop registration7340a8ed exited0. This is
  manual Dell review, not an automated Dell app test or user acceptance.
- Linux iteration delivered for review. Still open: real Dell hardware audio/USB
  review and earlier Desktop graphics fallback diagnosis; public signed ready-VM
  catalog/update feed (Hub currently publishes installer ISOs); Mac/Windows/ARM
  host qualification. Other task owns generic ARM Desktop. Tablet/TV/Smart Device
  profiles remain explicitly unavailable until qualified; never fake their guest.
- 2026-10-22 SSH disclosure follow-up0.3.1: enabled SSH now expands inline with
  selectable host, forwarded port, username and native Copy SSH Command button.
  Shows managed-key/no-password authentication, pending enable and start-to-connect
  states; collapses when disabled or in Try mode. Shared connection helper feeds
  both clipboard command and agent prompt without reading private key contents.
  Checks2ffa2c4e PASSED68 units and dark native UI disclosure/state checks.
  Actual guest33c17d4d PASSED restored workspace, clipboard command authenticated
  against native Mobile, same SSH identity and graceful window-close shutdown.
- SSH0.3.1 source09440941 packageda0359812 reusing unchanged qualified0.3.0
  native/tool/guest closure. AppImage c475c1a9b6dc34055e0388a4d4e9b30577db679468f4d1363c160dc7fef99cf4,
  283511288 bytes, owned HDD appimage-ssh-r24. Packaged0607a2ed PASSED real
  AppImage entry and actual bundled UI inline expansion/connection fields/collapse.
  Delivery6cffac1a verified app and unchanged Desktop/Mobile assets on Dell.
  Previous0.3.0 launcher already closed (stop returned unit-not-loaded); no QEMU
  running at fresh prelaunch checks. Launched0.3.1 via
  luma-preview-tester-ssh-20261022-7b8ef18e.service; registration030b96be.
  Same isolated workspace/config/data/cache retained; no automated Dell app tests.
- 2026-10-22 TV/Smart Home ARM iteration0.4.0: experience is independent of image
  target/CPU; TV, Tablet and Smart Home prefer ARM and never silently choose x86.
  Explicit x86 selection remains possible. Shared Desktop manifests/installation
  disks serve these experiments; genuine Mobile remains a separate required image.
  Sidebar Smart Home label replaces Smart Device. Presets retain experience.
- Added local generic UEFI ISO import (GUI ARM-default architecture sheet and CLI
  import-iso), persistent blank install disk and private UEFI variables, disk-first
  Boot from ISO switch, early embedded installer display without fake SSH/readiness,
  ACPI shutdown and confirmed Force Stop for unresponsive guests. Streamed downloads
  hash during copying; ISO supervisor verifies once instead of duplicate GUI checks.
- Proposed hint opt/luma/experience uses desktop/mobile/tablet/tv/home. OS interface
  implementation/installer/Quick Settings are deliberately unchanged. Public Tester
  guide documents fw_cfg and generic-VM vs Pi hardware qualification. Agent prompt
  includes requested experimental interface. ARM Desktop image remains other task.
- Matching QEMU10.2.2-1.fc44 ARM host tool and edk2-aarch6420260812-8 privately
  extracted under owned HDD arm-tools-10.2.2; no host package install. Portable
  producer gains optional ARM binary/firmware with RPM file digest and version
  checks, matching loader/modules/dependencies, relocated firmware and notices.
- Source checks: initialfc272a3f hit a mocked-start directory assumption, fixed.
  ARM fixturec092bb2b exposed unneeded PXE ROM lookup, disabled ARM network ROM.
  Private-prefix trials1a3c79be/7b6ca252 lacked module/data search paths; wrapper
  corrected. Trial14b42859 visibly booted EFI fixture but OCR read ARM64 as ARNG4;
  predicate now checks fixture message and actual QEMU architecture independently.
  Suitede7abd7f unit fixture required a nonexecuted emulator path; corrected fixture.
  Retry d64bea5d PASSED70 units, native profile/import UI and actual ARM64 optical
  EFI fixture, persistent variables/disk and disk-first configuration. This is
  emulator mechanics evidence, not a Luma ARM guest or Pi hardware qualification.
- Real cached Luma Nightly20261021.1 ISO58e6ee50 timed out during repeated large
  media hashing. Single-passb7949845 reached QEMU but exposed hidden widget frame
  wait; ISO reveal now waits for VNC initialization so mapping can paint frames.
  Retry0d1d4422 PASSED actual visible Luma ISO boot in native embedded UI, no fake
  managed SSH and confirmed Force Stop. Full OS installation not tested.
  Final9c3f02e7 PASSED70 checks; compatibility27287f4c PASSED71 incl old settings.
  Build/packaged regression and Dell delivery are next. No source edits during tests.
- Tester0.4.0/83efa578 built6b75c01c via full portable producer, with verified
  matching ARM emulator, libfdt, native tool closure, UEFI code/variables and notices.
  AppImage a33c792e6645959f54127d8a30f87b9783979fdbb49d4d9e324b6b60ec2c54e5,
  294857208 bytes, owned HDD appimage-arm-r25. Existing Desktop r15/Mobile r10
  sidecars unchanged; no bundled ARM OS until the other task qualifies its build.
- Packaged768ac5c0 PASSED actual AppImage startup, full genuine Mobile SDK workflow
  (touch/OSK/Home, shared folders, SSH revocation, visible hotfix, clean captures,
  graceful shutdown), and actual ARM64 EFI optical fixture using bundled QEMU,
  modules/libfdt/firmware. Fixture verifies persistent UEFI/disk and disk-first
  boot ordering; it is not a Luma ARM OS install or Raspberry Pi qualification.
  Evidence root-jobs/luma-tester-user-e99f776cb7/qualification.json.
- Dell route timed out during SSH banner twice after qualification;0.4.0 has NOT
  been transferred or launched. Last delivered candidate remains0.3.1/r24. Ready
  transfer helper /srv/dev/accounts/second/artifacts/tester-arm-r25-deliver.py.
  On reconnect, verify/transfer app and unchanged sidecars, preserve any running
  QEMU guest, register and launch using the same tester-display-20261017 isolation.
  No automatic retry daemon, publication, signing, OS interface flags or physical
  device changes were performed. Source clean after this evidence note commit.

- 2026-10-22 architecture picker: native LumaUI menu beside Images exposes
  Automatic/x86-64/ARM64, per-experience CPU and per-CPU remembered image selection.
  Explicit selections filter images with no cross-CPU or Mobile/Desktop fallback;
  changing CPU while running is blocked. Presets restore their image's CPU and
  existing image-specific disks/snapshots remain separate. Generic TV/Home/Tablet
  still default to ARM, but explicit x86 is now available for iteration.
- Source check43877e04 passed72 units, UI exposed startup initialization order;
  fixed. Retry2a1739de passed72 units and actual native GUI architecture choices,
  missing ARM Mobile state, per-CPU selection, running guard, per-experience
  preference and reconstructed application preferences. No ARM Mobile OS image
  exists in the qualified catalog. Dell reachable; no running QEMU guest observed.
  Next: package0.4.1/r26, server qualification, then requested manual Dell launch.

- Tester0.4.1 source1a58eb32 packaged successfully835cb960/root50f4194b78.
  Immutable artifact: owned HDD appimage-arch-r26/Luma-Tester-0.4.1-linux-x86_64.AppImage,
  294857208 bytes, SHA83124b041add8e4b0ea79428448c501280b6a6f756cc36f8c819d2ecb25db157.
  Parentr25 native/tool/firmware closure retained; fresh Tester source and guide.
- Packaged4034baab/root2f33299c37 PASSED actual AppImage startup, explicit x86
  genuine Mobile KVM boot, touch/keyboard/Home, Try/Developer live switching,
  shared-folder edits, SSH key revocation and visible SDK hotfix; graceful shutdown.
  Bundled ARM emulator visibly booted EFI optical fixture and retained disk/UEFI
  variables. This proves ARM emulator plumbing, not Luma ARM OS qualification.
- 2026-10-22 19:03 America/Chicago: transferred checksum-verifiedr26 to Dell
  /home/nick/.local/share/luma-previews/tester-arch-r26-20261022; reused unchanged
  Desktopr15/Mobiler10 sidecars and tester-display-20261017 workspace/config/data/cache.
  No running guest or old Tester process before launch. Desktop registration
  luma-preview-tester-arch-register-20261022-1c428deb exited0; preview
  luma-preview-tester-arch-20261022-9c256a46 active/running, ExecMainStatus0.
  Dell manual preview only; all automated checks ran on server. No ARM OS yet in
  qualified catalog (other ARM task record still says no built image). No release
  publication/signing, Mac/Windows qualification, or physical device changes.

- 2026-10-22 device controls in progress: Device sheet and CLI support screen
  profiles (Pixel Pro/XL, Fold, Duo, Titan, Custom), UI scale, rotation and posture.
  Guest transactions validate actual compositor geometry before acknowledging,
  publish device.json + live signal, mask only simulator-owned Titan OSK state,
  restore prior screen on failure. Viewer excludes the Duo hinge from touch.
  75 source units passed f83bd5a4. Native live probe showed phoc12 silently ignores
  custom modes despite wlr-randr success. It correctly fails the acknowledgement;
  controls are NOT shipped yet. New scripts/tester/patches/phoc-dynamic-virtual-modes.patch
  opts into bounded custom virtual modes using LUMA_TESTER_DYNAMIC_MODES=1. Build
  scoped simulator compositor and new r11 Mobile image, retain normal19 checks.
- Website inspected directly: /download/mobile publishes Alpha1 FP6 system plus
  separate FP6 boot image. Direct system prefix confirms Android sparse16GiB,
  public compressed file1104575926 bytes; no full hash/boot verification claimed.
  Generic ARM boot adapter remains required; existing ARM userspace is published.
  Primary source: QEMU virt is a virtual board, not FP6 hardware. No need for user
  to supply an image. Preserve same Mobile apps/userspace when adapting it.
- Native tests97105e88 stopped due missing private catalog; fixed harness.
  980f1f5a and9677af52 exposed the compositor behavior above. RAM/CPU/SSH tests
  unchanged. Root free25GiB with other ARM media job5GiB reservation: small UI job
  uses existing secondary guest, no new root images, disk estimate0; heavy writes
  must stay HDD. Retired only stopped AppImage extraction scratch in root jobs
  e99f776cb7/2f33299c37/410c93e7c0/4d2fc61ca5 with per-entry receipts; original
  immutable packages and all test evidence retained. No other task data touched.

- Follow-up source b5bab3cf adds Device controls, geometry CLI/state contract and
  virtual-mode patch. Current uncommitted follow-up adds a checksummed, known
  r9/r10-only startup compositor update, optional package+SRPM staging in portable
  producer and a verified shutdown-disconnect race check. Not qualified/shipped.
- Full bootprobe938effb4f8 returned wlr-randr0 but mode stayed432x960; shutdown SSH
  raced the poweroff. New host ack correctly refused the fake resize. The physical
  whitelist fallback in phoc_output_state_set_mode explains this. Scoped patch
  selects custom mode only with simulator environment opt-in and bounded geometry.
- Build worker ready: /srv/dev/accounts/second/artifacts/tester-device-build-phoc.py.
  It clones existing stopped component-builder disk into an HDD overlay, rebuilds
  matching source RPM plus scoped patch and retains all normal19 Meson checks and
  corresponding source. No compiler/dependency install on host. Output planned
  owned HDD device-phoc-r11-<rootjob>/artifacts.tar. Then stage guest-updates-device-r11
  and run tester-device-live-worker.py with LUMA_TESTER_GUEST_UPDATES pointing there.
- Resource gate now shared disk reservations5+20GiB from other ARM jobs, with
  root25.1GiB free. Even disk0 is refused; no build is running for this task.
  Respect reserve; do not stop other jobs or bypass admission. Three immutable
  own input tars moved to owned HDD retained-mobile-inputs with full SHA comparison
  and original-path symlinks; relocation receipt retained. Dell still0.4.1.

- Source follow-up committed f52782bc. Fully bounded one-shot pipeline is RUNNING
  as exec session4930 (not a recurring automation), waiting for normal admission:
  /srv/dev/accounts/second/artifacts/tester-device-r27-pipeline.py.
  Log: tester-device-r27-pipeline.log; live outcome: tester-device-r27-pipeline-result.json
  under same artifacts directory. Overall deadline7000s; individual jobs have
  bounded memory/runtime and normal devctl leases. It retries only admission75,
  never a failed test, never stops another task. Previous build-only queue5656
  was terminated while still waiting, with no build service started.
- Pipeline dependencies: build scoped Phoc RPM+SRPM/all19 normal checks; stage
  mobile-display.json via tester-device-stage-update.py; run current source checks;
  native tester-device-live-worker.py tests11 real geometry/input transitions,
  preserved existing Projects/app/main.py, same QEMU PID and hinge touch exclusion;
  build tester-device-r27-build.py from immutable r26 native closure; packaged
  entry/device/complete Mobile hotfix regression via tester-device-r27-qualify.py.
  Entry extraction is on HDD to keep root reserve; no extra root VM/image copies.
- Only on complete qualification success, pipeline transfers checksum-verified
  0.5.0/r27 to Dell tester-device-r27-20261022, reuses r24 sidecars (Desktopr15 and
  genuine Mobiler10), preserves tester-display-20261017 state and registers/launches.
  The new host bundles the small checksummed compositor update and its source RPM;
  recognized r9/r10 private workspaces upgrade before startup reveal, preserving
  user projects. No new whole r11 image is needed. Live resizing never reboots.
  Launch defers if QEMU or another manually launched Tester is active; only known
  old r26 launcher unit may be stopped. Read pipeline outcome before any retry.
- As of2026-10-23 01:12UTC pipeline is waiting; no normal compositor build/Device
  validation has passed yet, and no0.5.0 was delivered/launched. Dell remains0.4.1.
  ARM Mobile download is verified to exist; generic virtual boot adaptation is
  still outstanding. No physical phone operation, public release or signing.

## 2026-10-22, 10:01 PM Central — Device preview delivered

- Source56102e38; startup fixes ea3c42b7/ee2ff27b. Corrected the builder's stale
  SSD path. Phoc scoped simulator RPM+matching SRPM built in an HDD overlay of
  the stopped builder; all20 normal Meson checks passed (not19). Evidence:
  phone experiment root-jobs/luma-tester-user-ee816a31b6, artifacts in owned
  HDD device-phoc-r11-ee816a31b6. No physical-device changes.
- Greetd does not preserve the compositor opt-in through login. The known
  r9/r10-only update now inserts the export into the managed simulator launcher,
  keeps projects/disks, reboots only during startup and records package SHA.
- QEMU VNC clips beyond2160 rows. High-density presets use half render density
  in both orientations, preserving logical app sizes. Device panel discloses
  preview density; metadata separates native_pixels/native_scale from guest
  pixels/scale. Custom sizes also fit the scanout. Full native-panel screenshot
  fidelity is not claimed. Fold regions/posture metadata still need OS consumers.
- Source checks:78 passed, dev-tester-device-source-583d0356. Source native11
  transitions passed: root-job d9d77088d9. Actual packaged entry,11 transitions
  (including settled frames without black tiles), hinge input exclusion, Titan
  OSK handling, and full Mobile touch/type/SSH/share/hotfix/shutdown passed:
  root-jobs/luma-tester-user-971b3156c0/qualification.json, dev qualification
  cb31538a. Reviewed settled unfolded screenshot; shell fills resized screen.
- Immutable AppImage: owned HDD appimage-device-r27/
  Luma-Tester-0.5.0-linux-x86_64.AppImage; SHA256
  f8413e5a6318e9a6515fd1f7a1fcc1250d73c86205d84fba1c89ee3dff1f657d.
  Includes matching update source RPM/NOTICE; both QEMU CPU closures retained.
- Shared root admission was held by95GiB estimates for other jobs. Bound owned
  HDD bulk directory onto phone experiment hdd-admission; normal devctl admits
  against the actual build filesystem with8GiB estimate and an additional normal
  source-worktree flock. No limits/records lowered or cleared, no other jobs
  stopped. Root/secondary/HDD reserves remain25/21GiB/5.7TiB. Bind is retained for
  subsequent owned HDD builds; pipeline scripts under private artifacts.
- Dell checksum-verified delivery: /home/nick/.local/share/luma-previews/
  tester-device-r27-20261022. Existing tester-display-20261017 workspace retained.
  No guest was running. Old known r26 launcher stopped; new launcher unit
  luma-preview-tester-device-20261022-8a1747ee.service verified active/running,
  ExecMainStatus0. Desktop registration f3ed94d9 exited0. Automated tests ran
  only on server; Dell operation was requested local preview launch/metadata.
- One-shot pipeline finished successfully: private artifacts/
  tester-device-r27-pipeline-result.json. No background queue remains. No public
  release/signing/physical phone changes. Existing published ARM Mobile payload
  is real; QEMU boot adapter and generic ARM catalog qualification remain next.

## 2026-10-23 — General sharing/library polish and crash fix delivered

- Source6200ffc6. Commits09eeb5c6 (library/hardware/workflows),23e24f93
  (supported microphone codec),fdab8cb9 (immutable AppImage metadata staging),
  bed25d34/6200ffc6 (control/agent private-key permission repair).
- Dell0.5 journal confirms GTK popup/native-parent crash after menu use. The
  toolkit explicitly distinguishes reusable menus from menus that unparent
  after a choice. Architecture now uses keep_parent=True; Images is a native
  library sheet instead of the reused ephemeral popup/file-picker entry.
- Experience rows show no architecture badges. TV/Smart Home say Coming soon
  and are disabled. Fresh defaults: Desktop/Tablet x86-64, Mobile ARM64; explicit
  saved architecture choices persist. Tablet Automatic follows host CPU.
- Images includes local/installed/downloaded developer workspaces plus a live,
  cached Hub ISO catalog, latest3 and older builds, anonymous verified downloads,
  and local import. Nine real Hub ISO entries observed. ISO media stays distinct
  from Mobile developer profiles; no fabricated ARM-ready workspace/catalog row.
- Hardware sidebar plus opens LumaUI Audio/Microphone/Camera/USB grid. Generic
  Linux device/interface discovery includes storage and UVC cameras, mounted
  disk protection and grouped non-USB camera pipelines. No Dell model/PCI ID
  condition in product code. Real Dell metadata showed an IPU7 camera with32
  capture endpoints; a generic fixture verifies grouping into one camera.
  Integrated camera streaming still needs a cross-platform camera-sharing bridge;
  UI says so. No camera capture or USB transfer end-to-end qualification claimed.
- Separate folder list adds a folder read-only in one picker, exposes Allow
  changes and guest mount path inline, and supports removal. All existing shares,
  disks and projects retained. Hardware/folder changes apply next start.
- QEMU has no hda-input codec. Microphone uses supported hda-duplex and enables
  audio; disabling audio disables microphone. Actual QEMU device capability
  checked in tests. Source84 tests PASS dev-tester-library-source-6f455532.
- HDD default ACLs made a control key0640; SSH rejected it. Both new and reused
  control/agent keys now get0600. Tests verify repair, including copied agent
  connection details. This is a filesystem behavior fix for all users, not a
  Dell workaround. Tests initially misattributed SSH timeout entirely to disk IO.
- Native source UI114cd0f64e passed repeated menu/gallery/hardware/folder flows.
  Packaged X11 and actual headless Weston Wayland: a18954be92/ui and /wayland,
  each5 menu reopen cycles, live9-item catalog, defaults/folder access verified.
  All11 device transitions/settled no-black-tile/input checks passed /profiles.
  Exact gui.py/device_profiles.py/display.py/host_devices.py/image_library.py
  hashes are unchanged from that candidate to finalr30; retained evidence reused.
- Finalr30 actual AppImage entry/doctor/quit and full Mobile touch/type/SSH,
  bidirectional sharing, key revoke/reenable, visible SDK hotfix and shutdown
  passed root9ce60b4424/entry and /mobile. Desktop booted1920x1080 and wrote the
  persistence marker; screenshot /tablet/desktop.png. Tablet booted x86 using
  the same disk, selected1600x900, agreed with Mutter GetCurrentState, retained
  the marker and shut down cleanly: root63c1b1eb77/tablet/qualification.json.
  Original test wrongly assumed the EDID preferred1280x800 was a forced mode;
  that assumption was corrected, no product resolution override was introduced.
- Aggregated successful evidence: phone experiment root-jobs/
  luma-tester-user-63c1b1eb77/qualification.json. Native VM tests used3GiB guest
  memory under normal4GiB admission; production default stays4GiB. A duplicate
  admission-waiting pipeline was stopped after the already admitted final check
  completed; no background queue remains. Other agents' jobs untouched.
- Tablet test's stopped HDD base was retained as .hdd-retained; its cache path
  now symlinks to the already existing exact-SHA SSD Desktopr15 base. No original
  image/disk bytes removed; private test overlay remains on HDD. Receipt in final
  tablet output ssd-base-reuse.json. Reserves: root36GiB, secondary21GiB, HDD5.2TiB.
- Immutable candidate: owned HDD appimage-library-r30/
  Luma-Tester-0.6.0-linux-x86_64.AppImage. SHA256
  bf66029fb1438c2d65932600eb60d041913ca0868ecb1e2c3e4637a3b5c1ddb8.
  Dell destination tester-library-r30-20261023; all app/image hashes verified.
  Registration26d3b2c4 exited0. Preview unit
  luma-preview-tester-library-20261023-e224b10e.service verified active/running,
  actual packaged Python child present, ExecMainStatus0. Runtime emitted symlink
  extraction warnings during simultaneous registration/launch, but real GUI
  process remained active; serialize registration before future launches.
- Existing tester-display-20261017 workspace reused, no Dell guest was running.
  Automated app/guest tests only on server; Dell operations limited to requested
  launch plus logs/process/hardware metadata. No publication/signing/phone change.
- Final one-shot outcome: private artifacts/tester-library-r30-pipeline-result.json
  passed=true, stage Dell preview launched. Next: Nick's manual review; finish
  generic ARM Mobile boot adapter and integrated-camera stream bridge separately.

### 2026-10-23 — Images dropdown and architecture preference migration
- Source864a7775 restores Images in the bottom action bar as the shared LumaUI
  Menu component, with local workspaces, current-image check, latest3 server
  builds/older submenu, file import and refresh. Removed the duplicate sidebar
  footer and the card-based Images modal. Catalog refresh begins at startup;
  open menus are replaced only after closing, preserving native popup ownership.
- Dell preference metadata confirmed desktop=aarch64/tablet=x86_64/mobile=aarch64.
  Earlier previews stored defaults indistinguishably from explicit choices.
  General migration now resets unmarked legacy choices to host CPU for Desktop
  and Tablet, ARM for Mobile. Explicit architecture/image/preset choices carry
  provenance and persist thereafter. No Dell model-specific condition introduced.
-86 source tests passed dev-tester-dropdown-source-09b7534a. Native source UI
  luma-tester-user-b3aab8707e passed5 image dropdown selections, live9-item catalog,
  migration and explicit-choice persistence. Generated screenshot retained there.
- Immutable0.6.1/r31 candidate built: owned HDD appimage-dropdown-r31/
  Luma-Tester-0.6.1-linux-x86_64.AppImage, SHA256
  c7d8bbcb5d80acb1863b787bd6ed160d809609457ada7d37be0f25f58c8cfc5b.
  Build unit dev-tester-dropdown-r31-build-2a8394d9 passed, rooteb2ddb8b10.
- Packaged X11/Wayland/entry checks are queued behind normal memory admission;
  bounded driver owns no admitted worker yet. No Dell replacement launch yet.
  Next: finish packaged checks, verify hashes, serial desktop registration then
  reopen on Dell only if no guest is running; preserve existing workspace.
- Packaged interaction checks passed on X11 and nested Weston Wayland:
  rootaacacaac19/ui/qualification.json and /wayland/qualification.json, each5
  dropdown selections, real9-item Hub catalog, old-default migration and explicit
  preference persistence. Combined1GiB worker then hit its cgroup memory limit
  during AppImage extraction; not an application crash or passed aggregate job.
- AppImage extraction/real window/keyboard quit/bundled-tool discovery reran with
  normal2GiB admission and passed: dev-tester-dropdown-r31-entry-514ea4fd,
  rootf0eefcb0bc/qualification.json. Wayland rendering additionally captured and
  inspected successfully: dev-tester-dropdown-r31-visual-22623896,
  rootf2c659e406/images-dropdown.png and qualification.json. Xvfb without a
  compositor produced unusable popup screenshot pixels; the actual Wayland
  compositor screenshot shows the styled anchored menu and bottom action bar.
- Delivered AppImage/receipt and exact Desktopr15/Mobiler10 sidecars to Dell
  tester-dropdown-r31-20261023; all hashes verified. Existing r30 launcher had
  already closed and no guest/Tester was running. Registration4bafb400 completed
  inactive/exit0 before launching GUI02828e95, avoiding concurrent extraction.
- Dell unit luma-preview-tester-dropdown-20261023-02828e95.service verified
  active/running/exit0 and actual packaged Python child present. Read-only saved
  metadata confirms Desktopx86_64/Tabletx86_64/Mobileaarch64, explicit choices[];
  general migration applied without editing Dell preferences externally.
  Existing tester-display-20261017 workspace retained. All automated tests stayed
  on server; Dell used only delivery/registration/launch/process/preferences reads.
- Candidate ready for Nick's manual review. No queued retry driver remains.
  Generic ARM Mobile boot and integrated camera streaming remain separate gates.

### 2026-10-23 — Mac native build and live disk snapshots
- User requested Mac build, then saving without shutdown and discovering snapshots
  created by another agent. Sourceb6e82d15/1d4ca182 adds Darwin HVF for native CPU,
  TCG for cross CPU, CoreAudio, Cocoa standalone display, native data folder,
  relocated QEMU assets, Apple Silicon app/DMG recipe and shared LumaUI runtime.
- Explicitly reused code from codex-mac-windows-apps-20261017's retained source:
  scoped Meson/C guards (8files), platform_boot/platform/platform_secrets/
  private_paths and tools/luma-port/mac+common. Its own worktree was not changed;
  unrelated appkit/UI/application changes were not imported. Upstream font/OFL
  text retained verbatim, including existing trailing whitespace in OFL line21.
- Current pinned M4 route22442 works. Installed normal Homebrew gtk-vnc/xorriso
  with auto-update/cleanup disabled; source compilation2jobs. No personal files,
  other VM jobs, signing credentials or power/tunnel settings changed. Mac had
  approximately16GiB free at start; own app656MiB, outputs under task-owned
  Library/Application Support/Luma/Development/tester-mac-r1 and -r2.
- Shared snapshot code uses QMP blockdev-snapshot-internal-sync for live disk
  checkpoints and resumes only if initially running. It does not capture RAM,
  UEFI variables or host shares; restore still requires stopped guest. GUI no
  longer shuts down to save, and QMP listing refreshes while running.
-88 source tests PASS f883370f and71f51ba2, including actual QEMU live/paused
  checkpoints, same process continuing, duplicate refusal and restored disk bytes.
- Actual Dell read-only QMP metadata confirmed before-clock-left-20261023 exists
  on os in active Mobiler10 disk (vm-state-size0). Old0.6.1 sidebar suppresses
  running refresh. No user guest was stopped or modified by this diagnosis.
- Macr1 built; native UI/ARM HVF firmware+live snapshot passed but x86 UEFI -bios
  failed for Homebrew's split code/vars layout. Fixed paired pflash for Mac x86,
  and moved receipt hashing after final ad-hoc signing. Failed candidate retained.
- Macr2 source1d4ca182 built successfully dev-tester-mac-r2-build-aa2b6d30,
  root62d6d557f5. Reused unchanged r1 native C-kit build. Qualified on native Mac
  dev-tester-mac-r2-qualify-6c8f80bd: real mapped LumaUI window,3 Images/CPU menu
  cycles, Advanced, ARM HVF and x86 TCG firmware startup, live checkpoints with
  same process continuing and paused state preserved. No Luma OS guest boot
  qualification claimed. Private native report in tester-mac-r2/qa/qualification.json.
- Mac DMG/receipt/SHA retrieved to owned HDD artifacts/tester-mac-r2. Final mounted
  DMG/hash validation and Downloads delivery pending normal admission; no release
  signing/notarization/publication. Linuxr32 first concurrent attempt failed75
  because the same admission path was leased; no candidate built in that attempt.
- Next: mounted-DMG check+Mac delivery, Linuxr32 package and active/externally
  created snapshot sidebar regression; deliver Linux update without disturbing
  Nick's currently running Mobiler10 guest. Mac Intel host remains unbuilt.
- Native Mac delivery completed by ordinary artifact copy/checksum readback:
  /Users/nick/Downloads/Luma-Tester-0.7.0-macos-arm64.dmg,64860702bytes,
  SHA2562c08f6715a3a0df4752a0c991492fd56aab5d05635e3e2aa9402d9bd781fefb5.
  Actual bundle minimum macOS27.0. Native app/firmware/checkpoint qualification
  passed as above; optional mounted-DMG full-inventory recheck is still queued.
  Package remains ad-hoc signed/not notarized, Intel host unbuilt, actual Luma
  OS image boot on Mac unqualified. No app/UI tests ran outside admitted jobs.
- dd1df3cd additionally fixes Linux desktop registration to pass --state and pin
  the same resolved workspace. CLI forwards its custom state to registration.
  New regression launches a real GLib desktop entry through paths containing
  spaces/percent/quotes/backticks/dollar/backslash and checks exact argv. This
  new regression has not run yet: normal1GiB admission refused while all16GiB
  account job budget is held by the ARM/Surface lanes. Prior88 tests passed.
- Bounded finish controller private artifacts/tester-mac-live-finish.py is ready
  to run unit suite, optional mounted Mac DMG verification, Linuxr32 build, real
  running/externally-created snapshot sidebar regression, then verified Dell
  delivery/desktop registration with explicit existing workspace. Each stage
  uses normal admission. No guest/application process will be stopped; GUI
  reopening is deferred to the user while their VM is active. Failure/report:
  private artifacts/tester-mac-live-finish.json. No success claimed before it runs.
- Linuxr32 output does not exist yet (both attempted jobs were refused before
  actual build). Private testers-live-r32 helpers retain genuine r15/r10 sidecars;
  no hardware-specific workaround or guest payload change. Next controller run
  starts only after this checkpoint commit; read its result before repeating it.
- 2026-10-23 ~17:38UTC: user explicitly requested launching the Mac build.
  Pinned SSH confirmed nick's active console and no existing Tester process;
  LaunchServices open -a launched the qualified r2 app from its task output.
  Actual Contents/MacOS/python3 process19089 and fresh0.7.0 launch log verified.
  No VM was started or user data changed by this launch; no new app tests run.
  Linux follow-up controller remains waiting for normal tester-dock-state
  admission, per private tester-mac-live-finish.json.

### ARM Mobile follow-up checkpoint — 2026-10-23

- Source adc591998 follows d40b698c3: catalog VM profiles plus bounded display /
  control capabilities; registered image JSONs are now recovered independently
  of saved preferences.95 tests passed in image-recovery-checks-df93796b.
- Mac r3 app0.7.1 source d40b698c3 built/signed/inventory-checked successfully,
  unit19fdc385, SHA25680ef338eee3162fdc41ad3f146e8458829c9425006c021cd0aa2efcf0f42e158.
  Not delivered as the final update. Native QA first failed because fixture
  creation preceded PATH setup; corrected harness then exposed actual missing
  local-profile discovery, fixed in adc591998. Rebuild/actual native GUI QA pending.
- Original raw/prototype remain retained. New private adapter-uefi.raw has512-byte
  GPT geometry, preserved partition/filesystem identities, an EFI-system first
  partition and BOOTAA64.EFI UKI. Official Fedora systemd-boot-unsigned259.5 RPM
  signature verified. Initial objcopy VMAs were corrected relative to the stub's
  ImageBase before further testing. Native self-boot is NOT qualified yet.
- Offline bootstrap tools used Fedora44 release RPMs after updates metadata
  referenced a missing urllib3 file. Host installroot sysusers ignored the
  target's immutable account provider and failed. Restored the matching original
  account files from the prototype and completed dhcpcd using target-native RPM.
  pkg-repair-r2-124bda06 passed: cloud-init25.3 runs; all910 original installed
  package versions unchanged;25 virtual/bootstrap packages added (receipt in
  mobile-arm-alpha1/virtual-package-result.json). No host packages changed.
- Private helpers tester-arm-mobile-uefi-prepare-r4.py and
  tester-arm-mobile-package-repair-r2.py retain the exact successful preparation
  / repair commands. Prototype4Kn adapter.raw stays separate from the new512-byte
  adapter-uefi.raw. No mounts/VMs should remain from finished jobs.
- Next: configure real per-session NoCloud and native-ready services, remove all
  qualification/SSH host keys and machine identity, export/check standalone qcow2,
  qualify the fresh self-booting disk through Session on M4 and the rebuilt Mac
  LumaUI app. Then deliver/launch via stable Mac app location if no user VM is
  active. Keep public publication/catalog agreement separate. No final ARM image
  or public feed entry exists yet; do not claim the app integration is finished.
- Follow-up: Mac r4 source81f45bd85 built in39314ad0; native mapped LumaUI,
  profile discovery, ARM HVF/x86 TCG firmware and snapshot checks passed in
  dc35572a. DMG SHA256c14716b27490fa71de52fc77d3e1696d6011f45dbf79d9386b92e3350971fb0d.
  Private output tester-mac-r4 on owned HDD; not yet delivered as final update.
- Final ARM virtual disk export-r1 built/check passed in e71c3bd0,2038416896bytes,
  SHA256a31d583cd9369492086a7943e9a77d621d10b095dc644d36b5cefe520f6a2519.
  Genuine public Mobile20261015.21 plus generic UEFI/kernel/bootstrap adapter;
  fresh NoCloud and ready services configured, prototype/host keys removed.
  Standalone QCOW2 retained in mobile-arm-alpha1/export-r1. Self-boot/full fresh
  image integration is still unqualified, separate from earlier successful
  direct-kernel M4 prototype Home/SSH/graceful shutdown proof.
- Actual local-image/full-GUI controller0dba75d8 failed at SSH handshake before
  completing transfer. User reports Mac awake; subsequent pinned SSH attempt
  found loopback22442 connection refused. Existing outbound tunnel disconnected;
  asked user to reconnect it. No tunnel/power settings or other guests changed.
  Prepared private tester-arm-mobile-local-gui-r2.py with checksum-verified cache
  reuse and scoped caffeinate for bounded test. Next: reconnect pinned SSH,
  run admitted r2 controller, inspect guest/window screenshots and real fresh
  NoCloud/snapshot/reboot evidence, then register image and deliver/launch r4.
  No final-image success or public Hub catalog publication claimed.
