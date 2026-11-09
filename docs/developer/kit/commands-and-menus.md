---
title: Commands and menus
summary: One command model drives the identity menu, context menus, buttons, shortcuts and semantics. How to define commands, where each menu lives, and the menu rules.
order: 5
section: Kit
---

# Commands and menus

A command is something your app can do. In a Luma app every command is defined
once, with one stable ID, and every way of reaching it (the identity menu, a
context menu, a toolbar button, a keyboard shortcut, an accessibility action, a
semantic action) points at that one definition. They can never disagree about
what the command is called, whether it is available, or what it does.

## Define commands once

```python
from luma_appkit import Command, CommandGroup, CommandRegistry

commands = CommandRegistry((
    CommandGroup(None, (
        Command("document.new", "New document", self.new_document,
                icon="document-new-symbolic", shortcut=("Ctrl", "N")),
        Command("document.open", "Open…", self.open_document,
                icon="document-open-symbolic", shortcut=("Ctrl", "O")),
    )),
    CommandGroup("Edit", (
        Command("edit.undo", "Undo", self.undo, icon="edit-undo-symbolic",
                shortcut=("Ctrl", "Z"), enabled=lambda: self.history.can_undo),
        Command("view.grid", "Show grid", self.toggle_grid,
                checked=lambda: self.grid_visible),
        Command("view.sort", "Sort by", lambda: None, children=(
            Command("view.sort-name", "Name", self.sort_by_name,
                    description="Alphabetical", checked=lambda: self.sort == "name"),
            Command("view.sort-date", "Date modified", self.sort_by_date,
                    description="Newest first", checked=lambda: self.sort == "date"),
        )),
        Command("edit.delete", "Delete", self.delete_selection,
                icon="user-trash-symbolic", destructive=True,
                enabled=lambda: bool(self.selection)),
    )),
    CommandGroup(None, (
        Command("application.about", "About Canvas", self.show_about),
        Command("application.quit", "Quit Canvas", self.quit, shortcut=("Ctrl", "Q")),
    )),
))
```

Rules:

- **IDs are dotted and permanent.** `area.verb` or `area.noun-verb`. Duplicate
  IDs raise `ValueError`. See [semantics and actions](semantics-and-actions.md#action-ids).
- **Predicates are live.** `visible`, `enabled` and `checked` are functions,
  evaluated when a menu opens and when the command is invoked. Never cache the
  answer in the command.
- **One behavior per ID.** If two buttons do different things, they are two
  commands.
- **`registry.invoke(id)`** is the only way to run a command from your own
  code. It refuses hidden or disabled commands and returns whether it ran.

## Where commands appear

| Surface | How | Shows |
| --- | --- | --- |
| **Identity menu** (under your app’s name, top-left) | `AppWindow(commands=...)` builds it | App-wide commands, grouped in sections |
| **Keyboard shortcuts** | Each command’s `shortcut` becomes an application accelerator (commands in a submenu excepted) | Everywhere in the app |
| **Buttons** | `button.set_action_name("app.<id-with-dashes>")` | One control |
| **Context menus** | `attach_context_menu(widget, registry)` with a registry for that object | Commands for the thing under the pointer |
| **Menu buttons and popovers** | `Menu(registry)` or `command_popover(registry)` | A command subset, with checks, descriptions and submenus |
| **Text fields** | `attach_context_menu` on a `Gtk.Entry` or `Gtk.TextView` adds an extra section to GTK’s own menu | Commands next to Cut, Copy, Paste |
| **Semantic actions** | Publish the same IDs to the broker | Approved agents and automation |

Action names replace dots with dashes because GIO action names cannot contain
dots: `document.new` becomes `app.document-new` in the identity menu and
`menu.document-new` inside a `Menu`. `AppWindow` registers an `app.` action for
each command listed directly in a group; commands inside a submenu are reached
through their menu.

### The identity menu

`AppWindow` turns the registry into the menu LumaUI shows under your app’s
identity, and installs it as the application menubar. Keep it to app-wide
commands: new
window or document, open, settings, keyboard shortcuts, about, quit. Put
object-specific commands in context menus and toolbars.

The identity menu is a LumaUI `Menu`, so check marks, descriptions, submenus
and destructive rows work there as they do everywhere else. It is built once,
when the window is created, from the commands visible at that moment: keep
app-wide commands visible and use `enabled` for availability.

In C, build a `GMenu` whose items target your `app.` actions and pass it to
`luma_application_window_set_menu_model()`; see
[your first app in C](../getting-started/first-app-c.md).

### Context menus

Every surface owns its menu. Attach a registry to each widget that has one:

```python
from luma_appkit import attach_context_menu

row_commands = CommandRegistry((
    CommandGroup(None, (
        Command("note.rename", "Rename", lambda: self.rename(note)),
        Command("note.duplicate", "Duplicate", lambda: self.duplicate(note)),
    )),
    CommandGroup(None, (
        Command("note.trash", "Move to Trash", lambda: self.trash(note),
                icon="user-trash-symbolic", destructive=True),
    )),
))
attach_context_menu(row, row_commands)
```

That one call opens the menu by secondary click, by touch long press, and by
the **Menu** key or **Shift+F10** with focus on the first item. Right-clicking
does not fall through to a generic menu.

### Menu buttons

Build a fresh menu each time the button is pressed:

```python
from gi.repository import Gtk
from luma_appkit import command_popover

more = Gtk.Button(icon_name="view-more-symbolic", tooltip_text="More")
more.update_property([Gtk.AccessibleProperty.LABEL], ["More"])
more.connect("clicked", lambda button: command_popover(view_commands).present_for(button))
```

The menu is a `GtkPopoverMenu` whose rows LumaUI draws, so it looks exactly
like every other menu in a Luma app, and its enabled and checked states are
current every time it opens.

A menu from `command_popover()` is built for one press: it lets go of its
parent after a command runs. To give a `Gtk.MenuButton` a menu that stays, pass
`Menu(registry, keep_parent=True)` to `set_popover()`, as the identity menu
does.

### Quick actions

A group with `quick_actions=True` and two to four simple, non-destructive
commands is drawn as a row of large icon buttons at the top of a `Menu`:

```python
CommandGroup(None, (
    Command("notes.new", "New page", self.create_note, icon="document-new-symbolic"),
    Command("folders.new", "New folder", self.create_folder, icon="folder-new-symbolic"),
    Command("notes.search", "Search", self.toggle_search, icon="system-search-symbolic"),
), quick_actions=True)
```

## Shortcuts

Write shortcuts as tuples with Linux names: `("Ctrl", "Shift", "S")`. LumaUI
converts them with `accelerator()` and shows them as keycaps in menus.
Mac-style names such as `⌘` or `cmd` are mapped to `Ctrl`, but write Linux names
in your source.

Recommended conventions, the usual ones on the Linux desktop, so people’s
habits carry over:

| Shortcut | Command |
| --- | --- |
| Ctrl+N | New item, document or window |
| Ctrl+O | Open |
| Ctrl+S, Shift+Ctrl+S | Save, Save As |
| Ctrl+W | Close window or document |
| Ctrl+Q | Quit |
| Ctrl+Z, Shift+Ctrl+Z | Undo, Redo |
| Ctrl+F | Find or search |
| Ctrl+K | Search commands (where the app has a command palette) |
| Ctrl+, | Settings |
| Ctrl+? | Keyboard shortcuts |
| Alt+Left, Alt+Right | Back, Forward (`bind_navigation_input`) |
| Menu, Shift+F10 | Open the context menu for the focused item |
| Delete | Move the selection to Trash |

Never take a shortcut the shell uses (anything with **Super**).

## Menu writing and ordering

- Labels are short verbs or nouns in sentence case: “Move to Trash”, “Show grid”.
  See [writing style](../design/writing-style.md).
- End a label with an ellipsis (…) only when the command needs more input before
  it acts: “Open…”, “Rename…”.
- Order sections from most to least common. Destructive commands go last, in
  their own section.
- A disabled item keeps its place. Do not hide commands that are temporarily
  unavailable; hide only what does not apply to this object at all.
- Use `description` when a choice needs a second line to be understood, such as
  sort orders.
- Menus grow for long translations; never truncate a label to keep a width.

The complete visual contract for menus (sizes, rows, headings, separators,
destructive rows, keycaps) is owned by LumaUI, so you never style a menu.
