/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-live-shelf.c"
void cc_luma_live_ready(CcLumaFixture *m,const char *a) {}
void cc_luma_live_publish_dock(CcLumaFixture *m) {}
void cc_luma_live_desk_dump_watch(CcLumaFixture *m) {}
gboolean cc_luma_night_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_pointer_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_a11y_validate(CcLumaFixture *m){return FALSE;}
static gboolean writer(CcLumaFixture *m,const char *k,JsonNode *v,gpointer data,GError **e){return shelf_write(data,k,v,e);}
int main(int argc,char **argv){
 g_setenv("GSETTINGS_BACKEND","memory",TRUE);g_autoptr(GError) error=NULL;
 g_autoptr(JsonParser) parser=json_parser_new();g_assert_true(json_parser_load_from_file(parser,argv[1],&error));g_assert_no_error(error);
 g_autoptr(CcLumaFixture) m=cc_luma_fixture_new_live(json_parser_get_root(parser));Shelf *s=shelf_start(m);g_assert_nonnull(s->settings);cc_luma_fixture_set_writer(m,writer,s);g_assert_true(cc_luma_shelf_validate(m));
 JsonArray *presets=catalog(s);guint count=json_array_get_length(presets);g_assert_cmpuint(count,>=,4);
 for(guint i=0;i<count;i++){
  const char *id=json_array_get_string_element(json_array_get_array_element(presets,i),0);g_autofree char *chosen=g_strdup(id);
  g_assert_true(cc_luma_shelf_set_preset(m,chosen,&error));g_assert_no_error(error);
  publish(s);g_assert_cmpstr(json_node_get_string(cc_luma_fixture_get(m,"preset")),==,chosen);
  Shelf *again=shelf_start(m);g_assert_cmpstr(json_node_get_string(cc_luma_fixture_get(m,"preset")),==,chosen);shelf_stop(again);
  g_print("PASS native shelf preset write/read/reopen %s\n",chosen);
 }
 g_assert_false(cc_luma_shelf_set_preset(m,"split",&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);g_clear_error(&error);
 g_assert_false(cc_luma_shelf_set_choice(m,"edge","invalid",&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);g_clear_error(&error);
 shelf_stop(s);while(g_main_context_iteration(NULL,FALSE));g_print("PASS unsupported shelf preset/edge refusal\n");return 0;
}
