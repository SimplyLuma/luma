/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-audio-meter.h"
#include "luma-ui-private.h"
#include "luma-ui-tokens-private.h"

#include <math.h>

/* Ballistics: the bars fall at 24 dB/s; the peak tick holds 1.5 s, then falls. */
#define FALL_DB_PER_S 24.0
#define HOLD_US 1500000
#define FLOOR_DB -60.0

struct _LumaAudioMeter {
  GtkBox parent_instance;
  GtkWidget *nodes[5];
  guint channels;
  gboolean clipped;
  gboolean horizontal;
  int accessible_bucket;
  double target[2];
  double shown[2];
  double peak[2];
  gint64 peak_at[2];
  gint64 last_frame;
  guint tick;
};

enum { NODE_TRACK, NODE_LOW, NODE_MID, NODE_HIGH, NODE_PEAK };
static const char *node_names[] = {"lumaui-meter-track", "lumaui-meter-low", "lumaui-meter-mid", "lumaui-meter-high",
                                   "lumaui-meter-peak"};

G_DEFINE_FINAL_TYPE(LumaAudioMeter, luma_audio_meter, GTK_TYPE_BOX)

static gboolean settled(LumaAudioMeter *self) {
  for (guint i = 0; i < self->channels; i++)
    if (self->shown[i] > self->target[i] + 0.01 || self->peak[i] > self->shown[i] + 0.01)
      return FALSE;
  return TRUE;
}

/* Animate only while something falls; at rest the meter costs nothing. */
static gboolean meter_tick(GtkWidget *widget, GdkFrameClock *clock, gpointer data G_GNUC_UNUSED) {
  LumaAudioMeter *self = LUMA_AUDIO_METER(widget);
  gint64 now = gdk_frame_clock_get_frame_time(clock);
  double dt = self->last_frame > 0 ? (now - self->last_frame) / 1e6 : 0;
  self->last_frame = now;
  for (guint i = 0; i < 2; i++) {
    self->shown[i] = MAX(self->target[i], self->shown[i] - FALL_DB_PER_S * dt);
    if (now - self->peak_at[i] > HOLD_US)
      self->peak[i] = MAX(self->shown[i], self->peak[i] - FALL_DB_PER_S * dt);
  }
  gtk_widget_queue_draw(widget);
  if (settled(self)) {
    self->tick = 0;
    self->last_frame = 0;
    return G_SOURCE_REMOVE;
  }
  return G_SOURCE_CONTINUE;
}

static void luma_audio_meter_measure(GtkWidget *widget, GtkOrientation orientation, int for_size G_GNUC_UNUSED,
                                     int *minimum, int *natural, int *minimum_baseline G_GNUC_UNUSED,
                                     int *natural_baseline G_GNUC_UNUSED) {
  LumaAudioMeter *self = LUMA_AUDIO_METER(widget);
  gboolean across = (orientation == GTK_ORIENTATION_VERTICAL) == self->horizontal;
  if (across) {
    *minimum = *natural = (int)(self->channels * LUMA_UI_METER_BAR + (self->channels - 1) * LUMA_UI_METER_GAP);
  } else {
    *minimum = LUMA_UI_METER_MIN_LENGTH;
    *natural = LUMA_UI_METER_LENGTH;
  }
}

static void luma_audio_meter_size_allocate(GtkWidget *widget, int width G_GNUC_UNUSED, int height G_GNUC_UNUSED,
                                           int baseline G_GNUC_UNUSED) {
  LumaAudioMeter *self = LUMA_AUDIO_METER(widget);
  for (guint i = 0; i < G_N_ELEMENTS(self->nodes); i++) {
    int min, nat;
    gtk_widget_measure(self->nodes[i], GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
    gtk_widget_size_allocate(self->nodes[i], &(GtkAllocation){0, 0, 0, 0}, -1);
  }
}

/* One channel's bar: the track, then the zones up to the level, then the peak tick. */
static void draw_bar(LumaAudioMeter *self, GtkSnapshot *snapshot, const GdkRGBA *colors, float along, float across,
                     float offset, double level, double peak) {
  float radius = MIN(across / 2, 2);
  graphene_rect_t whole = self->horizontal ? GRAPHENE_RECT_INIT(0, offset, along, across)
                                           : GRAPHENE_RECT_INIT(offset, 0, across, along);
  GskRoundedRect rounded;
  gsk_rounded_rect_init_from_rect(&rounded, &whole, radius);
  gtk_snapshot_push_rounded_clip(snapshot, &rounded);
  gtk_snapshot_append_color(snapshot, &colors[NODE_TRACK], &whole);
  double fill = luma_audio_meter_db_to_fraction(level);
  double zones[3][2] = {{0, luma_audio_meter_db_to_fraction(LUMA_UI_METER_MID_DB)},
                        {luma_audio_meter_db_to_fraction(LUMA_UI_METER_MID_DB), luma_audio_meter_db_to_fraction(LUMA_UI_METER_HIGH_DB)},
                        {luma_audio_meter_db_to_fraction(LUMA_UI_METER_HIGH_DB), 1}};
  for (guint z = 0; z < 3; z++) {
    double a = zones[z][0], b = MIN(zones[z][1], fill);
    if (b <= a)
      continue;
    graphene_rect_t part = self->horizontal
                               ? GRAPHENE_RECT_INIT((float)(a * along), offset, (float)((b - a) * along), across)
                               : GRAPHENE_RECT_INIT(offset, (float)((1 - b) * along), across, (float)((b - a) * along));
    gtk_snapshot_append_color(snapshot, &colors[NODE_LOW + z], &part);
  }
  double tick = luma_audio_meter_db_to_fraction(peak);
  if (peak > FLOOR_DB + 0.5) {
    float t = 2;
    graphene_rect_t mark = self->horizontal ? GRAPHENE_RECT_INIT((float)(tick * along) - t, offset, t, across)
                                            : GRAPHENE_RECT_INIT(offset, (float)((1 - tick) * along), across, t);
    gtk_snapshot_append_color(snapshot, &colors[peak >= LUMA_UI_METER_HIGH_DB ? NODE_HIGH : NODE_PEAK], &mark);
  }
  gtk_snapshot_pop(snapshot);
}

static void luma_audio_meter_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaAudioMeter *self = LUMA_AUDIO_METER(widget);
  GdkRGBA colors[G_N_ELEMENTS(self->nodes)];
  for (guint i = 0; i < G_N_ELEMENTS(self->nodes); i++)
    gtk_widget_get_color(self->nodes[i], &colors[i]);
  float width = gtk_widget_get_width(widget), height = gtk_widget_get_height(widget);
  float along = self->horizontal ? width : height;
  float span = self->horizontal ? height : width;
  float bar = self->channels == 1 ? span : (span - LUMA_UI_METER_GAP) / 2;
  for (guint i = 0; i < self->channels; i++)
    draw_bar(self, snapshot, colors, along, bar, i * (bar + LUMA_UI_METER_GAP), self->shown[i], self->peak[i]);
}

static void luma_audio_meter_dispose(GObject *object) {
  LumaAudioMeter *self = LUMA_AUDIO_METER(object);
  if (self->tick != 0) {
    gtk_widget_remove_tick_callback(GTK_WIDGET(self), self->tick);
    self->tick = 0;
  }
  for (guint i = 0; i < G_N_ELEMENTS(self->nodes); i++)
    g_clear_pointer(&self->nodes[i], gtk_widget_unparent);
  G_OBJECT_CLASS(luma_audio_meter_parent_class)->dispose(object);
}

static void luma_audio_meter_class_init(LumaAudioMeterClass *klass) {
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  G_OBJECT_CLASS(klass)->dispose = luma_audio_meter_dispose;
  widget_class->measure = luma_audio_meter_measure;
  widget_class->size_allocate = luma_audio_meter_size_allocate;
  widget_class->snapshot = luma_audio_meter_snapshot;
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_METER);
}

static void luma_audio_meter_init(LumaAudioMeter *self) {
  luma_ui_install();
  /* The meter measures and draws itself, not as a box of children. */
  gtk_widget_set_layout_manager(GTK_WIDGET(self), NULL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-audio-meter");
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_LABEL, "Audio level",
                                 GTK_ACCESSIBLE_PROPERTY_VALUE_MIN, FLOOR_DB, GTK_ACCESSIBLE_PROPERTY_VALUE_MAX, 12.0,
                                 -1);
  for (guint i = 0; i < G_N_ELEMENTS(self->nodes); i++) {
    self->nodes[i] = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(self->nodes[i], node_names[i]);
    gtk_widget_set_child_visible(self->nodes[i], FALSE);
    gtk_widget_set_can_target(self->nodes[i], FALSE);
    gtk_accessible_update_state(GTK_ACCESSIBLE(self->nodes[i]), GTK_ACCESSIBLE_STATE_HIDDEN, TRUE, -1);
    gtk_widget_set_parent(self->nodes[i], GTK_WIDGET(self));
  }
  self->channels = 2;
  self->accessible_bucket = G_MININT;
  for (guint i = 0; i < 2; i++)
    self->target[i] = self->shown[i] = self->peak[i] = FLOOR_DB;
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
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

void luma_audio_meter_set_horizontal(LumaAudioMeter *self, gboolean horizontal) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  self->horizontal = !!horizontal;
  luma_ui_set_css_class(GTK_WIDGET(self), "horizontal", self->horizontal);
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

gboolean luma_audio_meter_get_horizontal(LumaAudioMeter *self) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), FALSE);
  return self->horizontal;
}

double luma_audio_meter_get_level_db(LumaAudioMeter *self, guint channel) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), FLOOR_DB);
  g_return_val_if_fail(channel < 2, FLOOR_DB);
  return self->shown[channel];
}

double luma_audio_meter_get_peak_db(LumaAudioMeter *self, guint channel) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), FLOOR_DB);
  g_return_val_if_fail(channel < 2, FLOOR_DB);
  return self->peak[channel];
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
  double levels[2] = {CLAMP(left_db, FLOOR_DB, 12.0), CLAMP(self->channels == 1 ? left_db : right_db, FLOOR_DB, 12.0)};
  const double peak_db = self->channels == 1 ? levels[0] : MAX(levels[0], levels[1]);
  const int bucket = (int)floor(peak_db / 6.0);
  gint64 now = g_get_monotonic_time();
  for (guint i = 0; i < 2; i++) {
    self->target[i] = levels[i];
    /* Rises show at once; falls follow the ballistics. */
    if (levels[i] >= self->shown[i] || luma_ui_reduced_motion())
      self->shown[i] = levels[i];
    if (levels[i] >= self->peak[i] || luma_ui_reduced_motion()) {
      self->peak[i] = levels[i];
      self->peak_at[i] = now;
    }
  }
  if (peak_db >= 0.0 && !self->clipped)
    luma_audio_meter_set_clipped(self, TRUE);
  if (bucket != self->accessible_bucket) {
    g_autofree char *summary = g_strdup_printf(
        self->channels == 1 ? "Audio level %.0f decibels"
                            : "Audio level left %.0f, right %.0f decibels",
        levels[0], levels[1]);
    gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, summary,
                                   GTK_ACCESSIBLE_PROPERTY_VALUE_NOW, peak_db, -1);
    self->accessible_bucket = bucket;
  }
  gtk_widget_queue_draw(GTK_WIDGET(self));
  if (self->tick == 0 && !settled(self))
    self->tick = gtk_widget_add_tick_callback(GTK_WIDGET(self), meter_tick, NULL, NULL);
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
