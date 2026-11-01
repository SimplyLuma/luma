/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Compile against the patched production owner, not a copy of its mappings. */
#include "cc-luma-live-shelf.c"
void cc_luma_live_ready (CcLumaFixture *model, const char *adapter) {}
static gboolean writer (CcLumaFixture *model, const char *key, JsonNode *value, gpointer data, GError **error)
{ return home_write (data, key, value, error); }
static void drain (void) { while (g_main_context_iteration (NULL, FALSE)); }
static void choose (CcLumaFixture *model, const char *key, const char *value)
{
  g_autoptr (GError) error = NULL;
  g_autoptr (JsonNode) n = json_node_init_string (json_node_alloc (), value);
  gboolean changed = cc_luma_fixture_set (model, key, n, &error);
  if (!changed) g_printerr ("Home write %s=%s: %s\n", key, value, error ? error->message : "no error");
  g_assert_true (changed);
  g_assert_no_error (error); drain ();
}
int main (int argc, char **argv)
{
  g_setenv ("GSETTINGS_BACKEND", "memory", TRUE);
  g_autoptr (GError) error = NULL;
  g_autoptr (JsonParser) parser = json_parser_new ();
  g_assert_true (json_parser_load_from_file (parser, argv[1], &error));
  g_assert_no_error (error);
  g_autoptr (CcLumaFixture) model = cc_luma_fixture_new_live (json_parser_get_root (parser));
  CcLumaGsTable *table = home_start (model);
  cc_luma_fixture_set_writer (model, writer, table);
  if (argc > 2) {
    g_assert_false (json_node_get_boolean (cc_luma_fixture_get (model, "homeAvailable")));
    g_assert_false (cc_luma_fixture_set_flag (model, "homeReset", TRUE, &error));
    g_assert_error (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED);
    cc_luma_gs_table_free (table);
    g_print ("PASS Home native owner: missing schema refuses writes without crashing\n");
    return 0;
  }
  g_assert_true (json_node_get_boolean (cc_luma_fixture_get (model, "homeAvailable")));
  g_autoptr (GSettings) settings = g_settings_new (SCHEMA);
  const char *layout = "{\"sentinel\":\"layout must not be reset\"}";
  g_settings_set_string (settings, "home-layouts", layout);
  for (guint i = 3; i <= 12; i++) {
    g_autofree char *number = g_strdup_printf ("%u", i);
    choose (model, "homeColumns", number);
    g_assert_cmpuint (g_settings_get_uint (settings, "home-columns"), ==, i);
    choose (model, "homeRows", number);
    g_assert_cmpuint (g_settings_get_uint (settings, "home-rows"), ==, i);
  }
  choose (model, "homeRows", "2");
  for (const char * const *size = (const char * const[]) { "automatic", "small", "medium", "large", NULL }; *size; size++) {
    choose (model, "homeIconSize", *size);
    g_autofree char *actual = g_settings_get_string (settings, "home-icon-size");
    g_assert_cmpstr (actual, ==, *size);
  }
  g_assert_true (cc_luma_fixture_set_flag (model, "homeLabels", FALSE, &error));
  g_assert_no_error (error);
  g_assert_false (g_settings_get_boolean (settings, "home-show-labels"));
  g_autoptr (JsonNode) invalid = json_node_init_string (json_node_alloc (), "99");
  g_assert_false (cc_luma_fixture_set (model, "homeColumns", invalid, &error));
  g_assert_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT);g_clear_error (&error);
  CcLumaGsTable *reopened = home_start (model);
  g_assert_cmpstr (json_node_get_string (cc_luma_fixture_get (model, "homeRows")), ==, "2");
  cc_luma_gs_table_free (reopened);
  g_assert_true (cc_luma_fixture_set_flag (model, "homeReset", TRUE, &error));
  g_assert_no_error (error);drain ();
  g_assert_cmpuint (g_settings_get_uint (settings, "home-columns"), ==, 0);
  g_assert_cmpuint (g_settings_get_uint (settings, "home-rows"), ==, 0);
  g_assert_true (g_settings_get_boolean (settings, "home-show-labels"));
  g_autofree char *saved = g_settings_get_string (settings, "home-layouts");
  g_assert_cmpstr (saved, ==, layout);
  cc_luma_gs_table_free (table);
  g_print ("PASS Home native owner: real schema writes, ranges, reopen, reset and layout preservation\n");
  return 0;
}
