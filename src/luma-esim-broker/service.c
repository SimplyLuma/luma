/* SPDX-License-Identifier: Apache-2.0 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "profile-store.h"
#include "profile-cache.h"
#include "lpac-client.h"
#include "native-terminal-client.h"
#include <gio/gunixfdlist.h>
#include <sys/stat.h>
#include <sys/resource.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>

#if defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER) && defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY)
#error Production owner and nonshipping candidate are mutually exclusive
#endif
#define BUS "org.projectluma.ESim1"
#define OBJECT "/org/projectluma/ESim1"
static GDBusConnection *bus;
static LumaEsimCache cache;
static gboolean busy;
typedef struct {
  GDBusMethodInvocation *invocation;
  GVariant *parameters;
  GCancellable *cancel;
  char *method,*sender;
  char *ims_owner,*reservation_token;
  int input;
  guint timer;
  gint mutation_attempted;
  gint observed_card_changed;
} Job;
/* Main-context lifetime: cleared by finished before task data can be freed. */
static Job *current_job;
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
#include "profile-native-binding.h"
#endif
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
/* Exactly one verified user download may grant a later, separately authorized
 * Enable. No agent activation, arbitrary profile toggle, delete or wildcard.
 * Immutable snapshot and selector stay private; disconnect/MM turnover erases it. */
static GMutex candidate_enable_lock;
static gchar *candidate_enable_sender,*candidate_enable_selector,*candidate_enable_mm;
static LumaEsimSnapshot *candidate_enable_snapshot;
static LumaMmBinding *candidate_enable_binding;
static gint64 candidate_enable_until;
static void candidate_enable_clear_locked(void){
 g_clear_pointer(&candidate_enable_sender,g_free);
 g_clear_pointer(&candidate_enable_selector,g_free);
 g_clear_pointer(&candidate_enable_mm,g_free);
 g_clear_pointer(&candidate_enable_snapshot,luma_esim_snapshot_unref);
 g_clear_pointer(&candidate_enable_binding,luma_mm_binding_free);
 candidate_enable_until=0;
}
static gchar *candidate_mm_owner(void){
 GVariant *reply=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
  "org.freedesktop.DBus","GetNameOwner",g_variant_new("(s)","org.freedesktop.ModemManager1"),
  G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,NULL);
 const gchar *owner=NULL;gchar *copy=NULL;
 if(reply){g_variant_get(reply,"(&s)",&owner);if(g_dbus_is_unique_name(owner))copy=g_strdup(owner);g_variant_unref(reply);}
 return copy;
}
static LumaMmBinding *candidate_enable_take(Job *j,const gchar *selector,gboolean enable,const LumaEsimStore *current){
 gchar *owner=candidate_mm_owner();gboolean ok=FALSE;LumaMmBinding *binding=NULL;
 g_mutex_lock(&candidate_enable_lock);
 if(enable&&owner&&candidate_enable_binding&&candidate_enable_sender&&candidate_enable_selector&&candidate_enable_mm&&
    !g_strcmp0(j->sender,candidate_enable_sender)&&!g_strcmp0(selector,candidate_enable_selector)&&
    !g_strcmp0(owner,candidate_enable_mm)&&g_get_monotonic_time()<candidate_enable_until&&
    luma_esim_store_equal(luma_esim_snapshot_store(candidate_enable_snapshot),current))ok=TRUE;
 /* Mutation is single-use. A failed/uncertain attempt cannot silently retry. */
 if(ok){binding=candidate_enable_binding;candidate_enable_binding=NULL;candidate_enable_clear_locked();}
 g_mutex_unlock(&candidate_enable_lock);g_free(owner);return binding;
}
static void candidate_enable_issue(Job *j,const LumaEsimStore *before,LumaEsimStore *after,const gchar *original_owner,LumaMmBinding **binding){
 gchar *owner=candidate_mm_owner();const gchar *selector=NULL;guint i,added=0;
 if(!binding||!*binding||!owner||!original_owner||strcmp(owner,original_owner)||strcmp(owner,(*binding)->owner)){g_free(owner);return;}
 for(i=0;i<after->profiles->len;i++){
  LumaEsimProfile *p=g_ptr_array_index(after->profiles,i);gboolean existed=FALSE;guint k;
  for(k=0;k<before->profiles->len;k++)if(!strcmp(p->selector,((LumaEsimProfile*)g_ptr_array_index(before->profiles,k))->selector))existed=TRUE;
  if(!existed){if(p->enabled){g_free(owner);return;}selector=p->selector;added++;}
 }
 if(added!=1||!selector){g_free(owner);return;}
 g_mutex_lock(&candidate_enable_lock);candidate_enable_clear_locked();
 candidate_enable_sender=g_strdup(j->sender);candidate_enable_selector=g_strdup(selector);
 candidate_enable_binding=*binding;*binding=NULL;
 candidate_enable_mm=owner;candidate_enable_snapshot=luma_esim_snapshot_take(after);
 candidate_enable_until=g_get_monotonic_time()+5*60*G_USEC_PER_SEC;
 g_mutex_unlock(&candidate_enable_lock);
}
#endif
static gboolean cancel_owned_job(const char *sender) {
  if(!current_job || !g_str_equal(current_job->sender,sender))return FALSE;
  g_cancellable_cancel(current_job->cancel);return TRUE;
}
static void sender_changed(GDBusConnection *connection,const char *sender_name,const char *object_path,const char *interface_name,const char *signal_name,GVariant *parameters,gpointer user_data) {
  (void)connection;(void)sender_name;(void)object_path;(void)interface_name;(void)signal_name;(void)user_data;
  const char *name,*old_owner,*new_owner;g_variant_get(parameters,"(&s&s&s)",&name,&old_owner,&new_owner);
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
  g_mutex_lock(&candidate_enable_lock);
  if((candidate_enable_sender&&!g_strcmp0(name,candidate_enable_sender)&&!*new_owner)||
     ((g_str_equal(name,"org.freedesktop.ModemManager1")||g_str_equal(name,luma_esim_reservation_name()))&&g_strcmp0(old_owner,new_owner)))candidate_enable_clear_locked();
  g_mutex_unlock(&candidate_enable_lock);
#endif
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  if((g_str_equal(name,"org.freedesktop.ModemManager1")||g_str_equal(name,luma_esim_reservation_name()))&&g_strcmp0(old_owner,new_owner)){profile_native_clear(NULL);luma_esim_cache_invalidate(&cache);}
  if(g_dbus_is_unique_name(name)&&g_str_equal(old_owner,name)&&!*new_owner)profile_native_clear(name);
#endif
  if(g_dbus_is_unique_name(name) && g_str_equal(old_owner,name) && !*new_owner){cancel_owned_job(name);luma_esim_cache_remove(&cache,name);}
  if(current_job && current_job->ims_owner && g_str_equal(old_owner,current_job->ims_owner) &&
     !g_str_equal(old_owner,new_owner))g_cancellable_cancel(current_job->cancel);
}
static void job_free(gpointer data) {
  Job*j=data;if(j->timer)g_source_remove(j->timer);
  if(j->input>=0)close(j->input);
  g_object_unref(j->invocation);g_variant_unref(j->parameters);
  g_object_unref(j->cancel);g_free(j->method);g_free(j->sender);g_free(j->ims_owner);g_free(j->reservation_token);g_free(j);
}
static gboolean deadline(gpointer data) {Job*j=data;j->timer=0;g_cancellable_cancel(j->cancel);return G_SOURCE_REMOVE;}
static gboolean authorize(Job*j) {
  if(g_cancellable_is_cancelled(j->cancel))return FALSE;
  GVariantBuilder subject,details;
  g_variant_builder_init(&subject,G_VARIANT_TYPE("a{sv}"));
  g_variant_builder_add(&subject,"{sv}","name",g_variant_new_string(j->sender));
  g_variant_builder_init(&details,G_VARIANT_TYPE("a{ss}"));
  const char *action=g_str_equal(j->method,"ListProfiles")?"org.projectluma.esim.read":"org.projectluma.esim.manage";
  /* Cancelling a local D-Bus wait does not dismiss the remote authentication
   * prompt. Give every check an exact cancellation identity, including reads,
   * so a deadline/disconnected caller cannot leave a stale password dialog. */
  g_autofree char *authorization_id=g_uuid_string_random();
  g_autoptr(GVariant) answer=g_dbus_connection_call_sync(bus,"org.freedesktop.PolicyKit1",
    "/org/freedesktop/PolicyKit1/Authority","org.freedesktop.PolicyKit1.Authority","CheckAuthorization",
    g_variant_new("((s@a{sv})s@a{ss}us)","system-bus-name",g_variant_builder_end(&subject),action,
      g_variant_builder_end(&details),1u,authorization_id),G_VARIANT_TYPE("((bba{ss}))"),G_DBUS_CALL_FLAGS_NONE,120000,j->cancel,NULL);
  if(!answer){
    /* This cleanup must not inherit the already-cancelled operation token.
     * A late completed/absent check may reject cancellation; still fail closed. */
    g_autoptr(GVariant) cancelled=g_dbus_connection_call_sync(bus,"org.freedesktop.PolicyKit1",
      "/org/freedesktop/PolicyKit1/Authority","org.freedesktop.PolicyKit1.Authority","CancelCheckAuthorization",
      g_variant_new("(s)",authorization_id),G_VARIANT_TYPE_UNIT,G_DBUS_CALL_FLAGS_NO_AUTO_START,5000,NULL,NULL);
    return FALSE;
  }
#if defined(LUMA_NATIVE_ESIM_CHANNEL_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_READONLY_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY)
  if(g_str_equal(j->method,"CheckNativeChannelLifecycle") || g_str_equal(j->method,"CheckNativeReadonlyEuicc") || g_str_equal(j->method,"CheckNativeUncertainOpenRecovery") || g_str_equal(j->method,"CheckNativeCandidateBinding")){
    g_autoptr(GVariant) uid=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
      "org.freedesktop.DBus","GetConnectionUnixUser",g_variant_new("(s)",j->sender),G_VARIANT_TYPE("(u)"),
      G_DBUS_CALL_FLAGS_NONE,2000,j->cancel,NULL);
    guint actual=G_MAXUINT;if(uid)g_variant_get(uid,"(u)",&actual);
    if(actual!=0)return FALSE;
  }
#endif
  if(!answer)return FALSE;
  gboolean allowed=FALSE,challenge=FALSE;GVariant *extra=NULL;
  g_variant_get(answer,"((bb@a{ss}))",&allowed,&challenge,&extra);g_variant_unref(extra);
  if(!allowed)return FALSE;
  /* Recheck that the exact unique sender still owns its bus connection. */
  g_autoptr(GVariant) owner=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
    "org.freedesktop.DBus","GetNameOwner",g_variant_new("(s)",j->sender),G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NONE,2000,j->cancel,NULL);
  const char *name=NULL;if(owner)g_variant_get(owner,"(&s)",&name);
  return name && g_str_equal(name,j->sender);
}
static gboolean sender_present(Job*j) {
  if(g_cancellable_is_cancelled(j->cancel))return FALSE;
  g_autoptr(GVariant) answer=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
    "org.freedesktop.DBus","GetNameOwner",g_variant_new("(s)",j->sender),G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NONE,2000,j->cancel,NULL);
  const char *owner=NULL;if(answer)g_variant_get(answer,"(&s)",&owner);
  return owner && g_str_equal(owner,j->sender) && !g_cancellable_is_cancelled(j->cancel);
}
static char *ims_owner(void) {
  g_autoptr(GVariant) result=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
    "org.freedesktop.DBus","GetNameOwner",g_variant_new("(s)",luma_esim_reservation_name()),
    G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,NULL);
  const char *owner=NULL;if(result)g_variant_get(result,"(&s)",&owner);
  return owner && g_dbus_is_unique_name(owner)?g_strdup(owner):NULL;
}
static gboolean reservation(Job*j,gboolean acquire) {
  if(!j->ims_owner || !j->reservation_token)return FALSE;
  /* Terminal signals and the IMS engine queue are asynchronous. Repeating only
   * Release for this exact owner/token permits the already-dispatched terminal
   * fact to reach its owner; it never grants release without native proof. */
  const gint64 deadline=g_get_monotonic_time()+(acquire?60:luma_esim_titan_backend()?90:5)*G_USEC_PER_SEC;
  do {
    g_autofree char *current=ims_owner();
    if(!current || !g_str_equal(current,j->ims_owner))return FALSE;
    gint64 remaining=deadline-g_get_monotonic_time();if(remaining<=0)return FALSE;
    gint timeout=(gint)MIN((remaining+999)/1000,90000);
    g_autoptr(GVariant) result=g_dbus_connection_call_sync(bus,j->ims_owner,luma_esim_reservation_path(),
      luma_esim_reservation_interface(),acquire?"AcquireESimReservation":"ReleaseESimReservation",
      g_variant_new("(s)",j->reservation_token),G_VARIANT_TYPE("(b)"),
      G_DBUS_CALL_FLAGS_NO_AUTO_START,timeout,NULL,NULL);
    gboolean accepted=FALSE;if(result)g_variant_get(result,"(b)",&accepted);
    if(accepted)return TRUE;
    if(acquire || !result)return FALSE;
    if(g_get_monotonic_time()>=deadline)return FALSE;
    g_usleep(50000);
  } while(!acquire);
  return FALSE;
}
static gboolean prepare_profile_change(Job *j,gboolean enabled) {
  if(!luma_esim_titan_backend())return TRUE;
  g_autofree char *current=ims_owner();
  if(!current||g_strcmp0(current,j->ims_owner))return FALSE;
  g_autoptr(GVariant) result=g_dbus_connection_call_sync(bus,j->ims_owner,
    luma_esim_reservation_path(),luma_esim_reservation_interface(),
    "PrepareESimProfileChange",g_variant_new("(sb)",j->reservation_token,enabled),
    G_VARIANT_TYPE("(b)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,40000,NULL,NULL);
  gboolean accepted=FALSE;if(result)g_variant_get(result,"(b)",&accepted);
  return accepted;
}
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
static gboolean inventory_native(Job*j,LumaEsimStore*out,LumaMmBinding **binding) {
  guint8 *raw=NULL;gsize size=0;GError *error=NULL;
  gboolean ok=luma_esim_lpac_bound(bus,j->reservation_token,j->ims_owner,"list",NULL,-1,j->cancel,&raw,&size,NULL,binding,&error);
  if(ok)ok=luma_esim_store_load(out,j->sender,raw,size,&error);
  if(!ok&&binding)g_clear_pointer(binding,luma_mm_binding_free);
  luma_esim_secret_free(raw,size);g_clear_error(&error);return ok;
}
static GArray *pending_notifications(Job *j,const LumaMmBinding *binding) {
  guint8 *raw=NULL;gsize size=0;GError *error=NULL;GArray *sequences=NULL;
  if(luma_esim_lpac_bound(bus,j->reservation_token,j->ims_owner,"notification-list",
      NULL,-1,j->cancel,&raw,&size,binding,NULL,&error))
    sequences=luma_esim_notification_sequences(raw,size);
  luma_esim_secret_free(raw,size);g_clear_error(&error);return sequences;
}
static gboolean notify_carriers(Job *j,const LumaMmBinding *binding) {
  g_autoptr(GArray) sequences=pending_notifications(j,binding);
  if(!sequences)return FALSE;
  for(guint i=0;i<sequences->len;i++){
    if(g_cancellable_is_cancelled(j->cancel)||!sender_present(j))return FALSE;
    char sequence[11];g_snprintf(sequence,sizeof sequence,"%u",g_array_index(sequences,guint32,i));
    guint8 *raw=NULL;gsize size=0;GError *error=NULL;
    gboolean ok=luma_esim_lpac_bound(bus,j->reservation_token,j->ims_owner,"notification-process",
      sequence,-1,j->cancel,&raw,&size,binding,NULL,&error);
    luma_esim_secret_free(raw,size);g_clear_error(&error);
    if(!ok)return FALSE;
  }
  if(!sequences->len)return TRUE;
  g_autoptr(GArray) remaining=pending_notifications(j,binding);
  return remaining && remaining->len==0;
}
#endif
#ifndef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
static gboolean inventory(Job*j,LumaEsimStore*out) {
  guint8*raw=NULL;gsize size=0;GError*error=NULL;
  gboolean ok=luma_esim_lpac_owned(bus,j->reservation_token,j->ims_owner,"list",NULL,-1,j->cancel,&raw,&size,&error);
  if(ok)ok=luma_esim_store_load(out,j->sender,raw,size,&error);
  luma_esim_secret_free(raw,size);g_clear_error(&error);return ok;
}
#endif
static int selector_pipe(const char*s) {
  int fds[2];
#ifdef __linux__
  if(pipe2(fds,O_CLOEXEC)!=0)return -1;
#else
  if(pipe(fds)!=0)return -1;
  for(guint i=0;i<2;i++){
    int flags=fcntl(fds[i],F_GETFD);
    if(flags<0 || fcntl(fds[i],F_SETFD,flags|FD_CLOEXEC)<0){close(fds[0]);close(fds[1]);return -1;}
  }
#endif
  gsize n=strlen(s);gboolean ok=write(fds[1],s,n)==(ssize_t)n && write(fds[1],"\n",1)==1;
  close(fds[1]);if(!ok){close(fds[0]);return -1;}return fds[0];
}
static gboolean name_valid(const char*s) {
  if(strlen(s)>256 || !g_utf8_validate(s,-1,NULL) || g_utf8_strlen(s,-1)>64)return FALSE;
  for(const char*p=s;*p;p=g_utf8_next_char(p)){
    gunichar c=g_utf8_get_char(p);
    if(g_unichar_iscntrl(c) || (c>=0x202a && c<=0x202e) || (c>=0x2066 && c<=0x2069))return FALSE;
  }return TRUE;
}
static const LumaEsimProfile *by_selector(const LumaEsimStore*s,const char*id) {
  for(guint i=0;s->profiles && i<s->profiles->len;i++){
    LumaEsimProfile*p=g_ptr_array_index(s->profiles,i);if(g_str_equal(p->selector,id))return p;
  }return NULL;
}
static gboolean verify_result(const LumaEsimStore*before,const LumaEsimStore*after,const char*op,const char*id,const char*name,gboolean enabled) {
  if(g_str_equal(op,"download")){
    if(after->profiles->len!=before->profiles->len+1)return FALSE;
    guint added=0;
    for(guint i=0;i<after->profiles->len;i++){
      const LumaEsimProfile*p=g_ptr_array_index(after->profiles,i),*old=by_selector(before,p->selector);
      if(!old){if(p->enabled)return FALSE;added++;}
      else if(p->enabled!=old->enabled || !g_str_equal(p->name,old->name) || !g_str_equal(p->nickname,old->nickname) || !g_str_equal(p->provider,old->provider))return FALSE;
    }return added==1;
  }
  const LumaEsimProfile *target=by_selector(before,id);if(!target)return FALSE;
  const LumaEsimProfile*p=by_selector(after,id);
  if(g_str_equal(op,"delete")){
    if(p || after->profiles->len+1!=before->profiles->len)return FALSE;
  }else{
    if(!p || after->profiles->len!=before->profiles->len)return FALSE;
    if(g_str_equal(op,"nickname")){if(!g_str_equal(p->nickname,name) || p->enabled!=target->enabled || !g_str_equal(p->provider,target->provider))return FALSE;}
    else if(p->enabled!=enabled)return FALSE;
    if(g_str_equal(op,"enable")){guint active=0;for(guint k=0;k<after->profiles->len;k++)active+=((LumaEsimProfile*)g_ptr_array_index(after->profiles,k))->enabled; if(active!=1)return FALSE;}
  }
  for(guint i=0;i<after->profiles->len;i++){
    const LumaEsimProfile*x=g_ptr_array_index(after->profiles,i),*old=by_selector(before,x->selector);
    if(!old)return FALSE;
    if(!g_str_equal(x->selector,id) && (!g_str_equal(x->name,old->name) || !g_str_equal(x->nickname,old->nickname) || !g_str_equal(x->provider,old->provider)))return FALSE;
    if(!g_str_equal(op,"nickname") && g_str_equal(x->selector,id) && (!g_str_equal(x->name,old->name) || !g_str_equal(x->nickname,old->nickname) || !g_str_equal(x->provider,old->provider)))return FALSE;
    if(!g_str_equal(x->selector,id) && !g_str_equal(op,"enable") && x->enabled!=old->enabled)return FALSE;
  }return TRUE;
}
static void worker(GTask*task,gpointer source,gpointer task_data,GCancellable*cancel) {
  (void)source;(void)cancel;Job*j=task_data;
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
  g_autofree gchar *candidate_download_mm_owner=NULL;
  LumaMmBinding *candidate_download_binding=NULL,*candidate_expected_binding=NULL;
#endif
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  LumaMmBinding *normal_current=NULL,*normal_expected=NULL,*normal_after=NULL;
#endif
  LumaEsimStore current={0},after={0};GVariant*reply=NULL;LumaEsimSnapshot *snapshot=NULL;
  const char *failure="The eSIM operation could not be verified. Refresh profiles before retrying.";
  /* NOT_INITIALIZED: phone ownership is not available yet (early boot, or a
   * call); clients retry instead of reporting a failure. */
  GIOErrorEnum code=G_IO_ERROR_FAILED;
  const char *generation=NULL,*handle=NULL,*name=NULL,*op=NULL,*id=NULL;gboolean enabled=FALSE,confirmed=FALSE,leased=FALSE;
  if(!authorize(j)){failure="eSIM access was not authorized.";goto out;}
  if(!sender_present(j) || !reservation(j,TRUE)){
    failure="Finish the current call, or wait for native phone ownership to become available.";code=G_IO_ERROR_NOT_INITIALIZED;goto out;
  }
  leased=TRUE;
  if(g_cancellable_is_cancelled(j->cancel) || !sender_present(j)){
    failure="The eSIM request was cancelled before modem access.";goto out;
  }
#if defined(LUMA_NATIVE_ESIM_CHANNEL_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_READONLY_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY)
  if(g_str_equal(j->method,"CheckNativeChannelLifecycle") || g_str_equal(j->method,"CheckNativeReadonlyEuicc") || g_str_equal(j->method,"CheckNativeUncertainOpenRecovery") || g_str_equal(j->method,"CheckNativeCandidateBinding")){
    GError *error=NULL;
    gboolean ok;
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
    if(g_str_equal(j->method,"CheckNativeUncertainOpenRecovery"))
      ok=luma_mm_transport_diagnostic_recovery(bus,j->reservation_token,j->ims_owner,j->cancel,&error);
    else if(g_str_equal(j->method,"CheckNativeCandidateBinding"))
      ok=luma_mm_transport_diagnostic_binding(bus,j->reservation_token,j->ims_owner,j->cancel,&error);
    else
#endif
    ok=g_str_equal(j->method,"CheckNativeReadonlyEuicc")?
      luma_mm_transport_diagnostic_readonly(bus,j->reservation_token,j->ims_owner,j->cancel,&error):
      luma_mm_transport_diagnostic_channel(bus,j->reservation_token,j->ims_owner,j->cancel,&error);
    g_clear_error(&error);
    if(ok)reply=g_variant_ref_sink(g_variant_new("(b)",TRUE));
    goto out;
  }
#endif
  if(g_str_equal(j->method,"ListProfiles")){
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
    if(!inventory_native(j,&current,&normal_current))goto out;
#else
    if(!inventory(j,&current))goto out;
#endif
    LumaEsimSnapshot *previous=luma_esim_cache_any(&cache);
    if(previous && !luma_esim_store_equal(luma_esim_snapshot_store(previous),&current)){
      luma_esim_cache_invalidate(&cache);g_atomic_int_set(&j->observed_card_changed,1);
    }
    luma_esim_snapshot_unref(previous);
    /* Same-process Settings windows share a unique sender. An unchanged card
     * must retain that sender's immutable generation/handles, not invalidate it. */
    snapshot=luma_esim_cache_get(&cache,j->sender);
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
    if(snapshot){
      LumaMmBinding *prior=profile_native_get(j->sender,snapshot,normal_current);
      if(!prior){
        profile_native_clear(NULL);luma_esim_cache_invalidate(&cache);g_atomic_int_set(&j->observed_card_changed,1);
        luma_esim_snapshot_unref(snapshot);snapshot=NULL;
      }
      luma_mm_binding_free(prior);
    }
#endif
    if(!snapshot)snapshot=luma_esim_snapshot_take(&current);
    if(!luma_esim_cache_put(&cache,j->sender,snapshot,j->cancel)){failure="Too many mobile-plan clients are open, or the read was cancelled.";goto out;}
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
    if(!profile_native_put(j->sender,snapshot,&normal_current,j->cancel)||!sender_present(j)){
      profile_native_clear(j->sender);luma_esim_cache_remove(&cache,j->sender);goto out;
    }
#endif
    reply=g_variant_ref_sink(luma_esim_store_public(luma_esim_snapshot_store(snapshot)));goto out;
  }
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  if(!inventory_native(j,&current,&normal_current))goto out;
#else
  if(!inventory(j,&current))goto out;
#endif
  if(g_str_equal(j->method,"Download")){
    op="download";
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
    normal_expected=profile_native_copy(normal_current);if(!normal_expected)goto out;
#endif
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
    candidate_download_mm_owner=candidate_mm_owner();
    if(!candidate_download_mm_owner)goto out;
#endif
  }
  else {
    if(g_str_equal(j->method,"SetEnabled"))g_variant_get(j->parameters,"(&s&sb)",&generation,&handle,&enabled);
    else if(g_str_equal(j->method,"Rename"))g_variant_get(j->parameters,"(&s&s&s)",&generation,&handle,&name);
    else if(g_str_equal(j->method,"Delete"))g_variant_get(j->parameters,"(&s&sb)",&generation,&handle,&confirmed);
    else goto out;
    snapshot=luma_esim_cache_get(&cache,j->sender);
    const LumaEsimStore *observed=luma_esim_snapshot_store(snapshot);
    const LumaEsimProfile*p=observed?luma_esim_store_select(observed,j->sender,generation,handle):NULL;
    if(observed && !luma_esim_store_equal(observed,&current)){
      luma_esim_cache_invalidate(&cache);g_atomic_int_set(&j->observed_card_changed,1);
      failure="The mobile plans changed. Refresh the list and try again.";goto out;
    }
    if(!p){failure="The mobile-plan handle is no longer valid. Refresh the list and try again.";goto out;}
    id=p->selector;
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
    normal_expected=profile_native_get(j->sender,snapshot,normal_current);
    if(!normal_expected){
      profile_native_clear(j->sender);luma_esim_cache_remove(&cache,j->sender);
      failure="Native card ownership changed or expired. Refresh mobile plans before changing the card.";goto out;
    }
#endif
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
    if(!g_str_equal(j->method,"SetEnabled")||!(candidate_expected_binding=candidate_enable_take(j,id,enabled,&current))){
      failure="This candidate can enable only the plan you just downloaded and verified. Refresh plans; no card change was made.";goto out;
    }
#endif
    if(g_str_equal(j->method,"Delete")){
      if(!confirmed || p->enabled){failure="Turn this plan off and confirm removal before deleting it.";goto out;}op="delete";
    }else if(g_str_equal(j->method,"Rename")){
      if(!name_valid(name)){failure="Use a plan name of at most 64 characters, without control characters.";goto out;}op="nickname";
    }else op=enabled?"enable":"disable";
    j->input=selector_pipe(id);if(j->input<0)goto out;
  }
  if(!sender_present(j)){failure="The eSIM request was cancelled before changing the card.";goto out;}
  gboolean active_after=g_str_equal(op,"enable");
  if(g_str_equal(op,"disable"))for(guint i=0;i<current.profiles->len;i++){
    const LumaEsimProfile *p=g_ptr_array_index(current.profiles,i);
    if(p->enabled&&!g_str_equal(p->selector,id))active_after=TRUE;
  }
  if((g_str_equal(op,"enable")||g_str_equal(op,"disable"))&&!prepare_profile_change(j,active_after)){
    failure="Native cellular service could not prepare the profile change.";goto out;
  }
  if(g_cancellable_is_cancelled(j->cancel)||!sender_present(j))goto out;
  g_atomic_int_set(&j->mutation_attempted,1);
  luma_esim_cache_invalidate(&cache);
  guint8*raw=NULL;gsize size=0;GError*error=NULL;
  int input=j->input;j->input=-1;
  /* A transport exit alone is not success; read back card state. In particular,
   * eUICC refresh can disconnect an APDU after a successfully committed change. */
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  luma_esim_lpac_bound(bus,j->reservation_token,j->ims_owner,op,name,input,j->cancel,&raw,&size,normal_expected,NULL,&error);
#elif defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY)
  luma_esim_lpac_bound(bus,j->reservation_token,j->ims_owner,op,name,input,j->cancel,&raw,&size,
    candidate_expected_binding,g_str_equal(op,"download")?&candidate_download_binding:NULL,&error);
#else
  luma_esim_lpac_owned(bus,j->reservation_token,j->ims_owner,op,name,input,j->cancel,&raw,&size,&error);
#endif
  luma_esim_secret_free(raw,size);g_clear_error(&error);
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  if(g_cancellable_is_cancelled(j->cancel)||!inventory_native(j,&after,&normal_after)||!profile_native_same(normal_current,normal_after))goto out;
#else
  if(g_cancellable_is_cancelled(j->cancel) || !inventory(j,&after))goto out;
#endif
  if(!verify_result(&current,&after,op,id,name,enabled))goto out;
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  /* Card installation/state readback does not notify the carrier. The eUICC
   * owns the durable pending queue; upstream LPAC sends it over HTTPS and only
   * removes acknowledged notices. Keep this inside the authorized operation,
   * bound to the independently observed card and existing cancellation limit.
   * Inventory never triggers network traffic. A later authorized action also
   * retries notices left by interruption or an older broker. */
  if(!notify_carriers(j,normal_after)){
    failure="The mobile plan changed, but its carrier notification is still pending. Do not download it again; reconnect to the internet and retry the plan action.";
    goto out;
  }
#endif
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
  if(g_str_equal(op,"download")&&!candidate_download_binding)goto out;
#endif
  reply=g_variant_ref_sink(g_variant_new("()"));
out:
  if(leased && !reservation(j,FALSE)){
    g_clear_pointer(&reply,g_variant_unref);
    failure="Native modem ownership could not be released. Wait for phone recovery before retrying.";
  }
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
  if(reply&&op&&g_str_equal(op,"download")&&sender_present(j))
    candidate_enable_issue(j,&current,&after,candidate_download_mm_owner,&candidate_download_binding);
  luma_mm_binding_free(candidate_download_binding);luma_mm_binding_free(candidate_expected_binding);
#endif
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  if(g_atomic_int_get(&j->mutation_attempted))profile_native_clear(NULL);
  luma_mm_binding_free(normal_current);luma_mm_binding_free(normal_expected);luma_mm_binding_free(normal_after);
#endif
  if(g_atomic_int_get(&j->mutation_attempted))luma_esim_cache_invalidate(&cache);
  if(!reply && g_cancellable_is_cancelled(j->cancel) && g_atomic_int_get(&j->mutation_attempted))failure="The operation was cancelled. The card may already have changed; refresh mobile plans before retrying.";
  luma_esim_snapshot_unref(snapshot);
  luma_esim_store_clear(&current);luma_esim_store_clear(&after);
  if(reply)g_task_return_pointer(task,reply,(GDestroyNotify)g_variant_unref);
  else g_task_return_new_error(task,G_IO_ERROR,code,"%s",failure);
}
static void finished(GObject*source,GAsyncResult*result,gpointer data) {
  (void)source;(void)data;GTask*task=G_TASK(result);Job*j=g_task_get_task_data(task);
  GError*error=NULL;GVariant*reply=g_task_propagate_pointer(task,&error);
  g_assert(current_job==j);current_job=NULL;busy=FALSE;
  if(g_atomic_int_get(&j->mutation_attempted) || g_atomic_int_get(&j->observed_card_changed))g_dbus_connection_emit_signal(bus,NULL,OBJECT,BUS,"ProfilesChanged",NULL,NULL);
  if(reply){g_dbus_method_invocation_return_value(j->invocation,reply);g_variant_unref(reply);

  }else{g_dbus_method_invocation_return_dbus_error(j->invocation,
    g_error_matches(error,G_IO_ERROR,G_IO_ERROR_NOT_INITIALIZED)?"org.projectluma.ESim1.NotReady":"org.projectluma.ESim1.Error",
    error?error->message:"eSIM unavailable");g_clear_error(&error);}
}
static gboolean private_input_pipe(int fd) {
  struct stat st;
  if(fd<0 || fstat(fd,&st)!=0 || !S_ISFIFO(st.st_mode))return FALSE;
  int flags=fcntl(fd,F_GETFL);
  if(flags<0 || (flags&O_ACCMODE)!=O_RDONLY)return FALSE;
  char path[64],target[80];
  g_snprintf(path,sizeof path,"/proc/self/fd/%d",fd);
  ssize_t n=readlink(path,target,sizeof target-1);
  if(n<=0 || n>=(ssize_t)sizeof target-1)return FALSE;
  target[n]=0;
  if(!g_str_has_prefix(target,"pipe:["))return FALSE;
  char*end=NULL;errno=0;
  guint64 inode=g_ascii_strtoull(target+6,&end,10);
  return errno==0 && end>target+6 && g_str_equal(end,"]") && inode==(guint64)st.st_ino;
}
static void method(GDBusConnection*c,const char*sender,const char*path,const char*interface,const char*name,GVariant*parameters,GDBusMethodInvocation*invocation,gpointer data) {
  (void)c;(void)path;(void)interface;(void)data;
#ifndef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
  if(g_str_equal(name,"CheckNativeUncertainOpenRecovery")||g_str_equal(name,"CheckNativeCandidateBinding")){
    g_dbus_method_invocation_return_dbus_error(invocation,"org.projectluma.ESim1.Unsupported",
      "Nonshipping native recovery diagnostic is unavailable.");return;
  }
#endif
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
  /* Same Settings interface, purpose restricted; no diagnostic root downloader. */
  if(!g_str_equal(name,"Download")&&!g_str_equal(name,"ListProfiles")&&!g_str_equal(name,"SetEnabled")&&
     !g_str_equal(name,"CancelOperation")&&!g_str_equal(name,"CancelReservation")&&
     !g_str_equal(name,"CheckNativeChannelLifecycle")&&!g_str_equal(name,"CheckNativeReadonlyEuicc")&&!g_str_equal(name,"CheckNativeUncertainOpenRecovery")&&!g_str_equal(name,"CheckNativeCandidateBinding")){
    g_dbus_method_invocation_return_dbus_error(invocation,"org.projectluma.ESim1.Unsupported","This nonshipping candidate admits download, readback and explicit enable of the same verified new plan.");return;
  }
#endif
  if(g_variant_get_size(parameters)>8192){g_dbus_method_invocation_return_dbus_error(invocation,"org.projectluma.ESim1.InvalidInput","The eSIM request is too large.");return;}
  if(g_str_equal(name,"CancelOperation")){
    if(cancel_owned_job(sender))g_dbus_method_invocation_return_value(invocation,g_variant_new("()"));
    else g_dbus_method_invocation_return_dbus_error(invocation,"org.projectluma.ESim1.NotOwner","No operation owned by this connection is running.");
    return;
  }
  if(g_str_equal(name,"CancelReservation")){
    const char *token=NULL;g_variant_get(parameters,"(&s)",&token);
    gboolean own=current_job && current_job->ims_owner && token &&
      g_str_equal(sender,current_job->ims_owner) &&
      g_str_equal(token,current_job->reservation_token);
    if(own)g_cancellable_cancel(current_job->cancel);
    /* This acknowledges cancellation only; ReleaseESimReservation follows
     * after the worker has waited for every actual LPAC child. */
    g_dbus_method_invocation_return_value(invocation,g_variant_new("(b)",own));return;
  }
  if(busy){g_dbus_method_invocation_return_dbus_error(invocation,"org.projectluma.ESim1.Busy","Another eSIM operation is running.");return;}
  Job*j=g_new0(Job,1);j->input=-1;j->invocation=g_object_ref(invocation);j->parameters=g_variant_ref(parameters);j->method=g_strdup(name);j->sender=g_strdup(sender);j->cancel=g_cancellable_new();
  j->ims_owner=ims_owner();
  g_autofree char *uuid=g_uuid_string_random();j->reservation_token=g_malloc0(33);
  for(guint i=0,k=0;uuid[i];i++)if(uuid[i]!='-')j->reservation_token[k++]=uuid[i];
  if(g_str_equal(name,"Download")){
    gint index=-1;g_variant_get(parameters,"(h)",&index);
    GUnixFDList*fds=g_dbus_message_get_unix_fd_list(g_dbus_method_invocation_get_message(invocation));
    if(fds)j->input=g_unix_fd_list_get(fds,index,NULL);
    if(!private_input_pipe(j->input)){
      g_dbus_method_invocation_return_dbus_error(invocation,"org.projectluma.ESim1.InvalidInput","Pass the activation code through a private pipe.");job_free(j);return;
    }
  }
  busy=TRUE;current_job=j;j->timer=g_timeout_add_seconds(180,deadline,j);
  GTask*t=g_task_new(NULL,j->cancel,finished,NULL);g_task_set_task_data(t,j,job_free);g_task_set_check_cancellable(t,FALSE);g_task_set_return_on_cancel(t,FALSE);g_task_run_in_thread(t,worker);g_object_unref(t);
}
static const GDBusInterfaceVTable vtable={.method_call=method};
int main(void) {
  struct rlimit core={0,0};if(setrlimit(RLIMIT_CORE,&core)!=0)return 1;
  if(geteuid()!=0)return 1;
  GError*error=NULL;char*xml=NULL;
#if defined(LUMA_NATIVE_ESIM_CHANNEL_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_READONLY_DIAGNOSTIC_ONLY)
  const char *xml_path="/usr/share/luma-esim/org.projectluma.ESim1-channel-diagnostic.xml";
#else
  const char *xml_path="/usr/share/luma-esim/org.projectluma.ESim1.xml";
#endif
  if(!g_file_get_contents(xml_path,&xml,NULL,&error)){g_clear_error(&error);return 1;}
  GDBusNodeInfo*node=g_dbus_node_info_new_for_xml(xml,&error);g_free(xml);if(!node){g_clear_error(&error);return 1;}
  bus=g_bus_get_sync(G_BUS_TYPE_SYSTEM,NULL,&error);if(!bus){g_clear_error(&error);g_dbus_node_info_unref(node);return 1;}
  luma_esim_cache_init(&cache);
  guint subscription=g_dbus_connection_signal_subscribe(bus,"org.freedesktop.DBus","org.freedesktop.DBus","NameOwnerChanged","/org/freedesktop/DBus",NULL,G_DBUS_SIGNAL_FLAGS_NONE,sender_changed,NULL,NULL);
  guint object=g_dbus_connection_register_object(bus,OBJECT,node->interfaces[0],&vtable,NULL,NULL,&error);
  if(!object){g_clear_error(&error);g_object_unref(bus);g_dbus_node_info_unref(node);return 1;}
  guint owner=g_bus_own_name_on_connection(bus,BUS,G_BUS_NAME_OWNER_FLAGS_DO_NOT_QUEUE,NULL,NULL,NULL,NULL);
  GMainLoop*loop=g_main_loop_new(NULL,FALSE);g_main_loop_run(loop);
  g_bus_unown_name(owner);g_dbus_connection_unregister_object(bus,object);g_main_loop_unref(loop);g_dbus_connection_signal_unsubscribe(bus,subscription);luma_esim_cache_clear(&cache);g_object_unref(bus);g_dbus_node_info_unref(node);return 0;
}
