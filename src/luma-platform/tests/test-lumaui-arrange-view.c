/* SPDX-License-Identifier: Apache-2.0 */
/* LumaArrangeView (LumaUI creative): boxes on a canvas, by hand. */
#include "luma-ui.h"

#include <math.h>

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void settle(void) {
  gboolean done = FALSE;
  g_timeout_add(250, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

typedef struct {
  char *selected;
  int selections, changes, commits;
  double x, y, w, h;
} Record;

static void on_selection(LumaArrangeView *view G_GNUC_UNUSED, const char *id, Record *record) {
  g_free(record->selected);
  record->selected = g_strdup(id);
  record->selections++;
}

static void on_changed(LumaArrangeView *view G_GNUC_UNUSED, const char *id G_GNUC_UNUSED, double x, double y,
                       double w, double h, Record *record) {
  record->changes++;
  record->x = x, record->y = y, record->w = w, record->h = h;
}

static void on_committed(LumaArrangeView *view G_GNUC_UNUSED, const char *id G_GNUC_UNUSED, double x G_GNUC_UNUSED,
                         double y G_GNUC_UNUSED, double w G_GNUC_UNUSED, double h G_GNUC_UNUSED, Record *record) {
  record->commits++;
}

static GtkEventController *controller_of(GtkWidget *widget, GType type) {
  g_autoptr(GListModel) controllers = gtk_widget_observe_controllers(widget);
  for (guint i = 0; i < g_list_model_get_n_items(controllers); i++) {
    g_autoptr(GObject) controller = g_list_model_get_item(controllers, i);
    if (G_TYPE_CHECK_INSTANCE_TYPE(controller, type))
      return GTK_EVENT_CONTROLLER(controller);
  }
  g_assert_not_reached();
}

static gboolean key(GtkWidget *view, guint keyval, GdkModifierType state) {
  gboolean handled = FALSE;
  g_signal_emit_by_name(controller_of(view, GTK_TYPE_EVENT_CONTROLLER_KEY), "key-pressed", keyval, 0, state,
                        &handled);
  return handled;
}

static void drag(GtkWidget *view, double x, double y, double dx, double dy) {
  GtkEventController *gesture = controller_of(view, GTK_TYPE_GESTURE_DRAG);
  g_signal_emit_by_name(gesture, "drag-begin", x, y);
  g_signal_emit_by_name(gesture, "drag-update", dx / 2, dy / 2);
  g_signal_emit_by_name(gesture, "drag-update", dx, dy);
  g_signal_emit_by_name(gesture, "drag-end", dx, dy);
}

static void test_snap_rule(void) {
  double lines[] = {0, 640, 960, 1920};
  double snapped;
  g_assert_cmpfloat(luma_arrange_snap(955, lines, 4, 6, &snapped), ==, 5);
  g_assert_cmpfloat(snapped, ==, 960);
  g_assert_cmpfloat(luma_arrange_snap(950, lines, 4, 6, &snapped), ==, 0);
  g_assert_true(isnan(snapped));
  /* The nearest line wins. */
  double close[] = {100, 103};
  g_assert_cmpfloat(luma_arrange_snap(102, close, 2, 6, &snapped), ==, 1);
  g_assert_cmpfloat(snapped, ==, 103);
}

static void test_view(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 960, 540);
  GtkWidget *picture = gtk_label_new("picture");
  GtkWidget *view = luma_arrange_view_new(picture);
  LumaArrangeView *arrange = LUMA_ARRANGE_VIEW(view);
  luma_arrange_view_set_canvas_size(arrange, 1920, 1080);
  luma_arrange_view_add_item(arrange, "display", "Display", 0, 0, 1920, 1080);
  luma_arrange_view_add_item(arrange, "camera", "Camera", 1400, 700, 400, 300);
  luma_arrange_view_set_item_keeps_aspect(arrange, "camera", TRUE);
  Record record = {0};
  g_signal_connect(view, "selection-changed", G_CALLBACK(on_selection), &record);
  g_signal_connect(view, "item-changed", G_CALLBACK(on_changed), &record);
  g_signal_connect(view, "item-committed", G_CALLBACK(on_committed), &record);
  gtk_window_set_child(GTK_WINDOW(window), view);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_assert_true(gtk_widget_has_css_class(view, "lumaui-arrange-view"));
  g_assert_true(gtk_widget_get_first_child(view) == picture);

  /* The canvas fits the view, centred; the picture covers exactly it. */
  int width = gtk_widget_get_width(view), height = gtk_widget_get_height(view);
  double scale = MIN(width / 1920.0, height / 1080.0);
  double vx, vy;
  luma_arrange_view_canvas_to_view(arrange, 0, 0, &vx, &vy);
  g_assert_cmpfloat(fabs(vx - (double)(int)((width - 1920 * scale) / 2)), <, 0.5);
  graphene_rect_t bounds;
  g_assert_true(gtk_widget_compute_bounds(picture, view, &bounds));
  g_assert_cmpfloat(fabs(bounds.origin.x - vx), <, 1);
  g_assert_cmpfloat(fabs(bounds.size.width - 1920 * scale), <, 1.5);

  /* Fit margins keep the canvas clear of floating panels. */
  luma_arrange_view_set_fit_margins(arrange, 100, 0, 60, 80);
  settle();
  double mx, my;
  luma_arrange_view_canvas_to_view(arrange, 0, 0, &mx, &my);
  double inner = MIN((width - 160) / 1920.0, (height - 80) / 1080.0);
  g_assert_cmpfloat(fabs(mx - (100 + (int)((width - 160 - 1920 * inner) / 2))), <, 1);
  luma_arrange_view_set_fit_margins(arrange, 0, 0, 0, 0);
  settle();
  /* Pressing picks the topmost box; pressing it again keeps it. */
  double cx, cy;
  luma_arrange_view_canvas_to_view(arrange, 1500, 800, &cx, &cy);
  drag(view, cx, cy, 0, 0);
  g_assert_cmpstr(record.selected, ==, "camera");
  g_assert_cmpstr(luma_arrange_view_get_selected(arrange), ==, "camera");
  g_assert_cmpint(record.commits, ==, 0);

  /* A drag moves it and snaps its right edge to the canvas edge. */
  drag(view, cx, cy, (118 + 3) * scale, 0);
  double x, y, w, h;
  g_assert_true(luma_arrange_view_get_item_rect(arrange, "camera", &x, &y, &w, &h));
  g_assert_cmpfloat(x + w, ==, 1920);
  g_assert_cmpfloat(y, ==, 700);
  g_assert_cmpint(record.commits, ==, 1);
  g_assert_cmpint(record.changes, >=, 2);
  /* With snapping off it lands where the pointer says. */
  luma_arrange_view_set_snapping(arrange, FALSE);
  luma_arrange_view_set_item_rect(arrange, "camera", 1400, 700, 400, 300);
  luma_arrange_view_canvas_to_view(arrange, 1500, 800, &cx, &cy);
  drag(view, cx, cy, 121 * scale, 0);
  luma_arrange_view_get_item_rect(arrange, "camera", &x, NULL, NULL, NULL);
  g_assert_cmpfloat(fabs(x - 1521), <, 0.01);
  luma_arrange_view_set_snapping(arrange, TRUE);

  /* The bottom-right handle resizes, keeping the camera's proportions. */
  luma_arrange_view_set_item_rect(arrange, "camera", 1000, 500, 400, 300);
  luma_arrange_view_canvas_to_view(arrange, 1400, 800, &cx, &cy);
  drag(view, cx, cy, 80 * scale, 0);
  luma_arrange_view_get_item_rect(arrange, "camera", &x, &y, &w, &h);
  g_assert_cmpfloat(x, ==, 1000);
  g_assert_cmpfloat(fabs(w - 480), <, 0.01);
  g_assert_cmpfloat(fabs(w / h - 4 / 3.0), <, 0.001);

  /* Keyboard: nudges, Shift nudges ten, Tab cycles, Esc clears. */
  g_assert_true(key(view, GDK_KEY_Right, 0));
  luma_arrange_view_get_item_rect(arrange, "camera", &x, NULL, NULL, NULL);
  g_assert_cmpfloat(x, ==, 1001);
  g_assert_true(key(view, GDK_KEY_Down, GDK_SHIFT_MASK));
  luma_arrange_view_get_item_rect(arrange, "camera", NULL, &y, NULL, NULL);
  g_assert_cmpfloat(y, ==, 510);
  g_assert_true(key(view, GDK_KEY_Tab, 0));
  g_assert_cmpstr(record.selected, ==, "display");
  g_assert_false(key(view, GDK_KEY_Tab, 0)); /* past the bottom: focus moves on */
  g_assert_true(key(view, GDK_KEY_Escape, 0));
  g_assert_null(record.selected);
  g_assert_false(key(view, GDK_KEY_Escape, 0));

  /* Locked: selectable, not movable. Hidden: not pickable. */
  luma_arrange_view_set_item_locked(arrange, "camera", TRUE);
  luma_arrange_view_set_selected(arrange, "camera");
  int commits = record.commits;
  g_assert_false(key(view, GDK_KEY_Left, 0));
  luma_arrange_view_canvas_to_view(arrange, 1100, 600, &cx, &cy);
  drag(view, cx, cy, 50, 50);
  g_assert_cmpint(record.commits, ==, commits);
  luma_arrange_view_set_item_visible(arrange, "camera", FALSE);
  drag(view, cx, cy, 0, 0);
  g_assert_cmpstr(record.selected, ==, "display");

  /* Order: set_order puts named boxes on top, in order. */
  const char *order[] = {"camera", "display", NULL};
  luma_arrange_view_set_item_visible(arrange, "camera", TRUE);
  luma_arrange_view_set_order(arrange, order);
  drag(view, cx, cy, 0, 0);
  g_assert_cmpstr(record.selected, ==, "display");
  luma_arrange_view_remove_item(arrange, "display");
  g_assert_null(luma_arrange_view_get_selected(arrange));
  g_assert_false(luma_arrange_view_get_item_rect(arrange, "display", NULL, NULL, NULL, NULL));
  luma_arrange_view_clear(arrange);
  g_assert_false(luma_arrange_view_get_item_rect(arrange, "camera", NULL, NULL, NULL, NULL));
  g_free(record.selected);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  gtk_init();
  g_test_add_func("/lumaui/creative/arrange/snap", test_snap_rule);
  g_test_add_func("/lumaui/creative/arrange/view", test_view);
  return g_test_run();
}
