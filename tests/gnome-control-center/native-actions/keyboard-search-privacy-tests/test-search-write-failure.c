/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-discovery.h"
static gboolean reject (CcLumaFixture *model,const char *key,JsonNode *value,gpointer data,GError **error)
{ g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_PERMISSION_DENIED,"Locked by policy");return FALSE; }
int main (void)
{
  g_autoptr(JsonNode) data=json_from_string("{\"settings\":{\"sa\":[[\"one\",true],[\"two\",false]],\"sl\":{\"file:///tmp/one\":false}},\"data\":{\"CFAPPINFO\":{\"one\":{\"name\":\"One\"},\"two\":{\"name\":\"Two\"}}}}",NULL);
  g_autoptr(CcLumaFixture) model=cc_luma_fixture_new_live(data);
  cc_luma_fixture_own_key(model,"sa");cc_luma_fixture_verify_key(model,"sa");cc_luma_fixture_own_key(model,"sl");cc_luma_fixture_verify_key(model,"sl");cc_luma_fixture_set_writer(model,reject,NULL);
  g_assert_true(cc_luma_search_validate(model));g_autoptr(GError) error=NULL;
  g_assert_false(cc_luma_search_set_app(model,"one",FALSE,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_PERMISSION_DENIED);g_clear_error(&error);
  JsonArray *apps=json_node_get_array(cc_luma_fixture_get(model,"sa"));g_assert_true(json_array_get_boolean_element(json_array_get_array_element(apps,0),1));
  g_assert_false(cc_luma_search_move_app(model,"one",1,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_PERMISSION_DENIED);g_clear_error(&error);
  g_assert_cmpstr(json_array_get_string_element(json_array_get_array_element(apps,0),0),==,"one");
  g_assert_false(cc_luma_search_add_place(model,"file:///tmp/two",&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_PERMISSION_DENIED);g_clear_error(&error);
  g_assert_false(json_object_has_member(json_node_get_object(cc_luma_fixture_get(model,"sl")),"file:///tmp/two"));
  g_print("SEARCH REJECTED PROVIDER/FOLDER WRITES PRESERVE CURRENT MODEL PASS\n");return 0;
}
