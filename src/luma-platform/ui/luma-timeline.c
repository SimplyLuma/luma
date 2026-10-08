/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-timeline.h"
#include "luma-ui-private.h"

#include <math.h>

typedef struct {
  gint64 offset, duration;
  guint pitch;
} Note;

typedef struct {
  char *id, *name, *kind;
  gint64 start, duration;
  GArray *peaks, *notes;
} Region;

typedef struct {
  char *id, *name, *kind, *icon;
  gboolean muted, solo, armed;
  double meter;
  GtkWidget *meter_widget; /* canvas-owned; cleared before every rebuild */
  GPtrArray *regions;
} Lane;

typedef struct {
  gint64 frame;
  char *label;
} Marker;

struct _LumaTimeline {
  GtkBox parent_instance;
  GtkWidget *header, *status, *progress, *tools, *snap_button;
  GtkWidget *headers, *scroller, *canvas, *playhead;
  GtkWidget *select_button, *blade_button;
  GPtrArray *lanes, *markers;
  guint fps_num, fps_den;
  gint64 duration, position, loop_start, loop_end;
  char *selected_id, *tool, *export_label;
  double zoom, export_progress, pps;
  gboolean snapping, phone;
  guint batch_depth;
  char *drag_id, *drag_action;
  double drag_start, drag_duration;
};

G_DEFINE_FINAL_TYPE(LumaTimeline, luma_timeline, GTK_TYPE_BOX)

enum {
  SEEK, SKIM, REGION_SELECTED, REGION_MOVE, REGION_TRIM, REGION_SPLIT,
  GESTURE_BEGIN, GESTURE_COMMIT, GESTURE_CANCEL, LANE_ACTION,
  TOOL_CHANGED, SNAPPING_CHANGED, ZOOM_CHANGED, N_SIGNALS
};
static guint signals[N_SIGNALS];

static void region_free(gpointer data) {
  Region *region = data;
  g_free(region->id);
  g_free(region->name);
  g_free(region->kind);
  g_clear_pointer(&region->peaks, g_array_unref);
  g_clear_pointer(&region->notes, g_array_unref);
  g_free(region);
}

static void lane_free(gpointer data) {
  Lane *lane = data;
  g_free(lane->id);
  g_free(lane->name);
  g_free(lane->kind);
  g_free(lane->icon);
  g_ptr_array_unref(lane->regions);
  g_free(lane);
}

static void marker_free(gpointer data) {
  Marker *marker = data;
  g_free(marker->label);
  g_free(marker);
}

static double fps(LumaTimeline *self) {
  return (double)self->fps_num / self->fps_den;
}

static double seconds(LumaTimeline *self, gint64 frame) {
  return frame / fps(self);
}

static double at_x(LumaTimeline *self, double x) {
  return CLAMP(x / self->pps, 0.0, seconds(self, self->duration));
}

static Lane *find_lane(LumaTimeline *self, const char *id) {
  for (guint i = 0; i < self->lanes->len; i++) {
    Lane *lane = g_ptr_array_index(self->lanes, i);
    if (g_strcmp0(lane->id, id) == 0)
      return lane;
  }
  return NULL;
}

static Region *find_region(LumaTimeline *self, const char *id) {
  for (guint i = 0; i < self->lanes->len; i++) {
    Lane *lane = g_ptr_array_index(self->lanes, i);
    for (guint j = 0; j < lane->regions->len; j++) {
      Region *region = g_ptr_array_index(lane->regions, j);
      if (g_strcmp0(region->id, id) == 0)
        return region;
    }
  }
  return NULL;
}

static void clear_box(GtkWidget *box) {
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(box)) != NULL)
    gtk_box_remove(GTK_BOX(box), child);
}

static void clear_fixed(GtkWidget *fixed) {
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(fixed)) != NULL)
    gtk_fixed_remove(GTK_FIXED(fixed), child);
}

static GtkWidget *icon_button(const char *icon, const char *label) {
  GtkWidget *button = luma_ui_icon_button(icon, label, "timeline-key", FALSE, NULL);
  return button;
}

static void tool_clicked(GtkButton *button, gpointer data) {
  LumaTimeline *self = data;
  luma_timeline_set_tool(self, button == GTK_BUTTON(self->blade_button) ? "blade" : "select");
  g_signal_emit(self, signals[TOOL_CHANGED], 0, self->tool);
}

static void snap_clicked(GtkButton *button G_GNUC_UNUSED, gpointer data) {
  LumaTimeline *self = data;
  luma_timeline_set_snapping(self, !self->snapping);
  g_signal_emit(self, signals[SNAPPING_CHANGED], 0, self->snapping);
}

static void zoom_clicked(GtkButton *button, gpointer data) {
  LumaTimeline *self = data;
  double factor = g_object_get_data(G_OBJECT(button), "zoom-out") ? 1.0 / 1.25 : 1.25;
  luma_timeline_set_zoom(self, self->zoom * factor);
  g_signal_emit(self, signals[ZOOM_CHANGED], 0, self->zoom);
}

static void lane_clicked(GtkButton *button, gpointer data) {
  LumaTimeline *self = data;
  const char *id = g_object_get_data(G_OBJECT(button), "lane-id");
  const char *action = g_object_get_data(G_OBJECT(button), "lane-action");
  Lane *lane = find_lane(self, id);
  if (!lane) return;
  gboolean value = !(g_strcmp0(action, "mute") == 0 ? lane->muted :
                     g_strcmp0(action, "solo") == 0 ? lane->solo : lane->armed);
  g_signal_emit(self, signals[LANE_ACTION], 0, id, action, value);
}

static void region_clicked(GtkButton *button, gpointer data) {
  LumaTimeline *self = data;
  const char *id = g_object_get_data(G_OBJECT(button), "region-id");
  Region *region = find_region(self, id);
  if (!region) return;
  if (g_strcmp0(self->tool, "blade") == 0)
    g_signal_emit(self, signals[REGION_SPLIT], 0, id,
                  seconds(self, region->start + region->duration / 2));
  else {
    luma_timeline_set_selected_region(self, id);
    g_signal_emit(self, signals[REGION_SELECTED], 0, id);
  }
}

static void drag_begin(GtkGestureDrag *gesture, double x, double y G_GNUC_UNUSED, gpointer data) {
  LumaTimeline *self = data;
  GtkWidget *button = gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(gesture));
  const char *id = g_object_get_data(G_OBJECT(button), "region-id");
  Region *region = find_region(self, id);
  if (!region) return;
  g_free(self->drag_id);
  g_free(self->drag_action);
  self->drag_id = g_strdup(id);
  self->drag_action = g_strdup(x >= gtk_widget_get_width(button) - 9 ? "trim" : "move");
  self->drag_start = seconds(self, region->start);
  self->drag_duration = seconds(self, region->duration);
  g_signal_emit(self, signals[GESTURE_BEGIN], 0, self->drag_id, self->drag_action);
}

static void drag_end(GtkGestureDrag *gesture G_GNUC_UNUSED, double dx,
                     double dy G_GNUC_UNUSED, gpointer data) {
  LumaTimeline *self = data;
  if (!self->drag_id) return;
  if (fabs(dx) < 3.0)
    g_signal_emit(self, signals[GESTURE_CANCEL], 0, self->drag_id, self->drag_action);
  else {
    double delta = round(dx / self->pps * fps(self)) / fps(self);
    if (g_strcmp0(self->drag_action, "move") == 0)
      g_signal_emit(self, signals[REGION_MOVE], 0, self->drag_id,
                    MAX(0.0, self->drag_start + delta));
    else
      g_signal_emit(self, signals[REGION_TRIM], 0, self->drag_id,
                    MAX(1.0 / fps(self), self->drag_duration + delta));
    g_signal_emit(self, signals[GESTURE_COMMIT], 0, self->drag_id, self->drag_action);
  }
  g_clear_pointer(&self->drag_id, g_free);
  g_clear_pointer(&self->drag_action, g_free);
}

static void ruler_pressed(GtkGestureClick *gesture, int n_press G_GNUC_UNUSED,
                          double x, double y G_GNUC_UNUSED, gpointer data) {
  LumaTimeline *self = data;
  GtkWidget *ruler = gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(gesture));
  double offset = gtk_widget_get_width(ruler) > 0 ? x : 0.0;
  g_signal_emit(self, signals[SEEK], 0, at_x(self, offset));
}

static void ruler_motion(GtkEventControllerMotion *motion G_GNUC_UNUSED,
                         double x, double y G_GNUC_UNUSED, gpointer data) {
  LumaTimeline *self = data;
  g_signal_emit(self, signals[SKIM], 0, at_x(self, x));
}

static void ruler_leave(GtkEventControllerMotion *motion G_GNUC_UNUSED, gpointer data) {
  g_signal_emit(data, signals[SKIM], 0, -1.0);
}

static gboolean key_pressed(GtkEventControllerKey *controller G_GNUC_UNUSED,
                            guint keyval, guint keycode G_GNUC_UNUSED,
                            GdkModifierType state G_GNUC_UNUSED, gpointer data) {
  LumaTimeline *self = data;
  if (keyval == GDK_KEY_a || keyval == GDK_KEY_b) {
    luma_timeline_set_tool(self, keyval == GDK_KEY_b ? "blade" : "select");
    g_signal_emit(self, signals[TOOL_CHANGED], 0, self->tool);
  } else if (keyval == GDK_KEY_n) {
    luma_timeline_set_snapping(self, !self->snapping);
    g_signal_emit(self, signals[SNAPPING_CHANGED], 0, self->snapping);
  } else if (keyval == GDK_KEY_Left || keyval == GDK_KEY_Right) {
    double step = (keyval == GDK_KEY_Right ? 1 : -1) / fps(self);
    g_signal_emit(self, signals[SEEK], 0, CLAMP(seconds(self, self->position) + step,
                                                  0.0, seconds(self, self->duration)));
  } else return FALSE;
  return TRUE;
}

static GtkWidget *lane_key(LumaTimeline *self, Lane *lane,
                           const char *icon, const char *label, const char *action, gboolean on) {
  GtkWidget *button = icon_button(icon, label);
  gtk_widget_add_css_class(button, "lane-key");
  luma_ui_set_css_class(button, "on", on);
  g_object_set_data_full(G_OBJECT(button), "lane-id", g_strdup(lane->id), g_free);
  g_object_set_data_full(G_OBJECT(button), "lane-action", g_strdup(action), g_free);
  g_signal_connect(button, "clicked", G_CALLBACK(lane_clicked), self);
  return button;
}

static void region_draw(GtkDrawingArea *area, cairo_t *cr, int width, int height, gpointer data) {
  Region *region = data;
  GdkRGBA ink;
  gtk_widget_get_color(GTK_WIDGET(area), &ink);
  ink.alpha *= 0.72;
  gdk_cairo_set_source_rgba(cr, &ink);
  if (region->peaks != NULL && region->peaks->len > 0) {
    for (guint i = 0; i < region->peaks->len; i++) {
      double amplitude = g_array_index(region->peaks, double, i);
      double half = CLAMP(amplitude, 0.0, 1.0) * (height - 8) * .45;
      double x = (double)i / region->peaks->len * width;
      cairo_rectangle(cr, x, height / 2.0 - half, MAX(1.0, (double)width / region->peaks->len * .65),
                      MAX(1.0, 2.0 * half));
    }
    cairo_fill(cr);
  }
  if (region->notes != NULL && region->notes->len > 0) {
    for (guint i = 0; i < region->notes->len; i++) {
      Note note = g_array_index(region->notes, Note, i);
      double x = (double)note.offset / region->duration * width;
      double w = (double)note.duration / region->duration * width;
      double y = 3 + (127.0 - note.pitch) / 127.0 * (height - 10);
      cairo_rectangle(cr, x, y, MAX(2.0, w), 4.0);
    }
    cairo_fill(cr);
  }
}

static void refresh_status(LumaTimeline *self) {
  if (self->export_label != NULL) {
    gtk_label_set_text(GTK_LABEL(self->status), self->export_label);
    gtk_progress_bar_set_fraction(GTK_PROGRESS_BAR(self->progress), self->export_progress);
    gtk_widget_set_visible(self->progress, TRUE);
  } else {
    gint64 total = (gint64)floor(seconds(self, self->duration));
    g_autofree char *text = g_strdup_printf("%" G_GINT64_FORMAT ":%02" G_GINT64_FORMAT " · %g fps",
                                            total / 60, total % 60, fps(self));
    gtk_label_set_text(GTK_LABEL(self->status), text);
    gtk_widget_set_visible(self->progress, FALSE);
  }
}

static void render(LumaTimeline *self) {
  if (self->batch_depth) return;
  g_clear_pointer(&self->drag_id, g_free);
  g_clear_pointer(&self->drag_action, g_free);
  for (guint i = 0; i < self->lanes->len; i++) {
    Lane *lane = g_ptr_array_index(self->lanes, i);
    lane->meter_widget = NULL;
  }
  clear_box(self->headers);
  clear_fixed(self->canvas);
  self->playhead = NULL;
  double duration = MAX(10.0, seconds(self, self->duration));
  self->pps = MAX(8.0, 760.0 / duration * self->zoom);
  int width = MAX(520, (int)ceil(seconds(self, self->duration) * self->pps) + 40);
  int height = 52 + 64 * (int)self->lanes->len;
  gtk_widget_set_size_request(self->canvas, width, height);
  gtk_widget_set_size_request(self->headers, self->phone ? 116 : 214, height);
  refresh_status(self);

  GtkWidget *spacer = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_size_request(spacer, -1, 52);
  gtk_box_append(GTK_BOX(self->headers), spacer);
  GtkWidget *ruler = gtk_button_new();
  gtk_widget_set_name(ruler, "timeline-ruler");
  gtk_widget_add_css_class(ruler, "timeline-ruler");
  luma_ui_set_accessible_label(ruler, "Timeline ruler; seek");
  gtk_widget_set_size_request(ruler, width, 52);
  gtk_fixed_put(GTK_FIXED(self->canvas), ruler, 0, 0);
  GtkGesture *ruler_click = gtk_gesture_click_new();
  g_signal_connect(ruler_click, "pressed", G_CALLBACK(ruler_pressed), self);
  gtk_widget_add_controller(ruler, GTK_EVENT_CONTROLLER(ruler_click));
  GtkEventController *motion = gtk_event_controller_motion_new();
  g_signal_connect(motion, "motion", G_CALLBACK(ruler_motion), self);
  g_signal_connect(motion, "leave", G_CALLBACK(ruler_leave), self);
  gtk_widget_add_controller(ruler, motion);

  int interval = self->pps > 40 ? 1 : self->pps > 18 ? 5 : 10;
  for (int sec = 0; sec <= (int)seconds(self, self->duration); sec += interval) {
    g_autofree char *label = g_strdup_printf("%d:%02d", sec / 60, sec % 60);
    GtkWidget *tick = gtk_label_new(label);
    gtk_widget_add_css_class(tick, "timeline-tick");
    gtk_widget_set_can_target(tick, FALSE);
    gtk_fixed_put(GTK_FIXED(self->canvas), tick, (int)round(sec * self->pps), 3);
  }
  for (guint i = 0; i < self->markers->len; i++) {
    Marker *marker = g_ptr_array_index(self->markers, i);
    g_autofree char *caption = g_strdup_printf("◆ %s", marker->label);
    GtkWidget *tick = gtk_label_new(caption);
    gtk_widget_add_css_class(tick, "timeline-marker");
    gtk_widget_set_tooltip_text(tick, marker->label);
    gtk_widget_set_can_target(tick, FALSE);
    gtk_fixed_put(GTK_FIXED(self->canvas), tick,
                  (int)round(seconds(self, marker->frame) * self->pps), 27);
  }
  if (self->loop_end > self->loop_start) {
    int start = (int)round(seconds(self, self->loop_start) * self->pps);
    int end = (int)round(seconds(self, self->loop_end) * self->pps);
    GtkWidget *loop = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(loop, "timeline-loop");
    gtk_widget_set_can_target(loop, FALSE);
    gtk_widget_set_size_request(loop, MAX(1, end - start), 3);
    gtk_fixed_put(GTK_FIXED(self->canvas), loop, start, 48);
  }

  for (guint i = 0; i < self->lanes->len; i++) {
    Lane *lane = g_ptr_array_index(self->lanes, i);
    int top = 52 + 64 * (int)i;
    GtkWidget *head = gtk_box_new(self->phone ? GTK_ORIENTATION_VERTICAL : GTK_ORIENTATION_HORIZONTAL, 2);
    gtk_widget_add_css_class(head, "timeline-track-head");
    gtk_widget_set_size_request(head, -1, 64);
    GtkWidget *identity = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 2);
    if (lane->icon && *lane->icon)
      gtk_box_append(GTK_BOX(identity), luma_ui_icon_image(lane->icon, 14));
    GtkWidget *name = gtk_label_new(lane->name);
    gtk_label_set_ellipsize(GTK_LABEL(name), PANGO_ELLIPSIZE_END);
    gtk_widget_set_hexpand(name, TRUE);
    gtk_box_append(GTK_BOX(identity), name);
    gtk_box_append(GTK_BOX(head), identity);
    GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_box_append(GTK_BOX(actions), lane_key(self, lane, "volume-x", "Mute", "mute", lane->muted));
    gtk_box_append(GTK_BOX(actions), lane_key(self, lane, "headphones", "Solo", "solo", lane->solo));
    gtk_box_append(GTK_BOX(actions), lane_key(self, lane, "circle", "Arm", "arm", lane->armed));
    GtkWidget *meter = gtk_level_bar_new_for_interval(0.0, 1.0);
    gtk_level_bar_set_value(GTK_LEVEL_BAR(meter), lane->meter);
    lane->meter_widget = meter;
    gtk_widget_set_size_request(meter, 24, 4);
    gtk_widget_set_tooltip_text(meter, "Track level");
    gtk_box_append(GTK_BOX(actions), meter);
    gtk_box_append(GTK_BOX(head), actions);
    gtk_box_append(GTK_BOX(self->headers), head);

    GtkWidget *band = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(band, "timeline-track");
    if (g_strcmp0(lane->kind, "audio") == 0)
      gtk_widget_add_css_class(band, "audio");
    gtk_widget_set_size_request(band, width, 64);
    gtk_fixed_put(GTK_FIXED(self->canvas), band, 0, top);
    for (guint j = 0; j < lane->regions->len; j++) {
      Region *region = g_ptr_array_index(lane->regions, j);
      int x = (int)round(seconds(self, region->start) * self->pps);
      int w = MAX(6, (int)round(seconds(self, region->duration) * self->pps) - 2);
      GtkWidget *button = gtk_button_new();
      gtk_widget_set_name(button, region->id);
      gtk_widget_add_css_class(button, "timeline-region");
      if (g_strcmp0(region->kind, "midi") == 0)
        gtk_widget_add_css_class(button, "midi");
      else if (g_strcmp0(region->kind, "audio") == 0)
        gtk_widget_add_css_class(button, "audio");
      luma_ui_set_css_class(button, "selected", g_strcmp0(self->selected_id, region->id) == 0);
      gtk_widget_set_size_request(button, w, 58);
      g_autofree char *description = g_strdup_printf("%s, %.2f seconds, %.2f seconds long",
                                                      region->name, seconds(self, region->start),
                                                      seconds(self, region->duration));
      gtk_widget_set_tooltip_text(button, description);
      luma_ui_set_accessible_label(button, description);
      GtkWidget *content = gtk_overlay_new();
      GtkWidget *drawing = gtk_drawing_area_new();
      gtk_widget_set_hexpand(drawing, TRUE);
      gtk_widget_set_vexpand(drawing, TRUE);
      gtk_drawing_area_set_draw_func(GTK_DRAWING_AREA(drawing), region_draw, region, NULL);
      gtk_overlay_set_child(GTK_OVERLAY(content), drawing);
      GtkWidget *caption = gtk_label_new(region->name);
      gtk_label_set_ellipsize(GTK_LABEL(caption), PANGO_ELLIPSIZE_END);
      gtk_widget_set_halign(caption, GTK_ALIGN_START);
      gtk_widget_set_valign(caption, GTK_ALIGN_START);
      gtk_overlay_add_overlay(GTK_OVERLAY(content), caption);
      gtk_button_set_child(GTK_BUTTON(button), content);
      g_object_set_data_full(G_OBJECT(button), "region-id", g_strdup(region->id), g_free);
      g_signal_connect(button, "clicked", G_CALLBACK(region_clicked), self);
      GtkGesture *drag = gtk_gesture_drag_new();
      g_signal_connect(drag, "drag-begin", G_CALLBACK(drag_begin), self);
      g_signal_connect(drag, "drag-end", G_CALLBACK(drag_end), self);
      gtk_widget_add_controller(button, GTK_EVENT_CONTROLLER(drag));
      gtk_fixed_put(GTK_FIXED(self->canvas), button, x, top + 3);
    }
  }
  self->playhead = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(self->playhead, "timeline-playhead");
  gtk_widget_set_can_target(self->playhead, FALSE);
  gtk_widget_set_size_request(self->playhead, 2, height);
  gtk_fixed_put(GTK_FIXED(self->canvas), self->playhead,
                (int)round(seconds(self, self->position) * self->pps), 0);
}

static void width_changed(GtkWidget *widget, int width, gpointer data G_GNUC_UNUSED) {
  LumaTimeline *self = LUMA_TIMELINE(widget);
  gboolean phone = width > 0 && width <= 600;
  if (self->phone == phone) return;
  self->phone = phone;
  luma_ui_set_css_class(widget, "phone", phone);
  render(self);
}

static void luma_timeline_dispose(GObject *object) {
  LumaTimeline *self = LUMA_TIMELINE(object);
  g_clear_pointer(&self->lanes, g_ptr_array_unref);
  g_clear_pointer(&self->markers, g_ptr_array_unref);
  g_clear_pointer(&self->selected_id, g_free);
  g_clear_pointer(&self->tool, g_free);
  g_clear_pointer(&self->export_label, g_free);
  g_clear_pointer(&self->drag_id, g_free);
  g_clear_pointer(&self->drag_action, g_free);
  G_OBJECT_CLASS(luma_timeline_parent_class)->dispose(object);
}

static void luma_timeline_class_init(LumaTimelineClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_timeline_dispose;
  GType type = G_TYPE_FROM_CLASS(klass);
  signals[SEEK] = g_signal_new("seek", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_DOUBLE);
  signals[SKIM] = g_signal_new("skim", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_DOUBLE);
  signals[REGION_SELECTED] = g_signal_new("region-selected", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                          G_TYPE_NONE, 1, G_TYPE_STRING);
  for (int i = REGION_MOVE; i <= REGION_SPLIT; i++) {
    const char *name = i == REGION_MOVE ? "region-move" : i == REGION_TRIM ? "region-trim" : "region-split";
    signals[i] = g_signal_new(name, type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                              G_TYPE_NONE, 2, G_TYPE_STRING, G_TYPE_DOUBLE);
  }
  for (int i = GESTURE_BEGIN; i <= GESTURE_CANCEL; i++) {
    const char *name = i == GESTURE_BEGIN ? "gesture-begin" : i == GESTURE_COMMIT ? "gesture-commit" : "gesture-cancel";
    signals[i] = g_signal_new(name, type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                              G_TYPE_NONE, 2, G_TYPE_STRING, G_TYPE_STRING);
  }
  signals[LANE_ACTION] = g_signal_new("lane-action", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                      G_TYPE_NONE, 3, G_TYPE_STRING, G_TYPE_STRING, G_TYPE_BOOLEAN);
  signals[TOOL_CHANGED] = g_signal_new("tool-changed", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                       G_TYPE_NONE, 1, G_TYPE_STRING);
  signals[SNAPPING_CHANGED] = g_signal_new("snapping-changed", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                           G_TYPE_NONE, 1, G_TYPE_BOOLEAN);
  signals[ZOOM_CHANGED] = g_signal_new("zoom-changed", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                       G_TYPE_NONE, 1, G_TYPE_DOUBLE);
}

static void luma_timeline_init(LumaTimeline *self) {
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-timeline");
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_focusable(GTK_WIDGET(self), TRUE);
  luma_ui_set_accessible_label(GTK_WIDGET(self), "Timeline");
  self->fps_num = 24;
  self->fps_den = 1;
  self->zoom = 1.0;
  self->snapping = TRUE;
  self->tool = g_strdup("select");
  self->lanes = g_ptr_array_new_with_free_func(lane_free);
  self->markers = g_ptr_array_new_with_free_func(marker_free);

  self->header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
  gtk_widget_add_css_class(self->header, "timeline-header");
  self->status = gtk_label_new("");
  gtk_widget_set_hexpand(self->status, TRUE);
  gtk_label_set_xalign(GTK_LABEL(self->status), 0.0);
  gtk_label_set_ellipsize(GTK_LABEL(self->status), PANGO_ELLIPSIZE_END);
  gtk_box_append(GTK_BOX(self->header), self->status);
  self->progress = gtk_progress_bar_new();
  gtk_widget_set_visible(self->progress, FALSE);
  gtk_box_append(GTK_BOX(self->header), self->progress);
  self->tools = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 2);
  gtk_widget_add_css_class(self->tools, "timeline-tools");
  self->select_button = icon_button("mouse-pointer-2", "Select");
  self->blade_button = icon_button("scissors", "Blade");
  self->snap_button = icon_button("magnet", "Snapping");
  gtk_box_append(GTK_BOX(self->tools), self->select_button);
  gtk_box_append(GTK_BOX(self->tools), self->blade_button);
  gtk_box_append(GTK_BOX(self->tools), self->snap_button);
  gtk_box_append(GTK_BOX(self->header), self->tools);
  GtkWidget *zoom_out = icon_button("zoom-out", "Zoom out");
  GtkWidget *zoom_in = icon_button("zoom-in", "Zoom in");
  g_object_set_data(G_OBJECT(zoom_out), "zoom-out", GINT_TO_POINTER(1));
  gtk_box_append(GTK_BOX(self->header), zoom_out);
  gtk_box_append(GTK_BOX(self->header), zoom_in);
  g_signal_connect(self->select_button, "clicked", G_CALLBACK(tool_clicked), self);
  g_signal_connect(self->blade_button, "clicked", G_CALLBACK(tool_clicked), self);
  g_signal_connect(self->snap_button, "clicked", G_CALLBACK(snap_clicked), self);
  g_signal_connect(zoom_out, "clicked", G_CALLBACK(zoom_clicked), self);
  g_signal_connect(zoom_in, "clicked", G_CALLBACK(zoom_clicked), self);
  gtk_box_append(GTK_BOX(self), self->header);

  GtkWidget *body = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  self->headers = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(self->headers, "timeline-heads");
  gtk_widget_set_hexpand(self->headers, FALSE);
  gtk_box_append(GTK_BOX(body), self->headers);
  self->scroller = gtk_scrolled_window_new();
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(self->scroller),
                                  GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  gtk_widget_set_hexpand(self->scroller, TRUE);
  gtk_widget_set_vexpand(self->scroller, TRUE);
  self->canvas = gtk_fixed_new();
  gtk_widget_add_css_class(self->canvas, "timeline-canvas");
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->scroller), self->canvas);
  gtk_box_append(GTK_BOX(body), self->scroller);
  gtk_box_append(GTK_BOX(self), body);
  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(key_pressed), self);
  gtk_widget_add_controller(GTK_WIDGET(self), keys);
  luma_ui_width_watch(GTK_WIDGET(self), width_changed, NULL, 600);
  luma_timeline_set_tool(self, "select");
  luma_timeline_set_snapping(self, TRUE);
  render(self);
}

GtkWidget *luma_timeline_new(void) { return g_object_new(LUMA_TYPE_TIMELINE, NULL); }

void luma_timeline_begin_update(LumaTimeline *self) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  self->batch_depth++;
}

void luma_timeline_end_update(LumaTimeline *self) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(self->batch_depth > 0);
  if (--self->batch_depth == 0) render(self);
}

void luma_timeline_clear(LumaTimeline *self) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_ptr_array_set_size(self->lanes, 0);
  g_ptr_array_set_size(self->markers, 0);
  g_clear_pointer(&self->selected_id, g_free);
  render(self);
}

void luma_timeline_set_frame_rate(LumaTimeline *self, guint numerator, guint denominator) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(numerator > 0 && denominator > 0);
  self->fps_num = numerator;
  self->fps_den = denominator;
  render(self);
}

void luma_timeline_set_duration_frames(LumaTimeline *self, gint64 frames) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(frames >= 0);
  self->duration = frames;
  self->position = MIN(self->position, frames);
  render(self);
}

void luma_timeline_set_position_frames(LumaTimeline *self, gint64 frames) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  self->position = CLAMP(frames, 0, self->duration);
  if (self->playhead)
    gtk_fixed_move(GTK_FIXED(self->canvas), self->playhead,
                   (int)round(seconds(self, self->position) * self->pps), 0);
}

void luma_timeline_set_loop_frames(LumaTimeline *self, gint64 start, gint64 end) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(start >= 0 && end >= start);
  self->loop_start = start;
  self->loop_end = end;
  render(self);
}

void luma_timeline_append_marker(LumaTimeline *self, gint64 frame, const char *label) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(frame >= 0);
  Marker *marker = g_new0(Marker, 1);
  marker->frame = frame;
  marker->label = g_strdup(label != NULL ? label : "Marker");
  g_ptr_array_add(self->markers, marker);
  render(self);
}

void luma_timeline_append_lane(LumaTimeline *self, const char *id, const char *name,
                               const char *kind, const char *icon) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(id != NULL && *id != '\0' && find_lane(self, id) == NULL);
  Lane *lane = g_new0(Lane, 1);
  lane->id = g_strdup(id);
  lane->name = g_strdup(name != NULL ? name : id);
  lane->kind = g_strdup(kind != NULL ? kind : "audio");
  lane->icon = g_strdup(icon);
  lane->regions = g_ptr_array_new_with_free_func(region_free);
  g_ptr_array_add(self->lanes, lane);
  render(self);
}

void luma_timeline_set_lane_state(LumaTimeline *self, const char *id, gboolean muted,
                                  gboolean solo, gboolean armed, double meter) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  Lane *lane = find_lane(self, id);
  g_return_if_fail(lane != NULL);
  gboolean state_changed = lane->muted != !!muted || lane->solo != !!solo || lane->armed != !!armed;
  lane->muted = !!muted;
  lane->solo = !!solo;
  lane->armed = !!armed;
  lane->meter = CLAMP(isfinite(meter) ? meter : 0.0, 0.0, 1.0);
  if (state_changed)
    render(self);
  else if (lane->meter_widget != NULL)
    gtk_level_bar_set_value(GTK_LEVEL_BAR(lane->meter_widget), lane->meter);
}

void luma_timeline_append_region(LumaTimeline *self, const char *lane_id,
                                 const char *id, const char *name, const char *kind,
                                 gint64 start_frame, gint64 duration_frames,
                                 gboolean selected) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  Lane *lane = find_lane(self, lane_id);
  g_return_if_fail(lane != NULL && id != NULL && *id != '\0' && find_region(self, id) == NULL);
  g_return_if_fail(start_frame >= 0 && duration_frames > 0);
  Region *region = g_new0(Region, 1);
  region->id = g_strdup(id);
  region->name = g_strdup(name != NULL ? name : id);
  region->kind = g_strdup(kind != NULL ? kind : "audio");
  region->start = start_frame;
  region->duration = duration_frames;
  region->peaks = g_array_new(FALSE, FALSE, sizeof(double));
  region->notes = g_array_new(FALSE, FALSE, sizeof(Note));
  g_ptr_array_add(lane->regions, region);
  if (selected) luma_timeline_set_selected_region(self, id);
  else render(self);
}

void luma_timeline_set_region_waveform(LumaTimeline *self, const char *id,
                                       const double *peaks, gsize n_peaks) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(peaks != NULL || n_peaks == 0);
  Region *region = find_region(self, id);
  g_return_if_fail(region != NULL);
  g_array_set_size(region->peaks, 0);
  for (gsize i = 0; i < n_peaks; i++) {
    double value = CLAMP(isfinite(peaks[i]) ? peaks[i] : 0.0, 0.0, 1.0);
    g_array_append_val(region->peaks, value);
  }
  render(self);
}

void luma_timeline_append_note(LumaTimeline *self, const char *id,
                               gint64 offset_frame, gint64 duration_frames, guint pitch) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  Region *region = find_region(self, id);
  g_return_if_fail(region != NULL);
  g_return_if_fail(offset_frame >= 0 && duration_frames > 0 &&
                   offset_frame < region->duration && pitch <= 127);
  Note note = {offset_frame, MIN(duration_frames, region->duration - offset_frame), pitch};
  g_array_append_val(region->notes, note);
  render(self);
}

void luma_timeline_set_selected_region(LumaTimeline *self, const char *id) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_free(self->selected_id);
  self->selected_id = g_strdup(id);
  render(self);
}

void luma_timeline_set_tool(LumaTimeline *self, const char *tool) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(g_strcmp0(tool, "select") == 0 || g_strcmp0(tool, "blade") == 0);
  g_free(self->tool);
  self->tool = g_strdup(tool);
  luma_ui_set_css_class(self->select_button, "on", g_strcmp0(tool, "select") == 0);
  luma_ui_set_css_class(self->blade_button, "on", g_strcmp0(tool, "blade") == 0);
}

void luma_timeline_set_snapping(LumaTimeline *self, gboolean enabled) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  self->snapping = !!enabled;
  luma_ui_set_css_class(self->snap_button, "on", self->snapping);
}

void luma_timeline_set_zoom(LumaTimeline *self, double zoom) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_return_if_fail(isfinite(zoom) && zoom > 0);
  self->zoom = CLAMP(zoom, 1.0, 6.0);
  render(self);
}

void luma_timeline_set_export_status(LumaTimeline *self, const char *label,
                                     double progress_fraction) {
  g_return_if_fail(LUMA_IS_TIMELINE(self));
  g_free(self->export_label);
  self->export_label = g_strdup(label);
  self->export_progress = CLAMP(isfinite(progress_fraction) ? progress_fraction : 0.0, 0.0, 1.0);
  refresh_status(self);
}
