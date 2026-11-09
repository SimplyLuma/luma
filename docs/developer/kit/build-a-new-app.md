---
title: Build a new app
summary: A walkthrough from an empty folder to a LumaUI app that works on desktop, tablet, phone and TV, covering the project, luma-app.toml, the window, the action bar, sidebars and drawers, lists, sheets, light and dark, icons, and testing every size class and posture.
order: 1.5
section: Kit
---

# Build a new app

This guide builds **Recipes**, a small Python app, from an empty folder. It
has a sidebar of collections, a list of recipes, a recipe page with a details
pane, an action bar, a sheet for adding a recipe, and a confirmation before
deleting. Written with LumaUI's parts, the same code works in a desktop
window, full screen on a tablet or a phone, and on a TV.

Read [LumaUI](index.md) first for the ideas. This page is the practice. Labels
follow that page:

- **Available**: in the current Luma Developer Platform (LumaUI API level 2).
- **Preview**: running on Luma test devices; names may change.
- **Planned**: decided, not in LumaUI yet.

## 1. Start the project

On Luma the `luma` command is already installed. Elsewhere, build against the
Luma Platform runtime; see [Install the SDK](../getting-started/install-the-sdk.md).

```sh
mkdir recipes && cd recipes
luma new "Recipes" --id com.example.Recipes
```

You get this tree. [Project layout](../getting-started/project-layout.md)
explains every file.

```text
recipes/
├── luma-app.toml
├── src/
│   └── main.py
├── data/
│   ├── com.example.Recipes.desktop
│   ├── com.example.Recipes.metainfo.xml
│   └── com.example.Recipes.svg
├── flatpak/
│   └── com.example.Recipes.json
└── verification/
    └── luma-native-evidence.json
```

The generated `src/main.py` uses the C library through introspection.
Replace it with the code in this guide. Use a reverse-DNS ID you control; see
[App IDs and namespaces](../publishing/app-ids-and-namespaces.md).

## 2. Say where it runs: luma-app.toml

`luma-app.toml` is the contract `luma lint` checks. The part that matters for
design is `[responsive]`: the widths, inputs and presentations you promise to
test.

```toml
schema_version = "0.2"

[application]
id = "com.example.Recipes"
name = "Recipes"
version = "0.1.0"
entrypoint = "src/main.py"

[platform]
ui_contract = "1"
semantics_contract = "1"

[responsive]
adaptive = true
test_widths = [360, 500, 1024, 1440]
input_modes = ["pointer", "touch", "keyboard"]
presentation_modes = ["windowed", "fullscreen-mobile"]
```

360, 500 and 1024 are required; add your widest window. Keep the other
tables `luma new` wrote (`[distribution]`, `[lifecycle]`, `[conformance]`).
Every key is in [luma-app.toml](../getting-started/luma-app-toml.md).

To appear on the TV home, add a `[tv]` table (preview, see
[step 10](#10-tv)).

## 3. The window

`AppWindow` is LumaUI's window. LumaUI draws its frame, title row, identity
and window controls. Your app gives it commands, which become the identity
menu, the shortcuts and the actions buttons point at.

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Recipes: a LumaUI app for every screen."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from luma_appkit import (
    ActionCenter, AppWindow, BarAction, Command, CommandGroup, CommandRegistry,
    CornerPill, DestructiveDialog, DetailsPane, EmptyState, Island, ListFirst,
    NavigationRow, NavigationSidebar, SidebarFoot, SidebarToggle, SwipeAction,
    SwipeRow, TextField, Toast, ToastHost, install_appkit, lumaui_icon,
)

APP_ID = "com.example.Recipes"


class RecipesWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self.recipes: list[str] = []
        self.current: str | None = None
        commands = CommandRegistry((
            CommandGroup(None, (
                Command("recipe.new", "New recipe", self.ask_new, shortcut=("Ctrl", "N")),
                Command("recipe.delete", "Delete recipe", self.ask_delete, destructive=True,
                        enabled=lambda: self.current is not None),
            )),
            CommandGroup(None, (
                Command("application.quit", "Quit Recipes", application.quit, shortcut=("Ctrl", "Q")),
            )),
        ))
        super().__init__(application=application, app_id=APP_ID, title="Recipes",
                         icon_name=APP_ID, commands=commands,
                         minimum_width=360, minimum_height=294)
        self.build()
```

- `minimum_width=360, minimum_height=294` lets you shrink the window to phone
  width on a desktop. The defaults are larger.
- The window opens at Luma's shared size for its shape. Pass
  `size_class="compact" | "regular" | "wide"` only if the default guess from
  your default size is wrong.
- Never draw a title bar, window controls or your own frame. On Tablet and
  Mobile presentations LumaUI removes the title row and controls for you.

## 4. Sidebar, list and page

Name your structure with LumaUI parts so LumaUI knows what to move at each
size.

```python
    def build(self) -> None:
        # The places: a sidebar with search and New at its foot.
        self.sidebar = NavigationSidebar()
        self.sidebar.append_section("Collections")
        self.sidebar.append_row(NavigationRow("All recipes", icon_name=lumaui_icon("book-open")))
        self.sidebar.append_row(NavigationRow("Favourites", icon_name=lumaui_icon("star")))
        self.sidebar.append_footer(SidebarFoot(
            search="Search recipes", on_search=self.search,
            add=("New recipe", "square-pen", self.ask_new),
        ))

        # The list of recipes.
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.connect("row-activated", lambda _l, row: self.open(row.recipe))
        list_island = Island()
        list_island.append(Gtk.ScrolledWindow(child=self.list, vexpand=True))

        # The page: one recipe, or an empty state.
        self.page = Gtk.Stack(vexpand=True, hexpand=True)
        self.page.add_named(EmptyState(
            "No recipe open", "Pick a recipe, or add your first one.", lumaui_icon("book-open"),
            primary=("New recipe", self.ask_new)), "empty")
        self.recipe_view = Gtk.Label(xalign=0, yalign=0, wrap=True)
        self.page.add_named(self.recipe_view, "recipe")
        page_island = Island()
        overlay = Gtk.Overlay(child=self.page)
        page_island.append(overlay)
        self.page_host = ToastHost(page_island)       # toasts and the bar centre on the page

        # Details beside the page; a drawer at phone width.
        self.details = DetailsPane("Details")

        # Top right of the page: Share, Favourite, Information, then ⋯.
        overlay.add_overlay(CornerPill(share=self.share,
                                       states=[("star", "Favourite", False, self.favourite)],
                                       info=self.details, more=self.commands))

        # List and page side by side; list first on a phone.
        content = Gtk.Box(spacing=0)
        content.append(self.page_host)
        content.append(self.details)
        self.stack = ListFirst(list_island, content, title="Recipes")

        row = Gtk.Box(spacing=8)
        row.append(self.sidebar)
        row.append(self.stack)
        self.set_body(row)
        self.set_leading(SidebarToggle(self.sidebar))  # F9 hides and shows it

        self.build_bar()
```

What this gives you without more code:

| Width | Sidebar | List and page | Details | Menus |
| --- | --- | --- | --- | --- |
| Desktop (over 900) | Beside the list, F9 toggles | Side by side | Beside the page | Popovers |
| Compact (560 to 900) | Toggle to show it | Side by side | Beside the page | Popovers |
| Phone (under 560) | Drawer from the bottom-left corner | List first; picking a row pushes the page; Back top left | Bottom drawer | Bottom drawers |

On a phone, navigation is top left, never in the bar. If your page has a
title island (`TitleIsland`), pass it to `SidebarToggle(sidebar,
island=title_island)` and `ListFirst.attach_island()` so its ☰ and Back do
the work.

Filters (All, Favourites, Recently deleted) go in `SidebarFoot(filters=...)`
at the sidebar's foot, never as tabs stacked under the search field.

## 5. The action bar

The bar is where the person acts on what is open. Attach one to the page and
change its contents as the state changes. It grows into panels in place
instead of opening dialogs.

```python
    def build_bar(self) -> None:
        self.bar = ActionCenter()
        self.bar.attach(self.page_host)
        self.show_bar()

    def show_bar(self) -> None:
        if self.current is None:
            self.bar.show_bar([BarAction("plus", "New recipe", primary=True, on_activate=self.ask_new)])
            return
        self.bar.show_bar([
            BarAction("timer", "Start cooking", primary=True, on_activate=self.cook),
            BarAction("printer", tooltip="Print", on_activate=self.print_recipe),
        ])
        self.bar.more([BarAction("copy", "Duplicate", on_activate=self.duplicate),
                       BarAction("trash-2", "Delete", danger=True, on_activate=self.ask_delete)])
```

- One primary action per bar. Rare or destructive actions go under ⋯
  (`more()`).
- Views (grid or list, sort) are not actions. Put them in the corner pill or
  behind ⋯.
- With a selection, show only that item's actions.
- On a phone LumaUI makes the keys thumb-sized, shows icons alone, and moves
  what does not fit into ⋯. On a phone whose screen's long side is under 600
  it attaches the bar to the bottom edge.
- Give a scroller behind the bar room above it on a phone with
  `self.bar.attach_scroller(scroller)`.

For a reply or compose flow, give the bar an `ActionEditor` and call
`grow()`; see [The action bar](index.md#the-action-bar).

## 6. Sheets, confirmations and toasts

Ask for more with a sheet from the bar. On a phone it rises from the bar's
place; on a computer it is a card centred on the window.

```python
    def ask_new(self) -> None:
        name = TextField("Name", placeholder="Lemon tart")
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        form.append(name)
        add = Gtk.Button(label="Add")
        form.append(add)
        sheet = self.bar.sheet(form, title="New recipe")
        add.connect("clicked", lambda _b: (self.add(name.text), sheet.close()))

    def add(self, name: str) -> None:
        if not name:
            return
        self.recipes.append(name)
        row = SwipeRow(Gtk.Label(label=name, xalign=0),
                       start=SwipeAction("star", "yellow", lambda _r: self.favourite(True), label="Favourite"),
                       end=SwipeAction("trash-2", "red", lambda _r: self.ask_delete(), label="Delete"))
        list_row = Gtk.ListBoxRow(child=row)
        list_row.recipe = name
        self.list.append(list_row)
        Toast.show(self.page_host, f"Added {name}", kind="added")
        self.open(name)

    def ask_delete(self) -> None:
        if self.current is None:
            return
        DestructiveDialog.ask(self.page_host, title=f"Delete {self.current}?",
                              body="It is removed from every collection.", action="Delete",
                              on_confirm=lambda _option: self.delete())

    def delete(self) -> None:
        name = self.current
        self.recipes.remove(name)
        self.current = None
        self.page.set_visible_child_name("empty")
        self.details.show(subject=None)            # the details pane closes with its subject
        self.show_bar()
        Toast.show(self.page_host, f"{name} deleted", kind="deleted", undo=lambda: self.add(name))
```

- `DestructiveDialog` names the action ("Delete"), never "OK" or "Are you
  sure?". On a phone it rises from the bar's place.
- A toast is one plain past-tense line, with Undo when the action can be
  undone. Never a question.
- Swipe rows are on at phone width only. Every swipe action must also be in
  the row's menu.

Finish the window with the small methods the code above calls (`open`,
`search`, `share`, `favourite`, `cook`, `print_recipe`, `duplicate`) and the
application:

```python
    def open(self, name: str) -> None:
        self.current = name
        self.recipe_view.set_label(name)
        self.page.set_visible_child_name("recipe")
        self.details.show(subject=name)
        self.show_bar()
        self.stack.show_detail()


class RecipesApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_activate(self) -> None:
        install_appkit()                   # once, after GTK has a display
        (self.get_active_window() or RecipesWindow(self)).present()


if __name__ == "__main__":
    raise SystemExit(RecipesApplication().run(sys.argv))
```

## 7. Showing other apps' things

If a recipe mentions a person, an event, a file, a place or a song, show the
owning app's card for it: `ContactCard`, `EventCard`, `FileCard`, `PlaceCard`,
`SongCard`, `NoteCard`. People recognise them from Contacts, Calendar, Filer,
Maps, Tide and Notes. Never draw a look-alike. See
[Showing another app's things](index.md#showing-another-apps-things).

## 8. Light, dark, colour and type

You write no colours and no fonts.

- Every LumaUI part follows light, dark, Frost, Glass and high contrast by
  itself. The token sheets reload when the appearance changes.
- If you need a stylesheet for your own content, load it with
  `add_style_sheet(path)` and use only `@luma_*` colours. A hex or `rgba()`
  value, a `@define-color`, or a rule that selects a LumaUI part
  (`.lumaui-*`, `.luma-island`, `headerbar`, `windowcontrols`) is a defect.
- Where a screen belongs to something with a colour (a recipe's photo, a
  person's hue), use `ContentLitHeader` or `hue_class()` rather than painting
  it yourself.
- Type is Figtree. Use `TypeLabel` or `apply_type()` with a role (title 1,
  body, caption) instead of sizes. Changing numbers use tabular figures; line
  heights are whole pixels.

See [Color](../design/color.md) and [Typography](../design/typography.md).

## 9. Icons

- Symbols are Lucide names: `"book-open"`, `"star"`, `"trash-2"`. LumaUI's
  parts (`BarAction`, `SidebarFoot`, `SwipeAction`, `CornerPill`) take the
  Lucide name directly. Where a GTK widget wants a theme name (an
  `icon_name=` argument, `Gtk.Image`), pass `lumaui_icon("book-open")`.
- Never draw inline SVG symbols and never name an icon that has no file.
- Share is `share-2`, Export is `share`, Upload is `upload`; New is
  `square-pen`; Delete is a red `trash-2`.
- Your app icon (`data/com.example.Recipes.svg`) follows Luma's icon family:
  a plate lit from the upper left, one object, a rim. See
  [Icons](../design/icons.md).

## 10. TV

Preview. A sidebar app like Recipes needs no TV code: in TV posture the
window runs full screen without its title row, the sidebar lifts into a
floating island that folds to a rail, the page draws at TV size, the menus
become TV menus (hold OK or press Menu), and fields open the TV keyboard.

To appear on the TV home, opt in:

```toml
[tv]
supported = true
tile = "data/com.example.Recipes-tv.svg"   # the 16:9 wide tile
presentation = "fullscreen"                # your normal view, full screen; "tv" for your own TV view
```

`luma lint` checks that the desktop entry carries the matching keys:

```ini
X-Luma-TV=true
X-Luma-TV-Tile=com.example.Recipes-tv
X-Luma-TV-Presentation=fullscreen
```

Install the tile as an icon named `com.example.Recipes-tv`, made from your app
icon's plate, art and rim with the art centred at 1.15×.

A media, browsing or full-screen app can add its own TV view with the TV
parts and switch to it live:

```python
from luma_appkit import lumaui, TvStage, TvTopBar, TvFocus, TvCover

def show_for_posture(tv: bool) -> None:
    window.set_body(tv_view() if tv else normal_view())

stop = lumaui.watch_posture(show_for_posture)

def tv_view() -> Gtk.Widget:
    stage = TvStage()
    stage.place(TvTopBar(), **TvTopBar.PIN)
    focus = TvFocus(stage, on_back=None)
    for name in recipes:
        cover = TvCover(shape="landscape", title=name)
        focus.add(cover, on_activate=lambda n=name: open_recipe(n))
    return stage
```

Rules for a TV view: one bright thing (the focus); arrows move to the nearest
thing; OK opens; Back steps out; nothing depends on hover; sizes come from
the TV tokens, never your own numbers. See [TV](index.md#tv).

## 11. Test every size and posture

Run the app on a desktop with the switches LumaUI reads:

```sh
python3 src/main.py                                                   # desktop
LUMA_PRESENTATION_MODE=fullscreen-mobile LUMA_INPUT_MODE=touch \
  python3 src/main.py                                                 # phone presentation, touch
LUMA_DEVICE_CLASS=tablet LUMA_INPUT_MODE=touch python3 src/main.py    # tablet
LUMA_FORM_FACTOR=phone python3 src/main.py                            # a phone device
LUMA_POSTURE=tv python3 src/main.py                                   # TV posture (preview)
GTK_DEBUG=interactive python3 src/main.py                             # sizes and accessibility
```

Then, for each:

- Resize through 360, 500, 1024 and your widest width. At each, every
  function is still there: in the bar, behind ⋯, in a drawer or a sheet.
- Use touch only: targets are at least 44 px, nothing needs hover, every
  context menu opens on long press.
- Use the keyboard only: every function is reachable with a visible focus;
  Esc folds what grew; Ctrl+N and Ctrl+Q work.
- Try a screen reader, large text, high contrast, reduced motion, a long
  translated language and a right-to-left one.
- Size classes are read from the phone's screen, not the window (preview):
  pocket (long side under 600), phone (short side under 560), fold (short
  side 560 or more), tablet (short side 760 or more and long side 1024 or
  more). Check the pocket and tablet layouts on a device or an emulated
  display of that size.
- In TV posture, drive the app with the arrow keys, Return, Escape and Menu
  only.

Check the contract:

```sh
luma lint
luma inspect
luma test
luma release-check
```

See [The `luma` command](sdk-command-line.md).

## Rules to check before you publish

1. One source tree and one binary for every presentation and posture.
2. Layout follows width; it never guesses the device.
3. Compact layouts keep every function.
4. Grow the action bar before opening a dialog. One primary action per bar.
5. Navigation is top left on a phone; views are never in the bar.
6. Panels and popovers work as drawers on a phone.
7. Another app's things use the owning app's card.
8. Colours from tokens, icons from Lucide, type in Figtree. No restyled
   LumaUI parts.
9. Every custom widget has an accessible name and role.
10. Each operation has one command, shared by its button, menu item and
    shortcut.

## Next

- [LumaUI](index.md): every part, size class and posture.
- [Windows and islands](windows-and-islands.md),
  [Commands and menus](commands-and-menus.md),
  [Adaptive layouts](adaptive-layouts.md),
  [LumaUI behaviour](behaviour.md).
- [Python reference](appkit-reference.md) and
  [LumaUI-1 reference](lumaui-reference.md) for C.
- [Publishing to Depot](../publishing/index.md) when it is ready.
