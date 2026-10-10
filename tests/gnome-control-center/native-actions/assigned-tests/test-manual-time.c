/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <gio/gio.h>
static gboolean ntp;
static guint calls;
static GVariant *last;
static GVariant *test_property (GDBusProxy *proxy, const char *name) { return g_variant_ref_sink (g_variant_new_boolean (ntp)); }
static void test_call (GDBusProxy *proxy, const char *method, GVariant *parameters,
  GDBusCallFlags flags, gint timeout, GCancellable *cancel, GAsyncReadyCallback callback, gpointer data)
{
  g_assert_cmpstr (method, ==, "SetTime");
  g_assert_true ((flags & G_DBUS_CALL_FLAGS_ALLOW_INTERACTIVE_AUTHORIZATION) != 0);
  calls++;
  g_clear_pointer (&last, g_variant_unref);
  last = g_variant_ref_sink (parameters);
}
#define g_dbus_proxy_get_cached_property test_property
#define g_dbus_proxy_call test_call
#include SETTINGS_ACTION_SOURCE
gboolean cc_luma_gs_table_covers (CcLumaGsTable *table, const char *key) { return FALSE; }
gboolean cc_luma_gs_table_write (CcLumaGsTable *table, const char *key, JsonNode *node, GError **error) { return FALSE; }
void cc_luma_fixture_publish_boolean (CcLumaFixture *model, const char *key, gboolean value) { }
void cc_luma_fixture_publish_string (CcLumaFixture *model, const char *key, const char *value) { }
void cc_luma_live_ready (CcLumaFixture *model, const char *adapter) { }
void cc_luma_fixture_report_write_error (CcLumaFixture *model, const GError *error) { }
int main (int argc, char **argv)
{
  g_test_init (&argc, &argv, NULL);
  Clock clock = { .timedated = (GDBusProxy *) &clock };
  g_autoptr (GError) error = NULL;
  g_autoptr (JsonNode) good = json_node_init_int (json_node_alloc (), 1791517577000000);
  g_assert_true (clock_write (&clock, "manualTime", good, &error));
  g_assert_no_error (error);
  gint64 timestamp; gboolean relative, interactive;
  g_variant_get (last, "(xbb)", &timestamp, &relative, &interactive);
  g_assert_cmpint (timestamp, ==, 1791517577000000);
  g_assert_false (relative); g_assert_true (interactive);
  g_print ("PASS exact timestamp + absolute time + interactive authorization\n");
  ntp = TRUE;
  g_assert_false (clock_write (&clock, "manualTime", good, &error));
  g_assert_error (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED);
  g_clear_error (&error);
  g_assert_cmpuint (calls, ==, 1);
  ntp = FALSE;
  json_node_set_int (good, -1);
  g_assert_false (clock_write (&clock, "manualTime", good, &error));
  g_assert_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT);
  g_clear_error (&error);
  g_autoptr (JsonNode) wrong = json_node_init_string (json_node_alloc (), "tomorrow");
  g_assert_false (clock_write (&clock, "manualTime", wrong, &error));
  g_assert_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT);
  g_clear_error (&error);
  clock.timedated = NULL;
  g_assert_false (clock_write (&clock, "manualTime", good, &error));
  g_assert_error (error, G_IO_ERROR, G_IO_ERROR_NOT_CONNECTED);
  g_assert_cmpuint (calls, ==, 1);
  g_print ("PASS enabled NTP, invalid date types/range and missing service rejected without writes\n");
  g_clear_pointer (&last, g_variant_unref);
  return 0;
}
