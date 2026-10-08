/* SPDX-License-Identifier: Apache-2.0 */
#include <luma-semantics.h>

typedef struct {
  GTestDBus *bus;
  GDBusConnection *agent_bus;
  GDBusConnection *other;
  LumaBackgroundAgent *agent;
  GMainLoop *loop;
  char *reason;
  gboolean missed;
  gboolean changed;
} Fixture;

static GDBusConnection *
connect_new(void)
{
  g_autoptr(GError) error = NULL;
  GDBusConnection *connection = g_dbus_connection_new_for_address_sync(
      g_getenv("DBUS_SESSION_BUS_ADDRESS"),
      G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT |
          G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION,
      NULL, NULL, &error);
  g_assert_no_error(error);
  return connection;
}

static void
setup(Fixture *f, G_GNUC_UNUSED gconstpointer data)
{
  g_autoptr(GError) error = NULL;
  f->bus = g_test_dbus_new(G_TEST_DBUS_NONE);
  g_test_dbus_up(f->bus);
  f->agent_bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, &error);
  g_assert_no_error(error);
  f->other = connect_new();
  f->loop = g_main_loop_new(NULL, FALSE);
  f->agent = luma_background_agent_new("org.example.Chat", "org.example.Chat.Agent");
  g_assert_nonnull(f->agent);
  g_assert_true(luma_background_agent_register(f->agent, f->agent_bus, &error));
  g_assert_no_error(error);
}

static void
teardown(Fixture *f, G_GNUC_UNUSED gconstpointer data)
{
  g_clear_object(&f->agent);
  g_dbus_connection_close_sync(f->other, NULL, NULL);
  g_clear_object(&f->other);
  g_dbus_connection_close_sync(f->agent_bus, NULL, NULL);
  g_clear_object(&f->agent_bus);
  g_clear_pointer(&f->loop, g_main_loop_unref);
  g_free(f->reason);
  g_test_dbus_down(f->bus);
  g_clear_object(&f->bus);
}

static gboolean
quit_soon(gpointer data)
{
  g_main_loop_quit(data);
  return G_SOURCE_REMOVE;
}

static void
spin(Fixture *f)
{
  g_timeout_add(200, quit_soon, f->loop);
  g_main_loop_run(f->loop);
}

typedef struct {
  gboolean done;
  GVariant *reply;
  GError *error;
} Call;

static void
call_done(GObject *source, GAsyncResult *result, gpointer data)
{
  Call *call = data;
  call->reply = g_dbus_connection_call_finish(G_DBUS_CONNECTION(source), result, &call->error);
  call->done = TRUE;
}

/* The agent is served by this process's main context, so calls to it are
 * made asynchronously and the context is iterated until they complete. */
static GVariant *
call_and_wait(GDBusConnection *connection, const char *name, const char *interface,
              const char *method, GVariant *parameters, const GVariantType *reply_type,
              GError **error)
{
  Call call = {0};
  g_dbus_connection_call(connection, name, "/org/projectluma/BackgroundAgent1", interface, method,
                         parameters, reply_type, G_DBUS_CALL_FLAGS_NONE, 5000, NULL, call_done, &call);
  while (!call.done)
    g_main_context_iteration(NULL, TRUE);
  if (call.error)
    g_propagate_error(error, call.error);
  return call.reply;
}

static void
test_identifiers(void)
{
  if (g_test_subprocess()) {
    luma_background_agent_new("org.example.Chat", "org.other.App.Agent");
    return;
  }
  g_test_trap_subprocess(NULL, 0, G_TEST_SUBPROCESS_DEFAULT);
  g_test_trap_assert_failed();
  g_assert_true(luma_background_agent_is_requested(2, (char *[]){"chat", "--agent", NULL}));
  g_assert_false(luma_background_agent_is_requested(2, (char *[]){"--agent", "x", NULL}));
}

static void
on_properties_changed(G_GNUC_UNUSED GDBusConnection *c, G_GNUC_UNUSED const char *sender,
                      G_GNUC_UNUSED const char *path, G_GNUC_UNUSED const char *iface,
                      G_GNUC_UNUSED const char *signal, G_GNUC_UNUSED GVariant *params,
                      gpointer data)
{
  ((Fixture *)data)->changed = TRUE;
}

static void
test_publish(Fixture *f, G_GNUC_UNUSED gconstpointer data)
{
  g_autoptr(GError) error = NULL;
  g_autoptr(GVariant) reply = NULL;
  g_autoptr(GVariant) value = NULL;
  g_autoptr(GVariant) values = NULL;
  const char *unique = g_dbus_connection_get_unique_name(f->agent_bus);

  g_dbus_connection_signal_subscribe(f->other, unique, "org.freedesktop.DBus.Properties",
                                     "PropertiesChanged", "/org/projectluma/BackgroundAgent1",
                                     NULL, G_DBUS_SIGNAL_FLAGS_NONE, on_properties_changed, f, NULL);
  g_assert_true(luma_background_agent_publish(f->agent, "unread", g_variant_new_uint32(3), &error));
  g_assert_false(luma_background_agent_publish(f->agent, "Bad Name", g_variant_new_uint32(1), &error));
  g_assert_error(error, LUMA_BACKGROUND_AGENT_ERROR, LUMA_BACKGROUND_AGENT_ERROR_INVALID);
  g_clear_error(&error);
  spin(f);
  g_assert_true(f->changed);

  reply = call_and_wait(f->other, unique, "org.freedesktop.DBus.Properties", "Get",
                        g_variant_new("(ss)", "org.projectluma.BackgroundAgent1", "Values"),
                        G_VARIANT_TYPE("(v)"), &error);
  g_assert_no_error(error);
  g_assert_nonnull(reply);
  g_variant_get(reply, "(v)", &values);
  value = g_variant_lookup_value(values, "unread", G_VARIANT_TYPE_UINT32);
  g_assert_nonnull(value);
  g_assert_cmpuint(g_variant_get_uint32(value), ==, 3);
}

static void
on_wake(G_GNUC_UNUSED LumaBackgroundAgent *agent, const char *reason, GVariant *details, gpointer data)
{
  Fixture *f = data;
  g_free(f->reason);
  f->reason = g_strdup(reason);
  g_variant_lookup(details, "missed", "b", &f->missed);
}

static void
test_wake_only_from_service(Fixture *f, G_GNUC_UNUSED gconstpointer data)
{
  g_autoptr(GError) error = NULL;
  g_autoptr(GVariant) refused = NULL;
  g_autoptr(GVariant) accepted = NULL;
  GVariantBuilder details;
  const char *unique = g_dbus_connection_get_unique_name(f->agent_bus);

  g_signal_connect(f->agent, "wake", G_CALLBACK(on_wake), f);
  refused = call_and_wait(f->other, unique, "org.projectluma.BackgroundAgent1", "Wake",
                          g_variant_new("(sa{sv})", "network", NULL), NULL, &error);
  g_assert_null(refused);
  g_assert_nonnull(error);
  g_clear_error(&error);

  g_bus_own_name_on_connection(f->other, "org.projectluma.Background1", G_BUS_NAME_OWNER_FLAGS_NONE,
                               NULL, NULL, NULL, NULL);
  spin(f);
  g_variant_builder_init(&details, G_VARIANT_TYPE_VARDICT);
  g_variant_builder_add(&details, "{sv}", "schedule", g_variant_new_string("alarm-1"));
  g_variant_builder_add(&details, "{sv}", "missed", g_variant_new_boolean(TRUE));
  accepted = call_and_wait(f->other, unique, "org.projectluma.BackgroundAgent1", "Wake",
                           g_variant_new("(sa{sv})", "schedule", &details), NULL, &error);
  g_assert_no_error(error);
  spin(f);
  g_assert_cmpstr(f->reason, ==, "schedule");
  g_assert_true(f->missed);
}

static void
test_schedule_validation(Fixture *f, G_GNUC_UNUSED gconstpointer data)
{
  g_autoptr(GError) error = NULL;
  g_assert_false(luma_background_agent_schedule_every(f->agent, "fast", 10, &error));
  g_assert_error(error, LUMA_BACKGROUND_AGENT_ERROR, LUMA_BACKGROUND_AGENT_ERROR_INVALID);
  g_clear_error(&error);
  g_assert_false(luma_background_agent_unschedule(f->agent, "interval", &error));
  g_assert_error(error, LUMA_BACKGROUND_AGENT_ERROR, LUMA_BACKGROUND_AGENT_ERROR_INVALID);
  g_clear_error(&error);
  /* No service on this bus: a valid request is refused, not crashed. */
  g_assert_false(luma_background_agent_schedule_every(f->agent, "refresh", 900, &error));
  g_assert_error(error, LUMA_BACKGROUND_AGENT_ERROR, LUMA_BACKGROUND_AGENT_ERROR_REFUSED);
}

int
main(int argc, char **argv)
{
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/background-agent/identifiers", test_identifiers);
  g_test_add("/background-agent/publish", Fixture, NULL, setup, test_publish, teardown);
  g_test_add("/background-agent/wake", Fixture, NULL, setup, test_wake_only_from_service, teardown);
  g_test_add("/background-agent/schedule", Fixture, NULL, setup, test_schedule_validation, teardown);
  return g_test_run();
}
