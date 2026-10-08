# GNOME Shell downstream

Project Luma builds the Fedora 44 `gnome-shell-50.3-1.fc44` source package
with isolated, ordered downstream patches. GNOME Shell remains GPL-2.0-or-later and
retains its upstream name, source history, and About/diagnostic identity.

`0001-luma-panel-layout.patch` changes the normal user session only:

- the date menu is the first actor in the left panel box;
- the Activities actor is absent from the panel while the Super key and native
  overview remain unchanged;
- the label renders locale-aware time first, followed by the optional weekday
  and date, and continues to honor 12/24-hour, seconds, weekday, and date keys.
- the time and date are separate semantic labels with an explicit 9px spacer;
- the normal light-session panel uses Luma's 34px translucent material,
  hairline, typography, tray pill states, and compositor background blur;
- unread notifications use a numeric gold badge instead of a symbolic dot.

Shell's compositor blur supports radius and brightness but not CSS
`saturate(1.4)`, so saturation is intentionally omitted. The panel is
rectangular and therefore does not expose the rounded-clip limitation that
applies to the dock.

`0002-prairie-login-lock.patch` implements the Prairie Daybreak GDM and unlock
surface in GNOME Shell source. It shares native clock and power-button
components, reshapes the existing authentication prompt without replacing its
GDM verifier, uses a compiled vector resource for the exact stacked Daybreak
gradients, and leaves the centered authentication stack directly on that ground
without a surrounding card, border, or shadow. GDM and unlock session modes
deliberately have no panel actors. Single-user greeters proceed directly to the
named user's focused password prompt; Enter and the inline submit button retain
the same authentication action, password reveal remains native, and failures
remain inline without field motion.

`0003-luma-desktop-first-session.patch` separates overview availability from
the initial session presentation. A normal login completes Shell's required
startup preparation at final desktop geometry and opacity, without invoking
either the overview animation or the scale-and-fade path reserved for modes
that have no overview. The Super key, three-finger gesture, overview, app grid,
search, and workspace model remain fully available after login.

`0004-prairie-login-spinner-row.patch` separates authentication progress from
the submit glyph. `0006-prairie-auth-entry-and-progress.patch` completes that
work by reserving an inline progress slot immediately to the right of Submit,
so authentication never moves or replaces the control. The same patch makes
the Prairie unlock surface credential-first at construction time and disables
the obsolete curtain swipe state; it does not synthesize an input gesture.

The earlier spinner patch keeps the submit arrow in its native
button and gives authentication progress a dedicated, height-reserved row below
the credential field. It reuses GNOME Shell's existing spinner and verifier
state, so progress no longer overlays the button and the prompt does not jump
when verification starts or stops.

`0005-prairie-application-shortcuts.patch` connects Filer's registered-
application drags to two native Shell destinations. The existing Dash drop
contract continues to own favorites ordering and persistence, so a dock drop
uses the same application ID and `AppFavorites` path as an app-grid drop. A
desktop drop creates one user-owned symbolic link in the XDG Desktop directory
to the registered desktop entry, then renders those links as native Shell
actors below windows. The bridge accepts calls only from the
`org.gnome.Nautilus` session-bus owner, validates every application through
`Shell.AppSystem`, relies on Mutter's standard
`application/x-rootwindow-drop` Wayland contract, and never edits packaged
desktop files or populates the desktop at login.
The Shell side retains a validated Dash destination while the pointer crosses
that Dash's own temporary insertion placeholder, but only inside the same
actor subtree; leaving the dock clears it, preserving normal cancellation.
External application drags also enter and leave GNOME's native item-drag
lifecycle. This ensures every Dash instance, including the always-visible
dock, removes its insertion actor after either a drop or cancellation instead
of retaining an inert blank slot.
While that actor expands, Shell retains the open gap until the pointer crosses
its visual boundary. External application drags select the nearest insertion
boundary rather than GNOME's left-biased floor bucket, and clamp the result to
the real first and last dock gaps. The calculation uses the icon box's origin,
matching Xdnd's target actor.
For external drags, leaving the dock is determined from the pointer's stage
coordinates against the live icon-box bounds rather than the identity of the
actor under it. The centered dock can therefore move around a stationary
pointer while opening a slot without falsely treating that layout movement as
a pointer exit and collapsing the slot.
The stored drop destination uses the same live bounds fallback when Clutter
repicks a stationary pointer onto the dock background. Visual insertion and
the target later committed by `acceptDrop()` therefore cannot diverge.

`0007-luma-handheld-posture.patch` adds a runtime presentation posture without
forking Shell. Only a device image that declares `LUMA_DEVICE_CLASS=handheld`
and is currently using a portrait primary monitor receives a 64px top safe
area, 60px panel touch targets, 24px status glyphs, a center exclusion band for
the camera cutout, and zero-delay high-contrast OSK pressed feedback. Rotating
to a landscape primary display or docking to a landscape external display
returns the same running Shell to the existing 34px desktop panel geometry.

`0009-luma-notification-system.patch` moves the native notification presenter
from the superseded clock-relative center rule to a focused-monitor,
work-area-aware bottom-right surface with a 14px inset, seven-second ordinary
timeout, horizontal edge motion, Figtree, and Luma light/dark/high-contrast
materials. The existing Shell source and message list remain the one desktop
record/action/close-reason authority. The date-menu indicator is the compact
waiting beacon and remains visible during Do Not Disturb because DND suppresses
interruption, not history. This intentionally supersedes the
`Clutter.ActorAlign.CENTER` line in `0001`; no runtime override competes.

`0010-luma-notification-lifecycle-stack.patch` completes the desktop delivery
contract in the same native presenter. It retains up to twenty queued records,
renders no more than three simultaneous interactive banners, pauses each
secondary timeout for hover or keyboard focus, and records the shared
Queued/Arriving/Waiting/Seen/Acted/Dismissed/Expired/Replaced lifecycle. A
banner becoming visible is deliberately not acknowledgement: ordinary
non-transient cards return to Waiting after seven seconds, while mapping the
native message list is the point that marks them Seen. Replacement IDs,
actions, close reasons, DND, fullscreen policy, and source ownership remain
GNOME Shell's existing paths.

`0011-luma-search-window-view.patch` lifts Search out of Overview into a native
modal Beam that consumes the shared `org.projectluma.Search1` service. Bare
Super toggles Search using Mutter's solo-overlay-key signal, Escape and the
scrim dismiss it, and the upstream results presentation retains keyboard and
assistive-technology behavior. The same patch presents Overview as **Window
View**: window previews, workspaces, gestures, and compositor integration stay
native, while the Search field, Dash, and application grid are not presented
in that surface. The hot corner and explicit `<Super>w` binding open Window View.
The normal Applications binding opens the retained upstream app grid as a
separately named **Applications** surface. Window View gestures stop at the
window-picker state, so the app grid is not a hidden second page of Window View.

`0012-luma-search-beam-native-providers.patch` is the desktop correctness and
presentation follow-up. It keeps the standalone Beam and Window View split, but
routes the desktop presenter through GNOME Shell's existing
`SearchResultsView`, application catalog, SearchProvider2 discovery,
cancellation, metadata, and activation paths. This restores the exact mature
Activity View search behavior while the shared broker remains the handheld
client boundary. The patch also makes the modal container transparent, renders
the field and result list as separate 620px islands, and adds the compact
Figtree result-row, semantic kind, focus, hover, selected, empty, and no-result
states from the approved Beam study. Desktop/mobile broker parity remains an
explicit convergence gate rather than a completed claim.

`0013-luma-search-beam-layout-polish.patch` fixes the physical desktop review
findings without forking the search engine. It establishes the Beam field's
stable work-area anchor before its first allocation, keeps that anchor fixed
while provider content changes height, sizes the compact viewport in whole
result-row increments without provider separators or a fade gutter, and forces every
compact provider result—including application-owned result objects—through
the same bounded row renderer while retaining the provider-owned activation
delegate, prevents compact search actors from expanding to the monitor height,
and disables the generic dialog offscreen texture that
clipped island shadows. Search runs in the shell's Overview action mode so a
second Super press closes it, while the visible `esc` affordance is also an
accessible close button. Activity View keeps its upstream grid presentation.

`0000-luma-fedora-spec.patch` gives the package a Luma release and registers
the source patches. The build script verifies the archived Fedora source RPM
before applying them.

`0050-luma-status-island-material-owner.patch` corrects the separate Shelf
status island at its real actor boundary. The Quick Settings/status actor is
the `luma-shelf-material` itself, not an inner status child, so the patch names
its co-located material, status, and effective-treatment roles explicitly for
Light, Dark, Frost, and Glass. Connected mode continues to clear that actor and
paint only the one outer Shelf material.

`0022-luma-context-menu.patch` — applied on the shell train
(`work/shell-menu-contract`, after 0021, the shelf dock-width fix), where it is
declared as Patch1021 at release 1.luma.80. It is kept here so the one-kit
line carries the whole contract; this line's shell series does not apply it.

`0131-luma-shelf-surface-placement.patch` gives every surface opened from the
shelf one placement rule: Quick Options (from the status controls or the
clock), dock tooltips, dock menus, window previews, the Well's popover and item
menus, the live island's options, and Beam. `SHELF_SURFACE_GAP` (8 logical
pixels, `shelfMetrics.js`) is the only gap; `shelfSurfacePlacement()` puts the
surface that far beyond the desktop-facing edge of the painted island holding
the control that opened it, centred on the control, or aligned with the
island's edge when centring would cross the 14px inset. `lumaShelfSurface.js`
finds the island and is the one entry point. BoxPointer menus from an island
carry `luma-shelf-surface` (no beak) and are placed from their allocated size;
the 0062 `Math.max` override, the tooltip `-y-offset: 16px` and the menus'
`-boxpointer-gap: 8px` are removed. `tests/gnome-shell/shelf-surface-placement.js`
checks the rule at package build.

`0132-luma-beam-level-indicator.patch` presents GNOME's on-screen display as
Beam, Luma's volume and brightness level indicator. `OsdWindowManager` and its
`show`/`showOne`/`showAll` callers are unchanged. The 236 x 48 capsule uses the
Quick Options surface tokens for Light, Dark, Frost and Glass, fills to the
level (stopping at full width when over-amplified while the number shows the
real value), is placed by the shelf surface rule against the status island or
centred 8px inside the shelf's work-area edge where no shelf is shown, never
shows over Quick Options, honours reduced motion, and announces its value
politely to screen readers. Glyph geometry is Lucide (ISC, `LUCIDE-ICONS` in
the source register).

`0133-luma-silent-volume-change.patch` removes the `audio-volume-change`
sound the volume slider played. gnome-settings-daemon patch 0001 removes the
volume keys' sound and `luma-sound-theme` disables the event as a backstop.

`0135-luma-dock-items-settle-when-views-change.patch` fixes dock icons left a
few pixels wide after login on a multi-display, mixed-scale machine. A dock
item eases in from scale 0; the Shelf's monitors-changed sync removes and
re-adds its islands, which stops that ease unfinished, and the item kept the
partial scale. A stopped entrance now settles at full size and opacity, and a
stopped exit still destroys the item. `tests/gnome-shell/dock-entrance/`
reproduces it.

`0136-luma-dock-previews-resize-and-context-menu.patch`:
- Closing a window from the dock preview eases the preview to the remaining cards on one timeline, centred on its icon and `SHELF_SURFACE_GAP` from the island on every frame; with reduced motion the change is instant. Closing the last window closes the preview.
- The dock's context menu is titled with the app's name as a heading. It is grouped as `docs/design/context-menu-contract.md` groups: windows (two or more), app actions, how it starts, system actions. Rules appear only between groups.
- The menu gains an **Open at Login** switch backed by luma-background's login items (interface version 2). When an app's login start is its background agent, that switch is the agent's decision and "Run in the Background" is not shown twice.
- Evidence harness: `tests/gnome-shell/beam-shelf/dock94.js`.

`0137-luma-dock-menu-title-in-app-case.patch` titles the dock menu with the app's name in its own case ("Discord"), 13px at weight 700 — a step above the rows — in the rows' own ink for the surface it is drawn on, instead of the uppercase section heading. The menu contract has no separate title style.

`0138-luma-system-indicators-in-the-well.patch` moves GNOME's screen sharing, screencast, casting, dwell click, accessibility and keyboard indicators from the secondary island into the Well. Their own actors move, so clicks and menus still work. It also shows GNOME's recording mark (a recording remote-access handle) there, named for screen readers. `tests/gnome-shell/beam-shelf/share.js` exercises it with a PipeWire session.

`0139-luma-screenshot-shortcuts.patch` makes Ctrl+Shift+1 to 4 the only screenshot shortcuts:
- Ctrl+Shift+1: the tool as last used.
- Ctrl+Shift+2: the focused window.
- Ctrl+Shift+3: the new `show-screenshot-ui-area`, which opens straight in Selection mode.
- Ctrl+Shift+4: screen recording.

The Print-key defaults are removed. `luma-screenshot-shortcuts-migrate.service` (user, once) resets a person's keys that still hold GNOME's old defaults or the staging values; any other shortcut is kept. `tests/gnome-shell/screenshot-shortcuts-migrate.js` tests the migration at package build.

`0141-luma-beam-island-corners-and-slider.patch`:
- Beam uses the shelf islands' radius, `SHELF_ISLAND_RADIUS` (15, new in `shelfMetrics.js` and now also used by the islands).
- Beam is a slider. A press sets the level, a drag follows the pointer, scrolling steps it by 2%, and the volume glyph toggles mute. The level is written through Quick Options' own objects: the output stream slider, with its over-amplification limit, and the brightness manager's scale.
- Hover and dragging hold the 1.5 s hide timer.
- Beam never takes keyboard focus. It is exposed as an ATK slider.
- Evidence harness: `tests/gnome-shell/beam-shelf/beamslider.js`.

`0142-luma-capture-screenshot-tool.patch` replaces GNOME's screenshot panel with Capture (`js/ui/lumaCapture.js`, `data/theme/luma-capture.css`):
- One bar with Close, three screenshot modes (entire screen, a window, a selection), two recording modes (entire screen, a selection), Options and a labelled Capture/Record button, on the Quick Options surface in every appearance mode. `placeShelfBar` and `placeFromShelfBar` in `lumaShelfSurface.js` place the bar `SHELF_SURFACE_GAP` beyond the dock island (or `SHELF_INSET` above the work area with the dock at a side), and the Options menu, tooltips, recording pill and thumbnail from it.
- The selection opens empty and is drawn by dragging; choosing a selection mode draws the last selection (Remember Last Selection) or a default box. It has a dashed outline, eight handles, a size label in physical pixels, a 24 × 24 minimum, and a double click takes it. Windows stay in place: the one under the pointer gets an accent outline and wash, and a click takes it.
- Ctrl+Shift+1 opens the tool in its last mode with nothing selected, Ctrl+Shift+2 (`screenshot-window`) opens window mode waiting for a pick instead of capturing the focused window, Ctrl+Shift+3 opens selection mode, Ctrl+Shift+4 opens recording mode or stops a recording.
- Options: Save to (Pictures and Videos keep their Screenshots and Screencasts folders; Desktop; Clipboard for screenshots; another folder through the portal's file chooser), a 5 or 10 second timer with a countdown over the live screen, Show Floating Thumbnail, Remember Last Selection, Show Pointer. Every choice is in `org.project_luma.capture` (gnome-shell-common). Messages and Record Sound are not offered: GNOME 50.3's recorder captures video only.
- Files are "Screenshot YYYY-MM-DD at HH.MM.SS.png" and "Recording YYYY-MM-DD at HH.MM.SS.webm". A floating thumbnail replaces the screenshot and screencast notifications, and clicking it opens the file; failures still notify. A recording shows a pill with the time and Stop where the bar was, and GNOME's screen recording indicator no longer appears. Escape, the pill's Stop and Ctrl+Shift+4 stop it.
- The bar, menu, pill, countdown, flash, thumbnail and a recording's outline are drawn only in the screen's own paint, never in a screenshot or recording: those paint the stage without a redraw clip.
- Evidence harness: `tests/gnome-shell/capture/`.

`0143-luma-window-motion-to-dock-icon.patch` (`js/ui/lumaWindowMotion.js`, `js/ui/lumaWindowPath.js`):
- Minimize draws a window into its app's dock icon in 320 ms, and restore draws it back out. The icon is the dash's `getShelfIconGeometry`, then the dock island's centre, then the middle of the shelf edge on the window's monitor. The path follows the dock's edge: the window narrows along the shelf first and then pours into the icon.
- An app launched from an icon (dock or search results, through `AppIcon.activate`) grows its first normal window out of that icon in 280 ms. The launch is kept for 8 s and used once. Other windows grow from 94%. Close settles to 95% and fades in place in 180 ms.
- Only the window actor's scale, translation and opacity change, on one Clutter timeline. A restore that interrupts a minimize (or the reverse) turns around from where it was. Reduced motion completes at once, and the slow-down factor applies.
- `WindowManager.revealWindow(actor)` plays the opening for a window that was held hidden until placed.
- Unit test at package build: `tests/gnome-shell/window-motion.js`. Evidence harness: `tests/gnome-shell/beam-shelf/motion.js`, `matrix101.sh`.

`0144-luma-capture-thumbnail-drag.patch` lets the Capture thumbnail be dragged into apps. The Shell starts `luma-capture-drag` (GTK 4, libexecdir) as its own Wayland client (`Meta.WaylandClient`, hidden from the window list, above, on every workspace, never keeping focus) and lays its transparent window over the thumbnail. A drag offers text/uri-list (plus the portal file transfer) and x-special/gnome-copied-files; not image/png, which Files and other GTK apps would prefer and save as "Dropped Image.png". A drop dismisses the thumbnail and keeps the file; a click still opens it. Evidence harness: the `drag` case in `tests/gnome-shell/capture/` (GTK 4 on Wayland and Xwayland, Chromium on Wayland and X11, Files).

`0145-luma-now-playing-island-stays-whole.patch` (`js/ui/lumaMediaIslandGuard.js`):
- The now-playing island's show and hide end in a known state however they stop: a show cut short by anything but a hide still leaves the island opaque at its natural size, and a hide cut short by anything but a show still hides it. A stopped title or artwork fade leaves it opaque.
- A guard checks the island about a second after it last changed with nothing animating. If it is still translucent, held narrower than its content, has its controls hidden or squeezed out, or its title button is insensitive, the guard restores it. It logs one journal line starting "Luma shelf: the now-playing island was left" with each part's opacity, size against natural size, style classes, pseudo-classes, the title font, and the last show/hide and fade with what stopped them. The line is written at most once a minute; the times in between are counted.
- Evidence harness: `tests/gnome-shell/now-playing-island/` (`guard.js`, `beforeafter.js`, `media.js`, `chrome-mpris.py`).

`0147-luma-dock-unread-badges.patch` (`js/ui/lumaDockBadges.js`, `js/ui/lumaDockBadgeModel.js`):
- Ember unread badges on dock icons: a 15px count pill (99+ past 99) or a 9px dot, ringed in the shelf's own surface colour, top right of the icon at every dock edge. It lives inside the icon beside the masked artwork, so it rides hover, press, drag and the launch zoom and never changes the dock's layout; the dock viewport clips along the shelf only when the dock scrolls.
- Counts come from the standard `badge` value of an app's background agent (only agents that declare it; never started to read it), then from `com.canonical.Unity.LauncherEntry` (a Flatpak sender may badge only itself). `org.gnome.shell dock-badges-disabled-apps` turns an app's badge off.
- Owner questions are one-line switches in `lumaDockBadgeModel.js`: `BADGE_FILL`, `NOTIFICATION_COUNT_FALLBACK` (off), `URGENT_WINDOWS_DOT` (off).
- Tests: `tests/gnome-shell/dock-badges.js` in the verify block; evidence harness `tests/gnome-shell/dock-badges/`.

`0148-luma-dock-badge-deeper-ember.patch`: the badge fill becomes `#c24a0b` (owner, 2026-09-17), 4.91:1 with white against `#f26a1b`'s 3.06:1, which failed WCAG AA at 9.5px. Nothing else changes, and `tests/gnome-shell/dock-badges.js` now fails if white on the fill drops below 4.5:1. `tests/gnome-shell/dock-badges/flatpak-proxy.sh` records what a Flatpak app may do with `com.canonical.Unity.LauncherEntry`: with no extra D-Bus permissions it may emit `Update` through its proxy (which is all a badge needs) and may not own `com.canonical.Unity`, so no Flatpak permission is widened for badges.

`0149-luma-screen-sharing-picker.patch`: Share, the screen sharing picker, with the violet sharing edge and the Stop pill. `lumaShare.js` and `luma-share.css` draw the card in the authentication prompt's language (0146) over live previews of every window and screen; `luma-portal` answers `org.freedesktop.impl.portal.ScreenCast` and asks the Shell for the choice over `org.projectluma.Shell.ScreenShare`, then creates the stream through Mutter itself. ADR-041 records the decision. Window previews are the dock's own renderer (0117), now exported instead of copied; a screen preview clones `global.window_group`, so no preview can contain the picker. The edge and the pill are `CaptureHidden` actors (0142), so they never appear in the stream, and the pill sits in the shelf surface slot through `placeShelfBar` (0131). `messageTray` holds banners while sharing and shows them after Stop; the Well's sharing mark (0138) stands down while the pill is up. Owner questions are one-line switches at the top of `lumaShare.js`: `KEEP_WELL_MARK_WHILE_SHARING` (false), `PILL_VISIBLE_MS` (0, always), `OFFER_CHOOSE_ON_SCREEN` (false). The card carries the ink ledger (0150) for its own stylesheet: frost and glass are smoked veils with white ink, and the Share button and Stop pill keep a light plate on hover; `data/theme/verify-treatment-ink.py` passes on it, and 0149 and 0150 touch no file in common. A second pointer click on the chosen preview shares (no button-press handler); Space only selects and Enter shares. Tested end to end in a headless session through the real portal stack by `tests/gnome-shell/screen-sharing/` (54 checks, light and dark, 100% and 200%), which caught four bugs now fixed here: the screen edge's violet was drawn by the CaptureHidden actor itself and so reached every screen stream (it is now a child), `addTopChrome` threw on an input-region parameter this Shell rejects so a shared screen had no edge or pill, the title was not balanced, and tiles were sized before they were on the stage. Tests: `tests/gnome-shell/screen-sharing.js` in the verify block (52 assertions against the packaged module and stylesheet, including that no button-press override returns), and `tests/luma-portal/test_screencast.py` in the portal's `%check`.

`0151-luma-well-glyphs-and-hover-rhythm.patch`:
- Every glyph in the status row is drawn at the system indicators' 14px and in the surface's ink: Luma's own Well items, an application's tray icon and an extension's indicator. No full-colour logos in this row (owner, 2026-09-17).
- `Well._normaliseExtensionIcons` takes the theme's symbolic variant of an adopted icon where there is one; otherwise the icon keeps its own shape, holes included, through the `luma-well-silhouette` shader the Well's own items already use, inked from the actor's own foreground colour. It runs again on every Well rebuild, so an icon the application swaps is caught.
- An icon with no transparency has no shape, so its silhouette is a filled block. It is inked all the same: a third party's colour is outside the ink ledger's guarantee and can land on the surface's own luminance, where an icon shadow cannot save it — measured at 1.00:1 on light and dark, which carry no icon shadow, and 1.03:1 to 1.24:1 on the smoked treatments (ink agent, 2026-09-17). `_hasShape` measures the icon file's transparent fraction (a shape needs more than 4% clear) only so the Shell can say once in the journal which application sends one.
- A silhouetted glyph is inside the ledger's guarantee: `.luma-well-glyph` carries geometry only, so the shader inks it from `.luma-well-item`, which is the ledger's `ink` in all four modes.
- Measured for the worst case, a filled block of ink on veil plus recess, at 100%, 125% and 200% over six backdrops with the shadow discounted: 9.53:1 on light, 10.88:1 on dark, 8.93:1 on frost and 8.09:1 on glass, against a 3:1 threshold for a glyph (ink agent, 2026-09-17). The gate discounts the shadow entirely, so a block and a stroke of the same ink on the same surface measure the same; shape only changes how much the shadow would have added. That is also why the shadow could not rescue a colour block whose raw number was 1.00:1.
- The Well's items, an extension's indicator, the clock and Quick Options share one highlight box: 28 high, radius 8, 7px of air around a lone glyph and 8px beside text, inset 3px inside the Well's recess and 4px inside the island. The hit target keeps its full height. Pressed is the same box in the pressed fill. The dock's tiles keep their own language, where the artwork lifts and nothing is drawn behind it.
- Hover and pressed fills are the ink ledger's `hover` and `press`, per appearance mode (light and dark 0.06 and 0.09, frost and glass 0.07 and 0.11), replacing the older per-widget fills. The Well's recess keeps no pair of its own: the veil under it carries the headroom. No ink, shadow, veil or recess value changes here, and `tests/unit/lumaSurfaceInk.js` gates every state stack at %check.
- A dock tile still has no fill in any state: its artwork lifts instead, which is the dock's own language.
- The status cluster's hover wash is an actor, so it composites over a button that already carries a fill. `['press', 'hover']` is added to `STACKS` in `tests/unit/lumaSurfaceInk.js` so the gate measures that combination; every other target presents one fill at a time, because a rule replaces a fill rather than stacking it.
- A 1px ink rim around a full-colour logo was measured as the alternative and rejected (ink agent, 2026-09-17): a logical 1px rim fails light mode at 100% (2.53:1 to 2.69:1 against 3.0) because one device pixel dilutes to half coverage on a half-pixel boundary, and a physical 1px rim fails at every scale for the same reason. A 2px logical rim passes with room (worst 6.69:1) but leaves a 10px core inside the 14px box, which no brand logo survives. If brand colour in this row is ever wanted, the answer is a larger glyph box, which is row geometry and a spec question, not an ink one.
- Focus on the shelf is drawn only during keyboard navigation (`js/ui/lumaFocusVisible.js`), as :focus-visible works on the web. St marks a widget :focus whenever it holds key focus, and a menu returns key focus to its button when it closes, so after a click on Quick Options, the calendar or a menu the status island stayed ringed (`.luma-status-cluster:focus`, a 2px #91b6d2 ring) and a dock icon kept a blue glow around its artwork. Key focus is unchanged; inside the shelf the :focus pseudo-class is added after a navigation key (Tab, arrows, Home, End, Page keys, F10) and removed by the next click or touch. The status grid mirrors the drawn :focus rather than held key focus (`panel.js`). What is drawn is a 2px ring in the treatment's ink; the old pale blue ring measured 1.9:1 on light paper. After a keyboard close (Escape), focus returns to its button and is ringed on purpose, so a keyboard user can see where they are.
- The now-playing island never highlights as a whole: its artwork and title raise the player and paint nothing on hover or press. Only its controls, and a Live Extension's actions on the live island, take a hover and a press, now in the ledger's fills. Before, they carried per-widget values from before the ledger (0.14 white on glass, 0.10 on frost, 0.07 dark on light), with a generic `rgba(127,127,127,0.2)` grey wash underneath. An engaged call control keeps its red.
- An island that is one action highlights edge to edge (`ShelfIsland.setBody`): the beacon (a notification) and the live island (a Live Extension) wash the whole material at its radius, as an inset shadow over the veil, in the ledger's hover and press, and show keyboard focus as a ring on the same edge; buttons inside highlight on top. The body button itself paints nothing on the shelf. Before, only that inner button painted, inset by the island's padding, and the light stylesheet (light, frost, glass) gave a Live Extension a `rgba(255,255,255,0.92)` hover and a blue `rgba(72,120,184,0.14)` press that the shelf never overrode. The now-playing island is several controls and takes no island wash. `['hover','hover']` and `['hover','press']` join the ledger gate's stacks; a press on an inner button does not press the island, so two presses never stack.
- Evidence harness: `tests/gnome-shell/well-rhythm/` (`well.js`, `focus.js`, `islands.js`, `matrix.sh`).

`0155-luma-quiet-filled-controls.patch` (`js/ui/lumaControlInk.js`, `data/theme/luma-controls.css`): Quick Options' filled controls follow ADR-043. Every on state (an on tile's icon plate, a checked pill, a keyboard-brightness step) takes the one `state` slate with `stateInk` on it; slider handles are a `knob` disc ringed in `knobRing`; chevrons take no plate, only a hairline; Notifications and Calendar are quiet buttons from the ledger. `CONTROL_INK` carries the app kit's values. `data/theme/verify-treatment-ink.py` reads both ledgers and fails the build if `luma-controls.css` spends a colour its mode does not name, if a fill or ring drops below 3:1 or a glyph below 4.5:1, or if the notification island's hover and press leave the ledger or its ink drops below 4.5:1 in any hover or press stack.

`0154-luma-screenshots-match-blurred-surfaces.patch` makes screenshots show frost and glass surfaces as the screen does. `ShellBlurEffect`'s background mode, painting off stage (a screenshot or recording), assumed scale 1 at the stage origin; it now reads the scale and origin from the capture framebuffer's viewport and never copies from outside that framebuffer. Area screenshots through D-Bus (and the colour picker) paint the whole stage and keep the rectangle, so a blurred surface on the edge has its whole backdrop. Evidence harness: the `blur` case and `blurcheck.sh` in `tests/gnome-shell/capture/`.

`0156-luma-capture-hidden-backgrounds.patch`: a privacy fix for Capture. `CaptureHidden` (0142) keeps an actor's children and content out of screenshots, recordings and screen shares, but Clutter paints an actor's own background, border and effects around `paint()`, so the countdown's dark disc and backdrop blur and the flash's white went into every recording or share running during a timed screenshot (76 of 178 frames carried the disc, 12 the flash). The disc, its blur and the flash's white are now children of their `CaptureHidden`, and a comment on `CaptureHidden` states the rule for anything added later. Nothing changes on screen. Evidence: `tests/gnome-shell/capture/eval.js` and `hiddencheck.py`; the verify block checks the packaged module.

`0157-luma-frost-and-glass-menus-follow-the-ink.patch`: popup menus under frost and glass (the desktop menu, the dock and Well menus, the calendar) take the same smoked veils as Quick Options, with 0150's white ink and shadow, the ledger's smoked hover and pressed fills, and white separators, headings and sub-menus, in both variant sheets. They had kept 0045's light frosted veils with dark ink, so the inherited ink shadow smeared their text and white-inked labels vanished. Found by the .112 integration renders.

`0158-luma-live-islands-survive-appearance-changes.patch`: 0151 inked a Well glyph by connecting `style-changed` on it, which a plain Clutter tray actor (a pixmap or XEmbed icon) does not have; a tray icon recreated on a theme change threw on every shelf rebuild, before `_adoptPanelActors` put the live and now-playing islands back, so they vanished on Dark to Light. A plain actor now inks from its nearest St ancestor, a failing adoption is logged and skipped, and the light live-island subtitle ink goes to 66% (5.2:1). `tests/gnome-shell/live-modes/` switches Dark, Light, Frost, Glass, Dark with a Live Extension, a playing tab and a self-replacing pixmap tray icon, and asserts both islands are on the stage, sized and readable in each mode.

`0159-luma-detail-header-glyph-in-light.patch`: a Quick Options detail's header glyph is the detail's ink whether its toggle is on or off; upstream's `.active` rule painted it white for an accent disc Luma does not draw, so in light it vanished on the white detail (Wi-Fi, Bluetooth, Tiling and every other detail with its toggle on). The light detail subtitle ink moves to #6b7076 (5.0:1). `tests/gnome-shell/quick-details/` opens every detail in all four modes, toggle off and on, and asserts the header glyph (>= 3:1) and header text (>= 4.5:1).

`0160-luma-notifications-live-in-the-shelf.patch`: notifications live in the shelf (owner, 2026-09-18). The resting-notification beacon becomes the notifications island, the last island of the row after Quick Options, which it pushes towards the dock; ordinary notifications no longer show a banner. It shows the newest (mark, title, first body line, count pill), opens it on click, opens the 0116 stack from the count, offers up to two actions and a dismiss button on hover or focus, dismisses on a swipe (72 px) or fling (24 px at 0.5 px/ms) to the right or Delete, and leaves with the last one. It grows in from the row's end in 200 ms while the other islands slide aside, and only fades with reduced motion. Urgent notifications and calls keep a card, above the island. The shelf keeps its islands by ADR-044 id (`Shelf.getIsland(id)`, `island.islandId`, actor name `lumaShelfIsland-<id>`). It also makes a shelf sync requested inside a sync wait for the running one and empties the row from a copy: a nested sync had aborted the Shell in `clutter_actor_remove_all_children` (reproduced with a player playing at startup). `tests/gnome-shell/notification-island/` renders 0, 1 and 3 notifications in all four modes and the arrival frames, and exercises hover, dismiss, count, swipe, fling, click, urgent and reduced motion.

`0161-luma-quick-options-highlights-as-one.patch`: no segment of the Quick Options island (Well items and extension indicators, status icons, clock) paints a fill or ring of its own; the shelf washes the whole island on hover, presses it while one of its menus is open and rings the whole island for keyboard focus. Also fixes a blank symbolic extension glyph in the Well (its gicon was cleared after its name was set). `tests/gnome-shell/island-segments/` checks every segment at rest, hovered and focused in all four modes.

`0162-luma-capture-thumbnail-throw-menu-keys.patch`: the screenshot thumbnail is dismissed by a throw toward the right edge (at least 40 px, mostly level, peak 0.6 px/ms, not resting before release); short or slow drags spring back and drops still deliver the file. A right-click, Shift+F10 or Menu opens Open, Open With (default first, Other Application… via the portal), Copy, Copy File, Show in Filer, Rename… (in place) and Move to Trash (with an Undo pill for six seconds); the thumbnail stays while its menu is open. Delete trashes, Escape dismisses, Enter opens. `tests/gnome-shell/capture-thumbnail/` drives each of these with a virtual pointer and keyboard.

`0163-luma-sticky-dock-only-with-sticky-notes.patch`: the sticky note stack (0078, 0079) is built only while Sticky Notes is installed (its desktop entry is known, or `org.projectluma.StickyNotes` is activatable), rechecked on `installed-changed` and added or removed live; without it there is no dead "+" rail and nothing is logged as an error. `tests/gnome-shell/sticky-dock/` starts without the app, installs and removes its desktop entry, then a D-Bus service file alone.

`0164-luma-quick-options-match-the-design.patch`: Quick Options matches the Design Center again. Two shapes: Wi-Fi, Bluetooth and any other connection are 64px cards; every other switch is a 44px pill (icon and one word, no status line) in a three-column grid ordered Tiling, Location, Warm light, Airplane mode ("Airplane"), Keyboard, Cast, then GNOME's other switches. `QuickSettingsLayout` counts six units (card 3, pill 2, level 6) and never leaves a hole: two pills on a last row take half the width each, one pill or a lone card the whole width. An on pill takes the design's tinted wash (no longer 0155's filled slab); a pill with a menu toggles and opens its menu from a boxless chevron inside its trailing edge (26px of layout, 32px hit area through `QuickDisclosureButton`'s pick overhang). Cards and levels share one 30px raised disclosure with a filled chevron (`luma-disclosure-{down,up}-symbolic`). Volume reads "48% · <output>", every level keeps the disclosure's slot, and the microphone row (GNOME's rule: only while an app records) sits under it. Detail sheets move from the overlay into the grid: in flow under their owner's row, one at a time, drawn with their owner as one shape by `QuickSheetFrame` (`-luma-sheet-fill`, `-luma-sheet-line`, `-luma-sheet-radius`), titled in sentence case with a muted count and no icon, listing `QuickSheetRow` choices (two lines, whole-row button, raised fill and dot for the current one) and ending in plain settings links. The Notifications and Calendar buttons and the background-apps row are no longer shown; the background-apps indicator still runs. Cast shows only with a network or a known screen. `tests/gnome-shell/quick-details/` asserts every sheet is in flow, joins its owner, repeats no icon and keeps its title and count at 4.5:1 in all four modes.
`0167-luma-movable-shelf-islands.patch`: movable shelf islands (ADR-044). Any island (dock, live, media, notifications, Well, Quick Options, clock) is held still for 400 ms, or chosen from the Shelf menu (right-click: Arrange Islands…, Reset Island Layout, Shelf Settings…), or moved with Ctrl+Alt(+Shift)+arrow, and dropped on any edge or corner, free or attached to another group. The shelf becomes a controller over groups of attached islands, one tracked chrome actor per group, placed by the pure model `shelfArrangement.js` (zones, band solver, status fusion, drop targets, keyboard steps, announcements, struts, displays); arrange mode lives in `shelfArrange.js`. The arrangement is the shell-state key `shelf-arrangement` (luma-shell-state 1.luma.27); empty is today's layout, and a spanning shelf of separate islands is drawn as two groups (dock at the start, the rest at the end), exactly as before. One strut per occupied edge per monitor; bands stay reserved while their island is hidden; struts never move during a drag. Surfaces, the notification tray and banners follow each island's own edge. `tests/gnome-shell/shelf-arrangement.js` (gjs, run in %check verification) covers the model; `tests/gnome-shell/shelf-arrange/` drives the owner's scenarios, every island to every zone, attach and detach, struts and Tiling Shell, keyboard, the menu, the tray, a restart, two monitors, four modes and drag cost in the headless oracle.

`0170-luma-frost-and-glass-are-one-light-material.patch`: frost and glass are one light material, with application windows (owner, 2026-09-18: "dash is dark? and titlebars are not frost"). SURFACE_INK's frost and glass entries are light veils (frost islands rgba(246,247,249,.80), menus and sheets .88; glass .62 and .80) with ink one step deeper than paper's, and `LIGHT_FAMILY` names light, frost and glass. Every stylesheet rule for `luma-surface-light` also names frost and glass with the translucent veils swapped in, the smoked frost/glass colours of 0150, 0157, 0164 and 0167 are gone, menus, the beam and the prompt/share/capture cards take the veils, the notification Sass maps are regenerated, the status cluster and Well take the light recess, and the Shell loads its light variant sheet for frost and glass. Blur: frost radius 38 saturation 1.6, glass 18 and 1.35. `tests/unit/lumaSurfaceInk.js` measures every veil, ink and state stack over black, mid grey and white; `verify-treatment-ink.py` refuses near-white ink on the light family.

`0171-luma-dash-islands-on-every-edge-and-every-setting.patch`: the owner's feedback on movable islands. Shared monitor edges take islands, reserved through Mutter 0016's `meta_display_set_monitor_edge_reservation()` (closed as before without it). A drop goes to the zone the pointer is in, else the zone nearest the island's centre, and the ghost is a preview group of the island's real length in its target form. Islands attach or sit at start/centre/end unless `shelf-free-placement` (shell-state 1.luma.28) is on. Zones are drawn at a distance weight (22% at rest) and each edge has a faint line that steps aside around placeholders, groups and the ghost. Arranging shows a ghost live-extensions slot. Use islands off + span draws one bar, Float outer edges moves and squares the band ends, Protrude keeps the windows' padding, `shelf-reserve-work-area` and Position are honoured. Side-edge forms for media, live and the clock. The Dash menu is a kit menu reading "Dash Settings…". `tests/gnome-shell/shelf-arrange/` adds edges3, tiling3, targeting, weights, ghostlive, settings, menus and look.

`0172-luma-dash-islands-as-arranged.patch`: the owner's findings on .118 (ADR-044). Ships the `luma-live-extensions-symbolic` glyph 0171 named. Centred groups stay on the centre line (the dock shrinks both sides first); start/end reach the true ends (side bands stop at a corner only when a top/bottom band is there); hidden islands take no length and hold no edge, with arrange-mode ghosts for hidden live extensions and notifications and a `LUMA_SHELF_RESERVATIONS` journal field per change; the live and media islands are one family in one slot; attach/apart with a dead zone and hysteresis, measured against the layout at drag start; free placement exact with 12 px soft snaps and no zone grid; Use islands off draws one full-length bar per edge laid out by the band solver (all four edges alike); one 2 px full-height divider token. .120: empty edges reserve nothing (no margin strut or work-area inset unless Reserve work area is off); a click on the notifications island toggles the drawer, and the drawer and urgent cards open beside the island on any edge, moving in from it. Tests: owner, matrix, family, retile, notifplace, asarranged. .121: every edge's ends reach the corner unless a top or bottom group sits at that end (or a bar, which runs its edge); without islands adjacent bars meet as one L with no rim across the joint; the group handle is a 48 by 22 target that says what it does, and Quick Options, the clock and the status icons move together (Shift takes one out); arrange mode shows only the Live extensions and Notifications placeholders, always in their slot; empty items take no length, spacing or divider and grow in when they show; hovering the notifications island only changes its look; the drop index follows the pointer against the islands' midpoints; Tiling Shell's panel button never joins the Dash; the Dash announces work-area changes (`work-area-changing`, `Main.shelf.workAreaMotion`) and `Main.wm.lumaGlideFrame` moves a window in one glide. `Main.shelf.reservedMargins(monitor)` says how much of each edge's reservation is the margin a window keeps from an island, so Tiling Shell can make every gap the same. Tests: round121, livetile, tiles, gaps.

`0176-luma-dock-folders-glyph.patch`: ships `luma-dock-folders-symbolic`, the glyph arrange mode names for the folders island (0172). A folder with its tab and flap line, same 24-unit grid and outlined 2-unit strokes as the other island glyphs. Shell .141.
