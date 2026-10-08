/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-stacked-button.h"

static void settle(void) {
  gint64 until = g_get_monotonic_time() + 100000;
  while (g_get_monotonic_time() < until) {
    while (g_main_context_iteration(NULL, FALSE))
      ;
    g_usleep(1000);
  }
}

static void count_clicks(GtkButton *button G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }

static void test_button(void) {
  GtkWidget *button = g_object_ref_sink(luma_stacked_button_new("trash-2", "Delete", TRUE));
  g_assert_true(gtk_widget_has_css_class(button, "lumaui-stacked-button"));
  g_assert_true(gtk_widget_has_css_class(button, "danger"));
  g_assert_true(gtk_widget_get_hexpand(button));
  GtkWidget *box = gtk_button_get_child(GTK_BUTTON(button));
  g_assert_true(gtk_widget_has_css_class(box, "lumaui-stacked-content"));
  GtkWidget *glyph = gtk_widget_get_first_child(box);
  g_assert_true(GTK_IS_IMAGE(glyph));
  g_assert_true(gtk_widget_has_css_class(glyph, "lumaui-stacked-icon"));
  GtkWidget *label = gtk_widget_get_next_sibling(glyph);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "Delete");
  g_assert_cmpint(gtk_label_get_width_chars(GTK_LABEL(label)), ==, 6);
  g_assert_null(gtk_widget_get_tooltip_text(button));
  int clicks = 0;
  g_signal_connect(button, "clicked", G_CALLBACK(count_clicks), &clicks);
  g_signal_emit_by_name(button, "clicked");
  g_assert_cmpint(clicks, ==, 1);
  g_object_unref(button);

  GtkWidget *plain = g_object_ref_sink(luma_stacked_button_new("share-2", "Share with everyone nearby", FALSE));
  g_assert_false(gtk_widget_has_css_class(plain, "danger"));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(plain), ==, "Share with everyone nearby");
  GtkWidget *long_label = gtk_widget_get_last_child(gtk_button_get_child(GTK_BUTTON(plain)));
  g_assert_cmpint(gtk_label_get_width_chars(GTK_LABEL(long_label)), ==, 14);
  g_object_unref(plain);
}

static void test_group(void) {
  GtkWidget *group = luma_stacked_buttons_new(TRUE);
  g_assert_true(gtk_widget_has_css_class(group, "lumaui-stacked-buttons"));
  g_assert_true(gtk_widget_has_css_class(group, "lumaui-stack-small"));
  g_assert_true(gtk_box_get_homogeneous(GTK_BOX(group)));
  for (int i = 0; i < 3; i++)
    luma_stacked_buttons_append(LUMA_STACKED_BUTTONS(group),
                                LUMA_STACKED_BUTTON(luma_stacked_button_new("check", "Go", FALSE)));
  GtkWidget *fourth = g_object_ref_sink(luma_stacked_button_new("check", "Four", FALSE));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*pairs or trios*");
  luma_stacked_buttons_append(LUMA_STACKED_BUTTONS(group), LUMA_STACKED_BUTTON(fourth));
  g_test_assert_expected_messages();
  g_assert_null(gtk_widget_get_parent(fourth));
  g_object_unref(fourth);

  GtkWidget *window = gtk_window_new();
  gtk_window_set_child(GTK_WINDOW(window), group);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_assert_true(gtk_widget_get_mapped(group));
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_lone(void) {
  GtkWidget *group = luma_stacked_buttons_new(FALSE);
  g_assert_false(gtk_widget_has_css_class(group, "small"));
  luma_stacked_buttons_append(LUMA_STACKED_BUTTONS(group),
                              LUMA_STACKED_BUTTON(luma_stacked_button_new("check", "Alone", FALSE)));
  GtkWidget *window = gtk_window_new();
  gtk_window_set_child(GTK_WINDOW(window), group);
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*pairs or trios*");
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_test_assert_expected_messages();
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/stacked-button/button", test_button);
  g_test_add_func("/lumaui/stacked-button/group", test_group);
  g_test_add_func("/lumaui/stacked-button/lone", test_lone);
  return g_test_run();
}
