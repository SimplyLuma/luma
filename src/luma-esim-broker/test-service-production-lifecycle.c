/* SPDX-License-Identifier: Apache-2.0 */
/* Actual broker worker/D-Bus/cancellation code, private bus and controlled LPAC card.
 * No production bus, hardware, activation secret or /usr/bin/lpac invocation. */
#define LUMA_NATIVE_ESIM_PRODUCTION_OWNER 1
#ifdef LUMA_TEST_TITAN_BACKEND
#define luma_esim_titan_backend fixture_titan_backend
#endif
#define luma_esim_lpac_bound fixture_lpac_bound
#define main esim_service_main_not_called
#define luma_esim_lpac_owned fixture_lpac
#include "service.c"
#undef luma_esim_lpac_owned
#undef main
#undef luma_esim_lpac_bound

#ifdef LUMA_TEST_TITAN_BACKEND
gboolean fixture_titan_backend(void){return TRUE;}
#endif
static gint prepared, preparations;
static guint notification_attempts, pending_notices;
static gboolean notification_failure;
static gboolean prepared_enabled;
static gboolean deny_prepare, cancel_during_prepare;
static guint64 fixture_epoch=1,fixture_generation=1;
static gboolean card_deleted;
static gint block_mutation, mutation_started, cancellation_seen;
static gboolean card_enabled;
static char *card_nickname;
static const char *deny_sender;
static guint changes_seen;
static gboolean hold_authorization;
static GDBusMethodInvocation *pending_authorization;
static char *pending_authorization_id, *pending_authorization_caller;
static guint authorization_cancellations;
static gint lease_held, lease_acquisitions, lease_releases;
static char *lease_token;

gboolean fixture_lpac(GDBusConnection *native_bus,const char *token,const char *ims_owner,const char *op,const char *nickname,int input,GCancellable *cancel,guint8 **output,gsize *size,GError **error) {
  (void)native_bus;(void)token;(void)ims_owner;
 g_assert_cmpint(g_atomic_int_get(&lease_held),==,1);
 *output=NULL;*size=0;
 if(g_str_equal(op,"notification-list")){
  g_assert_cmpint(input,==,-1);g_assert_null(nickname);
  const char *json=pending_notices==2?"{\"payload\":{\"code\":0,\"data\":[{\"seqNumber\":2},{\"seqNumber\":1}]}}":pending_notices==1?"{\"payload\":{\"code\":0,\"data\":[{\"seqNumber\":2}]}}":"{\"payload\":{\"code\":0,\"data\":[]}}";
  *output=(guint8*)g_strdup(json);*size=strlen(json);return TRUE;
 }
 if(g_str_equal(op,"notification-process")){
  g_assert_cmpint(input,==,-1);g_assert_cmpuint(pending_notices,>,0);
  g_assert_cmpstr(nickname,==,pending_notices==2?"1":"2");notification_attempts++;
  if(notification_failure){g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_FAILED,"Controlled carrier unavailable");return FALSE;}
  pending_notices--;*output=(guint8*)g_strdup("{\"payload\":{\"code\":0,\"data\":null}}");*size=strlen((char*)*output);return TRUE;
 }
 if(input>=0){char id[64];ssize_t n=read(input,id,sizeof id);close(input);g_assert_cmpint(n,==,20);g_assert_cmpmem(id,n,"1234567890123456789\n",20);}
 if(!g_str_equal(op,"list")){
#ifdef LUMA_TEST_TITAN_BACKEND
  if(g_str_equal(op,"enable")||g_str_equal(op,"disable")){
    g_assert_cmpint(g_atomic_int_get(&prepared),==,1);
    g_assert_cmpint(prepared_enabled,==,g_str_equal(op,"enable"));
  }
#endif
  if(g_str_equal(op,"enable"))card_enabled=TRUE;
  else if(g_str_equal(op,"disable"))card_enabled=FALSE;
  else if(g_str_equal(op,"nickname")){g_free(card_nickname);card_nickname=g_strdup(nickname);}
  else if(g_str_equal(op,"delete"))card_deleted=TRUE;
  else g_assert_not_reached();
  pending_notices=2;
  g_atomic_int_inc(&mutation_started);
  if(g_atomic_int_get(&block_mutation)){
   gint64 until=g_get_monotonic_time()+5*G_TIME_SPAN_SECOND;
   while(!g_cancellable_is_cancelled(cancel) && g_get_monotonic_time()<until)g_usleep(1000);
   g_assert_true(g_cancellable_is_cancelled(cancel));g_atomic_int_inc(&cancellation_seen);
   g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_CANCELLED,"Controlled card committed before cancellation");return FALSE;
  }
 }
 g_autofree char *json=g_strdup_printf("{\"payload\":{\"code\":0,\"data\":[{\"iccid\":\"1234567890123456789\",\"profileState\":\"%s\",\"profileName\":\"Plan\",\"profileNickname\":\"%s\"}]}}",card_enabled?"enabled":"disabled",card_nickname?card_nickname:"");
 if(card_deleted){*size=strlen("{\"payload\":{\"code\":0,\"data\":[]}}");*output=(guint8*)g_strdup("{\"payload\":{\"code\":0,\"data\":[]}}");}
 else {*size=strlen(json);*output=(guint8*)g_strdup(json);}return TRUE;
}
gboolean fixture_lpac_bound(GDBusConnection *native_bus,const char *token,const char *ims_owner,const char *op,const char *nickname,int input,GCancellable *cancel,guint8 **output,gsize *size,const LumaMmBinding *expected,LumaMmBinding **observed,GError **error){
 if(observed)*observed=NULL;
 if(!g_str_equal(op,"list")){
  g_assert_nonnull(expected);g_assert_cmpuint(expected->epoch,==,fixture_epoch);
  g_assert_cmpstr(expected->owner,==,g_dbus_connection_get_unique_name(native_bus));g_assert_cmpstr(expected->sim,==,"/org/freedesktop/ModemManager1/SIM/0");
 }
 gboolean ok=fixture_lpac(native_bus,token,ims_owner,op,nickname,input,cancel,output,size,error);
 if(ok&&observed){
  LumaMmBinding *n=g_new0(LumaMmBinding,1);n->owner=g_strdup(g_dbus_connection_get_unique_name(native_bus));n->sim=g_strdup("/org/freedesktop/ModemManager1/SIM/0");n->handle=g_uuid_string_random();n->generation=fixture_generation++;n->epoch=fixture_epoch;*observed=n;
 }
 return ok;
}
static void policy(GDBusConnection *c,const char *sender,const char *path,const char *iface,const char *name,GVariant *parameters,GDBusMethodInvocation *call,gpointer data) {
 (void)c;(void)path;(void)iface;(void)data;
 if(g_str_equal(name,"CancelCheckAuthorization")){
  const char *id=NULL;g_variant_get(parameters,"(&s)",&id);
  g_assert_nonnull(pending_authorization);
  g_assert_cmpstr(id,==,pending_authorization_id);
  g_assert_cmpstr(sender,==,pending_authorization_caller);
  g_dbus_method_invocation_return_dbus_error(pending_authorization,"org.freedesktop.PolicyKit1.Error.Cancelled","Controlled prompt cancelled");
  g_clear_object(&pending_authorization);g_clear_pointer(&pending_authorization_id,g_free);g_clear_pointer(&pending_authorization_caller,g_free);
  authorization_cancellations++;
  g_dbus_method_invocation_return_value(call,NULL);return;
 }
 if(hold_authorization){
  g_assert_null(pending_authorization);
  g_autoptr(GVariant) id=g_variant_get_child_value(parameters,4);
  pending_authorization_id=g_variant_dup_string(id,NULL);
  pending_authorization_caller=g_strdup(sender);
  pending_authorization=g_object_ref(call);return;
 }
 g_autoptr(GVariant) subject=g_variant_get_child_value(parameters,0);g_autoptr(GVariant) details=g_variant_get_child_value(subject,1);const char *unique=NULL;
 g_assert_true(g_variant_lookup(details,"name","&s",&unique));g_assert_true(g_dbus_is_unique_name(unique));
 GVariantBuilder extra;g_variant_builder_init(&extra,G_VARIANT_TYPE("a{ss}"));
 g_dbus_method_invocation_return_value(call,g_variant_new("((bb@a{ss}))",!deny_sender || !g_str_equal(deny_sender,unique),FALSE,g_variant_builder_end(&extra)));
}
static void calls(GDBusConnection *c,const char *sender,const char *path,const char *iface,const char *name,GVariant *parameters,GDBusMethodInvocation *call,gpointer data) {
 (void)c;(void)sender;(void)path;(void)iface;(void)data;
 if(g_str_equal(name,"PrepareESimProfileChange")){
  const char *token=NULL;gboolean enabled=FALSE;g_variant_get(parameters,"(&sb)",&token,&enabled);
  g_assert_cmpint(g_atomic_int_get(&lease_held),==,1);g_assert_cmpstr(token,==,lease_token);
  gboolean accepted=!deny_prepare;
  if(accepted){g_atomic_int_set(&prepared,1);prepared_enabled=enabled;g_atomic_int_inc(&preparations);}
  if(accepted&&cancel_during_prepare)g_cancellable_cancel(current_job->cancel);
  g_dbus_method_invocation_return_value(call,g_variant_new("(b)",accepted));return;
 }
 if(g_str_equal(name,"AcquireESimReservation") || g_str_equal(name,"ReleaseESimReservation")){
  const char *token=NULL;g_variant_get(parameters,"(&s)",&token);g_assert_cmpuint(strlen(token),==,32);
  gboolean accepted=FALSE;
  if(g_str_equal(name,"AcquireESimReservation")){
   accepted=!g_atomic_int_get(&lease_held);
   if(accepted){g_free(lease_token);lease_token=g_strdup(token);g_atomic_int_set(&lease_held,1);g_atomic_int_inc(&lease_acquisitions);}
  }else{
   accepted=g_atomic_int_get(&lease_held) && g_strcmp0(token,lease_token)==0;
   if(accepted){g_atomic_int_set(&lease_held,0);g_atomic_int_set(&prepared,0);g_atomic_int_inc(&lease_releases);}
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
static void spin_count(gint *value,gint count) {
 gint64 end=g_get_monotonic_time()+5*G_TIME_SPAN_SECOND;
 while(g_atomic_int_get(value)<count && g_get_monotonic_time()<end){g_main_context_iteration(NULL,FALSE);g_usleep(1000);}g_assert_cmpint(g_atomic_int_get(value),>=,count);
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
 static const char polkit_xml[]="<node><interface name='org.freedesktop.PolicyKit1.Authority'><method name='CheckAuthorization'><arg type='(sa{sv})' direction='in'/><arg type='s' direction='in'/><arg type='a{ss}' direction='in'/><arg type='u' direction='in'/><arg type='s' direction='in'/><arg type='(bba{ss})' direction='out'/></method><method name='CancelCheckAuthorization'><arg type='s' direction='in'/></method></interface></node>";
 static const char ims_xml[]="<node><interface name='net.catcrafts.IMS1'><method name='GetCalls'><arg type='aa{sv}' direction='out'/></method><method name='AcquireESimReservation'><arg type='s' direction='in'/><arg type='b' direction='out'/></method><method name='PrepareESimProfileChange'><arg type='s' direction='in'/><arg type='b' direction='in'/><arg type='b' direction='out'/></method><method name='ReleaseESimReservation'><arg type='s' direction='in'/><arg type='b' direction='out'/></method></interface></node>";
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
 listed_ids(b,&bg,&bh);g_assert_cmpuint(notification_attempts,==,0);
 deny_sender=g_dbus_connection_get_unique_name(third);Answer denied={0};invoke(third,"ListProfiles",NULL,&denied);spin(&denied.done);g_assert_nonnull(denied.error);answer_clear(&denied);deny_sender=NULL;
 /* Cancelling a request or reaching its deadline must dismiss the outstanding
  * policy challenge before another operation starts. Neither can reach LPAC. */
 guint leases_before_auth=g_atomic_int_get(&lease_acquisitions);
 for(guint mode=0;mode<2;mode++){
  hold_authorization=TRUE;Answer waiting={0};
  invoke(a,"Rename",g_variant_new("(sss)",ag,ah,"Never applied"),&waiting);
  gint64 until=g_get_monotonic_time()+5*G_TIME_SPAN_SECOND;
  while(!pending_authorization&&g_get_monotonic_time()<until){g_main_context_iteration(NULL,FALSE);g_usleep(1000);}
  g_assert_nonnull(pending_authorization);
  if(mode==0){
   Answer stopped={0};invoke(a,"CancelOperation",NULL,&stopped);spin(&stopped.done);g_assert_no_error(stopped.error);answer_clear(&stopped);
  }else{
   g_assert_nonnull(current_job);g_source_remove(current_job->timer);current_job->timer=0;deadline(current_job);
  }
  spin(&waiting.done);g_assert_nonnull(waiting.error);answer_clear(&waiting);drain();
  g_assert_null(pending_authorization);
  g_assert_cmpuint(authorization_cancellations,==,mode+1);
  g_assert_cmpint(g_atomic_int_get(&mutation_started),==,0);
  g_assert_cmpuint(g_atomic_int_get(&lease_acquisitions),==,leases_before_auth);
  g_assert_null(current_job);g_assert_false(busy);hold_authorization=FALSE;
 }
 /* A's snapshot survives B's read and unrelated authorization failure. */
 Answer rename={0};invoke(a,"Rename",g_variant_new("(sss)",ag,ah,"Work"),&rename);spin(&rename.done);g_assert_no_error(rename.error);g_assert_nonnull(rename.reply);answer_clear(&rename);drain();g_assert_cmpuint(changes_seen,==,1);
 g_assert_cmpuint(notification_attempts,==,2);
 Answer stale={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",bg,bh,TRUE),&stale);spin(&stale.done);g_assert_nonnull(stale.error);answer_clear(&stale);
 g_clear_pointer(&ag,g_free);g_clear_pointer(&ah,g_free);listed_ids(a,&ag,&ah);g_atomic_int_set(&block_mutation,1);
 Answer mutation={0};invoke(a,"SetEnabled",g_variant_new("(ssb)",ag,ah,TRUE),&mutation);spin_count(&mutation_started,2);
 Answer wrong_cancel={0};invoke(b,"CancelOperation",NULL,&wrong_cancel);spin(&wrong_cancel.done);g_assert_nonnull(wrong_cancel.error);g_autofree char *remote=g_dbus_error_get_remote_error(wrong_cancel.error);g_assert_cmpstr(remote,==,"org.projectluma.ESim1.NotOwner");answer_clear(&wrong_cancel);g_assert_cmpint(g_atomic_int_get(&cancellation_seen),==,0);
 Answer cancel={0};invoke(a,"CancelOperation",NULL,&cancel);spin(&cancel.done);g_assert_no_error(cancel.error);answer_clear(&cancel);spin(&mutation.done);g_assert_nonnull(mutation.error);g_assert_nonnull(strstr(mutation.error->message,"may already have changed"));answer_clear(&mutation);drain();g_assert_cmpuint(changes_seen,==,2);g_assert_true(card_enabled);
 g_atomic_int_set(&block_mutation,0);g_clear_pointer(&ag,g_free);g_clear_pointer(&ah,g_free);listed_ids(a,&ag,&ah);
 g_atomic_int_set(&block_mutation,1);Answer disconnect={0};invoke(a,"SetEnabled",g_variant_new("(ssb)",ag,ah,FALSE),&disconnect);spin_count(&mutation_started,3);
 g_assert_true(g_dbus_connection_close_sync(a,NULL,&error));g_assert_no_error(error);spin_count(&cancellation_seen,2);spin(&disconnect.done);answer_clear(&disconnect);drain();g_assert_null(current_job);g_assert_false(busy);g_assert_cmpuint(changes_seen,==,3);
 g_assert_null(luma_esim_cache_get(&cache,g_dbus_connection_get_unique_name(a)));
 g_atomic_int_set(&block_mutation,0);g_clear_pointer(&bg,g_free);g_clear_pointer(&bh,g_free);listed_ids(b,&bg,&bh);g_assert_false(card_enabled);
 /* An external card change is first observed by a mutation, not List. */
 card_enabled=TRUE;Answer external_stale={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",bg,bh,FALSE),&external_stale);spin(&external_stale.done);g_assert_nonnull(external_stale.error);answer_clear(&external_stale);drain();g_assert_cmpuint(changes_seen,==,4);g_assert_null(luma_esim_cache_get(&cache,g_dbus_connection_get_unique_name(b)));
 g_autofree char *new_generation=NULL,*new_handle=NULL;listed_ids(third,&new_generation,&new_handle);drain();g_assert_cmpuint(changes_seen,==,4);
 /* A fabricated handle on an unchanged card does not dirty another client. */
 g_clear_pointer(&bg,g_free);g_clear_pointer(&bh,g_free);listed_ids(b,&bg,&bh);Answer wrong_handle={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",bg,"wrong",FALSE),&wrong_handle);spin(&wrong_handle.done);g_assert_nonnull(wrong_handle.error);answer_clear(&wrong_handle);drain();g_assert_cmpuint(changes_seen,==,4);LumaEsimSnapshot *still_current=luma_esim_cache_get(&cache,g_dbus_connection_get_unique_name(third));g_assert_nonnull(still_current);luma_esim_snapshot_unref(still_current);
 /* Native epoch changes while the public profile snapshot is byte-identical.
  * Old handles refuse before first mutation; refresh issues a new generation. */
 guint mutations_before=g_atomic_int_get(&mutation_started);fixture_epoch++;
 Answer epoch_stale={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",bg,bh,FALSE),&epoch_stale);spin(&epoch_stale.done);g_assert_nonnull(epoch_stale.error);answer_clear(&epoch_stale);g_assert_cmpuint(g_atomic_int_get(&mutation_started),==,mutations_before);
 g_autofree char *epoch_generation=NULL,*epoch_handle=NULL;listed_ids(b,&epoch_generation,&epoch_handle);g_assert_cmpstr(epoch_generation,!=,bg);
#ifdef LUMA_TEST_TITAN_BACKEND
 deny_prepare=TRUE;
 Answer refused_prepare={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",epoch_generation,epoch_handle,FALSE),&refused_prepare);spin(&refused_prepare.done);
 g_assert_nonnull(refused_prepare.error);answer_clear(&refused_prepare);
 g_assert_cmpuint(g_atomic_int_get(&mutation_started),==,mutations_before);
 g_assert_cmpint(g_atomic_int_get(&lease_held),==,0);g_assert_true(card_enabled);
 deny_prepare=FALSE;
 cancel_during_prepare=TRUE;
 Answer cancelled_prepare={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",epoch_generation,epoch_handle,FALSE),&cancelled_prepare);spin(&cancelled_prepare.done);
 g_assert_nonnull(cancelled_prepare.error);answer_clear(&cancelled_prepare);
 g_assert_cmpuint(g_atomic_int_get(&mutation_started),==,mutations_before);
 g_assert_cmpint(g_atomic_int_get(&lease_held),==,0);g_assert_true(card_enabled);
 cancel_during_prepare=FALSE;
#endif
 Answer disable={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",epoch_generation,epoch_handle,FALSE),&disable);spin(&disable.done);g_assert_no_error(disable.error);answer_clear(&disable);g_assert_false(card_enabled);
 g_clear_pointer(&epoch_generation,g_free);g_clear_pointer(&epoch_handle,g_free);listed_ids(b,&epoch_generation,&epoch_handle);
 /* Carrier failure must not pretend the card change failed or report success.
  * The next authorized action retries the durable queue; reads do not. */
 guint notices_before=notification_attempts;notification_failure=TRUE;
 Answer enable={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",epoch_generation,epoch_handle,TRUE),&enable);spin(&enable.done);
 g_assert_nonnull(enable.error);g_assert_nonnull(strstr(enable.error->message,"carrier notification is still pending"));
 answer_clear(&enable);g_assert_true(card_enabled);g_assert_cmpuint(notification_attempts,==,notices_before+1);g_assert_cmpuint(pending_notices,==,2);
 g_clear_pointer(&epoch_generation,g_free);g_clear_pointer(&epoch_handle,g_free);listed_ids(b,&epoch_generation,&epoch_handle);
 g_assert_cmpuint(notification_attempts,==,notices_before+1);notification_failure=FALSE;
 invoke(b,"SetEnabled",g_variant_new("(ssb)",epoch_generation,epoch_handle,TRUE),&enable);spin(&enable.done);
 g_assert_no_error(enable.error);answer_clear(&enable);g_assert_true(card_enabled);
 g_assert_cmpuint(notification_attempts,==,notices_before+3);g_assert_cmpuint(pending_notices,==,0);
 g_clear_pointer(&epoch_generation,g_free);g_clear_pointer(&epoch_handle,g_free);listed_ids(b,&epoch_generation,&epoch_handle);
 Answer enabled_delete={0};invoke(b,"Delete",g_variant_new("(ssb)",epoch_generation,epoch_handle,TRUE),&enabled_delete);spin(&enabled_delete.done);g_assert_nonnull(enabled_delete.error);answer_clear(&enabled_delete);g_assert_false(card_deleted);
 Answer disable_again={0};invoke(b,"SetEnabled",g_variant_new("(ssb)",epoch_generation,epoch_handle,FALSE),&disable_again);spin(&disable_again.done);g_assert_no_error(disable_again.error);answer_clear(&disable_again);
 g_clear_pointer(&epoch_generation,g_free);g_clear_pointer(&epoch_handle,g_free);listed_ids(b,&epoch_generation,&epoch_handle);
 Answer delete_unconfirmed={0};invoke(b,"Delete",g_variant_new("(ssb)",epoch_generation,epoch_handle,FALSE),&delete_unconfirmed);spin(&delete_unconfirmed.done);g_assert_nonnull(delete_unconfirmed.error);answer_clear(&delete_unconfirmed);g_assert_false(card_deleted);
 Answer delete={0};invoke(b,"Delete",g_variant_new("(ssb)",epoch_generation,epoch_handle,TRUE),&delete);spin(&delete.done);g_assert_no_error(delete.error);answer_clear(&delete);g_assert_true(card_deleted);
#ifdef LUMA_TEST_TITAN_BACKEND
 g_assert_cmpint(g_atomic_int_get(&preparations),>,0);
#else
 g_assert_cmpint(g_atomic_int_get(&preparations),==,0);
#endif
 profile_native_clear(NULL);
 g_assert_cmpint(g_atomic_int_get(&lease_held),==,0);g_assert_cmpint(g_atomic_int_get(&lease_acquisitions),>,0);g_assert_cmpint(g_atomic_int_get(&lease_acquisitions),==,g_atomic_int_get(&lease_releases));g_clear_pointer(&lease_token,g_free);
 g_dbus_connection_signal_unsubscribe(b,change);g_dbus_connection_signal_unsubscribe(bus,loss);g_dbus_connection_unregister_object(bus,object);g_dbus_connection_unregister_object(bus,pk_object);g_dbus_connection_unregister_object(bus,ims_object);luma_esim_cache_clear(&cache);
 g_assert_true(g_dbus_connection_close_sync(b,NULL,&error));g_assert_no_error(error);g_assert_true(g_dbus_connection_close_sync(third,NULL,&error));g_assert_no_error(error);g_assert_true(g_dbus_connection_close_sync(bus,NULL,&error));g_assert_no_error(error);g_clear_object(&bus);g_clear_pointer(&card_nickname,g_free);
}
int main(int argc,char **argv){g_test_init(&argc,&argv,NULL);g_test_add_func("/esim/private-bus/actual-production-profile-management",service_lifecycle);return g_test_run();}
