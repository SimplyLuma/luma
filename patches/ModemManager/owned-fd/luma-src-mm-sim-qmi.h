/* -*- Mode: C; tab-width: 4; indent-tabs-mode: nil; c-basic-offset: 4 -*- */
/*
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details:
 *
 * Copyright (C) 2012 Google, Inc.
 */

#ifndef MM_SIM_QMI_H
#define MM_SIM_QMI_H

#include <glib.h>
#include <glib-object.h>

#include "mm-base-sim.h"

#define MM_TYPE_SIM_QMI            (mm_sim_qmi_get_type ())
#define MM_SIM_QMI(obj)            (G_TYPE_CHECK_INSTANCE_CAST ((obj), MM_TYPE_SIM_QMI, MMSimQmi))
#define MM_SIM_QMI_CLASS(klass)    (G_TYPE_CHECK_CLASS_CAST ((klass),  MM_TYPE_SIM_QMI, MMSimQmiClass))
#define MM_IS_SIM_QMI(obj)         (G_TYPE_CHECK_INSTANCE_TYPE ((obj), MM_TYPE_SIM_QMI))
#define MM_IS_SIM_QMI_CLASS(klass) (G_TYPE_CHECK_CLASS_TYPE ((klass),  MM_TYPE_SIM_QMI))
#define MM_SIM_QMI_GET_CLASS(obj)  (G_TYPE_INSTANCE_GET_CLASS ((obj),  MM_TYPE_SIM_QMI, MMSimQmiClass))

typedef struct _MMSimQmi MMSimQmi;
typedef struct _MMSimQmiClass MMSimQmiClass;
typedef struct _MMSimQmiPrivate MMSimQmiPrivate;

#define MM_SIM_QMI_DMS_UIM_DEPRECATED "dms-uim-deprecated"

struct _MMSimQmi {
    MMBaseSim parent;
    MMSimQmiPrivate *priv;
};

struct _MMSimQmiClass {
    MMBaseSimClass parent;
};

GType mm_sim_qmi_get_type (void);
G_DEFINE_AUTOPTR_CLEANUP_FUNC (MMSimQmi, g_object_unref)

void       mm_sim_qmi_new        (MMBaseModem          *modem,
                                  gboolean              dms_uim_deprecated,
                                  GCancellable         *cancellable,
                                  GAsyncReadyCallback   callback,
                                  gpointer              user_data);
MMBaseSim *mm_sim_qmi_new_finish (GAsyncResult         *res,
                                  GError              **error);

MMBaseSim *mm_sim_qmi_new_initialized (MMBaseModem *modem,
                                       gboolean     dms_uim_deprecated,
                                       guint        slot_number,
                                       gboolean     active,
                                       const gchar *sim_identifier,
                                       const gchar *imsi,
                                       const gchar *eid,
                                       const gchar *operator_identifier,
                                       const gchar *operator_name,
                                       const GStrv  emergency_numbers);

/* Luma private SOURCE interface, runtime qualification default FALSE. */
typedef struct _MMLumaUimChannel MMLumaUimChannel;
/* open_finish returns transfer-full; close/APDU borrow the descriptor. Caller
 * ref/unref never substitutes for acknowledged channel close or owner release. */
MMLumaUimChannel *mm_sim_qmi_luma_profile_channel_ref (MMLumaUimChannel *);
void mm_sim_qmi_luma_profile_channel_unref (MMLumaUimChannel *);
G_DEFINE_AUTOPTR_CLEANUP_FUNC (MMLumaUimChannel, mm_sim_qmi_luma_profile_channel_unref)

void mm_sim_qmi_luma_profile_channel_open (MMSimQmi *, GDBusMethodInvocation *, gssize, guint8, GArray *, GAsyncReadyCallback, gpointer);
void mm_sim_qmi_luma_profile_channel_close (MMSimQmi *, MMLumaUimChannel *, GAsyncReadyCallback, gpointer);
void mm_sim_qmi_luma_profile_channel_send_apdu (MMSimQmi *, MMLumaUimChannel *, GArray *, GAsyncReadyCallback, gpointer);

MMLumaUimChannel *mm_sim_qmi_luma_profile_channel_open_finish (MMSimQmi *, GAsyncResult *, GError **);
gboolean mm_sim_qmi_luma_profile_channel_close_finish (MMSimQmi *, GAsyncResult *, GError **);
GArray *mm_sim_qmi_luma_profile_channel_send_apdu_finish (MMSimQmi *, GAsyncResult *, GError **);

/* Internal, DeviceControl-authorized only through modem Reset ownership. */
gboolean mm_sim_qmi_luma_profile_recovery_context (MMSimQmi *self, gssize *operation, guint64 *generation);
void mm_sim_qmi_luma_profile_recover (MMSimQmi *self, gssize operation, guint64 generation, GAsyncReadyCallback callback, gpointer data);
gboolean mm_sim_qmi_luma_profile_recover_finish (MMSimQmi *self, GAsyncResult *res, GError **error);

/* Invalidate current native profile authority before slot identity refresh. */
void mm_sim_qmi_invalidate_native_operation (MMBaseSim *sim);

#endif /* MM_SIM_QMI_H */
