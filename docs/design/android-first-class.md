# Android applications as Luma applications

Engineering decision and acceptance work, 2026-10-28. This is an implementation
plan with explicitly limited evidence, not a declaration of complete compatibility.

## Product and runtime boundary

An APK belongs in the same install flow, application registry, search, dock,
window manager, permissions UI and resource monitor as other Luma applications.
It must retain its original signing identity. A phone layout stays useful as a
phone layout; a resizable tablet layout receives real task configuration changes.
Closing a window retires its task, not all Android services or another app.

Use one maintained AOSP-derived platform per host user/trust profile, with a
shared immutable system image and separate app/data state. Matching-architecture
DEX/ART and native libraries execute on that architecture. Linux namespaces do
not imply a separate Android image for each app. The intended storage increment
is APK/splits, private data and bounded compilation caches. Never allocate another
platform image just because another application was installed.

Keep the replaceable runtime backend behind Luma-owned app and capability
contracts. Existing Waydroid/Lineage components are useful implementation inputs;
their current security, latency and compatibility properties are not assumed.
A kernel-sharing backend needs demonstrated isolation. Where that cannot be
provided, qualify a shared hardware-virtualized backend with the same app-facing
contract. Never silently migrate live app data or weaken isolation on a failed
launch. ChromeOS's [container/VM security rationale](https://chromium.googlesource.com/chromiumos/docs/+/d2df969632e7c227618deeb201e1aa7a168d42c7/containers_and_vms.md)
is a useful design reference, not proof of Luma's own implementation.

The smaller-runtime research path retains AOSP framework/ART/application behavior
while replacing selected services underneath it with native Luma adapters.
Evaluate it against the same corpus and permission/lifecycle tests. A framework
rewrite is not a prerequisite for improving the existing subsystem, and an empty
API implementation is never counted as compatibility.

## Lessons from current projects

| Input | Relevant finding | Decision |
| --- | --- | --- |
| [Mac.apk at ce1499e](https://github.com/kksimp/Mac.apk/tree/ce1499e8b856fb453c773acbf8584ad42307d312) | Direct framework/JVM/Bionic compatibility is possible for selected applications; inspected NotificationManager operations are empty and its security policy gives apps the host user's permissions. | Study its API-boundary experiments; do not inherit absent notification semantics or absent app isolation. |
| [Waydroid](https://github.com/waydroid/waydroid) | Luma already has native task surfaces, supervised launch and source-built framework/window patches. | Preserve useful work, fix demonstrated defects, and qualify a maintained platform rebase. |
| [Valve Lepton at 150aba0](https://gitlab.steamos.cloud/frame-public/lepton/-/blob/150aba0a65daba7ff512f0d25cebf529bb9eb79e/README.md) | Official source documents rootless execution, same-UID processes within an instance, per-app containers, reduced services and cached boot state. | Study caching and graphics integration. Its game-oriented isolation/service tradeoffs do not meet this shared general-app contract. |
| [ATL architecture](https://gitlab.com/android_translation_layer/android_translation_layer/-/raw/master/doc/Architecture.md) | Open Linux framework replacement experiment. | Use as a research/reference input; require real app, resize and sandbox evidence. |
| [Drion](https://actinis.io/drion/how-it-works) | Author describes retained Android framework with native service replacements; public source/build qualification was unavailable in this investigation. | Interesting seam to investigate independently; vendor claims are not Luma test results. |
| [Digitalis integration](https://github.com/DigitalisX64/digitalis/blob/e81431d67766f9a827b568b9137da131daecfa87/docs/integrating-digitalis.md) | Source-buildable ARM64-on-x86 Android Native Bridge candidate, tied to matching Android guest libraries/platform. | Evaluate after the platform rebase; do not insert Android 16 libraries into Android 13 images. |

The current Luma source pins Android 13/Lineage 20. Repairing that baseline makes
failures reproducible; it does not make it the final maintained shipping platform.
Newer source/image candidates must be pinned, audited, ported and built for both
CPU architectures. A third-party image download alone is not that rebase.

## Host integration contract

Android's framework derives app identity from kernel Binder UID and PackageManager
state, including signing identity and user/profile. Luma's broker authenticates
that framework/runtime endpoint. Strings supplied by an APK, Wayland app IDs and
icons are presentation metadata, not authority. Handles expire on uninstall,
signing-identity change, profile removal, revocation or runtime-generation change.

| Capability | Required behavior in both directions |
| --- | --- |
| Notifications | Native presentation; original PendingIntent activation; RemoteInput reply; dismissal versus timeout/cancel; channel/group/privacy/DND policy; no feedback loops. Host notification access requires a separately granted listener role. |
| Documents/share/drop | Host chooser and Android content URIs exchange scoped handles/streams, including seek/write-back and persistable grants. Revocation and reconnect work. No whole-home mount and no mandatory second copy for every document. |
| Clipboard/input | Focus/paste policy, MIME conversion, touch/stylus/keyboard/IME and accessibility semantics belong to the actual app. Background APKs do not receive the desktop clipboard indiscriminately. |
| Audio/media | Per-app stream and MediaSession identity, routing, focus, volume, interruptions and native transport controls. |
| Camera/microphone/location | Verified per-app consent, native device selection, live revocation and recording indicators; absent hardware is reported honestly. |
| Contacts/calendar/telephony | Consented providers reach Luma's authoritative native services. Do not mount native private databases or hand Android ownership of the modem/controller. |
| USB/Bluetooth/NFC/sensors | Shared controller/device arbitration, bounded rates and tested capability advertisement. |
| Processes/resources | Verified package/process identity, PSS and CPU accounting, background policy and scoped app termination. A single aggregate Android row is insufficient for app attribution. |

Android notifications use Luma's notification renderer, with Luma typography,
spacing, colors and controls in both banners and the notification center.
Android supplies structured content and behavior; it does not supply a second
notification shade or Android system chrome. Preserve actions, inline replies,
grouping, channels and privacy through the bridge. An application's custom
RemoteViews layout must not become executable host UI; a semantic fallback and
any isolated custom-content presentation need explicit compatibility tests.

Retain Android's lifecycle, permission enforcement and per-app isolation. The
[Android sandbox](https://source.android.com/docs/security/app-sandbox),
[document-provider contract](https://developer.android.com/guide/topics/providers/document-provider)
and [RemoteInput API](https://developer.android.com/reference/android/app/RemoteInput)
provide concrete semantics to preserve. Native host surfaces are real Wayland
toplevels. Prove buffer sharing and synchronization with traces; do not advertise
zero-copy from an architecture diagram. Keep a stable toplevel through Android
activity recreation and correlate configure generations with applied task bounds.

## Architecture selection and power

Prefer arm64-v8a on ARM64 and x86_64 on x86_64. Inspect the actual APK/split ABI
set, not the filename. DEX-only packages are architecture-neutral at inspection;
some applications later download native code. Same-family 32-bit support still
requires a compatible runtime/CPU. ARM host page sizes need explicit admission.

Foreign instruction support is opt-in and appears only when its installed engine
has been qualified for that direction. Digitalis is the ARM64-to-x86 candidate;
FEX is a possible x86-to-ARM core and still requires Android/Bionic/Native Bridge
engineering. Neither name is a universal translator. No proprietary extracted
Houdini or Google translation binaries in the default payload.

No installed apps means no unnecessary Android startup. Installed background apps
need Android-compatible jobs, push and alarm semantics. A global freeze cannot
preserve arbitrary delivery without another authorized wake/delivery mechanism.
Use Doze and host suspend coordination, then measure wakeups and battery on actual
mobile hardware. Never turn a VM idle CPU sample into a battery-life claim.

## Concrete work and evidence

The first source change adds a Luma-owned notification transport, additive Android
dismissal/reset callbacks, original-action PendingIntent handling, and a kernel
UID check on the privileged Android platform stub. Clean/incremental builders
apply the same patches. Existing provider-owned Waydroid files remain intact.

The root hardware Binder endpoint has a separate process-local admission
adapter. It checks kernel caller credentials before the provider can unmarshal
or dispatch a request. Only Android root and system-server UIDs are admitted
under the current non-remapped ABI; the session user's numeric identity is not
an extra root-hardware role. Its packaged systemd drop-in selects the adapter
without replacing the provider's unit or Python implementation. This narrow
boundary is not a complete isolation audit of the Android runtime. A label-only
SELinux module maps the adapter to the provider's existing entrypoint type; it
adds no allow rules. The adapter verifies its execution domain on SELinux hosts
before starting the service, so a missing policy/label fails closed.
Guest image-replacement transactions are rejected even from Android root or
system. Luma's verified host update path owns the matched system/vendor pair;
a guest-provided file path cannot authorize a privileged host image write.
The runtime package preserves the derived GPL transport's license and attribution.
The Luma-owned session also admits only kernel UID1000 at the host clipboard
and user-monitor callbacks, before parcel parsing. Apps use Android's normal
clipboard focus/permission checks and package APIs. Root hardware and session
roles have distinct admission policies. Unknown read-only operations have typed
control/denial replies for acceptance tests without touching clipboard contents
or generating package events. Live validation of these session guards remains
required; the root-hardware pass does not substitute for it.

A real mouse resize in the disposable x86 VM froze the baseline compositor.
Native debuggerd stacks identified an adapter/window lock inversion. Patch 0014
releases the window lock before adapter invalidation, with strong lifetime
ownership retained. A compiled test uses the exact production configure callback
bodies; a control removing the unlock reproduces the deadlock. Full Android image
compilation and repeating the real workload are separate required checks.

The source-owned APK and host transport qualification live in
[tests/android](../../tests/android/README.md). The APK exercises real notification
actions, state retention and document APIs. Its privileged-boundary probe runs
from its own ordinary app UID; unavailable reflection or an absent service is
not a permission pass. Host private-bus tests deliberately label the Binder
listener fixture. Build/signature, host protocol, full framework and physical
device results must remain distinguishable.

Acceptance order is: isolate and reproduce the baseline; build the matched
framework/vendor pair; repeat x86 app install/window/notification/permission
workloads; repeat on ARM; implement and qualify scoped document and other native
service adapters; validate multi-user isolation and upgrades; qualify mobile
suspend, push, idle power and real devices; then qualify optional foreign ABI
engines. The current baseline reports Android internal SELinux Disabled despite
Fedora host Enforcing. That remains a production isolation gate.

Retain exact OS/runtime/image/APK/source hashes, driver/page-size settings and
raw measurements. Measure cold system preparation, warm-runtime process start,
first presented frame and existing-window activation separately. Report PSS,
allocated disk blocks, frame-time distributions, CPU/wakeups and physical energy.
Repeat resize/maximize/minimize/restore/close, concurrent apps, service restart,
notification-server restart, permissions/revocation, data-preserving updates and
rollback. Publish failures and unsupported capabilities. A finite test corpus
cannot establish an all-APK success probability of 99.9999999999%.

## Distribution boundary

AOSP components can be modified and distributed under their actual licenses;
Apache-licensed framework code does not remove GPL/LGPL, notice or source-offer
obligations elsewhere. Preserve corresponding source and build inputs for the
exact shipped artifacts. Android branding and compatibility certification are
separate from copyright permission. See [AOSP licenses](https://source.android.com/docs/setup/about/licenses)
and [compatibility requirements](https://source.android.com/docs/compatibility/overview).

Google Play, GMS and proprietary translation libraries require their own rights;
AOSP access does not grant them. Emulator/SDK downloads are not a general runtime
redistribution license. Optional microG needs a separate code/service/signature
review. Play Integrity, hardware attestation and DRM remain app/service-specific
compatibility limits. Preserve publisher APK signatures, entitlements and asset
requirements. Release review must cover actual licenses, notices, source offers,
trademarks and service terms; this engineering decision is not a legal opinion.

Current caller checks reject ordinary APK UIDs; they do not establish complete
runtime origin or sandboxing. The old non-remapped transport shares UID1000 with
some host logins. Before production, move Android identities into a dedicated
user namespace and authenticate bridge callers against the actual runtime
instance; do not treat a numeric UID alone as proof of Android origin. Binder,
Wayland/PipeWire socket access, GPU device access, id-mapped application storage,
profile boundaries and host-service identities must be qualified together.
Android platform test keys are development inputs; production trust requires
protected platform signing keys and a release image that rejects test-key
privileged APKs. These remain shipping gates, alongside mandatory-access policy.
