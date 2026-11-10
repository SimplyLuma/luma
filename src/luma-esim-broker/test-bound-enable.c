/* SPDX-License-Identifier: Apache-2.0
 * Exact production functions extracted from r10 source; controlled bus/clock. */
#include <gio/gio.h>
#include <string.h>
#include <assert.h>
#include "profile-store.h"
#include "profile-cache.h"
#include "native-terminal-client.h"
static GDBusConnection *bus;
typedef struct {char *sender;} Job;
static const char *test_owner=":1.10";
static guint64 test_epoch=9,test_generation=17;
static gboolean test_refuse;
static gint64 test_now=10000000;
static gint64 controlled_time(void){return test_now;}
static GVariant *controlled_call(GDBusConnection *b,const gchar *destination,const gchar *path,const gchar *iface,const gchar *method,GVariant *params,const GVariantType *replytype,GDBusCallFlags flags,gint timeout,GCancellable *cancel,GError **error){
 (void)b;(void)destination;(void)path;(void)iface;(void)params;(void)replytype;(void)flags;(void)timeout;(void)cancel;(void)error;
 if(!strcmp(method,"GetNameOwner"))return test_owner?g_variant_ref_sink(g_variant_new("(s)",test_owner)):NULL;
 if(!strcmp(method,"GetLumaProfileCandidateBinding")){const gchar *handle=NULL;g_variant_get(params,"(&s)",&handle);if(test_refuse||strcmp(handle,"12345678-1234-4234-8234-123456789abc")||strcmp(path,"/org/freedesktop/ModemManager1/SIM/2"))return NULL;return g_variant_ref_sink(g_variant_new("(tt)",test_epoch,test_generation));}
 assert(0);return NULL;
}
#define g_get_monotonic_time controlled_time
#define g_dbus_connection_call_sync controlled_call
static gchar *mm_unique_owner(GDBusConnection *bus){
 GVariant *v=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
  "org.freedesktop.DBus","GetNameOwner",g_variant_new("(s)","org.freedesktop.ModemManager1"),
  G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,NULL);
 if(!v)return NULL;
 const gchar *s=NULL;g_variant_get(v,"(&s)",&s);
 gchar *copy=g_dbus_is_unique_name(s)?g_strdup(s):NULL;g_variant_unref(v);return copy;
}
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
 reply=g_dbus_connection_call_sync(bus,binding->owner,binding->sim,"org.freedesktop.ModemManager1.Sim",
  "GetLumaProfileCandidateBinding",g_variant_new("(s)",binding->handle),G_VARIANT_TYPE("(tt)"),
  G_DBUS_CALL_FLAGS_NO_AUTO_START,2000,NULL,error);
 if(reply){g_variant_get(reply,"(tt)",&epoch,&generation);ok=epoch==binding->epoch&&generation==binding->generation;}
out:g_clear_pointer(&reply,g_variant_unref);g_free(owner);return ok;
}
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
}static LumaMmBinding *binding(void){LumaMmBinding *b=g_new0(LumaMmBinding,1);b->owner=g_strdup(":1.10");b->sim=g_strdup("/org/freedesktop/ModemManager1/SIM/2");b->handle=g_strdup("12345678-1234-4234-8234-123456789abc");b->epoch=9;b->generation=17;return b;}
static void grant(Job *j,LumaEsimStore *before,LumaEsimStore *after){LumaMmBinding *b=binding();LumaEsimStore snapshot={0}; snapshot.profiles=g_ptr_array_new(); for(guint i=0;i<after->profiles->len;i++){LumaEsimProfile *old=g_ptr_array_index(after->profiles,i),*copy=g_new0(LumaEsimProfile,1);copy->selector=g_strdup(old->selector);copy->name=g_strdup(old->name);copy->nickname=g_strdup(old->nickname);copy->provider=g_strdup(old->provider);copy->enabled=old->enabled;g_ptr_array_add(snapshot.profiles,copy);}candidate_enable_issue(j,before,&snapshot,":1.10",&b);assert(!b);assert(!snapshot.profiles);}
int main(void){
 Job j={":1.50"},other={":1.51"};LumaEsimProfile profile={0};profile.selector="new-plan";profile.name="Example";profile.nickname="";profile.provider="Example";LumaEsimStore before={0},after={0};before.profiles=g_ptr_array_new();after.profiles=g_ptr_array_new();g_ptr_array_add(after.profiles,&profile);
 LumaMmBinding *b=binding();assert(mm_binding_current(bus,b,NULL));test_epoch++;assert(!mm_binding_current(bus,b,NULL));test_epoch=9;test_generation++;assert(!mm_binding_current(bus,b,NULL));test_generation=17;test_owner=":1.11";assert(!mm_binding_current(bus,b,NULL));test_owner=":1.10";test_refuse=TRUE;assert(!mm_binding_current(bus,b,NULL));test_refuse=FALSE;g_free(b->handle);b->handle=g_strdup("bad-handle");assert(!mm_binding_current(bus,b,NULL));luma_mm_binding_free(b);b=binding();g_free(b->handle);b->handle=g_strdup("12345678-1234-4234-8234-123456789abd");assert(!mm_binding_current(bus,b,NULL));luma_mm_binding_free(b);b=binding();g_free(b->sim);b->sim=g_strdup("/org/freedesktop/ModemManager1/SIM/3");assert(!mm_binding_current(bus,b,NULL));luma_mm_binding_free(b);b=binding();test_owner=NULL;assert(!mm_binding_current(bus,b,NULL));test_owner=":1.10";luma_mm_binding_free(b);
 grant(&j,&before,&after);assert(!candidate_enable_take(&other,"new-plan",TRUE,&after));assert(!candidate_enable_take(&j,"other-plan",TRUE,&after));assert(!candidate_enable_take(&j,"new-plan",FALSE,&after));b=candidate_enable_take(&j,"new-plan",TRUE,&after);assert(b);assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));luma_mm_binding_free(b);
 grant(&j,&before,&after);test_now=candidate_enable_until;assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));candidate_enable_clear_locked();test_now=10000000;
 grant(&j,&before,&after);test_owner=":1.11";assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));candidate_enable_clear_locked();test_owner=":1.10";
 grant(&j,&before,&after);profile.enabled=TRUE;assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));candidate_enable_clear_locked();profile.enabled=FALSE;
 grant(&j,&before,&after);candidate_enable_clear_locked();assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));
 b=NULL;candidate_enable_issue(&j,&before,&after,":1.10",&b);assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));b=binding();candidate_enable_issue(&j,&after,&after,":1.10",&b);assert(b);assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));luma_mm_binding_free(b);
 profile.enabled=TRUE;b=binding();candidate_enable_issue(&j,&before,&after,":1.10",&b);assert(b);assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));luma_mm_binding_free(b);profile.enabled=FALSE;
 grant(&j,&before,&after);test_epoch++;b=candidate_enable_take(&j,"new-plan",TRUE,&after);assert(b);assert(!mm_binding_current(bus,b,NULL));assert(!candidate_enable_take(&j,"new-plan",TRUE,&after));luma_mm_binding_free(b);test_epoch=9;
 g_ptr_array_unref(before.profiles);g_ptr_array_unref(after.profiles);g_print("production binding/grant negative controls PASS\n");return 0;
}
