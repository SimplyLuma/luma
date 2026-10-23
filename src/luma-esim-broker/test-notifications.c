/* SPDX-License-Identifier: Apache-2.0 */
/* Exercise the actual fixed command builder and private metadata parser. */
#include "lpac-client.c"
#include "profile-store.h"
static void commands(void) {
 const char *argv[6];
 g_assert_true(lpac_arguments("notification-list",NULL,-1,argv));
 g_assert_cmpstr(argv[0],==,"/usr/bin/lpac");g_assert_cmpstr(argv[1],==,"notification");
 g_assert_cmpstr(argv[2],==,"list");g_assert_null(argv[3]);
 g_assert_true(lpac_arguments("notification-process","4294967295",-1,argv));
 g_assert_cmpstr(argv[1],==,"notification");g_assert_cmpstr(argv[2],==,"process");
 g_assert_cmpstr(argv[3],==,"-r");g_assert_cmpstr(argv[4],==,"4294967295");g_assert_null(argv[5]);
 const char *invalid[]={"", "-a", "1 2", "4294967296", "1234567890123456789", "1;true", NULL};
 for(guint i=0;invalid[i];i++)g_assert_false(lpac_arguments("notification-process",invalid[i],-1,argv));
 g_assert_false(lpac_arguments("notification-process",NULL,-1,argv));
 g_assert_false(lpac_arguments("notification-process","1",5,argv));
 g_assert_false(lpac_arguments("notification-list","1",-1,argv));
 g_assert_false(lpac_arguments("notification-remove","1",-1,argv));
}
static GArray *parse(const char *json){return luma_esim_notification_sequences((const guint8*)json,strlen(json));}
static void inventory(void) {
 g_autoptr(GArray) ordered=parse("{\"payload\":{\"code\":0,\"data\":[{\"seqNumber\":4294967295},{\"seqNumber\":2},{\"seqNumber\":0}]}}");
 g_assert_nonnull(ordered);g_assert_cmpuint(ordered->len,==,3);
 g_assert_cmpuint(g_array_index(ordered,guint32,0),==,0);
 g_assert_cmpuint(g_array_index(ordered,guint32,1),==,2);
 g_assert_cmpuint(g_array_index(ordered,guint32,2),==,G_MAXUINT32);
 g_autoptr(GArray) empty=parse("{\"payload\":{\"code\":0,\"data\":[]}}");
 g_assert_nonnull(empty);g_assert_cmpuint(empty->len,==,0);
 const char *bad[]={"", "{}", "{\"payload\":{\"code\":1,\"data\":[]}}", "{\"payload\":{\"code\":0,\"data\":[{}]}}",
 "{\"payload\":{\"code\":0,\"data\":[{\"seqNumber\":-1}]}}", "{\"payload\":{\"code\":0,\"data\":[{\"seqNumber\":4294967296}]}}",
 "{\"payload\":{\"code\":0,\"data\":[{\"seqNumber\":\"1\"}]}}", "{\"payload\":{\"code\":0,\"data\":[{\"seqNumber\":1},{\"seqNumber\":1}]}}",NULL};
 for(guint i=0;bad[i];i++)g_assert_null(parse(bad[i]));
}
int main(int argc,char **argv){g_test_init(&argc,&argv,NULL);g_test_add_func("/esim/notifications/fixed-commands",commands);g_test_add_func("/esim/notifications/ordered-validated-inventory",inventory);return g_test_run();}
