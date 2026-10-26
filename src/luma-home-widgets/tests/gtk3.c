/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "luma-widget-gtk3.h"
static gboolean launched;
static void launch(GtkWidget *widget G_GNUC_UNUSED, const char *id, gpointer data G_GNUC_UNUSED) {
  g_assert_cmpstr(id, ==, "org.projectluma.Weather.desktop"); launched = TRUE;
}
int main(int argc, char **argv) {
  g_autofree char *directory = g_dir_make_tmp("luma-widget-test-XXXXXX", NULL);
  g_setenv("XDG_DATA_HOME", directory, TRUE); g_setenv("XDG_CACHE_HOME", directory, TRUE);
  gtk_init(&argc, &argv);
  g_autoptr(LumaWidgetsModel) model = luma_widgets_model_new("org.gnome.Shell.CalendarServer");
  GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 12);
  gtk_container_add(GTK_CONTAINER(window), box);
  GtkWidget *weather = luma_widgets_gtk3_new(model, "weather");
  GtkWidget *calendar = luma_widgets_gtk3_new(model, "calendar");
  gtk_box_pack_start(GTK_BOX(box), weather, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(box), calendar, TRUE, TRUE, 0);
  g_signal_connect(weather, "launch-requested", G_CALLBACK(launch), NULL);
  gtk_widget_show_all(window);
  while (gtk_events_pending()) gtk_main_iteration();
  g_assert_true(GTK_IS_BUTTON(weather)); g_assert_true(GTK_IS_BUTTON(calendar));
  g_assert_nonnull(g_strstr_len(atk_object_get_name(gtk_widget_get_accessible(weather)), -1, "Weather"));
  gtk_button_clicked(GTK_BUTTON(weather)); g_assert_true(launched);
  gtk_widget_destroy(window); g_object_run_dispose(G_OBJECT(model));
  g_print("PASS native GTK3 shared widget rendering, accessible content, activation and teardown\n");
  return 0;
}
