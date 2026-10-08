/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI core for C: the twin of lumaui.py, icons.py, content_type.py and
 * structure_adapt.py. */
#include "luma-ui-private.h"
#include "luma-ui.h"
#include "luma-layer-host.h"

#include <string.h>

int luma_ui_get_api_level(void) { return LUMA_UI_API_LEVEL; }

static void luma_ui_ensure_icons(GdkDisplay *display) {
  static GHashTable *done = NULL;
  const char *extra = g_getenv("LUMA_APPKIT_ICON_PATH");
  if (display == NULL)
    return;
  if (done == NULL)
    done = g_hash_table_new(NULL, NULL);
  if (g_hash_table_contains(done, display))
    return;
  g_hash_table_add(done, display);
  if (extra == NULL || *extra == '\0')
    return;
  GtkIconTheme *theme = gtk_icon_theme_get_for_display(display);
  g_auto(GStrv) paths = g_strsplit(extra, G_SEARCHPATH_SEPARATOR_S, -1);
  for (guint i = 0; paths[i] != NULL; i++)
    if (paths[i][0] != '\0' && g_file_test(paths[i], G_FILE_TEST_IS_DIR))
      gtk_icon_theme_add_search_path(theme, paths[i]);
}

void luma_ui_install(void) {
  luma_init();
  luma_ui_ensure_icons(gdk_display_get_default());
}

char *luma_ui_icon_name(const char *lucide) {
  g_return_val_if_fail(lucide != NULL && *lucide != '\0', NULL);
  if (g_str_has_suffix(lucide, "-symbolic"))
    return g_strdup(lucide);
  return g_strconcat("lumaui-", lucide, "-symbolic", NULL);
}

GtkWidget *luma_ui_icon_image(const char *lucide, int pixel_size) {
  g_autofree char *name = luma_ui_icon_name(lucide);
  GtkWidget *image = g_object_new(GTK_TYPE_IMAGE, "icon-name", name,
                                  "accessible-role", GTK_ACCESSIBLE_ROLE_PRESENTATION, NULL);
  if (pixel_size > 0)
    gtk_image_set_pixel_size(GTK_IMAGE(image), pixel_size);
  return image;
}

GtkWidget *luma_ui_icon_new(const char *lucide) {
  luma_ui_install();
  return luma_ui_icon_image(lucide, 0);
}

static const char *const type_roles[] = LUMA_UI_TYPE_ROLES;

static gboolean remove_line_height_attribute(PangoAttribute *attribute, gpointer user_data G_GNUC_UNUSED) {
  return attribute->klass->type == PANGO_ATTR_LINE_HEIGHT ||
         attribute->klass->type == PANGO_ATTR_ABSOLUTE_LINE_HEIGHT;
}

const char *const *luma_ui_type_roles(void) { return type_roles; }

void luma_ui_apply_type(GtkWidget *widget, const char *role) {
  luma_ui_apply_type_full(widget, role, FALSE);
}

void luma_ui_apply_type_full(GtkWidget *widget, const char *role, gboolean muted) {
  g_return_if_fail(GTK_IS_WIDGET(widget));
  luma_ui_set_css_class(widget, "lumaui-t-muted", muted);
  g_return_if_fail(role != NULL);
  g_autofree char *key = g_strdelimit(g_strdup(role), "_", '-');
  if (!g_strv_contains(type_roles, key)) {
    g_critical("unknown LumaUI type role '%s'", role);
    return;
  }
  for (guint i = 0; type_roles[i] != NULL; i++) {
    g_autofree char *name = g_strconcat("lumaui-t-", type_roles[i], NULL);
    luma_ui_set_css_class(widget, name, g_str_equal(type_roles[i], key));
  }
  if (GTK_IS_LABEL(widget) &&
      (g_str_equal(key, "display") || g_object_get_data(G_OBJECT(widget), "luma-display-line-height") != NULL)) {
    PangoAttrList *existing = gtk_label_get_attributes(GTK_LABEL(widget));
    PangoAttrList *attributes = existing != NULL ? pango_attr_list_copy(existing) : pango_attr_list_new();
    PangoAttrList *removed = pango_attr_list_filter(attributes, remove_line_height_attribute, NULL);
    if (removed != NULL)
      pango_attr_list_unref(removed);
    if (g_str_equal(key, "display")) {
      int height = (int) (LUMA_UI_TYPE_SCALE_DISPLAY_SIZE *
                          LUMA_UI_TYPE_SCALE_DISPLAY_LINE_HEIGHT * PANGO_SCALE + 0.5);
      pango_attr_list_insert(attributes, pango_attr_line_height_new_absolute(height));
    }
    gtk_label_set_attributes(GTK_LABEL(widget), attributes);
    pango_attr_list_unref(attributes);
    g_object_set_data(G_OBJECT(widget), "luma-display-line-height",
                      g_str_equal(key, "display") ? GINT_TO_POINTER(1) : NULL);
  }
}

gboolean luma_ui_reduced_motion(void) {
  GtkSettings *settings = gtk_settings_get_default();
  gboolean animations = TRUE;
  if (settings == NULL)
    return FALSE;
  g_object_get(settings, "gtk-enable-animations", &animations, NULL);
  if (!animations)
    return TRUE;
#if GTK_CHECK_VERSION(4, 20, 0)
  if (g_object_class_find_property(G_OBJECT_GET_CLASS(settings), "gtk-interface-reduced-motion")) {
    GtkReducedMotion reduced = GTK_REDUCED_MOTION_NO_PREFERENCE;
    g_object_get(settings, "gtk-interface-reduced-motion", &reduced, NULL);
    return reduced == GTK_REDUCED_MOTION_REDUCE;
  }
#endif
  return FALSE;
}

guint luma_ui_duration(guint ms, gboolean is_timeout) {
  if (is_timeout || !luma_ui_reduced_motion())
    return ms;
  GtkSettings *settings = gtk_settings_get_default();
  gboolean animations = TRUE;
  if (settings != NULL)
    g_object_get(settings, "gtk-enable-animations", &animations, NULL);
  return animations ? MIN(ms, (guint)LUMA_UI_MOTION_REDUCED_FADE_MS) : 0;
}

static double bezier_coord(double t, double a, double b) {
  return 3 * a * t * (1 - t) * (1 - t) + 3 * b * t * t * (1 - t) + t * t * t;
}

double luma_ui_ease(double x) {
  const double x1 = LUMA_UI_MOTION_EASE_0, y1 = LUMA_UI_MOTION_EASE_1;
  const double x2 = LUMA_UI_MOTION_EASE_2, y2 = LUMA_UI_MOTION_EASE_3;
  double low = 0.0, high = 1.0;
  for (int i = 0; i < 24; i++) {
    double mid = (low + high) / 2;
    if (bezier_coord(mid, x1, x2) < x)
      low = mid;
    else
      high = mid;
  }
  return bezier_coord((low + high) / 2, y1, y2);
}

typedef struct {
  GSourceFunc callback;
  gpointer data;
} NextFrame;

static gboolean next_frame_tick(GtkWidget *widget G_GNUC_UNUSED, GdkFrameClock *clock G_GNUC_UNUSED,
                                gpointer user_data) {
  NextFrame *frame = user_data;
  frame->callback(frame->data);
  return G_SOURCE_REMOVE;
}

void luma_ui_on_next_frame(GtkWidget *widget, GSourceFunc callback, gpointer data) {
  NextFrame *frame = g_new0(NextFrame, 1);
  frame->callback = callback;
  frame->data = data;
  gtk_widget_add_tick_callback(widget, next_frame_tick, frame, g_free);
}

void luma_ui_set_css_class(GtkWidget *widget, const char *name, gboolean on) {
  if (on)
    gtk_widget_add_css_class(widget, name);
  else
    gtk_widget_remove_css_class(widget, name);
}

void luma_ui_set_accessible_label(GtkWidget *widget, const char *label) {
  gtk_accessible_update_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_LABEL, label, -1);
}

GtkWidget *luma_ui_icon_button(const char *icon, const char *label, const char *css_class,
                               gboolean toggle, const char *shortcut) {
  GtkWidget *button = toggle ? gtk_toggle_button_new() : gtk_button_new();
  gtk_widget_add_css_class(button, css_class);
  gtk_button_set_child(GTK_BUTTON(button), luma_ui_icon_image(icon, 0));
  /* A LumaUI part, not the legacy icon-button look. */
  gtk_widget_remove_css_class(button, "image-button");
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  if (shortcut != NULL) {
    g_autofree char *tip = g_strdup_printf("%s (%s)", label, shortcut);
    gtk_widget_set_tooltip_text(button, tip);
  } else {
    gtk_widget_set_tooltip_text(button, label);
  }
  luma_ui_set_accessible_label(button, label);
  if (shortcut != NULL)
    gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_KEY_SHORTCUTS,
                                   shortcut, -1);
  return button;
}

typedef struct {
  GtkWidget *box;
  GtkOrientation orientation;
  gboolean activate;
} ArrowKeys;

static gboolean arrow_pressed(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval,
                              guint keycode G_GNUC_UNUSED, GdkModifierType state G_GNUC_UNUSED,
                              gpointer user_data) {
  ArrowKeys *keys = user_data;
  gboolean horizontal = keys->orientation == GTK_ORIENTATION_HORIZONTAL;
  guint forward = horizontal ? GDK_KEY_Right : GDK_KEY_Down;
  guint backward = horizontal ? GDK_KEY_Left : GDK_KEY_Up;
  if (keyval != forward && keyval != backward)
    return FALSE;
  g_autoptr(GPtrArray) items = g_ptr_array_new();
  for (GtkWidget *child = gtk_widget_get_first_child(keys->box); child != NULL;
       child = gtk_widget_get_next_sibling(child))
    if (gtk_widget_get_visible(child) && gtk_widget_is_sensitive(child) && gtk_widget_get_focusable(child))
      g_ptr_array_add(items, child);
  if (items->len == 0)
    return FALSE;
  GtkRoot *root = gtk_widget_get_root(keys->box);
  GtkWidget *focus = root != NULL ? gtk_root_get_focus(root) : NULL;
  int current = -1;
  for (guint i = 0; i < items->len && focus != NULL; i++) {
    GtkWidget *item = g_ptr_array_index(items, i);
    if (focus == item || gtk_widget_is_ancestor(focus, item)) {
      current = (int)i;
      break;
    }
  }
  int step = keyval == forward ? 1 : -1;
  int n = (int)items->len;
  GtkWidget *target = g_ptr_array_index(items, (guint)(((current + step) % n + n) % n));
  gtk_widget_grab_focus(target);
  if (keys->activate)
    gtk_widget_activate(target);
  return TRUE;
}

void luma_ui_arrow_keys(GtkWidget *box, GtkOrientation orientation, gboolean activate) {
  ArrowKeys *keys = g_new0(ArrowKeys, 1);
  keys->box = box;
  keys->orientation = orientation;
  keys->activate = activate;
  GtkEventController *controller = gtk_event_controller_key_new();
  g_signal_connect_data(controller, "key-pressed", G_CALLBACK(arrow_pressed), keys,
                        (GClosureNotify)(void (*)(void))g_free, 0);
  gtk_widget_add_controller(box, controller);
}

gboolean luma_ui_activate_action(GtkWidget *widget, const char *name, GVariant *target) {
  g_return_val_if_fail(GTK_IS_WIDGET(widget), FALSE);
  g_return_val_if_fail(name != NULL, FALSE);
  if (target != NULL)
    g_variant_ref_sink(target);
  gboolean done = FALSE;
  for (GtkWidget *node = widget; node != NULL && !done; node = gtk_widget_get_parent(node))
    done = gtk_widget_activate_action_variant(node, name, target);
  if (target != NULL)
    g_variant_unref(target);
  return done;
}

int luma_ui_window_width(GtkWidget *widget) {
  for (GtkWidget *node = widget; node != NULL; node = gtk_widget_get_parent(node))
    if (LUMA_IS_LAYER_HOST(node) &&
        g_strcmp0(luma_layer_host_get_layer_name(node), "window") == 0 && gtk_widget_get_width(node) > 0)
      return gtk_widget_get_width(node);
  GtkRoot *root = gtk_widget_get_root(widget);
  if (root == NULL)
    return 0;
  int width = gtk_widget_get_width(GTK_WIDGET(root));
  /* Asked before the window is laid out (a page that opens straight into a drawer), its default size
   * is the width it will have (Settings' phone Forget came out a centred card at 390). */
  if (width <= 0 && GTK_IS_WINDOW(root))
    gtk_window_get_default_size(GTK_WINDOW(root), &width, NULL);
  return MAX(width, 0);
}

/* Device identity, independent of window width. Keep the precedence aligned
 * with Python's lumaui.mobile_form_factor: explicit override, Luma device
 * class (environment then image marker), legacy systemd chassis. */
gboolean luma_ui_mobile_form_factor(void) {
  const char *value = g_getenv("LUMA_FORM_FACTOR");
  g_autofree char *override = g_ascii_strdown(value != NULL ? value : "", -1);
  g_strstrip(override);
  if (*override != '\0')
    return g_str_equal(override, "phone") || g_str_equal(override, "handset") ||
           g_str_equal(override, "mobile");
  const char *device_env = g_getenv("LUMA_DEVICE_CLASS");
  g_autofree char *device = g_ascii_strdown(device_env != NULL ? device_env : "", -1);
  g_strstrip(device);
  if (!g_str_equal(device, "handheld") && !g_str_equal(device, "tablet") &&
      !g_str_equal(device, "desktop")) {
    g_autofree char *marker = NULL;
    if (g_file_get_contents("/etc/luma-device-class", &marker, NULL, NULL)) {
      g_free(device);
      device = g_ascii_strdown(g_strstrip(marker), -1);
    }
  }
  if (g_str_equal(device, "handheld") || g_str_equal(device, "tablet") ||
      g_str_equal(device, "desktop"))
    return g_str_equal(device, "handheld");
  g_autofree char *info = NULL;
  if (!g_file_get_contents("/etc/machine-info", &info, NULL, NULL))
    return FALSE;
  g_auto(GStrv) lines = g_strsplit(info, "\n", -1);
  for (guint i = 0; lines[i] != NULL; i++) {
    if (!g_str_has_prefix(lines[i], "CHASSIS="))
      continue;
    g_autofree char *chassis = g_strstrip(g_strdup(lines[i] + strlen("CHASSIS=")));
    size_t n = strlen(chassis);
    if (n >= 2 && chassis[0] == '"' && chassis[n - 1] == '"') {
      chassis[n - 1] = '\0';
      return g_ascii_strcasecmp(chassis + 1, "handset") == 0;
    }
    return g_ascii_strcasecmp(chassis, "handset") == 0;
  }
  return FALSE;
}

gboolean luma_ui_is_phone(GtkWidget *widget) {
  /* v71: a phone is a window under 560 (the phone tier); v70's 639 is gone. */
  return luma_ui_tier_for_width(luma_ui_window_width(widget)) == LUMA_TIER_PHONE;
}

gboolean luma_ui_is_phone_width(GtkWidget *widget) {
  g_return_val_if_fail(GTK_IS_WIDGET(widget), FALSE);
  return luma_ui_is_phone(widget);
}

/* WidthWatch */
typedef struct {
  GtkWidget *widget;
  LumaWidthCallback callback;
  gpointer data;
  int threshold;
  int last;
  gboolean has_last;
  GtkWidget *root;
  gulong root_handlers[3];
  GdkSurface *surface;
  gulong surface_handler;
  guint pending;
  GdkFrameClock *clock;
  gulong after_paint;
  gboolean waiting;
  GtkWidget *tick_widget;
  guint tick_id;
} WidthWatch;

static void watch_check(WidthWatch *watch);

static gboolean watch_idle(gpointer user_data) {
  WidthWatch *watch = user_data;
  watch->pending = 0;
  watch_check(watch);
  return G_SOURCE_REMOVE;
}

static void watch_cancel_frame(WidthWatch *watch) {
  if (watch->clock != NULL && watch->after_paint != 0)
    g_signal_handler_disconnect(watch->clock, watch->after_paint);
  watch->after_paint = 0;
  g_clear_object(&watch->clock);
}

static void watch_after_paint(GdkFrameClock *clock G_GNUC_UNUSED, gpointer data) {
  WidthWatch *watch = data;
  watch_cancel_frame(watch);
  watch_check(watch);
}

static void watch_schedule(WidthWatch *watch) {
  GtkWidget *root = GTK_WIDGET(gtk_widget_get_root(watch->widget));
  GdkFrameClock *clock = root != NULL ? gtk_widget_get_frame_clock(root) : NULL;
  if (clock != NULL) {
    g_clear_handle_id(&watch->pending, g_source_remove);
    if (watch->after_paint == 0) {
      watch->clock = g_object_ref(clock);
      watch->after_paint = g_signal_connect(clock, "after-paint", G_CALLBACK(watch_after_paint), watch);
    }
    gdk_frame_clock_request_phase(clock, GDK_FRAME_CLOCK_PHASE_AFTER_PAINT);
  } else if (watch->pending == 0) {
    watch->pending = g_idle_add(watch_idle, watch);
  }
}

static gboolean watch_tick(GtkWidget *widget G_GNUC_UNUSED, GdkFrameClock *clock G_GNUC_UNUSED,
                           gpointer user_data) {
  WidthWatch *watch = user_data;
  watch->waiting = FALSE;
  watch->tick_id = 0;
  g_clear_weak_pointer(&watch->tick_widget);
  watch_schedule(watch);
  return G_SOURCE_REMOVE;
}

static void watch_check(WidthWatch *watch) {
  g_clear_handle_id(&watch->pending, g_source_remove);
  int width = luma_ui_window_width(watch->widget);
  if (width <= 0) {
    GtkWidget *frame_widget = GTK_WIDGET(gtk_widget_get_root(watch->widget));
    if (frame_widget == NULL) frame_widget = watch->widget;
    if (gtk_widget_get_mapped(frame_widget) && !watch->waiting) {
      watch->waiting = TRUE;
      g_set_weak_pointer(&watch->tick_widget, frame_widget);
      watch->tick_id = gtk_widget_add_tick_callback(frame_widget, watch_tick, watch, NULL);
    }
    return;
  }
  int value = watch->threshold > 0 ? (width <= watch->threshold) : width;
  if (!watch->has_last || value != watch->last) {
    watch->has_last = TRUE;
    watch->last = value;
    watch->callback(watch->widget, width, watch->data);
  }
}

static void watch_surface_width(GObject *object G_GNUC_UNUSED, GParamSpec *pspec G_GNUC_UNUSED,
                                gpointer user_data) {
  watch_schedule(user_data);
}

static void watch_unrealized(GtkWidget *widget G_GNUC_UNUSED, gpointer user_data) {
  WidthWatch *watch = user_data;
  if (watch->surface != NULL && watch->surface_handler != 0)
    g_signal_handler_disconnect(watch->surface, watch->surface_handler);
  g_clear_weak_pointer(&watch->surface);
  watch->surface_handler = 0;
  watch_cancel_frame(watch);
  g_clear_handle_id(&watch->pending, g_source_remove);
}

static void watch_realized(GtkWidget *widget G_GNUC_UNUSED, gpointer user_data) {
  WidthWatch *watch = user_data;
  GtkNative *native = gtk_widget_get_native(watch->widget);
  GdkSurface *surface = native != NULL ? gtk_native_get_surface(native) : NULL;
  if (surface == NULL || surface == watch->surface)
    return;
  watch_unrealized(NULL, watch);
  g_set_weak_pointer(&watch->surface, surface);
  watch->surface_handler = g_signal_connect(surface, "notify::width", G_CALLBACK(watch_surface_width), watch);
  watch_schedule(watch);
}

static void watch_mapped(GtkWidget *widget G_GNUC_UNUSED, gpointer user_data) {
  watch_schedule(user_data);
}

static void watch_disconnect_root(WidthWatch *watch) {
  if (watch->root != NULL)
    for (guint i = 0; i < G_N_ELEMENTS(watch->root_handlers); i++)
      g_clear_signal_handler(&watch->root_handlers[i], watch->root);
  g_clear_weak_pointer(&watch->root);
  if (watch->tick_widget != NULL && watch->tick_id != 0)
    gtk_widget_remove_tick_callback(watch->tick_widget, watch->tick_id);
  watch->tick_id = 0;
  watch->waiting = FALSE;
  g_clear_weak_pointer(&watch->tick_widget);
}

static void watch_rooted(GObject *object G_GNUC_UNUSED, GParamSpec *pspec G_GNUC_UNUSED,
                         gpointer user_data) {
  WidthWatch *watch = user_data;
  GtkWidget *root = GTK_WIDGET(gtk_widget_get_root(watch->widget));
  if (root == watch->root) return;
  watch_unrealized(NULL, watch);
  watch_disconnect_root(watch);
  if (root == NULL) return;
  g_set_weak_pointer(&watch->root, root);
  watch->root_handlers[0] = g_signal_connect(root, "realize", G_CALLBACK(watch_realized), watch);
  watch->root_handlers[1] = g_signal_connect(root, "unrealize", G_CALLBACK(watch_unrealized), watch);
  watch->root_handlers[2] = g_signal_connect(root, "map", G_CALLBACK(watch_mapped), watch);
  if (gtk_widget_get_realized(root)) watch_realized(root, watch);
}

static void watch_free(gpointer user_data) {
  WidthWatch *watch = user_data;
  watch_unrealized(NULL, watch);
  watch_disconnect_root(watch);
  g_clear_handle_id(&watch->pending, g_source_remove);
  g_free(watch);
}

void luma_ui_width_watch(GtkWidget *widget, LumaWidthCallback callback, gpointer data, int threshold) {
  WidthWatch *watch = g_new0(WidthWatch, 1);
  watch->widget = widget;
  watch->callback = callback;
  watch->data = data;
  watch->threshold = threshold;
  GPtrArray *watches = g_object_get_data(G_OBJECT(widget), "luma-ui-width-watches");
  if (watches == NULL) {
    watches = g_ptr_array_new_with_free_func(watch_free);
    g_object_set_data_full(G_OBJECT(widget), "luma-ui-width-watches", watches,
                           (GDestroyNotify)g_ptr_array_unref);
  }
  g_ptr_array_add(watches, watch);
  g_signal_connect(widget, "realize", G_CALLBACK(watch_realized), watch);
  g_signal_connect(widget, "unrealize", G_CALLBACK(watch_unrealized), watch);
  g_signal_connect(widget, "map", G_CALLBACK(watch_mapped), watch);
  g_signal_connect(widget, "notify::root", G_CALLBACK(watch_rooted), watch);
  watch_rooted(NULL, NULL, watch);
}

void luma_ui_width_watch_check(GtkWidget *widget) {
  GPtrArray *watches = g_object_get_data(G_OBJECT(widget), "luma-ui-width-watches");
  for (guint i = 0; watches != NULL && i < watches->len; i++)
    watch_check(g_ptr_array_index(watches, i));
}

/* Tiers (v71) */
G_DEFINE_ENUM_TYPE(LumaTier, luma_tier, G_DEFINE_ENUM_VALUE(LUMA_TIER_REGULAR, "regular"),
                   G_DEFINE_ENUM_VALUE(LUMA_TIER_COMPACT, "compact"), G_DEFINE_ENUM_VALUE(LUMA_TIER_PHONE, "phone"))

LumaTier luma_ui_tier_for_width(int width) {
  if (width > 0 && width < LUMA_TIER_PHONE_BELOW)
    return LUMA_TIER_PHONE;
  if (width > 0 && width <= LUMA_TIER_REGULAR_ABOVE)
    return LUMA_TIER_COMPACT;
  return LUMA_TIER_REGULAR;
}

LumaTier luma_ui_get_tier(GtkWidget *widget) {
  g_return_val_if_fail(GTK_IS_WIDGET(widget), LUMA_TIER_REGULAR);
  return luma_ui_tier_for_width(luma_ui_window_width(widget));
}

struct _LumaWidthWatch {
  GObject parent_instance;
  int width;
  LumaTier tier;
};

G_DEFINE_FINAL_TYPE(LumaWidthWatch, luma_width_watch, G_TYPE_OBJECT)

enum { WW_PROP_0, WW_PROP_WIDTH, WW_PROP_TIER, WW_N_PROPS };
static GParamSpec *width_watch_props[WW_N_PROPS];
enum { WW_TIER_CHANGED, WW_N_SIGNALS };
static guint width_watch_signals[WW_N_SIGNALS];

static void luma_width_watch_get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  LumaWidthWatch *self = LUMA_WIDTH_WATCH(object);
  if (id == WW_PROP_WIDTH)
    g_value_set_int(value, self->width);
  else if (id == WW_PROP_TIER)
    g_value_set_enum(value, self->tier);
  else
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
}

static void luma_width_watch_class_init(LumaWidthWatchClass *klass) {
  G_OBJECT_CLASS(klass)->get_property = luma_width_watch_get_property;
  /**
   * LumaWidthWatch:width:
   *
   * The window's width; 0 before it has one.
   */
  width_watch_props[WW_PROP_WIDTH] = g_param_spec_int("width", NULL, NULL, 0, G_MAXINT, 0,
                                                      G_PARAM_READABLE | G_PARAM_EXPLICIT_NOTIFY |
                                                          G_PARAM_STATIC_STRINGS);
  /**
   * LumaWidthWatch:tier:
   *
   * The window's tier.
   */
  width_watch_props[WW_PROP_TIER] = g_param_spec_enum("tier", NULL, NULL, LUMA_TYPE_TIER, LUMA_TIER_REGULAR,
                                                      G_PARAM_READABLE | G_PARAM_EXPLICIT_NOTIFY |
                                                          G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(G_OBJECT_CLASS(klass), WW_N_PROPS, width_watch_props);
  /**
   * LumaWidthWatch::tier-changed:
   * @self: the watch
   * @tier: the tier the window is in now
   *
   * Emitted after layout when the window first has a width, and whenever it
   * crosses a tier edge.
   */
  width_watch_signals[WW_TIER_CHANGED] = g_signal_new("tier-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                                                      0, NULL, NULL, NULL, G_TYPE_NONE, 1, LUMA_TYPE_TIER);
}

static void luma_width_watch_init(LumaWidthWatch *self) { self->tier = LUMA_TIER_REGULAR; }

static void width_watch_changed(GtkWidget *widget G_GNUC_UNUSED, int width, gpointer data) {
  LumaWidthWatch *self = LUMA_WIDTH_WATCH(data);
  gboolean first = self->width == 0;
  LumaTier tier = luma_ui_tier_for_width(width);
  if (self->width != width) {
    self->width = width;
    g_object_notify_by_pspec(G_OBJECT(self), width_watch_props[WW_PROP_WIDTH]);
  }
  if (tier != self->tier || first) {
    gboolean changed = tier != self->tier;
    self->tier = tier;
    if (changed)
      g_object_notify_by_pspec(G_OBJECT(self), width_watch_props[WW_PROP_TIER]);
    g_signal_emit(self, width_watch_signals[WW_TIER_CHANGED], 0, tier);
  }
}

LumaWidthWatch *luma_width_watch_get(GtkWidget *widget) {
  g_return_val_if_fail(GTK_IS_WIDGET(widget), NULL);
  LumaWidthWatch *self = g_object_get_data(G_OBJECT(widget), "luma-width-watch");
  if (self != NULL)
    return self;
  self = g_object_new(LUMA_TYPE_WIDTH_WATCH, NULL);
  /* The private watches list is freed with the widget's data, after this object would be: keep the
   * object alive as long as the widget, in the same data table (it is dropped first). */
  luma_ui_width_watch(widget, width_watch_changed, self, 0);
  g_object_set_data_full(G_OBJECT(widget), "luma-width-watch", self, g_object_unref);
  return self;
}

LumaTier luma_width_watch_get_tier(LumaWidthWatch *self) {
  g_return_val_if_fail(LUMA_IS_WIDTH_WATCH(self), LUMA_TIER_REGULAR);
  return self->tier;
}

int luma_width_watch_get_width(LumaWidthWatch *self) {
  g_return_val_if_fail(LUMA_IS_WIDTH_WATCH(self), 0);
  return self->width;
}

/* Text helpers */
char *luma_ui_count_text(int count, gboolean attention) {
  if (count <= 0)
    return g_strdup("");
  if (attention && count > LUMA_UI_COUNT_ATTENTION_CAP)
    return g_strdup_printf("%d+", LUMA_UI_COUNT_ATTENTION_CAP);
  g_autofree char *digits = g_strdup_printf("%d", count);
  GString *text = g_string_new(NULL);
  size_t len = strlen(digits);
  for (size_t i = 0; i < len; i++) {
    if (i > 0 && (len - i) % 3 == 0)
      g_string_append(text, LUMA_UI_COUNT_SEPARATOR);
    g_string_append_c(text, digits[i]);
  }
  return g_string_free(text, FALSE);
}

char *luma_ui_initials(const char *name) {
  g_return_val_if_fail(name != NULL, NULL);
  g_autofree char *spaced = g_strdelimit(g_strdup(name), "@", ' ');
  g_auto(GStrv) parts = g_strsplit_set(spaced, " \t\n\r\f\v", -1);
  g_autoptr(GPtrArray) words = g_ptr_array_new();
  for (guint i = 0; parts[i] != NULL; i++)
    if (parts[i][0] != '\0' && g_unichar_isalpha(g_utf8_get_char(parts[i])))
      g_ptr_array_add(words, parts[i]);
  if (words->len == 0)
    return g_strdup("");
  GString *out = g_string_new(NULL);
  const char *first = g_ptr_array_index(words, 0);
  g_string_append_unichar(out, g_unichar_toupper(g_utf8_get_char(first)));
  if (words->len > 1) {
    const char *last = g_ptr_array_index(words, words->len - 1);
    g_string_append_unichar(out, g_unichar_toupper(g_utf8_get_char(last)));
  }
  return g_string_free(out, FALSE);
}

/* zlib's crc32, as Python's zlib.crc32(name.encode()). */
static guint32 luma_crc32(const guchar *data, gsize length) {
  guint32 crc = 0xFFFFFFFFu;
  for (gsize i = 0; i < length; i++) {
    crc ^= data[i];
    for (int k = 0; k < 8; k++)
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return crc ^ 0xFFFFFFFFu;
}

const char *luma_ui_person_tone(const char *name) {
  static const char *const order[] = LUMA_UI_CATEGORY_ORDER;
  g_return_val_if_fail(name != NULL, order[0]);
  return order[luma_crc32((const guchar *)name, strlen(name)) % LUMA_UI_CATEGORY_ORDER_LEN];
}
