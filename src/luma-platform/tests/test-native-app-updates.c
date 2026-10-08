/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "../ui/luma-native-app-updates-private.h"
#include <string.h>

#define BUS "org.freedesktop.portal.Flatpak"
#define PATH "/org/freedesktop/portal/Flatpak"
#define MONITOR BUS ".UpdateMonitor"
#define ACTION "luma-application-update"

/* Real private-session GIO transport. This controls the portal peer, not
 * deployment/signature policy; installed-app qualification is separate. */
typedef struct {
  GDBusConnection *bus;
  GDBusNodeInfo *node;
  GArray *objects;
  GPtrArray *handles;
  GDBusMethodInvocation *pending_create, *pending_update;
  char *sender, *handle;
  guint creates, updates, closes, foreign_closes, notifications;
  gboolean hold_create, hold_update, refuse_update, wrong_handle;
} Portal;
static const char xml[] =
  "<node><interface name='" BUS "'>"
  "<method name='CreateUpdateMonitor'><arg type='a{sv}' direction='in'/><arg type='o' direction='out'/></method>"
  "</interface><interface name='" MONITOR "'>"
  "<method name='Close'/><method name='Update'><arg type='s' direction='in'/><arg type='a{sv}' direction='in'/></method>"
  "<signal name='UpdateAvailable'><arg type='a{sv}'/></signal>"
  "<signal name='Progress'><arg type='a{sv}'/></signal>"
  "</interface><interface name='org.freedesktop.Notifications'>"
  "<method name='Notify'><arg type='s' direction='in'/><arg type='u' direction='in'/>"
  "<arg type='s' direction='in'/><arg type='s' direction='in'/><arg type='s' direction='in'/>"
  "<arg type='as' direction='in'/><arg type='a{sv}' direction='in'/><arg type='i' direction='in'/>"
  "<arg type='u' direction='out'/></method>"
  "<method name='GetCapabilities'><arg type='as' direction='out'/></method>"
  "<method name='GetServerInformation'><arg type='s' direction='out'/><arg type='s' direction='out'/>"
  "<arg type='s' direction='out'/><arg type='s' direction='out'/></method>"
  "<method name='CloseNotification'><arg type='u' direction='in'/></method>"
  "</interface></node>";
static const char a[] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static const char b[] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
static void pump(guint milliseconds) {
  gint64 end = g_get_monotonic_time() + milliseconds * 1000;
  do { while (g_main_context_iteration(NULL, FALSE)); g_usleep(1000); }
  while (g_get_monotonic_time() < end);
}
static void wait_state(GObject *object, const char *expected) {
  gint64 end = g_get_monotonic_time() + 5000000;
  while (strcmp(luma_native_app_updates_state(object), expected) && g_get_monotonic_time() < end) pump(5);
  g_assert_cmpstr(luma_native_app_updates_state(object), ==, expected);
}
static void wait_count(guint *value, guint expected) {
  gint64 end = g_get_monotonic_time() + 5000000;
  while (*value < expected && g_get_monotonic_time() < end) pump(5);
  g_assert_cmpuint(*value, ==, expected);
}
static void commits(Portal *p, const char *running, const char *local, const char *remote) {
  GVariantBuilder d; g_variant_builder_init(&d, G_VARIANT_TYPE_VARDICT);
  g_variant_builder_add(&d, "{sv}", "running-commit", g_variant_new_string(running));
  g_variant_builder_add(&d, "{sv}", "local-commit", g_variant_new_string(local));
  g_variant_builder_add(&d, "{sv}", "remote-commit", g_variant_new_string(remote));
  g_assert_true(g_dbus_connection_emit_signal(p->bus, p->sender, p->handle, MONITOR,
                                            "UpdateAvailable", g_variant_new("(a{sv})", &d), NULL));
}
static void progress(Portal *p, guint status) {
  GVariantBuilder d; g_variant_builder_init(&d, G_VARIANT_TYPE_VARDICT);
  g_variant_builder_add(&d, "{sv}", "status", g_variant_new_uint32(status));
  g_assert_true(g_dbus_connection_emit_signal(p->bus, p->sender, p->handle, MONITOR,
                                            "Progress", g_variant_new("(a{sv})", &d), NULL));
}
static void method(GDBusConnection *connection, const char *sender, const char *path,
                   const char *interface G_GNUC_UNUSED, const char *name,
                   GVariant *parameters, GDBusMethodInvocation *invocation, gpointer data);
static const GDBusInterfaceVTable vtable = { .method_call = method };
static void register_monitor(Portal *p, const char *handle) {
  guint id = g_dbus_connection_register_object(p->bus, handle, p->node->interfaces[1],
                                               &vtable, p, NULL, NULL);
  g_assert_cmpuint(id, >, 0); g_array_append_val(p->objects, id);
  g_ptr_array_add(p->handles, g_strdup(handle));
}
static void method(GDBusConnection *connection G_GNUC_UNUSED, const char *sender, const char *path,
                   const char *interface G_GNUC_UNUSED, const char *name,
                   GVariant *parameters, GDBusMethodInvocation *invocation, gpointer data) {
  Portal *p = data;
  if (!strcmp(name, "CreateUpdateMonitor")) {
    const char *token = NULL;
    g_autoptr(GVariant) options = g_variant_get_child_value(parameters, 0);
    g_assert_true(g_variant_lookup(options, "handle_token", "&s", &token));
    g_autofree char *segment = g_strdup(sender + 1); g_strdelimit(segment, ".", '_');
    g_free(p->sender); p->sender = g_strdup(sender);
    g_free(p->handle); p->handle = g_strdup_printf(PATH "/update_monitor/%s/%s", segment, token);
    register_monitor(p, p->handle); p->creates++;
    commits(p, a, a, b); /* Signal intentionally precedes method acknowledgement. */
    if (p->hold_create) p->pending_create = g_object_ref(invocation);
    else g_dbus_method_invocation_return_value(invocation, g_variant_new("(o)",
                                    p->wrong_handle ? PATH "/update_monitor/foreign" : p->handle));
  } else if (!strcmp(name, "Update")) {
    p->updates++;
    g_assert_true(g_str_has_prefix(path, PATH "/update_monitor/"));
    if (p->hold_update) p->pending_update = g_object_ref(invocation);
    else if (p->refuse_update) g_dbus_method_invocation_return_dbus_error(invocation,
                              "org.freedesktop.DBus.Error.AccessDenied", "User decision required");
    else g_dbus_method_invocation_return_value(invocation, g_variant_new("()"));
  } else if (!strcmp(name, "Close")) {
    if (!strcmp(path, PATH "/update_monitor/foreign")) p->foreign_closes++;
    else p->closes++;
    g_dbus_method_invocation_return_value(invocation, g_variant_new("()"));
  } else if (!strcmp(name, "Notify")) {
    p->notifications++; g_dbus_method_invocation_return_value(invocation, g_variant_new("(u)", 1u));
  } else if (!strcmp(name, "GetCapabilities")) {
    const char *capabilities[] = { "actions", "body", NULL };
    g_dbus_method_invocation_return_value(invocation, g_variant_new("(^as)", capabilities));
  } else if (!strcmp(name, "GetServerInformation"))
    g_dbus_method_invocation_return_value(invocation, g_variant_new("(ssss)", "Private test", "Luma", "1", "1.2"));
  else if (!strcmp(name, "CloseNotification")) g_dbus_method_invocation_return_value(invocation, g_variant_new("()"));
  else g_assert_not_reached();
}
static void request_named(Portal *p, const char *method_name, const char *name) {
  g_autoptr(GVariant) result = g_dbus_connection_call_sync(p->bus, "org.freedesktop.DBus",
    "/org/freedesktop/DBus", "org.freedesktop.DBus", method_name,
    !strcmp(method_name, "RequestName") ? g_variant_new("(su)", name, 0u) : g_variant_new("(s)", name),
    G_VARIANT_TYPE("(u)"), G_DBUS_CALL_FLAGS_NONE, 5000, NULL, NULL);
  g_assert_nonnull(result);
  guint status; g_variant_get(result, "(u)", &status); g_assert_cmpuint(status, ==, 1);
}
static void request_name(Portal *p, const char *method_name) { request_named(p, method_name, BUS); }
static Portal *portal_new(void) {
  Portal *p = g_new0(Portal, 1);
  p->bus = g_dbus_connection_new_for_address_sync(g_getenv("DBUS_SESSION_BUS_ADDRESS"),
    G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT | G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION,
    NULL, NULL, NULL);
  g_assert_nonnull(p->bus);
  p->node = g_dbus_node_info_new_for_xml(xml, NULL);
  p->objects = g_array_new(FALSE, FALSE, sizeof(guint)); p->handles = g_ptr_array_new_with_free_func(g_free);
  guint id = g_dbus_connection_register_object(p->bus, PATH, p->node->interfaces[0], &vtable, p, NULL, NULL);
  g_assert_cmpuint(id, >, 0); g_array_append_val(p->objects, id);
  register_monitor(p, PATH "/update_monitor/foreign");
  request_name(p, "RequestName"); return p;
}
static void finish_create(Portal *p) {
  g_assert_nonnull(p->pending_create);
  g_dbus_method_invocation_return_value(p->pending_create, g_variant_new("(o)", p->handle));
  g_clear_object(&p->pending_create);
}
static void portal_free(Portal *p) {
  if (p->pending_create) { g_dbus_method_invocation_return_dbus_error(p->pending_create, "org.freedesktop.DBus.Error.Failed", "Fixture ended"); g_clear_object(&p->pending_create); }
  if (p->pending_update) { g_dbus_method_invocation_return_dbus_error(p->pending_update, "org.freedesktop.DBus.Error.Failed", "Fixture ended"); g_clear_object(&p->pending_update); }
  for (guint i = 0; i < p->objects->len; i++) g_dbus_connection_unregister_object(p->bus, g_array_index(p->objects, guint, i));
  g_dbus_connection_close_sync(p->bus, NULL, NULL); g_object_unref(p->bus);
  g_dbus_node_info_unref(p->node); g_array_unref(p->objects); g_ptr_array_unref(p->handles);
  g_free(p->sender); g_free(p->handle); g_free(p); pump(20);
}
static GtkApplication *app_new(const char *id) { return gtk_application_new(id, G_APPLICATION_NON_UNIQUE); }
static void app_free(GtkApplication *app, GObject *updates) {
  luma_native_app_updates_close(updates); g_object_unref(updates); g_object_unref(app); pump(20);
}
static void changed(GObject *object G_GNUC_UNUSED, const char *state, gpointer data) {
  if (!strcmp(state, "ready")) (*(guint *)data)++;
}
static void early_progress(void) {
  Portal *p = portal_new(); p->hold_create = TRUE;
  GtkApplication *app = app_new("org.projectluma.NativeEarly");
  GObject *u = luma_native_app_updates_new(app, "org.projectluma.Filer");
  wait_count(&p->creates, 1); pump(50);
  g_assert_cmpstr(luma_native_app_updates_state(u), ==, "checking");
  GAction *action = g_action_map_lookup_action(G_ACTION_MAP(app), ACTION);
  g_assert_false(g_action_get_enabled(action)); g_action_activate(action, NULL); g_assert_cmpuint(p->updates, ==, 0);
  finish_create(p); wait_state(u, "available"); g_assert_true(g_action_get_enabled(action));
  guint ready = 0; g_signal_connect(u, "changed", G_CALLBACK(changed), &ready);
  g_action_activate(action, NULL); wait_count(&p->updates, 1); pump(50); wait_state(u, "installing");
  commits(p, a, a, "invalid"); pump(20); wait_state(u, "installing");
  progress(p, 2); wait_state(u, "ready"); g_assert_cmpuint(ready, ==, 1);
  progress(p, 2); commits(p, a, b, b); pump(50); g_assert_cmpuint(ready, ==, 1);
  app_free(app, u); wait_count(&p->closes, 1); portal_free(p);
}
static void late_close(void) {
  Portal *p = portal_new(); p->hold_create = TRUE;
  GtkApplication *app = app_new("org.projectluma.NativeLate");
  GObject *u = luma_native_app_updates_new(app, "org.projectluma.Filer");
  wait_count(&p->creates, 1); luma_native_app_updates_close(u); finish_create(p);
  wait_count(&p->closes, 1); g_assert_false(g_action_get_enabled(g_action_map_lookup_action(G_ACTION_MAP(app), ACTION)));
  app_free(app, u); g_assert_cmpuint(p->closes, ==, 1); portal_free(p);
}
static void wrong_handle(void) {
  Portal *p = portal_new(); p->wrong_handle = TRUE;
  GtkApplication *app = app_new("org.projectluma.NativeWrong");
  GObject *u = luma_native_app_updates_new(app, "org.projectluma.Filer");
  wait_state(u, "unavailable"); wait_count(&p->closes, 1); g_assert_cmpuint(p->foreign_closes, ==, 0);
  app_free(app, u); g_assert_cmpuint(p->foreign_closes, ==, 0); portal_free(p);
}
static void refused_and_late_error(void) {
  Portal *p = portal_new(); p->refuse_update = TRUE;
  GtkApplication *app = app_new("org.projectluma.NativeRefused");
  GObject *u = luma_native_app_updates_new(app, "org.projectluma.Filer");
  wait_state(u, "available"); GAction *action = g_action_map_lookup_action(G_ACTION_MAP(app), ACTION);
  g_action_activate(action, NULL); wait_state(u, "review"); g_assert_cmpuint(p->updates, ==, 1);
  commits(p, a, a, b); wait_state(u, "available"); p->hold_update = TRUE;
  g_action_activate(action, NULL); wait_count(&p->updates, 2);
  progress(p, 2); wait_state(u, "ready");
  g_dbus_method_invocation_return_dbus_error(p->pending_update, "org.freedesktop.DBus.Error.Failed", "Late transport error");
  g_clear_object(&p->pending_update); pump(50); wait_state(u, "ready");
  app_free(app, u); portal_free(p);
}
static void owner_generation(void) {
  Portal *old = portal_new(); old->hold_create = TRUE;
  GtkApplication *app = app_new("org.projectluma.NativeOwner");
  GObject *u = luma_native_app_updates_new(app, "org.projectluma.Filer"); wait_count(&old->creates, 1);
  request_name(old, "ReleaseName"); wait_state(u, "unavailable");
  Portal *next = portal_new(); wait_count(&next->creates, 1); wait_state(u, "available");
  finish_create(old); wait_count(&old->closes, 1); commits(old, a, b, b); pump(40); wait_state(u, "available");
  g_action_activate(g_action_map_lookup_action(G_ACTION_MAP(app), ACTION), NULL);
  wait_count(&next->updates, 1); g_assert_cmpuint(old->updates, ==, 0);
  app_free(app, u); wait_count(&next->closes, 1); portal_free(old); portal_free(next);
}
static void bounded_signals(void) {
  Portal *p = portal_new();
  GtkApplication *app = app_new("org.projectluma.NativeBounds");
  GObject *u = luma_native_app_updates_new(app, "org.projectluma.Filer"); wait_state(u, "available");
  commits(p, a, b, "not-a-commit"); pump(20); wait_state(u, "available");
  GVariantBuilder d; g_variant_builder_init(&d, G_VARIANT_TYPE_VARDICT);
  g_autofree char *big = g_malloc0(70001); memset(big, 'x', 70000);
  g_variant_builder_add(&d, "{sv}", "running-commit", g_variant_new_string(a));
  g_variant_builder_add(&d, "{sv}", "local-commit", g_variant_new_string(b));
  g_variant_builder_add(&d, "{sv}", "remote-commit", g_variant_new_string(b));
  g_variant_builder_add(&d, "{sv}", "untrusted-extra", g_variant_new_string(big));
  g_assert_true(g_dbus_connection_emit_signal(p->bus, p->sender, p->handle, MONITOR,
                     "UpdateAvailable", g_variant_new("(a{sv})", &d), NULL));
  pump(30); wait_state(u, "available");
  g_assert_true(g_dbus_connection_emit_signal(p->bus, p->sender, PATH "/update_monitor/foreign", MONITOR,
                     "UpdateAvailable", g_variant_new("(s)", "wrong-signature"), NULL));
  pump(20); wait_state(u, "available");
  g_action_activate(g_action_map_lookup_action(G_ACTION_MAP(app), ACTION), NULL); wait_count(&p->updates, 1);
  g_variant_builder_init(&d, G_VARIANT_TYPE_VARDICT); g_variant_builder_add(&d, "{sv}", "status", g_variant_new_string("2"));
  g_assert_true(g_dbus_connection_emit_signal(p->bus, p->sender, p->handle, MONITOR,
                     "Progress", g_variant_new("(a{sv})", &d), NULL));
  progress(p, 99); pump(30); wait_state(u, "installing"); progress(p, 1); wait_state(u, "current");
  app_free(app, u); portal_free(p);
}
static void separate_apps_lifetime(void) {
  Portal *p = portal_new();
  GtkApplication *first = app_new("org.projectluma.NativeFirst"), *second = app_new("org.projectluma.NativeSecond");
  GObject *one = luma_native_app_updates_new(first, "org.projectluma.Filer"); wait_state(one, "available");
  GObject *two = luma_native_app_updates_new(second, "org.projectluma.Notes"); wait_state(two, "available");
  g_assert_true(one != two); g_assert_cmpuint(p->creates, ==, 2);
  gpointer weak = one; g_object_add_weak_pointer(one, &weak);
  /* Destroying one app closes its monitor, without disabling another app. */
  g_object_unref(one); g_object_unref(first); wait_count(&p->closes, 1); pump(30); g_assert_null(weak);
  g_assert_true(g_action_get_enabled(g_action_map_lookup_action(G_ACTION_MAP(second), ACTION)));
  g_action_activate(g_action_map_lookup_action(G_ACTION_MAP(second), ACTION), NULL); wait_count(&p->updates, 1);
  app_free(second, two); wait_count(&p->closes, 2); portal_free(p);
}
static guint menu_count(GMenuModel *menu, const char *wanted) {
  if (!menu) return 0;
  guint count = 0;
  for (int i = 0; i < g_menu_model_get_n_items(menu); i++) {
    g_autofree char *action = NULL;
    if (g_menu_model_get_item_attribute(menu, i, G_MENU_ATTRIBUTE_ACTION, "s", &action) && !g_strcmp0(action, wanted)) count++;
    g_autoptr(GMenuModel) section = g_menu_model_get_item_link(menu, i, G_MENU_LINK_SECTION);
    g_autoptr(GMenuModel) submenu = g_menu_model_get_item_link(menu, i, G_MENU_LINK_SUBMENU);
    count += menu_count(section, wanted) + menu_count(submenu, wanted);
  }
  return count;
}
static GtkMenuButton *find_menu(GtkWidget *widget) {
  if (GTK_IS_MENU_BUTTON(widget)) return GTK_MENU_BUTTON(widget);
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child; child = gtk_widget_get_next_sibling(child)) {
    GtkMenuButton *found = find_menu(child); if (found) return found;
  }
  return NULL;
}
static void frames_and_actions(void) {
  Portal *p = portal_new();
  guint notification_object = g_dbus_connection_register_object(p->bus, "/org/freedesktop/Notifications",
                                        p->node->interfaces[2], &vtable, p, NULL, NULL);
  g_assert_cmpuint(notification_object, >, 0); g_array_append_val(p->objects, notification_object);
  request_named(p, "RequestName", "org.freedesktop.Notifications");
  GtkApplication *app = app_new("org.projectluma.NativeFrames");
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GObject *u = luma_native_app_updates_new(app, "org.projectluma.Filer"); wait_state(u, "available");
  GObject *again = luma_native_app_updates_new(app, "org.projectluma.Filer"); g_assert_true(u == again); g_object_unref(again);
  g_assert_null(luma_native_app_updates_new(app, "org.projectluma.Other"));
  g_autoptr(GSimpleAction) original = g_simple_action_new("original", NULL);
  g_action_map_add_action(G_ACTION_MAP(app), G_ACTION(original));
  g_autoptr(GMenu) menu = g_menu_new(); g_menu_append(menu, "Original", "app.original");
  GtkWidget *one = adw_application_window_new(app), *two = adw_application_window_new(app);
  luma_application_window_frame_adopt(ADW_APPLICATION_WINDOW(one));
  luma_application_window_frame_set_menu_model(ADW_APPLICATION_WINDOW(one), G_MENU_MODEL(menu));
  luma_application_window_frame_adopt(ADW_APPLICATION_WINDOW(two));
  luma_application_window_frame_set_menu_model(ADW_APPLICATION_WINDOW(two), G_MENU_MODEL(menu));
  for (guint i = 0; i < 3; i++) luma_application_window_frame_set_menu_model(ADW_APPLICATION_WINDOW(one), G_MENU_MODEL(menu));
  GtkMenuButton *button = find_menu(one); g_assert_nonnull(button);
  GMenuModel *wrapped = gtk_menu_button_get_menu_model(button);
  g_assert_cmpuint(menu_count(wrapped, "app." ACTION), ==, 1);
  g_assert_cmpuint(menu_count(wrapped, "app.original"), ==, 1);
  g_menu_append(menu, "Second", "app.original");
  g_assert_cmpuint(menu_count(wrapped, "app.original"), ==, 2);
  g_assert_true(g_action_map_lookup_action(G_ACTION_MAP(app), "original") == G_ACTION(original));
  g_assert_cmpuint(p->creates, ==, 1);
  wait_count(&p->notifications, 1); commits(p, a, a, b); pump(40); g_assert_cmpuint(p->notifications, ==, 1);
  gtk_window_destroy(GTK_WINDOW(one)); g_assert_true(g_action_get_enabled(g_action_map_lookup_action(G_ACTION_MAP(app), ACTION)));
  gtk_window_destroy(GTK_WINDOW(two)); app_free(app, u); portal_free(p);
  /* Python AppWindow's existing action/controller must not be duplicated. */
  GtkApplication *python = app_new("org.projectluma.NativePython");
  g_autoptr(GSimpleAction) owned = g_simple_action_new(ACTION, NULL);
  g_action_map_add_action(G_ACTION_MAP(python), G_ACTION(owned));
  g_assert_null(luma_native_app_updates_new(python, "org.projectluma.Notes"));
  g_autoptr(GMenuModel) unchanged = luma_native_app_updates_menu(python, G_MENU_MODEL(menu));
  g_assert_true(unchanged == G_MENU_MODEL(menu)); g_assert_null(g_object_get_data(G_OBJECT(python), "luma-native-app-updates"));
  g_assert_true(g_action_map_lookup_action(G_ACTION_MAP(python), ACTION) == G_ACTION(owned));
  g_object_unref(python);
}
static void identity_bounds(void) {
  const char *good = "[Application]\nname=org.projectluma.Filer\n[Instance]\ninstance-id=123\n";
  g_autofree char *id = luma_native_app_updates_parse_identity(good, strlen(good)); g_assert_cmpstr(id, ==, "org.projectluma.Filer");
  const char *bad[] = {"[Application]\nname=../../bin/sh\n", "[Application]\nname=org.example\n", "[Application]\nname=1org.example.App\n", "[Application]\nname=org..App\n", "[Application]\nname=org.example.App\nname=org.other.App\n", "[Application]\nname=org.example.App\n[Application]\nname=org.other.App\n", "[Instance]\nname=org.example.App\n"};
  for (guint i = 0; i < G_N_ELEMENTS(bad); i++) g_assert_null(luma_native_app_updates_parse_identity(bad[i], strlen(bad[i])));
  g_assert_null(luma_native_app_updates_parse_identity(good, strlen(good) + 1));
  g_autofree char *oversized = g_malloc0(65538); memset(oversized, 'a', 65537);
  g_assert_null(luma_native_app_updates_parse_identity(oversized, 65537));
}
int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL); if (!gtk_init_check()) return 77; luma_init();
  g_test_add_func("/native-updates/identity-bounds", identity_bounds);
  g_test_add_func("/native-updates/early-progress", early_progress);
  g_test_add_func("/native-updates/late-close", late_close);
  g_test_add_func("/native-updates/wrong-handle", wrong_handle);
  g_test_add_func("/native-updates/refusal-late-error", refused_and_late_error);
  g_test_add_func("/native-updates/owner-generation", owner_generation);
  g_test_add_func("/native-updates/bounded-signals", bounded_signals);
  g_test_add_func("/native-updates/separate-apps-lifetime", separate_apps_lifetime);
  g_test_add_func("/native-updates/native-frame-python-action", frames_and_actions);
  return g_test_run();
}
