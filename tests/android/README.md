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
The fixture's Reply button deliberately exposes the missing RemoteInput path;
an action click without delivered text is not a reply pass.

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
