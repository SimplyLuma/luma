/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaBackgroundAgent: the C and introspection form of luma_appkit.background.
 * The contract is docs/developer/kit/background-agents.md (ADR-033).
 */
#include "luma-background-agent.h"

#include <glib-unix.h>
#include <signal.h>
#include <string.h>

#define SERVICE_NAME "org.projectluma.Background1"
#define SERVICE_PATH "/org/projectluma/Background1"
#define AGENT_INTERFACE "org.projectluma.BackgroundAgent1"
#define AGENT_PATH "/org/projectluma/BackgroundAgent1"

static const char agent_xml[] =
    "<node>"
    "  <interface name='org.projectluma.BackgroundAgent1'>"
    "    <method name='Wake'>"
    "      <arg name='reason' type='s' direction='in'/>"
    "      <arg name='details' type='a{sv}' direction='in'/>"
    "    </method>"
    "    <property name='AppId' type='s' access='read'/>"
    "    <property name='Values' type='a{sv}' access='read'/>"
    "  </interface>"
    "</node>";

struct _LumaBackgroundAgent {
  GObject parent_instance;
  char *app_id;
  char *agent_id;
  GHashTable *values; /* char* -> GVariant* */
  GDBusConnection *connection;
  GDBusNodeInfo *node;
  guint registration;
  guint name_id;
  guint flush_source;
  GMainLoop *loop;
  gboolean started;
  gboolean lost;
  int status;
};

G_DEFINE_FINAL_TYPE(LumaBackgroundAgent, luma_background_agent, G_TYPE_OBJECT)
G_DEFINE_QUARK(luma-background-agent-error-quark, luma_background_agent_error)

enum { PROP_0, PROP_APP_ID, PROP_AGENT_ID, N_PROPS };
enum { SIGNAL_START, SIGNAL_WAKE, SIGNAL_STOP, N_SIGNALS };
static GParamSpec *properties[N_PROPS];
static guint signals[N_SIGNALS];

static gboolean
valid_app_id(const char *value)
{
  static GRegex *pattern;
  if (value == NULL || strlen(value) > 255)
    return FALSE;
  if (g_once_init_enter_pointer(&pattern)) {
    GRegex *compiled = g_regex_new(
        "^[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+"
        "\\.[A-Za-z_][A-Za-z0-9_-]*$", G_REGEX_OPTIMIZE, 0, NULL);
    g_once_init_leave_pointer(&pattern, compiled);
  }
  return g_regex_match(pattern, value, 0, NULL);
}

static gboolean
matches(const char *pattern, const char *value)
{
  return value != NULL &&
         g_regex_match_simple(pattern, value, G_REGEX_DEFAULT, 0);
}

static gboolean
valid_value_name(const char *name)
{
  return matches("^[a-z][a-z0-9._-]{0,63}$", name);
}

static gboolean
valid_schedule_name(const char *name)
{
  return matches("^[a-z0-9][a-z0-9-]{0,39}$", name) &&
         g_strcmp0(name, "interval") != 0;
}

static void
luma_background_agent_finalize(GObject *object)
{
  LumaBackgroundAgent *self = LUMA_BACKGROUND_AGENT(object);
  g_clear_handle_id(&self->flush_source, g_source_remove);
  if (self->name_id)
    g_bus_unown_name(self->name_id);
  if (self->connection && self->registration)
    g_dbus_connection_unregister_object(self->connection, self->registration);
  g_clear_object(&self->connection);
  g_clear_pointer(&self->node, g_dbus_node_info_unref);
  g_clear_pointer(&self->values, g_hash_table_unref);
  g_clear_pointer(&self->loop, g_main_loop_unref);
  g_clear_pointer(&self->app_id, g_free);
  g_clear_pointer(&self->agent_id, g_free);
  G_OBJECT_CLASS(luma_background_agent_parent_class)->finalize(object);
}

static void
luma_background_agent_get_property(GObject *object, guint id, GValue *value,
                                   GParamSpec *pspec)
{
  LumaBackgroundAgent *self = LUMA_BACKGROUND_AGENT(object);
  switch (id) {
  case PROP_APP_ID:
    g_value_set_string(value, self->app_id);
    break;
  case PROP_AGENT_ID:
    g_value_set_string(value, self->agent_id);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void
luma_background_agent_set_property(GObject *object, guint id,
                                   const GValue *value, GParamSpec *pspec)
{
  LumaBackgroundAgent *self = LUMA_BACKGROUND_AGENT(object);
  switch (id) {
  case PROP_APP_ID:
    g_free(self->app_id);
    self->app_id = g_value_dup_string(value);
    break;
  case PROP_AGENT_ID:
    g_free(self->agent_id);
    self->agent_id = g_value_dup_string(value);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void
luma_background_agent_class_init(LumaBackgroundAgentClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->finalize = luma_background_agent_finalize;
  object_class->get_property = luma_background_agent_get_property;
  object_class->set_property = luma_background_agent_set_property;

  properties[PROP_APP_ID] = g_param_spec_string(
      "app-id", NULL, NULL, NULL,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  properties[PROP_AGENT_ID] = g_param_spec_string(
      "agent-id", NULL, NULL, NULL,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, N_PROPS, properties);

  /**
   * LumaBackgroundAgent::start:
   *
   * Emitted once the agent owns its bus name, before the first wake.
   */
  signals[SIGNAL_START] = g_signal_new("start", G_TYPE_FROM_CLASS(klass),
                                       G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                       G_TYPE_NONE, 0);
  /**
   * LumaBackgroundAgent::wake:
   * @reason: login, network, resume, schedule or request
   * @details: `a{sv}` with optional `schedule` (s) and `missed` (b)
   */
  signals[SIGNAL_WAKE] = g_signal_new(
      "wake", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
      NULL, G_TYPE_NONE, 2, G_TYPE_STRING, G_TYPE_VARIANT);
  signals[SIGNAL_STOP] = g_signal_new("stop", G_TYPE_FROM_CLASS(klass),
                                      G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                      G_TYPE_NONE, 0);
}

static void
luma_background_agent_init(LumaBackgroundAgent *self)
{
  self->values = g_hash_table_new_full(g_str_hash, g_str_equal, g_free,
                                       (GDestroyNotify)g_variant_unref);
}

LumaBackgroundAgent *
luma_background_agent_new(const char *app_id, const char *agent_id)
{
  g_return_val_if_fail(valid_app_id(app_id), NULL);
  g_return_val_if_fail(agent_id != NULL && g_str_has_prefix(agent_id, app_id) &&
                           agent_id[strlen(app_id)] == '.' &&
                           agent_id[strlen(app_id) + 1] != '\0' &&
                           g_dbus_is_name(agent_id) && !g_dbus_is_unique_name(agent_id),
                       NULL);
  return g_object_new(LUMA_TYPE_BACKGROUND_AGENT, "app-id", app_id,
                      "agent-id", agent_id, NULL);
}

const char *
luma_background_agent_get_app_id(LumaBackgroundAgent *self)
{
  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), NULL);
  return self->app_id;
}

const char *
luma_background_agent_get_agent_id(LumaBackgroundAgent *self)
{
  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), NULL);
  return self->agent_id;
}

gboolean
luma_background_agent_is_requested(int argc, char **argv)
{
  for (int i = 1; i < argc && argv != NULL; i++)
    if (g_strcmp0(argv[i], "--agent") == 0)
      return TRUE;
  return FALSE;
}

GVariant *
luma_background_agent_dup_values(LumaBackgroundAgent *self)
{
  GVariantBuilder builder;
  GHashTableIter iter;
  gpointer key, value;
  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), NULL);
  g_variant_builder_init(&builder, G_VARIANT_TYPE_VARDICT);
  g_hash_table_iter_init(&iter, self->values);
  while (g_hash_table_iter_next(&iter, &key, &value))
    g_variant_builder_add(&builder, "{sv}", key, value);
  return g_variant_ref_sink(g_variant_builder_end(&builder));
}

static gboolean
flush_values(gpointer data)
{
  LumaBackgroundAgent *self = data;
  self->flush_source = 0;
  if (self->connection && self->registration) {
    GVariantBuilder changed;
    g_autoptr(GVariant) values = luma_background_agent_dup_values(self);
    g_variant_builder_init(&changed, G_VARIANT_TYPE_VARDICT);
    g_variant_builder_add(&changed, "{sv}", "Values", values);
    g_dbus_connection_emit_signal(
        self->connection, NULL, AGENT_PATH, "org.freedesktop.DBus.Properties",
        "PropertiesChanged",
        g_variant_new("(sa{sv}as)", AGENT_INTERFACE, &changed, NULL), NULL);
  }
  return G_SOURCE_REMOVE;
}

static void
queue_flush(LumaBackgroundAgent *self)
{
  if (self->flush_source == 0)
    self->flush_source = g_idle_add(flush_values, self);
}

gboolean
luma_background_agent_publish(LumaBackgroundAgent *self, const char *name,
                              GVariant *value, GError **error)
{
  GVariant *current;
  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), FALSE);
  g_return_val_if_fail(value != NULL, FALSE);
  g_variant_ref_sink(value);
  if (!valid_value_name(name)) {
    g_variant_unref(value);
    g_set_error(error, LUMA_BACKGROUND_AGENT_ERROR,
                LUMA_BACKGROUND_AGENT_ERROR_INVALID,
                "invalid value name: %s", name ? name : "(null)");
    return FALSE;
  }
  current = g_hash_table_lookup(self->values, name);
  if (current != NULL && g_variant_equal(current, value)) {
    g_variant_unref(value);
    return TRUE;
  }
  g_hash_table_replace(self->values, g_strdup(name), value);
  queue_flush(self);
  return TRUE;
}

void
luma_background_agent_unpublish(LumaBackgroundAgent *self, const char *name)
{
  g_return_if_fail(LUMA_IS_BACKGROUND_AGENT(self));
  if (name != NULL && g_hash_table_remove(self->values, name))
    queue_flush(self);
}

static gboolean
call_service(LumaBackgroundAgent *self, const char *method,
             GVariant *parameters, GError **error)
{
  g_autoptr(GVariant) reply = NULL;
  g_autoptr(GError) local = NULL;
  g_variant_ref_sink(parameters);
  if (self->connection == NULL) {
    g_variant_unref(parameters);
    g_set_error_literal(error, LUMA_BACKGROUND_AGENT_ERROR,
                        LUMA_BACKGROUND_AGENT_ERROR_NOT_RUNNING,
                        "the agent is not running");
    return FALSE;
  }
  reply = g_dbus_connection_call_sync(
      self->connection, SERVICE_NAME, SERVICE_PATH, SERVICE_NAME, method,
      parameters, NULL, G_DBUS_CALL_FLAGS_NONE, 10000, NULL, &local);
  g_variant_unref(parameters);
  if (reply == NULL) {
    g_dbus_error_strip_remote_error(local);
    g_set_error_literal(error, LUMA_BACKGROUND_AGENT_ERROR,
                        LUMA_BACKGROUND_AGENT_ERROR_REFUSED, local->message);
    return FALSE;
  }
  return TRUE;
}

gboolean
luma_background_agent_schedule_at(LumaBackgroundAgent *self, const char *name,
                                  GDateTime *at, GError **error)
{
  GVariantBuilder options;
  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), FALSE);
  if (!valid_schedule_name(name) || at == NULL) {
    g_set_error(error, LUMA_BACKGROUND_AGENT_ERROR,
                LUMA_BACKGROUND_AGENT_ERROR_INVALID,
                "invalid schedule: %s", name ? name : "(null)");
    return FALSE;
  }
  g_variant_builder_init(&options, G_VARIANT_TYPE_VARDICT);
  g_variant_builder_add(&options, "{sv}", "at",
                        g_variant_new_int64(g_date_time_to_unix(at)));
  return call_service(self, "Schedule",
                      g_variant_new("(sa{sv})", name, &options), error);
}

gboolean
luma_background_agent_schedule_every(LumaBackgroundAgent *self,
                                     const char *name, guint seconds,
                                     GError **error)
{
  GVariantBuilder options;
  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), FALSE);
  if (!valid_schedule_name(name) || seconds < 300) {
    g_set_error(error, LUMA_BACKGROUND_AGENT_ERROR,
                LUMA_BACKGROUND_AGENT_ERROR_INVALID,
                "invalid repeating schedule: %s", name ? name : "(null)");
    return FALSE;
  }
  g_variant_builder_init(&options, G_VARIANT_TYPE_VARDICT);
  g_variant_builder_add(&options, "{sv}", "every", g_variant_new_uint32(seconds));
  return call_service(self, "Schedule",
                      g_variant_new("(sa{sv})", name, &options), error);
}

gboolean
luma_background_agent_unschedule(LumaBackgroundAgent *self, const char *name,
                                 GError **error)
{
  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), FALSE);
  if (!valid_schedule_name(name)) {
    g_set_error(error, LUMA_BACKGROUND_AGENT_ERROR,
                LUMA_BACKGROUND_AGENT_ERROR_INVALID,
                "invalid schedule: %s", name ? name : "(null)");
    return FALSE;
  }
  return call_service(self, "Unschedule", g_variant_new("(s)", name), error);
}

static gboolean
sender_is_service(LumaBackgroundAgent *self, const char *sender)
{
  g_autoptr(GVariant) reply = g_dbus_connection_call_sync(
      self->connection, "org.freedesktop.DBus", "/org/freedesktop/DBus",
      "org.freedesktop.DBus", "GetNameOwner", g_variant_new("(s)", SERVICE_NAME),
      G_VARIANT_TYPE("(s)"), G_DBUS_CALL_FLAGS_NONE, 2000, NULL, NULL);
  const char *owner = NULL;
  if (reply == NULL)
    return FALSE;
  g_variant_get(reply, "(&s)", &owner);
  return g_strcmp0(owner, sender) == 0;
}

typedef struct {
  LumaBackgroundAgent *agent;
  char *reason;
  GVariant *details;
} PendingWake;

static gboolean
emit_wake(gpointer data)
{
  PendingWake *wake = data;
  g_signal_emit(wake->agent, signals[SIGNAL_WAKE], 0, wake->reason,
                wake->details);
  g_object_unref(wake->agent);
  g_free(wake->reason);
  g_variant_unref(wake->details);
  g_free(wake);
  return G_SOURCE_REMOVE;
}

static void
handle_method(G_GNUC_UNUSED GDBusConnection *connection, const char *sender,
              G_GNUC_UNUSED const char *object_path,
              G_GNUC_UNUSED const char *interface_name,
              const char *method_name, GVariant *parameters,
              GDBusMethodInvocation *invocation, gpointer data)
{
  LumaBackgroundAgent *self = data;
  const char *reason = NULL;
  GVariant *details = NULL;
  PendingWake *wake;
  static const char *const reasons[] = {"login", "network", "resume",
                                        "schedule", "request", NULL};

  if (g_strcmp0(method_name, "Wake") != 0) {
    g_dbus_method_invocation_return_dbus_error(
        invocation, "org.freedesktop.DBus.Error.UnknownMethod", method_name);
    return;
  }
  if (!sender_is_service(self, sender)) {
    g_dbus_method_invocation_return_dbus_error(
        invocation, "org.projectluma.BackgroundAgent1.Error.NotAuthorized",
        "wakes are delivered by luma-background");
    return;
  }
  g_variant_get(parameters, "(&s@a{sv})", &reason, &details);
  if (!g_strv_contains(reasons, reason)) {
    g_variant_unref(details);
    g_dbus_method_invocation_return_dbus_error(
        invocation, "org.projectluma.BackgroundAgent1.Error.InvalidArgument",
        "unknown wake reason");
    return;
  }
  wake = g_new0(PendingWake, 1);
  wake->agent = g_object_ref(self);
  wake->reason = g_strdup(reason);
  wake->details = details;
  g_dbus_method_invocation_return_value(invocation, NULL);
  /* Answer first, then run the handler: a slow wake must not hold the
   * service's call open, and wakes are delivered in order by the loop. */
  g_idle_add(emit_wake, wake);
}

static GVariant *
handle_get_property(G_GNUC_UNUSED GDBusConnection *connection,
                    G_GNUC_UNUSED const char *sender,
                    G_GNUC_UNUSED const char *object_path,
                    G_GNUC_UNUSED const char *interface_name,
                    const char *property_name, GError **error, gpointer data)
{
  LumaBackgroundAgent *self = data;
  if (g_strcmp0(property_name, "AppId") == 0)
    return g_variant_new_string(self->app_id);
  if (g_strcmp0(property_name, "Values") == 0)
    return luma_background_agent_dup_values(self); /* full reference, consumed */
  g_set_error(error, G_DBUS_ERROR, G_DBUS_ERROR_UNKNOWN_PROPERTY,
              "unknown property %s", property_name);
  return NULL;
}

static const GDBusInterfaceVTable vtable = {handle_method, handle_get_property,
                                            NULL, {0}};

gboolean
luma_background_agent_register(LumaBackgroundAgent *self,
                               GDBusConnection *connection, GError **error)
{
  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), FALSE);
  g_return_val_if_fail(G_IS_DBUS_CONNECTION(connection), FALSE);
  if (self->registration)
    return TRUE;
  if (self->node == NULL)
    self->node = g_dbus_node_info_new_for_xml(agent_xml, error);
  if (self->node == NULL)
    return FALSE;
  g_set_object(&self->connection, connection);
  self->registration = g_dbus_connection_register_object(
      connection, AGENT_PATH, self->node->interfaces[0], &vtable, self, NULL,
      error);
  return self->registration != 0;
}

static void
name_acquired(G_GNUC_UNUSED GDBusConnection *connection,
              G_GNUC_UNUSED const char *name, gpointer data)
{
  LumaBackgroundAgent *self = data;
  if (self->started)
    return;
  self->started = TRUE;
  if (g_strcmp0(g_getenv("LUMA_BACKGROUND_MANAGED"), "1") != 0)
    g_printerr("%s: running unmanaged; luma-background will not wake it\n",
               self->agent_id);
  g_signal_emit(self, signals[SIGNAL_START], 0);
}

static void
name_lost(G_GNUC_UNUSED GDBusConnection *connection,
          G_GNUC_UNUSED const char *name, gpointer data)
{
  LumaBackgroundAgent *self = data;
  if (!self->started)
    g_printerr("%s: the agent's name is already owned\n", self->agent_id);
  self->lost = TRUE;
  if (self->loop)
    g_main_loop_quit(self->loop);
}

static gboolean
on_terminate(gpointer data)
{
  LumaBackgroundAgent *self = data;
  if (self->loop)
    g_main_loop_quit(self->loop);
  return G_SOURCE_CONTINUE;
}

void
luma_background_agent_quit(LumaBackgroundAgent *self, int status)
{
  g_return_if_fail(LUMA_IS_BACKGROUND_AGENT(self));
  self->status = status;
  if (self->loop)
    g_main_loop_quit(self->loop);
}

int
luma_background_agent_run(LumaBackgroundAgent *self, G_GNUC_UNUSED int argc,
                          G_GNUC_UNUSED char **argv)
{
  g_autoptr(GError) error = NULL;
  g_autoptr(GDBusConnection) connection = NULL;
  guint term_source, int_source;

  g_return_val_if_fail(LUMA_IS_BACKGROUND_AGENT(self), 1);
  connection = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, &error);
  if (connection == NULL) {
    g_printerr("%s: no session bus: %s\n", self->agent_id, error->message);
    return 1;
  }
  if (!luma_background_agent_register(self, connection, &error)) {
    g_printerr("%s: %s\n", self->agent_id, error->message);
    return 1;
  }
  self->loop = g_main_loop_new(NULL, FALSE);
  self->name_id = g_bus_own_name_on_connection(
      connection, self->agent_id, G_BUS_NAME_OWNER_FLAGS_DO_NOT_QUEUE,
      name_acquired, name_lost, self, NULL);
  term_source = g_unix_signal_add(SIGTERM, on_terminate, self);
  int_source = g_unix_signal_add(SIGINT, on_terminate, self);
  g_main_loop_run(self->loop);
  g_source_remove(term_source);
  g_source_remove(int_source);
  if (self->started)
    g_signal_emit(self, signals[SIGNAL_STOP], 0);
  g_bus_unown_name(self->name_id);
  self->name_id = 0;
  g_dbus_connection_flush_sync(connection, NULL, NULL);
  return self->lost && !self->started ? 1 : self->status;
}
