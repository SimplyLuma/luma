/* SPDX-License-Identifier: Apache-2.0 */
/* LumaArrangeView (LumaUI creative): boxes on a canvas, by hand. */
#include "luma-ui.h"

#include <math.h>

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void spin_frames(void) {
  for (int i = 0; i < 20; i++)
    g_main_context_iteration(NULL, FALSE);
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

static void modifier(GtkWidget *view, guint keyval, gboolean down, GdkModifierType state) {
  GtkEventController *keys = controller_of(view, GTK_TYPE_EVENT_CONTROLLER_KEY);
  gboolean handled = FALSE;
  if (down)
    g_signal_emit_by_name(keys, "key-pressed", keyval, 0, state, &handled);
  else
    g_signal_emit_by_name(keys, "key-released", keyval, 0, state);
}

typedef struct {
  int changes, commits;
  double c[4];
} Crops;

static void on_crop(LumaArrangeView *view G_GNUC_UNUSED, const char *id G_GNUC_UNUSED, double l, double t, double r,
                    double b, Crops *crops) {
  crops->changes++;
  crops->c[0] = l, crops->c[1] = t, crops->c[2] = r, crops->c[3] = b;
}

static void on_crop_committed(LumaArrangeView *view G_GNUC_UNUSED, const char *id G_GNUC_UNUSED,
                              double l G_GNUC_UNUSED, double t G_GNUC_UNUSED, double r G_GNUC_UNUSED,
                              double b G_GNUC_UNUSED, Crops *crops) {
  crops->commits++;
}

static void on_activated(LumaArrangeView *view G_GNUC_UNUSED, const char *id, char **activated) {
  g_free(*activated);
  *activated = g_strdup(id);
}

/* The wide box (32:9, keeps its aspect, croppable) back where it starts, uncropped. */
static void reset(LumaArrangeView *arrange) {
  luma_arrange_view_set_item_rect(arrange, "wide", 320, 440, 1280, 360);
  luma_arrange_view_set_item_crop(arrange, "wide", 0, 0, 0, 0);
  luma_arrange_view_set_selected(arrange, "wide");
}

static void assert_rect(LumaArrangeView *arrange, double ex, double ey, double ew, double eh) {
  double x, y, w, h;
  g_assert_true(luma_arrange_view_get_item_rect(arrange, "wide", &x, &y, &w, &h));
  if (fabs(x - ex) > 0.01 || fabs(y - ey) > 0.01 || fabs(w - ew) > 0.01 || fabs(h - eh) > 0.01)
    g_error("rect %.2f %.2f %.2f %.2f, expected %.2f %.2f %.2f %.2f", x, y, w, h, ex, ey, ew, eh);
}

static void assert_crop(LumaArrangeView *arrange, double el, double et, double er, double eb) {
  double l, t, r, b;
  g_assert_true(luma_arrange_view_get_item_crop(arrange, "wide", &l, &t, &r, &b));
  if (fabs(l - el) > 1e-6 || fabs(t - et) > 1e-6 || fabs(r - er) > 1e-6 || fabs(b - eb) > 1e-6)
    g_error("crop %.4f %.4f %.4f %.4f, expected %.4f %.4f %.4f %.4f", l, t, r, b, el, et, er, eb);
}

/* Drag a handle at canvas (hx, hy) by (dx, dy) canvas units with @keyval held throughout (0: none);
 * @mask is every modifier down while it is, the ones pressed before it too. */
static void pull(GtkWidget *view, double hx, double hy, double dx, double dy, guint keyval, GdkModifierType mask) {
  LumaArrangeView *arrange = LUMA_ARRANGE_VIEW(view);
  double vx, vy, ox, oy;
  luma_arrange_view_canvas_to_view(arrange, hx, hy, &vx, &vy);
  luma_arrange_view_canvas_to_view(arrange, 0, 0, &ox, &oy);
  double ex, ey;
  luma_arrange_view_canvas_to_view(arrange, 1, 1, &ex, &ey);
  double scale = ex - ox;
  GdkModifierType own = keyval == GDK_KEY_Shift_L ? GDK_SHIFT_MASK
                        : keyval == GDK_KEY_Control_L ? GDK_CONTROL_MASK
                        : keyval == GDK_KEY_Alt_L ? GDK_ALT_MASK : 0;
  if (keyval != 0)
    modifier(view, keyval, TRUE, mask & ~own); /* the others already held */
  GtkEventController *gesture = controller_of(view, GTK_TYPE_GESTURE_DRAG);
  g_signal_emit_by_name(gesture, "drag-begin", vx, vy);
  g_signal_emit_by_name(gesture, "drag-update", dx * scale / 2, dy * scale / 2);
  g_signal_emit_by_name(gesture, "drag-update", dx * scale, dy * scale);
  g_signal_emit_by_name(gesture, "drag-end", dx * scale, dy * scale);
  if (keyval != 0)
    modifier(view, keyval, FALSE, mask);
}

static void test_modifiers(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 960, 540);
  GtkWidget *view = luma_arrange_view_new(gtk_label_new("picture"));
  LumaArrangeView *arrange = LUMA_ARRANGE_VIEW(view);
  luma_arrange_view_add_item(arrange, "text", "Text", 100, 100, 200, 100);
  luma_arrange_view_add_item(arrange, "wide", "Wide camera", 320, 440, 1280, 360);
  luma_arrange_view_set_item_keeps_aspect(arrange, "wide", TRUE);
  luma_arrange_view_set_item_croppable(arrange, "wide", TRUE);
  Crops crops = {0};
  Record record = {0};
  char *activated = NULL;
  g_signal_connect(view, "crop-changed", G_CALLBACK(on_crop), &crops);
  g_signal_connect(view, "crop-committed", G_CALLBACK(on_crop_committed), &crops);
  g_signal_connect(view, "item-committed", G_CALLBACK(on_committed), &record);
  g_signal_connect(view, "item-activated", G_CALLBACK(on_activated), &activated);
  gtk_window_set_child(GTK_WINDOW(window), view);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  luma_arrange_view_set_snapping(arrange, FALSE);
  g_assert_cmpstr(luma_arrange_view_get_drag_mode(arrange), ==, "none");

  /* The right edge (1600, 620): plain scales from the left edge, centred across it. */
  reset(arrange);
  pull(view, 1600, 620, 320, 0, 0, 0);
  assert_rect(arrange, 320, 395, 1600, 450);
  /* Alt: from the centre, the 32:9 kept: 1280 + 2 x 320 wide, centred on (960, 620). */
  reset(arrange);
  pull(view, 1600, 620, 320, 0, GDK_KEY_Alt_L, GDK_ALT_MASK);
  assert_rect(arrange, 0, 350, 1920, 540);
  /* Shift stretches; Alt+Shift stretches from the centre. */
  reset(arrange);
  pull(view, 1600, 620, 320, 0, GDK_KEY_Shift_L, GDK_SHIFT_MASK);
  assert_rect(arrange, 320, 440, 1600, 360);
  reset(arrange);
  modifier(view, GDK_KEY_Shift_L, TRUE, 0);
  pull(view, 1600, 620, 320, 0, GDK_KEY_Alt_L, GDK_ALT_MASK | GDK_SHIFT_MASK);
  modifier(view, GDK_KEY_Shift_L, FALSE, GDK_SHIFT_MASK);
  assert_rect(arrange, 0, 440, 1920, 360);
  /* Corners: the opposite corner stays; Alt keeps the centre. */
  reset(arrange);
  pull(view, 1600, 800, 320, 0, 0, 0);
  assert_rect(arrange, 320, 440, 1600, 450);
  reset(arrange);
  pull(view, 320, 440, -320, -90, GDK_KEY_Alt_L, GDK_ALT_MASK);
  assert_rect(arrange, 0, 350, 1920, 540);
  /* The bottom edge, Alt: the top moves as much, the 32:9 kept across it. */
  reset(arrange);
  pull(view, 960, 800, 0, 90, GDK_KEY_Alt_L, GDK_ALT_MASK);
  assert_rect(arrange, 0, 350, 1920, 540);

  /* Modifiers switch the drag live, with no pointer motion. */
  reset(arrange);
  double vx, vy, scale = 0;
  luma_arrange_view_canvas_to_view(arrange, 1600, 620, &vx, &vy);
  {
    double ox, oy, ex, ey;
    luma_arrange_view_canvas_to_view(arrange, 0, 0, &ox, &oy);
    luma_arrange_view_canvas_to_view(arrange, 1, 1, &ex, &ey);
    scale = ex - ox;
  }
  GtkEventController *gesture = controller_of(view, GTK_TYPE_GESTURE_DRAG);
  g_signal_emit_by_name(gesture, "drag-begin", vx, vy);
  g_assert_cmpstr(luma_arrange_view_get_drag_mode(arrange), ==, "resize");
  g_signal_emit_by_name(gesture, "drag-update", 320 * scale, 0.0);
  assert_rect(arrange, 320, 395, 1600, 450);
  modifier(view, GDK_KEY_Shift_L, TRUE, 0);
  g_assert_cmpstr(luma_arrange_view_get_drag_mode(arrange), ==, "stretch");
  assert_rect(arrange, 320, 440, 1600, 360);
  modifier(view, GDK_KEY_Alt_L, TRUE, GDK_SHIFT_MASK);
  g_assert_cmpstr(luma_arrange_view_get_drag_mode(arrange), ==, "stretch-centre");
  assert_rect(arrange, 0, 440, 1920, 360);
  modifier(view, GDK_KEY_Shift_L, FALSE, GDK_SHIFT_MASK | GDK_ALT_MASK);
  g_assert_cmpstr(luma_arrange_view_get_drag_mode(arrange), ==, "resize-centre");
  assert_rect(arrange, 0, 350, 1920, 540);
  modifier(view, GDK_KEY_Alt_L, FALSE, GDK_ALT_MASK);
  modifier(view, GDK_KEY_Control_L, TRUE, 0);
  g_assert_cmpstr(luma_arrange_view_get_drag_mode(arrange), ==, "crop");
  assert_rect(arrange, 320, 440, 1280, 360); /* pulled out past the source: nothing to give back */
  modifier(view, GDK_KEY_Control_L, FALSE, GDK_CONTROL_MASK);
  g_signal_emit_by_name(gesture, "drag-end", 320 * scale, 0.0);
  g_assert_cmpstr(luma_arrange_view_get_drag_mode(arrange), ==, "none");
  assert_rect(arrange, 320, 395, 1600, 450);

  /* Ctrl crops that edge: a tenth off the right, then as much as may go off the left, then back. */
  reset(arrange);
  crops.changes = crops.commits = 0;
  int commits = record.commits;
  pull(view, 1600, 620, -128, 0, GDK_KEY_Control_L, GDK_CONTROL_MASK);
  assert_crop(arrange, 0, 0, 0.1, 0);
  assert_rect(arrange, 320, 440, 1152, 360);
  g_assert_cmpint(crops.changes, >=, 2);
  g_assert_cmpint(crops.commits, ==, 1);
  g_assert_cmpint(record.commits, ==, commits + 1);
  g_assert_cmpfloat(fabs(crops.c[2] - 0.1), <, 1e-6);
  pull(view, 320, 620, 5000, 0, GDK_KEY_Control_L, GDK_CONTROL_MASK);
  assert_crop(arrange, 0.49, 0, 0.1, 0);
  assert_rect(arrange, 320 + 0.49 * 1280, 440, 1280 * (1 - 0.59), 360);
  pull(view, 320 + 0.49 * 1280, 620, -5000, 0, GDK_KEY_Control_L, GDK_CONTROL_MASK);
  assert_crop(arrange, 0, 0, 0.1, 0);
  assert_rect(arrange, 320, 440, 1152, 360);
  /* Ctrl+Alt crops both opposite edges by as much; a corner crops both of its edges. */
  reset(arrange);
  modifier(view, GDK_KEY_Control_L, TRUE, 0);
  pull(view, 1600, 620, -128, 0, GDK_KEY_Alt_L, GDK_ALT_MASK | GDK_CONTROL_MASK);
  modifier(view, GDK_KEY_Control_L, FALSE, GDK_CONTROL_MASK);
  assert_crop(arrange, 0.1, 0, 0.1, 0);
  assert_rect(arrange, 448, 440, 1024, 360);
  reset(arrange);
  pull(view, 1600, 800, -128, -36, GDK_KEY_Control_L, GDK_CONTROL_MASK);
  assert_crop(arrange, 0, 0, 0.1, 0.1);
  /* A crop's edge snaps to the guides: the canvas's two thirds (1280). */
  reset(arrange);
  luma_arrange_view_set_snapping(arrange, TRUE);
  pull(view, 1600, 620, -316, 0, GDK_KEY_Control_L, GDK_CONTROL_MASK);
  assert_crop(arrange, 0, 0, 0.25, 0);
  luma_arrange_view_set_snapping(arrange, FALSE);

  /* Crop mode: Enter, plain handles crop, a drag inside slides the source, Esc leaves. */
  reset(arrange);
  g_assert_true(key(view, GDK_KEY_Return, 0));
  g_assert_cmpstr(luma_arrange_view_get_cropping(arrange), ==, "wide");
  pull(view, 1600, 620, -128, 0, 0, 0);
  assert_crop(arrange, 0, 0, 0.1, 0);
  pull(view, 320, 620, 128, 0, 0, 0);
  assert_crop(arrange, 0.1, 0, 0.1, 0);
  assert_rect(arrange, 448, 440, 1024, 360);
  pull(view, 960, 620, 64, 0, 0, 0); /* the source moves right: less cut on the left */
  assert_crop(arrange, 0.05, 0, 0.15, 0);
  assert_rect(arrange, 448, 440, 1024, 360);
  g_assert_true(key(view, GDK_KEY_Escape, 0));
  g_assert_null(luma_arrange_view_get_cropping(arrange));
  g_assert_cmpstr(luma_arrange_view_get_selected(arrange), ==, "wide");
  /* Out of crop mode a plain drag resizes again, the crop kept. */
  pull(view, 448 + 1024, 620, 256, 0, 0, 0);
  assert_crop(arrange, 0.05, 0, 0.15, 0);
  /* A double click crops a croppable box and activates any other. */
  GtkEventController *click = controller_of(view, GTK_TYPE_GESTURE_CLICK);
  luma_arrange_view_canvas_to_view(arrange, 960, 620, &vx, &vy);
  g_signal_emit_by_name(click, "pressed", 2, vx, vy);
  g_assert_cmpstr(luma_arrange_view_get_cropping(arrange), ==, "wide");
  g_assert_null(activated);
  luma_arrange_view_canvas_to_view(arrange, 150, 150, &vx, &vy);
  g_signal_emit_by_name(click, "pressed", 2, vx, vy);
  g_assert_cmpstr(activated, ==, "text");
  luma_arrange_view_set_selected(arrange, "text"); /* another selection leaves crop mode */
  g_assert_null(luma_arrange_view_get_cropping(arrange));
  /* The shown rectangle: set, read back, cleared; the live one is untouched. */
  luma_arrange_view_set_item_shown_rect(arrange, "wide", 300, 430, 1200, 340);
  double sx, sy, sw, sh;
  g_assert_true(luma_arrange_view_get_item_shown_rect(arrange, "wide", &sx, &sy, &sw, &sh));
  g_assert_cmpfloat(sx, ==, 300);
  g_assert_cmpfloat(sh, ==, 340);
  gtk_widget_queue_draw(view);
  spin_frames();
  luma_arrange_view_clear_item_shown_rect(arrange, "wide");
  g_assert_false(luma_arrange_view_get_item_shown_rect(arrange, "wide", NULL, NULL, NULL, NULL));
  /* The app's crop is clamped to 0..0.49 a side. */
  luma_arrange_view_set_item_crop(arrange, "wide", 0.7, -1, 0.2, 0.3);
  assert_crop(arrange, 0.49, 0, 0.2, 0.3);
  g_free(activated);
  g_free(record.selected);
  gtk_window_destroy(GTK_WINDOW(window));
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

typedef struct {
  int ids_changes, group_changes, group_commits;
  char **last_ids;
  GVariant *last_group;
} Multi;

static void on_ids(LumaArrangeView *view G_GNUC_UNUSED, char **ids, Multi *m) {
  m->ids_changes++;
  g_strfreev(m->last_ids);
  m->last_ids = g_strdupv(ids);
}

static void on_group(LumaArrangeView *view G_GNUC_UNUSED, GVariant *rects, Multi *m) {
  m->group_changes++;
  g_clear_pointer(&m->last_group, g_variant_unref);
  m->last_group = g_variant_ref(rects);
}

static void on_group_commit(LumaArrangeView *view G_GNUC_UNUSED, GVariant *rects G_GNUC_UNUSED, Multi *m) {
  m->group_commits++;
}

static void group_rect(Multi *m, const char *id, double *x, double *y, double *w, double *h) {
  g_assert_true(g_variant_lookup(m->last_group, id, "(dddd)", x, y, w, h));
}

static void test_multi(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 960, 540);
  GtkWidget *view = luma_arrange_view_new(gtk_label_new("picture"));
  LumaArrangeView *arrange = LUMA_ARRANGE_VIEW(view);
  luma_arrange_view_add_item(arrange, "a", "A", 100, 100, 200, 100);
  luma_arrange_view_add_item(arrange, "b", "B", 500, 300, 100, 100);
  luma_arrange_view_add_item(arrange, "c", "C", 1500, 800, 100, 100);
  luma_arrange_view_set_snapping(arrange, FALSE);
  Multi m = {0};
  g_signal_connect(view, "selection-ids-changed", G_CALLBACK(on_ids), &m);
  g_signal_connect(view, "group-changed", G_CALLBACK(on_group), &m);
  g_signal_connect(view, "group-committed", G_CALLBACK(on_group_commit), &m);
  gtk_window_set_child(GTK_WINDOW(window), view);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  /* A click selects one; Shift+click adds and removes. */
  pull(view, 200, 150, 0, 0, 0, 0);
  g_assert_cmpstr(luma_arrange_view_get_selected(arrange), ==, "a");
  pull(view, 550, 350, 0, 0, GDK_KEY_Shift_L, GDK_SHIFT_MASK);
  g_auto(GStrv) two = luma_arrange_view_get_selection(arrange);
  g_assert_cmpuint(g_strv_length(two), ==, 2);
  g_assert_cmpstr(two[0], ==, "a");
  g_assert_cmpstr(two[1], ==, "b");
  g_assert_cmpstr(luma_arrange_view_get_selected(arrange), ==, "b");
  g_assert_cmpstr(m.last_ids[1], ==, "b");
  /* Dragging one of them moves both. */
  pull(view, 200, 150, 50, 20, 0, 0);
  g_assert_cmpint(m.group_commits, ==, 1);
  double x, y, w, h;
  group_rect(&m, "a", &x, &y, &w, &h);
  g_assert_cmpfloat(fabs(x - 150), <, 0.01);
  g_assert_cmpfloat(fabs(y - 120), <, 0.01);
  luma_arrange_view_get_item_rect(arrange, "b", &x, &y, NULL, NULL);
  g_assert_cmpfloat(fabs(x - 550), <, 0.01);
  /* The group's right handle scales both inside the box, in proportion (box 150..650 x 120..420). */
  pull(view, 650, 270, 500, 0, 0, 0);
  luma_arrange_view_get_item_rect(arrange, "a", &x, &y, &w, &h);
  g_assert_cmpfloat(fabs(x - 150), <, 0.01);
  g_assert_cmpfloat(fabs(w - 400), <, 0.01);
  g_assert_cmpfloat(fabs(h - 200), <, 0.01);
  luma_arrange_view_get_item_rect(arrange, "b", &x, &y, &w, &h);
  g_assert_cmpfloat(fabs(x - 950), <, 0.01);
  g_assert_cmpfloat(fabs(w - 200), <, 0.01);
  g_assert_cmpint(m.group_commits, ==, 2);
  /* Shift on the handle stretches instead. */
  pull(view, 1150, 270, 100, 0, GDK_KEY_Shift_L, GDK_SHIFT_MASK);
  luma_arrange_view_get_item_rect(arrange, "a", NULL, NULL, &w, &h);
  g_assert_cmpfloat(fabs(h - 200), <, 0.01);
  /* Arrows nudge the group: one commit each. */
  int commits = m.group_commits;
  g_assert_true(key(view, GDK_KEY_Right, GDK_SHIFT_MASK));
  g_assert_cmpint(m.group_commits, ==, commits + 1);
  /* A rubber band from empty canvas chooses what it touches; Ctrl+A chooses everything; Esc clears. */
  luma_arrange_view_set_selected(arrange, NULL);
  pull(view, 1400, 700, 150, 150, 0, 0);
  g_auto(GStrv) band = luma_arrange_view_get_selection(arrange);
  g_assert_cmpuint(g_strv_length(band), ==, 1);
  g_assert_cmpstr(band[0], ==, "c");
  g_assert_true(key(view, GDK_KEY_a, GDK_CONTROL_MASK));
  g_auto(GStrv) all = luma_arrange_view_get_selection(arrange);
  g_assert_cmpuint(g_strv_length(all), ==, 3);
  g_assert_true(key(view, GDK_KEY_Escape, 0));
  g_auto(GStrv) none = luma_arrange_view_get_selection(arrange);
  g_assert_cmpuint(g_strv_length(none), ==, 0);
  const char *set[] = {"c", "a", NULL};
  luma_arrange_view_set_selection(arrange, set);
  g_assert_cmpstr(luma_arrange_view_get_selected(arrange), ==, "a");
  g_strfreev(m.last_ids);
  g_clear_pointer(&m.last_group, g_variant_unref);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  gtk_init();
  g_test_add_func("/lumaui/creative/arrange/snap", test_snap_rule);
  g_test_add_func("/lumaui/creative/arrange/view", test_view);
  g_test_add_func("/lumaui/creative/arrange/modifiers", test_modifiers);
  g_test_add_func("/lumaui/creative/arrange/multi", test_multi);
  return g_test_run();
}
