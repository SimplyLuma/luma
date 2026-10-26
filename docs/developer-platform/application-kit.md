# The Application Kit

One kit, in one place. This document is the contract every Luma application is
written against, and the one a person or a model should be handed before
building a new application. If something here disagrees with an application,
the application is wrong.

## Who draws what

There are exactly two parties, and the line between them does not move.

**The toolkit draws the frame.** Luma's patched GTK 4 and libadwaita draw the
window frame, its elevation, the title row, the identity in the top-left
corner (the application's name and icon, resolved from its desktop entry, with
its menu), the window controls in the top-right, the 9px gutter, and the
island — its 10px radius, its inset ring and its two-layer shadow. They do this
for every window on the system, including Filer, Calculator and Calendar,
which is why a native Luma application cannot be allowed a second answer.

**The kit draws what is inside an island.** Toolbars, buttons, connected groups,
segments, sidebar rows, status bars, groups, sliders, fields, avatars, empty
states. The kit's sheets state those measurements once, and an application
inherits them by using the kit's widgets.

An application draws neither. It composes.

## Opting in

A window becomes a Luma window by carrying two classes:

- `luma-app-window` on the window;
- `luma-titlebar` on its `AdwHeaderBar`.

That is the whole contract. `AppWindow` (Python) and `LumaApplicationWindow`
(C) do this for you, and add nothing of their own to the frame. Islands are
`Island` / `luma_pane_new()`, which carry `luma-island`; the toolkit does the
rest.

The identity's menu comes from the application: `AppWindow` builds a
`GMenuModel` from the command registry and installs it with
`set_menubar()`; a C application calls `luma_application_window_set_menu_model()`
which does the same. The identity's name and icon come from the desktop entry
and cannot be overridden — the corner of a window says what the dock says.

## Presentation and status safe areas

The session's resolved presentation is independent of the window's width and
input capabilities. AppKit follows Desktop, Tablet and Mobile changes in the
same live window. Width continues to choose the application's pane layout.

In Tablet presentation the shared frame paints through the transparent system
status strip. Sidebars and page islands begin 52 logical pixels below the top;
the status strip itself occupies 44. Applications must not add a second status
background, gradient or shadow over this region.

An application with an edge-to-edge background, such as Weather's sky, opts in
with `AppWindow.set_edge_to_edge(True)` or
`luma_application_window_set_edge_to_edge()`. Phone presentation also uses
`set_phone_bleed(True)` / `luma_application_window_set_phone_bleed()`.
Phone bleed alone retains the shared Tablet safe area. An explicitly
edge-to-edge application's background reaches the bezel;
its foreground content reserves `status_inset` (C:
`luma_application_window_get_status_inset()`) plus the normal content gap.
This inset must follow presentation changes even when the width stays constant.

`set_status_bar_style("light" | "dark" | "auto")` declares the required status
ink; the C equivalent is `luma_application_window_set_status_bar_style()`.
It describes foreground contrast and never paints a bar. The shell receives
the declaration through the window's standard GTK action group.

## Tokens

Colour is named, never written. The vocabulary is the toolkit's:

| Token | Design name | Meaning |
| --- | --- | --- |
| `@luma_window` | ground | the window's ground, seen in the gutter |
| `@luma_content` | surface | an island's surface |
| `@luma_ink` | ink | text and glyphs |
| `@luma_muted` | muted | secondary text |
| `@luma_faint` | faint | placeholder and disabled |
| `@luma_line` | `--f-line` | rules, rings and hairlines |
| `@luma_fill` | `--f-fill` | a quiet fill: segments, notes, inputs |
| `@luma_hover` | `--f-hover` | the pointer's fill |
| `@luma_selected` | `--f-sel` | the selected row's fill |
| `@luma_accent` | accent | the one accent |
| `@luma_blue` `@luma_green` `@luma_violet` `@luma_amber` | tones | identity and category tones |
| `@luma_destructive` | danger | destructive actions |

`@luma_border`, `@luma_passive` and `@luma_pressed` are aliases of
`@luma_line`, `@luma_fill` and `@luma_selected`, kept so existing sheets keep
resolving. New sheets use the toolkit's names.

A literal colour in an application's stylesheet is a defect.

## What an application may not declare

An application's stylesheet must not contain rules for any of these; each is a
second answer to a question the toolkit or the kit has settled:

- `windowcontrols`, `headerbar`, `.luma-titlebar`, `.luma-identity-*`
- `.luma-window-body`, `.luma-island`, `.luma-pane` — their radius, ring,
  shadow, margin or padding
- a `border-radius` on the window itself
- a `box-shadow` on anything that is an island
- a `@define-color`
- a hex or `rgba()` colour

`tests/unit/test_luma_design_contract.py` enforces the kit's side of this;
the applications' side is enforced by the same test as they are brought
across.

## Consuming the kit

The kit is a package, not a directory to copy. Applications build against
`luma-developer-platform-devel` — `pkg-config luma-ui-1` for C, the installed
`luma_appkit` module for Python — and pin the release they were verified
against. A copy of `src/luma-platform` inside an application's repository is
a fork, and a fork is how the twelve applications came to disagree.

Native shared navigation (candidate2026-09-05): a source-owned native list
may opt into `navigation-sidebar luma-navigation-sidebar`. It inherits the
Filer selection palette,32px minimum rows,14px symbols,6px list inset and
`luma-navigation-divider` spacing. Row content must wrap rather than forcing
translated labels to clip. `luma-sidebar-search` styles a real GtkSearchEntry.
An explicit `luma-sibling-islands` window provides its outer titlebar through
the existing `luma-window-title-row` object-data contract; native pane header
ownership then remains stable when adaptive panes collapse. Applications
retain their native navigation stack and actions. Islands use real widget
`overflow: hidden` to clip their child surfaces to the shared radius.

Follow-up shared workflow contract0031: source-owned chooser windows opt into
`luma-app-window`, explicit `luma-titlebar` and `luma-window-body`, with real
clipped `luma-island luma-island-quiet` children. A quiet island owns the single
outer material around native boxed preference rows. `luma-controls-quiet`
retains native control behavior and gives suggested actions inverse ink/content
colors. Shared navigation owns the row inset; native PlacesSidebar revealers
inside opted-in rows do not add another inset. Applications can supply their
existing GtkApplication menu model to the shared identity; it remains a native
keyboard-primary menu trigger. No application-local stylesheet is required.

## Empty panes and empty lists

`EmptyState(title, description, icon_name, primary=(label, callback),
secondary=(label, callback))` is the shared centred pane component. Keep one
primary action; a secondary action is optional and requires the primary. Both
callbacks must target the existing application commands. The kit owns the 84px
fill disc, 38px symbolic glyph, 19px title, 13px description, 320px text bound,
spacing, and light/dark tokens. Long action labels wrap without hiding actions.
The original three positional arguments remain supported for existing callers.
`ListEmptyState(text)` is a separate centred muted label with 24px padding and
no glyph or action. Application headers keep their allocated height when their
children are hidden. See `docs/changes/2026-09-06-core-empty-states.md` for the
Messages/Notes/Contacts source candidate and observed/open acceptance gates.

### Home grid editing

The native Mobile and Tablet Home presentations share one occupancy model and
ShellState persistence API. Apps occupy one cell; registered native widget hosts
provide integer column/row spans. Long press enters editing and starts a drag
with the held pointer. Holding at a screen edge changes pages or previews a new
page. The layout commits only after a valid drop; Escape cancels a drag. Done
leaves editing. Keyboard users can open an item's context menu and move it with
Ctrl+arrow keys or Ctrl+Page Up/Page Down.

Layouts are stored under `home-layouts` in the shell-state schema, keyed by
presentation and logical grid dimensions. `SetHomeLayout(profile, json)` validates
bounds, identity, occupancy and page limits before saving. A different viewport
reflows from the nearest saved grid in that presentation without overwriting the
original arrangement. New graphical applications append without moving arranged
items; hidden and terminal-only desktop entries are excluded. Widget factories
must supply real data and interactions; no example cards ship in the presentation.


### Shared Home widgets

Weather and Calendar use `src/luma-home-widgets`, extracted from Mobile's native
widget implementation. One shared model owns cache safety, freshness, units,
events, monitors, widget definitions and app launch IDs. Native St and GTK3 views
render those fields using generated shared design tokens. Shells own placement
and activation; they must not implement separate forecast or calendar providers.
Desktop placement can use the same component when its Home surface is available.
The Phosh adapter requires the maintained Mobile baseline and separate image
qualification; its existence does not imply a delivered phone build.

### Tablet tasks

Tablet normally presents one app fullscreen. A short upward Home-bar swipe
reveals the dock; dragging an app from that dock or its searchable library to a
side creates a pair of real app windows. Sliding the Home bar sideways places
the current app in one half and shows Home in the other. Each app receives its
actual allocated width. The divider returns to equal halves after resizing;
dragging it to an edge returns the remaining app to fullscreen. Moving a half's
bar toward the seam swaps sides; moving toward the outer edge removes that side
without closing its documents.

An upward swipe and hold opens the existing Desktop Grid switcher. Grid keeps
its native window enumeration, previews, navigation and close actions; activating
either member of a pair restores that pair. In Tablet, long-pressing an existing
Grid card starts the same native window drag used by the dock and library.
Dropping on a side places that real window; cancelling returns to Grid without
changing app placement. Desktop keeps its existing browsing gestures.
Desktop also supports long-pressing a Grid card: the held preview reveals the
existing tiling zones for the selected layout. Drop commits through the tiler's
normal placement API; cancel restores the original tile, geometry and window
visibility. The tiler remains responsible for layouts, monitor coordinates,
spacing, preview rendering and window memory. This gesture does not enable a
disabled tiler or replace Tablet's current fullscreen/pair policy.
Super+Left/Right places the
focused app in a half; Super+Up fills the display. Leaving Tablet restores native
Desktop window handling. Mobile retains its single-app presentation. Both
handheld presentations route the clock, status controls and downward status
swipes to the existing Desktop Quick Options menu. Presentation changes only
its status anchor and bounded viewport; the control actors, appearance, detail
pages and backing models are shared. Experience is one native QuickMenuToggle
row using the same detail picker as Wi-Fi and Bluetooth. It offers Automatic,
Desktop, Tablet and Mobile, and shows the effective mode beside Automatic.
Tablet status uses the same generated edge token as the AppKit inner frame.

Split placement reconciles native geometry/state changes at Mutter's redraw
boundary. Pending launches match the application's native identity as well as
Shell's tracker, and retain intent while startup size hints settle. Size hints
are compared in client coordinates using Mutter's frame conversion, accounting
for CSD shadows and X11 decorations. Visible halves meet at one integer seam;
no wallpaper gutter is reserved between them.
