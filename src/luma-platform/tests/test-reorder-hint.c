/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

static void settle (void) {
  gint64 end = g_get_monotonic_time () + 100000;
  while (g_get_monotonic_time () < end) {
    g_main_context_iteration (NULL, FALSE);
    g_usleep (1000);
  }
}

int main (int argc, char **argv) {
  g_test_init (&argc, &argv, NULL);
  if (!gtk_init_check ()) return 77;
  luma_init ();
  g_object_set (gtk_settings_get_default (), "gtk-enable-animations", FALSE,
                "gtk-font-name", "Sans 20", NULL);
  for (int dark = 0; dark < 2; dark++) {
    adw_style_manager_set_color_scheme (adw_style_manager_get_default (),
      dark ? ADW_COLOR_SCHEME_FORCE_DARK : ADW_COLOR_SCHEME_FORCE_LIGHT);
    for (int rtl = 0; rtl < 2; rtl++) {
      GtkWidget *window = gtk_window_new ();
      GtkWidget *list = gtk_list_box_new ();
      GtkWidget *source = gtk_list_box_row_new ();
      GtkWidget *target = gtk_list_box_row_new ();
      GtkWidget *button = gtk_button_new_with_label ("Documents");
      GtkWidget *label = gtk_label_new ("Pictures — a long translated folder label that wraps");
      gtk_label_set_wrap (GTK_LABEL (label), TRUE);
      gtk_list_box_row_set_child (GTK_LIST_BOX_ROW (source), button);
      gtk_list_box_row_set_child (GTK_LIST_BOX_ROW (target), label);
      gtk_list_box_append (GTK_LIST_BOX (list), source);
      gtk_list_box_append (GTK_LIST_BOX (list), target);
      gtk_window_set_child (GTK_WINDOW (window), list);
      gtk_widget_set_direction (list, rtl ? GTK_TEXT_DIR_RTL : GTK_TEXT_DIR_LTR);
      gtk_window_set_default_size (GTK_WINDOW (window), rtl ? 360 : 1024, 320);
      gtk_window_present (GTK_WINDOW (window));
      settle ();
      gtk_widget_grab_focus (button);
      GtkWidget *focus = gtk_root_get_focus (GTK_ROOT (window));
      int source_height = gtk_widget_get_height (source);
      int target_height = gtk_widget_get_height (target);
      luma_reorder_hint_begin (list, source);
      g_assert_true (gtk_widget_get_visible (source));
      g_assert_false (luma_reorder_hint_is_after (target, target_height * .25));
      g_assert_true (luma_reorder_hint_is_after (target, target_height * .75));
      luma_reorder_hint_set_target (list, target, FALSE);
      settle ();
      g_assert_cmpint (gtk_widget_get_height (source), ==, source_height);
      g_assert_cmpint (gtk_widget_get_height (target), ==, target_height);
      g_assert_true (gtk_widget_has_css_class (target, "luma-reorder-before"));
      luma_reorder_hint_set_target (list, target, TRUE);
      g_assert_false (gtk_widget_has_css_class (target, "luma-reorder-before"));
      g_assert_true (gtk_widget_has_css_class (target, "luma-reorder-after"));
      luma_reorder_hint_clear (list);
      g_assert_false (gtk_widget_has_css_class (target, "luma-reorder-after"));
      g_assert_true (gtk_widget_has_css_class (source, "luma-reorder-source"));
      luma_reorder_hint_set_target (list, source, TRUE);
      g_assert_false (gtk_widget_has_css_class (source, "luma-reorder-after"));
      luma_reorder_hint_end (list);
      g_assert_false (gtk_widget_has_css_class (source, "luma-reorder-source"));
      g_assert_false (gtk_widget_has_css_class (list, "luma-reordering"));
      g_assert_true (gtk_root_get_focus (GTK_ROOT (window)) == focus);
      gtk_window_destroy (GTK_WINDOW (window));
    }
  }
  return 0;
}
