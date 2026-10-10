/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-live-notifications.c"
const char *cc_luma_app_name(CcLumaFixture *m,const char *id){return id;}
void cc_luma_live_ready(CcLumaFixture *m,const char *a) {}
gboolean cc_luma_live_publish_app(CcLumaFixture *m,const char *id,GAppInfo *info){return info && g_app_info_get_name(info);}
void cc_luma_live_desk_dump_watch(CcLumaFixture *m) {}
gboolean cc_luma_notifications_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_night_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_pointer_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_a11y_validate(CcLumaFixture *m){return FALSE;}
static gboolean writer(CcLumaFixture *m,const char *k,JsonNode *v,gpointer s,GError **e){return notifications_write(s,k,v,e);}
int main(void){
 g_setenv("GSETTINGS_BACKEND","memory",TRUE);
 g_autoptr(JsonParser) parser=json_parser_new();g_assert_true(json_parser_load_from_data(parser,"{\"settings\":{},\"data\":{}}",-1,NULL));
 g_autoptr(CcLumaFixture) m=cc_luma_fixture_new_live(json_parser_get_root(parser));Notifications *n=notifications_start(m);cc_luma_fixture_set_writer(m,writer,n);
 const char *key="na.org-example-settingsprobe";g_assert_true(cc_luma_fixture_key_verified(m,key));
 g_autoptr(GError) error=NULL;g_autoptr(JsonNode) off=json_node_init_boolean(json_node_alloc(),FALSE);
 g_assert_true(cc_luma_fixture_write(m,key,off,&error));g_assert_no_error(error);
 GSettings *child=g_hash_table_lookup(n->apps,"org-example-settingsprobe");g_assert_nonnull(child);g_assert_false(g_settings_get_boolean(child,"enable"));
 notifications_stop(n);n=notifications_start(m);g_assert_false(g_settings_get_boolean(g_hash_table_lookup(n->apps,"org-example-settingsprobe"),"enable"));
 cc_luma_fixture_set_writer(m,writer,n);g_autoptr(JsonNode) bad=json_node_init_string(json_node_alloc(),"false");
 g_assert_false(cc_luma_fixture_write(m,key,bad,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);
 g_assert_false(cc_luma_fixture_key_verified(m,"na.unknown"));notifications_stop(n);while(g_main_context_iteration(NULL,FALSE));
 g_print("PASS native app notification write/read/reopen, type refusal and absent app unqualified\n");return 0;
}
