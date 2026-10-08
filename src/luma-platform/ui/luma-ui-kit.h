/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI core for C: the API level, Lucide icons, the type scale, motion and
 * width. The twin of luma_appkit/lumaui.py, icons.py, content_type.py and
 * structure_adapt.py. Apps say *what*; the kit owns *how* (ADR-052,
 * docs/developer/kit/lumaui-principles.md).
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LUMA_UI_API_LEVEL:
 *
 * The public LumaUI API level, the same number as Python's
 * `luma_appkit.LUMAUI_API_LEVEL`. It rises by one whenever a public name, an
 * argument or a toast kind is added; within a level nothing is removed.
 */
#define LUMA_UI_API_LEVEL 2

/**
 * luma_ui_get_api_level:
 *
 * Returns: %LUMA_UI_API_LEVEL of the library actually loaded.
 */
int luma_ui_get_api_level(void);

/**
 * luma_ui_install:
 *
 * Load the kit's stylesheets and icons for the default display, once. Every
 * part calls it on construction, so an app only needs it when it styles
 * something itself before any part exists. Same as luma_init().
 */
void luma_ui_install(void);

/**
 * luma_ui_icon_name:
 * @lucide: a Lucide glyph name, such as "share-2"
 *
 * The stable theme name for a Lucide glyph: "share-2" is
 * "lumaui-share-2-symbolic". Share is "share-2", Export is "share".
 *
 * Returns: (transfer full): the icon name
 */
char *luma_ui_icon_name(const char *lucide);

/**
 * luma_ui_icon_new:
 * @lucide: a Lucide glyph name
 *
 * A #GtkImage of the glyph at the size the kit's CSS gives it.
 *
 * Returns: (transfer floating): a new #GtkImage
 */
GtkWidget *luma_ui_icon_new(const char *lucide);

/**
 * luma_ui_apply_type:
 * @widget: a widget, usually a #GtkLabel
 * @role: a type role: "display", "hero", "title-1", "title-2", "lead",
 *   "body", "caption", "label" or "numeric" (the tokens' type scale)
 *
 * Give @widget one type role, replacing any other (class `lumaui-t-<role>`).
 * An unknown role is refused with a critical.
 */
void luma_ui_apply_type(GtkWidget *widget, const char *role);

/**
 * luma_ui_apply_type_full:
 * @widget: a widget
 * @role: a type role
 * @muted: set it in the quiet ink (class `lumaui-t-muted`)
 *
 * luma_ui_apply_type() with the muted tone (Python `apply_type(w, role, muted=True)`).
 */
void luma_ui_apply_type_full(GtkWidget *widget, const char *role, gboolean muted);

/**
 * luma_ui_reduced_motion:
 *
 * Returns: whether movement should become a fade (the desktop's reduced
 * motion, or animations off)
 */
gboolean luma_ui_reduced_motion(void);

/**
 * luma_ui_mobile_form_factor:
 *
 * Resolve phone presentation from LUMA_FORM_FACTOR, then LUMA_DEVICE_CLASS
 * or /etc/luma-device-class, then the legacy machine-info chassis. Width
 * determines layout separately. Applications should use this shared policy
 * rather than maintaining their own device detection.
 *
 * Returns: whether the current device uses phone presentation
 */
gboolean luma_ui_mobile_form_factor(void);

/**
 * luma_ui_is_phone_width:
 * @widget: a widget in a window
 *
 * Whether the window @widget is in is phone-width (the v71 phone tier,
 * under 560). Width adapts layout, never identity.
 *
 * Returns: %TRUE at phone width
 */
gboolean luma_ui_is_phone_width(GtkWidget *widget);

/**
 * LumaTier:
 * @LUMA_TIER_REGULAR: wider than 900: the desktop app
 * @LUMA_TIER_COMPACT: 560 to 900: sidebars fold into drawers, inspectors into sheets
 * @LUMA_TIER_PHONE: under 560: thumb-sized chrome, the phone drawer, list-first
 *
 * The v71 tiers (2026-09-29-mobile-layer-v71.md). Crossing 560 redraws in the
 * other shape.
 */
typedef enum {
  LUMA_TIER_REGULAR,
  LUMA_TIER_COMPACT,
  LUMA_TIER_PHONE,
} LumaTier;
GType luma_tier_get_type(void);
#define LUMA_TYPE_TIER (luma_tier_get_type())

/**
 * LUMA_TIER_PHONE_BELOW:
 * A window narrower than this is a phone.
 */
#define LUMA_TIER_PHONE_BELOW 560
/**
 * LUMA_TIER_REGULAR_ABOVE:
 * A window wider than this is regular; from %LUMA_TIER_PHONE_BELOW up to it, compact.
 */
#define LUMA_TIER_REGULAR_ABOVE 900

/**
 * luma_ui_tier_for_width:
 * @width: a window's width in logical pixels
 *
 * Returns: the tier of a window this wide
 */
LumaTier luma_ui_tier_for_width(int width);
/**
 * luma_ui_get_tier:
 * @widget: a widget in a window
 *
 * Returns: the tier of the window @widget is in now (regular before it has a width)
 */
LumaTier luma_ui_get_tier(GtkWidget *widget);

/**
 * LumaWidthWatch:
 *
 * The window width a widget lives at, after layout (Python `WidthWatch`):
 * #LumaWidthWatch:width, #LumaWidthWatch:tier, and #LumaWidthWatch::tier-changed
 * when a resize, a move or a first showing crosses 560 or 900. A window drawn
 * while hidden redraws in its real shape when it first shows.
 */
#define LUMA_TYPE_WIDTH_WATCH (luma_width_watch_get_type())
G_DECLARE_FINAL_TYPE(LumaWidthWatch, luma_width_watch, LUMA, WIDTH_WATCH, GObject)

/**
 * luma_width_watch_get:
 * @widget: a widget
 *
 * The widget's watch, made on first use and kept as long as @widget lives.
 *
 * Returns: (transfer none): the watch
 */
LumaWidthWatch *luma_width_watch_get(GtkWidget *widget);
LumaTier luma_width_watch_get_tier(LumaWidthWatch *self);
int luma_width_watch_get_width(LumaWidthWatch *self);

/**
 * luma_ui_count_text:
 * @count: the count
 * @attention: whether it needs attention (unread)
 *
 * What a count badge says: "" for 0, "1,284" for a total, "99+" past the
 * attention cap.
 *
 * Returns: (transfer full): the text
 */
char *luma_ui_count_text(int count, gboolean attention);

/**
 * luma_ui_initials:
 * @name: a person's or thing's name
 *
 * One or two letters for a name: "Priya Raman" is "PR"; never a digit or a
 * bracket.
 *
 * Returns: (transfer full): the initials
 */
char *luma_ui_initials(const char *name);

/**
 * luma_ui_person_tone:
 * @name: a name
 *
 * The hue a person or thing wears everywhere: one of the category names
 * ("create", "work", "media", "play", "tools"), picked by the name.
 *
 * Returns: (transfer none): the tone
 */
const char *luma_ui_person_tone(const char *name);

G_END_DECLS
