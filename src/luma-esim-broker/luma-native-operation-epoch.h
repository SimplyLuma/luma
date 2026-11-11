/* SPDX-License-Identifier: GPL-2.0-or-later
 * Draft MM-owned operation epoch. No diagnostic observations or persisted facts.
 * Integration/default shipping admission remains closed pending physical proof.
 */
#ifndef LUMA_NATIVE_OPERATION_EPOCH_H
#define LUMA_NATIVE_OPERATION_EPOCH_H
#include <glib-object.h>
#include <string.h>
#define LUMA_NATIVE_OPERATION_KEY "luma-native-operation-epoch"
typedef struct { GWeakRef client; gchar identity[65]; guint slot; guint64 epoch; GMainContext *context; gboolean current; } LumaNativeOperationEpoch;
static void luma_native_operation_free(gpointer data) {
 LumaNativeOperationEpoch *e=data;g_weak_ref_clear(&e->client);g_main_context_unref(e->context);memset(e->identity,0,sizeof e->identity);g_free(e);
}
static gboolean luma_native_operation_identity_valid(const gchar *identity,guint slot) {
 guint i;if(!identity||strlen(identity)!=64||!slot)return FALSE;
 for(i=0;i<64;i++)if(!g_ascii_isxdigit(identity[i]))return FALSE;
 return TRUE;
}
static gboolean luma_native_operation_same(LumaNativeOperationEpoch *e,GObject *client,const gchar *identity,guint slot) {
 GObject *held;gboolean same;
 if(!e||!client||!luma_native_operation_identity_valid(identity,slot)||!g_main_context_is_owner(e->context)||strcmp(e->identity,identity)||e->slot!=slot)return FALSE;
 held=g_weak_ref_get(&e->client);same=held==client;g_clear_object(&held);return same;
}
/* Invoke at actual authenticated native ownership boundary, with live SIM/EID,
 * native UIM client, exclusive operation lease and call/emergency exclusion.
 * It is not a standalone capability switch and cannot establish a QMI lease. */
static guint64 luma_native_operation_admit(GObject *sim,GObject *client,const gchar *identity,guint slot) {
 LumaNativeOperationEpoch *e;guint64 next;GMainContext *context;
 if(!sim)return 0;
 context=g_main_context_get_thread_default();if(!context)context=g_main_context_default();
 if(!g_main_context_is_owner(context))return 0;
 e=g_object_get_data(sim,LUMA_NATIVE_OPERATION_KEY);
 if(!client||!luma_native_operation_identity_valid(identity,slot)){
  if(e){e->current=FALSE;if(e->epoch<G_MAXUINT64)e->epoch++;}return 0;
 }
 if(luma_native_operation_same(e,client,identity,slot)&&e->current)return e->epoch;
 next=e?e->epoch:0;if(next==G_MAXUINT64)return 0;
 e=g_new0(LumaNativeOperationEpoch,1);g_weak_ref_init(&e->client,client);memcpy(e->identity,identity,65);
 e->slot=slot;e->epoch=next+1;e->current=TRUE;e->context=g_main_context_ref(context);
 g_object_set_data_full(sim,LUMA_NATIVE_OPERATION_KEY,e,luma_native_operation_free);return e->epoch;
}
/* Repeat does not create authority: observed A->B->A invalidates old bindings. */
static gboolean luma_native_operation_repeat(GObject *sim,GObject *client,const gchar *identity,guint slot,guint64 epoch) {
 LumaNativeOperationEpoch *e;if(!sim)return FALSE;e=g_object_get_data(sim,LUMA_NATIVE_OPERATION_KEY);
 if(!luma_native_operation_same(e,client,identity,slot)){
  if(e&&g_main_context_is_owner(e->context)){e->current=FALSE;if(e->epoch<G_MAXUINT64)e->epoch++;}return FALSE;
 }
 return e->current&&epoch&&e->epoch==epoch;
}
/* Actual native recovery dispatch/teardown invalidates before any subsequent
 * admission, even if QMI client object and stable EID later remain identical. */
static void luma_native_operation_invalidate(GObject *sim) {
 LumaNativeOperationEpoch *e;if(!sim)return;e=g_object_get_data(sim,LUMA_NATIVE_OPERATION_KEY);
 if(e&&g_main_context_is_owner(e->context)){e->current=FALSE;if(e->epoch<G_MAXUINT64)e->epoch++;}
}
/* Shared per-APDU call sites retain the same repeat semantics. Diagnostic
 * callback observations confer no production authority. */
#define LUMA_NATIVE_FACT_CLOSE 1u
#define LUMA_NATIVE_FACT_READONLY 2u
#define LUMA_NATIVE_FACT_SENDER_DEATH 4u
#define LUMA_NATIVE_FACT_RECOVERY 8u
static gboolean luma_native_evidence_repeat(GObject *sim,GObject *client,const gchar *identity,guint slot,guint64 epoch) {
 return luma_native_operation_repeat(sim,client,identity,slot,epoch);
}
static gboolean luma_native_evidence_observe(GObject *sim,GObject *client,const gchar *identity,guint slot,guint fact,gint64 now) {
 (void)client;(void)identity;(void)slot;(void)now;
 if(fact==LUMA_NATIVE_FACT_RECOVERY)luma_native_operation_invalidate(sim);
 return TRUE; /* observation is not admission */
}
#endif
