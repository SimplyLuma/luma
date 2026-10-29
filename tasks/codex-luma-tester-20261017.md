# Try Luma — cross-platform launcher

- Owner: Codex, luma-dev-2, current tester chat.
- Status: Try Luma 0.7.6 published; action-bar image deletion implemented, locally qualified and merged via PR91. No newer packaged release yet.
- Branch: codex/luma-tester, based on origin/main 925db44.
- Worktree: /srv/dev/worktrees/luma-os/codex-luma-tester-20261017.
- Scope: LumaUI Linux launcher, mobile-first and desktop profiles, x86_64 and
  aarch64 guest definitions, verified image ingestion, private persistent sessions,
  localhost SSH, source sync/app restart workflow, image/agent documentation.
- Owned: src/luma-tester, docs/developer-platform/tester*.md, README/BUILDING
  navigation, this task record. No app-porting source lanes modified.
- User authorized release publication/signing and native Mac tests. Preserve
  existing user projects and VM disks; use isolated copies for GPU qualification.
  No physical device operations. Canonical integration checkout remains unchanged.

## Current handoff

2026-10-28 image deletion follow-up (current owner/task): added an optional
LumaUI trash button beside downloaded images in the action-bar Images menu.
Selection and trash are separate controls. Confirmation names the image, size,
dependent workspace count and affected presets; dependent saved files/snapshots
are explicitly included. Cancel/close never delete data. Running/preparing or
downloading images are protected by per-image and session leases, including
other launcher processes. A changed dependency list requires reconfirmation.
Deletion removes only managed cache, dependent session directories and managed
registrations; external imports and shared host folders are preserved. Completed
independent ISO installations survive removal of their installer. Selections and
presets are reconciled so the next Start can download again. Shared source works
through existing platform locks on Mac/Windows/Linux; no new native Mac/Windows
deletion run claimed. Native Linux GTK/LumaUI picker/cancel/delete check passed
a447584c; 163 host regression tests including 11 focused deletion tests passed
f878d652, with a real qcow2 backing disk and internal snapshot. Earlier standalone
regression harness lacked child PYTHONPATH; corrected final run passed. Native
UI harness assertions initially searched the wrong modal subtree and button CSS
name; corrected accessibility/button selection passed. Screenshots in
/srv/dev/accounts/second/artifacts/dev-try-image-delete-ui-a447584c/.
Owned source: image_storage.py, core.py, image_library.py, gui.py, README and
tests/test_image_storage.py plus tests/image_delete_ui_smoke.py. Public0.7.6
downloads remain the already published build; this feature needs a new package.
Implementation b70ca83f2; curated public commit e413a6feae1f6bc68987ee792b9a03963f6ae402
merged via https://github.com/SimplyLuma/luma/pull/91 as
3fffd414165a61d3689d0d60a3b69deb251a56b8. All four source CI checks passed,
including tester (run40807872062). Existing private turn-capture ref corruption
caused ordinary fetch to report a bad object; unrelated refs were preserved and
the public source commit was created from the existing verified main object.

2026-10-28 final release supersedes the historical GPU prototype notes below.
Owner accepted the complete-frame Mac fix; source afb989fb17e15b1b30736c55e44508a5481e8308.
Public source PR90 merged 97dde0dce840ddee22b1b0fbdc0d14b113e6b74b, all four
CI checks passed (including real GTK/GVnc bindings for frame-stream tests).
Mac uses HVF CPU virtualization and SOFTWARE guest graphics, not the rejected
CGL/ANGLE prototype. Final app has no Resources/mac-gpu. Whole-frame worker
stream presentation and pinned upstream QEMU11.1.0 VNC refresh patch are shipped.
Final display.py SHA matches the owner's tested override byte-for-byte:
5f8f991fcbb6a78855d0517c131343ff8fe19fa9e8f7aa0f979d8bc767e5d623.
Exact corresponding QEMU tarball, patch, recipe and GPL license ship with source.

All final packages were built from afb989fb1. Linux actual AppImage native
sidebar/clipboard/version/source/channel QA passed. Windows ZIP CRC, source and
runtime comparison passed; no new native Windows OS boot claimed. Windows
runtime dependencies remain the previously qualified version. Mac Developer ID
signing (160 Mach-O files), app+DMG notarization/stapling, download quarantine,
native UI and hardened HVF/TCG QMP smoke passed. The final signed Mac app booted
the new ARM disk twice with automatic login, three actual Flatpak app launches,
persistence and clean shutdown; exact native evidence is release-final-076-r3/native-mac.
Linux x86 KVM new-disk graphical auto-login/materials/three-app launch checks
passed with normal startup settings and Flatpak repair disabled, confirming the
image itself initializes its app repository before login. Earlier deliberately
stripped-startup and short cold-app-deadline QA attempts failed; corrected final
evidence is prepared-images-074/qa-final076-linux-r4/BOOT-PASS.json. No new image
rebuild was needed to correct those QA fixtures.

Published https://github.com/SimplyLuma/try/releases/tag/v0.7.6 at19:50:22UTC.
AppImage SHA54872a8672416002c9a867340621d1a79830aada36d42f304e32c5eaacc328c4,
Windows SHA6710a1d0cbd317e41e1822218894aa055f65cfba9d3c8614a7ff3fc6660ca7c2,
Mac SHA8c86526464f74a7c6cbd29a1cd4f75773178965eadadc861f635eea6929b3c0d.
Source and SHA256SUMS also passed anonymous full-body readback. Previous0.7.5
asset IDs/digests/sizes unchanged. Publication job61cb0a8d passed; evidence
release-final-076-r3/PUBLIC-DOWNLOADS-VERIFIED.json on owned bulk.
Try repo main a9b115e published generation7 approved pair; docs-only808eaa6
clarifies preserving the website's tracking routes. Exactly two ready4 standalone
QCOW2 disks serve Desktop/Tablet/Mobile, publicly uploaded and fully read back:
ARM e182b3e2cef32dc58f0df8a58fa59079bdfa847390bb62372dd5bbf67e2a7ca8,
x86 d02987696cb0bfd692bdc15f6c3e328916731dfac1ff05230fc04f313037d4c2.
Evidence prepared-images-074/publication-r4/*-PUBLIC-PASS.json.

Website handoff: https://github.com/SimplyLuma/try/blob/main/docs/try-0.7.6-website-handoff.md
Hub must register the three0.7.6 app artifacts, update tracked /get/app/try/*
destinations and publish its signed tester update index. No website deployment
or Hub data mutation performed here. At~19:47–19:51UTC, actual website routes
delivered initial file bytes for current x86 and ARM OS nightlies, generic/FP6
Mobile system+boot, Titan system+boot, and all three Try apps (still routed to
0.7.5 then). No broad availability outage reproduced. A first ARM probe omitted
/aarch64/ and returned404; actual website route includes it and passed. User
reported only one completed download/hour; analytics ingestion, source filters,
traffic and processing delay remain unverified and need Hub investigation.

2026-10-27 launch prep: implementation6910be67a, Tester0.7.2. Public Hub
now lists ARM20261026.8 and x8620261026.9. Both complete HTTPS bodies passed
size/SHA256 verification through the actual Tester downloader with source=test;
qualification public-isos-20261027/qualification.json on owned bulk. Existing
exact release public gate records report actual installed/native boot pass for
both; ARM backend native-m4-hvf, x86 KVM. No full OS gate repeated here.

Shared installer ISOs now appear for Desktop/Tablet/Mobile, with explicit
architecture captions and installer labels. Dedicated developer disks retain
their target restrictions. Mobile ISO uses864x1920 to avoid ARM EDK2 console
assertion; installer sessions hide unsupported Home/device SSH controls. Mac
builder adds explicit --release attribution before signing. pyproject metadata
aligned separately to0.7.2. No live-media conversion or OS rebuild performed.

Linux lightweight release candidate:
/srv/dev-storage/projects/luma-os/artifacts/codex-luma-tester-20261017/appimage-release-r35/Luma-Tester-0.7.2-linux-x86_64.AppImage
SHA2568caa90be1ce6c376362c730a4194b41acb27decab04f5fa691779cfea70c740b,
282MiB, source6910be67a, DOWNLOAD_SOURCE=tester, no private preview catalog or
multi-GB guest image. Build52f54830 passed99 tests. Native LumaUI packaged
catalog testf5ef8b6e passed all6 experience/architecture combinations against
the real public catalog. UI evidence root-jobs/luma-tester-user-3686111662.

Packaged QEMU startup smoke0d83344c passed process/QMP checks for both ISOs;
x86KVM45sec screenshot shows Luma boot splash. ARMTCG45sec screenshot still
has inactive display, though console reached ExitBootServices; not an ARM
installed/graphical boot pass. Additional120sec ARM probe20d7de49 reached kernel/systemd but remained in
dracut-initqueue at capture; no graphical ARM boot claim. Both short probes
used3GiB guest RAM, below published4GiB minimum. Correct4GiB ARM probe75aaea85
running under6GiB cap,240sec bound. Exact release native M4 boot gate remains
the full ARM boot proof. f7f807c6e aligns pyproject0.7.2 and changes NEW workspace
disk default32→64GiB to meet public40GiB minimum; existing saved settings retained.
Final Linuxr36 rebuild pending the current probe closure.
Mac route22442 refuses connection; no2244x listeners. Reconnection requested;
private helper tester-mac-r9-build.py prepared for native0.7.2 --release build,
NOT RUN. Existing Mac0.7.1 unchanged. No Tester public upload/index/signing,
Mac native0.7.2 package, Intel Mac or Windows qualification claimed. Dell/user
VMs/projects untouched. All private receipts under own artifacts/root-jobs.

Previous Oct23 checkpoint (superseded for current launch prep):

Latest: Mac app0.7.1 r8 is open at ~/Applications/Luma Tester.app with genuine
ARM Mobile20261015.21 virtual adapterr6 installed in normal state. Native M4
fresh boot/Home, revocable SSH, live checkpoint, graceful restart/persistence,
visible progress and actual Home after reopen passed ace30e3d. Linuxr34 shows
measured download/verification progress, waiting state and explicit installer
labels; delivered and registered without reopening the active old Dell GUI.
Public prepared-image catalog publication remains a separate Hub/release gate.
Current implementation cc18fcd0f; full final evidence at the end of this record.

Earlier checkpoint (superseded by the latest delivery above):

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
- Mac tunnel reconnected after user confirmation. Local r1 ARM disk transfer and
  checksum succeeded. Full-image GUI caught real Mac rfb ctypes resolution bug
  (Linux soname fallback despite bundled dylib); fixed in ba8565881 with bundle
  resolution regression.96 source tests passed549a6a81. Macr5 built ba900343;
  mounted DMG complete file hashes/signatures/CLI passed11cb24ac. DMG delivered
  to Nick Downloads, SHA2567f8356ba0b39bbcb12fa876632e72ef59afd7e023523a9d432ccd3f16525c261.
- r3 actual GUI now loads the embedded framebuffer and native UEFI Linux, but
  fresh machine identity triggered interactive systemd-firstboot timezone setup.
  Virtual-only export-r2 masks that service; export907477fe check passed. Actual
  r4 test boots kernel7.2.11 with networking/sshd; cloud-init users_groups fails
  to disable luma password and cloud-final is skipped due ordering cycle caused
  by After=cloud-final on services ordered before multi-user. Stopped only owned
  QA guests after capturing console evidence; no normal user guest modified.
- Read-only original adapter account inspection cf96292f confirms luma entries
  exist in etc/passwd+shadow and usr/lib/passwd; do not assume missing entries.
  Next inspect actual stopped overlay cloud-init error/SELinux labels, remove
  the proven cloud-final ordering cycle and correct the actual account failure.
  Private cloud-read-r2 ef49c88e running with1GiB hard cap for read-only inspection;
  nested root unit8bf72a5700 converting stopped QA overlay to private raw before
  read-only mount. Its result pending. Earlier transfer wrapper without scoped
  HDD write access failed read-only before writing; retained as failed evidence.
  Original4GiB request was refused while other builds reserve14GiB; the separate
  read-only workload now has a matching1GiB runner/systemd bound. No gate bypass.
- Actual stopped-overlay inspection needed journal recovery on a private raw
  copy (ro,noload cannot read uncommitted ext4 metadata). Private copy recovered
  cleanly in7e0c0027. Cloud-init failed at passwd -l: restored account files had
  etc_t rather than account types. Existing entries were present; no account
  migration needed. Export-r3 corrected primary account labels and startup
  cycle; native test ddeeec91 then reached cloud-final but exposed shadow-
  backup still etc_t. Export-r4 a71f5ce7 now labels all matching account files,
  including backups, with target policy; currently compressing/checking.
- Separate Mac default-path bug: OpenSSH interprets unquoted UserKnownHostsFile
  as multiple paths when state contains spaces. c8c045b82 quotes/escapes its
  configuration value, retains strict accept-new per-workspace host pinning.
  New isolated regression attempts using ssh -G and refused-connect debug output
  did not validate parsing (output loses path boundaries / expansion occurs
  later); those test attempts failed and were removed in0519795d2. Real native
  fresh-image SSH integration is the qualification gate. Prior96 tests passed;
  full suite with the final SSH change still pending.
- Macr6 source0519795d2 built/signed successfully51885d22; not yet launched as
  final preview. New local-gui-r6 controller/worker use packaged r6 and fresh
  export-r4. Stable install helper requires that test's passed report, validates
  base checksum, preserves previous preferences, installs ~/Applications/Luma
  Tester.app and registers Mobile image in normal imported-images state. It
  refuses active app/guest collisions. Helpers are private artifacts only.
- ARM export-r5 fixed first-boot presets and private SSH directory ownership via
  virtual bootstrap service. Native Mac GUI tests1cefbdee and7f554c20 passed
  functional fresh NoCloud, agent key, live snapshot same boot, shutdown and
  persistence. Visual inspection after reopen still showed folded/hidden Home;
  do NOT treat those functional reports alone as full visual qualification.
  Removing pointer hardware in b534a2b0 did not resolve it; production input
  profile unchanged. Same shared native Home bridge as x86 is now ARM-built.
- Cross-compiled scripts/tester/mobile-control.c using a private ARM header SDK
  from signature-verified official Fedora44 RPMs. SDK fa0832c9 initially lacked
  shared-mime-info.pc;9ec835ae reused target metadata for that dependency and
  compiled/installed ELF AArch64 module successfully. No build SDK packages
  installed into the OS. Receipt mobile-arm-alpha1/control-sdk-receipt.json.
  Export-r6 d02f9ae8 includes module+Phosh environment; Home capability true,
  dynamic display still false. Fixed864x1920/scale2 profile. Actual module/Home
  UI proof pending Mac local-gui-r10 ace30e3d (1GiB controller hard cap); source
  platform unchanged while this artifact-only job runs.
- User requested real download progress after a stalled-looking transfer.
  f37306a43 adds shared measured TransferProgress/TransferReporter callbacks,
  percentage/byte counts and separate chunked verification to network, local
  image preparation and GUI; e38abdafb uses read1/64KiB chunks and shows Waiting
  for data after5seconds.99 tests passed f7fc8cd5 and6ad64e0c, including resume
  counts, intermediate progress and no ready state after failed SHA. Subsequent
  Macr8 build7cc644ff also ran99 checks successfully.
- cc18fcd0f labels prepared images Ready to run and ISOs Installer ISO, separates
  their menu groups and uses Start Installer for ISO selection. Compact Menu
  hides group headings, so captions carry the distinction. Native packaged
  LumaUI UI test030b806f passed measured bar fractions, visible progress, actual
  waiting interval, verification, completion cleanup and installer label. First
  UI harness a3bb74ca expected hidden headings; corrected to inspect visible
  captions. Screenshot/qualification in root-jobs/luma-tester-user-421157bd74.
- Linuxr33 c6d53123 built progress candidate; final r34 834e5024 includes installer
  labels. AppImage SHA available in appimage-progress-r34/SHA256SUMS on owned HDD.
  Delivery/registration f8fbcb59 passed: Dell tester-progress-r34-20261023,
  version0.7.1, pinned existing tester-display workspace. Guest preserved true,
  gui_reopened false, registration97b7179c. Current Dell still runs oldr31 GUI
  and an active guest; do not stop it to activate the update. Partial ISO was
  147849216 of7198212096bytes, unchanged between read-only checks around21:00UTC.
  Retained partial/metadata for resumption; no user ISO removed or modified.
- Macr7 397099f47 fixes expected display EOF during normal shutdown. r7 package
  check0b24b57f passed and delivered, now superseded by r8 sourcecc18fcd0f with
  progress/installer labels. r8 built7cc644ff; final package validation, stable
  install and real ARM image registration pending r10 native outcome. Owned
  failed QA bases/overlaysr2..r6 removed only after checking no normal registered
  references/active processes; server exports/evidence retained. This reclaimed
  Mac space without deleting personal files or the final image.
- FINAL Mac ARM preview qualification: export-r6 plus app r8 sourcecc18fcd0f
  passed local-gui-r10 ace30e3d (native M4/HVF, local disk, fresh NoCloud status
  done, real embedded Mobile Home, measured verification progress visible,
  revocable agent SSH, live snapshot without reboot, graceful shutdown and
  persisted project after restart). Inspected window.png and guest-second.png:
  complete native Home grid/dock returns after reboot. The ARM build of the
  existing Home bridge resolved the visual reopen issue; no input-profile hack.
- Mac r8 mounted-DMG full inventory/signature/CLI check d6f36160 passed. Stable
  install e5e839ed passed; /Users/nick/Applications/Luma Tester.app0.7.1 is OPEN
  (observed process87068) using normal default data and imported ARM profile
  luma-mobile-arm-20261015-virt-r6. JSON profile/preferences parsed on readback;
  target mobile. Image ready for Start; normal user guest was NOT auto-started.
  Delivery details and full image/package SHA in private local-gui-r10/DELIVERY.json
  and tester-mac-r8/DELIVERY.json. Nick Downloads DMG updated, previous own r7
  retained. Disposable failed QA overlays/base hardlinksr2..r9 reclaimed after
  active-process and normal-state reference checks; Mac has8GiB free afterward.
- Actual Source/GUI evidence for latest changes:99 tests passed in the Macr8
  build; packaged Linux progress/installer UI030b806f passed. Final Linuxr34
  delivered/desktop-registered as above without reopening the running oldr31
  GUI or stopping its current guest. Updated dock entry opens0.7.1 on next launch.
- Public VM artifacts have NOT been uploaded or added to the Hub catalog. New
  Hub handoff section explicitly separates preinstalled Tester disks from ISO
  installation/Imager media. Physical phone root release remains20261015.21;
  ARM virtual display still fixed864x1920/scale2, dynamic device resizing off.
  Windows/Intel Mac builds and Linux ARM-TCG boot qualification remain outside
  this native M4 preview result. No public release/notarization/signing claim.

## 2026-10-27 final launch-prep checkpoint

- Implementation6910be67a + default/versionf7f807c6e; final package source
  b8878bd80 (includes factual checkpoint). Linuxr36 170c92d4 PASS,99 tests
  PASS2.223s.295610872bytes, SHA256
  f839fe7e57e3b33452a11cae2fe5932b01390a41d0251c682478633fc7d54d1a.
  Final candidate appimage-release-r36 on own bulk. GUI/core/image-library
  receipt hashes equal r35, whose native LumaUI6-combination test passed.
- ARM4GiB240sec probe75aaea85 reached initrd-switch-root and real stage2
  systemd; ldconfig/udev startup still pending at cutoff, screenshot black.
  QEMU/QMP startup passed; NOT a graphical installer or full installed boot
  pass. Exact release native M4 gate remains actual full ARM boot evidence.
  Earlier3GiB probes below OS minimum retained, not product failures.
  ARM initrd inspectiona3c5f620 confirms virtio_scsi module present; no transport
  or OS patch introduced. All own probe children stopped normally.
- Final live Hub recheck still latestx8620261026.9/ARM20261026.8. Actual complete
  bodies/size/SHA passed source=test downloader, both are installer ISOs.
  No auto-Try promise; that work is explicitly deferred by owner.
- Durable publication handoff (not an uploaded release index):
  /srv/dev-storage/projects/luma-os/artifacts/codex-luma-tester-20261017/release-handoff-20261027.json
  includes exact public artifact metadata, package path/SHA/source and limits.
- Mac still offline/no2244x tunnel listeners; native0.7.2 builder helper r9
  prepared but NOTRUN. Existing0.7.1 and all user guests/projects untouched.
  Next: reconnect native Mac, build/package/test0.7.2 with --release, then
  authorized release publisher uploads app(s) and signs real Tester index.
  No publication, Windows/IntelMac, or ARM-TCG graphical qualification claimed.

## 2026-10-27 Windows and fixed-dark checkpoint

- Source e9326815e adds Windows host locking, private runtime ACLs, QMP named
  pipes, authenticated loopback embedded VNC, WHPX capability detection and TCG
  fallback, Windows firmware/audio selection, pure-Python NoCloud creation and
  portable package assembly. d6f4aa950 fixes SSH null-device selection. Native
  Windows functionality remains under qualification; no Windows OS boot pass.
- de197756a makes Tester fixed dark in the shared entry point and LumaUI kit.
  No light-mode option; host appearance is not changed. dbe5c2198 updates README
  for this contract, public shared ISOs and current host packaging.
- Linuxr37 build500219ca passed102 tests; source d6f4aa950. Final AppImage SHA
  292c6bcac13847f021883b10736a78bf615d73adda5b079b25a16766e231289f,
  appimage-release-r37 on owned bulk. Actual native LumaUI dark/catalog UI
  qualification4b8e610d passed all6 experience/architecture combinations;
  root-jobs/luma-tester-user-dead4d0249/qualification.json.
- Windows dependencies resolved from official MSYS2 checksum metadata; qualified
  old native LumaUI runtime reused read-only. Current native C/header kit source
  byte-identical to the prior qualified Windows lane; current Python/CSS staged.
  OpenSSH official release ZIP digest checked; pure pycdlib wheel verified.
- Windows candidate-r2 build3682d0a6 PASS, source dbe5c2198, 0.7.2 unsigned ZIP
  SHA076bb5e2d97ffcb20cdcedfe0273c8f374252893cd699ac93d2d05c1d7458264,
  windows/candidate-r2 on owned bulk. No guest OS image bundled; DOWNLOAD_SOURCE
  tester. Windows x64 candidate only; ARM64 Windows host unqualified.
- Windows11 clone uses4GiB/2CPU, own disk/NVRAM/TPM, original VM remains off and
  original chain read-only backing. Initial native QA confirmed Adw dark and
  shared kit dark, then its screenshot harness failed on an absent render node.
  Subsequent QA and actual EXE startup stalled before a visible window; do NOT
  call native QEMU/VNC/snapshot checks passed. Native live control fixture is
  unbootable firmware-only media, not Luma OS. WHPX disabled in this fixture.
- Current Windows services: outer admission dev-tester-windows-vm-r4-a2b8e01c,
  root luma-tester-win-95cd73b718 (deadline around15:23UTC); artifact HTTP
  dev-tester-windows-artifact-http-53e04b6f (loopback8237, deadline15:22UTC).
  Own clone reboot request15:16UTC failed SSH banner exchange, so no reboot claimed; original untouched.
  Stop own fixture/HTTP normally after checks. Native Mac0.7.2 rebuild pending
  offline22442 tunnel; existing0.7.1 untouched. No publication/signing performed.

- Windows trace b441bfa9ac shows native gi._gi extension load stalled during
  platform_boot._fresh_font_map. No visible native window or QEMU control gate
  pass. Import-before-font-registration tried in disposable staging only, not
  source or published ZIP; screenshot remained desktop. Own VM ended normally
  at bounded deadline15:23:41UTC; HTTP ended at its deadline15:22. No live own
  Windows fixture/HTTP left. Graceful ACPI request arrived after VM ended.
- M4 reconnected15:21UTC with5.1GiB free. Cleanup a1b1b90c verified Mac r2..r6
  DMG digests against server copies, no mounted/running consumers, and removed
  only generated app/work/inputs staging; retained packages/receipts, r1 native
  kit cache, r7/r8 and installed user app/VMs. Free space9,965,572,096bytes.
  Starting native0.7.2 r9 dark-only build from committed source; outcome pending.

## 2026-10-27 native Mac0.7.2 delivery and Windows runtime separation

- Macr9 build83943823 PASS, source6b944673d. DMG SHA256
  9d5a9ccab4e482f719c9671ee5f87f9abdffbe86194d1d93201a54adcc47f06c.
  Native qualification3547f3d7 PASS: fixed dark, real catalog for all6
  Desktop/Tablet/Mobile x86/ARM combinations, ARM HVF and x86 TCG firmware-only
  starts plus live snapshots with unchanged QEMU PID and paused-state retention.
  No additional full OS boot claim. Evidence tester-mac-r9/qualification.json.
- Mac mounted-DMG delivery93be70f5 PASS: every receipt file hash/size, deep strict
  signatures and CLI version checked. Installed /Users/nick/Applications/Luma
  Tester.app0.7.2 and OPEN; new DMG in Downloads. Known0.7.1 preserved under
  tester-mac-r9/previous; no user guest or app data touched. Minimum macOS27.0,
  ad-hoc signed, NOT notarized or publicly published. Delivery.json retained.
- Windows source59464fe6a separates QEMU's DLL closure into helpers/qemu; adds
  missing GVnc components without replacing qualified UI DLLs/typelibs/modules.
  Windowsr3 build82f466c2 PASS,255MiB unsigned ZIP, SHA256
  ed19b7c1a53d2f4832f44906851bb6045fb913c60e8a28b3ad3f1101cf41e5ec.
  Native qualification still pending. This package supersedes r2 for testing.
- Windows fixture r5 stopped15:36UTC after ACPI did not finish. r6 admitted6GiB
  with4GiB guest and clock/Hyper-V CPU optimizations from original XML; original
  VM remains off/unmodified. Root luma-tester-win-25e0dc5492, outer
  dev-tester-windows-vm-r6-2d65bab3, deadline about15:54UTC. HTTP r3b
  dev-tester-windows-http-r3b-e4d40bd0 bounded600sec from15:47.
- Important fixture correction: quser Active does NOT mean unlocked.15:47
  screenshot showed locked console; prior UI Run command went into masked login
  field and did not run. Explicit unlock d708954c followed by desktop screenshot
  proved unlocked at15:49; no credential output. SSH banner timeouts persist;
  using an interactive Run setup to SHA-check/extract r3 and open its exact EXE.
  Do not call Windows visible GUI or backend snapshots passed until verified.

- Final Windows r6 ended at its admitted deadline15:55:05UTC; no live VM left.
  R3 was not proven launched: SSH command failed before banner; interactive
  Run setup input was unreliable (last frame Windows cannot find Bypass).
  Do NOT attribute this input/transport failure to r3 startup code or call it
  passed. Initial r1 dark-only assertions remain narrow observed evidence.
  Own HTTP root28cfb5618f stopped afterward; original Windows VM untouched.
- Next Windows useful action: use a stable unlocked console/SSH connection with
  enough Windows memory, SHA-check and launch exact candidate-r3, then run the
  retained native QA for authenticated VNC, QMP pipe and live snapshots. Tests
  use firmware-only ISO, not a full OS boot. Private helpers/input failures are
  retained for diagnosis; do not propagate their console assumptions to users.
- Latest release-handoff-20261027.json on owned bulk now records Linuxr37,
  Macr9 installed/qualified (DELIVERY.json), Windowsr3 unsigned/unqualified,
  exact hashes and original two public installer ISOs. No public upload/signing
  or notarization performed. Mac application remains OPEN for owner testing.

## 2026-10-27 current-source Tester media

- Owner requested current GitHub x86/ARM media ahead of the proper nightly
  publication, plus a concrete image/app-update delivery plan. Read current
  nightly source and producer task records; preserve their work and signing
  lane. GitHub main is older; the reviewed release source is eccd6bb20f2b2fd0afb784ea868621aabc09ca75,
  the same PR76–78 set as the active nightly (PR79 installer changes excluded).
- Own admitted job dev-tester-current-isos-r2-941f8615 is running with4GiB RAM
  and64GiB HDD admission. Private producer build-current-isos.py and runner
  are in the owned luma-tester-phone-20261017 experiments directory. Bounded
  root driver has512MiB and a3GiB isolated keyless media container. It waits
  for exact normally signed exports20261027.2/x86 and20261027.1/ARM, then
  assembles private ISOs using the maintained runtime reuse producer. It does
  not compile ARM on x86, sign, publish, mutate another pipeline, or assert
  guest qualification. Current state: waiting for both signed exports.
- Output: owned bulk current-source-isos-20261027-r2. STARTED.json binds the
  archived source; STATUS.json records actual progress; BUILT.json appears
  only after full producer checks. FAILED.json records any failure.
- Added docs/developer-platform/tester-release-delivery.md: two shared OS
  ISOs through the existing Hub catalog/tickets, independent app packages,
  immutable workspace backing, user-initiated updates, signing/publication
  gates. It is a proposal, not a claim that auto-updates or live ISO mode ship.
- Next: inspect producer completion, resolve actual failures, boot each actual
  ISO on native CPU and report scope honestly. Public delivery remains gated
  on the normal release infrastructure; Windows and notarization gaps above
  remain open. No new application binaries built in this step.
- Preflight dev-tester-media-preflight-r1-0013a773 PASS: actual pinned native
  Atlas tools, generated frozen source with canonical read-only Git objects,
  exact old/new runtime source closure and BOTH real donor ISO metadata. This
  is not a new ISO or guest boot PASS. PREFLIGHT.json retains nine unchanged
  runtime source boundaries and both actual volume labels. Python compile
  checks and git diff --check passed.
- R1 was deliberately stopped while waiting, before any media assembly, to
  retain the already signed minimal payload summary and exact binary public
  key in R2. No unsigned replacement summary is generated. R2 validates the
  signed summary with normal OSTree GPG admission and compares it after use.
- Baseline helper experiment: first admission0fd02b9d correctly declined the
  shared coding lease75; e7d29925 started a separate keyless private baseline.
  Stopped ONLY that helper at16:25UTC after the upstream owner's retry appeared,
  avoiding duplicate bundling. No VERIFIED.json exists, and the wrapper's
  exit0 after systemd stop is NOT a baseline success. Partial own output kept.
- 16:39UTC: upstream x86 baseline-only c2807b38 actually closed0; all26
  bundles and manifest exist. This is input admission, not OS/image boot.
  ARM normal application signing47d010ca is active. Own R2 assembly remains
  healthy/waiting for both exact signed OS exports. It survives chat disconnect
  but has an approximately3-hour hard deadline; no ISOs have been built yet.
- Actual GitHub release branch head032672da6a9a7ca40b985585e3ce6ceddde6e44f
  inspected through API: commits after PR77 concern origin delivery/graph
  metadata. PR76 is desktop touch scrolling/Shell state; PR78 is Wi-Fi artwork.
  Main remains older; no unreviewed installer delta selected.

## 2026-10-27 curated images, updates and Try repository

- User approved proceeding, explicitly requested Installation media contain only
  the new approved CPU pair, and created SimplyLuma/try for cross-platform apps.
  Added Tester0.7.3 app-update checks/download prompts and exact ISO curation.
  updates.py selects by HOST OS/CPU, stages through the existing verified resumable
  downloader, reveals a package and never executes/replaces it. GUI defers the
  offer while active, remembers offered versions, and permits a manual recheck.
- image_approval.py consumes the owner-controlled HTTPS Try channel with exact
  artifact ID/SHA256, max one per architecture, monotonic generations and cached
  offline fallback. Old catalog rows are filtered at load, refresh and render;
  Older Builds removed. Local imports/workspaces are preserved. Embedded and
  public generation2 intentionally EMPTY until actual final IDs/digests qualify;
  reserved20261027.2 was superseded by retry4, so do not approve stale reservations.
- SimplyLuma/try main c09b08f initialized/pushed with README and channels contract.
  Source remains converged in OS; Try houses release assets/control metadata.
  New own worktree /srv/dev/worktrees/luma-try/codex-release-home-20261027, task
  codex-release-home-20261027 registered. No packages, signing keys or private
  evidence uploaded. Its task file is ignored. Public image channel remains empty.
- Final full unit job f84b34b4 PASS109. Focused channel af91988c PASS4 including
  fresh higher-generation persistence, rollback rejection and origin rejection.
  Final real LumaUI7efd5919 PASS: active deferral, once-per-version/manual recheck,
  package staging/reveal without execution, existing snapshot preservation,
  curated Installation media and absence of Older Builds. No real app update
  downloaded because the signed app index is not published. GUI test uses a fixture.
- Original x86 compose r3 failed its storage guard, not an image boot check.
  Native ARM compose r2 succeeded; export r1 failed its40GiB storage preflight.
  Current own assembly dev-tester-current-isos-r3-e3f32932 (bulk suffix-r3) scans
  only completed signed exports for exact source eccd, selecting their actual
  build IDs rather than stale reservations. No new ISO built/boot-qualified yet.
- Own inactive /experiments/luma-tester-20261017 uses about18GB root SSD. Normal
  relocation dab5e940 (2GiB/32GiB HDD) checks no mounts/active consumers, full SHA,
  mode/owner/xattr/hardlink equality and source identity before preserving the
  original path via symlink and retiring only the verified original. Private
  receipts old-preview-storage-20261027-r1 on owned bulk. It is ACTIVE; never
  claim space reclaimed until PASS.json exists. Other agents' data untouched.
- Next: finish relocation, build0.7.3 packages from committed source, continue
  actual ISO assembly/native boots, then pin only the two final approved bodies
  in a higher Try channel generation. Native Windows qualification, Mac signing/
  notarization and signed public app-index publication remain separate open gates.

### 17:58 UTC continuation

- Own storage relocation dab5e940 actually PASS: all191,774 entries checked,
  bytes/metadata/links preserved, original path remains usable through a symlink.
  Observed main-SSD free-space increase11,698,438,144bytes (concurrent export
  writes explain difference from18,454,040,576 allocated source bytes).
- Mac0.7.3 native build8319f0f6 PASS from f5b865277ffe9b9328ad3beca561748bab0eafdd.
  DMG SHA2562e3e215c7ea662cded1cd846d7d867c981423c61d5d6380f1d1ce9f433b819b8,
  bulk tester-mac-r10. Native actual-bundle LumaUI update/curation17352ac5 PASS;
  deep/strict ad-hoc signature verified. Isolated NON_UNIQUE fixture preserved
  active production app/guests and used its own log. No notarization claimed.
- Linux0.7.3 AppImage2da1ab4b PASS packaging; bulk appimage-release-r38,
  SHA256be288699cf4b94118eb86547cf1a29ea7c7626229ae49d06d401971a1f236692,
  295,614,968bytes. Actual packaged native UI/version8511a4b2 running separately.
- Windows0.7.3 cross-package07f775db running with1GiB cap; native Windows
  remains unqualified. No new Windows VM started while images have priority.
- ARM native prepared transport-r2 now complete:5,204,111,360bytes, exact
  origin/receiver SHA256d0dcd385fb8bdb8892250416e046fa06393ab9859126a5682bb2ad66a3cbdcf7.
  This supersedes the earlier export-storage blocker but is UNSIGNED input;
  wait for the normal server signing/export completion. x86 export62ba8032
  imported its tree17:54UTC and is copying into the candidate repository.
  Own ISO job remains bounded/waiting; no new media/guest qualification yet.

- 18:00UTC: deliberately stopped ONLY idle own R3 assembler/rootunit before
  any media construction, releasing4GiB admission for upstream ARM intake/sign.
  This is canceled waiting, NOT build PASS. Retained R3 source/status, no BUILT.
  Prepared private build-current-isos-r4.py/run-current-isos-r4.py (new bulk-r4)
  to start after signed inputs actually exist; do not hold resources idly while
  upstream needs them. Both ordinary exports still pending at this observation.

- 18:03UTC: actual Linux packaged UI/version8511a4b2 PASS. Windows ZIP07f775db
  packaged successfully, SHA256156f3428681ef1bb024d4fb616204a602483eed89a0d8eda29ee0a7f711f78d4,
  266,916,337bytes, native Windows remains pending.
- SimplyLuma/try DRAFT release425808030, tagv0.7.3, uploaded all3 candidate
  apps plus SHA256SUMS. GitHub's asset SHA256 values match the local bodies.
  GitHub normalized Mac name to Luma.Tester-0.7.3-macos-arm64.dmg; combined
  SHA256SUMS corrected/reuploaded to match the actual asset name. No public
  release, signed app-index publication or image-channel approval performed.
- x86 export.env closed17:59:46UTC, candidatef823c2620fb49c7617cee9cdec459d0f809b97815a9f65ec54d133eb7e52f6bb.
  Minimal targeted repo exists but summary.sig not yet present18:02UTC.
  ARM native primary QA d938d539 is active; server pipeline still absent.
  Next image action: when final signed minimal repos exist, admit own r4 media
  assembly OR consume the producer's exact same-source qualified remaster to
  avoid duplicate I/O. No old media approved as a substitute.

User explicitly asks where ISOs are. Both signed export.env now exist, ARM
completed18:04:39UTC. x86 producer remaster953cf215 active, output pipeline/media/nightly/20261027.4;
do not duplicate. Own ARM-only normal f1f51a3e now actually assembling, bulk
current-source-arm-iso-20261027-r4/aarch64; exact signed candidate adf510f5...
and source eccd, normal4GiB/64GiBHDD. Initial admission75 respected, bounded
lightweight queue then admitted normally. No ARM executable runs on x86;
reuse maintained native donor runtime plus authenticated ARM payload as data.
Prefer consuming this exact same-source remaster rather than starting another
ARM ISO build. No BUILT/boot qualification yet.

Current ISO blocker: x86 remaster953cf215 FAILED because published donor omits
optional /.treeinfo. Upstream helper owner has added conditional handling in
its own worktree; preserve their fix. ARM r4 still recompressing clock-only
runtime, likely reaches the same missing optional metadata next. Own private
resume-iso-clock.py adapter applies the same optional-metadata condition and
reuses ONLY the successfully completed original clock-only images after sealing
full hashes, original donor hashes, exact epoch, source archive and known
failure. It keeps the prior runtime read-only and reruns all normal source,
signature, new-ISO runtime, UEFI and boot menu checks. No OS source/payload or
signature policy changed. Operator adapter/hash is recorded separately.
Private build-current-arm-iso-r5.py/run-current-arm-iso-r5.py prepared; bounded
queue-arm-resume-r5.py running as exec session41917 (15-minute bound), waits for
r4 actual close and uses devctl4GiB/64GiB normally. It skips recovery if r4 builds.
No finished ISO or new native boot PASS can be claimed at this observation.

User status continuation: upstream ARM data-only remaster aaacaf87 is now
actually running native-media-20261027.1-r1. Stopped ONLY own superseded ARM
r4 rootunit+normal f1f51a3e to avoid duplicate compression/I/O and free4GiB
normal admission for upstream x86 remaster resume. Own r5 queue is no longer
active; no r5 build started. Own canceled r4 is not a build PASS. x86 owner has
remaster-resume-r5.py and maintained conditional optional-treeinfo fix prepared;
upstream targeted guest QA e4895194 currently active. Follow actual upstream
media receipts; do not claim either ISO finished or download-ready yet.

User explicitly directs BOTH ISO jobs actually run. Narrow launch of already
frozen x86 resume operator (no source edits to the peer worktree): normal
c8c32a09, paired MAIN3GiB/1GiB-memory holder1ae6e387; original owner holder
script writes its expected MEDIA-RESUME-HOT-RESERVATION.json. Protected original
resume helper and E2 export digest unchanged. x86 writer actually27% observed;
normal root unit tester-oct27-x64-remaster-r5-20261027T182811.

Upstream ARM remaster aaacaf87 actually failed: mksquashfs SIGKILL; no finished
clock receipt. Started own corrected ARM r6, normal cd24f489 (4GiB/64GiBHDD),
with paired main-SSD7GiB/1GiB-memory holder2002fb3f. Initial8GiB SSD request
was correctly refused;7GiB bounds the approximately5.5GiB installer scratch,
normal admission accepted without reserve changes. No attempt to use secondary
SSD. New private operator arm-media-memory-bounded.py makes optional .treeinfo
conditional and sets mksquashfs2workers/512MiB cache; container3GiB. Installed
OS source/payload/signatures unchanged. Every normal source/runtime/signature/
new-ISO/UEFI check remains. Own work on SSD arm-fast-work-r6, ISO/proofs in
bulk current-source-arm-iso-20261027-r6. OPERATOR-CONTROL.json retains adapter
hash and actual SSD admission. Root worker polls physical20GiB floor and holder
identity during container execution. Both actual root packaging processes
confirmed active, not just queued wrappers. Do not duplicate either ISO job.

## Try Luma name and icon directions

User requests Try Luma display name and three Prairie-style icon choices, with
native Mac/Windows/Linux formats. Renamed UI, desktop entry, native bundle/exe,
portable launchers, package filenames, update prompts and current product docs.
Kept app ID io.luma.Tester, existing state directories, attribution/schema IDs,
CLI and guest managed-file markers stable. Source pyproject version synchronized
with existing0.7.3 module value. No running installed user app replaced.

Three authored SVG concepts in src/luma-tester/icon-concepts: Luma Dot, Play,
Portal, sharing existing Prairie tile/lighting geometry. No candidate selected
as the final shipped icon yet. export-icons.py generated actual ICNS, Apple
1x/2x iconset, Windows ICO10sizes16..256 (readback asserted), and Linux hicolor
SVG/PNG16..1024 for each. Native package/export zip plus comparison PNG in own
bulk branding-20261027. Initial render completed exports but preview font lookup
failed (DejaVu absent); corrected to installed Noto Sans. Final bf154d2b and
native-export ZIP ba7d40ff actuallyPASS. No raster/package committed to source.

Windows packager now builds its own PE launcher with native ICO + VERSIONINFO
via MinGW-w64 gcc/windres, replacing the unrelated copied launcher. Requires
explicit build tools; final new PE/nativeWindows validation is still pending.
Mac existing native iconutil/Info.plist pipeline retained. Rename check1e7bee50
PASS110 existing unit tests plus actual native LumaUI update/image-menu smoke;
all modified Python parsed and gitdiffcheck passed. Generated exports inspected.

ISO continuation: r6 failed before runtime work due old canceled container name
remaining registered (raw125 preserved). r7 uses a distinct container name,
new output current-source-arm-iso-20261027-r7 and SSD arm-fast-work-r7, same
normal4GiB/HDD64GiB and paired acceptedSSD7GiB reservation. New normal b74eea4c
launched; confirm actual container activity before calling it active. x86 resume
c8c32a09 remains the existing frozen producer, no duplicate x86 task started.

User selected icon2 Play, requests proper alignment. Pending aligning/publishing
selected SVG and regenerated native assets. User then prioritizes continuous ISO
monitoring and immediate failure recovery. x86 writer74.5% observed. ARM r7
failed guard because shared auxiliary holder's .closed marker was written
CLOSED_FAILED_PARENT_NOT_IMAGE_PASS, holder exited; no finished runtime cached.
Own no-holder HDD r9 prepared but admission correctly refused memory; NOT RUN.
Current upstream arm-media-recovery-oct27/build-hdd-r2.py already applies the
same standalone-HDD correction, self-owned reservation identity,3GiB container,
512MiB compressor cache and optional metadata fix. Its normal2f85b604 active;
output /srv/dev-storage/projects/luma-os/build/arm-desktop/arm-media-recovery-oct27-r2.
Consume this ARM output; do NOT start a duplicate own r9. All normal4GiB/HDD64
admission checks remain. Monitoring x86 c8c32a09 and upstream ARM2f85b604 through
actual completion/boot checks. No image completion claimed yet.

x86 REMASTERED.json now actuallyPASS (not boot qualification):7,034,372,096B,
SHA2569eb954fc858cd3e801f395399e0c833e29791c25eb31de03de05cd9800f24604,
pipeline/media/nightly/20261027.4/luma-nightly-20261027.4.iso. Native boot pending.
ARM stable r2 finished its full clock-only compression/proof, then failed ONLY
Lorax mkksiso's host-architecture check. No more compression is needed.

Own r10 normal63d90e51 launched4GiB/HDD64GiB, output bulk
current-source-arm-iso-20261027-r10. Seals full SHA of r2 completed stage2/initrd/
clock proof and exact source archive. Cached files mounted read-only. Private
resume-iso-clock-native-map.py reruns normal source/key/candidate/runtime checks
and replaces only mkksiso invocation with native xorriso replay of authenticated
existing boot data, explicit3file mappings and implantisomd5. No ARM binary
execution or architecture spoofing. Extracted donor/output EFI FAT bytes must
match exactly in NATIVE-DATA-MAP.json; normal new-runtime/menu/font/embedded
UEFI checks and full ISO digest still run. This is private operator continuation,
not a source/payload change. Do not start a duplicate ARM or redo compression.

Icon2 selected: corrected vertical symmetry and translated complete filled
play glyph+11px depth by(-21.918965,-5.5) so its area centroid is512,512. Canonical
SVG updated along with concept2. Native export prepared, initial admission
correctly declined memory while both image jobs had priority; retry after slots
free. No selected native exports or new source commit claimed yet.

2026-10-27 19:24 UTC: BOTH ISOs actually completed against ECCd source.
Own ARM r10 BUILT.json:20261027.1/adf510f5...,6,530,924,544B,
SHA9fd8fcb0da681789cc9357f294b5cc896eb06bf68969c618d1f7fdc9459dccc9.
Native xorriso data replay retained exact donor EFI FAT digest; original
runtime/source/signature/menu/font assertions passed. No guest boot claim yet.
x86 REMASTERED.json:20261027.4/f823c262...,7,034,372,096B,
SHA9eb954fc858cd3e801f395399e0c833e29791c25eb31de03de05cd9800f24604.
Do not recompose/recompress either. Upstream separately adopts its own ARM
body; only ONE final published exact ARM hash may enter approval feed.

Actual x86 boot attempts d38aa39c/832445ce failed fixture checks: media file
had already been handed to uid107/QEMU, then root-private parent blocked
hypervisor traversal. ISO remains intact. Own r3 helper verifies full ISO hash
and exact uid107/bytes, temporarily grants only qemu traverse ACL to ISO parent,
records before/during and restores only if ACL unchanged concurrently. Maintained
UEFI/interactive installer test and VM7GiB hard cap unchanged. Normal8/HDD4
launch queued with bounded15s admission retries; do not stop peers or bypass caps.
ARM native exact-body transfer actually active normal1 f4cf0b5b to private M4
/Volumes/A016/Luma-Try-ISO-QA-20261027-r1 (29GiB available at preflight), 2.6GiB
transferred at19:24. Own VM uses bundled product QEMU/HVF,3072MiB/2vCPU, five
QMP screenshots, cleanup-finally. Exact copied digest required before boot.
Production Mac app/savedVMs untouched; no native OS boot claimed yet.

Selected icon native exports actually PASS e84193f3, finalfiles under bulk
branding-play-final-20261027:Mac ICNS+Retinaiconset,full Windows ICO,Linux
hicolorSVG/PNGs,Try-Luma-icon.zip,alignment.json and darkpreview. SVG2+canonical
mark complete silhouette area centroid512,512 including11px depth. Diffcheck
clean; source rename's earlier110 tests unchanged (no redundant icon-only rerun).

2026-10-27 19:28 UTC: actual ISO boot checks BOTH completed. x86 normal
eea3b297 PASS, maintained interactive installer UEFI test took129.86s and three
stable screenshots ui6/7/8 allSHA02f15f55d03ad61cdecadac18e6408423610815592a2c1c614b9a1bfadfb1ed5.
Visually inspected ui6: actual language/keyboard/Continue page, no error screen.
Own ARM r10 body exactSHA9fd8... transferred and digest-verified on M4, bundled
QEMU/HVF3072MiB2CPU boot, normalf4cf0b5b PASS19:28:05 after five30/60/120/180/240s
captures and ownVM cleanup. Visually inspected120s: same genuine installer UI.
First30s display was inactive during startup, not classified as a bootfailure.
Readable PNGs and actual visual-review receipt in account artifacts:
try-luma-iso-boot-visual-review-20261027.json. Original rootBOOTED receipts retained
in bulk/x86-iso-boot-20261027-r3 and bulk/arm-iso-native-boot-20261027-r1.
This is installer-only validation; no full install repeated. Upstream adopted
ARM r4 uses SHA c0a68aeca9c5698f60c2a9b3be29888730b52f9e5eb069785c1859f25d5550b8,
same ECCd source/adf payload but DIFFERENT ISO bytes. Own ARM boot result MUST
NOT be relabeled as canonical r4 exact-body validation. Hub catalog queried
19:27 still serves previous26 images; curated channel stays empty until final
public qualified exact ID+SHA pair. Publication is separate from built/booted.

2026-10-27 19:36 UTC: publication peer independently booted CANONICAL ARM r4
body c0a68aeca9c5698f60c2a9b3be29888730b52f9e5eb069785c1859f25d5550b8 on M4/HVF.
Consumed protected native-media-20261027.1-r2/ISO-BOOT-PENDING.json, matching
ECCd/adf/6,530,924,544B. Viewed actual atlas-boot-r1-1.png: genuine language/
keyboard chooser and Continue; both stable copies independently SHA583311945ebf53e9079f65e93355cf61a04f532d5c800df41de47d94f1150bb3.
Own account visual-review receipt now adds canonical_arm_review separately;
no own-r10 result relabeling. Publisher retains its qualification/gate scope.

Renamed packages built from fb032f484 (selected play icon source d89582266):
- Linux r39 normal173d70c9 PASS; real AppImage version + packaged LumaUI QA
  e453ef33 PASS. Both installed and module SVGs match selected icon; desktop
  Name=Try Luma. SHA47e4c21a0ab0e268f2c5166c0c4d769f36e66950f28a6cd35c90720b1b12966a,
  295,614,968B. Bulk/appimage-release-r39.
- Mac r11 build4315abfe and native UI/deep strict codesign QA246561ad PASS.
  Native ICNS/retina icon, ad-hoc only, not notarized. DMG SHA
  b3f3b239201a2f9333fe9381648eb5411fd9f9e9f175363fb74e94143bc61c44,
  65,432,392B. Bulk/try-luma-mac-r11. Production app untouched.
- Windows r6 build860e637d PASS. New compiled Try Luma.exe contains 10 ICONs,
  GROUP_ICON and VERSIONINFO (AMD64 GUI/ASLR/NX; imports only standard Windows
  KERNEL32/msvcrt/USER32). ZIP CRC all files PASS; no old root folder name.
  SHA4c8ececa4ef72a6320de2667d1766743a6fa95c9faa58c0c160bbf16d1df146d,
  267,059,767B. Native Windows app runtime remains UNQUALIFIED. Build-r5 raw
  failure preserved: extracted MinGW gcc omitted split cpp package/cc1; scoped
  authenticated Fedora cpp intake fixed tooling, r6 compiled normally. No host
  package installation, alternate identity or copied Leaf launcher.

Draft refresh19311a7f PASS: uploaded all three new names, independently compared
GitHub asset digest+bytes with local hashes, then removed ONLY this task's
previous old-name draft packages and replaced SHA256SUMS (SHA9a12b158...).
SimplyLuma/try release425808030 title Try Luma0.7.3, draft=true/prerelease=true.
Actual names include GitHub-normalized Try.Luma-0.7.3-macos-arm64.dmg. Receipt
bulk/try-luma-draft-073-r2/DRAFT-UPDATED.json. Public release NOT performed;
Mac notarization, native Windows runtime, final source publication and final
public OS exact-ID/hash channel approval remain separate gates. Hub still
served26 images at last check; curated images generation2 intentionally empty.
All own image/boot/package/upload jobs are completed; none left stalled.

2026-10-27 icon revision: user requested a left nudge after viewing selected
play icon. Shifted complete glyph+depth+shadow 16 source pixels left, preserving
vertical placement and tile styling. Updated both source SVGs and concept note.
Native export normal e045f4b1 PASS; review preview and Mac/Windows/Linux assets
in bulk/branding-play-left-r2-20261027. Previously uploaded draft binaries still
contain prior placement; no package rebuild or draft replacement claimed here.

2026-10-27 final publication authorized explicitly by user. Approved optical-left
icon committed9b67dd912. Final builds all PASS: Linux r40 acec4751, Mac r12
5b04e70f, Windows r7 3bc71c08. Actual final AppImage/native LumaUI ddd627fe and
native Mac LumaUI/deep codesign9b0b5de9 PASS. Final draft refresh a519e0ed PASS:
three packages plus exact packaged-source archive uploaded and GitHub digest/
size verified, receipt bulk/try-luma-draft-073-final-r3/DRAFT-UPDATED.json.
The draft API reassigns an untagged temporary tag when saving without tag_name;
final publish MUST explicitly set tag_name=v0.7.3 in SAME PATCH as draft=false.
Website agent was given permanent v0.7.3 Mac/Windows/Linux URLs, not yet public.
Mac filename is GitHub-normalized Try.Luma-0.7.3-macos-arm64.dmg.

Public converged source PR84 all four CI checks PASS (including Tester) and
merged e5e69d1c1c60dec8ba3e233a2a2579b2e3ec9150. Clean source e14e70f6
publishes only 46 relevant files, preserves public toolkit features and adds
only six-line fixed-dark opt-in to shared widgets; no private task history or
unrelated toolkit rollback. Exact packaged older-kit source accompanies release
archive, SHAfc4d1a48408000616cc7a65f126006b4166b6952bf6be5e48a95ae30b85ed5ee.
SimplyLuma/try title corrected # Try Luma, alt text and description/homepage,
pushed bb247ca after owner steering; no unrelated screenshot edits lost.

Native Windows attempt r8 normal699aca2f did not establish a test verdict:
private console locked, Interactive scheduled task did not start until later
manual private fixture sign-in, then outer polling budget expired. Original
failure preserved, original Windows builder untouched and own VM cleaned.
Corrected r9 normal a5da622d reuses only its own existing overlay/NVRAM/TPM,
retains6GiB/4CPU actual guest in normal8/root7GiB, actual original NIC identity
preserves Windows network profile (QA fixture only, not product changes).
Private console lockscreen visually verified then signed in with existing
private fixture credential; quser confirms consoleActive. CONSOLE_READY flag
set after sign-in, so actual native QA timeout begins after readiness. Current
bulk/windows/native-final-r9; original base is read-only backing.

ARM signed publication normal51cda6d3 belongs to existing OS publisher and was
already active; not duplicated or stopped. It uploaded canonical ARM .1 body
and signed media metadata; Hub now exposes exact c0a68aeca... approved checksum.
Public origin/readback still active at latest observation. x86 .4 publication
and final website acceptance/channel generation3 remain pending; do not approve
old x86 .9 or wrong ARM own-r10 body. No public app release PATCH yet.

Final native Windows check exposed a real cross-platform image cache bug:
core._prepare renamed/unlinked its NamedTemporaryFile while still open, which
Windows rejects with WinError32 (actual launcher.log recovered in native-final-r11).
Corrected writer lifecycle: exit with-block before hash validation/promotion,
close before failure cleanup, clear read-only bit on a remaining owned temp file.
Regression tests enforce Windows close-before-rename/unlink semantics using real
files; corrected their initial invalid cloud_init fixture setup. Full112tests
normal8b56562d PASS; firstfe5203e8 fixture errors retained. Native UI dark observed,
but no Windows backend PASS until actual rerun with this correction.

Own frozen x86 dispatcher515103e5 failed pre-publication predecessor comparison
because earlier ARM origin metadata did not match its local canonical summary.
No x86 ISO or OTA bytes were published by that attempt. All graph timers restored.
ARM canonical ISO+signed media are genuinely public (Hub c0a68aeca...), while
its subsequent OTA sync-origin13-check failure remains separately unqualified.
Preparing legitimate MEDIA-ONLY publication for qualified x86 ISO using unchanged
normal provenance manifest generator and frozen media prepare/signer/publisher;
no OTA predecessor check removed and no OTA publication PASS fabricated.

Final rebuilds with close-before-cache-rename fix75a130db1: Linuxr41 normal45cf090f, Macr13a26d5a7b, Windowsr814611b2e allPASS. Actual Linux AppImage/native LumaUI QA3b0c3ab8PASS; actual native Mac LumaUI/codesign QA retryccd4fe63PASS (first565fb288refused shared worktree lease75, no test). PublicsourcePR85 repaired against exact recovered publicmain without touching unrelated broken Codex ref, fourCI PASS, merged2a7c1214bd3d9fd38f8287878d7e50811063f28f. Owned Windows fixture r12b2e348e24 active; earlier own99363ec8 stopped before QA because controller path stillr9, corrected normally. Fixed draft refresh e846ed97 active, x86 signed media upload bd683ed5 active. Still no public app release claimed.

Both final ISOs now PUBLIC: qualified x86 .4 media-only bd683ed5PASS with PUBLICATION-READY confirms full7,034,372,096B SHA9eb954... public body plus signed listings; ARM canonical .1 public6,530,924,544B SHAc0a68... . Both actual Hub IDs/digests confirmed. Approved exactly this pair in SimplyLuma/try channel generation3 commitc74eb4b; no older catalog stragglers. OTA ARM origin issue remains separate, no OTA PASS asserted.
Windows r12 native check proved image-cache fix (Image verified) but found QEMU Windows pipe chardev cannot reconnect after first client close. Isolated native reproduction confirmed firstopenPASS/reopensEINVAL. Private AF_UNIX via native Winsock then passed three independent QMP greetings/capabilities in actual bundledQEMU/Windows, normal a2397aa1 (prior insufficient readiness probe ece9b490 inconclusive, no nativePASS claimed). Implemented shared private local-control socket helper, keeps timeout/owner-onlydirectory/no new TCP listener. Windows packager now also exports LUMA_APP_NAME=Try Luma so kit boot preserves the full display name.115 tests fbbd082aPASS. Source da1ab26ce. Rebuilding final packages with this correction; draft remains private until actual finalWindows session/snapshot QA.

2026-10-27 21:28 UTC: PUBLIC RELEASE COMPLETE. Try Luma0.7.3 published as preview at https://github.com/SimplyLuma/try/releases/tag/v0.7.3, normal55a0a456PASS. Full unauthenticated download body+SHA readback PASS for all three OS packages, source archive and SHA256SUMS; proof bulk/try-luma-public-release-073/PUBLIC-DOWNLOADS-VERIFIED.json. Final source da1ab26ce, native Linuxr42b10ceccaPASS, Macr146df2adb0PASS, Windowsr9 candidate nativecfe1de7fPASS. Windows actual dark LumaUI, password-protected embeddedVNC frame, private localQMP, live same-process snapshot, paused snapshot preserves pause, snapshot persists afterexit, HTTPS botharches; scope firmware/control ISO fixture, no full LumaOS-on-Windows boot claim. Own freshruntime-final-r15 backing originalread-only; oldr14 fixture recovery prevented verdict, preserved. Only owned clone console credential prepared (never printed/committed), original WindowsVM unchanged; test clone shut down cleanly. Optional early widget snapshot absent, actual windows-control.png exists. Public source PR86 fourCIchecksPASS merged d398bbf513c51a3c483d55f024696fc725d3fcbd. Both finalISO exactpublicbodies+signedlistings alreadyPASS, approved pairchannel3c74eb4b. Mac ad-hoc/notnotarized, Windowsunsigned explicitly disclosed in release. No OTA origin repair/pass claimed.

Mac signing follow-up owner authorized: Developer ID Application teamY98NLN3Q93 valid on M4, no private keys/passwords exported. SSH attemptsr1/r2failed errSecInternalComponent because keychain access/session; user explicitly unlockedlogin. AquaLaunchAgent jobr3 normal29b27f8b signed161MachOs and outerapp withsecuretimestamp+hardenedruntime, deepstrictPASS. Native signedapp LumaUI QA05d6e94cPASS; native signedQEMUHVF aarch64 andTCGx86_64 accelerator/firmware control smoke7a475319PASS (no fullOSbootclaim). Macapp remote ~/Library/Application Support/Luma/Development/try-luma-sign-073-r3; localUIproof bulk/try-luma-sign-073-r3/updates-ui.json. Xcodearchiveupload failed solely because onlyCommandLineTools installed, no fullXcode.app. No savednotaryprofile found, standardAPIkeydirectoriesabsent; user asked to securelystore profileTryLuma with notarytool onMac, no password requested inchat. SignedvalidatedZIP prepared normald441bd84PASS, NOTARY-INPUT.json notyetnotarized. APP+DMG notarization/stapling/Gatekeeper job scripts prepared underprivateartifacts try-mac-notarize-r3*, NOT launched until credentialsetupreply. Currentpublic0.7.3 untouched andstillaccuratelymarkedMacadhoc/notnotarized.

2026-10-27 21:54 UTC: NOTARIZED MAC PUBLICATION COMPLETE. Owner stored credentials underMacKeychain profileTryLuma; no password read/copied tochat/server/source. Normal8ffc0a24PASS: Apple APP145fcc65-2d68-4f60-a039-e197b975525dAccepted, DMGc6b7590a-c9ee-4bc5-aa58-5228d7c32504Accepted. Both app and signedDMG stapled/validated, Gatekeeper execute/openacceptedNotarizedDeveloperID. QuarantineactualpackagedDMG and appcopiedfromread-onlymountedDMG normal4264829bPASS; mountdetachedcleanly. Native signedUI05d6e94c andHVF/TCG7a475319 alreadyPASS; no runtimecodechanges. Publicadditive release normala230df63PASS: https://github.com/SimplyLuma/try/releases/download/v0.7.3/Try.Luma-0.7.3-macos-arm64-notarized.dmg 65,718,163B SHAcda63039c7208fe1558c8550b270d7f00218ef7cfabdf1d80a3a8eb4c8f94685. Complete unauthenticatedpublicdownload+newSHA256SUMS-macos-notarizedverified. All existing publicassets IDs/digests/bytespreserved; originalMacad-hocdownload remains foroldlinks, releasebody andTryREADME nowpoint to notarizedMacasset. Website/tryMacbutton mustuse NEW notarizedURL. Proof bulk/macos-notarized-073-r3/PUBLIC-NOTARIZED-PASS.json, Apple/Gatekeeper/quarantine receipts alongside. No outstandingownjobs or extraVMs; GUI signing/notarization agents unloaded.

2026-10-27 22:24 UTC: owner reports public Mac opens installer instead of Desktop.
Implementing 0.7.4: Desktop default (9d345402d), explicit Mac display-name env,
one-click approved native-image resolution/download/autostart, private one-time
unattended setup from SAME two approved ISOs with signed medium payload, then
normal persistent/autologin disk boot. Existing disk.qcow2 never partitioned;
setup uses separate workspace-install.qcow2 and completion receipt before promotion.
119 unit tests normal1a095d93 PASS before latest cancellation/progress/UI changes.
Exact public x86 input independently SHA verified normal01156f4b. Actual x86
first-start normal96a43ac4 active, installer pulling carried OSTree payload;
no Desktop PASS yet. Earlier harness attempts failed readonly-output/cross-bind
hardlink; corrected owned writable alias and fixture symlink, not product hacks.
Mac r1 fixture lacked bundled approval fallback; bundled policy now exact public
channel3 pair. Mac r2 refused safely because only4.3GiB free, no installer started.
Owner explicitly authorized moving obvious old artifacts to8TB. Normal e7cfd470
archives only own obsolete Mac build directories, full bytes/modes/link targets
verified before scoped removal; current signing output/current ISO/user VM/keys
untouched. Windows fullboot reservation refused memory; do not bypass, retry
when capacity released. Private scripts try-074-first-start-qa.py,
try-074-mac-first-start.py, try-074-windows-vm.py and orchestrate.py under account
artifacts. Mac old-build retirement script try-074-mac-archive.py and HDD
hdd-admission/mac-archive-074 retained evidence. Public0.7.3 remains unchanged;
no new release or cross-platform Desktop-boot PASS claimed.

22:30 UTC: source QA c1a220ca PASS:120 unit tests and actual bundled native
LumaUI fresh Desktop Start -> approved host image resolve -> download -> launch
(with isolated network/download/VM fixtures). First UI harness failures were
40s cold-runtime timeout, then wrong host_arch import; corrected writable cache,
120s outer bound and imports, no product workaround. Added private per-workspace
Ed25519 control key to setup KS; only public key goes into guest, sshd reachable
through loopback forwarding. Native workspace readiness now requires active
Wayland/X11 user session plus real gnome-shell before viewer reveal; enables
existing developer controls for provisioned disks and SSH poweroff. Linux first
install still running older no-control-key prototype, so not a final readiness
PASS. Mac archive e7cfd470 verified all40,751 content/mode/link records, SHA
6e6ed6ddbbd46edc5a667644f653648d841c04cb0ccdfca6d3bd58b94f016554,
then source rehash+scoped retirement completed; available Mac disk now10GiB.
Owner authorized further obvious old migration: second normal4b487a3f archives
old Sept30/Oct1 non-Git LumaUI handoff bundles, already-published buildr14 and
three redundant notarization staging copies, preserving current signed app,
credentials, native kit build cache, user VM and current ISO. More Mac space
needed before retesting new control-key setup. No Windows/desktop-boot claim.

22:51 UTC: Mac ARM actual public c0a68...ISO -> private unattended setup ->
autologin desktop PASS, normal560d6cda completed0. Setup took about83s on M4;
actual SSH active graphical session+gnome-shell and full desktop screendump
visually reviewed (bulk/first-start-074/mac-native/desktop.png). Native packaged
Mac0.7.4 UI8bc425e8PASS: title Try Luma, Desktop default, no-image Start resolves,
downloads and launches. First private UI helper had replacement indentation
error; corrected count=1, no product change. Macr15 build636a5d0ePASS.
Developer ID r4 ec2f4f21PASS, hardened nativeUI/HVF/TCG/zip9192e73cPASS; not yet
notarized/published. Linuxr43 package89db3244PASS, actual first x86 setup96a43ac4
still pulling on slow HDD (8.6GiB allocated at28m). Windows fullboot e04b7df6
active in own clean r15 overlay, original VM off; whole exact publicISO received,
verified, private unattended installer actually booting. No Windows desktop PASS.

Mac restart/snapshot cf8c7006 found actual shutdown race: SSH closes promptly
but QEMU needs >5s to power off. Raised bounded wait to60s; disconnect alone still
fails if VM remains up.122 tests178a0bfePASS, including regression exceedingold
5s window and negative disconnect case. Native live snapshot completed before
that failure; persistence retest2491ed1a active with fixed control code and new
snapshot name, same existing QA disk. Mac QA disk13GiB leaves2.8GiB free;
after persistence checks archive/verify/move ONLY own completed QA session toHDD
before rebuilding final Mac or launching user's real setup. Current user's VM,
ISO, keychain untouched. Mac old-artifact migrations e7cfd470 +4b487a3f bothPASS;
second verifies35,086 files, SHA4a8bec48e4e5f5aaf265f8c0a113f07372ac7e8cda50d0ef1f90ac482a2c9f81.
Exact private archives/manifests/retirement receipts under HDD hdd-admission
mac-archive-074 and mac-archive-074-r2. Both restore complete archived copies.
Public source draftPR87 c668dbed allfourCI PASS; pending shutdown fix update.
Attach-artifact tool called but hung, execution stopped; retry attach later.
Public0.7.3 unchanged; do not publish old r4/r43 packages before shutdown fix.

23:18 UTC: live status verified, not inferred: Linux private setup disk grew
13.0GiB ->13.2GiB and serial advanced Writing objects ->Deployment starting.
Windows verified full7,034,372,096B public ISO and installer serial Writing objects
advanced pastguest1586s. Neither x86 desktop boot has passed yet; no release claim.
Mac persistence2491ed1a PASS: two disk boots, graphical SSH readiness, preserved
marker and live snapshot, clean shutdown. QA fixture archive0232b07c PASS after
correct GNU sparse1.0 parser handling and independent whole-file SHA+source rehash;
only completed owned QA tree retired, no user VM/ISO/keychain touched.
Slow HDD setup approached original60min timeout while still advancing; product
bound extended to120min for slow disks/emulation. Existing owned Linux prototype
QA supervisor3362985 received the same deadline extension through scoped Python
remote debugging, receipt QA-SETUP-TIME-BUDGET.json. This is explicitly not an
unmodified final-package run. Memory/disk admissions unchanged. Final Windowsr10
build43af6d16 started before timeout change and must be treated as superseded.

23:55 UTC: final Linuxr44 build18ab4264 and native packagedUI55ac885b PASS;
Windowsr11 buildbe832521 PASS. Extended Linux installed disk boot6157a430 reached
actual autologin Luma wallpaper+dock, reviewed boot-r3-desktop-21.png, at~8min.
Serial showed luma-app-baseline holding display-manager startup: prior150s capture
was premature, not a proven renderer failure. Provisioned GUI graphical readiness
bound240s is too short on slow first boot; extend to1800s matching bounded OS
baseline service, retaining real SSH/session proof before display reveal.
Windows first-start e04b7df6 ended external2900s harness timeout, not desktopPASS.
Retrybd4e34e5 active with finalr11, cached ISO, bounded9000s admitted root job,
7200s product setup bound and8000s QA watcher. Own failed setup temp can be retried;
no user workspaces involved. Macr16/signr5/nativeUI+HVF/TCG allPASS; notarization
attempts never submitted because staleSSH forwarding2235490 owned22442 and did
not answer banners. Verified exactUID1003, only22442 listener, no child jobs;
SIGTERM cleared only that stale connection. User restarted tunnel, pinnedSSH
connected23:54. Rebuild finalMac after first-boot readiness bound before signing.
Public0.7.4 remains draft, not released; no new signed/notarized release claim.

User explicitly approved replacing default unattended ISO installation with
exactly TWO prepared direct-boot images: ARM64 and x86-64, each shared across
Desktop/Tablet/Mobile, with automatic login. New preparation active:
ARM verified archive restoration487a5cdf PASS, sourceworkspace fullSHA08f421...;
x86 source already autologin desktop-qualified. Guestfish inspectionff53f8b0
failed appliance launch; switched to bounded private-mount raw-copy exporter.
Both exports actually running d3b2d85d/x86 and8e0187e4/ARM (normal4GiB each,
root3072MiB/7200s). Source disks unchanged; fresh raw copies only. At00:34UTC
actual allocated rawbytes~5.4GB x86/~10.6GB ARM, both fresh writes.
Cancelled ONLY owned obsolete installer Windows retrybd4e34e5/rootunit...234756
because that flow no longer qualifies the requested default; retained all logs
and Windows overlay, original Windows backing unchanged. This is NOT a Windows
boot PASS. New prepared flow needs fresh Windows qualification after export.
Added reproducible exporter + minimal private NoCloud key bootstrap under/etc,
retains signed/usr OS content and system Flatpak baseline, removes QA home data,
SSH hostkeys/machine identity/randomseed/logs, trims and compresses standalone
QCOW2. New luma-unified-v1 manifest protocol lets samebytes cover all three
interfaces and uses existing OS fw_cfg experience contract. Automatic GDM login
included. Exact prepared catalog not published/approved until fresh boot proof.
126 source unit checks411e296a PASS before final ISO-catalog suppression tweak.
No new prepared disk qualified/published yet; current Mac0.7.4 installed package
still prior613655bf1 source, do not claim it has the new prepared protocol.

00:59UTC: both prepared exports complete and disk checks PASS, ARM5486804992B
SHAa50b1452f1a9f7690b65959135c89be24e4d2d7d6d424f32cbedb8199cd3cf02,
x867547060224B SHA67ec675b7f7d951cf1a05ce3c59f210c541489d43456f4717361a33fb63cf6d7.
Final export dirs ownership corrected; no original source disk altered.
Linux fresh directboot5143fd6d active: newthinQAoverlay,3GiBguest, noinstaller.
Mac ARM transferc0aeb65e active to ownA016QAroot, fullSHA required beforeboot.
Initial transfere5fc8e3c refused75 samepathlock; retried separateownadmissiondir.
Macr18b3961f2e/sign88ccbd57/signedQA f94d821c/notarization6ac6be87 PASS.
Linuxr46 ea4bd977 and Windowsr13 a9bd6434 buildPASS. No prepared fresh graphical
bootPASS yet, neither imagepublished, public0.7.4draft unchanged.

01:15UTC: Linux prepared5143fd6d PASS actual graphical autologin111s
including7.55GBchecksum, seedserviceactive, nativeOSdesktopflag. ARMtransfer
c0aeb65e fullSHA PASS. Mac nativeQAe0add7bf active, signedr7Helpers/firmware
entrypointenv explicitlyset inprivateharness afterr1 lacked qemu-imgPATH.
Windows preparedQA3e79e068/root luma-arm-desktop-try-prepared-windows-r1-20261028T011246
active; ownedouterVMSSH ready01:14:25UTC, stagingr13ZIP+newQCOW. Generic
try-prepared-native-qa-r1.py verifiesgraphicalautologin+two reboots+persistedmarker,
privateSSHkeysinternalMac/largeQCOWandthinQAoverlayexternalA016. ExistinguserVM
untouched. NeitherMacnorWindowsdesktopPASSyet, no publicationclaim.

01:19UTC: Macr2 two actual autologin desktops42s/34s, secondbootmarker
persisted BOOT-1-PASS. HarnessoverallfailedACPIpowerdown120s; production
alreadyusesSSHSession.shutdown, correctedprivateharnessandfreshMacr3
801313c4 active. Windows transfer server3650122 FD7pos5.39GB/7.55GB,
Windowsdirreports0whileopen misleading. CorrectWindowsQAshutdowninplace
beforecurlfinishes via shortPSfile.Replace, noappchanges. Neitherimagepublished.

02:09UTC current release status: both prepared images PUBLIC full SHA readback
PASS a102eab8, keys tester/20261027-ready1/try-luma-{aarch64,x86_64}.qcow2.
Try channel generation4 approved/pushed db32d8f; legacy ISO approvals retained
only for compatibility, new clients default prepared pair. Public app0.7.4 still
DRAFT426082525. Draft staged files now Linuxr48/Windowsr15/sourcea915e4bba,
normal eaeabf11 PASS; Mac+SHA256SUMS still missing, do not publish yet.
Final private runtime6fb737613/publicPR87 a915e4bba allfourCI PASS,128unitsPASS.
User requested known localpassword TryLuma andsidebarbothTry/Developer. Added
Preview login username/password+Copy button, prepared-profileonly; native actual
packaged LumaUI sidebar both modes be7e55b0 PASS. First-run localpassword set,
SSHpassword+keyboard auth disabled/asserted, GSettings idle0/lockfalse onceonly
preserving laterworkspacechoices. Linuxfreshpassword/autologin b0ab1708 PASS132s,
passwdP and effectiveSSHno/no verified. Windowsliteralnewlines in SSH nowencoded
one-line transport (roundtriptestPASS), allSSHreadinessprobes15s bounded.
Macr20e8e589a6/LINUXr48ba45894c/Winr15fb598139 buildsPASS,source6fb737613.
Windows previousQA a0? actualold3e79e068 failed on explicitstop: ShiftwokeLuma
lockscreen, usercorrect aboutscreen sleep. No GPUfailureproven. NewQA a0af7692
root luma-arm-desktop-try-preview-password-windows-r2-20261028T020439 active,
candidate15, new thinworkspace/cachedbasehardlink,2CPU3GiBinnerguest,600s bounds.
Macsignr9/nativeUI/HVF/TCGPASS, Apple+quarantine+retrieval pipeline71db8eabactive.
Oldr8ApplePASS but quarantineattachfailedexternalmountpoint; r9usesinternalempty
mountpoint, dataexternal. Macuser allowed PythonremovablevolumeTCC and signing
resumed. NewnativeMacpasswordtwo-bootQAr4 started withsignedr9; userVMpreserved.

02:40UTC: Final Mac pipeline71db8eab PASS including notarization, internal
quarantinemount and full receivedDMG:65904143B SHA8a7dfbf403523c2da67032e24fc7d71abe88d341876304dcb9a489469f5d402f.
Fresh signedMacr9 password two-boots/persistence/clean shutdown1bbeba58 PASS.
Windowsr15 actualOSautologin/activeWayland/gnome-shell existed and password
configured, idle0/screensaverfalse; old automatedcheck stuck on Win32OpenSSH
nested quotes even after base64newlinewrapper. Numeric JSON argv transport
native Windows probe returned activeWayland/Classuser/Activeyes in2.97s,
no nested quotes. Corrected runtime Windows-only36331bd65,128unitsPASS and
public0b1168390 fourCI PASS. Candidate-r16 normal220fa8ebPASS; package differs
fromr15 onlycore.py and PEbuildtimestamp. Macr20/Linuxr48 unaffected, retain
validated6fbsource. SupersededWindowsr2 QA now stopping after guestcleanpoweroff;
next freshWindowsr3 controller usesactualcandidate16 and guestreboot in sameVM
for persistence, avoiding repeated7.55GBcache rehash solelyforreboot. Do not
claimr2PASS. Old gatedpublisher e498b743 explicitly stopped; needsreplacement
withr3 gate. Finaldraftrefresh e1178d8c active, replacesWindowsZIP/source/SHA,
sourcearchivePLATFORM-SOURCES.json documents Mac/Linuxa915 andWindows0b1168.
Trychannelgen4db32d8f live; bothpreparedfilespublicfullSHAverified.0.7.4stilldraft.

03:31UTC: Windows r3 revealed real GNOME startup deadline failure: user unit
started02:50:53, timed out02:51:38 after45s and was subsequently SIGABRT/SIGKILL.
This is not OOM or proven CPU incompatibility. Live owned-guest diagnostic adds
bounded TimeoutStartSec300 and restartsGDM; new shell currentlyactivating with
5min allowance, still awaiting active/visual proof. App now independently applies
this first-use override while preserving existingpassword/settings, readiness
requires systemd GNOMEunit active.130 componenttestsPASS. No new releasepublished;
old publisherd1f9 exited1 waitingWindows and must be replaced after actualPASS.

03:40UTC: Shared startupfix ceaa6632d/public3929872c1, allfourCI PASS130tests.
New builds Linuxr4994645164/Windowsr174cc775be/Macr21f398826d PASS.
PackagedLinuxsidebar2modes UI2ab8876f PASS; freshLinuxprepared strongerready
34a0c7d2 PASS168s. Macr10 signed, nativeUI/HVF/TCG, Applebothnotaryaccepted,
quarantineGatekeeper+receivedDMG allPASS c046? actualsuccessfulpipeline dcae813e.
Earlierr10pipeline8eb/c046 failures were missing privateharnesspaths, corrected;
no appfailure claimed. Macfresh2bootr5 4b2c0060 active. Windowsr3 diagnostic
restartdidnotprove shellactive before stoppingoldstuckQA. Windowsr4 outerVM
booted to Windows 'Please wait'; controller3minSSHdeadline expired3566b567;
no innerguestbootattempt yet. Longerbounded bootretry52f7a10f active, sameowned
outeroverlay, originalWindowsdiskreadonlyunchanged. Windowsnotqualified.
Draftstage5 42894c2e active Linux49/Win17/Mac10/source3929872c1/checksums;
publication script4 prepared but NOT RUN until WindowsPASS+visualreview.

03:42UTC: Maclatest signedr10 nativefresh2boot4b2c0060 PASS includingclean
shutdown, strongerGNOMEready42.4s/31.4s+persistence. Draftstage5 42894c2e PASS
all3latest packages/source3929872c1/Githubassetdigest+fullSHAverified. Windows
retryouterready03:38:49, candidate17 launchedfreshQArootr4 and cached7.55GBbase
hashing03:41; noinnerguestdesktopPASS yet. Currentrootunit
luma-arm-desktop-try-preview-windows-final-r4-retry-20261028T033756, normal52f7a10f.
Publisher4filegatesupdated but NOTRUN, visualreview stillrequired. Sidebarfeature
complete, finalrelease stilldraft pendingWindows.

03:55UTC: Windowsr4 actualstronggraphicalcheck PASS at462.8s elapsed including
~200sbasehash, confirmed13088byteWindowsSSHCONFIGUREworked andlocalpassword+
startupmarkers existed. Harnesssubsequentlyfailed collecting compounddiagnostic
SSHstdout via check_output(timeout40), notgraphicalcheck; noBOOT-PASSfile/screenshot
written because evidenceprecededcapture. Newr5df239657 reusesexactcandidate17/
sameownedr4workspace; screenshotscapturedBEFOREsimple'id' diagnostic redirected
tofile (noPIPE), persistencecheck usesremote'test', no test-onlycompoundloginctl.
Root luma-arm-desktop-try-preview-windows-final-r5-20261028T035114 active; outerSSH
ready03:52:16, repeatbaseverification57%03:54. NoWindowsfinalPASS/visualyet.
Do not claimreleasespublished. Gatedpublisher5prepared/notrun; usesr5results.

03:58UTC: Caught duplicate screenshotdestination in private r5QA beforedesktop
check completed; explicitly stoppedownedr5root (notPASS), correctedr6toonecapture
perboot+resetONLYownoldBOOT/PASS/screenshotoutputs before rerun. Rootnormal
adf25cdd active, helpertry-preview-windows-vm-r6.py, sameownedouterVM and unchanged
candidate17. Privateharness now redirects'id' stdouttofile; no compoundloginctl.
Expectedresults windows/prepared-074-r6, currentremoteQAroot remainsr4. Publisher6
preparedNOTRUN; must waitnormaladf25cddresult0, inspectfinaldesktopPNG, writevisual
proof, then execute gatedpublisher6. NativeLinux/Mac and sourceCI alreadyPASS,
all3latestassetsstagedverified. Sidebarfeatureimplemented andvalidated.

04:05UTC: UserrequestedWindowsstatus. Actualr6notPASS: cachedbaseverified,
launcher reported 'QEMU did not open its controls' (Windows10secpoll exhausted,
childhadnotexited). Stoppedownedrootr6 explicitly; no publication. Expandingonly
Windowslistenerstartup tobounded60s withchildlivenesschecks; nativeverification
required, do not claimfixqualified fromcodechangealone. Mac/Linuxpreviousruntime
unaffected byWindows-onlyguard.

04:10UTC: Windows-onlycoldcontrolwait bec89cfe7/public1183c6e0d,130tests+allCI
PASS. Candidate18 build6b2245c3 PASS. Draftstage6 181c2950 PASS (Windows18/source/
SHAupdated; Mac/Linuxunchanged source3929872c1). Finalr7 6910a50c active underroot
luma-arm-desktop-try-preview-windows-final-r7-20261028T040621; outerSSH04:07:14.
AddedisolatedbundledWinQEMU/AF_UNIXprobe beforeguestboot: actualtwo attemptsPASS
3.246s/0.108s, query-statusprelaunch, cleanquit. Thus localcontroltransportworks;
actualfullVM60sdeadline and2boots/visualstillpending. Probereceiptonremoteown
C:\TryLumaQA-Prepared074-r4\state\CONTROL-PROBE-PASS.json; willcopyto
windows/prepared-074-r7 afterQA. Publisher7preparedNOTRUN requiresr7PASS+visual.

04:18UTC: Actual Windows candidate18 r7 reachedstronggraphicalready422s,
then screenshot0 +id-to-file +persistentmarker creation allPASS (BOOT-0-PASS
written). Harnessfailedonlyonasserting SSHrebootexitcode0or255; WindowsSSH
closedconnection with anothercode (oldassertdidnotprintcode). Do not claim
rebootPASS. FullVMcontrolfilewritten5.391s afterprocesscreation; disklessprobes
3.246/.108s. Windowscore shutdownalsoassumed255 fordisconnect; expandedONLY
Windowshostnonzero handling toconfirmactualstoppedVM beforeacceptingsuccess,
otherwisebounded60sfailure. Regressioncovers-1/1/255actualstopand1stillrunning.
Nextnativecheckreopenssameworkspacewithpersistentmarkerandfinalpackage,
retainsR7firstbootproofand capturessecondactualdesktop+cleanpoweroff. This
checksrealclosing/reopeningwithoutdependingonSSHrebootreturncode. No release.

04:40UTC IMPORTANT correction: Windows service-ready checks are insufficient.
Retrieved r7desktop0 and r8desktop1 both visibly say 'Display output is not active'.
No visualPASS; do NOT publish. Linux r3 actual screenshot visually inspected and
shows full Luma desktop/dock, same x86base. Windows nativeGVnc authenticated and
receivedfullframe (privatecontinuousviewer), so investigating guestpower/renderer.
R8 actualsecondboot+persistedfilecontentPASS (BOOT1written), then actualpoweroff
occurred but Session.shutdown raisedTimeoutExpired15s onWinSSHstderrPIPE.
Uncommitted core/tests fix usesWindowsstderrTemporaryFile andacceptsSSHtimeout
ONLYafteractualVMstopped;132testsPASS in try-windows-poweroff-tests.log. Notyet
built/nativequalified. CurrentpublicPRheadf8d82605c/sourceprivate132e15ac2,
Windows19stage7466d0e71PASS; Mac/Linuxretainedvalidatedsource3929872c1.

Owned Windows outerfixture remainsALIVE for diagnostics under normalr8c4b41f48,
root luma-arm-desktop-try-preview-windows-final-r8-20261028T042128, mainPID4025360.
Afterr8failure04:30:51 controllerholds900s via runtime-final-r15/r8-diagnostic-hold,
then shutsouterVM at~04:45:51 unless intentionallysuspended/handled. Rootunit has
5400s cap until~05:51; normal8GiBreservation remainsactive. OriginalWindowsVM
viola-windows-builder remainsOFF, originaldiskreadonlybacking preserved.
Current diagnostic guest runs app19 actual Session.start with ONLY lab _prepare
bypassed afteralreadyverified cache (notnewfirstbootqualification), firmware/data
envexplicitlyset. Windowsprivate diagnostic-start.py/log in C:\TryLumaQA-Prepared074-r4.
HeldSSH helper execsession84266 (later actualrun) starts it; endpoint22390/VMQMP
sameownedruntime. WindowscontinuousGVncviewer helper exec66469 requestsincremental
updateslikeactualGUI; nativeGVnc initializationworked. EarlierStart-Process children
withclosedSSHparentwerekilled, fixedbyholdingSSHparent; notproductbug.
Linuxroothelpertry-preview-windows-wake-diagnostic-r8.py exec2347 currentlyconnected
throughouterSSH22490, waitsGNOMEunitactive thenprints ScreenSaver.GetActive,
Mutter.PowerSaveMode, doesdiagnosticSetActivefalse, printsPowerSaveafter. Watchresult.
Allhelpers /srv/dev/accounts/second/artifacts. Do not printprivatekey/VNCpassword.

04:44UTC DEFINITIVE display result: diagnosticWindowsguest renderedfullLuma
wallpaper+dock, inspected actualPNG try-windows-diagnostic-current.png. No input
or successfulwakeAPI was needed; earlierwakeprobe timedoutbeforemutation. GNOME
systemdunitREADY precededinteractiveJSmainloop; GetActive timedout20s while
serviceactive, later responds(false), PowerSaveMode0, idle0/lockfalse. Added
boundedGNOMEScreenSaver.GetActive response check to sharedwait_graphical so
launcher waitsforinteractiveShell ratherthanservicealone.132unitPASS. Current
Windowsnativefullstart took13.034s toopencontrols, definitivelyexceedingold10s;
60sWindowscontrolbound fixedthisactualcase. NoGPU/image change needed.

ROOTFIXTURELIFETIME: pausedONLYownedcontrollerPID4025360 withSIGSTOP after
checkingexactcmdline. QEMUWindowsVMcontinuesrunningunderexisting7168MiB/root
5400s cap,normal8GiBreservation. Prevents15mPythonfailureholdfromendingVMmid-
diagnosis; systemd90mcapstillends~05:51UTC. MUSTCLEANUP: removeonly
windows/vm-admission/runtime-final-r15/r8-diagnostic-hold thenSIGCONT4025360;
oldr8willshutdownownouterVM andexit1 (previousfailure, neverPASS). Do thisonly
AFTERnewqualificationusingthisVMfinishes. OriginalWindowsVMremainsuntouched.
Currentinnerguest diagnosticstartapp19 running (nohashduringlabdebug only),
WindowscontinuousGVncviewerprivateclientconnected; terminateguestcleanly before
newtestsession. Newfinalsource includesWinshutdownstderrTemporaryFile +actual
poweroffconfirmationonSSHtimeout, andsharedinteractive-shellreadiness. Needs
all3finalrebuilds +nativecheck; previousstage19 remainsdraft, no publication.

2026-10-28 05:45UTC: Current final source0e61631a0 was built as Linux50,
Windows20, Mac22/sign11; source PR87 public448bb77d79eb1c4eef9e0f952821692cdb3d3641
and132unitchecksPASS. Mac notarization, native2boot/persistentmarker/cleanshutdown
PASS; LinuxfreshbootandactualUIbothmodesPASS. GitHub0.7.4 remainsDRAFT426082525.
PreparedARM/x86QCOWs publicverified unchanged. Sourcearchive nowpins exactprivate
packagedtree0e61631a0 (publicsharedplatformhadunrelatednewerchanges); receipt
release-final-074/FINAL-DRAFT-VERIFIED.json includesbothcommits.

Windows R11 currently running normal44c8dbf4/root
luma-arm-desktop-try-preview-windows-final-r11-rpc-20261028T052943.service,
Windowscandidate20, outwindows/prepared-074-r11. Reusesfullyverifiedcache from
priorchecks, stockguestcommand/readiness/shutdown,1800sgraphicalbound. No final
PASS yet;05:44QMPframe inactive, guestserialttylogin, innerQEMUstillrunning.
WHPX featureenableattempttimedout and capabilityremainsfalse afterownedfixture
reboot; no accelerationqualificationclaimed. OriginalWindowsVM/diskuntouched.
Outerfixturecontroller4025360 intentionallySIGSTOP aspreviousnote, QEMUalive.
Extended ONLY its rootunit runtimeboundto9000s via
/run/systemd/system/luma-arm-desktop-try-preview-windows-final-r8-20261028T042128.service.d/90-qualification-bound.conf
(daemonreload/showverified2h30m; expires~06:51UTC). Same8GiBnormalreservation.
Cleanupremoveownr8-diagnostic-holdthenSIGCONT4025360 AFTERfinaltest.

Reproduced real frontendbug: GuestDisplay usedGTKtickcallback forVNCframepump;
widgetunmappedbehindloadingpage=>frames0/no texture. ActualQEMU+bundledGVnc
baseline a62c3489 failsassert; samefixture with GLib.idle_add/framepump+source
cancellation passes8890ed31 HIDDEN_VIEWPORT_FRAME_PASS1. No emulatedguestOS or
hardware-specificworkaround. Enhancedexistingdisplay_smoke checksframebeforemap.
Needcommit/buildthissmallsharedfixandfinalWindowsvisualqualificationbeforepublish.
Mac firstcapturecanprecedefirstpaint5sdespiteGNOMEservice+D-Busready; realQMP
frame after5sfull desktop. Do not claimearlyinactiveframesasvisualPASS.

2026-10-28 06:02UTC DEFINITIVE Windows cause: serial login and actual journal
prove try-luma-seed.service Result=timeout; started05:33:26,killed05:33:55 SIGTERM.
sshd/gdm Requires=seed then fail dependency, so waiting1800s cannot recover.
RuntimeONLYdiagnostic /run serviceoverride300s +restartseed succeeded28.529s,
Result=success/ActiveState=active, followedexplicitsshd/gdmstart. R11 now marked
DIAGNOSTIC-ONLY.json and must NEVER qualify originalimage, eveniflaterPASS.
Evidence windows/prepared-074-r11/{seed-service-journal.log,
seed-service-timeout-recovery.log,guest-bootstrap-diagnosis.log}. Serialbackend
changedviaQMP toWindowsloopback22391 fordiagnosticlogin (localpasswordworks).

Sourceeef986826 adds300sseedallowance,120sboundedudevlabelwait, bootstrap
journal+consoleoutput.133unitPASSdd628e39; publicsource69bb8daa399980e7a1bdf21a4d20a6d5e905e6ab
PR87 all4CIpass. Sourceframefixe330d982d builtLinux51 acb5938e,
Windows21 0c18b16c, Mac23 3198815b/sign12 b35201de ALLPASS; actualLinuxUI
7d95c475PASS, MacsignedUI/HVF/TCG/notary+quarantine/fullDMGSHAallPASS.
Draftstageupdatedassets/source 2a2731b2 running; archivepinnedprivateeef with
exactapp/platforme330 andupdatedimageexporter, notnewerpublicplatform.

Avoidedneedlessfullimageconversion: initialrootexport-r2 jobs d4cd4658 label
underscorefailedbeforestart; retryc9c03307 andARM2dc7bfa3 startedbutexplicitly
stopped at06:00 beforemount, retainedpartialraws. Fastercorrectedimageclones
areRUNNING x86normal6b3fb110, ARMf7294649; privatehelper
/srv/dev/accounts/second/artifacts/try-seed-image-patch-r3.py usesXFSreflinkclone
ofunchangedpublicr1, privateNBDmount, changesONLYseedunit/bootstrap tosourceeef,
preservesSELinuxlabels, unmount/disconnect/check/SHA, outputexport-r3.
Loadednbdmodule (4devices requested, actualmax_part31), previouslynotloaded.
OwnNBD0(x86)/NBD1(ARM) protectedwith/run/lock/try-luma-prepared-nbdN.lock,
checkedunownedbeforeattach; cleanupmustdisconnectifjobfailure. No source disk
oruserVMmodified. Rootjobs768MiB/900s normal1GiBeach. NeedfreshR3Windows+
Linux/Macboot andnewimmutablepublickeys/channelgeneration BEFOREreleasepublish.
Currentpublisher-r9obsoletepointsfailedR9; DO NOT RUN. Release074stillDRAFT.
OuterWindowsfixturelifetimestill9000s until~06:51, pausedcontroller4025360
cleanuprequired afterWindowschecks. OriginalWindowsVM/diskuntouched.

06:24UTC: Corrected standalone image pair export-r3 complete/checks+fullSHA:
ARM5489623040B SHA346ad5a79a0763b8a4b0b2708f972d2eec7685f6b0d63e487be0e7579f1c644b
(finalizer45a2ca58 PASS); x867549550592B SHA61ca6ea5de84051f1690cebecc99b268b598af1c2d45dfa9155cca8284f94640
(finalizer300cbb0e PASS). Earlierx86patchfailedonlypost-disconnectlockrace;
earlierfinalizersfailedonlyrootgitdubiousownershipafterchecks/hash. No image
corruption; explicitfinalizerspassed, originalr1imagesunchanged. TemporaryNBD
moduleunloadedafterall4devicespidabsent/refcnt0; nohostdeviceleftattached.

Fresh MaccorrectedARM 2boots40s/38.6s (includesverification), persistentmarker,
seed300sactive+success, cleanstopPASS d1fbdc6e. Proofsretrievedprepared-images-074/
qa-mac-seed-r1; actualdesktop1 inspectedfullwallpaper+dock
try-mac-corrected-image-desktop.png. LatestSigned12actualpackageused.
Fresh Linuxcorrectedx86 88e99f29 PASS,364s includingHDDverification+GNOMEstart,
seedactive; actualdesktop inspectedfullwallpaper+dock
try-linux-corrected-image-desktop.png. Mac/Linuxnewimagequalificationcomplete.

Windowsouterrebootedafterservicing (observedfirmware→PleaseWait→SSHreturns).
R12 4ad114be failedBEFOREstaging dueouterSSHbanner; R13 23062f93 failedouter
ver30stimeout, noinnerguestlaunched. R14 24a22b0d ACTIVE, root
luma-arm-desktop-try-seed-windows-r14-20261028T061431.service,
outwindows/prepared-074-r14, freshC:\TryLumaQA-Ready074-r14. Exactcandidate21ZIP
SHAverified, all4632filesextracted; correctedx86downloadactual4.42GB/7.55GB at
06:23. Nativeqa try-seed-native-qa-r12.py tests2separateQEMUstarts,2048MiBguest,
actualstockcacheverification+seed300success+autologin+persistentmarker+shutdown,
8svisualsettle. No runtimeoverrides. NeedbothPASSandactualvisualreview.
Outerfixturecapnow14400s viaownruntimeoverride,expires~08:21UTC; same8GiB
reservation, pausedcontroller4025360cleanupstillREQUIREDwhenfinished.

Correctedimagepublication8a4d182c active, immutable20261028-ready2 keys.
ARMFULLpublicreadbackPASS receiptpublication-r2/aarch64-PUBLIC-PASS.json;
x86upload111/225parts at06:21. Do notpublishchannelgen5 untilWindowsproof.
Allfinalappassets+source/checksumsDRAFTverified2a2731b2, Linux51 Windows21 Mac12.
Publishertry-seed-final-publish-r1.py PREPARED NOTRUN; requiresR14PASS+visual,
newpublicimagereceiptsandgen5channel/maincommit. Oldpublishersobsolete.

2026-10-28 13:34 UTC audited update: final Mac signed13/package24 PASS
4fea36c8 (06:56:38), source37db08239, notarized/stapled/quarantine verified.
Linux52 actual packaged UI PASS90324526; 136 component tests d973d08f PASS.
Corrected ready2 ARM/x86 images public with full readback; channelgen5 live.
Windows R15 db008f4f FAILED07:01:49: reached GRAPHICAL_AUTOLOGIN_READY
iteration0 after517.6s, then a separate guest evidence SSH command timed out90s.
No two-boot/persistence/shutdown PASS; no release publication. This falsifies
the suspicion that the initial controls connect was stuck in this run.
Own outer Windows fixture expired its bounded unit; inactive/22257 absent
at13:32. Restarted only owned runtime-final-r15 overlay with bounded R16
controller (no old R8 QA), normal9ba5b17b, max4200s, memory7GiB. Controller
holds3600s after SSH readiness for focused native timeout diagnosis.
No speculative AF_UNIX source changes made. Final candidate22 ZIP binding
and native visual/persistence/shutdown acceptance remain required.

13:53 UTC: R16 outer Windows screen independently inspected: first30%updates,
then restart spinner; outer SSH ver subsequently succeeded Windows26200.9711.
R16 native qualification now RUNNING ee7a04b6; root unit
luma-arm-desktop-try-windows-native-r16-20261028T135121.service.
Package21→22 ZIP diff13ac2498 PASS: exact same4632members, onlyexe/core/policy
changed. R16 deploys delta and verifies SHA ofall3 before native two-boot QA;
retrieves binding.jsonwithfinalZIPsha80eb519294d1eb993e637b3eefc0633e70d27ffbc665490497b755adc232ac0c.
QA savesdesktopbeforeevidence and gives evidenceSSH300s (prior90sfailed);
this is lab deadline only, no product change. Native/image/source still37db.
Finaldraft restage989504ef PASS13:51:09: Linux52/Win22/Mac13 plus exact37db
source archive/checksums, allGitHubassetdigests verified. Stilldraft.
Publisher-r2 prepared/notrun: gatesactualR16PASS+binding+visualreview,
Macsign13/LinuxUI52+nativeimages, currentpublicsourcef9477...,136tests.

2026-10-28 14:29 UTC: Windows timeout isolated to native OpenSSH anonymous
PIPE handles: s.ssh(['id']) stalls even with -vvv and plain command/absolute
exe/no-window/close_fds=False; identical call with temporary output files exits0.
Native probes in windows/ssh-diagnose-r17/{probe,pipes18,fixed19b}.log.
R16 ee7a04b6 FAILED14:01:49 aftergraphical login; markedDIAGNOSTIC-ONLY;
no release acceptance. R17 diagnostic f4058df6 stoppeditsguest cleanly.

Source a5129def7 + binary-mode correction d0a5fe9e7 centralizes Windows
noninteractive SSH output through private files. All internal capture users
(readiness, experience/device updates, guest commands, CLI) use helper;
POSIX delegates standard subprocess.run. Windows stdin uses batch SFTP to a
0600 mktemp guest file, opens that file as remote stdin, removes it afterward;
shared total deadline and cleanup. Native original failing evidence now~0.6s,
256KiB binary upload SHA verified1.92s (fixed19b.log latest). Native large binary
stdout echo stress still timed out; do not claim arbitrary binary SSH stdout
qualified. Normal control/CLI output and binary uploads are the release scope.
144 component tests PASS0ce5832a; publicsource e75c032062d6bedb6c575cce8bbe28c003aa8d7b
PR87 all4CI PASS and mergeable. Mac/Linux remain exact validated37db packages.

Build23 failedonly root Git dubiousownership afterassembly; no source failure.
Scoped process-envsafe.directory build24 c8b3f756 PASS butpredatesbinaryfix.
Final Windows25 source d0a5fe9e7 build4dff9147 PASS14:25:38.
R21 221fd0b7 RUNNING, root luma-arm-desktop-try-windows-native-r21-20261028T142619.service:
21→25 same-member ZIP diff/fullmemberSHAbinding deployed; native two boots,
seed300success, persistentmarker, cleanstop, CLI capturedid. Correctedimage
cache verifiedstockR14 reused. R20 diagnosticguest explicitlyquit; owned
ssh-diagnose20.py stoppedbefore newQA topreventitsfinallytouchingnewguest.
OldR16 screenshot archivedonlyifmtimebeforeR21start; avoidsdestinationcollision.

Draftrestage r12 1ba2347b running: Linux52/Mac13 retained, Windows25 updated.
Corresponding source rootd0a plus nested PINNED-LINUX-MACOS-SOURCE.tar.gz from
37db; manifestrecords exactperplatformtrees. No unnecessaryMac/Linuxrebuild.
Publisher-r3 PREPARED NOTRUN: gatesR21PASS+exact25ZIPbinding+actualvisualreview,
lateststageddigests,144tests+CI, oldnativeMac/Linux+signedMac13proofs.
OuterR16 privatefixture stillbounded4200s from13:32:56; controllertime.sleep
ends~14:43:58. Finishqualificationbeforethen or explicitlyextendownbounded
fixturewithoutdiscardingtest. Apprelease074stillDRAFT;0.7.3untouched.

14:35UTC: R21 failedonlyQA leftover screenshotfilename collision afteractual
graphicalautologin120.9s; no sourcefailure. Replacedreusedoutputnameswith
newprivateqa-proof-r22 directory. R22 758a5ea7 active root
luma-arm-desktop-try-windows-native-r22-20261028T143022.service. BothBOOT0
andBOOT1 PASS files observed onWindows, savedmarkerretained; actualpackaged
CLIcaptured id passedbothboots. Firstscreenshotdesktop0 retrievedandinspected:
fullLuma wallpaper+dock (try-windows-r22-guest0.png). Finalshutdown+collection
pending. Finaldraftrestage1ba2347b PASS14:28:16,source+5assetdigestsverified.
Publisher-r4 prepared/notrun gated R22complete+actualsecondvisualreview.
All3packagedchannels verifiedgen5 .qcow2 pair (WindowsZIP25, Linuxactual
AppImageQAextraction52, Mac13signedsource37db receipt). NormalStartselects
preparedworkspaces anddownload/verifies/bootsautomatic; public0.7.3stilloldflow.

2026-10-28 14:38 UTC RELEASE COMPLETE: R22 native Windows758a5ea7 PASS
14:35:36, boots127.46s/122.67s on nestedlab, graphicalautologin/persistentmarker/
packagedCLI/actualcleanshutdown bothpass. Exactcandidate25ZIP binding
80b7f1c805a2b8549cb8111683d961003f565f68129c3cc1674faf03c12e3813;
actualdesktop0+desktop1 inspectedfullwallpaper+dock, visualreceiptwritten.
Publicpublisher e2ea2e61 PASS14:36:45. PR87 merged c315aa9033ff331313ebd91423567825534df71b
frompublic e75c032062d6bedb6c575cce8bbe28c003aa8d7b. Releasev0.7.4 PUBLIC
(preview) https://github.com/SimplyLuma/try/releases/tag/v0.7.4; everypublic
app/source/SHA256SUMS FULLdownloadsize+SHAverified. Receipt release-final-074/
PUBLIC-DOWNLOADS-VERIFIED.json; previous0.7.3assetsunchanged.
TryrepoREADME publicmainc477b0e linksall3newpackages,preparedVMs,credentials,
channelgen5; appupdatesstillneed separateHub signedindexregistration asdocumented.
OwnedouterWindows fixture gracefullyshutdown viaSSH(return0),22257listener
absentbeforeexplicitstopofOWNR16rootunit. OriginalWindowsVM/diskuntouched.
NoMacuserrunningapp/VMmodified. All3newpackagechannelsgen5 .qcow2 pair verified.
Userwebsitehandoff: explicitversionedv0.7.4URLs; hostlabelsMacAppleSilicon,
Windowsx86-64,Linuxx86-64. Bothguestarches inautomaticpreparedimagechannel.

2026-10-28 owner-requested Mac space cleanup complete. Stale22442listener
sshd-session3565719 verifiedownedaccountandONLY22442forward; terminatedafter
banner timeout so ownercouldreconnect. Macinternalfree117,714,944B initially.
Removedonlyinactiveownbuildoutputs r17/r18,obsolete signingr6/r7 bundlesand
archives,oldownfirst-start074r1 ISOcache,pipcache,and3Oct21–23CodexSparkle
installationcaches. PreservednewestOct24Sparklepackage/activeLauncher,
allDownloads/projects,installedTryLuma,liveDMGbackings,anduserVMworkspaces.
NoQEMU/buildwasactive. Actualfreegain9,103,712,256B; finalavailable~9.22GB
(8.6GiB). Remote receipt /Users/nick/Library/Application Support/Luma/Development/
mac-space-cleanup-20261028.json; installedapp/download/sourcefoldersverifiedpresent.

2026-10-28 bounded Mac window-drag performance investigation (owner asked
quicklook, avoidbroadwork): read-onlyactualM4 session25144 is ARM aarch64
with -accel hvf/-cpu host,4vCPU4096MiB,1920x1080 virtio-gpu-pci andprivate
VNC. Currentmountedpublicapp /Volumes/Try Luma 2; existingISO-deriveduser
workspace retained. graphics.json mode software; actualbundledQEMUdevicehelp
hasONLYvirtio-gpu-device/pci (noGL/rutabaga), displayhelpnone/curses/cocoa/dbus
(noegl-headless). GuestGNOMEjournal explicitlyEGLfailedDRI2/fallbackkms_swrast.
Hostmemory_pressure reports60%free; guest~844MiBavailable/18MiBswapused.
Noresource/machineconfigurationchanges, injections, restarts, orreleaseedits.
DiagnosticglxinfocouldnotopenXdisplay; do notclaimGLXrenderermeasured.
Viewer source also copiesfull8.3MB1080p framebuffer perpaint; notprofiled as
primarybottleneckandnotmodifiedspeculatively. Immediateworkaroundlowerguest
resolution; actualfixneeds GPU-capableMacQEMU/renderbackendqualification,
notmerelyvirglsettingunsupportedbybundledruntime. OfficialQEMUvirtio-GPU
docsconfirmdefault2Drequiressoftware3Drenderer. No performancefixclaimed.

2026-10-28 urgent Mac GPU + concurrent sidebar correction:
Sidebar agent commit d3f9242b5 integrated cf15baaea. Shared managed-preview
classifier now includes completed legacy installer workspaces, both Try/Developer
show luma/TryLuma with unlock/install guidance; one-time defaults preserve later
custom passwords. 147tests + native Mac/Linux UI passed; Mac receipt
/srv/dev/accounts/second/artifacts/try-login-mac-ui-r1/PASS.json.
Mac GPU prototype uses pinned QEMU fb806cdfca39542e59b2eae58c6fd6662afd3787
from verified23MB bottle, UTM5.0.8 Metal ANGLE/virgl/Vulkan libraries; full input
checksums and exact source pins in remote try-gpu-075-r1/relocated-r1/runtime.json.
Original tap ANGLE crashed in AppleGL; its virgl emitted unsupported GLSL310.
Qualified prototype combination r7 booted HVF ARM, guest glxinfo explicitly
Accelerated:yes/Device:virgl/ES3.0, VNC frames219173colors, QMPquit exit0.
QMPscreendump reports no surface forGL; existing VNC capture path works.
Remote QA /Volumes/A016/Try-Luma-GPU-075-r1/angle-boot-r7; no userVM mutation.
Relocated runtime private resource prefix probe+VNC passed withoutDYLD overrides.
Native app QA caught probe's omitted pflash drive references (silent software
fallback); fixed stripping pflash0/1 from diskless machine and retaining-L;
regression test added. Agent nativeUI rerun pending. Candidate isolated app:
/Volumes/A016/Luma-Try-GPU-UI-Candidate-20261028-r1/Try Luma GPU QA.app.
Release0.7.4 remains unchanged; corrective0.7.5 source integration in progress.


2026-10-28 native Mac GPU correction and final sidebar integration:
Metal/GLES prototype failed visual qualification (missing dock textures), despite
functional input passing. Replaced with isolated patched QEMU native CGL desktop
OpenGL 4.1 backend; exact source pin/build recipe/patch in scripts/tester/patches.
No global Homebrew changes. Actual prepared ARM guest reports Accelerated:yes,
virgl(Apple M3 Pro), GL4.1; correct colorful dock, raw VNC proof and clean QMPquit.
Native app QA7101f8b9 rendered default GTK test window, moved it108x54 guestpixels
via actual viewer input, native clipboard and both sidebar modes passed. Candidate
left running privately for owner test; original user workspace not modified.
Owner now reports dock animation without app opening, while Settings opens from
right-click desktop menu. Dock-launch path investigation remains OPEN; do not
publish GPU release until diagnosed. No performance benchmark claimed.
Latest sidebar spacing agent8e1452b0d integrated471ffb518; shared inline TextButton
removes old copy-icon gap and divider normalizes section styling. Native measured
Preview login/Presets section gaps12px and same text right edge216px. QA app's
wrong icon lookup corrected; actual approved ICNS artwork unchanged.
149 component tests PASS4d416701 including AppleGL/GLES backend commands, explicit
software fallback, x86 scope and headless selection. Exact pinned QEMU patch applies
cleanly (dry-run). Corresponding source/build artifacts retained privately.
Public0.7.4 unchanged;0.7.5 draft held for latest final packages/qualification.

2026-10-28 owner manual test caught real app launch failure after native GPUQA:
actual guest Flatpak journal reports config-only user OSTree repo missing objects;
all25 system Flatpak refs/apps remain valid. Context-menu system Settings opens.
Narrow managed-preview repair initializes only config-only, non-symlink user repo
using its existing bare-user mode, outside once-only markers; existing data-bearing
repos remain untouched.151 component tests PASSfeda4878 includes real OSTree
init/config-preservation/refs/idempotence and preservation of existing app data.
Agent verifying actual dock+launchpad activation after repair in own MacQA.
Source patch regenerated from actually built QEMU files for exact correspondence
(includes harmless duplicate system.h include present in qualified binary build).

2026-10-28 16:48 UTC: Published Try Luma0.7.5 stable-renderer preview per owner:
https://github.com/SimplyLuma/try/releases/tag/v0.7.5 . Exactall3appsource71ea4613d;
publicsourcePR89mergedaab65580,4CIchecksPASS. Linuxc1403b80/018e355d,
Windowse0709676/b765704e passedpackage/nativeLinuxUI/ZIPsource+runtimebinding.
No newWindowsOSboot;2450native-qualified0.7.4runtimefilesunchanged.
StockMacbuildbf713a3e/finalize98267778 passedDeveloperIDdeepverify,nativeUI,
HVF/TCGcontrol,Appleapp+DMGAccepted,staple,quarantineGatekeeper,fullreceivedSHA.
StockMacResources/mac-gpuABSENT;GPUprototypeexplicitlyheldbackafteruserreports
severelag. Publish526cc760 PASS: all5publicdownloadsfullsize/SHAverified,
0.7.4assetsunchanged. Evidence bulk/release-final-075-r2/PUBLIC-DOWNLOADS-VERIFIED.json.
MacSHA39cb08f4dc5db48c1231467b5095eecbf9cc9a3f133c176095d27511fc665c94;
Linuxbb550619fad1cae8f4e496b7133624c115ca169405e1706522edf07d7ce91b05;
Windowsf9c18bbe4634e468d8a7e4a2230932b42f58c19da7c0810524872d93587590f5.
Releasehomemaincdf5b2fupdatesdownloadsanddocs/try-0.7.5-website-handoff.md.
Hub signedreleaseindexupdate remainswebsite/Hubagenthandoff;GitHubpublication
alone doesnotenablein-appupdateoffers. PreparedVMgeneration5unchanged.
OwnerexplicitlyrequestedhandoffnowthencontinuedMacGPUwork;performbounded
actualcurrent-sessiondiagnostics, donotclaimCGLperformancequalified.

2026-10-28 17:00 UTC Mac follow-up: read-only observef279fc01 confirmed manual
Hardware selection correctly uses HVF + virtio-gpu-gl + apple-opengl, no CPU
emulation fallback. Guest availableRAM1138MiB, idleload0.08; not an established
resource saturation issue. Log contains GLSL130blitter errors. Rebuilt pinned
UTM VirGL with Apple-only GLSL150blitter correction (private venvPyYAML6.0.3,
no global tools changed), builda5d13743/staged052f743PASS. New isolatedprefix
relocated-cgl-r2 actualpreparedARMbootb9e05bcbPASS: Acceleratedyes GL4.1,
correctdock screenshottry-cgl-desktop-r3.png inspected, shader130errorsabsent,
QMPquit0. Performance notqualified and public0.7.5unchanged.
User requested standard baseline; read officialUTM Linux/macOSgraphicsdocs,
confirmed AppleCoreOpenGL4.1 native+SPICE IOSurfacepath documented. Standard
stockQEMU/HVF/Cocoa2Dbaseline6ecb2e58bootedownprivateoverlay andnativewindow,
guestllvmpipesoftwareasexpected; nativevslaunchercomparisonongoing.
Useroptionalmanualquestionpending; no opinionassumed. Originaluserdiskunchanged.

2026-10-28 Mac viewer bottleneck reproduced: owner reports native Cocoa baseline
vastly smoother. Native baseline app launch issue repaired separately (plainQEMU
bypassedlauncherbootstrap). Actual warmGTK/VNC animationA/B3a4824c5: idle path
10120fullbuffercopies/10.28s,70.6%onecore;16mscoalescing316/10s,35.1%.
Implementeddisplay-clockcoalescingwhenmapped plus16msfallbackwhenhidden,
transferringpendingcallbacksacrossmap/unmap andcancelingbothonclose.
RealLinuxQEMU/GVnc/GTKframe+pixelparity+input+hidden/mapped/hiddenregression
PASSf4a412cc againstnewsource throughactualportablelibraries. InitialhostPython
attemptf8300ca6 lackedGVnctypelib; correctedusingqualifiedbundledruntime.
NativeMacactualnewsourcehashboundr5 measured idle16281copies/10.48s95.5%core
versusframeclock578/10s50.6%core,correctedVirGLacceleratedGL4.1. Evidence
/srv/dev/accounts/second/artifacts/try-mac-frame-clock-r5-PASS.json. Enclosing
harness4c3c6a15 failedaftermeasurements dueexecnamespaceoverwritingpathvariable;
independentfollowupverifiedmeasurementfile, exactdisplay.pySHA andownedQEMU
88724stopped. Do notlabelthatwholeharnessPASS; measurementandcleanupareverified.
Public0.7.5unchanged; preparingprivateintegratedMacusercandidatebeforeGPUrelease.
