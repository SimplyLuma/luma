/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-media-fader.h"

#include <math.h>

struct _LumaMediaFader {
  GtkScale parent_instance;
  double reset_db;
  gboolean supplied_adjustment;
};

G_DEFINE_FINAL_TYPE(LumaMediaFader, luma_media_fader, GTK_TYPE_SCALE)

enum { PROP_ADJUSTMENT = 1 };

static void luma_media_fader_set_property(GObject *object, guint property_id,
                                        const GValue *value, GParamSpec *pspec) {
  LumaMediaFader *self = LUMA_MEDIA_FADER(object);
  if (property_id == PROP_ADJUSTMENT) {
    GtkAdjustment *adjustment = g_value_get_object(value);
    self->supplied_adjustment = adjustment != NULL;
    gtk_range_set_adjustment(GTK_RANGE(self), adjustment);
  } else {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, pspec);
  }
}

static void luma_media_fader_get_property(GObject *object, guint property_id,
                                        GValue *value, GParamSpec *pspec) {
  if (property_id == PROP_ADJUSTMENT)
    g_value_set_object(value, gtk_range_get_adjustment(GTK_RANGE(object)));
  else
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, pspec);
}

static void reset_pressed(GtkGestureClick *gesture, int n_press, double x,
                          double y, gpointer user_data) {
  LumaMediaFader *self = LUMA_MEDIA_FADER(user_data);
  (void)gesture;
  (void)x;
  (void)y;
  if (n_press == 2)
    gtk_range_set_value(GTK_RANGE(self), self->reset_db);
}

static char *format_db(GtkScale *scale, double value, gpointer user_data) {
  (void)scale;
  (void)user_data;
  if (value <= -59.95)
    return g_strdup("−∞ dB");
  return g_strdup_printf("%+.1f dB", value);
}

static void luma_media_fader_constructed(GObject *object) {
  LumaMediaFader *self = LUMA_MEDIA_FADER(object);
  G_OBJECT_CLASS(luma_media_fader_parent_class)->constructed(object);
  /* GtkRange owns the adjustment. Configure it after construct properties:
   * setting a floating model in init would be replaced by the default NULL
   * adjustment property, and unreffing that model would free GtkRange's ref. */
  if (!self->supplied_adjustment)
    gtk_adjustment_configure(gtk_range_get_adjustment(GTK_RANGE(object)),
                             0.0, -60.0, 12.0, 0.1, 3.0, 0.0);
  gtk_scale_set_draw_value(GTK_SCALE(self), FALSE);
  gtk_scale_add_mark(GTK_SCALE(self), -60.0, GTK_POS_BOTTOM, "−∞");
  gtk_scale_add_mark(GTK_SCALE(self), 0.0, GTK_POS_BOTTOM, "0");
  gtk_scale_add_mark(GTK_SCALE(self), 12.0, GTK_POS_BOTTOM, "+12");
  gtk_scale_set_format_value_func(GTK_SCALE(self), format_db, NULL, NULL);
}

static void luma_media_fader_class_init(LumaMediaFaderClass *klass) {
  G_OBJECT_CLASS(klass)->constructed = luma_media_fader_constructed;
  G_OBJECT_CLASS(klass)->set_property = luma_media_fader_set_property;
  G_OBJECT_CLASS(klass)->get_property = luma_media_fader_get_property;
  g_object_class_override_property(G_OBJECT_CLASS(klass), PROP_ADJUSTMENT,
                                   "adjustment");
}

static void luma_media_fader_init(LumaMediaFader *self) {
  GtkGesture *gesture;
  self->reset_db = 0.0;
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-media-fader");
  gtk_widget_set_tooltip_text(GTK_WIDGET(self),
                              "Level in decibels; double-click to reset");
  gtk_accessible_update_property(GTK_ACCESSIBLE(self),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL, "Level", -1);
  gesture = gtk_gesture_click_new();
  gtk_gesture_single_set_button(GTK_GESTURE_SINGLE(gesture), GDK_BUTTON_PRIMARY);
  g_signal_connect(gesture, "pressed", G_CALLBACK(reset_pressed), self);
  gtk_widget_add_controller(GTK_WIDGET(self), GTK_EVENT_CONTROLLER(gesture));
}

GtkWidget *luma_media_fader_new(GtkOrientation orientation) {
  return g_object_new(LUMA_TYPE_MEDIA_FADER, "orientation", orientation, NULL);
}

void luma_media_fader_set_db(LumaMediaFader *self, double db) {
  g_return_if_fail(LUMA_IS_MEDIA_FADER(self));
  gtk_range_set_value(GTK_RANGE(self), CLAMP(db, -60.0, 12.0));
}

double luma_media_fader_get_db(LumaMediaFader *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_FADER(self), 0.0);
  return gtk_range_get_value(GTK_RANGE(self));
}

void luma_media_fader_set_reset_db(LumaMediaFader *self, double db) {
  g_return_if_fail(LUMA_IS_MEDIA_FADER(self));
  self->reset_db = CLAMP(db, -60.0, 12.0);
}

double luma_media_fader_get_reset_db(LumaMediaFader *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_FADER(self), 0.0);
  return self->reset_db;
}

double luma_media_fader_db_to_amplitude(double db) {
  if (db <= -60.0)
    return 0.0;
  return pow(10.0, CLAMP(db, -60.0, 12.0) / 20.0);
}
