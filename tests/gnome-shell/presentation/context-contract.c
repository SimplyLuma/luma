/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-context.h"
#include <gio/gio.h>

static void call(GDBusConnection *bus, const char *method, GVariant *args) {
  g_autoptr(GError) error = NULL;
  g_autoptr(GVariant) reply = g_dbus_connection_call_sync(bus,
      "org.project_luma.ShellState1", "/org/project_luma/ShellState1",
      "org.project_luma.ShellState1", method, args, NULL,
      G_DBUS_CALL_FLAGS_NONE, 3000, NULL, &error);
  g_assert_no_error(error);
  g_assert_nonnull(reply);
}

static void settle(LumaContext *context, gboolean decorated, gboolean touch) {
  gint64 deadline = g_get_monotonic_time() + 3000000;
  while (g_get_monotonic_time() < deadline &&
         (luma_context_get_decorated(context) != decorated ||
          luma_context_get_touch_targets(context) != touch)) {
    g_main_context_iteration(NULL, FALSE);
    g_usleep(1000);
  }
  g_assert_cmpint(luma_context_get_decorated(context), ==, decorated);
  g_assert_cmpint(luma_context_get_touch_targets(context), ==, touch);
}

int main(void) {
  g_autoptr(GError) error = NULL;
  g_autoptr(GDBusConnection) bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, &error);
  g_assert_no_error(error);
  g_setenv("LUMA_DEVICE_CLASS", "desktop", TRUE);
  g_autoptr(LumaContext) context = luma_context_new_from_environment();
  call(bus, "SetPresentation", g_variant_new("(s)", "tablet"));
  settle(context, FALSE, FALSE);
  call(bus, "SetPresentation", g_variant_new("(s)", "desktop"));
  settle(context, TRUE, FALSE);
  /* The isolated fixture compositor owns the same identity as native Shell. */
  g_autoptr(GVariant) owner = g_dbus_connection_call_sync(bus,
      "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
      "RequestName", g_variant_new("(su)", "org.gnome.Shell", 0),
      NULL, G_DBUS_CALL_FLAGS_NONE, 3000, NULL, &error);
  g_assert_no_error(error);
  g_assert_nonnull(owner);
  call(bus, "ReportInputCapabilities", g_variant_new("(s)",
      "{\"touchscreen\":true,\"tablet_mode\":true,\"keyboard\":false}"));
  settle(context, TRUE, TRUE);
  call(bus, "SetPresentation", g_variant_new("(s)", "automatic"));
  settle(context, FALSE, TRUE);
  call(bus, "ReportInputCapabilities", g_variant_new("(s)",
      "{\"touchscreen\":true,\"tablet_mode\":false,\"keyboard\":true}"));
  settle(context, TRUE, TRUE);
  call(bus, "SetPresentation", g_variant_new("(s)", "mobile"));
  settle(context, FALSE, TRUE);
  g_print("PASS native C context follows live modes; input remains independent\n");
  return 0;
}
