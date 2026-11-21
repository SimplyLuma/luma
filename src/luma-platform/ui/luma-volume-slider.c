/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-volume-slider.h"
#include "luma-ui-private.h"
#include "luma-ui-tokens-private.h"

#include <math.h>

#define TRACK LUMA_UI_VOLUME_SLIDER_TRACK
/* A compact slider (a panel's row) has the smaller knob and slot. */
#define KNOB (self->compact ? LUMA_UI_VOLUME_SLIDER_COMPACT_KNOB : LUMA_UI_VOLUME_SLIDER_KNOB)
#define HIT (self->compact ? LUMA_UI_VOLUME_SLIDER_COMPACT_HIT : LUMA_UI_VOLUME_SLIDER_HIT)
#define METER_GAP LUMA_UI_VOLUME_SLIDER_METER_GAP
#define MARK LUMA_UI_VOLUME_SLIDER_MARK
#define FOCUS LUMA_UI_VOLUME_SLIDER_FOCUS

enum { NODE_TRACK, NODE_FILL, NODE_KNOB, NODE_RING, NODE_SHADOW, NODE_MARK, NODE_FOCUS, N_NODES };
static const char *node_names[N_NODES] = {"lumaui-volume-track", "lumaui-volume-fill", "lumaui-volume-knob",
                                          "lumaui-volume-ring", "lumaui-volume-shadow", "lumaui-volume-mark",
                                          "lumaui-volume-focus"};

struct _LumaVolumeSlider {
  GtkWidget parent_instance;
  GtkWidget *nodes[N_NODES];
  GtkWidget *meter;
  GtkOrientation orientation;
  double lower, upper, value, unity, step, page;
  char *unit;
  gboolean muted;
  gboolean compact;
  double grab;    /* where on the knob the drag took it, along, in px */
  double start;   /* the knob's centre when the drag began */
  gint64 last_press;
};

enum { PROP_0, PROP_VALUE, PROP_MUTED, PROP_ORIENTATION };
enum { MOVED, N_SIGNALS };
static guint signals[N_SIGNALS];

static void accessible_range_init(GtkAccessibleRangeInterface *iface);

G_DEFINE_FINAL_TYPE_WITH_CODE(LumaVolumeSlider, luma_volume_slider, GTK_TYPE_WIDGET,
                              G_IMPLEMENT_INTERFACE(GTK_TYPE_ORIENTABLE, NULL)
                                  G_IMPLEMENT_INTERFACE(GTK_TYPE_ACCESSIBLE_RANGE, accessible_range_init))

static gboolean vertical(LumaVolumeSlider *self) { return self->orientation == GTK_ORIENTATION_VERTICAL; }

static double fraction(LumaVolumeSlider *self) {
  return self->upper > self->lower ? CLAMP((self->value - self->lower) / (self->upper - self->lower), 0, 1) : 0;
}

/* Across the slider: the knob, then (with a meter) a gap and the meter, the
 * whole at least HIT wide and centred in it. */
static int meter_across(LumaVolumeSlider *self) {
  if (!self->meter || !gtk_widget_get_visible(self->meter))
    return 0;
  int min, nat;
  gtk_widget_measure(self->meter, vertical(self) ? GTK_ORIENTATION_HORIZONTAL : GTK_ORIENTATION_VERTICAL, -1, &min,
                     &nat, NULL, NULL);
  return nat;
}

static int content_across(LumaVolumeSlider *self) {
  int meter = meter_across(self);
  return meter ? KNOB + METER_GAP + meter : KNOB;
}

static int across(LumaVolumeSlider *self) { return MAX(HIT, content_across(self)); }

static double along_size(LumaVolumeSlider *self) {
  return vertical(self) ? gtk_widget_get_height(GTK_WIDGET(self)) : gtk_widget_get_width(GTK_WIDGET(self));
}

static double across_size(LumaVolumeSlider *self) {
  return vertical(self) ? gtk_widget_get_width(GTK_WIDGET(self)) : gtk_widget_get_height(GTK_WIDGET(self));
}

/* The knob's centre line across the slider. */
static double knob_axis(LumaVolumeSlider *self) {
  return floor((across_size(self) - content_across(self)) / 2) + KNOB / 2.0;
}

/* Along: the knob's centre for a fraction (vertical runs bottom to top). */
static double position(LumaVolumeSlider *self, double f) {
  double span = MAX(0, along_size(self) - KNOB);
  return KNOB / 2.0 + (vertical(self) ? 1 - f : f) * span;
}

static double fraction_at(LumaVolumeSlider *self, double at) {
  double span = along_size(self) - KNOB;
  if (span <= 0)
    return 0;
  double f = CLAMP((at - KNOB / 2.0) / span, 0, 1);
  return vertical(self) ? 1 - f : f;
}

void luma_volume_slider_set_compact(LumaVolumeSlider *self, gboolean compact) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  if (self->compact == !!compact)
    return;
  self->compact = !!compact;
  luma_ui_set_css_class(GTK_WIDGET(self), "compact", self->compact);
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

gboolean luma_volume_slider_get_compact(LumaVolumeSlider *self) {
  g_return_val_if_fail(LUMA_IS_VOLUME_SLIDER(self), FALSE);
  return self->compact;
}

void luma_volume_slider_get_track_bounds(LumaVolumeSlider *self, graphene_rect_t *bounds) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  /* From the knob's centre at one end to its centre at the other, plus the
   * track's rounded ends. */
  double length = MAX(0, along_size(self) - KNOB) + TRACK, axis = knob_axis(self);
  double start = (KNOB - TRACK) / 2.0;
  if (vertical(self))
    graphene_rect_init(bounds, axis - TRACK / 2.0, start, TRACK, length);
  else
    graphene_rect_init(bounds, start, axis - TRACK / 2.0, length, TRACK);
}

static void spoken(LumaVolumeSlider *self) {
  g_autofree char *text = NULL;
  if (g_strcmp0(self->unit, "dB") == 0)
    text = self->value <= self->lower + 0.05 ? g_strdup("−∞ dB")
           : round(self->value) == 0         ? g_strdup("0 dB")
                                             : g_strdup_printf("%+.0f dB", self->value);
  else
    text = g_strdup_printf("%.0f%%", self->value);
  if (self->muted) {
    char *muted = g_strdup_printf("%s, muted", text);
    g_free(text);
    text = muted;
  }
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_VALUE_MIN, self->lower,
                                 GTK_ACCESSIBLE_PROPERTY_VALUE_MAX, self->upper, GTK_ACCESSIBLE_PROPERTY_VALUE_NOW,
                                 self->value, GTK_ACCESSIBLE_PROPERTY_VALUE_TEXT, text, -1);
}

static gboolean store(LumaVolumeSlider *self, double value) {
  value = CLAMP(value, self->lower, self->upper);
  if (value == self->value)
    return FALSE;
  self->value = value;
  spoken(self);
  gtk_widget_queue_draw(GTK_WIDGET(self));
  g_object_notify(G_OBJECT(self), "value");
  return TRUE;
}

/* A move by the person: stored and announced as ::moved. */
static void move_to(LumaVolumeSlider *self, double value) {
  if (store(self, value))
    g_signal_emit(self, signals[MOVED], 0, self->value);
}

static gboolean accessible_set(GtkAccessibleRange *range, double value) {
  move_to(LUMA_VOLUME_SLIDER(range), value);
  return TRUE;
}

static void accessible_range_init(GtkAccessibleRangeInterface *iface) { iface->set_current_value = accessible_set; }

static void measure(GtkWidget *widget, GtkOrientation orientation, int for_size G_GNUC_UNUSED, int *minimum,
                    int *natural, int *minimum_baseline G_GNUC_UNUSED, int *natural_baseline G_GNUC_UNUSED) {
  LumaVolumeSlider *self = LUMA_VOLUME_SLIDER(widget);
  if ((orientation == GTK_ORIENTATION_VERTICAL) != vertical(self)) {
    *minimum = *natural = across(self);
  } else {
    *minimum = LUMA_UI_VOLUME_SLIDER_MIN_LENGTH;
    *natural = LUMA_UI_VOLUME_SLIDER_LENGTH;
  }
}

static void size_allocate(GtkWidget *widget, int width G_GNUC_UNUSED, int height G_GNUC_UNUSED,
                          int baseline G_GNUC_UNUSED) {
  LumaVolumeSlider *self = LUMA_VOLUME_SLIDER(widget);
  for (guint i = 0; i < N_NODES; i++) {
    int min, nat;
    gtk_widget_measure(self->nodes[i], GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
    gtk_widget_size_allocate(self->nodes[i], &(GtkAllocation){0, 0, 0, 0}, -1);
  }
  if (self->meter && gtk_widget_get_visible(self->meter)) {
    /* The meter spans exactly the track's length, beside it or under it. */
    graphene_rect_t track;
    luma_volume_slider_get_track_bounds(self, &track);
    int thick = meter_across(self);
    int at = (int)(knob_axis(self) + KNOB / 2.0) + METER_GAP;
    GtkAllocation box = vertical(self)
                            ? (GtkAllocation){at, (int)track.origin.y, thick, (int)round(track.size.height)}
                            : (GtkAllocation){(int)track.origin.x, at, (int)round(track.size.width), thick};
    int min, nat;
    gtk_widget_measure(self->meter, GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
    gtk_widget_size_allocate(self->meter, &box, -1);
  }
}

static void pill(GtkSnapshot *snapshot, const graphene_rect_t *rect, const GdkRGBA *color) {
  GskRoundedRect rounded;
  float radius = MIN(rect->size.width, rect->size.height) / 2;
  gsk_rounded_rect_init_from_rect(&rounded, rect, radius);
  gtk_snapshot_push_rounded_clip(snapshot, &rounded);
  gtk_snapshot_append_color(snapshot, color, rect);
  gtk_snapshot_pop(snapshot);
}

static void snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaVolumeSlider *self = LUMA_VOLUME_SLIDER(widget);
  GdkRGBA colors[N_NODES];
  for (guint i = 0; i < N_NODES; i++)
    gtk_widget_get_color(self->nodes[i], &colors[i]);
  graphene_rect_t track;
  luma_volume_slider_get_track_bounds(self, &track);
  double axis = knob_axis(self), knob = position(self, fraction(self));
  /* The recessed track, rounded at its ends past the knob's centres. */
  graphene_rect_t well = track;
  pill(snapshot, &well, &colors[NODE_TRACK]);
  /* The fill, from the low end to the knob. */
  graphene_rect_t fill = vertical(self)
                             ? GRAPHENE_RECT_INIT(well.origin.x, knob, TRACK, well.origin.y + well.size.height - knob)
                             : GRAPHENE_RECT_INIT(well.origin.x, well.origin.y, knob - well.origin.x, TRACK);
  if (fraction(self) > 0)
    pill(snapshot, &fill, &colors[NODE_FILL]);
  /* Unity, marked across the track where it is not an end. */
  if (!isnan(self->unity) && self->unity > self->lower && self->unity < self->upper) {
    double at = round(position(self, (self->unity - self->lower) / (self->upper - self->lower)));
    graphene_rect_t mark = vertical(self) ? GRAPHENE_RECT_INIT(axis - MARK / 2.0, at - 0.5, MARK, 1)
                                          : GRAPHENE_RECT_INIT(at - 0.5, axis - MARK / 2.0, 1, MARK);
    gtk_snapshot_append_color(snapshot, &colors[NODE_MARK], &mark);
  }
  /* The raised knob: a soft shadow, the ring, the face. */
  graphene_point_t centre = vertical(self) ? GRAPHENE_POINT_INIT(axis, knob) : GRAPHENE_POINT_INIT(knob, axis);
  graphene_rect_t face = GRAPHENE_RECT_INIT(centre.x - KNOB / 2.0, centre.y - KNOB / 2.0, KNOB, KNOB);
  GskRoundedRect round_face;
  gsk_rounded_rect_init_from_rect(&round_face, &face, KNOB / 2.0);
  if (gtk_widget_has_visible_focus(widget)) {
    graphene_rect_t halo = face;
    graphene_rect_inset(&halo, -FOCUS - 1, -FOCUS - 1);
    pill(snapshot, &halo, &colors[NODE_FOCUS]);
  }
  gtk_snapshot_append_outset_shadow(snapshot, &round_face, &colors[NODE_SHADOW], 0, 1, 0, 2);
  pill(snapshot, &face, &colors[NODE_RING]);
  graphene_rect_t inner = face;
  graphene_rect_inset(&inner, 2, 2);
  pill(snapshot, &inner, &colors[NODE_KNOB]);
  if (self->meter && gtk_widget_get_visible(self->meter))
    gtk_widget_snapshot_child(widget, self->meter, snapshot);
}

/* Pointer: a press on the knob drags it from where it was taken; a press on
 * the track jumps the knob there first. */
static void drag_begin(GtkGestureDrag *gesture, double x, double y, LumaVolumeSlider *self) {
  double at = vertical(self) ? y : x;
  /* A second press within the double-click time returns to unity. */
  int interval = 400;
  g_object_get(gtk_widget_get_settings(GTK_WIDGET(self)), "gtk-double-click-time", &interval, NULL);
  gint64 now = g_get_monotonic_time();
  gboolean twice = now - self->last_press <= (gint64)interval * 1000;
  self->last_press = twice ? 0 : now;
  if (twice && !isnan(self->unity)) {
    move_to(self, self->unity);
    self->grab = 0;
    self->start = position(self, fraction(self));
    gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
    return;
  }
  double knob = position(self, fraction(self));
  if (fabs(at - knob) <= KNOB / 2.0 + 2) {
    self->grab = at - knob;
  } else {
    self->grab = 0;
    move_to(self, self->lower + fraction_at(self, at) * (self->upper - self->lower));
  }
  self->start = position(self, fraction(self)) + self->grab;
  gtk_widget_grab_focus(GTK_WIDGET(self));
  gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
}

static void drag_update(GtkGestureDrag *gesture G_GNUC_UNUSED, double dx, double dy, LumaVolumeSlider *self) {
  double at = self->start + (vertical(self) ? dy : dx) - self->grab;
  move_to(self, self->lower + fraction_at(self, at) * (self->upper - self->lower));
}

static gboolean scrolled(GtkEventControllerScroll *controller G_GNUC_UNUSED, double dx, double dy,
                         LumaVolumeSlider *self) {
  /* Upright, a sideways scroll is left for the row around it (a mixer's strips). */
  if (vertical(self) && fabs(dx) > fabs(dy))
    return FALSE;
  double delta = fabs(dy) >= fabs(dx) ? -dy : dx;
  if (delta == 0)
    return FALSE;
  move_to(self, self->value + delta * self->step);
  return TRUE;
}

static gboolean key_pressed(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval, guint keycode G_GNUC_UNUSED,
                            GdkModifierType state G_GNUC_UNUSED, LumaVolumeSlider *self) {
  /* Arrows along the slider move it; arrows across it move focus on (a TV's
   * remote walks a column of flat sliders with Up and Down). */
  gboolean up = vertical(self) ? keyval == GDK_KEY_Up || keyval == GDK_KEY_KP_Up
                               : keyval == GDK_KEY_Right || keyval == GDK_KEY_KP_Right;
  gboolean down = vertical(self) ? keyval == GDK_KEY_Down || keyval == GDK_KEY_KP_Down
                                 : keyval == GDK_KEY_Left || keyval == GDK_KEY_KP_Left;
  if (up || down) {
    move_to(self, self->value + (up ? self->step : -self->step));
    return TRUE;
  }
  switch (keyval) {
  case GDK_KEY_Page_Up: case GDK_KEY_KP_Page_Up:
    move_to(self, self->value + self->page);
    return TRUE;
  case GDK_KEY_Page_Down: case GDK_KEY_KP_Page_Down:
    move_to(self, self->value - self->page);
    return TRUE;
  case GDK_KEY_Home: case GDK_KEY_KP_Home:
    move_to(self, self->lower);
    return TRUE;
  case GDK_KEY_End: case GDK_KEY_KP_End:
    move_to(self, self->upper);
    return TRUE;
  default:
    return FALSE;
  }
}

static void set_orientation(LumaVolumeSlider *self, GtkOrientation orientation) {
  self->orientation = orientation;
  luma_ui_set_css_class(GTK_WIDGET(self), "vertical", orientation == GTK_ORIENTATION_VERTICAL);
  luma_ui_set_css_class(GTK_WIDGET(self), "horizontal", orientation == GTK_ORIENTATION_HORIZONTAL);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_ORIENTATION, orientation, -1);
  if (self->meter)
    luma_audio_meter_set_horizontal(LUMA_AUDIO_METER(self->meter), orientation == GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

static void set_property(GObject *object, guint id, const GValue *value, GParamSpec *pspec) {
  LumaVolumeSlider *self = LUMA_VOLUME_SLIDER(object);
  switch (id) {
  case PROP_VALUE: luma_volume_slider_set_value(self, g_value_get_double(value)); break;
  case PROP_MUTED: luma_volume_slider_set_muted(self, g_value_get_boolean(value)); break;
  case PROP_ORIENTATION:
    if (self->orientation != (GtkOrientation)g_value_get_enum(value)) {
      set_orientation(self, g_value_get_enum(value));
      g_object_notify_by_pspec(object, pspec);
    }
    break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  LumaVolumeSlider *self = LUMA_VOLUME_SLIDER(object);
  switch (id) {
  case PROP_VALUE: g_value_set_double(value, self->value); break;
  case PROP_MUTED: g_value_set_boolean(value, self->muted); break;
  case PROP_ORIENTATION: g_value_set_enum(value, self->orientation); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void dispose(GObject *object) {
  LumaVolumeSlider *self = LUMA_VOLUME_SLIDER(object);
  for (guint i = 0; i < N_NODES; i++)
    g_clear_pointer(&self->nodes[i], gtk_widget_unparent);
  g_clear_pointer(&self->meter, gtk_widget_unparent);
  G_OBJECT_CLASS(luma_volume_slider_parent_class)->dispose(object);
}

static void finalize(GObject *object) {
  g_free(LUMA_VOLUME_SLIDER(object)->unit);
  G_OBJECT_CLASS(luma_volume_slider_parent_class)->finalize(object);
}

static void luma_volume_slider_class_init(LumaVolumeSliderClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->set_property = set_property;
  object_class->get_property = get_property;
  object_class->dispose = dispose;
  object_class->finalize = finalize;
  widget_class->measure = measure;
  widget_class->size_allocate = size_allocate;
  widget_class->snapshot = snapshot;
  g_object_class_install_property(object_class, PROP_VALUE,
                                  g_param_spec_double("value", NULL, NULL, -G_MAXDOUBLE, G_MAXDOUBLE, 0,
                                                      G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY |
                                                          G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(object_class, PROP_MUTED,
                                  g_param_spec_boolean("muted", NULL, NULL, FALSE,
                                                       G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY |
                                                           G_PARAM_STATIC_STRINGS));
  g_object_class_override_property(object_class, PROP_ORIENTATION, "orientation");
  /**
   * LumaVolumeSlider::moved:
   * @value: the value the person moved it to
   *
   * The person moved the volume (pointer, wheel, keys or assistive technology).
   */
  signals[MOVED] = g_signal_new("moved", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                G_TYPE_NONE, 1, G_TYPE_DOUBLE);
  gtk_widget_class_set_css_name(widget_class, "lumaui-volume-slider");
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_SLIDER);
}

static void luma_volume_slider_init(LumaVolumeSlider *self) {
  luma_ui_install();
  GtkWidget *widget = GTK_WIDGET(self);
  self->lower = 0;
  self->upper = 100;
  self->step = 1;
  self->page = 10;
  self->unity = NAN;
  self->unit = g_strdup("%");
  gtk_widget_set_focusable(widget, TRUE);
  gtk_widget_add_css_class(widget, "lumaui-volume-slider");
  for (guint i = 0; i < N_NODES; i++) {
    self->nodes[i] = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(self->nodes[i], node_names[i]);
    gtk_widget_set_child_visible(self->nodes[i], FALSE);
    gtk_widget_set_can_target(self->nodes[i], FALSE);
    gtk_accessible_update_state(GTK_ACCESSIBLE(self->nodes[i]), GTK_ACCESSIBLE_STATE_HIDDEN, TRUE, -1);
    gtk_widget_set_parent(self->nodes[i], widget);
  }
  GtkGesture *drag = gtk_gesture_drag_new();
  gtk_gesture_single_set_button(GTK_GESTURE_SINGLE(drag), GDK_BUTTON_PRIMARY);
  g_signal_connect(drag, "drag-begin", G_CALLBACK(drag_begin), self);
  g_signal_connect(drag, "drag-update", G_CALLBACK(drag_update), self);
  gtk_widget_add_controller(widget, GTK_EVENT_CONTROLLER(drag));
  GtkEventController *scroll =
      gtk_event_controller_scroll_new(GTK_EVENT_CONTROLLER_SCROLL_BOTH_AXES | GTK_EVENT_CONTROLLER_SCROLL_DISCRETE);
  g_signal_connect(scroll, "scroll", G_CALLBACK(scrolled), self);
  gtk_widget_add_controller(widget, scroll);
  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(key_pressed), self);
  gtk_widget_add_controller(widget, keys);
  set_orientation(self, GTK_ORIENTATION_VERTICAL);
  spoken(self);
}

GtkWidget *luma_volume_slider_new(GtkOrientation orientation, double lower, double upper) {
  LumaVolumeSlider *self = g_object_new(LUMA_TYPE_VOLUME_SLIDER, "orientation", orientation, NULL);
  luma_volume_slider_set_range(self, lower, upper);
  return GTK_WIDGET(self);
}

void luma_volume_slider_set_value(LumaVolumeSlider *self, double value) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  store(self, value);
}

double luma_volume_slider_get_value(LumaVolumeSlider *self) {
  g_return_val_if_fail(LUMA_IS_VOLUME_SLIDER(self), 0);
  return self->value;
}

void luma_volume_slider_set_range(LumaVolumeSlider *self, double lower, double upper) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  g_return_if_fail(upper > lower);
  self->lower = lower;
  self->upper = upper;
  /* A narrower range clamps the value; that is not the person moving it. */
  if (!store(self, self->value)) {
    spoken(self);
    gtk_widget_queue_draw(GTK_WIDGET(self));
  }
}

void luma_volume_slider_get_range(LumaVolumeSlider *self, double *lower, double *upper) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  if (lower)
    *lower = self->lower;
  if (upper)
    *upper = self->upper;
}

void luma_volume_slider_set_steps(LumaVolumeSlider *self, double step, double page) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  g_return_if_fail(step > 0 && page > 0);
  self->step = step;
  self->page = page;
}

void luma_volume_slider_set_unity(LumaVolumeSlider *self, double unity) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  self->unity = unity;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

double luma_volume_slider_get_unity(LumaVolumeSlider *self) {
  g_return_val_if_fail(LUMA_IS_VOLUME_SLIDER(self), NAN);
  return self->unity;
}

void luma_volume_slider_set_unit(LumaVolumeSlider *self, const char *unit) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  g_free(self->unit);
  self->unit = g_strdup(unit ? unit : "%");
  spoken(self);
}

void luma_volume_slider_set_muted(LumaVolumeSlider *self, gboolean muted) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  muted = !!muted;
  if (self->muted == muted)
    return;
  self->muted = muted;
  luma_ui_set_css_class(GTK_WIDGET(self), "muted", muted);
  if (muted && self->meter)
    luma_audio_meter_set_levels_db(LUMA_AUDIO_METER(self->meter), -60, -60);
  spoken(self);
  gtk_widget_queue_draw(GTK_WIDGET(self));
  g_object_notify(G_OBJECT(self), "muted");
}

gboolean luma_volume_slider_get_muted(LumaVolumeSlider *self) {
  g_return_val_if_fail(LUMA_IS_VOLUME_SLIDER(self), FALSE);
  return self->muted;
}

void luma_volume_slider_set_meter_channels(LumaVolumeSlider *self, guint channels) {
  g_return_if_fail(LUMA_IS_VOLUME_SLIDER(self));
  g_return_if_fail(channels <= 2);
  if (channels == 0) {
    if (self->meter)
      gtk_widget_set_visible(self->meter, FALSE);
  } else {
    if (!self->meter) {
      self->meter = luma_audio_meter_new(channels);
      gtk_widget_set_can_target(self->meter, FALSE);
      gtk_widget_set_parent(self->meter, GTK_WIDGET(self));
    }
    luma_audio_meter_set_channels(LUMA_AUDIO_METER(self->meter), channels);
    luma_audio_meter_set_horizontal(LUMA_AUDIO_METER(self->meter), !vertical(self));
    gtk_widget_set_visible(self->meter, TRUE);
  }
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

LumaAudioMeter *luma_volume_slider_get_meter(LumaVolumeSlider *self) {
  g_return_val_if_fail(LUMA_IS_VOLUME_SLIDER(self), NULL);
  return self->meter && gtk_widget_get_visible(self->meter) ? LUMA_AUDIO_METER(self->meter) : NULL;
}
