/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "profile-store.h"
#define LUMA_ESIM_MAX_CLIENTS 32
/* Immutable after construction. A worker's reference survives disconnect/invalidation. */
typedef struct _LumaEsimSnapshot LumaEsimSnapshot;
typedef struct { GMutex lock; GHashTable *senders; } LumaEsimCache;
LumaEsimSnapshot *luma_esim_snapshot_take(LumaEsimStore *store);
LumaEsimSnapshot *luma_esim_snapshot_ref(LumaEsimSnapshot *snapshot);
void luma_esim_snapshot_unref(LumaEsimSnapshot *snapshot);
const LumaEsimStore *luma_esim_snapshot_store(LumaEsimSnapshot *snapshot);
void luma_esim_cache_init(LumaEsimCache *cache);
void luma_esim_cache_clear(LumaEsimCache *cache);
LumaEsimSnapshot *luma_esim_cache_any(LumaEsimCache *cache);
LumaEsimSnapshot *luma_esim_cache_get(LumaEsimCache *cache,const char *sender);
gboolean luma_esim_cache_put(LumaEsimCache *cache,const char *sender,LumaEsimSnapshot *snapshot,GCancellable *cancel);
void luma_esim_cache_remove(LumaEsimCache *cache,const char *sender);
void luma_esim_cache_invalidate(LumaEsimCache *cache);
