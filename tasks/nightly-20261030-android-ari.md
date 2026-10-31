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

## Native packaging checkpoint, October30 23:20 Chicago

Both current Android compiler/image exports completed successfully. Native image
RPMs and SRPMs for x86_64 and AArch64 are built (normal job11daf54a exit0);
inside-image architecture, task-removal Java/native/VINTF correspondence,
notices and exact byte/provenance checks passed. These are private candidates;
Android device, platform-security and full source-offer gates remain open.

Native CI results: toolkit41165040069 both CPUs PASS; Android runtime97 run
41165557492 PASS with143 tests/no skips; Valet82 run41166369625 PASS; Ari21
jobs for both CPUs in41165777568 PASS (the older failed Valet job in that matrix
is retained and was superseded separately). Selected widgets, icons, Shell
state and APK metadata run41164195195 passed. Complete archives were checked
against GitHub's digest, source receipts and RPM headers before staging.

The first combined Shell numbering incorrectly followed builder copy order,
changing historical patch dependencies. The current68a24e0d8 restores the main
sequence and merges added patches around their original anchors. All277 patches
were applied to the actual source; three affected modules parsed. Two new
contexts were rebased while preserving native touch, wheel scrolling and the
bounded Desktop-only presentation wait. Filer's actual161-patch preparation
also succeeded after restoring lexical declaration order. Native UI run
41167355646 has x86 Shell PASS; ARM Shell and both Filer compilers are active.

Resolved actual package integration failures: final Filer release76; optional
Android sources/licenses included only as Valet test dependencies; OS baseline
excludes separate Office/Creative products; service test follows the actual
packaged baseline entrypoint. Updated matrix consumer tests passed19 cases;
baseline tests passed8. No skipped package tests or waived failures were used.

Root HDD release alias: /srv/luma-build/nightly-20261030. Protected source v2 is
e95b48cec before OS compose, with its prior source record retained. The final
Shell/Filer source update is still needed before compose. Core107/Tide36 build
is job1a221ab6 using a short private builder path,6GiB admission and a4GiB actual
container cap. Long temporary paths were rejected by a socket preflight; no
package checks were skipped. Local rootless socket failures were avoided by
using the native CI producer for runtime97. Compiler images were not rebuilt.

The old Nightly21 pipeline was restored read-only from its retained filesystem.
One inactive Oct11 ISO was fully verified and relocated to HDD with a persistent
read-only original-path bind (job3518e703 PASS), restoring secondary reserve to
about25.7GiB. Current OS/build/signer work stays on HDD. No shared cache prune.
WebKit's unchanged native provider artifacts from40433447051 are being reused;
their GitHub merge receipt has the exact head tree and declared parent.

Still required: finish affected native UI/Core/Tide packages, assemble/sign the
matching app runtimes and updated apps, compose both complete OS candidates and
matching media, run bounded basic candidate checks, merge PR102 and reconcile
PR41/83/99/101 against the actual integrated heads. No complete OS image,
installation-media acceptance or public nightly publication is claimed yet.

## Integration checkpoint, October30 23:47 Chicago

PR102 merged at51d6c9ee444bbf049ae26c6161675c28ce611a1b. PR101 and41 are
merged; PR83 closed with integrated-head ancestry proof. PR99 advanced by three
Keycap/input commits after the original snapshot; its actual9e5339d6d head is
included in PR103. Final native Shell133 run41168295245 and Mutter15
run41168297092 passed both CPUs; Filer76 passed in41167355646. All eight UI
archives and exact CPU/RPM bodies were admitted in normal33e5a84f. Source checks
41168298737 and41169464259 passed. Redundant automatic toolkit/all-package
and Mutter reruns were cancelled; automatic UI run41169464257 failed because
its default bootstrap still named Platform116. The default now selects the
already authenticated Platform120 producer41165040069; actual default
admission passed both CPUs in b3393997, without rebuilding toolkit/UI again.

Runtime assembly is active in normal194b455b, private root
/srv/luma-build/nightly-20261030/runtime-{x86_64,aarch64}. x86 Platform assembled;
SDK is installing its RPM closure. The assembler now explicitly selects target
CPU independently of native release-tools CPU, passing --forcearch to DNF;
actual ARM payload verification remains pending. WebKit was fully archived and
verified from unchanged native40433447051; no engine rebuild. Private signing
control and independent old signed aggregate seed prepared574c4b25. Fifteen
changed shipping app sources were sealed in0d71c7ea, including Ari, Tide, Filer,
Core apps and Depot; unchanged apps will retain their signed commits.

Core107/Tide36 active normal7d20be71. Earlier failures remain recorded: inherited
HDD ACLs rejected private receipts; long temporary paths rejected sockets;
HDD fsync exceeded the mailbox test's ten-second deadline. The current bounded
512MiB private RAM temporary filesystem preserves all checks. One GTK screenshot
check incorrectly rendered a newly created WidgetPaintable before its first
frame; it now waits boundedly for a real nonempty frame before the original
pixel assertions. The corrected build has passed that stage and continues the
normal package checks. No Core/Tide RPM success is claimed yet.

Installer runtime source is unchanged versus the qualified October27 source;
normal reuse and clock/signature checks remain required for new payloads.
Installer tools preparation10bd57d3 completed; this did not build a Lorax runtime.
Normal OS pool waiting job752adebb was stopped (its exact root wait unit also
stopped) to release an idle reservation; relaunch only after Core closes. Next:
finish Core/Tide, merge103/reconcile99, finish runtime/apps/baselines, then both
complete OS candidates/exports/matching media and bounded basic checks. No full
OS candidate, new ISO, device acceptance or public publication is claimed.
