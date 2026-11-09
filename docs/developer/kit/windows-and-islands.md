---
title: Windows and islands
summary: The anatomy of a Luma window, how the title row, identity, body and islands fit together, common island arrangements, and what your stylesheet must never declare.
order: 4
section: Kit
---

# Windows and islands

Every Luma window has the same anatomy. Learning it once tells you where
everything in your app belongs.

```text
┌──────────────────────────────────────────────────────────────────┐
│ ◉ Notebook ▾                                            ⌄  ▢  ✕  │ ← title row (46px), drawn by LumaUI
│                ┌───────────────────────────────────────────────┐ │
│  LIBRARY       │ All notes                                   + │ │ ← toolbar inside the island
│  All notes  3  │                                               │ │
│  Starred       │                    content                    │ │
│                │                                               │ │
│                ├───────────────────────────────────────────────┤ │
│                │ 3 notes                                       │ │ ← status bar inside the island
│                └───────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
  ↑ sidebar on the window frame       ↑ island: 12px radius, 8px gutter
```

| Part | Owner | Notes |
| --- | --- | --- |
| **Frame** | LumaUI | 20px corner radius and a layered shadow; square only in full screen |
| **Title row** | LumaUI | 46px, holding the identity, any title-row slots and the window controls |
| **Identity** | LumaUI | Your app’s icon and name from its desktop entry, opening your identity menu. It cannot be overridden: the corner says what the dock says. |
| **Window controls** | LumaUI | Minimize, maximize and close as one connected control on the right. Hidden on a phone. |
| **Body** | LumaUI (`luma-window-body`) | The window ground, showing as an 8px gutter around and between islands |
| **Sidebar** | LumaUI (`NavigationSidebar`) | Stands on the window frame beside the content island, not in an island of its own |
| **Islands** | LumaUI (`luma-island`) | Rounded work surfaces that clip their content |
| **Content** | Your app | What goes inside islands |

Everything except your content is LumaUI’s, so a change to the Luma design
reaches your window without a change to your app.

## Building the window

Python:

```python
class MyWindow(AppWindow):
    def __init__(self, application):
        super().__init__(application=application, app_id=APP_ID, title="My app",
                         icon_name=APP_ID, commands=self.build_commands(),
                         minimum_width=360, minimum_height=294)
        sidebar = NavigationSidebar()
        content = Island()
        self.body.append(sidebar)   # body is a horizontal box with 8px spacing
        self.body.append(content)
        content.set_hexpand(True)
```

C:

```c
GtkWidget *window = luma_application_window_new (application, context);
GtkWidget *sidebar = luma_navigation_sidebar_new (NULL, NULL);
GtkWidget *island = luma_island_new (GTK_ORIENTATION_VERTICAL);
luma_application_window_set_frame (LUMA_APPLICATION_WINDOW (window), sidebar, island);
```

Use `set_body()` (`luma_application_window_set_body()` in C) when a single
widget, such as a split view or a stack of pages, holds everything. In Python,
append to `AppWindow.body` when you lay out islands directly; in C,
`luma_application_window_set_frame()` places a frame sidebar (or `NULL`) beside
one content island.

## The title row is not a toolbar

The title row holds the identity and the window controls. Put your app’s
controls in island toolbars, next to the content they act on. A control that
affects the whole window, such as a sidebar toggle, may sit in the title row:
use the named slots `set_leading()`, `set_title_content()` and
`set_trailing()`, which exist on `AppWindow` in Python and as
`luma_application_window_set_leading()` and friends in C.

The **window title** says what the window holds, such as the open document. The
shell, window switcher and screen readers read it; the title row itself does not
repeat it. Set it with `set_identity_subtitle("Chapter 3")` in Python or
`luma_application_window_set_identity(window, "Write", "Chapter 3", NULL)` in C.

## Islands

An island is a rounded surface for one kind of work: a document, a list, an
inspector, a transport bar. LumaUI draws its 12px radius, a faint outer ring,
and an inner edge and shade over its content, and the island clips its children
to its corners. Use `Island` in Python and `luma_island_new()` in C.

- **One job per island.** A canvas and an inspector are two islands. The
  navigation sidebar is not an island: it stands on the window frame beside
  them.
- **Never nest islands.** Inside an island, separate regions with spacing,
  section labels, or a toolbar or status bar.
- **Toolbars and status bars live inside the island** they control.
- **Islands are siblings in the body** with the body’s 8px gutter between them.
  Do not add margins between islands yourself.
- **Content that must run edge to edge** (a photo, a map, video) still sits in
  an island and is clipped to its corners.

### Common arrangements

| Arrangement | Islands | Examples |
| --- | --- | --- |
| Single | One content island | A simple utility |
| Sidebar and content | `NavigationSidebar` on the frame beside an `Island` | Notes, Tide |
| Three panes | A sidebar on the frame, then canvas and inspector islands | Canvas, Write |
| Stacked | Content row above a full-width transport island | Tide’s player bar, Reel’s timeline |
| Command island above | A slim island for a command bar above the work area | Write |

### Split views

In Python, use `IslandSplitView` (sidebar and content pages) or
`IslandOverlaySplitView` (sidebar overlays content). They keep island shadows
visible while side by side and restore clipping when collapsed; a plain
`Adw.NavigationSplitView` clips the shadows of islands at the page edge. In C,
`LumaAdaptiveScaffold` gives you a sidebar and content that collapse to one
page at phone width. See [adaptive layouts](adaptive-layouts.md).

### Quiet islands

For chooser and preference windows, a quiet island (`luma-island
luma-island-quiet`) gives boxed rows one outer surface with a lighter shadow.

## Dialogs

Ask as little as possible. Anything that can be undone happens at once, with
Undo in a `Toast`. For something that can’t come back, use LumaUI’s destructive
dialog: `DestructiveDialog.ask()` in Python or `luma_destructive_dialog_ask()`
in C. It names the thing in its question and labels the action with a verb
(“Delete”), never “OK”. Cancel has focus, so Enter never deletes by accident.
At phone width it shows in the bar’s frame at the bottom of the screen, with
Cancel and the action side by side.

If you present an `Adw.AlertDialog` on your window, `AppWindow` and
`LumaApplicationWindow` give it LumaUI’s quiet control treatment.

### Asking to save before closing

Never build your own “save before closing?” dialog. There is one save changes
sheet, built once in the platform (`LumaSaveRequest` in libluma-ui, `LumaUI`
through introspection), and every application asks with it: give it the
facts, and it supplies the words, layout and keys.

From Python, call `luma_appkit.confirm_save(window, documents, on_choice)`
from your close and quit paths:

```python
from luma_appkit import SaveChoice, UnsavedDocument, confirm_save

def close_requested(self, _window):
    if not self.dirty:
        return False
    document = UnsavedDocument(self.name, unsaved_seconds=self.seconds_since_first_unsaved_edit(),
                               never_saved=self.path is None, extension=".layouts",
                               recovery_copy=True, window=self)
    confirm_save(self, [document], self.save_answered)
    return True

def save_answered(self, response):
    if response.choice is SaveChoice.SAVE:
        # response.destination is set for a document that was never saved.
        ok, problem = self.write(response.destination)
        response.done() if ok else response.fail(problem)
        if ok:
            self.destroy()
    elif response.choice is SaveChoice.DISCARD:
        self.destroy()
```

From C or C++, the same sheet:

```c
LumaSaveDocument *document = luma_save_document_new (title);
luma_save_document_set_never_saved (document, path == NULL);
luma_save_document_set_extension (document, ".xlsx");
luma_save_document_set_window (document, window);
LumaSaveRequest *request = luma_save_request_new (&document, 1, FALSE);
g_signal_connect (request, "response", G_CALLBACK (save_answered), self);
luma_save_request_present (request, window);
/* On SAVE, write the file (to `destination` when it is set), then call
 * luma_save_request_finish_save (request, NULL), or pass the problem. */
```

The sheet hangs from the title bar of the window that asked and dims only that
window's content; a window without LumaUI's sheet layer gets one the first
time it asks, under its header bar. Enter takes Save, except on Cancel, Don't
save or the Where menu, which answer for themselves; Escape cancels; Ctrl+D is
the only key that discards. While the sheet is open, your window's key handlers
and accelerators are parked, so your own Ctrl+D never fires behind it, and
focus returns where it was when the sheet closes. A Save keeps the sheet up
until you report it (`done()`/`fail(message)`, or `finish_save`); a failure
shows on the sheet. An untitled document is named and placed on the sheet
(your folders first, then Documents and Desktop, then Other location), and
`destination` is the file to write: the sheet has checked that nothing is
there. Pass several documents with `quitting=True` (C: `TRUE`) to quit: the
person reviews each in its own window (SAVE or DISCARD per document, CANCEL
stops the quit) or discards all, and you receive QUIT when it is time to go.

Give the seconds since the first edit that is not saved yet only when you
record it; otherwise the sheet says the changes haven't been saved. Never
guess it.

## Handheld presentation

When the context is `fullscreen-mobile`, the shell owns the surface chrome:
the window is undecorated and the title row is hidden. Your app shows each page
with a header of its own, such as a
[`MobileHeader`](appkit-reference.md#mobileheader), and every command remains
reachable through navigation, sheets or overflow menus. See
[adaptive layouts](adaptive-layouts.md).

## Your stylesheet

Add app styles with `add_style_sheet()` (Python) or
`luma_ui_add_style_resource()` (C) so they follow appearance changes. Style only
your own content classes, and name colors with [tokens](../design/color.md).

Your stylesheet must never contain rules for:

- `windowcontrols`, `headerbar`, `.luma-titlebar`, `.luma-identity-*`
- `.luma-window-body`, `.luma-island` or `.luma-pane`: their radius, ring,
  shadow, margin or padding
- a `border-radius` on the window itself
- a `box-shadow` on anything that is an island
- a `@define-color`
- a hex or `rgba()` color (document data drawn in code, such as a swatch or a
  video surround, is the exception; say why in a comment)

Each of these is a second answer to a question LumaUI already answers. Review
for the Luma tier checks for them.
