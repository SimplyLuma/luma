/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

typedef struct {
  double seek, move, trim, split;
  int selected, begins, commits, cancels, lane_actions;
  char selected_id[32], lane_id[32], lane_action[16];
  gboolean lane_enabled;
} Events;

static GtkWidget *find_name(GtkWidget *root, const char *name) {
  if (g_strcmp0(gtk_widget_get_name(root), name) == 0) return root;
  for (GtkWidget *child = gtk_widget_get_first_child(root); child;
       child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *match = find_name(child, name);
    if (match) return match;
  }
  return NULL;
}

static GtkWidget *find_class(GtkWidget *root, const char *name) {
  if (gtk_widget_has_css_class(root, name)) return root;
  for (GtkWidget *child = gtk_widget_get_first_child(root); child;
       child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *match = find_class(child, name);
    if (match) return match;
  }
  return NULL;
}

static void pump(void) {
  gint64 end = g_get_monotonic_time() + 150 * G_TIME_SPAN_MILLISECOND;
  while (g_get_monotonic_time() < end) {
    while (g_main_context_iteration(NULL, FALSE)) {}
    g_usleep(1000);
  }
}

static void seeked(LumaTimeline *timeline G_GNUC_UNUSED, double seconds, gpointer data) {
  ((Events *)data)->seek = seconds;
}

static void selected(LumaTimeline *timeline G_GNUC_UNUSED, const char *id, gpointer data) {
  Events *events = data;
  events->selected++;
  g_strlcpy(events->selected_id, id, sizeof(events->selected_id));
}

static void moved(LumaTimeline *timeline G_GNUC_UNUSED, const char *id,
                  double seconds, gpointer data) {
  Events *events = data;
  g_assert_cmpstr(id, ==, "region-1");
  events->move = seconds;
}

static void trimmed(LumaTimeline *timeline G_GNUC_UNUSED, const char *id,
                    double seconds, gpointer data) {
  Events *events = data;
  g_assert_cmpstr(id, ==, "region-1");
  events->trim = seconds;
}

static void split(LumaTimeline *timeline G_GNUC_UNUSED, const char *id,
                  double seconds, gpointer data) {
  Events *events = data;
  g_assert_cmpstr(id, ==, "region-1");
  events->split = seconds;
}

static void gesture(LumaTimeline *timeline G_GNUC_UNUSED, const char *id,
                    const char *action, gpointer data) {
  Events *events = data;
  g_assert_cmpstr(id, ==, "region-1");
  g_assert_true(g_strcmp0(action, "move") == 0 || g_strcmp0(action, "trim") == 0);
  events->begins++;
}

static void committed(LumaTimeline *timeline G_GNUC_UNUSED, const char *id G_GNUC_UNUSED,
                      const char *action G_GNUC_UNUSED, gpointer data) {
  ((Events *)data)->commits++;
}

static void cancelled(LumaTimeline *timeline G_GNUC_UNUSED, const char *id G_GNUC_UNUSED,
                      const char *action G_GNUC_UNUSED, gpointer data) {
  ((Events *)data)->cancels++;
}

static void lane_action(LumaTimeline *timeline G_GNUC_UNUSED, const char *lane_id,
                        const char *action, gboolean enabled, gpointer data) {
  Events *events = data;
  events->lane_actions++;
  g_strlcpy(events->lane_id, lane_id, sizeof(events->lane_id));
  g_strlcpy(events->lane_action, action, sizeof(events->lane_action));
  events->lane_enabled = enabled;
}

static GtkGestureDrag *drag_controller(GtkWidget *widget) {
  g_autoptr(GListModel) controllers = gtk_widget_observe_controllers(widget);
  for (guint i = 0; i < g_list_model_get_n_items(controllers); i++) {
    g_autoptr(GObject) object = g_list_model_get_item(controllers, i);
    if (GTK_IS_GESTURE_DRAG(object)) return GTK_GESTURE_DRAG(g_object_ref(object));
  }
  return NULL;
}

static void populate(LumaTimeline *timeline) {
  luma_timeline_begin_update(timeline);
  luma_timeline_clear(timeline);
  luma_timeline_set_frame_rate(timeline, 24, 1);
  luma_timeline_set_duration_frames(timeline, 480);
  luma_timeline_append_marker(timeline, 96, "Verse");
  luma_timeline_set_loop_frames(timeline, 48, 192);
  luma_timeline_append_lane(timeline, "drums", "Drums", "midi", "music");
  luma_timeline_append_lane(timeline, "vocals", "Vocals", "audio", "mic");
  luma_timeline_set_lane_state(timeline, "drums", FALSE, FALSE, TRUE, .5);
  luma_timeline_append_region(timeline, "drums", "region-1", "Beat", "midi", 24, 48, TRUE);
  luma_timeline_append_region(timeline, "vocals", "region-2", "Verse", "audio", 96, 120, FALSE);
  luma_timeline_append_note(timeline, "region-1", 0, 12, 60);
  luma_timeline_append_note(timeline, "region-1", 24, 12, 67);
  const double peaks[] = {.2, .6, .9, .4, .8, .3};
  luma_timeline_set_region_waveform(timeline, "region-2", peaks, G_N_ELEMENTS(peaks));
  luma_timeline_set_position_frames(timeline, 48);
  luma_timeline_end_update(timeline);
}

static void test_contract(void) {
  GtkWidget *widget = g_object_ref_sink(luma_timeline_new());
  LumaTimeline *timeline = LUMA_TIMELINE(widget);
  Events events = {0};
  g_signal_connect(timeline, "seek", G_CALLBACK(seeked), &events);
  g_signal_connect(timeline, "region-selected", G_CALLBACK(selected), &events);
  g_signal_connect(timeline, "region-move", G_CALLBACK(moved), &events);
  g_signal_connect(timeline, "region-trim", G_CALLBACK(trimmed), &events);
  g_signal_connect(timeline, "region-split", G_CALLBACK(split), &events);
  g_signal_connect(timeline, "gesture-begin", G_CALLBACK(gesture), &events);
  g_signal_connect(timeline, "gesture-commit", G_CALLBACK(committed), &events);
  g_signal_connect(timeline, "gesture-cancel", G_CALLBACK(cancelled), &events);
  g_signal_connect(timeline, "lane-action", G_CALLBACK(lane_action), &events);
  populate(timeline);
  GtkWidget *region = find_name(widget, "region-1");
  g_assert_nonnull(region);
  g_assert_true(gtk_widget_has_css_class(region, "selected"));
  luma_timeline_set_lane_state(timeline, "drums", FALSE, FALSE, TRUE, .9);
  g_assert_true(find_name(widget, "region-1") == region); /* live meter does not rebuild clips */
  g_signal_emit_by_name(region, "clicked");
  g_assert_cmpint(events.selected, ==, 1);
  g_assert_cmpstr(events.selected_id, ==, "region-1");

  luma_timeline_set_tool(timeline, "blade");
  region = find_name(widget, "region-1");
  g_signal_emit_by_name(region, "clicked");
  g_assert_cmpfloat_with_epsilon(events.split, 2.0, .001);
  luma_timeline_set_tool(timeline, "select");
  luma_timeline_set_selected_region(timeline, "region-2");
  g_assert_false(gtk_widget_has_css_class(find_name(widget, "region-1"), "selected"));
  g_assert_true(gtk_widget_has_css_class(find_name(widget, "region-2"), "selected"));

  GtkWidget *window = gtk_window_new();
  gtk_window_set_child(GTK_WINDOW(window), widget);
  gtk_window_set_default_size(GTK_WINDOW(window), 980, 680);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  region = find_name(widget, "region-1");
  g_autoptr(GtkGestureDrag) drag = drag_controller(region);
  g_assert_nonnull(drag);
  double pps = (double)gtk_widget_get_width(region) / 2.0;
  g_signal_emit_by_name(drag, "drag-begin", 5.0, 5.0);
  g_signal_emit_by_name(drag, "drag-end", pps, 0.0);
  g_assert_cmpint(events.begins, ==, 1);
  g_assert_cmpint(events.commits, ==, 1);
  g_assert_cmpfloat(events.move, >, 1.0);
  g_signal_emit_by_name(drag, "drag-begin", (double)gtk_widget_get_width(region) - 2, 5.0);
  g_signal_emit_by_name(drag, "drag-end", pps, 0.0);
  g_assert_cmpfloat(events.trim, >, 2.0);
  g_signal_emit_by_name(drag, "drag-begin", 5.0, 5.0);
  g_signal_emit_by_name(drag, "drag-end", 1.0, 0.0);
  g_assert_cmpint(events.cancels, ==, 1);

  GtkWidget *heads = find_class(widget, "timeline-heads");
  g_assert_nonnull(heads);
  GtkWidget *head = find_class(heads, "timeline-track-head");
  GtkWidget *mute = find_class(head, "lane-key");
  g_signal_emit_by_name(mute, "clicked");
  g_assert_cmpint(events.lane_actions, ==, 1);
  g_assert_cmpstr(events.lane_id, ==, "drums");
  g_assert_cmpstr(events.lane_action, ==, "mute");
  g_assert_true(events.lane_enabled);
  luma_timeline_set_export_status(timeline, "Exporting", .42);
  g_assert_nonnull(find_class(widget, "timeline-header"));
  luma_timeline_set_export_status(timeline, NULL, 0);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_responsive_widths(void) {
  const int widths[] = {360, 500, 980, 1024};
  for (guint i = 0; i < G_N_ELEMENTS(widths); i++) {
    GtkWidget *window = gtk_window_new();
    GtkWidget *widget = luma_timeline_new();
    populate(LUMA_TIMELINE(widget));
    gtk_window_set_child(GTK_WINDOW(window), widget);
    gtk_window_set_default_size(GTK_WINDOW(window), widths[i], 650);
    gtk_window_present(GTK_WINDOW(window));
    pump();
    gboolean phone = widths[i] <= 600;
    g_assert_cmpint(gtk_widget_has_css_class(widget, "phone"), ==, phone);
    g_assert_cmpint(gtk_widget_get_width(find_class(widget, "timeline-heads")), <=,
                    phone ? 116 : 214);
    g_assert_nonnull(find_name(widget, "region-1"));
    g_assert_nonnull(find_name(widget, "region-2"));
    gtk_window_destroy(GTK_WINDOW(window));
  }
}

static void test_narrow_header(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *widget = luma_timeline_new();
  populate(LUMA_TIMELINE(widget));
  GtkWidget *header = find_class(widget, "timeline-header");
  GtkWidget *status = gtk_widget_get_first_child(header);
  gtk_widget_set_margin_start(widget, 16);
  gtk_widget_set_margin_end(widget, 16);
  gtk_window_set_child(GTK_WINDOW(window), widget);
  gtk_window_set_default_size(GTK_WINDOW(window), 360, 400);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  gtk_label_set_text(GTK_LABEL(status), "Preparing selected clips for export");
  pump();
  g_assert_cmpint(gtk_widget_get_width(window), ==, 360);
  graphene_rect_t bounds;
  g_assert_true(gtk_widget_compute_bounds(gtk_widget_get_last_child(header), window, &bounds));
  g_assert_cmpfloat(bounds.origin.x + bounds.size.width, <=, 344);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  gtk_test_init(&argc, &argv);
  luma_init();
  g_test_add_func("/timeline/contract", test_contract);
  g_test_add_func("/timeline/narrow-header", test_narrow_header);
  g_test_add_func("/timeline/responsive", test_responsive_widths);
  return g_test_run();
}
