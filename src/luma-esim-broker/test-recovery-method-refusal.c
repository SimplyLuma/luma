/* SPDX-License-Identifier: Apache-2.0 */
#define main esim_service_main_not_called
#define luma_esim_lpac_owned fixture_unreachable_lpac
#include "service.c"
#undef main
#undef luma_esim_lpac_owned
gboolean fixture_unreachable_lpac(GDBusConnection *b,const char *token,const char *ims,const char *op,const char *nickname,int fd,GCancellable *cancel,guint8 **out,gsize *size,GError **error){
 (void)b;(void)token;(void)ims;(void)op;(void)nickname;(void)fd;(void)cancel;(void)out;(void)size;(void)error;g_assert_not_reached();return FALSE;
}
typedef struct{gboolean done;GVariant *reply;GError *error;} Answer;
static void answered(GObject *source,GAsyncResult *res,gpointer data){Answer *a=data;a->reply=g_dbus_connection_call_finish(G_DBUS_CONNECTION(source),res,&a->error);a->done=TRUE;}
static GDBusConnection *connection(void){GError *error=NULL;GDBusConnection *c=g_dbus_connection_new_for_address_sync(g_getenv("DBUS_SESSION_BUS_ADDRESS"),G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT|G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION,NULL,NULL,&error);g_assert_no_error(error);return c;}
int main(void){
 GError *error=NULL;bus=connection();GDBusConnection *client=connection();char *xml=NULL;g_assert_true(g_file_get_contents(ESIM_TEST_XML,&xml,NULL,&error));g_assert_no_error(error);GDBusNodeInfo *node=g_dbus_node_info_new_for_xml(xml,&error);g_free(xml);g_assert_no_error(error);guint id=g_dbus_connection_register_object(bus,OBJECT,node->interfaces[0],&vtable,NULL,NULL,&error);g_assert_no_error(error);
 GVariant *owned=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","RequestName",g_variant_new("(su)",BUS,4u),G_VARIANT_TYPE("(u)"),0,5000,NULL,&error);g_assert_no_error(error);guint status=0;g_variant_get(owned,"(u)",&status);g_assert_cmpuint(status,==,1);g_variant_unref(owned);
 const char *methods[]={"CheckNativeUncertainOpenRecovery","CheckNativeCandidateBinding"};
 for(guint i=0;i<G_N_ELEMENTS(methods);i++){
 Answer a={0};g_dbus_connection_call(client,BUS,OBJECT,BUS,methods[i],NULL,NULL,G_DBUS_CALL_FLAGS_NONE,3000,NULL,answered,&a);gint64 deadline=g_get_monotonic_time()+5*G_USEC_PER_SEC;while(!a.done&&g_get_monotonic_time()<deadline){g_main_context_iteration(NULL,FALSE);g_usleep(1000);}g_assert_true(a.done);g_assert_nonnull(a.error);char *name=g_dbus_error_get_remote_error(a.error);g_assert_cmpstr(name,==,"org.projectluma.ESim1.Unsupported");g_free(name);g_clear_error(&a.error);g_assert_null(a.reply);g_assert_false(busy);g_assert_null(current_job);
 }
 g_dbus_connection_unregister_object(bus,id);g_dbus_node_info_unref(node);g_assert_true(g_dbus_connection_close_sync(client,NULL,&error));g_assert_no_error(error);g_assert_true(g_dbus_connection_close_sync(bus,NULL,&error));g_assert_no_error(error);g_object_unref(client);g_clear_object(&bus);g_print("native recovery method explicit mode refusal PASS\n");return 0;
}
