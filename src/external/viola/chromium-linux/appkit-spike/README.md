# Native AppKit integration probe — not a preview release

The r4/r6 custom Views/WebUI presentation was rejected. Copying Luma colors,
window buttons, an invented globe identity, and menu geometry is not an AppKit
integration. The r7 compatibility repairs fix some concrete ownership defects
but are not the replacement UI and must not be promoted as an accepted build.

This directory measures the native host / retained Chromium boundary before a
UI rewrite. The host must compose installed `luma_appkit` components, real
`Gtk.WindowControls`, the shipped application icon and GTK menus. It must not
redefine frame, titlebar, identity, island radius, elevation, fonts or icon theme.

`engine_pipe.py` launches the existing Viola executable with a new, empty
profile and an anonymous DevTools pipe. This isolates engine experiments from
both the stable browser and the user's preview state. It does not open a
network debugging port or turn off Chromium's sandbox.

The initial capture probe is a measurement of CPU frame transport, not the
production embedding contract. An encoded screenshot stream is **not accepted**
as the final page transport. A production integration needs GPU buffer delivery
and lifetime/fence handling, native input/IME, popups, drag/drop, accessibility
(including the complete Chromium page tree), fractional scale, fullscreen,
portal media and retained Viola profile/tab/extension behavior.

CEF's upstream Linux accelerated OSR implementation is a reference for DMA-BUF
frame ownership, not authorization to replace Viola's engine, extensions or
profile model. Its documented X11-related constraints must not be hidden behind
a Wayland GTK window. No new engine is downloaded by this probe.

Do not install this as the normal launcher, update the public package feed, or
show its native window as evidence that the browser migration is finished.

Verified boundary work is recorded in `docs/LINUX_LUMA_NATIVE_REBUILD.md`:
native DMA-BUF import/lease turnover, exact engine target identities, the
installed title/toolbar measures, retained navigation commands and a native
GTK shell-menu adapter. None is full-window visual acceptance. The final
native composition, complete input/accessibility and all popup families are
still unfinished. All engine probes use fresh profiles; GPU/menu probes use a
minimized Wayland engine window because Chromium headless overrides Wayland.
