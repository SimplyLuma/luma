/* SPDX-License-Identifier: Apache-2.0
 * Native twin of appkit/app_updates.py. The installed Flatpak portal owns
 * signatures, dependencies, permissions and deployment. No URL, executable,
 * command, privileged write, automatic restart or document mutation belongs
 * here. Only explicitly adopted Luma windows use this implementation.
 */
#include "luma-native-app-updates-private.h"
#include "luma-toast.h"
#include <adwaita.h>
#include <string.h>
#include <stdio.h>

#define BUS "org.freedesktop.portal.Flatpak"
#define PATH "/org/freedesktop/portal/Flatpak"
#define MONITOR BUS ".UpdateMonitor"
#define PREFIX PATH "/update_monitor/"
#define ACTION "luma-application-update"

typedef struct {
  GObject parent;
  GWeakRef application;
  GDBusConnection *connection;
  GCancellable *cancellable;
  char *app_id, *handle, *owner, *announced, *ready_announced;
  GSimpleAction *action;
  guint generation;
  char running[65], local[65], remote[65];
  const char *state;
  guint subscription, watch;
  gboolean closed, created, have_commits;
} NativeUpdates;
typedef GObjectClass NativeUpdatesClass;
typedef struct { NativeUpdates *self; guint generation; char *owner, *handle; } Request;
static Request *request_new(NativeUpdates *self) {
  Request *r = g_new0(Request, 1); r->self = g_object_ref(self); r->generation = self->generation;
  r->owner = g_strdup(self->owner); r->handle = g_strdup(self->handle); return r;
}
static void request_free(Request *r) { g_object_unref(r->self); g_free(r->owner); g_free(r->handle); g_free(r); }
static gboolean request_current(Request *r) { return !r->self->closed && r->generation == r->self->generation && !g_strcmp0(r->owner,r->self->owner) && !g_strcmp0(r->handle,r->self->handle); }
G_DEFINE_TYPE(NativeUpdates, native_updates, G_TYPE_OBJECT)

static gboolean valid_app_id(const char *value) {
  if (value == NULL || strlen(value) > 255) return FALSE;
  guint dots = 0;
  gboolean first = TRUE;
  for (const char *p = value; *p; p++) {
    if (*p == '.') { if (first) return FALSE; dots++; first = TRUE; }
    else {
      if (first && dots==0 && !g_ascii_isalpha(*p)) return FALSE;
      if (!g_ascii_isalnum(*p) && *p != '_' && *p != '-') return FALSE;
      first = FALSE;
    }
  }
  return !first && dots >= 2;
}

/** luma_native_app_updates_parse_identity: (skip) */
char *luma_native_app_updates_parse_identity(const char *text, gsize size) {
  if (text == NULL || size > 65536 || memchr(text, '\0', size)) return NULL;
  g_autoptr(GKeyFile) info = g_key_file_new();
  if (!g_key_file_load_from_data(info, text, size, G_KEY_FILE_NONE, NULL)) return NULL;
  gsize keys_count=0;
  g_auto(GStrv) keys=g_key_file_get_keys(info,"Application",&keys_count,NULL);
  guint names=0;
  for (gsize i=0;i<keys_count;i++) if (!strcmp(keys[i],"name")) names++;
  if (names!=1) return NULL;
  char *id = g_key_file_get_string(info, "Application", "name", NULL);
  if (!valid_app_id(id)) g_clear_pointer(&id, g_free);
  return id;
}

static gboolean valid_commit(const char *value) {
  if (value == NULL || strlen(value) != 64) return FALSE;
  for (guint i = 0; i < 64; i++)
    if (!g_ascii_isdigit(value[i]) && (value[i] < 'a' || value[i] > 'f')) return FALSE;
  return TRUE;
}

static void notice(NativeUpdates *self, const char *message, gboolean notify) {
  g_autoptr(GtkApplication) app = g_weak_ref_get(&self->application);
  if (app == NULL || self->closed) return;
  GtkWindow *window = gtk_application_get_active_window(app);
  if (window != NULL && gtk_widget_get_mapped(GTK_WIDGET(window)))
    luma_toast_show(GTK_WIDGET(window), message, notify ? "notified" : "done");
  if (notify && g_application_get_is_registered(G_APPLICATION(app))) {
    g_autoptr(GNotification) notification = g_notification_new("App update available");
    g_notification_set_body(notification, message);
    g_notification_set_default_action(notification, "app." ACTION);
    g_application_send_notification(G_APPLICATION(app), "luma-app-update", notification);
  }
}

static void state(NativeUpdates *self, const char *value) {
  if (self->closed) return;
  gboolean changed = strcmp(self->state, value) != 0;
  self->state = value;
  if (self->action != NULL)
    g_simple_action_set_enabled(self->action, strcmp(value, "checking") && strcmp(value, "installing") &&
                                (self->created || !strcmp(value,"unavailable") || !strcmp(value,"review")));
  if (!strcmp(value, "available") && self->created && g_strcmp0(self->announced, self->remote)) {
    g_free(self->announced); self->announced = g_strdup(self->remote);
    notice(self, "An update is available in the app menu", TRUE);
  }
  if (!strcmp(value, "ready") && self->created && g_strcmp0(self->ready_announced, self->local)) {
    g_free(self->ready_announced); self->ready_announced = g_strdup(self->local);
    notice(self, "Update ready. Reopen this app when you are ready.", FALSE);
  }
  if (changed) g_signal_emit_by_name(self, "changed", value);
}

static void close_handle(GDBusConnection *connection, const char *owner, const char *handle) {
  /* Never close a foreign returned handle or send cleanup to a new owner. */
  if (connection != NULL && owner != NULL && handle != NULL && g_str_has_prefix(handle, PREFIX))
    g_dbus_connection_call(connection, owner, handle, MONITOR, "Close", NULL, NULL,
                           G_DBUS_CALL_FLAGS_NONE, 5000, NULL, NULL, NULL);
}

static void unsubscribe(NativeUpdates *self) {
  if (self->subscription && self->connection)
    g_dbus_connection_signal_unsubscribe(self->connection, self->subscription);
  self->subscription = 0;
}

/** luma_native_app_updates_close: (skip) */
void luma_native_app_updates_close(GObject *object) {
  NativeUpdates *self = (NativeUpdates *)object;
  if (self->closed) return;
  self->closed = TRUE;
  self->generation++;
  if (self->action) g_simple_action_set_enabled(self->action,FALSE);
  g_cancellable_cancel(self->cancellable);
  unsubscribe(self);
  if (self->watch) g_bus_unwatch_name(self->watch);
  self->watch = 0;
  if (self->created) close_handle(self->connection, self->owner, self->handle);
  self->created = FALSE;
}

static void commits_state(NativeUpdates *self) {
  if (self->have_commits) state(self, strcmp(self->remote, self->local) ? "available" :
                              strcmp(self->local, self->running) ? "ready" : "current");
}
static void created(GObject *connection, GAsyncResult *result, gpointer data) {
  Request *r = data; NativeUpdates *self = r->self;
  g_autoptr(GError) error = NULL;
  g_autoptr(GVariant) reply = g_dbus_connection_call_finish(G_DBUS_CONNECTION(connection), result, &error);
  const char *handle = NULL;
  if (reply != NULL) g_variant_get(reply, "(&o)", &handle);
  gboolean owned = handle != NULL && !strcmp(handle, r->handle);
  if (!request_current(r)) {
    if (owned) close_handle(G_DBUS_CONNECTION(connection), r->owner, r->handle);
  } else if (!owned) {
    /* The generated path belongs to this request even if an invalid peer
     * returns a different path. Never adopt or close that foreign reply. */
    close_handle(G_DBUS_CONNECTION(connection), r->owner, r->handle);
    unsubscribe(self); state(self,"unavailable");
  } else {
    self->created = TRUE;
    /* An initial signal may precede the reply. It cannot enable Update until
     * this exact generated monitor has actually been acknowledged. */
    commits_state(self);
    
  }
  request_free(r);
}

static void signal_received(GDBusConnection *connection G_GNUC_UNUSED,
                           const char *sender, const char *path,
                           const char *interface G_GNUC_UNUSED, const char *name,
                           GVariant *parameters, gpointer data) {
  NativeUpdates *self = data;
  if (self->closed || g_strcmp0(sender,self->owner) || g_strcmp0(path,self->handle) ||
      !g_variant_is_of_type(parameters, G_VARIANT_TYPE("(a{sv})")) || g_variant_get_size(parameters)>65536) return;
  g_autoptr(GVariant) info = g_variant_get_child_value(parameters, 0);
  if (!strcmp(name, "UpdateAvailable")) {
    const char *running = NULL, *local = NULL, *remote = NULL;
    if (!g_variant_lookup(info, "running-commit", "&s", &running) ||
        !g_variant_lookup(info, "local-commit", "&s", &local) ||
        !g_variant_lookup(info, "remote-commit", "&s", &remote) ||
        !valid_commit(running) || !valid_commit(local) || !valid_commit(remote)) return;
    g_strlcpy(self->running, running, 65); g_strlcpy(self->local, local, 65); g_strlcpy(self->remote, remote, 65);
    self->have_commits = TRUE;
    if (self->created && strcmp(self->state, "installing")) commits_state(self);
  } else if (!strcmp(name, "Progress") && !strcmp(self->state, "installing")) {
    guint status;
    if (!g_variant_lookup(info, "status", "u", &status)) return;
    if (status == 2) { g_strlcpy(self->local,self->remote,65); state(self, "ready"); }
    else if (status == 1) state(self, strcmp(self->local, self->running) ? "ready" : "current");
    else if (status == 3) {
      const char *error = NULL;
      g_variant_lookup(info, "error", "&s", &error);
      state(self, !g_strcmp0(error, "org.freedesktop.DBus.Error.NotSupported") ||
                  !g_strcmp0(error, "org.freedesktop.DBus.Error.AccessDenied") ? "review" : "failed");
    }
  }
}

static void start_monitor(NativeUpdates *self) {
  g_autofree char *token = g_uuid_string_random(); g_strdelimit(token,"-",'_');
  g_autofree char *sender = g_strdup(g_dbus_connection_get_unique_name(self->connection)+1);
  g_strdelimit(sender,".",'_');
  g_free(self->handle); self->handle=g_strdup_printf(PREFIX "%s/luma_%s",sender,token);
  self->subscription=g_dbus_connection_signal_subscribe(self->connection,self->owner,MONITOR,NULL,
      self->handle,NULL,G_DBUS_SIGNAL_FLAGS_NONE,signal_received,g_object_ref(self),g_object_unref);
  GVariantBuilder options; g_variant_builder_init(&options,G_VARIANT_TYPE_VARDICT);
  g_autofree char *handle_token=g_strconcat("luma_",token,NULL);
  g_variant_builder_add(&options,"{sv}","handle_token",g_variant_new_string(handle_token));
  g_dbus_connection_call(self->connection,self->owner,PATH,BUS,"CreateUpdateMonitor",
      g_variant_new("(a{sv})",&options),G_VARIANT_TYPE("(o)"),G_DBUS_CALL_FLAGS_NONE,
      10000,NULL,created,request_new(self));
}
static void appeared(GDBusConnection *connection G_GNUC_UNUSED, const char *name G_GNUC_UNUSED,
                     const char *owner, gpointer data) {
  NativeUpdates *self=data;
  if (self->closed || !g_strcmp0(owner,self->owner)) return;
  if (self->created) close_handle(self->connection,self->owner,self->handle);
  self->generation++; unsubscribe(self); self->created=FALSE; self->have_commits=FALSE;
  g_free(self->owner); self->owner=g_strdup(owner); state(self,"checking"); start_monitor(self);
}
static void vanished(GDBusConnection *connection G_GNUC_UNUSED, const char *name G_GNUC_UNUSED, gpointer data) {
  NativeUpdates *self=data;
  if (self->closed) return;
  if (self->created) close_handle(self->connection,self->owner,self->handle);
  self->generation++; self->created=FALSE; self->have_commits=FALSE;
  g_clear_pointer(&self->owner,g_free); unsubscribe(self); state(self,"unavailable");
}
static void connected(GObject *source G_GNUC_UNUSED, GAsyncResult *result, gpointer data) {
  NativeUpdates *self=data; g_autoptr(GError) error=NULL;
  GDBusConnection *connection=g_bus_get_finish(result,&error);
  if (self->closed) g_clear_object(&connection);
  else if (connection==NULL) state(self,"unavailable");
  else {
    self->connection=connection;
    self->watch=g_bus_watch_name_on_connection(connection,BUS,G_BUS_NAME_WATCHER_FLAGS_AUTO_START,
                                              appeared,vanished,g_object_ref(self),g_object_unref);
  }
  g_object_unref(self);
}

static void requested(GObject *connection, GAsyncResult *result, gpointer data) {
  Request *r=data; NativeUpdates *self=r->self;
  g_autoptr(GError) error = NULL;
  g_autoptr(GVariant) reply = g_dbus_connection_call_finish(G_DBUS_CONNECTION(connection), result, &error);
  if (reply == NULL && request_current(r) && !strcmp(self->state, "installing")) {
    g_autofree char *remote = g_dbus_error_get_remote_error(error);
    state(self, !g_strcmp0(remote, "org.freedesktop.DBus.Error.AccessDenied") ||
                !g_strcmp0(remote, "org.freedesktop.DBus.Error.NotSupported") ? "review" : "failed");
  }
  /* Request acceptance is not successful deployment: Progress owns that. */
  request_free(r);
}

typedef struct { NativeUpdates *self; GWeakRef window; } ManagerRequest;
static void manager_opened(GObject *launcher, GAsyncResult *result, gpointer data) {
  ManagerRequest *r=data;
  g_autoptr(GtkWindow) window=g_weak_ref_get(&r->window);
  g_autoptr(GError) error=NULL;
  gboolean success=gtk_uri_launcher_launch_finish(GTK_URI_LAUNCHER(launcher),result,&error);
  if (!success && !r->self->closed && window!=NULL && gtk_widget_get_mapped(GTK_WIDGET(window))) {
    AdwDialog *dialog=adw_alert_dialog_new("Open your software manager",
                                         "Find this app in your software manager and choose Update.");
    gtk_widget_add_css_class(GTK_WIDGET(dialog),"luma-controls-quiet");
    adw_alert_dialog_add_response(ADW_ALERT_DIALOG(dialog),"close","Close");
    adw_dialog_present(dialog,GTK_WIDGET(window));
  }
  g_weak_ref_clear(&r->window); g_object_unref(r->self); g_free(r);
}

static void activate(GSimpleAction *action G_GNUC_UNUSED, GVariant *parameter G_GNUC_UNUSED, gpointer data) {
  NativeUpdates *self = data;
  if (self->closed) return;
  if (!strcmp(self->state, "review") || !strcmp(self->state, "unavailable")) {
    g_autoptr(GtkApplication) app = g_weak_ref_get(&self->application);
    if (app == NULL) return;
    GtkWindow *window = gtk_application_get_active_window(app);
    g_autofree char *uri = g_strconcat("appstream://", self->app_id, NULL);
    g_autoptr(GtkUriLauncher) launcher = gtk_uri_launcher_new(uri);
    ManagerRequest *r=g_new0(ManagerRequest,1); r->self=g_object_ref(self); g_weak_ref_init(&r->window,window);
    gtk_uri_launcher_launch(launcher,window,self->cancellable,manager_opened,r);
  } else if (!self->closed && self->created &&
             (!strcmp(self->state, "available") || !strcmp(self->state, "failed"))) {
    state(self, "installing");
    GVariantBuilder options; g_variant_builder_init(&options, G_VARIANT_TYPE_VARDICT);
    g_dbus_connection_call(self->connection, self->owner, self->handle, MONITOR, "Update",
        g_variant_new("(sa{sv})", "", &options), G_VARIANT_TYPE_UNIT, G_DBUS_CALL_FLAGS_NONE,
        5000, self->cancellable, requested, request_new(self));
  } else if (!strcmp(self->state, "ready"))
    notice(self, "Save your work, then reopen this app to use the update.", FALSE);
  else if (!strcmp(self->state, "current")) notice(self, "This app is up to date.", FALSE);
}

static void shutdown(GApplication *app G_GNUC_UNUSED, gpointer data) { luma_native_app_updates_close(data); }
static void native_updates_finalize(GObject *object) {
  NativeUpdates *self = (NativeUpdates *)object;
  g_weak_ref_clear(&self->application);
  g_clear_object(&self->connection); g_clear_object(&self->cancellable);
  g_free(self->app_id); g_free(self->handle); g_free(self->owner); g_free(self->announced);
  g_free(self->ready_announced); g_clear_object(&self->action);
  G_OBJECT_CLASS(native_updates_parent_class)->finalize(object);
}
static void native_updates_class_init(NativeUpdatesClass *klass) {
  klass->finalize = native_updates_finalize;
  g_signal_new("changed",G_TYPE_FROM_CLASS(klass),G_SIGNAL_RUN_LAST,0,NULL,NULL,NULL,G_TYPE_NONE,1,G_TYPE_STRING);
}
static void native_updates_init(NativeUpdates *self) {
  g_weak_ref_init(&self->application, NULL);
  self->cancellable = g_cancellable_new(); self->state = "checking";
}

static void release(gpointer data) { luma_native_app_updates_close(data); g_object_unref(data); }

/** luma_native_app_updates_new: (skip) */
GObject *luma_native_app_updates_new(GtkApplication *application, const char *app_id) {
  g_return_val_if_fail(GTK_IS_APPLICATION(application) && valid_app_id(app_id), NULL);
  NativeUpdates *existing=g_object_get_data(G_OBJECT(application),"luma-native-app-updates");
  if (existing!=NULL) return !strcmp(existing->app_id,app_id) ? G_OBJECT(g_object_ref(existing)):NULL;
  if (g_action_map_lookup_action(G_ACTION_MAP(application),ACTION)!=NULL) return NULL;
  NativeUpdates *self = g_object_new(native_updates_get_type(), NULL);
  g_weak_ref_set(&self->application, application); self->app_id = g_strdup(app_id);
  g_autoptr(GSimpleAction) action = g_simple_action_new(ACTION, NULL);
  self->action=g_object_ref(action);
  g_simple_action_set_enabled(action, FALSE);
  g_signal_connect_object(action, "activate", G_CALLBACK(activate), self, 0);
  g_action_map_add_action(G_ACTION_MAP(application), G_ACTION(action));
  g_signal_connect_object(application, "shutdown", G_CALLBACK(shutdown), self, 0);
  g_object_set_data_full(G_OBJECT(application),"luma-native-app-updates",g_object_ref(self),release);
  g_bus_get(G_BUS_TYPE_SESSION, self->cancellable, connected, g_object_ref(self));
  return G_OBJECT(self);
}
/** luma_native_app_updates_state: (skip) */
const char *luma_native_app_updates_state(GObject *updates) { return ((NativeUpdates *)updates)->state; }
static gboolean contains_action(GMenuModel *model,guint depth) {
  if (model==NULL || depth>16) return FALSE;
  for (int i=0;i<g_menu_model_get_n_items(model);i++) {
    g_autofree char *action=NULL;
    if (g_menu_model_get_item_attribute(model,i,G_MENU_ATTRIBUTE_ACTION,"s",&action) && !g_strcmp0(action,"app." ACTION)) return TRUE;
    g_autoptr(GMenuModel) section=g_menu_model_get_item_link(model,i,G_MENU_LINK_SECTION);
    g_autoptr(GMenuModel) submenu=g_menu_model_get_item_link(model,i,G_MENU_LINK_SUBMENU);
    if (contains_action(section,depth+1) || contains_action(submenu,depth+1)) return TRUE;
  }
  return FALSE;
}
/** luma_native_app_updates_menu: (skip) */
GMenuModel *luma_native_app_updates_menu(GtkApplication *application, GMenuModel *menu) {
  if (application == NULL) return menu != NULL ? g_object_ref(menu) : NULL;
  GObject *updates = g_object_get_data(G_OBJECT(application), "luma-native-app-updates");
  if (updates==NULL && g_action_map_lookup_action(G_ACTION_MAP(application),ACTION)!=NULL)
    return menu!=NULL ? g_object_ref(menu):NULL;
  if (updates == NULL) {
    /* Identity comes only from this process's real sandbox file. Native
     * application IDs can differ from a portable Flatpak ID (e.g. Filer). */
    FILE *file = fopen("/.flatpak-info", "re");
    if (file == NULL) return menu != NULL ? g_object_ref(menu) : NULL;
    char bytes[65537]; gsize size = fread(bytes, 1, sizeof bytes, file);
    gboolean valid_read = !ferror(file); fclose(file);
    g_autofree char *id = valid_read ? luma_native_app_updates_parse_identity(bytes, size) : NULL;
    if (id == NULL) return menu != NULL ? g_object_ref(menu) : NULL;
    updates = luma_native_app_updates_new(application, id);
    if (updates==NULL) return menu!=NULL ? g_object_ref(menu):NULL;
    g_object_unref(updates);
    updates=g_object_get_data(G_OBJECT(application),"luma-native-app-updates");
  }
  if (contains_action(menu,0)) return g_object_ref(menu);
  GMenu *combined = g_menu_new();
  if (menu != NULL) g_menu_append_section(combined, NULL, menu);
  g_autoptr(GMenu) section = g_menu_new();
  g_menu_append(section, "App updates", "app." ACTION);
  g_menu_append_section(combined, NULL, G_MENU_MODEL(section));
  return G_MENU_MODEL(combined);
}
