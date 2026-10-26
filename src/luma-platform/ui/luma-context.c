/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-context.h"
#include <gio/gio.h>

struct _LumaContext {
  GObject parent_instance;
  LumaPresentationMode presentation;
  LumaInputMode input;
  GDBusProxy *session;
  const char *experience;
};
G_DEFINE_FINAL_TYPE(LumaContext, luma_context, G_TYPE_OBJECT)
G_DEFINE_ENUM_TYPE(LumaPresentationMode, luma_presentation_mode,
                   G_DEFINE_ENUM_VALUE(LUMA_PRESENTATION_WINDOWED, "windowed"),
                   G_DEFINE_ENUM_VALUE(LUMA_PRESENTATION_FULLSCREEN_MOBILE,
                                       "fullscreen-mobile"))
G_DEFINE_ENUM_TYPE(LumaInputMode, luma_input_mode,
                   G_DEFINE_ENUM_VALUE(LUMA_INPUT_POINTER, "pointer"),
                   G_DEFINE_ENUM_VALUE(LUMA_INPUT_TOUCH, "touch"))
enum { PROP_0, PROP_PRESENTATION, PROP_INPUT, PROP_EXPERIENCE, N_PROPS };
static GParamSpec *properties[N_PROPS];

static void apply_session(LumaContext *self) {
  g_object_freeze_notify(G_OBJECT(self));
  g_autoptr(GVariant) mode = g_dbus_proxy_get_cached_property(self->session, "Presentation");
  if (mode && g_variant_is_of_type(mode, G_VARIANT_TYPE_STRING)) {
    const char *value = g_variant_get_string(mode, NULL);
    if (g_str_equal(value, "desktop") || g_str_equal(value, "tablet") || g_str_equal(value, "mobile")) {
      if (g_strcmp0(self->experience, value) != 0) {
        self->experience = g_intern_string(value);
        g_object_notify_by_pspec(G_OBJECT(self), properties[PROP_EXPERIENCE]);
      }
      LumaPresentationMode presentation = g_str_equal(value, "desktop")
          ? LUMA_PRESENTATION_WINDOWED : LUMA_PRESENTATION_FULLSCREEN_MOBILE;
      if (self->presentation != presentation) {
        self->presentation = presentation;
        g_object_notify_by_pspec(G_OBJECT(self), properties[PROP_PRESENTATION]);
      }
    }
  }
  g_autoptr(GVariant) capabilities = g_dbus_proxy_get_cached_property(self->session, "InputCapabilities");
  gboolean touch;
  if (capabilities && g_variant_is_of_type(capabilities, G_VARIANT_TYPE("a{sb}")) &&
      g_variant_lookup(capabilities, "touchscreen", "b", &touch)) {
    LumaInputMode input = touch ? LUMA_INPUT_TOUCH : LUMA_INPUT_POINTER;
    if (self->input != input) {
      self->input = input;
      g_object_notify_by_pspec(G_OBJECT(self), properties[PROP_INPUT]);
    }
  }
  g_object_thaw_notify(G_OBJECT(self));
}

static void session_changed(GDBusProxy *proxy, GVariant *changed,
                            const char *const *invalidated, LumaContext *self) {
  (void)proxy; (void)changed; (void)invalidated;
  apply_session(self);
}

static void session_ready(GObject *source, GAsyncResult *result, gpointer data) {
  (void)source;
  g_autoptr(LumaContext) self = data;
  g_autoptr(GError) error = NULL;
  self->session = g_dbus_proxy_new_for_bus_finish(result, &error);
  if (self->session == NULL)
    return; /* Standalone apps retain their explicit environment fallback. */
  g_signal_connect_object(self->session, "g-properties-changed",
                          G_CALLBACK(session_changed), self, 0);
  apply_session(self);
}

static void luma_context_dispose(GObject *object) {
  LumaContext *self = LUMA_CONTEXT(object);
  g_clear_object(&self->session);
  G_OBJECT_CLASS(luma_context_parent_class)->dispose(object);
}
static void luma_context_get_property(GObject *o, guint id, GValue *v,
                                      GParamSpec *p) {
  LumaContext *s = LUMA_CONTEXT(o);
  switch (id) {
  case PROP_PRESENTATION:
    g_value_set_enum(v, s->presentation);
    break;
  case PROP_INPUT:
    g_value_set_enum(v, s->input);
    break;
  case PROP_EXPERIENCE:
    g_value_set_string(v, s->experience);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, p);
  }
}
static void luma_context_set_property(GObject *o, guint id, const GValue *v,
                                      GParamSpec *p) {
  LumaContext *s = LUMA_CONTEXT(o);
  switch (id) {
  case PROP_PRESENTATION:
    s->presentation = g_value_get_enum(v);
    s->experience = s->presentation == LUMA_PRESENTATION_WINDOWED ? "desktop" : "mobile";
    break;
  case PROP_INPUT:
    s->input = g_value_get_enum(v);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, p);
  }
}
static void luma_context_class_init(LumaContextClass *k) {
  GObjectClass *o = G_OBJECT_CLASS(k);
  o->get_property = luma_context_get_property;
  o->set_property = luma_context_set_property;
  o->dispose = luma_context_dispose;
  properties[PROP_PRESENTATION] = g_param_spec_enum(
      "presentation", NULL, NULL, LUMA_TYPE_PRESENTATION_MODE,
      LUMA_PRESENTATION_WINDOWED,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  properties[PROP_INPUT] = g_param_spec_enum(
      "input", NULL, NULL, LUMA_TYPE_INPUT_MODE, LUMA_INPUT_POINTER,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  properties[PROP_EXPERIENCE] = g_param_spec_string(
      "experience", NULL, NULL, "desktop", G_PARAM_READABLE | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(o, N_PROPS, properties);
}
static void luma_context_init(LumaContext *s) {
  s->presentation = LUMA_PRESENTATION_WINDOWED;
  s->input = LUMA_INPUT_POINTER;
  s->experience = "desktop";
}
LumaContext *luma_context_new(LumaPresentationMode p, LumaInputMode i) {
  return g_object_new(LUMA_TYPE_CONTEXT, "presentation", p, "input", i, NULL);
}
static gboolean is_touch_profile(void) {
  const char *d = g_getenv("LUMA_DEVICE_CLASS");
  if (g_strcmp0(d, "desktop") == 0)
    return FALSE;
  if (g_strcmp0(d, "handheld") == 0 || g_strcmp0(d, "tablet") == 0 ||
      g_strcmp0(d, "mobile") == 0)
    return TRUE;
  g_autofree char *contents = NULL;
  if (g_file_get_contents("/etc/luma-device-class", &contents, NULL, NULL)) {
    g_strstrip(contents);
    return g_strcmp0(contents, "handheld") == 0 || g_strcmp0(contents, "tablet") == 0 ||
           g_strcmp0(contents, "mobile") == 0;
  }
  return FALSE;
}
LumaContext *luma_context_new_from_environment(void) {
  gboolean handheld = is_touch_profile();
  const char *p = g_getenv("LUMA_PRESENTATION_MODE");
  const char *i = g_getenv("LUMA_INPUT_MODE");
  LumaPresentationMode pm = handheld ? LUMA_PRESENTATION_FULLSCREEN_MOBILE
                                     : LUMA_PRESENTATION_WINDOWED;
  LumaInputMode im = handheld ? LUMA_INPUT_TOUCH : LUMA_INPUT_POINTER;
  if (p) {
    if (g_str_equal(p, "windowed"))
      pm = LUMA_PRESENTATION_WINDOWED;
    else if (g_str_equal(p, "fullscreen-mobile"))
      pm = LUMA_PRESENTATION_FULLSCREEN_MOBILE;
    else
      g_warning("Ignoring invalid LUMA_PRESENTATION_MODE=%s", p);
  }
  if (i) {
    if (g_str_equal(i, "pointer"))
      im = LUMA_INPUT_POINTER;
    else if (g_str_equal(i, "touch"))
      im = LUMA_INPUT_TOUCH;
    else
      g_warning("Ignoring invalid LUMA_INPUT_MODE=%s", i);
  }
  LumaContext *context = luma_context_new(pm, im);
  g_dbus_proxy_new_for_bus(G_BUS_TYPE_SESSION, G_DBUS_PROXY_FLAGS_NONE, NULL,
      "org.project_luma.ShellState1", "/org/project_luma/ShellState1",
      "org.project_luma.ShellState1", NULL, session_ready, g_object_ref(context));
  return context;
}
LumaPresentationMode luma_context_get_presentation(LumaContext *s) {
  g_return_val_if_fail(LUMA_IS_CONTEXT(s), LUMA_PRESENTATION_WINDOWED);
  return s->presentation;
}
LumaInputMode luma_context_get_input(LumaContext *s) {
  g_return_val_if_fail(LUMA_IS_CONTEXT(s), LUMA_INPUT_POINTER);
  return s->input;
}
gboolean luma_context_get_decorated(LumaContext *s) {
  return luma_context_get_presentation(s) == LUMA_PRESENTATION_WINDOWED;
}
gboolean luma_context_get_touch_targets(LumaContext *s) {
  return luma_context_get_input(s) == LUMA_INPUT_TOUCH;
}

const char *luma_context_get_experience(LumaContext *s) {
  g_return_val_if_fail(LUMA_IS_CONTEXT(s), "desktop");
  return s->experience;
}
