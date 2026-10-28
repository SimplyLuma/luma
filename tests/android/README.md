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

`dbus-run-session -- python3 tests/android/notification-dbus.py` exercises the
production host transport over a real private D-Bus connection and a separate
notification server process. Only its Binder listener is a fixture. It checks
identity, plain-text escaping, activation tokens, dismissal versus expiration,
producer withdrawal, server restart and stale replacement. Its JSON result
explicitly does not claim Android, compositor, ARM or hardware qualification.

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
