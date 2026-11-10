/* SPDX-License-Identifier: Apache-2.0
 * Exact actual service main, private D-Bus, controlled privilege/XML filesystem.
 * This regression is source-entrypoint proof, not packaged/hardware proof.
 */
#define _GNU_SOURCE
#include <gio/gio.h>
#include <unistd.h>
#include <string.h>
static uid_t fixture_uid(void){return 0;}
static gboolean fixture_file(const gchar *,gchar **,gsize *,GError **);
static GDBusConnection *fixture_bus(GBusType,GCancellable *,GError **);
static void fixture_loop(GMainLoop *);
#define geteuid fixture_uid
#define g_file_get_contents fixture_file
#define g_bus_get_sync fixture_bus
#define g_main_loop_run fixture_loop
#define main actual_service_entry
#include "service.c"
#undef main
#undef g_main_loop_run
#undef g_bus_get_sync
#undef g_file_get_contents
#undef geteuid
static gboolean absent;
static gint introspected;
static gboolean fixture_file(const gchar *path,gchar **body,gsize *size,GError **error){
 g_assert_cmpstr(path,==,"/usr/share/luma-esim/org.projectluma.ESim1.xml");
 if(absent){g_set_error_literal(error,G_FILE_ERROR,G_FILE_ERROR_NOENT,"controlled installed XML absent");return FALSE;}
 return g_file_get_contents(ESIM_TEST_XML,body,size,error);
}
static GDBusConnection *fixture_bus(GBusType type,GCancellable *cancel,GError **error){
 g_assert_cmpint(type,==,G_BUS_TYPE_SYSTEM);return g_bus_get_sync(G_BUS_TYPE_SESSION,cancel,error);
}
static gpointer inspect(gpointer data){
 GMainLoop *loop=data;
 GError *error=NULL;
 gchar *address=g_dbus_address_get_for_bus_sync(G_BUS_TYPE_SESSION,NULL,&error);g_assert_no_error(error);
 GDBusConnection *client=g_dbus_connection_new_for_address_sync(address,G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT|G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION,NULL,NULL,&error);g_free(address);g_assert_no_error(error);
 gint64 until=g_get_monotonic_time()+4*G_TIME_SPAN_SECOND;GVariant *answer=NULL;
 while(!answer&&g_get_monotonic_time()<until){
  answer=g_dbus_connection_call_sync(client,"org.projectluma.ESim1","/org/projectluma/ESim1","org.freedesktop.DBus.Introspectable","Introspect",NULL,G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,500,NULL,NULL);
  if(!answer)g_usleep(10000);
 }
 if(answer){const gchar *xml=NULL;g_variant_get(answer,"(&s)",&xml);
  g_assert_nonnull(strstr(xml,"name=\"Download\""));g_assert_nonnull(strstr(xml,"name=\"ListProfiles\""));g_assert_nonnull(strstr(xml,"name=\"SetEnabled\""));
  g_atomic_int_set(&introspected,1);g_variant_unref(answer);
 }
 g_dbus_connection_close_sync(client,NULL,NULL);g_object_unref(client);g_main_loop_quit(loop);return NULL;
}
static void fixture_loop(GMainLoop *loop){
 GThread *thread=g_thread_new("actual-main-introspection",inspect,loop);g_main_loop_run(loop);g_thread_join(thread);g_assert_cmpint(g_atomic_int_get(&introspected),==,1);
}
int main(void){
 absent=TRUE;g_assert_cmpint(actual_service_entry(),==,1);
 absent=FALSE;g_assert_cmpint(actual_service_entry(),==,0);
 return 0;
}
