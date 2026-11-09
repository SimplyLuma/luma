# Prairie Waydroid product configuration

This patch applies to `device/waydroid/waydroid` `lineage-20` at commit
`2234f4b75220be7065546a2a23f9a4091b8c77d4`.

It declares `ro.luma.host_window_chrome=true` in the Prairie desktop/tablet
Waydroid product so Android framework and SystemUI omit their internal
freeform captions. The property is read-only and baked into the image; it is
not a per-session toggle or boot-time script.

It also defaults `persist.waydroid.suspend` to false, preventing the framework
from freezing background delivery when the last app window closes. The host
runtime reapplies the explicit `idle_freeze` policy through Waydroid's supported
property interface. This availability default does not qualify idle power or
replace host suspend coordination.

The OMX codec service is 32-bit only, so 64-bit-only images never build it.
`0002` declares OMX only where it is built and keeps Codec2 on otherwise;
declaring it made mediaserver wait forever, so MediaProvider never mounted
shared storage. It also lets the software codecs set a thread's scheduling
policy: Mesa does that while mapping a decoded frame, and the codec seccomp
filter killed the process on the first video frame.

`0003` ships `LumaInputMethod` (hardware/waydroid `0022`). At boot it selects
itself in place of Android's keyboard where the composer offers host text input
(unless the person chose another keyboard), and gives Android's keyboard back
where it does not, such as a nested compositor without `zwp_text_input_v3`.

## Patch order

1. `0001-prairie-host-window-product-property.patch`
2. `0002-luma-codec-services-match-the-image.patch`
3. `0003-luma-input-method.patch`

`0004` applies the host's presentation as Android's forced display density.
`ro.sf.lcd_density` is set once per boot, from the presentation Android boots
into; when the host presentation changes afterwards (a Fold posture, phone to
tablet, docking), luma-android publishes `persist.luma.forced_density` and an
init trigger runs `luma-forced-density` as system, which sets or resets
WindowManager's forced density. It replaces only an override it applied
(`persist.luma.density_override`), so a display size chosen in Android's
settings is kept.

`0005` ships microG (Apache-2.0) as privileged system apps: GmsCore
(`com.google.android.gms`), GsfProxy (`com.google.android.gsf`) and FakeStore
(`com.android.vending`), so Android apps can sign in with a Google account and
receive push messages without Google's proprietary apps. The APKs are microG's
official signed releases, pinned with URL, version, size and SHA-256 in
`config/android/microg.env` and placed by `scripts/android/fetch-microg.sh`;
they are not committed. LineageOS' framework already restricts signature
spoofing to these two package names signed with microG's certificate
(`9bd06727…d14165`), presenting only Google's signature. The patch adds the
privileged-permission allowlist the build enforces, a power-save exemption for
push delivery, and microG's license notice. The APKs are installed byte for
byte (Make prebuilts with `LOCAL_REPLACE_PREBUILT_APK_INSTALLED`): Soong's app
import uncompresses privileged apps and strips their v2 signature.

microG works without setup: `/system/etc/com.google.android.gms/settings.xml`
turns on device registration, cloud messaging, device attestation (which also
gates Play Integrity) and the companion's licensing, billing, asset delivery
and split installs; every runtime permission microG requests is a default
grant; `luma-microg-defaults` runs once to allow its overlay and
cross-profile app-ops, and grants the runtime permissions on a device whose
`/data` predates microG. Users can change each in microG's settings.

`0006` installs `/vendor/etc/task_profiles.json` for a cgroup v2-only host.
Waydroid on Luma gives Android one delegated cgroup2 hierarchy; the v1 cpuset,
schedtune, blkio, cpu and memory controllers in Android's `cgroups.json` cannot
be mounted (EPERM in a cgroup namespace, no v1 cpuset filesystem, no
schedtune), so the 36 profiles that join them, set their attributes or use the
unimplemented `SetClamps` failed and logged on every service start and process
state change. libprocessgroup reads the vendor file last and replaces profiles
by name, so these are emptied; timer slack, the cgroup2 freezer and the
`uid_N/pid_N` process groups keep working, and thread nice values never went
through cgroups. A vendor `cgroups.json` cannot help: declaring those
controllers on the cgroup2 root would make libprocessgroup treat them as
usable and move whole processes out of their `uid_N/pid_N` groups. The
one-time mount failures at container start remain.
`tests/android/cgroup-v2-task-profiles.py --source-root` checks the file
against the tree.
