/* SPDX-License-Identifier: Apache-2.0
 * Broker-side source draft. Exact MM unique owner + SIM object + opaque lease
 * handle must survive every operation; child process exit is never terminal.
 */
#include "native-terminal-client.h"
#include <gio/gunixfdlist.h>
#include <unistd.h>
#include <string.h>
#define TITAN_BUS "org.projectluma.Titan2e.Modem1"
#define TITAN_PATH "/org/projectluma/Titan2e/Modem1"
gboolean luma_esim_titan_backend(void) {
 gchar *value=NULL;gboolean result=FALSE;
 if(g_file_get_contents("/usr/lib/luma-esim-broker/transport",&value,NULL,NULL))
  result=!strcmp(value,"titan2e\n");
 g_free(value);return result;
}
const gchar *luma_esim_reservation_name(void) {
 return luma_esim_titan_backend()?TITAN_BUS:"net.catcrafts.IMS1";
}
const gchar *luma_esim_reservation_path(void) {
 return luma_esim_titan_backend()?TITAN_PATH:"/net/catcrafts/IMS1";
}
const gchar *luma_esim_reservation_interface(void) {
 return luma_esim_reservation_name();
}
static const gchar *transport_name(void) {
 return luma_esim_titan_backend()?TITAN_BUS:"org.freedesktop.ModemManager1";
}
static const gchar *transport_interface(void) {
 return luma_esim_titan_backend()?TITAN_BUS:"org.freedesktop.ModemManager1.Sim";
}
struct LumaMmTransport {
 GDBusConnection *bus;
 gchar *owner,*sim,*handle;
 guint64 generation;
 gint fd;
 gboolean terminal;
 guint state;
};
static gchar *mm_unique_owner(GDBusConnection *bus){
 GVariant *v=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
  "org.freedesktop.DBus","GetNameOwner",g_variant_new("(s)",transport_name()),
  G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,NULL);
 if(!v)return NULL;
 const gchar *s=NULL;g_variant_get(v,"(&s)",&s);
 gchar *copy=g_dbus_is_unique_name(s)?g_strdup(s):NULL;g_variant_unref(v);return copy;
}
static gboolean mm_same_owner(LumaMmTransport *t){
 gchar *s=mm_unique_owner(t->bus);gboolean same=s && !g_strcmp0(s,t->owner);g_free(s);return same;
}
static gboolean mm_terminal_status(LumaMmTransport *t,GError **error){
 if(!mm_same_owner(t)){
  g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_CLOSED,"Native modem owner changed before terminal proof");return FALSE;
 }
 GVariant *v=g_dbus_connection_call_sync(t->bus,t->owner,t->sim,transport_interface(),
  "GetLumaProfileTransportStatus",g_variant_new("(s)",t->handle),G_VARIANT_TYPE("(tub)"),
  G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,error);
 if(!v)return FALSE;
 guint64 generation;guint state;gboolean safe;
 g_variant_get(v,"(tub)",&generation,&state,&safe);g_variant_unref(v);
 if(!generation || (t->generation && generation!=t->generation) || state>5){
  g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_INVALID_DATA,"Native terminal generation or state is invalid");return FALSE;
 }
 t->generation=generation;t->state=state;
 /* Only positive native close or qualified device teardown are terminal. */
 t->terminal=safe && (state==1 || state==4 || state==5) && mm_same_owner(t);
 if(state==3){
  g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_FAILED,"Native eSIM recovery refused; modem ownership remains excluded");return FALSE;
 }
 return TRUE;
}
static void mm_transport_free(LumaMmTransport *t){
 if(!t)return;
 if(t->fd>=0)close(t->fd);
 g_clear_object(&t->bus);g_free(t->owner);g_free(t->sim);g_free(t->handle);g_free(t);
}
static LumaMmTransport *luma_mm_transport_open_for_owner(GDBusConnection *bus,const gchar *selected_native_sim,const gchar *selected_owner,const gchar *reservation_token,const gchar *ims_owner,const LumaMmBinding *expected,gboolean diagnostic_loss,GError **error){
 if(!selected_native_sim || !g_variant_is_object_path(selected_native_sim) ||
  !(luma_esim_titan_backend()?g_str_equal(selected_native_sim,TITAN_PATH):g_str_has_prefix(selected_native_sim,"/org/freedesktop/ModemManager1/SIM/")) || strlen(selected_native_sim)>256)
  return NULL;
 LumaMmTransport *t=g_new0(LumaMmTransport,1);t->fd=-1;t->bus=g_object_ref(bus);
 t->owner=g_strdup(selected_owner);t->sim=g_strdup(selected_native_sim);
 if(!t->owner || !g_dbus_is_unique_name(t->owner) || !mm_same_owner(t))goto fail;
 GUnixFDList *fds=NULL;
 GVariant *v=g_dbus_connection_call_with_unix_fd_list_sync(bus,t->owner,t->sim,
  transport_interface(),expected?"OpenLumaProfileTransportBound":diagnostic_loss?"OpenLumaProfileTransportDiagnosticLostResponse":"OpenLumaProfileTransport",
  expected?g_variant_new("(sst)",reservation_token,ims_owner,expected->epoch):g_variant_new("(ss)",reservation_token,ims_owner),G_VARIANT_TYPE("(hs)"),
  G_DBUS_CALL_FLAGS_NO_AUTO_START,20000,NULL,&fds,NULL,error);
 if(!v)goto fail;
 gint index;const gchar *handle;g_variant_get(v,"(h&s)",&index,&handle);
 if(fds && g_unix_fd_list_get_length(fds)==1 && index==0 && g_uuid_string_is_valid(handle)){
  t->handle=g_strdup(handle);t->fd=g_unix_fd_list_get(fds,index,error);
 }
 g_variant_unref(v);g_clear_object(&fds);
 if(t->fd<0 || !t->handle || !mm_terminal_status(t,error))goto fail;
 return t;
fail:mm_transport_free(t);return NULL;
}
gboolean luma_mm_transport_attach(LumaMmTransport *t,GSubprocessLauncher *launcher){
 if(!t || t->fd<0 || !mm_same_owner(t))return FALSE;
 g_subprocess_launcher_setenv(launcher,"LPAC_APDU","luma_mm_fd",TRUE);
 g_subprocess_launcher_setenv(launcher,"LPAC_APDU_LUMA_FD","3",TRUE);
 g_subprocess_launcher_unsetenv(launcher,"LPAC_APDU_QMI_UIM_SLOT");
 g_subprocess_launcher_take_fd(launcher,t->fd,3);t->fd=-1;return TRUE;
}
void luma_mm_transport_abandon_local_fd(LumaMmTransport *t){
 if(t && t->fd>=0){gint fd=t->fd;t->fd=-1;close(fd);}
 /* Local EOF only initiates native retirement; it never proves terminal. */
}
gboolean luma_mm_transport_wait_terminal(LumaMmTransport *t,GError **error){
 if(!t)return FALSE;
 /* Caller must first wait/retire its exact LPAC subprocess. User cancellation
  * does not cancel this owner-terminal proof or cause early IMS Release. */
 gint64 deadline=g_get_monotonic_time()+65*G_USEC_PER_SEC;
 while(g_get_monotonic_time()<deadline){
  if(!mm_terminal_status(t,error))return FALSE;
  if(t->terminal)return TRUE;
  g_usleep(100000);
 }
 g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_TIMED_OUT,"Native eSIM channel terminal proof unavailable");
 return FALSE;
}
void luma_mm_transport_destroy(LumaMmTransport *t){mm_transport_free(t);}

/* Native Modem metadata advertises SIM paths; directly exported native SIM
 * properties select one eSIM. No UIM slot/path guess or Android transport. */
static LumaMmTransport *mm_transport_open_current_mode(GDBusConnection *bus,const gchar *reservation_token,
 const gchar *ims_owner,gboolean diagnostic_loss,GError **error){
 if(!reservation_token || !ims_owner || !g_dbus_is_unique_name(ims_owner))return NULL;
 guint length=0;while(length<33 && reservation_token[length])length++;
 if(length!=32)return NULL;
 for(guint i=0;i<32;i++)if(!g_ascii_isdigit(reservation_token[i]) &&
  !(reservation_token[i]>='a' && reservation_token[i]<='f'))return NULL;
 gchar *owner=mm_unique_owner(bus);
 if(!owner)return NULL;
 if(luma_esim_titan_backend()){
  LumaMmTransport *t=luma_mm_transport_open_for_owner(bus,TITAN_PATH,owner,reservation_token,ims_owner,NULL,FALSE,error);
  g_free(owner);return t;
 }
 GVariant *reply=g_dbus_connection_call_sync(bus,owner,"/org/freedesktop/ModemManager1",
  "org.freedesktop.DBus.ObjectManager","GetManagedObjects",NULL,
  G_VARIANT_TYPE("(a{oa{sa{sv}}})"),G_DBUS_CALL_FLAGS_NO_AUTO_START,5000,NULL,error);
 if(!reply){g_free(owner);return NULL;}
 if(g_variant_get_size(reply)>4*1024*1024){g_variant_unref(reply);g_free(owner);return NULL;}
 GVariant *objects=g_variant_get_child_value(reply,0);GVariantIter it;
 const gchar *path;GVariant *interfaces;gchar *selected=NULL;guint count=0;gboolean refused=FALSE;
 GHashTable *sim_paths=g_hash_table_new_full(g_str_hash,g_str_equal,g_free,NULL);
 g_variant_iter_init(&it,objects);
 while(g_variant_iter_next(&it,"{&o@a{sa{sv}}}",&path,&interfaces)){
  if(++count>512 || g_variant_n_children(interfaces)>32){g_variant_unref(interfaces);refused=TRUE;break;}
  GVariant *properties=g_variant_lookup_value(interfaces,"org.freedesktop.ModemManager1.Modem",G_VARIANT_TYPE("a{sv}"));
  if(properties){
   GVariant *slots=NULL;const gchar *sim_path=NULL;
   if(g_variant_n_children(properties)>128)refused=TRUE;
   else{
    slots=g_variant_lookup_value(properties,"SimSlots",G_VARIANT_TYPE("ao"));
    if(slots){
     GVariantIter slot_it;const gchar *slot_path;
     if(g_variant_n_children(slots)>32)refused=TRUE;
     else{
      g_variant_iter_init(&slot_it,slots);
      while(g_variant_iter_next(&slot_it,"&o",&slot_path))
       if(g_strcmp0(slot_path,"/"))g_hash_table_add(sim_paths,g_strdup(slot_path));
     }
     g_variant_unref(slots);
    }
    if(g_variant_lookup(properties,"Sim","&o",&sim_path) && g_strcmp0(sim_path,"/"))
     g_hash_table_add(sim_paths,g_strdup(sim_path));
    if(g_hash_table_size(sim_paths)>32)refused=TRUE;
   }
   g_variant_unref(properties);
  }
  g_variant_unref(interfaces);
  if(refused)break;
 }
 g_variant_unref(objects);g_variant_unref(reply);
 /* MMBaseSim is directly exported, not part of ObjectManager. Query only
  * native Modem-advertised SIM paths, on the same captured unique owner. */
 GHashTableIter paths_it;gpointer sim_key;
 gint64 metadata_deadline=g_get_monotonic_time()+15*G_USEC_PER_SEC;
 g_hash_table_iter_init(&paths_it,sim_paths);
 while(!refused && g_hash_table_iter_next(&paths_it,&sim_key,NULL)){
  gint64 remaining=metadata_deadline-g_get_monotonic_time();
  if(remaining<=0){refused=TRUE;break;}
  gint timeout=(gint)MIN((gint64)5000,(remaining+999)/1000);
  GVariant *sim_reply=g_dbus_connection_call_sync(bus,owner,(const gchar*)sim_key,
   "org.freedesktop.DBus.Properties","GetAll",g_variant_new("(s)","org.freedesktop.ModemManager1.Sim"),
   G_VARIANT_TYPE("(a{sv})"),G_DBUS_CALL_FLAGS_NO_AUTO_START,timeout,NULL,error);
  if(!sim_reply){refused=TRUE;break;}
  if(g_variant_get_size(sim_reply)>64*1024){g_variant_unref(sim_reply);refused=TRUE;break;}
  GVariant *properties=g_variant_get_child_value(sim_reply,0);
#ifndef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  gboolean active=FALSE;
#endif
  guint type=0;
  if(g_variant_n_children(properties)>128)refused=TRUE;
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
  else if(g_variant_lookup(properties,"SimType","u",&type) && type==2){
   const gchar *eid=NULL;guint k;gboolean valid;
   valid=g_variant_lookup(properties,"Eid","&s",&eid)&&eid&&strlen(eid)==32;
   for(k=0;valid&&k<32;k++)if(!g_ascii_isdigit(eid[k]))valid=FALSE;
   if(!valid)refused=TRUE;
#else
  else if(g_variant_lookup(properties,"Active","b",&active) && active &&
          g_variant_lookup(properties,"SimType","u",&type) && type==2){
#endif
   if(selected)refused=TRUE;
   else if(!refused)selected=g_strdup((const gchar*)sim_key);
  }
  g_variant_unref(properties);g_variant_unref(sim_reply);
 }
 g_hash_table_unref(sim_paths);
 gchar *after=mm_unique_owner(bus);
 gboolean same=after && !g_strcmp0(after,owner);g_free(after);
 LumaMmTransport *result=(!refused && same && selected)?luma_mm_transport_open_for_owner(bus,selected,owner,reservation_token,ims_owner,NULL,diagnostic_loss,error):NULL;
 g_free(owner);g_free(selected);return result;
}

#if defined(LUMA_NATIVE_ESIM_CHANNEL_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_READONLY_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY)
#include <poll.h>
#include <sys/socket.h>
#include <errno.h>
#include "luma-owned-apdu-wire.h"
/* Nonshipping, fixed OPEN/CLOSE only. No APDU or profile operation is available. */
static gboolean channel_wire(LumaMmTransport *t,guint32 sequence,guint32 op,
                             const guint8 *payload,guint32 size,gint64 deadline,
                             GCancellable *cancel,guint8 *channel) {
 guint8 request[32]={0},response[17]={0};
 if(size>16 || !mm_same_owner(t) || g_cancellable_is_cancelled(cancel))return FALSE;
 luma_wire_put(request,LUMA_APDU_MAGIC);luma_wire_put(request+4,sequence);
 luma_wire_put(request+8,op);luma_wire_put(request+12,size);
 if(size)memcpy(request+16,payload,size);
 if(send(t->fd,request,16+size,MSG_DONTWAIT|MSG_NOSIGNAL)!=(ssize_t)(16+size))return FALSE;
 for(;;){
  struct pollfd p={t->fd,POLLIN,0};
  gint64 now=g_get_monotonic_time();
  if(now>=deadline || g_cancellable_is_cancelled(cancel))return FALSE;
  int rc=poll(&p,1,(int)MIN((gint64)100,(deadline-now+999)/1000));
  if(rc<0){if(errno==EINTR)continue;return FALSE;}
  if(!rc)continue;
  if(!(p.revents&POLLIN))return FALSE;
  struct iovec iov={response,sizeof response};struct msghdr msg={0};
  msg.msg_iov=&iov;msg.msg_iovlen=1;
  ssize_t n=recvmsg(t->fd,&msg,MSG_DONTWAIT);
  if(n<16 || (msg.msg_flags&(MSG_TRUNC|MSG_CTRUNC)) ||
     luma_wire_get(response)!=LUMA_APDU_MAGIC || luma_wire_get(response+4)!=sequence ||
     luma_wire_get(response+8)!=0 || luma_wire_get(response+12)!=(guint32)n-16 || !mm_same_owner(t))return FALSE;
  if(op==LUMA_OPEN){if(n!=17 || response[16]<1 || response[16]>3)return FALSE;*channel=response[16];}
  else if(n!=16)return FALSE;
  return TRUE;
 }
}
static gboolean mm_diagnostic_channel(GDBusConnection *bus,const gchar *token,
 const gchar *ims_owner,GCancellable *cancel,gboolean require_binding,GError **error){
 static const guint8 aid[16]={0xa0,0,0,5,0x59,0x10,0x10,0xff,0xff,0xff,0xff,0x89,0,0,1,0};
 LumaMmTransport *t=luma_mm_transport_open_current(bus,token,ims_owner,error);
 if(!t)return FALSE;
 guint8 channel=0;gint64 deadline=g_get_monotonic_time()+45*G_USEC_PER_SEC;
 gboolean ok=channel_wire(t,1,LUMA_CONNECT,NULL,0,deadline,cancel,&channel) &&
             channel_wire(t,2,LUMA_OPEN,aid,16,deadline,cancel,&channel);
 if(ok){
  /* Nonshipping observation window; no APDU or additional native I/O. */
  g_message("CHANNEL-DIAGNOSTIC: OPEN acknowledged; TRANSMIT count=0");
  gint64 observe_until=g_get_monotonic_time()+2*G_USEC_PER_SEC;
  while(g_get_monotonic_time()<observe_until && !g_cancellable_is_cancelled(cancel))
   g_usleep(20000);
  ok=channel_wire(t,3,LUMA_CLOSE,&channel,1,deadline,cancel,&channel) &&
     channel_wire(t,4,LUMA_DISCONNECT,NULL,0,deadline,cancel,&channel);
 }
 luma_mm_transport_abandon_local_fd(t);
 if(!luma_mm_transport_wait_terminal(t,error))ok=FALSE;
 if(ok&&require_binding){
  LumaMmBinding *binding=luma_mm_transport_binding(t,error);
  if(!binding)ok=FALSE;
  luma_mm_binding_free(binding);
 }
 luma_mm_transport_destroy(t);
 if(!ok && (!error || !*error))g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_FAILED,
     "Native channel lifecycle did not complete; native lease remains independently observed");
 return ok;
}

gboolean luma_mm_transport_diagnostic_channel(GDBusConnection *bus,const gchar *token,
 const gchar *ims_owner,GCancellable *cancel,GError **error){return mm_diagnostic_channel(bus,token,ims_owner,cancel,FALSE,error);}
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
gboolean luma_mm_transport_diagnostic_binding(GDBusConnection *bus,const gchar *token,
 const gchar *ims_owner,GCancellable *cancel,GError **error){return mm_diagnostic_channel(bus,token,ims_owner,cancel,TRUE,error);}
#endif

/* Pinned LPAC ES10 framing, restricted to GetEuiccInfo1/ProfileInfoList. */
static gboolean readonly_wire(LumaMmTransport *t,guint32 sequence,const guint8 *apdu,guint32 size,
                              gint64 deadline,GCancellable *cancel,guint8 *response,gsize *length){
 guint8 request[LUMA_APDU_HEADER+8];guint8 packet[LUMA_APDU_HEADER+258];
 gboolean ok=FALSE;*length=0;
 if(size>8||!mm_same_owner(t)||g_cancellable_is_cancelled(cancel))goto done;
 luma_wire_put(request,LUMA_APDU_MAGIC);luma_wire_put(request+4,sequence);
 luma_wire_put(request+8,LUMA_TRANSMIT);luma_wire_put(request+12,size);memcpy(request+16,apdu,size);
 if(send(t->fd,request,16+size,MSG_NOSIGNAL)!=(ssize_t)(16+size))goto done;
 for(;;){
  gint64 left=deadline-g_get_monotonic_time();if(left<=0||g_cancellable_is_cancelled(cancel))goto done;
  struct pollfd f={t->fd,POLLIN,0};int rc=poll(&f,1,(int)MIN(left/1000+1,100));
  if(rc<0&&errno==EINTR)continue;
  if(rc<0||f.revents&(POLLERR|POLLHUP|POLLNVAL))goto done;
  if(!rc)continue;
  struct iovec iov={packet,sizeof packet};struct msghdr m={0};m.msg_iov=&iov;m.msg_iovlen=1;
  ssize_t n=recvmsg(t->fd,&m,MSG_DONTWAIT);
  if(n<18||m.msg_flags&(MSG_TRUNC|MSG_CTRUNC)||luma_wire_get(packet)!=LUMA_APDU_MAGIC||
     luma_wire_get(packet+4)!=sequence||luma_wire_get(packet+8)!=0||
     luma_wire_get(packet+12)!=(guint32)n-16||!mm_same_owner(t))goto done;
  *length=(gsize)n-16;memcpy(response,packet+16,*length);ok=TRUE;break;
 }
 done:;
 volatile guint8 *wipe=request;for(gsize i=0;i<sizeof request;i++)wipe[i]=0;
 wipe=packet;for(gsize i=0;i<sizeof packet;i++)wipe[i]=0;
 return ok;
}
static gboolean readonly_tlv(const guint8 *p,gsize n,guint tag,gsize *header,gsize *body){
 gsize i=0,len=0;guint actual;if(n<2)return FALSE;actual=p[i++];
 if((actual&31)==31){if(i>=n||p[i]&0x80)return FALSE;actual=(actual<<8)|p[i++];}
 if(actual!=tag||i>=n)return FALSE;
 guint lead=p[i++];if(!(lead&0x80))len=lead;else{
  guint count=lead&127;if(!count||count>2||i+count>n||p[i]==0)return FALSE;
  while(count--)len=(len<<8)|p[i++];
  if(len<128)return FALSE;
 }
 if(len>n-i)return FALSE;
 *header=i;*body=len;return TRUE;
}
static gboolean readonly_es10(LumaMmTransport *t,guint8 channel,guint8 tag,guint32 *sequence,
                             gint64 deadline,GCancellable *cancel){
 guint8 apdu[8]={0,0xe2,0x91,0,3,0xbf,0,0};guint8 response[258],aggregate[8192];
 gsize used=0,length=0,header=0,body=0;gboolean ok=FALSE;apdu[0]=(guint8)(0x80|channel);apdu[6]=tag;
 guint request_size=8;
 for(guint count=0;count<32;count++){
  if(!readonly_wire(t,++*sequence,apdu,request_size,deadline,cancel,response,&length)||length<2||
     length-2>sizeof aggregate-used)goto done;
  memcpy(aggregate+used,response,length-2);used+=length-2;
  if(response[length-2]==0x61){apdu[1]=0xc0;apdu[2]=0;apdu[3]=0;apdu[4]=response[length-1];request_size=5;continue;}
  if(response[length-2]!=0x90||response[length-1]!=0)goto done;
  if(!readonly_tlv(aggregate,used,0xbf00|tag,&header,&body)||header+body!=used)goto done;
  if(tag==0x2d){
   gsize h=0,n=0;if(!readonly_tlv(aggregate+header,body,0xa0,&h,&n)||h+n!=body)goto done;
   gsize at=0;guint profiles=0;
   while(at<n){gsize fh=0,fn=0;if(++profiles>64||!readonly_tlv(aggregate+header+h+at,n-at,0xe3,&fh,&fn))goto done;at+=fh+fn;}
  }
  ok=TRUE;goto done;
 }
 done:;
 volatile guint8 *wipe=aggregate;for(gsize i=0;i<sizeof aggregate;i++)wipe[i]=0;
 wipe=response;for(gsize i=0;i<sizeof response;i++)wipe[i]=0;
 wipe=apdu;for(gsize i=0;i<sizeof apdu;i++)wipe[i]=0;
 return ok;
}
gboolean luma_mm_transport_diagnostic_readonly(GDBusConnection *bus,const gchar *token,
                                              const gchar *ims_owner,GCancellable *cancel,GError **error){
 static const guint8 aid[16]={0xa0,0,0,5,0x59,0x10,0x10,0xff,0xff,0xff,0xff,0x89,0,0,1,0};
 LumaMmTransport *t=luma_mm_transport_open_current(bus,token,ims_owner,error);if(!t)return FALSE;
 guint8 channel=0;guint32 sequence=2;gint64 deadline=g_get_monotonic_time()+45*G_USEC_PER_SEC;
 gboolean opened=channel_wire(t,1,LUMA_CONNECT,NULL,0,deadline,cancel,&channel)&&
                 channel_wire(t,2,LUMA_OPEN,aid,16,deadline,cancel,&channel);
 gboolean ok=opened&&readonly_es10(t,channel,0x20,&sequence,deadline,cancel)&&
                     readonly_es10(t,channel,0x2d,&sequence,deadline,cancel);
 if(opened){gboolean closed=channel_wire(t,++sequence,LUMA_CLOSE,&channel,1,deadline,cancel,&channel);
  gboolean disconnected=closed&&channel_wire(t,++sequence,LUMA_DISCONNECT,NULL,0,deadline,cancel,&channel);ok=ok&&closed&&disconnected;}
 luma_mm_transport_abandon_local_fd(t);
 if(!luma_mm_transport_wait_terminal(t,error))ok=FALSE;
 luma_mm_transport_destroy(t);
 if(!ok&&(!error||!*error))g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_FAILED,"Native read-only eUICC proof or terminal close refused");
 return ok;
}
#endif

void luma_mm_binding_free(LumaMmBinding *binding){
 if(!binding)return;
 g_free(binding->owner);g_free(binding->sim);g_free(binding->handle);g_free(binding);
}
static gboolean mm_binding_current(GDBusConnection *bus,const LumaMmBinding *binding,GError **error){
 gchar *owner=NULL;GVariant *reply=NULL;guint64 epoch=0,generation=0;gboolean ok=FALSE;
 if(!binding||!binding->epoch||!binding->generation||!binding->owner||!binding->sim||
    !binding->handle||!g_dbus_is_unique_name(binding->owner)||!g_variant_is_object_path(binding->sim)||
    !g_uuid_string_is_valid(binding->handle))return FALSE;
 owner=mm_unique_owner(bus);
 if(!owner||strcmp(owner,binding->owner))goto out;
 reply=g_dbus_connection_call_sync(bus,binding->owner,binding->sim,transport_interface(),
  "GetLumaProfileCandidateBinding",g_variant_new("(s)",binding->handle),G_VARIANT_TYPE("(tt)"),
  G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,error);
 if(reply){g_variant_get(reply,"(tt)",&epoch,&generation);ok=epoch==binding->epoch&&generation==binding->generation;}
out:g_clear_pointer(&reply,g_variant_unref);g_free(owner);return ok;
}
LumaMmBinding *luma_mm_transport_binding(LumaMmTransport *t,GError **error){
 LumaMmBinding *binding=NULL;GVariant *reply=NULL;
 if(!t||!t->terminal||!mm_same_owner(t))return NULL;
 reply=g_dbus_connection_call_sync(t->bus,t->owner,t->sim,transport_interface(),
  "GetLumaProfileCandidateBinding",g_variant_new("(s)",t->handle),G_VARIANT_TYPE("(tt)"),
  G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,error);
 if(!reply)return NULL;
 binding=g_new0(LumaMmBinding,1);
 g_variant_get(reply,"(tt)",&binding->epoch,&binding->generation);g_variant_unref(reply);
 binding->owner=g_strdup(t->owner);binding->sim=g_strdup(t->sim);binding->handle=g_strdup(t->handle);
 if(!binding->epoch||binding->generation!=t->generation||!mm_binding_current(t->bus,binding,error)){
  luma_mm_binding_free(binding);return NULL;
 }
 return binding;
}
LumaMmTransport *luma_mm_transport_open_bound(GDBusConnection *bus,const gchar *token,
 const gchar *ims_owner,const LumaMmBinding *binding,GError **error){
 if(!mm_binding_current(bus,binding,error))return NULL;
 /* Actual native open callback compares epoch again after queued authorization
  * and UIM OPEN; every later APDU retains native repeat/lease guards. */
 return luma_mm_transport_open_for_owner(bus,binding->sim,binding->owner,token,ims_owner,binding,FALSE,error);
}

LumaMmTransport *luma_mm_transport_open_current(GDBusConnection *bus,const gchar *token,
 const gchar *ims_owner,GError **error){return mm_transport_open_current_mode(bus,token,ims_owner,FALSE,error);}
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
/* Native ObjectManager mapping, never a guessed modem index. */
static gchar *mm_diagnostic_modem(LumaMmTransport *t,GError **error){
 GVariant *reply=NULL,*objects=NULL,*interfaces=NULL;GVariantIter it;
 gchar *selected=NULL;const gchar *path;guint count=0;gboolean ambiguous=FALSE;
 reply=g_dbus_connection_call_sync(t->bus,t->owner,"/org/freedesktop/ModemManager1",
  "org.freedesktop.DBus.ObjectManager","GetManagedObjects",NULL,G_VARIANT_TYPE("(a{oa{sa{sv}}})"),
  G_DBUS_CALL_FLAGS_NO_AUTO_START,5000,NULL,error);
 if(!reply||g_variant_get_size(reply)>4*1024*1024)goto out;
 objects=g_variant_get_child_value(reply,0);g_variant_iter_init(&it,objects);
 while(g_variant_iter_next(&it,"{&o@a{sa{sv}}}",&path,&interfaces)){
  GVariant *props=NULL;const gchar *sim=NULL;
  if(++count>512||g_variant_n_children(interfaces)>32){ambiguous=TRUE;g_variant_unref(interfaces);break;}
  props=g_variant_lookup_value(interfaces,"org.freedesktop.ModemManager1.Modem",G_VARIANT_TYPE("a{sv}"));
  if(props){
   if(g_variant_n_children(props)>128)ambiguous=TRUE;
   else if(g_variant_lookup(props,"Sim","&o",&sim)&&!g_strcmp0(sim,t->sim)){
    if(selected)ambiguous=TRUE;else selected=g_strdup(path);
   }
   g_variant_unref(props);
  }
  g_variant_unref(interfaces);if(ambiguous)break;
 }
out:g_clear_pointer(&objects,g_variant_unref);g_clear_pointer(&reply,g_variant_unref);
 if(ambiguous||!mm_same_owner(t))g_clear_pointer(&selected,g_free);
 return selected;
}
gboolean luma_mm_transport_diagnostic_recovery(GDBusConnection *bus,const gchar *token,
 const gchar *ims_owner,GCancellable *cancel,GError **error){
 static const guint8 aid[16]={0xa0,0,0,5,0x59,0x10,0x10,0xff,0xff,0xff,0xff,0x89,0,0,1,0};
 LumaMmTransport *t=mm_transport_open_current_mode(bus,token,ims_owner,TRUE,error);
 GVariant *reply=NULL;gchar *modem=NULL;gboolean lost=FALSE,ok=FALSE;guint8 channel=0;
 gint64 deadline=g_get_monotonic_time()+20*G_USEC_PER_SEC;
 if(!t)return FALSE;
 if(!channel_wire(t,1,LUMA_CONNECT,NULL,0,deadline,cancel,&channel))goto out;
 /* A returned usable channel means the explicit diagnostic did not lose its
  * response. Never reinterpret that success as uncertain ownership. */
 if(channel_wire(t,2,LUMA_OPEN,aid,16,deadline,cancel,&channel))goto out;
 if(g_cancellable_is_cancelled(cancel)||!mm_terminal_status(t,error)||t->state!=2||t->terminal)goto out;
 reply=g_dbus_connection_call_sync(bus,t->owner,t->sim,transport_interface(),
  "GetLumaProfileDiagnosticLostOpenStatus",g_variant_new("(s)",t->handle),G_VARIANT_TYPE("(b)"),
  G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,error);
 if(!reply)goto out;
 g_variant_get(reply,"(b)",&lost);g_clear_pointer(&reply,g_variant_unref);
 if(!lost||!mm_same_owner(t))goto out;
 modem=mm_diagnostic_modem(t,error);if(!modem)goto out;
 /* Existing authenticated DeviceControl Reset owns actual OFF/DOWN/ON/READY.
  * This caller never emits modem messages, sets facts or releases an unsafe lease. */
 reply=g_dbus_connection_call_sync(bus,t->owner,modem,"org.freedesktop.ModemManager1.Modem",
  "Reset",NULL,G_VARIANT_TYPE("()"),G_DBUS_CALL_FLAGS_NO_AUTO_START,60000,NULL,error);
 if(!reply)goto out;
 g_clear_pointer(&reply,g_variant_unref);
 if(luma_mm_transport_wait_terminal(t,error)&&t->state==4&&t->terminal)ok=TRUE;
out:
 luma_mm_transport_abandon_local_fd(t);
 /* Always observe independently; failure never upgrades unsafe native state. */
 if(!ok){GError *cleanup=NULL;luma_mm_transport_wait_terminal(t,&cleanup);g_clear_error(&cleanup);}
 g_clear_pointer(&reply,g_variant_unref);g_free(modem);luma_mm_transport_destroy(t);
 if(!ok&&(!error||!*error))g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_FAILED,
  "Native lost-OPEN fault and physical power recovery were not both verified");
 return ok;
}
#endif
