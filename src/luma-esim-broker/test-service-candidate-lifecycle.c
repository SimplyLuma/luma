/* SPDX-License-Identifier: Apache-2.0 */
/* Actual broker worker/D-Bus/cancellation code, private bus and controlled LPAC card.
 * No production bus, hardware, activation secret or /usr/bin/lpac invocation. */
#define LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY 1
#define luma_esim_lpac_bound fixture_lpac_bound
#define main esim_service_main_not_called
#define luma_esim_lpac_owned fixture_lpac
#include "service.c"
#undef luma_esim_lpac_owned
#undef main
#undef luma_esim_lpac_bound

static gint block_mutation, mutation_started, cancellation_seen;
static gboolean card_enabled;
static char *card_nickname;
static const char *deny_sender;
static guint changes_seen;
static gint lease_held, lease_acquisitions, lease_releases;
static char *lease_token;

gboolean fixture_lpac(GDBusConnection *native_bus,const char *token,const char *ims_owner,const char *op,const char *nickname,int input,GCancellable *cancel,guint8 **output,gsize *size,GError **error) {
  (void)native_bus;(void)token;(void)ims_owner;
 g_assert_cmpint(g_atomic_int_get(&lease_held),==,1);
 *output=NULL;*size=0;
 if(input>=0){char id[64];ssize_t n=read(input,id,sizeof id);close(input);g_assert_cmpint(n,==,20);g_assert_cmpmem(id,n,"1234567890123456789\n",20);}
 if(!g_str_equal(op,"list")){
  if(g_str_equal(op,"enable"))card_enabled=TRUE;
  else if(g_str_equal(op,"disable"))card_enabled=FALSE;
  else if(g_str_equal(op,"nickname")){g_free(card_nickname);card_nickname=g_strdup(nickname);}
  else g_assert_not_reached();
  g_atomic_int_inc(&mutation_started);
  if(g_atomic_int_get(&block_mutation)){
   gint64 until=g_get_monotonic_time()+5*G_TIME_SPAN_SECOND;
   while(!g_cancellable_is_cancelled(cancel) && g_get_monotonic_time()<until)g_usleep(1000);
   g_assert_true(g_cancellable_is_cancelled(cancel));g_atomic_int_inc(&cancellation_seen);
   g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_CANCELLED,"Controlled card committed before cancellation");return FALSE;
  }
 }
 g_autofree char *json=g_strdup_printf("{\"payload\":{\"code\":0,\"data\":[{\"iccid\":\"1234567890123456789\",\"profileState\":\"%s\",\"profileName\":\"Plan\",\"profileNickname\":\"%s\"}]}}",card_enabled?"enabled":"disabled",card_nickname?card_nickname:"");
 *size=strlen(json);*output=(guint8*)g_strdup(json);return TRUE;
}
gboolean fixture_lpac_bound(GDBusConnection *native_bus,const char *token,const char *ims_owner,const char *op,const char *nickname,int input,GCancellable *cancel,guint8 **output,gsize *size,const LumaMmBinding *expected,LumaMmBinding **observed,GError **error){
 (void)native_bus;(void)token;(void)ims_owner;(void)op;(void)nickname;(void)cancel;(void)expected;(void)error;
 if(input>=0) { close(input); }
 *output=NULL;*size=0;if(observed) { *observed=NULL; }
 g_assert_not_reached();return FALSE; /* No refused candidate mutation may reach LPAC. */
}
static void policy(GDBusConnection *c,const char *sender,const char *path,const char *iface,const char *name,GVariant *parameters,GDBusMethodInvocation *call,gpointer data) {
 (void)c;(void)sender;(void)path;(void)iface;(void)name;(void)data;
 g_autoptr(GVariant) subject=g_variant_get_child_value(parameters,0);g_autoptr(GVariant) details=g_variant_get_child_value(subject,1);const char *unique=NULL;
 g_assert_true(g_variant_lookup(details,"name","&s",&unique));g_assert_true(g_dbus_is_unique_name(unique));
 GVariantBuilder extra;g_variant_builder_init(&extra,G_VARIANT_TYPE("a{ss}"));
 g_dbus_method_invocation_return_value(call,g_variant_new("((bb@a{ss}))",!deny_sender || !g_str_equal(deny_sender,unique),FALSE,g_variant_builder_end(&extra)));
}
static void calls(GDBusConnection *c,const char *sender,const char *path,const char *iface,const char *name,GVariant *parameters,GDBusMethodInvocation *call,gpointer data) {
 (void)c;(void)sender;(void)path;(void)iface;(void)data;
 if(g_str_equal(name,"AcquireESimReservation") || g_str_equal(name,"ReleaseESimReservation")){
  const char *token=NULL;g_variant_get(parameters,"(&s)",&token);g_assert_cmpuint(strlen(token),==,32);
  gboolean accepted=FALSE;
  if(g_str_equal(name,"AcquireESimReservation")){
   accepted=!g_atomic_int_get(&lease_held);
   if(accepted){g_free(lease_token);lease_token=g_strdup(token);g_atomic_int_set(&lease_held,1);g_atomic_int_inc(&lease_acquisitions);}
  }else{
   accepted=g_atomic_int_get(&lease_held) && g_strcmp0(token,lease_token)==0;
   if(accepted){g_atomic_int_set(&lease_held,0);g_atomic_int_inc(&lease_releases);}
  }
  g_dbus_method_invocation_return_value(call,g_variant_new("(b)",accepted));return;
 }
 GVariantBuilder rows;g_variant_builder_init(&rows,G_VARIANT_TYPE("aa{sv}"));g_dbus_method_invocation_return_value(call,g_variant_new("(@aa{sv})",g_variant_builder_end(&rows)));
}
typedef struct {gboolean done;GVariant *reply;GError *error;} Answer;
static void answered(GObject *source,GAsyncResult *result,gpointer data) {
 Answer *a=data;a->reply=g_dbus_connection_call_finish(G_DBUS_CONNECTION(source),result,&a->error);a->done=TRUE;
}
static void spin(gboolean *done) {
 gint64 end=g_get_monotonic_time()+5*G_TIME_SPAN_SECOND;
 while(!*done && g_get_monotonic_time()<end){g_main_context_iteration(NULL,FALSE);g_usleep(1000);}g_assert_true(*done);
}

static void invoke(GDBusConnection *c,const char *method_name,GVariant *args,Answer *a) {
 g_dbus_connection_call(c,BUS,OBJECT,BUS,method_name,args,NULL,G_DBUS_CALL_FLAGS_NONE,5000,NULL,answered,a);
}
static void answer_clear(Answer *a){g_clear_pointer(&a->reply,g_variant_unref);g_clear_error(&a->error);memset(a,0,sizeof *a);}
static void listed_ids(GDBusConnection *c,char **generation,char **handle) {
 Answer a={0};invoke(c,"ListProfiles",NULL,&a);spin(&a.done);g_assert_no_error(a.error);g_assert_nonnull(a.reply);
 const char *g=NULL;GVariant *rows=NULL;g_variant_get(a.reply,"(&s@aa{sv})",&g,&rows);*generation=g_strdup(g);g_assert_cmpuint(g_variant_n_children(rows),==,1);
 g_autoptr(GVariant) row=g_variant_get_child_value(rows,0);const char *id=NULL;g_assert_true(g_variant_lookup(row,"id","&s",&id));*handle=g_strdup(id);g_variant_unref(rows);answer_clear(&a);
}
static GDBusConnection *connection(void) {
 g_autoptr(GError) error=NULL;const char *address=g_getenv("DBUS_SESSION_BUS_ADDRESS");g_assert_nonnull(address);
 GDBusConnection *c=g_dbus_connection_new_for_address_sync(address,G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT|G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION,NULL,NULL,&error);g_assert_no_error(error);g_assert_nonnull(c);return c;
}
static void request_name(const char *name) {
 g_autoptr(GError) error=NULL;g_autoptr(GVariant) reply=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","RequestName",g_variant_new("(su)",name,4u),G_VARIANT_TYPE("(u)"),0,5000,NULL,&error);g_assert_no_error(error);guint code=0;g_variant_get(reply,"(u)",&code);g_assert_cmpuint(code,==,1);
}
static void changed_signal(GDBusConnection *c,const char *sender,const char *path,const char *iface,const char *name,GVariant *args,gpointer data) {
 (void)c;(void)sender;(void)path;(void)iface;(void)name;(void)args;(void)data;changes_seen++;
}
static void drain(void){for(guint i=0;i<50;i++){g_main_context_iteration(NULL,FALSE);g_usleep(1000);}}
static void service_lifecycle(void) {
 static const char polkit_xml[]="<node><interface name='org.freedesktop.PolicyKit1.Authority'><method name='CheckAuthorization'><arg type='(sa{sv})' direction='in'/><arg type='s' direction='in'/><arg type='a{ss}' direction='in'/><arg type='u' direction='in'/><arg type='s' direction='in'/><arg type='(bba{ss})' direction='out'/></method></interface></node>";
 static const char ims_xml[]="<node><interface name='net.catcrafts.IMS1'><method name='GetCalls'><arg type='aa{sv}' direction='out'/></method><method name='AcquireESimReservation'><arg type='s' direction='in'/><arg type='b' direction='out'/></method><method name='ReleaseESimReservation'><arg type='s' direction='in'/><arg type='b' direction='out'/></method></interface></node>";
 g_autoptr(GError) error=NULL;bus=connection();g_autoptr(GDBusConnection) a=connection(),b=connection(),third=connection();
 g_autoptr(GDBusNodeInfo) pk=g_dbus_node_info_new_for_xml(polkit_xml,&error);g_assert_no_error(error);g_autoptr(GDBusNodeInfo) ims=g_dbus_node_info_new_for_xml(ims_xml,&error);g_assert_no_error(error);
 char *xml=NULL;g_assert_true(g_file_get_contents(ESIM_TEST_XML,&xml,NULL,&error));g_assert_no_error(error);g_autoptr(GDBusNodeInfo) node=g_dbus_node_info_new_for_xml(xml,&error);g_free(xml);g_assert_no_error(error);
 const GDBusInterfaceVTable pk_table={.method_call=policy},ims_table={.method_call=calls};
 guint object=g_dbus_connection_register_object(bus,OBJECT,node->interfaces[0],&vtable,NULL,NULL,&error);g_assert_no_error(error);
 guint pk_object=g_dbus_connection_register_object(bus,"/org/freedesktop/PolicyKit1/Authority",pk->interfaces[0],&pk_table,NULL,NULL,&error);g_assert_no_error(error);
 guint ims_object=g_dbus_connection_register_object(bus,"/net/catcrafts/IMS1",ims->interfaces[0],&ims_table,NULL,NULL,&error);g_assert_no_error(error);
 luma_esim_cache_init(&cache);request_name(BUS);request_name("org.freedesktop.PolicyKit1");request_name("net.catcrafts.IMS1");
 guint loss=g_dbus_connection_signal_subscribe(bus,"org.freedesktop.DBus","org.freedesktop.DBus","NameOwnerChanged","/org/freedesktop/DBus",NULL,0,sender_changed,NULL,NULL);
 guint change=g_dbus_connection_signal_subscribe(b,BUS,BUS,"ProfilesChanged",OBJECT,NULL,0,changed_signal,NULL,NULL);drain();
 g_autofree char *ag=NULL,*ah=NULL,*bg=NULL,*bh=NULL;listed_ids(a,&ag,&ah);
 g_autofree char *again_generation=NULL,*again_handle=NULL;listed_ids(a,&again_generation,&again_handle);g_assert_cmpstr(again_generation,==,ag);g_assert_cmpstr(again_handle,==,ah);
 listed_ids(b,&bg,&bh);
 deny_sender=g_dbus_connection_get_unique_name(third);Answer denied={0};invoke(third,"ListProfiles",NULL,&denied);spin(&denied.done);g_assert_nonnull(denied.error);answer_clear(&denied);deny_sender=NULL;
 /* The candidate has explicit bounded operations: read profiles, user download,
  * and separately authorized enable of that exact downloaded profile. These
  * unsupported/existing-plan mutations must never reach the controlled card. */
 gint before_mutations=g_atomic_int_get(&mutation_started);
 Answer rename={0};invoke(a,"Rename",g_variant_new("(sss)",ag,ah,"Work"),&rename);spin(&rename.done);g_assert_nonnull(rename.error);answer_clear(&rename);
 Answer remove={0};invoke(a,"Delete",g_variant_new("(ssb)",ag,ah,TRUE),&remove);spin(&remove.done);g_assert_nonnull(remove.error);answer_clear(&remove);
 Answer disable={0};invoke(a,"SetEnabled",g_variant_new("(ssb)",ag,ah,FALSE),&disable);spin(&disable.done);g_assert_nonnull(disable.error);answer_clear(&disable);
 Answer old_enable={0};invoke(a,"SetEnabled",g_variant_new("(ssb)",ag,ah,TRUE),&old_enable);spin(&old_enable.done);g_assert_nonnull(old_enable.error);answer_clear(&old_enable);
 drain();g_assert_cmpint(g_atomic_int_get(&mutation_started),==,before_mutations);g_assert_false(card_enabled);g_assert_cmpuint(changes_seen,==,0);g_assert_false(busy);g_assert_null(current_job);
 g_assert_cmpint(g_atomic_int_get(&lease_held),==,0);g_assert_cmpint(g_atomic_int_get(&lease_acquisitions),>,0);g_assert_cmpint(g_atomic_int_get(&lease_acquisitions),==,g_atomic_int_get(&lease_releases));g_clear_pointer(&lease_token,g_free);
 g_dbus_connection_signal_unsubscribe(b,change);g_dbus_connection_signal_unsubscribe(bus,loss);g_dbus_connection_unregister_object(bus,object);g_dbus_connection_unregister_object(bus,pk_object);g_dbus_connection_unregister_object(bus,ims_object);luma_esim_cache_clear(&cache);
 g_assert_true(g_dbus_connection_close_sync(b,NULL,&error));g_assert_no_error(error);g_assert_true(g_dbus_connection_close_sync(third,NULL,&error));g_assert_no_error(error);g_assert_true(g_dbus_connection_close_sync(bus,NULL,&error));g_assert_no_error(error);g_clear_object(&bus);g_clear_pointer(&card_nickname,g_free);
}
int main(int argc,char **argv){g_test_init(&argc,&argv,NULL);g_test_add_func("/esim/private-bus/candidate-operation-refusals",service_lifecycle);return g_test_run();}
