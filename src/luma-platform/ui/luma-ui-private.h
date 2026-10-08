/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI internals shared by the C parts; not installed. The twins of
 * structure_adapt.py (width, icon buttons, arrow keys) and lumaui.py (motion).
 */
#pragma once

#include <gtk/gtk.h>

#include "luma-ui-kit.h"
#include "luma-ui-tokens-private.h"

G_BEGIN_DECLS

/* Register a final subtype of a GTK type whose instance struct is private
 * (GtkLabel, GtkOverlay), so the part keeps its Python twin's CSS node
 * ("label", "overlay") with no wrapper node. The header declares the part with
 * G_DECLARE_FINAL_TYPE(..., GtkWidget); the instance data lives in
 * TypeName##Private, reached with type_name##_get_instance_private(). */
#define LUMA_DEFINE_OPAQUE_SUBTYPE(TypeName, type_name, PARENT_TYPE)                                  \
  static void type_name##_init(TypeName *self);                                                      \
  static void type_name##_class_init(TypeName##Class *klass);                                         \
  static gpointer type_name##_parent_class = NULL;                                                   \
  static int TypeName##_private_offset;                                                              \
  static void type_name##_class_intern_init(gpointer klass, gpointer data G_GNUC_UNUSED) {           \
    type_name##_parent_class = g_type_class_peek_parent(klass);                                      \
    g_type_class_adjust_private_offset(klass, &TypeName##_private_offset);                           \
    type_name##_class_init((TypeName##Class *)klass);                                                \
  }                                                                                                  \
  G_GNUC_UNUSED static inline TypeName##Private *type_name##_get_instance_private(TypeName *self) {  \
    return (TypeName##Private *)G_STRUCT_MEMBER_P(self, TypeName##_private_offset);                  \
  }                                                                                                  \
  GType type_name##_get_type(void) {                                                                 \
    static gsize type_id = 0;                                                                        \
    if (g_once_init_enter(&type_id)) {                                                               \
      GTypeQuery query;                                                                              \
      g_type_query(PARENT_TYPE, &query);                                                             \
      const GTypeInfo info = {                                                                       \
        (guint16)query.class_size, NULL, NULL, type_name##_class_intern_init, NULL, NULL,            \
        (guint16)query.instance_size, 0, (GInstanceInitFunc)(void (*)(void))type_name##_init, NULL}; \
      GType type = g_type_register_static(PARENT_TYPE, g_intern_static_string(#TypeName), &info,    \
                                          G_TYPE_FLAG_FINAL);                                        \
      TypeName##_private_offset = g_type_add_instance_private(type, sizeof(TypeName##Private));      \
      g_once_init_leave(&type_id, type);                                                             \
    }                                                                                                \
    return type_id;                                                                                  \
  }

/* Classes */
void luma_ui_set_css_class(GtkWidget *widget, const char *name, gboolean on);
/* The accessible label (and nothing else). */
void luma_ui_set_accessible_label(GtkWidget *widget, const char *label);

/* A decorative Gtk.Image of a glyph (icons.image); pixel_size <= 0 leaves it to CSS. */
GtkWidget *luma_ui_icon_image(const char *lucide, int pixel_size);
/* A quiet icon button: the glyph, its name as tooltip and accessible label
 * (structure_adapt.icon_button). */
GtkWidget *luma_ui_icon_button(const char *icon, const char *label, const char *css_class,
                               gboolean toggle, const char *shortcut);
/* Arrows move focus between the focusable children of @box, wrapping; with
 * @activate, moving also activates the control reached (a radio group). */
void luma_ui_arrow_keys(GtkWidget *box, GtkOrientation orientation, gboolean activate);

/* Activate a GAction from @widget. GTK 4.22 does not refresh a subtree's
 * action muxer when an ancestor gets its action group after the subtree was
 * built, so activating on @widget alone can silently do nothing: walk up the
 * ancestors until one resolves @name. Returns whether it ran. */
gboolean luma_ui_activate_action(GtkWidget *widget, const char *name, GVariant *target);

/* Width (structure_adapt): the window's width, or the "window" layer host's. */
int luma_ui_window_width(GtkWidget *widget);
gboolean luma_ui_is_phone(GtkWidget *widget);

/* WidthWatch: calls @callback(widget, width, data) after layout whenever the
 * window's width changes; with @threshold > 0 only when width <= threshold
 * changes truth. It lives as long as @widget. */
typedef void (*LumaWidthCallback)(GtkWidget *widget, int width, gpointer data);
void luma_ui_width_watch(GtkWidget *widget, LumaWidthCallback callback, gpointer data, int threshold);
/* Measure every watch on @widget now (tests, parts just shown). */
void luma_ui_width_watch_check(GtkWidget *widget);

/* Motion (lumaui.duration): a motion token in ms, honouring reduced motion.
 * Timeouts (toast, toast undo, tooltip) pass @is_timeout and are never cut. */
guint luma_ui_duration(guint ms, gboolean is_timeout);
/* The kit's ease curve (MOTION.ease, a CSS cubic-bezier) at progress @x. */
double luma_ui_ease(double x);
/* Run @callback once the widget has been drawn in its starting state. */
void luma_ui_on_next_frame(GtkWidget *widget, GSourceFunc callback, gpointer data);

/* The type roles, NULL-terminated ("display", "hero", "title-1", ...). */
const char *const *luma_ui_type_roles(void);

/* Private LayerHost API for the parts (structure_layers). The layer name is
 * "window" for a window's own host, "content" otherwise. */
const char *luma_layer_host_get_layer_name(gpointer host);
/* Add or remove a floating child (not modal): toasts, the action center,
 * menus, the selection bubble. */
void luma_layer_host_add_layer(gpointer host, GtkWidget *widget);
void luma_layer_host_remove_layer(gpointer host, GtkWidget *widget);
/* The bars toasts stay above (track_bar), for the toast's placement. */
GPtrArray *luma_layer_host_get_tracked_bars(gpointer host);
/* A host named "window" (LumaApplicationWindow's, under its title row). */
GtkWidget *luma_layer_host_new_window(GtkWidget *child);

G_END_DECLS
