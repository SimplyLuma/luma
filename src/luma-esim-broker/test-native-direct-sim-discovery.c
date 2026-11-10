/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include <gio/gio.h>
#include "native-terminal-client.c"
#define SIM0 "/org/freedesktop/ModemManager1/SIM/0"
#define SIM1 "/org/freedesktop/ModemManager1/SIM/1"
static gint mode,opens,properties_seen,turned;
static GMutex ready_mutex;static GCond ready_cond;static gboolean ready;
static GMainLoop *loop;static GDBusConnection *server,*replacement;
static GDBusConnection *new_bus(void){
 GError *e=NULL;GDBusConnection *b=g_dbus_connection_new_for_address_sync(g_getenv("DBUS_SESSION_BUS_ADDRESS"),G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT|G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION,NULL,NULL,&e);
 g_assert_no_error(e);g_assert_nonnull(b);return b;
}
static void name_call(GDBusConnection *b,const gchar *method){
 GError *e=NULL;GVariant *v=g_dbus_connection_call_sync(b,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus",method,
 g_str_equal(method,"RequestName")?g_variant_new("(su)","org.freedesktop.ModemManager1",4):g_variant_new("(s)","org.freedesktop.ModemManager1"),G_VARIANT_TYPE("(u)"),G_DBUS_CALL_FLAGS_NONE,2000,NULL,&e);
 g_assert_no_error(e);g_assert_nonnull(v);g_variant_unref(v);
}
static void manager_call(GDBusConnection *b,const gchar *sender,const gchar *path,const gchar *iface,const gchar *method,GVariant *params,GDBusMethodInvocation *inv,gpointer data){
 (void)b;(void)sender;(void)path;(void)iface;(void)params;(void)data;g_assert_cmpstr(method,==,"GetManagedObjects");
 GVariantBuilder objects,interfaces,props,slots;
 g_variant_builder_init(&objects,G_VARIANT_TYPE("a{oa{sa{sv}}}"));
 g_variant_builder_init(&interfaces,G_VARIANT_TYPE("a{sa{sv}}"));g_variant_builder_init(&props,G_VARIANT_TYPE("a{sv}"));g_variant_builder_init(&slots,G_VARIANT_TYPE("ao"));
 gint m=g_atomic_int_get(&mode);
 if(m==4)g_variant_builder_add(&slots,"o","/org/freedesktop/ModemManager1/SIM/99");
 else if(m!=5){g_variant_builder_add(&slots,"o",SIM0);if(m==2)g_variant_builder_add(&slots,"o",SIM0);if(m==3)g_variant_builder_add(&slots,"o",SIM1);}
 else g_variant_builder_add(&slots,"o","/");
 g_variant_builder_add(&props,"{sv}","SimSlots",g_variant_builder_end(&slots));
 g_variant_builder_add(&props,"{sv}","Sim",g_variant_new_object_path(m==5?"/":m==4?"/org/freedesktop/ModemManager1/SIM/99":SIM0));
 g_variant_builder_add(&interfaces,"{s@a{sv}}","org.freedesktop.ModemManager1.Modem",g_variant_builder_end(&props));
 g_variant_builder_add(&objects,"{o@a{sa{sv}}}","/org/freedesktop/ModemManager1/Modem/0",g_variant_builder_end(&interfaces));
 /* A decoy SIM in ObjectManager must not become an unadvertised native path. */
 if(m==5){
  g_variant_builder_init(&interfaces,G_VARIANT_TYPE("a{sa{sv}}"));g_variant_builder_init(&props,G_VARIANT_TYPE("a{sv}"));
  g_variant_builder_add(&props,"{sv}","SimType",g_variant_new_uint32(2));g_variant_builder_add(&props,"{sv}","Eid",g_variant_new_string("12345678901234567890123456789012"));
  g_variant_builder_add(&interfaces,"{s@a{sv}}","org.freedesktop.ModemManager1.Sim",g_variant_builder_end(&props));
  g_variant_builder_add(&objects,"{o@a{sa{sv}}}",SIM1,g_variant_builder_end(&interfaces));
 }
 g_dbus_method_invocation_return_value(inv,g_variant_new("(@a{oa{sa{sv}}})",g_variant_builder_end(&objects)));
}
static GVariant *sim_property(GDBusConnection *b,const gchar *sender,const gchar *path,const gchar *iface,const gchar *property,GError **e,gpointer data){
 (void)b;(void)sender;(void)path;(void)iface;(void)e;(void)data;g_atomic_int_inc(&properties_seen);
 if(g_atomic_int_get(&mode)==7 && g_atomic_int_compare_and_exchange(&turned,0,1)){name_call(server,"ReleaseName");name_call(replacement,"RequestName");}
 if(g_str_equal(property,"Active"))return g_variant_new_boolean(FALSE);
 if(g_str_equal(property,"SimType"))return g_variant_new_uint32(2);
 if(g_str_equal(property,"Eid"))return g_variant_new_string(g_atomic_int_get(&mode)==6?"invalid":"12345678901234567890123456789012");
 g_assert_not_reached();
}
static void sim_call(GDBusConnection *b,const gchar *sender,const gchar *path,const gchar *iface,const gchar *method,GVariant *params,GDBusMethodInvocation *inv,gpointer data){
 (void)b;(void)sender;(void)iface;(void)params;(void)data;g_assert_cmpstr(method,==,"OpenLumaProfileTransport");g_assert_cmpstr(path,==,SIM0);g_atomic_int_inc(&opens);
 /* Discovery must reach this actual native dispatch. No FD/APDU is supplied. */
 g_dbus_method_invocation_return_dbus_error(inv,"org.projectluma.Tests.NativeSelected","direct SIM selected");
}
static gpointer server_main(gpointer data){
 (void)data;GMainContext *context=g_main_context_new();g_main_context_push_thread_default(context);loop=g_main_loop_new(context,FALSE);server=new_bus();
 GError *e=NULL;GDBusNodeInfo *manager=g_dbus_node_info_new_for_xml("<node><interface name='org.freedesktop.DBus.ObjectManager'><method name='GetManagedObjects'><arg type='a{oa{sa{sv}}}' direction='out'/></method></interface></node>",&e);g_assert_no_error(e);
 GDBusNodeInfo *sim=g_dbus_node_info_new_for_xml("<node><interface name='org.freedesktop.ModemManager1.Sim'><property name='Active' type='b' access='read'/><property name='SimType' type='u' access='read'/><property name='Eid' type='s' access='read'/><method name='OpenLumaProfileTransport'><arg type='s' direction='in'/><arg type='s' direction='in'/><arg type='h' direction='out'/><arg type='s' direction='out'/></method></interface></node>",&e);g_assert_no_error(e);
 const GDBusInterfaceVTable mv={.method_call=manager_call},sv={.method_call=sim_call,.get_property=sim_property};
 guint m=g_dbus_connection_register_object(server,"/org/freedesktop/ModemManager1",manager->interfaces[0],&mv,NULL,NULL,&e);g_assert_no_error(e);g_assert_cmpuint(m,>,0);
 guint s0=g_dbus_connection_register_object(server,SIM0,sim->interfaces[0],&sv,NULL,NULL,&e);g_assert_no_error(e);g_assert_cmpuint(s0,>,0);
 guint s1=g_dbus_connection_register_object(server,SIM1,sim->interfaces[0],&sv,NULL,NULL,&e);g_assert_no_error(e);g_assert_cmpuint(s1,>,0);
 name_call(server,"RequestName");g_mutex_lock(&ready_mutex);ready=TRUE;g_cond_signal(&ready_cond);g_mutex_unlock(&ready_mutex);g_main_loop_run(loop);
 g_dbus_connection_unregister_object(server,s1);g_dbus_connection_unregister_object(server,s0);g_dbus_connection_unregister_object(server,m);
 g_assert_true(g_dbus_connection_close_sync(server,NULL,&e));g_assert_no_error(e);g_clear_object(&server);g_dbus_node_info_unref(sim);g_dbus_node_info_unref(manager);g_main_loop_unref(loop);g_main_context_pop_thread_default(context);g_main_context_unref(context);return NULL;
}
int main(void){
 replacement=new_bus();GThread *thread=g_thread_new("native-direct-sim",server_main,NULL);g_mutex_lock(&ready_mutex);while(!ready)g_cond_wait(&ready_cond,&ready_mutex);g_mutex_unlock(&ready_mutex);
 GDBusConnection *client=new_bus();const gchar *caller=g_dbus_connection_get_unique_name(client);
 for(gint m=1;m<=7;m++){
  g_atomic_int_set(&mode,m);g_atomic_int_set(&opens,0);g_atomic_int_set(&properties_seen,0);GError *e=NULL;
  LumaMmTransport *t=luma_mm_transport_open_current(client,"0123456789abcdef0123456789abcdef",caller,&e);g_assert_null(t);
  g_assert_cmpint(g_atomic_int_get(&opens),==,(m==1||m==2)?1:0);
  if(m==1||m==2){g_assert_nonnull(e);gchar *remote=g_dbus_error_get_remote_error(e);g_assert_cmpstr(remote,==,"org.projectluma.Tests.NativeSelected");g_free(remote);}
  if(m==5)g_assert_cmpint(g_atomic_int_get(&properties_seen),==,0);
  g_clear_error(&e);g_print("direct SIM discovery control %d PASS\n",m);
 }
 g_main_loop_quit(loop);g_thread_join(thread);GError *e=NULL;g_assert_true(g_dbus_connection_close_sync(client,NULL,&e));g_assert_no_error(e);g_object_unref(client);g_assert_true(g_dbus_connection_close_sync(replacement,NULL,&e));g_assert_no_error(e);g_object_unref(replacement);return 0;
}
