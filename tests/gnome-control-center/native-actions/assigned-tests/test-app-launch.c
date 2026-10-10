/* SPDX-License-Identifier: GPL-2.0-or-later */
#include SETTINGS_ACTION_SOURCE
static JsonObject *model_data;
static int errors;
JsonObject *cc_luma_fixture_get_data (CcLumaFixture *model) { return model_data; }
gboolean cc_luma_fixture_is_live (CcLumaFixture *model) { return TRUE; }
void cc_luma_view_report_error (View *v, const GError *error) { errors++; }
static gboolean wait_for (const char *path)
{
  gint64 end = g_get_monotonic_time () + 5 * G_USEC_PER_SEC;
  while (g_get_monotonic_time () < end)
    {
      if (g_file_test (path, G_FILE_TEST_IS_REGULAR)) return TRUE;
      while (g_main_context_iteration (NULL, FALSE));
      g_usleep (1000);
    }
  return FALSE;
}
int main (int argc, char **argv)
{
  g_test_init (&argc, &argv, NULL);
  g_autoptr (GError) error = NULL;
  g_autofree char *base = g_dir_make_tmp ("luma-settings-launch-test-XXXXXX", &error);
  g_autofree char *apps = g_build_filename (base, "applications", NULL);
  g_mkdir_with_parents (apps, 0700);
  g_setenv ("XDG_DATA_HOME", base, TRUE);
  g_autofree char *launched = g_build_filename (base, "app-launched", NULL);
  g_autofree char *routed = g_build_filename (base, "depot-routed", NULL);
  g_autofree char *app_file = g_build_filename (apps, "org.example.SettingsTest.desktop", NULL);
  g_autofree char *app = g_strdup_printf ("[Desktop Entry]\nType=Application\nName=Settings Test\nExec=/usr/bin/touch %s\n", launched);
  g_assert_true (g_file_set_contents (app_file, app, -1, &error));
  g_autofree char *script = g_build_filename (base, "depot-test", NULL);
  g_autofree char *script_text = g_strdup_printf ("#!/bin/sh\nprintf '%%s' \"$1\" > %s\n", routed);
  g_assert_true (g_file_set_contents (script, script_text, -1, &error));
  g_assert_cmpint (chmod (script, 0700), ==, 0);
  g_autofree char *depot_file = g_build_filename (apps, "org.projectluma.Depot.desktop", NULL);
  g_autofree char *depot = g_strdup_printf ("[Desktop Entry]\nType=Application\nName=Depot Test\nExec=%s %%U\nMimeType=x-scheme-handler/luma-depot;\n", script);
  g_assert_true (g_file_set_contents (depot_file, depot, -1, &error));
  gtk_init ();
  GtkWidget *window = gtk_window_new ();
  GtkWidget *root = gtk_box_new (GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child (GTK_WINDOW (window), root);
  View v = { .root = root, .page = "app:normalized-child-id", .model = (CcLumaFixture *) root };
  model_data = json_object_new ();
  JsonObject *infos = json_object_new (), *entry = json_object_new ();
  json_object_set_string_member (entry, "desktopId", "org.example.SettingsTest.desktop");
  json_object_set_string_member (entry, "flatpakId", "org.example.SettingsTest");
  json_object_set_object_member (infos, "normalized-child-id", entry);
  json_object_set_object_member (model_data, "CFAPPINFO", infos);
  app_open (NULL, &v);
  g_assert_cmpint (errors, ==, 0);
  g_assert_true (wait_for (launched));
  g_print ("PASS registered application launched despite normalized Settings id\n");
  app_manage_in_depot (&v);
  g_assert_cmpint (errors, ==, 0);
  g_assert_true (wait_for (routed));
  g_autofree char *uri = NULL;
  g_assert_true (g_file_get_contents (routed, &uri, NULL, &error));
  g_assert_cmpstr (uri, ==, "luma-depot://app/org.example.SettingsTest");
  g_print ("PASS exact installed application routed to Depot\n");
  json_object_set_string_member (entry, "desktopId", "org.example.DoesNotExist.desktop");
  app_open (NULL, &v);
  g_assert_cmpint (errors, ==, 1);
  g_print ("PASS missing app reports error in original pane\n");
  gtk_window_destroy (GTK_WINDOW (window));
  json_object_unref (model_data);
  unlink (app_file); unlink (depot_file); unlink (script); unlink (launched); unlink (routed); rmdir (apps); rmdir (base);
  return 0;
}
