# Task: android-first-class

- Status: active
- Owner: Codex, luma-dev-2; Android first-class implementation chat
- Updated: 2026-10-29T07:27:00+00:00
- Project: luma-os
- Branch and last useful commit: codex/android-first-class; 59aecadc4 (correct native HIDL admission/reconnect and .91 package)
- Worktree: /srv/dev/worktrees/luma-os/android-first-class
- Scope and expected result: implement the researched Android subsystem, native install/app identity/window and host-service integration, both CPU architectures and measured qualification. User explicitly authorized downloads, edits and testing without questions. No unsupported performance or hardware claims.
- Files / interfaces owned by this task: src/luma-android, Android integration patches/build profiles and tests; coordinate any shared installer/platform changes by inspecting existing owners.
- Related tasks and integration owner: this chat owns Android implementation; preserve active mobile/ARM/device/release tasks and device gates.

## Current handoff

Source/worktree isolated; canonical untouched. HEAD59aecadc4. Current milestone
adds APKr12 native-HIDL controls and bounded native clipboard transfer0016
(worker/cache/header, both builders/state hashes and real pipe/callback tests).
**DO NOT BUILD SNAPSHOT901d5f18c OR luma-project-aidl.squashfs**: wrong backend.

CURRENT ACTIVE BUILD: normal dev-android-images-build-vm-r6-ae325a4f.service,
10GiB/450GiB, root luma-android-first-class-build-controller.service, VM UUID
809f0871-fd88-4770-83dd-624c90259c06/IP192.168.122.36. Started06:28Z, input
hashes verified06:33Z, guest startup06:33:26Z. Correct read-only project disk
luma-project-native.squashfs70,553,600B SHA
e6167a6ae3cebe0dc8e5ea71288b8edc2e8ee7f426bf1552324fe8496e33db92,
source59aecadc4. Fresh upper-native-v1/work-native-v1 preserve older attempts.
At07:03Z still maintained repo Git/LFS integrity preflight, git reading SquashFS,
~6.26GiB guest available/1MiB swap, build log0bytes. Not compilation or pass.
Current snapshot does NOT contain in-progress clipboard transfer0016. Stop root
unit first for deliberate cancellation; owner watchdog also protects job death.

.91 clean RPM r14/cb8f4f4a PASSED84 actual tests, zero skips/failures/errors,
profile directories755. Binary356b704556fadc48de60570cc784522349e594d31152ec5ec5ec41ea01bf9c76,
SRPM72ca08e914f8d16da4f9f28de3d14143f5a5639916b9a10263b8c5566f7011dd,
rpm-r10/source/build/candidate-91/build/packages/luma-android-runtime. Not yet
installed/live-tested. HIDL SDK283a9cc5/native policyf03ebfd2 passed corrected
fixtures. APKr12 b142754e built; native HIDL reflection controls not run live.

New transfer helper test e98dfcab PASSED real >pipe-capacity UTF-8 self roundtrip,
partial I/O, timeout/cancellation, oversize/invalid UTF-8, EPIPE, bounded writes,
shutdown/FD ownership and cache generations. Helper concurrent submission/shutdown stress now passes ASan/UBSan and TSan
(0d019389; separate asan.txt/tsan.txt preserve completion receipts). Initial
612c2875 could not link GCC libasan, which is absent; Clang runtime is installed.
A59191a4 command failed because systemd expanded a shell-array expression; private
script corrected it. Complete production callbacks with explicit Wayland/HIDL
fixtures pass91d70a0f ASan/UBSan and f4415ac5 TSan. These exercise real asynchronous
pipes, denied callers, rapid immutable copies, retired queued callbacks and late
HAL calls after joined teardown. Not actual Wayland/HwBinder IPC qualification.
Full upstream patch series264ac866 passes through0016, exact header identity and
configure-lock/linkage controls. Both builders hash/apply0016. Old raw listener
payload eviction would race dispatch; final source uses existing generation-token
registry, admits strong snapshots before retirement and bounds sources to8 while
later copies continue. Queued stale sends close FDs; stale cancels touch no proxy.
APK12 hashbf7e2aa3496aae6999f39f87b73e8617f6786423de1fddb009c313a6e4bc38b9;
not installed/live-qualified. Native HIDL1MiB transaction-limit/large text, focus,
rich MIME and actual image paths remain gates.

Critical correction: actual installed framework DEX and hardware/waydroid source
show Clipboard uses vendor.waydroid.clipboard@1.0 HIDL in native HWComposer,
not the host's legacy AIDL endpoint. sdkobserve2 captured/disassembled only the
public framework jar (actual /system/framework/org.lineageos.platform.jar), then
ran pinned dexdump asUID1003 under a256MiB capped root diagnostic within the6GiB
VM reservation. Original93dd6267 used wrong system_ext path and failed. Exact
proof sdk-image-observe/clipboard-class.txt. Legacy AIDL UID tests remain valid
ONLY for that unused endpoint; they do not qualify native clipboard admission.

Source .91 now preserves HIDL, disables the unused Python/AIDL clipboard manager,
removes its pyclip/wl-clipboard dependencies, adds native HwBinder UID1000 caller
admission before cached-data read/mutation, and reconnects SDK failed proxies.
Both builders/state hashes track native patch0015 and replacement SDK0004.
Native HIDL generated stubs assert successful Return and exactly one getter
callback. Initial94e78f80 fake-transport test did not model that contract; corrected
before any image build: denied read calls back empty once, denied write is no-op.
Updated32-case actual-method testda69c3a5 PASSED, unpatched negative control42.
HIDL production SDK wrapper430cbf55 PASSED failure/reconnection/race fixture tests.
Neither is real HwBinder proof. Root package .91/84-case check subsequently PASSED (current handoff above).

Native WaydroidClipboard.cpp read_selection blocks Wayland event loop with an
unbounded pipe read, and clipboard cache access crosses threads without a mutex.
Next substantive work: bounded asynchronous native transfers + synchronized
cache/focus ownership, with real stalled/partial/oversize pipe tests. Then source
APK probe must exercise actual HIDL get/set with known nonempty trusted controls.
Do not mistake ordinary-empty for denial when the trusted control is also empty.
Keep all arbitrary clipboard contents out of logs; synthetic token classes only.

Historical06:18Z state (superseded by active r6 build above): ALL owned VMs and image jobs were OFF. Stopped r5/09340d25 root controller and
admission before native-path correction. No completed replacement image exists.
Own Luma VM original6GiB/8GiB config restored inactive05:54:45Z from intact
vm/domain.xml; actual restored XML SHA
b3f5031d32bf43a5ed2c0241f1e4dec6a1369be53ea25f36ed8ddbba82c37fe5.
EMPTY domain-before-lowram-20261029T020943.xml remains unusable. Next full-RAM
runtime lifetime needs normal9GiB reservation; do not run it under old6GiB budget.
Its .90 usr overlay is gone on reboot; APKr11 remains installed on its disk.

Build VM UUID809f0871-fd88-4770-83dd-624c90259c06 remains defined/off,7GiB RAM,
9GiB hard/4vCPU/24GiB guest swap/280GiB root disk. Private SSH key only
/root/.ssh/luma-android-builder-20261029/ssh-key. Root controller private path
build-vm-driver/controller.py and run.sh now watch admitted parent PID/start/UID,
cgroup/memory.max and have EXIT cleanup + own-UUID ExecStopPost. No limits/reserves
changed. Synthetic forced death/normal stop tests2dbfcc8d/4f675d66 stopped root
actor within1s; normal test exercised watchdog, primary trap alone not proved.

Prior build attempts preserved, not image passes. r3 did create a startup marker
and entered the builder; old log must establish how far it got. r4/763bed83 stopped
on that marker and cleaned own VM. r5 uses explicit prior-boot resume: archives
started/result/log files under/root/luma-build-attempts only if timestamp predates
current boot. Before next build, pack a new corrected readonly project disk and
switch away from luma-project-aidl; retain all old inputs/logs. Consider a fresh
named overlay for this source correction, preserving old upper/work; original
compressed source is unchanged and validated. Never weaken Git/LFS/HEAD gates.

Android source SFS57,095,458,816B SHA
48f1c8c31596993fc4ec10e97ab17c571d3eb9e1d32b06a709d9f4be8a76f7be.
Rootfs/tools/original project disks retained. Compiler guest chroot UID1003,
no-new-privileges, private network namespace. Build root actor128MiB/6h and
QEMU9GiB hard fit normal10GiB/450GiB HDD admission; no host credentials enter VM.

Latest installed test package .90 (now reboot-cleared) passed83 actual package
checks/zero skips/failures, directory755/import/signature checks. Binary
 db6e7408e591d25aa83713eaccf625a38d69052a652618fd9021894d3c570754,
SRPMdaa21b8aa950cff398ecc063e0907d879ff542f5979ec98718d3c0da85bce38c.
Actual .90 root-hardware/user-monitor and LEGACY clipboard trusted/app controls
passed; r10 APK UID10131 denied at all3 tested AIDL endpoints. Rootwaydroid_t,
FedoraEnforcing; AndroidDisabled, old platform/property still allowed. Volatile
fixture property cleared. No native-HIDL ordinary-APK admission pass yet.

Real native clipboard plain-text round trip r10 passed. r11 APK
SHA862bf4e6e0d931c7c8e714c34eb3060c4d5410423fd8f442c1a85b9447c090eb
was built17ec82d0, installed/launched normally. Native GTK focus control recorded
actual activation/boottime. Initial native-app focus control opened too late;
fixed-token background match might have been legitimately cached. Strong round:
normal Android copy, confirmed native focus, fresh host token published after
that, then delayed APK read returned unexpected-content, NOT fresh target, while
Android hasWindowFocus remained true. vm/clipboard-focus-r11-result.json records
neither privacy pass nor confirmed fresh disclosure. Native control stopped by
its timer. Personal profiles never used; source/evidence only synthetic.

Actual sampler:16 samples/30.001s, observed Android PSS204.95..205.25MiB including
fixture20.396MiB; cgroup memory497.88..498.42MiB;175,266CPUus,0.584% of one core,
zero unreadable/changed or frozen samples. vm/observed-r90-20261029T0525.jsonl.
Not physical battery, first-frame/GPU-memory completeness or comparative proof.

Open gates: maintained AOSP rebase (current Android13/security patch2026-02-01),
full-image native resize/dismissal/platform/property denial, actual clipboard
HAL/IO/focus, MAC/usernamespace/origin attestation/private platform keys, ARM
native/foreign ABIs, portals/hardware/media/reply and physical suspend/push/power.
FP6 hardware gate/other agents/devices untouched. Preserve20GiB disk and8GiB host
memory reserves. All bulk/private evidence stays task HDD, not Git. No release,
push, PR, deployment or human sign-off. Research recommendation remains under
/srv/dev/accounts/second/artifacts/android-first-class-research-20261028/RECOMMENDATION.md.

## Validation and artifacts

Exact commands, observed results, output paths and untested parts. Never turn
an intended test into a passed result. Keep long logs in the artifact directory.

## Dated history

- UTC timestamp — concrete change or finding, outcome, and relevant commit/unit.

2026-10-28 baseline: dev-android-baseline-db8fb1f2 ran60 tests, one pre-existing nonroot sysfs-fixture ownership failure and16 root-only skips. No baseline pass claimed. Preparing an independent flattened copy of an inactive synthetic Luma gate VM; original disks and domains remain unchanged. Bulk storage bind established at /srv/dev/accounts/second/experiments/android-first-class -> /srv/dev-storage/projects/luma-os/build/android-first-class, to account actual HDD usage through normal admission. VM preparation291473c6 active; no VM started yet.


2026-10-28 implementation evidence (source candidate, not release):
- `dev-android-component-tests-r2-5a91c9fa`: 71 tests, success, 16 root-only skips.
- `dev-android-runtime-unit-r3-5bfef57c`: 73 repository Android tests passed with src/luma-platform/appkit on PYTHONPATH. Earlier two invocations lacked that dependency; their failures are retained.
- `dev-android-notification-dbus-r2-603d425a`: real private D-Bus lifecycle checks passed, Binder listener fixture. First run caught and fixed a signed CloseNotification argument.
- `dev-android-fixture-build-r2-a882f291`: actual APK compile/signature verification passed. First build exposed missing Java lambda bootstrap stubs; --release 8 compilation plus D8 fixed it. Tools are checked against the committed lock, not a downloaded manifest.
- `dev-android-caller-policy-6bd5f259`: compiled production Java admission helper passed 100 UID combinations. This is not a framework/APK permission test.
- `dev-android-tools-fetch-verify-1bea9815`: maintained downloader fetched and verified all three official pinned tool artifacts.
- Disposable domain luma-android-first-class-x86 has its own flattened HDD disk, 6 GiB RAM/8 GiB hard ceiling, two vCPUs and accelerated virtio graphics. Original source domain/disks are untouched. The synthetic account was granted its normal installer group only inside this VM. Source APK installed through Luma and launched in a native Luma window; one warm command took 2.945 seconds. This is command latency, not first-frame timing or a statistical benchmark.
- Actual mouse resize froze the baseline HWC. Android debuggerd captured a cycle: xdg_toplevel_handle_configure holds chromeMutex and enters HWC2On1Adapter::hwc1Invalidate, while adapter present holds its lock and enters prairie::frame waiting for chromeMutex. Patch 0014 releases window locks before adapter invalidation, including display/legacy paths.
- `dev-android-windowing-lock-regression-575ae0d9`: exact production callback bodies compiled and passed the three-path lock test; removing the unlock reproduces the deadlock with exit42. Full pinned HWC patch series/header/license checks and actual two-translation-unit cancellation linkage passed, with broken-scope negative control rejected. Full image build and VM resize retest remain open.
- Archived Android source recovery is isolated in task-owned root-private HDD staging. First 1.5 GiB root driver hit its cgroup limit (7166c5f6); preserved failure, retry 53b804ed admitted with 4 GiB total/3.5 GiB root bound. No existing build tree is reset or overwritten.
- Current baseline Android image reports internal SELinux Disabled while Fedora host is Enforcing. This is an unresolved production isolation gate, not qualified sandboxing. ARM execution, reverse ABI translation, document/camera/media brokers, inline reply, suspend/push/battery, accessibility and complete integration remain open.

2026-10-28 native notification and packaging evidence:
- Source commits feb302cf3, 0c6ac15f3 and 765a59e1c preserve the bridge/patches, clean-package fix and source fixture. Runtime RPM .81 was built from clean 0c6ac15f3 by dev-android-runtime-rpm-r2-3fd91682: actual package checks ran 71 tests with zero skips/failures. Binary SHA256 062802d71e88534daad0621b98d400a922cfd50df4cb394523812d7e1abd294e. Package artifacts are in that account job's source/build/packages/luma-android-runtime.
- Installed .81 only in the disposable VM's reboot-cleared ostree development overlay. Existing release/installer protection was not changed. New Luma-owned session transport is running; the Android image remains the old matched system/vendor pair, without the new SDK/HWC patches.
- dev-android-fixture-build-r3-efa332bb built the permission-aware, UID-boundary fixture. APK SHA256 0c32ff3c4af425708ae9b5247e5c5f8d04a774b2c5fc5b46d1f9c032f9508d00; own HDD apk-r3/AndroidIntegrationTest.apk. Source/tool hashes match its build receipt.
- The real APK posted a notification rendered as a native Luma notification-center card. Actual desktop identity was waydroid.org.projectluma.androidtest. Clicking native Mark read delivered action:read to the APK's private events.jsonl and cleared the card. Screenshot evidence is own HDD vm/fixture-r3-window.png and vm/notification-mark-read.png. This qualifies the tested content/action path only; inline text reply, grouping, privacy/channel controls and new dismissal callbacks remain open.
- The ordinary APK boundary probe returned allowed against the old image. This confirms why the new platform caller patch is needed, not a security pass. Its compiled policy helper passes; complete-image denial remains to be tested.
- Found a separate framework freeze path controlled by persist.waydroid.suspend. Default product property and host readiness now follow the existing explicit idle_freeze setting. dev-android-idle-policy-tests-r3-d575e1a7 ran all 74 repository Android tests successfully. First test invocation found a stale expected call ordering; next invocation was refused by the already-held recovery path lock; retained both outcomes, then used the task worktree admission path.
- Applying the supported property in the VM and closing all app windows left open_windows=0 and cgroup.freeze=0. A 30-second no-window sample retained freeze=0 and used 25,304 microseconds of aggregate Android cgroup CPU. This controlled VM observation is not a physical power, push-delivery or Doze qualification. Sample: own HDD vm/idle-policy-background-sample.json.
- Product decision recorded explicitly: Android notifications use Luma styling, preserving Android semantics through structured bridges. Android custom RemoteViews must not execute as host UI.

Active jobs at this update: dev-android-vm-r4-be5a1197 (own VM, automatic finite lifetime) and dev-android-source-recovery-r2-53b804ed (root unit luma-android-first-class-source-recovery-r2-20261028T223745.service). Recovery is running/thawed, capped at 3.5 GiB with low IO weight and 60M/s write limit; files had reached prebuilts/vndk. No complete image rebuild is claimed. Root staging and original retired archive remain private. Next: inspect recovered preparation state, arrange bounded matched-image build, then retest the reproduced resize deadlock and ordinary-APK admission on that image.

2026-10-28T23:23Z follow-up:
- dev-android-runtime-rpm-r3-b4fb3ebf built .82 from clean a7d895b01007bc1915589af71422f0b11bd6454e successfully. Actual package checks: 71 tests, zero skips/failures; peak 2 GiB. Binary SHA256 8500e1b773849cc7e2b1a4d559052a68f8a06a8596c59e0a247aa6d9b6a087ee; SRPM SHA256 973add20b62566da21ce2852674d5295226a65696615e3d6944051e3f69b7ff9. Output is the corresponding account artifact directory's source/build/packages/luma-android-runtime.
- Installed .82 successfully only in the existing disposable VM overlay. The RPM transaction completed with exit 0; final query reports .82 only. Reloaded user units and restarted luma-android-session.service and luma-android.service; both report active. A concurrent query during update briefly showed both versions while update-mime-database was still running; that was not the final package state. No other VM/package/update policies changed.
- Source recovery completed successfully at 23:20:33Z (42m47s, 3.5 GiB peak). dev-android-source-recovery-r2-53b804ed and its root unit are now inactive/success. The recovered out/ contains directory scaffolding without the prior Ninja graph or images; this is a cold build input, not a restored warm build.
- Read-only recovered HEADs: hardware/waydroid 18e73d3882d97d909843925419ef25ea60f6a376 (old Prairie chrome commit on pinned 85f31a9); frameworks/base 79d859d0a657dc234241a56fb49279a0f448fe56; device/waydroid/waydroid 5ff343563d2ea187534c9791993137c61d198c0b; lineage-sdk 631dcfbbd15efcd44c970eb25e3c78f8d44b0283 (upstream Waydroid service patches). Vendor is vendor/extra, not vendor/waydroid. Prepared-state metadata is retained intact. Next build setup must verify the complete recorded snapshot before using the normal reset/preparation path; no reset or build has occurred.
- Only the finite private VM lifetime job remains active. Full image build, resize acceptance and ordinary APK rejection are still open.

2026-10-28 continuation (active; no subsystem completion claimed):
- Builder preparation dev-android-source-builder-prepare-9b3bc3ef passed. Task-private Ubuntu 22.04 rootfs, official base digest 8a2321a92b438238f93b4346fff61bc4259c99fdd4d9b52ee86f153fee0c344f; built image f91addbcbf3c30955bd1439a73b880700e88e2e30d1b2d2b8a7f0c098ca51964. Exact export/containerfile receipt is own HDD source-builder/receipt.json. No build uses host credentials or network. The normal worker's unprivileged Bubblewrap namespace probe passed (6e5d0cdd).
- Recovered Android source custody was explicitly assigned to this task owner only (03cdecc8), preserving the original archive. Found an archived unfinished HWC delta. Preserved it and the original recovered tree, then made an independent XFS reflink candidate (c100bd02); selected its recorded HWC commit only in that fork. Original delta is retained and hashed in source-recovery/candidate-provenance.json. Current maintained HWC already has the corresponding logical-to-pixel resize conversion.
- dev-android-source-build-preflight-1dbcf889 is verifying that candidate's full repo snapshot and file cleanliness inside the offline namespace. It has passed the recorded head-snapshot check and is still hashing copied working files through repo forall. At 23:44Z it had reached prebuilts. No reset or Android compilation has started. Launcher: own HDD source-builder/run.py; source snapshot 79198ee0e; candidate path source-recovery/candidate/lineage-20. Namespace mounts only the build inputs and private scratch.
- dev-android-fixture-build-r4-4ec59e62 passed actual APK compilation/signature verification. APK SHA256 b865d34e96fa2a82e3d4b5ee47a80e1b8afabbbd2f3bc53edcdb263426b1a276. Its local-alarm receiver is now source owned. Installer correctly rejected a root-only copied APK; assigning that private fixture file to the synthetic VM user allowed normal Luma installation. No installer checks were weakened.
- Real no-window background delivery passed on runtime .82/old Android image: fixture scheduled at elapsed5107999ms, fired at5138004ms, posted at5138006ms; host observed waydroid.org.projectluma.androidtest and its actions. Android reported zero open windows and cgroup.freeze=0. Native Luma notification-center screenshot confirms the card with no Android app window. Private receipts: own HDD vm/background-notification-r4.txt and vm/background-notification-r4.png. This is a local alarm test, not network push/suspend/battery qualification.
- A diagnostic Android run-as invocation exited139; its attempted pre-update synthetic event backup was empty. Earlier notification action evidence remains in screenshots, transport logs and this factual record. Subsequent receipt capture uses root cat of only the source fixture's private synthetic events; no user data is involved.
- Root hardware transport audit used official Waydroid 1.6.3 commit5b7e2e71be3f6bfaaaab3b461251dacaf1ce4991. Initial fetch had an incorrect services/container_manager.py path and failed; corrected actions/container_manager.py fetch passed (02fb891f). Source/hash receipt is private upstream/root-bridge/receipt.json.
- Added a process-local root Binder constructor adapter and package-owned systemd drop-in. It admits kernel UIDs0/1000 before upstream parcel read/dispatch, fails closed when credentials are missing, and leaves provider files/global gbinder intact. Four dedicated boundary tests passed (bf4a61c0); component run fb682892 ran75 tests successfully with16 root-only skips. Real Binder admission and candidate RPM checks remain next.
Active jobs: private VM lifetime be5a1197; source preflight1dbcf889. Next: package/runtime Binder acceptance, finish clean-source audit, stop only this VM to fit the admitted cold full-image build, then boot its matched images and retest resize/framework admission/dismissal. All ARM/mobile/power/portal/translation gates remain open.

2026-10-29T00:59Z active handoff:
- Commit083c9a97d records root hardware admission and real no-window notification evidence. RPM .83 built successfully by dev-android-runtime-rpm-r4-74cb90c7: 75 actual tests, zero skips/failures, binary SHA25620962f70ca72722ada6f5f41f95074ebe1fe92376701a806b1301a77b5a58c21. Installed only private VM overlay; later manual .84 source overlays mean current VM is not a clean RPM qualification.
- Caught custom root entrypoint losing the existing provider SELinux transition. Uncommitted .84 adds label-only CIL for the helper using existing waydroid_exec_t, no allow rules, and refuses startup outside waydroid_t on SELinux hosts. Disposable VM source overlay now confirms root process waydroid_t with host Enforcing. A discarded service PYTHONPATH/sitecustomize prototype is inert in the VM; no prototype source retained. First prototype used wrong /usr/local purelib and failed import; fixed actual RPM path before proceeding. No production files or host policy changed.
- Early raw Binder probes used obsolete /dev/anbox-binder or generic transport errors: not security passes. Correct endpoint is /dev/binder with aidl3 protocols. R6 APK b865 predecessor was replaced by apk-r6; its root probe incorrectly treated an empty Parcel as allowed. Fixed source to reject empty replies; rebuild pending. Root hardware unknown transaction65536 is read-only and has typed positive-control versus denial replies. Actual live tests exposed python-gbinder append_int32 rejecting negative values; fixed using unsigned wire words and strengthened fake accordingly. Six focused tests passed (bfa211bc); actual typed Binder denial is still being retested. No mutable hardware operation was invoked.
- Source preflight1dbcf889 canceled after lengthy duplicate cold audit; prepare64ad0adb failed after39m40s without useful stderr. Traced retry8f9172b3 failed cleanliness on WebView/Mesa LFS files: isolated Ubuntu lacked Git LFS/filter, while host LFS-aware status is clean. Original recovered source, binaries and archive were preserved. No reset/Android compile pass claimed.
- Added private Meson1.9.2 (official PyPI SHA1a284dc1912929098a6462401af58dc49ae3f324e94814a38a8f1020cee07cba) and Git LFS3.0.2 from signature-verified official Ubuntu apt (binary SHA682e3dbfed122987bfd8e73e21852bc92703e53fb2b20d1b34a361e3ce65774a). Own build-home global LFS filter, no account credentials. Tools check ea50c049 passes including exact pinned Android clang14.0.6, Meson, Ninja and LFS config. Maintained prepare retry25ed13ab is active under normal worker6GiB/20GiB HDD reservation, offline Bubblewrap. Source snapshot79198ee0e; independent candidate source-recovery/candidate/lineage-20. Full-image compilation has NOT started.
- Active units: dev-android-vm-r4-be5a1197 (own VM finite lifetime, expected end about01:11Z), dev-android-source-prepare-r3-25ed13ab. Do not extend VM/root bound or bypass16GiB account reservation. Full build10GiB requires stopping/releasing only this VM9GiB first. Source builder launcher own HDD source-builder/run.py. Source overlays own HDD vm/hardware-guard-source-84b.*, native probe vm guest /var/tmp/luma-hardware-probe-r5.py.
- Next: finish typed read-only root versus ordinary-UID Binder checks; commit and build/install .84 clean RPM; finish LFS-aware maintained prepare, bounded matched system/vendor build, then actual resize/platform rejection/dismissal acceptance. Source audit also found Android init property permission checks commented out in old Waydroid source and host clipboard/user-monitor Binder endpoints needing review; no actual mitigation/pass yet. Internal Android SELinux, maintained AOSP rebase, ARM/translation, hardware/documents/audio/media, inline reply/privacy, latency and physical suspend/push/power remain open. No complete or shipping claim.

2026-10-29T01:01Z follow-up:
- Maintained prepare25ed13ab succeeded after3min29s with Git LFS configured, peak1GiB. Full recorded HEAD snapshot and cleanliness passed; current task patches refreshed in the independent source candidate. No Android compilation yet.
- Explicit guarded entrypoint reached Android boot_completed=1. A first broker attempt timed out under concurrent HDD audit load; later standalone session boot succeeded. post-stop hook /dev/null returns126 and predates this adapter; no unsupported claim that it caused boot delay.
- Real Android service client control (root, unknown transaction65536) returned AIDL UnsupportedOperationException(-7); ordinary UID10127 returned SecurityException(-1). Fedora root service remains waydroid_t/Enforcing. Generic host python-gbinder client returned transport status1 and cannot qualify the path; replaced acceptance script with bounded Android-native service client and exact exception parsing. Actual APK Java boundary receipt is next.
- Latest fixture build5487068e succeeded: apk-r7 SHA0bcb811bfeeba066cdf533b87b5543c10b07c51b0339c0cc217cfeb6e9517460. Initial848067e7 failed because wrapper referenced r6 output; no original APK overwritten. One retry admission refused while other account jobs held memory. Corrected private wrapper and normal admission succeeded. First remove command used unsupported --confirmed; corrected without weakening installer.

2026-10-29T01:05Z:
- Source commit0169f2ff5 contains .84 guarded-entrypoint policy and clean-source Git LFS preflight. dev-android-runtime-rpm-r5-bb6c4fc1 passed:77 actual tests, zero skips/failures. Binary SHA5440a3bbac4f54a82e37ab6810c4f7572a92cd495817537016b46233197d43c6, SRPM e3c90df203dfbaac0e2d2c69f7c11ea874d271d20b25e2e2b6a2799a2a05024b, outputs own HDD rpm-r5/source/build/packages/luma-android-runtime. VM installation ongoing in update-mime-database; wait for final transaction before claiming package pass.
- Real r7 APK, installed normally and running UID10129, returned root-hardware-boundary=denied. Old framework platform-boundary remains allowed, as expected before rebuilt images. Read-only native positive control(-7)/ordinary UID denial(-1) also pass. Retained exact evidence own HDD vm/fixture-r7-boundary-events.txt. Triggered source-owned probe_boundary activity extra using Android am, not a privileged APK identity.
- Harmless volatile luma.test.property_boundary was writable by ordinary Android UID10129 in old image; readback confirmed then root cleared it. Added uncommitted Android init property admission patch plus both builder paths/state tracking. It rejects application and isolated app IDs across Android users before global/control writes, retains existing native service identities. No complete SELinux replacement claimed. Compilation/image acceptance pending; do not modify Android candidate tree outside maintained preparation.

2026-10-29T01:07Z property policy candidate:
- dev-android-property-writer-policy-139e36cc compiled the actual patched CheckPermissions function with permissive dependency fixtures.160 UID/user/property combinations passed; compiling the original function failed the same tests with expected42. Uses pinned Android UID constants, includes ordinary/control names as pure strings; no power/control operation executed. Candidate patch applies cleanly to exact recovered Waydroid-prepared system/core. Both clean and incremental builders apply/hash it; full init/image test still pending.
- .84 RPM installation completed successfully; final query reports only .84, helper is waydroid_exec_t, ExecStart selects it and no prototype PYTHONPATH remains. Restart/actual boundary recheck from installed RPM is active. Other abandoned prototype files are inert in this disposable overlay; no source prototype retained.

2026-10-29T01:08Z installed-package findings:
- .84 installed RPM's guarded root entrypoint booted Android and passes native typed root/app denial after starting the supervised session. First automated recheck found a stopped container and correctly failed; starting session explicitly restored the tested endpoint. Installed root service remains waydroid_t/Enforcing.
- Found a separate real packaging defect in HDD builds: inherited private/setgid directory modes entered the RPM, including /usr/share/luma/android/profiles. Broker could not read its profile. Did not bypass permissions in the VM. Added .85 explicit RPM directory mode0755 and build verification for every packaged directory (including generated cache/license dirs). .84 package tests passed, but its user session integration is not accepted. New .85 build/retest needed.

2026-10-29T01:10Z active build handoff:
- .85 clean RPM build dev-android-runtime-rpm-r6-2bc2b668 passed77 tests, zero skips/failures, and header verification of every packaged directory0755. Binary e3d34f570731d11738eb91708b0ab9e8932e91ebefd81c008687e83e3cc24931; SRPM029b2baf9f4455d0537e1ba57edee1748a6b2a20893dd911dc7f5d1b3c6e16f8. Source1273cbaf5, task HDD rpm-r6/source/build/packages/luma-android-runtime. This fixes observed .84 profile traversal; .85 VM installation remains next.
- Own VM shut down cleanly before its finite lifetime; dev-android-vm-r4-be5a1197 inactive/success. Original other VMs untouched. No VM should run concurrently with this account's10GiB full image worker without fresh normal admission.
- Full matched-image builder job dev-android-images-x86-r2-d3916ea0 admitted01:09:38Z with10GiB/200GiB HDD reservation, two build jobs, GOGC20, offline unprivileged Bubblewrap. First attempt refused memory while own VM reservation remained; no override. It uses exact source snapshot948b609305be896cb7c0e78ad058608885aba6ed, tar SHA4d3639e426bdcbb224e1db90986448b8fb20046e5099a9c2a1814ec57b67dc03. This snapshot includes property admission and prior SDK/HWC changes; current runtime .85 packaging does not affect image code. Launcher own HDD source-builder/run.py; original source snapshot retained. Builder begins with maintained preparation/full cleanliness checks before compilation. No image completion claimed.
- Source audit official Waydroid1.6.3 IClipboard/IUserMonitor shows unguarded host callback entrypoints too. Next independent source work is kernel-UID admission in Luma-owned session adapters before parcel reads, preserving Android clipboard focus policy. Do not read real user clipboards; test only synthetic VM content.

2026-10-29T01:14Z independent session admission candidate:
- Uncommitted .86 uses the same process-local constructor facade with distinct exact policies: root hardware allows0/1000, clipboard/user-monitor only1000. Repeat installation refuses policy drift. Luma session installs both adapters before upstream managers start; no provider file/global gbinder mutation. Android clipboard source still performs focus/lock/permission checks before system_server consults host clipboard.
- Actual policy tests a185b02b ran8 focused tests successfully. Component7b3acbdf ran79 tests successfully with16 root-only skips. Package build still required for zero-skip count. New fixture unknown-operation probes do not access clipboard contents or create package events; its shared receipt writer is synchronized for the concurrent probes. Read-only native acceptance script supports service-specific trusted controls; actual clipboard/user-monitor VM checks remain open.
- Full-image worker d3916ea0 has compiled the Soong build tools and is progressing into graph generation; this is not an image completion percentage. Current source includes property admission. Keep job normal10GiB, two compiler jobs/GOGC20; monitor actual result instead of raising memory/reserve or relaunching other VMs. .85 RPM mode validation passed but .85/.86 installation is deferred to next admitted test-VM lifetime after image build.

2026-10-29T01:18Z:
- .86 clean build ef21b4e4 passed79 actual tests, zero skips/failures and all package directory-mode checks. Binary6f32c3dd600750ba7ac27c5bf2141d9023f00e3ec28cd20286799aac6fe33e5c; SRPM6df655539478efeb7ba260aa9d0858d993f78eeade22179357fa779275077731. Source d3450923f, own HDD rpm-r7. New APK r8/05d00363 passed pinned-tool build:4c333923949aa3267fe0bf0cf4c1eb1069968599ac8a9504cb266a5b67200eb5. Neither installed in VM yet.
- Further source audit: current non-remapped Android system UID1000 can collide with host login UID1000. Kernel UID guard rejects ordinary APKs but is not complete origin attestation. Dedicated user-namespace identity mapping plus actual runtime-instance peer validation and protected production platform signing remain shipping gates; no sandbox completion claimed. Do not invent a cgroup path or weaken admission if origin cannot be verified.
- Added uncommitted .87 rejection of root Binder image-replacement transactions5/6, before parcel reads, even for trusted Android UIDs. Host verified update path owns image replacement; installed-image upstream already disables guest updater. ShutdownRequest7 only stops/restarts Android in the inspected upstream source, not host power; it remains intact. No mutable upgrade/power operation was invoked during tests.
- Image worker d3916ea0 remains active; Soong build-graph generation near its normal7GiB MemoryHigh, bounded10GiB maximum. No limit changes, VM restart or new image success claim.

2026-10-29T01:24Z acceptance accuracy:
- e83b615e1 commits host-owned image replacement; eight exact adapter tests pass ecd26fff. New .87 package build remains pending (last clean package .86).
- Updated desktop acceptance script to use only org.projectluma.androidtest and the existing disposable graphical session. Removed nested compositor/user-manager display mutation and misleading notification/SELinux claims. It records functional observations but fails the isolation gate for disabled Android MAC, shared user namespace or sampled UID collisions. Full mapping audit and actual notification/action receipts remain separate. Runtime smoke now checks installation of the actual Luma-owned transport instead of unrelated provider source strings. Shell syntax/diff checks pass; live script execution is pending new-image VM acceptance.
- Full image worker remains active, graph generation IO-bound on HDD (observed IO pressure ~80%, no OOM/max events), MemoryHigh7GiB/MemoryMax10GiB unchanged. Do not report old 399-step bootstrap progress as whole-image completion. Keep original archive/source/VM evidence intact.

2026-10-29T01:31Z image build pressure correction:
- d3916ea0 reached actual soong_build graph generation, not just bootstrap. It then accumulated ~86GiB read_bytes for25MiB read syscalls, with ~7.13GiB anonymous RSS and only164KiB file RSS, ~80% IO pressure and repeated file refaults. MemoryHigh7GiB/MemoryMax10GiB remained intact; no OOM/max events. This is executable-page thrashing under memory pressure, not useful image progress. Stopping only this owned attempt normally; keep all compiled objects/source/evidence.
- Pinned compiler Go is1.18, so GOMEMLIMIT is unavailable. Next use GOGC5 and GOMAXPROCS2, explicitly forward the latter through Soong's env-i boundary with an additive build-host-only patch and tracked preparation hash. Do not raise limits, remove reserves or adopt another build tree.
- .87 RPM build7d684f6e succeeded; counts/hashes pending receipt extraction. APK r9/7cfb98fa built successfully, SHA4408dd0e925e753ca6ea800179b9334b788b7c464344e79f4c1524290d4c67f7. It adds bounded actual app-UID writes to only volatile fixture property, with native positive-control/readback instructions. Source changes in BoundaryProbe.java/README remain uncommitted at this note until preserved with receipt.

2026-10-29T01:33Z compiler retry preparation:
- First graph job stopped normally; no source reset or policy change. Additive Soong patch0002 preserves explicit GOMAXPROCS through env-i; both builder paths and prepared-state hash tracking updated, applying after existing GC patch. Exact prepared source apply-check and shell syntax pass. Actual new compiler environment/graph result remains next.
- .87 RPM7d684f6e actual79 tests, zero skips/failures, directory checks pass. Binary0b549cb63ce1911b3b4f94bd1f6da0337464ad37551fe197c53127428059fdeb; SRPMd3b1f5591b196c6e42367f296ce4fffed455e38b316703f7262da4d38097cd89. Source12231bb40, own HDD rpm-r8. Latest source-owned APK r9 compile/signature checks pass; no live property/clipboard/user-monitor pass claimed.

2026-10-29T01:41Z Blueprint override correction:
- Retried worker7d31767a received actual GOGC5/GOMAXPROCS2 (verified in its compiler environment), but Blueprint bootstrap/command.go unconditionally calls runtime.GOMAXPROCS(runtime.NumCPU()). Observed105 threads, >1000% CPU and renewed ~7.14GiB anonymous RSS/file-page starvation. Stopped only this owned retry normally, no limit changes. Pinned args.NoGC is false; no claim that GC itself was disabled.
- New additive Blueprint patch honors explicit GOMAXPROCS while retaining upstream default behavior. Both maintained builder paths and incremental-state tracking apply it. Exact source apply-check/shell checks pass. c1196e84 is compiling/executing the actual production concurrency prelude and unpatched negative control with pinned Go1.18. New full-image retry follows the result.
- Added read-only bounded tests/android/measure-runtime.py for real cgroup CPU, memory.current, proportional memory and synthetic app attribution, with generation/incomplete-read checks and private exclusive output. Python compilation passes; actual VM measurement not yet run. No first-frame/battery claim.

- c1196e84 passed in7.9s/115MiB: actual patched Blueprint prelude respects explicit concurrency2 and retains unset default; original prelude fails the positive test with expected42. Public builder derives Go concurrency from an explicit LUMA_BUILD_JOBS budget unless separately provided, and refuses invalid explicit GOMAXPROCS. New retry will verify the actual compiler environment plus running thread/memory behavior; policy test alone is not a full build.

2026-10-29T01:50Z:
- Full-image worker c3c0c929 uses source9db3a1c96 and actual GOGC5/GOMAXPROCS2. Verified compiler now five threads/~196% CPU, ~3.65GiB RSS (including17MiB file pages) and only4KiB read_bytes at the sampled graph point, rather than the earlier executable-page refault loop. Different cache states/stages make this operational evidence, not a comparative benchmark or completed image.
- .88 source drops delivered notification body/image payloads from the ownership map, retains only package/action authority, and uses dbus.ByteArray instead of per-byte wrapper allocation. Weak-reference retention regression c53406cb passed9 tests. Real private-D-Bus a36830a1 passed9 checks including exact image bytes, actions/token, dismissal/expiration/withdrawal and server restart. Binder remains a fixture there; actual APK/image delivery requalification still required. Package .88 zero-skip checks next.
- An attempted registry claim refresh refused because the existing own record already exists; it was not overwritten. Actual branch/path/owner remain explicit in this task note. No unrelated task was adopted.

2026-10-29T02:02Z current continuation:
- e01969db5 commits .88 notification payload release/ByteArray changes. rpm-r9/3079868e passed80 real tests, zero skips/failures and directory-mode checks; binary9b6f0f9a40c1c92c0f06b60a595c2f43cb86d8e748f251cc08626c361583aaad, SRPMa1bffc9eac4d444b27fae563ff2348a8396edc4abeddc71a65e9136fb6096ce1. It remains uninstalled (own VM off).
- c3c0c929 eventually grew to the MemoryHigh threshold despite correct five-thread/two-CPU behavior and began refaulting executable pages again. Stopped only this owned attempt; no image completion. A separate normally admitted10GiB bounded180s direct compiler diagnostic c6ef88c8 ended124 as designed, preserving GC trace. Last trace at178.7s: ~2,759MiB live heap,5% goal~2,897MiB; diagnostics had3.2GiB peak but had not reached the later large graph stage. It proves GC is active, not that full graph now fits. No active full build currently.
- Preparing an explicit image-build-only Make patch that omits unused Windows SDK host-cross variants. Guest target/secondary architectures and ABIs are untouched; both builder paths/state hashes track the patch. Initial pure Make test44c5faf0 exposed command-line variable precedence; corrected explicit flag with override and retried abdb14d6. Exact source apply/shell syntax checks pass. Full generated Soong JSON/heap/build acceptance are next. This is a host graph optimization, not reduced Android API or native ABI coverage.

2026-10-29T02:05Z host graph policy check:
- abdb14d6 passed the patched cases but failed its negative-control assertion because the test supplied empty HOST_CROSS_OS on Make's command line, suppressing the upstream default. Corrected the fixture to supply the actual environment contract; 2d044c66 now passes all four real host-fragment/default comparisons and the upstream Windows control. Source policy and test fix are preserved separately. Generated full Soong configuration and graph fit remain next; no full image pass.

2026-10-29T03:00Z live .88 boundary finding:
- Missing clipboard positive control is endpoint absence, not a failed UID denial: real Waydroid clipboard_manager skips registration when pyclip is absent. VM has wl-clipboard2.2.1 but no python3-pyclip. User-monitor real system-UID control(-7) and ordinary UID10130 denial(-1) pass; root hardware had passed before the missing endpoint. No clipboard data read/written by those unknown-operation probes.
- Added .89 required python3-pyclip + wl-clipboard and build import check, fixing the observed missing optional provider. New fixture controls copy only fixed synthetic Android text and record only whether a whitelisted synthetic host string matched; arbitrary clipboard contents are never logged. Compilation/package/live text-copy/paste checks pending; no broad MIME clipboard qualification claimed.
- Low-RAM .88 source APKr9 normal remove/install/launch succeeded after Android completed its slow boot: command runtimes11.9/19.1/4.5s under concurrent HDD load, not benchmarks. Cold120s failures retained. No freeze (persist.waydroid.suspend=false); system_server later reports BOOT_COMPLETED. Earlier auto-warm session had started before the Wayland socket and exhausted its startup burst, another lifecycle issue to address.
- Build VM cloud-image preparation e67a90c1 passed, inactive domain UUID809f0871-fd88-4770-83dd-624c90259c06. Private SSH key stays under/root/.ssh/luma-android-builder-20261029.9GiB hard/7GiB RAM/4vCPU profile is to run under fresh10GiB normal admission; source disk pending successful squashfs pack. Generic host limits unchanged; guest-only24GiB disk paging. Rootfs285081600B SHAe01d7eb66e4b3bc04b974438dba450435fc45adc73c9cd5dd01dea37b0276e7f; tools6316032B SHA224ab89186bdc083938e1372c6cb29b4a6a0715dced6a1a9a85dea3fc98ed202. Source packf50c7d55 remains active.

2026-10-29T04:25Z compositor readiness:
- Source18bbe1d27 supervised session now waits for an actual listening, current-user Unix Wayland socket before invoking Waydroid. Directory monitoring plus bounded startup-only retry covers bind-before-listen/backlog races; every timer/monitor is removed at readiness/deadline. Broker starts warm session from idle callback, replacing blind4s delay. No resident readiness polling. Actual socket tests34936b83 passed three tests (delayed bind/listen, immediate absolute path, missing/stale/regular/symlink deadlines). Full package/live graphical reboot test pending.

2026-10-29T04:36Z continuation:
- .89 package31a8444c PASSED80 tests/zero skips/failures; binary15dd4a1a950d270949cfe44661e494104fa510ec7ffff654d3f8ab31e9e68692, SRPM79658a955b56e3f588a3b80147aa1aca8b7f20325ee47109ce48dd57de599013. .90 sourcecc15bd5a2 packageff59a12f PASSED83 tests/zero skips/failures plus directory modes. Binarydb6e7408e591d25aa83713eaccf625a38d69052a652618fd9021894d3c570754, SRPMdaa21b8aa950cff398ecc063e0907d879ff542f5979ec98718d3c0da85bce38c; outputs rpm-r10/source/build/candidate-90/build/packages/luma-android-runtime. Private builder luma-android-runtime-89-20261029 preserves damaged earlier cache.
- Build VM5145f446 verified all4 input hashes and booted, then failed because controller's missing initial log tail returned1 while guest setup was still active. ExecStopPost successfully destroyed ONLY its own UUID. Corrected startup status command and added failed-unit journal capture. Retry dev-android-images-build-vm-r2-ad1c3fe2 active10GiB/450GiB, rootunit luma-android-first-class-build-controller.service; re-verifying inputs, no compilation/image pass yet. Guest swap activation fallback supports clean restart; no host swap change.
- Runtime VM dev-android-runtime-vm-r6-7ef0192b active6GiB/10GiB, rootunit luma-android-first-class-runtime-controller.service with6,000s cap and own-UUID ExecStopPost. QGA became reachable before normal network/root services; earlier NM-not-running/absent journal were early boot, not a guest network diagnosis. By04:30Z normal SSH/IP192.168.122.35 and synthetic UID1000 Wayland session are ready. Root capped install90/20261029T043113 active, signature verified/ostree disposable unlock done, .90+pyclip installed, RPM waits for update-mime-database. Wait terminal transaction before restart/tests.
- Further clipboard source audit: HWC keyboard_handle_leave only releases keys; Android clipboard read policy trusts mWm.isUidFocused, and writes permit unfocused apps. This suggests stale Android focus after switching to native windows; actual leakage is NOT yet measured. Add native-host-focus background negative control and enforce cross-domain focus before claiming clipboard privacy. Current private bridge test is functional/UID filtering only, never full isolation.

2026-10-29T05:02Z clipboard/cleanup evidence:
- .90 installation completed04:39:54Z, signature/import/profile755 checks passed. Restarted own services; Android boot_completed1, session NRestarts0. r10 ordinary remove first timed out after17.4s while source checksum saturated disk; preserved root journal fixture10. Paused only checksum unit for120s with automatic thaw (04:45:14..04:47:14); ordinary remove/install/launch then succeeded8.74/8.84/2.77s command times, not frame or speed qualification. Private fixture-r10-install.json now holds the retry; original failure remains root journal and chat evidence.
- Actual .90 native typed trusted/app controls passed all3 hardware/clipboard/usermonitor endpoints. r10 APK actualUID10131 reports all3 host endpoints denied. Old image still allows platform and test property; root cleared the volatile test key. Rootwaydroid_t and FedoraEnforcing retained, AndroidDisabled. Exact guest result /var/tmp/luma-android-qualification/host-bridge-88-result.json contains actual .90 package; filename is legacy, not a claim .88 passed clipboard.
- Real r10 normal UI copied synthetic Android text; native wl-paste comparison matched, no arbitrary clipboard contents logged. Native wl-copy fixed host token then normal APK UI read returned matched-host-fixture. Root private logs clipboard10/20261029T044813 and own VM clipboard-android-to-host-r10.txt, captures clipboard-copy/read-r10.png. Bidirectional plain-text function only; native-focus/background privacy remains unqualified.
- Recovered SDK Clipboard.java uses vendor HIDL while tested host exposes lineageos.waydroid.IClipboard AIDL. Stopped buildr3 before compilation to align contract. New source0004 adds matching AIDL, reconnects after binder death/errors without replaying failed writes, and preserves a newer connection against late old-proxy failure. Both maintained builders apply it; preparation state includes hash. Pinned AIDL generation+official API compile+production wrapper dependency-fixture tests14e4dee5 PASSED; first28942a04 failed because javac absent from host PATH, corrected to pinned source JDK17. No rebuilt-image IPC pass.
- Stopping normal build job158f8b65 did NOT initially stop its separate root controller. Found and manually stopped root controller/own UUID04:52:55Z; original/other domains untouched. New private build-vm-driver/run.sh has EXIT cleanup and job_owner.py watches actual admitted parent PID/start/UID/cgroup/memory limit; root actor terminates if owner disappears. Synthetic no-VM watcher tests: forcibly killed owner2dbfcc8d at04:55:58; root stopped04:55:59. Normal devctl stop4f675d66 also stopped root actor via watchdog within1s (primary trap alone not proven). ExecStopPost own-UUID shutdown already observed. No host limits/reserves changed. Future lowram driver run-r3.sh uses same watchdog; CURRENT runtime VM still uses old run-r2.sh, so stop its root unit explicitly before stopping admission.
- r3 build VM did get past corrected kernel swap parser and into guest mount preparation, then was manually stopped for the protocol fix. No image compiled. All build VMs now INACTIVE. New controller logs every guest mount command; update readonly Luma project disk with source0004 before next full build. Android source/rootfs/tools disks retained unchanged.
- New APKr11/17ec82d0 compiled/signed successfully SHA862bf4e6e0d931c7c8e714c34eb3060c4d5410423fd8f442c1a85b9447c090eb, adds10s delayed synthetic clipboard read plus actual Android focus receipt. Live host-focus negative control is next. Current live runtime6GiB admission7ef0192b continues, deadline about06:00Z; own build off.

2026-10-29T06:12Z actual-path correction:
- Corrected earlier AIDL-backend assumption after actual DEX plus composer implementation inspection. Never use901d5f18c project image; HIDL-native reconnect/admission source candidate replaces it. Legacy dependency/provider removed from .91 candidate. No prior claim that legacy endpoint guards protect native clipboard remains current. Actual HIDL generator assert/callback review caught and corrected a transport-error denial that would abort the service; da69c3a5 updated contract tests pass32 cases with negative42, 430cbf55 wrapper lifecycle passes. Full image/wire and blocked-transfer tests remain next.

2026-10-29T06:18Z source correction validation:
- Added explicit libhwbinder_headers dependency for native UID API. Updated native policyf03ebfd2 passes32 source-body cases including exact callback-once/successful-return contract and unpatched negative42. SDK lifecycle283a9cc5 passes after removing misleading AIDL dependency fixtures; actual HIDL source remains unchanged in protocol. Adapter0a622f4d passes9 tests, including legacy provider suppression; first51ecc79f failed test import name and was corrected. .91 package check still next.

2026-10-29T07:27Z native transfer milestone:
- Added0016: one sleeping poll/eventfd worker per runtime,2s/1MiB text limits,
  at most16 accepted writes, partial I/O, worker-local SIGPIPE mask, UTF-8 checks,
  generation-checked synchronized cache and joined shutdown. Clipboard data source
  snapshots use existing token registry to avoid listener-payload UAF during
  Binder-side retirement; at most8 sources, later copies keep working.
- Production-source dependency-fixture ASan/UBSan91d70a0f and TSan f4415ac5 pass;
  real-helper stress ASan/UBSan and TSan receipts0d019389 pass800 concurrent read
  submissions across shutdown with exactly800 callbacks and no descriptor leak.
  Full pinned upstream series264ac866 passes. Android image/wire still unqualified.
- Build r6 still in Git/LFS preflight at07:24Z (prebuilts ABI dumps/JDK and later
  projects advancing). Read-only project59aecadc4 has0015, not0016. Runtime VMoff.
