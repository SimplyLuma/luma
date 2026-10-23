// SPDX-License-Identifier: GPL-2.0-or-later
#include <assert.h>
#include <glib-object.h>
#define QMI_CLIENT(x) (x)
typedef struct {GObject *client;} Channel;
typedef struct {guint64 candidate_epoch;} Terminal;
typedef struct {Channel *channel;Terminal *terminal;guint64 candidate_epoch,expected_candidate_epoch;gboolean retired;} Owner;
static void fd_owner_retire(Owner *o){o->retired=TRUE;}
static void actual_open_guard(Owner *o,GObject *current){
        if(current!=QMI_CLIENT(o->channel->client)||
           (o->expected_candidate_epoch&&o->candidate_epoch!=o->expected_candidate_epoch)){
            fd_owner_retire(o);return;
        }
        o->terminal->candidate_epoch=o->candidate_epoch;
}
int main(void){GObject *a=g_object_new(G_TYPE_OBJECT,NULL),*b=g_object_new(G_TYPE_OBJECT,NULL);Channel c={a};Terminal t={0};Owner o={&c,&t,10,9,FALSE};actual_open_guard(&o,a);assert(o.retired&&!t.candidate_epoch);o.retired=FALSE;o.candidate_epoch=9;actual_open_guard(&o,b);assert(o.retired&&!t.candidate_epoch);o.retired=FALSE;actual_open_guard(&o,a);assert(!o.retired&&t.candidate_epoch==9);g_object_unref(a);g_object_unref(b);g_print("verbatim queued OPEN epoch/client guard PASS\n");return 0;}
