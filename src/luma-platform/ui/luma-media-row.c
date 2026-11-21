/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-media-row.h"
#include "luma-ui-private.h"
#include "luma-ui-tokens-private.h"

#include <math.h>

/* ── The level glyph: three bars that move while sound is coming out ───────── */

#define LEVEL_BARS 3
static const double level_heights[LEVEL_BARS] = {0.6, 1.0, 0.45};
/* Where in the period each bar starts (the mockup's animation delays). */
static const double level_phase[LEVEL_BARS] = {0.0, 0.4, 0.7};

#define LUMA_TYPE_MEDIA_LEVEL (luma_media_level_get_type())
G_DECLARE_FINAL_TYPE(LumaMediaLevel, luma_media_level, LUMA, MEDIA_LEVEL, GtkWidget)

struct _LumaMediaLevel {
  GtkWidget parent_instance;
  gboolean on;
  guint tick;
  gint64 started;
  double phase; /* seconds into the animation */
};

G_DEFINE_FINAL_TYPE(LumaMediaLevel, luma_media_level, GTK_TYPE_WIDGET)

static void level_measure(GtkWidget *widget G_GNUC_UNUSED, GtkOrientation orientation, int for_size G_GNUC_UNUSED,
                          int *minimum, int *natural, int *minimum_baseline, int *natural_baseline) {
  if (orientation == GTK_ORIENTATION_HORIZONTAL)
    *minimum = *natural =
        (int)ceil(LEVEL_BARS * LUMA_UI_MEDIA_ROW_LEVEL_BAR + (LEVEL_BARS - 1) * LUMA_UI_MEDIA_ROW_LEVEL_GAP);
  else
    *minimum = *natural = LUMA_UI_MEDIA_ROW_LEVEL_HEIGHT;
  *minimum_baseline = *natural_baseline = -1;
}

/* Ease in and out between 1 and the low scale and back, once a period each way. */
static double level_scale(double seconds) {
  double period = LUMA_UI_MEDIA_ROW_LEVEL_PERIOD_MS / 1000.0;
  double t = fmod(seconds, 2 * period) / period; /* 0..2 */
  double x = t <= 1 ? t : 2 - t;                 /* alternate */
  double eased = x < 0.5 ? 2 * x * x : 1 - pow(-2 * x + 2, 2) / 2;
  return 1 - eased * (1 - LUMA_UI_MEDIA_ROW_LEVEL_LOW_SCALE);
}

static void level_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaMediaLevel *self = LUMA_MEDIA_LEVEL(widget);
  GdkRGBA color;
  gtk_widget_get_color(widget, &color);
  float height = gtk_widget_get_height(widget);
  float bar = LUMA_UI_MEDIA_ROW_LEVEL_BAR, gap = LUMA_UI_MEDIA_ROW_LEVEL_GAP;
  gboolean rtl = gtk_widget_get_direction(widget) == GTK_TEXT_DIR_RTL;
  float width = gtk_widget_get_width(widget);
  for (guint i = 0; i < LEVEL_BARS; i++) {
    float full = self->on ? (float)(level_heights[i] * height) : LUMA_UI_MEDIA_ROW_LEVEL_REST;
    /* A bar stands on the line and is scaled about its own middle. */
    float scale = self->on && !luma_ui_reduced_motion()
                      ? (float)level_scale(self->phase + level_phase[i] * LUMA_UI_MEDIA_ROW_LEVEL_PERIOD_MS / 1000.0)
                      : 1;
    float shown = full * scale;
    float x = i * (bar + gap);
    if (rtl)
      x = width - x - bar;
    graphene_rect_t rect = GRAPHENE_RECT_INIT(x, height - full + (full - shown) / 2, bar, shown);
    GskRoundedRect rounded;
    gsk_rounded_rect_init_from_rect(&rounded, &rect, MIN(LUMA_UI_MEDIA_ROW_LEVEL_RADIUS, shown / 2));
    gtk_snapshot_push_rounded_clip(snapshot, &rounded);
    gtk_snapshot_append_color(snapshot, &color, &rect);
    gtk_snapshot_pop(snapshot);
  }
}

static gboolean level_tick(GtkWidget *widget, GdkFrameClock *clock, gpointer data G_GNUC_UNUSED) {
  LumaMediaLevel *self = LUMA_MEDIA_LEVEL(widget);
  gint64 now = gdk_frame_clock_get_frame_time(clock);
  if (self->started == 0)
    self->started = now;
  self->phase = (now - self->started) / 1e6;
  gtk_widget_queue_draw(widget);
  return G_SOURCE_CONTINUE;
}

/* The bars move only while the glyph is on screen and sound is coming out. */
static void level_sync(LumaMediaLevel *self) {
  gboolean run = self->on && gtk_widget_get_mapped(GTK_WIDGET(self)) && !luma_ui_reduced_motion();
  if (run && self->tick == 0) {
    self->started = 0;
    self->tick = gtk_widget_add_tick_callback(GTK_WIDGET(self), level_tick, NULL, NULL);
  } else if (!run && self->tick != 0) {
    gtk_widget_remove_tick_callback(GTK_WIDGET(self), self->tick);
    self->tick = 0;
    self->phase = 0;
  }
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void level_map(GtkWidget *widget) {
  GTK_WIDGET_CLASS(luma_media_level_parent_class)->map(widget);
  level_sync(LUMA_MEDIA_LEVEL(widget));
}

static void level_unmap(GtkWidget *widget) {
  GTK_WIDGET_CLASS(luma_media_level_parent_class)->unmap(widget);
  level_sync(LUMA_MEDIA_LEVEL(widget));
}

static void luma_media_level_class_init(LumaMediaLevelClass *klass) {
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  widget_class->measure = level_measure;
  widget_class->snapshot = level_snapshot;
  widget_class->map = level_map;
  widget_class->unmap = level_unmap;
  gtk_widget_class_set_css_name(widget_class, "lumaui-media-row-level");
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_PRESENTATION);
}

static void luma_media_level_init(LumaMediaLevel *self) {
  gtk_widget_set_can_target(GTK_WIDGET(self), FALSE);
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_CENTER);
}

static void level_set_on(LumaMediaLevel *self, gboolean on) {
  self->on = !!on;
  luma_ui_set_css_class(GTK_WIDGET(self), "off", !self->on);
  level_sync(self);
}

#define LEVEL_FLOOR_DB -60.0

/* ── The row ───────────────────────────────────────────────────────────────── */

typedef struct {
  int art, art_top, tile_padding, badge, badge_offset, column_gap, row_gap;
  int padding_top, padding_end, padding_bottom, padding_start, play, min_height;
} Metrics;

struct _LumaMediaRow {
  GtkWidget parent_instance;
  LumaMediaRowDensity density;
  LumaMediaRowTransport transport;
  GtkWidget *art, *picture, *tile_icon, *badge, *badge_icon;
  GtkWidget *text, *title, *line_box, *level, *line;
  GtkWidget *keys, *previous, *play, *next;
  GtkWidget *volume_icon, *slider, *readout, *meter, *trailing;
  char *app_name;
  GIcon *app_icon;
  GCancellable *art_load;
  guint meter_segments;
  gboolean playing, sounding, muted, has_art, stacked;
  gboolean raised_once; /* the artwork was pressed once: the next press mutes */
};

enum { RAISE, MUTE_TOGGLED, PLAY_PAUSE, PREVIOUS, NEXT, VOLUME_CHANGED, N_SIGNALS };
static guint signals[N_SIGNALS];
enum { PROP_0, PROP_DENSITY, PROP_PLAYING, PROP_MUTED, PROP_VOLUME, N_PROPS };
static GParamSpec *props[N_PROPS];

G_DEFINE_FINAL_TYPE(LumaMediaRow, luma_media_row, GTK_TYPE_WIDGET)

static gboolean regular(LumaMediaRow *self) { return self->density == LUMA_MEDIA_ROW_REGULAR; }

static Metrics metrics(LumaMediaRow *self) {
  if (regular(self))
    return (Metrics){LUMA_UI_MEDIA_ROW_REGULAR_ART,
                     LUMA_UI_MEDIA_ROW_REGULAR_ART_TOP,
                     LUMA_UI_MEDIA_ROW_REGULAR_TILE_PADDING,
                     LUMA_UI_MEDIA_ROW_REGULAR_BADGE,
                     LUMA_UI_MEDIA_ROW_REGULAR_BADGE_OFFSET,
                     LUMA_UI_MEDIA_ROW_REGULAR_COLUMN_GAP,
                     LUMA_UI_MEDIA_ROW_REGULAR_ROW_GAP,
                     LUMA_UI_MEDIA_ROW_REGULAR_PADDING_TOP,
                     LUMA_UI_MEDIA_ROW_REGULAR_PADDING_X,
                     LUMA_UI_MEDIA_ROW_REGULAR_PADDING_BOTTOM,
                     LUMA_UI_MEDIA_ROW_REGULAR_PADDING_X,
                     LUMA_UI_MEDIA_ROW_REGULAR_PLAY,
                     0};
  gboolean touch = self->density == LUMA_MEDIA_ROW_TOUCH;
  return (Metrics){touch ? LUMA_UI_MEDIA_ROW_TOUCH_ART : LUMA_UI_MEDIA_ROW_COMPACT_ART,
                   LUMA_UI_MEDIA_ROW_COMPACT_ART_TOP,
                   LUMA_UI_MEDIA_ROW_COMPACT_TILE_PADDING,
                   LUMA_UI_MEDIA_ROW_COMPACT_BADGE,
                   LUMA_UI_MEDIA_ROW_COMPACT_BADGE_OFFSET,
                   LUMA_UI_MEDIA_ROW_COMPACT_COLUMN_GAP,
                   LUMA_UI_MEDIA_ROW_COMPACT_ROW_GAP,
                   touch ? LUMA_UI_MEDIA_ROW_TOUCH_PADDING_Y : LUMA_UI_MEDIA_ROW_COMPACT_PADDING_TOP,
                   LUMA_UI_MEDIA_ROW_COMPACT_PADDING_END,
                   touch ? LUMA_UI_MEDIA_ROW_TOUCH_PADDING_Y : LUMA_UI_MEDIA_ROW_COMPACT_PADDING_BOTTOM,
                   LUMA_UI_MEDIA_ROW_COMPACT_PADDING_START,
                   touch ? LUMA_UI_MEDIA_ROW_TOUCH_PLAY : LUMA_UI_MEDIA_ROW_COMPACT_PLAY,
                   touch ? LUMA_UI_MEDIA_ROW_TOUCH_ROW : LUMA_UI_MEDIA_ROW_COMPACT_ROW};
}

static void nat(GtkWidget *widget, int for_width, int *width, int *height) {
  int min;
  *width = *height = 0;
  if (!widget || !gtk_widget_should_layout(widget))
    return;
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &min, width, NULL, NULL);
  if (for_width >= 0)
    *width = MAX(min, MIN(*width, for_width));
  gtk_widget_measure(widget, GTK_ORIENTATION_VERTICAL, for_width >= 0 ? MAX(*width, min) : -1, &min, height, NULL,
                     NULL);
}

/* The slider's knob overhangs its track's ends by this much: the row lays
 * the track, not the slider's box, on its columns, as the mockup's range does. */
static int overhang(LumaMediaRow *self) {
  return regular(self) ? (LUMA_UI_VOLUME_SLIDER_KNOB - LUMA_UI_VOLUME_SLIDER_TRACK) / 2
                       : (LUMA_UI_VOLUME_SLIDER_COMPACT_KNOB - LUMA_UI_VOLUME_SLIDER_TRACK) / 2;
}

static int min_width(GtkWidget *widget) {
  int min = 0, natural;
  if (widget && gtk_widget_should_layout(widget))
    gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &min, &natural, NULL, NULL);
  return min;
}

static void place(LumaMediaRow *self, GtkWidget *child, int x, int y, int width, int height) {
  if (!child || !gtk_widget_should_layout(child))
    return;
  if (gtk_widget_get_direction(GTK_WIDGET(self)) == GTK_TEXT_DIR_RTL)
    x = gtk_widget_get_width(GTK_WIDGET(self)) - x - width;
  gtk_widget_size_allocate(child, &(GtkAllocation){x, y, MAX(0, width), MAX(0, height)}, -1);
}

/* What a regular row's lines need, for a width (or -1). */
typedef struct {
  int text_h, keys_w, keys_h, trailing_w, trailing_h, readout_w, slider_h, meter_h, row1, row2;
} Lines;

static Lines lines(LumaMediaRow *self, int text_width) {
  Lines l = {0};
  int w, h;
  nat(self->text, text_width, &w, &l.text_h);
  nat(self->keys, -1, &l.keys_w, &l.keys_h);
  nat(self->trailing, -1, &l.trailing_w, &l.trailing_h);
  nat(self->readout, -1, &l.readout_w, &h);
  l.readout_w = MAX(l.readout_w, LUMA_UI_MEDIA_ROW_REGULAR_READOUT_WIDTH);
  nat(self->slider, -1, &w, &l.slider_h);
  l.slider_h = MAX(MAX(l.slider_h, h), LUMA_UI_MEDIA_ROW_REGULAR_VOLUME_ICON);
  nat(self->meter, -1, &w, &l.meter_h);
  l.row1 = MAX(l.text_h, l.keys_h);
  l.row2 = MAX(l.slider_h, self->stacked ? 0 : l.trailing_h);
  return l;
}

static int regular_height(LumaMediaRow *self, int width) {
  Metrics m = metrics(self);
  Lines l = lines(self, -1);
  int column3 = self->stacked ? l.keys_w : MAX(l.keys_w, l.trailing_w);
  int text_width = width < 0 ? -1
                             : width - m.padding_start - m.padding_end - m.art - m.column_gap -
                                   (column3 ? column3 + m.column_gap : 0);
  if (text_width >= 0)
    l = lines(self, MAX(0, text_width));
  int content = l.row1 + m.row_gap + l.row2;
  /* The meter sits in the row gap under the volume line: the block grows by that gap, not by the meter. */
  if (l.meter_h)
    content += self->stacked ? LUMA_UI_MEDIA_ROW_REGULAR_METER_TOP + l.meter_h
                             : MAX(m.row_gap, LUMA_UI_MEDIA_ROW_REGULAR_METER_TOP + l.meter_h);
  if (self->stacked && l.trailing_h)
    content += m.row_gap + l.trailing_h;
  return m.padding_top + MAX(m.art_top + m.art, content) + m.padding_bottom;
}

static int compact_height(LumaMediaRow *self) {
  Metrics m = metrics(self);
  int w, text_h, slider_h, play_h = gtk_widget_should_layout(self->play) ? m.play : 0;
  nat(self->text, -1, &w, &text_h);
  nat(self->slider, -1, &w, &slider_h);
  int content = text_h + m.row_gap + slider_h;
  /* The box is the row token (60 px in a panel, 64 under a finger), never less than the artwork or the
   * key with their padding; the words and the slider sit inside it. */
  return MAX(MAX(m.min_height, content), m.padding_top + MAX(m.art, play_h) + m.padding_bottom);
}

static gboolean would_stack(LumaMediaRow *self, int width) {
  return regular(self) && width >= 0 && width < LUMA_UI_MEDIA_ROW_REGULAR_STACK_BELOW;
}

static void luma_media_row_measure(GtkWidget *widget, GtkOrientation orientation, int for_size, int *minimum,
                                   int *natural, int *minimum_baseline, int *natural_baseline) {
  LumaMediaRow *self = LUMA_MEDIA_ROW(widget);
  Metrics m = metrics(self);
  *minimum_baseline = *natural_baseline = -1;
  if (orientation == GTK_ORIENTATION_VERTICAL) {
    if (regular(self)) {
      gboolean was = self->stacked;
      self->stacked = would_stack(self, for_size);
      *minimum = *natural = regular_height(self, for_size);
      self->stacked = was;
    } else {
      *minimum = *natural = compact_height(self);
    }
    return;
  }
  int text_w, h, keys_w, trailing_w, slider_w;
  nat(self->text, -1, &text_w, &h);
  nat(self->keys, -1, &keys_w, &h);
  nat(self->trailing, -1, &trailing_w, &h);
  nat(self->slider, -1, &slider_w, &h);
  int fixed = m.padding_start + m.art + m.column_gap + m.padding_end;
  if (regular(self)) {
    /* The narrowest row is a stacked one: the keys beside the words, the
     * slider line under them, the picker under that. */
    int line = LUMA_UI_MEDIA_ROW_REGULAR_VOLUME_ICON + 2 * LUMA_UI_MEDIA_ROW_REGULAR_SLIDER_GAP +
               LUMA_UI_MEDIA_ROW_REGULAR_READOUT_WIDTH;
    int keys = keys_w ? keys_w + m.column_gap : 0;
    *minimum = fixed + MAX(MAX(min_width(self->text) + keys, line + min_width(self->slider)),
                           min_width(self->trailing) - m.art - m.column_gap);
    int column3 = MAX(keys_w, trailing_w);
    *natural = MAX(*minimum, fixed + MAX(text_w, line + slider_w) + (column3 ? column3 + m.column_gap : 0));
  } else {
    int play = gtk_widget_should_layout(self->play) ? m.play + m.column_gap : 0;
    *minimum = fixed + play + MAX(min_width(self->text), min_width(self->slider) + LUMA_UI_MEDIA_ROW_COMPACT_SLIDER_END);
    *natural = MAX(*minimum, fixed + play + MAX(text_w, slider_w + LUMA_UI_MEDIA_ROW_COMPACT_SLIDER_END));
  }
}

static GtkSizeRequestMode luma_media_row_get_request_mode(GtkWidget *widget G_GNUC_UNUSED) {
  return GTK_SIZE_REQUEST_HEIGHT_FOR_WIDTH;
}

static void allocate_art(LumaMediaRow *self, Metrics m, int x, int y) {
  place(self, self->art, x, y, m.art, m.art);
  place(self, self->badge, x + m.art + m.badge_offset - m.badge, y + m.art + m.badge_offset - m.badge, m.badge,
        m.badge);
}

static void luma_media_row_size_allocate(GtkWidget *widget, int width, int height, int baseline G_GNUC_UNUSED) {
  LumaMediaRow *self = LUMA_MEDIA_ROW(widget);
  Metrics m = metrics(self);
  gboolean stacked = would_stack(self, width);
  if (stacked != self->stacked) {
    self->stacked = stacked;
    luma_ui_set_css_class(widget, "stacked", stacked);
  }
  int column2 = m.padding_start + m.art + m.column_gap;
  if (!regular(self)) {
    int w, text_h, slider_h;
    gboolean has_play = gtk_widget_should_layout(self->play);
    int play_x = width - m.padding_end - m.play;
    int end = has_play ? play_x - m.column_gap : width - m.padding_end;
    nat(self->text, MAX(0, end - column2), &w, &text_h);
    nat(self->slider, -1, &w, &slider_h);
    int content = text_h + m.row_gap + slider_h, top = (height - content) / 2;
    allocate_art(self, m, m.padding_start, (height - m.art) / 2);
    place(self, self->text, column2, top, end - column2, text_h);
    place(self, self->slider, column2 - overhang(self), top + text_h + m.row_gap,
          end - column2 - LUMA_UI_MEDIA_ROW_COMPACT_SLIDER_END + 2 * overhang(self), slider_h);
    /* The keys' box holds only the play key here. */
    place(self, self->keys, play_x, (height - m.play) / 2, m.play, m.play);
    return;
  }
  Lines l = lines(self, -1);
  int inner = width - m.padding_start - m.padding_end;
  int column3 = stacked ? l.keys_w : MAX(l.keys_w, l.trailing_w);
  int column3_x = m.padding_start + inner - column3;
  int text_w = column3_x - (column3 ? m.column_gap : 0) - column2;
  l = lines(self, MAX(0, text_w));
  int y = m.padding_top;
  allocate_art(self, m, m.padding_start, y + m.art_top);
  place(self, self->text, column2, y, text_w, l.text_h);
  /* The keys start the third column; the picker ends it. */
  place(self, self->keys, column3_x, y + (l.row1 - l.keys_h) / 2, l.keys_w, l.keys_h);
  y += l.row1 + m.row_gap;
  int line_w = stacked ? m.padding_start + inner - column2 : text_w;
  int icon = LUMA_UI_MEDIA_ROW_REGULAR_VOLUME_ICON, gap = LUMA_UI_MEDIA_ROW_REGULAR_SLIDER_GAP;
  /* The track, and the meter under it, run from 10 px after the glyph to 10 px before the readout. */
  int track_x = column2 + icon + gap, track_w = line_w - icon - 2 * gap - l.readout_w;
  place(self, self->volume_icon, column2, y + (l.row2 - icon) / 2, icon, icon);
  place(self, self->slider, track_x - overhang(self), y + (l.row2 - l.slider_h) / 2, track_w + 2 * overhang(self),
        l.slider_h);
  int w, readout_h;
  nat(self->readout, -1, &w, &readout_h);
  place(self, self->readout, column2 + line_w - l.readout_w, y + (l.row2 - readout_h) / 2, l.readout_w, readout_h);
  if (!stacked)
    place(self, self->trailing, column3_x + column3 - l.trailing_w, y + (l.row2 - l.trailing_h) / 2, l.trailing_w,
          l.trailing_h);
  y += l.row2;
  if (l.meter_h) {
    y += LUMA_UI_MEDIA_ROW_REGULAR_METER_TOP;
    place(self, self->meter, track_x, y, track_w, l.meter_h);
    y += l.meter_h;
  }
  if (stacked)
    place(self, self->trailing, m.padding_start, y + m.row_gap, inner, l.trailing_h);
}

/* ── State ─────────────────────────────────────────────────────────────────── */

static void sync_keys(LumaMediaRow *self) {
  gboolean can_play = self->transport & LUMA_MEDIA_ROW_PLAY_PAUSE;
  gtk_widget_set_visible(self->play, can_play);
  gtk_widget_set_visible(self->previous, regular(self) && (self->transport & LUMA_MEDIA_ROW_PREVIOUS));
  gtk_widget_set_visible(self->next, regular(self) && (self->transport & LUMA_MEDIA_ROW_NEXT));
  gtk_widget_set_visible(self->keys, can_play || (regular(self) && self->transport != LUMA_MEDIA_ROW_NO_TRANSPORT));
  /* A raised key carries the filled glyph, a round panel key the outline. */
  gtk_button_set_icon_name(GTK_BUTTON(self->play),
                           regular(self) ? (self->playing ? "lumaui-pause-filled-symbolic" : "lumaui-play-filled-symbolic")
                                         : (self->playing ? "lumaui-pause-symbolic" : "lumaui-play-symbolic"));
  const char *label = self->playing ? "Pause" : "Play";
  gtk_widget_set_tooltip_text(self->play, label);
  luma_ui_set_accessible_label(self->play, label);
}

static void sync_volume(LumaMediaRow *self) {
  double value = luma_volume_slider_get_value(LUMA_VOLUME_SLIDER(self->slider));
  g_autofree char *text = g_strdup_printf("%.0f%%", value);
  gtk_label_set_label(GTK_LABEL(self->readout), text);
  gtk_image_set_from_icon_name(GTK_IMAGE(self->volume_icon),
                               self->muted || value < 0.5 ? "lumaui-volume-x-symbolic" : "lumaui-volume-2-symbolic");
}

static void sync_level(LumaMediaRow *self) {
  gtk_widget_set_visible(self->level, regular(self) && self->sounding && self->playing);
  level_set_on(LUMA_MEDIA_LEVEL(self->level), self->sounding && self->playing);
}

static void sync_art(LumaMediaRow *self) {
  Metrics m = metrics(self);
  gtk_widget_set_visible(self->picture, self->has_art);
  gtk_widget_set_visible(self->tile_icon, !self->has_art);
  luma_ui_set_css_class(self->art, "tile", !self->has_art);
  gtk_image_set_pixel_size(GTK_IMAGE(self->tile_icon), m.art - 2 * m.tile_padding);
  gtk_image_set_pixel_size(GTK_IMAGE(self->badge_icon), m.badge);
  gtk_widget_set_visible(self->badge, self->app_icon != NULL);
}

static void sync_density(LumaMediaRow *self) {
  GtkWidget *widget = GTK_WIDGET(self);
  luma_ui_set_css_class(widget, "regular", regular(self));
  luma_ui_set_css_class(widget, "compact", !regular(self));
  luma_ui_set_css_class(widget, "touch", self->density == LUMA_MEDIA_ROW_TOUCH);
  gtk_widget_set_visible(self->volume_icon, regular(self));
  gtk_widget_set_visible(self->readout, regular(self));
  if (self->meter)
    gtk_widget_set_visible(self->meter, regular(self));
  if (self->trailing)
    gtk_widget_set_visible(self->trailing, regular(self));
  luma_volume_slider_set_compact(LUMA_VOLUME_SLIDER(self->slider), !regular(self));
  luma_ui_set_css_class(self->title, "lumaui-t-media-row-title", regular(self));
  luma_ui_set_css_class(self->title, "lumaui-t-media-row-compact-title", !regular(self));
  luma_ui_set_css_class(self->line, "lumaui-t-media-row-line", regular(self));
  luma_ui_set_css_class(self->line, "lumaui-t-media-row-compact-line", !regular(self));
  sync_keys(self);
  sync_art(self);
  sync_level(self);
  gtk_widget_queue_resize(widget);
}

static void sync_label(LumaMediaRow *self) {
  const char *title = gtk_label_get_label(GTK_LABEL(self->title));
  g_autofree char *label = self->app_name && *self->app_name && g_strcmp0(self->app_name, title) != 0
                               ? g_strdup_printf("%s, %s", title, self->app_name)
                               : g_strdup(title);
  luma_ui_set_accessible_label(GTK_WIDGET(self), label);
  g_autofree char *volume = g_strdup_printf("%s volume", self->app_name && *self->app_name ? self->app_name : title);
  luma_ui_set_accessible_label(self->slider, volume);
}

/* ── The person ────────────────────────────────────────────────────────────── */

static void toggle_mute(LumaMediaRow *self) { g_signal_emit(self, signals[MUTE_TOGGLED], 0, !self->muted); }

static void art_pressed(GtkGestureClick *gesture, int n_press G_GNUC_UNUSED, double x G_GNUC_UNUSED,
                        double y G_GNUC_UNUSED, gpointer data) {
  LumaMediaRow *self = data;
  gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
  if (self->raised_once) {
    toggle_mute(self);
  } else {
    self->raised_once = TRUE;
    g_signal_emit(self, signals[RAISE], 0);
  }
}

static void badge_pressed(GtkGestureClick *gesture, int n_press G_GNUC_UNUSED, double x G_GNUC_UNUSED,
                          double y G_GNUC_UNUSED, gpointer data) {
  gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
  toggle_mute(data);
}

/* Leaving the row ends the "second press": the next press raises again. */
static void pointer_left(GtkEventControllerMotion *motion G_GNUC_UNUSED, gpointer data) {
  LUMA_MEDIA_ROW(data)->raised_once = FALSE;
}

static gboolean key_pressed(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval, guint keycode G_GNUC_UNUSED,
                            GdkModifierType state, gpointer data) {
  LumaMediaRow *self = data;
  if (state & (GDK_CONTROL_MASK | GDK_ALT_MASK | GDK_SUPER_MASK))
    return GDK_EVENT_PROPAGATE;
  if (keyval == GDK_KEY_m || keyval == GDK_KEY_M) {
    toggle_mute(self);
    return GDK_EVENT_STOP;
  }
  /* Space, and OK on a remote: the keys' own buttons take theirs first. */
  if ((keyval == GDK_KEY_space || keyval == GDK_KEY_KP_Space || keyval == GDK_KEY_Return ||
       keyval == GDK_KEY_KP_Enter || keyval == GDK_KEY_ISO_Enter) &&
      (self->transport & LUMA_MEDIA_ROW_PLAY_PAUSE)) {
    g_signal_emit(self, signals[PLAY_PAUSE], 0);
    return GDK_EVENT_STOP;
  }
  return GDK_EVENT_PROPAGATE;
}

static void play_clicked(GtkButton *button G_GNUC_UNUSED, gpointer data) { g_signal_emit(data, signals[PLAY_PAUSE], 0); }
static void previous_clicked(GtkButton *button G_GNUC_UNUSED, gpointer data) { g_signal_emit(data, signals[PREVIOUS], 0); }
static void next_clicked(GtkButton *button G_GNUC_UNUSED, gpointer data) { g_signal_emit(data, signals[NEXT], 0); }

static void slider_moved(LumaVolumeSlider *slider G_GNUC_UNUSED, double value, gpointer data) {
  LumaMediaRow *self = data;
  sync_volume(self);
  g_signal_emit(self, signals[VOLUME_CHANGED], 0, value);
  g_object_notify_by_pspec(G_OBJECT(self), props[PROP_VOLUME]);
}

/* ── GObject ───────────────────────────────────────────────────────────────── */

static void luma_media_row_dispose(GObject *object) {
  LumaMediaRow *self = LUMA_MEDIA_ROW(object);
  if (self->art_load) {
    g_cancellable_cancel(self->art_load);
    g_clear_object(&self->art_load);
  }
  GtkWidget **children[] = {&self->art, &self->badge, &self->text, &self->keys, &self->volume_icon,
                            &self->slider, &self->readout, &self->meter, &self->trailing};
  for (guint i = 0; i < G_N_ELEMENTS(children); i++)
    g_clear_pointer(children[i], gtk_widget_unparent);
  g_clear_object(&self->app_icon);
  g_clear_pointer(&self->app_name, g_free);
  G_OBJECT_CLASS(luma_media_row_parent_class)->dispose(object);
}

static void luma_media_row_get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  LumaMediaRow *self = LUMA_MEDIA_ROW(object);
  switch (id) {
  case PROP_DENSITY: g_value_set_int(value, self->density); break;
  case PROP_PLAYING: g_value_set_boolean(value, self->playing); break;
  case PROP_MUTED: g_value_set_boolean(value, self->muted); break;
  case PROP_VOLUME: g_value_set_double(value, luma_media_row_get_volume(self)); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void luma_media_row_set_property(GObject *object, guint id, const GValue *value, GParamSpec *pspec) {
  LumaMediaRow *self = LUMA_MEDIA_ROW(object);
  switch (id) {
  case PROP_DENSITY: luma_media_row_set_density(self, g_value_get_int(value)); break;
  case PROP_PLAYING: luma_media_row_set_playing(self, g_value_get_boolean(value)); break;
  case PROP_MUTED: luma_media_row_set_muted(self, g_value_get_boolean(value)); break;
  case PROP_VOLUME: luma_media_row_set_volume(self, g_value_get_double(value)); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static gboolean luma_media_row_grab_focus(GtkWidget *widget) {
  return gtk_widget_grab_focus(LUMA_MEDIA_ROW(widget)->slider);
}

static void luma_media_row_class_init(LumaMediaRowClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->dispose = luma_media_row_dispose;
  object_class->get_property = luma_media_row_get_property;
  object_class->set_property = luma_media_row_set_property;
  widget_class->measure = luma_media_row_measure;
  widget_class->size_allocate = luma_media_row_size_allocate;
  widget_class->get_request_mode = luma_media_row_get_request_mode;
  widget_class->grab_focus = luma_media_row_grab_focus;
  gtk_widget_class_set_css_name(widget_class, "lumaui-media-row");
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_GROUP);

  props[PROP_DENSITY] = g_param_spec_int("density", NULL, NULL, LUMA_MEDIA_ROW_REGULAR, LUMA_MEDIA_ROW_TOUCH,
                                         LUMA_MEDIA_ROW_REGULAR,
                                         G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_EXPLICIT_NOTIFY);
  props[PROP_PLAYING] = g_param_spec_boolean("playing", NULL, NULL, FALSE,
                                             G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_EXPLICIT_NOTIFY);
  props[PROP_MUTED] = g_param_spec_boolean("muted", NULL, NULL, FALSE,
                                           G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_EXPLICIT_NOTIFY);
  props[PROP_VOLUME] = g_param_spec_double("volume", NULL, NULL, 0, 1000, 100,
                                           G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_EXPLICIT_NOTIFY);
  g_object_class_install_properties(object_class, N_PROPS, props);

  GType type = G_TYPE_FROM_CLASS(klass);
  /**
   * LumaMediaRow::raise:
   * @self: the row
   *
   * The artwork was pressed: bring the app's window forward.
   */
  signals[RAISE] = g_signal_new("raise", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
  /**
   * LumaMediaRow::mute-toggled:
   * @self: the row
   * @muted: what the person asked for
   */
  signals[MUTE_TOGGLED] =
      g_signal_new("mute-toggled", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_BOOLEAN);
  signals[PLAY_PAUSE] = g_signal_new("play-pause", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
  signals[PREVIOUS] = g_signal_new("previous", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
  signals[NEXT] = g_signal_new("next", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
  /**
   * LumaMediaRow::volume-changed:
   * @self: the row
   * @percent: where the person moved the slider
   */
  signals[VOLUME_CHANGED] =
      g_signal_new("volume-changed", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_DOUBLE);
}

static GtkWidget *key(const char *icon, const char *label, GCallback clicked, LumaMediaRow *self) {
  GtkWidget *button = gtk_button_new_from_icon_name(icon);
  gtk_widget_add_css_class(button, "lumaui-media-row-key");
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  gtk_widget_set_tooltip_text(button, label);
  luma_ui_set_accessible_label(button, label);
  g_signal_connect(button, "clicked", clicked, self);
  return button;
}

static GtkWidget *clickable(GtkWidget *widget, GCallback pressed, LumaMediaRow *self) {
  GtkGesture *click = gtk_gesture_click_new();
  gtk_gesture_single_set_button(GTK_GESTURE_SINGLE(click), GDK_BUTTON_PRIMARY);
  g_signal_connect(click, "pressed", pressed, self);
  gtk_widget_add_controller(widget, GTK_EVENT_CONTROLLER(click));
  gtk_widget_set_cursor_from_name(widget, "pointer");
  return widget;
}

static void luma_media_row_init(LumaMediaRow *self) {
  GtkWidget *widget = GTK_WIDGET(self);
  luma_ui_install();

  self->art = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->art, "lumaui-media-row-art");
  gtk_widget_set_overflow(self->art, GTK_OVERFLOW_HIDDEN);
  self->picture = gtk_picture_new();
  gtk_picture_set_content_fit(GTK_PICTURE(self->picture), GTK_CONTENT_FIT_COVER);
  gtk_picture_set_can_shrink(GTK_PICTURE(self->picture), TRUE);
  gtk_widget_set_hexpand(self->picture, TRUE);
  self->tile_icon = gtk_image_new();
  gtk_widget_set_hexpand(self->tile_icon, TRUE);
  gtk_widget_set_halign(self->tile_icon, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(self->art), self->picture);
  gtk_box_append(GTK_BOX(self->art), self->tile_icon);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->art), GTK_ACCESSIBLE_PROPERTY_LABEL, "Show app", -1);
  gtk_widget_set_parent(clickable(self->art, G_CALLBACK(art_pressed), self), widget);

  self->text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(self->text, "lumaui-media-row-text");
  self->title = gtk_label_new(NULL);
  gtk_widget_add_css_class(self->title, "lumaui-media-row-title");
  gtk_label_set_xalign(GTK_LABEL(self->title), 0);
  gtk_label_set_ellipsize(GTK_LABEL(self->title), PANGO_ELLIPSIZE_END);
  self->line_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->line_box, "lumaui-media-row-line");
  self->level = g_object_new(LUMA_TYPE_MEDIA_LEVEL, NULL);
  self->line = gtk_label_new(NULL);
  gtk_label_set_xalign(GTK_LABEL(self->line), 0);
  gtk_label_set_ellipsize(GTK_LABEL(self->line), PANGO_ELLIPSIZE_END);
  gtk_widget_set_hexpand(self->line, TRUE);
  gtk_box_append(GTK_BOX(self->line_box), self->level);
  gtk_box_append(GTK_BOX(self->line_box), self->line);
  gtk_box_append(GTK_BOX(self->text), self->title);
  gtk_box_append(GTK_BOX(self->text), self->line_box);
  gtk_widget_set_visible(self->line_box, FALSE);
  gtk_widget_set_parent(self->text, widget);

  self->keys = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->keys, "lumaui-media-row-keys");
  self->previous = key("lumaui-skip-back-symbolic", "Previous", G_CALLBACK(previous_clicked), self);
  self->play = key("lumaui-play-symbolic", "Play", G_CALLBACK(play_clicked), self);
  gtk_widget_add_css_class(self->play, "play");
  self->next = key("lumaui-skip-forward-symbolic", "Next", G_CALLBACK(next_clicked), self);
  gtk_box_append(GTK_BOX(self->keys), self->previous);
  gtk_box_append(GTK_BOX(self->keys), self->play);
  gtk_box_append(GTK_BOX(self->keys), self->next);
  gtk_widget_set_parent(self->keys, widget);

  self->volume_icon = gtk_image_new_from_icon_name("lumaui-volume-2-symbolic");
  gtk_widget_add_css_class(self->volume_icon, "lumaui-media-row-volume");
  gtk_image_set_pixel_size(GTK_IMAGE(self->volume_icon), LUMA_UI_MEDIA_ROW_REGULAR_VOLUME_ICON);
  gtk_accessible_update_state(GTK_ACCESSIBLE(self->volume_icon), GTK_ACCESSIBLE_STATE_HIDDEN, TRUE, -1);
  gtk_widget_set_parent(self->volume_icon, widget);

  self->slider = luma_volume_slider_new(GTK_ORIENTATION_HORIZONTAL, 0, 100);
  luma_volume_slider_set_steps(LUMA_VOLUME_SLIDER(self->slider), 5, 20);
  luma_volume_slider_set_value(LUMA_VOLUME_SLIDER(self->slider), 100);
  g_signal_connect(self->slider, "moved", G_CALLBACK(slider_moved), self);
  gtk_widget_set_parent(self->slider, widget);

  self->readout = gtk_label_new("100%");
  gtk_widget_add_css_class(self->readout, "lumaui-media-row-readout");
  gtk_widget_add_css_class(self->readout, "lumaui-t-media-row-readout");
  gtk_label_set_xalign(GTK_LABEL(self->readout), 1);
  gtk_accessible_update_state(GTK_ACCESSIBLE(self->readout), GTK_ACCESSIBLE_STATE_HIDDEN, TRUE, -1);
  gtk_widget_set_parent(self->readout, widget);

  /* Last, so it is drawn over the artwork's corner and takes its presses. */
  self->badge = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->badge, "lumaui-media-row-badge");
  gtk_widget_set_overflow(self->badge, GTK_OVERFLOW_HIDDEN);
  self->badge_icon = gtk_image_new();
  gtk_box_append(GTK_BOX(self->badge), self->badge_icon);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->badge), GTK_ACCESSIBLE_PROPERTY_LABEL, "Mute", -1);
  gtk_widget_set_parent(clickable(self->badge, G_CALLBACK(badge_pressed), self), widget);

  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(key_pressed), self);
  gtk_widget_add_controller(widget, keys);
  GtkEventController *motion = gtk_event_controller_motion_new();
  g_signal_connect(motion, "leave", G_CALLBACK(pointer_left), self);
  gtk_widget_add_controller(widget, motion);

  self->playing = FALSE;
  gtk_widget_add_css_class(widget, "paused");
  sync_density(self);
  sync_volume(self);
}

GtkWidget *luma_media_row_new(void) { return g_object_new(LUMA_TYPE_MEDIA_ROW, NULL); }

gboolean luma_media_row_focus_sibling(LumaMediaRow *self, GtkDirectionType direction) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), FALSE);
  gboolean forward = direction == GTK_DIR_DOWN || direction == GTK_DIR_TAB_FORWARD;
  GtkWidget *sibling = GTK_WIDGET(self);
  /* Rows of one list are siblings, with hairlines or nothing between them. */
  while ((sibling = forward ? gtk_widget_get_next_sibling(sibling) : gtk_widget_get_prev_sibling(sibling))) {
    if (LUMA_IS_MEDIA_ROW(sibling) && gtk_widget_get_visible(sibling))
      return gtk_widget_grab_focus(sibling);
  }
  return FALSE;
}

void luma_media_row_set_density(LumaMediaRow *self, LumaMediaRowDensity density) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  g_return_if_fail(density <= LUMA_MEDIA_ROW_TOUCH);
  if (self->density == density)
    return;
  self->density = density;
  sync_density(self);
  g_object_notify_by_pspec(G_OBJECT(self), props[PROP_DENSITY]);
}

LumaMediaRowDensity luma_media_row_get_density(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), LUMA_MEDIA_ROW_REGULAR);
  return self->density;
}

void luma_media_row_set_title(LumaMediaRow *self, const char *title) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  gtk_label_set_label(GTK_LABEL(self->title), title ? title : "");
  gtk_widget_set_tooltip_text(self->title, title);
  sync_label(self);
}

const char *luma_media_row_get_title(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), NULL);
  return gtk_label_get_label(GTK_LABEL(self->title));
}

void luma_media_row_set_line(LumaMediaRow *self, const char *line) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  gtk_label_set_label(GTK_LABEL(self->line), line ? line : "");
  gtk_widget_set_visible(self->line_box, line && *line);
}

const char *luma_media_row_get_line(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), NULL);
  return gtk_label_get_label(GTK_LABEL(self->line));
}

void luma_media_row_set_app(LumaMediaRow *self, const char *name, GIcon *icon) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  g_return_if_fail(icon == NULL || G_IS_ICON(icon));
  g_set_str(&self->app_name, name);
  g_set_object(&self->app_icon, icon);
  gtk_image_set_from_gicon(GTK_IMAGE(self->tile_icon), icon);
  gtk_image_set_from_gicon(GTK_IMAGE(self->badge_icon), icon);
  g_autofree char *show = g_strdup_printf("Show %s", name && *name ? name : "app");
  g_autofree char *mute = g_strdup_printf("Mute %s", name && *name ? name : "app");
  gtk_widget_set_tooltip_text(self->art, show);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->art), GTK_ACCESSIBLE_PROPERTY_LABEL, show, -1);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->badge), GTK_ACCESSIBLE_PROPERTY_LABEL, mute, -1);
  sync_art(self);
  sync_label(self);
}

void luma_media_row_set_art(LumaMediaRow *self, GdkPaintable *art) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  g_return_if_fail(art == NULL || GDK_IS_PAINTABLE(art));
  if (self->art_load) {
    g_cancellable_cancel(self->art_load);
    g_clear_object(&self->art_load);
  }
  gtk_picture_set_paintable(GTK_PICTURE(self->picture), art);
  self->has_art = art != NULL;
  sync_art(self);
}

static void art_loaded(GObject *source, GAsyncResult *result, gpointer data) {
  g_autoptr(GBytes) bytes = g_file_load_bytes_finish(G_FILE(source), result, NULL, NULL);
  LumaMediaRow *self = data; /* valid unless cancelled: dispose cancels */
  if (!bytes)
    return;
  g_autoptr(GdkTexture) texture = gdk_texture_new_from_bytes(bytes, NULL);
  if (!texture)
    return;
  g_clear_object(&self->art_load);
  gtk_picture_set_paintable(GTK_PICTURE(self->picture), GDK_PAINTABLE(texture));
  self->has_art = TRUE;
  sync_art(self);
}

void luma_media_row_set_art_file(LumaMediaRow *self, GFile *file) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  g_return_if_fail(file == NULL || G_IS_FILE(file));
  luma_media_row_set_art(self, NULL);
  if (!file)
    return;
  self->art_load = g_cancellable_new();
  g_file_load_bytes_async(file, self->art_load, art_loaded, self);
}

gboolean luma_media_row_get_has_art(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), FALSE);
  return self->has_art;
}

void luma_media_row_set_transport(LumaMediaRow *self, LumaMediaRowTransport transport) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  self->transport = transport;
  sync_keys(self);
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

LumaMediaRowTransport luma_media_row_get_transport(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), LUMA_MEDIA_ROW_NO_TRANSPORT);
  return self->transport;
}

void luma_media_row_set_playing(LumaMediaRow *self, gboolean playing) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  playing = !!playing;
  if (self->playing == playing)
    return;
  self->playing = playing;
  luma_ui_set_css_class(GTK_WIDGET(self), "paused", !playing);
  sync_keys(self);
  sync_level(self);
  if (!playing && self->meter)
    luma_audio_meter_set_levels_db(LUMA_AUDIO_METER(self->meter), LEVEL_FLOOR_DB, LEVEL_FLOOR_DB);
  g_object_notify_by_pspec(G_OBJECT(self), props[PROP_PLAYING]);
}

gboolean luma_media_row_get_playing(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), FALSE);
  return self->playing;
}

void luma_media_row_set_sounding(LumaMediaRow *self, gboolean sounding) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  if (self->sounding == !!sounding)
    return;
  self->sounding = !!sounding;
  sync_level(self);
}

gboolean luma_media_row_get_sounding(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), FALSE);
  return self->sounding;
}

void luma_media_row_set_volume(LumaMediaRow *self, double percent) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  if (luma_volume_slider_get_value(LUMA_VOLUME_SLIDER(self->slider)) == percent)
    return;
  luma_volume_slider_set_value(LUMA_VOLUME_SLIDER(self->slider), percent);
  sync_volume(self);
  g_object_notify_by_pspec(G_OBJECT(self), props[PROP_VOLUME]);
}

double luma_media_row_get_volume(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), 0);
  return luma_volume_slider_get_value(LUMA_VOLUME_SLIDER(self->slider));
}

void luma_media_row_set_muted(LumaMediaRow *self, gboolean muted) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  muted = !!muted;
  if (self->muted == muted)
    return;
  self->muted = muted;
  luma_ui_set_css_class(GTK_WIDGET(self), "muted", muted);
  luma_volume_slider_set_muted(LUMA_VOLUME_SLIDER(self->slider), muted);
  g_autofree char *label =
      g_strdup_printf("%s %s", muted ? "Unmute" : "Mute", self->app_name && *self->app_name ? self->app_name : "app");
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->badge), GTK_ACCESSIBLE_PROPERTY_LABEL, label, -1);
  sync_volume(self);
  g_object_notify_by_pspec(G_OBJECT(self), props[PROP_MUTED]);
}

gboolean luma_media_row_get_muted(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), FALSE);
  return self->muted;
}

void luma_media_row_set_meter_segments(LumaMediaRow *self, guint segments) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  self->meter_segments = segments;
  if (segments == 0) {
    g_clear_pointer(&self->meter, gtk_widget_unparent);
  } else {
    if (!self->meter) {
      /* An output's level: thin rounded segments in one green, no zones. */
      self->meter = luma_audio_meter_new(1);
      luma_audio_meter_set_horizontal(LUMA_AUDIO_METER(self->meter), TRUE);
      luma_audio_meter_set_zones(LUMA_AUDIO_METER(self->meter), FALSE);
      luma_audio_meter_set_segment_shape(LUMA_AUDIO_METER(self->meter), LUMA_UI_MEDIA_ROW_REGULAR_METER_THICKNESS,
                                         LUMA_UI_MEDIA_ROW_REGULAR_METER_GAP, LUMA_UI_MEDIA_ROW_REGULAR_METER_RADIUS);
      gtk_widget_insert_before(self->meter, GTK_WIDGET(self), self->badge);
      gtk_widget_set_visible(self->meter, regular(self));
    }
    luma_audio_meter_set_segments(LUMA_AUDIO_METER(self->meter), segments);
  }
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

void luma_media_row_set_level(LumaMediaRow *self, double position) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  if (!self->meter)
    return;
  /* The sound service's meter position: 0 is -60 dBFS or quieter, 1 is 0 dBFS. */
  double db = LEVEL_FLOOR_DB - (self->playing ? CLAMP(position, 0, 1) : 0) * LEVEL_FLOOR_DB;
  luma_audio_meter_set_levels_db(LUMA_AUDIO_METER(self->meter), db, db);
}

void luma_media_row_set_trailing(LumaMediaRow *self, GtkWidget *trailing) {
  g_return_if_fail(LUMA_IS_MEDIA_ROW(self));
  g_return_if_fail(trailing == NULL || GTK_IS_WIDGET(trailing));
  if (self->trailing == trailing)
    return;
  g_clear_pointer(&self->trailing, gtk_widget_unparent);
  self->trailing = trailing;
  if (trailing) {
    gtk_widget_insert_before(trailing, GTK_WIDGET(self), self->badge);
    gtk_widget_set_visible(trailing, regular(self));
  }
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

GtkWidget *luma_media_row_get_trailing(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), NULL);
  return self->trailing;
}

LumaVolumeSlider *luma_media_row_get_slider(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), NULL);
  return LUMA_VOLUME_SLIDER(self->slider);
}

LumaAudioMeter *luma_media_row_get_meter(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), NULL);
  return self->meter ? LUMA_AUDIO_METER(self->meter) : NULL;
}

GtkWidget *luma_media_row_get_play_button(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), NULL);
  return self->play;
}

GtkWidget *luma_media_row_get_art_widget(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), NULL);
  return self->art;
}

GtkWidget *luma_media_row_get_badge_widget(LumaMediaRow *self) {
  g_return_val_if_fail(LUMA_IS_MEDIA_ROW(self), NULL);
  return self->badge;
}

/* ── The trailing picker ───────────────────────────────────────────────────── */

GtkWidget *luma_media_row_chip_new(const char *icon_name, const char *label) {
  luma_ui_install();
  GtkWidget *chip = gtk_menu_button_new();
  gtk_widget_add_css_class(chip, "lumaui-media-row-chip");
  gtk_menu_button_set_always_show_arrow(GTK_MENU_BUTTON(chip), FALSE);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, LUMA_UI_MEDIA_ROW_CHIP_GAP);
  GtkWidget *icon = gtk_image_new();
  gtk_image_set_pixel_size(GTK_IMAGE(icon), LUMA_UI_MEDIA_ROW_CHIP_ICON);
  gtk_widget_add_css_class(icon, "lumaui-media-row-chip-icon");
  GtkWidget *text = gtk_label_new(NULL);
  gtk_widget_add_css_class(text, "lumaui-t-media-row-chip");
  gtk_label_set_ellipsize(GTK_LABEL(text), PANGO_ELLIPSIZE_END);
  gtk_label_set_xalign(GTK_LABEL(text), 0);
  gtk_widget_set_hexpand(text, TRUE);
  GtkWidget *chevron = gtk_image_new_from_icon_name("lumaui-chevron-down-symbolic");
  gtk_image_set_pixel_size(GTK_IMAGE(chevron), LUMA_UI_MEDIA_ROW_CHIP_CHEVRON);
  gtk_widget_add_css_class(chevron, "lumaui-media-row-chip-icon");
  gtk_box_append(GTK_BOX(content), icon);
  gtk_box_append(GTK_BOX(content), text);
  gtk_box_append(GTK_BOX(content), chevron);
  gtk_menu_button_set_child(GTK_MENU_BUTTON(chip), content);
  g_object_set_data(G_OBJECT(chip), "lumaui-media-row-chip-icon", icon);
  g_object_set_data(G_OBJECT(chip), "lumaui-media-row-chip-label", text);
  luma_media_row_chip_set(chip, icon_name, label, FALSE);
  return chip;
}

void luma_media_row_chip_set(GtkWidget *chip, const char *icon_name, const char *label, gboolean raised) {
  g_return_if_fail(GTK_IS_MENU_BUTTON(chip));
  GtkWidget *icon = g_object_get_data(G_OBJECT(chip), "lumaui-media-row-chip-icon");
  GtkWidget *text = g_object_get_data(G_OBJECT(chip), "lumaui-media-row-chip-label");
  g_return_if_fail(icon != NULL && text != NULL);
  gtk_image_set_from_icon_name(GTK_IMAGE(icon), icon_name);
  gtk_label_set_label(GTK_LABEL(text), label ? label : "");
  luma_ui_set_css_class(chip, "raised", raised);
  luma_ui_set_accessible_label(chip, label);
}
