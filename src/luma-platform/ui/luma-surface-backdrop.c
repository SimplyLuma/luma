/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-surface-backdrop.h"

#include "luma-appearance.h"

#include <gdk/wayland/gdkwayland.h>
#include <wayland-client.h>

#include "ext-background-effect-v1-client-protocol.h"

typedef struct _LumaBackdropDisplay LumaBackdropDisplay;

struct _LumaBackdropDisplay {
  GObject parent_instance;
  struct wl_registry *registry;
  struct wl_compositor *compositor;
  struct ext_background_effect_manager_v1 *manager;
  guint32 manager_name;
  gboolean blur_available;
  GPtrArray *bindings; /* weak LumaSurfaceBackdrop pointers */
};

#define LUMA_TYPE_BACKDROP_DISPLAY (luma_backdrop_display_get_type())
G_DECLARE_FINAL_TYPE(LumaBackdropDisplay, luma_backdrop_display, LUMA,
                     BACKDROP_DISPLAY, GObject)
G_DEFINE_FINAL_TYPE(LumaBackdropDisplay, luma_backdrop_display, G_TYPE_OBJECT)

struct _LumaSurfaceBackdrop {
  GObject parent_instance;
  GtkWindow *window; /* weak */
  GdkSurface *surface; /* owned by the realized window */
  LumaSurfacePolicy *policy;
  LumaBackdropDisplay *display_state;
  struct ext_background_effect_surface_v1 *effect;
  gulong realize_handler;
  gulong unrealize_handler;
  gulong surface_width_handler;
  gulong surface_height_handler;
  guint geometry_tick_id;
  gulong policy_handler;
  gboolean active;
};

G_DEFINE_FINAL_TYPE(LumaSurfaceBackdrop, luma_surface_backdrop, G_TYPE_OBJECT)

enum {
  PROP_0,
  PROP_AVAILABLE,
  PROP_ACTIVE,
  N_PROPERTIES,
};

static GParamSpec *properties[N_PROPERTIES];
static GQuark display_state_quark;
static GQuark window_binding_quark;
void luma_init(void);

static void luma_surface_backdrop_sync(LumaSurfaceBackdrop *self);
void luma_ui_set_surface_capabilities(gboolean available);

static gboolean
treatment_is_translucent(const char *treatment) {
  return g_str_equal(treatment, "frost") || g_str_equal(treatment, "glass");
}

static void
set_blur_available(LumaBackdropDisplay *self, gboolean available) {
  available = !!available;
  if (self->blur_available == available)
    return;

  self->blur_available = available;
  luma_ui_set_surface_capabilities(available);
  for (guint i = 0; i < self->bindings->len; i++) {
    LumaSurfaceBackdrop *binding = g_ptr_array_index(self->bindings, i);
    luma_surface_backdrop_sync(binding);
    g_object_notify_by_pspec(G_OBJECT(binding), properties[PROP_AVAILABLE]);
  }
}

static void
manager_capabilities(void *data,
                     struct ext_background_effect_manager_v1 *manager G_GNUC_UNUSED,
                     guint32 flags) {
  LumaBackdropDisplay *self = data;
  set_blur_available(
      self,
      (flags & EXT_BACKGROUND_EFFECT_MANAGER_V1_CAPABILITY_BLUR) != 0);
}

static const struct ext_background_effect_manager_v1_listener manager_listener = {
  manager_capabilities,
};

static void
registry_global(void *data, struct wl_registry *registry, guint32 name,
                const char *interface, guint32 version) {
  LumaBackdropDisplay *self = data;

  if (g_str_equal(interface, wl_compositor_interface.name) &&
      self->compositor == NULL) {
    self->compositor = wl_registry_bind(
        registry, name, &wl_compositor_interface, MIN(version, 4));
  } else if (g_str_equal(
                 interface, ext_background_effect_manager_v1_interface.name) &&
             self->manager == NULL) {
    self->manager_name = name;
    self->manager = wl_registry_bind(
        registry, name, &ext_background_effect_manager_v1_interface, 1);
    ext_background_effect_manager_v1_add_listener(
        self->manager, &manager_listener, self);
  }
}

static void
registry_global_remove(void *data, struct wl_registry *registry G_GNUC_UNUSED,
                       guint32 name) {
  LumaBackdropDisplay *self = data;
  if (name != self->manager_name)
    return;

  set_blur_available(self, FALSE);
  g_clear_pointer(&self->manager,
                  ext_background_effect_manager_v1_destroy);
  self->manager_name = 0;
}

static const struct wl_registry_listener registry_listener = {
  registry_global,
  registry_global_remove,
};

static void
luma_backdrop_display_dispose(GObject *object) {
  LumaBackdropDisplay *self = LUMA_BACKDROP_DISPLAY(object);
  set_blur_available(self, FALSE);
  g_clear_pointer(&self->manager,
                  ext_background_effect_manager_v1_destroy);
  g_clear_pointer(&self->compositor, wl_compositor_destroy);
  g_clear_pointer(&self->registry, wl_registry_destroy);
  g_clear_pointer(&self->bindings, g_ptr_array_unref);
  G_OBJECT_CLASS(luma_backdrop_display_parent_class)->dispose(object);
}

static void
luma_backdrop_display_class_init(LumaBackdropDisplayClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_backdrop_display_dispose;
}

static void
luma_backdrop_display_init(LumaBackdropDisplay *self) {
  self->bindings = g_ptr_array_new();
}

static LumaBackdropDisplay *
display_state_for(GdkDisplay *display) {
  LumaBackdropDisplay *state;
  struct wl_display *wl_display;

  if (!GDK_IS_WAYLAND_DISPLAY(display))
    return NULL;

  if (display_state_quark == 0)
    display_state_quark = g_quark_from_static_string(
        "luma-surface-backdrop-display-state");

  state = g_object_get_qdata(G_OBJECT(display), display_state_quark);
  if (state != NULL)
    return g_object_ref(state);

  state = g_object_new(LUMA_TYPE_BACKDROP_DISPLAY, NULL);
  wl_display = gdk_wayland_display_get_wl_display(display);
  state->registry = wl_display_get_registry(wl_display);
  wl_registry_add_listener(state->registry, &registry_listener, state);

  /* Discovery happens once per display. There is no helper, polling loop or
   * second Wayland connection; GDK continues to own and dispatch the socket. */
  if (wl_display_roundtrip(wl_display) < 0 ||
      wl_display_roundtrip(wl_display) < 0) {
    g_object_unref(state);
    return NULL;
  }

  g_object_set_qdata_full(G_OBJECT(display), display_state_quark,
                          g_object_ref(state), g_object_unref);
  return state;
}

static void
remove_effect(LumaSurfaceBackdrop *self) {
  if (self->effect == NULL)
    return;

  ext_background_effect_surface_v1_set_blur_region(self->effect, NULL);
  ext_background_effect_surface_v1_destroy(self->effect);
  self->effect = NULL;
  if (self->surface != NULL && GDK_IS_WAYLAND_SURFACE(self->surface)) {
    gdk_wayland_surface_force_next_commit(self->surface);
    gdk_surface_queue_render(self->surface);
  }
}

static void
set_active(LumaSurfaceBackdrop *self, gboolean active) {
  active = !!active;
  if (self->active == active)
    return;
  self->active = active;
  g_object_notify_by_pspec(G_OBJECT(self), properties[PROP_ACTIVE]);
}

static int
rounded_row_inset(int radius, int row) {
  int doubled_radius = radius * 2;
  int doubled_y = doubled_radius - (row * 2 + 1);

  for (int inset = 0; inset < radius; inset++) {
    int doubled_x = doubled_radius - (inset * 2 + 1);
    if (doubled_x * doubled_x + doubled_y * doubled_y <=
        doubled_radius * doubled_radius)
      return inset;
  }
  return radius;
}

static void
add_rounded_region(struct wl_region *region, int x, int y,
                   int width, int height, int radius) {
  radius = MIN(radius, MIN(width, height) / 2);
  if (radius <= 0) {
    wl_region_add(region, x, y, width, height);
    return;
  }

  if (height > radius * 2)
    wl_region_add(region, x, y + radius, width, height - radius * 2);
  for (int row = 0; row < radius; row++) {
    int inset = rounded_row_inset(radius, row);
    int row_width = width - inset * 2;
    if (row_width <= 0)
      continue;
    wl_region_add(region, x + inset, y + row, row_width, 1);
    wl_region_add(region, x + inset, y + height - row - 1, row_width, 1);
  }
}

static void
update_effect_region(LumaSurfaceBackdrop *self) {
  struct wl_region *region;
  double surface_x = 0;
  double surface_y = 0;
  int x;
  int y;
  int width;
  int height;

  if (self->effect == NULL || self->surface == NULL || self->window == NULL ||
      self->display_state == NULL || self->display_state->compositor == NULL)
    return;

  gtk_native_get_surface_transform(GTK_NATIVE(self->window),
                                   &surface_x, &surface_y);
  x = MAX(0, (int)(surface_x + 0.5));
  y = MAX(0, (int)(surface_y + 0.5));
  width = MIN(gtk_widget_get_width(GTK_WIDGET(self->window)),
              gdk_surface_get_width(self->surface) - x);
  height = MIN(gtk_widget_get_height(GTK_WIDGET(self->window)),
               gdk_surface_get_height(self->surface) - y);
  if (width <= 0 || height <= 0)
    return;

  region = wl_compositor_create_region(self->display_state->compositor);
  add_rounded_region(region, x, y, width, height, 15);
  ext_background_effect_surface_v1_set_blur_region(self->effect, region);
  wl_region_destroy(region);
  gdk_wayland_surface_force_next_commit(self->surface);
  gdk_surface_queue_render(self->surface);
}

static gboolean
update_effect_region_after_layout(GtkWidget *widget G_GNUC_UNUSED,
                                  GdkFrameClock *frame_clock G_GNUC_UNUSED,
                                  gpointer data) {
  LumaSurfaceBackdrop *self = data;
  self->geometry_tick_id = 0;
  update_effect_region(self);
  return G_SOURCE_REMOVE;
}

static void
schedule_effect_region_update(LumaSurfaceBackdrop *self) {
  if (self->window == NULL || self->geometry_tick_id != 0)
    return;
  self->geometry_tick_id = gtk_widget_add_tick_callback(
      GTK_WIDGET(self->window), update_effect_region_after_layout, self, NULL);
}

static void
surface_geometry_changed(GObject *surface G_GNUC_UNUSED,
                         GParamSpec *pspec G_GNUC_UNUSED,
                         LumaSurfaceBackdrop *self) {
  update_effect_region(self);
  /* GdkSurface dimensions can notify before GTK has recomputed the client
   * shadow transform. Re-sample once on the next frame; this is a coalesced
   * layout completion callback, not a resident timer or polling loop. */
  schedule_effect_region_update(self);
}

static void
update_window_classes(LumaSurfaceBackdrop *self, gboolean translucent) {
  GtkWidget *widget;

  if (self->window == NULL)
    return;
  widget = GTK_WIDGET(self->window);
  /* What colour a window is painted is one decision for the whole display,
   * and libadwaita's style manager makes it for every toplevel (libadwaita
   * Patch0042). This used to set the treatment class here as well, from a
   * policy gated on whether *this* surface had negotiated its own blur
   * region -- so one window could be in Glass and the window beside it, whose
   * handshake had not landed or which was never a kit window at all, in
   * opaque Light. That is the mismatch the owner was looking at.
   *
   * What is genuinely per-window is the blur region itself, and that is all
   * this says now. A surface without one shows the same veil unblurred,
   * which the veil is thick enough to carry. */
  if (translucent)
    gtk_widget_add_css_class(widget, "luma-translucent");
  else
    gtk_widget_remove_css_class(widget, "luma-translucent");
}

static void
luma_surface_backdrop_sync(LumaSurfaceBackdrop *self) {
  gboolean available = self->display_state != NULL &&
                       self->display_state->blur_available &&
                       self->display_state->manager != NULL &&
                       self->display_state->compositor != NULL;
  const char *effective;
  gboolean wants_blur;

  luma_surface_policy_set_capabilities(self->policy, available, available,
                                       available);
  effective = luma_surface_policy_get_effective(self->policy);
  wants_blur = available && self->surface != NULL &&
               treatment_is_translucent(effective);
  update_window_classes(self, wants_blur);

  if (!wants_blur) {
    remove_effect(self);
    set_active(self, FALSE);
    return;
  }

  if (self->effect == NULL) {
    struct wl_surface *wl_surface =
        gdk_wayland_surface_get_wl_surface(self->surface);
    if (wl_surface == NULL)
      return;

    self->effect =
        ext_background_effect_manager_v1_get_background_effect(
            self->display_state->manager, wl_surface);
    /* GTK's wl_surface includes large transparent client-shadow margins. The
     * background effect belongs only to the native window allocation, or the
     * transparent margins become a bright rectangular blur halo. */
    update_effect_region(self);
  }
  schedule_effect_region_update(self);
  set_active(self, self->effect != NULL);
}

static void
surface_realized(GtkWidget *widget, LumaSurfaceBackdrop *self) {
  GtkNative *native = gtk_widget_get_native(widget);
  GdkDisplay *display;
  gboolean was_available = luma_surface_backdrop_get_available(self);

  if (native == NULL)
    return;
  self->surface = gtk_native_get_surface(native);
  if (self->surface == NULL)
    return;
  self->surface_width_handler = g_signal_connect(
      self->surface, "notify::width", G_CALLBACK(surface_geometry_changed), self);
  self->surface_height_handler = g_signal_connect(
      self->surface, "notify::height", G_CALLBACK(surface_geometry_changed), self);
  display = gdk_surface_get_display(self->surface);
  self->display_state = display_state_for(display);
  if (self->display_state != NULL)
    g_ptr_array_add(self->display_state->bindings, self);
  luma_surface_backdrop_sync(self);
  /* Protocol capability discovery completes inside display_state_for(),
   * before this newly realized binding is added to the display's binding
   * array.  Emit the instance property transition explicitly so consumers
   * which select their token sheet from notify::available do not remain on
   * the opaque Light fallback while this backdrop is already active. */
  if (was_available != luma_surface_backdrop_get_available(self))
    g_object_notify_by_pspec(G_OBJECT(self), properties[PROP_AVAILABLE]);
}

static void
surface_unrealized(GtkWidget *widget G_GNUC_UNUSED,
                   LumaSurfaceBackdrop *self) {
  remove_effect(self);
  set_active(self, FALSE);
  if (self->geometry_tick_id != 0 && self->window != NULL)
    gtk_widget_remove_tick_callback(GTK_WIDGET(self->window),
                                    self->geometry_tick_id);
  self->geometry_tick_id = 0;
  if (self->surface != NULL) {
    if (self->surface_width_handler != 0)
      g_signal_handler_disconnect(self->surface, self->surface_width_handler);
    if (self->surface_height_handler != 0)
      g_signal_handler_disconnect(self->surface, self->surface_height_handler);
  }
  self->surface_width_handler = 0;
  self->surface_height_handler = 0;
  if (self->display_state != NULL) {
    g_ptr_array_remove(self->display_state->bindings, self);
    g_clear_object(&self->display_state);
  }
  self->surface = NULL;
}

static void
policy_changed(LumaSurfacePolicy *policy G_GNUC_UNUSED,
               LumaSurfaceBackdrop *self) {
  luma_surface_backdrop_sync(self);
}

static void
window_gone(gpointer data, GObject *where_the_window_was G_GNUC_UNUSED) {
  LumaSurfaceBackdrop *self = data;
  self->window = NULL;
}

static void
luma_surface_backdrop_get_property(GObject *object, guint property_id,
                                   GValue *value, GParamSpec *pspec) {
  LumaSurfaceBackdrop *self = LUMA_SURFACE_BACKDROP(object);
  switch (property_id) {
  case PROP_AVAILABLE:
    g_value_set_boolean(value, luma_surface_backdrop_get_available(self));
    break;
  case PROP_ACTIVE:
    g_value_set_boolean(value, self->active);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, pspec);
  }
}

static void
luma_surface_backdrop_dispose(GObject *object) {
  LumaSurfaceBackdrop *self = LUMA_SURFACE_BACKDROP(object);
  if (self->window != NULL) {
    if (self->realize_handler != 0)
      g_signal_handler_disconnect(self->window, self->realize_handler);
    if (self->unrealize_handler != 0)
      g_signal_handler_disconnect(self->window, self->unrealize_handler);
    g_object_weak_unref(G_OBJECT(self->window), window_gone, self);
  }
  surface_unrealized(NULL, self);
  if (self->policy != NULL && self->policy_handler != 0)
    g_signal_handler_disconnect(self->policy, self->policy_handler);
  g_clear_object(&self->policy);
  self->window = NULL;
  G_OBJECT_CLASS(luma_surface_backdrop_parent_class)->dispose(object);
}

static void
luma_surface_backdrop_class_init(LumaSurfaceBackdropClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->get_property = luma_surface_backdrop_get_property;
  object_class->dispose = luma_surface_backdrop_dispose;
  properties[PROP_AVAILABLE] = g_param_spec_boolean(
      "available", NULL, NULL, FALSE,
      G_PARAM_READABLE | G_PARAM_STATIC_STRINGS);
  properties[PROP_ACTIVE] = g_param_spec_boolean(
      "active", NULL, NULL, FALSE,
      G_PARAM_READABLE | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, N_PROPERTIES, properties);
}

static void
luma_surface_backdrop_init(LumaSurfaceBackdrop *self) {
  self->policy =
      luma_surface_policy_new(LUMA_SURFACE_TARGET_APPLICATION);
  self->policy_handler = g_signal_connect(
      self->policy, "changed", G_CALLBACK(policy_changed), self);
}

LumaSurfaceBackdrop *
luma_surface_backdrop_new(GtkWindow *window) {
  LumaSurfaceBackdrop *self;
  g_return_val_if_fail(GTK_IS_WINDOW(window), NULL);

  if (window_binding_quark == 0)
    window_binding_quark = g_quark_from_static_string(
        "luma-surface-backdrop-window-binding");
  self = g_object_get_qdata(G_OBJECT(window), window_binding_quark);
  if (self != NULL)
    return g_object_ref(self);

  self = g_object_new(LUMA_TYPE_SURFACE_BACKDROP, NULL);
  g_object_set_qdata_full(G_OBJECT(window), window_binding_quark,
                          g_object_ref(self), g_object_unref);
  self->window = window;
  g_object_weak_ref(G_OBJECT(window), window_gone, self);
  self->realize_handler = g_signal_connect(
      window, "realize", G_CALLBACK(surface_realized), self);
  self->unrealize_handler = g_signal_connect(
      window, "unrealize", G_CALLBACK(surface_unrealized), self);
  if (gtk_widget_get_realized(GTK_WIDGET(window)))
    surface_realized(GTK_WIDGET(window), self);
  else
    luma_surface_backdrop_sync(self);
  return self;
}

gboolean
luma_surface_backdrop_get_available(LumaSurfaceBackdrop *self) {
  g_return_val_if_fail(LUMA_IS_SURFACE_BACKDROP(self), FALSE);
  return self->display_state != NULL &&
         self->display_state->blur_available;
}

gboolean
luma_surface_backdrop_get_active(LumaSurfaceBackdrop *self) {
  g_return_val_if_fail(LUMA_IS_SURFACE_BACKDROP(self), FALSE);
  return self->active;
}

gboolean
luma_surface_backdrop_display_is_available(GdkDisplay *display) {
  g_autoptr(LumaBackdropDisplay) state = NULL;

  g_return_val_if_fail(GDK_IS_DISPLAY(display), FALSE);
  /* Capability queries are part of luma-ui adoption. Initialize the shared
   * provider and bind the application's existing and future windows before
   * answering, so a picker cannot advertise translucency to a window that
   * never requests the compositor effect itself. */
  luma_init();
  state = display_state_for(display);
  return state != NULL && state->blur_available;
}
