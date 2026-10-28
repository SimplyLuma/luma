# Android framework integration

These patches extend the pinned LineageOS SDK after the complete, matching
Waydroid patch series. Both supported product architectures use the same series.
The canonical and incremental builders apply it; changing either patch refreshes
the prepared SDK project. Never overlay a separately compiled privileged APK
onto an unrelated Android system image.

`0001` registers Android's existing `TaskStackListener`. Only authoritative
`onTaskRemoved(taskId)` dispatches the backward-compatible window HAL 1.3 method.
It does not infer removal from missing surfaces or timers, and does not remove
an Android task recursively. The matching hardware patch retains HAL 1.0–1.2
and admits the new operation only from system_server UID 1000. Callback
retirement precedes collection erase; in-flight native listeners retain strong
ownership. Exact HIDL definitions, dependencies and VINTF manifest belong to
the matching image pair.
`0002` adds notification dismissal and notification-server recovery. Its AIDL
callbacks append transactions 2 and 3; the original action transaction remains
1. A host dismissal cancels only a matching, clearable user-zero notification.
Expiration and producer withdrawal do not cancel Android notifications. Default
activation sends the original PendingIntent and respects `FLAG_AUTO_CANCEL`.
The Android main looper owns notification map updates and callback handling.

The matching host transport lives in
`src/luma-android/luma_android/waydroid_notifications.py`. It admits Binder
kernel UID 1000 before reading a parcel, for the current user-zero Waydroid
system service. This is not an admission rule for a future UID-remapped runtime.
APKs receive neither a host D-Bus connection nor arbitrary host commands.

`0003` checks kernel Binder UID before the privileged platform stub unmarshals
any request. Only Android root/system or the captured host identity is admitted.
The host identity must be in the non-application UID range (1000–9999). The
current ABI cannot safely identify a desktop UID that overlaps Android's app
UID range; it requires a different transport or UID mapping. Package names,
caller-provided parcels, mutable properties read during requests and PIDs do
not grant authority. The pure Java helper is compared against its patched copy
by both builders and exercised by `tests/android/run-host-caller-policy.sh`.

An old Android image retains action activation but cannot implement the added
callbacks. A new image with an old host retains the original callback ABI.
Source application and host-side tests do not qualify a matched image pair.
Inline replies, channel settings, lock-screen privacy, activation-token delivery
through HWC, multi-user runtimes, and mobile background delivery need additional
implementation and end-to-end acceptance.

The host adapter derives from Waydroid 1.6.3's GPL-3.0-or-later notification
transport, copyright 2025 Alessandro Astone. Its upstream license and attribution
are preserved; the runtime RPM includes the GPL license. The Android patch
modifies the upstream Apache-licensed framework files and preserves their
notices in the corresponding source tree.
