---
title: Your first app in Python
summary: Build Notebook, a complete adaptive Luma app with a sidebar, a toolbar, an empty state, commands and a keyboard shortcut, using LumaUI from Python.
order: 2
section: Getting started
---

# Your first app in Python

LumaUI’s Python package, `luma_appkit`, is the fastest way to build a Luma
app. In this tutorial you build **Notebook**: a window with a sidebar
island and a content island, a toolbar button, an empty state, a status bar,
commands that drive the identity menu and a keyboard shortcut, and a layout that
collapses into a navigation stack on a phone.

The code on this page targets `luma-developer-platform` 0.1.0 (preview).

## Before you start

To run the app directly you need a Luma system, where the platform is
preinstalled. Elsewhere, build it as a Flatpak on the Luma runtime with the
manifest in [project layout](project-layout.md). See
[install the SDK](install-the-sdk.md).

## 1. Create the project

Make this layout. [Project layout](project-layout.md) explains every file.

```text
notebook/
├── luma-app.toml
├── src/
│   └── main.py
├── data/
│   ├── com.example.Notebook.desktop
│   ├── com.example.Notebook.metainfo.xml
│   └── com.example.Notebook.svg
├── flatpak/
│   └── com.example.Notebook.json
└── verification/
    └── luma-native-evidence.json
```

You can also run `luma new "Notebook" --id com.example.Notebook --destination notebook`
to generate a starting tree (it also writes a `README.md`, a `run-app`
launcher and a YAML copy of the Flatpak manifest). The generated `src/main.py`
uses the C library through introspection rather than LumaUI’s Python package,
so replace it with the code below. Replace the generated Flatpak manifest too;
[project layout](project-layout.md#flatpak-manifest) explains why.

Use your own reverse-DNS ID instead of `com.example.Notebook`, one you can prove
you control. See [app IDs and namespaces](../publishing/app-ids-and-namespaces.md).

## 2. Write the app

Save this as `src/main.py`:

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Notebook: a minimal Luma application written with LumaUI."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from luma_appkit import (
    AppWindow,
    Command,
    CommandGroup,
    CommandRegistry,
    EmptyState,
    IconButton,
    Island,
    IslandSplitView,
    NavigationRow,
    NavigationSidebar,
    StatusBar,
    Toolbar,
    install_appkit,
)

APP_ID = "com.example.Notebook"


class NotebookWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self.notes: list[str] = []
        commands = CommandRegistry((
            CommandGroup(None, (
                Command("note.new", "New note", self.new_note,
                        icon="list-add-symbolic", shortcut=("Ctrl", "N")),
                Command("note.clear", "Delete all notes", self.clear_notes,
                        icon="user-trash-symbolic", destructive=True,
                        enabled=lambda: bool(self.notes)),
            )),
            CommandGroup(None, (
                Command("application.quit", "Quit Notebook", application.quit,
                        shortcut=("Ctrl", "Q")),
            )),
        ))
        super().__init__(
            application=application,
            app_id=APP_ID,
            title="Notebook",
            icon_name=APP_ID,
            commands=commands,
            minimum_width=360,
            minimum_height=294,
        )

        # Sidebar island: destinations.
        self.sidebar = NavigationSidebar()
        self.sidebar.append_section("Library")
        self.all_notes = NavigationRow("All notes", icon_name="folder-symbolic")
        self.sidebar.append_row(self.all_notes)

        # Content island: a toolbar, the page, and a status bar.
        content = Island()
        toolbar = Toolbar()
        title = Gtk.Label(label="All notes", xalign=0, hexpand=True)
        toolbar.append(title)
        add = IconButton("list-add-symbolic", "New note", context=self.context)
        add.set_action_name("app.note-new")  # "note.new" becomes app.note-new
        toolbar.append(add)
        content.append(toolbar)

        self.pages = Gtk.Stack(vexpand=True)
        self.pages.add_named(
            EmptyState("No notes yet", "Notes you create appear here.",
                       "document-new-symbolic",
                       primary=("New note", lambda: commands.invoke("note.new"))),
            "empty",
        )
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.pages.add_named(Gtk.ScrolledWindow(child=self.list), "list")
        content.append(self.pages)

        self.status = StatusBar()
        self.count_label = self.status.append_text("0 notes")
        content.append(self.status)

        # One split view serves every width; below 600sp it collapses into
        # a navigation stack instead of dropping the sidebar.
        self.split = IslandSplitView(
            sidebar=Adw.NavigationPage(child=self.sidebar, title="Library"),
            content=Adw.NavigationPage(child=content, title="All notes"),
        )
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        compact.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(compact)
        self.set_body(self.split)

    def new_note(self) -> None:
        self.notes.append(f"Note {len(self.notes) + 1}")
        self.list.append(Gtk.Label(label=self.notes[-1], xalign=0))
        self.refresh()

    def clear_notes(self) -> None:
        self.notes.clear()
        self.list.remove_all()
        self.refresh()

    def refresh(self) -> None:
        count = len(self.notes)
        self.pages.set_visible_child_name("list" if count else "empty")
        self.count_label.set_label(f"{count} note" if count == 1 else f"{count} notes")
        self.all_notes.set_trailing(str(count) if count else "")


class NotebookApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_activate(self) -> None:
        install_appkit()  # once, after GTK has a display
        window = self.get_active_window() or NotebookWindow(self)
        window.present()


def main() -> int:
    return NotebookApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
```

## 3. Run it

```sh
chmod +x src/main.py
python3 src/main.py
```

Try it:

- Press **Ctrl+N** or the plus button. The empty state gives way to the list,
  and the sidebar row and status bar count the notes.
- Open the menu under the app’s name in the top-left corner. It lists **New note**,
  **Delete all notes** and **Quit Notebook**, built from your commands.
- Make the window narrower than 600sp. The two islands become a navigation
  stack; nothing is removed.

## What just happened

**`install_appkit()`** loads LumaUI’s tokens, base sheet and structural sheet
for the current display, and keeps them in step with light, dark, Frost, Glass
and high contrast. Call it once after GTK has a display, which is why it lives
in `do_activate`. `AppWindow` and `add_style_sheet()` also call it, so an
extra call does nothing.

**`AppWindow`** is LumaUI’s window. LumaUI draws its frame, title row, identity
(your app’s name and icon from its desktop entry) and window controls, so your
app draws none of them. The window also comes back at the size it had last
time and hides its title row when the app runs fullscreen on a phone. See
[windows and islands](../kit/windows-and-islands.md).

**`CommandRegistry`** is the single list of things your app can do. The window
turns it into the identity menu, registers each command as an `app.` action
(dots become dashes: `note.new` becomes `app.note-new`) and installs the
shortcuts. Buttons point at the same action, so a menu item, a button and a
shortcut can never disagree. `enabled` is checked every time the command is
invoked. See [commands and menus](../kit/commands-and-menus.md).

**`Island`**, **`NavigationSidebar`**, **`Toolbar`**, **`EmptyState`** and
**`StatusBar`** are LumaUI components. Your app never sets their radius,
shadow, padding or colors, so a change to the Luma design reaches them without
a change to your app. See the [Python reference](../kit/appkit-reference.md).

**`IslandSplitView`** is a split view that keeps island shadows intact while
its pages sit side by side. The breakpoint collapses it on narrow windows. See
[adaptive layouts](../kit/adaptive-layouts.md).

## 4. Describe and check the project

Add `luma-app.toml`, the desktop entry, metainfo, icon and Flatpak manifest from
[project layout](project-layout.md), then run the static contract checks:

```sh
luma lint
luma inspect
luma test
```

`luma lint` prints `Luma application contract passed` when the app ID, desktop
entry, metainfo, icon, Flatpak manifest, permissions and lifecycle agree. A new
project reports the status `Development` in `luma inspect`; that is expected.
See [the `luma` command](../kit/sdk-command-line.md).

## Next steps

- Add a second destination and use `NavigationTrail` for back and forward:
  [adaptive layouts](../kit/adaptive-layouts.md).
- Give rows a right-click menu with `attach_context_menu`:
  [commands and menus](../kit/commands-and-menus.md).
- Save notes to a file the person chooses through the file chooser portal:
  [permissions and privacy](../permissions-and-privacy.md).
- Check the [design guidelines](../design/principles.md) before you polish.
