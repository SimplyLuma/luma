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

## Host status bar inset

`0002-luma-host-status-bar-inset.patch` reserves Luma's top bar as Android's
status bar. Waydroid's `app launch` writes `policy_control=immersive.full=*` in
multi-window mode, and its PolicyControl port then reports a 0 px status bar
and force-hides the `ITYPE_STATUS_BAR` source; freeform tasks also drop that
source in `InsetsPolicy`, and `Task.computeFreeformBounds` clamps freeform tasks
to the stable area, shifting each task down by the status bar height so it no
longer meets the bar and gets no inset. When the host sets `persist.luma.status_bar_px` (the
top bar in physical pixels, from luma-android's readiness pass on handheld and
tablet), SystemUI's status bar window takes that height and remains the inset
source, transparent and untouchable, and freeform tasks keep their bounds and
the inset.
Applications lay out below the host bar and draw their own status bar colour
behind it. Unset or `0` (desktop) keeps the previous behaviour. SystemUI reads
the height at start and on configuration or `policy_control` changes; the
property persists, so a changed value applies from the next Android start.

## Patch order

1. `0001-prairie-host-owned-window-chrome.patch`
2. `0002-luma-host-status-bar-inset.patch`

This patch changes both the boot framework and WindowManager Shell. Deploy it
only in a system image built from the same resolved manifest as SystemUI and
its boot artifacts; do not overlay the generated SystemUI APK onto an unrelated
Waydroid system image.
