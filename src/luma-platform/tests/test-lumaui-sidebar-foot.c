/* SPDX-License-Identifier: Apache-2.0 */
/* LumaSidebarFoot and LumaFilterHeading: the twins of structure_sidebar.py. */
#include "luma-ui.h"
#include "luma-sidebar-foot.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void settle(void) {
  gboolean done = FALSE;
  g_timeout_add(300, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static int count(GtkWidget *box) {
  int n = 0;
  for (GtkWidget *c = gtk_widget_get_first_child(box); c != NULL; c = gtk_widget_get_next_sibling(c))
    n++;
  return n;
}

static void got_text(GObject *object G_GNUC_UNUSED, const char *text, gpointer data) {
  GPtrArray *texts = data;
  g_ptr_array_add(texts, g_strdup(text));
}

static void counted(GObject *object G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }

static void test_heading(void) {
  GtkWidget *heading = g_object_ref_sink(luma_filter_heading_new("Favourites"));
  g_assert_true(gtk_widget_has_css_class(heading, "lumaui-filter-heading"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(heading)), ==, GTK_ACCESSIBLE_ROLE_STATUS);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(gtk_widget_get_first_child(heading))), ==, "Favourites");
  int shown = 0;
  g_signal_connect(heading, "show-all", G_CALLBACK(counted), &shown);
  g_signal_emit_by_name(gtk_widget_get_last_child(heading), "clicked");
  g_assert_cmpint(shown, ==, 1);
  g_object_unref(heading);
}

static void test_filters(void) {
  GtkWidget *foot = g_object_ref_sink(luma_sidebar_foot_new("Search people"));
  LumaSidebarFoot *f = LUMA_SIDEBAR_FOOT(foot);
  g_assert_true(gtk_widget_has_css_class(foot, "lumaui-sidebar-foot"));
  g_assert_cmpint(count(foot), ==, 1);
  g_assert_cmpstr(luma_sidebar_foot_get_filter(f), ==, "");
  luma_sidebar_foot_set_add(f, "Add a contact", "user-plus", "win.add");
  luma_sidebar_foot_add_filter(f, "all", "All", "users");
  luma_sidebar_foot_add_filter(f, "fav", "Favourites", "star");
  /* search · filter · add, whatever the order they were given in */
  g_assert_cmpint(count(foot), ==, 3);
  GtkWidget *filter = gtk_widget_get_next_sibling(gtk_widget_get_first_child(foot));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(filter), ==, "Show: All");
  g_assert_cmpstr(gtk_widget_get_tooltip_text(gtk_widget_get_last_child(foot)), ==, "Add a contact");
  g_assert_cmpstr(luma_sidebar_foot_get_filter(f), ==, "all");
  GtkWidget *heading = GTK_WIDGET(luma_sidebar_foot_get_heading(f));
  g_assert_false(gtk_widget_get_visible(heading));
  g_autoptr(GPtrArray) keys = g_ptr_array_new_with_free_func(g_free);
  g_signal_connect(foot, "filter-changed", G_CALLBACK(got_text), keys);
  luma_sidebar_foot_set_filter(f, "fav");
  g_assert_cmpuint(keys->len, ==, 0);
  g_assert_true(gtk_widget_has_css_class(filter, "on"));
  g_assert_true(gtk_widget_get_visible(heading));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(filter), ==, "Show: Favourites");
  /* Show all, as a person would. */
  g_signal_emit_by_name(gtk_widget_get_last_child(heading), "clicked");
  g_assert_cmpuint(keys->len, ==, 1);
  g_assert_cmpstr(g_ptr_array_index(keys, 0), ==, "all");
  g_assert_false(gtk_widget_get_visible(heading));
  /* Picking from the menu is the foot's filter action. */
  gtk_widget_activate_action(foot, "lumaui-foot.filter", "s", "fav");
  g_assert_cmpuint(keys->len, ==, 2);
  g_assert_cmpstr(luma_sidebar_foot_get_filter(f), ==, "fav");
  luma_sidebar_foot_set_filter_count(f, "all", 214);
  luma_sidebar_foot_set_filter_count(f, "fav", -1);
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown filter*");
  luma_sidebar_foot_set_filter(f, "nope");
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown filter*");
  luma_sidebar_foot_set_filter_count(f, "nope", 3);
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*filter keys are unique*");
  luma_sidebar_foot_add_filter(f, "fav", "Again", "star");
  g_test_assert_expected_messages();
  g_object_unref(foot);
}

static void test_search(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *foot = luma_sidebar_foot_new("Search mail");
  gtk_window_set_child(GTK_WINDOW(window), foot);
  gtk_window_present(GTK_WINDOW(window));
  g_autoptr(GPtrArray) texts = g_ptr_array_new_with_free_func(g_free);
  g_signal_connect(foot, "search-changed", G_CALLBACK(got_text), texts);
  GtkWidget *entry = luma_sidebar_foot_get_entry(LUMA_SIDEBAR_FOOT(foot));
  g_assert_true(GTK_IS_TEXT(entry));
  GtkWidget *clear = gtk_widget_get_next_sibling(entry);
  g_assert_true(GTK_IS_BUTTON(clear));
  g_assert_false(gtk_widget_get_visible(clear));
  gtk_editable_set_text(GTK_EDITABLE(entry), "p");
  gtk_editable_set_text(GTK_EDITABLE(entry), "pr");
  gtk_editable_set_text(GTK_EDITABLE(entry), "pri");
  settle();
  /* One signal after the typing pause. */
  g_assert_cmpuint(texts->len, ==, 1);
  g_assert_cmpstr(g_ptr_array_index(texts, 0), ==, "pri");
  g_assert_cmpstr(luma_sidebar_foot_get_text(LUMA_SIDEBAR_FOOT(foot)), ==, "pri");
  g_assert_true(gtk_widget_get_visible(clear));
  g_signal_emit_by_name(clear, "clicked");
  settle();
  g_assert_cmpstr(luma_sidebar_foot_get_text(LUMA_SIDEBAR_FOOT(foot)), ==, "");
  g_assert_false(gtk_widget_get_visible(clear));
  g_assert_cmpuint(texts->len, ==, 2);
  g_assert_cmpstr(g_ptr_array_index(texts, 1), ==, "");
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_one_filter(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *foot = luma_sidebar_foot_new("Search");
  luma_sidebar_foot_add_filter(LUMA_SIDEBAR_FOOT(foot), "all", "All", "layers");
  gtk_window_set_child(GTK_WINDOW(window), foot);
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*at least two views*");
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_test_assert_expected_messages();
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/sidebar-foot/heading", test_heading);
  g_test_add_func("/lumaui/sidebar-foot/filters", test_filters);
  g_test_add_func("/lumaui/sidebar-foot/search", test_search);
  g_test_add_func("/lumaui/sidebar-foot/one-filter", test_one_filter);
  return g_test_run();
}
