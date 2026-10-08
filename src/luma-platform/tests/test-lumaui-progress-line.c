/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

static void settle(void) {
  for (int i = 0; i < 30; i++) {
    while (g_main_context_iteration(NULL, FALSE));
    g_usleep(4000);
  }
}

/* Settings' toner (v70 .lprog): 5 px in a well, 140 wide, filled to the fraction. */
static void test_well(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *line = luma_progress_line_new(0.7, "well", NULL);
  luma_progress_line_set_label(LUMA_PROGRESS_LINE(line), "Black toner");
  gtk_widget_set_size_request(line, 140, -1);
  gtk_widget_set_hexpand(line, FALSE);
  gtk_widget_set_halign(line, GTK_ALIGN_START);
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(line)), ==, GTK_ACCESSIBLE_ROLE_PROGRESS_BAR);
  gtk_window_set_child(GTK_WINDOW(window), line);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_assert_cmpint(gtk_widget_get_height(line), ==, 5);
  g_assert_cmpint(gtk_widget_get_width(gtk_widget_get_first_child(line)), ==, 98);
  luma_progress_line_set_fraction(LUMA_PROGRESS_LINE(line), 2.0);
  g_assert_cmpfloat(luma_progress_line_get_fraction(LUMA_PROGRESS_LINE(line)), ==, 1.0);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/progress-line/well", test_well);
  return g_test_run();
}
