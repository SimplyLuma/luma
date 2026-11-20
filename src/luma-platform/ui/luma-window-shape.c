/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-window-shape.h"

/* Binds luma_window_shape_v1 (src/luma-platform/protocols), which Luma's
 * Mutter implements. Off Wayland, or on a compositor without the protocol,
 * every call is a no-op and luma_window_set_aspect_ratio() says so. */
#ifdef LUMA_HAVE_WAYLAND
#include <gdk/wayland/gdkwayland.h>
#include <wayland-client.h>

#include "luma-window-shape-v1-client-protocol.h"
#else
struct wl_registry;
struct luma_window_shape_manager_v1;
struct luma_window_shape_v1;
#endif

typedef struct {
  struct wl_registry *registry;
  struct luma_window_shape_manager_v1 *manager;
  guint32 manager_name;
} ShapeDisplay;

typedef struct {
  GtkWindow *window; /* owns this binding */
  GtkWidget *content; /* weak */
  GdkSurface *surface;
  ShapeDisplay *display;
  struct luma_window_shape_v1 *shape;
  int width;
  int height;
  /* What the compositor was last told; -1 when nothing was sent. */
  int sent_width;
  int sent_height;
  int sent_base_width;
  int sent_base_height;
  gulong realize_handler;
  gulong unrealize_handler;
  gulong layout_handler;
} WindowShape;

static GQuark display_quark;
static GQuark window_quark;

#ifdef LUMA_HAVE_WAYLAND
static void
registry_global(void *data, struct wl_registry *registry, guint32 name,
                const char *interface, guint32 version G_GNUC_UNUSED) {
  ShapeDisplay *self = data;

  if (self->manager == NULL &&
      g_str_equal(interface, luma_window_shape_manager_v1_interface.name)) {
    self->manager_name = name;
    self->manager = wl_registry_bind(
        registry, name, &luma_window_shape_manager_v1_interface, 1);
  }
}

static void
registry_global_remove(void *data, struct wl_registry *registry G_GNUC_UNUSED,
                       guint32 name) {
  ShapeDisplay *self = data;

  if (name != self->manager_name)
    return;
  /* Shape objects already made stay valid until their windows unrealize. */
  g_clear_pointer(&self->manager, luma_window_shape_manager_v1_destroy);
  self->manager_name = 0;
}

static const struct wl_registry_listener registry_listener = {
  registry_global,
  registry_global_remove,
};

static void
shape_display_free(gpointer data) {
  ShapeDisplay *self = data;

  g_clear_pointer(&self->manager, luma_window_shape_manager_v1_destroy);
  g_clear_pointer(&self->registry, wl_registry_destroy);
  g_free(self);
}
#endif

static ShapeDisplay *
shape_display_for(GdkDisplay *display) {
#ifndef LUMA_HAVE_WAYLAND
  (void)display;
  (void)display_quark;
  return NULL;
#else
  ShapeDisplay *self;
  struct wl_display *wl_display;

  if (display == NULL || !GDK_IS_WAYLAND_DISPLAY(display))
    return NULL;

  if (display_quark == 0)
    display_quark = g_quark_from_static_string("luma-window-shape-display");
  self = g_object_get_qdata(G_OBJECT(display), display_quark);
  if (self != NULL)
    return self;

  self = g_new0(ShapeDisplay, 1);
  wl_display = gdk_wayland_display_get_wl_display(display);
  self->registry = wl_display_get_registry(wl_display);
  wl_registry_add_listener(self->registry, &registry_listener, self);
  /* One discovery per display, on GDK's own connection; GDK keeps
   * dispatching it. */
  if (wl_display_roundtrip(wl_display) < 0) {
    shape_display_free(self);
    return NULL;
  }
  g_object_set_qdata_full(G_OBJECT(display), display_quark, self,
                          shape_display_free);
  return self;
#endif
}

static void
measure_base(WindowShape *self, int *base_width, int *base_height) {
  GtkWidget *window = GTK_WIDGET(self->window);

  *base_width = 0;
  *base_height = 0;
  if (self->content == NULL || !gtk_widget_get_visible(self->content))
    return;
  /* The window widget is the window geometry GTK gives the compositor (its
   * client-side shadow lies outside it). */
  *base_width = MAX(gtk_widget_get_width(window) -
                        gtk_widget_get_width(self->content),
                    0);
  *base_height = MAX(gtk_widget_get_height(window) -
                         gtk_widget_get_height(self->content),
                     0);
}

static void
window_shape_sync(WindowShape *self) {
#ifdef LUMA_HAVE_WAYLAND
  int base_width;
  int base_height;

  if (self->surface == NULL || self->display == NULL)
    return;

  if (self->shape == NULL) {
    struct wl_surface *wl_surface;

    if (self->width == 0 || self->display->manager == NULL)
      return;
    wl_surface = gdk_wayland_surface_get_wl_surface(self->surface);
    if (wl_surface == NULL)
      return;
    self->shape = luma_window_shape_manager_v1_get_window_shape(
        self->display->manager, wl_surface);
    self->sent_width = self->sent_height = -1;
  }

  if (self->width == 0) {
    if (self->sent_width == 0)
      return;
    luma_window_shape_v1_unset_aspect_ratio(self->shape);
    self->sent_width = self->sent_height = 0;
    self->sent_base_width = self->sent_base_height = 0;
  } else {
    measure_base(self, &base_width, &base_height);
    /* A content widget that is not laid out yet has no size; wait for the
     * layout that gives it one rather than send the whole window as base. */
    if (self->content != NULL && gtk_widget_get_visible(self->content) &&
        (gtk_widget_get_width(self->content) <= 0 ||
         gtk_widget_get_height(self->content) <= 0))
      return;
    if (self->sent_width == self->width && self->sent_height == self->height &&
        self->sent_base_width == base_width &&
        self->sent_base_height == base_height)
      return;
    luma_window_shape_v1_set_aspect_ratio(self->shape, self->width,
                                          self->height, base_width,
                                          base_height);
    self->sent_width = self->width;
    self->sent_height = self->height;
    self->sent_base_width = base_width;
    self->sent_base_height = base_height;
  }

  /* The request is double-buffered: make sure a commit carries it. */
  gdk_wayland_surface_force_next_commit(self->surface);
  gdk_surface_queue_render(self->surface);
#else
  (void)self;
  (void)measure_base;
#endif
}

static void
destroy_shape(WindowShape *self) {
#ifdef LUMA_HAVE_WAYLAND
  g_clear_pointer(&self->shape, luma_window_shape_v1_destroy);
#endif
  self->sent_width = self->sent_height = -1;
}

static void
surface_layout(GdkSurface *surface G_GNUC_UNUSED, int width G_GNUC_UNUSED,
               int height G_GNUC_UNUSED, WindowShape *self) {
  /* Connected after GtkWindow's own handler, so the window and its content
   * are allocated for this size. */
  window_shape_sync(self);
}

static void
window_realized(GtkWidget *widget G_GNUC_UNUSED, WindowShape *self) {
  self->surface = gtk_native_get_surface(GTK_NATIVE(self->window));
  if (self->surface == NULL)
    return;
  self->display = shape_display_for(gdk_surface_get_display(self->surface));
  self->layout_handler = g_signal_connect_after(
      self->surface, "layout", G_CALLBACK(surface_layout), self);
  window_shape_sync(self);
}

static void
window_unrealized(GtkWidget *widget G_GNUC_UNUSED, WindowShape *self) {
  destroy_shape(self);
  if (self->surface != NULL && self->layout_handler != 0)
    g_signal_handler_disconnect(self->surface, self->layout_handler);
  self->layout_handler = 0;
  self->surface = NULL;
}

static void
window_shape_free(gpointer data) {
  WindowShape *self = data;

  /* The window is being finalized: its surface is gone already. */
  destroy_shape(self);
  if (self->content != NULL)
    g_object_remove_weak_pointer(G_OBJECT(self->content),
                                 (gpointer *)&self->content);
  g_free(self);
}

static WindowShape *
window_shape_for(GtkWindow *window) {
  WindowShape *self;

  if (window_quark == 0)
    window_quark = g_quark_from_static_string("luma-window-shape");
  self = g_object_get_qdata(G_OBJECT(window), window_quark);
  if (self != NULL)
    return self;

  self = g_new0(WindowShape, 1);
  self->window = window;
  self->sent_width = self->sent_height = -1;
  g_object_set_qdata_full(G_OBJECT(window), window_quark, self,
                          window_shape_free);
  self->realize_handler = g_signal_connect(
      window, "realize", G_CALLBACK(window_realized), self);
  self->unrealize_handler = g_signal_connect(
      window, "unrealize", G_CALLBACK(window_unrealized), self);
  if (gtk_widget_get_realized(GTK_WIDGET(window)))
    window_realized(GTK_WIDGET(window), self);
  return self;
}

gboolean
luma_window_get_aspect_ratio_enforced(GtkWindow *window) {
  ShapeDisplay *display;

  g_return_val_if_fail(GTK_IS_WINDOW(window), FALSE);
  display = shape_display_for(gtk_widget_get_display(GTK_WIDGET(window)));
  return display != NULL && display->manager != NULL;
}

gboolean
luma_window_set_aspect_ratio(GtkWindow *window, int width, int height) {
  WindowShape *self;

  g_return_val_if_fail(GTK_IS_WINDOW(window), FALSE);
  g_return_val_if_fail(width >= 0 && height >= 0, FALSE);
  g_return_val_if_fail((width == 0) == (height == 0), FALSE);
  g_return_val_if_fail(width <= 65535 && height <= 65535, FALSE);

  self = window_shape_for(window);
  self->width = width;
  self->height = height;
  window_shape_sync(self);
  return width > 0 && luma_window_get_aspect_ratio_enforced(window);
}

void
luma_window_set_aspect_content(GtkWindow *window, GtkWidget *content) {
  WindowShape *self;

  g_return_if_fail(GTK_IS_WINDOW(window));
  g_return_if_fail(content == NULL || GTK_IS_WIDGET(content));

  self = window_shape_for(window);
  if (self->content == content)
    return;
  if (self->content != NULL)
    g_object_remove_weak_pointer(G_OBJECT(self->content),
                                 (gpointer *)&self->content);
  self->content = content;
  if (content != NULL)
    g_object_add_weak_pointer(G_OBJECT(content), (gpointer *)&self->content);
  window_shape_sync(self);
}
