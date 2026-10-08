/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-adaptive-scaffold.h"

static void settle(void) {
  gint64 until = g_get_monotonic_time() + 150000;
  do {
    while (g_main_context_iteration(NULL, FALSE)) {}
    g_usleep(1000);
  } while (g_get_monotonic_time() < until);
}

static GtkWidget *new_sidebar(void) {
  GtkWidget *label = gtk_label_new("A very long instrument library label with a natural width larger than the sidebar");
  gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
  int minimum, natural;
  gtk_widget_measure(label, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(minimum, <=, 190);
  g_assert_cmpint(natural, >, 190);
  return label;
}

static GtkWindow *new_window(LumaAdaptiveScaffold **out, GtkWidget **sidebar,
                             GtkWidget **content) {
  GtkWindow *window = GTK_WINDOW(gtk_window_new());
  *out = LUMA_ADAPTIVE_SCAFFOLD(luma_adaptive_scaffold_new());
  *sidebar = new_sidebar();
  *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_box_append(GTK_BOX(*content), gtk_button_new_with_label("Content action"));
  luma_adaptive_scaffold_set_sidebar(*out, *sidebar, "Library");
  luma_adaptive_scaffold_set_content(*out, *content, "Content");
  gtk_window_set_decorated(window, FALSE);
  gtk_window_set_child(window, GTK_WIDGET(*out));
  gtk_window_set_default_size(window, 1024, 600);
  gtk_window_present(window);
  settle();
  return window;
}

static void notified(GObject *object, GParamSpec *pspec, gpointer data) {
  guint width;
  g_assert_cmpstr(pspec->name, ==, "sidebar-width");
  g_object_get(object, "sidebar-width", &width, NULL);
  g_assert_cmpuint(width, ==,
                  luma_adaptive_scaffold_get_sidebar_width(LUMA_ADAPTIVE_SCAFFOLD(object)));
  (*(guint *)data)++;
}

static void test_defaults_and_opt_in(void) {
  LumaAdaptiveScaffold *s = LUMA_ADAPTIVE_SCAFFOLD(luma_adaptive_scaffold_new());
  GtkWidget *native = adw_navigation_split_view_new();
  AdwNavigationSplitView *split = ADW_NAVIGATION_SPLIT_VIEW(
      adw_breakpoint_bin_get_child(ADW_BREAKPOINT_BIN(s)));
  guint notifications = 0;
  g_object_ref_sink(s);
  g_object_ref_sink(native);
  g_assert_cmpfloat(adw_navigation_split_view_get_min_sidebar_width(split), ==,
                    adw_navigation_split_view_get_min_sidebar_width(ADW_NAVIGATION_SPLIT_VIEW(native)));
  g_assert_cmpfloat(adw_navigation_split_view_get_max_sidebar_width(split), ==,
                    adw_navigation_split_view_get_max_sidebar_width(ADW_NAVIGATION_SPLIT_VIEW(native)));
  g_assert_cmpint(adw_navigation_split_view_get_sidebar_width_unit(split), ==,
                  adw_navigation_split_view_get_sidebar_width_unit(ADW_NAVIGATION_SPLIT_VIEW(native)));
  g_assert_cmpuint(luma_adaptive_scaffold_get_sidebar_width(s), ==, 180);
  g_signal_connect(s, "notify::sidebar-width", G_CALLBACK(notified), &notifications);
  /* Explicitly requesting the dormant default applies it without false notify. */
  g_object_set(s, "sidebar-width", 180u, NULL);
  g_assert_cmpuint(notifications, ==, 0);
  g_assert_cmpfloat(adw_navigation_split_view_get_min_sidebar_width(split), ==, 180);
  g_assert_cmpfloat(adw_navigation_split_view_get_max_sidebar_width(split), ==, 180);
  g_assert_cmpint(adw_navigation_split_view_get_sidebar_width_unit(split), ==, ADW_LENGTH_UNIT_PX);
  g_object_unref(native);
  g_object_unref(s);
}

static void test_limits_and_notifications(void) {
  LumaAdaptiveScaffold *s = LUMA_ADAPTIVE_SCAFFOLD(luma_adaptive_scaffold_new());
  guint notifications = 0;
  g_object_ref_sink(s);
  g_signal_connect(s, "notify::sidebar-width", G_CALLBACK(notified), &notifications);
  luma_adaptive_scaffold_set_sidebar_width_limits(s, 160, 320);
  g_assert_cmpuint(notifications, ==, 0);
  luma_adaptive_scaffold_set_sidebar_width(s, 190);
  g_assert_cmpuint(notifications, ==, 1);
  g_object_set(s, "sidebar-width", 190u, NULL);
  g_assert_cmpuint(notifications, ==, 1);
  luma_adaptive_scaffold_set_sidebar_width_limits(s, 400, 640);
  g_assert_cmpuint(luma_adaptive_scaffold_get_sidebar_width(s), ==, 400);
  g_assert_cmpuint(notifications, ==, 2);
  luma_adaptive_scaffold_set_sidebar_width(s, G_MAXUINT);
  g_assert_cmpuint(luma_adaptive_scaffold_get_sidebar_width(s), ==, 640);
  g_assert_cmpuint(notifications, ==, 3);
  luma_adaptive_scaffold_set_sidebar_width_limits(s, 120, 150);
  g_assert_cmpuint(luma_adaptive_scaffold_get_sidebar_width(s), ==, 150);
  g_assert_cmpuint(notifications, ==, 4);
  luma_adaptive_scaffold_set_sidebar_width(s, 0);
  g_assert_cmpuint(luma_adaptive_scaffold_get_sidebar_width(s), ==, 120);
  g_assert_cmpuint(notifications, ==, 5);
  luma_adaptive_scaffold_set_sidebar_width_limits(s, 120, 120);
  luma_adaptive_scaffold_set_sidebar_width(s, 0);
  g_assert_cmpuint(notifications, ==, 5);
  g_object_unref(s);
}

static void test_allocations_and_navigation(void) {
  const int widths[] = {360, 500, 1024, 1280, 1440, 500, 360};
  GtkTextDirection original_direction = gtk_widget_get_default_direction();
  for (guint rtl = 0; rtl < 2; rtl++) {
    gtk_widget_set_default_direction(rtl ? GTK_TEXT_DIR_RTL : GTK_TEXT_DIR_LTR);
    LumaAdaptiveScaffold *s;
    GtkWidget *sidebar, *content;
    GtkWindow *window = new_window(&s, &sidebar, &content);
    gtk_widget_set_direction(GTK_WIDGET(window), rtl ? GTK_TEXT_DIR_RTL : GTK_TEXT_DIR_LTR);
    gtk_widget_set_direction(GTK_WIDGET(s), rtl ? GTK_TEXT_DIR_RTL : GTK_TEXT_DIR_LTR);
    GtkWidget *split = adw_breakpoint_bin_get_child(ADW_BREAKPOINT_BIN(s));
    g_assert_cmpint(gtk_widget_get_direction(split), ==,
                    rtl ? GTK_TEXT_DIR_RTL : GTK_TEXT_DIR_LTR);
    luma_adaptive_scaffold_set_sidebar_width(s, 190);
    for (guint i = 0; i < G_N_ELEMENTS(widths); i++) {
      gtk_window_set_default_size(window, widths[i], 600);
      settle();
      g_assert_cmpint(gtk_widget_get_width(GTK_WIDGET(window)), ==, widths[i]);
      gboolean collapsed = widths[i] < 560; /* v71: the phone tier */
      g_assert_cmpint(luma_adaptive_scaffold_get_collapsed(s), ==, collapsed);
      luma_adaptive_scaffold_set_show_content(s, FALSE);
      settle();
      g_assert_cmpint(gtk_widget_get_width(sidebar), ==, collapsed ? widths[i] : 190);
      if (!collapsed) {
        graphene_point_t origin = GRAPHENE_POINT_INIT(0, 0), position;
        g_assert_true(gtk_widget_compute_point(sidebar, GTK_WIDGET(s), &origin, &position));
        g_assert_cmpfloat(position.x, ==, rtl ? widths[i] - 190 : 0);
      }
      luma_adaptive_scaffold_set_show_content(s, TRUE);
      settle();
      g_assert_true(gtk_widget_get_mapped(content));
      if (collapsed)
        g_assert_cmpint(gtk_widget_get_width(content), ==, widths[i]);
      g_assert_cmpuint(luma_adaptive_scaffold_get_sidebar_width(s), ==, 190);
      g_test_message("rtl=%u actual=%d sidebar=%d content=%d collapsed=%d", rtl,
                     gtk_widget_get_width(GTK_WIDGET(window)), gtk_widget_get_width(sidebar),
                     gtk_widget_get_width(content), collapsed);
    }
    /* Changing the breakpoint remains source-owned and preserves navigation. */
    gtk_window_set_default_size(window, 1024, 600);
    luma_adaptive_scaffold_set_compact_width(s, 1100);
    settle();
    g_assert_true(luma_adaptive_scaffold_get_collapsed(s));
    luma_adaptive_scaffold_set_compact_width(s, 639);
    settle();
    g_assert_false(luma_adaptive_scaffold_get_collapsed(s));
    g_assert_cmpint(gtk_widget_get_width(sidebar), ==, 190);
    gtk_window_destroy(window);
    settle();
  }
  gtk_widget_set_default_direction(original_direction);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check()) {
    g_test_message("A GTK display is required for scaffold runtime tests.");
    return 77;
  }
  adw_init();
  g_object_set(gtk_settings_get_default(), "gtk-enable-animations", FALSE, NULL);
  g_test_add_func("/luma/scaffold/defaults-opt-in", test_defaults_and_opt_in);
  g_test_add_func("/luma/scaffold/limits-notifications", test_limits_and_notifications);
  g_test_add_func("/luma/scaffold/allocations-navigation", test_allocations_and_navigation);
  return g_test_run();
}
