---
title: LumaUI-1 reference
summary: LumaUI's C library, LumaUI-1: initialization, the window, islands, the adaptive scaffold, context and the media and imaging controls, with properties, signals and introspection names, as they exist in platform 0.1.0.
order: 3
section: Kit
---

# LumaUI-1 reference

`LumaUI-1` (`libluma-ui-1.so.0`) is LumaUI’s C library. It is a GObject library
with introspection metadata, so the same API is available as `LumaUI` to any
language with GObject introspection. This page covers platform
**0.1.0 (preview)**; the ABI is not yet stable.

```c
#include <luma-ui.h>   /* pkg-config: luma-ui-1 */
```

`luma-ui.h` includes every public header. The `pkg-config` module requires
`gtk4`, `libadwaita-1` and `luma-semantics-1`. LumaUI builds on libadwaita’s
window, header bar, breakpoint and split-view types and draws them with its own
sheets, which is why some types below have `Adw` parents. Every type is final
(`G_DECLARE_FINAL_TYPE`), so compose rather than subclass, and `g_autoptr()`
works for all of them.

This page covers the core types. The LumaUI parts (islands, the navigation
sidebar, toasts, dialogs, the action center, cards, fields, menus and more) are
listed next to their Python twins in [LumaUI for C](lumaui-c.md), and the
gtk-doc comments in each header are their reference.

| Type | Parent | Purpose |
| --- | --- | --- |
| [`LumaApplicationWindow`](#lumaapplicationwindow) | `AdwApplicationWindow` | The Luma window |
| [`LumaPane`](#lumapane) | `GtkBox` | An island |
| [`LumaAdaptiveScaffold`](#lumaadaptivescaffold) | `AdwBreakpointBin` | Sidebar and content that collapse on narrow widths |
| [`LumaActionBar`](#lumaactionbar) | `GtkBox` | A bar with leading, center and trailing areas |
| [`LumaContext`](#lumacontext) | `GObject` | Presentation and input |
| [`LumaSegmentedControl`](#lumasegmentedcontrol) | `GtkBox` | Mutually exclusive segments |
| [`LumaInspectorSection`](#lumainspectorsection) | `GtkBox` | A collapsible inspector section |
| [`LumaColorWell`](#lumacolorwell) | `GtkBox` | A color button with an editable value |
| [`LumaMediaFader`](#lumamediafader) | `GtkScale` | A decibel fader |
| [`LumaAudioMeter`](#lumaaudiometer) | `GtkBox` | A mono or stereo level meter |
| [`LumaHistogram`](#lumahistogram) | `GtkDrawingArea` | An image histogram |
| [`LumaCurveEditor`](#lumacurveeditor) | `GtkDrawingArea` | A tone curve editor |
| [`LumaWelcomeView`](#lumawelcomeview) | `GtkWidget` | The welcome surface for creative apps |
| [`LumaApplicationIcon`](#lumaapplicationicon) | `GObject` (`GdkPaintable`) | Application artwork on the Luma tile |
| [`LumaSurfaceBackdrop`](#lumasurfacebackdrop) | `GObject` | The compositor backdrop binding for a window |

## Initialization and styles

```c
void luma_init (void);
void luma_ui_add_style_resource (const char *resource_path, guint priority);
void luma_ui_centre_controls (GtkWidget *bar);
```

**`luma_init()`** installs LumaUI on the default display: LumaUI’s window
sheets, which draw the frame, title row and controls; the token sheet for the
current appearance and a legacy token sheet at
`GTK_STYLE_PROVIDER_PRIORITY_APPLICATION`; the base sheet at the same priority;
and the structural sheet at `APPLICATION + 2`. It follows dark and
high-contrast changes and Luma’s surface policy, and binds a
`LumaSurfaceBackdrop` to every window of the default `GtkApplication`, now and
as windows are added. It does nothing without a display and runs only once.
Python: `LumaUI.init()`.

**`luma_ui_add_style_resource()`** loads your stylesheet from a GResource path
at `priority` (use `GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 1`) and re-parses
it whenever the tokens change, so `@luma_*` colors follow the appearance. It
calls `luma_init()` first.

```c
luma_ui_add_style_resource ("/com/example/Counter/counter.css",
                            GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 1);
```

**`luma_ui_centre_controls()`** changes every child of `bar` whose vertical
alignment is `GTK_ALIGN_FILL` to `GTK_ALIGN_CENTER`, so controls keep their
designed height. Call it after filling a bar you built from a plain box.

---

## `LumaApplicationWindow`

```c
GtkWidget  *luma_application_window_new            (GtkApplication        *application,
                                                    LumaContext           *context);
void        luma_application_window_set_body       (LumaApplicationWindow *self,
                                                    GtkWidget             *body);
GtkWidget  *luma_application_window_get_body       (LumaApplicationWindow *self);
void        luma_application_window_set_identity   (LumaApplicationWindow *self,
                                                    const char            *title,
                                                    const char            *subtitle,
                                                    const char            *icon_name);
void        luma_application_window_set_menu_model (LumaApplicationWindow *self,
                                                    GMenuModel            *menu_model);
void        luma_application_window_set_leading    (LumaApplicationWindow *self,
                                                    GtkWidget             *widget);
void        luma_application_window_set_title_content (LumaApplicationWindow *self,
                                                    GtkWidget             *widget,
                                                    gboolean               centred);
void        luma_application_window_set_trailing   (LumaApplicationWindow *self,
                                                    GtkWidget             *widget);
GtkWidget  *luma_application_window_get_title_bar  (LumaApplicationWindow *self);
GtkWidget  *luma_application_window_get_layer_host (LumaApplicationWindow *self);
```

| Property | Type | Flags |
| --- | --- | --- |
| `context` | `LumaContext` | read, write, construct-only |

The Luma window. It carries `luma-app-window` and `luma-application-window`,
and contains an `AdwToolbarView` (`luma-window-toolbar-view`) whose top bar is
an `AdwHeaderBar` carrying `luma-titlebar` with its own title hidden. Under the
title row sits the window’s layer host (where toasts, dialogs and drawers
appear) around an `AdwBin` carrying `luma-window-body`. LumaUI draws the frame,
identity and window controls: `luma_init()` loads LumaUI’s window sheets, and
the window builds its own identity.

- **`_new()`** requires a `GtkApplication`. `context` may be `NULL`. When the
  context is not windowed, the window is undecorated and the title bar hidden;
  with touch input, the window carries `luma-touch`. If the application has no
  menubar yet, a stable empty `GMenu` is installed so the identity can bind to
  it. The window also binds its compositor backdrop.
- **`_set_body()`** sets the single child of the body. Pass a `LumaPane`, a
  box of panes, or a `LumaAdaptiveScaffold`. Returns (transfer none) from
  `_get_body()`.
- **`_set_identity()`** sets the window title to `title`, or to `subtitle` and
  `title` together when `subtitle` is not empty. The name and icon drawn in the
  corner always come from the desktop entry; `icon_name` is ignored.
- **`_set_menu_model()`** sets the identity menu. The window copies the model’s
  top-level items into the application’s stable menu so the identity stays
  bound; otherwise it calls `gtk_application_set_menubar()`.
- **`_set_leading()`**, **`_set_title_content()`** and **`_set_trailing()`**
  fill the title row’s three slots: one control just after the identity (such
  as the sidebar toggle), what the window is about (after the leading control,
  or centred on the window when `centred` is true), and the window’s own
  actions before the window controls. A new widget replaces the previous one;
  `NULL` clears the slot. `_get_title_bar()` and `_get_layer_host()` return
  (transfer none) the title row and the layer host.
- Presenting an `AdwAlertDialog` on the window gives it the quiet control
  treatment. For a destructive confirmation, prefer LumaUI’s
  `luma_destructive_dialog_ask()`.
- To give an existing `AdwApplicationWindow` subclass the Luma frame without
  rebuilding it, call `luma_application_window_frame_adopt()`; its content,
  actions and template children stay in place.

## `LumaPane`

```c
GtkWidget *luma_pane_new       (void);
void       luma_pane_set_child (LumaPane *self, GtkWidget *child);
GtkWidget *luma_pane_get_child (LumaPane *self);
```

A vertical box that expands in both directions and carries `luma-pane` and
`luma-island`, so LumaUI draws it as an island. `_set_child()` replaces the one
child and makes it expand. `LumaIsland` (see [LumaUI for C](lumaui-c.md)) is
the closer twin of the Python `Island`.

## `LumaAdaptiveScaffold`

```c
GtkWidget *luma_adaptive_scaffold_new                   (void);
void       luma_adaptive_scaffold_set_sidebar           (LumaAdaptiveScaffold *self,
                                                          GtkWidget *sidebar, const char *title);
void       luma_adaptive_scaffold_set_content           (LumaAdaptiveScaffold *self,
                                                          GtkWidget *content, const char *title);
void       luma_adaptive_scaffold_set_compact_width     (LumaAdaptiveScaffold *self, guint width);
void       luma_adaptive_scaffold_set_sidebar_width_limits (LumaAdaptiveScaffold *self,
                                                          guint minimum_width, guint maximum_width);
void       luma_adaptive_scaffold_set_sidebar_width     (LumaAdaptiveScaffold *self, guint width);
guint      luma_adaptive_scaffold_get_sidebar_width     (LumaAdaptiveScaffold *self);
gboolean   luma_adaptive_scaffold_get_collapsed         (LumaAdaptiveScaffold *self);
void       luma_adaptive_scaffold_set_show_content      (LumaAdaptiveScaffold *self,
                                                          gboolean show_content);
```

| Property | Type | Range | Default |
| --- | --- | --- | --- |
| `sidebar-width` | `guint` | 120 to 640 | 180 |

An `AdwBreakpointBin` holding an `AdwNavigationSplitView`, carrying
`luma-adaptive-scaffold`. A breakpoint collapses the split view at
`max-width: 559px` by default, the same phone width every LumaUI part uses.

- **`_set_sidebar()`** and **`_set_content()`** wrap the widget in an
  `AdwNavigationPage` with `title` (used for the back button and accessibility
  when collapsed).
- **`_set_compact_width()`** moves the breakpoint; `width` must be 320 to 1200.
- **`_set_sidebar_width_limits()`** and **`_set_sidebar_width()`** opt into a
  fixed sidebar width in logical pixels. Until you call either, native adaptive
  sizing is unchanged. Limits default to 160 to 320 and must stay within 120 to
  640; the width is clamped to them. The getter returns the configured
  preference, not the current allocation.
- **`_get_collapsed()`** reports whether the split view is collapsed now.
- **`_set_show_content()`** shows the content page when collapsed.

## `LumaActionBar`

```c
GtkWidget  *luma_action_bar_new          (void);
void        luma_action_bar_set_title    (LumaActionBar *self, const char *title);
const char *luma_action_bar_get_title    (LumaActionBar *self);
void        luma_action_bar_add_leading  (LumaActionBar *self, GtkWidget *widget);
void        luma_action_bar_add_trailing (LumaActionBar *self, GtkWidget *widget);
GtkWidget  *luma_action_bar_get_leading  (LumaActionBar *self);
GtkWidget  *luma_action_bar_get_center   (LumaActionBar *self);
GtkWidget  *luma_action_bar_get_trailing (LumaActionBar *self);
```

A horizontal bar carrying `luma-action-bar` with three boxes: leading, an
expanding center that starts with an ellipsizing title label
(`luma-action-title`), and trailing. Controls added with `_add_leading()` and
`_add_trailing()` are vertically centered. The getters return the boxes
(transfer none) for anything else you need to place.

## `LumaContext`

```c
typedef enum { LUMA_PRESENTATION_WINDOWED, LUMA_PRESENTATION_FULLSCREEN_MOBILE } LumaPresentationMode;
typedef enum { LUMA_INPUT_POINTER, LUMA_INPUT_TOUCH } LumaInputMode;

LumaContext          *luma_context_new                  (LumaPresentationMode presentation,
                                                          LumaInputMode        input);
LumaContext          *luma_context_new_from_environment (void);
LumaPresentationMode  luma_context_get_presentation     (LumaContext *self);
LumaInputMode         luma_context_get_input            (LumaContext *self);
gboolean              luma_context_get_decorated        (LumaContext *self);
gboolean              luma_context_get_touch_targets    (LumaContext *self);
```

| Property | Type | Flags |
| --- | --- | --- |
| `presentation` | `LumaPresentationMode` (nicks `windowed`, `fullscreen-mobile`) | read, write, construct-only |
| `input` | `LumaInputMode` (nicks `pointer`, `touch`) | read, write, construct-only |

An immutable description of how the app is presented.
`_new_from_environment()` starts from the device class: when
`LUMA_DEVICE_CLASS` is `handheld`, or `/etc/luma-device-class` contains
`handheld`, it defaults to fullscreen and touch; otherwise windowed and pointer.
`LUMA_PRESENTATION_MODE` and `LUMA_INPUT_MODE` override those defaults, and an
invalid value logs a warning and is ignored. `_get_decorated()` is true when
windowed; `_get_touch_targets()` is true for touch input.

Python: `LumaUI.Context.new_from_environment()`. LumaUI’s Python package has its own
`AppContext`, which also treats a `tablet` device class as touch input.

## `LumaSegmentedControl`

```c
GtkWidget  *luma_segmented_control_new          (void);
void        luma_segmented_control_append       (LumaSegmentedControl *self,
                                                  const char *id, const char *label);
void        luma_segmented_control_set_selected (LumaSegmentedControl *self, const char *id);
const char *luma_segmented_control_get_selected (LumaSegmentedControl *self);
```

| Signal | Signature |
| --- | --- |
| `changed` | `void (*)(LumaSegmentedControl *self, const char *id, gpointer user_data)` |

Homogeneous grouped toggle buttons carrying `luma-segmented-control`. `id` must
not be empty. The first segment appended is selected. `changed` is emitted when
the selection moves to a different ID.

## `LumaInspectorSection`

```c
GtkWidget *luma_inspector_section_new          (const char *title);
void       luma_inspector_section_set_title    (LumaInspectorSection *self, const char *title);
void       luma_inspector_section_set_child    (LumaInspectorSection *self, GtkWidget *child);
GtkWidget *luma_inspector_section_get_child    (LumaInspectorSection *self);
void       luma_inspector_section_set_expanded (LumaInspectorSection *self, gboolean expanded);
gboolean   luma_inspector_section_get_expanded (LumaInspectorSection *self);
```

| Signal | Signature |
| --- | --- |
| `expanded-changed` | `void (*)(LumaInspectorSection *self, gboolean expanded, gpointer user_data)` |

A vertical section with a disclosure button (chevron and title) and one child
that is shown only when expanded. Starts expanded. The title is also the
disclosure button’s accessible name.

## `LumaColorWell`

```c
GtkWidget     *luma_color_well_new      (void);
void           luma_color_well_set_rgba (LumaColorWell *self, const GdkRGBA *rgba);
const GdkRGBA *luma_color_well_get_rgba (LumaColorWell *self);
void           luma_color_well_set_text (LumaColorWell *self, const char *text);
const char    *luma_color_well_get_text (LumaColorWell *self);
```

| Signal | Signature |
| --- | --- |
| `changed` | `void (*)(LumaColorWell *self, gpointer user_data)` |

A `GtkColorDialogButton` (with alpha) beside an entry showing the color as
text. Typing a valid color and pressing Enter applies it; an invalid value is
reverted. `_set_text()` ignores text that does not parse. `changed` is emitted
whenever the color changes.

## `LumaMediaFader`

```c
GtkWidget *luma_media_fader_new             (GtkOrientation orientation);
void       luma_media_fader_set_db          (LumaMediaFader *self, double db);
double     luma_media_fader_get_db          (LumaMediaFader *self);
void       luma_media_fader_set_reset_db    (LumaMediaFader *self, double db);
double     luma_media_fader_get_reset_db    (LumaMediaFader *self);
double     luma_media_fader_db_to_amplitude (double db);
```

A `GtkScale` from −60 dB to +12 dB (step 0.1, page 3) with marks at −∞, 0 and
+12, formatting values in decibels. Double-clicking resets it to the reset
level (default 0 dB). Values are clamped to the range. Use `GtkRange::value-changed`
to follow it. `_db_to_amplitude()` returns 0 at or below −60 dB, otherwise
10^(dB/20). If you construct it with your own `adjustment`, LumaUI leaves that
adjustment’s range alone.

## `LumaAudioMeter`

```c
GtkWidget *luma_audio_meter_new             (guint channels);
void       luma_audio_meter_set_channels    (LumaAudioMeter *self, guint channels);
guint      luma_audio_meter_get_channels    (LumaAudioMeter *self);
void       luma_audio_meter_set_levels_db   (LumaAudioMeter *self, double left_db, double right_db);
void       luma_audio_meter_set_levels      (LumaAudioMeter *self, double left, double right);
void       luma_audio_meter_reset_clip      (LumaAudioMeter *self);
void       luma_audio_meter_set_clipped     (LumaAudioMeter *self, gboolean clipped);
gboolean   luma_audio_meter_get_clipped     (LumaAudioMeter *self);
double     luma_audio_meter_db_to_fraction  (double db);
```

One or two vertical level bars (`channels` must be 1 or 2) mapping −60 dB to
+12 dB. `_set_levels()` takes linear amplitudes. A peak at or above 0 dB latches
the `clipping` style until `_reset_clip()`. The accessible description is
updated in 6 dB steps rather than on every frame, so screen readers are not
flooded.

## `LumaHistogram`

```c
GtkWidget *luma_histogram_new                   (void);
void       luma_histogram_set_channels          (LumaHistogram *self, GVariant *channels);
GVariant  *luma_histogram_get_channels          (LumaHistogram *self);
void       luma_histogram_set_clipping_visible  (LumaHistogram *self, gboolean visible);
gboolean   luma_histogram_get_clipping_visible  (LumaHistogram *self);
```

| Property | Type | Default |
| --- | --- | --- |
| `clipping-visible` | `gboolean` | `FALSE` |

A 116px tall histogram. `channels` is a `GVariant` of type `aad` with exactly
four arrays of up to 256 values each; values are normalized to the peak.
`_get_channels()` returns a new reference to the normalized `aad`.
`clipping-visible` shows shadow and highlight clipping.

## `LumaCurveEditor`

```c
GtkWidget *luma_curve_editor_new        (void);
void       luma_curve_editor_set_points (LumaCurveEditor *self, GVariant *points);
GVariant  *luma_curve_editor_get_points (LumaCurveEditor *self);
void       luma_curve_editor_reset      (LumaCurveEditor *self);
```

| Signal | Signature |
| --- | --- |
| `changed` | `void (*)(LumaCurveEditor *self, gpointer user_data)` |

A draggable tone curve. Points are a `GVariant` of type `a(dd)` with `x` and
`y` from 0 to 1; values are clamped, and a dragged point cannot pass its
neighbors. `_reset()` restores the identity curve.

## `LumaWelcomeView`

```c
GtkWidget *luma_welcome_view_new             (const char *title, const char *icon_name,
                                               const char *description, const char *noun,
                                               const char *settings_schema);
GtkWidget *luma_welcome_view_get_new_button  (LumaWelcomeView *self);
GtkWidget *luma_welcome_view_get_open_button (LumaWelcomeView *self);
gboolean   luma_welcome_view_should_show     (const char *settings_schema);
```

The welcome surface Luma’s creative apps show when opened without a document:
a panel with the app icon, a “CREATIVE COLLECTION” eyebrow, the title, the
description, **New <noun>** and **Open…** buttons, and a “Show this when
<title> opens” check. Wider than 640px it sits beside a photograph; narrower,
it stacks over a dark scrim. Bind the buttons to your existing New and Open
commands. If `settings_schema` is installed and has a boolean `show-welcome`
key, the check is bound to it and `_should_show()` returns its value; otherwise
the check is disabled and `_should_show()` returns true. The eyebrow text is
fixed, so this view suits apps in Luma’s creative collection.

## `LumaApplicationIcon`

```c
GdkPaintable *luma_application_icon_new (GdkPaintable *artwork);
```

Returns a new paintable (transfer full) that draws `artwork` on Luma’s square
gradient tile with a rounded mask whose radius is a quarter of the side, and a
faint outline. Use it to show application icons the way the dock does. Do not
use it for file or folder thumbnails.

## `LumaSurfaceBackdrop`

```c
LumaSurfaceBackdrop *luma_surface_backdrop_new                  (GtkWindow  *window);
gboolean             luma_surface_backdrop_get_available        (LumaSurfaceBackdrop *self);
gboolean             luma_surface_backdrop_get_active           (LumaSurfaceBackdrop *self);
gboolean             luma_surface_backdrop_display_is_available (GdkDisplay *display);
```

| Property | Type | Flags |
| --- | --- | --- |
| `available` | `gboolean` | read-only: the compositor advertises background blur |
| `active` | `gboolean` | read-only: the window has a live blur region now |

Negotiates the `ext-background-effect-v1` Wayland protocol so Frost and Glass
materials can blur what is behind a window, and removes the effect when
accessibility settings or renderer capability require an opaque surface.
`LumaApplicationWindow`, `AppWindow` and `luma_init()` create these for you;
you only need `_display_is_available()` if you are building a settings surface.

---

## `LumaAppearance-1`

`libluma-appearance-1` provides `LumaSurfacePolicy`, which decides the
effective surface treatment (`light`, `dark`, `frost` or `glass`) from Luma’s
settings, high contrast, reduced transparency and renderer capability. LumaUI
uses it internally; apps normally never call it. Its header is
`<luma-appearance.h>` (`pkg-config luma-appearance-1`).

## Introspection names

| C | Python (`gi.repository`) |
| --- | --- |
| `luma_init()` | `LumaUI.init()` |
| `luma_application_window_new()` | `LumaUI.ApplicationWindow.new()` |
| `luma_context_new_from_environment()` | `LumaUI.Context.new_from_environment()` |
| `LUMA_PRESENTATION_FULLSCREEN_MOBILE` | `LumaUI.PresentationMode.FULLSCREEN_MOBILE` |
| `luma_welcome_view_should_show()` | `LumaUI.WelcomeView.should_show()` |
