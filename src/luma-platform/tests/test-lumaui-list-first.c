/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}
static void spin(void) {
  gboolean done = FALSE;
  g_timeout_add(300, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static char *last_showing;
static void showing(LumaListFirst *stack G_GNUC_UNUSED, const char *which, gpointer data G_GNUC_UNUSED) {
  g_free(last_showing);
  last_showing = g_strdup(which);
}
static void count(gpointer instance G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }

static GtkWidget *window_at(int width, GtkWidget *child) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), width, 700);
  gtk_window_set_child(GTK_WINDOW(window), child);
  luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  spin();
  return window;
}

static void test_initial_measure(void) {
  GtkWidget *list = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_size_request(list, 200, 100);
  gtk_widget_set_size_request(page, 200, 100);
  GtkWidget *stack = luma_list_first_new(list, page, "Settings");
  int minimum = 0;
  gtk_widget_measure(stack, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, NULL, NULL, NULL);
  g_assert_cmpint(minimum, <=, 360);
  GtkWidget *window = window_at(1000, stack);
  g_assert_false(luma_list_first_get_phone(LUMA_LIST_FIRST(stack)));
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_desktop(void) {
  GtkWidget *list = gtk_list_box_new();
  gtk_list_box_append(GTK_LIST_BOX(list), gtk_label_new("Wi-Fi"));
  GtkWidget *page = gtk_label_new("page");
  GtkWidget *stack = luma_list_first_new(list, page, "Settings");
  GtkWidget *window = window_at(1000, stack);
  g_assert_true(gtk_widget_has_css_class(stack, "lumaui-list-first"));
  g_assert_false(luma_list_first_get_phone(LUMA_LIST_FIRST(stack)));
  /* Side by side: both in the split. */
  GtkWidget *split = gtk_widget_get_parent(list);
  g_assert_true(gtk_widget_has_css_class(split, "lumaui-list-first-split"));
  g_assert_true(gtk_widget_get_parent(page) == split);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_phone(void) {
  GtkWidget *list = gtk_list_box_new();
  gtk_list_box_append(GTK_LIST_BOX(list), gtk_label_new("Wi-Fi"));
  GtkWidget *page = gtk_label_new("page");
  GtkWidget *stack = luma_list_first_new(list, page, "Settings");
  g_signal_connect(stack, "showing", G_CALLBACK(showing), NULL);
  int backs = 0;
  g_signal_connect(stack, "back", G_CALLBACK(count), &backs);
  GtkWidget *window = window_at(400, stack);
  if (gtk_widget_get_width(window) >= LUMA_TIER_PHONE_BELOW) {
    g_test_skip("the display did not give a phone-width window");
    gtk_window_destroy(GTK_WINDOW(window));
    return;
  }
  g_assert_true(luma_list_first_get_phone(LUMA_LIST_FIRST(stack)));
  g_assert_true(gtk_widget_has_css_class(stack, "phone"));
  GtkWidget *screen = gtk_widget_get_parent(list);
  g_assert_true(gtk_widget_has_css_class(screen, "lumaui-list-first-list"));
  g_assert_cmpstr(luma_list_first_get_showing(LUMA_LIST_FIRST(stack)), ==, "list");
  /* A row picked pushes the page; the floating ‹ returns. */
  g_signal_emit_by_name(list, "row-activated", gtk_list_box_get_row_at_index(GTK_LIST_BOX(list), 0));
  g_assert_cmpstr(luma_list_first_get_showing(LUMA_LIST_FIRST(stack)), ==, "detail");
  g_assert_cmpstr(last_showing, ==, "detail");
  GtkWidget *overlay = gtk_widget_get_parent(page);
  g_assert_true(gtk_widget_has_css_class(overlay, "lumaui-list-first-page"));
  GtkWidget *back = gtk_widget_get_last_child(overlay);
  g_assert_true(gtk_widget_has_css_class(back, "lumaui-list-first-back"));
  g_assert_true(gtk_widget_get_visible(back));
  g_signal_emit_by_name(back, "clicked");
  g_assert_cmpstr(luma_list_first_get_showing(LUMA_LIST_FIRST(stack)), ==, "list");
  g_assert_cmpint(backs, ==, 1);
  /* With a title island, its ‹ returns and the floating one steps aside. */
  GtkWidget *island = g_object_ref_sink(luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_BACK, "Wi-Fi", NULL));
  luma_list_first_attach_island(LUMA_LIST_FIRST(stack), LUMA_TITLE_ISLAND(island));
  g_assert_false(gtk_widget_get_visible(back));
  luma_list_first_show_detail(LUMA_LIST_FIRST(stack));
  g_signal_emit_by_name(gtk_widget_get_first_child(gtk_widget_get_first_child(island)), "clicked");
  g_assert_cmpstr(luma_list_first_get_showing(LUMA_LIST_FIRST(stack)), ==, "list");
  /* Widening puts them side by side again. */
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  spin();
  if (gtk_widget_get_width(window) >= LUMA_TIER_PHONE_BELOW) {
    g_assert_false(luma_list_first_get_phone(LUMA_LIST_FIRST(stack)));
    g_assert_true(gtk_widget_has_css_class(gtk_widget_get_parent(list), "lumaui-list-first-split"));
  }
  g_object_unref(island);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/list-first/initial-measure", test_initial_measure);
  g_test_add_func("/lumaui/list-first/desktop", test_desktop);
  g_test_add_func("/lumaui/list-first/phone", test_phone);
  return g_test_run();
}
