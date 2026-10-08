/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-curve-editor.h"

typedef struct { double x; double y; } CurvePoint;
struct _LumaCurveEditor {
  GtkDrawingArea parent_instance;
  GArray *points;
  int selected;
};
G_DEFINE_FINAL_TYPE(LumaCurveEditor, luma_curve_editor, GTK_TYPE_DRAWING_AREA)
enum { CHANGED, N_SIGNALS };
static guint signals[N_SIGNALS];

static int compare_points(gconstpointer a, gconstpointer b) {
  const CurvePoint *left = a, *right = b;
  return (left->x > right->x) - (left->x < right->x);
}

static void draw(GtkDrawingArea *area, cairo_t *cr, int width, int height,
                 gpointer user_data) {
  LumaCurveEditor *self = LUMA_CURVE_EDITOR(area);
  (void)user_data;
  cairo_set_source_rgba(cr, .5, .5, .5, .18);
  for (guint i = 1; i < 4; i++) {
    cairo_move_to(cr, width * i / 4.0, 0); cairo_line_to(cr, width * i / 4.0, height);
    cairo_move_to(cr, 0, height * i / 4.0); cairo_line_to(cr, width, height * i / 4.0);
  }
  cairo_set_line_width(cr, 1); cairo_stroke(cr);
  cairo_set_source_rgba(cr, .88, .9, .94, .96);
  cairo_set_line_width(cr, 2);
  for (guint i = 0; i < self->points->len; i++) {
    CurvePoint point = g_array_index(self->points, CurvePoint, i);
    if (i == 0) cairo_move_to(cr, point.x * width, (1 - point.y) * height);
    else cairo_line_to(cr, point.x * width, (1 - point.y) * height);
  }
  cairo_stroke(cr);
  for (guint i = 0; i < self->points->len; i++) {
    CurvePoint point = g_array_index(self->points, CurvePoint, i);
    cairo_arc(cr, point.x * width, (1 - point.y) * height, i == (guint)self->selected ? 5 : 4, 0, G_PI * 2);
    cairo_set_source_rgba(cr, i == (guint)self->selected ? .28 : .88, i == (guint)self->selected ? .55 : .9, .96, 1);
    cairo_fill(cr);
  }
}

static void update_point(LumaCurveEditor *self, double x, double y, int width, int height) {
  CurvePoint *point;
  if (self->selected < 0 || self->selected >= (int)self->points->len) return;
  point = &g_array_index(self->points, CurvePoint, self->selected);
  point->x = CLAMP(x / MAX(1, width), self->selected == 0 ? 0 : g_array_index(self->points, CurvePoint, self->selected - 1).x + .005,
                   self->selected == (int)self->points->len - 1 ? 1 : g_array_index(self->points, CurvePoint, self->selected + 1).x - .005);
  point->y = CLAMP(1.0 - y / MAX(1, height), 0, 1);
  gtk_widget_queue_draw(GTK_WIDGET(self));
  g_signal_emit(self, signals[CHANGED], 0);
}

static void drag_begin(GtkGestureDrag *gesture, double x, double y, gpointer data) {
  LumaCurveEditor *self = data;
  const int width = gtk_widget_get_width(GTK_WIDGET(self));
  const int height = gtk_widget_get_height(GTK_WIDGET(self));
  double best = 144;
  self->selected = -1;
  for (guint i = 0; i < self->points->len; i++) {
    CurvePoint p = g_array_index(self->points, CurvePoint, i);
    const double delta_x = p.x * width - x;
    const double delta_y = (1 - p.y) * height - y;
    double distance = delta_x * delta_x + delta_y * delta_y;
    if (distance < best) { best = distance; self->selected = i; }
  }
  if (self->selected < 0 && self->points->len < 16) {
    CurvePoint p = {CLAMP(x / MAX(1, width), 0, 1), CLAMP(1 - y / MAX(1, height), 0, 1)};
    g_array_append_val(self->points, p); g_array_sort(self->points, compare_points);
    for (guint i = 0; i < self->points->len; i++) {
      const double delta = g_array_index(self->points, CurvePoint, i).x - p.x;
      if (delta > -.001 && delta < .001)
        self->selected = i;
    }
    g_signal_emit(self, signals[CHANGED], 0);
  }
  g_object_set_data(G_OBJECT(gesture), "start-x", GINT_TO_POINTER((int)x));
  g_object_set_data(G_OBJECT(gesture), "start-y", GINT_TO_POINTER((int)y));
  gtk_widget_grab_focus(GTK_WIDGET(self));
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void drag_update(GtkGestureDrag *gesture, double dx, double dy, gpointer data) {
  LumaCurveEditor *self = data;
  update_point(self, GPOINTER_TO_INT(g_object_get_data(G_OBJECT(gesture), "start-x")) + dx,
               GPOINTER_TO_INT(g_object_get_data(G_OBJECT(gesture), "start-y")) + dy,
               gtk_widget_get_width(GTK_WIDGET(self)), gtk_widget_get_height(GTK_WIDGET(self)));
}

static gboolean key_pressed(GtkEventControllerKey *controller, guint keyval,
                            guint keycode, GdkModifierType state, gpointer data) {
  LumaCurveEditor *self = data;
  CurvePoint *point;
  double step = (state & GDK_SHIFT_MASK) ? .04 : .01;
  (void)controller; (void)keycode;
  if (self->selected < 0 || self->selected >= (int)self->points->len) return FALSE;
  if ((keyval == GDK_KEY_Delete || keyval == GDK_KEY_BackSpace) && self->selected > 0 && self->selected < (int)self->points->len - 1) {
    g_array_remove_index(self->points, self->selected); self->selected = MIN(self->selected, (int)self->points->len - 1);
  } else {
    point = &g_array_index(self->points, CurvePoint, self->selected);
    if (keyval == GDK_KEY_Left) point->x = MAX(self->selected == 0 ? 0 : g_array_index(self->points, CurvePoint, self->selected - 1).x + .005, point->x - step);
    else if (keyval == GDK_KEY_Right) point->x = MIN(self->selected == (int)self->points->len - 1 ? 1 : g_array_index(self->points, CurvePoint, self->selected + 1).x - .005, point->x + step);
    else if (keyval == GDK_KEY_Up) point->y = MIN(1, point->y + step);
    else if (keyval == GDK_KEY_Down) point->y = MAX(0, point->y - step);
    else return FALSE;
  }
  gtk_widget_queue_draw(GTK_WIDGET(self)); g_signal_emit(self, signals[CHANGED], 0); return TRUE;
}

static void luma_curve_editor_finalize(GObject *object) {
  g_array_unref(LUMA_CURVE_EDITOR(object)->points);
  G_OBJECT_CLASS(luma_curve_editor_parent_class)->finalize(object);
}
static void luma_curve_editor_class_init(LumaCurveEditorClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = luma_curve_editor_finalize;
  signals[CHANGED] = g_signal_new("changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
}
static void luma_curve_editor_init(LumaCurveEditor *self) {
  GtkGesture *drag = gtk_gesture_drag_new();
  GtkEventController *keys = gtk_event_controller_key_new();
  self->points = g_array_new(FALSE, FALSE, sizeof(CurvePoint)); self->selected = -1;
  gtk_widget_set_focusable(GTK_WIDGET(self), TRUE);
  gtk_drawing_area_set_content_height(GTK_DRAWING_AREA(self), 178);
  gtk_drawing_area_set_draw_func(GTK_DRAWING_AREA(self), draw, NULL, NULL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-curve-editor");
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_LABEL, "Tone curve", -1);
  g_signal_connect(drag, "drag-begin", G_CALLBACK(drag_begin), self);
  g_signal_connect(drag, "drag-update", G_CALLBACK(drag_update), self);
  g_signal_connect(keys, "key-pressed", G_CALLBACK(key_pressed), self);
  gtk_widget_add_controller(GTK_WIDGET(self), GTK_EVENT_CONTROLLER(drag));
  gtk_widget_add_controller(GTK_WIDGET(self), keys);
  luma_curve_editor_reset(self);
}
GtkWidget *luma_curve_editor_new(void) { return g_object_new(LUMA_TYPE_CURVE_EDITOR, NULL); }
void luma_curve_editor_set_points(LumaCurveEditor *self, GVariant *points) {
  GVariantIter iter; double x, y;
  g_return_if_fail(LUMA_IS_CURVE_EDITOR(self));
  g_return_if_fail(points && g_variant_is_of_type(points, G_VARIANT_TYPE("a(dd)")));
  g_array_set_size(self->points, 0); g_variant_iter_init(&iter, points);
  while (g_variant_iter_loop(&iter, "(dd)", &x, &y)) { CurvePoint p = {CLAMP(x, 0, 1), CLAMP(y, 0, 1)}; g_array_append_val(self->points, p); }
  if (self->points->len < 2) luma_curve_editor_reset(self); else { g_array_sort(self->points, compare_points); gtk_widget_queue_draw(GTK_WIDGET(self)); }
}
GVariant *luma_curve_editor_get_points(LumaCurveEditor *self) {
  GVariantBuilder builder; g_return_val_if_fail(LUMA_IS_CURVE_EDITOR(self), NULL);
  g_variant_builder_init(&builder, G_VARIANT_TYPE("a(dd)"));
  for (guint i = 0; i < self->points->len; i++) { CurvePoint p = g_array_index(self->points, CurvePoint, i); g_variant_builder_add(&builder, "(dd)", p.x, p.y); }
  return g_variant_ref_sink(g_variant_builder_end(&builder));
}
void luma_curve_editor_reset(LumaCurveEditor *self) {
  CurvePoint start = {0, 0}, end = {1, 1}; g_return_if_fail(LUMA_IS_CURVE_EDITOR(self));
  g_array_set_size(self->points, 0); g_array_append_val(self->points, start); g_array_append_val(self->points, end); self->selected = -1; gtk_widget_queue_draw(GTK_WIDGET(self));
}

