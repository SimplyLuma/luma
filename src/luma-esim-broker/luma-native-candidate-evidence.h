/* SPDX-License-Identifier: GPL-2.0-or-later
 * Nonshipping candidate: MM-owned, process-local observations only.
 * No receipt file, environment variable, external setter or D-Bus boolean.
 * This state is attached to the actual SIM and observed QMI client. It is not
 * persisted across MM restart, SIM identity change or native recovery epoch.
 */
#ifndef LUMA_NATIVE_CANDIDATE_EVIDENCE_H
#define LUMA_NATIVE_CANDIDATE_EVIDENCE_H
#include <glib-object.h>
#include <string.h>
#define LUMA_NATIVE_EVIDENCE_KEY "luma-native-candidate-observations"
#define LUMA_NATIVE_FACT_CLOSE 1u
#define LUMA_NATIVE_FACT_READONLY 2u
#define LUMA_NATIVE_FACT_SENDER_DEATH 4u
#define LUMA_NATIVE_FACT_RECOVERY 8u
typedef struct {
 GWeakRef client;
 gchar identity[65];
 guint slot,facts;
 guint64 epoch;
 gint64 observed_at;
 GMainContext *context;
} LumaNativeEvidence;
static void luma_native_evidence_free(gpointer data) {
 LumaNativeEvidence *e=data;
 g_weak_ref_clear(&e->client);g_main_context_unref(e->context);
 memset(e->identity,0,sizeof e->identity);g_free(e);
}
static gboolean luma_native_evidence_current(LumaNativeEvidence *e,GObject *client,
 const gchar *identity,guint slot) {
 GObject *held;gboolean same;
 if(!e||!client||!identity||strlen(identity)!=64||!slot||
    !g_main_context_is_owner(e->context)||strcmp(e->identity,identity)||e->slot!=slot)return FALSE;
 held=g_weak_ref_get(&e->client);same=held==client;g_clear_object(&held);return same;
}
/* Called only by actual native terminal callbacks after their existing exact
 * operation/generation/drain/publication guards succeeded. Recovery starts a
 * new epoch and invalidates every earlier normal-channel/read-only observation. */
static gboolean luma_native_evidence_observe(GObject *sim,GObject *client,
 const gchar *identity,guint slot,guint fact,gint64 now) {
 LumaNativeEvidence *e;
 guint64 prior_epoch;
 if(!sim||!client||!identity||strlen(identity)!=64||!slot||now<=0||
    !g_main_context_is_owner(g_main_context_get_thread_default()?g_main_context_get_thread_default():g_main_context_default()))return FALSE;
 if(fact!=LUMA_NATIVE_FACT_RECOVERY&&fact!=LUMA_NATIVE_FACT_CLOSE&&
    fact!=LUMA_NATIVE_FACT_SENDER_DEATH&&fact!=(LUMA_NATIVE_FACT_CLOSE|LUMA_NATIVE_FACT_READONLY))return FALSE;
 e=g_object_get_data(sim,LUMA_NATIVE_EVIDENCE_KEY);
 if(!luma_native_evidence_current(e,client,identity,slot)) {
  prior_epoch=e?e->epoch:0;
  if(prior_epoch==G_MAXUINT64)return FALSE;
  e=g_new0(LumaNativeEvidence,1);e->epoch=prior_epoch+1;g_weak_ref_init(&e->client,client);
  memcpy(e->identity,identity,65);e->slot=slot;e->context=g_main_context_ref_thread_default();
  g_object_set_data_full(sim,LUMA_NATIVE_EVIDENCE_KEY,e,luma_native_evidence_free);
 }
 if(fact==LUMA_NATIVE_FACT_RECOVERY){
  if(e->epoch==G_MAXUINT64)return FALSE;
  e->epoch++;e->facts=LUMA_NATIVE_FACT_RECOVERY;
 }else if(!(e->facts&LUMA_NATIVE_FACT_RECOVERY))return FALSE;
 else e->facts|=fact;
 e->observed_at=now;return TRUE;
}
/* A consumer that actually observes a mismatched current identity/client must
 * retire the old evidence even if that old identity subsequently reappears. */
static void luma_native_evidence_invalidate(LumaNativeEvidence *e) {
 if(!e||!g_main_context_is_owner(e->context))return;
 e->facts=0;
 if(e->epoch<G_MAXUINT64)e->epoch++;
}
static guint64 luma_native_evidence_admit(GObject *sim,GObject *client,
 const gchar *identity,guint slot,gint64 now) {
 LumaNativeEvidence *e=g_object_get_data(sim,LUMA_NATIVE_EVIDENCE_KEY);
 if(!luma_native_evidence_current(e,client,identity,slot)){
  luma_native_evidence_invalidate(e);return 0;
 }
 if(now<e->observed_at||
    now-e->observed_at>300*G_USEC_PER_SEC||e->facts!=15)return 0;
 return e->epoch;
}
/* Admission lifetime is one already-owned bounded FD operation. An expired
 * preflight cannot start another operation; identity/epoch must still match
 * before each APDU. Existing operation lease/drain guards remain authoritative. */
static gboolean luma_native_evidence_repeat(GObject *sim,GObject *client,
 const gchar *identity,guint slot,guint64 epoch) {
 LumaNativeEvidence *e=g_object_get_data(sim,LUMA_NATIVE_EVIDENCE_KEY);
 if(!luma_native_evidence_current(e,client,identity,slot)){
  luma_native_evidence_invalidate(e);return FALSE;
 }
 return epoch&&e->epoch==epoch&&e->facts==15;
}
#endif
