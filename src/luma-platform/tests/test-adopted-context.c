/* SPDX-License-Identifier: Apache-2.0 */
#include <luma-ui.h>

#define SESSION_NAME "org.project_luma.ShellState1"
#define SESSION_PATH "/org/project_luma/ShellState1"

static const char *mode = "desktop";
static gboolean touch = TRUE;
static GDBusConnection *connection;

static GVariant *property(GDBusConnection *bus, const char *sender,
                          const char *path, const char *interface,
                          const char *name, GError **error, gpointer data) {
  (void)bus; (void)sender; (void)path; (void)interface; (void)error; (void)data;
  if (g_str_equal(name, "Presentation"))
    return g_variant_new_string(mode);
  GVariantBuilder capabilities;
  g_variant_builder_init(&capabilities, G_VARIANT_TYPE("a{sb}"));
  g_variant_builder_add(&capabilities, "{sb}", "touchscreen", touch);
  return g_variant_builder_end(&capabilities);
}

static void await_mode(LumaContext *context, const char *expected) {
  gint64 deadline = g_get_monotonic_time() + 3 * G_TIME_SPAN_SECOND;
  while (g_strcmp0(luma_context_get_experience(context), expected) != 0 &&
         g_get_monotonic_time() < deadline) {
    g_main_context_iteration(NULL, FALSE);
    g_usleep(1000);
  }
  g_assert_cmpstr(luma_context_get_experience(context), ==, expected);
  while (g_main_context_iteration(NULL, FALSE));
}

static void change_mode(LumaContext *context, const char *next) {
  mode = next;
  GVariantBuilder changed, invalidated;
  g_variant_builder_init(&changed, G_VARIANT_TYPE("a{sv}"));
  g_variant_builder_init(&invalidated, G_VARIANT_TYPE("as"));
  g_variant_builder_add(&changed, "{sv}", "Presentation", g_variant_new_string(mode));
  g_assert_true(g_dbus_connection_emit_signal(connection, NULL, SESSION_PATH,
      "org.freedesktop.DBus.Properties", "PropertiesChanged",
      g_variant_new("(sa{sv}as)", SESSION_NAME, &changed, &invalidated), NULL));
  await_mode(context, next);
}

static void test_adopted_live_context(void) {
  g_autoptr(GtkApplication) app = gtk_application_new(
      "org.projectluma.AdoptedContextTest", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = g_object_new(ADW_TYPE_APPLICATION_WINDOW, "application", app, NULL);
  GtkWidget *toolbar = adw_toolbar_view_new();
  GtkWidget *header = adw_header_bar_new();
  GtkWidget *body = gtk_label_new("Existing native application content");
  adw_toolbar_view_add_top_bar(ADW_TOOLBAR_VIEW(toolbar), header);
  adw_toolbar_view_set_content(ADW_TOOLBAR_VIEW(toolbar), body);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(window), toolbar);
  gtk_window_set_default_size(GTK_WINDOW(window), 1100, 800);
  g_assert_true(luma_application_window_frame_adopt(ADW_APPLICATION_WINDOW(window)) == header);
  LumaContext *context = g_object_get_data(G_OBJECT(window), "luma-adopted-context");
  g_assert_true(LUMA_IS_CONTEXT(context));
  await_mode(context, "desktop");
  /* Touch capability confirms the asynchronous proxy loaded its initial state. */
  gint64 deadline = g_get_monotonic_time() + 3 * G_TIME_SPAN_SECOND;
  while (!luma_context_get_touch_targets(context) && g_get_monotonic_time() < deadline) {
    g_main_context_iteration(NULL, FALSE);
    g_usleep(1000);
  }
  g_assert_true(luma_context_get_touch_targets(context));
  for (int round = 0; round < 2; round++) {
    change_mode(context, "tablet");
    g_assert_false(gtk_window_get_decorated(GTK_WINDOW(window)));
    g_assert_false(gtk_widget_get_visible(header));
    g_assert_false(gtk_widget_get_can_target(header));
    g_assert_true(gtk_widget_has_css_class(window, "lumaui-tablet-mode"));
    g_assert_true(adw_toolbar_view_get_content(ADW_TOOLBAR_VIEW(toolbar)) == body);
    change_mode(context, "mobile");
    g_assert_false(gtk_widget_get_visible(header));
    g_assert_false(gtk_widget_has_css_class(window, "lumaui-tablet-mode"));
    change_mode(context, "desktop");
    g_assert_true(gtk_window_get_decorated(GTK_WINDOW(window)));
    g_assert_true(gtk_widget_get_visible(header));
    g_assert_true(gtk_widget_get_can_target(header));
    g_assert_false(gtk_widget_has_css_class(window, "lumaui-phone-device"));
    g_assert_true(luma_application_window_frame_adopt(ADW_APPLICATION_WINDOW(window)) == header);
    g_assert_true(g_object_get_data(G_OBJECT(window), "luma-adopted-context") == context);
  }
  gtk_window_destroy(GTK_WINDOW(window));
}

static void run_isolated_child(void) {
  g_test_trap_subprocess(NULL, 10 * G_TIME_SPAN_SECOND, G_TEST_SUBPROCESS_DEFAULT);
  g_test_trap_assert_passed();
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  g_autoptr(GTestDBus) bus = NULL;
  if (!g_test_subprocess()) {
    /* The parent owns the bus; GTK's process-wide bus references stay in the
     * child, so teardown can verify the parent's clean connection lifecycle. */
    bus = g_test_dbus_new(G_TEST_DBUS_NONE);
    g_autofree char *display = g_strdup(g_getenv("DISPLAY"));
    g_test_dbus_up(bus);
    if (display != NULL)
      g_setenv("DISPLAY", display, TRUE);
    g_test_add_func("/window/adopted-live-context", run_isolated_child);
  } else {
    gtk_init();
    luma_init();
    connection = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, NULL);
    g_assert_nonnull(connection);
    static const GDBusInterfaceVTable vtable = {.get_property = property};
    g_autoptr(GDBusNodeInfo) node = g_dbus_node_info_new_for_xml(
        "<node><interface name='" SESSION_NAME "'>"
        "<property name='Presentation' type='s' access='read'/>"
        "<property name='InputCapabilities' type='a{sb}' access='read'/>"
        "</interface></node>", NULL);
    guint registration = g_dbus_connection_register_object(connection, SESSION_PATH,
        node->interfaces[0], &vtable, NULL, NULL, NULL);
    g_assert_cmpuint(registration, >, 0);
    g_autoptr(GVariant) reply = g_dbus_connection_call_sync(connection,
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
        "RequestName", g_variant_new("(su)", SESSION_NAME, 0), NULL,
        G_DBUS_CALL_FLAGS_NONE, -1, NULL, NULL);
    g_assert_nonnull(reply);
    g_test_add_func("/window/adopted-live-context", test_adopted_live_context);
  }
  int result = g_test_run();
  if (connection != NULL) {
    g_dbus_connection_close_sync(connection, NULL, NULL);
    g_clear_object(&connection);
  }
  if (bus != NULL)
    g_test_dbus_down(bus);
  return result;
}
