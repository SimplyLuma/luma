---
title: LumaUI Python reference
summary: LumaUI's Python package, luma_appkit: its public classes and functions, with their purpose, constructors, properties, signals and methods as they exist in platform 0.1.0.
order: 2
section: Kit
---

# LumaUI Python reference

`luma_appkit` is LumaUI’s Python package. Import names from the package root:

```python
from luma_appkit import AppWindow, Island, CommandRegistry, Command, CommandGroup
```

This page covers platform **0.1.0 (preview)**. The public names are those in
`luma_appkit.__all__`; anything starting with an underscore is private. The
signatures below show the main parameters. Some constructors take further
keyword arguments, and the newer LumaUI parts are summarized at the end of the
page; each module’s docstrings describe the rest. For the C library, see the
[LumaUI-1 reference](lumaui-reference.md) and [LumaUI for C](lumaui-c.md).

**Requirements.** Python 3.11 or newer, PyGObject, GTK 4, libadwaita (LumaUI
builds on its window, breakpoint and split-view types and draws them itself),
and the `LumaUI-1` and `LumaAppearance-1` typelibs from
`luma-developer-platform`.
Importing a widget loads GTK; `luma_appkit.commands`, `luma_appkit.context` and
`luma_appkit.navigation` can be imported without a display, which makes them
easy to unit test.

**Spelling.** A few API names use British spelling (`centre_controls`, the
`colour` argument of `ColorSwatch`). Use them exactly as written.

## Contents

| Area | Names |
| --- | --- |
| [Setup](#setup) | `install_appkit`, `add_style_sheet`, `motion_duration` |
| [Context](#context) | `AppContext`, `PresentationMode`, `InputMode` |
| [Window and structure](#window-and-structure) | `AppWindow`, `Island`, `IslandSplitView`, `IslandOverlaySplitView`, `MobileHeader` |
| [Commands and menus](#commands-and-menus) | `Command`, `CommandGroup`, `CommandRegistry`, `command_menu_model`, `Menu`, `command_popover`, `attach_context_menu`, `command_actions`, `command_menu`, `accelerator`, `modifier_text`, `point_rectangle` |
| [Navigation](#navigation) | `NavigationSidebar`, `NavigationRow`, `SectionLabel`, `NavigationTrail`, `Place`, `bind_navigation_input` |
| [Bars and controls](#bars-and-controls) | `Toolbar`, `centre_controls`, `StatusBar`, `IconButton`, `ConnectedButtonGroup`, `SaveStatus`, `InlineRenameField` |
| [Empty states](#empty-states) | `EmptyState`, `ListEmptyState` |
| [People and facts](#people-and-facts) | `Avatar`, `avatar_tone`, `AVATAR_TONES`, `FieldRow` |
| [Design tools](#design-tools) | `NumericField`, `evaluate_number`, `AlignCluster`, `ColorSwatch` |
| [Media and transactions](#media-and-transactions) | `AudioWaveform`, `TransactionCard` |
| [LumaUI](#lumaui) | `LUMAUI_API_LEVEL`, `install_lumaui`, `reduced_motion`, `lumaui_icon`, `LayerHost`, `Toast`, `ToastHost`, `TOAST_KINDS`, `DestructiveDialog`, `StackedButton`, `StackedButtons`, `CountBadge`, `count_text`, `CategoryPill`, `CATEGORIES`, `TypeLabel`, `apply_type`, `type_class`, `TYPE_ROLES` |

---

## Setup

### `install_appkit()`

```python
install_appkit() -> None
```

Loads LumaUI for the default display: the token sheet for the current
appearance (light, dark, Frost, Glass or high contrast), LumaUI’s window sheets,
the base and component sheets at `GTK_STYLE_PROVIDER_PRIORITY_APPLICATION`, and
the structural sheet at `APPLICATION + 2`. It then reloads them whenever the
appearance or high contrast changes. Call it once after GTK has a display, for
example at the top of `do_activate`; later calls do nothing. `AppWindow` calls
it for you.

Environment overrides, for testing LumaUI from source: `LUMA_APPKIT_TOKENS_PATH`,
`LUMA_APPKIT_BASE_PATH`, `LUMA_APPKIT_STRUCTURE_PATH`, and
`LUMA_APPKIT_STYLE_PATH` (which also stops the automatic reload).

### `add_style_sheet(path, priority=Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)`

```python
add_style_sheet(path: str, priority: int = 601) -> Gtk.CssProvider
```

Loads your app’s own stylesheet and re-parses it whenever the appearance
changes, so any `@luma_*` color it names follows the desktop into dark and back.
Calls `install_appkit()` first. Returns the provider. Use this rather than
creating a `Gtk.CssProvider` yourself.

```python
add_style_sheet(str(Path(__file__).with_name("notebook.css")))
```

### `motion_duration(milliseconds)`

```python
motion_duration(milliseconds: int) -> int
```

Returns `0` when the person has turned animations off
(`gtk-enable-animations` is false), otherwise the duration. Pass every
animation duration through it.

---

## Context

### `AppContext`

```python
@dataclass(frozen=True)
class AppContext:
    presentation: PresentationMode
    input_mode: InputMode
    device_class: str   # "desktop", "tablet" or "handheld"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> AppContext
```

How the app is being presented. `from_environment()` reads
`LUMA_DEVICE_CLASS` (else `/etc/luma-device-class`, else `desktop`),
`LUMA_PRESENTATION_MODE` and `LUMA_INPUT_MODE`. Without explicit values, a
handheld is presented fullscreen, and tablets and handhelds use touch input.
Unknown values fall back silently. `AppWindow` creates one as `window.context`.

Use presentation to decide chrome and input to decide target sizes. Never use
either, or the device class, to choose a layout; use width.

### `PresentationMode`

`str` enum: `WINDOWED = "windowed"`, `FULLSCREEN = "fullscreen-mobile"`.

### `InputMode`

`str` enum: `POINTER = "pointer"`, `TOUCH = "touch"`.

---

## Window and structure

### `AppWindow`

```python
class AppWindow(Adw.ApplicationWindow):
    def __init__(self, *, application: Adw.Application, app_id: str, title: str,
                 icon_name: str, commands: CommandRegistry, subtitle: str = "",
                 default_width: int = 820, default_height: int = 560,
                 minimum_width: int = 460, minimum_height: int = 380,
                 geometry_scope: str = "", size_class: str = "auto",
                 narrow_width: int = 0, identity_menu_variant: str = "app",
                 identity_icon: bool = True) -> None
```

The Luma window. It carries `luma-app-window`, holds an `Adw.ToolbarView`
whose top bar is an `Adw.HeaderBar` carrying `luma-titlebar`, and a body that
carries `luma-window-body`. LumaUI draws the frame, the title row, the
identity (`WindowIdentity`, with LumaUI’s `Menu`) and the controls from its own
window sheets; apps draw none of it. See
[windows and islands](windows-and-islands.md).

On construction it:

- creates `self.context` from the environment;
- sets the minimum size and restores the last width, height and maximized
  state (bounded to 4096 by 2160). When the desktop’s own window memory is on,
  the window leaves placement to it. With nothing remembered, the window opens
  at the shared Luma opening size for its shape;
- is decorated when windowed and undecorated when `fullscreen-mobile`, hiding
  the title bar;
- binds a compositor backdrop for Frost and Glass where the compositor supports
  it, falling back to opaque;
- calls `application.set_menubar(command_menu_model(commands, application))`,
  which registers each command as an `app.` action and installs its shortcut;
- gives any `Adw.AlertDialog` it presents the quiet control treatment.

`icon_name` is a fallback: the identity takes its name and icon from your
desktop entry, and uses `icon_name` only when no installed entry names an icon
(a test run, for example). `identity_icon=False` shows the name without the
icon, and `identity_menu_variant` is the `Menu` variant of the identity menu.
`size_class` is `"auto"` (the shape follows the default size), `"compact"`,
`"regular"`, `"wide"` or `"utility"`. `narrow_width` is the width at which your
narrow layout begins, so the window opens wider than that on a desktop; any
`Adw.Breakpoint` you add with `add_breakpoint` is counted automatically.
**Minimums:** the defaults are 460 by 380. Pass
`minimum_width=360, minimum_height=294` for an app that must fit a phone.

On a desktop the title row cannot be hidden: if something hides it on a
decorated window, `AppWindow` shows it again so the window can still be closed,
moved and resized.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `context` | `AppContext` | Presentation and input |
| `commands` | `CommandRegistry` | The registry passed in; application actions route to the active window’s registry |
| `title_bar` | `Adw.HeaderBar` | The title row. Use the slot methods below rather than packing it directly; most controls belong in an island toolbar. |
| `body` | `Gtk.Box` | Horizontal box, 8px spacing, carrying `luma-window-body` |
| `tier` | `str` | `"phone"` (under 560px), `"compact"` (560 to 900px) or `"regular"` |

| Method | Behavior |
| --- | --- |
| `set_body(widget)` | Replaces the body’s content with `widget` and makes it expand in both directions |
| `set_identity_subtitle(subtitle)` | Sets the window title to “subtitle — app title”, or just the app title when empty. Use it for the open document. |
| `set_leading(widget)` | One control in the title row just after the identity, such as a `SidebarToggle`. `None` empties it. |
| `set_title_content(widget, *, centred=False)` | What the window is about (a document’s name and state), after the leading control or centred on the window. `None` empties it. |
| `set_trailing(widget)` | The window’s own actions at the right of the title row, before the window controls. `None` empties it. |
| `present_sheet(sheet, keys=None)` / `dismiss_sheet()` | Hangs a sheet from the title row and makes the rest of this window wait (dimmed, not focusable), with the app’s own key handling parked until it is dismissed. `presented_sheet` is the sheet showing, if any. |
| `declare_narrow_width(width)` | Says where the narrow layout begins when its breakpoints live on an `Adw.BreakpointBin` inside the window |
| `remember(key, value)` | Saves one small JSON-serializable value beside the window geometry |
| `recall(key, default=None)` | Returns a remembered value |
| `set_geometry_scope(scope, *, default_width, default_height)` | Switches to a separately remembered size, for example a welcome surface. `scope` must be ASCII letters, digits, `-` or `_`. |

State lives in `$XDG_STATE_HOME/luma/windows/<app_id>.json` (and
`<app_id>.<scope>.json` for a scope), written with mode `0600` when the window
closes or when you call `remember`.

### `Island`

```python
class Island(Gtk.Box):
    def __init__(self, *, orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL) -> None
```

A work island: a `Gtk.Box` carrying `luma-island` that clips its children to
its rounded corners (`overflow: hidden`). LumaUI draws its radius, ring and
shadow. Put islands in `AppWindow.body` or in a split view. Do not nest islands.

### `IslandSplitView(**properties)`

```python
IslandSplitView(**properties) -> Adw.NavigationSplitView
```

Creates a real `Adw.NavigationSplitView` (it is final, so LumaUI composes it)
carrying `luma-island-split`. While sidebar and content sit side by side, the
split view and its pages stop clipping so island shadows show on every edge;
when collapsed, clipping returns so navigation transitions stay clean. Pass the
usual properties, such as `sidebar=` and `content=` with `Adw.NavigationPage`
children, and collapse it with an `Adw.Breakpoint`.

### `IslandOverlaySplitView(**properties)`

```python
IslandOverlaySplitView(**properties) -> Adw.OverlaySplitView
```

The same treatment for `Adw.OverlaySplitView`, for a sidebar that overlays the
content on narrow windows instead of becoming a separate page.

### `MobileHeader`

```python
class MobileHeader(Gtk.Box):
    def __init__(self, title: str, *, context: AppContext,
                 leading_icon: str | None = None, leading_label: str = "Back",
                 trailing_icon: str | None = None, trailing_label: str = "More") -> None
```

The handheld title row for a page when the app is presented fullscreen and has
no window title row: a leading control, a centered title, a trailing control.
Both slots always take space so the title stays centered. Slots with an icon
are quiet `IconButton`s; connect them yourself.

| Attribute | Type |
| --- | --- |
| `leading`, `trailing` | `IconButton`, or an empty `Gtk.Box` placeholder |
| `title_label` | `Gtk.Label` |

```python
header = MobileHeader("All notes", context=window.context,
                      leading_icon="go-previous-symbolic",
                      trailing_icon="view-more-symbolic")
header.leading.connect("clicked", lambda *_: trail.back())  # your NavigationTrail
```

---

## Commands and menus

See [commands and menus](commands-and-menus.md) for how these fit together.

### `Command`

```python
@dataclass
class Command:
    id: str
    label: str
    execute: Callable[[], None]
    icon: str | None = None
    description: str = ""
    shortcut: tuple[str, ...] = ()
    visible: Callable[[], bool] = lambda: True
    enabled: Callable[[], bool] = lambda: True
    checked: Callable[[], bool] | None = None
    destructive: bool = False
    children: tuple[Command, ...] = ()
    count: int | None = None
```

| Field | Meaning |
| --- | --- |
| `id` | Stable dotted ID such as `note.new`. It becomes the action name with dots replaced by dashes. Never reuse or rename a shipped ID. |
| `label` | Menu text, in sentence case |
| `execute` | Called with no arguments |
| `icon` | Symbolic icon name, shown in menus that show icons |
| `description` | A second line under the label, for choices that need explaining |
| `shortcut` | Keys as a tuple, for example `("Ctrl", "Shift", "S")`. See `accelerator`. |
| `visible` | Predicate. Hidden commands are left out of menus and cannot be invoked. |
| `enabled` | Predicate. Disabled commands cannot be invoked. |
| `checked` | Predicate for a toggle. Makes the action stateful; `Menu` draws the chosen row filled and heavier. |
| `destructive` | Draws the row in the destructive treatment on hover and focus. Put these last. |
| `children` | A submenu |
| `count` | A number drawn as a count badge at the end of the row, such as a filter’s total. `None` draws nothing. |

### `CommandGroup`

```python
@dataclass(frozen=True)
class CommandGroup:
    label: str | None
    commands: tuple[Command, ...] = ()
    quick_actions: bool = False
    in_menu: bool = True
```

A section of a menu. `label` becomes the section heading; `None` for no
heading. With `quick_actions=True`, a group of two to four commands that have
no children and are not destructive is drawn by `Menu` as a strip of large icon
buttons at the top of the menu. With `in_menu=False` the group’s commands keep
their actions and shortcuts but are left out of menus.

### `CommandRegistry`

```python
class CommandRegistry:
    def __init__(self, groups: Iterable[CommandGroup] = ()) -> None
    groups: tuple[CommandGroup, ...]
    def get(self, command_id: str) -> Command          # raises KeyError
    def invoke(self, command_id: str) -> bool
    def visible_groups(self, *, menu: bool = False) -> tuple[CommandGroup, ...]
```

The single list of what your app can do. Raises `ValueError` on a duplicate ID,
including IDs of children. `invoke` runs the command only if it is visible and
enabled and returns whether it ran. `visible_groups` returns groups with hidden
commands removed and empty groups dropped; `menu=True` also drops groups with
`in_menu=False`.

### `command_menu_model(registry, application)`

```python
command_menu_model(registry: CommandRegistry, application: Gio.Application) -> Gio.Menu
```

Builds the application menubar model. For each visible top-level command it
adds an `app.<id-with-dashes>` action (once per name) that invokes the command
on the active window’s `commands`, adds a menu item with the command’s icon, and
sets its shortcut as an application accelerator. `AppWindow` calls this for you.

The identity pill itself shows a LumaUI `Menu` built from the same registry,
so checked rows, descriptions, destructive rows and submenus appear there. Both
are built once, when the window is created, so which commands are listed is
decided by `visible` at that moment.

### `Menu`

```python
class Menu(Gtk.PopoverMenu):
    def __init__(self, registry: CommandRegistry, *, variant: str = "app",
                 keep_parent: bool = False, phone_placement: str = "drawer") -> None
    def present_at_pointer(self, widget: Gtk.Widget, x: float, y: float) -> None
    def present_for(self, widget: Gtk.Widget, *, focus_first: bool = False) -> None
    def popup_below(self, anchor: Gtk.Widget) -> None
```

A `Gtk.PopoverMenu` built from a registry, whose rows LumaUI draws. Variants:
`"app"`, `"compact"`, `"desktop"`, `"dock"`, `"submenu"` (unknown values become
`"app"`). A menu lets go of its parent after a command runs unless
`keep_parent=True`; pass that for a menu you give to a `Gtk.MenuButton`. At
phone width it opens as a bottom drawer (`phone_placement="drawer"`) or stays
a popover (`"popover"`). It supports section headings, shortcuts shown as keycaps,
checked rows, disabled rows that keep their place, destructive rows, two-line
described rows, submenus that open beside their row, and the quick-actions
strip. Enabled and checked state are refreshed every time it opens. Choosing a
row closes the menu and runs the command on the next idle, so a command may
safely rebuild the widget the menu was opened from.

`present_at_pointer` parents the menu to `widget` if needed and opens it with
its corner at the point. `present_for` opens it against `widget`; with
`focus_first=True` it moves keyboard focus to the first row. `popup_below`
opens it under an anchor it is already parented to, left-aligned with it.

### `command_popover(registry, *, variant="app")`

```python
command_popover(registry: CommandRegistry, *, variant: str = "app") -> Gtk.Popover
```

Returns a `Menu` that unparents itself after it closes. Use it when you build a
menu per click, then call `present_for(button)` or
`present_at_pointer(widget, x, y)`.

A menu from `command_popover()` lets go of its parent after it closes, so it
can’t be a `Gtk.MenuButton`’s popover. Open a new one from a button’s `clicked`
handler, or give a menu button `Menu(registry, keep_parent=True)`. See
[commands and menus](commands-and-menus.md#menu-buttons).

### `attach_context_menu(widget, registry, *, variant="desktop")`

```python
attach_context_menu(widget: Gtk.Widget, registry: CommandRegistry, *, variant: str = "desktop") -> None
```

Gives a widget its own context menu, opened by secondary click, by touch long
press, and by the **Menu** key or **Shift+F10** (which also focuses the first
row). For widgets that already have GTK’s context menu (anything with
`set_extra_menu`, such as `Gtk.Entry`, `Gtk.TextView` and selectable
`Gtk.Label`), it adds your
commands as an extra menu instead of a second popover.

### `command_actions(registry, *, on_invoked=None, before_invoke=None)`

```python
command_actions(registry, *, on_invoked=None, before_invoke=None) -> Gio.SimpleActionGroup
```

One `Gio.SimpleAction` per visible command (including children), named with
dashes, stateful when `checked` is set, enabled per `enabled`. Activating it
calls `registry.invoke`. `on_invoked` runs after a successful invoke;
`before_invoke(run)` receives the invocation and decides when to call it.
Insert the group on a widget with `insert_action_group("menu", group)`.

### `command_menu(registry, *, prefix="menu", custom=None, decorations=None, own_rows=False)`

```python
command_menu(registry, *, prefix: str = "menu", custom: dict | None = None,
             decorations: dict | None = None, own_rows: bool = False) -> Gio.Menu
```

The registry as a `Gio.MenuModel` whose items target `<prefix>.<id-with-dashes>`,
with icons, accelerators and submenus. Groups with `in_menu=False` are left
out. Without `custom`, destructive and described commands become ordinary rows,
which is right for a text view’s extra menu. `Menu` uses `custom`,
`decorations` and `own_rows` internally.

### `accelerator(shortcut)`

```python
accelerator(shortcut: tuple[str, ...]) -> str
```

Converts a shortcut tuple into a GTK accelerator string.
`accelerator(("Ctrl", "Shift", "S"))` returns `"<Control><Shift>s"`. Mac-style
names are accepted and mapped to Linux keys: `⌘` and `cmd` become Ctrl, `⌥` and
`option` become Alt.

### `modifier_text(key)`

```python
modifier_text(key: str) -> str
```

The key as a Linux keyboard names it: `modifier_text("⌫")` returns
`"Backspace"`, `"cmd"` returns `"Ctrl"`. Used for keycaps.

### `point_rectangle(x, y)`

```python
point_rectangle(x: float, y: float) -> Gdk.Rectangle
```

A 1 by 1 rectangle at a point for `Gtk.Popover.set_pointing_to`. Use it instead
of `Gdk.Rectangle(x, y, 1, 1)`, which PyGObject silently creates at 0, 0.

---

## Navigation

### `NavigationSidebar`

```python
class NavigationSidebar(Gtk.Box):
    def __init__(self, *, variant: str | None = None, width: str | None = None,
                 compact: bool = False) -> None
```

The navigation sidebar, with a scrolling single-selection list and an optional
pinned header and footer. It is not a
boxed island: it stands on the window frame's own surface, its rows 10px from
the window edge and from the content island, 272px from the window edge to the
island. Rows are 50px with a 34px round face; the selected row is the raised
chip. Every number is a `lumaui.sidebar` token. `variant` is one of
`SIDEBAR_VARIANTS` (`"people"`, `"destinations"`, `"resources"`, `"files"`,
`"tree"` or `"settings"`), and `width` a named width (`"narrow"`, `"regular"`,
`"wide"`). `compact=True` is deprecated: it is `variant="files"`.

| Attribute | Type |
| --- | --- |
| `list` | `Gtk.ListBox` in single selection mode. Connect `row-selected` or `row-activated` here. |
| `footer` | `Gtk.Box`, hidden until you add to it |

| Method | Behavior |
| --- | --- |
| `append_section(label, *, action=None, subtitle=None, hue=None, ruled=False)` | Adds a non-selectable heading, as written ("A", "This week"), with a hairline beside it. `action=(icon, label, callback)` gives it its + button. |
| `append_row(row)` | Adds a `NavigationRow` or `SidebarRow` |
| `append_header(widget)` | Pins a widget above the list, such as a search entry |
| `append_footer(widget)` | Pins a widget below the list, such as account or sync state, and shows the footer |
| `set_phone_title(title)` | A large title over the list, shown only at phone width. `None` removes it. |
| `clear()` | Removes every row and heading from the list |

### `NavigationRow`

```python
class NavigationRow(Gtk.ListBoxRow):
    def __init__(self, title: str, *, icon_name: str | None = None,
                 icon_widget: Gtk.Widget | None = None, trailing: str = "",
                 subtitle: str = "", status: str | None = None,
                 thumbnail: bool = False) -> None
    def set_trailing(self, trailing: str) -> None
```

A sidebar destination. `icon_name` shows a 14px symbolic icon on a 34px round
tile; pass `icon_widget` instead for an avatar (sized to 34px in a sidebar) or a
glyph you draw. `trailing` is a live value such as
an unread count; update it with `set_trailing` rather than rebuilding the row,
so selection and focus are kept. `status` adds a 6px status dot whose CSS class
and tooltip come from the string. `thumbnail` uses the larger thumbnail row. The
row’s accessible label combines title, subtitle and trailing value.

### `SectionLabel`

```python
class SectionLabel(Gtk.Label):
    def __init__(self, label: str, *, variant: str = "sidebar") -> None
```

A quiet uppercase section label. `variant` is `"sidebar"` or `"content"`;
anything else raises `ValueError`. The text is uppercased for you, so pass it in
normal case.

### `Place`

```python
@dataclass(frozen=True)
class Place:
    view: str
    title: str
    subject: Hashable = None
```

One step in a navigation trail: which page, its title, and what it shows (for
example an album ID).

### `NavigationTrail`

```python
class NavigationTrail:
    def __init__(self, root: Place, on_change: Callable[[Place, str], None] | None = None) -> None
    places: tuple[Place, ...]      # read-only
    current: Place                 # read-only
    can_go_back: bool              # read-only
    can_go_forward: bool           # read-only
    def start(self, root: Place) -> None
    def open(self, place: Place) -> None
    def replace(self, place: Place) -> None
    def back(self) -> bool
    def forward(self) -> bool
    def go_to(self, index: int) -> bool
```

Back and forward history inside an app. It is hierarchical at the root and
chronological above it: choosing a top-level destination calls `start`, opening
something from a page calls `open`. `back`, `forward` and `go_to` (return to an
earlier breadcrumb) return whether they moved. `replace` changes the current
page without adding a step or calling `on_change`. `on_change(place, direction)`
is called with `direction` of `"root"`, `"forward"` or `"back"`; render the page
there. No GTK is needed to use it.

### `bind_navigation_input(widget, back, forward)`

```python
bind_navigation_input(widget, back: Callable[[], bool], forward: Callable[[], bool]) -> None
```

Routes every conventional back and forward input on `widget` (usually the
window) to your callbacks: mouse buttons 8 and 9, **Alt+Left** and
**Alt+Right**, and the Back and Forward keys. Each callback returns whether it
moved; if not, the input continues to the rest of the window.

```python
self.trail = NavigationTrail(Place("albums", "Albums"), self.show_place)
bind_navigation_input(self, self.trail.back, self.trail.forward)
```

---

## Bars and controls

### `Toolbar`

```python
class Toolbar(Gtk.Box):
    def __init__(self) -> None
    def append(self, child: Gtk.Widget) -> None
```

The standard island toolbar: horizontal, 8px between controls, carrying
`luma-toolbar`. `append` centers each control vertically instead of stretching
it to the bar’s height.

### `centre_controls(bar)`

```python
centre_controls(bar: Gtk.Widget) -> None
```

Stops a bar you built yourself from stretching its controls: in a horizontal
bar, children with `FILL` vertical alignment become `CENTER`; in a vertical rail,
the same for horizontal alignment. Call it after filling the bar. `Toolbar`
already does this.

### `StatusBar`

```python
class StatusBar(Gtk.Box):
    def __init__(self) -> None
    def append_text(self, label: str, *, end: bool = False) -> Gtk.Label
```

A compact island footer for totals, ranges, capacity and sync state.
`append_text` adds a label and returns it so you can update it; `end=True`
right-aligns it and lets it take the remaining space.

### `IconButton`

```python
class IconButton(Gtk.Button):
    def __init__(self, icon_name: str, label: str, *, context: AppContext,
                 quiet: bool = False) -> None
```

A button showing a 14px symbolic icon, with `label` as both its accessible name
and tooltip. `quiet=True` removes its resting fill. Connect `clicked` or set an
action name. `context` is required, but the button does not change its target
size for touch input.

### `ConnectedButtonGroup`

```python
class ConnectedButtonGroup(Gtk.Box):
    def __init__(self, *, tint: str | None = None, compact: bool = False,
                 context: AppContext | None = None) -> None
```

Related buttons drawn as one connected control, such as view mode or text
alignment. Append `Gtk.Button` or `Gtk.ToggleButton` children. `tint` adds a CSS
class for a tinted variant. `compact=True` uses the compact size, and with touch
input adds larger targets; if `context` is omitted it is read from the
environment.

### `SaveStatus`

```python
class SaveStatus(Gtk.Label):
    def __init__(self) -> None
    def pending(self) -> None
    def saved(self, label: str = "Last saved just now") -> None
    def failed(self, message: str) -> None
```

Quiet status text for autosaving editors. Starts as “Saved”. `pending` shows
“Saving…”; `saved` shows your label; `failed` shows “Couldn’t save ·
Retrying…” in the error treatment, with `message` as its tooltip. The shown
strings are English today; see [writing style](../design/writing-style.md).

### `InlineRenameField`

```python
class InlineRenameField(Gtk.Entry):
    def __init__(self, text: str, *, commit: Callable[[str], bool],
                 cancel: Callable[[], None]) -> None
    def begin(self) -> None
    def finish(self) -> None
```

An inline rename editor. `begin` focuses it and selects all text. Enter or
moving focus away calls `commit(text)`; return `True` to accept, `False` to keep
editing (for example when the name is taken). **Escape** calls `cancel`. Replace
the field with the plain label in both callbacks.

---

## Empty states

### `EmptyState`

```python
class EmptyState(Adw.Bin):
    def __init__(self, title: str, description: str, icon_name: str, *,
                 primary: tuple[str, Callable] | None = None,
                 secondary: tuple[str, Callable] | None = None,
                 compact: bool = False) -> None
    def set_text(self, title: str, description: str) -> None
```

The centered empty state for a whole pane: an 84px disc with a 38px symbolic
glyph, a 19px title, a 13px description bound to 320px, and up to two buttons.
`primary` and `secondary` are `(label, callback)` pairs that must call your
existing commands. A `secondary` without a `primary` raises `ValueError`. Long
button labels wrap rather than hiding. `compact=True` uses a 48px disc and 24px
glyph bound to 260px, for inspectors and pickers. The glyph is hidden from
assistive technology.

| Attribute | Type |
| --- | --- |
| `heading`, `description` | `Gtk.Label` |
| `icon` | `Gtk.Image` |
| `primary_button`, `secondary_button` | `Gtk.Button` or `None` |

### `ListEmptyState`

```python
class ListEmptyState(Gtk.Label):
    def __init__(self, text: str) -> None
```

A quiet centered hint for an empty list or collection, with 24px padding and no
glyph or button. Use `EmptyState` when the whole pane is empty.

---

## People and facts

### `Avatar`

```python
class Avatar(Gtk.Box):
    def __init__(self, name: str, *, large: bool = False, hero: bool = False,
                 compact: bool = False, medium: bool = False,
                 tone: str | None = None, group: bool = False,
                 paintable: Gdk.Paintable | None = None) -> None
```

A soft-square avatar: the photo when you pass a `paintable`, otherwise up to two
initials, otherwise a person, business or group glyph (`group=True` forces the
group glyph). The tone is derived from the name with `avatar_tone` unless you
pass one of `AVATAR_TONES`, so a person has the same tone in every Luma app.
Sizes: default, `compact`, `medium`, `large` and `hero`. Its accessible label
is “<name> avatar”.

### `avatar_tone(name)` and `AVATAR_TONES`

```python
AVATAR_TONES = ("blue", "green", "violet", "amber")
avatar_tone(name: str) -> str
```

The stable identity tone for a name, ignoring case and whitespace.

### `FieldRow`

```python
class FieldRow(Gtk.Box):
    def __init__(self, label: str, value: str, *, numeric: bool = False) -> None
```

A labeled value with a fixed label column, so values line up down a card, as in
Contacts or an inspector. The value is selectable and ellipsized; `numeric=True`
uses tabular figures. Attribute `value` is the value `Gtk.Label`.

---

## Design tools

### `NumericField`

```python
class NumericField(Gtk.Box):
    __gsignals__ = {"value-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,))}
    def __init__(self, label: str, *, unit: str = "", step: float = 1.0,
                 minimum: float | None = None, maximum: float | None = None,
                 digits: int = 0) -> None
    def get_value(self) -> float
    def set_value(self, value: float) -> None
    def set_mixed(self) -> None
```

A number with a unit that behaves like a design tool’s: drag the label to scrub,
**Up** and **Down** to step (in both, **Shift** is coarse and **Ctrl** fine),
type arithmetic such as `120*2`, and show “Mixed” when a selection disagrees. Values are clamped
to `minimum` and `maximum`. `value-changed` is emitted for changes the person
makes, not for `set_value`. Attributes `label` and `entry` are the parts.

### `evaluate_number(text)`

```python
evaluate_number(text: str) -> float | None
```

Reads a number or simple arithmetic (`+ - * / ** %`, unary signs), ignoring a
trailing `%`, `px` or `pt`. Returns `None` for anything else. It walks the
expression tree and never evaluates code.

### `AlignCluster`

```python
class AlignCluster(Gtk.Box):
    __gsignals__ = {"align": (GObject.SignalFlags.RUN_FIRST, None, (str,))}
    ACTIONS: tuple[tuple[str, str, str], ...]
    buttons: dict[str, Gtk.Button]
```

Six alignments and two distributions as one control. Emits `align` with
`"left"`, `"center-x"`, `"right"`, `"top"`, `"center-y"`, `"bottom"`,
`"distribute-x"` or `"distribute-y"`.

### `ColorSwatch`

```python
class ColorSwatch(Gtk.ToggleButton):
    def __init__(self, colour: str, label: str, *, context: AppContext) -> None
```

A named, selectable color from a document, such as a palette entry. `colour` is
any string `Gdk.RGBA.parse` accepts; an invalid color or empty `label` raises
`ValueError`. The label is the accessible name and tooltip. Pointer targets are
22px and touch targets 44px. The selected ring uses the current foreground
color. This is document data, so a literal color here is correct.

---

## Media and transactions

### `AudioWaveform`

```python
class AudioWaveform(Gtk.Widget):
    __gsignals__ = {
        "seek": (GObject.SignalFlags.RUN_LAST, None, (float,)),
        "selection-changed": (GObject.SignalFlags.RUN_LAST, None, (float, float)),
    }
    def __init__(self, kind: str = "well") -> None
    def set_audio(self, peaks: Iterable[float], duration: float) -> None
    def set_position(self, seconds: float) -> None
    def set_marks(self, seconds: Iterable[float]) -> None
    def set_selection_mode(self, enabled: bool) -> None
    def set_live(self, live: bool) -> None
    def push_peak(self, value: float) -> None
```

A recording’s shape as a seekable range. You supply real peaks (0 to 1) and the
duration in seconds; the widget never invents data. `kind` is one of
`WAVEFORM_KINDS`: `"well"` (the recessed player well), `"deck"` (inside a
`MediaTransport`), `"clip"` (a voice message) or `"mini"` (a small, inert
glance for a row). A click seeks and a drag scrubs, emitting `seek(seconds)`;
**Shift**+drag, or a drag in selection mode, emits
`selection-changed(start, end)`. To assistive technology it is a slider:
**Left** and **Right** move 5 seconds, **Page Up** and **Page Down** 15, and
**Home** and **End** go to the ends. Feed playback position back with
`set_position`. For a live recording, call `set_live(True)` and then
`push_peak(value)` as audio arrives.

### `TransactionCard`

```python
class TransactionCard(Island):
    def __init__(self) -> None
    def set_icon(self, icon: str | Gio.Icon) -> None
    def set_phase(self, phase: str, action: str, text: str = "", fraction: float = 0.0,
                  *, removing: bool = False, cancellable: bool = False) -> None
    def set_fraction(self, value: float | None) -> None
```

An install or remove card for package and document workflows. `phase` is
`"ready"`, `"working"` or `"done"`. `action` is the primary button label.
`removing=True` makes the ready state destructive and shows the “Keep settings
and data” check. `set_fraction(None)` makes the progress ring indeterminate; a
number animates the ring to that fraction, respecting reduced motion. Changing phase
changes content and opacity only, never the widget hierarchy, so focus is stable.

| Attribute | Type |
| --- | --- |
| `primary`, `secondary` | `Gtk.Button` |
| `keep` | `Gtk.CheckButton` |
| `advanced` | `Gtk.ToggleButton` |
| `name`, `sub`, `phase_label`, `file_label` | `Gtk.Label` |

---

## LumaUI

LumaUI’s semantic parts. An app says what happened or what it needs; LumaUI
owns the look, spacing, motion and phone behaviour, all from tokens. They also
load where Luma’s typelibs are missing, on plain GTK 4 and libadwaita.
Principles: [LumaUI principles](lumaui-principles.md);
behaviour: [LumaUI behaviour](behaviour.md).

### `LUMAUI_API_LEVEL`

An integer, 2 today. It rises when a public name, keyword or toast kind is
added; nothing public is removed within a level.

### `install_lumaui(display=None)`

Loads LumaUI’s token, component and base sheets and adds its icon folder to
the display’s icon theme, once. On a Luma system it calls `install_appkit()`;
without the Luma appearance library it loads the same sheets directly, beside
the package first and `/usr/share/luma-appkit` last, and follows the desktop’s
dark and high-contrast state. Every part calls it for you.

### `reduced_motion()`

`True` when the desktop asks for reduced motion or has animations off. Parts
already honour it; use it only for motion you draw yourself. Durations:
`luma_appkit.lumaui.duration("toast_in")` returns the token in milliseconds,
shortened to a fade under reduced motion.

### `lumaui_icon(name)`

```python
lumaui_icon("share-2")  # "lumaui-share-2-symbolic"
```

The theme name of a Lucide glyph LumaUI ships. Share is `share-2`, Export is
`share`, Upload is `upload`. Only glyphs LumaUI ships resolve to an icon.

### `LayerHost`

```python
class LayerHost(Gtk.Overlay):
    def __init__(self, child: Gtk.Widget | None = None, *, name: str = "content") -> None
    @classmethod
    def for_widget(cls, widget) -> LayerHost        # nearest host, or the window's own
    @classmethod
    def window_host(cls, widget) -> LayerHost       # the host standing for the window
    @classmethod
    def install(cls, window) -> LayerHost           # wraps a window's content, once
    def present_modal(self, card, *, on_cancel=None, initial_focus=None, drawer=None,
                      scrim=True) -> ModalHandle
```

The one place floating parts appear. `AppWindow` has one under its title row;
any other window gets one at its root on first use. Wrap a region (such as the
main island) in a `ToastHost` to centre toasts on it. `present_modal()` shows a
card over a scrim (`scrim=False` for an undimmed backdrop), centred, or as a
drawer from the bottom edge at phone width; Esc cancels, Tab stays inside, the
window behind can't take focus, focus returns afterwards. The returned `ModalHandle`
has `close()`, `cancel()` and `nudge()`. Kit parts use it; apps rarely need to.

### `Toast`, `ToastHost`, `TOAST_KINDS`

```python
Toast.show(where: Gtk.Widget, message: str, *, kind="done", undo=None, busy=False,
           action=None) -> Toast
toast.dismiss()
ToastHost(child, *, edge="bottom", offset=0).track_bar(bar)
```

What just happened, as a plain past-tense line with the icon for it, centred
on the region `where` is in, just above a tracked bar. Undo shows for 4 s,
otherwise 2.4 s; `busy=True` shows a spinner and stays until `dismiss()`.
`action=(label, callback)` adds one button for the one way to fix what went
wrong, such as “Sign in again”. A `ToastHost` puts toasts at its `"bottom"`
(the default) or `"top"`. A new toast replaces the one showing. Announced politely to screen readers (role
status). `kind` is one of `TOAST_KINDS`: `done`, `added`, `saved`, `sent`,
`place`, `favourite`, `unfavourite`, `copied`, `deleted`, `archived`,
`downloaded`, `notified`, `signed-out`, `paused`, `undone`, `opening`,
`warning`, `error`. Use it instead of `Adw.Toast`; never for a question.

### `DestructiveDialog`

```python
DestructiveDialog.ask(where, *, title, body, action="Delete", icon="trash-2",
                      option=None, on_confirm=None, on_cancel=None) -> DestructiveDialog
```

For what can't come back. The question as the title, naming the thing; one
line of consequence; Cancel and the red action as equal buttons; an optional
checkbox (`option`), whose state `on_confirm(checked)` receives. Cancel has
focus, Esc cancels, Tab stays inside, a click behind nudges it; at phone width
it is a drawer with the red action on top. Refuses "Are you sure?" as a title
and "OK" as an action. Anything that can be undone uses `Toast` with `undo`.

### `StackedButton`, `StackedButtons`

```python
StackedButton(icon: str, label: str, *, danger=False, on_click=None, sensitive=True)
StackedButtons(buttons: Sequence[StackedButton], *, small=False, size=None)
```

Icon over label on the raised chip, in equal pairs or trios (anything else
raises). `small=True` for tight panes. `size="tile"` draws up to four taller
tiles. A danger button is red and must still
confirm through `DestructiveDialog`.

### `CountBadge`, `count_text`

```python
CountBadge(count: int = 0, *, attention: bool = False)   # properties: count, attention
count_text(1284) == "1,284"; count_text(120, True) == "99+"; count_text(0) == ""
```

A total, exact with a separator, or (attention) unread and needs-you, in the
accent and capped at 99+. Hidden at 0. Screen readers hear the exact number.

### `CategoryPill`, `CATEGORIES`

```python
CategoryPill("media")   # CATEGORIES: create, work, media, play, tools
```

Depot's category in its own colour, light and dark, text at 6.2–8.2:1. An
unknown category keeps its name and the neutral Work colour.

### `TypeLabel`, `apply_type`, `type_class`, `TYPE_ROLES`

```python
apply_type(label, "caption")
TypeLabel("9:41", role="display", unit=":07 AM")
```

The core roles: `display` (72/600, tabular), `hero` (30/700), `title-1`
(24/650), `title-2` (15/650), `lead` (14/500), `body` (13/400), `caption`
(11.5/500, muted), `label` (11.5/600, muted) and `numeric` (30/500, tabular).
`TYPE_ROLES` lists every role, including ones for particular surfaces such as
`reading` and `mono`. No one-off sizes or weights. `TypeLabel` adds the small
unit (seconds, AM/PM, mph) at 40% size on the baseline.

### Structure

Every structure part becomes a drawer or collapses at phone width by itself,
based on the window’s width, never on a device guess.

```python
pane = DetailsPane("Details")                       # beside the island, no spacing
pane.add_hero("Launch crew", "4 people", lead=avatar)
pane.add_section("Conversation"); pane.add_facts([("Created", "Sep 2")])
pane.add_list([DetailsRow("Priya Raman", "@priya", lead=av, actions=[("phone", "Call", call)]),
               DetailsItem("Press kit review", "Tuesday", icon="mail"),
               FactRow("phone", "Mobile", "+1 816 555 0142"), AddRow("Add people", on_activate=add)])
pane.add_photos(paths); pane.add(StackedButtons([...]))
pane.show(open=True, subject=thread)                # Info; show(subject=None) closes it
DetailsPane("Today", main=True)                     # a main view: stays, no close, no Esc

foot = SidebarFoot(search="Search people", on_search=find,
                   filters=[("all", "All", "users", 214), ("fav", "Favourites", "star", 12)],
                   on_filter=show, add=("Add a contact", "user-plus", add))
sidebar.append_header(foot.heading); sidebar.append_footer(foot)
toggle = SidebarToggle(sidebar)                     # F9; a drawer from the left on a phone

header = TableHeader([Column("title", "Title", expand=True), Column("time", "Time", width=56, end=True)],
                     sort=("title", "ascending"), on_sort=resort)
header.align(row_box)                               # rows share the header's columns
TableHeader.for_column_view(column_view)            # a ColumnView's own header, same bar
Selection.apply(list_box); Selection.trail(step); Selection.mark(tile)

NavigationTrailBar(trail, meta=["12 albums"])       # over NavigationTrail/Place

corner = CornerPill(modes=ModeSwitch([("view", "View", "eye"), ("markup", "Mark up", "pen-line")]),
                    open_in=open_in, share=share, actions=[("pencil", "Edit", edit)], primary="Edit",
                    labelled=True, info=pane, more=registry)
corner.edit(on_cancel=discard, on_done=save)        # Cancel · Done

MenuDrawer.present(widget, registry)                # what every Menu does at phone width
```

- `DetailsPane.show(open=, subject=, main=)` returns whether it shows; Info’s
  wish is kept across subjects and forgotten when the subject goes; `shown` is
  a notify property; `info_button()` makes the pressed-while-open Info toggle.
  `DetailsPane(title, main=True, closable=True)` is a main pane with a close
  button; `back=(label, fn)` or `set_back(label, fn)` puts “Back to <label>”
  before the title; `add_section(label, count=4)` adds a count badge.
- `SidebarFoot`: search, a filter picker (menu opens upward, counts optional),
  a New/Add square, or both; `set_filter()`, `set_filter_counts()`, `heading`.
- `SidebarToggle(sidebar, closed=fn)` hands the closed look to the app.
- `CornerPill` places its controls in one order: modes · search · zoom ·
  people · Open in · Share · states · Info · actions · ···. `order=` names a
  different order for the groups it lists; the rest keep theirs after them.
- `Command(count=)` puts a count badge on a menu row.

### Other LumaUI parts

These families follow the same rules. Each module’s docstrings hold the full
API.

| Family | Names | Module |
| --- | --- | --- |
| Action center and selection | `ActionCenter`, `ActionEditor`, `BarAction`, `BarChip`, `BarContext`, `BarPrompt`, `SEPARATOR`, `SPACER`, `SelectionBubble` | `luma_appkit/action_*.py` |
| Bar items, sharing and export | `BarEntry`, `BarSearch`, `BarThumbnail`, `BarReadout`, `BarMenu`, `SplitAction`, `ZoomControl`, `DocumentHeader`, `ShareSheet`, `ExportSheet` | `luma_appkit/bar_*.py` |
| Content | `Card`, `ContentLitCard`, `AccountCard`, `PersonAvatar`, `ContentLitHeader`, `FileCard`, `OpenButton`, `OpenInMenu`, `ContactCard`, `ContactActions`, `EventCard`, `SongCard`, `PlaceCard`, `PlaceSearch`, `TextField`, `HeroTitleField`, `ParagraphField`, `MessageBubble`, `StatusPill` | `luma_appkit/content_*.py` |
| Rows and sidebars | `SidebarRow`, `SidebarSection`, `RowLead`, `RowAction`, `ProgressLine`, `AvatarStack`, `FavouritesStrip`, `Mark`, `MarkButton`, `MarkPicker`, `MarkValue`, `RichMenuItem`, `MenuSection`, `AppIcon`, `NoteCard` | `luma_appkit/rows_*.py` |
| Media and imaging | `MediaGrid`, `MediaTile`, `ImageViewport`, `MediaTransport`, `Timeline`, `AdjustmentPanel`, `CoverArt`, `VoiceClip`, `ValueSlider`, `CreativeWorkspace`, `FloatingPanel` | `luma_appkit/media_*.py`, `luma_appkit/creative_workspace.py` |

```python
sidebar = NavigationSidebar(variant="people")        # people, destinations, resources, files, tree
sidebar.append_row(SidebarRow("Launch crew", lead=RowLead.group(["Priya Raman", "Sam Ortiz"]),
                              meta="9:41", subtitle="Sam: see you there", attention=True, trail=3))
sidebar.append_row(SidebarRow("Priya Raman", lead=RowLead.face("Priya Raman", online=True), pinned=True))
sidebar.append_section("Lists", action=("plus", "New list", new_list))

ProgressLine(0.42); ProgressLine(0.5, size="hero", label="Downloading")
AvatarStack(people, size="row", max=3, live={"Priya Raman"})
FavouritesStrip([("Priya", "Priya Raman", None)], on_open=call)
MarkButton(MarkValue(hue="orange"), on_change=save)
```
