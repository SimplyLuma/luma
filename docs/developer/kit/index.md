---
title: LumaUI
summary: What LumaUI is and what it draws, the action bar, how one app runs on desktop, tablet, phone and TV, the components that exist today, type, icons, colour, checks, API levels and what changed recently.
order: 1
section: Kit
---

# LumaUI

LumaUI is Luma's app toolkit and design system. It is Luma's own layer on
GTK 4, not a fork: GTK renders, handles input and carries accessibility.
LumaUI owns everything a person sees in a Luma app, on every kind of screen.

Apps say **what** they need ("a count that needs attention", "ask before
deleting this", "these are the actions for the selection"). LumaUI decides
**how** it looks and behaves. When the design improves, every app built with
LumaUI gets the change without being edited.

This page is the map. Each part links to its detailed page.

### What the labels mean

- **Available**: in the current Luma Developer Platform (LumaUI API level 2).
- **Preview**: built and running on Luma test devices, on a development line
  of the platform. Names may still change before it reaches a platform
  release.
- **Planned**: designed and decided, not in LumaUI yet.

## Who draws what

There are two parties, and the line between them does not move.

**LumaUI draws the app.** The window frame and its elevation, the 46 px title
row, the identity in the top-left corner (your app's name and icon from its
desktop entry, with its menu), the window controls, the 8 px gutter, every
island, the action bar, and every component inside: sidebars, fields, menus,
drawers, sheets, toasts, empty states. It owns the tokens, the motion, and how
each part changes on a phone, a tablet and a TV.

**Your app composes those parts and draws only its content:** a document, a
canvas, a list of songs, a photo.

| Layer | Owns | Never |
| --- | --- | --- |
| GTK 4 | Rendering, input, accessibility, Wayland, text | Luma's look |
| LumaUI | Frame, title row, identity, islands, action bar, every component, tokens, motion, phone/tablet/TV behaviour | Your content |
| Your app | Content, and which parts it composes | Restyling a LumaUI part |

## The pieces

| Piece | Use it from | What it gives you | Reference |
| --- | --- | --- | --- |
| LumaUI for Python (`luma_appkit`) | Python | Windows, islands, the action bar, commands, menus, navigation and every LumaUI part | [Python reference](appkit-reference.md) |
| `LumaUI-1` (`pkg-config luma-ui-1`) | C, C++, GObject introspection | The same parts in C: most Python parts have a C twin | [LumaUI-1 reference](lumaui-reference.md), [LumaUI for C](lumaui-c.md) |
| `LumaAppearance-1` | C, introspection | Light, dark and the appearance variants | [LumaUI-1 reference](lumaui-reference.md) |
| `LumaSemantics-1` | C, C++, introspection; Python in the SDK | Semantic objects, actions, risk, privacy, Live Extensions | [Semantics and actions](semantics-and-actions.md) |
| `luma` command | Terminal and CI | Project creation and contract checks | [The `luma` command](sdk-command-line.md) |

The Python package is named `luma_appkit`. A rename to `lumaui` is planned;
until it ships, import `luma_appkit`. It uses `LumaUI-1` and
`LumaAppearance-1` through introspection, so a Python app needs the platform
installed, not a copy of the toolkit.

## One app on every screen

A Luma app is one app, one package and one binary on a desktop, a tablet, a
phone and a TV. There are no tablet builds, no phone forks and no TV apps. The
app reads a few independent facts and LumaUI does most of the adapting.

| Fact | Values | Decides | Read it with |
| --- | --- | --- | --- |
| Window width (tier) | `phone` under 560, `compact` 560 to 900, `regular` over 900 | Layout: panes, drawers, what folds away | `WidthWatch`, `tier()`, `is_phone_width()`; `Adw.Breakpoint` |
| Presentation | Desktop, Tablet, Mobile | Chrome: frame, title row, window controls, status safe area | `AppContext.presentation` (`windowed`, `fullscreen-mobile`), `AppContext.device_class` |
| Size class (phones and tablets) | `pocket`, `phone`, `fold`, `tablet` | Bar placement, panes, home layout | Preview, see below |
| Posture | normal or TV | The TV presentation | Preview: `lumaui.tv_posture()` |
| Input | pointer, touch, keyboard; remote and gamepad in TV posture | Target sizes and affordances | `AppContext.input_mode` (`pointer`, `touch`) |

The rules that hold them apart:

1. Width changes layout. It never identifies a device: a narrow desktop window
   gets the same drawers a phone does, with its title row and pointer targets.
2. Presentation changes chrome, independently of width.
3. Input changes target sizes and affordances, independently of both.
4. Posture is independent of presentation. A TV is a desktop presentation
   underneath; in TV posture `mobile_form_factor()` is always false.
5. Compact layouts keep every function. Actions move into the bar's ⋯,
   drawers or sheets; they are never dropped.

### Width tiers

The tiers come from the window (or the frame standing in for one), not the
screen. GTK has no signal for "my window crossed a breakpoint" when a widget's
own size did not change, so LumaUI has one:

```python
from luma_appkit import WidthWatch, tier

watch = WidthWatch(window, on_tier=lambda t: self.redraw(t))   # "phone" | "compact" | "regular"
watch.connect("tier-changed", lambda _w, t: ...)
tier(widget)                                                   # the tier now
```

Most parts adapt by themselves at these tiers (see
[What adapts by itself](#what-adapts-by-itself)). Use `WidthWatch` only when
your own layout changes shape. `Adw.Breakpoint` still works for your own
panes; prefer `sp` units so layouts follow the person's text size.

### Presentations: Desktop, Tablet and Mobile

The shell resolves one presentation for the session: Automatic, Desktop,
Tablet or Mobile, chosen in Quick Options under Experience. Apps follow a
change live, in the same window.

- **Desktop**: windows, the title row, window controls.
- **Tablet**: one app full screen (or two side by side), no window controls.
  The shared frame paints through the transparent status strip; sidebars and
  page islands start 52 px from the top (the strip is 44).
- **Mobile**: one app full screen, no window controls, the shell's status bar
  and home gesture.

Apps whose background should reach the bezel (a sky, a photo) opt in, and
keep their foreground clear of the status area:

```python
window.set_edge_to_edge(True)          # background to the bezel
window.set_phone_bleed(True)           # phone presentation
inset = window.status_inset            # reserve this above foreground content
window.set_status_bar_style("light")   # "light" | "dark" | "auto": the status ink you need
```

C: `luma_application_window_set_edge_to_edge()`,
`luma_application_window_set_phone_bleed()`,
`luma_application_window_get_status_inset()` and
`luma_application_window_set_status_bar_style()`. The style declares ink
only; it never paints a bar. Never add your own status background, gradient
or shadow.

On a desktop, `AppWindow` opens every window at a shared height and a width
from its shape (`size_class="compact" | "regular" | "wide" | "utility"`, or
`"auto"` from your default size), so a desk of windows looks like a set, and
it never lets a narrow layout hide the title row, which is the close control.

### Size classes

Preview. On phones and tablets the screen's logical size gives one more word,
read again on rotation and on every monitor change:

| Class | Rule | Examples |
| --- | --- | --- |
| `pocket` | long side under 600 | 432 × 480, 480 × 480 |
| `phone` | short side under 560 | 390 × 844, a folded phone 411 × 923 |
| `fold` | short side 560 or more, unless it is a tablet | an open foldable 791 × 820, two screens spanned 1100 × 757 |
| `tablet` | short side 760 or more and long side 1024 or more | 1280 × 800 either way up, 1024 × 768 |

Orientation (landscape or portrait) is a second word. A desktop window has no
size class; its layout is its tier's.

- **pocket**: the action bar attaches to the bottom edge, full width, rounded
  at the top only. That part is available: LumaUI attaches the bar on any
  phone whose screen's long side is under 600, never in a narrow desktop
  window. In preview, the bar also tucks away while the page scrolls down and
  large titles drop to 22 px.
- **phone**: the phone layout throughout.
- **fold**: a list-and-page app splits at the fold, the list on one side and
  the page on the other; nothing sits on the crease. What is open folded stays
  open unfolded, in the same place.
- **tablet**: the phone's design with room. See [Tablets](#tablets).

The fold rules and the split at the crease are design decisions (21 Oct 2026)
that LumaUI does not apply by itself yet.

The phone builds' LumaUI reads the class with `structure_adapt.form(widget)`
(C: CSS classes `lumaui-form-pocket`, `-phone`, `-fold`, `-tablet` on the
window), and parts that need the phone or the regular shape ask
`phone_shape()`, which follows the window and its class, rather than whether
the device is a phone. These names are preview.

### Input

- **Pointer**: the compact control sizes and hover states.
- **Touch**: targets at least 44 × 44 px. Nothing depends on hover; anything
  revealed on hover also shows on focus or tap. Every secondary-click menu
  also opens on long press (`attach_context_menu` does this). LumaUI parts
  switch sizes by themselves.
- **Keyboard**: always available, in every presentation. Every function is
  reachable and operable from the keyboard with a visible focus. A real
  keyboard types straight into any field, on a TV too.
- **Remote and gamepad**: TV posture only. They arrive as keys (arrows,
  Return, Escape, Menu), never as a protocol. See [TV](#tv).

Control heights: 28 compact, 36 standard, 44 touch.

### What adapts by itself

| Part | Desktop | Phone width | Tablet (planned) | TV (preview) |
| --- | --- | --- | --- | --- |
| Action bar | Floating at the island's foot | Thumb-sized, icons only, ⋯ for what does not fit | One bar under each pane | Planned: the TV action bar |
| Menus and context menus | Popover | Bottom drawer, 46 px rows | Phone drawer | TV menu, hold OK or Menu |
| Sidebar | Beside the content, F9 toggles | Drawer from the bottom-left corner | Recessed places sidebar, 248 px | Floating island that folds to a rail |
| List and page (`ListFirst`) | Side by side | List first, page pushes in, Back top left | Side by side, no Back | Island and page |
| Details pane | Beside the island | Bottom drawer | Third island, 300 px | Page in the frame |
| Destructive dialog, sheets | Centred card | Rise from the bar's place | Phone | TV sheet, Cancel focused |
| Mode switch | Every label | Current label only | Phone | Tabs |
| Toast | Above the bar | Above the bar | Above the bar | TV toast above bar or keyboard |
| Swipe rows | Off | On | On | Off |
| Title island | Hidden | Top left: ☰ or Back, the title | Names the page | None |

What you do:

- Name your structure with LumaUI parts (`IslandSplitView`, `ListFirst`,
  `DetailsPane`, `ActionCenter`) instead of laying out plain boxes, so LumaUI
  knows what to move.
- Put views (grid or list, sort, years, months, days) in the corner pill or
  behind ⋯, not in the bar.
- Decide, per app, whether a tablet takes the phone arrangement or the desktop
  one when the generic pane rules are not enough.
- Supply a TV presentation if your app is a media, browsing or full-screen
  app (see [TV](#tv)). A sidebar app gets the floating island with no TV code.
- Test at the declared widths, with touch and keyboard only.

### Testing presentations on a desktop

```sh
LUMA_PRESENTATION_MODE=fullscreen-mobile LUMA_INPUT_MODE=touch python3 src/main.py   # phone
LUMA_DEVICE_CLASS=tablet LUMA_INPUT_MODE=touch python3 src/main.py                    # tablet
LUMA_FORM_FACTOR=phone python3 src/main.py                                            # phone device
LUMA_POSTURE=tv python3 src/main.py                                                   # TV (preview)
GTK_DEBUG=interactive python3 src/main.py                                             # sizes, accessibility
```

Set `LUMA_INPUT_MODE` explicitly when testing tablets. `luma-app.toml`
declares what you test and `luma lint` checks it:

```toml
[responsive]
adaptive = true
test_widths = [360, 500, 1024, 1280]
input_modes = ["pointer", "touch", "keyboard"]
presentation_modes = ["windowed", "fullscreen-mobile"]
```

360, 500 and 1024 are required; add your widest. Set
`minimum_width=360, minimum_height=294` on `AppWindow` so the phone width is
reachable on a desktop.

## The action bar

The action bar is where a person acts. It floats at the foot of an island and
holds the actions for what is open or selected. It is not confined to a bar:
it grows, goes double height, splits, and becomes an editor in place. LumaUI
calls it the action center (`ActionCenter`, C `LumaActionCenter`). Prefer
growing the bar to opening a dialog.

### States

| State | What it is | Example |
| --- | --- | --- |
| `hidden` | No bar | A screen with nothing to act on |
| `bar` | The actions for what is open or selected | Reply, Archive, Delete |
| `double` | A context line over the bar | "Replying to Priya's message" |
| `editor` | The editor the bar grows into: header with mode and recipient, optional field rows, tools, your content, Discard, the key hint and the primary action | Compose a reply, a new event typed in the bar |
| `split` | Two bars side by side (stacked on a phone) | Selection chip and Share beside Favourite and Delete |

Beyond the states, the bar can:

- **grow a panel** above its own row, inside the same glass, for ⋯, Details,
  Share, Add, a context menu or a confirm. Growing the same key again folds
  it; the button that grew it is raised while it is open.
- **replace its row with a field** that is the point of a panel (search, "Add
  a city"), with ✕ or a word such as Cancel to give the row back.
- **carry a head row** above the actions (a mini player) and a **foot row**
  below them (an always-there add field), under hairlines.
- **hold** something across views (Move): a second row shows what is held and
  where it would go ("Move to Launch · Move here").
- **search in place**: a search glyph that expands into the field.
- **present a sheet** that rises from the bar's place on a phone and centres
  on the window on a computer.

Esc folds the editor back to the bar and keeps the draft ("Draft: …" in the
prompt). Ctrl+Return runs the primary action. Everything is reachable with
Tab. The bar registers with the nearest toast host, so toasts clear it.

### Code

```python
from luma_appkit import ActionCenter, ActionEditor, BarAction, BarChip, BarContext, BarPrompt

center = ActionCenter()
center.attach(island_host)        # a LayerHost or ToastHost around the island

center.set_editor(ActionEditor(
    "Reply", "reply",
    modes=[("one", "Reply", "reply"), ("all", "Reply all", "reply-all"), ("fwd", "Forward", "forward")],
    mode="one", on_mode=switch_mode,
    tools=[BarAction("paperclip", tooltip="Attach", on_activate=attach)],
    placeholder="Write your reply",
    primary=BarAction("send-horizontal", "Send", on_activate=send),
))

center.show_bar([BarPrompt("Reply to Priya…"),
                 BarAction("paperclip", tooltip="Attach", on_activate=attach)])
center.show_bar(items, context=BarContext("reply", "Replying to {}’s message", emphasis="Priya"))
center.show_split([BarChip("3 photos", icon="image"), BarAction("share-2", "Share", on_activate=share)],
                  [BarAction("heart", tooltip="Favourite", on_activate=fav),
                   BarAction("trash-2", tooltip="Delete", danger=True, on_activate=delete)])
center.grow()                     # into the editor; fold() and discard() go back
center.grow("details", details)   # a panel above the row
center.more([...])                # what ⋯ lists
```

```c
LumaActionCenter *center = LUMA_ACTION_CENTER (luma_action_center_new (NULL));
luma_action_center_attach (center, layer_host);

LumaBarItem *items[] = {
  luma_bar_item_new_chip ("invoice.pdf", "file", FALSE),
  luma_bar_item_new_action ("copy", NULL, "win.copy"),
  luma_bar_item_new_spacer (),
  luma_bar_item_new_action ("share-2", "Share", "win.share"),
};
luma_action_center_show_bar (center, items, G_N_ELEMENTS (items), NULL);
for (guint i = 0; i < G_N_ELEMENTS (items); i++)
  g_object_unref (items[i]);      /* the center keeps its own references */
```

Other C entry points: `luma_action_center_show_split()`,
`luma_action_center_grow()`, `_fold()`, `_discard()`, `_grow_panel()`,
`_grow_entry()`, `_grow_menu()`, `_more()`, `_hold()`, `_search()`,
`_set_head()`, `_set_foot()`, `_attach_scroller()`; items from
`luma_bar_item_new_action()`, `_new_chip()`, `_new_prompt()`,
`_new_context()`, `_new_search()`, `_new_modes()`, `_new_widget()`,
`_new_separator()`, `_new_spacer()`. The editor is `LumaActionEditor`.

`BarAction` takes `primary` (one per bar), `danger`, `active` (a toggle that
is on), `badge`, `panel` (what it grows the bar into), `menu` (a menu that
hangs from it), `dropdown` (a place or view picker), `keep_label` and `fill`
(phone width). There are no colour, size or radius arguments. Apps never
style the bar; they hand it actions and content.

### Where actions live

- **Top right of the island (`CornerPill`)**: modes and the view, and actions
  on the thing itself, always in this order: mode switch · who's here ·
  Open in · Share · object actions (Edit) · on/off states (Favourite) ·
  Information · ⋯.
- **The bar**: what you do in this mode right now, or with the selection.
  Insert is a `+` menu in the bar.
- **The sidebar's foot (`SidebarFoot`)**: search, filters (All, Favourites,
  Unread, Deleted) as a picker that opens upward, and the raised New square.
  Filters never stack under search.

Rules for every bar:

- One button look: grey at rest. Only the single primary action is filled.
- A switch in the bar is a row of the same buttons with the chosen one
  raised, never a frame inside a frame.
- With something selected, the bar shows that item's actions only: no chip
  repeating what you picked, no Open (a tap opens).
- One-subject screens (a memo, a contact, a photo) drop the name chip and hold
  direct actions; rare or destructive ones (Duplicate, Delete) go under ⋯.
- Bars never name the page you are already on.

### On a phone

- The bar holds only what you act on, within thumb reach: 48 px keys, 20 px
  corners, icons alone unless a key keeps its label (`keep_label=True`).
- Navigation is top left, never in the bar: ☰ for the app's places at a top
  level, Back inside something (the `TitleIsland`).
- Views stay off the bar: a top-right island, or behind ⋯.
- ⋯ grows the bar upward in place. When the bar does not fit, its least
  important items move into ⋯ first: single actions leave from the end, a
  switch leaves last; the primary key, the context chip and the first control
  stay.
- A bar that is only a switch between three or more places with icons becomes
  the app's tab bar (`TabBar`): full width, an icon over each name.
- `show_bar(fill=True)` spans the 16 px gutter and shares the width.
- Menus, confirms and sheets rise from the bar's place, on the 16 px gutter,
  34 px up, 26 px corners (`BarFrame`). Apps rarely call it directly.
- On pocket screens the bar attaches to the bottom edge (tucking away on
  scroll is preview).
- Drop what does not belong on a phone: column views, and `+` where the app is
  where things already live.

### On a tablet

Planned. Each pane keeps its own phone bar under it (the list's bar under the
list, the page's under the page): actions only, 48 px keys.

### On a TV

Planned. The action bar translates to TV: it floats at the bottom centre on
the safe line and grows large, and on a TV it can absorb a desktop app's
title bar, address or search field and sidebar toggle. It is for full-screen
activities (a browser page, a game's pause menu), not for standing over a grid
you scroll. While the TV keyboard is open, any action bar sits directly above
it. Today `TvStage.docked = "action-bar"` reserves its place so toasts rise
above it; the TV action bar itself is not in LumaUI yet.

## Phones

Most of this is automatic once you use the parts.

- **Title island** (`TitleIsland`): where you are, top left, in the bar's
  material. ☰ grows into the places; Back goes up one level. Tap it and it
  grows down into its menu or details.
- **List first** (`ListFirst`): list-and-page apps open on their full-screen
  list with a large title; picking a row pushes the page in; Back returns.
- **Drawers**: every sidebar opens as one drawer from the bottom-left corner,
  below the header, square to the left and bottom edges with one rounded
  top-right corner, 44 px rows, a 42 px search. Picking a place closes it.
  Menus become bottom drawers (`MenuDrawer`): full width, grab handle,
  46 px rows, the window dimmed, submenus as a second page with a Back row.
  For any panel or popover you design, ask whether it would make a good drawer.
- **Details** (`DetailsPane`) become a bottom drawer with the same content;
  widening the window puts the pane back.
- **Swipe rows** (`SwipeRow`, `SwipeAction`): slide a row to act without
  opening it; past 90 px it commits. On by default at phone width only. The
  same actions must also be in the row's menu.
- **Safe areas**: content stops under the status bar and above the home
  gesture; backgrounds do not. `ActionCenter.attach_scroller()` gives a
  scroller's content room above the bar.
- **Page headers** (`PageHeader`): large titles stack their meta under the
  title.
- **Keyboard**: one on-screen keyboard, the shell's Keycap keyboard, for
  phones, tablets and TVs.

## Tablets

A tablet runs the phone's design with room, never the desktop shell. The
Tablet presentation, its frame (the 44 px status strip, panes 52 px down,
12 px edges, 20 px page island) and two apps side by side are available. The
pane rules below were decided on 22 Oct 2026 and their sizes are in the
tokens; turning them into LumaUI parts that apps get by naming their panes is
planned. Until then a tablet shows an app's phone layout at tablet size.

- **Landscape, three panes**: the places the phone keeps in a drawer become a
  recessed sidebar on the window's ground (248 px; 44 px rows, 15 px type,
  19 px icons); the list beside it (340 px); the page an island (20 px
  radius, 12 px from the edges).
- **Portrait, two panes**: list and page; the places are the drawer again,
  unless they are the app's only side pane.
- Every pane starts under the status strip (52 px down).
- Bars stay the phone's, each under its own pane. Views move to the page's
  title row.
- No Back and no ☰ while the list is on screen. The selected row shows as a
  chip.
- A details pane is a third island (300 px) in landscape, never a sheet.
- Touch stays 44 px. Nothing shrinks to desktop sizes because there is room.
- **Two apps**: an app dragged from the dock to a side, or the current app
  slid sideways by its home bar, makes a pair of real windows, each told its
  real width. Each half lays itself out by its own width and never knows it
  shares the screen. Super+Left/Right places the app in a half, Super+Up fills
  the screen.

## TV

Preview. TV Mode is a posture of the Luma shell beside desktop, tablet and
phone, for any Luma install, not a separate shell, session or catalogue. An
app gains a TV presentation and shows it while the shell is in TV posture;
the same app ID, package and data serve every posture.

### The posture

```python
from luma_appkit import lumaui

if lumaui.tv_posture():
    show_tv_presentation()

stop = lumaui.watch_posture(lambda tv: show_tv_presentation() if tv else show_normal())
window.connect("notify::tv-posture", lambda w, _p: switch(w.tv_posture))   # AppWindow
```

`AppContext.posture` is `"tv"` or `""`; `AppContext.tv` is the boolean. The
shell owns the setting (`luma-shell-statectl set posture tv`, and `auto` to
leave); the switch is live. Never infer a TV from the screen size. Without the
shell-state service (another distribution, a sandbox without the bus name)
the posture is never TV.

### What every window gets

- The window runs full screen. LumaUI drops the title row and window controls
  and adds the `lumaui-tv` style class.
- Back that nothing in the window handled returns to the TV home.
- No hover-only affordances.
- **The floating island**: a sidebar app needs no TV code. The sidebar lifts
  off the window edge into a floating island, as tall as its rows. Up and Down
  open each page at once; Right steps into the page and folds the island to a
  100-unit rail; Left off the page's edge or Back returns. The page keeps the
  desktop's order and draws its parts at TV size: switches flip on OK, sliders
  move with Left and Right, choices drop a list, fields open the TV keyboard.
  `AppWindow` (with an `IslandSplitView`, or a `NavigationSidebar` beside an
  `Island`) and `LumaApplicationWindow` do this themselves; other code calls
  `luma_window_set_tv_frame()` and `luma_window_set_tv_active()`. C pages name
  their parts with `luma_ui_set_part()` (`header`, `hero`, `group`, `card`,
  `row`, `chips`, `chip`, `swatch`).
- System dialogs (Wi-Fi passwords, the administrator prompt, end session,
  access prompts) become TV sheets driven by the remote, focused on Cancel so
  a stray OK never confirms.

### Sizes for the room

One unit for every TV length: one pixel of a 1920 × 1080 layout,
`lumaui.tv_unit(width, height)` = `min(width/1920, height/1080)`. Everything
is sized in units and pinned to an edge or the centre, so any screen size and
aspect ratio works.

| Token | Value (units) |
| --- | --- |
| Safe area | 96 at the sides, 56 at top and bottom |
| Top bar | 96 in, 56 down, 74 tall; tabs 56 tall at 26/650; clock 28/700 |
| Type | h1 52/750, h2 30/700, body 26/500, meta 20/600, caption 22/650 and 19 |
| Covers | square 260, portrait 230 wide, landscape 400 wide, radius 20 |
| Buttons | 66 tall, radius 22; round transport 84, the main one 108 |
| Wide tiles | 16:9, corners 11.875% / 21.111% of the tile |

The values live in the `tv` block of the shared design tokens; Python reads
them as `luma_appkit.lumaui_tokens.TV`. Use the tokens. CSS for a TV view
takes lengths in `tv` units through `add_tv_style()`.

### Materials and focus

- **well**: recessed (tracks, fields); **chip**: chosen; **key**: the focus;
  **bar**: floating panels. Translucent over the room; opaque under Reduce
  Transparency and High Contrast.
- Exactly one bright thing on screen: the focus.
- Covers and tiles lift 1.06 to 1.08 with a white ring on the artwork only,
  never the caption.
- Arrows move to the nearest focusable thing in that direction. OK opens or
  plays. Back steps out level by level, then to the TV home.
- Holding OK (500 ms) or pressing Menu opens an item's menu. It is the same
  `CommandRegistry` you give `attach_context_menu` on the desktop, never a TV
  copy. Key repeat only moves focus or adjusts a slider; it never activates.

### TV parts

```python
from luma_appkit import TvStage, TvTopBar, TvFocus, TvToast, TvIdle, TvCover, TvRoom

stage = TvStage()
bar = stage.place(TvTopBar(), **TvTopBar.PIN)        # the shared bar, same place on every screen
room = stage.place(TvRoom(), fill=True)              # the item's own art, blurred, as the backdrop
focus = TvFocus(stage, on_back=leave_level)
cover = TvCover(shape="square", artwork=path, title="Album", subtitle="Artist")
focus.add(cover, on_activate=open_album, menu=lambda: commands_for(album))
TvToast.show(stage, "Added Lisbon", lead="map-pin")
TvIdle(focus, seconds=20, on_idle=show_glance, on_wake=hide_glance)
```

| Family | Parts |
| --- | --- |
| Stage and focus | `TvStage`, `TvLift`, `TvFocus`, `tv_nearest()`, `add_tv_style()` |
| Bar and controls | `TvTopBar`, `TvTab`, `TvButton`, `TvField`, `TvMenu`, `TvToast`, `TvIdle` |
| Media | `TvCover`, `TvTransportButton`, `TvNowPlayingPill`, `TvEqualizer`, `TvProgress`, `TvRoom`, `TvSlide` |
| App surfaces | `TvWideTile`, `TvShelf`, `TvTileGrid`, `TvPage`, `TvProgressButton`, `TvScreenshotViewer`, `TvEmptyState` |
| Games | `claim_gamepads(True/False)` while a game reads the pad itself |

- **One toast** for every app: bottom centre on the safe line, rising above
  the action bar or keyboard. Parts: `lead`, `kicker`, `title`, `value`,
  `sub`, `tint`. It says one thing.
- **Idle views**: an app may supply an idle layer that cross-fades in after a
  delay without input (Weather after 20 s, Tide after 15 s while playing). Any
  key wakes it and does nothing else.
- **The room**: an app may colour the background from its content (the
  focused album's art, the hour's sky). The backdrop is blurred once and
  cached, never live, so a 4K TV on a small GPU stays smooth.

### The TV keyboard

There is one on-screen keyboard. In TV posture the shell's Keycap keyboard
takes its TV form for every text field: docked to the bottom, every key the
same size on an aligned grid so a remote moves in straight lines, a blue
action key labelled for the context. There is no keyboard widget to embed:
use ordinary text widgets.

- It belongs to its field: it opens when a field takes the focus and closes
  when the focus leaves. Back, or Up from the top row, puts it away.
- A real keyboard types straight in.
- It is 428 units tall and never resizes the window. Lift what sits at the
  bottom: `TvStage` does it for you; anything else can follow it.

```python
field = TvField(placeholder=_("Add a place"), action=_("Add"))
set_keyboard_action(entry, _("Search"))       # label the action key for any field
stop = watch_tv_keyboard(lambda visible, height: lift(height))
```

Without a label the key reads Go for an address field and Done otherwise.
Other toolkits use the session-bus service `org.projectluma.Shell.Keyboard1`
(`SetActionLabel`, properties `Visible` and `Height`).

The Design Center's TV mockups use a JavaScript stand-in,
`LumaTVKeyboard.open({value, onInput, onDone, onClose})`. It is a design tool,
not an app API.

### Remotes and gamepads

The shell turns gamepads, TV remotes and the TV's own remote over HDMI-CEC
into the same keys before they reach any window. Handle the keys and every
controller works.

| TV action | Key your app sees |
| --- | --- |
| Move | Up, Down, Left, Right |
| OK | Return |
| Back | Escape (Backspace where nothing is being typed) |
| Menu | Menu |
| Home | none: the shell opens the TV home |

Which face button is OK is the shell-state key `tv-confirm-button`
(`automatic`, `south`, `east`). Apps without a TV presentation get the pad
untouched, as on a desktop.

### Appearing on the TV home

Only apps that opt in appear on the TV home. Declare it in `luma-app.toml`:

```toml
[tv]
supported = true
tile = "data/org.example.App-tv.svg"   # the 16:9 wide tile
presentation = "tv"                    # "tv": your own TV view; "fullscreen": your normal view, full screen
```

It is installed as the desktop-entry keys `X-Luma-TV`, `X-Luma-TV-Tile` and
`X-Luma-TV-Presentation`, which `luma lint` checks against the manifest. The
tile is an icon named after your app ID with `-tv`, made from your app icon
with the plate, the art centred at 1.15× and the rim. An app without its own
tile gets one composed at runtime.

Keep the app's spirit and its way of finding things, so someone coming from
the desktop recognises it, but standardise sizes through LumaUI rather than
copying another app.

## Components

Every part below is available unless marked. Python names are in
`luma_appkit`; most have a C twin named `Luma<Name>` in `LumaUI-1`. The full
lists are in the [Python reference](appkit-reference.md) and the
[LumaUI-1 reference](lumaui-reference.md).

| Family | Parts |
| --- | --- |
| Window | `AppWindow` / `LumaApplicationWindow`, `Island` / `LumaIsland`, `LumaPane`, `IslandSplitView`, `IslandOverlaySplitView`, `LumaAdaptiveScaffold`, `WindowIdentity`, `WindowControls` |
| Layers | `LayerHost`: the one place floating parts appear (toasts, dialogs, drawers, the selection bubble); centred cards on a computer, drawers at phone width |
| Navigation | `NavigationSidebar`, `NavigationRow`, `SidebarRow`, `SidebarSection`, `SidebarFoot`, `FilterHeading`, `SidebarToggle`, `NavigationTrail`, `NavigationTrailBar`, `TitleIsland`, `ListFirst`, `TabBar`, `PageHeader`, `ModeSwitch`, `CornerPill` |
| Action | `ActionCenter`, `ActionEditor`, `BarAction`, `BarChip`, `BarContext`, `BarPrompt`, `BarSearch`, `BarEntry`, `BarFrame`, `SelectionBubble`, `SplitAction`, `ZoomControl` |
| Menus | `Menu`, `MenuDrawer`, `command_popover`, `attach_context_menu`, `RichMenuItem`, `MenuSection` |
| Panels | `DetailsPane`, `DetailsRow`, `DetailsFacts`, `DetailsPhotos`, `FactRow`, `AddRow`, `PanelRow`, `PanelSwitch`, `PanelChoices`, `PanelConfirm`, `SharePanel`, `ShareSheet` |
| Feedback | `Toast`, `ToastHost`, `DestructiveDialog`, `StackedButtons`, `EmptyState`, `ListEmptyState`, `CountBadge`, `StatusPill`, `ProgressLine` |
| Rows and lists | `SwipeRow`, `AZIndex`, `FavouritesStrip`, `AvatarStack`, `TableHeader`, `ChoiceList` |
| Fields | `TextField`, `HeroTitleField`, `ParagraphField`, `InlineRenameField`, `NumericField`, `PlaceSearch`, `Switch` |
| People and things | `Avatar`, `PersonAvatar`, `ContactActions`, `ContactCard`, `EventCard`, `SongCard`, `PlaceCard`, `NoteCard`, `FileCard`, `OpenButton`, `OpenInMenu`, `MessageBubble` |
| Cards | `Card`, `ContentLitCard`, `ContentLitHeader`, `AccountCard`, `MiniCard` |
| Media | `MediaTransport`, `MediaMiniPlayer`, `MediaGrid`, `MediaTile`, `CoverArt`, `VoiceClip`, `AudioWaveform`, `Timeline`, `ImageViewport`, `Lightbox` |
| Documents | `SaveSheet`, `ExportSheet`, `DocumentHeader`, `DocumentCheck` |
| Creative tools | `CreativeWorkspace`, `ToolBar`, `Tool`, `Flyout`, `FloatingPanel`, `LayerTree`, `PropertySection`, `PropertyNumber`, `PropertyColor`, `PropertyChoice`, `AlignmentActions`, `ValueSlider`, `AdjustmentPanel` |
| Type | `TypeLabel`, `apply_type`, `TYPE_ROLES` |
| TV (preview) | See [TV parts](#tv-parts) |

Where GTK has no standard (a bar that grows into an editor, a details pane
that closes itself when its subject goes away, layers, drawers, a motion
system), LumaUI provides a proper primitive instead of a per-app workaround.
If LumaUI cannot express what you need, ask for the component rather than
drawing a local imitation; see the
[FAQ](../knowledge-base/faq.md#lumaui-is-missing-a-component-i-need).

### Showing another app's things

When your app shows something another app owns (a person, an event, a file,
a song, a place, a note), show a small version of the owning app's own view
of it, through the shared part. People learn a thing once, in its home app.

| Thing | Owning app | Part |
| --- | --- | --- |
| A person and the ways to reach them | Contacts | `ContactCard`, `ContactActions` (Message, Call, Video, Email, in that order) |
| An event | Calendar | `EventCard` |
| A file | Filer | `FileCard`, `OpenButton`, `OpenInMenu` |
| A song | Tide | `SongCard` |
| A place | Maps | `PlaceCard` |
| A note | Notes | `NoteCard` |

```python
ContactCard(person)
EventCard("Launch rehearsal", start, where="Studio", on_add=add)
SongCard("Song", "Artist", "Album", artwork=texture, on_play=play)
PlaceCard("Duende", "468 19th St", eta="9 min", icon="utensils", on_directions=go)
FileCard("~/Downloads/launch-deck.stage")
```

Never design a look-alike. If the owning app has no shared part for what you
show, ask for one.

### Shared parts

- **One transport** for every media app (`MediaTransport`, variants `deck`,
  `attached`, `lcd`, `waveform`), attached to what it controls. The app says
  where playback is; LumaUI owns the keys, the scrub and the readouts.
- **One file card** for every file mention (messages, mail, downloads,
  details).
- **One progress and slider element**: the accent in a recessed well with a
  raised knob, never per-app colours.
- **One toast**: centred on the island above its bar, a plain past-tense line,
  Undo when it can be undone. Never a question (that is `DestructiveDialog`).

## Typography

The system typeface is **Figtree**, a variable font, weights 300 to 900. It
is used everywhere: apps, the shell, sign-in and the website. Its weight axis
stops at 300; anything lighter is drawn at 300.

Use type roles, not sizes (`TypeLabel`, `apply_type`): display, hero,
title 1, title 2, lead, body (13 px), caption and label (11.5 px), numeric.
Changing numbers (times, counters, prices) use tabular figures. Line heights
are whole pixels, because GTK rounds fractional CSS line heights up. On TV
the TV type scale applies. Details: [Typography](../design/typography.md).

## Icons

- **Symbols are Lucide, and only Lucide.** Name a glyph by its Lucide name
  (`"share-2"`, `"trash-2"`); LumaUI resolves it to a stable
  `lumaui-<name>-symbolic` theme name and ships the glyphs with the kit. Never
  draw an inline SVG and never name an icon that has no file: a test fails
  when a part or an app does.
- Standard meanings: Share is `share-2`, Export is `share`, Upload is
  `upload`; New is `square-pen`, Add person is `user-plus`; Delete is a red
  `trash-2`.
- Third-party marks use only their official files.
- **App icons** follow Luma's current icon family: a plate lit from the upper
  left, one object, a rim. On a TV the wide tile is built from the same parts.

Details: [Icons](../design/icons.md).

## Colour, light and dark

Colour is named, never written. Use the `@luma_*` roles (`@luma_window`,
`@luma_content`, `@luma_ink`, `@luma_muted`, `@luma_line`, `@luma_fill`,
`@luma_selected`, `@luma_accent`, `@luma_destructive`, the tones). A literal
colour in an app stylesheet is a defect.

- **Light and dark** follow the system setting. The token sheets reload when
  the appearance changes, so light, dark, Frost, Glass and high contrast are a
  token change, not an app change.
- **Reduce Transparency and High Contrast** make translucent materials opaque.
- **Content is the colour**: where a screen belongs to something, it takes
  that thing's colour (an album, the sky, a person's hue) instead of flat
  grey. `ContentLitHeader`, `ContentLitCard` and the person hues
  (`person_hue()`, `hue_class()`) do this within the contrast rules.
- **Materials**: well (recessed), chip (chosen, raised), bar (floating),
  key (the one primary or focused thing).

Details: [Color](../design/color.md).

### D-Ink

Planned. D-Ink (digital ink) is Luma's appearance for reflective e-paper
screens, a switch beside Light and Dark rather than a mode inside them: paper
and ink, a few fixed line weights, one solid fill for the single selected or
primary thing, nothing tinted, shaded, blurred or faded, photos drawn harder,
app icons as line drawings. Mono suits grayscale panels; Colour suits colour
e-paper. It is designed in the Design Center; LumaUI does not provide it yet.

## Motion

Motion comes from tokens: hover 120 ms, press 150 ms, morphs about 300 ms,
drawers 260 ms, the bar growing 320 ms, toasts 2.4 s (4 s with Undo).
Reduced Motion swaps movement for a short fade. Every vertical scroller fades
at its top edge once scrolled. Details: [LumaUI behaviour](behaviour.md) and
[Motion](../design/motion.md).

## Rules every app follows

1. One source tree and one binary across every Luma presentation and posture.
2. Width changes layout; it never identifies a device.
3. Presentation controls chrome, independently of width.
4. Input controls target sizes and affordances, independently of both.
5. Compact layouts keep every function, moving actions into the bar's ⋯,
   drawers or sheets.
6. Grow the action bar before opening a dialog.
7. Show another app's things with the owning app's shared part.
8. Every custom widget sets accessible names, roles and relationships.
9. Each meaningful operation has one stable action ID, shared by its button,
   menu item, shortcut, accessibility action and semantic action.
10. Secrets never appear in a semantic object or Live Extension.
11. Third-party code never renders inside the shell.
12. Colours come from tokens; icons come from Lucide; type is Figtree.
13. An app stylesheet never selects a LumaUI part (`.lumaui-*`) or the frame
    (`headerbar`, `windowcontrols`, `.luma-titlebar`, `.luma-island`).

## Use the installed toolkit

Build against the installed platform: `pkg-config luma-ui-1` for C and C++,
the installed `luma_appkit` for Python. A copy of LumaUI's source or
stylesheets inside your repository is a fork, and forks drift. Pin the
platform release you tested against.

Ordinary GTK 4 and libadwaita apps that do not use LumaUI still get the Luma
frame, Figtree, the icons and the accent on Luma, but not LumaUI's components
or its phone, tablet and TV behaviour. See
[Bringing an existing GTK app](../getting-started/existing-gtk-apps.md).

## Checking your app

- `luma lint` checks `luma-app.toml`, including the test widths, input modes,
  presentations and (preview) the `[tv]` table.
- `luma test` checks the static adaptive and semantic contracts.
- `luma release-check` checks the evidence for Luma Native status: layouts at
  360 × 294, 500 × 800, 1024 × 600 and a wide desktop; pointer, touch,
  keyboard-only and screen reader; long translated text, right-to-left,
  large text, high contrast and reduced motion; x86-64 and aarch64.

## API level and stability

The platform is a **0.x preview**. The C libraries use the `-1` namespace so
1.0 can stabilise without breaking the name; the 0.x ABI is not stable.

- `luma_appkit.LUMAUI_API_LEVEL` (C: `LUMA_UI_API_LEVEL`) rises by one
  whenever a public name, keyword argument or toast kind is added. Check it
  before using a newer part. Within a level nothing public is removed,
  renamed or changes meaning.
- A name that is going away is deprecated first: it keeps working, warns once,
  and is removed no sooner than one API level and one monthly release later.
- Looks and motion are not API. They change whenever the design does.
- Only names exported from the package root (`luma_appkit.__all__`) are
  covered. Names starting with an underscore are private.

| Level | Adds | Status |
| --- | --- | --- |
| 2 | The structure, action and content parts; person hues; `Card(recessed=)` | Available |
| 3 | TV posture: `tv_posture`, `watch_posture`, `tv_unit`, `AppContext.posture`, `AppWindow` `tv-posture` | Preview |
| 4 | TV parts and media parts | Preview |
| 5 | The system TV keyboard: `set_keyboard_action`, `watch_tv_keyboard`, `tv_keyboard_state` | Preview |
| 6 | TV menus and long press: `TvMenu`, `TvFocus.add(menu=)` | Preview |
| 7 | Remotes and gamepads: `claim_gamepads` | Preview |
| 8 | The C kit's TV island: `luma_ui_tv_posture`, `luma_ui_tv_unit`, `luma_ui_set_part`, `luma_ui_set_keyboard_action` | Preview |
| 9 | `device_noun()`; the floating island for every window: `luma_window_set_tv_frame`, `luma_window_set_tv_active` | Preview |
| 10 | TV wide tiles and app surfaces | Preview |

Read the [changelog](../CHANGELOG.md) before moving to a new platform release.

## Recent changes

| Date | Change |
| --- | --- |
| 2 Nov 2026 | Preview: floating island for sidebar apps on TV (C and Python); TV system dialogs, quick panel and app switcher; TV wide tiles, shelves and grids; key repeat never activates on a TV |
| 1 Nov 2026 | Preview: TV posture, the TV scale and materials, `TvFocus`, the TV toast, idle views, TV media parts, the system TV keyboard, remotes, gamepads and HDMI-CEC, `[tv]` in `luma-app.toml` |
| 30 Oct 2026 | One on-screen keyboard: the Keycap keyboard on the shell; Tablet hides window controls |
| 26 Oct 2026 | Desktop, Tablet and Mobile presentations, followed live; tablet status safe area; `set_edge_to_edge`, `set_status_bar_style`; two apps side by side on a tablet |
| 22 Oct 2026 | Tablet size class decided: short side 760 and long side 1024 or more; three panes landscape, two portrait |
| 21 Oct 2026 | Folds and dual screens: split at the fold, nothing on the crease, continuity when folding |
| 18 to 22 Oct 2026 | Preview: size classes in LumaUI; pocket bar attached to the bottom edge, tucking on scroll; phone insets taken from what the compositor reserves |
| 18 Oct 2026 | The action bar draws one edge |
| 17 Oct 2026 | Size classes: pocket, phone, fold, tablet |
| 16 Oct 2026 | Desktop and phone never diverge: shared parts change once, in shared source |
| 1 and 2 Oct 2026 | Panel triggers become a close key while open; document styles in panel choices; favourites strip; one live media readout for transports and phone footers |
| 30 Sep 2026 | Swipe rows |
| 29 Sep 2026 | Phone layer: tiers, drawers from the bottom-left corner, list first, title island, tab bars; phone action bar rules |
| 25 Sep 2026 | The action center: the bar grows, goes double height, splits and becomes an editor; where actions live; owning-app parts; LumaUI as its own layer on GTK 4 |
| 24 Sep 2026 | One-subject bars, filters at the sidebar's foot, Lucide only, one transport, one file card |

## Topics

- [Build a new app](build-a-new-app.md): from an empty folder to a LumaUI
  app that works on desktop, tablet, phone and TV.
- [LumaUI principles](lumaui-principles.md): why LumaUI exists, the
  semantic-API rule, who owns what, and the deprecation policy.
- [LumaUI behaviour](behaviour.md): hover, press, focus, keyboard, motion,
  auto-close, reduced motion and phone width for every part.
- [Windows and islands](windows-and-islands.md): the window contract, the
  body, islands, split views, and what your stylesheet may not declare.
- [Commands and menus](commands-and-menus.md): one command model for the
  identity menu, context menus, shortcuts, TV menus and semantics.
- [Adaptive layouts](adaptive-layouts.md): breakpoints, test widths and
  patterns from real apps.
- [Background agents](background-agents.md): keep your app working when its
  windows are closed.
- [Semantics and actions](semantics-and-actions.md): stable action IDs, risk
  and privacy, the Semantic Broker, Live Extensions.
- [Python reference](appkit-reference.md) and
  [LumaUI-1 reference](lumaui-reference.md): every part, by name.
