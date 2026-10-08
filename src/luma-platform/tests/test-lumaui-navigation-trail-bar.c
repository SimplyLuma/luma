/* SPDX-License-Identifier: Apache-2.0 */
/* LumaNavigationTrailBar: the twin of structure_trail.NavigationTrailBar. */
#include "luma-ui.h"
#include "luma-navigation-trail-bar.h"

static int count(GtkWidget *box) {
  int n = 0;
  for (GtkWidget *c = gtk_widget_get_first_child(box); c != NULL; c = gtk_widget_get_next_sibling(c))
    n++;
  return n;
}

static void counted(GObject *object G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }

static void test_page(void) {
  GtkWidget *bar = g_object_ref_sink(luma_navigation_trail_bar_new(TRUE));
  g_assert_true(gtk_widget_has_css_class(bar, "lumaui-trail"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(bar)), ==, GTK_ACCESSIBLE_ROLE_NAVIGATION);
  GtkWidget *back = gtk_widget_get_first_child(bar);
  GtkWidget *title = gtk_widget_get_next_sibling(back);
  GtkWidget *meta = gtk_widget_get_last_child(bar);
  g_assert_false(gtk_widget_get_visible(back));
  g_assert_false(gtk_widget_get_visible(meta));
  g_assert_true(gtk_widget_has_css_class(back, "icon-only"));
  luma_navigation_trail_bar_set_place(LUMA_NAVIGATION_TRAIL_BAR(bar), "Albums", "Midnight Drive");
  g_assert_true(gtk_widget_get_visible(back));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(back), ==, "Back to Albums");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(title)), ==, "Midnight Drive");
  luma_navigation_trail_bar_add_meta(LUMA_NAVIGATION_TRAIL_BAR(bar), "12 songs", NULL);
  luma_navigation_trail_bar_add_meta(LUMA_NAVIGATION_TRAIL_BAR(bar), "Nova", "win.artist");
  luma_navigation_trail_bar_add_meta(LUMA_NAVIGATION_TRAIL_BAR(bar), "", NULL);
  g_assert_true(gtk_widget_get_visible(meta));
  g_assert_cmpint(count(meta), ==, 4); /* dot, text, dot, link */
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_first_child(meta), "lumaui-trail-dot"));
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_last_child(meta), "lumaui-trail-link"));
  g_assert_cmpstr(gtk_actionable_get_action_name(GTK_ACTIONABLE(gtk_widget_get_last_child(meta))), ==, "win.artist");
  int backs = 0;
  g_signal_connect(bar, "back", G_CALLBACK(counted), &backs);
  g_signal_emit_by_name(back, "clicked");
  g_assert_cmpint(backs, ==, 1);
  luma_navigation_trail_bar_set_place(LUMA_NAVIGATION_TRAIL_BAR(bar), NULL, "Albums");
  g_assert_false(gtk_widget_get_visible(back));
  luma_navigation_trail_bar_clear_meta(LUMA_NAVIGATION_TRAIL_BAR(bar));
  g_assert_false(gtk_widget_get_visible(meta));
  g_assert_cmpint(count(meta), ==, 0);
  g_object_unref(bar);
}

static void test_pane(void) {
  GtkWidget *bar = g_object_ref_sink(luma_navigation_trail_bar_new(FALSE));
  GtkWidget *back = gtk_widget_get_first_child(bar);
  GtkWidget *title = gtk_widget_get_next_sibling(back);
  g_assert_false(gtk_widget_get_visible(title));
  g_assert_false(gtk_widget_has_css_class(back, "icon-only"));
  luma_navigation_trail_bar_set_place(LUMA_NAVIGATION_TRAIL_BAR(bar), "Sources", "Nova");
  GtkWidget *label = gtk_widget_get_last_child(gtk_button_get_child(GTK_BUTTON(back)));
  g_assert_true(gtk_widget_get_visible(label));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "Sources");
  luma_navigation_trail_bar_add_meta(LUMA_NAVIGATION_TRAIL_BAR(bar), "3 sources", NULL);
  g_assert_cmpint(count(gtk_widget_get_last_child(bar)), ==, 1); /* no dot before the first */
  gtk_widget_set_direction(bar, GTK_TEXT_DIR_RTL);
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*title != NULL*");
  luma_navigation_trail_bar_set_place(LUMA_NAVIGATION_TRAIL_BAR(bar), NULL, NULL);
  g_test_assert_expected_messages();
  g_object_unref(bar);
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/navigation-trail-bar/page", test_page);
  g_test_add_func("/lumaui/navigation-trail-bar/pane", test_pane);
  return g_test_run();
}
