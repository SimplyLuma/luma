# Tiling Shell downstream patch

Project Luma packages the reviewed GNOME Extensions version 76 payload for
Tiling Shell 17.3. Its upstream name, UUID, interface, settings, links, and
credits remain intact.

`0001-disconnect-signals-before-destroying-indicator.patch` moves the existing
signal-registry disconnect ahead of indicator destruction. GNOME Shell 50
otherwise reports a disposed `Indicator` and stale handler IDs whenever the
extension is switched off. The patch changes teardown order only.

`0002-luma-visible-drag-layout.patch` applies that behavior to the compiled
JavaScript and schema files in the reviewed GNOME Extensions payload. It exposes
the existing layout renderer and drop target as an explicit **Show layout while
dragging** preference. When enabled, moving any window reveals the active layout
without a modifier key, and releasing it over a zone uses Tiling Shell's existing
tile path. It also makes a two-column `Luma Split` the first fresh-profile layout.
Control is the fresh-profile deactivation modifier, and that deactivation state
is honored both while rendering the layout and on the final drop.

`0004-luma-quick-settings-interface.patch` keeps Tiling Shell's indicator and
editor implementation intact but defaults the redundant top-panel button off.
It adds `selectLayout` and `newLayout` methods beside the upstream
`openLayoutEditor` method on Tiling Shell's existing Shell D-Bus object. Luma's
Quick Settings bridge can therefore use the tiler's own global state and editor
instead of duplicating either implementation.

`0003-luma-visible-drag-layout-source.patch` is the matching TypeScript source
change for the pinned upstream 17.3 tag. It is retained as provenance and as the
maintenance path for future source builds. The reviewed extensions payload does
not contain `src/`, so the RPM intentionally applies only `0002`; it must not
pretend to patch source files that are absent from that archive.

`0005-luma-quick-settings-interface-source.patch` is the matching TypeScript
source correspondence for `0004` and follows the same provenance-only rule.

The package also normalizes the review ZIP's file modes during `%prep`; that is
packaging metadata rather than a source patch. Every directory becomes `0755`
and every payload file becomes `0644` before installation.

Drop the corresponding runtime and source patches together when an equivalent
upstream release is selected and its drag-layout acceptance test passes without
them. Upstream identity, preferences, credits, and layout engine remain intact.

`0006-luma-tell-clients-a-window-is-tiled.patch` mirrors Tiling Shell's
`assignedTile` state into Mutter's `meta_window_set_externally_tiled()` (added
by Luma's Mutter patch 0008). Mutter only sent tiled states for its own
side-by-side tiling, so clients drawing their own decorations kept a floating
window's shadows and resize borders in a tile, and Chromium's content spilled
past the tile. The call is optional-chained, so the extension still runs on a
Mutter without it.

`0007-luma-right-click-breaks-the-tiling-spell.patch` lets a right-click or a tap of
Control during a mouse drag do what holding the deactivation modifier does, for the rest of that
drag, and a second right-click restores tiling. It depends on Mutter patch
`0009`, which keeps the drag running for that click. There is no source
correspondence patch yet; the payload change is small and self-contained.

`0008-luma-layouts-follow-monitors-and-setups.patch` remembers the layouts a person
chooses for each combination of connected monitors and for each physical monitor
(maker, model and serial), and restores them when monitors change, instead of
keeping layouts by monitor position.

`0009-luma-unique-layout-ids-and-editor-cursors.patch` gives every new layout an
unused id and renames repeated ids when layouts load. Upstream named new layouts
after the current event time, which is 0 outside event handling, so several
layouts could share "0" and only the first could ever be selected. It also moves
the layout editor's divider cursors to GNOME 50's `Clutter.CursorType` interface.

`0010-luma-window-placement-memory.patch` adds window placement memory (ADR-036) in
`components/lumaWindowMemory/windowMemory.js`. Windows are remembered by app
identity and window role for each monitor setup, as a last placement and a
long-term habit (hours lived in each tile, maximized state or floating rect).
Apps open in their strong habit, otherwise their last placement, and stay
invisible until they are there. It also declares the `unlock-dialog` session
mode, so tiles survive lock and sleep. Windows go back where they lived when the
monitors settle after hotplug, sleep or unlock. Dialogs and splash windows, and
windows the person moved, are left alone. Tiling Settings gets **Remember window
positions** and **Forget Window Positions**. The headless scenario test is in
`tests/tiling-shell/window-memory/`.

`0011-luma-windows-settle-once-after-monitor-changes.patch` makes windows appear
once, in place, after resume and dock changes. Windows are put back before the
first frame of each new monitor layout (after the Shell updates its work areas)
and confirmed when the monitors are stable, instead of 1.5 s later; a monitor
change that comes before logind's wake signal is recognized as the resume. A
window moved for new monitors stays invisible until its client shows it in place
(at most 1 s). A window left alone, or moved off its work area by anything but
the person (such as a client acknowledging an old configure after resume), is
brought fully inside the work area of the monitor it overlaps most, in one move,
and such a place is never remembered. `tests/tiling-shell/window-memory/resume-trace.js`
records where every window is painted, frame by frame, across undock, resume
docked, a late client move and resume undocked, on a mixed-scale layout.

`0012-luma-apps-appear-as-soon-as-they-open.patch` makes sure window memory
never holds a window noticeably. The short reveal timer used to start only on
Mutter's "shown" signal, so an X11 client that mapped and drew without "shown"
arriving after the hold began (an Electron app through the container launcher)
stayed invisible for the whole 10 s safety timeout. Now the actor's first frame,
damage or mapping also count as shown, including when the window already is on
screen as the hold starts; the reveal comes at most 250 ms after that and 1 s
after the hold began, and a window not yet in place is shown where it is and
then moved to its place with Tiling Shell's own animation. Each reveal logs how
long the window was held and what showed it, with a warning above 500 ms.
`tests/tiling-shell/window-memory/wm-test.js` asserts the reveal time for
Wayland GTK, Chromium, XWayland GTK and a self-resizing X11 client.

`0014-luma-tiles-follow-the-work-area.patch`: on a work-area change (the Dash freeing or taking an edge), windows in tiles are eased into the same tile of the new work area with the layout's gaps; floating windows are untouched and Mutter re-fits maximized ones. Only once the layout has been stable for 500 ms (after a resume or hotplug window memory puts windows back first, also debounced and once per settled layout); each window moves at most once per settled work area, never to another monitor, and a window moved 3 times in 2 s is left alone (Vitals `reflow-stopped`, `restore-churn`). Tested by `tests/gnome-shell/shelf-arrange/retile.js` and `tests/tiling-shell/window-memory`. .20: a change the Dash makes itself is followed at once on its curve and duration (a shrinking work area before Mutter applies it, a growing one as soon as Mutter has it), each window gliding in one move with no cross-fade (`Main.wm.lumaGlideFrame`); a window is put back in its tile when a late client answer left it elsewhere (Vitals `tile-corrected`, `tile-refused`); a one-time migration (`luma-settings-migration`) turns an outer gap of 0 into the inner gap and the panel button back off. `90_luma-tiling-shell.gschema.override` (installed into the extension's schemas) keeps the panel button off and an outer gap of 16. The outer gap is now per edge: the Dash's reservation already holds the margin a window keeps from an island, so the outer gap on a reserved edge is the rest of the gap and the whole gap on an edge the Dash does not hold (`lumaOuterGaps`). A window is then exactly one gap from another window, from an island and from the screen. Tested by the shelf-arrange oracle's livetile, tiles and gaps scenarios.

`0016-luma-windows-open-straight-into-their-tile.patch`: window memory answers Mutter's initial `configure` signal (Mutter 48 and later) for a window with a remembered place, so a Wayland client's first configure already carries the tile's size and tiled state, and an X11 window is mapped at its tile; its first frame is drawn in place. The position and size are set separately because `set_rect()` takes its rectangle by value and reaches Mutter as garbage from GJS. The hold stays as a safety net for clients that ignore the size, shortened to 150 ms after the window first reaches the screen, then the usual open animation. Session-restored windows, dialogs, transients, fixed-size and splash windows, an app's second window, windows the person moved and a Mutter without the signal keep the old path (placed when first shown), and each reveal logs which path it took. `tests/tiling-shell/open-in-place/` traces every painted frame of a launch for GTK4 on Wayland (also slow to relayout, and maximized), GTK4 and a plain client on XWayland, and Chromium on Wayland and XWayland. The upstream auto-tiling option still tiles new windows after their first frame.
