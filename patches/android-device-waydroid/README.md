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
privileged-permission allowlist the build enforces, default runtime grants,
a power-save exemption for push delivery, and microG's license notice.
