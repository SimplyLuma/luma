/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gio/gio.h>
#include "native-terminal-client.h"
/* Fixed native executable and transport; never a shell. input_fd is transferred
 * to the launcher. Secret-bearing output is caller-owned and must be wiped. */
gboolean luma_esim_lpac_owned (GDBusConnection *bus,const char *token,const char *ims_owner,const char *operation, const char *nickname, int input_fd,
                         GCancellable *cancel, guint8 **output, gsize *size, GError **error);
void luma_esim_secret_free (guint8 *data, gsize size);

gboolean luma_esim_lpac_bound(GDBusConnection *,const char *,const char *,const char *,const char *,int,
 GCancellable *,guint8 **,gsize *,const LumaMmBinding *,LumaMmBinding **,GError **);
