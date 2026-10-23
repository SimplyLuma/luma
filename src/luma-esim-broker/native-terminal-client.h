/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gio/gio.h>
typedef struct LumaMmTransport LumaMmTransport;
LumaMmTransport *luma_mm_transport_open_current(GDBusConnection *,const gchar *,const gchar *,GError **);
gboolean luma_mm_transport_attach(LumaMmTransport *,GSubprocessLauncher *);
void luma_mm_transport_abandon_local_fd(LumaMmTransport *);
gboolean luma_mm_transport_wait_terminal(LumaMmTransport *,GError **);
void luma_mm_transport_destroy(LumaMmTransport *);

#if defined(LUMA_NATIVE_ESIM_CHANNEL_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_READONLY_DIAGNOSTIC_ONLY) || defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY)
gboolean luma_mm_transport_diagnostic_channel(GDBusConnection *,const gchar *,const gchar *,GCancellable *,GError **);
gboolean luma_mm_transport_diagnostic_readonly(GDBusConnection *,const gchar *,const gchar *,GCancellable *,GError **);
#endif

/* Private broker memory only. Issued by exact MM terminal handle query. */
typedef struct { gchar *owner,*sim,*handle; guint64 generation,epoch; } LumaMmBinding;
void luma_mm_binding_free(LumaMmBinding *);
LumaMmBinding *luma_mm_transport_binding(LumaMmTransport *,GError **);
LumaMmTransport *luma_mm_transport_open_bound(GDBusConnection *,const gchar *,const gchar *,const LumaMmBinding *,GError **);

#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
gboolean luma_mm_transport_diagnostic_recovery(GDBusConnection *,const gchar *,const gchar *,GCancellable *,GError **);
#endif

#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
gboolean luma_mm_transport_diagnostic_binding(GDBusConnection *,const gchar *,const gchar *,GCancellable *,GError **);
#endif

/* Root-owned board transport selection; default remains the Fairphone owner. */
gboolean luma_esim_titan_backend(void);
const gchar *luma_esim_reservation_name(void);
const gchar *luma_esim_reservation_path(void);
const gchar *luma_esim_reservation_interface(void);
