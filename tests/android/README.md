# Android integration qualification

The source-owned APK fixture is `org.projectluma.androidtest`. It contains DEX
without native libraries, so the same artifact can exercise x86_64 and ARM64.
It is a debuggable test app, not a bundled user application. It requests
notification permission, no Internet or privileged permissions, and stores only
its own synthetic events.

Build in a disposable JDK environment with `aapt2`, D8/R8 and an Android public
API JAR. `config/android/acceptance-tools.json` pins the official source URLs,
decoded byte sizes and SHA256 hashes. `scripts/android/build-acceptance-apk.py`
verifies them against that repository lock, compiles the source and signs with
an ephemeral test-only key. Build output contains an APK and a source/tool hash
receipt. A fresh build has a different key; remove the previous fixture before
installing another build. Keep generated APKs and keys outside the repository.

Example inside that isolated environment:

```sh
python3 scripts/android/fetch-acceptance-tools.py /tools
python3 scripts/android/build-acceptance-apk.py --tools /tools --output /output
bash tests/android/run-host-caller-policy.sh
```

Download tools before disabling network access for compilation. The Java policy
check executes the production admission helper against 100 host/caller UID
combinations, including application, isolated and secondary-profile UIDs. It
does not substitute for an unprivileged APK test against the complete framework.

The UI exercises text/state retention during resize, Android notifications with
default, Mark read and RemoteInput actions, and the Android Storage Access
Framework's open/edit/write flow. The latter tests an actual host document bridge
only when the selected provider is a host-backed provider; Android's internal
document picker alone is not host-storage integration. Private `events.jsonl`
receipts distinguish notification dispatch, action receipt and saved state.
The fixture's Reply action exercises the RemoteInput path. Require the exact
synthetic text in its receipt; an action click without delivered text is not a
reply pass. The rebuilt Android image, host runtime and native Shell reply patch
must all be installed before claiming an end-to-end pass.

The `background_notification` launch extra schedules a local alarm 30 seconds
ahead and removes the activity's task. Verify its scheduled/fired/posted receipts,
the absence of app windows, an unfrozen runtime and actual host notification
traffic. The fixture targets API 28 for this exact-alarm test. Passing it does
not qualify network push, host suspend/wakeup or physical battery behavior.

`dbus-run-session -- python3 tests/android/notification-dbus.py` exercises the
production host transport over a real private D-Bus connection and a separate
notification server process. Only its Binder listener is a fixture. It checks
identity, plain-text escaping, activation tokens, dismissal versus expiration,
producer withdrawal, server restart and stale replacement. Its JSON result
explicitly does not claim Android, compositor, ARM or hardware qualification.

In a disposable graphical test session, `python3 tests/android/notification-observer.py --output
/private/path/fixture-notify.jsonl --seconds 180` can record the fixture's actual
outgoing notification metadata. It records desktop identity, replacement and
action IDs, without message text or other apps' notifications. This is a passive
bounded monitor, not proof that an action reached Android; confirm that using
the fixture's private receipt after an actual native notification action.

Run component and repository unit checks separately:

```sh
PYTHONPATH=src/luma-android python3 -m unittest discover -s src/luma-android/tests
PYTHONPATH=src/luma-platform/appkit python3 -m unittest discover -s tests/unit -p test_luma_android.py
```

Record the actual executed/skipped counts. Sixteen initialization checks need
root ownership semantics; the non-root run cannot substitute for them.

`python3 tests/android/mesa-host-tools.py` checks the production Mesa preflight,
including rejection of Meson 0.61.2 against Mesa's declared minimum 1.4.0 and
the upstream `/usr/bin:/usr/local/bin` PATH precedence. The image builder runs
the check before source preparation or compilation; a newer Meson elsewhere
on PATH does not satisfy it when Mesa selects an older system copy. These are
dependency-selection fixtures, not a graphics or image build qualification.

`python3 tests/android/source-preparation-cleanliness.py` executes the production
Bash source-cleanliness functions with real Git repositories and an explicit
repo-enumeration fixture. Tracked edits, deletions, untracked files, Git failures
and repo failures must prevent preparation/reset. The old false-clean Git-error
behavior is retained as a negative control. Source verification defaults to two
projects at a time (`LUMA_ANDROID_VERIFY_JOBS=1..8`), independently of compiler
parallelism, with Git index preloading disabled to reduce compressed-filesystem
contention. This changes scheduling, while preserving file checks. It does not
replace a full Android source or image qualification run.

For system acceptance, retain the exact OS/runtime/image/APK hashes and CPU/GPU
configuration. Measure command latency separately from first presented frame;
count PSS instead of adding shared RSS; record allocated disk blocks rather than
sparse image size. Test native matching-CPU apps before optional translation.
VM CPU or memory samples cannot establish physical battery drain, hardware
capture quality, touch/stylus latency or suspend/push reliability.

The read-only hardware admission check runs inside a disposable VM using
Android's own Binder client. After confirming the container is running and
unfrozen, run `python3 tests/android/hardware-binder-probe.py --uid 0 --expect
control`, then repeat with the fixture's actual Android application UID and
`--expect denied`. The fixed unknown operation has no hardware side effects;
endpoint absence, empty replies and generic transport failures do not pass.
Also trigger the fixture's `probe_boundary` extra or its boundary button and
verify its own Java SecurityException receipt. This checks the privileged host
hardware endpoint separately from Android's platform service.

Before applying the property-writer patch to a Waydroid-prepared source tree,
run `python3 tests/android/property-writer-admission.py /path/to/system/core`.
It compiles the actual patched permission function with permissive policy
fixtures, tests application/isolated identities across Android users, and
requires the unpatched negative control to fail. The source tree stays intact.
This is not a complete init/image test; retain an ordinary-APK test plus native
service positive controls on the rebuilt image before qualification.

The same read-only probe accepts `--service clipboard` or `--service usermonitor`.
Their positive control uses Android system UID1000; ordinary APK UIDs must be
rejected before parsing. The APK also probes unknown operations on both endpoints,
without reading/writing clipboard contents or creating package events.

`scripts/android/acceptance-desktop.sh` runs in an existing disposable graphical
account with the source fixture, without replacing the compositor or modifying
the user manager's display environment. It removes only the fixed synthetic
fixture package. It collects functional observations but returns failure when
Android mandatory-access or user-namespace identity gates are absent. Source
presence and a Binder-ready log are never notification-delivery proof; native
notification/fixture receipts are separate. Mapping samples do not substitute
for a complete sandbox audit. Never use a personal user profile as this fixture.

The fixture boundary button/extra also attempts only the volatile
`luma.test.property_boundary=fixture-app` write from its ordinary app identity.
For property acceptance, initialize that key as an Android native service to a
synthetic control value, run the fixture probe, verify its rejection and the
unchanged native readback, then clear the key. A failed process launch or timeout
is unavailable, not a property-permission pass. Never probe power/control keys.

`python3 tests/android/blueprint-go-budget.py /path/to/build/blueprint
/path/to/prebuilts/go/linux-x86/bin/go` compiles the actual Blueprint concurrency
prelude with the pinned Go compiler. It requires the patched explicit budget and
unchanged default behavior, and rejects the upstream override as a negative
control. Run before the maintained builder applies the patch to that source tree.

In the disposable VM, `sudo python3 tests/android/measure-runtime.py --seconds
30 --interval 2 --output /root/android-runtime-sample.jsonl` records bounded
read-only cgroup/PSS samples. It resolves the actual Android init cgroup and
checks process/runtime generations. Output is a new private file; existing
files and symlinks are refused. PSS counts shared pages proportionally, while
memory.current includes page cache/kernel memory. Unreadable/changing processes
are reported as incomplete observations. Only the synthetic fixture gets app
attribution; no command arguments or app data are recorded. This measures
neither first presented frame nor physical battery power. Actual VM execution
is a separate acceptance gate; compilation of this tool is not a metric result.

`python3 tests/android/image-host-tools.py /path/to/build/make` tests the actual
host-selection Make fragment. The explicit runtime-image flag removes unused
Windows host-tool variants while retaining upstream defaults without the flag.
The full builder must also prove its generated Soong configuration retains
Android's target/secondary architectures and ABIs; this fragment test alone
does not qualify an image or application compatibility.

`python3 tests/android/clipboard-client.py --source-root /path/to/android`
applies the SDK reconnect patch to an independent temporary copy and executes
the production HIDL wrapper with explicit dependency/transport fixtures. It tests
late registration, read/write failure and concurrent proxy replacement. These
host JVM checks do not qualify actual Android HwBinder IPC.

`python3 tests/android/native-clipboard-admission.py /path/to/hardware/waydroid`
compiles the exact patched native HIDL method bodies with dependency fixtures.
It checks32 read/write cases, including unchanged data/native calls for ordinary
UIDs, and requires the unpatched negative control to fail. HIDL's getter callback
must be called exactly once and the transport result must remain successful even
when data is denied. Real nonempty trusted and ordinary-APK HAL controls remain
required on rebuilt images. The Python/AIDL clipboard probe covers a legacy
endpoint and cannot qualify this native path.

`clipboard-transfer.cpp`, compiled with C++17, pthreads and the include directory
`patches/android-hardware-waydroid/native`, executes the actual transfer/cache
helper. It tests a real pipe round trip larger than pipe capacity, independent
send progress during a stalled receive, UTF-8/size limits, cancellation/deadlines,
SIGPIPE handling, bounded descriptor ownership, concurrent submissions/shutdown
and stale cache completion. Clang ASan/UBSan can be enabled with
`-fsanitize=address,undefined -fno-omit-frame-pointer`.

`python3 tests/android/native-clipboard-transfer.py /path/to/hardware/waydroid
--sanitize --compiler clang++` applies0015/0016 to an independent copy and compiles
the complete clipboard translation unit, substituting only platform includes
with explicit HIDL/Wayland dependency fixtures. Its pipes and worker are real.
It checks nonblocking selection dispatch, immutable source data, ordinary caller
denial, bounded rapid-copy snapshots and late HAL calls after joined teardown.
It requires the executable's completion receipt and preserves its source/binary
under DEV_ARTIFACT_DIR when set. This is not an Android or Wayland-server IPC test.

`python3 tests/android/bionic-clipboard-compile.py /path/to/android --output
/path/to/artifacts` compiles the production transfer helper for ARM64 and x86_64
using the pinned Clang and actual Bionic/libc++ headers, excluding host headers.
It instantiates the worker/cache and checks the resulting64-bit ELF machine IDs.
The report records commands, compiler version and hashes. This is compile-only;
it does not qualify linking, Android IPC, execution or physical ARM hardware.

The source APK's **Probe native clipboard HAL** button exercises raw HIDL from an
ordinary app through the HIDL1.0 wire ABI and reflected HwBinder/HwParcel methods.
Hidden-API restrictions or unavailable transport leave this test inconclusive;
never count those as a native UID denial. The image's platform jar is not a
registered application shared library. A UID1000 `app_process` invocation of
`org.projectluma.androidtest.NativeClipboardProbe read` provides a fixed read-only
native control. `native-clipboard-control.py --expect android-fixture` runs that
control inside a disposable VM, using only whitelisted public init/classpath
exports and the explicit platform jar. It requires a nonempty match. Require a
recognized nonempty fixture before and
after the APK's empty reads/no-op write; unavailable or empty trusted controls
cannot pass. A returned setter request indicates transport success, not admission.

`native-guard-qualification.py --apk-sha256 EXACT_FIXTURE_SHA256 --output
/private/new-result.json` automates that narrow native admission check as root
inside the already running disposable VM. Stage `native-clipboard-control.py`
beside it and install the current fixture first. It verifies the installed APK
hash, ordinary kernel UID/PID and runtime generation, seeds only the fixed
synthetic Android clipboard token through a UID1000 control, and requires
nonempty clipboard/task-name controls before and after actual APK probes.
Unique `guard_run` UUIDs keep old receipts from passing a new run. The fixture's
`probe_native_clipboard` and `probe_native_task` launch extras avoid coordinate
clicks. Missing/duplicate/stale/wrong-process receipts and unavailable controls
are inconclusive; observed disclosure or the forbidden write is a failure.
The output is private and exclusively created. Use an outer bounded job too.
This deliberately does not qualify focus, task mutations, the whole sandbox or
matching image provenance; the image-install driver must establish the latter.
Run its evidence-classification controls with
`python3 tests/android/test_native_guard_qualification.py`.

The clipboard fixture copies only `luma-android-clipboard-fixture` and records
only whether known host/Android fixture tokens matched on reads. Never use it in a
personal profile. For the background negative control, select **Check after
switching to a Luma app**, then focus a native host window within ten seconds.
Keep evidence of the native focus transition and the delayed fixture receipt.
`android-window-focus=focused` does not establish host focus; a matched host
clipboard read after switching away leaves the privacy gate failed. Also test
background writes before claiming a complete clipboard boundary.

For a reproducible host-focus control, run `native-focus-fixture.py --output
/run/user/UID/luma-android/native-focus.jsonl` as the disposable graphical user.
Move its GTK window beside Android. It records only `Gtk.Window:is-active` and
boot-clock timestamps as `native-window-activation` events with `active` values.
These events do not attest compositor input focus; the old `focused` field was
misnamed and is no longer emitted. The qualification runner must also collect
actual compositor seat/window focus for the same live surface generation. An
activated decoration alone is insufficient, including on touch-only devices.
First copy the Android token normally to clear the Android clipboard, then
schedule the delayed read, focus the native control, and publish the host target
**after** confirmed compositor focus. Confirm that focus spans the read. A token
already available while Android was focused can have been cached and is not
fresh host-disclosure proof. Keep unclassified content inconclusive; do not log
it. Also check focus loss during a stalled read, denied background writes, focus
regain, window removal, runtime/compositor restart, and pasting an existing
Android copy into a native app after switching away.

`python3 tests/android/native-task-admission.py /path/to/hardware/waydroid`
compiles the complete task HAL source with explicit Binder/HIDL dependencies.
Its64 method cases require successful transport/one name callback, UID1000-only
forwarding and no service lookup for denied callers. Service replacement, missing
services, failed resize without replay and concurrent HIDL/resize calls are checked.
The unpatched control must fail42. `--compiler clang++ --sanitize address,undefined`
or `--sanitize thread` adds sanitizer checks. This is not real HwBinder admission.

`python3 tests/android/native-task-channel.py /path/to/hardware/waydroid` runs the
exact production resize socket handler with real SOCK_SEQPACKET sockets and
SO_PEERCRED. Private user namespaces map the test's own host identity to Android
UID1000 or10000; no host account is changed. It checks malformed, oversized,
short, stalled, closed and backpressured peers. The original oversized-prefix
control must fail42. The resize implementation is a fixture; socket deadlines
do not bound framework Binder execution. Linux unprivileged user namespaces are
required for this check.

The ordinary APK's **Check native task boundary** action performs the raw
`vendor.waydroid.task@1.0` transaction4 name read for its fixed fixture package.
It records only `fixture-label`, `empty`, `other-label` or `unavailable`.
The trusted control is `native-clipboard-control.py --service task --expect
fixture-label`, run as root inside the owned disposable VM. Require a recognized
nonempty trusted label both before and after the ordinary request; unavailable
transport is inconclusive. This read-only probe does not establish denial of
focus/remove/remove-all mutations, which require controlled task fixtures.

Reply source checks:

```sh
python3 tests/android/notification-reply-java.py --source-root /path/to/pinned/android
python3 tests/android/notification-shell-reply.py /path/to/prepared/gnome-shell
dbus-run-session -- python3 tests/android/notification-reply-dbus.py
dbus-run-session -- python3 tests/android/notification-dbus.py --native
```

The Java check expects the preserved post-Waydroid/lifecycle SDK baseline,
generates the additive AIDL with the pinned compiler, compiles against API33,
and executes the production dispatcher with explicit Android dependencies.
The Shell check executes the effective production admission/routing method in
Node with D-Bus fixtures; it does not exercise GJS or rendering. Private-bus
checks use real, separate connections, but a fixture Android listener and
notification server. They check caller/token admission, duplicate requests,
uncertain outcomes and native-Shell versus FDO-forwarder owner epochs. No
fixture result replaces a normal APK reply through the actual native UI.
