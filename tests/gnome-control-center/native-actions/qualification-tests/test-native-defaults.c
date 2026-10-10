/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-live-app-options.c"
void cc_luma_live_ready(CcLumaFixture *m,const char *a) {}
gboolean cc_luma_live_publish_app(CcLumaFixture *m,const char *id,GAppInfo *info){return info && g_app_info_get_name(info);}
void cc_luma_live_desk_dump_watch(CcLumaFixture *m) {}
gboolean cc_luma_application_options_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_night_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_pointer_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_a11y_validate(CcLumaFixture *m){return FALSE;}
static gboolean writer(CcLumaFixture *m,const char *k,JsonNode *v,gpointer data,GError **e){return options_write(data,k,v,e);}
int main(void){
 g_setenv("GSETTINGS_BACKEND","memory",TRUE);g_autoptr(GError) error=NULL;
 g_autoptr(GDesktopAppInfo) a=g_desktop_app_info_new("org.example.SettingsProbe.desktop"),b=g_desktop_app_info_new("org.example.Alternate.desktop");g_assert_nonnull(a);g_assert_nonnull(b);
 for(guint i=0;i<G_N_ELEMENTS(defaults);i++){
  g_assert_true(g_app_info_set_as_default_for_type(G_APP_INFO(a),defaults[i].type,&error));g_assert_no_error(error);
  g_assert_true(g_app_info_set_as_last_used_for_type(G_APP_INFO(b),defaults[i].type,&error));g_assert_no_error(error);
 }
 g_autoptr(JsonParser) parser=json_parser_new();g_assert_true(json_parser_load_from_data(parser,"{\"settings\":{},\"data\":{}}",-1,NULL));
 g_autoptr(CcLumaFixture) m=cc_luma_fixture_new_live(json_parser_get_root(parser));Options *o=options_start(m);cc_luma_fixture_set_writer(m,writer,o);
 for(guint i=0;i<G_N_ELEMENTS(defaults);i++){
  g_assert_true(cc_luma_fixture_key_verified(m,defaults[i].key));
  g_autoptr(JsonNode) chosen=json_node_init_string(json_node_alloc(),"org-example-alternate");g_assert_true(cc_luma_fixture_write(m,defaults[i].key,chosen,&error));g_assert_no_error(error);
  g_autoptr(GAppInfo) actual=g_app_info_get_default_for_type(defaults[i].type,FALSE);g_assert_cmpstr(g_app_info_get_id(actual),==,"org.example.Alternate.desktop");
  Options *again=options_start(m);g_assert_cmpstr(json_node_get_string(cc_luma_fixture_get(m,defaults[i].key)),==,"org-example-alternate");options_stop(again);
  g_print("PASS native MIME default and reopen %s\n",defaults[i].key);
 }
 for(guint i=0;i<G_N_ELEMENTS(media);i++){
  const char *choices[]={"folder","nothing","ask"};
  for(guint c=0;c<3;c++){
   g_autoptr(JsonNode) value=json_node_init_string(json_node_alloc(),choices[c]);g_assert_true(cc_luma_fixture_write(m,media[i].key,value,&error));g_assert_no_error(error);
   g_auto(GStrv) folder=g_settings_get_strv(o->media,"autorun-x-content-open-folder");g_auto(GStrv) ignore=g_settings_get_strv(o->media,"autorun-x-content-ignore");g_auto(GStrv) start=g_settings_get_strv(o->media,"autorun-x-content-start-app");
   g_assert_cmpint(g_strv_contains((const char * const*)folder,media[i].type),==,c==0);g_assert_cmpint(g_strv_contains((const char * const*)ignore,media[i].type),==,c==1);g_assert_false(g_strv_contains((const char * const*)start,media[i].type));
  }
  g_print("PASS native removable media list transaction %s\n",media[i].key);
 }
 g_autoptr(JsonNode) bad=json_node_init_string(json_node_alloc(),"not-offered");g_assert_false(cc_luma_fixture_write(m,"def.mail",bad,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);
 options_stop(o);while(g_main_context_iteration(NULL,FALSE));g_print("PASS unknown default app refusal\n");return 0;
}
