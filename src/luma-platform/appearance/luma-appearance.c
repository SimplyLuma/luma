/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-appearance.h"
#include "luma-surface-recipes.h"

struct _LumaSurfacePolicy {
  GObject parent_instance;
  GSettings *settings, *interface_settings, *a11y_settings;
  LumaSurfaceTarget target;
  gboolean hardware, shell_blur, application_blur;
  gboolean locked, reduce;
  guint requested, effective, surface;
  const char *reason;
};
G_DEFINE_FINAL_TYPE(LumaSurfacePolicy, luma_surface_policy, G_TYPE_OBJECT)
G_DEFINE_ENUM_TYPE(LumaSurfaceTarget, luma_surface_target,
  G_DEFINE_ENUM_VALUE(LUMA_SURFACE_TARGET_SHELL, "shell"),
  G_DEFINE_ENUM_VALUE(LUMA_SURFACE_TARGET_APPLICATION, "application"))
static const char *names[] = {"light", "dark", "frost", "glass"};
static guint signals[1];

static gint parse(const char *name) {
  for (guint i = 0; i < G_N_ELEMENTS(names); i++)
    if (g_strcmp0(name, names[i]) == 0) return (gint)i;
  return -1;
}
static gboolean dark(LumaSurfacePolicy *self) {
  if (!self->interface_settings) return FALSE;
  g_autofree char *scheme = g_settings_get_string(self->interface_settings, "color-scheme");
  return g_str_equal(scheme, "prefer-dark");
}
static gboolean available(LumaSurfacePolicy *self) {
  return self->hardware && self->shell_blur && !self->locked && !self->reduce;
}
static void refresh(LumaSurfacePolicy *self) {
  guint requested = dark(self) ? 1 : 0;
  if (self->settings) {
    g_autoptr(GVariant) value = g_settings_get_user_value(self->settings, "surface-treatment");
    if (value) {
      gint chosen = parse(g_variant_get_string(value, NULL));
      if (chosen >= 0) requested = (guint)chosen;
    }
  }
  self->requested = requested;
  self->locked = self->a11y_settings &&
    g_settings_get_boolean(self->a11y_settings, "high-contrast");
  self->reduce = self->settings &&
    g_settings_get_boolean(self->settings, "reduce-transparency");
  self->effective = requested;
  self->reason = "";
  /* What a surface is *painted* in is a different question from whether a
   * live backdrop can be rendered behind it. Only accessibility answers the
   * first: high contrast and reduced transparency are the user saying they do
   * not want this material. A renderer that cannot blur is a reason not to
   * ask the compositor for a blur region, not a reason for this application
   * to be a different colour from the window beside it -- which is what it
   * became, because no other toolkit on the desktop knows or cares about this
   * application's own protocol handshake. */
  self->surface = requested;
  if (self->locked) {
    self->surface = dark(self) ? 1 : 0;
  } else if (requested >= 2 && self->reduce) {
    self->surface = 0;
  }
  if (self->locked) {
    self->effective = dark(self) ? 1 : 0;
    self->reason = "high-contrast";
  } else if (requested >= 2 && self->reduce) {
    self->effective = 0;
    self->reason = "reduce-transparency";
  } else if (requested >= 2 && (!self->hardware || !self->shell_blur)) {
    self->effective = 0;
    self->reason = "renderer-unavailable";
  } else if (requested >= 2 && self->target == LUMA_SURFACE_TARGET_APPLICATION &&
             !self->application_blur) {
    self->effective = 0;
    self->reason = "shell-only";
  }
  g_signal_emit(self, signals[0], 0);
}
static void changed(GSettings *settings G_GNUC_UNUSED, const char *key G_GNUC_UNUSED,
                    LumaSurfacePolicy *self) {
  refresh(self);
}
static void dispose(GObject *object) {
  LumaSurfacePolicy *self = LUMA_SURFACE_POLICY(object);
  GSettings **sources[] = {&self->settings, &self->interface_settings, &self->a11y_settings};
  for (guint i = 0; i < G_N_ELEMENTS(sources); i++) {
    if (*sources[i]) g_signal_handlers_disconnect_by_data(*sources[i], self);
    g_clear_object(sources[i]);
  }
  G_OBJECT_CLASS(luma_surface_policy_parent_class)->dispose(object);
}
static void luma_surface_policy_class_init(LumaSurfacePolicyClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = dispose;
  signals[0] = g_signal_new("changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                           0, NULL, NULL, NULL, G_TYPE_NONE, 0);
}
static void luma_surface_policy_init(LumaSurfacePolicy *self) { self->reason = ""; }
static GSettings *optional_settings(const char *name, const char *key) {
  GSettingsSchemaSource *source = g_settings_schema_source_get_default();
  g_autoptr(GSettingsSchema) schema = source ? g_settings_schema_source_lookup(source, name, TRUE) : NULL;
  return schema && g_settings_schema_has_key(schema, key) ? g_settings_new_full(schema, NULL, NULL) : NULL;
}
LumaSurfacePolicy *luma_surface_policy_new(LumaSurfaceTarget target) {
  g_autoptr(GSettings) settings = optional_settings("org.project_luma.shell-state", "surface-treatment");
  g_autoptr(GSettings) interface = optional_settings("org.gnome.desktop.interface", "color-scheme");
  g_autoptr(GSettings) a11y = optional_settings("org.gnome.desktop.a11y.interface", "high-contrast");
  return luma_surface_policy_new_for_settings(target, settings, interface, a11y);
}
LumaSurfacePolicy *luma_surface_policy_new_for_settings(LumaSurfaceTarget target,
    GSettings *settings, GSettings *interface, GSettings *a11y) {
  LumaSurfacePolicy *self = g_object_new(LUMA_TYPE_SURFACE_POLICY, NULL);
  self->target = target;
  self->settings = settings ? g_object_ref(settings) : NULL;
  self->interface_settings = interface ? g_object_ref(interface) : NULL;
  self->a11y_settings = a11y ? g_object_ref(a11y) : NULL;
  GSettings *sources[] = {settings, interface, a11y};
  for (guint i = 0; i < G_N_ELEMENTS(sources); i++)
    if (sources[i]) g_signal_connect(sources[i], "changed", G_CALLBACK(changed), self);
  refresh(self);
  return self;
}
void luma_surface_policy_set_capabilities(LumaSurfacePolicy *self,
    gboolean hardware, gboolean shell_blur, gboolean application_blur) {
  g_return_if_fail(LUMA_IS_SURFACE_POLICY(self));
  if (self->hardware == !!hardware && self->shell_blur == !!shell_blur &&
      self->application_blur == !!application_blur) return;
  self->hardware = !!hardware;
  self->shell_blur = !!shell_blur;
  self->application_blur = !!application_blur;
  refresh(self);
}
const char *luma_surface_policy_get_requested(LumaSurfacePolicy *self) { return names[self->requested]; }
const char *luma_surface_policy_get_effective(LumaSurfacePolicy *self) { return names[self->effective]; }
const char *luma_surface_policy_get_surface(LumaSurfacePolicy *self) { return names[self->surface]; }
const char *luma_surface_policy_get_reason(LumaSurfacePolicy *self) { return self->reason; }
gboolean luma_surface_policy_get_has_selection(LumaSurfacePolicy *self) {
  if (!self->settings) return FALSE;
  g_autoptr(GVariant) selected = g_settings_get_user_value(self->settings, "surface-treatment");
  return selected != NULL;
}
gboolean luma_surface_policy_get_locked(LumaSurfacePolicy *self) { return self->locked; }
gboolean luma_surface_policy_get_translucency_available(LumaSurfacePolicy *self) { return available(self); }
GVariant *luma_surface_policy_get_recipe(LumaSurfacePolicy *self) {
  return g_variant_ref_sink(g_variant_parse(G_VARIANT_TYPE_VARDICT,
    luma_surface_recipes[self->effective], NULL, NULL, NULL));
}
gboolean luma_surface_policy_select(LumaSurfacePolicy *self, const char *treatment,
                                    GError **error) {
  gint selected = parse(treatment);
  if (selected < 0 || !self->settings || self->locked || (selected >= 2 && !available(self))) {
    g_set_error_literal(error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
                        "Surface treatment is unavailable under the current capabilities or accessibility policy");
    return FALSE;
  }
  if (!g_settings_is_writable(self->settings, "surface-treatment")) {
    g_set_error_literal(error, G_IO_ERROR, G_IO_ERROR_PERMISSION_DENIED, "Surface treatment is locked by policy");
    return FALSE;
  }
  return g_settings_set_string(self->settings, "surface-treatment", treatment);
}
