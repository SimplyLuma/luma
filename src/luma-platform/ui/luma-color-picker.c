/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI creative family: LumaColorPicker (a colour, picked by hand). */
#include "luma-color-picker.h"
#include "luma-creative-private.h"

#include <math.h>
#include <string.h>

enum { SIG_CHANGED, SIG_COMMITTED, N_SIGNALS };
static guint signals[N_SIGNALS];

/* Luma's palette: the mark hues (blue … violet) and the neutrals. */
static const char *const luma_palette[] = {"60AAF3", "00BCC5", "62BB78", "BEA333", "E28D4F", "EB827B", "E181B0",
                                           "B191EA", "9D98F2", "FFFFFF", "8E9196", "14161A"};

struct _LumaColorPicker {
  GtkBox parent_instance;
  GtkWidget *area, *hue, *alpha;
  GtkWidget *luma, *document, *document_heading, *recent, *recent_heading;
  GtkWidget *eyedropper, *hex;
  GdkRGBA rgba;
  double h, s, v; /* the master for the planes: hue survives grey */
  gboolean with_alpha;
  gboolean dirty;   /* changed since the last commit */
  gboolean picking; /* the portal is asked */
  double press_x, press_y; /* where the drag on a plane began */
};

G_DEFINE_FINAL_TYPE(LumaColorPicker, luma_color_picker, GTK_TYPE_BOX)

/* ── Colour ── */

static void hsv_to_rgb(double h, double s, double v, double *r, double *g, double *b) {
  h = fmod(h, 360);
  if (h < 0)
    h += 360;
  double c = v * s, x = c * (1 - fabs(fmod(h / 60, 2) - 1)), m = v - c;
  double rgb[6][3] = {{c, x, 0}, {x, c, 0}, {0, c, x}, {0, x, c}, {x, 0, c}, {c, 0, x}};
  int i = CLAMP((int)(h / 60), 0, 5);
  *r = rgb[i][0] + m;
  *g = rgb[i][1] + m;
  *b = rgb[i][2] + m;
}

static void rgb_to_hsv(double r, double g, double b, double *h, double *s, double *v) {
  double max = MAX(r, MAX(g, b)), min = MIN(r, MIN(g, b)), d = max - min;
  *v = max;
  *s = max > 0 ? d / max : 0;
  if (d <= 0)
    return; /* grey: the hue stays where it was */
  if (max == r)
    *h = 60 * fmod((g - b) / d, 6);
  else if (max == g)
    *h = 60 * ((b - r) / d + 2);
  else
    *h = 60 * ((r - g) / d + 4);
  if (*h < 0)
    *h += 360;
}

/* ── Recent colours, per app ── */

static GArray *recent; /* GdkRGBA, newest first */

static char *recent_path(void) {
  GApplication *app = g_application_get_default();
  const char *id = app != NULL ? g_application_get_application_id(app) : NULL;
  if (id == NULL)
    id = g_get_prgname();
  if (id == NULL)
    id = "lumaui";
  return g_build_filename(g_get_user_config_dir(), id, "lumaui.ini", NULL);
}

static void recent_load(void) {
  if (recent != NULL)
    return;
  recent = g_array_new(FALSE, FALSE, sizeof(GdkRGBA));
  g_autofree char *path = recent_path();
  g_autoptr(GKeyFile) file = g_key_file_new();
  if (!g_key_file_load_from_file(file, path, G_KEY_FILE_NONE, NULL))
    return;
  g_auto(GStrv) codes = g_key_file_get_string_list(file, "colours", "recent", NULL, NULL);
  for (guint i = 0; codes != NULL && codes[i] != NULL && recent->len < LUMA_UI_CREATIVE_PICKER_RECENT_MAX; i++) {
    GdkRGBA rgba;
    if (luma_property_color_parse(codes[i], &rgba))
      g_array_append_val(recent, rgba);
  }
}

static void recent_save(void) {
  g_autofree char *path = recent_path();
  g_autofree char *dir = g_path_get_dirname(path);
  g_autoptr(GKeyFile) file = g_key_file_new();
  g_key_file_load_from_file(file, path, G_KEY_FILE_KEEP_COMMENTS, NULL);
  g_autoptr(GPtrArray) codes = g_ptr_array_new_with_free_func(g_free);
  for (guint i = 0; i < recent->len; i++)
    g_ptr_array_add(codes, luma_property_color_format(&g_array_index(recent, GdkRGBA, i)));
  g_key_file_set_string_list(file, "colours", "recent", (const char *const *)codes->pdata, codes->len);
  g_autoptr(GError) error = NULL;
  if (g_mkdir_with_parents(dir, 0700) != 0 || !g_key_file_save_to_file(file, path, &error))
    g_debug("LumaColorPicker: recent colours not kept: %s", error != NULL ? error->message : dir);
}

GdkRGBA *luma_color_picker_get_recent_colors(guint *n_colors) {
  g_return_val_if_fail(n_colors != NULL, NULL);
  recent_load();
  *n_colors = recent->len;
  return g_memdup2(recent->data, sizeof(GdkRGBA) * MAX(recent->len, 1));
}

void luma_color_picker_add_recent_color(const GdkRGBA *rgba) {
  g_return_if_fail(rgba != NULL);
  recent_load();
  g_autofree char *code = luma_property_color_format(rgba);
  for (guint i = 0; i < recent->len; i++) {
    g_autofree char *other = luma_property_color_format(&g_array_index(recent, GdkRGBA, i));
    if (g_str_equal(code, other)) {
      g_array_remove_index(recent, i);
      break;
    }
  }
  g_array_prepend_val(recent, *rgba);
  if (recent->len > LUMA_UI_CREATIVE_PICKER_RECENT_MAX)
    g_array_set_size(recent, LUMA_UI_CREATIVE_PICKER_RECENT_MAX);
  recent_save();
}

/* ── State ── */

static void describe(LumaColorPicker *self) {
  g_autofree char *code = luma_property_color_format(&self->rgba);
  g_autofree char *area = g_strdup_printf("Saturation %.0f%%, brightness %.0f%%", self->s * 100, self->v * 100);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->area), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, area, -1);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->hue), GTK_ACCESSIBLE_PROPERTY_VALUE_NOW, round(self->h), -1);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->alpha), GTK_ACCESSIBLE_PROPERTY_VALUE_NOW,
                                 round(self->rgba.alpha * 100), -1);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, code, -1);
}

static void show(LumaColorPicker *self) {
  gtk_widget_queue_draw(self->area);
  gtk_widget_queue_draw(self->hue);
  gtk_widget_queue_draw(self->alpha);
  if (!gtk_widget_has_focus(self->hex)) {
    g_autofree char *code = luma_property_color_format(&self->rgba);
    gtk_editable_set_text(GTK_EDITABLE(self->hex), code);
  }
  describe(self);
}

/* A person's change: show it and say so. */
static void change_hsv(LumaColorPicker *self, double h, double s, double v, double a) {
  self->h = CLAMP(h, 0, 360);
  self->s = CLAMP(s, 0, 1);
  self->v = CLAMP(v, 0, 1);
  double r, g, b;
  hsv_to_rgb(self->h, self->s, self->v, &r, &g, &b);
  GdkRGBA rgba = {(float)r, (float)g, (float)b, self->with_alpha ? (float)CLAMP(a, 0, 1) : 1.0f};
  if (gdk_rgba_equal(&rgba, &self->rgba))
    return;
  self->rgba = rgba;
  self->dirty = TRUE;
  show(self);
  g_signal_emit(self, signals[SIG_CHANGED], 0, &self->rgba);
}

static void change_rgba(LumaColorPicker *self, const GdkRGBA *rgba) {
  GdkRGBA next = *rgba;
  if (!self->with_alpha)
    next.alpha = 1;
  rgb_to_hsv(next.red, next.green, next.blue, &self->h, &self->s, &self->v);
  if (gdk_rgba_equal(&next, &self->rgba))
    return;
  self->rgba = next;
  self->dirty = TRUE;
  show(self);
  g_signal_emit(self, signals[SIG_CHANGED], 0, &self->rgba);
}

/* The person finished: one undo step, and a recent colour. */
static void commit(LumaColorPicker *self) {
  if (!self->dirty)
    return;
  self->dirty = FALSE;
  luma_color_picker_add_recent_color(&self->rgba);
  g_signal_emit(self, signals[SIG_COMMITTED], 0, &self->rgba);
}

/* ── Drawing ── */

static void rounded(cairo_t *cr, double x, double y, double w, double h, double r) {
  r = MIN(r, MIN(w, h) / 2);
  cairo_new_sub_path(cr);
  cairo_arc(cr, x + w - r, y + r, r, -G_PI / 2, 0);
  cairo_arc(cr, x + w - r, y + h - r, r, 0, G_PI / 2);
  cairo_arc(cr, x + r, y + h - r, r, G_PI / 2, G_PI);
  cairo_arc(cr, x + r, y + r, r, G_PI, 3 * G_PI / 2);
  cairo_close_path(cr);
}

static void checkers(cairo_t *cr, double w, double h) {
  cairo_set_source_rgb(cr, 1, 1, 1);
  cairo_paint(cr);
  cairo_set_source_rgb(cr, 0.8, 0.8, 0.8);
  for (int y = 0; y < h; y += 4)
    for (int x = (y / 4) % 2 * 4; x < w; x += 8)
      cairo_rectangle(cr, x, y, 4, 4);
  cairo_fill(cr);
  (void)w;
}

static void knob(cairo_t *cr, double x, double y, const GdkRGBA *fill) {
  double r = LUMA_UI_CREATIVE_PICKER_KNOB / 2.0;
  cairo_arc(cr, x, y, r, 0, 2 * G_PI);
  cairo_set_source_rgba(cr, 0, 0, 0, 0.35);
  cairo_fill(cr);
  cairo_arc(cr, x, y, r - 1, 0, 2 * G_PI);
  cairo_set_source_rgb(cr, 1, 1, 1);
  cairo_fill(cr);
  cairo_arc(cr, x, y, r - 3, 0, 2 * G_PI);
  cairo_set_source_rgba(cr, fill->red, fill->green, fill->blue, 1);
  cairo_fill(cr);
}

/* The strips' track runs between the knob's half widths, so either end is reachable. */
static double strip_inset(void) {
  return LUMA_UI_CREATIVE_PICKER_KNOB / 2.0;
}

static void draw_area(GtkDrawingArea *area G_GNUC_UNUSED, cairo_t *cr, int w, int h, gpointer data) {
  LumaColorPicker *self = data;
  double r, g, b;
  hsv_to_rgb(self->h, 1, 1, &r, &g, &b);
  cairo_save(cr);
  rounded(cr, 0, 0, w, h, LUMA_UI_CREATIVE_PICKER_AREA_RADIUS);
  cairo_clip(cr);
  cairo_set_source_rgb(cr, r, g, b);
  cairo_paint(cr);
  cairo_pattern_t *white = cairo_pattern_create_linear(0, 0, w, 0);
  cairo_pattern_add_color_stop_rgba(white, 0, 1, 1, 1, 1);
  cairo_pattern_add_color_stop_rgba(white, 1, 1, 1, 1, 0);
  cairo_set_source(cr, white);
  cairo_paint(cr);
  cairo_pattern_destroy(white);
  cairo_pattern_t *black = cairo_pattern_create_linear(0, 0, 0, h);
  cairo_pattern_add_color_stop_rgba(black, 0, 0, 0, 0, 0);
  cairo_pattern_add_color_stop_rgba(black, 1, 0, 0, 0, 1);
  cairo_set_source(cr, black);
  cairo_paint(cr);
  cairo_pattern_destroy(black);
  cairo_restore(cr);
  knob(cr, self->s * w, (1 - self->v) * h, &self->rgba);
}

static void strip_shape(cairo_t *cr, int w, int h) {
  double t = LUMA_UI_CREATIVE_PICKER_STRIP;
  rounded(cr, 0, (h - t) / 2, w, t, t / 2);
}

static void draw_hue(GtkDrawingArea *area G_GNUC_UNUSED, cairo_t *cr, int w, int h, gpointer data) {
  LumaColorPicker *self = data;
  double inset = strip_inset();
  cairo_save(cr);
  strip_shape(cr, w, h);
  cairo_clip(cr);
  cairo_pattern_t *hues = cairo_pattern_create_linear(inset, 0, w - inset, 0);
  for (int i = 0; i <= 6; i++) {
    double r, g, b;
    hsv_to_rgb(i * 60, 1, 1, &r, &g, &b);
    cairo_pattern_add_color_stop_rgb(hues, i / 6.0, r, g, b);
  }
  cairo_set_source(cr, hues);
  cairo_paint(cr);
  cairo_pattern_destroy(hues);
  cairo_restore(cr);
  double r, g, b;
  hsv_to_rgb(self->h, 1, 1, &r, &g, &b);
  knob(cr, inset + self->h / 360 * (w - 2 * inset), h / 2.0, &(GdkRGBA){(float)r, (float)g, (float)b, 1});
}

static void draw_alpha(GtkDrawingArea *area G_GNUC_UNUSED, cairo_t *cr, int w, int h, gpointer data) {
  LumaColorPicker *self = data;
  double inset = strip_inset();
  cairo_save(cr);
  strip_shape(cr, w, h);
  cairo_clip(cr);
  checkers(cr, w, h);
  cairo_pattern_t *ramp = cairo_pattern_create_linear(inset, 0, w - inset, 0);
  cairo_pattern_add_color_stop_rgba(ramp, 0, self->rgba.red, self->rgba.green, self->rgba.blue, 0);
  cairo_pattern_add_color_stop_rgba(ramp, 1, self->rgba.red, self->rgba.green, self->rgba.blue, 1);
  cairo_set_source(cr, ramp);
  cairo_paint(cr);
  cairo_pattern_destroy(ramp);
  cairo_restore(cr);
  knob(cr, inset + self->rgba.alpha * (w - 2 * inset), h / 2.0, &self->rgba);
}

static void draw_swatch(GtkDrawingArea *area G_GNUC_UNUSED, cairo_t *cr, int w, int h, gpointer data) {
  const GdkRGBA *rgba = data;
  rounded(cr, 0, 0, w, h, LUMA_UI_CREATIVE_PICKER_SWATCH_RADIUS);
  cairo_clip(cr);
  if (rgba->alpha < 1)
    checkers(cr, w, h);
  gdk_cairo_set_source_rgba(cr, rgba);
  cairo_paint(cr);
  /* A hairline, so white shows on a light surface and black on a dark one. */
  rounded(cr, 0.5, 0.5, w - 1, h - 1, LUMA_UI_CREATIVE_PICKER_SWATCH_RADIUS - 0.5);
  cairo_set_source_rgba(cr, 0.5, 0.5, 0.5, 0.35);
  cairo_set_line_width(cr, 1);
  cairo_stroke(cr);
}

/* ── Pointer ── */

static void point_at(LumaColorPicker *self, GtkWidget *plane, double x, double y) {
  int w = gtk_widget_get_width(plane), h = gtk_widget_get_height(plane);
  if (w <= 0 || h <= 0)
    return;
  if (plane == self->area) {
    change_hsv(self, self->h, x / w, 1 - y / h, self->rgba.alpha);
    return;
  }
  double inset = strip_inset(), t = CLAMP((x - inset) / MAX(w - 2 * inset, 1), 0, 1);
  if (plane == self->hue)
    change_hsv(self, t * 360, self->s, self->v, self->rgba.alpha);
  else
    change_hsv(self, self->h, self->s, self->v, t);
}

static void plane_begin(GtkGestureDrag *drag, double x, double y, gpointer data) {
  LumaColorPicker *self = data;
  GtkWidget *plane = gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(drag));
  gtk_widget_grab_focus(plane);
  self->press_x = x;
  self->press_y = y;
  point_at(self, plane, x, y);
}

static void plane_update(GtkGestureDrag *drag, double dx, double dy, gpointer data) {
  LumaColorPicker *self = data;
  point_at(self, gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(drag)), self->press_x + dx, self->press_y + dy);
}

static void plane_end(GtkGestureDrag *drag, double dx, double dy, gpointer data) {
  plane_update(drag, dx, dy, data);
  commit(data);
}

/* ── Keyboard: arrows step 1% (1° of hue), Shift ten; letting go commits ── */

static gboolean plane_key(GtkEventControllerKey *keys, guint keyval, guint code G_GNUC_UNUSED, GdkModifierType state,
                          gpointer data) {
  LumaColorPicker *self = data;
  GtkWidget *plane = gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(keys));
  double step = (state & GDK_SHIFT_MASK) ? 10 : 1, dx = 0, dy = 0;
  switch (keyval) {
  case GDK_KEY_Left: case GDK_KEY_KP_Left: dx = -step; break;
  case GDK_KEY_Right: case GDK_KEY_KP_Right: dx = step; break;
  case GDK_KEY_Up: case GDK_KEY_KP_Up: dy = step; break;
  case GDK_KEY_Down: case GDK_KEY_KP_Down: dy = -step; break;
  default: return FALSE;
  }
  if (plane == self->area)
    change_hsv(self, self->h, self->s + dx / 100, self->v + dy / 100, self->rgba.alpha);
  else if (plane == self->hue)
    change_hsv(self, self->h + dx + dy, self->s, self->v, self->rgba.alpha);
  else
    change_hsv(self, self->h, self->s, self->v, self->rgba.alpha + (dx + dy) / 100);
  return TRUE;
}

static void plane_key_up(GtkEventControllerKey *keys G_GNUC_UNUSED, guint keyval G_GNUC_UNUSED,
                         guint code G_GNUC_UNUSED, GdkModifierType state G_GNUC_UNUSED, gpointer data) {
  commit(data);
}

static void left_plane(GtkEventControllerFocus *focus G_GNUC_UNUSED, gpointer data) {
  commit(data);
}

static GtkWidget *plane_new(LumaColorPicker *self, const char *css_class, GtkDrawingAreaDrawFunc draw, int height,
                            GtkAccessibleRole role, const char *label) {
  GtkWidget *plane = g_object_new(GTK_TYPE_DRAWING_AREA, "accessible-role", role, "focusable", TRUE,
                                  "hexpand", TRUE, "content-height", height, NULL);
  gtk_widget_add_css_class(plane, css_class);
  gtk_drawing_area_set_draw_func(GTK_DRAWING_AREA(plane), draw, self, NULL);
  luma_ui_set_accessible_label(plane, label);
  gtk_widget_set_cursor_from_name(plane, "crosshair");
  GtkGesture *drag = gtk_gesture_drag_new();
  gtk_gesture_single_set_button(GTK_GESTURE_SINGLE(drag), GDK_BUTTON_PRIMARY);
  g_signal_connect(drag, "drag-begin", G_CALLBACK(plane_begin), self);
  g_signal_connect(drag, "drag-update", G_CALLBACK(plane_update), self);
  g_signal_connect(drag, "drag-end", G_CALLBACK(plane_end), self);
  gtk_widget_add_controller(plane, GTK_EVENT_CONTROLLER(drag));
  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(plane_key), self);
  g_signal_connect(keys, "key-released", G_CALLBACK(plane_key_up), self);
  gtk_widget_add_controller(plane, keys);
  GtkEventController *focus = gtk_event_controller_focus_new();
  g_signal_connect(focus, "leave", G_CALLBACK(left_plane), self);
  gtk_widget_add_controller(plane, focus);
  gtk_box_append(GTK_BOX(self), plane);
  return plane;
}

/* ── Swatches ── */

static void swatch_clicked(GtkButton *button, gpointer data) {
  LumaColorPicker *self = data;
  const GdkRGBA *rgba = g_object_get_data(G_OBJECT(button), "luma-color");
  change_rgba(self, rgba);
  commit(self);
}

static void swatch_add(LumaColorPicker *self, GtkWidget *row, const GdkRGBA *rgba) {
  GtkWidget *button = gtk_button_new();
  gtk_widget_add_css_class(button, "lumaui-color-picker-swatch");
  GdkRGBA *copy = g_memdup2(rgba, sizeof *rgba);
  g_object_set_data_full(G_OBJECT(button), "luma-color", copy, g_free);
  GtkWidget *paint = g_object_new(GTK_TYPE_DRAWING_AREA, "can-target", FALSE, "content-width",
                                  LUMA_UI_CREATIVE_PICKER_SWATCH, "content-height", LUMA_UI_CREATIVE_PICKER_SWATCH,
                                  NULL);
  gtk_drawing_area_set_draw_func(GTK_DRAWING_AREA(paint), draw_swatch, copy, NULL);
  gtk_button_set_child(GTK_BUTTON(button), paint);
  g_autofree char *code = luma_property_color_format(rgba);
  gtk_widget_set_tooltip_text(button, code);
  luma_ui_set_accessible_label(button, code);
  g_signal_connect(button, "clicked", G_CALLBACK(swatch_clicked), self);
  gtk_flow_box_append(GTK_FLOW_BOX(row), button);
}

static GtkWidget *swatch_row(LumaColorPicker *self, const char *kind) {
  GtkWidget *row = g_object_new(GTK_TYPE_FLOW_BOX, "selection-mode", GTK_SELECTION_NONE, "homogeneous", TRUE,
                                "max-children-per-line", 8, "min-children-per-line", 8, "column-spacing",
                                LUMA_UI_CREATIVE_PICKER_SWATCH_GAP, "row-spacing",
                                LUMA_UI_CREATIVE_PICKER_SWATCH_GAP, NULL);
  gtk_widget_add_css_class(row, "lumaui-color-picker-swatches");
  gtk_widget_add_css_class(row, kind);
  gtk_box_append(GTK_BOX(self), row);
  return row;
}

static void swatch_clear(GtkWidget *row) {
  gtk_flow_box_remove_all(GTK_FLOW_BOX(row));
}

static GtkWidget *heading(LumaColorPicker *self, const char *text) {
  GtkWidget *label = gtk_label_new(text);
  gtk_label_set_xalign(GTK_LABEL(label), 0);
  gtk_widget_add_css_class(label, "lumaui-color-picker-heading");
  luma_ui_apply_type(label, "caption");
  gtk_box_append(GTK_BOX(self), label);
  return label;
}

/* The recent colours as they are when the picker shows (they never move under the pointer). */
static void recent_show(LumaColorPicker *self) {
  swatch_clear(self->recent);
  guint n = 0;
  g_autofree GdkRGBA *colors = luma_color_picker_get_recent_colors(&n);
  for (guint i = 0; i < n; i++)
    swatch_add(self, self->recent, &colors[i]);
  gtk_widget_set_visible(self->recent, n > 0);
  gtk_widget_set_visible(self->recent_heading, n > 0);
}

static void mapped(GtkWidget *widget, gpointer data G_GNUC_UNUSED) {
  recent_show(LUMA_COLOR_PICKER(widget));
}

/* ── Hex field ── */

static void hex_commit(LumaColorPicker *self) {
  GdkRGBA parsed;
  if (luma_property_color_parse(gtk_editable_get_text(GTK_EDITABLE(self->hex)), &parsed))
    change_rgba(self, &parsed);
  commit(self);
  g_autofree char *code = luma_property_color_format(&self->rgba);
  gtk_editable_set_text(GTK_EDITABLE(self->hex), code);
}

static void hex_activated(GtkEntry *entry G_GNUC_UNUSED, gpointer data) {
  hex_commit(data);
}

static void hex_left(GtkEventControllerFocus *focus G_GNUC_UNUSED, gpointer data) {
  hex_commit(data);
}

/* ── Eyedropper: org.freedesktop.portal.Screenshot.PickColor ── */

typedef struct {
  LumaColorPicker *self;
  GDBusConnection *bus;
  guint subscription;
} Pick;

static void pick_done(Pick *pick) {
  if (pick->subscription != 0)
    g_dbus_connection_signal_unsubscribe(pick->bus, pick->subscription);
  pick->self->picking = FALSE;
  gtk_widget_set_sensitive(pick->self->eyedropper, TRUE);
  g_object_unref(pick->self);
  g_object_unref(pick->bus);
  g_free(pick);
}

static void pick_unavailable(LumaColorPicker *self) {
  gtk_widget_set_tooltip_text(self->eyedropper, "Picking colours from the screen isn't available");
  gtk_widget_add_css_class(self->eyedropper, "unavailable");
}

static void pick_response(GDBusConnection *bus G_GNUC_UNUSED, const char *sender G_GNUC_UNUSED,
                          const char *path G_GNUC_UNUSED, const char *interface G_GNUC_UNUSED,
                          const char *signal G_GNUC_UNUSED, GVariant *parameters, gpointer data) {
  Pick *pick = data;
  guint32 response = 2;
  g_autoptr(GVariant) results = NULL;
  g_variant_get(parameters, "(u@a{sv})", &response, &results);
  double r, g, b;
  if (response == 0 && g_variant_lookup(results, "color", "(ddd)", &r, &g, &b)) {
    LumaColorPicker *self = pick->self;
    change_rgba(self, &(GdkRGBA){(float)CLAMP(r, 0, 1), (float)CLAMP(g, 0, 1), (float)CLAMP(b, 0, 1),
                                 self->rgba.alpha});
    commit(self);
  }
  pick_done(pick);
}

static void pick_called(GObject *source, GAsyncResult *result, gpointer data) {
  Pick *pick = data;
  g_autoptr(GError) error = NULL;
  g_autoptr(GVariant) reply = g_dbus_connection_call_finish(G_DBUS_CONNECTION(source), result, &error);
  if (reply == NULL) {
    g_debug("LumaColorPicker: PickColor: %s", error->message);
    pick_unavailable(pick->self);
    pick_done(pick);
  }
}

static void bus_ready(GObject *source G_GNUC_UNUSED, GAsyncResult *result, gpointer data) {
  LumaColorPicker *self = data;
  g_autoptr(GError) error = NULL;
  GDBusConnection *bus = g_bus_get_finish(result, &error);
  if (bus == NULL) {
    pick_unavailable(self);
    self->picking = FALSE;
    gtk_widget_set_sensitive(self->eyedropper, TRUE);
    g_object_unref(self);
    return;
  }
  Pick *pick = g_new0(Pick, 1);
  pick->self = self; /* the ref taken when the pick began */
  pick->bus = bus;
  g_autofree char *token = g_strdup_printf("lumaui%u", g_random_int());
  g_autofree char *sender = g_strdup(g_dbus_connection_get_unique_name(bus) + 1);
  g_strdelimit(sender, ".", '_');
  g_autofree char *handle = g_strdup_printf("/org/freedesktop/portal/desktop/request/%s/%s", sender, token);
  pick->subscription = g_dbus_connection_signal_subscribe(bus, "org.freedesktop.portal.Desktop",
                                                          "org.freedesktop.portal.Request", "Response", handle, NULL,
                                                          G_DBUS_SIGNAL_FLAGS_NONE, pick_response, pick, NULL);
  GVariantBuilder options;
  g_variant_builder_init(&options, G_VARIANT_TYPE_VARDICT);
  g_variant_builder_add(&options, "{sv}", "handle_token", g_variant_new_string(token));
  g_dbus_connection_call(bus, "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                         "org.freedesktop.portal.Screenshot", "PickColor", g_variant_new("(sa{sv})", "", &options),
                         G_VARIANT_TYPE("(o)"), G_DBUS_CALL_FLAGS_NONE, -1, NULL, pick_called, pick);
}

void luma_color_picker_pick_from_screen(LumaColorPicker *self) {
  g_return_if_fail(LUMA_IS_COLOR_PICKER(self));
  if (self->picking)
    return;
  self->picking = TRUE;
  gtk_widget_set_sensitive(self->eyedropper, FALSE);
  g_bus_get(G_BUS_TYPE_SESSION, NULL, bus_ready, g_object_ref(self));
}

static void eyedropper_clicked(GtkButton *button G_GNUC_UNUSED, gpointer data) {
  luma_color_picker_pick_from_screen(data);
}

/* ── Object ── */

static void luma_color_picker_class_init(LumaColorPickerClass *klass) {
  gtk_widget_class_set_css_name(GTK_WIDGET_CLASS(klass), "box");
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_GROUP);
  /**
   * LumaColorPicker::color-changed:
   * @self: the picker
   * @rgba: the colour now
   *
   * Every change a person makes, as it happens.
   */
  signals[SIG_CHANGED] = g_signal_new("color-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                                      NULL, G_TYPE_NONE, 1, GDK_TYPE_RGBA);
  /**
   * LumaColorPicker::color-committed:
   * @self: the picker
   * @rgba: the colour picked
   *
   * Once, when the person finishes a change: one undo step.
   */
  signals[SIG_COMMITTED] = g_signal_new("color-committed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                        NULL, NULL, G_TYPE_NONE, 1, GDK_TYPE_RGBA);
}

static void luma_color_picker_init(LumaColorPicker *self) {
  luma_creative_install();
  self->rgba = (GdkRGBA){0, 0, 0, 1};
  self->with_alpha = TRUE;
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_box_set_spacing(GTK_BOX(self), LUMA_UI_CREATIVE_PICKER_GAP);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-color-picker");
  luma_ui_set_accessible_label(GTK_WIDGET(self), "Colour");
  self->area = plane_new(self, "lumaui-color-picker-area", draw_area, LUMA_UI_CREATIVE_PICKER_AREA_HEIGHT,
                         GTK_ACCESSIBLE_ROLE_GROUP, "Saturation and brightness");
  self->hue = plane_new(self, "lumaui-color-picker-hue", draw_hue, LUMA_UI_CREATIVE_PICKER_KNOB,
                        GTK_ACCESSIBLE_ROLE_SLIDER, "Hue");
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->hue), GTK_ACCESSIBLE_PROPERTY_VALUE_MIN, 0.0,
                                 GTK_ACCESSIBLE_PROPERTY_VALUE_MAX, 360.0, -1);
  self->alpha = plane_new(self, "lumaui-color-picker-alpha", draw_alpha, LUMA_UI_CREATIVE_PICKER_KNOB,
                          GTK_ACCESSIBLE_ROLE_SLIDER, "Opacity");
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->alpha), GTK_ACCESSIBLE_PROPERTY_VALUE_MIN, 0.0,
                                 GTK_ACCESSIBLE_PROPERTY_VALUE_MAX, 100.0, -1);
  self->luma = swatch_row(self, "luma");
  for (guint i = 0; i < G_N_ELEMENTS(luma_palette); i++) {
    GdkRGBA rgba;
    luma_property_color_parse(luma_palette[i], &rgba);
    swatch_add(self, self->luma, &rgba);
  }
  self->document_heading = heading(self, "Document");
  self->document = swatch_row(self, "document");
  self->recent_heading = heading(self, "Recent");
  self->recent = swatch_row(self, "recent");
  gtk_widget_set_visible(self->document_heading, FALSE);
  gtk_widget_set_visible(self->document, FALSE);
  recent_show(self);
  GtkWidget *foot = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, LUMA_UI_CREATIVE_PICKER_SWATCH_GAP);
  gtk_widget_add_css_class(foot, "lumaui-color-picker-foot");
  self->eyedropper = luma_ui_icon_button("pipette", "Pick a colour from the screen",
                                         "lumaui-color-picker-eyedropper", FALSE, NULL);
  g_signal_connect(self->eyedropper, "clicked", G_CALLBACK(eyedropper_clicked), self);
  gtk_box_append(GTK_BOX(foot), self->eyedropper);
  self->hex = gtk_entry_new();
  gtk_widget_add_css_class(self->hex, "lumaui-creative-hex");
  luma_ui_apply_type(self->hex, "mono");
  gtk_widget_set_hexpand(self->hex, TRUE);
  gtk_editable_set_width_chars(GTK_EDITABLE(self->hex), 8);
  gtk_entry_set_max_length(GTK_ENTRY(self->hex), 16);
  luma_ui_set_accessible_label(self->hex, "Colour code");
  g_signal_connect(self->hex, "activate", G_CALLBACK(hex_activated), self);
  GtkEventController *focus = gtk_event_controller_focus_new();
  g_signal_connect(focus, "leave", G_CALLBACK(hex_left), self);
  gtk_widget_add_controller(self->hex, focus);
  gtk_box_append(GTK_BOX(foot), self->hex);
  gtk_box_append(GTK_BOX(self), foot);
  g_signal_connect(self, "map", G_CALLBACK(mapped), NULL);
  show(self);
}

GtkWidget *luma_color_picker_new(void) {
  return g_object_new(LUMA_TYPE_COLOR_PICKER, NULL);
}

void luma_color_picker_set_rgba(LumaColorPicker *self, const GdkRGBA *rgba) {
  g_return_if_fail(LUMA_IS_COLOR_PICKER(self));
  g_return_if_fail(rgba != NULL);
  self->rgba = *rgba;
  if (!self->with_alpha)
    self->rgba.alpha = 1;
  rgb_to_hsv(rgba->red, rgba->green, rgba->blue, &self->h, &self->s, &self->v);
  self->dirty = FALSE;
  show(self);
}

void luma_color_picker_get_rgba(LumaColorPicker *self, GdkRGBA *rgba) {
  g_return_if_fail(LUMA_IS_COLOR_PICKER(self));
  g_return_if_fail(rgba != NULL);
  *rgba = self->rgba;
}

void luma_color_picker_set_palette(LumaColorPicker *self, const GdkRGBA *colors, guint n_colors) {
  g_return_if_fail(LUMA_IS_COLOR_PICKER(self));
  g_return_if_fail(colors != NULL || n_colors == 0);
  swatch_clear(self->document);
  for (guint i = 0; i < n_colors; i++)
    swatch_add(self, self->document, &colors[i]);
  gtk_widget_set_visible(self->document, n_colors > 0);
  gtk_widget_set_visible(self->document_heading, n_colors > 0);
}

void luma_color_picker_set_with_alpha(LumaColorPicker *self, gboolean with_alpha) {
  g_return_if_fail(LUMA_IS_COLOR_PICKER(self));
  self->with_alpha = !!with_alpha;
  gtk_widget_set_visible(self->alpha, self->with_alpha);
  if (!self->with_alpha && self->rgba.alpha < 1) {
    self->rgba.alpha = 1;
    show(self);
  }
}
