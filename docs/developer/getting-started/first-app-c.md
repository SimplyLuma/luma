---
title: Your first app in C
summary: Build Counter, a Luma window with two islands, an action bar, a native empty state and an identity menu, using the LumaUI-1 C library and Meson.
order: 3
section: Getting started
---

# Your first app in C

`LumaUI-1` is LumaUI’s C library. Luma’s own C++ apps, such as Grid, Stage,
Session and Write, use it directly. In this tutorial you build **Counter**: a
`LumaApplicationWindow` with a sidebar pane and a content pane in a
`LumaAdaptiveScaffold`, a `LumaActionBar`, an empty state, and an identity
menu.

The code on this page targets `LumaUI-1` 0.1.0 (preview).

## Before you start

You need `gcc`, `meson`, `ninja`, `pkgconf`, `gtk4-devel`, `libadwaita-devel`
and `luma-developer-platform-devel`. The devel package is not published in a
public package repository, so the way to get all of them is the
`org.projectluma.Sdk//44` Flatpak SDK, together with `flatpak-builder`. See
[install the SDK](install-the-sdk.md).

## 1. Create the project

```text
counter/
├── com.example.Counter.json
├── meson.build
└── src/
    └── main.c
```

## 2. Write `meson.build`

```meson
project('counter', 'c',
  version: '0.1.0',
  meson_version: '>= 1.3.0',
  default_options: ['c_std=c17', 'warning_level=2'],
)

gtk = dependency('gtk4', version: '>= 4.18')
adwaita = dependency('libadwaita-1', version: '>= 1.7')
luma_ui = dependency('luma-ui-1', version: '>= 0.1.0')

executable('counter', 'src/main.c',
  dependencies: [gtk, adwaita, luma_ui],
  install: true,
)
```

`luma-ui-1` brings in `luma-semantics-1`, GTK 4 and libadwaita through its
`Requires`. Its headers live in `luma-1/luma-ui`, so you include
`<luma-ui.h>`.

## 3. Write the app

Save this as `src/main.c`:

```c
/* SPDX-License-Identifier: Apache-2.0 */
#include <adwaita.h>
#include <luma-ui.h>

#define APP_ID "com.example.Counter"

typedef struct {
  GtkWidget *window;
  GtkWidget *pages;
  GtkLabel *count_label;
  guint count;
} Counter;

static void refresh(Counter *self) {
  g_autofree char *text = g_strdup_printf("%u taps", self->count);
  gtk_label_set_label(self->count_label, text);
  gtk_stack_set_visible_child_name(GTK_STACK(self->pages),
                                   self->count > 0 ? "count" : "empty");
}

static void on_tap(GSimpleAction *action, GVariant *parameter, gpointer data) {
  (void)action;
  (void)parameter;
  Counter *self = data;
  self->count++;
  refresh(self);
}

static void on_reset(GSimpleAction *action, GVariant *parameter, gpointer data) {
  (void)action;
  (void)parameter;
  Counter *self = data;
  self->count = 0;
  refresh(self);
}

static GtkWidget *build_sidebar(void) {
  GtkWidget *list = gtk_list_box_new();
  gtk_widget_add_css_class(list, "navigation-sidebar");
  gtk_widget_add_css_class(list, "luma-navigation-sidebar");
  gtk_list_box_append(GTK_LIST_BOX(list), gtk_label_new("Counter"));
  GtkWidget *pane = luma_pane_new();
  luma_pane_set_child(LUMA_PANE(pane), list);
  return pane;
}

static GtkWidget *build_content(Counter *self) {
  GtkWidget *bar = luma_action_bar_new();
  luma_action_bar_set_title(LUMA_ACTION_BAR(bar), "Counter");
  GtkWidget *tap = gtk_button_new_from_icon_name("list-add-symbolic");
  gtk_actionable_set_action_name(GTK_ACTIONABLE(tap), "app.tap");
  gtk_widget_set_tooltip_text(tap, "Tap");
  gtk_accessible_update_property(GTK_ACCESSIBLE(tap),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL, "Tap", -1);
  luma_action_bar_add_trailing(LUMA_ACTION_BAR(bar), tap);

  GtkWidget *empty = adw_status_page_new();
  adw_status_page_set_icon_name(ADW_STATUS_PAGE(empty), "list-add-symbolic");
  adw_status_page_set_title(ADW_STATUS_PAGE(empty), "No taps yet");
  adw_status_page_set_description(ADW_STATUS_PAGE(empty),
                                  "Press Ctrl+T or the plus button.");
  luma_empty_state_configure(ADW_STATUS_PAGE(empty), FALSE);

  self->count_label = GTK_LABEL(gtk_label_new(""));
  gtk_widget_add_css_class(GTK_WIDGET(self->count_label), "title-1");

  self->pages = gtk_stack_new();
  gtk_widget_set_vexpand(self->pages, TRUE);
  gtk_stack_add_named(GTK_STACK(self->pages), empty, "empty");
  gtk_stack_add_named(GTK_STACK(self->pages), GTK_WIDGET(self->count_label), "count");

  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_box_append(GTK_BOX(box), bar);
  gtk_box_append(GTK_BOX(box), self->pages);
  GtkWidget *pane = luma_pane_new();
  luma_pane_set_child(LUMA_PANE(pane), box);
  return pane;
}

static void on_activate(GtkApplication *application, gpointer data) {
  Counter *self = data;
  if (self->window != NULL) {
    gtk_window_present(GTK_WINDOW(self->window));
    return;
  }

  luma_init(); /* once, after GTK has a display */

  static const GActionEntry actions[] = {
      {.name = "tap", .activate = on_tap},
      {.name = "reset", .activate = on_reset},
  };
  g_action_map_add_action_entries(G_ACTION_MAP(application), actions,
                                  G_N_ELEMENTS(actions), self);
  gtk_application_set_accels_for_action(application, "app.tap",
                                        (const char *[]){"<Control>t", NULL});

  g_autoptr(LumaContext) context = luma_context_new_from_environment();
  self->window = luma_application_window_new(application, context);
  luma_application_window_set_identity(LUMA_APPLICATION_WINDOW(self->window),
                                       "Counter", NULL, NULL);
  gtk_widget_set_size_request(self->window, 360, 294);
  gtk_window_set_default_size(GTK_WINDOW(self->window), 820, 560);

  g_autoptr(GMenu) menu = g_menu_new();
  g_autoptr(GMenu) section = g_menu_new();
  g_menu_append(section, "Tap", "app.tap");
  g_menu_append(section, "Reset", "app.reset");
  g_menu_append_section(menu, NULL, G_MENU_MODEL(section));
  luma_application_window_set_menu_model(LUMA_APPLICATION_WINDOW(self->window),
                                         G_MENU_MODEL(menu));

  GtkWidget *scaffold = luma_adaptive_scaffold_new();
  luma_adaptive_scaffold_set_sidebar(LUMA_ADAPTIVE_SCAFFOLD(scaffold),
                                     build_sidebar(), "Library");
  luma_adaptive_scaffold_set_content(LUMA_ADAPTIVE_SCAFFOLD(scaffold),
                                     build_content(self), "Counter");
  luma_adaptive_scaffold_set_show_content(LUMA_ADAPTIVE_SCAFFOLD(scaffold), TRUE);
  luma_application_window_set_body(LUMA_APPLICATION_WINDOW(self->window), scaffold);

  refresh(self);
  gtk_window_present(GTK_WINDOW(self->window));
}

int main(int argc, char **argv) {
  Counter self = {0};
  g_autoptr(AdwApplication) application =
      adw_application_new(APP_ID, G_APPLICATION_DEFAULT_FLAGS);
  g_signal_connect(application, "activate", G_CALLBACK(on_activate), &self);
  return g_application_run(G_APPLICATION(application), argc, argv);
}
```

## 4. Build and run

Save this Flatpak manifest as `com.example.Counter.json`. It builds the Meson
project inside the Luma SDK and runs it on the Luma runtime:

```json
{
  "id": "com.example.Counter",
  "runtime": "org.projectluma.Platform",
  "runtime-version": "44",
  "sdk": "org.projectluma.Sdk",
  "command": "counter",
  "finish-args": [
    "--share=ipc",
    "--socket=wayland",
    "--socket=fallback-x11",
    "--device=dri"
  ],
  "modules": [
    {
      "name": "counter",
      "buildsystem": "meson",
      "sources": [
        {
          "type": "dir",
          "path": "."
        }
      ]
    }
  ]
}
```

Then build, install and run it:

```sh
flatpak-builder --user --install --install-deps-from=luma --force-clean build-dir com.example.Counter.json
flatpak run com.example.Counter
```

On a machine that has the development packages installed, the plain Meson
build works too: `meson setup build`, `ninja -C build`, `./build/counter`.

Press **Ctrl+T** or the plus button. Open the menu under the app’s name in
the top-left corner for **Tap** and **Reset**. Make the window narrower than
560px and the scaffold collapses to one page at a time.

## What just happened

**`luma_init()`** installs LumaUI’s window, token, base and structural
stylesheets for the default display and follows the desktop’s appearance from
then on. Call it once after GTK has a display; calling it again does nothing.
It also binds the compositor backdrop for every window in the application.

**`luma_application_window_new()`** creates LumaUI’s window, carrying
`luma-app-window`, with a title row carrying `luma-titlebar` and a body that
carries `luma-window-body`. LumaUI draws the frame, identity and window
controls. The `LumaContext` you pass decides whether the window is decorated
(windowed) or bare (fullscreen on a phone) and whether it uses touch targets.

**`luma_application_window_set_identity()`** sets the window title, which is
what the shell, the window switcher and screen readers read. With a subtitle it
becomes the subtitle followed by the title, joined by a dash. The name and icon in the corner always come from
your desktop entry, so the `icon_name` argument is accepted and ignored.

**`luma_application_window_set_menu_model()`** gives the identity its menu. The
window keeps one stable menu on the application and copies your model’s items
into it.

**`luma_pane_new()`** creates a pane that carries `luma-island`, so LumaUI
draws it as an island.

**`luma_adaptive_scaffold_new()`** wraps an `AdwNavigationSplitView` in a
breakpoint bin that collapses it at 559px wide and below, LumaUI’s phone width.
`luma_adaptive_scaffold_set_compact_width()` changes that width.

**`luma_empty_state_configure()`** gives a status page LumaUI’s empty-state
look and sizes without taking over its title, description or accessibility.

The complete API is in the [LumaUI-1 reference](../kit/lumaui-reference.md).

## C++

C++ apps use the same headers; they are plain C with `G_BEGIN_DECLS`. Declare
the dependency in Meson the same way:

```meson
luma_ui = dependency('luma-ui-1', version: '>= 0.1.0')
```

Keep GTK calls on the main thread, and keep realtime or document-engine work
behind your own thread boundary.

## Other languages

`LumaUI-1.typelib` and `LumaSemantics-1.typelib` make the library available
through GObject introspection. Python, C and C++ are the supported languages;
other languages can reach `LumaUI-1` through introspection, but there are no
examples or tests for them yet. From Python:

```python
import gi
gi.require_version("LumaUI", "1")
from gi.repository import LumaUI

LumaUI.init()
context = LumaUI.Context.new_from_environment()
```

For Python, prefer [LumaUI’s Python package](first-app-python.md), which adds
the command registry, navigation history and more on top.

## Next

- [Project layout](project-layout.md) and [`luma-app.toml`](luma-app-toml.md)
- [Windows and islands](../kit/windows-and-islands.md)
- [Semantics and actions](../kit/semantics-and-actions.md)
