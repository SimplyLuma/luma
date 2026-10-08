/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-live-extension.h"

struct _LumaLiveExtension {
  GObject parent_instance;
  char *id;
  char *app_id;
  char *title;
  char *subtitle;
  char *expires_at;
  char *starts_at;
  LumaLiveCategory category;
  LumaPrivacy privacy;
  double progress;
  GListStore *actions;
};
G_DEFINE_FINAL_TYPE(LumaLiveExtension, luma_live_extension, G_TYPE_OBJECT)
enum {
  PROP_0,
  PROP_ID,
  PROP_APP_ID,
  PROP_CATEGORY,
  PROP_TITLE,
  PROP_SUBTITLE,
  PROP_PRIVACY,
  PROP_PROGRESS,
  PROP_EXPIRES_AT,
  PROP_STARTS_AT,
  N_PROPS
};
static GParamSpec *properties[N_PROPS];
static void luma_live_extension_finalize(GObject *o) {
  LumaLiveExtension *s = LUMA_LIVE_EXTENSION(o);
  g_clear_pointer(&s->id, g_free);
  g_clear_pointer(&s->app_id, g_free);
  g_clear_pointer(&s->title, g_free);
  g_clear_pointer(&s->subtitle, g_free);
  g_clear_pointer(&s->expires_at, g_free);
  g_clear_pointer(&s->starts_at, g_free);
  g_clear_object(&s->actions);
  G_OBJECT_CLASS(luma_live_extension_parent_class)->finalize(o);
}
static void luma_live_extension_get_property(GObject *o, guint id, GValue *v,
                                             GParamSpec *p) {
  LumaLiveExtension *s = LUMA_LIVE_EXTENSION(o);
  switch (id) {
  case PROP_ID:
    g_value_set_string(v, s->id);
    break;
  case PROP_APP_ID:
    g_value_set_string(v, s->app_id);
    break;
  case PROP_CATEGORY:
    g_value_set_enum(v, s->category);
    break;
  case PROP_TITLE:
    g_value_set_string(v, s->title);
    break;
  case PROP_SUBTITLE:
    g_value_set_string(v, s->subtitle);
    break;
  case PROP_PRIVACY:
    g_value_set_enum(v, s->privacy);
    break;
  case PROP_PROGRESS:
    g_value_set_double(v, s->progress);
    break;
  case PROP_EXPIRES_AT:
    g_value_set_string(v, s->expires_at);
    break;
  case PROP_STARTS_AT:
    g_value_set_string(v, s->starts_at);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, p);
  }
}
static void luma_live_extension_set_property(GObject *o, guint id,
                                             const GValue *v, GParamSpec *p) {
  LumaLiveExtension *s = LUMA_LIVE_EXTENSION(o);
  switch (id) {
  case PROP_ID:
    s->id = g_value_dup_string(v);
    break;
  case PROP_APP_ID:
    s->app_id = g_value_dup_string(v);
    break;
  case PROP_CATEGORY:
    s->category = g_value_get_enum(v);
    break;
  case PROP_TITLE:
    s->title = g_value_dup_string(v);
    break;
  case PROP_SUBTITLE:
    luma_live_extension_set_subtitle(s, g_value_get_string(v));
    break;
  case PROP_PRIVACY:
    luma_live_extension_set_privacy(s, g_value_get_enum(v));
    break;
  case PROP_PROGRESS:
    luma_live_extension_set_progress(s, g_value_get_double(v));
    break;
  case PROP_EXPIRES_AT:
    luma_live_extension_set_expires_at(s, g_value_get_string(v));
    break;
  case PROP_STARTS_AT:
    luma_live_extension_set_starts_at(s, g_value_get_string(v));
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, p);
  }
}
static void luma_live_extension_class_init(LumaLiveExtensionClass *k) {
  GObjectClass *o = G_OBJECT_CLASS(k);
  o->finalize = luma_live_extension_finalize;
  o->get_property = luma_live_extension_get_property;
  o->set_property = luma_live_extension_set_property;
  properties[PROP_ID] = g_param_spec_string(
      "id", NULL, NULL, NULL,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  properties[PROP_APP_ID] = g_param_spec_string(
      "app-id", NULL, NULL, NULL,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  properties[PROP_CATEGORY] = g_param_spec_enum(
      "category", NULL, NULL, LUMA_TYPE_LIVE_CATEGORY,
      LUMA_LIVE_CATEGORY_GENERIC,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  properties[PROP_TITLE] = g_param_spec_string(
      "title", NULL, NULL, NULL,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  properties[PROP_SUBTITLE] = g_param_spec_string(
      "subtitle", NULL, NULL, NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS);
  properties[PROP_PRIVACY] = g_param_spec_enum(
      "privacy", NULL, NULL, LUMA_TYPE_PRIVACY, LUMA_PRIVACY_PRIVATE,
      G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS);
  properties[PROP_PROGRESS] =
      g_param_spec_double("progress", NULL, NULL, -1.0, 1.0, -1.0,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS);
  properties[PROP_EXPIRES_AT] = g_param_spec_string(
      "expires-at", NULL, NULL, NULL,
      G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS);
  properties[PROP_STARTS_AT] = g_param_spec_string(
      "starts-at", NULL, NULL, NULL,
      G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(o, N_PROPS, properties);
}
static void luma_live_extension_init(LumaLiveExtension *s) {
  s->privacy = LUMA_PRIVACY_PRIVATE;
  s->progress = -1.0;
  s->actions = g_list_store_new(LUMA_TYPE_ACTION);
}
LumaLiveExtension *luma_live_extension_new(const char *id, const char *app_id,
                                           LumaLiveCategory c, const char *t) {
  g_return_val_if_fail(id && *id, NULL);
  g_return_val_if_fail(app_id && *app_id, NULL);
  g_return_val_if_fail(t && *t, NULL);
  return g_object_new(LUMA_TYPE_LIVE_EXTENSION, "id", id, "app-id", app_id,
                      "category", c, "title", t, NULL);
}
const char *luma_live_extension_get_id(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), NULL);
  return s->id;
}
const char *luma_live_extension_get_app_id(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), NULL);
  return s->app_id;
}
const char *luma_live_extension_get_title(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), NULL);
  return s->title;
}
const char *luma_live_extension_get_subtitle(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), NULL);
  return s->subtitle;
}
void luma_live_extension_set_subtitle(LumaLiveExtension *s, const char *v) {
  g_return_if_fail(LUMA_IS_LIVE_EXTENSION(s));
  g_set_str(&s->subtitle, v);
  g_object_notify_by_pspec(G_OBJECT(s), properties[PROP_SUBTITLE]);
}
double luma_live_extension_get_progress(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), -1);
  return s->progress;
}
void luma_live_extension_set_progress(LumaLiveExtension *s, double v) {
  g_return_if_fail(LUMA_IS_LIVE_EXTENSION(s));
  g_return_if_fail(v >= -1 && v <= 1);
  if (s->progress == v)
    return;
  s->progress = v;
  g_object_notify_by_pspec(G_OBJECT(s), properties[PROP_PROGRESS]);
}
const char *luma_live_extension_get_expires_at(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), NULL);
  return s->expires_at;
}
void luma_live_extension_set_expires_at(LumaLiveExtension *s, const char *v) {
  g_return_if_fail(LUMA_IS_LIVE_EXTENSION(s));
  g_set_str(&s->expires_at, v);
  g_object_notify_by_pspec(G_OBJECT(s), properties[PROP_EXPIRES_AT]);
}
const char *luma_live_extension_get_starts_at(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), NULL);
  return s->starts_at;
}
void luma_live_extension_set_starts_at(LumaLiveExtension *s, const char *v) {
  g_return_if_fail(LUMA_IS_LIVE_EXTENSION(s));
  g_set_str(&s->starts_at, v);
  g_object_notify_by_pspec(G_OBJECT(s), properties[PROP_STARTS_AT]);
}
LumaPrivacy luma_live_extension_get_privacy(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), LUMA_PRIVACY_SECRET);
  return s->privacy;
}
void luma_live_extension_set_privacy(LumaLiveExtension *s, LumaPrivacy v) {
  g_return_if_fail(LUMA_IS_LIVE_EXTENSION(s));
  if (s->privacy == v)
    return;
  s->privacy = v;
  g_object_notify_by_pspec(G_OBJECT(s), properties[PROP_PRIVACY]);
}
GListModel *luma_live_extension_get_actions(LumaLiveExtension *s) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), NULL);
  return G_LIST_MODEL(s->actions);
}
gboolean luma_live_extension_add_action(LumaLiveExtension *s, LumaAction *a,
                                        GError **error) {
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), FALSE);
  g_return_val_if_fail(LUMA_IS_ACTION(a), FALSE);
  if (g_list_model_get_n_items(G_LIST_MODEL(s->actions)) >= 3) {
    g_set_error_literal(error, G_IO_ERROR, G_IO_ERROR_NO_SPACE,
                        "A Live Extension may expose at most three actions");
    return FALSE;
  }
  g_list_store_append(s->actions, a);
  return TRUE;
}
GVariant *luma_live_extension_to_variant(LumaLiveExtension *s) {
  GVariantBuilder b;
  GVariantBuilder actions;
  g_return_val_if_fail(LUMA_IS_LIVE_EXTENSION(s), NULL);
  g_variant_builder_init(&b, G_VARIANT_TYPE_VARDICT);
  g_variant_builder_add(&b, "{sv}", "schema_version",
                        g_variant_new_string("0.1"));
  g_variant_builder_add(&b, "{sv}", "id", g_variant_new_string(s->id));
  g_variant_builder_add(&b, "{sv}", "app_id", g_variant_new_string(s->app_id));
  g_variant_builder_add(&b, "{sv}", "title", g_variant_new_string(s->title));
  g_variant_builder_add(
      &b, "{sv}", "category",
      g_variant_new_string(luma_live_category_to_string(s->category)));
  g_variant_builder_add(
      &b, "{sv}", "privacy",
      g_variant_new_string(luma_privacy_to_string(s->privacy)));
  g_variant_builder_add(&b, "{sv}", "progress",
                        g_variant_new_double(s->progress));
  g_variant_builder_init(&actions, G_VARIANT_TYPE("aa{sv}"));
  for (guint i = 0; i < g_list_model_get_n_items(G_LIST_MODEL(s->actions));
       i++) {
    g_autoptr(LumaAction) action =
        g_list_model_get_item(G_LIST_MODEL(s->actions), i);
    g_autoptr(GVariant) serialized = luma_action_to_variant(action);
    g_variant_builder_add_value(&actions, serialized);
  }
  g_variant_builder_add(&b, "{sv}", "actions", g_variant_builder_end(&actions));
  if (s->expires_at)
    g_variant_builder_add(&b, "{sv}", "expires_at",
                          g_variant_new_string(s->expires_at));
  if (s->starts_at)
    g_variant_builder_add(&b, "{sv}", "starts_at",
                          g_variant_new_string(s->starts_at));
  if (s->subtitle)
    g_variant_builder_add(&b, "{sv}", "subtitle",
                          g_variant_new_string(s->subtitle));
  return g_variant_ref_sink(g_variant_builder_end(&b));
}
