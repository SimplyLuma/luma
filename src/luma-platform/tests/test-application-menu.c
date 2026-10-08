/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check()) return 77;
  luma_init();
  GtkApplication *app = gtk_application_new("org.projectluma.MenuTest",
                                            G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = luma_application_window_new(app, NULL);
  GMenuModel *initial = gtk_application_get_menubar(app);
  g_assert_nonnull(initial);
  g_object_ref(initial);
  GMenu *commands = g_menu_new();
  g_menu_append(commands, "New", "win.new");
  luma_application_window_set_menu_model(LUMA_APPLICATION_WINDOW(window),
                                         G_MENU_MODEL(commands));
  g_assert_true(gtk_application_get_menubar(app) == initial);
  g_assert_cmpint(g_menu_model_get_n_items(initial), ==, 1);
  g_menu_append(commands, "Open…", "win.open");
  luma_application_window_set_menu_model(LUMA_APPLICATION_WINDOW(window),
                                         G_MENU_MODEL(commands));
  g_assert_true(gtk_application_get_menubar(app) == initial);
  g_assert_cmpint(g_menu_model_get_n_items(initial), ==, 2);
  luma_application_window_set_menu_model(LUMA_APPLICATION_WINDOW(window), NULL);
  g_assert_cmpint(g_menu_model_get_n_items(initial), ==, 0);
  g_object_unref(commands);
  g_object_unref(initial);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
  return 0;
}
