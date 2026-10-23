/* SPDX-License-Identifier: Apache-2.0 */
#include "profile-store.h"
#include <string.h>
static const char *one="{\"payload\":{\"code\":0,\"data\":[{\"iccid\":\"1234567890123456789\",\"profileState\":\"enabled\",\"profileNickname\":\"Personal\",\"serviceProviderName\":\"Example\",\"isdpAid\":\"00112233445566778899aabbccddeeff\"}]}}";
static gboolean load(LumaEsimStore*s,const char*json) {GError*e=NULL;gboolean ok=luma_esim_store_load(s,":1.42",(const guint8*)json,strlen(json),&e);if(!ok){g_assert_nonnull(e);g_assert_null(strstr(e->message,"123456789"));}g_clear_error(&e);return ok;}
static void public_boundary(void) {
 LumaEsimStore s={0};g_assert_true(load(&s,one));
 g_autoptr(GVariant) v=g_variant_ref_sink(luma_esim_store_public(&s));g_autofree char *text=g_variant_print(v,TRUE);
 g_assert_nonnull(strstr(text,"Personal"));g_assert_null(strstr(text,"1234567890123456789"));g_assert_null(strstr(text,"00112233445566778899"));
 g_autoptr(GVariant) rows=g_variant_get_child_value(v,1);
 g_autoptr(GVariant) row=g_variant_get_child_value(rows,0);
 const char *suffix=NULL;g_assert_true(g_variant_lookup(row,"identifier-suffix","&s",&suffix));
 g_assert_cmpstr(suffix,==,"6789");
 g_assert_null(luma_esim_store_select(&s,":1.42",s.generation,suffix));
 LumaEsimProfile*p=g_ptr_array_index(s.profiles,0);g_assert_true(luma_esim_store_select(&s,":1.42",s.generation,p->handle)==p);
 g_assert_null(luma_esim_store_select(&s,":1.43",s.generation,p->handle));g_assert_null(luma_esim_store_select(&s,":1.42","old",p->handle));g_assert_null(luma_esim_store_select(&s,":1.42",s.generation,"bad"));
 g_autofree char*old=g_strdup(s.generation);g_autofree char*handle=g_strdup(p->handle);g_assert_true(load(&s,one));g_assert_null(luma_esim_store_select(&s,":1.42",old,handle));luma_esim_store_clear(&s);
}
static void bad_inventory(void) {
 const char *bad[]={"{}","[]","{\"payload\":{\"code\":false,\"data\":[]}}","{\"payload\":{\"code\":1,\"data\":[]}}","{\"payload\":{\"code\":0,\"data\":[{}]}}","{\"payload\":{\"code\":0,\"data\":[{\"iccid\":\"1234567890123456789\",\"profileState\":\"unknown\"}]}}"};
 for(guint i=0;i<G_N_ELEMENTS(bad);i++){LumaEsimStore s={0};g_assert_false(load(&s,bad[i]));g_assert_null(s.profiles);luma_esim_store_clear(&s);}
 LumaEsimStore s={0};g_assert_true(load(&s,one));g_autofree char*gen=g_strdup(s.generation);g_assert_false(load(&s,"{}"));g_assert_null(s.generation);g_assert_null(s.profiles);luma_esim_store_clear(&s);
}
static void empty_inventory(void) {LumaEsimStore s={0};g_assert_true(load(&s,"{\"payload\":{\"code\":0,\"data\":[]}}"));g_assert_cmpuint(s.profiles->len,==,0);luma_esim_store_clear(&s);}
static void same_inventory(void) {LumaEsimStore a={0},b={0};g_assert_true(load(&a,one));g_assert_true(load(&b,one));g_assert_true(luma_esim_store_equal(&a,&b));((LumaEsimProfile*)g_ptr_array_index(b.profiles,0))->enabled=FALSE;g_assert_false(luma_esim_store_equal(&a,&b));luma_esim_store_clear(&a);luma_esim_store_clear(&b);}
static void suffix_collision(void) {
 const char *json="{\"payload\":{\"code\":0,\"data\":[{\"iccid\":\"1234567890123456789\",\"profileState\":\"disabled\",\"profileName\":\"Same carrier\"},{\"iccid\":\"9234567890123456789\",\"profileState\":\"disabled\",\"profileName\":\"Same carrier\"}]}}";
 LumaEsimStore s={0};g_assert_true(load(&s,json));
 g_autoptr(GVariant) v=g_variant_ref_sink(luma_esim_store_public(&s));
 g_autoptr(GVariant) rows=g_variant_get_child_value(v,1);
 g_autoptr(GVariant) a=g_variant_get_child_value(rows,0);
 g_autoptr(GVariant) b=g_variant_get_child_value(rows,1);
 const char *as=NULL,*bs=NULL,*ai=NULL,*bi=NULL;
 g_assert_true(g_variant_lookup(a,"identifier-suffix","&s",&as));
 g_assert_true(g_variant_lookup(b,"identifier-suffix","&s",&bs));
 g_assert_cmpstr(as,==,bs);
 g_assert_true(g_variant_lookup(a,"id","&s",&ai));
 g_assert_true(g_variant_lookup(b,"id","&s",&bi));g_assert_cmpstr(ai,!=,bi);
 g_assert_null(luma_esim_store_select(&s,":1.42",s.generation,as));
 g_assert_true(luma_esim_store_select(&s,":1.42",s.generation,ai)==g_ptr_array_index(s.profiles,0));
 g_assert_true(luma_esim_store_select(&s,":1.42",s.generation,bi)==g_ptr_array_index(s.profiles,1));
 luma_esim_store_clear(&s);
}
int main(int argc,char**argv){g_test_init(&argc,&argv,NULL);g_test_add_func("/esim/public-boundary",public_boundary);g_test_add_func("/esim/malformed-inventory",bad_inventory);g_test_add_func("/esim/empty-card",empty_inventory);g_test_add_func("/esim/stale-inventory",same_inventory);g_test_add_func("/esim/suffix-collision-is-not-authority",suffix_collision);return g_test_run();}
