# Luma Tiling Quick Toggle

This Luma-owned GNOME Shell extension provides one narrow integration point: a
standard Quick Settings menu named **Tiling**. Its primary tile calls GNOME
Shell's live extension service to enable or disable
`tilingshell@ferrarodomenico.com` immediately. The submenu reads Tiling Shell's
compiled extension-local schema, renders its saved tile geometry, and uses the
tiler's Shell D-Bus object to select a layout or open its existing edit/new
layout interfaces. A direct **Tiling settings** action remains available.
GNOME Shell owns the persisted enabled-extension list, preserving every
unrelated extension.

Tiling Shell remains a separate and explicitly credited upstream package with
its narrow downstream deltas recorded in Luma's component ledger. This bridge
does not copy, rename, or replace its implementation. Tiling Shell's separate
top-panel indicator is off by default because the same controls are available
in Quick Settings.

The layout picker is drawn by `stylesheet.css` in the appearance mode Quick
Options is showing (its `luma-surface-*` class), from the Shell's ink ledger:
neutral outlines for every layout and the shared state slate for the chosen
one (ADR-043). `verify-ink.py` measures each pair in all four modes from the
package's `%check`.
