/* SPDX-License-Identifier: Apache-2.0 */
#define main esim_service_main_not_called
#include "service.c"
#undef main
static gboolean load_test(LumaEsimStore*s,const char*rows){g_autofree char*j=g_strdup_printf("{\"payload\":{\"code\":0,\"data\":%s}}",rows);return luma_esim_store_load(s,":1.42",(const guint8*)j,strlen(j),NULL);}
static const char*id="1234567890123456789";
static const char*disabled="[{\"iccid\":\"1234567890123456789\",\"profileState\":\"disabled\",\"profileName\":\"Plan\"}]";
static const char*enabled_row="[{\"iccid\":\"1234567890123456789\",\"profileState\":\"enabled\",\"profileName\":\"Plan\"}]";
static void mutation_results(void){
 LumaEsimStore a={0},b={0};g_assert_true(load_test(&a,disabled));g_assert_true(load_test(&b,enabled_row));
 g_assert_true(verify_result(&a,&b,"enable",id,NULL,TRUE));g_assert_false(verify_result(&a,&b,"disable",id,NULL,FALSE));
 g_assert_true(load_test(&b,"[]"));g_assert_true(verify_result(&a,&b,"delete",id,NULL,FALSE));
 g_assert_true(load_test(&b,disabled));g_assert_false(verify_result(&a,&b,"delete",id,NULL,FALSE));
 g_assert_true(verify_result(&a,&b,"nickname",id,"",FALSE));g_assert_false(verify_result(&a,&b,"nickname",id,"Work",FALSE));
 g_assert_true(load_test(&a,"[]"));g_assert_true(verify_result(&a,&b,"download",NULL,NULL,FALSE));
 g_assert_true(load_test(&b,enabled_row));g_assert_false(verify_result(&a,&b,"download",NULL,NULL,FALSE));
 luma_esim_store_clear(&a);luma_esim_store_clear(&b);
}
static void names(void){g_assert_true(name_valid("Work"));g_assert_true(name_valid(""));g_assert_true(name_valid("家族"));g_assert_false(name_valid("one\nother"));g_assert_false(name_valid("x\342\200\256hidden"));g_autofree char*s=g_strnfill(65,'x');g_assert_false(name_valid(s));}
static void authorization_shape(void){GVariantBuilder d;g_variant_builder_init(&d,G_VARIANT_TYPE("a{ss}"));g_autoptr(GVariant)v=g_variant_ref_sink(g_variant_new("((bb@a{ss}))",TRUE,FALSE,g_variant_builder_end(&d)));g_assert_true(g_variant_is_of_type(v,G_VARIANT_TYPE("((bba{ss}))")));gboolean a=FALSE,c=TRUE;GVariant*e=NULL;g_variant_get(v,"((bb@a{ss}))",&a,&c,&e);g_assert_true(a);g_assert_false(c);g_variant_unref(e);}
static void rename_preserves_card_state(void) {
 LumaEsimStore a={0},b={0};g_assert_true(load_test(&a,disabled));
 const char *drift="[{\"iccid\":\"1234567890123456789\",\"profileState\":\"enabled\",\"profileNickname\":\"Work\"}]";
 g_assert_true(load_test(&b,drift));g_assert_false(verify_result(&a,&b,"nickname",id,"Work",FALSE));
 g_assert_false(verify_result(&a,&b,"nickname","9999999999999999999","Work",FALSE));
 luma_esim_store_clear(&a);luma_esim_store_clear(&b);
}
static void nickname_rejects_unrelated_changes(void) {
 const char *before="[{\"iccid\":\"1234567890123456789\",\"profileState\":\"disabled\",\"profileName\":\"Plan\"},{\"iccid\":\"2234567890123456789\",\"profileState\":\"disabled\",\"serviceProviderName\":\"Carrier\"}]";
 const char *after="[{\"iccid\":\"1234567890123456789\",\"profileState\":\"disabled\",\"profileNickname\":\"Work\"},{\"iccid\":\"2234567890123456789\",\"profileState\":\"disabled\",\"serviceProviderName\":\"Different\"}]";
 LumaEsimStore a={0},b={0};g_assert_true(load_test(&a,before));g_assert_true(load_test(&b,after));g_assert_false(verify_result(&a,&b,"nickname",id,"Work",FALSE));luma_esim_store_clear(&a);luma_esim_store_clear(&b);
}
static void sender_scoped_cancel(void) {
 luma_esim_cache_init(&cache);g_autoptr(GCancellable) cancel=g_cancellable_new();Job job={.sender=":1.42",.cancel=cancel};current_job=&job;
 g_assert_false(cancel_owned_job(":1.43"));g_assert_false(g_cancellable_is_cancelled(cancel));
 g_autoptr(GVariant) unrelated=g_variant_ref_sink(g_variant_new("(sss)",":1.43",":1.43",""));sender_changed(NULL,NULL,NULL,NULL,NULL,unrelated,NULL);g_assert_false(g_cancellable_is_cancelled(cancel));
 g_autoptr(GVariant) own=g_variant_ref_sink(g_variant_new("(sss)",":1.42",":1.42",""));sender_changed(NULL,NULL,NULL,NULL,NULL,own,NULL);g_assert_true(g_cancellable_is_cancelled(cancel));
 current_job=NULL;g_assert_false(cancel_owned_job(":1.42"));luma_esim_cache_clear(&cache);
}
static void cancelled_task_keeps_unknown_commit_error(void) {
 g_autoptr(GCancellable) cancel=g_cancellable_new();g_autoptr(GTask) task=g_task_new(NULL,cancel,NULL,NULL);
 g_task_set_check_cancellable(task,FALSE);g_cancellable_cancel(cancel);
 g_task_return_new_error(task,G_IO_ERROR,G_IO_ERROR_FAILED,"The card may already have changed; refresh mobile plans before retrying.");
 g_autoptr(GError) error=NULL;g_assert_null(g_task_propagate_pointer(task,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_FAILED);g_assert_nonnull(strstr(error->message,"may already have changed"));
}
static void selector_descriptor(void) {
 int fd=selector_pipe(id);g_assert_cmpint(fd,>=,0);g_assert_true((fcntl(fd,F_GETFD)&FD_CLOEXEC)!=0);
 char bytes[64];ssize_t n=read(fd,bytes,sizeof bytes);g_assert_cmpint(n,==,20);g_assert_cmpmem(bytes,n,"1234567890123456789\n",20);g_assert_cmpint(read(fd,bytes,sizeof bytes),==,0);g_assert_cmpint(close(fd),==,0);
}
int main(int argc,char**argv){g_test_init(&argc,&argv,NULL);g_test_add_func("/esim/private-selector-descriptor",selector_descriptor);g_test_add_func("/esim/operation-readback",mutation_results);g_test_add_func("/esim/rename-input",names);g_test_add_func("/esim/polkit-result-shape",authorization_shape);g_test_add_func("/esim/rename-preserves-card-state",rename_preserves_card_state);g_test_add_func("/esim/rename-unrelated-change",nickname_rejects_unrelated_changes);g_test_add_func("/esim/sender-scoped-cancel",sender_scoped_cancel);g_test_add_func("/esim/cancel-unknown-commit-error",cancelled_task_keeps_unknown_commit_error);return g_test_run();}
