# Task: android-first-class

- Status: active
- Owner: Codex, luma-dev-2; Android first-class implementation chat
- Updated: 2026-10-30T04:38:15+00:00
- Project: luma-os
- Branch and last useful commit: codex/android-first-class; 89cde87b0 (gated probe runner), d1601ca0b (focused fixture), 1d6510a8a (Mesa preflight); SDK4 x86 scope tests passed; SDK0005 compiled/exported; corrected host packages installed; graphical-login diagnosis active
- Worktree: /srv/dev/worktrees/luma-os/android-first-class
- Scope and expected result: implement the researched Android subsystem, native install/app identity/window and host-service integration, both CPU architectures and measured qualification. User explicitly authorized downloads, edits and testing without questions. No unsupported performance or hardware claims.
- Files / interfaces owned by this task: src/luma-android, Android integration patches/build profiles and tests; coordinate any shared installer/platform changes by inspecting existing owners.
- Related tasks and integration owner: this chat owns Android implementation; preserve active mobile/ARM/device/release tasks and device gates.

## Acceptance audit

Owner for every open gate: this Android task. No gate closes because a source
unit test, baseline run or image build passed. Require the named behavior on a
recorded matching OS/runtime/Shell/system/vendor/APK set, with actual architecture,
profile and hardware scope. Failures and unavailable controls stay open. Package
.94/126 tests and Shell124 compiled resources are component results only.

| Gate | Current evidence/status | Required closure / next action |
| --- | --- | --- |
| A01 Matched candidate | PARTIAL x86: actual SDK5/host96/Shell127/APKr16 boot and native image hashes passed183e21f8; UI reply/other scopes pending | SDK4 image guards/resize passed. Observe current matching SDK5/host95/Shell127/APKr16 test through actual native reply and repeated scope checks. |
| A02 Install and app identity | Partial old-image CLI install/remove/launch | Actual APK double-click in Filer; app drawer/search/dock pin/activation; split/universal packages; updates, signing identity and uninstall/data behavior. |
| A03 Shared runtime/storage cost | Architecture defined; complete measurement OPEN | Prove multiple apps share one platform image; measure allocated blocks/private data/cache growth and idle footprint, not sparse file size or summed RSS. |
| A04 Lifecycle/windows | PARTIAL: actual rebuilt x86 VM resize/maximize/restore, two state restorations and responsive activation passed2ad2f9cc; visual capture unavailable | Repeat resize/maximize/minimize/restore/rotation/activity recreation/close, concurrent apps and runtime/compositor restarts; preserve task identity/state. |
| A05 Startup/performance | .92 full-RAM VM cold/warm command observations only; earlier failures retained | First-frame and frame-time distributions, warm activation, sustained workloads, native comparison and hardware traces; no UI stalls or hidden resource omissions. |
| A06 Native notifications | Old-image native card and Mark read observed; new lifecycle/reply runtime OPEN | Real APK text reply through Luma UI and original PendingIntent; replacement/dismissal/restart/deduplication/uncertain delivery with exact synthetic receipts. |
| A07 Notification policy/bidirectionality | OPEN implementation/qualification | Group/channel/DND/lock-screen privacy, custom-content semantic fallback and explicitly consented host-notification listener access; no echo loops. |
| A08 Clipboard | Plain text worked on old image; source transfer guards; focus policy unimplemented/test inconclusive | Actual new native-HIDL admission with nonempty trusted controls; compositor-attested logical focus including touch-only profiles; read/write revocation, rich MIME, stalled/large transfers and lifetime/restart tests. |
| A09 Documents/share/drop | Android picker alone is insufficient; OPEN host broker | Scoped native file handles/URIs, seek/read/write-back, persist/revoke, reconnect, share/drop and denied paths; no whole-home mount. |
| A10 Input/layout/accessibility | OPEN system qualification | Phone/tablet layouts, DPI/rotation, touch/stylus, IME/compose/keyboard, selection and actual accessibility roles/actions on desktop and mobile. |
| A11 Audio/media | OPEN per-app native bridge | PipeWire stream identity/routing/focus/volume/interruptions and MediaSession transport controls with concurrent native apps. |
| A12 Camera/mic/location | OPEN per-app native bridge | Per-app permission/device choice, recording/location indicators, live revocation, denied/background cases and actual hardware. |
| A13 Contacts/calendar/telephony | OPEN consented providers | Native authoritative services via scoped grants; no private native database mounts or Android ownership of modem/controllers. |
| A14 Other hardware | OPEN arbitration/qualification | USB/Bluetooth/NFC/sensors with native coexistence, real device reconnect/revocation and accurate capability advertisement. |
| A15 Isolation/origin | BLOCKED for production: Android SELinux Disabled, UID1000 collision/shared namespace baseline | Enforced MAC and separate/remapped identities; attest runtime origin; audit Binder/device/socket/data/profile boundaries and cross-app/profile negatives. Host Enforcing alone does not pass. |
| A16 Privileged native services | PARTIAL: actual ordinary UID10128 native clipboard read/write and task-name guards passed with nonempty UID1000 before/after controls2ad2f9cc; remaining methods/boundaries open | Clipboard read/write and task-name getter passed on SDK4 x86 image. Require actual task focus/remove/remove-all negatives and trusted controls, plus property/platform/host-service boundaries and repeat on final image. |
| A17 Process/resource integration | OPEN full per-app attribution | Verified process/package/profile identity, CPU/PSS/GPU accounting and scoped app termination across native Luma surfaces; aggregate runtime row insufficient. |
| A18 CPU/page/GPU matrix | x86 VM baseline only; ARM/page config source checks | Actual native x86_64 and ARM64 builds/corpus; admitted page sizes, acceleration/buffer sharing/synchronization and physical Dell/Surface behavior. |
| A19 Optional translation | OPEN; no qualified engine | Prefer inspected native ABI; explicit opt-in and separately qualify ARM-to-x86 and x86-to-ARM libraries, performance and licensing. Do not advertise either until it works. |
| A20 Background/mobile power | Old-image local alarm/no-window observation only | Jobs/alarms/network push, suspend/resume/wakeup, Doze/wakelocks, idle drain and sustained physical energy/thermal measurements; no freeze shortcut. |
| A21 Recovery/data lifecycle | Partial baseline lifetime/restart controls | Atomic runtime/image updates, rollback/data migration, corruption/out-of-space recovery and multi-user/profile removal without data leakage/loss. |
| A22 Maintained platform/trust | Android13 repair baseline; rebase/trust OPEN | Maintained security baseline, pinned both-CPU sources and reproducible artifacts; protected production signing and rejection of test-key privileged apps. |
| A23 Distribution/rights | Research/source notices retained; final payload review OPEN | Actual component licenses/NOTICE/corresponding source/offers, original APK signatures, branding/service/proprietary rights and authorized distribution infrastructure. |
| A24 Build recovery/observation | Save/restore synthetic control; actual compiler restoration OPEN | One-shot validated compiler restore and exported-image checks; observe every final child; failure/cancellation/deadline/admission/budget exhaustion remain visible. |

Order of immediate work: finish the current guard image, run real native negative
and positive controls, build the reply-enabled pair, exercise actual native UI,
then repeat the CPU/device matrix and implement/qualify the remaining brokers.
An x86 development test image is not a declaration of daily-use or ARM readiness.

## Current handoff

CURRENT04:38Z — single-boot continuation under larger accounted cache cap active:
- r41 f711e6d8 FAILED before Android/UI: fresh user Shell service timeout.
  No new reply/window/guard qualification. Its original source and unique token
 6c0944d9 remain unused; cleanup readback was inconclusive, so fixture remains
  owned/recoverable. No claim that all preferences were restored by that run.
- Host observation showed guest pressure alone misses QEMU cache reclaim:
  old8GiB cap hit392 times, no OOM; anon~6GiB/file~1.6GiB. Host had~46GiB
  available with VM off. This supports a controlled cap trial, not a proven
  primary cause. Guest remains6GiB, CPU2/GPU unchanged; QEMU cap now12GiB.
- LIVE parent dev-android-highmem-reply-489c58bc.service normal1GiB/root256MiB,
  vm-cache-budget-r44 guard; child dev-android-reply-vm-r44-560f0a3a.service
  normal14GiB/root1GiB/QEMU12. TEMPORARY SHARED policy changes ONLY worker
  ceiling10->14; account16/global32/host8/disk20 reserves unchanged. Original
  policy SHA24cfbd91..., expandedb38b7783...; exact-byte automatic restoration
  and refusal to overwrite foreign edits. Root guard stops child/restores owned
  XML, and has a separate ExecStopPost restore fallback. Observe BOTH final jobs.
- Earlier guard245d18fb failed absent absolute devctl path before child; real
  original policy restoration passed. R43 guard e22344e4/child9d79cf4a then
  failed initial SSH master connection, before fixture recovery. Both restored
  XML and original policy. Actual live memtune12 verified, but no speedup claim.
- Frozen r44 uses ONE fresh OS boot and the exact preexisting r41 helper/probe/
  candidate hashes. Enables ONLY that already discovered owned extension via
  actual user manager, requires exact same boot/payload/unused reply-token checks,
  then real UI plus independent window/native scopes. No action retries; only
  idempotent SSH true readiness retries. Preflight585480f9 PASSED syntax/hashes
  and4 isolated capacity restoration/alias/foreign-change cases. Fixture tests
  do not mutate live policy; live mutation occurs only in the guarded parent.
- F-Droid2.0.2 downloaded/fully APK-signature verified e51c8898 PASS.12,569,951B,
  SHA19d51fad482239f6df6a99120e34513667e9a86d136dafdc4a211a68c3327b52,
  certificate matches published43238d51...; minSDK24,target37. Private corpus-
  fdroid-r42/download-proof.json. NOT installed/launched/integration-qualified.

HISTORICAL04:06Z — actual Luma card/input reached; dispatch observation corrected:
- r40 e6cc8f8e failed at editor text comparison before Send. Actual96 label and
  waydroid_t process under Enforcing passed again; SDK5/APKr16 hashes and app UID/
  PID matched. Real live Luma card, policy(dndfalse/bannertrue), matching native
  owner, mapped entry/button and actual pointer focus all passed. Failed-state.png
  captured real Luma card/editor with synthetic text, not a mock or injected card.
- No Send click and no Android reply receipt. Observer previously compared text
  immediately after queued virtual keyboard events. Source now waits at most2s
  for actual editor contents and records observed text; no direct set_text, input
  bypass or fabricated receipt. Still UNQUALIFIED until actual runtime pass.
- r40 cleanup restored settings/removed owned extension, original XML restored/
  raw alias unmounted. No VM active at this note. Next run also records window
  and native guard results independently of UI failure so separate gates progress;
  any incomplete scope still makes the overall job fail. Same immutable96/127/
  SDK5/APKr16 payloads, no package/image rebuild required for probe-only change.

HISTORICAL03:59Z — corrected live-card probe active:
- LIVE dev-android-reply-vm-r40-e6cc8f8e.service normal10GiB/root1GiB;
  frozen reply-vm-r40, source probe940dad211. Preflight5ab3d228 PASSED all
  helper/package hashes and Python/Bash/JS syntax. Same96/127/SDK5/APKr16.
- First actual user Shell/bus and preparation passed; currently fresh reboot
  before real live-card input. No history opening or hidden-editor mutation.
  Repeats deployed label/root-domain checks and requires fresh exact Android
  PendingIntent receipt, then actual resize/native guard scopes. All still pending.
- Initial pressure: IO waits high, memory pressure0, CPU some~5%; no RAM/CPU
  increase justified by these samples. Do not present VM timings as device perf.

HISTORICAL03:53Z — actual96 label/domain/SDK5 boot passed; UI probe corrected:
- r39 183e21f8 staged70093fa6fe77fb146b09b98c942fa4c2401964b1117f70af5c49e468efd9711d.
  Fresh96/127/SDK5 boot: actual user Shell running, health success, launcher
  waydroid_exec_t, root service PID1678 actually waydroid_t with host Enforcing.
  Android boot completed; installed APKr16 and native SDK5 image component hashes
  passed. Ordinary APK UID10128/PID1529 launched/posted real notification42.
- Reply negotiation succeeded, but probe opened HISTORY (which deliberately
  calls setInlineReplyVisible(false)) and then looked for a mapped editor. Test
  FAILED1 at03:49:23Z, with no input sent and no reply receipt. No UI pass.
  Cleanup restored original settings/removed fixture; XML restored/alias unmounted.
- Corrected source probe observes the daemon's actual LIVE Luma notification
  card, its real ownership/policy and mapped controls. No private presentation
  calls, toggling hidden editors, synthetic card or direct transport. Adds failed
  screenshot for diagnosis. Normal syntaxeab65098 PASSED; actual input still open.
- Runtime96 label-copyup change now has actual atomic install/start evidence;
  this does not close Android internal SELinux/shared-UID production gate A15.
  No VM currently active. Next freeze a new probe run against same96/127/SDK5.

HISTORICAL03:42Z — runtime96 packaged; actual atomic label/reply test active:
- Candidate96 source52564ea7e. Initial generic build8249b3cd failed before
  packaging: NoNewPrivileges prevented rootless UID mapping. Correct normal
  --kind package one-shot a5c91e96 PASSED03:38:04.853083Z:128 tests/0 skips,
  payload/Python/desktop/mode checks. No runner policy change or root bypass.
- RPM25dc4ccb3e3dd50745c092df2d4d074090bc58ea07227a1802521e9e3b5679f1;
  SRPMdf2d1ad6642b8eade9c926723038b780d4cc0d01eef2c258b9cd17596e88a104.
  Immutable receipt rpm-r38/source-receipt.json; outputs beneath existing
  rpm-r10/source/build/candidate-96/build/packages/luma-android-runtime.
- LIVE dev-android-reply-vm-r39-183e21f8.service10GiB/root1GiB, frozen
  reply-vm-r39. Same buffered raw6GiB/QEMU8/2CPU. Initial actual user Shell/bus
  running checks passed; currently replacing95->96 with unchangedShell127/SDK5.
  Fresh boot must prove launcher waydroid_exec_t AND real root process
  waydroid_t under Enforcing before native APK reply/resize/guard qualification.
  Preflight1e956766 PASSED syntax/pinned bytes. No end-to-end pass yet.
- Cleanup7ec3652a PASSED removing ONLY owned completed one-shot builder image
  b7076710, no force/pruning. RPM/SRPM/source receipts retained; main free~23GiB.

HISTORICAL03:34Z — actual runtime label regression; candidate96 source prepared:
- r33 506ec195 FAILED actual resume before Android started. Both real user Shell
  boots passed running-service checks; fixture settings persisted and the native
  extension initialized after reboot. Fresh health success and exact SDK5 image
  hashes passed. Android refused startup outside waydroid_t; no reply qualified.
  Cleanup restored settings/removed fixture, original XML restored/unmounted raw
  alias, VM gracefully OFF. Original raw test disk retained for label diagnosis.
- Offline e4b312ef/7bef6813 show new810ef deployment launcher xattr bin_t, while
  declared policy and compiled contexts require waydroid_exec_t; older f865
  launcher is correctly labeled. No local rule overrides. b13af7a8 copied actual
  context files with SHA readback. Its automatic mode0100755 probes were invalid;
  subsequent read-only matchpathcon -m file checks both binary/text context sets
  and both return waydroid_exec_t. Do not count invalid probes as passing matches.
- Inspected pinned official rpm-ostree2026.2 d5ef5f0c and libostree2026.4
 4f4445a8 sources. Binary-policy checksum excludes file_contexts; rpm-ostree can
  treat cached inodes as canonical when that checksum is unchanged. This gives
  a concrete candidate explanation for the observed label-only update failure,
  not yet an experimentally closed root-cause claim.
- Candidate96 spec gives ONLY the owned launcher a fresh inode after module
  installation, preserving exact contents/mode/timestamp, before normal final
  labeling. No new allow rules, permissive mode or bypass of confinement check.
  Updated check floor128 includes both installer activation tests. Source change
  remains UNQUALIFIED until package checks, real atomic replacement, correct
  deployed xattr and actual waydroid_t startup/Android/UI tests pass.
- No VM or compile currently active. Next: immutable96 package build, replace
  ONLY95->96 in owned buffered raw VM, verify labels before native reply retry.

HISTORICAL03:12Z — diagnosed stale readiness; buffered VM profile test active:
- r31 ec852439 FAILED1 at03:06:37Z before preparing Android/UI fixture. Exact
 127/95 triple booted, but actual UID1000 Shell process was absent despite the
  socket. Original XML restored, raw alias unmounted, VM OFF before next work.
- Offline raw evidence97e1a65e PASSED verified selected-journal/profile extraction.
  Boot1f392b61 logs actual user Shell1509 startup timeout at140.744s, Wayland
  socket created144.573s after termination began, then systemd SIGABRT145.748s.
  This was not an unexplained spontaneous crash. Main thread was importing GJS
  modules at abort. Native health had accepted a login session before Shell was
  usable; its success alone is insufficient. No boot-health timeout/policy changed.
- LIVE dev-android-reply-vm-r33-506ec195.service normal10GiB/root1GiB;
  frozen reply-vm-r33. Same6GiB guest/8GiB QEMU/2CPU/GPU/reset watchdog. ONLY
  diagnostic disk cache changed none->writeback (normal flushes retained).
  Requires two consecutive actual UID1000 Shell/process/bus plus user service
  ActiveStateactive/SubStaterunning/Resultsuccess observations before setup,
  and rechecks the real user Shell service after fresh reboot before UI tests.
  Generic failure journals collected even before guest staging exists.
- Syntax/hash preflight7171ac6d PASSED. Native reply, Android SDK5 execution,
  resize/guard repeat remain OPEN. Earlier r28 reached a user Shell process and
  active manager/bus, but did NOT prove stable usable desktop/first frame; do not
  upgrade those preliminary observations to complete GUI acceptance.

HISTORICAL03:04Z — verified copy complete; corrected native QA actor active:
- Full buffered qemu-img comparison56b49bea PASSED02:58:02Z, with unchanged
  source/target bytes during comparison. Standalone raw copy is guest-byte equal
  to original at that checkpoint; subsequent owned VM boots intentionally write
  only the raw copy. Original qcow2 remains retained and unbooted in these runs.
- Success-only handoff5f3891b0 launched r28 b0607261. Actual raw boot reached
  user GNOME1501, active user manager/bus, and guest pressure showed storage waits
  with near-zero CPU/memory pressure. No Android processes in recorded snapshot.
  BUT observer tried /proc/pressure through MAC-confined guest-agent execution:
  PermissionError made readiness fail. Actual socket-only correction verified
  via agent, and separate actual SSH read verified manager/bus/pressure. No MAC
  relaxation. r28 remains FAILED1, not Android qualification. End03:03:14Z;
  original XML restored, diagnostic file bind unmounted, VM gracefully OFF.
- LIVE dev-android-reply-vm-r31-ec852439.service normal10GiB/disk10GiB;
  root luma-android-first-class-reply-vm-r31.service1GiB, raw VM6GiB/QEMU8.
  Frozen reply-vm-r31 helpers. Readiness checks only readable socket/boot identity
  via guest agent; actual user-manager/GNOME/bus and pressure verified through
  real SSH after readiness. No change to APK authorization or production policy.
  Preflight6fe5fad5 PASSED syntax and all helper/package manifest bytes.
- Candidate remains95/127/SDK5/APKr16. Real native inline-reply execution, fresh
  exact original PendingIntent receipt, repeated resize and native guards STILL
  pending. Observe this child through final result and original-profile restore.

HISTORICAL02:54Z — buffered comparison active; success-only boot handoff waiting:
- r27 conversion completed (raw48GiB virtual,35,024,945,152 allocated bytes).
  Direct-read qemu-img comparison was slow on the same HDD; deliberately stopped
  original normalf0197be8 at02:50:20Z, result143. Original handoff962aa96c saw
  that failure and exited1 WITHOUT booting. Neither is a passing qualification.
- LIVE dev-android-runtime-raw-compare-56b49bea.service normal4GiB/root3GiB,
  driver runtime-raw-compare-r29. Full qemu-img compare now uses buffered reads;
  comparison.json and qemu-compare.log are its authoritative outcome. No Android
  performance claim. Both owned VMs remain off during comparison.
- LIVE dev-android-raw-qualification-handoff-5f3891b0.service normal1GiB,
  private raw-qualification-handoff-r30. It requires exact comparison exit0 and
  verified proof, then normally admits frozen reply-vm-r28 at10GiB/root1GiB/
  VM6GiB/QEMU8GiB. Child result propagates; failure never starts a VM.
- r28 readiness uses QEMU guest-agent read-only observations instead of repeated
  PAM logins; records guest CPU/IO/memory pressure and domain counters. Disabled
  agent commands fail explicitly. Real APK authorization still uses the actual
  graphical user manager, not guest-agent execution. Frozen helper preflight
 6c3c5dec PASSED syntax and all manifest bytes; no UI/runtime pass yet.
- Raw disk physical parents are private. r28 uses a temporary single-file bind
  inside the existing owned VM image directory, not broader shared-parent ACLs.
  It retains original XML/disk, checks alias inode and qemu readability, and
  restores XML/unmounts after shutdown; wrapper has a fallback restore path.
  Actual restored evidence still required. Do not modify pinned r28 once live.

HISTORICAL02:42Z — no usable-build or inline-reply qualification claim:
- Corrected runtime95/Shell127 and SDK5 images are installed. r20 ba8fb087
  booted actual user Shell127 with successful native boot health and no bootstrap
  mounts; the owned reply extension did not initialize. No Android reply reached.
  Cleanup restored original extension settings and removed the owned extension.
- r21 c35178cd failed user-bus setup; r22 cb96e24f failed graphical readiness.
  Both preserved as failures. Offline evidence8ee98dd8 PASSED read-only extraction
  with matching guest/host file hashes. Its journals show user-manager and other
  system-service startup timeouts, and virtual CPU clock watchdog warnings;
  neither Android causation nor a RAM bottleneck is established. kvm-clock was
  already selected. VM and builder are OFF during disk-copy work.
- LIVE normal dev-android-runtime-raw-copy-f0197be8.service4GiB/disk60GiB;
  root luma-android-first-class-runtime-raw-copy-r27.service3GiB. One-shot raw
  diagnostic copy on HDD, source original qcow2 retained/unmodified. Conversion
  followed by full qemu-img guest-byte comparison is required. Domain has NOT
  been switched. IMPORTANT: actual backing-chain inventory contains ONE qcow2,
  so earlier commentary calling this a layered-disk comparison was inaccurate.
  It compares raw versus qcow2 (and physical storage paths), not backing layers.
- Private evidence: artifacts/dev-android-offline-runtime-evidence-8ee98dd8;
  artifacts/dev-android-runtime-raw-copy-f0197be8/copy-proof.json. Private drivers
  offline-runtime-evidence-r26 and runtime-raw-copy-r27. Observe exact copy job
  to completion, then perform bounded boot comparison preserving original XML.
- Native reply probe now permits a strictly named directory in Shell's own user
  runtime directory. Syntax job a37c9165 PASSED; no actual GJS/UI/reply claim.
  APKr16, SDK5 image and package hashes remain the previously recorded pins.

HISTORICAL handoffs below retain prior attempts; their LIVE labels are dated
history, not current jobs.

Source/worktree isolated; canonical untouched. Current source is on this task branch; older immutable build sources are named below. Historical implementation497a61054
includes supervised cold-CLI handoff, corrected raw-wire APKr13/native controls,
page-size admission and native clipboard0016. DO NOT BUILD SNAPSHOT901d5f18c
OR luma-project-aidl.squashfs: wrong backend.

LIVE01:42Z — corrected triple staged/booted; current native fixture setup:
- CURRENT dev-android-reply-vm-r20-ba8fb087.service normal10GiB/10GiB disk;
  root luma-android-first-class-reply-vm-r20.service1GiB. Frozen reply-vm-r20
  helpers, same127/95/SDK5/APKr16. Pipeline installing-local-candidate-triple-
  and-image (setup phase). Builder OFF; original watchdog/health checks intact.
- r18 08c5ab7c successfully staged ALL3 local candidates: derived checksum
 810efb5672e9b92a153056d0d058d59de09843181d8a9597f26d62b2a7e101a2.
  Native transaction upgraded nightlyShell124 -> AndroidShell127 and runtime94
  ->95. THEN harness settings JSON parse failed; no reply test. Error preserved.
- r19 e849e30c booted actual127 (bootstrap mounts EMPTY), exact existing triple
  reused. Settings reader now captures stdout/stderr separately and pins Gio2;
  actual diagnostics retained. First95a8f9bb launch failed before VM boot because
  missing manifest after a Python quoting/syntax preflight failure; fixed only
  after actors inactive. Never reported either failed attempt as passed.
- r19 stopped because recovery profile globally disabled extensions. Original
  enabled-list/disabled flag restored and test extension absent per cleanup.json.
  r20 temporarily selects ONLY the owned fixture when globally disabled, then
  enables it; unrelated extensions remain disabled. Finally restores BOTH exact
  original settings and removes only owned fixture. This is test instrumentation,
  not production UI code. No ordinary-user reply pass yet.
- Latest source public task branch a4ed46942 before this handoff. Root source/
  payload hash pins unchanged; no new image/package rebuild. Observe fresh native
  GUI health, original PendingIntent receipt, resize/admission checks and cleanup.

LIVE01:28Z — current matching VM update; prior harness attempts retained:
- CURRENT normal dev-android-reply-vm-r18-08c5ab7c.service10GiB/10GiB disk;
  root luma-android-first-class-reply-vm-r18.service1GiB, VM6GiB/QEMU8. Frozen
  reply-vm-r18 manifest, same verified95/127/imageSDK5/APKr16 candidates. Pipeline
  is installing-local-candidate-triple-and-image. Builder remains OFF. Observe
  this actual child through fresh reboot/tests/cleanup, do not stop at launch.
- r16 f390d264 FAILED01:16:02 because native rpm-ostree Rollback transaction was
  already running; override applied nothing. r17 d5664919 adds bounded180s wait
  for ONLY that rollback, refuses other active transactions. No transaction
  canceled or health policy weakened.
- r17 booted bad124; temporary same-byte/context read-only copies corrected only
  private-library/resource traversal and produced a healthy GDM greeter. Actual
  greenboot Resultsuccess/SubStateexited. However GDM restart left the login
  screen, so old preparation incorrectly waited for logged-in user Wayland.
  No native Android test reached. Gracefully powered off01:24:35UTC; confirmed shut off01:25:22, then stopped obsolete admitted actor.
  Retained result as failed/canceled, never pass; no force stop needed here.
- r18 initial update setup now starts only the existing test user's manager/bus
  as needed for settings/stopping units. It does NOT count this as an active
  graphical session or run APK authorization tests there. Final reboot STILL
  requires real user Wayland, no bootstrap bind mounts, matching RPM triple and
  completed successful native boot-health unit before any reply qualification.
- Root native reply fixture is boot/gate synchronized, no synthetic Shell
  notification/direct Reply RPC. Fresh one exact Android action:reply required,
  then actual resize and native clipboard/task-name controls; all still pending.

LIVE01:14Z — corrected Shell127 fully packaged; coherent VM test active:
- Normal dev-android-shell127-package-be016db1.service PASSED01:06:56.228019Z.
  Fresh full build sourcea270220b0, immutable private127 release/bounded2CPU and
  prior documented source assertions; freshly built Home widget prerequisite,
  verified Figtree inputs. All native %check, packaged resources/fixtures and new
  EVERY-RPM header directory guard passed. Glycin sandbox-enabled compiled
  symbolic test receipt required and passed; no skipped native check.
- Shell main694b36212a18e4bc2759bd47411b459f675365b22f9371ecdd0cc0a9fad0cd95;
  common1922523a46e378bc7b3efbe1bb5f73e59d18c27d71c1c82f6621cfb29a88b513;
  SRPM588b7ec03c9af42c1d9e1d7eb15dab0b074920cca3347405ce364a03eb617573.
  Output rpm-r10/source/build/candidate-shell127/build/packages/gnome-shell.
- CURRENT dev-android-reply-vm-r16-f390d264.service normal10GiB/10GiB disk;
  root luma-android-first-class-reply-vm-r16.service1GiB, owned6GiB/QEMU8 runtime.
  Frozen reply-vm-r16/manifest.json pins helpers and exact95/127 RPM triple.
  Original reset watchdog and boot health checks retained. Builder stays off.
- Bad124 may still boot due trial recovery selection. Bootstrap is explicitly
  nonqualifying: same-byte/context copies of only private Shell library/resource
  subtrees with intended directory permissions, temporary read-only bind mounts
  inside owned VM. No package payload changes or MAC/health-check relaxation.
  These mounts must be absent after fresh corrected127/95 reboot, which must
  also pass actual greenboot health checks. No bootstrap result counts as pass.
- Actual image pair reused only after full SHA verification/reflink; otherwise
  verified streaming transfer. Atomic cache-only triple update accepts ONLY
  known task-owned fallback pending checksums and compares explicit package sets.
- Native UI probe waits at a private boot-specific gate until Android/APK hashes
  are verified and fixture force-stopped; then posts a fresh real notification.
  Real pointer/keyboard, exact one fresh action:reply, then actual resize and
  native clipboard/task-name guard checks. Finally restore original extension
  settings/remove fixture and observe VM shutdown. None passed yet on this set.
- Continue foreground through this child, retain failed runs/independent scopes.
  All ARM, production isolation, physical power and remaining24-gate work stays
  open. Source installer95 fix is packaged but actual Filer install still open.

OBSERVED01:05Z — full126 build/native checks passed; final directory audit FAILED:
- Normal768f4a04 built all Shell126 RPMs and ran the existing full %check,
  including native symbolic-stroke test through bounded same-job host executor;
  native-test-receipts.json status0, Glycin sandbox enabled. Earlier commentary
  that native checks had not completed was preliminary and incorrect.
- New final all-RPM guard correctly rejected /usr/share/doc/gnome-shell mode
 40770. Private-library directories now normalized, but RPM creates doc/license
  directories AFTER %install and inherited default ACLs survived chmod.
  Original failed126 results/RPMs remain unqualified and preserved.
- Canonical spec now BuildRequires acl, clears ONLY staged system-directory
  default ACLs with setfacl -k, then chmod00755. No shared source/build ancestor
  ACL change. Later RPM mkdir obeys umask022. e62e6be4 PASSED real inherited
  setgid/default-ACL and later doc/license creation regression, prior bad-RPM
  rejection, original SRPM patch application and builder syntax. Require full
  package guard and actual ordinary-user graphical boot before claiming fixed.
- Preparing fresh127 candidate from this source, retaining all native checks
  and guard. Runtime95 already built/128checks passed; SDK5 images already
  exported/installed. Actual new coherent native reply still OPEN.

OBSERVED00:59Z — exact directory-mode normalization verified:
- Direct Linux check found GNU chmod0755 preserves directory setgid (2755),
  so canonical recipe now uses00755 to clear it explicitly. Regression78a0f186
  PASSED actual2770 -> exact0755 directories, actual bad-RPM rejection, correct
  original-source patch application and Bash syntax. Earlier3d28a2b1 did not
  exercise the actual chmod semantics; do not treat it as that proof.
- First Shell125 preparation070b84f6 FAILED before compilation: Figtree RPM/SRPM
  manifests passed, but old Home widget build has NO SHA256SUMS. No source or
  font digest mismatch. Prior note of a wrong manifest base path was an initial
  diagnosis; actual issue is absent widget manifest. Rebuild widgets from pinned
  source for the next candidate rather than claim missing input verification.
- New Shell126 preparation will preserve125 failed source tree/artifacts and use
  source with00755 normalization plus all-package header checks. Native compiled
  symbolic-stroke test still required through same bounded host executor with
  normal Glycin sandbox; no test skips or decoder/sandbox replacement.

OBSERVED00:57Z — root cause confirmed; Shell packaging fix/regression passed:
- Read-only log jobs5b2df417 and23c7b0c4 PASSED, VMs powered off afterward.
  Failed candidate boot journals explicitly show greenboot's display-manager
  health check invoking reboot after repeated user/greeter session failures.
  This failure was health-check initiated, not evidence of watchdog expiration.
- FIRST Shell error: /usr/bin/gnome-shell could not open libshell-18.so, exit127.
  Actual candidate124 RPM metadata gives /usr/lib64/gnome-shell and its typelib
  directory mode42770 (setgid2770), denying ordinary-user traversal. RUNPATH is
  correct and the library is present. Root package checks had not caught this.
- Added final %install normalization of static system directories to0755 in
 0000-luma-fedora-spec.patch. Builder now checks directory modes in EVERY main/
  common RPM header with source-owned check-rpm-directory-modes.py. Bad workspace
  ACL/setgid modes must never become installed OS permissions.
- Regression3d28a2b1 PASSED: rejects actual bad124 RPM, applies fixed patch to
  exact original Fedora50.3 SRPM, verifies normalization after install, Bash
  syntax passes. Earlier64370de8/ccf4baf4 failed because fixture extraction
  requested gnome-shell.spec instead of ./gnome-shell.spec; retained, corrected
  to assert extracted source is nonempty. No actual fixed Shell boot yet.
- Runtime95 package0f0a3d09 PASSED00:49:37 with128 checks/0 skipped. Exact
  RPM34713ac778e81bc6befa0999119cfeea90d93fedda6eec76e6dd21644ce9798c;
  SRPM6e2aeaac10fa24c6c03f786e6f89c5b7bbd66d71a1dd692c56c3c215e370122d.
  Source27371d18bd6cd5dc77773e00b9f4c1c5e306f3bd; rpm-r18 receipt/minimal
  archive; output rpm-r10/source/build/candidate-95/build/packages/luma-android-runtime.
  Native graphical install still unqualified. Preparing fresh Shell125 build,
  preserve124 artifacts and full native Glycin sandbox-required check.

OBSERVED00:44Z — automatic fallback confirmed; diagnostic cleanup complete:
- Original d4e9f7f8 FAILED00:40:24 after graphical readiness failure and
  unresponsive graceful shutdown; ExecStopPost stopped ONLY owned VM. No reply
  test executed and no production/new-image native gate closed.
- Diagnostic24919723 PASSED its diagnostic scope: owned watchdog reset action
  temporarily none, original runtime6GiB/QEMU8 inactive XML restored
  exactly afterward with reset action. Guest graphical session reached. Restored
  original extension settings and removed only owned temporary extension; explicit
  extension-cleanup.json confirms both. Graceful shutdown completed.
- Actual boot history shows repeated candidate boots00:31:35,00:33:23,00:35:05;
  diagnostic boot00:41:31 ran original nightly Shell124, NOT reply Shell124.
  Automatic fallback occurred. Do not attribute previous resets to watchdog from
  this experiment: the booted deployment also changed. Need failed-boot journals.
- CURRENT normal dev-android-reply-boot-logs-5b2df417.service10GiB; root
  luma-android-first-class-reply-boot-logs-r14.service1GiB. Original reset
  watchdog retained. Collecting specific failed boot journals and Atomic/boot
  counter status. Observe normal final child and powered-off VM before updates.
- Source installer delegation passes all128 root checks. Preparing distinct
  candidate95 Android20261030 RPM identity; accepted OS pins unchanged. Not built
  or installed yet; current installed.94 lacks installer activation fix.

OBSERVED00:38Z — combined candidate installed; graphical boot gate FAILED:
- d4e9f7f8 prepared local Shell124 main/common override alongside existing.94,
  matched SDK5 pair and retained image backup. Exact package dependencies resolved
  against Mutter50.4/luma14. This proves installation, not an executing Shell UI.
- New boot never met the300s graphical readiness gate. pipeline.json records
  qualification-failed-or-inconclusive/Graphical session readiness deadline.
  NO native reply or new-image runtime qualification pass. Normal/root actor
  still active in shutdown cleanup; observe final child before restarting VM.
- Earlier root SSH uptime82s then later103s several minutes later suggests a
  repeated boot; cause unconfirmed. Guest SSH intermittently refused/timed out,
  last fetched GNOME Shell journal had no entries. Serial reached GDM startup.
  Owned libvirt VM has an itco reset watchdog; evaluate boot history/watchdog
  events, do not assume this caused the reboot or disable health checks to pass.
- Finally's extension cleanup failed while SSH unavailable. Owned test extension
  and original settings record must be restored on next reachable boot; do not
  report cleanup completed. Original settings at d4e9f7f8 artifact and guest
  /var/tmp/luma-android-reply-r12-a65b419f/original-extension-settings.json.
- Independent source root check5750ac25 PASSED128 tests, ZERO skipped in1.794s
  after installer delegation fix7e932221e. .94 installed VM lacks this fix;
  updated runtime package/graphical install remain to build and test.

SOURCE00:31Z — installer activation delegation fixed; VM test unaffected:
- Added installer_activation.py: RequestInstall asks the ordinary user manager
  to launch the fixed graphical installer in its application context. Broker
  NoNewPrivileges/read-only home/private tmp remain intact; the existing package
  helper still performs native authorization. Only display/activation variables
  are forwarded, filename stays one literal argv item, and failed manager launch
  returns an explicit bounded error instead of silently accepting the request.
- b83ac8dd PASSED2 focused tests (literal hostile filename, unique transient
  units, limited environment, failure/timeout reporting). d0bf8867 PASSED128
  discovered checks,16 skipped for root semantics;112 executed. This is source
  component evidence, not a packaged or actual graphical install pass. Current
  SDK5 reply VM remains pinned to installed host.94, which lacks this new fix.
- Reply VM installed local Shell124 pair with existing runtime.94 override;
  exact Mutter50.4/luma14 satisfied dependencies. SDK5 image pair installed,
  original image backup retained. Original extension list preserved plus owned
  test instrumentation. Now rebooting into actual new Shell; no reply pass yet.

REVIEW00:26Z — installer D-Bus activation hardening needs an actual check:
- service.py RequestInstall directly spawns luma-android-installer. That parent
  user service has NoNewPrivileges=yes, PrivateTmp=yes and ProtectHome=read-only;
  child GUI inherits those restrictions, while package transactions invoke
  pkexec. The ordinary CLI passed from the user manager, not this D-Bus route.
  Do not claim this route works from CLI evidence. Exercise actual Filer/installer
  activation and fix delegation to the normal graphical application context as
  needed, preserving broker hardening and native package authorization.
- Rechecked effective Shell124 source: uncertain reply already says Open app /
  Open app to check delivery via0286; no hardcoded Messages label remains in
  the effective candidate. Error-path actual runtime behavior remains open.

OBSERVED00:23Z (Oct30 UTC) — SDK5 COMPILED/EXPORTED; NATIVE UI TEST ACTIVE:
- Actual r12 m systemimage/vendorimage633/633 completed successfully in19:40;
  guest synced output and wrote exit0 at1793318999.528394. Immutable source
 0c0cc1a9f034c7048a7aef1947ac3ef33f34d23c, no preparation overlay.
- Original normal e60f41b6 FAILED00:10:49: root1GiB controller OOM during
  bulk export AFTER guest success. Guest compiler did not OOM. Original failed
  result/partial export retained; do not relabel it passed.
- Dedicated normal dev-android-reply-export-r12-49b74252.service PASSED.
  Bounded4GiB root +4GiB guest/QEMU5 under normal10GiB reservation streamed
 64KiB chunks, synced every8MiB, checked size/SHA before atomic publication,
  then independently read back every artifact. Owned VM powered off and exact
 24GiB/25GiB inactive builder profile restored. Shared policy original restored
 00:10:50; no temporary budget remains active.
- Verified export build-vm/output-x86_64-r12-20261030T001611Z; manifest SHA256
 3fd927d110054b9db0673fbc13772933da819f1cce46421dc2d2db6044de8b1c;
  system d721cbb27b6ee4c477fad225325e547391078acaaea8348bf3e4cff7a0ba9c26,
  vendor2a25f1e1c286b3108ba18a30e53f67fc5861fa17970c547e2bbe108112d05bb8.
  SystemUI99ad1201424eb672fcc1fec81c97dd7e2073e395b79e48c6c6bbb5028e958810.
  Exact tool/source receipt verified, no clean reproducibility claim.
- Public export-stream.py component tests a4bc2d7d PASSED5, including incremental
  sync, size/checksum rejection, non-overwrite/symlinks and deadline. Actual
  dedicated export used the same frozen helper d53936a4d9c0956712ad632d80af68b7ac460cd7f9c48f9633399d4e6cf8303d.
- CURRENT dev-android-reply-vm-r12-d4e9f7f8.service normal10GiB/12GiB disk;
  root luma-android-first-class-reply-vm-r12.service1GiB, owned runtime VM6GiB/
  QEMU8. Private reply-vm-r12 helpers frozen by manifest. Streaming transfer,
  local cache-only Shell124 main+common Atomic replacement, matched SDK5 pair,
  reboot, temporary owned test extension and actual virtual pointer/keyboard.
  Original extension settings restored/removal attempted in finally. APKr16
  exact synthetic reply must arrive once through original PendingIntent; UI
  report alone never qualifies delivery. No actual native UI pass yet.
- Public probe options/boot identity syntax check73145d6f PASSED Node only.
  Continue observing current child through final result/cleanup; do not stop at
  admission. SDK4 native guards/resize evidence remains independent. All ARM,
  full isolation, physical power and broader acceptance gates remain open.

OBSERVED23:58Z — r12 NINJA ACTIVE; PREPARED REAL NATIVE REPLY PROBE:
- Source checks/SDK5 incremental refresh completed23:47Z. Soong graph regenerated
  with actual soong_build using~6cores early; guest available~8GiB at graph peak,
  zero swap. Target lineage_waydroid_x86_64 confirmed. Ninja started~23:55Z,
  PID15330, dependency scanning; no compiler action/error reported yet.
- Prepared public tests/android/notification-reply-native.js for installed
  Shell124. Actual Android source/app identity and negotiated inline reply
  required; opens Luma notification tray, locates mapped live entry/button,
  uses compositor virtual pointer+keyboard to type/click, attempts internal Shell
  screenshot. Does NOT synthesize a notification or call Reply transport directly.
  JSresult ALWAYS leaves android_delivery_qualified=false; outside wrapper must
  verify one fresh exact unique APK action:reply receipt with matched identities.
- 8df213de PASSED Node ES-module syntax ONLY. No installed GJS/native UI/input or
  Android reply claim yet. Private reply-vm-r12/README.txt records setup need;
  actual VM update/test driver still to prepare. Shell124 main/common RPMs
  are2,428,083B/16,746B; strict common dependency must be replaced together via
  local rpm-ostree transaction, reboot to execute new Shell, retain rollback.
- Active normal build e60f41b6 and policy guard e184fa82 unchanged; do not mutate
  their pinned private helper files/inputs. Continue through build/export then
  coherent runtime/Shell/image UI test. All broader gates remain open.

OBSERVED23:46Z — r12 COLD TOOL PREFLIGHT PASSED; SOURCE CHECKS ACTIVE:
- Four immutable inputs verified23:45:13Z; vdd project/vde tools replaced ONLY on
  owned stopped builder, then VM started. Guest current-attempt timestamp
 1793317538.6160452/source0c0cc1a9f, overlays{} and explicit verification2/build8/
  GOMAXPROCS8/GOGC20. Root preboot XML24GiB/QEMU25/8CPU assertions passed.
- Actual new guest env-i PATH=/usr/bin:/usr/local/bin:/bin preflight PASSED:
  Meson1.9.2>=1.4 andglslang16.6.0>=12.2. Same versions also verified by actual
  build entrypoint. No warm/manual glslang addon required. Current log
  `Checking Android source projects (verification jobs=2)...`; Ninja not begun.
- Main normal e60f41b6 and policy guard e184fa82 remain active. No image/reply
  runtime pass; continue through source check/SDK5 build/export/real matching UI.

LIVE23:44Z — REPLY-ENABLED IMAGE BUILD ADMITTED; STAY FOREGROUND:
- CURRENT normal dev-android-reply-image-r12-e60f41b6.service27GiB/450GiB,
  admitted23:40:12Z; root luma-android-first-class-build-controller.service1GiB/
 128swap/CPU200/Runtime21600/TimeoutStop360, guest unchanged24GiB/QEMU25/8CPU/jobs8.
  Hashing57,095,458,816B source input BEFORE VM boot; original inputs retained.
  Root controller reported active1GiB cap (file-cache accounting included), not
  OOM observed. Do not confuse current unit with old22:02 exporter OOM in journal.
- Current immutable source0c0cc1a9f034c7048a7aef1947ac3ef33f34d23c now includes
  SDK0005 replies and public source/Mesa checks. Source SFS70,651,904B SHA
 93501baab2f32578e64357a87cb67f8dc5a1bc8e474a133de51c662571adc780.
  New immutable tools9,302,016B SHA9b19404bfd360b53596f977c4ab753eed969ba5524d0d67b37ecf02bc0ee8ac2;
  old verified Meson bundle plus self-contained launcher468133..., exact glslang
 16.6.0 binary30cdc0... and preserved upstream LICENSE17e70c676e15... .
  Cold guest binds both tools over actual /usr/bin selection; source checker
  sanitizesPYTHONPATH. NO old SDK4 preparation overlay or one-time output repair.
  Guest only publishes completion after sync. New metadata+full export manifest
  verified; bounded SCP16x64KiB/root1GiB. Prior image export/proof stays separate.
- a21ee3a0 PASSED source/tool input preparation. Initial79c90257 failed reading
  qemu-owned old tools input;81e2ddf9 root copy asserted wrong owner(root vsactual
  qemuUID107).60ab7899 exact-hash readable COPY succeeded, original unchanged.
  Initial failed source archive/snapshot retained. No compilation pass yet.
- Private helpers reply-image-r12/{guest,controller,budget,policy-guard}.py and
  run.sh/run-policy-guard.sh, prepared.json/drivers.json pin source/tool/control
  identities. Static syntax, guest destination/launch agreement and preservation
  of global32GiB/host8GiB/disk20GiB reserve checks passed. Interrupt handler tries
  sync/poweroff; actual compiler checkpoint restore remains unqualified.
- POLICY GUARD CURRENT dev-android-reply-policy-e184fa82.service normal1GiB,
  root luma-android-first-class-reply-policy-guard-20261029T234005.service128MiB.
  It raised only shared per-account budget16->32/build10->27 (global32 unchanged),
  waits for registered build e60f41b6 and restores exact original policy in
  finally. Registration/build-admission.json published; policy-ready.json23:40Z.
  Verify policy-restored.json + actual policy hash after build termination.
  Source/AUDIT guard refuses overwriting another owner's policy changes.
- NEXT: inspect root controller journal/current normal result, guest current
  source0c0 startup/tool preflight then actual compiler. Stay through export and
  actual matching .94/Shell124/image/APKr16 native reply UI tests; do not end on
  launch. Latest7c13 guarded/resize-qualified image remains below, not SDK5 pass.

VERIFIED23:32Z — ACTUAL X86 WINDOW AND NATIVE GUARD MILESTONE PASSED:
- dev-android-window-live-2ad2f9cc.service EXIT0 finished23:30:29.634923Z;
  root window actor inactive; owned runtime VM observed SHUT OFF. No ongoing VM
  actor or watcher remains. Same exact system/vendor7c13 pair, host.94 and APKr16
  hashes verified before tests. Source/native provenance remains above; SDK4 only.
- Window PASS: initial349x635, new1242x641 then459x635; two saved/restored activity
  events retained `State survives resizing`; same processPID1541 and responsive
  am-start after resize/maximize/restore. This exercises the old configure deadlock
  path; no freeze observed in this case. Not full phone/tablet/window corpus.
- Screenshot UNAVAILABLE all four attempts: QEMU screendump `no surface`.
  Actual error retained. No rendered pixels, visual smoothness, first-frame or
  physical/performance claim; actual client dimensions/state are the evidence.
- Native PASS run29bf83a3-deab-495d-baec-ca8b35b4ad75: APKUID10128/PID1638,
  both before/after UID1000 controls clipboardandroid-fixture/taskfixture-label
  return0. Ordinary APK task-nameempty, clipboardempty/request-returned/empty.
  Synthetic seeded clipboard unchanged; same Android init3062/start9151 and
  system_server322/start13384. Actual kernel app UID/PID matched receipts.
  Scope ONLY native clipboard read/write and task-name getter admission.
  Focus/remove/resize mutators, platform/property/host-service boundaries open.
- Android SELinuxDisabled STILL productionBLOCK; A15 not closed. Tests do not
  establish identity namespace isolation or test-key/origin protection.
- Private guard-vm-auto/latest.json explicitly records independent passed scope
  with exact normal result/evidence digests; original failed runs unchanged.
  Evidence: artifacts/dev-android-window-live-2ad2f9cc/{pipeline,window-qualification,
  native-guard-result,result}.json andreadiness-output.txt. Prepared private
  check/ready/driver/run/helpers pinned in window-live manifest. No PR/push/release.
- NEXT: prepare current immutable project+verified Meson/glslang tool inputs and
  coherent SDK0005 reply-enabled image; do not reuse old r11 cold driver as-is
  (glslang mount missing, SDK4 preparation overlay, old128MiB bulk exporter).
  Use bounded26/27GiB normal admission with24GiB/QEMU25, adequate controller cap,
  explicit restored shared policy, sync before results/clean shutdown, and
  separate or bounded-SFTP export. Then test image+host.94+Shell124 native reply
  UI. Continue ARM/isolation/hardware/power/rights gates; no daily-use claim.

LIVE23:28Z — WINDOW/NATIVE TESTS CONTINUE, no completed pass:
- 431f4ddf verified booted.94/APKr16/native payloads; initial actual layout349x635,
  PID1555. FAILED23:26:08 at initial virsh screenshot before any resize key.
  Source traceback retained in root window unit journal; old wrapper omitted
  command stdout. No resize regression verdict. No image/UI capture claimed.
- Updated PRIVATE window helper records screenshot unavailability+actual error
  and continues independent client-layout/saved-state/responsive checks; any
  actual resize command/test failure still fails. It then executes corrected
  native gate runner on same verified installed APK/images, avoiding another
  cold boot. Both result files retained; aggregate succeeds ONLY if both pass.
- CURRENT dev-android-window-live-2ad2f9cc.service normal9GiB, root same window
  actor512MiB/CPU200/runtime1800. Same guest6/QEMU8 limits; no other VM actor.
  Own private window-live manifest pins all six helper files. Syntax/bash checks
  passed; actual screenshots/resize/native verdicts pending. Guard auto watchers
  remain stopped; DO NOT arm guard or change window files during current actor.
- Next read window pipeline/window-qualification/native-guard-result and actual
  normal result, fix concrete errors, continue. Canonical source untouched.

LIVE23:24Z — INDEPENDENT ACTUAL WINDOW TEST ACTIVE; gated native release repaired:
- b80cd765 APKr16 installed/launched and reached focused private gate. Both
  before-controls UID1000 nonempty. INCONCLUSIVE23:22:15 at ordinary-apk-probes:
  Android run-as trigger helper returned nonzero before release; no native probe
  verdict. Cause inside run-as not captured. Replaced ONLY marker creation with
  disposable VM administrator touch of fixed private runUUID file; native calls
  still execute in ordinary APK and kernelUID/PID must match. b3d82dcd PASS13.
- Current independent job dev-android-window-live-431f4ddf.service normal9GiB;
  root luma-android-first-class-window-qualification.service512MiB/128swap/
  CPU200/runtime1200, same guest6/QEMU8 profile. Helpers+exact image/APKr16/native
  hashes in private window-live/manifest.json. Readiness and UI resize result
  separate. Executes keyboard resize/maximize/restore, screenshots, actual client
  layout dimension change, synthetic saved-state and responsive reactivation;
  no first-frame/perf/focus/production claim. Syntax checked; actual test pending.
- Guard watcher c93f147f/childb80 stopped before modifying guard sources. Next
  guard manifest668d24bcfa871da55f7cb3d343c1194545829ea869a9bfeb1bfe4d7ed4914489.
  DO NOT launch guard while window VM actor active. Continue foreground through
  window child result and graceful off, then new normal guard run with corrected
  marker. Window test may proceed independently of inconclusive security gate.

OBSERVED23:18Z — actual controls reveal invalidated clipboard seed; gated fixture built:
- e6471513 native report retained full evidence: ordinaryUID10127/PID1698 returned
  clipboardempty/request-returned/empty and taskempty. Before UID1000 controls
  clipboardandroid-fixture/taskfixture-label; after clipboardEMPTY(return1),
  taskfixture-label(return0). VerdictINCONCLUSIVE, no clipboard denial/setter pass.
  RunUUID8abddc0f-1e12-4e9d-b2ea-b91339c027d4; init4415/start15512,
  system_server282/start16065. Focus transition invalidation suspected, not proved.
- APKr16 adds explicit focused-ready/private-file release gate. Runner starts
  ordinary activity, waits matching UID/run focused-ready, THEN seeds before
  controls and releases probes via run-as fixed private trigger. No framework or
  native guard/policy bypass; same ordinary app IPC. Actual behavior not run yet.
- 5f9a2d7b PASS APKr16 Java/D8/build/signature,15,685B SHA
  e681607bc91fb97b8267ef2689360c3ffb78b4632075e01d192727030c606318.
  d95b5066 PASS13 runner+5 stop/Atomic+9 policy+8 inventory/readonly VM checks.
- Private harness now reuses guest images ONLY after exact fullhash comparison;
  cp --reflink=auto into stage and skip reinstall of already exact target pair.
  Actual running native hashes still required. Avoid unnecessary backups/disk
  copies across repeated same-image tests; original raw pair untouched.
- CURRENT observer dev-android-guard-build-handoff-c93f147f.service; manifest
  8a045fc81e8775f888ab300de5f9806587955735db628f5744b924950697df5f.
  Next actual gated APK native controls; then independent resize/native UI test.

OBSERVED23:12Z — actual native probes reached; post-control inconclusive:
- 78667cb4 progressed past installed-path/hash/UID/generation checks. Ordinary
  APK UID10126, Android init4365/start15621, system_server275/start16070.
  Actual before controls BOTH nonempty UID1000: clipboardandroid-fixture and
  taskfixture-label. Native raw APK probes ran; kernel process UID verified.
- INCONCLUSIVE23:10:09 at trusted-after-controls: old helper raised on a
  post-control state mismatch and discarded that state plus completed APK
  receipts. Therefore actual denial/disclosure/write verdict unknown, NOT PASS.
  Android SELinuxDisabled remains productionBLOCK. No inference from HIDL code.
- Runner now records before/after controls incrementally, typed post-control
  mismatches with return code and completed probe/UID/PID receipts before any
  following failure. Wrong trusted UID/service/state schema still rejected.
  96d6a101 PASSED13 runner/path/evidence tests. ef4a4c39 PASSED5+9+8 harness
  checks+readonly source/VM preflight. Window-live/check.py prepared privately
  only (keyboard resize/maximize/state), syntax checked, not executed/qualified.
- CURRENT observer dev-android-guard-build-handoff-a1773142.service; manifest
  8ac68e6d4b2d8c618c7fb31c22eb3dcee408cdc7df6b5ee6a8d3157ca845fbba.
  Continue to actual child/native receipt, diagnose observed after-control state.

OBSERVED23:07Z — native .94 reboot and normal-user APK operations PASSED:
- ef606d67 booted .94 derived deployment f865bb9e...; previous base retained.
  Read-only pkcheck from UID1000 user-manager service returnedYES, no policy
  modifications. Test user alreadywheel. This resolves harness context rejection.
- Same candidate images booted. Resume34.936s; CLI remove8.595s, install12.335s,
  launch4.787s returned0 from normal-user service. Command times only; no first
  frame/performance claim. Actual running HWC/task-service hashes match export.
- Native guard runner INCONCLUSIVE23:05:16 with RuntimeError before android_uid
  recorded; no actual probe pass. Original report lacked message/phase. Found
  installed-path validator excludes Android's randomized `~~` directory form;
  fixed that source issue and added phase/fixed RuntimeError diagnostics. Actual
  old failure cause not proved yet.72233dcb PASSED11 classifier/path tests.
- 9ba8e7fd PASSED5+9+8 helper/read-only checks. Fresh observer
  dev-android-guard-build-handoff-9e95810d.service; manifest
  40745aace2146ca87607091ec343dbb359777554045c8a203cb5be3fa152600b.
  Continue through actual native controls; exact failure phase now recorded.

OBSERVED23:02Z — rebuilt Android boot PASSED; ordinary CLI authorization OPEN:
- e3424e03 installed exact image pair and CLI resume returned0 in58.934s;
  boot_completed=1 confirmed before package query. Android RUNNING independently
  observed22:58Z. No first-frame or performance qualification inferred.
- Failed22:59:15 at normal-user remove of existing synthetic APK: pkexec Not
  authorized; no new APK/native probes executed. Source policy requires active,
  local wheel user. Harness runuser from root SSH may carry remote/root session
  context; exact cause remains to prove. No policy bypass or root APK install.
- Native Atomic replace/apply succeeded again; new derived deployment f865bb9e...
  Cleanup waited60s for graceful poweroff before fallback; VM off23:00:17.
  Next harness allows180s to finish deployment finalization under actual HDD I/O.
- New CLI invocation uses systemd-run --user --wait --pipe normal UID1000 desktop
  user manager, plus read-only pkcheck before large image copies; existing policy
  unchanged.5981acea PASSED5+9+8 harness checks/read-only source+VM preflight.
- CURRENT observer dev-android-guard-build-handoff-b351bf16.service; manifest
  2372ccfed2164e0d4bf2a3e661d8157f35dceb148f71ef14761e946ccff3f646.
  Continue through actual desktop-context auth/APK/native controls; inspect child
  preflight/qualification/errors. New image native binary live hashes/control
  qualification still open. ARM/production/isolation/all acceptance gates remain.

OBSERVED22:51Z — new images installed; qualification sequencing repaired:
- f7901d90 FAILED22:49:46 AFTER exact image installation/target hash check,
  before actual Android boot. Normal-user list returned0 in0.682s but only
  metadata; my harness incorrectly called pm before starting the Android session.
  lxc-attach failed `Failed to get init pid`. No native-control pass inferred.
- New harness invokes normal-user CLI resume and requires actual boot_completed=1
  before package inspection. Graceful sync/poweroff now precedes bounded destroy
  fallback. Exact known pending .94 deployment62d5... may be reused; any other
  pending deployment still rejected.1837f0bf PASSED5+9+8 checks/read-only preflight.
- CURRENT observer dev-android-guard-build-handoff-519c61db.service;
  child dev-android-guard-image-qualification-e3424e03.service passed Atomic
  replacement22:57:22Z and now copying images; guest stage225230Z; manifest
  0bb360b75c6ac9baf562daadb1ea409749b8a52b7bc07f704a58bea2e26f9300.
  Read new child pipeline and continue through Android boot/native controls.

OBSERVED22:49Z — f7901d90 native Atomic runtime replacement PASSED:
- rpm-ostree local cache-only override and apply-live both exit0. Exact .94 RPM
  digest/installed identity verified; before/after package inventory unchanged
  outside luma-android-runtime. Base .80 retained; pending .94 derived deployment
  62d5d9e5b2e5e0e9a399c0f30e5d473964bc47796eb42f7ffac87718611c419d,
  live-replaced same. Host Enforcing. This establishes package installation ONLY.
- Child now copying image pair; first staged system.img has full1,829,220,352B.
  No Android boot/native-controls pass yet. Root qualification finally currently
  destroys VM, so staged OS may not finalize: prefer bounded graceful poweroff
  in next harness revision after current pinned actor stops. Do not mutate live.

LIVE FOLLOW-THROUGH22:44Z — image export remains verified; runtime boot untested:
- d6f47577 FAILED22:34:09 before large upload. Actual RPM--test reported856KB
  needed on immutable `/`, despite writable stage30,110,994,432B free and
  MemAvailable4,332,576KiB. No missing dependency was established. Previous direct
  rpm install failure still has no preserved RPM stdout, so same cause unproved.
- Replaced private qualification harness direct ostree-unlock/rpm-Uvh with native
  rpm-ostree override replace --cache-only LOCAL exact-hashed .94 RPM followed by
  apply-live --allow-replacement, as upstream administrator handbook documents.
  Refuse preexisting pending deployment; compare before/after package inventories
  and require no changes outside runtime. Original OS rollback deployment retained.
  Actual native transaction has NOT passed yet. Preflight JSON/errors retained on
  failure. Guest qualification only asserts the exact preflight+installed identity.
- b69ce5a8 PASSED5 stop/Atomic-guard fixtures,9 policy,8 inventory and readonly
  inactive-VM/source/syntax preflight. These are harness checks, not an OS install.
- CURRENT observer dev-android-guard-build-handoff-0392730f.service, admitted22:43Z;
  manifest e10042f1fe0a9820bcbb7669e811c8331145383a0751967f060f353ea0542e7c.
  Continue foreground through actual child outcome; next read guard-vm-auto/latest,
  child pipeline/preflight/error/qualification. No native guard run has passed yet.

LIVE HANDOFF22:33Z — IMAGE BUILT/VERIFIED; RUNTIME QUALIFICATION STILL OPEN:
- Full Android m systemimage/vendorimage completed successfully at~22:01Z,
  compiler report55:06. Guest packaging and sync completed; raw result exits0,
  finished_unix1793311306.8638635. Root-owned raw result
  build-vm/build-r9-result-20261029T220225Z.json SHA
  df17ffeba44366d80a2bb4f6b49567b84a4b17edc2035f0f0d2cb914ec25aae9.
- Original normal e37c3216 FAILED22:02:33Z during bulk artifact export: separately
  capped root controller128MiB was OOM-killed. Guest24GiB was not the failed cap.
  Guest outputs had been synced BEFORE publishing successful completion, so
  no compilation rerun was needed. Preserve original failure/partial220225Z.
  Shared policy restored automatically to original24cfbd91...; override is OFF.
- Normal dev-android-image-export-recovery-1e0431d5.service PASSED22:08:43Z.
  Dedicated normal7GiB/root1GiB actor temporarily cold-booted builder at4GiB,
  QEMU5GiB hard+combined, bounded SFTP16x64KiB, COPIED completed output only.
  Actual full export checksum verification passed. Guest sync/poweroff observed;
  exact prior24/25GiB inactive builder XML restored by finally and stop hook.
  Backups/helpers in export-recovery; domain-original.xml/SHA root-owned.
  Actor backup already exists: do not blindly rerun one-shot driver.
- VERIFIED export: build-vm/output-x86_64-r9-20261029T220751Z. Raw images,
  system1,829,220,352B SHA88dbed302af8bd933e12c6e098bed9a9870db5f342ee59efaacb71bb30a76d09;
  vendor566,210,560B SHA2a25f1e1c286b3108ba18a30e53f67fc5861fa17970c547e2bbe108112d05bb8.
  Main manifest9899baf62d4f95be95cc380c6c6d0e90d84be8163ad4396e838e3fae16dee5de.
  HWC60e4bc0a34cfcff9b9937ea553db946e1714141e890cfff4ea7946af44754e81;
  task5a6fdb7734bdf8f2f89830984ea96ff9819ac73d662635deb3e3da7ad49c4041.
  SystemUI551952bb9cee20aba677ad35372cf17059c46fff8efb251d2f1e5d170eb946df;
  frameworkf4f69537c7f0438f4fa17f46a1cd2d72ac1e689b9c7915639f09a0a076de7996.
  Host-tool metadata22d0f6... below remains required. No source/ABI/image test claim
  beyond source7c13c192f/SDK0004 (no replies). ARM not built/qualified.
- build-vm-auto/latest.json now EXPLICIT image_export_verified_pending_qualification
  pointing to successful EXPORT recovery normal1e0431d5, retaining original
  e37 failure identity and original successful guest result hash. Original job
  result never rewritten. Guard watch accepts this separate phase only after
  actual successful normal recovery result, original compile0 and exact export
  manifest match. Controls66f1274c PASSED9 policy+8 root inventory checks+VM
  preflight. No automatic compiler watcher remains active; e40f42bb terminated.
- First guard childf0bca862 FAILED22:12:35Z copying system.img, scp255; failed
  subprocess output was not preserved by old wrapper. No native controls ran.
  Kernel OOM not established for this transfer; do not equate it with root
  exporter OOM. Root128MiB cap was expanded to512MiB within normal9GiB/QEMU8;
  bounded SCP buffers and60s keepalive, old runtime stop and storage preflight
  now occur BEFORE uploading images; errors are retained.6dffbf91 PASSED3
  stop-state fixtures+9 policy+8 root inventory+readonly VM/source preflight.
- Guard child8beee6cf then staged images successfully. Actual preflight stock
  runtime.80, both user units loaded/stopped, LXC STOPPED, MemAvailable~4.5GiB,
 32,497,623,040B free vs5,864,603,648B required. Therefore no storage shortage
  was established. It FAILED22:24:29Z at rpm-Uvh .94 AFTER transient ostree
  unlock, before images installed/Android boot/APK/native tests. Old guest
  exception omitted RPM stdout: underlying transaction error still unknown.
  Artifacts8beee6cf/guest-output.txt andqualification.json retain exact step.
- New harness fixes: guest exception records full bounded transaction output;
  pre-transfer RPM--test uses PRIVATE COPIED installed RPM DB (real DB unchanged)
  to fail before large transfer; APK filename now unique under/var/tmp, outside
  root-only stage (my earlier stage-local filename would have been unreadable
  by normal user; it had not executed). Ordinary CLI list/remove/install/launch
  still required; fixture cleanup uses actual pm package identity, not display
  text.74d238fa PASSED3 stop fixtures+9 policy+8 root inventory+read-only preflight.
- CURRENT observer dev-android-guard-build-handoff-27c1e556.service admitted
 22:32:10Z, waiting/running a fresh child. Guard manifest
  f2f60c2d6ac96eb630208bbe6fec541af10313ac6f95727d9ead02c76e62c898.
  NEXT: read latest.json/child pipeline and preserved RPM-test output, fix actual
  package prerequisites WITHOUT bypassing dependency checks, continue same run
  through real image boot/APK/native controls. Original raw image pair untouched.
- Future main builds: do NOT reuse128MiB root bulk exporter; separate compile
  and bounded export stages (the7GiB/1GiB transfer path is proven), or validate
  a larger controller within normal reservation. Cold r11 also does not reapply
  glslang addon; see notes below. Do not mutate pinned sources of live actors.

HISTORICAL ACTUAL PROGRESS21:44:32Z:
7511/9913 remaining Ninja actions (75%).
The repaired unstripped libart.so is valid x86_64 ELF (observed21:24:30Z),
buildID85b88bd9fe02d64f563ebd3e1295a245. BOTH actual main-build Mesa32/64
.build.timestamp markers fresh21:28:10Z; these are created only after configure
AND nested compilation succeed. So the Meson/glslang fixes passed actual Mesa
builds, beyond earlier separate configure diagnostics. Main image remains
in framework/Java packages, no completed/exported image yet. Source/R8 warnings
have not stopped subsequent actions; no test/runtime claim. Guardf7f6a184 still
armed for real post-build matched-image native controls. Continue foreground
follow-through; do not end merely because the services remain active.

CURRENT BUILD: normal dev-android-images-build-vm-r9-e37c3216.service admitted
20:50:11Z,26GiB/450GiB, same24GiB/25GiB/8CPU/8job VM. Input verification
passed20:55:41..43Z, VM boot20:55:44Z;
source checks finished21:03:54Z. Full graph regeneration completed by21:19;
actual Ninja-j8 MainPID21453. At21:21:51Z1044/9913 remaining actions completed,
C++/Rust processes active and both actual Mesa configurations running. No image. Recovery observer
 dev-android-image-auto-highram-watch-0-e40f42bb.service; guard handoff
 dev-android-guard-build-handoff-f7f6a184.service. Live verifier
 dev-android-highram-live-verification-0fe08472.service PASSED20:57:26Z with
NEW attempt timestamp,4 operational overlay hashes, actual24GiB/8CPU/8jobs,
25GiB QEMU hard+combined and Meson1.9.2 under env-i. Actual repair completed
20:57:24Z:915 logged outputs preserved in quarantine; both required ART outputs
selected. Preserved unstripped ART header is zero bytes, not ELF; cause remains
unproven beyond confirmed prior unclean VM teardown.
Source manifest b0477f2e2dcda81c9ffee0257104da925e9ac5e10cf4a09b410c165bcb35e4fc.
Guard manifest a93c31066447a14353065d433597f8e3203331cf43a6eff85a2b79f2e7a78b7b
(the former6ac681f2 manifest is superseded).
Project7c13c192f/native patch set unchanged; still no SDK0005 reply image.


HOST SHADER TOOL ADD-ON / REAL MESA CONFIGURATION:
- First separate actual Mesa diagnostic63e09483 ran21:04..05:41: both32/64
  configurations selected Meson1.9.2 but FAILED glslang minimum12.2; Ubuntu
  binary11.8. Diagnostic job exited0 because its scope was collecting both
  results. This is NOT a Mesa pass. Prior Meson-only preflight was insufficient.
- Official Khronos16.6.0 Linux x86_64 archive normal0bdad353 downloaded/verified
  against GitHub API published SHA2c34071f56ecf39d16233294d3739cf43f981fd39080f2fd9dccb782101191ce,
  7,923,164B. Binary8,274,896B SHA30cdc0e1a09ccfe7164c4b9d3d82bcae6baf759bcd101b053ef087b5c82fade6.
  ABI inspection requires GLIBC2.34/GLIBCXX3.4.29, compatible with guest.
- Normal c120a553 PASSED21:14:15Z. Guest compiler unit frozen briefly BEFORE
  primary image Ninja (asserted against fresh log), binary SHA verified, root
  /root/luma-glslang-16.6.0 mounted readonly over guest-rootfs/usr/bin/glslangValidator.
  Four input disks unchanged. Empty-environment version16.6.0 and actual
  synthetic Vulkan1.2 vertex->SPIR-V magic check passed. Finally thaw executed;
  FreezerState=running independently observed21:14:31Z. Main build never rebooted.
- c120a553 then configured actual retained Mesa/cross files in SEPARATE temporary
  output directories: BOTH obj64/445 targets and obj_x86/436 targets exit0.
  Actual Meson1.9.2/Ninja1.10.1, target Clang14.0.6, host GCC11.4.0.
  Configuration only; not Mesa compilation, final image, renderer or hardware pass.
- Guest export includes root-created BUILD-HOST-TOOLS.json, SHA
  22d0f6e5a534b01e9852a97bff47f27e6fe57042a4ef9df786a9bee80121eea9.
  Metadata captures exact archive/binary/hash, version, phase and smoke check.
  Its image-root SHA manifest will include it. Updated export inventory REQUIRES
  this exact metadata/event/hash; it no longer accepts only the base4 overlays.
  1e7da539 PASSED8 root inventory cases including unrecorded tool change refusal,
  plus readonly inactive test-VM/source preflight. Current guard manifest above.
- Waiting guard observer2340ddfb, then4ce9ddd0 were INTENTIONALLY stopped to
  reallocate only this task's1GiB for diagnostics/add-on while another4GiB job
  occupied the global budget. Compiler/recovery observer stayed active.
  f7f6a184 rearmed21:14:53Z after controls; it is waiting on correct current unit.
- Public preflight now checks Mesa's actual glslang declaration as well as Meson;
  067a0554 PASSED4 version/environment fixtures, Bash syntax and diff checks.
  This public helper update is for the NEXT project snapshot; current7c13
  preparer overlay remains the previously recorded4 hashes, plus additive tool
  metadata above. Do not mutate pinned live r11/recovery sources.
- COLD-BOOT LIMIT: r11 does NOT automatically reapply the new glslang bind.
  /root/luma-glslang-16.6.0 persists but its mount does not. Before another cold
  build, prepare a validated next driver/tools input with this pin, or a fresh
  recorded pre-Ninja activation. Existing add-on installer is one-shot and
  refuses an already-existing export receipt. Do not blindly rerun it. A saved
  whole-VM checkpoint should retain current mounts; root receipt/disk/source
  checks still apply. No production reproducibility claim for this incremental
  development candidate; full clean maintained-baseline builds remain A22.
- Host cgroup read21:18:39/21:19:51: QEMU ancestor memory.max25GiB; global
  machine.slice cpu.max800000/100000 (shared across VMs), nr_throttled6830
  unchanged across samples. Guest15GiB available/no swap at21:19:51. No hidden
  CPU bottleneck established and no shared cap was changed.

Prior fdd0d35d FAILED20:44:19Z after48/9187 remaining actions, not a new89%.
Source checks finished20:34:28Z; actual Ninja-j8 observed20:37. Dependency file
out/soong/build.ninja11GiB, process mapped RSS temporarily13GiB, then~6GiB;
16GiB available/no swap at20:41. The prior 961d8dd3 failed20:08:50Z at
75677/84730 (89%) from Mesa selecting Meson0.61.2 while requiring>=1.4.0.

Root cause of FAILED first tool recovery: binding the bundle launcher did not
make its Python import self-contained. Preflight retained PYTHONPATH and saw
Meson1.9.2, while Soong's sanitized Ninja environment imported system0.61.2.
Do not describe d9b1a4be preflight as proof of actual Mesa compilation.
New production preflight removes PYTHONPATH; guest operational launcher inserts
verified/immutable bundle module directory itself. New root check runs env-i.
Also observed retained ART unstripped88,707,664-byte libart.so classified as data,
stripped95-byte output classified HFS resource, causing real link failures.
Underlying cause of invalid contents not proven. Prior controller's terminal
virsh-destroy without guest sync is confirmed; new boot journal marked unclean.
Guest sync d3750127 PASSED20:44:05Z before fdd teardown. It cannot restore
already invalid prior artifacts.

New r11 guest preserves logged recent outputs since20:06:28Z (120s before the
first failed compiler result) in out/.luma-quarantine-unflushed-20261029T200849Z,
requiring both known invalid ART targets in its plan. Owner/path/scope guards,
durable plan and one-time completion prevent silently reusing/repeatedly
invalidating new work. Regenerate selected targets from source. Preparation
scheduling/error-propagation overlay remains exact c85ed1d81 functions against
7c13's native patch set; source checks not skipped. Four overlay source hashes
are recorded in result and required by export inventory. Actual repair pending.
Compiler completion now runs guest sync before writing/fsyncing result; normal
root terminal success/failure flushes and powers off, waits<=90s, then returns.
The old ExecStopPost destroy remains last-resort cleanup on abnormal controller
exit; no claim that cancellation/host loss is a clean shutdown. New checkpoints
remain tied to operational guest-driver SHA.

Checks c33364fd PASSED2 production preflight fixtures/Bash syntax/diff checks,
including PYTHONPATH removal. 1fe855c3 PASSED3 real-filesystem repair fixtures,
3 production shutdown-function dependency fixtures,9 watcher cases,4 root
budget cases (actual parent-directory symlink alias/reapply/refusal),7 root
export cases and readonly inactive runtime VM/source preflight. These are not
actual repair, clean terminal shutdown, image or runtime qualification.
665da51d PASSED20:50:07Z: exact restored original shared policy matched root
backup before reapplying26GiB; global32/host8/disk20 reserves unchanged.

FOLLOW-THROUGH: user explicitly objected to repeated turn endings at background
launch. Watchers record terminal failure and hand successful image to tests;
they DO NOT wake a reasoning agent or repair compiler failures. No available
standalone automation/wakeup tool was found; do not promise unconfigured future
notifications/repairs. Stay with current run through the next actual compiler/
image/test outcome and investigate failures in the same active run. An active
unit, fixtures, or a past preflight is not evidence of compiler progress.

User explicitly authorized a larger allocation when spare RAM is available.
At change time server had~34GiB available; only this builder plus three1GiB
admitted observers/server were reserved. Host8GiB reserve, combined32GiB job
budget and every20GiB disk reserve remain unchanged. TEMPORARY SHARED policy
override: build maximum26GiB/account budget32GiB, with the same GLOBAL32GiB
cap; worker/server/container/package maxima remain10/2/4/4. The second tooling
directory is a symlink to /srv/dev/tooling: both account aliases see this override,
not separate policy files. Initial inode/hardlink interpretation was incorrect;
actual post-change hashes resolved that. Build-kind default is now26 while the
override is active: request smaller jobs explicitly. Original exact
policy is root-owned build-highram/policy-original.json. New recovery watcher
restores original bytes after a terminal build result, including failure, but
retains this allocation across exit75 checkpoints. Refuse overwriting a policy
changed by another owner. If observer is lost, restore with normally admitted
root-capped build-highram/budget.py restore after stopping this builder.

Old normala1c4e3af/root compiler stopped intentionally17:19Z for this resize.
Native build reached59947/143205,41%; ~30GiB out tree retained. Guest compiler
was stopped/synced and guest powered off cleanly. A root-owned XFS reflink copy
of the47,742,976,000-byte qcow2 and original XML are under build-highram; the
copy is NOT fully hash-verified. First migration13c60323 failed after shutdown:
cp rejected incompatible reflink/sparse flags before creating any copy. Finish
6a85c4c1 PASSED17:20:23Z using reflink alone, then verified inactive24/25GiB and
8vCPU/800% settings. This is a cold incremental restart, not VM-heap restoration.
Runtime VM remains off; its6GiB/QEMU8GiB profile is unchanged.

REPLY SOURCE CANDIDATE (coherent source, matched runtime NOT qualified):
- Host notification_reply.py/transport.py/dbus.py plus bridge/session integration;
  SDK0005 plus NotificationReplyDispatcher.java; native Shell0286 extends0025 UI.
  Runtime candidate.94; accepted OS/Shell package pins unchanged. Current image
  source7c13c192f and installed .92 do not contain these changes.
-45 notification component tests PASSED c767e61914:56:17Z.
  Production dispatch closure tested with credential/PID fixtures and explicit Binder
  dependencies; no actual Binder claim. Includes8-worker C-userdata lifetime,
  canonical request/token/text validation, generation revocation and deduplication.
-Private reply endpoint878dc0ef PASSED14:38:38Z: separate real D-Bus connections,
  Shell/foreign caller admission, replacements, owner loss and uncertain results.
  Native-capability owner-epoch test3ecfc401 PASSED14:58:32Z: real private bus
  + fixture server/listener; forwarder restart preserves Shell-held IDs/tokens;
  Shell loss clears grants. Legacy d2f97939 PASSED15:02:56Z. No actual renderer.
-Shell97558b26 PASSED14:51:01Z: exact verified Fedora50.3 source + spec-selected
  patch list through0285, new0286 and updated spec apply. Production reply method
  executed in Node with D-Bus fixtures: existing Messages/new Android fixed path
  and interface routing, caller shape, tokens and cross-app/path denial.
  Not a GJS/runtime/rendering test. Private effective-r4/ receipt retained.
-Java latest4feddc80 PASSED15:03:07Z: pinned AIDL compiler confirms service1..3
  and callback1..3 retained, new service4/5 and callback4. Generated AIDL and
  production dispatcher compile against actual API33. Production helper JVM
  fixtures test original RemoteInput payload, UID policy, text limits, receipts,
  replacement/removal/reset, immutable/auth/multiple-input fallback, cancelled
  queued work and a running send whose ACK arrives after the5s wait. Late receipt
  reconciles without another send. Not Android IPC or a full SDK/image build.
-Android callback checks captured host UID policy BEFORE AIDL unmarshalling.
  Native input is the original mutable PendingIntent, one free-form RemoteInput;
  unsupported shapes never become an empty-reply ordinary button. No new image
  yet. Host service5 requires posting kernel PID match a live registered listener.
-Host uses one strongly retained gbinder.Client per reply until C destroy,
  including timed-out workers; global8 calls. libgbinder cancellation does not
  stop the worker.15s host deadline /30s Shell deadline; uncertain is not retried.
  Reply tokens bind post/listener/generation; same-connection endpoint requires
  current Shell unique owner. Records retain hashes/receipts, no message text.
-Candidate.93 clean RPM c06af05e PASSED15:05:09Z:124 tests/0 skips/failures/
 errors, imports/desktop/payload/profile755 checks. Binarycb1f98535c45c163cc24b190d524a4a133118ff5c0c7fe4d3fe9ce48c805e306,
 SRPMe0ba8094d11631b6edc757d4a0bcee29eae4d9ad3f6bf3a27df0e76b42ab8fbc,
 under rpm-r10/source/build/candidate-93/build/packages/luma-android-runtime.
 DO NOT use .93 for reply qualification: later wire audit found two bugs hidden
 by inaccurate fixtures. Android Parcel.writeByte writes int32, gbinder.read_byte
 advances1; appended reply_action misaligns. RemoteReply.read_int32 convenience
 methods each start at0; use one init_reader cursor for header+ACK.
-New realistic byte/cursor fixtures against preserved .93 failed as expected:
 fe3bccf1,2 assertion failures+1 alignment error at15:10:45Z. Correctedsource
 d07356fa PASSED47 component tests15:11:18Z. Candidate.94 full126-test RPM PASSED f468a1a6 at15:12:50Z; see below.
-New reply project SFS ed446c6f PASSED15:08:27Z, sourceb22ed165d,
 build-vm/luma-project-native-reply.squashfs70,615,040B,
 SHAe6ed231917685db4321cb91d57ce249e1c333831af0fe370080c472aea7d7b02.
 Android SDK0005 is unchanged by host-only .94 fixes; prefer latest host RPM.
 Snapshot is NOT attached to current builder; never replace its input mid-build.
-Native Shell candidate normalb4b733eb FAILED15:10:22Z in existing%check
 st/test-symbolic-strokes: icon pixbuf loader/theme warning aborts. All290 native
 build steps reached%check; no Shell RPM pass. Both actual Figtree/widgets
 prerequisites built first. Private candidate-shell124 derivedb22ed165d changes
 only release1.luma.124.android20261029.1 (acceptedpinsunchanged), bounded2jobs,
 and two obsolete package assertions40->45 /16->18 matching existing0284 and
 independently confirmed Surface task correction. No checks skipped. Source
 receipt shell-r124/source-receipt.json. Later retry resolved the build environment; see below.
-TODO: actual GJS/native UI and Binder/RemoteInput receipt in
  a matched image/Shell pair; source-level full SDK image build; physical tests.
  Update the next image snapshot after current native-guard image finishes or
  is safely saved. Never replace its immutable project disk mid-build.

LATEST QUALIFIED CANDIDATE ARTIFACTS / ACTIVE CONTINUATION:
-.94 f468a1a6 PASSED15:12:50Z, source497a61054:126 tests/0 skips/failures/errors,
 imports/desktop/payload/profile755. Binary3ba654b17f14cbee15f0c15c0acb851a7d024fd8c5a31a1a6f4c41d5aa0e1292,
 SRPM71b10726c6967e46338e94b61193ee33d237cfa228b3cb31c5f77eca921875e7,
 rpm-r10/source/build/candidate-94/build/packages/luma-android-runtime.
 .94 NOT installed/live runtime tested; .92 remains last live lifecycle pass.
-Native Shell124 retry a62273fe PASSED15:22:30Z; full ordinary rpmbuild + all
 packaged checks (no short-circuit or skipped test). The existing symbolic SVG
 test could not start Glycin sandbox inside rootless container. Diagnostic
55ad7743 reproduced it with a fixed SVG; rsvg-pixbuf-loader is now a virtual
 provide, not a separate native decoder (61b77658 correctly failed its query).
 The exact compiled test passed natively under normal worker1GiB f8cb544b;
 actual package%check then used a bounded private file-mailbox executor inside
 the SAME normal package4GiB job, executing the exact compiled test/library on
 native Fedora with Glycin sandbox enabled. No alternate decoder, sandbox/
 policy disable, omitted check, host package install or production change.
 Test exit0 and executable/library SHA before/after in
 shell-r124/native-test-receipts.json; private source adaptations in
 native-executor-source-receipt.json. The candidate SRPM includes the test
 helper; absent the optional mailbox it executes normally inside the builder.
 Sourceb22ed165d plus private release124/2-job/assertion corrections already
 recorded. Accepted OS/Shell pins unchanged, no publication or device install.
 ShellRPMf1eaec320bc57cde0b3c8ec15fd71c1cc52dfc55863ab245236b6d354a715e17,
 commonf74a8a452ae2654bc31655feae9ee65d1ab10729252a374bfaaedecd69904626,
 SRPMb9d823e1d842c6df1b2fd7f228902185037b384d2c8564c83582c509282df478,
 rpm-r10/source/build/candidate-shell124/build/packages/gnome-shell.
-Packaged reply resources3a23e032 PASSED15:26:17Z: actual ELF GResource bytes
 equal prepared effective-r4 sources and production method/syntax checks pass.
 Firste6311d1f failed because host lacks gresource CLI; corrected by parsing
 ELF resource sections + actual Gio.Resource lookup, no package installed.
 Receipt shell-r124/packaged-reply-receipt.json. Not installed GJS/native UI.
-Main image now uses root r11, normal961d8dd3 build26GiB, source7c13c192f.
 Former8GiB attempt reached41%; deliberate clean restart preserves disk outputs.
-Automatic bounded continuation ACTIVE:
 dev-android-image-auto-highram-watch-0-eca8b2c8.service, normal1GiB,
 MainPID611446; private build-vm-auto/watch-v3.py watches exact961d8dd3 result.
 Original66408768 watcher intentionally stopped for migration. Only exit75 with
 a fresh root-owned complete/unconsumed own-UUID/source checkpoint can resume.
 Each child is separately admitted kind build26GiB/450GiB with VM24GiB/QEMU25GiB
 and root128/256 limits. No retry of compiler failures/cancellation; at most4
 resumes. Each watcher5.5h bounded, overall deadline2026-10-30T15:30:00Z.
 Resource refusal waits at most30m, then stops. STOP file build-vm-auto/STOP
 prevents NEW continuations; stop active root controller before its normal job.
 Source manifest build-vm-auto/source-manifest-v3.json SHA
 8a71203e48f8039ad5d392915c5971d1fee7fb5801cec2ee300dd7372dc19696.
 Seven operational scripts, including guest/budget helper, are pinned at startup
 and before resume. This guards accidental edits, not a hostile owner. Previous
 v2 scripts/manifests remain historical. Future resume wrapper is
 build-highram/run-r11.sh; root6h deadline and bounded guest timer rearming remain.
-Terminal v3 outcomes attempt original shared-policy restoration before
 publishing completion/failure. A restoration conflict fails visibly; it never
 overwrites another owner's policy. Policy/backup details above. Post-build VM
 qualification is separately owned by the success-only handoff below.
-Resume prerequisite15b20427 FAILED safely: guest249 rejects RuntimeMaxSec via
 set-property; original compiler deadline stayed20500s. Replacement actual guest
 timer controlac3445e7 PASSED15:35:03Z: baseline stopped3s, drop-in/reload timer
 survived4s and stopped8s. Compiler MainPID and original deadline unchanged;
 synthetic unit/drop-in removed. Root checkpoint inventory returns null now.
 Policy093cb68b passed cancellation/failure/success/no-resume and fresh/unconsumed
 source/UUID selection fixtures. Actual compiler save/restore/r10 automation is
 NOT yet exercised; original synthetic KVM save/restore result remains below.
 New wrapper syntax and embedded Python/newline bytes checked before launch.
-User ETA given as rough24–48h for an x86 Dell development build if compilation/
 integration succeed, NOT a delivery promise or daily-use/ARM/power/security ETA.

POST-BUILD GUARD VM HANDOFF ACTIVE:
- Normal dev-android-guard-build-handoff-eea5005f.service,1GiB, MainPID614946;
  restarted after the deliberate migration; actual waiting heartbeat17:21:48Z in guard-vm-auto/latest.json. No runtime VM
  started while builder runs. This extends build monitoring into actual tests;
  it does not automatically implement remaining brokers or qualify a release.
- Private guard-vm-auto/watch.py waits for successful final normal build result
  and inactive build unit, follows bounded resume units, and stops on build/
  recovery failures, cancellation, exhausted budget or deadline. Wait deadline
 2026-10-30T15:30Z. Normal admission refusal retries at most30min, without
  changing limits. Each stage records structured status; no blind failure retry.
- Then a separately admitted normal9GiB/20GiB-HDD job boots ONLY owned runtime
  VM3c485515-a910-4f44-80a0-4189ec58cab8, guest6GiB/QEMU8GiB combined. Root
  driver128MiB+128swap, CPU200%, IO25, RuntimeMax2400s, owner watchdog and
  ExecStopPost own-UUID destruction; observer waits at most2700s for that child.
- Inventory verifies a fresh root-owned successful7c13c192f export, safe manifest
  paths, complete image/native payloads and every listed file hash. Staged files
  are rehashed in the VM before changes. It stops the old runtime, installs host
  .94 in a disposable /usr overlay, retains the old image pair through the source
  deploy helper, cold-starts the runtime, replaces only the synthetic fixture
  with APKr15, verifies boot/native payload hashes and runs actual native guards.
  Results/logs are retained under that admitted child's artifact directory.
- Passing status is native_guards_passed_pending_other_gates, with production
  qualification and notification reply qualification explicitly false. Current
  image7c13c192f lacks SDK0005; Shell124 is not installed by this narrow guard run.
  Focus/task mutators/hardware/security/performance and reply pair remain open.
  Both success/failure destroy only the owned disposable VM, retaining its disk.
- Pins guard-vm-auto/manifest.json SHA
  fb45a3d4d6f9574cb689cc4a84f33ec8dabbbfff435bea8891cc08359b547a89.
  Do not change pinned scripts/artifacts under a live observer. STOP file
  guard-vm-auto/STOP or build-vm-auto/STOP prevents a NEW qualification job.
  Once admitted, stop root luma-android-first-class-guard-qualification.service
  before stopping its normal job; stopping its observer alone does not kill it.
- Policy4780fe1b PASSED7 tests; root export controls243c45ad PASSED6 actual
  root-owned fixture cases plus read-only inactive-VM limits/source/syntax
  preflight16:31:50Z. Altered/symlink/non-root-owned/escaping/wrong-source/failed
  exports reject. These do NOT claim the new boot/install/qualification sequence
  has executed; its first actual run waits for a completed compiled image.

FAILED BUILD: normal dev-android-images-build-vm-r8-3a3af2ff.service,
10GiB/450GiB HDD; root luma-android-first-class-build-controller.service;
VM809f0871-fd88-4770-83dd-624c90259c06/IP192.168.122.36. Started09:01:21Z,
startup passed09:06:41Z. Project luma-project-native-io.squashfs70,574,080B,
SHA8b8ef91de1bfcb394a2b10b8325155cce958cd18195a4e0db72aad1120b18317,
source3ff01af63 includes0016. Builder8GiB RAM/QEMU9GiB hard, 24GiB disk swap
plus4GiB virtual zram with1GiB compressed cap, priority100. Source/out overlay
upper-native-v1 reused. At13:35Z soong_build active~3h50m, RSS6.35GiB, total
guest swap10.8GiB; severe memory pressure remains. No completed image exists. Build guest was destroyed by controller13:52:51Z
after a30s SSH poll timeout during swap relocation. Heap work was NOT saved.
Do NOT repeat swapoff on a memory-starved live compiler. Disk artifacts retained.
Next startup needs compressed swap configured adequately BEFORE compilation,
transient transport retry within bounded actor lifetime, and early state saving.
Guest startup verifies zram cap via mm_stat[3] (mem_limit is write-only).
Root controller watches normal owner; ExecStopPost destroys only this VM.
Deliberate cancellation: stop root controller before normal job.

r6 was deliberately frozen/synced/powered off08:46Z to take latest0016 and8GiB
profile; durable r6-checkpoint.json/logs retained. No process-heap resume claim.
r7 failed before compilation08:56Z due reading write-only zram mem_limit;
no result claimed. r8 fixes startup. Immutable input verification remains intact.

COMPLETED RUNTIME (OFF since14:07:08Z): normal dev-android-runtime-full-r9-5706d8ea.service,
9GiB/10GiB HDD; root luma-android-first-class-runtime-controller.service,
VM3c485515-a910-4f44-80a0-4189ec58cab8/IP192.168.122.35. Guest6GiB RAM/
QEMU8GiB hard AND combined RAM+swap, root128MiB+128swap, actual guest MemTotal
5844MiB. Original restored XML had no swap_hard_limit, so first0484888f refused
BEFORE BOOT. Retry5706d8ea added ONLY combined cap while inactive; backup
vm/domain-before-full-bounded-r8.xml SHA b3f5031d32bf43a5ed2c0241f1e4dec6a1369be53ea25f36ed8ddbba82c37fe5,
current vm/domain-full-bounded-r8.xml SHA
5cfc05320de373e991c6f0ac143356876dd6b14a7d222f5d31abbc02d81427ca.
Original6GiB RAM preserved. Stop root controller before normal job when done.
No compiler runs concurrently. Runtime needs normal9GiB; builder10GiB cannot
coexist in account16GiB. Private helper stages run separately under normal1GiB.

.92 INSTALLED in disposable /usr overlay13:59:45Z, normal c6175059 PASSED,
profiles755/import transaction completed. Earlier4585a133 failed waiting for
SSH during early boot before mutation; bounded readiness retry corrected it.
Initial cold39333e14 failed BEFORE launch because reset-failed named an unloaded
unit. Retry28da51d2 removed that unnecessary setup command and PASSED14:02:20Z:
packaged.92 after broker/session stop and root container-service restart,
CLI launch exited0 in25.2808s, Android boot_completed1, MainPID6155 alive in
luma-android-session.service cgroup after caller exit. This is full6GiB guest,
old Android image, not first-frame/physical performance or proof of the cause
of earlier3GiB failures. Evidence runtime-cold92-full/result-r2.json.
Warm fixture14 normal de38a382 PASSED14:03:36Z: normal CLI remove4.660s,
install8.103s, launch3.172s (command completion only). APK14 persists disk.
Native screenshot native-task-live/fixture14-full.png shows rendered app with
native titlebar; NOT timing evidence. New task button at(640,398), native
clipboard now(640,344), copy remains(640,452); recapture before future clicks.

REAL TASK HAL NEGATIVE e5fe4bee PASSED AS PROBE14:05:07Z: ordinary Android
UID10135 raw transaction4 returned fixed fixture label. UID1000 trusted raw
control returned known label before AND after; endpoint not unavailable.
Actual installed APKsha916d4214440eaeb55fb25852eb2dd657cf91ea72bb97d59136c8358d7b7a4755
checked inside Android. Evidence native-task-live/result.json. Old-image name
access is NOT isolated; no actual task mutation tested.0017 must deny ordinary
reads while preserving trusted labels and normal native window controls.

CURRENT BUILDER INPUT: project luma-project-native-task.squashfs
70,582,272B SHA849a73b0388eb90c5d3297deece9f3e37a2e42d45e47cf0bb9552f679d205bdd,
commit7c13c192faacdcaa32170fef1e68322c22e4a8d5, snapshot normal3d91c58f PASSED.
Private build-vm-driver/{controller-r9.py,guest-build-r9.py,run-r9.sh} syntax
passed. Same normal10GiB/guest8GiB/QEMU9GiB fixed budget. Guest configures zstd
zram24GiB virtual/4GiB compressed cap BEFORE compilation, keeps disk swap24GiB,
uses GOGC5/GOMAXPROCS2; no live swapoff. Controller retries up to6 transient SSH
failures and saves VM RAM/disk identity before4.5h or repeated transport failures.
One-shot restore verifies immutable inputs, disk bytes, XML, RAM-state SHA and
marks consumed before restore. Restore driver not yet run against actual builder.
Synthetic KVM control b826926d PASSED14:04:08Z: exact counter+marker unchanged
across save/restore-paused, counter advances after resume. VM256MiB/QEMU512MiB
combined under normal1GiB + root128/128, no network/data. SELinuxEnforcing retained;
fixture domain destroyed/undefined. checkpoint-control/result.json. This tests
KVM save semantics, NOT Android/compiler recovery or the full r9 controller.

.92 clean RPM r15/164b9abc PASSED08:47:48Z:91 actual tests, zero skips/failures/
errors, payload/import/desktop/profile755 checks. Source3ff01af63. BinarySHA
8d22a623638dea6d318ec86a794a97413e999e9fadc821f5ab546a6490207fae,
SRPM94c22d4acf8083bc10956f1964ec513057e5b9b8345b8c0a89293e6e668187f0,
rpm-r10/source/build/candidate-92/build/packages/luma-android-runtime.
.92 installed/live lifecycle tested as recorded above. Full91 source tests passed08:07:06Z.
.91 clean RPM cb8f4f4a passed84 tests, installed07:34:04Z in disposable overlay.

Runtime cold managed-source92 test FAILED deadline08:13Z; MainPID13327 stayed
in separate session-service cgroup after caller exit (lifetime boundary only).
Android boot_completed1 by08:22:26Z; warm client FAILED response timeout08:25Z.
No reliable cold/warm startup claim. Captured composer/SurfaceFlinger normal
wait stacks and Python3.14 GLib loops do not establish root cause or rule out
native callbacks. Py-spy actual version0.4.2, receipt vm/pyspy/receipt.json.
Harmless sleeper cgroup control confirmed setsid insufficient and managed unit
survives caller exit. Runtime stopped/root config restored after evidence capture.

New Bionic compile check743a43b2 PASSED pinned AndroidClang14.0.6 r450784d:
exact production clipboard helper compiled with actual Bionic/libc++ headers
for ARM64(machine183) andx86_64(62), no host headers. Compile-only, not linking,
execution, HwBinder or hardware qualification. Report under account artifacts
/dev-android-bionic-clipboard-compile-743a43b2/bionic-clipboard-compile.json.
Script and README await source commit.

NEW AUDIT/FIXES (13:50Z):0017 adds actual HwBinder UID1000 admission to all4
native task methods before framework lookup, successful HIDL Void/one denied
empty getter callback. Replaces shared mutable sp caches with per-call local
checkService proxies; no automatic mutation replay, no stale cached framework
proxy across SystemServer replacement. Internal native resize relies on existing
Unix SO_PEERCRED admission, not HIDL caller state. Actual composer rc user system
confirmed. No ordinary-APK task exploit has been measured yet.
64 production-method cases + replacement/missing registry/failure/concurrency
PASSED ASan/UBSan485feeb9 and TSan f6cc0fc9, unpatched negative42.0018 rejects
oversized packet prefixes/nonzero reserved fields and bounds accepted resize
socket receive/send250ms. Real SOCK_SEQPACKET/SO_PEERCRED tests with private
user namespaces mapped to1000/10000 PASSED a746000a, original oversize control42.
No host account changes. Framework Binder work remains unbounded by socket limits.
Full pinned patch series through0018 + configure-lock/linkage controls PASSED
14f48ee7. Both builders and incremental patch-state hashes include0017/18.
Current immutable image build remains0016; subsequent image required for fixes.
Individual HAL checks do not replace outstanding Android MAC/userns isolation.

APKr14 read-only raw task HIDL name probe BUILT/SIGNED13:48:21Z, normal2e75410f,
252MiB peak, private user+network namespaces, pinned JDK17/tools, clean env/Xmx192m.
SHA916d4214440eaeb55fb25852eb2dd657cf91ea72bb97d59136c8358d7b7a4755.
No live execution yet. Earlier95acd51d failed unshare network without userns;
no compiler ran. Trusted UID1000 read control shares raw wire request; expects
nonempty fixed fixture label. Ordinary unavailable/other-label cannot pass.
Read-only probe does NOT establish denial of destructive task mutations.
New task button shifts all following UI coordinates; recapture before clicks.

Builder-only tuning: normal87d3720f PASSED13:43:02Z adds zram1 virtual16GiB/
compressed-memory cap3GiB, lz4, priority110. Existing4GiB/1GiB zram and24GiB disk
retained, guest8GiB/QEMU9GiB/normal10GiB unchanged. zram-expand/receipt.json.
Normal a2ca00dd FAILED13:52:47Z (350s SSH timeout): bounded300s swapoff attempt relocates disk pages
to new compressed capacity, finally re-enables disk fallback if off. Root128MiB/
420s, log/receipt under zram-expand. Normal d7f080e0 PASSED13:49:20Z changes only
guest vm.page-cluster3->6 for swap readahead; swap-readahead/receipt.json.
The bounded swapoff did not return a receipt before SSH transport failed; do not
claim it was fully bounded or that disk fallback re-enable executed. VM is OFF.
At13:49Z soong_build still4h04 elapsed/29m32CPU, guest disk swap~6.77GiB,
zram1~313MiB original/78MiB memory. No measured throughput improvement claimed.

CRITICAL REAL NATIVE BASELINE: APKr12 could not load private vendor interface
as an app (no registered org.lineageos.platform shared library); unavailable is
not denial. Its UID1000 app_process control DID read the expected token after
using /init.environ.rc plus /data/system/environ/classpath whitelist. New APKr13
uses raw HIDL1.0 HwBinder/HwParcel (wire1 setter/2 getter), no private shared-lib
permission and no hidden-API policy change. Actual Android UID10134 read the
Android token and replaced it with fixed forbidden-write token; UID1000 control
confirmed Android token before and forbidden token after. Exact evidence
vm/native-clipboard-r13-old-image-negative.json. Probe reaches the real HAL;
old image is NOT isolated/qualified. Actual guard0015 image must now deny read/
write with the same nonempty trusted controls and preserve normal ClipboardManager.
APKr13 SHA f99ed9e69ded19e2d497ddfcee20e0cdcc6185f5c0a1cc1c97da914e83afbf8a.
Built offline07:57:21..26Z,275MiB peak, source JDK17/verified tools asUID1003 under
384MiB root cap within existing6GiB VM reservation (VM5GiB hard+controllers fit).
No Podman/package-runner bypass; unshare network, setpriv no-new-privileges,
explicit clean compiler env/Xmx192m, normal owner job remained active.

.91 root install finished07:34:04Z; first ordinary fixture remove failed after
123.8s waiting for cold Android. Explicit supervised session then boot_completed1
at07:41:54Z. APK12 normal retry remove/install/launch11.83/12.55/3.92s succeeded;
APK13 later20.27/22.68/10.01s under graph I/O, not first-frame/performance evidence.
The real harmless-sleeper user-systemd control08:04:34Z proved Popen setsid child
is killed with its transient parent and separate managed child service survives.
Current source uses existing user session unit for packaged detached cold launches;
manager failure cannot spawn caller-owned fallback. Actual cold source test FAILED after182s with unit still active/MainPID13327
in its service cgroup. Android reported boot_completed1 by08:22:26Z; later warm
client still timed out. Native SF/composer stacks were captured; composer Wayland
thread in normal dispatch, not evidence of a clipboard deadlock. Py-spy0.5.x
metadata under vm/pyspy/receipt.json (use actual receipt version) dumped Python
3.14 session idle in GLib loops; root-side stack next. No cold/warm restart pass.

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

Upstream native read_selection blocked the event loop and shared its cache
without synchronization;6118eec17 fixes these source paths. Next real-image
acceptance uses APKr12's raw HIDL controls with known nonempty trusted reads.
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

2026-10-29T07:35Z runtime/page-size progress:
- Owned runtime r7/1da16b36 active under6GiB, root watchdog observes normal owner.
  .91 transaction finished07:34:04Z, replacing.80 in disposable overlay; profiles755.
  An early read-only RPM query during transaction reported lock contention and
  both versions, not a completion result. Only terminal driver result accepted.
- New .92 kernel-page preflight candidate passes5 source tests in own x86 VM;
  full package89-case result not yet run. Surface source selects4KiB; primary
  AOSP docs confirm Android15 introduced16KiB support, which is not evidence
  that our older Android13 images or64KiB host kernels are compatible.

2026-10-29T08:12Z native baseline and lifecycle progress:
- Build r6 passed source checks and compiled bootstrap; real soong_build graph
  now paging inside7GiB guest+24GiB internal swap, source snapshot59aecadc4 still
  lacks0016. No matched image ready. Root/controller normal ownership intact.
- Raw APK13 ordinaryUID10134 confirmed missing native-HIDL ACL in old image with
  nonempty trusted before/after tokens. Corrected probe is executable and catches
  the real gap; prior APK12 unavailable result was never accepted as denial.
- Source suite91 passes including cold handoff manager failure/no-child tests;
  full package .92 pending. Live managed-cold92 source test still active.

2026-10-29T08:41Z source milestone / remaining gates:
- APK13 raw-HIDL negative control passed as a probe08:01:25Z: ordinaryUID10134
  read Android fixture and wrote forbidden fixture, UID1000 before/after agreed.
  It proves the older image is vulnerable, never a security pass. New0015/0016
  image still required. Source tool native-clipboard-control.py now reproduces
  the fixed read-only trusted control with required nonempty --expect.
- Current source91 tests passed08:07Z. Real cold client failed182s deadline; its
  supervised unit survived. Android completed later, but warm command timed out.
  These old-image restart failures remain explicit release gates.
- Guest-only zram setup succeeded08:27:39Z: official Ubuntu kernel extras
  5.15.0-198.208 SHA43528923613a77891402e3b722729e7a866ac24f76b087b63a02cdd7ae56134d,
  wireless-regdb dependency SHA0c8f9893f577c890b3dd6d1f2b90a564b1627879dd58b2bf3ce17434d5ca17d0.
  Download hashes checked against signed apt metadata; no host swap/limits changed.
  lz4 zram4GiB virtual/1GiB compressed-memory cap, priority100; original24GiB disk
  swap preserved. Receipt build-vm/zram-receipt.json. First setup guard stopped on
  the small dependency; second hit Python3.10's missing hashlib.file_digest before
  install; corrected streaming hash, third succeeded. At08:39Z ~640MiB original
  data occupied~142MiB compressed,179MiB peak; no image/performance pass implied.
- r6 soong_build graph is IO bound (~12m CPU/~46m elapsed, RSS~6GiB plus disk swap).
  Plan next: save root runtime stack, stop owned runtime/restore XML; commit source,
  clean-package .92 and pack latest0016 project snapshot. Checkpoint/stop r6 if
  still incomplete, then use8GiB guest RAM within EXISTING9GiB hard/10GiB normal
  reservation and guest zram from startup, preserving source/upper/output. No
  hard-cap, host memory reserve or host swap increase. Current r6 is STILL ACTIVE
  with7GiB guest and source59aecadc4; do not assume that plan already ran.

2026-10-29T15:12Z reply candidate milestone:
- Sourceb22ed165d committed host/Android/Shell reply path; source/API/private-bus
  checks pass with the exact limits above. .93 RPM124 tests passed but subsequent
  byte/cursor negative controls exposed wire bugs; .94 correction is pending
  commit/package and no .93 reply runtime pass exists. Native Shell build failed
  existing icon-loader%check; preserving the failure, diagnosing prerequisites.
- Main image r9 remains active source7c13c192f, graph CPU progressing with guest
  zstd compression, no completed system/vendor images. No hardware/release claim.

2026-10-29T15:38Z package/recovery milestone:
- .94 runtime126 tests and native Shell124 full packaged checks passed, with
  actual packaged reply-resource verification. Installed native reply/Binder/
  APK text receipt and hardware gates remain open; no release/push/deployment.
- Current image7c13c192f reached actual compiler10781/143205,7%. Bounded normal
  checkpoint continuation watcher74758108 active; details and stop path above.
  It never converts checkpoint/failure into an image pass or installs outputs.

2026-10-29T15:58Z acceptance/recovery audit:
- Added the24-gate current acceptance audit above. No component, old-image,
  fixture or compiler result closes whole-system acceptance. Android MAC/UID
  isolation is a known production blocker. Current guard image7c13c192f does
  not contain SDK0005 replies, so its completion cannot qualify the new reply UI.
- Found last-resume observer gap in private watcher. New watch-v2.py always
  admits an observer for the last child, with zero further retries. Atomic
  per-job records + task-wide build-vm-auto/latest.json retain terminal failure,
  cancellation, deadline, admission refusal and budget exhaustion. Heartbeat60s.
  Successful build is explicitly image_built_pending_qualification, never ready.
- Policy95b0ee0b PASSED8 tests15:53:44Z. First3c510deb failed because its
  temporary fixture omitted build-vm-auto parent; corrected setup, not assertions.
  Tests cover actual final observer admission branch, terminal cases, no resume
  after failure/success/cancellation, and atomic unqualified records.
- Live systemd controls50d42f95 PASSED15:55:22Z: separately admitted synthetic
  workers exited23/0; real observer processes recorded build_failed/
  image_built_pending_qualification and exit1/0 respectively. Tests used a
  separate state directory, no VM, Android image or restore claim.
- Source-pin extension17ffac45 PASSED9 tests16:00:03Z, including rejecting a
  modified synthetic controller. Startup of the actual pinned observer verified
  its manifest and published that hash in the live heartbeat.
- Old watchers74758108 and d1ad8be9 intentionally stopped; pinned watcher
  dev-android-image-auto-pinned-watch-0-66408768.service active under normal1GiB.
  Root controller r9/image normala1c4e3af uninterrupted. Same maximum4 resumes,
 24h outer deadline, unchanged reservations/caps and STOP path. Every observer
  failure remains a failed normal job with structured status; no automatic code
  repair or image installation/publication. Actual compiler restore still open.
- Corrected design document's stale cold-launch statement to include qualified
  old-image .92/full6GiB command observations while preserving prior failures.

2026-10-29T16:07Z clipboard focus audit:
- Traced pinned native keyboard leave handler: releases pressed keys but does
  not revoke clipboard access. Existing0015/0016 do not implement focus policy.
- Primary xdg-shell1.31 protocol explicitly says activated decorations do not
  guarantee keyboard/pointer focus. An activated-state-only guard would not
  establish clipboard privacy. Logical seat/window focus must be attested by
  the compositor for desktop and touch-only profiles; preserve existing Android
  copies for native pastes while revoking new background reads/writes.
- Corrected native-focus-fixture.py: records native-window-activation/active,
  source Gtk.Window:is-active and input_focus_attested=false. It no longer emits
  misleading focused fields. Old private r11 runner/receipts remain historical;
  they must not be reused as compositor-focus proof or run against the changed
  schema without adapting qualification. The old result remains inconclusive.
- Updated design and acceptance instructions with this distinction, focus-loss
  races, generation changes and copy-after-switch controls. Normal syntax job
  a60a1149 PASSED16:07:24Z; no GTK/compositor/runtime execution claim. Runtime VM
  stays off while current guard image compiles. Builder19%,27842/143205 at
 16:07:33Z; pinned observer and original build still active. No focus gate closed.

2026-10-29T16:25Z native guard qualification preparation:
- New source-owned native-guard-qualification.py runs actual ordinary-APK raw
  native clipboard read/write and task-name probes with UID1000 nonempty controls
  before/after. Exact installed APK hash, kernel app UID/PID, unique run UUID
  and unchanged init/system_server generations prevent stale/unavailable evidence
  from passing. Scope excludes focus, task mutators and complete image/isolation
  qualification. Its result always retains production_qualified=false.
- Fixture can launch both native probes without coordinate clicks. Receipts now
  include UID/PID and canonical guard_run UUID. A trusted-only fixed synthetic
  clipboard seed avoids destructive or arbitrary clipboard controls; all use is
  confined to the disposable test profile.
- Evidence-classification e433a76d PASSED8 cases: expected denial, observed old
  disclosure/write, missing/empty/unavailable controls, stale UUID or wrong
  process, unavailable IPC, duplicate/missing receipts and isolated task-name
  leakage. This is classification evidence, not actual rebuilt-image IPC.
- APKr15 normal60701700 PASSED build/signature verification. SHA256
  e54edd6f88584354cd472a50fe9fc879b87d9a9054b48cef7e0cde725db8b680,
 14893 bytes; apk-r15/build.json records exact source and verified tools. Not
  installed or executed yet. Original APK14 evidence remains unchanged.
- User correctly identified that build monitoring alone does not perform the
  subsequent qualification. Preparing a bounded success-only handoff into the
  owned disposable runtime VM under separate normal9GiB admission. Not active
  or claimed qualified until that driver and its controls are validated.


2026-10-29T17:22Z owner-authorized build capacity increase:
- User explicitly authorized using idle server memory. Verified x86_64 native
  KVM host/guest/Android target (secondaryx86), not an emulated ARM build. Host
  has16 logical CPUs/8 physical cores and~34GiB available before the resize.
- Increased the admitted build profile as detailed above; no shared runner code,
  global32GiB budget or host/disk reserve change. Policy is shared through a
  tooling-directory symlink, so both account aliases see the temporary account32/
  build26 values. Initial private-policy assumption corrected by actual hash/path
  checks; original policy restoration remains automatic. Other1GiB server preserved. Actual helper c9fb1537 PASSED9
  watcher tests +3 root policy-fixture cases, including hardlink separation,
  exact restoration and rejecting another owner's intervening policy change.
  The hardlink fixture did not model the real parent-directory symlink; do not
  claim per-account policy separation from that test.
- Intentionally stopped old recovery66408768 and VM handoff99f51328 before
  stopping the compiler, then resumed with the same immutable project/disk
  outputs. Migration failure and correction retained; no full output reset.
- New admitted normal961d8dd3/controller-r11, source-pin observereca8b2c8 and
  post-build handoffeea5005f are active. Waiting for input SHA verification,
  actual24GiB boot, eight-job compiler activity and new throughput observation.
  Do not report a speedup merely from the larger configured allocation.

- Actual larger-builder verification26f58230 PASSED17:26:26Z: guest MemTotal
 25,195,495,424 bytes/8 CPUs, current started marker jobs8/source7c13c192f,
  native KVM XML RAM24GiB/QEMU25GiB hard+combined/CPU800%. Normal26GiB
  admission remains active; runtime VM stays off. This proves startup/settings,
  not eight simultaneous compiler processes, build completion or a speedup.
  Evidence artifacts/dev-android-highram-live-verification-26f58230/live-verification.json.

2026-10-29T18:15Z actual post-resize progress diagnosis:
- User asked whether done. Actual compiler log was still empty at18:05 and no
  Ninja process existed: unit active/startup settings did NOT mean C++ work had
  resumed. Worker had~23GiB available, no swap activity, and heavy source-disk
  waits. Exact parent was maintained prep dirty_projects -> repo forall -> eight
  concurrent git status checks across the full compressed source checkout.
  LLVM check alone read~17GiB over~25min. Full-source checks after remount are a
  distinct bottleneck; do not report a new compile percentage from service state.
- Read-only core.checkStat/minimal + trustctime=false experiment5660e2cb timed
  out30s and was NOT adopted. No persisted stat/ignore relaxation.
- Scheduling-only probea5c7ddc5 paused seven status processes with exact PID/start
  checks, then restored them in finally/alarm paths. Oldest LLVM process finished
  during1s; causality/speedup NOT established. Boundedcc4d54ce caps status tasks
  at2 for at most230s, then releases every original process. No source/index
  mutations or checks skipped. Its completion receipt still pending at18:14Z.
- Actual724de78d PASSED18:12:36Z: builder-only global core.preloadIndex=false
  verified, original config retained in guest/root, owner1003 preserved. This
  affects scheduling for subsequent Git processes, not Android sources or the
  immutable project disk. No controller/source pins were changed live.
- Canonical future preparation now uses bounded verification jobs default2 and
  Git index preloading disabled. Added explicit progress messages and fixed the
  false-clean error path: Git/repo failures propagate through all four initial,
  incremental and destructive-reset callers. Git failure must never authorize
  reset. Current immutable7c13c192f project does NOT include this source change.
- Actual Bash/Git fixture058c36fc PASSED6 checks; repeat73579ac3 PASSED6 after
  making the preserved broken-function control independent of Git HEAD/history.
  Tracked/untracked/delete checks, failed Git/repo, explicit job bounds and all
  call sites covered. Repo enumeration is an explicit fixture, not a full repo
  Android execution claim. Need next source snapshot to carry this fix.
- Build961d8dd3, source-pin observereca8b2c8 and post-build handoffeea5005f remain
  active. No completed image and no actual new-image VM test yet; last completed
  C++ work remains59947/143205 before the deliberate resize. Do not claim done.
- Boundedcc4d54ce scheduling control completed18:15:08Z after230.86s; every
  original status task released. No paused probe processes remain. Source
  checks were still active at18:15:32Z; no measured end-to-end speedup or new
  compile progress is established by that completed probe.
