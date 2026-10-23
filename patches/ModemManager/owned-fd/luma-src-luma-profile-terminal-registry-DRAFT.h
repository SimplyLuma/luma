/* SPDX-License-Identifier: GPL-2.0-or-later
 * MM-owner-context only. Native close/teardown callbacks supply terminal facts;
 * client EOF, process exit, timeout and reset acknowledgement never do.
 */
#ifndef LUMA_PROFILE_TERMINAL_REGISTRY_H
#define LUMA_PROFILE_TERMINAL_REGISTRY_H
#include <gio/gio.h>
#include <string.h>
typedef enum {
    LUMA_PROFILE_PENDING,
    LUMA_PROFILE_CLOSE_CONFIRMED,
    LUMA_PROFILE_RECOVERY_PENDING,
    LUMA_PROFILE_RECOVERY_REFUSED,
    LUMA_PROFILE_NATIVE_TEARDOWN_CONFIRMED,
    LUMA_PROFILE_NO_CHANNEL_DISPATCHED
} LumaProfileTerminalState;
typedef struct {
    gatomicrefcount refs;
    gchar *handle;
    gchar *sender;
    guint64 generation;
    guint64 candidate_epoch;
    gboolean diagnostic_open_response_lost;
    gssize operation;
    gint64 recovery_deadline;
    gint64 terminal_at;
    LumaProfileTerminalState state;
} LumaProfileTerminal;
static LumaProfileTerminal *luma_profile_terminal_ref(LumaProfileTerminal *r) {
    g_atomic_ref_count_inc(&r->refs);return r;
}
static void luma_profile_terminal_free(gpointer value) {
    LumaProfileTerminal *r=value;
    if(g_atomic_ref_count_dec(&r->refs)){
        g_free(r->handle); g_free(r->sender); g_free(r);
    }
}
/* A registry is attached to the SIM object; MM restart destroys it. The broker
 * binds responses to the same MM unique owner and cannot reuse old handles. */
static GHashTable *luma_profile_terminal_registry_new(void) {
    return g_hash_table_new_full(g_str_hash,g_str_equal,g_free,luma_profile_terminal_free);
}
static LumaProfileTerminal *luma_profile_terminal_issue(GHashTable *registry,
    const gchar *sender,gssize operation,guint64 generation,gint64 now) {
    if(!sender || !g_dbus_is_unique_name(sender) || operation<0 || !generation || now<=0)
        return NULL;
    GHashTableIter it; gpointer value;
    g_hash_table_iter_init(&it,registry);
    while(g_hash_table_iter_next(&it,NULL,&value)) {
        LumaProfileTerminal *r=value;
        if(r->operation==operation && r->generation==generation && !r->terminal_at)
            return NULL;
        if(r->terminal_at && now-r->terminal_at>=300*G_USEC_PER_SEC)
            g_hash_table_iter_remove(&it);
    }
    /* Never evict a pending or uncertain owner to admit another request. */
    if(g_hash_table_size(registry)>=64) return NULL;
    LumaProfileTerminal *r=g_new0(LumaProfileTerminal,1);
    g_atomic_ref_count_init(&r->refs);
    r->handle=g_uuid_string_random(); r->sender=g_strdup(sender);
    r->operation=operation; r->generation=generation;
    r->state=LUMA_PROFILE_PENDING;
    g_hash_table_insert(registry,g_strdup(r->handle),r);
    return r;
}
static LumaProfileTerminal *luma_profile_terminal_lookup(GHashTable *registry,
    const gchar *handle,const gchar *sender) {
    if(!handle || !sender)
        return NULL;
    guint length=0;while(length<37 && handle[length])length++;
    if(length!=36 || !g_uuid_string_is_valid(handle))
        return NULL;
    LumaProfileTerminal *r=g_hash_table_lookup(registry,handle);
    return r && !g_strcmp0(r->sender,sender) ? r : NULL;
}
static gboolean luma_profile_terminal_observe(LumaProfileTerminal *r,
    gssize operation,guint64 generation,LumaProfileTerminalState state,gint64 now) {
    if(!r || r->operation!=operation || r->generation!=generation || now<=0)
        return FALSE;
    if(r->terminal_at) return r->state==state;
    if(state==LUMA_PROFILE_PENDING) return FALSE;
    if(state==LUMA_PROFILE_CLOSE_CONFIRMED || state==LUMA_PROFILE_NATIVE_TEARDOWN_CONFIRMED || state==LUMA_PROFILE_NO_CHANNEL_DISPATCHED) {
        r->state=state; r->terminal_at=now; return TRUE;
    }
    if(state==LUMA_PROFILE_RECOVERY_PENDING) {
        if(r->state==LUMA_PROFILE_RECOVERY_REFUSED) return FALSE;
        if(!r->recovery_deadline) r->recovery_deadline=now+30*G_USEC_PER_SEC;
        r->state=state; return TRUE;
    }
    if(state==LUMA_PROFILE_RECOVERY_REFUSED && r->recovery_deadline && now>=r->recovery_deadline) {
        r->state=state; return TRUE;
    }
    return FALSE;
}
static gboolean luma_profile_terminal_safe_to_release(const LumaProfileTerminal *r) {
    return r && r->terminal_at && (r->state==LUMA_PROFILE_CLOSE_CONFIRMED ||
        r->state==LUMA_PROFILE_NATIVE_TEARDOWN_CONFIRMED || r->state==LUMA_PROFILE_NO_CHANNEL_DISPATCHED);
}
#endif
