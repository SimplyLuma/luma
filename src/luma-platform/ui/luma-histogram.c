/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-histogram.h"

struct _LumaHistogram {
  GtkDrawingArea parent_instance;
  double channels[4][256];
  gboolean clipping_visible;
  int hover_bin;
};

G_DEFINE_FINAL_TYPE(LumaHistogram, luma_histogram, GTK_TYPE_DRAWING_AREA)

enum { PROP_0, PROP_CLIPPING_VISIBLE, N_PROPERTIES };
static GParamSpec *properties[N_PROPERTIES];

static void draw_channel(cairo_t *cr, const double *values, double width,
                         double height, double red, double green, double blue,
                         double alpha) {
  cairo_new_path(cr);
  cairo_move_to(cr, 0.0, height);
  for (guint i = 0; i < 256; i++) {
    const double x = width * i / 255.0;
    const double y = height - (values[i] * (height - 2.0));
    cairo_line_to(cr, x, y);
  }
  cairo_line_to(cr, width, height);
  cairo_close_path(cr);
  cairo_set_source_rgba(cr, red, green, blue, alpha);
  cairo_fill(cr);
}

static void luma_histogram_draw(GtkDrawingArea *area, cairo_t *cr, int width,
                                int height, gpointer user_data) {
  LumaHistogram *self = LUMA_HISTOGRAM(area);
  (void)user_data;
  cairo_set_source_rgba(cr, 0.5, 0.5, 0.5, 0.13);
  for (guint i = 1; i < 4; i++) {
    cairo_move_to(cr, width * i / 4.0, 0);
    cairo_line_to(cr, width * i / 4.0, height);
    cairo_move_to(cr, 0, height * i / 4.0);
    cairo_line_to(cr, width, height * i / 4.0);
  }
  cairo_set_line_width(cr, 1.0);
  cairo_stroke(cr);
  draw_channel(cr, self->channels[0], width, height, 0.68, 0.72, 0.78, 0.28);
  draw_channel(cr, self->channels[1], width, height, 0.94, 0.24, 0.28, 0.34);
  draw_channel(cr, self->channels[2], width, height, 0.20, 0.82, 0.48, 0.30);
  draw_channel(cr, self->channels[3], width, height, 0.20, 0.48, 0.96, 0.34);
  if (self->clipping_visible) {
    const double shadow = MAX(MAX(self->channels[1][0], self->channels[2][0]),
                              self->channels[3][0]);
    const double highlight = MAX(MAX(self->channels[1][255], self->channels[2][255]),
                                 self->channels[3][255]);
    if (shadow > 0.01) {
      cairo_set_source_rgba(cr, 0.25, 0.52, 0.96, 0.95);
      cairo_rectangle(cr, 0, 0, 4, height);
      cairo_fill(cr);
    }
    if (highlight > 0.01) {
      cairo_set_source_rgba(cr, 0.95, 0.28, 0.24, 0.95);
      cairo_rectangle(cr, width - 4, 0, 4, height);
      cairo_fill(cr);
    }
  }
  if (self->hover_bin >= 0) {
    const double x = width * self->hover_bin / 255.0;
    cairo_set_source_rgba(cr, 1, 1, 1, 0.62);
    cairo_move_to(cr, x, 0);
    cairo_line_to(cr, x, height);
    cairo_set_line_width(cr, 1.0);
    cairo_stroke(cr);
  }
}

static void motion(GtkEventControllerMotion *controller, double x, double y,
                   gpointer user_data) {
  LumaHistogram *self = LUMA_HISTOGRAM(user_data);
  const int width = gtk_widget_get_width(GTK_WIDGET(self));
  (void)controller;
  (void)y;
  self->hover_bin = width > 0 ? CLAMP((int)(x * 255.0 / width + 0.5), 0, 255) : -1;
  g_autofree char *description = g_strdup_printf(
      "Level %d; luminance %.0f percent; red %.0f; green %.0f; blue %.0f",
      self->hover_bin, self->channels[0][self->hover_bin] * 100.0,
      self->channels[1][self->hover_bin] * 100.0,
      self->channels[2][self->hover_bin] * 100.0,
      self->channels[3][self->hover_bin] * 100.0);
  gtk_widget_set_tooltip_text(GTK_WIDGET(self), description);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void leave(GtkEventControllerMotion *controller, gpointer user_data) {
  LumaHistogram *self = LUMA_HISTOGRAM(user_data);
  (void)controller;
  self->hover_bin = -1;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void luma_histogram_set_property(GObject *object, guint property_id,
                                        const GValue *value, GParamSpec *pspec) {
  LumaHistogram *self = LUMA_HISTOGRAM(object);
  if (property_id == PROP_CLIPPING_VISIBLE)
    luma_histogram_set_clipping_visible(self, g_value_get_boolean(value));
  else
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, pspec);
}

static void luma_histogram_get_property(GObject *object, guint property_id,
                                        GValue *value, GParamSpec *pspec) {
  LumaHistogram *self = LUMA_HISTOGRAM(object);
  if (property_id == PROP_CLIPPING_VISIBLE)
    g_value_set_boolean(value, self->clipping_visible);
  else
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, pspec);
}

static void luma_histogram_class_init(LumaHistogramClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->set_property = luma_histogram_set_property;
  object_class->get_property = luma_histogram_get_property;
  properties[PROP_CLIPPING_VISIBLE] = g_param_spec_boolean(
      "clipping-visible", "Clipping visible", "Show shadow and highlight clipping",
      FALSE, G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, N_PROPERTIES, properties);
}

static void luma_histogram_init(LumaHistogram *self) {
  GtkEventController *controller = gtk_event_controller_motion_new();
  self->hover_bin = -1;
  gtk_drawing_area_set_content_height(GTK_DRAWING_AREA(self), 116);
  gtk_drawing_area_set_draw_func(GTK_DRAWING_AREA(self), luma_histogram_draw,
                                 NULL, NULL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-histogram");
  gtk_accessible_update_property(GTK_ACCESSIBLE(self),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL,
                                 "Image histogram", -1);
  g_signal_connect(controller, "motion", G_CALLBACK(motion), self);
  g_signal_connect(controller, "leave", G_CALLBACK(leave), self);
  gtk_widget_add_controller(GTK_WIDGET(self), controller);
}

GtkWidget *luma_histogram_new(void) {
  return g_object_new(LUMA_TYPE_HISTOGRAM, NULL);
}

void luma_histogram_set_channels(LumaHistogram *self, GVariant *channels) {
  g_return_if_fail(LUMA_IS_HISTOGRAM(self));
  g_return_if_fail(channels != NULL && g_variant_is_of_type(channels, G_VARIANT_TYPE("aad")));
  g_return_if_fail(g_variant_n_children(channels) == 4);
  for (guint channel = 0; channel < 4; channel++) {
    g_autoptr(GVariant) values = g_variant_get_child_value(channels, channel);
    const gsize count = MIN((gsize)256, g_variant_n_children(values));
    double peak = 1.0;
    for (gsize i = 0; i < count; i++) {
      g_variant_get_child(values, i, "d", &self->channels[channel][i]);
      peak = MAX(peak, self->channels[channel][i]);
    }
    for (guint i = 0; i < 256; i++)
      self->channels[channel][i] = self->channels[channel][i] / peak;
  }
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

GVariant *luma_histogram_get_channels(LumaHistogram *self) {
  GVariantBuilder outer;
  g_return_val_if_fail(LUMA_IS_HISTOGRAM(self), NULL);
  g_variant_builder_init(&outer, G_VARIANT_TYPE("aad"));
  for (guint channel = 0; channel < 4; channel++) {
    GVariantBuilder inner;
    g_variant_builder_init(&inner, G_VARIANT_TYPE("ad"));
    for (guint i = 0; i < 256; i++)
      g_variant_builder_add(&inner, "d", self->channels[channel][i]);
    g_variant_builder_add_value(&outer, g_variant_builder_end(&inner));
  }
  return g_variant_ref_sink(g_variant_builder_end(&outer));
}

void luma_histogram_set_clipping_visible(LumaHistogram *self, gboolean visible) {
  g_return_if_fail(LUMA_IS_HISTOGRAM(self));
  visible = !!visible;
  if (self->clipping_visible == visible)
    return;
  self->clipping_visible = visible;
  g_object_notify_by_pspec(G_OBJECT(self), properties[PROP_CLIPPING_VISIBLE]);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

gboolean luma_histogram_get_clipping_visible(LumaHistogram *self) {
  g_return_val_if_fail(LUMA_IS_HISTOGRAM(self), FALSE);
  return self->clipping_visible;
}

