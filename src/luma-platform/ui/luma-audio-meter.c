/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-audio-meter.h"
#include "luma-ui-private.h"
#include "luma-ui-tokens-private.h"

#include <math.h>

/* Ballistics: the bars fall at 24 dB/s; the peak tick holds 1.5 s, then falls. */
#define FALL_DB_PER_S 24.0
#define HOLD_US 1500000
#define FLOOR_DB -60.0
#define MAX_SEGMENTS 128

struct _LumaAudioMeter {
  GtkBox parent_instance;
  GtkWidget *nodes[5];
  guint channels;
  gboolean clipped;
  gboolean horizontal;
  guint segments;
  /* A segmented bar's own shape, or negative for the tokens'; zones off is an output's meter. */
  double segment_thickness, segment_gap, segment_radius;
  gboolean zones;
  /* What a segmented bar shows now: it redraws only when one of these changes. */
  guint drawn_lit[2];
  int drawn_peak[2];
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

enum { PROP_0, PROP_SEGMENTS, N_PROPS };
static GParamSpec *properties[N_PROPS];

static int peak_segment_at(guint segments, double peak) {
  if (segments == 0 || peak <= FLOOR_DB + 0.5)
    return -1;
  return (int)luma_audio_meter_segments_lit_at(segments, peak) - 1;
}

static double bar_thickness(LumaAudioMeter *self) {
  return self->segments > 0 && self->segment_thickness > 0 ? self->segment_thickness : LUMA_UI_METER_BAR;
}

static double segment_gap(LumaAudioMeter *self) {
  return self->segment_gap >= 0 ? self->segment_gap : LUMA_UI_METER_SEGMENT_GAP;
}

/* A continuous bar redraws on every change; a segmented one when a segment
 * lights, goes out, or the peak segment moves. */
static void queue_redraw(LumaAudioMeter *self) {
  if (self->segments == 0) {
    gtk_widget_queue_draw(GTK_WIDGET(self));
    return;
  }
  gboolean changed = FALSE;
  for (guint i = 0; i < 2; i++) {
    guint lit = luma_audio_meter_segments_lit_at(self->segments, self->shown[i]);
    int peak = self->zones ? peak_segment_at(self->segments, self->peak[i]) : -1;
    if (lit != self->drawn_lit[i] || peak != self->drawn_peak[i]) {
      self->drawn_lit[i] = lit;
      self->drawn_peak[i] = peak;
      changed = TRUE;
    }
  }
  if (changed)
    gtk_widget_queue_draw(GTK_WIDGET(self));
}

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
  queue_redraw(self);
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
    *minimum = *natural = (int)ceil(self->channels * bar_thickness(self) + (self->channels - 1) * LUMA_UI_METER_GAP);
  } else if (self->segments > 0) {
    /* Every segment keeps its shortest length and every gap. */
    int fit = (int)ceil(self->segments * LUMA_UI_METER_SEGMENT_MIN + (self->segments - 1) * segment_gap(self));
    *minimum = MAX(LUMA_UI_METER_MIN_LENGTH, fit);
    *natural = MAX(LUMA_UI_METER_LENGTH, fit);
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
    gtk_snapshot_append_color(snapshot, &colors[self->zones ? NODE_LOW + z : NODE_LOW], &part);
  }
  double tick = luma_audio_meter_db_to_fraction(peak);
  if (peak > FLOOR_DB + 0.5) {
    float t = 2;
    graphene_rect_t mark = self->horizontal ? GRAPHENE_RECT_INIT((float)(tick * along) - t, offset, t, across)
                                            : GRAPHENE_RECT_INIT(offset, (float)((1 - tick) * along), across, t);
    gtk_snapshot_append_color(snapshot, &colors[self->zones && peak >= LUMA_UI_METER_HIGH_DB ? NODE_HIGH : NODE_PEAK], &mark);
  }
  gtk_snapshot_pop(snapshot);
}

/* A segmented bar: N whole-pixel segments a gap apart, lit to the level in
 * the zone colour of each segment's middle, and the peak's segment lit.
 * Without zones every lit segment is the quiet colour and no peak is held.
 * With a segment radius each segment is rounded; else the bar's ends are. */
static void draw_segments(LumaAudioMeter *self, GtkSnapshot *snapshot, const GdkRGBA *colors, float along,
                          float across, float offset, double level, double peak) {
  gboolean each = self->segment_radius >= 0;
  float gap = (float)segment_gap(self);
  GskRoundedRect rounded;
  if (!each) {
    graphene_rect_t whole = self->horizontal ? GRAPHENE_RECT_INIT(0, offset, along, across)
                                             : GRAPHENE_RECT_INIT(offset, 0, across, along);
    gsk_rounded_rect_init_from_rect(&rounded, &whole, MIN(across / 2, 2));
    gtk_snapshot_push_rounded_clip(snapshot, &rounded);
  }
  guint n = self->segments;
  guint lit = luma_audio_meter_segments_lit_at(n, level);
  int peak_index = self->zones ? peak_segment_at(n, peak) : -1;
  double pitch = (along + gap) / n;
  for (guint i = 0; i < n; i++) {
    float start = (float)round(i * pitch);
    float end = (float)round((i + 1) * pitch - gap);
    if (end <= start)
      continue;
    guint node = NODE_TRACK;
    if (i < lit || (int)i == peak_index) {
      double middle = FLOOR_DB + (i + 0.5) / n * 72.0;
      node = !self->zones ? NODE_LOW
             : middle >= LUMA_UI_METER_HIGH_DB ? NODE_HIGH : middle >= LUMA_UI_METER_MID_DB ? NODE_MID : NODE_LOW;
    }
    graphene_rect_t part = self->horizontal ? GRAPHENE_RECT_INIT(start, offset, end - start, across)
                                            : GRAPHENE_RECT_INIT(offset, along - end, across, end - start);
    if (each) {
      gsk_rounded_rect_init_from_rect(&rounded, &part, (float)MIN(self->segment_radius, MIN(end - start, across) / 2));
      gtk_snapshot_push_rounded_clip(snapshot, &rounded);
    }
    gtk_snapshot_append_color(snapshot, &colors[node], &part);
    if (each)
      gtk_snapshot_pop(snapshot);
  }
  if (!each)
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
  for (guint i = 0; i < self->channels; i++) {
    float offset = i * (bar + LUMA_UI_METER_GAP);
    if (self->segments > 0)
      draw_segments(self, snapshot, colors, along, bar, offset, self->shown[i], self->peak[i]);
    else
      draw_bar(self, snapshot, colors, along, bar, offset, self->shown[i], self->peak[i]);
  }
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

static void luma_audio_meter_get_property(GObject *object, guint prop_id, GValue *value, GParamSpec *pspec) {
  LumaAudioMeter *self = LUMA_AUDIO_METER(object);
  switch (prop_id) {
  case PROP_SEGMENTS:
    g_value_set_uint(value, self->segments);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
  }
}

static void luma_audio_meter_set_property(GObject *object, guint prop_id, const GValue *value, GParamSpec *pspec) {
  LumaAudioMeter *self = LUMA_AUDIO_METER(object);
  switch (prop_id) {
  case PROP_SEGMENTS:
    luma_audio_meter_set_segments(self, g_value_get_uint(value));
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
  }
}

static void luma_audio_meter_class_init(LumaAudioMeterClass *klass) {
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->dispose = luma_audio_meter_dispose;
  object_class->get_property = luma_audio_meter_get_property;
  object_class->set_property = luma_audio_meter_set_property;
  /**
   * LumaAudioMeter:segments:
   *
   * 0 draws a continuous bar; N draws N segments per bar.
   */
  properties[PROP_SEGMENTS] = g_param_spec_uint("segments", NULL, NULL, 0, MAX_SEGMENTS, 0,
                                                G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, N_PROPS, properties);
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
  self->segment_thickness = self->segment_gap = self->segment_radius = -1;
  self->zones = TRUE;
  self->accessible_bucket = G_MININT;
  for (guint i = 0; i < 2; i++) {
    self->target[i] = self->shown[i] = self->peak[i] = FLOOR_DB;
    self->drawn_peak[i] = -1;
  }
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

void luma_audio_meter_set_segments(LumaAudioMeter *self, guint segments) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  g_return_if_fail(segments <= MAX_SEGMENTS);
  if (self->segments == segments)
    return;
  self->segments = segments;
  for (guint i = 0; i < 2; i++) {
    self->drawn_lit[i] = luma_audio_meter_segments_lit_at(segments, self->shown[i]);
    self->drawn_peak[i] = peak_segment_at(segments, self->peak[i]);
  }
  luma_ui_set_css_class(GTK_WIDGET(self), "segmented", segments > 0);
  gtk_widget_queue_resize(GTK_WIDGET(self));
  g_object_notify_by_pspec(G_OBJECT(self), properties[PROP_SEGMENTS]);
}

guint luma_audio_meter_get_segments(LumaAudioMeter *self) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), 0);
  return self->segments;
}

void luma_audio_meter_set_segment_shape(LumaAudioMeter *self, double thickness, double gap, double radius) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  thickness = thickness > 0 ? thickness : -1;
  gap = gap >= 0 ? gap : -1;
  radius = radius >= 0 ? radius : -1;
  if (self->segment_thickness == thickness && self->segment_gap == gap && self->segment_radius == radius)
    return;
  self->segment_thickness = thickness;
  self->segment_gap = gap;
  self->segment_radius = radius;
  gtk_widget_queue_resize(GTK_WIDGET(self));
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_audio_meter_get_segment_shape(LumaAudioMeter *self, double *thickness, double *gap, double *radius) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  if (thickness)
    *thickness = bar_thickness(self);
  if (gap)
    *gap = segment_gap(self);
  if (radius)
    *radius = self->segment_radius;
}

void luma_audio_meter_set_zones(LumaAudioMeter *self, gboolean zones) {
  g_return_if_fail(LUMA_IS_AUDIO_METER(self));
  zones = !!zones;
  if (self->zones == zones)
    return;
  self->zones = zones;
  luma_ui_set_css_class(GTK_WIDGET(self), "plain", !zones);
  for (guint i = 0; i < 2; i++)
    self->drawn_peak[i] = zones ? peak_segment_at(self->segments, self->peak[i]) : -1;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

gboolean luma_audio_meter_get_zones(LumaAudioMeter *self) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), TRUE);
  return self->zones;
}

guint luma_audio_meter_segments_lit_at(guint segments, double db) {
  if (segments == 0 || isnan(db))
    return 0;
  return MIN(segments, (guint)floor(luma_audio_meter_db_to_fraction(db) * segments + 0.5));
}

guint luma_audio_meter_get_lit_segments(LumaAudioMeter *self, guint channel) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), 0);
  g_return_val_if_fail(channel < 2, 0);
  return luma_audio_meter_segments_lit_at(self->segments, self->shown[channel]);
}

int luma_audio_meter_get_peak_segment(LumaAudioMeter *self, guint channel) {
  g_return_val_if_fail(LUMA_IS_AUDIO_METER(self), -1);
  g_return_val_if_fail(channel < 2, -1);
  return self->zones ? peak_segment_at(self->segments, self->peak[channel]) : -1;
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
  /* A level that is not a number reads as silence. */
  if (isnan(left_db))
    left_db = FLOOR_DB;
  if (isnan(right_db))
    right_db = FLOOR_DB;
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
  queue_redraw(self);
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
