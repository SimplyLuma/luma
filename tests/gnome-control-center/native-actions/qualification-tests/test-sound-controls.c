/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Real mixer writes and mapped Luma view; private PulseAudio sources only. */
#include "cc-luma-view-private.h"
#include <signal.h>
#include "cc-luma-live-sound.c"

void cc_luma_live_ready (CcLumaFixture *model, const char *adapter)
{ cc_luma_fixture_changed (model); }
static void spin (guint ms)
{
  gint64 until = g_get_monotonic_time () + ms * 1000;
  do { while (g_main_context_iteration (NULL, FALSE)); g_usleep (1000); }
  while (g_get_monotonic_time () < until);
}
static guint delegated;
static gboolean delegate (GtkWidget *root, const char *page, gpointer unused)
{ delegated++; return TRUE; }
static gboolean write_sound (CcLumaFixture *model, const char *key,
                              JsonNode *value, gpointer data, GError **error)
{ return sound_write (data, key, value, error); }
static CcLumaFixture *model_new (Sound **owner)
{
  g_autoptr (JsonParser) parser = json_parser_new ();
  g_assert_true (json_parser_load_from_data (parser,
    "{\"settings\":{\"overamp\":false},\"data\":{\"CFAPPINFO\":{\"probe\":{\"name\":\"Probe\"}}},"
    "\"pages\":[{\"id\":\"sound\"},{\"id\":\"sound-levels\"},{\"id\":\"sound-alert\"}]}", -1, NULL));
  CcLumaFixture *model = cc_luma_fixture_new_live (json_parser_get_root (parser));
  *owner = sound_start (model);
  cc_luma_fixture_set_writer (model, write_sound, *owner);
  for (guint i = 0; i < 100; i++) {
    spin (50);
    JsonNode *input = cc_luma_fixture_get (model, "inDev");
    if (input && JSON_NODE_HOLDS_VALUE (input)) return model;
  }
  g_error ("Private microphones were not reported by the live adapter");
  return NULL;
}
static GtkWidget *named (GtkWidget *widget, const char *name)
{
  if (g_strcmp0 (gtk_widget_get_name (widget), name) == 0) return widget;
  for (GtkWidget *c = gtk_widget_get_first_child (widget); c; c = gtk_widget_get_next_sibling (c)) {
    GtkWidget *found = named (c, name); if (found) return found;
  }
  return NULL;
}
static GtkWidget *open_view (CcLumaFixture *model, GtkWidget **root)
{
  g_autoptr (GError) error = NULL;
  *root = cc_luma_view_new (model, "sound", &error);
  g_assert_no_error (error); g_assert_nonnull (*root);
  cc_luma_view_set_delegate (*root, delegate, NULL);
  GtkWidget *window = gtk_window_new ();
  gtk_window_set_default_size (GTK_WINDOW (window), 1100, 800);
  gtk_window_set_child (GTK_WINDOW (window), *root);
  gtk_window_present (GTK_WINDOW (window)); spin (200);
  return window;
}
static char *input_id (CcLumaFixture *model, const char *name)
{
  JsonArray *inputs = json_object_get_array_member (cc_luma_fixture_get_data (model), "CFIN");
  for (guint i = 0; i < json_array_get_length (inputs); i++) {
    JsonObject *item = json_array_get_object_element (inputs, i);
    if (strstr (json_object_get_string_member (item, "n"), name))
      return g_strdup (json_object_get_string_member (item, "id"));
  }
  g_error ("Private input %s not found", name); return NULL;
}
static char *default_source (void)
{
  char *output = NULL; gint status;
  g_assert_true (g_spawn_command_line_sync ("pactl get-default-source", &output, NULL, &status, NULL));
  g_assert_true (g_spawn_check_wait_status (status, NULL));
  return g_strstrip (output);
}
static void selection (void)
{
  Sound *owner;
  g_autoptr (CcLumaFixture) model = model_new (&owner);
  g_autofree char *id = input_id (model, "Test_microphone_B");
  GtkWidget *root, *window = open_view (model, &root);
  JsonArray *outputs = json_object_get_array_member (cc_luma_fixture_get_data (model), "CFOUT");
  const char *out_id = NULL;
  for (guint i = 0; i < json_array_get_length (outputs); i++) {
    JsonObject *out = json_array_get_object_element (outputs, i);
    if (strstr (json_object_get_string_member (out, "n"), "Test_output_B")) out_id = json_object_get_string_member (out, "id");
  }
  g_assert_nonnull (out_id);
  g_autofree char *out_name = g_strconcat ("cf-out-", out_id, NULL);
  GtkWidget *out_button = named (root, out_name); g_assert_nonnull (out_button);
  g_signal_emit_by_name (out_button, "clicked"); spin (500);
  g_autofree char *sink = NULL; gint status;
  g_assert_true (g_spawn_command_line_sync ("pactl get-default-sink", &sink, NULL, &status, NULL));
  g_assert_cmpstr (g_strstrip (sink), ==, "test_output_b");
  g_assert_cmpuint (delegated, ==, 0);
  char *play[] = { "pacat", "--playback", "--raw", "--rate=48000", "--channels=2", "--property=application.id=org.example.Probe", "/dev/zero", NULL };
  GPid child; g_assert_true (g_spawn_async (NULL, play, NULL, G_SPAWN_SEARCH_PATH | G_SPAWN_DO_NOT_REAP_CHILD, NULL, NULL, &child, NULL)); spin (400);
  g_assert_true (cc_luma_fixture_key_verified (model, "lv.probe"));
  g_autoptr (JsonNode) level = json_node_init_int (json_node_alloc (), 33); g_autoptr (GError) write_error = NULL;
  g_assert_true (cc_luma_fixture_write (model, "lv.probe", level, &write_error)); spin (400);
  g_assert_cmpint (json_node_get_int (cc_luma_fixture_get (model, "lv.probe")), ==, 33);
  kill (child, SIGTERM); g_spawn_close_pid (child); spin (300);
  g_assert_false (cc_luma_fixture_write (model, "lv.probe", level, &write_error));
  g_assert_error (write_error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED); g_clear_error (&write_error);
  g_autoptr (JsonNode) cfg = json_node_init_string (json_node_alloc (), "not-offered");
  g_assert_false (cc_luma_fixture_write (model, "cfg", cfg, &write_error));
  g_assert_error (write_error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED); g_clear_error (&write_error);
  GtkWidget *picker = named (root, "cf-inDev");
  g_assert_nonnull (picker); g_assert_true (gtk_widget_get_mapped (picker));
  g_signal_emit_by_name (picker, "clicked"); spin (100);
  g_assert_true (gtk_widget_activate_action (root, "cf.pick", "(ss)", "inDev", id)); spin (500);
  /* This is the reported boundary: a real picker must not replace this page. */
  g_assert_cmpuint (delegated, ==, 0);
  g_autofree char *source = default_source ();
  g_assert_cmpstr (source, ==, "mic_b");
  g_assert_cmpstr (json_node_get_string (cc_luma_fixture_get (model, "inDev")), ==, id);
  GtkWidget *volume = named (root, "cf-inVol");
  g_assert_true (GTK_IS_RANGE (volume));
  gtk_range_set_value (GTK_RANGE (volume), 42); spin (500);
  g_assert_cmpuint (delegated, ==, 0);
  g_assert_cmpint (json_node_get_int (cc_luma_fixture_get (model, "inVol")), ==, 42);
  gtk_window_destroy (GTK_WINDOW (window)); sound_stop (owner); g_clear_object (&model); spin (100);
  model = model_new (&owner);
  g_autofree char *reopened = input_id (model, "Test_microphone_B");
  g_assert_cmpstr (json_node_get_string (cc_luma_fixture_get (model, "inDev")), ==, reopened);
  g_assert_cmpint (json_node_get_int (cc_luma_fixture_get (model, "inVol")), ==, 42);
  window = open_view (model, &root);
  g_assert_true (gtk_widget_activate_action (root, "cf.pick", "(ss)", "inDev", "not-connected")); spin (100);
  g_assert_nonnull (cc_luma_view_get_error (root));
  g_assert_cmpuint (delegated, ==, 0);
  g_autofree char *unchanged = default_source ();
  g_assert_cmpstr (unchanged, ==, "mic_b");
  gtk_window_destroy (GTK_WINDOW (window)); sound_stop (owner);
}
int main (int argc, char **argv)
{
  const char *server = g_getenv ("PULSE_SERVER");
  g_assert_nonnull (server);
  g_assert_true (g_str_has_prefix (server, "unix:/tmp/luma-sound-input-test-"));
  gtk_test_init (&argc, &argv, NULL);
  /* The
   * virtual-card warning is expected; preserve fatal criticals and other
   * domains' warnings. Native hardware-card checks are a separate test. */
  g_log_set_always_fatal (G_LOG_FATAL_MASK | G_LOG_LEVEL_CRITICAL);
  g_log_set_fatal_mask ("Gtk", G_LOG_FATAL_MASK | G_LOG_LEVEL_CRITICAL | G_LOG_LEVEL_WARNING);
  g_log_set_fatal_mask ("GLib", G_LOG_FATAL_MASK | G_LOG_LEVEL_CRITICAL | G_LOG_LEVEL_WARNING);
  g_test_add_func ("/settings/sound/output-app-volume-input-reopen", selection);
  return g_test_run ();
}
