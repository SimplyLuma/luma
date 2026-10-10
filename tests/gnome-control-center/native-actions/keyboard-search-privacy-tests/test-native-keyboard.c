/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-live-gsettings.h"
#include "cc-luma-keyboard.h"
extern const CcLumaAdapter cc_luma_adapter_keyboard;
void cc_luma_live_ready (CcLumaFixture *model, const char *adapter) { cc_luma_fixture_changed (model); }
static gpointer state;
static gboolean writer (CcLumaFixture *model, const char *key, JsonNode *value, gpointer data, GError **error)
{ return cc_luma_adapter_keyboard.write (state, key, value, error); }
int main (void)
{
  g_setenv ("GSETTINGS_BACKEND", "memory", TRUE);
  g_autoptr (GSettings) settings = g_settings_new ("org.gnome.desktop.input-sources");
  g_assert_true (g_settings_set_value (settings, "sources", g_variant_new_parsed ("[('xkb','us'),('xkb','fr')]")));
  const char *options[] = { "caps:escape", NULL };
  g_assert_true (g_settings_set_strv (settings, "xkb-options", options));
  g_autoptr (JsonNode) structure = json_from_string ("{\"settings\":{},\"data\":{\"CFKB\":{\"codes\":[],\"w\":{},\"L\":{\"English (US)\":[]}}}}", NULL);
  g_autoptr (CcLumaFixture) model = cc_luma_fixture_new_live (structure);
  state = cc_luma_adapter_keyboard.start (model); cc_luma_fixture_set_writer (model, writer, NULL);
  g_assert_true (cc_luma_keyboard_validate (model));
  JsonObject *available = json_object_get_object_member (json_object_get_object_member (cc_luma_fixture_get_data (model), "CFKB"), "available");
  g_autoptr (GList) layouts = json_object_get_members (available);
  const char *german = NULL;
  for (GList *l=layouts;l;l=l->next) if (g_strcmp0 (json_object_get_string_member (available,l->data),"de")==0) german=l->data;
  g_assert_nonnull (german);
  g_autofree char *name = g_strdup (german);
  g_autoptr (GError) error = NULL;
  g_assert_true (cc_luma_keyboard_add (model,name,&error)); g_assert_no_error (error);
  g_autoptr (GVariant) sources = g_settings_get_value (settings,"sources"); g_assert_cmpuint (g_variant_n_children(sources),==,3);
  g_assert_true (cc_luma_keyboard_move (model,2,0,&error)); g_assert_no_error (error);
  g_clear_pointer (&sources,g_variant_unref); sources=g_settings_get_value (settings,"sources");
  const char *type,*id; g_variant_get_child (sources,0,"(&s&s)",&type,&id); g_assert_cmpstr(id,==,"de");
  g_assert_true (cc_luma_keyboard_remove (model,name,&error)); g_assert_no_error(error);
  g_assert_false(cc_luma_keyboard_add(model,"not a known layout",&error)); g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);g_clear_error(&error);
  g_assert_true(cc_luma_keyboard_set_choice(model,"compose","CapsLock",&error));g_assert_no_error(error);
  g_auto(GStrv) saved=g_settings_get_strv(settings,"xkb-options");g_assert_true(g_strv_contains((const char*const*)saved,"caps:escape"));g_assert_true(g_strv_contains((const char*const*)saved,"compose:caps"));
  cc_luma_adapter_keyboard.stop(state);state=cc_luma_adapter_keyboard.start(model);
  g_assert_cmpuint(json_array_get_length(json_node_get_array(cc_luma_fixture_get(model,"srcs"))),==,2);
  cc_luma_adapter_keyboard.stop(state);
  g_print("NATIVE KEYBOARD ADD/MOVE/REMOVE/REOPEN/UNKNOWN/PRESERVED OPTIONS PASS\n");return 0;
}
