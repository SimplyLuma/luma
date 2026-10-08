# Prairie Android framework integration

This patch applies to LineageOS `frameworks/base` `lineage-20.0` at commit
`45a6bfd66cd878aad7988e9506c12035f8eee127`.

When the immutable product property `ro.luma.host_window_chrome=true` is set,
Android treats the caption boundary as host/Shell-owned so app processes do
not inflate legacy `DecorCaptionView`, while the guarded WindowManager Shell
view model declines to create its own decoration. Android therefore does not
instantiate either of its freeform-window caption systems:

- the legacy app-process `DecorCaptionView`; or
- WindowManager Shell's `CaptionWindowDecoration`, including its interior
  drag/resize handles.

Prairie's Wayland hardware-composer client then owns one native title bar,
one outer resize boundary, and one lifecycle for each Android application.
This is deliberately a product source decision rather than a runtime CSS or
view-hiding workaround. Handheld builds may omit the property and retain their
normal Android presentation.

## Patch order

1. `0001-prairie-host-owned-window-chrome.patch`

This patch changes both the boot framework and WindowManager Shell. Deploy it
only in a system image built from the same resolved manifest as SystemUI and
its boot artifacts; do not overlay the generated SystemUI APK onto an unrelated
Waydroid system image.
