# libadwaita downstream patch

Project Luma carries a source-level light design-system patch against Fedora
44's exact `libadwaita-1.9.3-1.fc44` source package. This is the canonical
owner for generic GTK/libadwaita colors and controls; application packages must
not duplicate these rules.

The patch changes the light named-color palette, default blue accent, shared
radii, header bars, window controls, buttons, entries, and navigation-sidebar
rows. The global application/view surface is white; sidebars use the defined
neutral gray, faint divider, 8-pixel rows, neutral hover, and solid-accent
selection. It also defines reusable Luma secondary, faint, hover, active, and
soft-accent named colors for source-patched applications. Dark palette values
are unchanged. Applications continue to use the normal libadwaita API and CSS
variables. There is no user stylesheet,
`GTK_THEME` override, injected provider, boot script, or extra theme package.
GtkPlacesSidebar's semantic revealer receives the same 7-by-11-pixel row inset
as libadwaita navigation sidebars; this preserves bookmark reveal animations
while keeping Files and native file choosers on the shared spacing contract.

The source RPM SHA-256 is
`deef9fd2804795f6c6b6ccd24ddf5ce78ad866964a3aac4c2554387ae49ed6e8`.
libadwaita retains its upstream identity, API, LGPL-2.1-or-later/MIT licensing,
and attribution.

`0002-luma-title-label-metrics.patch` owns the shared Figtree title-label
metrics used by Prairie application title components. Its explicit vertical
padding prevents glyph ascenders from being clipped while leaving application
packages responsible only for applying the semantic `luma-title-label` class.
`0003-luma-title-label-baseline.patch` corrects that component's actual optical
baseline with a 4px top inset inside a 22px minimum content allocation. This is
shared typography geometry—not an application transform or offset—and keeps
Calculator, Filer, and Calendar on one title contract.

`0004-prairie-generic-headerbar-contract.patch` makes the shared 34px header
the owner of generic title-bar geometry and deliberately leaves ordinary
`AdwWindowTitle` labels on their native intrinsic title/subtitle allocation.
It does not attach stock labels to the custom one-line `luma-title-label`
class. Its stock title line has only a 16px minimum—the measured Figtree
12.5px logical line at 1×—so GTK cannot round that label down to the 15px
allocation that sheared capital tops. No padding or optical translation is
applied. The patch also gives generic title-bar actions a 28px hit target with
a muted 14px symbolic glyph, while preserving Prairie's independent 20px
window-control target and 10px control glyph. The foreground follows
libadwaita's semantic header color, so light, dark, active, and backdrop states
remain toolkit-owned.

`0005-luma-application-components.patch` adds semantic application-surface
colors generated from Luma's canonical design tokens for Light and Dark, plus
the reusable application-identity trigger geometry. The definitions are
additive: ordinary upstream and third-party applications continue to consume
their existing libadwaita colors and controls. Luma source-integrated apps can
share window, content, ink, state, semantic icon, and reserved Frost/Glass
material names without duplicating literals or injecting a user stylesheet.
Applications still own their real menus and registered icons.

`0006-stylesheet-add-shared-Luma-work-islands.patch` adds the opt-in
`luma-window-body` and `luma-island` primitives used by native Luma work
surfaces. The toolkit owns the canonical 9px body inset, 10px radius, semantic
stroke, and material-aware near-surface elevation. The classes remain additive
and do not alter applications that have not opted into the Luma contract.

`0007-luma-application-window-elevation.patch` supplies the shared 15px frame
and simulator-matched light/dark elevation only to explicitly classed Luma
product windows. It does not alter the decoration of an unrelated application.

`0008-luma-connected-window-controls.patch` gives those same opt-in product
windows the AppKit connected control island while retaining native
`GtkWindowControls` behavior. Ordinary libadwaita applications keep their
upstream controls, and applications do not copy control geometry locally.

`0009-luma-appkit-component-boundaries.patch` locks canonical target and glyph
geometry and marks opted-in split panes as transparent at the common layer.

`0010-luma-native-surface-integrity-and-global-chrome.patch` makes the safe
window-chrome portion of the Luma contract native for every libadwaita
application: 42px Figtree title rows and the connected native window-control
surface. It does not replace application titles, actions, content, or menus.
The patch also changes work-island inset shadows into protected real borders,
uses the simulator's layered elevation, and clears split-pane edges at their
final stylesheet ownership points so no backing sidebar leaks between islands.

`0011-luma-native-command-surfaces.patch` adds the reusable command-row and
connected command-group classes used by source-integrated applications. The
toolkit owns their material, spacing, stroke, elevation, focus-safe targets,
and interaction states; each application continues to own its real actions,
menus, ordering, shortcuts, and adaptive layout. This keeps shared visuals in
one native source without pretending that a toolkit can infer app semantics.

`0012-luma-generic-application-identity.patch` supplies the compatibility-tier
identity for ordinary application-owned libadwaita windows, including windows
registered with a `GtkApplication` without using the narrower
`GtkApplicationWindow` subclass. One primary header bar
derives the localized name and registered icon from `GtkApplication`, then
uses the application's existing menubar or supported Preferences, About, and
Quit actions. It never moves or mirrors app-specific controls. An explicit
`luma-identity-button` wins, and dialogs, sheets, adaptive previews, and the
documented `luma-no-automatic-identity` escape class remain untouched. The
header bar also observes `GtkWindow:application`, covering template-built
windows whose application is assigned after the header first enters the widget
hierarchy without introducing an app-specific retry path.

`0013-luma-stable-backdrop-materials.patch` keeps opted-in AppKit title,
command, and work-island materials stable when a window becomes inactive. It
neutralizes libadwaita's generic windowhandle opacity filter only inside the
explicit Luma product boundary, preventing the pale faux-frost band observed
in background Text Editor windows while retaining native focus and elevation.

`0015-luma-application-identity-resolution.patch` resolves the automatic
identity through `GDesktopAppInfo` first, so the localized application name and
the registered icon come from the same desktop entry the shell uses, then falls
back to the window and default icon names, the icon-theme entry for the
application ID, and only then the generic executable icon. The icon is hosted in
a clipping box that carries the shared 6px identity radius, the non-interactive
display form receives the same 32px / Figtree 13px 650 geometry as the
menu-button form, and a synthesized title label that merely repeats the identity
name is hidden. `gio-unix-2.0` becomes a private build dependency on Unix.

`0016-luma-generic-command-row.patch` is the generic title/command separation.
A window-level `AdwHeaderBar` that owns the automatic identity keeps the clean
42px title row (identity, contextual title, native window controls) and hosts
its packed start/end children on a `.luma-command-bar` directly below it. The
application's real widgets are re-hosted, never cloned: their actions, signals,
tooltips, accessible names, and adaptive visibility are untouched, and
`adw_header_bar_pack_start`, `pack_end`, and `remove` keep routing into the
command groups so applications that reparent controls at runtime keep working.
Only structurally safe patterns opt in: the first top bar of the window's
`AdwToolbarView`, the window titlebar, or the leading child of a vertical
`GtkBox` window content, inside a non-transient generic application window with
no split view, dialog, sheet, or adaptive preview. Explicit Luma product windows
are excluded and `luma-no-command-row` on the header bar opts an application out.
An empty command row is never shown.

`0017-luma-native-surface-rhythm.patch` makes the marked work surface own its
rounded clip (restoring the prior overflow when the role is removed) so no child
can overpaint the island border or corners, starts the island one 9px step
below the title row or flush under a command row, applies the 12px/8px title-row
inset, keeps generic header bars materially stable in backdrop, recognises the
vertical-box-with-header content pattern, and brings application-owned
`AdwWindow` toplevels into the same contract as `AdwApplicationWindow`.

`0018-luma-identity-title-duplicates.patch` marks an application-owned
`AdwWindowTitle` whose title is the bare application name with no subtitle with
`luma-repeats-identity` while the automatic identity shows that name; only a
presentation class changes and it follows title/subtitle updates.

`0019-luma-dark-design-system.patch` mirrors the light design-system patch for
dark: window, view, header bar, sidebar, card, dialog, popover, and thumbnail
named colors come from the shared dark tokens so generic dark applications paint
on the same surfaces as the Luma frame.

`0020-luma-title-slot-and-sidebar-islands.patch` keeps only text titles on the
title row (controls in the title slot are hosted at the center of the command
row) and marks a navigation or overlay split view that is the work surface as a
split surface whose panes are sibling islands.
`0021-luma-split-island-shadows.patch` keeps those islands' deep shadow off the
neighbouring pane (negative spread of at least half the blur).
`0022-luma-sidebar-pane-command-host.patch` lets the sidebar pane's header bar
host the command row when the split view is the native work surface, so
split views with per-pane header bars (Characters, Settings) get one identity
row and one command row in the sidebar island; it carries
`luma-pane-command-host`.
`0023-luma-one-title-row-per-window.patch` makes the window provide the 42px
title row when its header bars live inside split-view panes
(`luma-window-title-row`); the sidebar pane's header becomes a pane toolbar
(`luma-pane-toolbar`, actions on the command row, no title row) and the content
pane's header a flat pane header (`luma-pane-header`); neither owns the identity
or shows window controls, and the roles lift while the split view is collapsed.

`0024-luma-menu-contract.patch` gives every `GtkPopoverMenu` the context-menu
contract (`docs/design/context-menu-contract.md`): a 15px container with 8px
padding, an inset ring and two shadows, 32px rows at 10px radius, tracked
headings, a rule between groups, shortcuts as key caps, and a destructive row
that is calm at rest. Widths are fixed per variant (`luma-menu-desktop`,
`luma-menu-dock`, `luma-menu-submenu` on the popover; none means the
application menu). The colours are `luma_menu*` tokens in both design systems.

Settings/native surfaces integration (2026-09-05): retain decision sheets0025,
preferences0026, quiet workflow0027, and add test-link0028, adaptive navigation
contrast0029 and shared sibling-island/navigation0030. 0030 promotes the
maintained Filer selection palette and divider geometry into the opt-in
luma-navigation-sidebar role, and fixes dark backdrop frame specificity.
Native header ownership persists when explicitly opted-in sibling islands
collapse; no CSS provider or runtime helper is introduced.

0028 uses GCC fat objects for test-only links, retaining every fixture and
hardening flag. Shipped library/demo links keep LTO. The spec bounds LTO to one
worker and the wrapper retains its normal RPM/SRPM build cache with --noclean.
Source provenance remains Fedora libadwaita1.9.3 plus the numbered maintained
Luma stack. The integrated release40 pin includes its matching preview20260905 suffix;
visual/native acceptance remains separate from build/source admission.

Follow-up41 adds0031: native shared-sidebar revealer inset normalization,
class-based SearchEntry geometry, one quiet material around native boxed
preference lists, inverse-ink primary actions, and keyboard-primary native
application identities when a GtkApplication menu model is supplied. Native
focus/default/disabled behavior is retained. Packaged14-assertion and shared
light/dark/HC/backdrop fixtures pass; no CSS parser errors were observed.

Release42 adds0032 at the native `_window.scss` owner: shared-token contact,
ambient and far elevation, separate inset stroke, explicit focus/backdrop
identity and no focus shadow transition. Edge states remove the outer shadow;
inner island shadows are unchanged. GTK calculates invisible surface extents.
Reserved treatment/no-frame-shadow classes are renderer contracts, not an OS
appearance switch. See `docs/changes/2026-09-07-window-elevation.md` for exact
artifacts, native rendering and guest evidence, rollback and open gates.

`0037-luma-identity-inset-and-pointer-state.patch` fixes the automatic
identity button `create_luma_identity()` builds in `adw-header-bar.c`
(`0012`): `.luma-identity-button` and its inner `button` node each carried
their own `min-height: 32px`, stacking with the inner node's own vertical
padding to a ~40px tall highlight that touched the header bar's top and
bottom edges instead of the 24px-min-height/5px-padding rhythm every other
header-bar button uses. Both the interactive (`menubutton > button`) and
menu-less (`box.luma-identity-button`) variants now use that same 34px total.
The same patch adds `luma_identity_guard_pointer_state()`, wiring motion,
focus and a capture-phase legacy event controller onto the button so a
stuck `:hover`/`:active` — as seen when a tiling resize's implicit pointer
grab ends (Ctrl-break, then mouse-up) without GTK routing a crossing event
back to the button — is cleared on pointer leave, focus leaving the button,
`GDK_GRAB_BROKEN`, or `GDK_BUTTON_RELEASE`, rather than relying on a
crossing event a grabbed gesture may never deliver. The same patch also adds
`luma_identity_guard_initial_focus()`: a freshly mapped window gives its
first focusable widget initial keyboard focus, marked focus-visible, and the
identity button is usually that first focusable widget, ahead of the
window's actual content, so it was the one seen lit with a focus ring the
moment a window opened with no keyboard used at all. The guard releases
exactly that first, silent focus assignment back to nothing so GTK's own
focus chain moves on to the window's content, while a later, genuine Tab or
mnemonic to the button still focuses and rings it normally; it disarms
itself for good the first time the window sees a real key press, click, or
touch. This is shared `AdwHeaderBar` code, so every Luma application gets
all three fixes, not only the one where each was first reported.

`0039-luma-quiet-filled-controls.patch` gives native apps the one filled-control
treatment of ADR-043 (`docs/decisions/043-filled-controls-one-emphasis-per-view.md`).
A checked button (raised or flat), a toggle group's selected toggle and a
scale's value fill with `--luma-state` and write `--luma-state-ink`; a scale's
handle is a `--luma-knob` disc with a 2px inset `--luma-knob-ring`. The four
custom properties are defined on `:root` for light and dark, and redefined on
frost and glass header bars, which are smoked (`0038`). Suggested, destructive,
opaque and OSD buttons keep their own fills: a suggested action is the one
emphasis a view may keep.

1.luma.49: 0037's root input watcher (`luma_identity_note_real_input`) was
declared `void` though `GtkEventControllerLegacy::event` returns `gboolean`, so
the capture-phase controller on every window root returned garbage and stopped
the event: libadwaita windows with the automatic identity showed hover but took
no clicks, drags or keys. It now returns `GDK_EVENT_PROPAGATE`.
`tests/tiling-shell/window-memory/launch-input.js` clicks and drags a freshly
launched libadwaita window.
