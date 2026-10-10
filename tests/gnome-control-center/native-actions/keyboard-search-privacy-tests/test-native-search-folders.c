/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-view-discovery.c"
static void spin (View *v)
{
  gint64 until=g_get_monotonic_time()+5000000;
  while(v->search_write_cancel&&g_get_monotonic_time()<until){g_main_context_iteration(NULL,FALSE);g_usleep(1000);}
  g_assert_null(v->search_write_cancel);
}
int main(void)
{
  g_setenv("GSETTINGS_BACKEND","memory",TRUE);g_setenv("GTK_A11Y","none",TRUE);g_setenv("GSK_RENDERER","cairo",TRUE);gtk_init();
  g_autoptr(GError) error=NULL;g_autofree char *dir=g_dir_make_tmp("luma-search-write-XXXXXX",&error);g_assert_no_error(error);
  g_autoptr(GFile) folder=g_file_new_for_path(dir);g_autoptr(GSettings) settings=cc_luma_search_locations_new(&error);g_assert_no_error(error);g_assert_nonnull(settings);
  GtkWidget *root=g_object_ref_sink(gtk_box_new(GTK_ORIENTATION_VERTICAL,0));
  g_autoptr(JsonNode) data=json_from_string("{\"settings\":{\"sa\":[],\"sl\":{}},\"data\":{\"CFAPPINFO\":{}}}",NULL);g_autoptr(CcLumaFixture) model=cc_luma_fixture_new_live(data);
  View v={.root=root,.model=model,.page="search-places",.generation=1,.refresh=1};g_object_set_data(G_OBJECT(root),"cf-view",&v);
  g_autofree char *backup=g_build_filename(dir,"backups",NULL);g_assert_true(cc_luma_view_bind_search_locations(root,settings,backup,&error));g_assert_no_error(error);
  write_live_place(&v,folder,TRUE);spin(&v);g_assert_no_error(v.image_error);
  g_autofree char *uri=g_file_get_uri(folder);g_assert_true(json_object_get_boolean_member(json_node_get_object(cc_luma_fixture_get(model,"sl")),uri));
  g_autoptr(GPtrArray) saved=cc_luma_search_locations_dup_folders(settings);gboolean found=FALSE;for(guint i=0;i<saved->len;i++)found|=g_file_equal(folder,saved->pdata[i]);g_assert_true(found);
  write_live_place(&v,folder,FALSE);spin(&v);g_assert_no_error(v.image_error);g_assert_false(json_object_get_boolean_member(json_node_get_object(cc_luma_fixture_get(model,"sl")),uri));
  g_autoptr(GFile) remote=g_file_new_for_uri("smb://invalid.example/share");write_live_place(&v,remote,TRUE);spin(&v);g_assert_error(v.image_error,G_IO_ERROR,G_IO_ERROR_NOT_SUPPORTED);g_clear_error(&v.image_error);
  if(v.search_signal)g_signal_handler_disconnect(settings,v.search_signal);
  g_clear_object(&v.search_locations);g_free(v.search_backup_dir);g_object_set_data(G_OBJECT(root),"cf-view",NULL);g_object_unref(root);
  g_print("NATIVE ASYNC SEARCH BIND/ADD/READBACK/REMOVE/REMOTE REJECT PASS\n");return 0;
}
