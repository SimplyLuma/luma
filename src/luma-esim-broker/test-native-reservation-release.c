/* SPDX-License-Identifier: Apache-2.0 */
/* Verbatim broker Release protocol; controlled bus/clock, no fabricated native proof. */
#include <gio/gio.h>
#include <string.h>
typedef struct {char *ims_owner,*reservation_token;} Job;
static GDBusConnection *bus;
static gint64 now;
static unsigned calls,mode;
static gint64 fake_clock(void){return now;}
static void fake_sleep(gulong n){now+=n;}
static char *ims_owner(void){return g_strdup(mode==3&&calls?":1.99":":1.42");}
static GVariant *fake_call(GDBusConnection*a,const char*b,const char*c,const char*d,const char*method,GVariant*args,const GVariantType*t,GDBusCallFlags f,gint timeout,GCancellable*x,GError**e){
 (void)a;(void)c;(void)d;(void)t;(void)f;(void)x;(void)e;g_assert_cmpstr(b,==,":1.42");g_assert_cmpint(timeout,>,0);const char*token;g_variant_get(args,"(&s)",&token);g_assert_cmpstr(token,==,"same-token");g_variant_ref_sink(args);g_variant_unref(args);calls++;
 gboolean released=mode==1&&calls==3; if(mode==4)return NULL;
 if(g_str_equal(method,"AcquireESimReservation"))g_assert_cmpuint(calls,==,1);
 return g_variant_ref_sink(g_variant_new("(b)",released));
}
#define g_get_monotonic_time fake_clock
#define g_usleep fake_sleep
#define g_dbus_connection_call_sync fake_call
static gboolean reservation(Job*j,gboolean acquire) {
  if(!j->ims_owner || !j->reservation_token)return FALSE;
  /* Terminal signals and the IMS engine queue are asynchronous. Repeating only
   * Release for this exact owner/token permits the already-dispatched terminal
   * fact to reach its owner; it never grants release without native proof. */
  const gint64 deadline=g_get_monotonic_time()+(acquire?60:5)*G_USEC_PER_SEC;
  do {
    g_autofree char *current=ims_owner();
    if(!current || !g_str_equal(current,j->ims_owner))return FALSE;
    gint64 remaining=deadline-g_get_monotonic_time();if(remaining<=0)return FALSE;
    gint timeout=(gint)MIN((remaining+999)/1000,60000);
    g_autoptr(GVariant) result=g_dbus_connection_call_sync(bus,j->ims_owner,"/net/catcrafts/IMS1",
      "net.catcrafts.IMS1",acquire?"AcquireESimReservation":"ReleaseESimReservation",
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
int main(void){Job j={":1.42","same-token"};mode=1;calls=0;now=0;g_assert_true(reservation(&j,FALSE));g_assert_cmpuint(calls,==,3);
 mode=2;calls=0;now=0;g_assert_false(reservation(&j,FALSE));g_assert_cmpint(now,<=,5000000);g_assert_cmpuint(calls,==,100);
 mode=3;calls=0;now=0;g_assert_false(reservation(&j,FALSE));g_assert_cmpuint(calls,==,1);
 mode=4;calls=0;now=0;g_assert_false(reservation(&j,FALSE));g_assert_cmpuint(calls,==,1);
 mode=2;calls=0;now=0;g_assert_false(reservation(&j,TRUE));g_assert_cmpuint(calls,==,1);return 0;}
