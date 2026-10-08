# The menu

One menu serves the whole system: the desktop's right-click, the application
menu under a window's identity, the dock tile's menu, a text view's cut and
paste, and every in-application context menu. They are one drawing with
variants, never separate components. A menu that is two pixels different
somewhere is a defect.

The menu is the toolkit's. Every `GtkPopoverMenu` takes the numbers below from
the libadwaita stylesheet (`patches/libadwaita/0024-luma-menu-contract.patch`,
with `luma_menu*` colours in both design systems), so an application gets them
by using GTK's own menus and never by styling anything. The shell's own popup
menus take the same numbers through `patches/gnome-shell/0022-luma-context-menu.patch`
in the subset of CSS St understands.

The application kit's part is the bridge from its command model
(`src/luma-platform/appkit/luma_appkit/menus.py`): a `CommandRegistry` becomes
a `GMenuModel` with named sections, shortcuts as accelerators, checked and
disabled states, nested submenus, and — for the two things the model cannot
say, a destructive command and a choice that explains itself — a custom row
inside the toolkit's menu. `command_popover` hands back a `GtkPopoverMenu` to
parent, point and pop up; `attach_context_menu` wires a widget's right-click
and Menu key to one; and a widget that already has GTK's menu (a text view, an
entry) is given an extra menu with `command_menu`, never a second popover over
the same click. `tests/unit/test_luma_menu_contract.py` holds the bridge to
this on any display and the drawn numbers where Luma's toolkit is installed.

## Container

| | |
| --- | --- |
| Padding | 8px — 6px for a dock tile's menu and a submenu |
| Radius | 15px |
| Ring | inset, 1px, `@luma_menu_border` |
| Shadow | `0 4px 12px rgba(12,16,21,.20)`, then `0 24px 56px -18px rgba(12,16,21,.68)` |

Two shadows deliberately: the tight one seats the menu on the surface, the wide
offset one lifts it off the wallpaper. Collapsed into one, a menu either looks
pasted on or looks like fog.

## Width — compact base, natural growth when required

| Variant | Rendered | Content min-width |
| --- | --- | --- |
| Application menu | 220px | 204 |
| Desktop right-click | 220px | 204 |
| Dock tile | 186px | 174 |
| Submenu | 220px | 208 |

These are outer surface widths at 100% scale. Ordinary menus share a compact
220px base; dock menus retain their smaller 186px base. Native measurement may
grow a menu for long translated labels, larger text, and accelerator keycaps;
never clip a shortcut or hide an action to force the base width. Menus with an
action strip use a 220px content minimum because their horizontal inset is
inside the rows. The current width follow-ups are libadwaita patch0033 and
Shell patch0043.

## Rows

32px, 10px radius, `0 8px` padding, 10px gap, 600/13 Figtree, left aligned.

**Hover and keyboard focus are identical** — the fill *is* the focus indicator,
and a ring drawn on top of it makes keyboard navigation look broken.

**A destructive row looks like any other row at rest.** The colour arrives only
under the pointer or focus: `rgba(170,64,70,.12)` behind `#a44449`. A
permanently red item reads as an error state rather than as a warning at the
moment of commitment. Destructive items go last.

**A disabled row keeps its place** at 0.38 opacity and is stepped over by the
arrow keys. A menu whose items move between openings cannot be learned.

## Headings and separators

A heading is 18px tall, `4px 8px 0`, 700/11 Figtree, 0.11em tracking,
uppercase, at 0.46 opacity of the current colour — opacity rather than a muted
token, so one value is right on every background the menu sits on.

A separator is 1px, inset `5px 7px`, and sits **between every group** — the
line above `SYSTEM` in the reference screenshot, and the matching one above
`PERSONALIZE`. The quick-action strip is the exception: its own bottom hairline
is already the divider there.

*This follows the reference screenshot rather than the written spec, which says
a heading needs no rule above it. The two disagree; the screenshot is what the
desktop actually shows.*

## Trailing elements

Shortcut keys are separate elements, 3px apart, 600/11 at 0.5 opacity, so they
align down the menu's right edge. **On Fedora they read `Ctrl`, `Shift`, `Alt`,
`Super`** — the simulator draws ⌘ as shorthand; a Command glyph here is simply
wrong. A shortcut is shown, never actioned from the menu: the binding lives in
the command registry.

Submenu chevron and checkmark are 15px at 0.5 opacity. Each shortcut key is
drawn as the key it names — an 18px-wide cap on `@luma_passive` at a 6px
radius — rather than as loose text.

## Rows that explain themselves

A command may carry a `description`, drawn as a second, quieter line under its
label: "Name / Alphabetical". The row grows to 44px. A submenu of choices reads
as a list of answers rather than a list of words.

A submenu also heads itself with the row that opened it and the number of
choices it holds — "Arrange by  5" — so the question stays in view while it is
being answered.

## Submenus

220px base width, 7px clear of the parent, with the **first row level with the row
that opened it** — that alignment is what makes the two read as connected.

A submenu opens on a settled pointer, not a passing one: 175ms of intent, and a
pointer travelling toward an open submenu keeps it open while it crosses the
rows in between.

## Quick-action strip

Two to four frequent, non-destructive actions, never overflow. Tiles are 76px
tall with a 24px icon above a 600/13 label, bleeding to the menu's edges so the
strip's hairline becomes the menu's own divider.

## Still to do: the shell's own menus

The desktop's right-click and the dock tile's menu are **GNOME Shell
`PopupMenu`s**, drawn by St and styled by the Shell's stylesheet — not by this
component. They do not yet follow the contract above.

Closing that gap means a patch against
`data/theme/gnome-shell-sass/` and the compiled `gnome-shell-{light,dark}.css`,
in the same shape as the existing shell patches, carrying the same numbers.
Per `patches/gnome-shell/README.md` the styling belongs in the patch: no user
stylesheet, no injected CSS provider, no extension.

Until that lands, "one implementation" is true of every GTK surface and not yet
true of the two the Shell draws. That is the honest state.
