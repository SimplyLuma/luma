/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-view-privacy.c"
static guint trash_requests, temp_requests;
static gboolean fail_request;
static void method (GDBusConnection *bus, const char *sender, const char *path, const char *iface,
                    const char *name, GVariant *parameters, GDBusMethodInvocation *call, gpointer data)
{
  if (fail_request) { g_dbus_method_invocation_return_dbus_error(call,"org.gnome.SettingsDaemon.Housekeeping.Error","Cleanup unavailable");return; }
  if(g_str_equal(name,"EmptyTrash")) trash_requests++; else if(g_str_equal(name,"RemoveTempFiles")) temp_requests++;
  g_dbus_method_invocation_return_value(call,NULL);
}
static void spin (guint ms)
{
  gint64 until=g_get_monotonic_time()+1000*ms;
  while(g_get_monotonic_time()<until) { while(g_main_context_iteration(NULL,FALSE));g_usleep(1000); }
}
static GtkWidget *find_dialog (GtkWidget *root)
{
  if (LUMA_IS_DESTRUCTIVE_DIALOG(root)) return root;
  for(GtkWidget *c=gtk_widget_get_first_child(root);c;c=gtk_widget_get_next_sibling(c)){GtkWidget *found=find_dialog(c);if(found)return found;}
  return NULL;
}
static guint delegated;
static gboolean delegate (GtkWidget *root,const char *page,gpointer data) { delegated++; return TRUE; }
int main (void)
{
  g_setenv("GSK_RENDERER","cairo",TRUE); g_setenv("GTK_A11Y","none",TRUE); g_setenv("GSETTINGS_BACKEND","memory",TRUE);
  g_assert_cmpstr(g_getenv("LUMA_PRIVATE_TEST_BUS"),==,"1");
  gtk_init();
  g_autoptr(GError) error=NULL;
  g_autoptr(GDBusConnection) bus=g_bus_get_sync(G_BUS_TYPE_SESSION,NULL,&error);g_assert_no_error(error);
  g_autoptr(GDBusNodeInfo) info=g_dbus_node_info_new_for_xml("<node><interface name='org.gnome.SettingsDaemon.Housekeeping'><method name='EmptyTrash'/><method name='RemoveTempFiles'/></interface></node>",&error);g_assert_no_error(error);
  static const GDBusInterfaceVTable table={.method_call=method};
  guint registration=g_dbus_connection_register_object(bus,"/org/gnome/SettingsDaemon/Housekeeping",info->interfaces[0],&table,NULL,NULL,&error);g_assert_no_error(error);g_assert_cmpuint(registration,>,0);
  g_autoptr(GVariant) request=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","RequestName",g_variant_new("(su)","org.gnome.SettingsDaemon.Housekeeping",0),G_VARIANT_TYPE("(u)"),G_DBUS_CALL_FLAGS_NONE,-1,NULL,&error);g_assert_no_error(error);
  GtkWidget *window=gtk_window_new(),*root=gtk_box_new(GTK_ORIENTATION_VERTICAL,0);gtk_window_set_child(GTK_WINDOW(window),root);gtk_window_present(GTK_WINDOW(window));
  g_autoptr(JsonNode) structure=json_from_string("{\"settings\":{},\"data\":{}}",NULL);
  g_autoptr(CcLumaFixture) model=cc_luma_fixture_new_live(structure);
  View v={.root=root,.model=model,.page="p-files",.generation=7,.delegate=delegate};g_object_set_data(G_OBJECT(root),"cf-view",&v);
  // Cancel does nothing: requesting confirmation alone never executes cleanup.
  empty_trash(NULL,&v);spin(50);g_assert_cmpuint(trash_requests,==,0);
  GtkWidget *cancelled=find_dialog(window);g_assert_nonnull(cancelled);g_signal_emit_by_name(cancelled,"cancelled");spin(50);g_assert_cmpuint(trash_requests,==,0);
  LumaDestructiveDialog *dialog=luma_destructive_dialog_ask(root,"Delete files?","Test","Delete","trash-2",NULL);
  guint64 *generation=g_new(guint64,1);*generation=v.generation;
  g_object_set_data_full(G_OBJECT(dialog),"cf-privacy-generation",generation,g_free);
  g_object_set_data(G_OBJECT(dialog),"cf-privacy-method","EmptyTrash");g_object_set_data(G_OBJECT(dialog),"cf-privacy-message","Trash cleanup requested");
  confirmed(dialog,FALSE,root);spin(250);g_assert_cmpuint(trash_requests,==,1);g_assert_cmpuint(delegated,==,0);
  g_object_set_data(G_OBJECT(dialog),"cf-privacy-method","RemoveTempFiles");confirmed(dialog,FALSE,root);spin(250);g_assert_cmpuint(temp_requests,==,1);
  *generation=6;confirmed(dialog,FALSE,root);spin(80);g_assert_cmpuint(temp_requests,==,1);*generation=7;
  fail_request=TRUE;confirmed(dialog,FALSE,root);spin(250);g_assert_cmpuint(temp_requests,==,1);g_assert_cmpuint(delegated,==,0);g_assert_nonnull(v.image_error);g_assert_nonnull(strstr(v.image_error->message,"Cleanup unavailable"));g_clear_error(&v.image_error);
  g_object_set_data(G_OBJECT(root),"cf-view",NULL);gtk_window_destroy(GTK_WINDOW(window));spin(50);
  g_dbus_connection_unregister_object(bus,registration);g_clear_object(&bus);
  g_print("NATIVE PRIVACY CONFIRM/CANCEL/STALE/DBUS FAILURE NO-DELEGATE PASS\n");return 0;
}
