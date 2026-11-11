/* SPDX-License-Identifier: Apache-2.0 */
#include "profile-cache.h"
#include <string.h>
struct _LumaEsimSnapshot { gatomicrefcount refs; LumaEsimStore store; };
LumaEsimSnapshot *luma_esim_snapshot_take(LumaEsimStore *store) {
  LumaEsimSnapshot *s=g_new0(LumaEsimSnapshot,1);g_atomic_ref_count_init(&s->refs);
  s->store=*store;memset(store,0,sizeof *store);return s;
}
LumaEsimSnapshot *luma_esim_snapshot_ref(LumaEsimSnapshot *s) {if(s)g_atomic_ref_count_inc(&s->refs);return s;}
void luma_esim_snapshot_unref(LumaEsimSnapshot *s) {
  if(s && g_atomic_ref_count_dec(&s->refs)){luma_esim_store_clear(&s->store);g_free(s);}
}
const LumaEsimStore *luma_esim_snapshot_store(LumaEsimSnapshot *s) {return s?&s->store:NULL;}
void luma_esim_cache_init(LumaEsimCache *c) {
  g_mutex_init(&c->lock);c->senders=g_hash_table_new_full(g_str_hash,g_str_equal,g_free,(GDestroyNotify)luma_esim_snapshot_unref);
}
void luma_esim_cache_clear(LumaEsimCache *c) {g_clear_pointer(&c->senders,g_hash_table_unref);g_mutex_clear(&c->lock);}
LumaEsimSnapshot *luma_esim_cache_get(LumaEsimCache *c,const char *sender) {
  g_mutex_lock(&c->lock);LumaEsimSnapshot *s=luma_esim_snapshot_ref(g_hash_table_lookup(c->senders,sender));g_mutex_unlock(&c->lock);return s;
}
LumaEsimSnapshot *luma_esim_cache_any(LumaEsimCache *c) {
  GHashTableIter iter;gpointer value=NULL;LumaEsimSnapshot *s=NULL;
  g_mutex_lock(&c->lock);g_hash_table_iter_init(&iter,c->senders);
  if(g_hash_table_iter_next(&iter,NULL,&value))s=luma_esim_snapshot_ref(value);
  g_mutex_unlock(&c->lock);return s;
}
gboolean luma_esim_cache_put(LumaEsimCache *c,const char *sender,LumaEsimSnapshot *s,GCancellable *cancel) {
  gboolean ok=FALSE;
  if(!g_dbus_is_unique_name(sender) || !s || !s->store.sender || !s->store.generation || !s->store.profiles || !g_str_equal(s->store.sender,sender))return FALSE;
  g_mutex_lock(&c->lock);
  /* Cancel/remove is ordered by the same short lock; no hardware/bus calls inside. */
  if(!g_cancellable_is_cancelled(cancel) && (g_hash_table_contains(c->senders,sender) || g_hash_table_size(c->senders)<LUMA_ESIM_MAX_CLIENTS)){
    g_hash_table_replace(c->senders,g_strdup(sender),luma_esim_snapshot_ref(s));ok=TRUE;
  }
  g_mutex_unlock(&c->lock);return ok;
}
void luma_esim_cache_remove(LumaEsimCache *c,const char *sender) {g_mutex_lock(&c->lock);g_hash_table_remove(c->senders,sender);g_mutex_unlock(&c->lock);}
void luma_esim_cache_invalidate(LumaEsimCache *c) {g_mutex_lock(&c->lock);g_hash_table_remove_all(c->senders);g_mutex_unlock(&c->lock);}
