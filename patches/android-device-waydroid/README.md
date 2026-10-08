# Prairie Waydroid product configuration

This patch applies to `device/waydroid/waydroid` `lineage-20` at commit
`2234f4b75220be7065546a2a23f9a4091b8c77d4`.

It declares `ro.luma.host_window_chrome=true` in the Prairie desktop/tablet
Waydroid product so Android framework and SystemUI omit their internal
freeform captions. The property is read-only and baked into the image; it is
not a per-session toggle or boot-time script.

## Patch order

1. `0001-prairie-host-window-product-property.patch`
