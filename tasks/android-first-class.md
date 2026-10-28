# Task: android-first-class

- Status: active
- Owner: Codex, luma-dev-2; Android first-class implementation chat
- Updated: 2026-10-28T23:20:00+00:00
- Project: luma-os
- Branch and last useful commit: codex/android-first-class; 765a59e1c (plus the following idle-policy change)
- Worktree: /srv/dev/worktrees/luma-os/android-first-class
- Scope and expected result: implement the researched Android subsystem, native install/app identity/window and host-service integration, both CPU architectures and measured qualification. User explicitly authorized downloads, edits and testing without questions. No unsupported performance or hardware claims.
- Files / interfaces owned by this task: src/luma-android, Android integration patches/build profiles and tests; coordinate any shared installer/platform changes by inspecting existing owners.
- Related tasks and integration owner: this chat owns Android implementation; preserve active mobile/ARM/device/release tasks and device gates.

## Current handoff

Source baseline is current observed GitHub main, isolated from canonical checkout.
Research report: /srv/dev/accounts/second/artifacts/android-first-class-research-20261028/RECOMMENDATION.md.
Host notification transport, Android lifecycle/caller patches, a reproduced configure/present deadlock fix, and a source-owned APK fixture are under qualification. No complete subsystem or hardware pass claimed.
Bulk outputs must use mounted HDD: root SSD has about32GiB free and secondary about22GiB; preserve20GiB reserves. No other task services running in this account at initial snapshot.

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
