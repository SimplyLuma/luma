/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-audio-meter.h"

#include <math.h>

struct _LumaAudioMeter {
  GtkBox parent_instance;
  GtkWidget *left;
  GtkWidget *right;
  guint channels;
  gboolean clipped;
  int accessible_bucket;
};

G_DEFINE_FINAL_TYPE(LumaAudioMeter, luma_audio_meter, GTK_TYPE_BOX)

static void configure_bar(GtkWidget *bar) {
  gtk_level_bar_set_min_value(GTK_LEVEL_BAR(bar), 0.0);
  gtk_level_bar_set_max_value(GTK_LEVEL_BAR(bar), 1.0);
  gtk_level_bar_add_offset_value(GTK_LEVEL_BAR(bar), "nominal", 0.75);
  gtk_level_bar_add_offset_value(GTK_LEVEL_BAR(bar), "warning", 0.875);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(bar), GTK_ORIENTATION_VERTICAL);
  gtk_widget_set_vexpand(bar, TRUE);
}

static void luma_audio_meter_class_init(LumaAudioMeterClass *klass) {
  (void)klass;
}

static void luma_audio_meter_init(LumaAudioMeter *self) {
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_box_set_spacing(GTK_BOX(self), 2);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-audio-meter");
  gtk_accessible_update_property(GTK_ACCESSIBLE(self),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL, "Audio level", -1);
  self->left = gtk_level_bar_new();
  self->right = gtk_level_bar_new();
  configure_bar(self->left);
  configure_bar(self->right);
  gtk_box_append(GTK_BOX(self), self->left);
  gtk_box_append(GTK_BOX(self), self->right);
  self->channels = 2;
  self->accessible_bucket = -1;
}

GtkWidget *luma_audio_meter_new(guint channels) {
  GtkWidget *meter = g_object_new(LUMA_TYPE_AUDIO_METER, NULL);
  luma_audio_meter_set_channels(LUMA_AUDIO_METER(meter), channels);
  return meter;
}

void luma_audio_meter_set_channels(LumaAudioMeter *self, guint channels) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  g_return_if_fail(channels == 1 || channels == 2);
  self->channels = channels;
  gtk_widget_set_visible(self->right, channels == 2);
}

guint luma_audio_meter_get_channels(LumaAudioMeter *self) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), 0);
  return self->channels;
}

double luma_audio_meter_db_to_fraction(double db) {
  return CLAMP((db + 60.0) / 72.0, 0.0, 1.0);
}

void luma_audio_meter_set_levels_db(LumaAudioMeter *self, double left_db,
                                    double right_db) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  const double peak_db = self->channels == 1 ? left_db : MAX(left_db, right_db);
  const int bucket = (int)(CLAMP(peak_db, -60.0, 12.0) / 6.0);
  gtk_level_bar_set_value(GTK_LEVEL_BAR(self->left),
                          luma_audio_meter_db_to_fraction(left_db));
  gtk_level_bar_set_value(GTK_LEVEL_BAR(self->right),
                          luma_audio_meter_db_to_fraction(right_db));
  if (peak_db >= 0.0 && !self->clipped) {
    self->clipped = TRUE;
    gtk_widget_add_css_class(GTK_WIDGET(self), "clipping");
  }
  if (bucket != self->accessible_bucket) {
    g_autofree char *summary = g_strdup_printf(
        self->channels == 1 ? "Audio level %.0f decibels"
                            : "Audio level left %.0f, right %.0f decibels",
        left_db, right_db);
    gtk_accessible_update_property(GTK_ACCESSIBLE(self),
                                   GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, summary, -1);
    self->accessible_bucket = bucket;
  }
}

void luma_audio_meter_set_levels(LumaAudioMeter *self, double left,
                                 double right) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  const double left_db = left <= 0.0 ? -60.0 : 20.0 * log10(CLAMP(left, 0.0, 3.981071706));
  const double right_db =
      right <= 0.0 ? -60.0 : 20.0 * log10(CLAMP(right, 0.0, 3.981071706));
  luma_audio_meter_set_levels_db(self, left_db, right_db);
}

void luma_audio_meter_reset_clip(LumaAudioMeter *self) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  luma_audio_meter_set_clipped(self, FALSE);
}

void luma_audio_meter_set_clipped(LumaAudioMeter *self, gboolean clipped) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  self->clipped = clipped;
  if (clipped)
    gtk_widget_add_css_class(GTK_WIDGET(self), "clipping");
  else
    gtk_widget_remove_css_class(GTK_WIDGET(self), "clipping");
}

gboolean luma_audio_meter_get_clipped(LumaAudioMeter *self) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), FALSE);
  return self->clipped;
}
