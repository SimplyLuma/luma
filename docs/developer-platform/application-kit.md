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
