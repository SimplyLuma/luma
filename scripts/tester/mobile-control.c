/* SPDX-License-Identifier: GPL-3.0-or-later
 * Optional automation bridge for the pinned native phone renderer.
 * Loaded only in the disposable tester's Phosh process. No alternate UI. */
#include <gtk/gtk.h>
#include <gio/gio.h>
#include <gmodule.h>

static const char xml[] =
  "<node><interface name='org.project_luma.Tester1'>"
  "<method name='ShowHome'/></interface></node>";
static GDBusNodeInfo *info;

typedef struct {
  GtkWidget *home;
  GDBusMethodInvocation *invocation;
  guint attempts;
} PendingHome;

static void
suppress_pointer_tooltips (GtkWidget *widget, gpointer unused)
{
  (void) unused;
  /* The phone uses touch. A parked emulated pointer can otherwise open a
   * tooltip after its parent folds; Wayland promotes that orphan surface to
   * an app window and the phone correctly folds Home to reveal the new app.
   * Remove pointer-only hints from the simulator shell, leaving app processes
   * and the native Home/Library renderer unchanged. */
  gtk_widget_set_tooltip_text (widget, NULL);
  gtk_widget_set_has_tooltip (widget, FALSE);
  if (GTK_IS_CONTAINER (widget))
    gtk_container_forall (GTK_CONTAINER (widget), suppress_pointer_tooltips, NULL);
}

static gboolean
activate_home (GtkWidget *home)
{
  GType type = g_type_from_name ("PhoshHome");
  if (!type || gtk_widget_in_destruction (home))
    return FALSE;
  GObject *gesture = gtk_widget_get_template_child (home, type, "click_gesture");
  if (!GTK_IS_GESTURE_MULTI_PRESS (gesture))
    return FALSE;
  /* These are the renderer's existing accessible Home action handlers. Using
   * the action avoids a synthetic touch competing with the folding surface. */
  g_signal_emit_by_name (gesture, "pressed", 1, 0.0, 0.0);
  g_signal_emit_by_name (gesture, "released", 1, 0.0, 0.0);
  return TRUE;
}

static gboolean
finish_home (gpointer data)
{
  PendingHome *pending = data;
  /* Let the native IME hide and restore its gesture bar first, then perform
   * the same idempotent Home action after that asynchronous layout change. */
  if (activate_home (pending->home))
    g_dbus_method_invocation_return_value (pending->invocation, NULL);
  else
    g_dbus_method_invocation_return_dbus_error (pending->invocation,
      "org.project_luma.Tester1.NotReady", "Native phone Home is unavailable.");
  g_object_unref (pending->home);
  g_object_unref (pending->invocation);
  g_free (pending);
  return G_SOURCE_REMOVE;
}

static gboolean
wait_folded (gpointer data)
{
  PendingHome *pending = data;
  gint state = 0;
  GParamSpec *spec = g_object_class_find_property (G_OBJECT_GET_CLASS (pending->home), "drag-state");
  if (spec && G_IS_PARAM_SPEC_ENUM (spec)) {
    g_object_get (pending->home, "drag-state", &state, NULL);
    GEnumValue *value = g_enum_get_value (G_PARAM_SPEC_ENUM (spec)->enum_class, state);
    if (value && !g_str_equal (value->value_nick, "folded") && pending->attempts++ < 30)
      return G_SOURCE_CONTINUE;
  }
  /* Synchronize the public Home state with the compositor's completed fold
   * before requesting its native unfold. A keyboard resize can otherwise
   * leave the two cached states disagreeing and make ShowHome a no-op. */
  g_object_notify (G_OBJECT (pending->home), "drag-state");
  activate_home (pending->home);
  g_timeout_add (200, finish_home, pending);
  return G_SOURCE_REMOVE;
}

static void
method_call (GDBusConnection *connection, const char *sender, const char *path,
             const char *interface, const char *method, GVariant *parameters,
             GDBusMethodInvocation *invocation, gpointer data)
{
  (void) connection; (void) sender; (void) path; (void) interface;
  (void) parameters; (void) data;
  if (!g_str_equal (method, "ShowHome")) {
    g_dbus_method_invocation_return_dbus_error (invocation,
      "org.project_luma.Tester1.UnknownMethod", "Unknown simulator action.");
    return;
  }
  GType type = g_type_from_name ("PhoshHome");
  GList *windows = gtk_window_list_toplevels ();
  g_list_foreach (windows, (GFunc) suppress_pointer_tooltips, NULL);
  GtkWidget *home = NULL;
  for (GList *item = windows; item; item = item->next) {
    if (type && G_TYPE_CHECK_INSTANCE_TYPE (item->data, type)) {
      home = g_object_ref (item->data);
      break;
    }
  }
  g_list_free (windows);
  GParamSpec *state_spec = home ? g_object_class_find_property (G_OBJECT_GET_CLASS (home), "state") : NULL;
  GEnumValue *folded = state_spec && G_IS_PARAM_SPEC_ENUM (state_spec) ?
    g_enum_get_value_by_nick (G_PARAM_SPEC_ENUM (state_spec)->enum_class, "folded") : NULL;
  if (!home || !folded) {
    g_clear_object (&home);
    g_dbus_method_invocation_return_dbus_error (invocation,
      "org.project_luma.Tester1.NotReady", "The pinned native Home action is unavailable.");
    return;
  }
  PendingHome *pending = g_new0 (PendingHome, 1);
  pending->home = home;
  pending->invocation = g_object_ref (invocation);
  g_object_set (home, "state", folded->value, NULL);
  g_timeout_add (50, wait_folded, pending);
}

static const GDBusInterfaceVTable vtable = { .method_call = method_call };

static void
bus_acquired (GDBusConnection *connection, const char *name, gpointer data)
{
  (void) name; (void) data;
  GError *error = NULL;
  info = g_dbus_node_info_new_for_xml (xml, &error);
  if (info)
    g_dbus_connection_register_object (connection, "/org/project_luma/Tester",
      info->interfaces[0], &vtable, NULL, NULL, &error);
  if (error) {
    g_warning ("Tester control interface: %s", error->message);
    g_error_free (error);
  }
}

G_MODULE_EXPORT void
gtk_module_init (gint *argc, gchar ***argv)
{
  (void) argc; (void) argv;
  if (g_strcmp0 (g_getenv ("LUMA_TESTER_CONTROL"), "1") == 0 &&
      g_strcmp0 (g_get_prgname (), "phosh") == 0)
    g_bus_own_name (G_BUS_TYPE_SESSION, "org.project_luma.Tester",
      G_BUS_NAME_OWNER_FLAGS_NONE, bus_acquired, NULL, NULL, NULL, NULL);
}
