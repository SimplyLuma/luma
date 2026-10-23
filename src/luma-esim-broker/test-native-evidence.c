// SPDX-License-Identifier: Apache-2.0
#include <assert.h>
#include "luma-native-candidate-evidence.h"
static guint64 qualify(GObject *sim,GObject *client,const gchar *id,gint64 now){
 assert(luma_native_evidence_observe(sim,client,id,2,LUMA_NATIVE_FACT_RECOVERY,now));
 assert(luma_native_evidence_observe(sim,client,id,2,LUMA_NATIVE_FACT_CLOSE|LUMA_NATIVE_FACT_READONLY,now));
 assert(!luma_native_evidence_admit(sim,client,id,2,now));
 assert(luma_native_evidence_observe(sim,client,id,2,LUMA_NATIVE_FACT_SENDER_DEATH,now));
 return luma_native_evidence_admit(sim,client,id,2,now);
}
int main(void){
 GMainContext *context=g_main_context_default();
 GObject *sim=g_object_new(G_TYPE_OBJECT,NULL),*client=g_object_new(G_TYPE_OBJECT,NULL),*other=g_object_new(G_TYPE_OBJECT,NULL);
 const gchar *id="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
 const gchar *changed="1123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
 gint64 now=g_get_monotonic_time();guint64 epoch;
 assert(!luma_native_evidence_observe(sim,client,id,2,LUMA_NATIVE_FACT_RECOVERY,now));
 assert(g_main_context_acquire(context));
 assert(!luma_native_evidence_admit(sim,client,id,2,now));
 assert(!luma_native_evidence_observe(sim,client,id,2,LUMA_NATIVE_FACT_CLOSE,now));
 assert(!luma_native_evidence_observe(sim,client,id,2,15,now));
 epoch=qualify(sim,client,id,now);assert(epoch>0);
 assert(luma_native_evidence_repeat(sim,client,id,2,epoch));
 assert(!luma_native_evidence_admit(sim,other,id,2,now));
 assert(!luma_native_evidence_repeat(sim,client,id,2,epoch));
 epoch=qualify(sim,client,id,now);
 assert(!luma_native_evidence_repeat(sim,client,changed,2,epoch));
 assert(!luma_native_evidence_admit(sim,client,id,2,now));
 epoch=qualify(sim,client,id,now);
 assert(!luma_native_evidence_admit(sim,client,id,1,now));
 assert(!luma_native_evidence_repeat(sim,client,id,2,epoch));
 epoch=qualify(sim,client,id,now);
 assert(!luma_native_evidence_admit(sim,client,id,2,now+301*G_USEC_PER_SEC));
 assert(!luma_native_evidence_admit(sim,client,id,2,now-1));
 assert(luma_native_evidence_repeat(sim,client,id,2,epoch));
 assert(qualify(sim,client,changed,now+1)>epoch);
 assert(qualify(sim,client,id,now+2)>epoch);
 assert(!luma_native_evidence_repeat(sim,client,id,2,epoch));
 epoch=qualify(sim,client,id,now+2);
 assert(qualify(sim,other,id,now+3)>epoch);
 assert(qualify(sim,client,id,now+4)>epoch);
 assert(!luma_native_evidence_repeat(sim,client,id,2,epoch));
 g_object_unref(client);assert(!luma_native_evidence_admit(sim,other,id,2,now+4));
 g_object_unref(other);g_object_unref(sim);g_main_context_release(context);return 0;
}
