/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Verbatim maintained MM owner callbacks; controlled close boundary, real GLib lifetime. */
#include <gio/gio.h>
#include <unistd.h>
#include <stdio.h>
typedef struct {gboolean closed,close_pending;} Channel;
typedef struct {guint state;gpointer handle;} Terminal;
typedef gpointer MMSimQmi;
typedef struct {gatomicrefcount refs;gboolean finished,dead,pending,released;guint source,expiry;gint fd;gulong cancelled;GCancellable *cancel;Channel *channel;gpointer sim,modem;GMainContext *context;gssize operation;guint64 generation;Terminal *terminal;} LumaFdOwner;
enum {LUMA_PROFILE_CLOSE_CONFIRMED=1,LUMA_PROFILE_RECOVERY_PENDING=2,LUMA_PROFILE_NO_CHANNEL_DISPATCHED=5};
static guint destroyed,close_calls,fences,own_cancel_calls,channel_cancel_calls,queued_channel_callbacks;
static gboolean close_result,release_result;
static void fd_owner_retire(LumaFdOwner *o);
static void fd_owner_destroy(LumaFdOwner *o){destroyed++;g_clear_object(&o->cancel);g_free(o->channel);g_free(o->terminal);g_free(o);}
static gboolean mm_sim_qmi_luma_profile_channel_close_finish(MMSimQmi *s,GAsyncResult *r,GError **e){(void)s;(void)r;(void)e;return close_result;}
static void mm_sim_qmi_luma_profile_channel_close(gpointer s,Channel*c,GAsyncReadyCallback cb,gpointer o){(void)s;(void)c;(void)cb;(void)o;close_calls++;}
static gboolean mm_base_modem_profile_operation_release(gpointer m,gssize op,guint64 gen){(void)m;(void)op;(void)gen;return release_result;}
static gboolean luma_profile_terminal_observe(Terminal*t,gssize op,guint64 gen,guint state,gint64 now){(void)op;(void)gen;(void)now;t->state=state;return TRUE;}
static void mm_sim_qmi_luma_profile_channel_unref(Channel*c){g_free(c);}
static void fd_schedule_recovery_fence(LumaFdOwner*o){(void)o;fences++;}
static gboolean fd_owner_reply(LumaFdOwner*o,gpointer d,guint n){(void)o;(void)d;(void)n;return TRUE;}
static void fd_owner_watch(LumaFdOwner*o){(void)o;}
static void mm_gdbus_sim_emit_luma_profile_transport_terminal(gpointer s,gpointer h,guint64 gen,guint state){(void)s;(void)h;(void)gen;(void)state;}
#define MM_GDBUS_SIM(x) (x)
#define mm_obj_warn(...) ((void)0)
static LumaFdOwner *fd_owner_ref(LumaFdOwner *o){g_atomic_ref_count_inc(&o->refs);return o;}

static void fd_owner_unref(gpointer data){LumaFdOwner *o=data;if(g_atomic_ref_count_dec(&o->refs))fd_owner_destroy(o);}

static void fd_owner_free(LumaFdOwner *o){
    if(o->finished)return;
    o->finished=TRUE;
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    if(o->candidate_sender_watch){g_bus_unwatch_name(o->candidate_sender_watch);o->candidate_sender_watch=0;}
#endif
    if(o->source){g_source_remove(o->source);o->source=0;}
    if(o->expiry){g_source_remove(o->expiry);o->expiry=0;}
    if(o->fd>=0){close(o->fd);o->fd=-1;}
    if(o->cancelled){gulong id=o->cancelled;o->cancelled=0;g_cancellable_disconnect(o->cancel,id);}
    /* This descriptor owner is finished and its actual callback has drained.
     * Retained uncertain channels must become cancelled so the native recovery
     * owner can admit OFF/DOWN/ON/READY; cancellation is not terminal proof.
     * Disconnect our own handler first; channel cancellation queues onto its
     * owner context and retains its independent channel reference. */
    if(!o->released && !o->pending)
        g_cancellable_cancel(o->cancel);
    fd_owner_unref(o);
}

static gboolean fd_owner_cancel_on_owner(gpointer data){
    LumaFdOwner *o=data;
    if(!o->finished)fd_owner_retire(o);
    return G_SOURCE_REMOVE;
}

static void fd_owner_cancelled(GCancellable *cancel,gpointer data){
    LumaFdOwner *o=data;(void)cancel;
    GSource *source=g_idle_source_new();
    g_source_set_callback(source,fd_owner_cancel_on_owner,fd_owner_ref(o),fd_owner_unref);
    g_source_attach(source,o->context);g_source_unref(source);
}

static void fd_owner_closed(MMSimQmi *sim,GAsyncResult *res,gpointer data) {
    LumaFdOwner *o=data;g_autoptr(GError) error=NULL;
    gboolean closed=mm_sim_qmi_luma_profile_channel_close_finish(sim,res,&error);
    if (!closed)
        mm_obj_warn (sim, "Native profile close completion refused: domain=%s code=%d",
                     error ? g_quark_to_string (error->domain) : "none",
                     error ? error->code : 0);
    o->pending=FALSE;
    /* close_finish already qualifies native terminal and releases the lease.
     * An uncertain close stays owned by sim->priv->profile_channel; freeing
     * this extra descriptor reference must not unlock or erase recovery. */
    luma_profile_terminal_observe(o->terminal,o->operation,o->generation,
        closed?LUMA_PROFILE_CLOSE_CONFIRMED:LUMA_PROFILE_RECOVERY_PENDING,g_get_monotonic_time());
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    if(closed&&!o->pending&&o->channel&&luma_profile_terminal_safe_to_release(o->terminal)){
        guint fact=o->candidate_sender_vanished?LUMA_NATIVE_FACT_SENDER_DEATH:LUMA_NATIVE_FACT_CLOSE;
        if(!o->dead&&!o->candidate_epoch&&o->readonly_complete==3&&!o->readonly.pending&&!o->readonly.more)
            fact|=LUMA_NATIVE_FACT_READONLY;
        if((!o->dead||o->candidate_sender_vanished)&&
           !g_dbus_connection_is_closed(g_dbus_method_invocation_get_connection(o->invocation)))
            luma_candidate_observe(o->sim,o->channel,fact);
    }
#endif
    if(!closed)fd_schedule_recovery_fence(o);
    if(!closed||o->dead){fd_owner_free(o);return;}
    g_clear_pointer(&o->channel,mm_sim_qmi_luma_profile_channel_unref);
    o->released=TRUE;
    if(!fd_owner_reply(o,NULL,0)){fd_owner_retire(o);return;}
    fd_owner_watch(o);
}

static void fd_owner_retire(LumaFdOwner *o) {
    o->dead=TRUE;
    if(o->source){g_source_remove(o->source);o->source=0;}
    if(o->fd>=0){close(o->fd);o->fd=-1;}
    if(o->pending)return; /* actual I/O callback owns this pointer until finish */
    if(o->channel&&!o->channel->closed&&!o->channel->close_pending){
        o->pending=TRUE;
        mm_sim_qmi_luma_profile_channel_close(o->sim,o->channel,
            (GAsyncReadyCallback)fd_owner_closed,o);return;
    }
    if(!o->channel&&!o->released){
        /* This guard refuses pending/open/uncertain native channels itself. */
        o->released=mm_base_modem_profile_operation_release(o->modem,o->operation,o->generation);
        luma_profile_terminal_observe(o->terminal,o->operation,o->generation,
            o->released?LUMA_PROFILE_NO_CHANNEL_DISPATCHED:LUMA_PROFILE_RECOVERY_PENDING,g_get_monotonic_time());
        if(o->released)
            mm_gdbus_sim_emit_luma_profile_transport_terminal(MM_GDBUS_SIM(o->sim),
                o->terminal->handle,o->generation,LUMA_PROFILE_NO_CHANNEL_DISPATCHED);
        else fd_schedule_recovery_fence(o);
    }
    fd_owner_free(o);
}

static gboolean idle_channel(gpointer p){(void)p;queued_channel_callbacks++;return G_SOURCE_REMOVE;}
static void channel_cancel(GCancellable*c,gpointer p){(void)c;(void)p;channel_cancel_calls++;g_idle_add(idle_channel,NULL);}
static void own_cancel(GCancellable*c,gpointer p){(void)c;(void)p;own_cancel_calls++;}
static LumaFdOwner* make(gboolean closed){LumaFdOwner*o=g_new0(LumaFdOwner,1);g_atomic_ref_count_init(&o->refs);fd_owner_ref(o);o->fd=-1;o->cancel=g_cancellable_new();o->channel=g_new0(Channel,1);o->channel->closed=closed;o->terminal=g_new0(Terminal,1);o->cancelled=g_cancellable_connect(o->cancel,G_CALLBACK(own_cancel),o,NULL);g_cancellable_connect(o->cancel,G_CALLBACK(channel_cancel),NULL,NULL);return o;}
static void reset(void){destroyed=close_calls=fences=own_cancel_calls=channel_cancel_calls=queued_channel_callbacks=0;close_result=release_result=FALSE;}
static void drain(void){while(g_main_context_iteration(NULL,FALSE));}
static void failure_closed(void){reset();LumaFdOwner*o=make(FALSE);o->pending=TRUE;fd_owner_closed(NULL,NULL,o);g_assert_true(o->finished);g_assert_false(o->pending);g_assert_true(g_cancellable_is_cancelled(o->cancel));g_assert_cmpuint(o->terminal->state,==,2);g_assert_cmpuint(fences,==,1);g_assert_cmpuint(own_cancel_calls,==,0);g_assert_cmpuint(channel_cancel_calls,==,1);g_assert_cmpuint(destroyed,==,0);drain();g_assert_cmpuint(queued_channel_callbacks,==,1);fd_owner_unref(o);g_assert_cmpuint(destroyed,==,1);}
static void eof_close(void){reset();LumaFdOwner*o=make(FALSE);fd_owner_retire(o);g_assert_true(o->dead);g_assert_true(o->pending);g_assert_cmpuint(close_calls,==,1);g_assert_false(g_cancellable_is_cancelled(o->cancel));g_assert_false(o->finished);fd_owner_closed(NULL,NULL,o);g_assert_true(o->finished);g_assert_true(g_cancellable_is_cancelled(o->cancel));g_assert_cmpuint(o->terminal->state,==,2);drain();fd_owner_unref(o);g_assert_cmpuint(destroyed,==,1);}
static void pending_eof(void){reset();LumaFdOwner*o=make(FALSE);o->pending=TRUE;fd_owner_retire(o);g_assert_true(o->dead);g_assert_false(o->finished);g_assert_false(g_cancellable_is_cancelled(o->cancel));g_assert_cmpuint(close_calls,==,0);fd_owner_closed(NULL,NULL,o);g_assert_true(o->finished);g_assert_true(g_cancellable_is_cancelled(o->cancel));drain();fd_owner_unref(o);g_assert_cmpuint(destroyed,==,1);}
static void confirmed_closed(void){reset();LumaFdOwner*o=make(TRUE);o->released=TRUE;fd_owner_free(o);g_assert_false(g_cancellable_is_cancelled(o->cancel));g_assert_cmpuint(channel_cancel_calls,==,0);fd_owner_unref(o);g_assert_cmpuint(destroyed,==,1);}
static void pending_free(void){reset();LumaFdOwner*o=make(FALSE);o->pending=TRUE;fd_owner_free(o);g_assert_false(g_cancellable_is_cancelled(o->cancel));fd_owner_unref(o);g_assert_cmpuint(destroyed,==,1);}
static void no_channel(void){reset();LumaFdOwner*o=make(FALSE);g_clear_pointer(&o->channel,g_free);o->released=TRUE;fd_owner_free(o);g_assert_false(g_cancellable_is_cancelled(o->cancel));fd_owner_unref(o);g_assert_cmpuint(destroyed,==,1);}
static void unknown_open(void){reset();LumaFdOwner*o=make(FALSE);g_clear_pointer(&o->channel,g_free);fd_owner_free(o);g_assert_true(g_cancellable_is_cancelled(o->cancel));g_assert_cmpuint(own_cancel_calls,==,0);g_assert_cmpuint(channel_cancel_calls,==,1);drain();fd_owner_unref(o);g_assert_cmpuint(destroyed,==,1);}
static void queued_owner_cancel_lifetime(void){
 reset();LumaFdOwner *o=make(FALSE);
 fd_owner_cancelled(o->cancel,o); /* actual callback queues a retained owner reference */
 fd_owner_free(o);g_assert_true(o->finished);g_assert_cmpuint(destroyed,==,0);
 drain();g_assert_cmpuint(close_calls,==,0);g_assert_cmpuint(destroyed,==,0);
 fd_owner_unref(o);g_assert_cmpuint(destroyed,==,1);
}
int main(int argc,char**argv){g_test_init(&argc,&argv,NULL);g_test_add_func("/native/failed-close",failure_closed);g_test_add_func("/native/eof-drained-close",eof_close);g_test_add_func("/native/eof-pending-io",pending_eof);g_test_add_func("/native/closed-no-cancel",confirmed_closed);g_test_add_func("/native/pending-no-early-cancel",pending_free);g_test_add_func("/native/no-channel-no-cancel",no_channel);g_test_add_func("/native/unknown-open-shared-cancel",unknown_open);g_test_add_func("/native/queued-owner-cancel-lifetime",queued_owner_cancel_lifetime);return g_test_run();}
