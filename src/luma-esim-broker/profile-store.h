/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gio/gio.h>
#include <json-glib/json-glib.h>

typedef struct {
  char *handle, *selector, *name, *nickname, *provider;
  gboolean enabled;
} LumaEsimProfile;
typedef struct {
  char *generation, *sender;
  GPtrArray *profiles;
} LumaEsimStore;
void luma_esim_store_clear (LumaEsimStore *store);
gboolean luma_esim_store_load (LumaEsimStore *store, const char *sender,
                               const guint8 *json, gsize size, GError **error);
GVariant *luma_esim_store_public (const LumaEsimStore *store);
const LumaEsimProfile *luma_esim_store_select (const LumaEsimStore *store, const char *sender,
                                       const char *generation, const char *handle);
gboolean luma_esim_store_equal (const LumaEsimStore *a, const LumaEsimStore *b);

/* NULL is invalid; an empty array proves no pending carrier notifications. */
GArray *luma_esim_notification_sequences(const guint8 *data,gsize size);
