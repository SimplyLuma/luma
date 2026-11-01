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
shared storage.

`0003` ships `LumaInputMethod` (hardware/waydroid `0022`). At boot it selects
itself in place of Android's keyboard where the composer offers host text input
(unless the person chose another keyboard), and gives Android's keyboard back
where it does not, such as a nested compositor without `zwp_text_input_v3`.

## Patch order

1. `0001-prairie-host-window-product-property.patch`
2. `0002-luma-codec-services-match-the-image.patch`
3. `0003-luma-input-method.patch`
