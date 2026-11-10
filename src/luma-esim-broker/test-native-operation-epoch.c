/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "luma-native-operation-epoch.h"
int main(void) {
 GMainContext *context=g_main_context_default();GObject *sim=g_object_new(G_TYPE_OBJECT,NULL),*a=g_object_new(G_TYPE_OBJECT,NULL),*b=g_object_new(G_TYPE_OBJECT,NULL);
 const char *id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";guint64 x,y;
 g_assert_cmpuint(luma_native_operation_admit(sim,a,id,1),==,0);
 g_assert(g_main_context_acquire(context));
 x=luma_native_operation_admit(sim,a,id,1);g_assert_cmpuint(x,>,0);
 g_assert(luma_native_operation_repeat(sim,a,id,1,x));
 g_assert(!luma_native_operation_repeat(sim,b,id,1,x));
 g_assert(!luma_native_operation_repeat(sim,a,id,1,x));
 y=luma_native_operation_admit(sim,a,id,1);g_assert_cmpuint(y,>,x);
 g_assert(!luma_native_operation_repeat(sim,a,id,1,x));
 g_assert(luma_native_operation_repeat(sim,a,id,1,y));
 luma_native_operation_invalidate(sim);
 g_assert(!luma_native_operation_repeat(sim,a,id,1,y));
 x=luma_native_operation_admit(sim,a,id,1);g_assert_cmpuint(x,>,y);
 g_assert_cmpuint(luma_native_operation_admit(sim,NULL,id,1),==,0);
 g_assert(!luma_native_operation_repeat(sim,a,id,1,x));
 x=luma_native_operation_admit(sim,a,id,1);g_assert_cmpuint(x,>,0);
 g_assert(!luma_native_operation_repeat(sim,a,id,2,x));
 g_assert(!luma_native_operation_repeat(sim,a,id,1,x));
 g_assert_cmpuint(luma_native_operation_admit(sim,a,"invalid",1),==,0);
 x=luma_native_operation_admit(sim,a,id,1);g_assert(luma_native_operation_repeat(sim,a,id,1,x));
 g_assert(luma_native_evidence_repeat(sim,a,id,1,x));
 g_assert(luma_native_evidence_observe(sim,a,id,1,LUMA_NATIVE_FACT_CLOSE,0));
 g_assert(luma_native_evidence_repeat(sim,a,id,1,x));
 g_assert(luma_native_evidence_observe(sim,a,id,1,LUMA_NATIVE_FACT_RECOVERY,0));
 g_assert(!luma_native_evidence_repeat(sim,a,id,1,x));
 g_object_unref(sim);g_object_unref(a);g_object_unref(b);g_main_context_release(context);return 0;
}
