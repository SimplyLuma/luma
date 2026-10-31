# October 30 nightly: Android and Ari, x86 and ARM

- Owner: luma-dev-2; Android integration conversation, now owning this coordinated build.
- Branch: codex/nightly-20261030-android-ari
- Worktree: /srv/dev/worktrees/luma-os/nightly-20261030-android-ari
- Status: source integration and bounded build preflight.
- Authorization: Nick requested GitHub integration, reconciliation of open PRs and complete x86_64/AArch64 nightly builds, explicitly including Android and the new Ari features. Avoid redundant checks and rebuild loops. No unrelated private APK/account content may enter source or images.

## Execution limits

1. Freeze one converged source and package cohort after reviewing PR41/83/99 and Android. Ari is already on main2ea78edeaf. Keep each source change and actual test result visible.
2. Reuse exact unchanged packages, native compiler outputs and authenticated installer donors. Rebuild affected components only. Use the existing architecture-aware release tooling and normal resource/signing admission.
3. Run one relevant source/component check pass, then basic exact-candidate install/boot and affected Android/Ari checks. Run required build/publication checks; do not invent repeated broad test cycles or silently mark failures passing.
4. Bound every expensive stage and retain durable results. Diagnose a failed stage once, make the concrete correction and resume only affected work. Stop retrying unchanged failing operations. Surface a real blocker instead of spending twelve hours in a loop.
5. Produce complete private nightly candidates and matching installation media for both CPUs. Public binary publication is a separate result and must not be inferred from source push or an image build.

## Preflight observations

- Master Maintainer thread and recorded release/performance handoffs consulted. Both authorized attempts to message that existing chat failed at the app transport before delivery; use recorded guidance and current actual release state.
- Shared Git had one empty internal Codex diff-capture reference. Its exact empty body/path were retained privately, then that invalid metadata was quarantined; normal fetch now works. No source branch was removed.
- Android branch79ff12e6d pushed to GitHub. Physical Dell uses the verified229a7a3ed x86 Android build with native settings transport and corrected desktop canvas. ARM Android output exists from an earlier source and needs a current incremental build. The current image RPM admits x86 only; both-CPU packaging must be made real.
- Three existing PRs:41 ARM pipeline,83 installer/runtime reuse,99 mobile Shell. Do not close until their intended changes are integrated or demonstrably superseded.
- SSD capacity is tight (main about21GiB, secondary about19GiB free); HDD has1.8TiB. Resolve actual scratch placement before starting compose. Preserve current reserve and other owners' storage.

## Integration checkpoint, October30 local evening

All four heads are merged into this branch with conflict resolutions preserving
new Ari services, Mobile/Keycap, native Android drawer/notification support and
the ARM-aware installer/runtime pipeline. Package cohort: Shell132, Platform120,
installer82, Android runtime97/images10, Filer76, core107, icons44 and Tide36.
ARM preview now includes the architecture-matched Android runtime/images.

Focused checks actually passed:94 source contracts,13 device-checker cases and
22 device-tree tests,12 image-admission cases including ARM/cross-CPU rejection,
two actual RPM patch-number parser controls,113 package/spec comparisons with
the existing Phosh exception,31 release-note fragments and four app-registry
checks. Initial duplicate Shell-row/profile mismatch and missing jsonschema
were corrected before package builds. Later checks caught one Depot Tide pin
and four old unformatted note fragments; affected checks now pass. No OS build
or install acceptance is claimed by these source results.

Current Android ARM build: dev-android-window-native-build-r469-68d3386f.service,
guard dcdf6e7e, monitor b7ed5a6c. Private window-controls-build-r469 under the
Android task HDD binding. Uses original229a7a3ed runtime source and retained ARM
compiler outputs, no reset. Worker90min/root controller110min hard deadlines.
Input hashes passed, VM booted and Ninja startup entered03:05Z. Keep current
x86 r453 image byte proofs and existing public compiler/source notices.

Root preflight ef8a6635 passed: no other nightly build unit active; only the
existing OS HTTP server and this Android VM are active. Mac ARM port22442 is
currently disconnected; native package CI on GitHub supplies both CPU runners.
Physical free: main23.15GB, secondary20.40GB (below20GiB floor), HDD1.92TB.
Use dedicated HDD pipeline/storage and resolve the secondary reserve before
compose; no arbitrary old-cache deletion or shared global prune.

## Build checkpoint, October30 22:23 Chicago

PR102 is pushed with the combined source; PR41/83/99/101 remain retained until
integration lands. GitHub source checks passed on both CPUs. Toolkit run
41163229722 is building both native CPUs. UI/browser attempts stopped early
on missing Platform120; rerun affected consumers after toolkit success. Broad
unchanged WebKit, GTK3/Search and all-component matrices were cancelled to avoid
redundant expensive rebuilds. Selected affected packages will run explicitly.

ARM Android r469 actual guest compiler exit0 was recorded03:21UTC; verified
export/controller finalization remains pending. Source229a7a3ed remains the
Android compiler input, while the full OS packaging source includes Ari.

The canonical old Nightly21 pipeline is an unmounted empty placeholder; retained
HDD/volume cache recovery is being inspected before compose. Storage relocation
job3518e703 preserves one inactive Oct11 ISO on HDD and its original pathname,
with full verification before retiring only its old SSD inode. No cache prune.

Final source-cohort alignment includes Shell spec132 and installer compatibility
constant82, matching the already updated pins. No full image acceptance claimed.
