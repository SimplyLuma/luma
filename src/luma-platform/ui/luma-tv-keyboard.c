/* SPDX-License-Identifier: Apache-2.0 */
/* The system's TV keyboard, from C (docs/developer-platform/tv-mode.md "Typing"): the action key's
 * label for a field, and the keyboard's height for whatever must stay above it. The contract is the
 * session service org.projectluma.Shell.Keyboard (SetActionLabel, Visible, Height in TV units),
 * as luma_appkit.tv_keyboard uses it. Outside TV posture the shell ignores the label. */
#include "luma-ui-kit.h"
#include "luma-tv-frame-private.h"

#define KB_NAME "org.projectluma.Shell.Keyboard"
#define KB_PATH "/org/projectluma/Shell/Keyboard"
#define KB_IFACE "org.projectluma.Shell.Keyboard1"

static void label_now(const char *label) {
  GDBusConnection *bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, NULL);
  if (bus == NULL)
    return;
  g_dbus_connection_call(bus, KB_NAME, KB_PATH, KB_IFACE, "SetActionLabel", g_variant_new("(s)", label ? label : ""),
                         NULL, G_DBUS_CALL_FLAGS_NO_AUTO_START, 1000, NULL, NULL, NULL);
  g_object_unref(bus);
}

static void entered(GtkEventControllerFocus *focus, gpointer data) {
  (void)data;
  GtkWidget *widget = gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(focus));
  label_now(g_object_get_data(G_OBJECT(widget), "luma-keyboard-action"));
}

static void left(GtkEventControllerFocus *focus, gpointer data) {
  (void)focus; (void)data;
  label_now("");
}

void luma_ui_set_keyboard_action(GtkWidget *widget, const char *label) {
  g_return_if_fail(GTK_IS_WIDGET(widget));
  gboolean first = g_object_get_data(G_OBJECT(widget), "luma-keyboard-action") == NULL;
  g_object_set_data_full(G_OBJECT(widget), "luma-keyboard-action", g_strdup(label ? label : ""), g_free);
  if (first) {
    GtkEventController *focus = gtk_event_controller_focus_new();
    g_signal_connect(focus, "enter", G_CALLBACK(entered), NULL);
    g_signal_connect(focus, "leave", G_CALLBACK(left), NULL);
    gtk_widget_add_controller(widget, focus);
  }
  if (gtk_widget_has_focus(widget) || gtk_widget_get_focus_child(widget) != NULL)
    label_now(label);
}

/* The keyboard's height (TV units, 0 while hidden), followed for the TV frame. */
typedef struct {
  GDBusProxy *proxy;
  LumaTvKeyboardFunc func;
  gpointer data;
} Watch;

static void report(Watch *w) {
  g_autoptr(GVariant) visible = w->proxy ? g_dbus_proxy_get_cached_property(w->proxy, "Visible") : NULL;
  g_autoptr(GVariant) height = w->proxy ? g_dbus_proxy_get_cached_property(w->proxy, "Height") : NULL;
  gboolean shown = visible && g_variant_is_of_type(visible, G_VARIANT_TYPE_BOOLEAN) && g_variant_get_boolean(visible);
  guint units = height && g_variant_is_of_type(height, G_VARIANT_TYPE_UINT32) ? g_variant_get_uint32(height) : 0;
  w->func(shown ? units : 0, w->data);
}

static void changed(GDBusProxy *proxy, GVariant *props, GStrv invalidated, Watch *w) {
  (void)proxy; (void)props; (void)invalidated;
  report(w);
}

static void ready(GObject *source, GAsyncResult *result, gpointer data) {
  (void)source;
  Watch *w = data;
  w->proxy = g_dbus_proxy_new_for_bus_finish(result, NULL);
  if (w->func == NULL) { /* unwatched while it was coming */
    g_clear_object(&w->proxy);
    g_free(w);
    return;
  }
  if (w->proxy == NULL)
    return;
  g_signal_connect(w->proxy, "g-properties-changed", G_CALLBACK(changed), w);
  report(w);
}

gpointer luma_tv_keyboard_watch(LumaTvKeyboardFunc func, gpointer data) {
  Watch *w = g_new0(Watch, 1);
  w->func = func;
  w->data = data;
  g_dbus_proxy_new_for_bus(G_BUS_TYPE_SESSION, G_DBUS_PROXY_FLAGS_DO_NOT_AUTO_START, NULL, KB_NAME, KB_PATH, KB_IFACE,
                           NULL, ready, w);
  return w;
}

void luma_tv_keyboard_unwatch(gpointer watch) {
  Watch *w = watch;
  if (w == NULL)
    return;
  if (w->proxy != NULL) {
    g_signal_handlers_disconnect_by_data(w->proxy, w);
    g_object_unref(w->proxy);
    g_free(w);
  } else if (w->func == NULL) {
    g_free(w);
  } else {
    w->func = NULL; /* the proxy is still coming: forget it there */
  }
}
