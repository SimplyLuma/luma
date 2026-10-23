#include "luma-profile-terminal-registry-DRAFT.h"
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
 * Copyright (C) 2016 Aleksander Morgado <aleksander@aleksander.es>
 * Copyright (c) 2022 Qualcomm Innovation Center, Inc.
 */

#include <config.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <ctype.h>

#include <ModemManager.h>
#define _LIBMM_INSIDE_MM
#include <libmm-glib.h>

#include "mm-broadband-modem-qmi.h"
#include "mm-log-object.h"
#include "mm-sim-qmi.h"
#include "mm-modem-helpers-qmi.h"
#include "mm-shared-qmi.h"
#include "mm-bind.h"

typedef struct _MMLumaUimChannel MMLumaUimChannel;

G_DEFINE_TYPE (MMSimQmi, mm_sim_qmi, MM_TYPE_BASE_SIM)

enum {
    PROP_0,
    PROP_DMS_UIM_DEPRECATED,
    PROP_LAST
};

static GParamSpec *properties[PROP_LAST];

struct _MMSimQmiPrivate {
    gboolean  dms_uim_deprecated;
    gchar    *imsi;
    MMLumaUimChannel *profile_channel;
};

static const guint16 mf_file_path[]  = { 0x3F00 };
static const guint16 adf_file_path[] = { 0x3F00, 0x7FFF };

/*****************************************************************************/

static gboolean
ensure_qmi_client (GTask       *task,
                   MMSimQmi    *self,
                   QmiService   service,
                   QmiClient  **o_client)
{
    MMBaseModem *modem = NULL;
    QmiClient *client;
    g_autoptr(GError) error = NULL;

    g_object_get (self,
                  MM_BASE_SIM_MODEM, &modem,
                  NULL);
    g_assert (MM_IS_BASE_MODEM (modem));

    g_assert (MM_IS_SHARED_QMI (modem));
    client = mm_shared_qmi_peek_client (MM_SHARED_QMI (modem),
                                        service,
                                        MM_PORT_QMI_FLAG_DEFAULT,
                                        &error);

    g_object_unref (modem);

    if (!client) {
        if (task) {
            g_task_return_error (task, g_steal_pointer (&error));
            g_object_unref (task);
        }
        return FALSE;
    }

    *o_client = client;
    return TRUE;
}

/*****************************************************************************/
/* Wait for SIM ready */

#define SIM_READY_CHECKS_MAX 5
#define SIM_READY_CHECKS_TIMEOUT_SECS 1

typedef struct {
    QmiClient *client_uim;
    guint      ready_checks_n;
} WaitSimReadyContext;

static void
wait_sim_ready_context_free (WaitSimReadyContext *ctx)
{
    g_clear_object (&ctx->client_uim);
    g_slice_free (WaitSimReadyContext, ctx);
}

static gboolean
wait_sim_ready_finish (MMBaseSim     *self,
                       GAsyncResult  *res,
                       GError       **error)
{
    return g_task_propagate_boolean (G_TASK (res), error);
}

static void sim_ready_check (GTask *task);

static gboolean
sim_ready_retry_cb (GTask *task)
{
    sim_ready_check (task);
    return G_SOURCE_REMOVE;
}

static void
sim_ready_retry (GTask *task)
{
    g_timeout_add_seconds (SIM_READY_CHECKS_TIMEOUT_SECS, (GSourceFunc) sim_ready_retry_cb, task);
}

static void
uim_get_card_status_ready (QmiClientUim *client,
                           GAsyncResult *res,
                           GTask        *task)
{
    g_autoptr(QmiMessageUimGetCardStatusOutput)  output = NULL;
    g_autoptr(GError)                            error = NULL;
    MMSimQmi                                    *self;

    self = g_task_get_source_object (task);

    output = qmi_client_uim_get_card_status_finish (client, res, &error);
    if (!output ||
        !qmi_message_uim_get_card_status_output_get_result (output, &error) ||
        (!mm_qmi_uim_get_card_status_output_parse (self, output, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, &error) &&
         (g_error_matches (error, MM_MOBILE_EQUIPMENT_ERROR, MM_MOBILE_EQUIPMENT_ERROR_SIM_NOT_INSERTED) ||
          g_error_matches (error, MM_CORE_ERROR, MM_CORE_ERROR_RETRY)))) {
        mm_obj_dbg (self, "sim not yet considered ready... retrying");
        sim_ready_retry (task);
        return;
    }

    /* SIM is considered ready now */
    mm_obj_dbg (self, "sim is ready");
    g_task_return_boolean (task, TRUE);
    g_object_unref (task);
}

static void
sim_ready_check (GTask *task)
{
    WaitSimReadyContext *ctx;
    MMSimQmi            *self;

    self = g_task_get_source_object (task);
    ctx  = g_task_get_task_data (task);

    ctx->ready_checks_n++;
    if (ctx->ready_checks_n == SIM_READY_CHECKS_MAX) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_FAILED,
                                 "failed waiting for SIM readiness");
        g_object_unref (task);
        return;
    }

    mm_obj_dbg (self, "checking SIM readiness");
    qmi_client_uim_get_card_status (QMI_CLIENT_UIM (ctx->client_uim),
                                    NULL,
                                    5,
                                    NULL,
                                    (GAsyncReadyCallback) uim_get_card_status_ready,
                                    task);
}

static void
wait_sim_ready (MMBaseSim           *_self,
                GAsyncReadyCallback  callback,
                gpointer             user_data)
{
    QmiClient           *client;
    MMSimQmi            *self;
    GTask               *task;
    WaitSimReadyContext *ctx;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    mm_obj_dbg (self, "waiting for SIM to be ready...");
    if (!self->priv->dms_uim_deprecated) {
        g_task_return_boolean (task, TRUE);
        g_object_unref (task);
        return;
    }

    if (!ensure_qmi_client (task, self, QMI_SERVICE_UIM, &client))
        return;

    ctx = g_slice_new0 (WaitSimReadyContext);
    ctx->client_uim = g_object_ref (client);
    g_task_set_task_data (task, ctx, (GDestroyNotify) wait_sim_ready_context_free);

    sim_ready_check (task);
}

/*****************************************************************************/
/* Load SIM ID (ICCID) */

static GArray *
uim_read_finish (QmiClientUim  *client,
                 GAsyncResult  *res,
                 GError       **error)
{
    return g_task_propagate_pointer (G_TASK (res), error);
}

static void
uim_read_ready (QmiClientUim *client,
                GAsyncResult *res,
                GTask        *task)
{
    QmiMessageUimReadTransparentOutput *output;
    GError *error = NULL;

    output = qmi_client_uim_read_transparent_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_uim_read_transparent_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't read data from UIM: ");
        g_task_return_error (task, error);
    } else {
        GArray *read_result = NULL;

        qmi_message_uim_read_transparent_output_get_read_result (output, &read_result, NULL);
        if (read_result)
            g_task_return_pointer (task,
                                   g_array_ref (read_result),
                                   (GDestroyNotify) g_array_unref);
        else
            g_task_return_new_error (task,
                                     MM_CORE_ERROR,
                                     MM_CORE_ERROR_FAILED,
                                     "Read malformed data from UIM");
    }

    if (output)
        qmi_message_uim_read_transparent_output_unref (output);

    g_object_unref (task);
}

static void
uim_read (MMSimQmi            *self,
          guint16              file_id,
          const guint16       *file_path,
          gsize                file_path_len,
          GAsyncReadyCallback  callback,
          gpointer             user_data)
{
    GTask *task;
    QmiClient *client = NULL;
    GArray *file_path_bytes;
    gsize i;
    QmiMessageUimReadTransparentInput *input;
    GArray *aid;

    task = g_task_new (self, NULL, callback, user_data);

    if (!ensure_qmi_client (task,
                            self,
                            QMI_SERVICE_UIM, &client))
        return;

    file_path_bytes = g_array_sized_new (FALSE, FALSE, 1, file_path_len * 2);
    for (i = 0; i < file_path_len; ++i) {
        guint8 byte;

        byte = file_path[i] & 0xFF;
        g_array_append_val (file_path_bytes, byte);
        byte = (file_path[i] >> 8) & 0xFF;
        g_array_append_val (file_path_bytes, byte);
    }

    input = qmi_message_uim_read_transparent_input_new ();
    aid = g_array_new (FALSE, FALSE, sizeof (guint8)); /* empty AID */
    qmi_message_uim_read_transparent_input_set_session (
        input,
        QMI_UIM_SESSION_TYPE_PRIMARY_GW_PROVISIONING,
        aid,
        NULL);
    g_array_unref (aid);
    qmi_message_uim_read_transparent_input_set_file (input,
                                                     file_id,
                                                     file_path_bytes,
                                                     NULL);
    qmi_message_uim_read_transparent_input_set_read_information (input,
                                                                 0,
                                                                 0,
                                                                 NULL);
    g_array_unref (file_path_bytes);

    qmi_client_uim_read_transparent (QMI_CLIENT_UIM (client),
                                     input,
                                     10,
                                     NULL,
                                     (GAsyncReadyCallback)uim_read_ready,
                                     task);
    qmi_message_uim_read_transparent_input_unref (input);
}

static gchar *
load_sim_identifier_finish (MMBaseSim     *self,
                            GAsyncResult  *res,
                            GError       **error)
{
    return g_task_propagate_pointer (G_TASK (res), error);
}

static void
uim_get_iccid_ready (QmiClientUim *client,
                     GAsyncResult *res,
                     GTask        *task)
{
    GError            *error = NULL;
    g_autoptr(GArray)  read_result = NULL;
    g_autofree gchar  *raw_iccid = NULL;
    gchar             *iccid;

    read_result = uim_read_finish (client, res, &error);
    if (!read_result) {
        g_task_return_error (task, error);
        g_object_unref (task);
        return;
    }

    raw_iccid = mm_utils_bin2hexstr ((const guint8 *) read_result->data, read_result->len);
    g_assert (raw_iccid);
    iccid = mm_3gpp_parse_iccid (raw_iccid, &error);
    if (!iccid)
        g_task_return_error (task, error);
    else
        g_task_return_pointer (task, iccid, g_free);
    g_object_unref (task);
}

static void
uim_get_iccid (MMSimQmi *self,
               GTask    *task)
{
    uim_read (self,
              0x2FE2,
              mf_file_path,
              G_N_ELEMENTS (mf_file_path),
              (GAsyncReadyCallback)uim_get_iccid_ready,
              task);
}

static void
dms_uim_get_iccid_ready (QmiClientDms *client,
                         GAsyncResult *res,
                         GTask        *task)
{
    QmiMessageDmsUimGetIccidOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_dms_uim_get_iccid_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_dms_uim_get_iccid_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't get UIM ICCID: ");
        g_task_return_error (task, error);
    } else {
        const gchar *str = NULL;

        qmi_message_dms_uim_get_iccid_output_get_iccid (output, &str, NULL);
        g_task_return_pointer (task, g_strdup (str), g_free);
    }

    if (output)
        qmi_message_dms_uim_get_iccid_output_unref (output);

    g_object_unref (task);
}

static void
dms_uim_get_iccid (MMSimQmi *self,
                   GTask    *task)
{
    QmiClient *client = NULL;

    if (!ensure_qmi_client (task,
                            self,
                            QMI_SERVICE_DMS, &client))
        return;

    qmi_client_dms_uim_get_iccid (QMI_CLIENT_DMS (client),
                                  NULL,
                                  5,
                                  NULL,
                                  (GAsyncReadyCallback)dms_uim_get_iccid_ready,
                                  task);
}

static void
load_sim_identifier (MMBaseSim           *_self,
                     GAsyncReadyCallback  callback,
                     gpointer             user_data)
{
    MMSimQmi *self;
    GTask *task;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    mm_obj_dbg (self, "loading SIM identifier...");
    if (!self->priv->dms_uim_deprecated)
        dms_uim_get_iccid (self, task);
    else
        uim_get_iccid (self, task);
}

/*****************************************************************************/
/* Load IMSI */

static gchar *
load_imsi_finish (MMBaseSim     *self,
                  GAsyncResult  *res,
                  GError       **error)
{
    return g_task_propagate_pointer (G_TASK (res), error);
}

static void
uim_get_imsi_ready (QmiClientUim *client,
                    GAsyncResult *res,
                    GTask        *task)
{
    MMSimQmi          *self;
    GError            *error = NULL;
    g_autoptr(GArray)  read_result = NULL;
    g_autofree gchar  *imsi = NULL;

    self = g_task_get_source_object (task);

    read_result = uim_read_finish (client, res, &error);
    if (!read_result) {
        g_task_return_error (task, error);
        g_object_unref (task);
        return;
    }

    imsi = mm_bcd_to_string ((const guint8 *) read_result->data, read_result->len,
                             TRUE /* low_nybble_first */);
    g_assert (imsi);
    if (strlen (imsi) < 3)
        g_task_return_new_error (task,
                                 MM_CORE_ERROR,
                                 MM_CORE_ERROR_FAILED,
                                 "IMSI is malformed");
    else {
        /* EFimsi contains a length byte, followed by a nibble for parity,
         * and then followed by the actual IMSI in BCD. After converting
         * the BCD into a decimal string, we simply skip the first 3
         * decimal digits to obtain the IMSI. */

        /* Cache IMSI */
        g_free (self->priv->imsi);
        self->priv->imsi = g_strdup (imsi + 3);

        g_task_return_pointer (task, g_strdup (imsi + 3), g_free);
    }
    g_object_unref (task);
}

static void
uim_get_imsi (MMSimQmi *self,
              GTask    *task)
{
    uim_read (self,
              0x6F07,
              adf_file_path,
              G_N_ELEMENTS (adf_file_path),
              (GAsyncReadyCallback)uim_get_imsi_ready,
              task);
}

static void
dms_uim_get_imsi_ready (QmiClientDms *client,
                        GAsyncResult *res,
                        GTask        *task)
{
    MMSimQmi                      *self;
    QmiMessageDmsUimGetImsiOutput *output = NULL;
    GError                        *error = NULL;

    self = g_task_get_source_object (task);

    output = qmi_client_dms_uim_get_imsi_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_dms_uim_get_imsi_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't get UIM IMSI: ");
        g_task_return_error (task, error);
    } else {
        const gchar *str = NULL;

        qmi_message_dms_uim_get_imsi_output_get_imsi (output, &str, NULL);

        /* Cache IMSI */
        g_free (self->priv->imsi);
        self->priv->imsi = g_strdup (str);

        g_task_return_pointer (task, g_strdup (str), g_free);
    }

    if (output)
        qmi_message_dms_uim_get_imsi_output_unref (output);

    g_object_unref (task);
}

static void
dms_uim_get_imsi (MMSimQmi *self,
                  GTask    *task)
{
    QmiClient *client = NULL;

    if (!ensure_qmi_client (task,
                            self,
                            QMI_SERVICE_DMS, &client))
        return;

    qmi_client_dms_uim_get_imsi (QMI_CLIENT_DMS (client),
                                 NULL,
                                 5,
                                 NULL,
                                 (GAsyncReadyCallback)dms_uim_get_imsi_ready,
                                 task);
}

static void
load_imsi (MMBaseSim           *_self,
           GAsyncReadyCallback  callback,
           gpointer             user_data)
{
    MMSimQmi *self;
    GTask *task;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    mm_obj_dbg (self, "loading IMSI...");
    if (!self->priv->dms_uim_deprecated)
        dms_uim_get_imsi (self, task);
    else
        uim_get_imsi (self, task);
}

/*****************************************************************************/
/* Load GID1 and GID2 */

static GByteArray *
common_load_gid_finish (MMBaseSim     *self,
                        GAsyncResult  *res,
                        GError       **error)
{
    return g_task_propagate_pointer (G_TASK (res), error);
}

static void
uim_get_gid_ready (QmiClientUim *client,
                   GAsyncResult *res,
                   GTask        *task)
{
    GError            *error = NULL;
    g_autoptr(GArray)  read_result = NULL;

    read_result = uim_read_finish (client, res, &error);
    if (!read_result)
        g_task_return_error (task, error);
    else
        g_task_return_pointer (task,
                               g_byte_array_append (g_byte_array_sized_new (read_result->len),
                                                    (const guint8 *)(read_result->data),
                                                    read_result->len),
                               (GDestroyNotify)g_byte_array_unref);
    g_object_unref (task);
}

static void
common_load_gid (MMBaseSim           *self,
                 guint16              file_id,
                 GAsyncReadyCallback  callback,
                 gpointer             user_data)
{
    GTask *task;

    task = g_task_new (self, NULL, callback, user_data);

    uim_read (MM_SIM_QMI (self),
              file_id,
              adf_file_path,
              G_N_ELEMENTS (adf_file_path),
              (GAsyncReadyCallback)uim_get_gid_ready,
              task);
}

static GByteArray *
load_gid1_finish (MMBaseSim     *self,
                  GAsyncResult  *res,
                  GError       **error)
{
    return common_load_gid_finish (self, res, error);
}

static void
load_gid1 (MMBaseSim           *self,
           GAsyncReadyCallback  callback,
           gpointer             user_data)
{
    common_load_gid (self, 0x6F3E, callback, user_data);
}

static GByteArray *
load_gid2_finish (MMBaseSim     *self,
                  GAsyncResult  *res,
                  GError       **error)
{
    return common_load_gid_finish (self, res, error);
}

static void
load_gid2 (MMBaseSim           *self,
           GAsyncReadyCallback  callback,
           gpointer             user_data)
{
    common_load_gid (self, 0x6F3F, callback, user_data);
}

/*****************************************************************************/
/* Load operator identifier */

static gchar *
load_operator_identifier_finish (MMBaseSim     *self,
                                 GAsyncResult  *res,
                                 GError       **error)
{
    return g_task_propagate_pointer (G_TASK (res), error);
}

static void
uim_read_efad_ready (QmiClientUim *client,
                     GAsyncResult *res,
                     GTask        *task)
{
    MMSimQmi          *self;
    GError            *error = NULL;
    g_autoptr(GArray)  read_result = NULL;
    guint              mnc_length;

    self = g_task_get_source_object (task);

    read_result = uim_read_finish (client, res, &error);
    if (!read_result) {
        g_task_return_error (task, error);
        g_object_unref (task);
        return;
    }

    mnc_length = mm_sim_validate_mnc_length ((const guint8 *) read_result->data, read_result->len, &error);
    if (!mnc_length) {
        g_task_return_error (task, error);
    } else {
        g_task_return_pointer (task, g_strndup (self->priv->imsi, 3 + mnc_length), g_free);
    }
    g_object_unref (task);
}

static void
load_operator_identifier (MMBaseSim           *_self,
                          GAsyncReadyCallback  callback,
                          gpointer             user_data)
{
    MMSimQmi *self;
    GTask    *task;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    mm_obj_dbg (self, "loading SIM operator identifier...");

    if (!self->priv->imsi) {
        g_task_return_new_error (task,
                                 MM_CORE_ERROR,
                                 MM_CORE_ERROR_FAILED,
                                 "Couldn't load SIM operator identifier without IMSI");
        g_object_unref (task);
        return;
    }

    uim_read (self,
              0x6FAD,
              adf_file_path,
              G_N_ELEMENTS (adf_file_path),
              (GAsyncReadyCallback)uim_read_efad_ready,
              task);
}

/*****************************************************************************/
/* Load operator name */

static gchar *
load_operator_name_finish (MMBaseSim     *self,
                           GAsyncResult  *res,
                           GError       **error)
{
    return g_task_propagate_pointer (G_TASK (res), error);
}

static void
uim_read_efspn_ready (QmiClientUim *client,
                      GAsyncResult *res,
                      GTask        *task)
{
    GError            *error = NULL;
    g_autoptr(GArray)  read_result = NULL;
    gchar             *spn;

    read_result = uim_read_finish (client, res, &error);
    if (!read_result) {
        g_task_return_error (task, error);
        g_object_unref (task);
        return;
    }

    spn = mm_sim_convert_spn_to_utf8 ((const guint8 *) read_result->data, read_result->len, &error);
    if (!spn) {
        g_task_return_error (task, error);
    } else {
        g_task_return_pointer (task, spn, g_free);
    }

    g_object_unref (task);
}

static void
load_operator_name (MMBaseSim           *_self,
                    GAsyncReadyCallback  callback,
                    gpointer             user_data)
{
    MMSimQmi *self;
    GTask    *task;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    mm_obj_dbg (self, "loading SIM operator name...");

    uim_read (self,
              0x6F46,
              adf_file_path,
              G_N_ELEMENTS (adf_file_path),
              (GAsyncReadyCallback)uim_read_efspn_ready,
              task);
}

/*****************************************************************************/
/* Load preferred networks */

static GList *
load_preferred_networks_finish (MMBaseSim     *self,
                                GAsyncResult  *res,
                                GError       **error)
{
    return g_task_propagate_pointer (G_TASK (res), error);
}

static GList *
parse_get_preferred_networks (QmiMessageNasGetPreferredNetworksOutput *output)
{
    GList  *result = NULL;
    GArray *preferred_nets_array = NULL;
    GArray *preferred_nets_mnc_pcs_digit_array = NULL;
    guint   i;

    if (qmi_message_nas_get_preferred_networks_output_get_preferred_networks (output,
                                                                              &preferred_nets_array,
                                                                              NULL)) {
        qmi_message_nas_get_preferred_networks_output_get_mnc_pcs_digit_include_status (output,
                                                                                        &preferred_nets_mnc_pcs_digit_array,
                                                                                        NULL);
        for (i = 0; i < preferred_nets_array->len; i++) {
            QmiMessageNasGetPreferredNetworksOutputPreferredNetworksElement        *net;
            QmiMessageNasGetPreferredNetworksOutputMncPcsDigitIncludeStatusElement *mnc_pcs_digit = NULL;
            MMSimPreferredNetwork   *new_item;
            g_autofree gchar        *operator_code = NULL;
            MMModemAccessTechnology  act = MM_MODEM_ACCESS_TECHNOLOGY_UNKNOWN;

            net = &g_array_index (preferred_nets_array,
                                  QmiMessageNasGetPreferredNetworksOutputPreferredNetworksElement, i);
            if (preferred_nets_mnc_pcs_digit_array && i < preferred_nets_mnc_pcs_digit_array->len)
                mnc_pcs_digit = &g_array_index (preferred_nets_mnc_pcs_digit_array,
                                                QmiMessageNasGetPreferredNetworksOutputMncPcsDigitIncludeStatusElement, i);

            new_item = mm_sim_preferred_network_new ();

            if (net->mnc > 99 || (mnc_pcs_digit != NULL && mnc_pcs_digit->includes_pcs_digit))
                operator_code = g_strdup_printf ("%03d%03d", net->mcc, net->mnc);
            else
                operator_code = g_strdup_printf ("%03d%02d", net->mcc, net->mnc);
            mm_sim_preferred_network_set_operator_code (new_item, operator_code);

            if (net->radio_access_technology & QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_GSM)
                act |= MM_MODEM_ACCESS_TECHNOLOGY_GSM;
            if (net->radio_access_technology & QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_GSM_COMPACT)
                act |= MM_MODEM_ACCESS_TECHNOLOGY_GSM_COMPACT;
            if (net->radio_access_technology & QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_UTRAN)
                act |= MM_MODEM_ACCESS_TECHNOLOGY_UMTS;
            if (net->radio_access_technology & QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_EUTRAN)
                act |= MM_MODEM_ACCESS_TECHNOLOGY_LTE;
            if (net->radio_access_technology & QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_NGRAN)
                act |= MM_MODEM_ACCESS_TECHNOLOGY_5GNR;
            mm_sim_preferred_network_set_access_technology (new_item, act);

            result = g_list_append (result, new_item);
        }
    }

    return result;
}

static void
load_preferred_networks_ready (QmiClientNas *client,
                               GAsyncResult *res,
                               GTask        *task)
{
    QmiMessageNasGetPreferredNetworksOutput *output;
    GError *error = NULL;

    output = qmi_client_nas_get_preferred_networks_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_nas_get_preferred_networks_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't get preferred networks: ");
        g_task_return_error (task, error);
    } else
        g_task_return_pointer (task, parse_get_preferred_networks (output), (GDestroyNotify) mm_sim_preferred_network_list_free);

    if (output)
        qmi_message_nas_get_preferred_networks_output_unref (output);
    g_object_unref (task);
}

static void
load_preferred_networks (MMBaseSim           *self,
                         GAsyncReadyCallback  callback,
                         gpointer             user_data)
{
    GTask     *task;
    QmiClient *client = NULL;

    task = g_task_new (self, NULL, callback, user_data);
    if (!ensure_qmi_client (task,
                            MM_SIM_QMI (self),
                            QMI_SERVICE_NAS, &client))
        return;

    mm_obj_dbg (self, "loading preferred network list...");
    qmi_client_nas_get_preferred_networks (QMI_CLIENT_NAS (client),
                                           NULL,
                                           5,
                                           NULL,
                                           (GAsyncReadyCallback)load_preferred_networks_ready,
                                           task);
}

/*****************************************************************************/
/* Set preferred networks */

typedef struct {
    /* Preferred network list to be set, used for after-check comparison */
    GList    *set_list;
} SetPreferredNetworksContext;

static void
set_preferred_network_context_free (SetPreferredNetworksContext *ctx)
{
    g_list_free_full (ctx->set_list, (GDestroyNotify) mm_sim_preferred_network_free);
    g_slice_free (SetPreferredNetworksContext, ctx);
}

static gboolean
set_preferred_networks_finish (MMBaseSim *self,
                               GAsyncResult *res,
                               GError **error)
{
    return g_task_propagate_boolean (G_TASK (res), error);
}

static void
set_preferred_networks_reload_ready (MMBaseSim    *self,
                                     GAsyncResult *res,
                                     GTask        *task)
{
    GError                      *error = NULL;
    GList                       *loaded_list;
    GList                       *loaded_iter;
    GList                       *set_iter;
    SetPreferredNetworksContext *ctx;

    ctx = g_task_get_task_data (task);

    loaded_list = load_preferred_networks_finish (self, res, &error);
    if (error) {
        mm_obj_warn (self, "couldn't reload list of preferred networks: %s", error->message);
        g_task_return_error (task, error);
        g_object_unref (task);
        return;
    }

    /* Compare the set and loaded network list for differences */
    loaded_iter = loaded_list;
    set_iter = ctx->set_list;

    while (loaded_iter && set_iter) {
        const gchar             *loaded_op_code;
        const gchar             *set_op_code;
        MMModemAccessTechnology  loaded_act;
        MMModemAccessTechnology  set_act;

        loaded_op_code = mm_sim_preferred_network_get_operator_code (loaded_iter->data);
        set_op_code = mm_sim_preferred_network_get_operator_code (set_iter->data);
        loaded_act = mm_sim_preferred_network_get_access_technology (loaded_iter->data);
        set_act = mm_sim_preferred_network_get_access_technology (set_iter->data);

        /* Operator code mismatch is never expected, but check it just in case */
        if (g_strcmp0 (loaded_op_code, set_op_code)) {
            mm_obj_warn (self, "operator code mismatch, expected '%s' loaded '%s'",
                         set_op_code, loaded_op_code);
            error = g_error_new (MM_CORE_ERROR, MM_CORE_ERROR_FAILED,
                                 "Mismatch in requested and set operator code");
            break;
        }
        /* Check if there are access technology bits requested but unset */
        if ((loaded_act & set_act) != set_act) {
            MMModemAccessTechnology  unset = set_act & ~loaded_act;
            gchar                   *act;

            act = mm_modem_access_technology_build_string_from_mask (unset);
            mm_obj_warn (self, "access technologies '%s' not set for operator code '%s'",
                         act, set_op_code);
            g_free (act);
            error = g_error_new (MM_CORE_ERROR, MM_CORE_ERROR_UNSUPPORTED,
                                 "Access technology unsupported by modem or SIM");
            break;
        }
        loaded_iter = g_list_next (loaded_iter);
        set_iter = g_list_next (set_iter);
    }
    if (!error && loaded_iter == NULL && set_iter != NULL) {
        /* Not all networks were written; some modems silently discard networks
         * that exceed the SIM card capacity.
         */
        error = g_error_new (MM_CORE_ERROR, MM_CORE_ERROR_TOO_MANY,
                             "Too many networks; %u networks written",
                             g_list_length (loaded_list));
    }

    if (error) {
        /* Update the PreferredNetworks property to real SIM contents */
        mm_gdbus_sim_set_preferred_networks (MM_GDBUS_SIM (self),
                                             mm_sim_preferred_network_list_get_variant (loaded_list));
        g_task_return_error (task, error);
    } else
        g_task_return_boolean (task, TRUE);

    g_list_free_full (loaded_list, (GDestroyNotify) mm_sim_preferred_network_free);
    g_object_unref (task);
}

static void
set_preferred_networks_ready (QmiClientNas *client,
                              GAsyncResult *res,
                              GTask        *task)
{
    QmiMessageNasSetPreferredNetworksOutput *output;
    GError                                  *error = NULL;
    MMBaseSim                               *self;

    self = g_task_get_source_object (task);

    output = qmi_client_nas_set_preferred_networks_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_nas_set_preferred_networks_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't set preferred networks: ");
        g_task_return_error (task, error);
    } else {
        /* Reload the networks from modem to check whether everything was written */
        load_preferred_networks (self, (GAsyncReadyCallback) set_preferred_networks_reload_ready, task);
        qmi_message_nas_set_preferred_networks_output_unref (output);
        return;
    }

    if (output)
        qmi_message_nas_set_preferred_networks_output_unref (output);
    g_object_unref (task);
}

static void
set_preferred_networks (MMBaseSim *self,
                        GList *preferred_network_list,
                        GAsyncReadyCallback callback,
                        gpointer user_data)
{
    GTask                                  *task;
    QmiMessageNasSetPreferredNetworksInput *input;
    QmiClient                              *client = NULL;
    GArray                                 *preferred_nets_array;
    GArray                                 *preferred_nets_mnc_pcs_digit_array;
    SetPreferredNetworksContext            *ctx;

    task = g_task_new (self, NULL, callback, user_data);
    if (!ensure_qmi_client (task,
                            MM_SIM_QMI (self),
                            QMI_SERVICE_NAS, &client))
        return;

    ctx = g_slice_new0 (SetPreferredNetworksContext);
    ctx->set_list = mm_sim_preferred_network_list_copy (preferred_network_list);
    g_task_set_task_data (task, ctx, (GDestroyNotify) set_preferred_network_context_free);

    mm_obj_dbg (self, "setting preferred networks...");

    input = qmi_message_nas_set_preferred_networks_input_new ();

    preferred_nets_array = g_array_new (FALSE, TRUE, sizeof (QmiMessageNasSetPreferredNetworksInputPreferredNetworksElement));
    preferred_nets_mnc_pcs_digit_array = g_array_new (FALSE, TRUE, sizeof (QmiMessageNasSetPreferredNetworksInputMncPcsDigitIncludeStatusElement));

    while (preferred_network_list) {
        QmiMessageNasSetPreferredNetworksInputPreferredNetworksElement         preferred_nets_element;
        QmiMessageNasSetPreferredNetworksInputMncPcsDigitIncludeStatusElement  pcs_digit_element;
        const gchar                                                           *operator_code;
        MMModemAccessTechnology                                                act;

        memset (&preferred_nets_element, 0, sizeof (preferred_nets_element));
        memset (&pcs_digit_element, 0, sizeof (pcs_digit_element));

        operator_code = mm_sim_preferred_network_get_operator_code (preferred_network_list->data);
        act = mm_sim_preferred_network_get_access_technology (preferred_network_list->data);
        if (mm_3gpp_parse_operator_id (operator_code, &preferred_nets_element.mcc, &preferred_nets_element.mnc,
                                       &pcs_digit_element.includes_pcs_digit, NULL)) {
            pcs_digit_element.mcc = preferred_nets_element.mcc;
            pcs_digit_element.mnc = preferred_nets_element.mnc;

            preferred_nets_element.radio_access_technology = QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_UNSPECIFIED;
            if (act & MM_MODEM_ACCESS_TECHNOLOGY_GSM)
                preferred_nets_element.radio_access_technology |= QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_GSM;
            if (act & MM_MODEM_ACCESS_TECHNOLOGY_GSM_COMPACT)
                preferred_nets_element.radio_access_technology |= QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_GSM_COMPACT;
            if (act & MM_MODEM_ACCESS_TECHNOLOGY_UMTS)
                preferred_nets_element.radio_access_technology |= QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_UTRAN;
            if (act & MM_MODEM_ACCESS_TECHNOLOGY_LTE)
                preferred_nets_element.radio_access_technology |= QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_EUTRAN;
            if (act & MM_MODEM_ACCESS_TECHNOLOGY_5GNR)
                preferred_nets_element.radio_access_technology |= QMI_NAS_PLMN_ACCESS_TECHNOLOGY_IDENTIFIER_NGRAN;

            g_array_append_val (preferred_nets_array, preferred_nets_element);
            g_array_append_val (preferred_nets_mnc_pcs_digit_array, pcs_digit_element);
        }

        preferred_network_list = g_list_next (preferred_network_list);
    }

    qmi_message_nas_set_preferred_networks_input_set_preferred_networks (input, preferred_nets_array, NULL);
    qmi_message_nas_set_preferred_networks_input_set_mnc_pcs_digit_include_status (input, preferred_nets_mnc_pcs_digit_array, NULL);
    /* Always clear any pre-existing networks */
    qmi_message_nas_set_preferred_networks_input_set_clear_previous_preferred_networks (input, TRUE, NULL);

    qmi_client_nas_set_preferred_networks (QMI_CLIENT_NAS (client),
                                           input,
                                           5,
                                           NULL,
                                           (GAsyncReadyCallback)set_preferred_networks_ready,
                                           task);

    qmi_message_nas_set_preferred_networks_input_unref (input);
    g_array_unref (preferred_nets_array);
    g_array_unref (preferred_nets_mnc_pcs_digit_array);
}

/*****************************************************************************/
/* Send PIN */

static GError *
pin_qmi_error_to_mobile_equipment_error (GError *qmi_error)
{
    GError *me_error = NULL;

    if (g_error_matches (qmi_error,
                         QMI_PROTOCOL_ERROR,
                         QMI_PROTOCOL_ERROR_INCORRECT_PIN)) {
        me_error = g_error_new_literal (MM_MOBILE_EQUIPMENT_ERROR,
                                        MM_MOBILE_EQUIPMENT_ERROR_INCORRECT_PASSWORD,
                                        qmi_error->message);
    } else if (g_error_matches (qmi_error,
                                QMI_PROTOCOL_ERROR,
                                QMI_PROTOCOL_ERROR_PIN_BLOCKED)) {
        me_error = g_error_new_literal (MM_MOBILE_EQUIPMENT_ERROR,
                                        MM_MOBILE_EQUIPMENT_ERROR_SIM_PUK,
                                        qmi_error->message);
    }

    if (me_error) {
        g_error_free (qmi_error);
        return me_error;
    }

    return qmi_error;
}

static gboolean
send_pin_finish (MMBaseSim     *self,
                 GAsyncResult  *res,
                 GError       **error)
{
    return g_task_propagate_boolean (G_TASK (res), error);
}

static void
uim_verify_pin_ready (QmiClientUim *client,
                      GAsyncResult *res,
                      GTask        *task)
{
    QmiMessageUimVerifyPinOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_uim_verify_pin_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_uim_verify_pin_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't verify PIN: ");
        g_task_return_error (task, pin_qmi_error_to_mobile_equipment_error (error));
    } else
        g_task_return_boolean (task, TRUE);

    if (output)
        qmi_message_uim_verify_pin_output_unref (output);
    g_object_unref (task);
}

static void
uim_verify_pin (MMSimQmi *self,
                GTask    *task)
{
    QmiMessageUimVerifyPinInput *input;
    QmiClient *client = NULL;
    GArray *aid;

    if (!ensure_qmi_client (task,
                            self,
                            QMI_SERVICE_UIM, &client))
        return;

    input = qmi_message_uim_verify_pin_input_new ();
    qmi_message_uim_verify_pin_input_set_info (
        input,
        QMI_UIM_PIN_ID_PIN1,
        g_task_get_task_data (task),
        NULL);
    aid = g_array_new (FALSE, FALSE, sizeof (guint8)); /* empty AID */
    qmi_message_uim_verify_pin_input_set_session (
        input,
        QMI_UIM_SESSION_TYPE_CARD_SLOT_1,
        aid,
        NULL);
    g_array_unref (aid);
    qmi_client_uim_verify_pin (QMI_CLIENT_UIM (client),
                               input,
                               5,
                               NULL,
                               (GAsyncReadyCallback) uim_verify_pin_ready,
                               task);
    qmi_message_uim_verify_pin_input_unref (input);
}

static void
dms_uim_verify_pin_ready (QmiClientDms *client,
                          GAsyncResult *res,
                          GTask        *task)
{
    QmiMessageDmsUimVerifyPinOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_dms_uim_verify_pin_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_dms_uim_verify_pin_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't verify PIN: ");
        g_task_return_error (task, pin_qmi_error_to_mobile_equipment_error (error));
    } else
        g_task_return_boolean (task, TRUE);

    if (output)
        qmi_message_dms_uim_verify_pin_output_unref (output);
    g_object_unref (task);
}

static void
dms_uim_verify_pin (MMSimQmi *self,
                    GTask    *task)
{
    QmiMessageDmsUimVerifyPinInput *input;
    QmiClient *client = NULL;

    if (!ensure_qmi_client (NULL,
                            self,
                            QMI_SERVICE_DMS, &client)) {
        /* Very unlikely that this will ever happen, but anyway, try with
         * UIM service instead */
        uim_verify_pin (self, task);
        return;
    }

    mm_obj_dbg (self, "sending PIN...");
    input = qmi_message_dms_uim_verify_pin_input_new ();
    qmi_message_dms_uim_verify_pin_input_set_info (
        input,
        QMI_DMS_UIM_PIN_ID_PIN,
        g_task_get_task_data (task),
        NULL);
    qmi_client_dms_uim_verify_pin (QMI_CLIENT_DMS (client),
                                   input,
                                   5,
                                   NULL,
                                   (GAsyncReadyCallback) dms_uim_verify_pin_ready,
                                   task);
    qmi_message_dms_uim_verify_pin_input_unref (input);
}

static void
send_pin (MMBaseSim           *_self,
          const gchar         *pin,
          GAsyncReadyCallback  callback,
          gpointer             user_data)
{
    GTask *task;
    MMSimQmi *self;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    g_task_set_task_data (task, g_strdup (pin), g_free);

    mm_obj_dbg (self, "verifying PIN...");
    if (!self->priv->dms_uim_deprecated)
        dms_uim_verify_pin (self, task);
    else
        uim_verify_pin (self, task);
}

/*****************************************************************************/
/* Send PUK */

typedef struct {
    gchar    *puk;
    gchar    *new_pin;
} UnblockPinContext;

static void
unblock_pin_context_free (UnblockPinContext *ctx)
{
    g_free (ctx->puk);
    g_free (ctx->new_pin);
    g_slice_free (UnblockPinContext, ctx);
}

static gboolean
send_puk_finish (MMBaseSim     *self,
                 GAsyncResult  *res,
                 GError       **error)
{
    return g_task_propagate_boolean (G_TASK (res), error);
}

static void
uim_unblock_pin_ready (QmiClientUim *client,
                       GAsyncResult *res,
                       GTask        *task)
{
    QmiMessageUimUnblockPinOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_uim_unblock_pin_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_uim_unblock_pin_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't unblock PIN: ");
        g_task_return_error (task, pin_qmi_error_to_mobile_equipment_error (error));
    } else
        g_task_return_boolean (task, TRUE);

    if (output)
        qmi_message_uim_unblock_pin_output_unref (output);
    g_object_unref (task);
}

static void
uim_unblock_pin (MMSimQmi *self,
                 GTask    *task)
{
    QmiMessageUimUnblockPinInput *input;
    QmiClient *client = NULL;
    UnblockPinContext *ctx;
    GArray *aid;

    if (!ensure_qmi_client (task,
                            self,
                            QMI_SERVICE_UIM, &client))
        return;

    ctx = g_task_get_task_data (task);

    input = qmi_message_uim_unblock_pin_input_new ();
    qmi_message_uim_unblock_pin_input_set_info (
        input,
        QMI_UIM_PIN_ID_PIN1,
        ctx->puk,
        ctx->new_pin,
        NULL);
    aid = g_array_new (FALSE, FALSE, sizeof (guint8)); /* empty AID */
    qmi_message_uim_unblock_pin_input_set_session (
        input,
        QMI_UIM_SESSION_TYPE_CARD_SLOT_1,
        aid,
        NULL);
    g_array_unref (aid);
    qmi_client_uim_unblock_pin (QMI_CLIENT_UIM (client),
                                input,
                                5,
                                NULL,
                                (GAsyncReadyCallback) uim_unblock_pin_ready,
                                task);
    qmi_message_uim_unblock_pin_input_unref (input);
}

static void
dms_uim_unblock_pin_ready (QmiClientDms *client,
                           GAsyncResult *res,
                           GTask        *task)
{
    QmiMessageDmsUimUnblockPinOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_dms_uim_unblock_pin_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_dms_uim_unblock_pin_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't unblock PIN: ");
        g_task_return_error (task, pin_qmi_error_to_mobile_equipment_error (error));
    } else
        g_task_return_boolean (task, TRUE);

    if (output)
        qmi_message_dms_uim_unblock_pin_output_unref (output);
    g_object_unref (task);
}

static void
dms_uim_unblock_pin (MMSimQmi *self,
                     GTask    *task)
{
    QmiMessageDmsUimUnblockPinInput *input;
    QmiClient *client = NULL;
    UnblockPinContext *ctx;

    if (!ensure_qmi_client (NULL,
                            self,
                            QMI_SERVICE_DMS, &client)) {
        /* Very unlikely that this will ever happen, but anyway, try with
         * UIM service instead */
        uim_unblock_pin (self, task);
        return;
    }

    ctx = g_task_get_task_data (task);

    input = qmi_message_dms_uim_unblock_pin_input_new ();
    qmi_message_dms_uim_unblock_pin_input_set_info (
        input,
        QMI_DMS_UIM_PIN_ID_PIN,
        ctx->puk,
        ctx->new_pin,
        NULL);
    qmi_client_dms_uim_unblock_pin (QMI_CLIENT_DMS (client),
                                    input,
                                    5,
                                    NULL,
                                    (GAsyncReadyCallback)dms_uim_unblock_pin_ready,
                                    task);
    qmi_message_dms_uim_unblock_pin_input_unref (input);
}

static void
send_puk (MMBaseSim           *_self,
          const gchar         *puk,
          const gchar         *new_pin,
          GAsyncReadyCallback  callback,
          gpointer             user_data)
{
    GTask *task;
    UnblockPinContext *ctx;
    MMSimQmi *self;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    ctx = g_slice_new (UnblockPinContext);
    ctx->puk = g_strdup (puk);
    ctx->new_pin = g_strdup (new_pin);
    g_task_set_task_data (task, ctx, (GDestroyNotify) unblock_pin_context_free);

    mm_obj_dbg (self, "unblocking PIN...");
    if (!self->priv->dms_uim_deprecated)
        dms_uim_unblock_pin (self, task);
    else
        uim_unblock_pin (self, task);
}

/*****************************************************************************/
/* Change PIN */

typedef struct {
    gchar    *old_pin;
    gchar    *new_pin;
} ChangePinContext;

static void
change_pin_context_free (ChangePinContext *ctx)
{
    g_free (ctx->old_pin);
    g_free (ctx->new_pin);
    g_slice_free (ChangePinContext, ctx);
}

static gboolean
change_pin_finish (MMBaseSim     *self,
                   GAsyncResult  *res,
                   GError       **error)
{
    return g_task_propagate_boolean (G_TASK (res), error);
}

static void
uim_change_pin_ready (QmiClientUim *client,
                      GAsyncResult *res,
                      GTask        *task)
{
    QmiMessageUimChangePinOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_uim_change_pin_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_uim_change_pin_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't change PIN: ");
        g_task_return_error (task, pin_qmi_error_to_mobile_equipment_error (error));
    } else
        g_task_return_boolean (task, TRUE);

    if (output)
        qmi_message_uim_change_pin_output_unref (output);
    g_object_unref (task);
}

static void
uim_change_pin (MMSimQmi *self,
                GTask    *task)
{
    QmiMessageUimChangePinInput *input;
    QmiClient *client = NULL;
    ChangePinContext *ctx;
    GArray *aid;

    if (!ensure_qmi_client (task,
                            self,
                            QMI_SERVICE_UIM, &client))
        return;

    ctx = g_task_get_task_data (task);

    input = qmi_message_uim_change_pin_input_new ();
    qmi_message_uim_change_pin_input_set_info (
        input,
        QMI_UIM_PIN_ID_PIN1,
        ctx->old_pin,
        ctx->new_pin,
        NULL);
    aid = g_array_new (FALSE, FALSE, sizeof (guint8)); /* empty AID */
    qmi_message_uim_change_pin_input_set_session (
        input,
        QMI_UIM_SESSION_TYPE_CARD_SLOT_1,
        aid,
        NULL);
    g_array_unref (aid);
    qmi_client_uim_change_pin (QMI_CLIENT_UIM (client),
                               input,
                               5,
                               NULL,
                               (GAsyncReadyCallback) uim_change_pin_ready,
                               task);
    qmi_message_uim_change_pin_input_unref (input);
}

static void
dms_uim_change_pin_ready (QmiClientDms *client,
                          GAsyncResult *res,
                          GTask        *task)
{
    QmiMessageDmsUimChangePinOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_dms_uim_change_pin_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_dms_uim_change_pin_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't change PIN: ");
        g_task_return_error (task, pin_qmi_error_to_mobile_equipment_error (error));
    } else
        g_task_return_boolean (task, TRUE);

    if (output)
        qmi_message_dms_uim_change_pin_output_unref (output);
    g_object_unref (task);
}

static void
dms_uim_change_pin (MMSimQmi *self,
                    GTask    *task)
{
    QmiMessageDmsUimChangePinInput *input;
    QmiClient *client = NULL;
    ChangePinContext *ctx;

    if (!ensure_qmi_client (NULL,
                            self,
                            QMI_SERVICE_DMS, &client)) {
        /* Very unlikely that this will ever happen, but anyway, try with
         * UIM service instead */
        uim_change_pin (self, task);
        return;
    }

    ctx = g_task_get_task_data (task);

    input = qmi_message_dms_uim_change_pin_input_new ();
    qmi_message_dms_uim_change_pin_input_set_info (
        input,
        QMI_DMS_UIM_PIN_ID_PIN,
        ctx->old_pin,
        ctx->new_pin,
        NULL);
    qmi_client_dms_uim_change_pin (QMI_CLIENT_DMS (client),
                                   input,
                                   5,
                                   NULL,
                                   (GAsyncReadyCallback) dms_uim_change_pin_ready,
                                   task);
    qmi_message_dms_uim_change_pin_input_unref (input);
}

static void
change_pin (MMBaseSim           *_self,
            const gchar         *old_pin,
            const gchar         *new_pin,
            GAsyncReadyCallback  callback,
            gpointer             user_data)
{
    GTask *task;
    ChangePinContext *ctx;
    MMSimQmi *self;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    ctx = g_slice_new (ChangePinContext);
    ctx->old_pin = g_strdup (old_pin);
    ctx->new_pin = g_strdup (new_pin);
    g_task_set_task_data (task, ctx, (GDestroyNotify) change_pin_context_free);

    mm_obj_dbg (self, "changing PIN...");
    if (!self->priv->dms_uim_deprecated)
        dms_uim_change_pin (self, task);
    else
        uim_change_pin (self, task);
}

/*****************************************************************************/
/* Enable PIN */

typedef struct {
    gchar    *pin;
    gboolean  enabled;
} EnablePinContext;

static void
enable_pin_context_free (EnablePinContext *ctx)
{
    g_free (ctx->pin);
    g_slice_free (EnablePinContext, ctx);
}

static gboolean
enable_pin_finish (MMBaseSim *self,
                   GAsyncResult *res,
                   GError **error)
{
    return g_task_propagate_boolean (G_TASK (res), error);
}

static void
uim_set_pin_protection_ready (QmiClientUim *client,
                              GAsyncResult *res,
                              GTask        *task)
{
    QmiMessageUimSetPinProtectionOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_uim_set_pin_protection_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_uim_set_pin_protection_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't enable PIN: ");
        g_task_return_error (task, pin_qmi_error_to_mobile_equipment_error (error));
    } else
        g_task_return_boolean (task, TRUE);

    if (output)
        qmi_message_uim_set_pin_protection_output_unref (output);
    g_object_unref (task);
}

static void
uim_enable_pin (MMSimQmi *self,
                GTask    *task)
{
    QmiMessageUimSetPinProtectionInput *input;
    QmiClient *client = NULL;
    EnablePinContext *ctx;
    GArray *aid;

    if (!ensure_qmi_client (task,
                            MM_SIM_QMI (self),
                            QMI_SERVICE_UIM, &client))
        return;

    ctx = g_task_get_task_data (task);

    input = qmi_message_uim_set_pin_protection_input_new ();
    qmi_message_uim_set_pin_protection_input_set_info (
        input,
        QMI_UIM_PIN_ID_PIN1,
        ctx->enabled,
        ctx->pin,
        NULL);
    aid = g_array_new (FALSE, FALSE, sizeof (guint8)); /* empty AID */
    qmi_message_uim_set_pin_protection_input_set_session (
        input,
        QMI_UIM_SESSION_TYPE_CARD_SLOT_1,
        aid,
        NULL);
    g_array_unref (aid);
    qmi_client_uim_set_pin_protection (QMI_CLIENT_UIM (client),
                                       input,
                                       5,
                                       NULL,
                                       (GAsyncReadyCallback)uim_set_pin_protection_ready,
                                       task);
    qmi_message_uim_set_pin_protection_input_unref (input);
}

static void
dms_uim_set_pin_protection_ready (QmiClientDms *client,
                                  GAsyncResult *res,
                                  GTask        *task)
{
    QmiMessageDmsUimSetPinProtectionOutput *output = NULL;
    GError *error = NULL;

    output = qmi_client_dms_uim_set_pin_protection_finish (client, res, &error);
    if (!output) {
        g_prefix_error (&error, "QMI operation failed: ");
        g_task_return_error (task, error);
    } else if (!qmi_message_dms_uim_set_pin_protection_output_get_result (output, &error)) {
        g_prefix_error (&error, "Couldn't enable PIN: ");
        g_task_return_error (task, pin_qmi_error_to_mobile_equipment_error (error));
    } else
        g_task_return_boolean (task, TRUE);

    if (output)
        qmi_message_dms_uim_set_pin_protection_output_unref (output);
    g_object_unref (task);
}

static void
dms_uim_enable_pin (MMSimQmi *self,
                    GTask    *task)
{
    QmiMessageDmsUimSetPinProtectionInput *input;
    QmiClient *client = NULL;
    EnablePinContext *ctx;

    if (!ensure_qmi_client (NULL,
                            MM_SIM_QMI (self),
                            QMI_SERVICE_DMS, &client)) {
        /* Very unlikely that this will ever happen, but anyway, try with
         * UIM service instead */
        uim_enable_pin (self, task);
        return;
    }

    ctx = g_task_get_task_data (task);

    input = qmi_message_dms_uim_set_pin_protection_input_new ();
    qmi_message_dms_uim_set_pin_protection_input_set_info (
        input,
        QMI_DMS_UIM_PIN_ID_PIN,
        ctx->enabled,
        ctx->pin,
        NULL);
    qmi_client_dms_uim_set_pin_protection (QMI_CLIENT_DMS (client),
                                           input,
                                           5,
                                           NULL,
                                           (GAsyncReadyCallback)dms_uim_set_pin_protection_ready,
                                           task);
    qmi_message_dms_uim_set_pin_protection_input_unref (input);
}

static void
enable_pin (MMBaseSim           *_self,
            const gchar         *pin,
            gboolean             enabled,
            GAsyncReadyCallback  callback,
            gpointer             user_data)
{
    GTask *task;
    EnablePinContext *ctx;
    MMSimQmi *self;

    self = MM_SIM_QMI (_self);
    task = g_task_new (self, NULL, callback, user_data);

    ctx = g_slice_new (EnablePinContext);
    ctx->pin = g_strdup (pin);
    ctx->enabled = enabled;
    g_task_set_task_data (task, ctx, (GDestroyNotify) enable_pin_context_free);

    mm_obj_dbg (self, "%s PIN...", enabled ? "enabling" : "disabling");
    if (!self->priv->dms_uim_deprecated)
        dms_uim_enable_pin (self, task);
    else
        uim_enable_pin (self, task);
}

/*****************************************************************************/

MMBaseSim *
mm_sim_qmi_new_finish (GAsyncResult  *res,
                       GError       **error)
{
    GObject *source;
    GObject *sim;

    source = g_async_result_get_source_object (res);
    sim = g_async_initable_new_finish (G_ASYNC_INITABLE (source), res, error);
    g_object_unref (source);

    if (!sim)
        return NULL;

    /* Only export valid SIMs */
    mm_base_sim_export (MM_BASE_SIM (sim));

    return MM_BASE_SIM (sim);
}

void
mm_sim_qmi_new (MMBaseModem         *modem,
                gboolean             dms_uim_deprecated,
                GCancellable        *cancellable,
                GAsyncReadyCallback  callback,
                gpointer             user_data)
{
    g_async_initable_new_async (MM_TYPE_SIM_QMI,
                                G_PRIORITY_DEFAULT,
                                cancellable,
                                callback,
                                user_data,
                                MM_BASE_SIM_MODEM, modem,
                                MM_BIND_TO, G_OBJECT (modem),
                                MM_SIM_QMI_DMS_UIM_DEPRECATED, dms_uim_deprecated,
                                "active", TRUE, /* by default always active */
                                NULL);
}

/* Native EID syntax; no profile identifier can substitute for the eUICC. */
static gboolean
luma_native_eid_valid (const gchar *eid)
{
    guint i;
    if (!eid || strlen (eid) != 32)
        return FALSE;
    for (i = 0; i < 32; i++) {
        if (!g_ascii_isdigit (eid[i]))
            return FALSE;
    }
    return TRUE;
}

MMBaseSim *
mm_sim_qmi_new_initialized (MMBaseModem *modem,
                            gboolean     dms_uim_deprecated,
                            guint        slot_number,
                            gboolean     active,
                            const gchar *sim_identifier,
                            const gchar *imsi,
                            const gchar *eid,
                            const gchar *operator_identifier,
                            const gchar *operator_name,
                            const GStrv  emergency_numbers)
{
    MMBaseSim *sim;

    sim = MM_BASE_SIM (g_object_new (MM_TYPE_SIM_QMI,
                                     MM_BASE_SIM_MODEM,             modem,
                                     MM_BIND_TO,                    G_OBJECT (modem),
                                     MM_SIM_QMI_DMS_UIM_DEPRECATED, dms_uim_deprecated,
                                     MM_BASE_SIM_SLOT_NUMBER,       slot_number,
                                     "active",                      active,
                                     "sim-identifier",              sim_identifier,
                                     "imsi",                        imsi,
                                     "eid",                         eid,
                                     "operator-identifier",         operator_identifier,
                                     "operator-name",               operator_name,
                                     "emergency-numbers",           emergency_numbers,
                                     NULL));

    /* Native slot-status construction already decoded this slot's eUICC EID.
     * Do not infer an embedded card from ICCID/profile presence. */
    if (luma_native_eid_valid (eid)) {
        mm_gdbus_sim_set_sim_type (MM_GDBUS_SIM (sim), MM_SIM_TYPE_ESIM);
        /* ICCID presence proves a profile; absence does not prove the card's
         * inventory is empty (all profiles may simply be disabled). */
        if (sim_identifier && *sim_identifier)
            mm_gdbus_sim_set_esim_status (MM_GDBUS_SIM (sim), MM_SIM_ESIM_STATUS_WITH_PROFILES);
    }
    mm_base_sim_export (sim);
    return sim;
}

/*****************************************************************************/

static gboolean luma_fd_bus_handle(MmGdbusSim*,GDBusMethodInvocation*,GUnixFDList*,const gchar*,const gchar*,gpointer);
static gboolean luma_fd_bus_status(MmGdbusSim*,GDBusMethodInvocation*,const gchar*,gpointer);
static gboolean luma_fd_bus_bound(MmGdbusSim*,GDBusMethodInvocation*,GUnixFDList*,const gchar*,const gchar*,guint64,gpointer);
static gboolean luma_fd_bus_candidate_binding(MmGdbusSim*,GDBusMethodInvocation*,const gchar*,gpointer);
static gboolean luma_fd_bus_diagnostic_lost_open(MmGdbusSim*,GDBusMethodInvocation*,const gchar*,gpointer);

static void
mm_sim_qmi_init (MMSimQmi *self)
{
    g_signal_connect(self,"handle-open-luma-profile-transport",G_CALLBACK(luma_fd_bus_handle),NULL);
    g_signal_connect(self,"handle-get-luma-profile-transport-status",G_CALLBACK(luma_fd_bus_status),NULL);
    g_signal_connect(self,"handle-open-luma-profile-transport-bound",G_CALLBACK(luma_fd_bus_bound),NULL);
    g_signal_connect(self,"handle-get-luma-profile-candidate-binding",G_CALLBACK(luma_fd_bus_candidate_binding),NULL);
    g_signal_connect(self,"handle-open-luma-profile-transport-diagnostic-lost-response",G_CALLBACK(luma_fd_bus_handle),NULL);
    g_signal_connect(self,"handle-get-luma-profile-diagnostic-lost-open-status",G_CALLBACK(luma_fd_bus_diagnostic_lost_open),NULL);
    /* Initialize private data */
    self->priv = G_TYPE_INSTANCE_GET_PRIVATE (self,
                                              MM_TYPE_SIM_QMI,
                                              MMSimQmiPrivate);
}

static void
set_property (GObject      *object,
              guint         prop_id,
              const GValue *value,
              GParamSpec   *pspec)
{
    MMSimQmi *self = MM_SIM_QMI (object);

    switch (prop_id) {
    case PROP_DMS_UIM_DEPRECATED:
        self->priv->dms_uim_deprecated = g_value_get_boolean (value);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
        break;
    }
}

static void
get_property (GObject    *object,
              guint       prop_id,
              GValue     *value,
              GParamSpec *pspec)
{
    MMSimQmi *self = MM_SIM_QMI (object);

    switch (prop_id) {
    case PROP_DMS_UIM_DEPRECATED:
        g_value_set_boolean (value, self->priv->dms_uim_deprecated);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
        break;
    }
}

static void
finalize (GObject *object)
{
    MMSimQmi *self = MM_SIM_QMI (object);

    g_free (self->priv->imsi);

    G_OBJECT_CLASS (mm_sim_qmi_parent_class)->finalize (object);
}

static void
mm_sim_qmi_class_init (MMSimQmiClass *klass)
{
    GObjectClass *object_class = G_OBJECT_CLASS (klass);
    MMBaseSimClass *base_sim_class = MM_BASE_SIM_CLASS (klass);

    g_type_class_add_private (object_class, sizeof (MMSimQmiPrivate));

    object_class->get_property = get_property;
    object_class->set_property = set_property;
    object_class->finalize = finalize;

    base_sim_class->wait_sim_ready = wait_sim_ready;
    base_sim_class->wait_sim_ready_finish = wait_sim_ready_finish;
    base_sim_class->load_sim_identifier = load_sim_identifier;
    base_sim_class->load_sim_identifier_finish = load_sim_identifier_finish;
    base_sim_class->load_imsi = load_imsi;
    base_sim_class->load_imsi_finish = load_imsi_finish;
    base_sim_class->load_operator_identifier = load_operator_identifier;
    base_sim_class->load_operator_identifier_finish = load_operator_identifier_finish;
    base_sim_class->load_operator_name = load_operator_name;
    base_sim_class->load_operator_name_finish = load_operator_name_finish;
    base_sim_class->load_gid1 = load_gid1;
    base_sim_class->load_gid1_finish = load_gid1_finish;
    base_sim_class->load_gid2 = load_gid2;
    base_sim_class->load_gid2_finish = load_gid2_finish;
    base_sim_class->load_preferred_networks = load_preferred_networks;
    base_sim_class->load_preferred_networks_finish = load_preferred_networks_finish;
    base_sim_class->set_preferred_networks = set_preferred_networks;
    base_sim_class->set_preferred_networks_finish = set_preferred_networks_finish;
    base_sim_class->send_pin = send_pin;
    base_sim_class->send_pin_finish = send_pin_finish;
    base_sim_class->send_puk = send_puk;
    base_sim_class->send_puk_finish = send_puk_finish;
    base_sim_class->change_pin = change_pin;
    base_sim_class->change_pin_finish = change_pin_finish;
    base_sim_class->enable_pin = enable_pin;
    base_sim_class->enable_pin_finish = enable_pin_finish;

    properties[PROP_DMS_UIM_DEPRECATED] =
        g_param_spec_boolean (MM_SIM_QMI_DMS_UIM_DEPRECATED,
                              "DMS UIM deprecated",
                              "Whether DMS UIM commands should be skipped",
                              FALSE,
                              G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY);
    g_object_class_install_property (object_class, PROP_DMS_UIM_DEPRECATED, properties[PROP_DMS_UIM_DEPRECATED]);
}

/*****************************************************************************/
/* Luma SOURCE candidate: held MM operation lease, shared UIM client.
 * This internal API is not exported on D-Bus; mutation capability stays FALSE.
 * A failed close deliberately retains the lease for owner recovery. */
struct _MMLumaUimChannel {
    gatomicrefcount refs;
    MMBaseModem *modem;
    MMSimQmi *sim;
    QmiClientUim *client;
    GCancellable *cancel;
    GDBusConnection *connection;
    gchar *sender;
    gssize operation_id;
    guint64 generation;
    guint8 slot;
    guint8 channel;
    gboolean channel_known;
    gboolean closed;
    LumaProfileTerminal *terminal;
    gboolean io_pending;
    gboolean close_pending;
    gboolean recovery_pending;
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    gboolean diagnostic_lose_open_response;
#endif
    guint close_attempts;
    GMainContext *owner_context;
    gulong cancelled_id;
    GError *cleanup_error;
};

static MMLumaUimChannel *
luma_uim_channel_ref (MMLumaUimChannel *channel)
{
    g_atomic_ref_count_inc (&channel->refs);
    return channel;
}

static void
luma_uim_channel_unref (gpointer data)
{
    MMLumaUimChannel *channel = data;
    if (!g_atomic_ref_count_dec (&channel->refs)) return;
    /* The enclosing operation retains its own reference until closed/recovered. */
    g_assert (channel->closed);
    if (channel->cancelled_id) g_signal_handler_disconnect (channel->cancel, channel->cancelled_id);
    g_clear_pointer (&channel->owner_context, g_main_context_unref);
    g_clear_pointer (&channel->terminal, luma_profile_terminal_free);
    g_clear_error (&channel->cleanup_error);
    g_clear_object (&channel->cancel);
    g_clear_object (&channel->connection);
    g_free (channel->sender);
    g_clear_object (&channel->client);
    g_clear_object (&channel->modem);
    g_clear_object (&channel->sim);
    g_free (channel);
}

/* Transfer-full open_finish descriptors belong to the caller. Dropping a caller
 * reference does not close/release the native channel: private owner, tasks and
 * scheduled cancellation sources retain independent references until acknowledged
 * terminal close/recovery. The final destructor still requires closed TRUE. */
MMLumaUimChannel *
mm_sim_qmi_luma_profile_channel_ref (MMLumaUimChannel *channel)
{
    g_return_val_if_fail (channel != NULL, NULL);
    return luma_uim_channel_ref (channel);
}

void
mm_sim_qmi_luma_profile_channel_unref (MMLumaUimChannel *channel)
{
    g_return_if_fail (channel != NULL);
    luma_uim_channel_unref (channel);
}

static void luma_uim_channel_close (MMLumaUimChannel *channel, GTask *task);

static void
luma_uim_cancel_close_ready (MMSimQmi *self, GAsyncResult *res, gpointer unused)
{
    g_autoptr(GError) error = NULL;
    (void) unused;
    if (!g_task_propagate_boolean (G_TASK (res), &error)) {
        MMLumaUimChannel *channel = g_task_get_task_data (G_TASK (res));
        if (channel) {
            g_clear_error (&channel->cleanup_error);
            channel->cleanup_error = g_steal_pointer (&error);
        }
    }
    (void) self;
}

static gboolean
luma_uim_cancel_on_owner (gpointer data)
{
    MMLumaUimChannel *channel = data;
    /* Unknown open remains reserved: no numeric channel is guessed, and
     * disable/reprobe alone is not proof of modem channel reset. */
    if (g_cancellable_is_cancelled (channel->cancel) && channel->channel_known &&
        !channel->closed && !channel->io_pending && !channel->close_pending &&
        !channel->recovery_pending) {
        GTask *task = g_task_new (channel->sim, NULL,
                                 (GAsyncReadyCallback) luma_uim_cancel_close_ready, NULL);
        g_task_set_check_cancellable (task, FALSE);
        luma_uim_channel_close (channel, task);
    }
    return G_SOURCE_REMOVE;
}

static void
luma_uim_cancelled (GCancellable *cancel, MMLumaUimChannel *channel)
{
    GSource *source = g_idle_source_new ();
    (void) cancel;
    g_source_set_callback (source, luma_uim_cancel_on_owner,
                           luma_uim_channel_ref (channel), luma_uim_channel_unref);
    g_source_attach (source, channel->owner_context);
    g_source_unref (source);
}

static gboolean
luma_uim_closed_release (MMLumaUimChannel *channel)
{
    if (channel->sim->priv->profile_channel != channel ||
        !channel->closed || channel->io_pending || channel->close_pending ||
        !mm_base_modem_profile_operation_release (channel->modem,
             channel->operation_id, channel->generation))
        return FALSE;
    g_assert (channel->sim->priv->profile_channel == channel);
    channel->sim->priv->profile_channel = NULL;
    /* Publish only after exact native CLOSE ACK, pending I/O drained and
     * generation-bound lease release succeeded. Auto-close shares this path. */
    if(channel->terminal &&
       luma_profile_terminal_observe(channel->terminal,channel->operation_id,channel->generation,
            LUMA_PROFILE_CLOSE_CONFIRMED,g_get_monotonic_time())) {
        /* No APDU, activation code, subscriber identity or slot is published.
         * Consumers must bind this signal to the captured MM unique owner,
         * SIM path, opaque handle and generation registered before FD use. */
        mm_gdbus_sim_emit_luma_profile_transport_terminal(MM_GDBUS_SIM(channel->sim),
            channel->terminal->handle,channel->generation,LUMA_PROFILE_CLOSE_CONFIRMED);
    }
    luma_uim_channel_unref (channel);
    return TRUE;
}

static void
luma_uim_channel_close_ready (QmiClientUim *client, GAsyncResult *res, GTask *task)
{
    MMLumaUimChannel *channel = g_task_get_task_data (task);
    g_autoptr(QmiMessageUimLogicalChannelOutput) output = NULL;
    g_autoptr(GError) error = NULL;
    gboolean closed;
    channel->close_pending = FALSE;
    output = qmi_client_uim_logical_channel_finish (client, res, &error);
    closed = output && qmi_message_uim_logical_channel_output_get_result (output, &error);
    if (!closed)
        mm_obj_warn (g_task_get_source_object (task),
                     "Native UIM close refused: domain=%s code=%d attempt=%u",
                     error ? g_quark_to_string (error->domain) : "none",
                     error ? error->code : 0, channel->close_attempts);
    if (!mm_base_modem_profile_operation_io_terminal (channel->modem,
            channel->operation_id, channel->generation, closed)) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                "Stale UIM close completion; lease retained");
    } else if (!closed) {
        /* Actual completion above drained the previous I/O. Retry only this
         * owner-held numeric channel, never an uncertain OPEN or stale lease.
         * Three native 10-second calls bound the complete close recovery. */
        if(channel->close_attempts<3){
            g_clear_error(&error);
            luma_uim_channel_close(channel,task);
            return;
        }
        g_task_return_error (task, g_steal_pointer (&error));
    } else {
        channel->closed = TRUE;
        if (!luma_uim_closed_release (channel))
            g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                    "Closed channel but owner release refused");
        else {
            g_task_return_boolean (task, TRUE);
        }
    }
    g_object_unref (task);
}

/* Called only by the held operation's owner, never by a numeric caller channel.
 * No caller/sender cancellable is passed to the actual close request. */
static void
luma_uim_channel_close (MMLumaUimChannel *channel, GTask *task)
{
    g_autoptr(QmiMessageUimLogicalChannelInput) input = NULL;
    g_autoptr(GError) error = NULL;
    g_task_set_task_data (task, luma_uim_channel_ref (channel), luma_uim_channel_unref);
    if (channel->closed) {
        /* A previous actual close ACK is already retained. Retry only the
         * exact generation-bound lease release; never send another close. */
        if (luma_uim_closed_release (channel))
            g_task_return_boolean (task, TRUE);
        else
            g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                    "Closed UIM channel owner release still refused");
        g_object_unref (task);
        return;
    }
    if(channel->close_attempts>=3){
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_FAILED,
                               "Native channel close recovery exhausted");
        g_object_unref(task);return;
    }
    if (!channel->channel_known ||
        !mm_base_modem_profile_operation_begin_close (channel->modem,
             channel->operation_id, channel->generation)) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                "No terminal held UIM channel available for close");
        g_object_unref (task);
        return;
    }
    input = qmi_message_uim_logical_channel_input_new ();
    if (!qmi_message_uim_logical_channel_input_set_slot (input, channel->slot, &error) ||
        !qmi_message_uim_logical_channel_input_set_channel_id (input, channel->channel, &error)) {
        mm_base_modem_profile_operation_io_terminal (channel->modem,
             channel->operation_id, channel->generation, FALSE);
        g_task_return_error (task, g_steal_pointer (&error));
        g_object_unref (task);
        return;
    }
    channel->close_pending = TRUE;
    channel->close_attempts++;
    qmi_client_uim_logical_channel (channel->client, input, 10, NULL,
        (GAsyncReadyCallback) luma_uim_channel_close_ready, task);
}

static void
luma_uim_channel_apdu_ready (QmiClientUim *client, GAsyncResult *res, GTask *task)
{
    MMLumaUimChannel *channel = g_task_get_task_data (task);
    g_autoptr(QmiMessageUimSendApduOutput) output = NULL;
    g_autoptr(GError) error = NULL;
    GArray *response = NULL;
    channel->io_pending = FALSE;
    output = qmi_client_uim_send_apdu_finish (client, res, &error);
    if (!mm_base_modem_profile_operation_io_terminal (channel->modem,
         channel->operation_id, channel->generation, FALSE)) {
        g_clear_error (&error);
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                "Stale APDU completion; card state unknown");
    } else if (!output || !qmi_message_uim_send_apdu_output_get_result (output, &error) ||
               !qmi_message_uim_send_apdu_output_get_apdu_response (output, &response, &error)) {
        g_task_return_error (task, g_steal_pointer (&error));
    } else if (g_cancellable_is_cancelled (channel->cancel)) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                "APDU caller cancelled; card commit state requires readback");
    } else {
        g_task_return_pointer (task, g_array_ref (response), (GDestroyNotify) g_array_unref);
    }
    /* Cancellation closes only after this exact IO terminal callback. */
    luma_uim_cancel_on_owner (channel);
    g_object_unref (task);
}

static void
luma_uim_channel_apdu (MMLumaUimChannel *channel, GArray *apdu, GTask *task)
{
    g_autoptr(QmiMessageUimSendApduInput) input = NULL;
    g_autoptr(GError) error = NULL;
    if (!channel->channel_known || channel->closed || !apdu ||
        apdu->len < 4 || apdu->len > 65535 ||
        !mm_base_modem_profile_operation_begin_io (channel->modem,
            channel->operation_id, channel->generation)) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                "APDU requires current uncancelled held channel");
        g_object_unref (task);
        return;
    }
    input = qmi_message_uim_send_apdu_input_new ();
    if (!qmi_message_uim_send_apdu_input_set_slot (input, channel->slot, &error) ||
        !qmi_message_uim_send_apdu_input_set_channel_id (input, channel->channel, &error) ||
        !qmi_message_uim_send_apdu_input_set_apdu (input, apdu, &error)) {
        mm_base_modem_profile_operation_io_terminal (channel->modem,
            channel->operation_id, channel->generation, FALSE);
        g_task_return_error (task, g_steal_pointer (&error));
        g_object_unref (task);
        return;
    }
    g_task_set_task_data (task, luma_uim_channel_ref (channel), luma_uim_channel_unref);
    channel->io_pending = TRUE;
    qmi_client_uim_send_apdu (channel->client, input, 10, channel->cancel,
        (GAsyncReadyCallback) luma_uim_channel_apdu_ready, task);
}

/* This owner callback is deliberately unavailable through production D-Bus.
 * It is a SOURCE implementation requiring native MM/QMI/emergency qualification. */
static void
luma_uim_channel_open_ready (QmiClientUim *client, GAsyncResult *res, GTask *task)
{
    MMLumaUimChannel *channel = g_task_get_task_data (task);
    g_autoptr(QmiMessageUimOpenLogicalChannelOutput) output = NULL;
    g_autoptr(GError) error = NULL;
    guint8 id = 0;
    channel->io_pending = FALSE;
    output = qmi_client_uim_open_logical_channel_finish (client, res, &error);
    if (!mm_base_modem_profile_operation_io_terminal (channel->modem,
             channel->operation_id, channel->generation, FALSE)) {
        g_clear_error (&error);
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                "Stale UIM open completion; owner recovery required");
    }
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    else if (channel->diagnostic_lose_open_response && channel->terminal && output &&
             qmi_message_uim_open_logical_channel_output_get_result(output,NULL) &&
             qmi_message_uim_open_logical_channel_output_get_channel_id(output,&id,NULL) && id) {
        /* Explicit one-owner fault injection AFTER the real QMI finish and
         * lease IO-terminal callback drained. Deliberately lose the successful
         * response instead of retaining its usable numeric channel id. No safe
         * CLOSE/reset/recovery fact is fabricated; orphaned hardware channel
         * and original uncertain lease require actual native power recovery. */
        channel->diagnostic_lose_open_response=FALSE;
        channel->terminal->diagnostic_open_response_lost=TRUE;
        id=0;
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_FAILED,
            "Nonshipping diagnostic intentionally lost a drained native OPEN response");
    }
#endif
    else if (!output || !qmi_message_uim_open_logical_channel_output_get_result (output, &error) ||
               !qmi_message_uim_open_logical_channel_output_get_channel_id (output, &id, &error) || !id) {
        /* No numeric channel may be guessed for an uncertain open. The enclosing
         * owner retains channel/lease until supported device recovery qualifies. */
        if (error) g_task_return_error (task, g_steal_pointer (&error));
        else g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_FAILED,
                                     "Missing logical channel id; owner recovery required");
    } else {
        channel->channel = id;
        channel->channel_known = TRUE;
        if (g_cancellable_is_cancelled (channel->cancel)) {
            luma_uim_cancel_on_owner (channel);
            g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                     "Open completed after cancellation; owned close requested");
        } else
            g_task_return_pointer (task, luma_uim_channel_ref (channel), luma_uim_channel_unref);
    }
    g_object_unref (task);
}

/* Only for a request that has never entered begin_io. On refusal the exact
 * private owner stays retained; caller loss must not erase its recovery state. */
static gboolean
luma_uim_unissued_release (MMLumaUimChannel *channel)
{
    if (!mm_base_modem_profile_operation_release (channel->modem,
            channel->operation_id, channel->generation))
        return FALSE;
    g_assert (channel->sim->priv->profile_channel == channel);
    channel->closed = TRUE;
    channel->sim->priv->profile_channel = NULL;
    luma_uim_channel_unref (channel);
    return TRUE;
}

static void
luma_uim_channel_owner_checked (GDBusConnection *connection, GAsyncResult *res, GTask *task)
{
    MMLumaUimChannel *channel = g_task_get_task_data (task);
    g_autoptr(GVariant) reply = NULL;
    g_autoptr(GError) error = NULL;
    const gchar *owner = NULL;
    g_autoptr(QmiMessageUimOpenLogicalChannelInput) input = NULL;
    GArray *aid = g_object_get_data (G_OBJECT (task), "luma-uim-held-aid");
    reply = g_dbus_connection_call_finish (connection, res, &error);
    if (reply) g_variant_get (reply, "(&s)", &owner);
    if (!reply || g_strcmp0 (owner, channel->sender) ||
        g_dbus_connection_is_closed (connection) || g_cancellable_is_cancelled (channel->cancel)) {
        if (!luma_uim_unissued_release (channel)) {
            g_clear_error (&error);
            g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                    "Undelivered UIM owner release refused");
        } else if (error) g_task_return_error (task, g_steal_pointer (&error));
        else g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                     "UIM caller vanished before request");
        g_object_unref (task);
        return;
    }
    input = qmi_message_uim_open_logical_channel_input_new ();
    if (!qmi_message_uim_open_logical_channel_input_set_slot (input, channel->slot, &error) ||
        !qmi_message_uim_open_logical_channel_input_set_aid (input, aid, &error) ||
        !mm_base_modem_profile_operation_begin_io (channel->modem,
             channel->operation_id, channel->generation)) {
        if (!luma_uim_unissued_release (channel)) {
            g_clear_error (&error);
            g_set_error_literal (&error, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                 "Unissued UIM owner release refused");
        }
        if (error) g_task_return_error (task, g_steal_pointer (&error));
        else g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                     "Current UIM owner refused before dispatch");
        g_object_unref (task);
        return;
    }
    channel->io_pending = TRUE;
    /* Finish a dispatched open even if the caller disappears, so its returned
     * channel id can be closed by this owner. The request remains bounded10s;
     * caller cancellation blocks delivery/APDUs and triggers owned close. */
    qmi_client_uim_open_logical_channel (channel->client, input, 10, NULL,
         (GAsyncReadyCallback) luma_uim_channel_open_ready, task);
}

#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
#include "luma-native-operation-epoch.h"
#else
#include "luma-native-candidate-evidence.h"
#endif
#include "luma-readonly-es10-guard.h"
static gchar *luma_candidate_sim_identity(MMSimQmi *self,guint slot) {
 gboolean active=FALSE;
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
 const gchar *id=mm_gdbus_sim_get_eid(MM_GDBUS_SIM(self));
 active=luma_native_eid_valid(id); /* slot can exist without enabled profile */
#else
 const gchar *id=mm_gdbus_sim_get_sim_identifier(MM_GDBUS_SIM(self));
 g_object_get(self,"active",&active,NULL);
#endif
 if(!active||mm_base_sim_get_slot_number(MM_BASE_SIM(self))!=slot||
    mm_gdbus_sim_get_sim_type(MM_GDBUS_SIM(self))!=MM_SIM_TYPE_ESIM||!id||!*id)return NULL;
 /* Never emit the identifier or its digest on a bus/log/file. */
 return g_compute_checksum_for_string(G_CHECKSUM_SHA256,id,-1);
}
static gboolean luma_candidate_observe(MMSimQmi *self,MMLumaUimChannel *c,guint fact) {
 gchar *identity;gboolean ok=FALSE;
 if(!c||c->io_pending||c->close_pending||!c->closed||!c->terminal||
    !luma_profile_terminal_safe_to_release(c->terminal)||
    mm_shared_qmi_peek_client(MM_SHARED_QMI(c->modem),QMI_SERVICE_UIM,
       MM_PORT_QMI_FLAG_DEFAULT,NULL)!=QMI_CLIENT(c->client))return FALSE;
 identity=luma_candidate_sim_identity(self,c->slot);
 if(identity)ok=luma_native_evidence_observe(G_OBJECT(self),G_OBJECT(c->client),identity,
      c->slot,fact,g_get_monotonic_time());
 g_free(identity);return ok;
}
static gboolean luma_candidate_repeat(MMSimQmi *self,MMLumaUimChannel *c,guint64 epoch) {
 gchar *identity=luma_candidate_sim_identity(self,c->slot);gboolean ok=FALSE;
 QmiClient *current=mm_shared_qmi_peek_client(MM_SHARED_QMI(c->modem),QMI_SERVICE_UIM,
     MM_PORT_QMI_FLAG_DEFAULT,NULL);
 /* Feed actual mismatch/identity disappearance into the invalidating consumer;
  * do not short-circuit it and leave an old epoch reusable. */
 ok=luma_native_evidence_repeat(G_OBJECT(self),current?G_OBJECT(current):NULL,
     identity,c->slot,epoch);
 if(current!=QMI_CLIENT(c->client))ok=FALSE;
 g_free(identity);return ok;
}
#endif

/* Explicit new internal interface. Runtime enable is intentionally FALSE until
 * the shared MM/IMS queue, emergency preemption and real UIM terminal gates pass. */
void
mm_sim_qmi_luma_profile_channel_open (MMSimQmi *self, GDBusMethodInvocation *invocation,
                                    gssize operation_id, guint8 slot, GArray *aid,
                                    GAsyncReadyCallback callback, gpointer user_data)
{
    GTask *task = g_task_new (self, NULL, callback, user_data);
    g_task_set_source_tag (task, mm_sim_qmi_luma_profile_channel_open);
    #if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    const gboolean channel_probe_allowed = TRUE; /* nonshipping probe; APDU guard below */
#else
    const gboolean channel_probe_allowed = FALSE;
#endif
    MMLumaUimChannel *channel;
    QmiClient *client;
    MMBaseModem *modem = NULL;
    guint64 generation;
    GCancellable *cancel = NULL;
    if (!channel_probe_allowed) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_UNSUPPORTED,
                                "Native eSIM owner/channel/emergency qualification is incomplete");
        g_object_unref (task);
        return;
    }
    if (self->priv->profile_channel || !aid || g_array_get_element_size (aid) != sizeof (guint8) || !aid->len || aid->len > 32 || !slot) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_INVALID_ARGS,
                                "Invalid owned eUICC slot or AID");
        g_object_unref (task); return;
    }
    if (!ensure_qmi_client (task, self, QMI_SERVICE_UIM, &client)) return;
    g_object_get (self, MM_BASE_SIM_MODEM, &modem, NULL);
    if (!mm_base_modem_profile_operation_get_context (modem, operation_id,
          g_dbus_method_invocation_get_sender (invocation), &generation, &cancel)) {
        g_object_unref (modem);
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                "No current authenticated native operation owner");
        g_object_unref (task); return;
    }
    channel = g_new0 (MMLumaUimChannel, 1);g_atomic_ref_count_init (&channel->refs);
    channel->modem=modem;channel->sim=g_object_ref (self);channel->client=g_object_ref (QMI_CLIENT_UIM (client));
    channel->cancel=cancel;channel->operation_id=operation_id;channel->generation=generation;
    channel->slot=slot;channel->connection=g_object_ref (g_dbus_method_invocation_get_connection (invocation));
    channel->sender=g_strdup (g_dbus_method_invocation_get_sender (invocation));
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    channel->diagnostic_lose_open_response=g_str_equal(g_dbus_method_invocation_get_method_name(invocation),
        "OpenLumaProfileTransportDiagnosticLostResponse");
#endif
    GHashTable *terminal_registry=g_object_get_data(G_OBJECT(self),"luma-profile-terminal-registry");
    if(terminal_registry){
        GHashTableIter iter;gpointer value;
        g_hash_table_iter_init(&iter,terminal_registry);
        while(g_hash_table_iter_next(&iter,NULL,&value)){
            LumaProfileTerminal *record=value;
            if(record->operation==operation_id && record->generation==generation &&
               !g_strcmp0(record->sender,channel->sender)){
                channel->terminal=luma_profile_terminal_ref(record);break;
            }
        }
    }
    /* One private owner slot retains the channel across every callback/error.
     * Uncertain open/failed close keeps this slot and lease until device recovery. */
    self->priv->profile_channel=channel;
    channel->owner_context = g_main_context_ref_thread_default ();
    channel->cancelled_id = g_signal_connect (channel->cancel, "cancelled",
                                             G_CALLBACK (luma_uim_cancelled), channel);
    g_task_set_task_data (task, luma_uim_channel_ref (channel), luma_uim_channel_unref);
    GArray *held_aid=g_array_sized_new (FALSE,FALSE,sizeof(guint8),aid->len);
    g_array_append_vals (held_aid,aid->data,aid->len);
    g_object_set_data_full (G_OBJECT (task), "luma-uim-held-aid", held_aid, (GDestroyNotify) g_array_unref);
    g_dbus_connection_call (channel->connection, "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "GetNameOwner", g_variant_new ("(s)", channel->sender),
        G_VARIANT_TYPE ("(s)"), G_DBUS_CALL_FLAGS_NONE, 5000, NULL,
        (GAsyncReadyCallback) luma_uim_channel_owner_checked, task);
}

/* Explicit private call sites validate the exact owner-held descriptor, never
 * let a D-Bus sender provide an arbitrary slot/channel/client or generation. */
void
mm_sim_qmi_luma_profile_channel_close (MMSimQmi *self, MMLumaUimChannel *channel,
                                     GAsyncReadyCallback callback, gpointer data)
{
    GTask *task=g_task_new(self,NULL,callback,data);
    g_task_set_source_tag (task, mm_sim_qmi_luma_profile_channel_close);
    if (!channel || self->priv->profile_channel != channel) {
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_ABORTED,"Unknown held channel owner");
        g_object_unref(task);return;
    }
    luma_uim_channel_close(channel,task);
}

void
mm_sim_qmi_luma_profile_channel_send_apdu (MMSimQmi *self, MMLumaUimChannel *channel,
                                         GArray *apdu, GAsyncReadyCallback callback, gpointer data)
{
    GTask *task=g_task_new(self,NULL,callback,data);
    g_task_set_source_tag (task, mm_sim_qmi_luma_profile_channel_send_apdu);
    if (!channel || self->priv->profile_channel != channel || !apdu || g_array_get_element_size(apdu)!=sizeof(guint8)) {
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_ABORTED,"Unknown held channel or APDU encoding");
        g_object_unref(task);return;
    }
    GArray *held=g_array_sized_new(FALSE,FALSE,sizeof(guint8),apdu->len);
    g_array_append_vals(held,apdu->data,apdu->len);
    luma_uim_channel_apdu(channel,held,task);
    g_array_unref(held);
}

/* Finish methods propagate the exact asynchronous task result; they do not
 * infer channel closure or card commit from cancellation. */
MMLumaUimChannel *
mm_sim_qmi_luma_profile_channel_open_finish (MMSimQmi *self, GAsyncResult *res, GError **error)
{
    g_return_val_if_fail (g_task_is_valid (res, self), NULL);
    g_return_val_if_fail (g_task_get_source_tag (G_TASK (res)) == mm_sim_qmi_luma_profile_channel_open, NULL);
    return g_task_propagate_pointer (G_TASK (res), error);
}

gboolean
mm_sim_qmi_luma_profile_channel_close_finish (MMSimQmi *self, GAsyncResult *res, GError **error)
{
    g_return_val_if_fail (g_task_is_valid (res, self), FALSE);
    g_return_val_if_fail (g_task_get_source_tag (G_TASK (res)) == mm_sim_qmi_luma_profile_channel_close, FALSE);
    return g_task_propagate_boolean (G_TASK (res), error);
}

GArray *
mm_sim_qmi_luma_profile_channel_send_apdu_finish (MMSimQmi *self, GAsyncResult *res, GError **error)
{
    g_return_val_if_fail (g_task_is_valid (res, self), NULL);
    g_return_val_if_fail (g_task_get_source_tag (G_TASK (res)) == mm_sim_qmi_luma_profile_channel_send_apdu, NULL);
    return g_task_propagate_pointer (G_TASK (res), error);
}

/* SPDX-License-Identifier: GPL-2.0-or-later
 * Append inside maintained mm-sim-qmi.c after its existing channel callbacks.
 * SOURCE draft; no public method or qualification flag is enabled here.
 * Uses the existing authenticated MM profile operation and UIM client.
 */
#include <glib-unix.h>
#include <sys/socket.h>
#include <unistd.h>
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) && defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
#error Production owner and nonshipping fault candidate are mutually exclusive
#endif
#include "luma-owned-apdu-wire.h"
#include "luma-profile-terminal-registry-DRAFT.h"
#include "mm-auth-provider.h"
#include "mm-iface-op-lock.h"
typedef struct {
    gatomicrefcount refs;
    MMSimQmi *sim;
    MMBaseModem *modem;
    GDBusMethodInvocation *invocation;
    GCancellable *cancel;
    MMLumaUimChannel *channel;
    gssize operation;
    guint64 generation;
    LumaProfileTerminal *terminal;
    guint8 slot;
    gint fd;
    guint source,expiry;
    gulong cancelled;
    GMainContext *context;
    guint32 sequence;
    gboolean connected,pending,dead,released,finished;
    guint64 expected_candidate_epoch;
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    guint64 candidate_epoch;
    guint candidate_sender_watch;
    gboolean candidate_sender_vanished;
    LumaReadonlyEs10 readonly;
    guint readonly_tag,readonly_complete;
    guint8 readonly_body[8192];
    gsize readonly_used;
#endif
} LumaFdOwner;

static void fd_owner_retire(LumaFdOwner *owner);
static gboolean fd_owner_read(gint fd,GIOCondition condition,gpointer data);
static gboolean fd_owner_expired(gpointer data);
static void fd_owner_destroy(LumaFdOwner *o) {
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    if(o->candidate_sender_watch)g_bus_unwatch_name(o->candidate_sender_watch);
#endif
    if(o->source)g_source_remove(o->source);
    if(o->expiry)g_source_remove(o->expiry);
    if(o->fd>=0)close(o->fd);
    g_clear_pointer(&o->terminal,luma_profile_terminal_free);
    g_clear_pointer(&o->channel,mm_sim_qmi_luma_profile_channel_unref);
    g_clear_object(&o->cancel);g_clear_object(&o->invocation);
    g_clear_object(&o->modem);g_clear_object(&o->sim);
    #if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    memset(o->readonly_body,0,sizeof o->readonly_body);
#endif
    g_main_context_unref(o->context);g_free(o);
}
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
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
static void luma_candidate_sender_vanished(GDBusConnection *connection,const gchar *name,gpointer data){
    LumaFdOwner *o=data;
    if(!o->finished&&!g_dbus_connection_is_closed(connection)&&
       !g_strcmp0(name,g_dbus_method_invocation_get_sender(o->invocation))){
        o->candidate_sender_vanished=TRUE;fd_owner_retire(o);
    }
}
#endif
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
static gboolean fd_owner_reply(LumaFdOwner *o,const guint8 *bytes,guint32 length) {
    guint8 header[LUMA_APDU_HEADER];
    luma_wire_put(header,LUMA_APDU_MAGIC);luma_wire_put(header+4,o->sequence);
    luma_wire_put(header+8,0);luma_wire_put(header+12,length);
    struct iovec iov[2]={{header,sizeof header},{(gpointer)bytes,length}};
    struct msghdr m={0};m.msg_iov=iov;m.msg_iovlen=length?2:1;
    return !o->dead&&sendmsg(o->fd,&m,MSG_NOSIGNAL|MSG_DONTWAIT)==(ssize_t)(sizeof header+length);
}
static void fd_owner_watch(LumaFdOwner *o) {
    if(o->dead){fd_owner_retire(o);return;}
    g_assert(!o->source&&!o->pending);
    GSource *source=g_unix_fd_source_new(o->fd,G_IO_IN|G_IO_HUP|G_IO_ERR);
    g_source_set_callback(source,G_SOURCE_FUNC(fd_owner_read),o,NULL);
    o->source=g_source_attach(source,o->context);g_source_unref(source);
}

typedef struct { GDBusMethodInvocation *invocation;MMBaseModem *modem;guint8 slot; gssize operation; gint64 deadline; gchar *reservation_token,*reservation_ims_owner; guint64 expected_candidate_epoch; } LumaFdRequest;
static void fd_request_free(gpointer data){
    LumaFdRequest *r=data;g_object_unref(r->invocation);g_object_unref(r->modem);g_free(r->reservation_token);g_free(r->reservation_ims_owner);g_free(r);
}
typedef struct { gint fd; gchar *handle; } LumaFdResult;
static void fd_result_free(gpointer data){
    LumaFdResult *r=data;if(r->fd>=0)close(r->fd);g_free(r->handle);g_free(r);
}
/* SPDX-License-Identifier: GPL-2.0-or-later
 * Integrate after LumaFdResult, before fd_authority_repeated. No native OPEN
 * or APDU is dispatched until the authenticated IMS handshake commits.
 * Request carries the broker's already-issued 32-byte reservation token.
 * ACK: (accepted, reservation-token, MM-unique-owner, handle, generation).
 */
typedef struct {
    GTask *task;
    LumaFdOwner *owner;
    LumaFdResult *result;
    gchar *ims_owner;
} LumaFdBinding;
static void fd_binding_abort(LumaFdBinding *b,const gchar *reason) {
    LumaFdOwner *o=b->owner;
    /* At this point no FD has been published, no read source attached, and
     * no UIM channel/IO exists. Release checks exact operation/generation.
     * This fact is NO_CHANNEL_DISPATCHED, never a native CLOSE ACK. */
    g_assert(!o->channel && !o->pending && !o->source);
    gboolean released=mm_base_modem_profile_operation_release(o->modem,o->operation,o->generation);
    if(released && luma_profile_terminal_observe(o->terminal,o->operation,o->generation,
        LUMA_PROFILE_NO_CHANNEL_DISPATCHED,g_get_monotonic_time()))
        mm_gdbus_sim_emit_luma_profile_transport_terminal(MM_GDBUS_SIM(o->sim),
            o->terminal->handle,o->generation,LUMA_PROFILE_NO_CHANNEL_DISPATCHED);
    fd_result_free(b->result);fd_owner_free(o);
    g_task_return_new_error(b->task,MM_CORE_ERROR,MM_CORE_ERROR_ABORTED,"%s",reason);
    g_object_unref(b->task);g_free(b->ims_owner);g_free(b);
}
static void fd_binding_owner_repeated(GDBusConnection *bus,GAsyncResult *res,LumaFdBinding *b) {
    LumaFdRequest *r=g_task_get_task_data(b->task);LumaFdOwner *o=b->owner;
    g_autoptr(GVariant) reply=g_dbus_connection_call_finish(bus,res,NULL);
    const gchar *name=NULL;if(reply)g_variant_get(reply,"(&s)",&name);
    guint64 generation=0;GCancellable *cancel=NULL;
    gboolean current=mm_base_modem_profile_operation_get_context(o->modem,o->operation,
        g_dbus_method_invocation_get_sender(r->invocation),&generation,&cancel);
    gboolean cancelled=!current || !cancel || g_cancellable_is_cancelled(cancel);
    g_clear_object(&cancel);
    if(!name || g_strcmp0(name,b->ims_owner) || cancelled || generation!=o->generation ||
       g_get_monotonic_time()>r->deadline){
        fd_binding_abort(b,"IMS owner/broker lease changed before transport publish");return;
    }
    o->cancelled=g_cancellable_connect(o->cancel,G_CALLBACK(fd_owner_cancelled),fd_owner_ref(o),fd_owner_unref);
    GSource *expiry=g_timeout_source_new_seconds(300);
    g_source_set_callback(expiry,fd_owner_expired,o,NULL);
    o->expiry=g_source_attach(expiry,o->context);g_source_unref(expiry);
    /* Only this committed branch attaches the packet reader/publishes FD. */
    fd_owner_watch(o);
    g_task_return_pointer(b->task,b->result,fd_result_free);
    g_object_unref(b->task);g_free(b->ims_owner);g_free(b);
}
static void fd_binding_ack(GDBusConnection *bus,GAsyncResult *res,LumaFdBinding *b) {
    LumaFdRequest *r=g_task_get_task_data(b->task);LumaFdOwner *o=b->owner;
    g_autoptr(GVariant) reply=g_dbus_connection_call_finish(bus,res,NULL);
    gboolean accepted=FALSE;const gchar *token=NULL,*mm_owner=NULL,*handle=NULL;guint64 generation=0;
    if(reply)g_variant_get(reply,"(b&s&s&st)",&accepted,&token,&mm_owner,&handle,&generation);
    if(!accepted || g_strcmp0(token,r->reservation_token) ||
       g_strcmp0(mm_owner,g_dbus_connection_get_unique_name(bus)) ||
       g_strcmp0(handle,o->terminal->handle) || generation!=o->generation ||
       g_get_monotonic_time()>r->deadline){
        fd_binding_abort(b,"Native IMS transport binding ACK refused");return;
    }
    g_dbus_connection_call(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
        "org.freedesktop.DBus","GetNameOwner",g_variant_new("(s)","net.catcrafts.IMS1"),
        G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,1500,NULL,
        (GAsyncReadyCallback)fd_binding_owner_repeated,b);
}
static void fd_binding_uid(GDBusConnection *bus,GAsyncResult *res,LumaFdBinding *b) {
    LumaFdRequest *r=g_task_get_task_data(b->task);LumaFdOwner *o=b->owner;
    g_autoptr(GVariant) reply=g_dbus_connection_call_finish(bus,res,NULL);
    guint uid=G_MAXUINT;if(reply)g_variant_get(reply,"(u)",&uid);
    if(uid!=0 || g_get_monotonic_time()>r->deadline){fd_binding_abort(b,"IMS root owner refused");return;}
    g_dbus_connection_call(bus,b->ims_owner,"/net/catcrafts/IMS1","net.catcrafts.IMS1",
        "BindESimTransport",g_variant_new("(sssst)",
            g_dbus_method_invocation_get_sender(r->invocation),r->reservation_token,
            mm_base_sim_get_path(MM_BASE_SIM(o->sim)),o->terminal->handle,o->generation),
        G_VARIANT_TYPE("(bssst)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,5000,NULL,
        (GAsyncReadyCallback)fd_binding_ack,b);
}
static void fd_binding_name(GDBusConnection *bus,GAsyncResult *res,LumaFdBinding *b) {
    g_autoptr(GVariant) reply=g_dbus_connection_call_finish(bus,res,NULL);
    const gchar *name=NULL;if(reply)g_variant_get(reply,"(&s)",&name);
    LumaFdRequest *r=g_task_get_task_data(b->task);
    if(!name || !g_dbus_is_unique_name(name) || g_strcmp0(name,r->reservation_ims_owner)){
        fd_binding_abort(b,"Reservation's captured IMS unique owner changed");return;
    }
    b->ims_owner=g_strdup(name);
    g_dbus_connection_call(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
        "org.freedesktop.DBus","GetConnectionUnixUser",g_variant_new("(s)",name),
        G_VARIANT_TYPE("(u)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,1500,NULL,
        (GAsyncReadyCallback)fd_binding_uid,b);
}
static void fd_binding_begin(GTask *task,LumaFdOwner *owner,LumaFdResult *result) {
    LumaFdBinding *b=g_new0(LumaFdBinding,1);b->task=task;b->owner=owner;b->result=result;
    GDBusConnection *bus=g_dbus_method_invocation_get_connection(owner->invocation);
    g_dbus_connection_call(bus,"org.freedesktop.DBus","/org/freedesktop/DBus",
        "org.freedesktop.DBus","GetNameOwner",g_variant_new("(s)","net.catcrafts.IMS1"),
        G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,1500,NULL,
        (GAsyncReadyCallback)fd_binding_name,b);
}

static void fd_authority_repeated(GDBusConnection *connection,GAsyncResult *res,GTask *task) {
    LumaFdRequest *r=g_task_get_task_data(task);
    MMBaseModem *modem=r->modem;
    g_autoptr(GError) error=NULL;
    g_autoptr(GVariant) reply=g_dbus_connection_call_finish(connection,res,&error);
    const gchar *owner=NULL;
    if(reply)g_variant_get(reply,"(&s)",&owner);
    const gchar *actual_sender=g_dbus_method_invocation_get_sender(r->invocation);
    if(!owner || g_strcmp0(owner,actual_sender) || g_get_monotonic_time()>r->deadline) {
        mm_iface_op_lock_unlock(MM_IFACE_OP_LOCK(modem),r->operation);
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_UNAUTHORIZED,
                               "Native broker ownership changed during authorization");
        g_object_unref(task);return;
    }
    guint64 generation=0;GCancellable *cancel=NULL;
    const gchar *sender=g_dbus_method_invocation_get_sender(r->invocation);
    if(!mm_base_modem_profile_operation_get_context(modem,r->operation,sender,&generation,&cancel)){
        /* No I/O was dispatched; ordinary MM operation unlock is safe here. */
        mm_iface_op_lock_unlock(MM_IFACE_OP_LOCK(modem),r->operation);
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_ABORTED,"Caller disappeared before owned FD admission");
        g_object_unref(task);return;
    }
    gint sockets[2];
    if(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC|SOCK_NONBLOCK,0,sockets)){
        mm_base_modem_profile_operation_release(modem,r->operation,generation);g_object_unref(cancel);
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_FAILED,"Private APDU socket could not be created");
        g_object_unref(task);return;
    }
    gint capacity=131104;
    if(setsockopt(sockets[0],SOL_SOCKET,SO_SNDBUF,&capacity,sizeof capacity)||
       setsockopt(sockets[0],SOL_SOCKET,SO_RCVBUF,&capacity,sizeof capacity)||
       setsockopt(sockets[1],SOL_SOCKET,SO_SNDBUF,&capacity,sizeof capacity)||
       setsockopt(sockets[1],SOL_SOCKET,SO_RCVBUF,&capacity,sizeof capacity)){
        close(sockets[0]);close(sockets[1]);mm_base_modem_profile_operation_release(modem,r->operation,generation);
        g_object_unref(cancel);g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_FAILED,"Private APDU socket limits refused");
        g_object_unref(task);return;
    }
    LumaFdOwner *o=g_new0(LumaFdOwner,1);g_atomic_ref_count_init(&o->refs);
    o->sim=g_object_ref(g_task_get_source_object(task));o->modem=g_object_ref(modem);
    o->invocation=g_object_ref(r->invocation);o->cancel=cancel;o->operation=r->operation;
    o->expected_candidate_epoch=r->expected_candidate_epoch;
    o->generation=generation;o->slot=r->slot;o->fd=sockets[0];o->context=g_main_context_ref_thread_default();
    GHashTable *registry=g_object_get_data(G_OBJECT(o->sim),"luma-profile-terminal-registry");
    if(!registry){
        registry=luma_profile_terminal_registry_new();
        g_object_set_data_full(G_OBJECT(o->sim),"luma-profile-terminal-registry",registry,
                              (GDestroyNotify)g_hash_table_unref);
    }
    o->terminal=luma_profile_terminal_issue(registry,sender,r->operation,generation,g_get_monotonic_time());
    if(!o->terminal){
        close(sockets[1]);mm_base_modem_profile_operation_release(modem,r->operation,generation);
        fd_owner_free(o);g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_IN_PROGRESS,
                                                "Owned transport terminal registry is full");
        g_object_unref(task);return;
    }
    luma_profile_terminal_ref(o->terminal); /* owner reference survives registry TTL eviction */
    LumaFdResult *result=g_new0(LumaFdResult,1);result->fd=sockets[1];
    result->handle=g_strdup(o->terminal->handle);
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    o->candidate_sender_watch=g_bus_watch_name_on_connection(
        g_dbus_method_invocation_get_connection(o->invocation),sender,G_BUS_NAME_WATCHER_FLAGS_NONE,
        NULL,luma_candidate_sender_vanished,o,NULL);
#endif
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
    {
        gchar *identity=luma_candidate_sim_identity(o->sim,o->slot);
        QmiClient *current=mm_shared_qmi_peek_client(MM_SHARED_QMI(o->modem),QMI_SERVICE_UIM,
            MM_PORT_QMI_FLAG_DEFAULT,NULL);
        o->candidate_epoch=luma_native_operation_admit(G_OBJECT(o->sim),current?G_OBJECT(current):NULL,identity,o->slot);
        g_free(identity);
        if(!o->candidate_epoch || (o->expected_candidate_epoch && o->candidate_epoch!=o->expected_candidate_epoch)) {
            fd_result_free(result);fd_owner_retire(o);
            g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_ABORTED,"Current native eUICC binding refused before channel dispatch");
            g_object_unref(task);return;
        }
    }
#endif
    fd_binding_begin(task,o,result); /* reader/FD remain private until exact ACK */
}
static void fd_authorized(MMBaseModem *modem,GAsyncResult *res,GTask *task){
    LumaFdRequest *r=g_task_get_task_data(task);g_autoptr(GError) error=NULL;
    gssize operation=mm_iface_op_lock_authorize_and_lock_finish(MM_IFACE_OP_LOCK(modem),res,&error);
    if(operation<0){g_task_return_error(task,g_steal_pointer(&error));g_object_unref(task);return;}
    r->operation=operation;
    GDBusConnection *connection=g_dbus_method_invocation_get_connection(r->invocation);
    g_dbus_connection_call(connection,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus",
        "GetNameOwner",g_variant_new("(s)","org.projectluma.ESim1"),G_VARIANT_TYPE("(s)"),
        G_DBUS_CALL_FLAGS_NO_AUTO_START,5000,NULL,(GAsyncReadyCallback)fd_authority_repeated,task);
}
static void fd_broker_uid(GDBusConnection *connection,GAsyncResult *res,GTask *task){
    LumaFdRequest *r=g_task_get_task_data(task);g_autoptr(GError) error=NULL;
    g_autoptr(GVariant) reply=g_dbus_connection_call_finish(connection,res,&error);guint32 uid=G_MAXUINT32;
    if(reply)g_variant_get(reply,"(u)",&uid);
    if(!reply||uid!=0){
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_UNAUTHORIZED,"Native eSIM broker caller credentials refused");
        g_object_unref(task);return;
    }
    /* The real invocation, not a caller-supplied sender string, enters MM's
     * existing polkit/queued lease and sender-death machinery. */
    mm_base_modem_profile_authorize_and_lock(r->modem,r->invocation,MM_AUTHORIZATION_DEVICE_CONTROL,
        (GAsyncReadyCallback)fd_authorized,task);
}
static void fd_broker_name(GDBusConnection *connection,GAsyncResult *res,GTask *task){
    LumaFdRequest *r=g_task_get_task_data(task);g_autoptr(GError) error=NULL;
    g_autoptr(GVariant) reply=g_dbus_connection_call_finish(connection,res,&error);const gchar *owner=NULL;
    if(reply)g_variant_get(reply,"(&s)",&owner);
    const gchar *sender=g_dbus_method_invocation_get_sender(r->invocation);
    if(!owner||!g_dbus_is_unique_name(sender)||g_strcmp0(owner,sender)){
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_UNAUTHORIZED,"Native eSIM broker bus ownership refused");
        g_object_unref(task);return;
    }
    g_dbus_connection_call(connection,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus",
        "GetConnectionUnixUser",g_variant_new("(s)",sender),G_VARIANT_TYPE("(u)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,
        5000,NULL,(GAsyncReadyCallback)fd_broker_uid,task);
}
void mm_sim_qmi_luma_profile_fd_open(MMSimQmi *sim,GDBusMethodInvocation *invocation,guint8 slot,
                                   const gchar *reservation_token,const gchar *reservation_ims_owner,
                                   GAsyncReadyCallback callback,gpointer user_data){
    GTask *task=g_task_new(sim,NULL,callback,user_data);
    g_task_set_source_tag(task,mm_sim_qmi_luma_profile_fd_open);
    /* No environment override or diagnostic enable flag. Qualification stays
     * false until native queue/emergency/channel-terminal gates actually pass. */
    #if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    const gboolean transport_probe_allowed=TRUE; /* existing native auth/IMS binding unchanged */
#else
    const gboolean transport_probe_allowed=FALSE;
#endif
    guint token_length=0,i;
    if(reservation_token)while(token_length<33 && reservation_token[token_length])token_length++;
    gboolean token_valid=token_length==32;
    for(i=0;token_valid && i<32;i++)
        token_valid=g_ascii_isdigit(reservation_token[i]) ||
                    (reservation_token[i]>='a' && reservation_token[i]<='f');
    guint native_slot=0;g_object_get(sim,MM_BASE_SIM_SLOT_NUMBER,&native_slot,NULL);
    if(!transport_probe_allowed||!token_valid||!reservation_ims_owner||
       !g_dbus_is_unique_name(reservation_ims_owner)||!slot||native_slot!=slot||
       !g_dbus_method_invocation_get_sender(invocation)){
        g_task_return_new_error(task,MM_CORE_ERROR,MM_CORE_ERROR_UNSUPPORTED,"Native MM-owned APDU transport remains unqualified");
        g_object_unref(task);return;
    }
    LumaFdRequest *r=g_new0(LumaFdRequest,1);r->invocation=g_object_ref(invocation);r->slot=slot;
    if(g_str_equal(g_dbus_method_invocation_get_method_name(invocation),"OpenLumaProfileTransportBound")) {
        GVariant *body=g_dbus_message_get_body(g_dbus_method_invocation_get_message(invocation));
        GVariant *epoch=g_variant_get_child_value(body,2);
        r->expected_candidate_epoch=g_variant_get_uint64(epoch);g_variant_unref(epoch);
    }
    r->reservation_token=g_strdup(reservation_token);r->reservation_ims_owner=g_strdup(reservation_ims_owner);
    r->deadline=g_get_monotonic_time()+20*G_USEC_PER_SEC;
    g_object_get(sim,MM_BASE_SIM_MODEM,&r->modem,NULL);
    g_task_set_task_data(task,r,fd_request_free);
    GDBusConnection *connection=g_dbus_method_invocation_get_connection(invocation);
    g_dbus_connection_call(connection,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus",
        "GetNameOwner",g_variant_new("(s)","org.projectluma.ESim1"),G_VARIANT_TYPE("(s)"),
        G_DBUS_CALL_FLAGS_NO_AUTO_START,5000,NULL,(GAsyncReadyCallback)fd_broker_name,task);
}
gint mm_sim_qmi_luma_profile_fd_open_finish(MMSimQmi *sim,GAsyncResult *res,gchar **handle,GError **error){
    g_return_val_if_fail(handle!=NULL,-1);*handle=NULL;
    g_return_val_if_fail(g_task_is_valid(res,sim),-1);
    g_return_val_if_fail(g_task_get_source_tag(G_TASK(res))==mm_sim_qmi_luma_profile_fd_open,-1);
    LumaFdResult *result=g_task_propagate_pointer(G_TASK(res),error);if(!result)return -1;
    gint fd=result->fd;*handle=g_steal_pointer(&result->handle);g_free(result);return fd;
}
typedef struct { MMBaseModem *modem; LumaProfileTerminal *record; } LumaRecoveryFence;
static void fd_recovery_fence_free(gpointer data){
    LumaRecoveryFence *f=data;g_object_unref(f->modem);luma_profile_terminal_free(f->record);g_free(f);
}
static gboolean fd_recovery_expired(gpointer data){
    LumaRecoveryFence *f=data;LumaProfileTerminal *r=f->record;
    if(r&&!luma_profile_terminal_safe_to_release(r)){
        luma_profile_terminal_observe(r,r->operation,r->generation,LUMA_PROFILE_RECOVERY_REFUSED,
                                     g_get_monotonic_time());
        mm_base_modem_profile_operation_recovery_refuse(f->modem,r->operation,r->generation);
    }
    return G_SOURCE_REMOVE;
}
static void fd_schedule_recovery_fence(LumaFdOwner *o){
    LumaRecoveryFence *f=g_new0(LumaRecoveryFence,1);
    f->modem=g_object_ref(o->modem);f->record=luma_profile_terminal_ref(o->terminal);
    GSource *source=g_timeout_source_new_seconds(30);
    g_source_set_callback(source,fd_recovery_expired,f,fd_recovery_fence_free);
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
static gboolean fd_owner_expired(gpointer data) {
    LumaFdOwner *o=data;o->expiry=0;
    g_cancellable_cancel(o->cancel);fd_owner_retire(o);return G_SOURCE_REMOVE;
}
static void fd_owner_opened(MMSimQmi *sim,GAsyncResult *res,gpointer data) {
    LumaFdOwner *o=data;g_autoptr(GError) error=NULL;
    o->channel=mm_sim_qmi_luma_profile_channel_open_finish(sim,res,&error);
    if (!o->channel || !o->channel->channel_known ||
        o->channel->channel < 1 || o->channel->channel > 3)
        mm_obj_warn (sim, "Native profile open completion refused: domain=%s code=%d known=%u channel=%u",
                     error ? g_quark_to_string (error->domain) : "none",
                     error ? error->code : 0, o->channel ? o->channel->channel_known : 0,
                     o->channel && o->channel->channel_known ? o->channel->channel : 0);
    o->pending=FALSE;
    if(!o->channel||!o->channel->channel_known||o->channel->channel<1||
       o->channel->channel>3||o->dead||g_cancellable_is_cancelled(o->cancel)){
        fd_owner_retire(o);return;
    }
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    {
        gchar *identity=luma_candidate_sim_identity(o->sim,o->slot);
        QmiClient *current=mm_shared_qmi_peek_client(MM_SHARED_QMI(o->modem),QMI_SERVICE_UIM,
            MM_PORT_QMI_FLAG_DEFAULT,NULL);
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
        if(!luma_native_operation_repeat(G_OBJECT(o->sim),current?G_OBJECT(current):NULL,
             identity,o->slot,o->candidate_epoch))o->candidate_epoch=0;
#else
        o->candidate_epoch=luma_native_evidence_admit(G_OBJECT(o->sim),
            current?G_OBJECT(current):NULL,identity,o->slot,g_get_monotonic_time());
#endif
        g_free(identity);
        if(current!=QMI_CLIENT(o->channel->client)||
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
           !o->candidate_epoch||
#endif
           (o->expected_candidate_epoch&&o->candidate_epoch!=o->expected_candidate_epoch)){
            fd_owner_retire(o);return;
        }
        o->terminal->candidate_epoch=o->candidate_epoch;
    }
#endif
    guint8 channel=o->channel->channel;
    if(!fd_owner_reply(o,&channel,1)){fd_owner_retire(o);return;}
    fd_owner_watch(o);
}
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
static gboolean readonly_tlv(const guint8 *p,gsize n,guint tag,gsize *header,gsize *body){
 gsize i=0,len=0;guint actual,lead,count;if(n<2)return FALSE;actual=p[i++];
 if((actual&31)==31){if(i>=n||p[i]&0x80)return FALSE;actual=(actual<<8)|p[i++];}
 if(actual!=tag||i>=n)return FALSE;
 lead=p[i++];if(!(lead&0x80))len=lead;else{
  count=lead&127;if(!count||count>2||i+count>n||p[i]==0)return FALSE;
  while(count--)len=(len<<8)|p[i++];
  if(len<128)return FALSE;
 }
 if(len>n-i)return FALSE;
 *header=i;*body=len;return TRUE;
}
static gboolean luma_candidate_readonly_response(LumaFdOwner *o,const guint8 *p,gsize n) {
 gsize h=0,b=0,at=0,fh=0,fn=0;guint profiles=0;gboolean ok=FALSE;
 if(!luma_readonly_es10_response(&o->readonly,p,n)||n-2>sizeof o->readonly_body-o->readonly_used)return FALSE;
 memcpy(o->readonly_body+o->readonly_used,p,n-2);o->readonly_used+=n-2;
 if(o->readonly.more)return TRUE;
 if(!readonly_tlv(o->readonly_body,o->readonly_used,0xbf00|o->readonly_tag,&h,&b)||h+b!=o->readonly_used)goto done;
 if(o->readonly_tag==0x2d){
  if(!readonly_tlv(o->readonly_body+h,b,0xa0,&fh,&fn)||fh+fn!=b)goto done;
  while(at<fn){gsize ph=0,pn=0;
   if(++profiles>64||!readonly_tlv(o->readonly_body+h+fh+at,fn-at,0xe3,&ph,&pn))goto done;
   at+=ph+pn;
  }
 }
 o->readonly_complete|=o->readonly_tag==0x20?1:2;ok=TRUE;
 done:
 memset(o->readonly_body,0,sizeof o->readonly_body);o->readonly_used=0;return ok;
}
#endif
static void fd_owner_transmitted(MMSimQmi *sim,GAsyncResult *res,gpointer data) {
    LumaFdOwner *o=data;g_autoptr(GError) error=NULL;
    g_autoptr(GArray) bytes=mm_sim_qmi_luma_profile_channel_send_apdu_finish(sim,res,&error);
    o->pending=FALSE;
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    if(!o->candidate_epoch&&(!bytes||!luma_candidate_readonly_response(o,(guint8*)bytes->data,bytes->len))){
        fd_owner_retire(o);return;
    }
#endif
    if(!bytes||bytes->len<2||bytes->len>LUMA_APDU_MAX||o->dead||
       g_cancellable_is_cancelled(o->cancel)||!fd_owner_reply(o,(guint8*)bytes->data,bytes->len)){
        fd_owner_retire(o);return;
    }
    /* Never publish an APDU payload or status on a public bus or diagnostic log. */
    fd_owner_watch(o);
}
static gboolean fd_owner_read(gint fd,GIOCondition condition,gpointer data) {
    LumaFdOwner *o=data;guint8 packet[LUMA_APDU_HEADER+LUMA_APDU_MAX];
    volatile guint8 *wipe=packet;
    gsize i;
    struct iovec iov={packet,sizeof packet};
    struct msghdr m={0};
    ssize_t n;
    guint32 op,size;
    guint8 *payload=packet+LUMA_APDU_HEADER;
    o->source=0;
    if(condition&(G_IO_HUP|G_IO_ERR)){fd_owner_retire(o);goto out;}
    m.msg_iov=&iov;m.msg_iovlen=1;
    n=recvmsg(fd,&m,MSG_DONTWAIT);
    if(n<(ssize_t)LUMA_APDU_HEADER||(m.msg_flags&(MSG_TRUNC|MSG_CTRUNC))||
       luma_wire_get(packet)!=LUMA_APDU_MAGIC||o->sequence>=LUMA_APDU_CALLS||
       luma_wire_get(packet+4)!=o->sequence+1||luma_wire_get(packet+12)!=(guint32)n-LUMA_APDU_HEADER){
        fd_owner_retire(o);goto out;
    }
    o->sequence++;op=luma_wire_get(packet+8);size=(guint32)n-LUMA_APDU_HEADER;
    if(g_cancellable_is_cancelled(o->cancel)){fd_owner_retire(o);goto out;}
    if(op==LUMA_CONNECT&&!o->connected&&!size){
        o->connected=TRUE;if(!fd_owner_reply(o,NULL,0))fd_owner_retire(o);else fd_owner_watch(o);
    }else if(op==LUMA_OPEN&&o->connected&&!o->released&&!o->channel&&size==16&&
             !memcmp(payload,"\xA0\x00\x00\x05\x59\x10\x10\xFF\xFF\xFF\xFF\x89\x00\x00\x01\x00",16)){
        g_autoptr(GArray) aid=g_array_sized_new(FALSE,FALSE,1,size);g_array_append_vals(aid,payload,size);
        o->pending=TRUE;mm_sim_qmi_luma_profile_channel_open(o->sim,o->invocation,o->operation,
            o->slot,aid,(GAsyncReadyCallback)fd_owner_opened,o);
    }else if(op==LUMA_TRANSMIT&&o->channel&&!o->channel->closed&&size>=4&&
             !(payload[0]&0x4c)&&(payload[0]&3)==o->channel->channel){
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
        if(o->candidate_epoch){
            if(!luma_candidate_repeat(o->sim,o->channel,o->candidate_epoch)){fd_owner_retire(o);goto out;}
        }else{
            if(!luma_readonly_es10_request(&o->readonly,o->channel->channel,payload,size)){fd_owner_retire(o);goto out;}
            if(size==8&&payload[1]==0xe2){o->readonly_tag=payload[6];o->readonly_used=0;}
        }
#endif
        g_autoptr(GArray) apdu=g_array_sized_new(FALSE,FALSE,1,size);g_array_append_vals(apdu,payload,size);
        o->pending=TRUE;mm_sim_qmi_luma_profile_channel_send_apdu(o->sim,o->channel,apdu,
            (GAsyncReadyCallback)fd_owner_transmitted,o);
    }else if(op==LUMA_CLOSE&&o->channel&&size==1&&payload[0]==o->channel->channel){
        /* Terminal close releases the underlying lease: no further APDU is
         * allowed on this FD, and an acknowledgement is sent only on success. */
        o->pending=TRUE;mm_sim_qmi_luma_profile_channel_close(o->sim,o->channel,
            (GAsyncReadyCallback)fd_owner_closed,o);
    }else if(op==LUMA_DISCONNECT&&!size){
        if(o->released)fd_owner_reply(o,NULL,0);
        fd_owner_retire(o);
    }else fd_owner_retire(o);
out:
    /* packet contains confidential bytes; wipe regardless of request outcome. */
    for(i=0;i<sizeof packet;i++)wipe[i]=0;
    return G_SOURCE_REMOVE;
}

/* No caller-supplied generation is accepted. The authenticated invocation's
 * sender selects only its opaque record in this exact MM owner's SIM registry.
 * Recovery refusal is not permission to release IMS or reuse the UIM client. */
gboolean mm_sim_qmi_luma_profile_fd_status(MMSimQmi *sim,GDBusMethodInvocation *invocation,
    const gchar *handle,guint64 *generation,guint *state,gboolean *safe_to_release,GError **error) {
    GHashTable *registry=g_object_get_data(G_OBJECT(sim),"luma-profile-terminal-registry");
    LumaProfileTerminal *r=registry?luma_profile_terminal_lookup(registry,handle,
        g_dbus_method_invocation_get_sender(invocation)):NULL;
    if(!r){g_set_error(error,MM_CORE_ERROR,MM_CORE_ERROR_UNAUTHORIZED,"Owned transport handle refused");return FALSE;}
    if(r->state==LUMA_PROFILE_RECOVERY_PENDING && g_get_monotonic_time()>=r->recovery_deadline)
        luma_profile_terminal_observe(r,r->operation,r->generation,LUMA_PROFILE_RECOVERY_REFUSED,g_get_monotonic_time());
    *generation=r->generation;*state=r->state;*safe_to_release=luma_profile_terminal_safe_to_release(r);return TRUE;
}

/* SPDX-License-Identifier: GPL-2.0-or-later
 * Append after receiver in mm-sim-qmi.c. Signal hookup belongs in QMI init.
 * Signatures replayed by actual gdbus-codegen with maintained MmGdbus prefix.
 */
static void luma_fd_bus_ready(MMSimQmi *self,GAsyncResult *res,gpointer data){
    GDBusMethodInvocation *invocation=data;g_autoptr(GError) error=NULL;
    g_autofree gchar *handle=NULL;
    gint fd=mm_sim_qmi_luma_profile_fd_open_finish(self,res,&handle,&error);
    if(fd<0){g_dbus_method_invocation_return_gerror(invocation,error);g_object_unref(invocation);return;}
    g_autoptr(GUnixFDList) list=g_unix_fd_list_new();
    gint index=g_unix_fd_list_append(list,fd,&error);close(fd);
    if(index<0)g_dbus_method_invocation_return_gerror(invocation,error);
    else if(g_str_equal(g_dbus_method_invocation_get_method_name(invocation),"OpenLumaProfileTransportBound"))
        mm_gdbus_sim_complete_open_luma_profile_transport_bound(MM_GDBUS_SIM(self),invocation,list,g_variant_new_handle(index),handle);
    else if(g_str_equal(g_dbus_method_invocation_get_method_name(invocation),"OpenLumaProfileTransportDiagnosticLostResponse"))
        mm_gdbus_sim_complete_open_luma_profile_transport_diagnostic_lost_response(MM_GDBUS_SIM(self),invocation,list,g_variant_new_handle(index),handle);
    else mm_gdbus_sim_complete_open_luma_profile_transport(MM_GDBUS_SIM(self),invocation,list,g_variant_new_handle(index),handle);
    g_object_unref(invocation);
}
static gboolean luma_fd_bus_handle(MmGdbusSim *object,GDBusMethodInvocation *invocation,
                                   GUnixFDList *input,const gchar *reservation_token,const gchar *reservation_ims_owner,
                                   gpointer unused){
    (void)unused;
#ifndef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
    if(g_str_equal(g_dbus_method_invocation_get_method_name(invocation),"OpenLumaProfileTransportDiagnosticLostResponse")
#ifndef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
       ||g_str_equal(g_dbus_method_invocation_get_method_name(invocation),"OpenLumaProfileTransportBound")
#endif
       ){
        g_dbus_method_invocation_return_error_literal(invocation,MM_CORE_ERROR,MM_CORE_ERROR_UNSUPPORTED,
            "Nonshipping native diagnostic is unavailable");return TRUE;
    }
#endif
    GVariant *body=g_dbus_message_get_body(g_dbus_method_invocation_get_message(invocation));
    if(!body || g_variant_get_size(body)>4096){
        g_dbus_method_invocation_return_error_literal(invocation,MM_CORE_ERROR,MM_CORE_ERROR_INVALID_ARGS,
            "Native transport request is oversized");return TRUE;
    }
    if(input&&g_unix_fd_list_get_length(input)){
        g_dbus_method_invocation_return_error_literal(invocation,MM_CORE_ERROR,MM_CORE_ERROR_INVALID_ARGS,
            "The native transport method does not accept caller file descriptors");return TRUE;
    }
    guint slot=0;g_object_get(object,MM_BASE_SIM_SLOT_NUMBER,&slot,NULL);
    if(!MM_IS_SIM_QMI(object)||!slot||slot>255){
        g_dbus_method_invocation_return_error_literal(invocation,MM_CORE_ERROR,MM_CORE_ERROR_UNSUPPORTED,
            "No observed native QMI SIM slot");return TRUE;
    }
    mm_sim_qmi_luma_profile_fd_open(MM_SIM_QMI(object),invocation,(guint8)slot,
        reservation_token,reservation_ims_owner,
        (GAsyncReadyCallback)luma_fd_bus_ready,g_object_ref(invocation));return TRUE;
}
/* In mm_sim_qmi_init(), after the existing private state initialization:
 * g_signal_connect(self,"handle-open-luma-profile-transport",
 *                  G_CALLBACK(luma_fd_bus_handle),NULL);
 * This exposes an explicit UNSUPPORTED reply while transport_qualified FALSE.
 */

static gboolean luma_fd_bus_status(MmGdbusSim *object,GDBusMethodInvocation *invocation,
                                  const gchar *handle,gpointer unused){
    (void)unused;guint64 generation=0;guint state=0;gboolean safe=FALSE;g_autoptr(GError) error=NULL;
    if(!MM_IS_SIM_QMI(object) || !mm_sim_qmi_luma_profile_fd_status(MM_SIM_QMI(object),invocation,
        handle,&generation,&state,&safe,&error)){
        if(error)g_dbus_method_invocation_return_gerror(invocation,error);
        else g_dbus_method_invocation_return_error_literal(invocation,MM_CORE_ERROR,MM_CORE_ERROR_UNSUPPORTED,
            "No native transport terminal record");
        return TRUE;
    }
    mm_gdbus_sim_complete_get_luma_profile_transport_status(object,invocation,generation,state,safe);
    return TRUE;
}
/* QMI init also connects handle-get-luma-profile-transport-status to luma_fd_bus_status. */

/* Nonshipping SOURCE candidate. No public transport qualification is changed. */
#include "mm-uim-deactivation-recovery.inc"

gboolean
mm_sim_qmi_luma_profile_recovery_context (MMSimQmi *self, gssize *operation, guint64 *generation)
{
    MMLumaUimChannel *c = self->priv->profile_channel;
    if (!c || c->closed || !g_cancellable_is_cancelled (c->cancel)) return FALSE;
    *operation = c->operation_id;
    *generation = c->generation;
    return TRUE;
}

static void
luma_profile_recovered (MMSimQmi *self, GAsyncResult *res, GTask *task)
{
    MMLumaUimChannel *c = g_task_get_task_data (task);
    GError *error = NULL;
    gint64 now;
    LumaProfileTerminal proof;
    c->recovery_pending = FALSE;
    if (!g_task_propagate_boolean (G_TASK (res), &error)) {
        g_task_return_error (task, error);
        g_object_unref (task);
        return;
    }
    /* Validate the exact terminal transition on a copy before releasing the
     * lease. No asynchronous dispatch occurs between this check and publication. */
    now = g_get_monotonic_time ();
    proof = *c->terminal;
    if (self->priv->profile_channel != c || c->io_pending || c->close_pending ||
        c->terminal->terminal_at || !luma_profile_terminal_observe (&proof,
            c->operation_id, c->generation, LUMA_PROFILE_NATIVE_TEARDOWN_CONFIRMED, now)) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_FAILED,
                                "Recovered native terminal generation refused");
        g_object_unref (task);
        return;
    }
    c->closed = TRUE; /* real deactivation proof, even if lease release fails */
    if (!mm_base_modem_profile_operation_release (c->modem, c->operation_id, c->generation)) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_FAILED,
                                "Recovered native channel release refused");
        g_object_unref (task);
        return;
    }
    if (!luma_profile_terminal_observe (c->terminal, c->operation_id, c->generation,
            LUMA_PROFILE_NATIVE_TEARDOWN_CONFIRMED, now)) {
        /* Never report Reset success or discard the retained owner on an
         * unexpected publication refusal after release. */
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_FAILED,
                                "Recovered native terminal publication refused");
        g_object_unref (task);
        return;
    }
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    luma_candidate_observe(self,c,LUMA_NATIVE_FACT_RECOVERY);
#endif
    self->priv->profile_channel = NULL;
    mm_gdbus_sim_emit_luma_profile_transport_terminal (MM_GDBUS_SIM (self),
        c->terminal->handle, c->generation, LUMA_PROFILE_NATIVE_TEARDOWN_CONFIRMED);
    luma_uim_channel_unref (c); /* enclosing SIM owner; task still owns a ref */
    g_task_return_boolean (task, TRUE);
    g_object_unref (task);
}

void
mm_sim_qmi_luma_profile_recover (MMSimQmi *self, gssize operation, guint64 generation,
                                 GAsyncReadyCallback callback, gpointer data)
{
    GTask *task = g_task_new (self, NULL, callback, data);
    MMLumaUimChannel *c = self->priv->profile_channel;
    if (!c || c->operation_id != operation || c->generation != generation ||
        c->closed || c->io_pending || c->close_pending || c->recovery_pending ||
        !g_cancellable_is_cancelled (c->cancel) || !c->terminal || !c->slot ||
        c->terminal->operation != c->operation_id || c->terminal->generation != c->generation ||
        mm_shared_qmi_peek_client (MM_SHARED_QMI (c->modem), QMI_SERVICE_UIM,
            MM_PORT_QMI_FLAG_DEFAULT, NULL) != QMI_CLIENT (c->client)) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_IN_PROGRESS,
                                "Exact cancelled native channel callbacks have not drained");
        g_object_unref (task);
        return;
    }
    g_task_set_task_data (task, luma_uim_channel_ref (c), luma_uim_channel_unref);
    c->recovery_pending = TRUE;
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
    luma_native_operation_invalidate(G_OBJECT(self));
#endif
    luma_deactivation_recover (self, c->modem, c->operation_id, c->generation, c->slot,
                              (GAsyncReadyCallback) luma_profile_recovered, task);
}

gboolean
mm_sim_qmi_luma_profile_recover_finish (MMSimQmi *self, GAsyncResult *res, GError **error)
{
    g_return_val_if_fail (g_task_is_valid (res, self), FALSE);
    return g_task_propagate_boolean (G_TASK (res), error);
}

/* Exact old terminal record and current native SIM/client/epoch are both required.
 * A broker-local profile snapshot or a caller-supplied epoch cannot create proof. */
static gboolean luma_fd_bus_candidate_binding(MmGdbusSim *object,GDBusMethodInvocation *invocation,
                                              const gchar *handle,gpointer unused){
    guint64 epoch=0,generation=0;
    (void)unused;
#if defined(LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY) || defined(LUMA_NATIVE_ESIM_PRODUCTION_OWNER)
    if(MM_IS_SIM_QMI(object)){
        MMSimQmi *sim=MM_SIM_QMI(object);
        GHashTable *registry=g_object_get_data(G_OBJECT(sim),"luma-profile-terminal-registry");
        LumaProfileTerminal *record=registry?luma_profile_terminal_lookup(registry,handle,
            g_dbus_method_invocation_get_sender(invocation)):NULL;
        MMBaseModem *modem=NULL;
        guint slot=0;
        gchar *identity=NULL;
        QmiClient *client=NULL;
        g_object_get(sim,MM_BASE_SIM_MODEM,&modem,MM_BASE_SIM_SLOT_NUMBER,&slot,NULL);
        if(modem&&MM_IS_SHARED_QMI(modem))client=mm_shared_qmi_peek_client(MM_SHARED_QMI(modem),
            QMI_SERVICE_UIM,MM_PORT_QMI_FLAG_DEFAULT,NULL);
        identity=luma_candidate_sim_identity(sim,slot);
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
        if(record&&luma_native_operation_repeat(G_OBJECT(sim),client?G_OBJECT(client):NULL,
            identity,slot,record->candidate_epoch))epoch=record->candidate_epoch;
#else
        epoch=luma_native_evidence_admit(G_OBJECT(sim),client?G_OBJECT(client):NULL,
            identity,slot,g_get_monotonic_time());
#endif
        if(!record||!luma_profile_terminal_safe_to_release(record)||
           record->state!=LUMA_PROFILE_CLOSE_CONFIRMED||!record->candidate_epoch||
           epoch!=record->candidate_epoch)epoch=0;
        if(epoch)generation=record->generation;
        g_free(identity);g_clear_object(&modem);
    }
#endif
    if(!epoch){g_dbus_method_invocation_return_error_literal(invocation,MM_CORE_ERROR,
        MM_CORE_ERROR_ABORTED,"Native terminal SIM binding is no longer current");return TRUE;}
    mm_gdbus_sim_complete_get_luma_profile_candidate_binding(object,invocation,epoch,generation);
    return TRUE;
}
static gboolean luma_fd_bus_bound(MmGdbusSim *object,GDBusMethodInvocation *invocation,
    GUnixFDList *input,const gchar *token,const gchar *ims_owner,guint64 epoch,gpointer unused){
    if(!epoch){g_dbus_method_invocation_return_error_literal(invocation,MM_CORE_ERROR,
        MM_CORE_ERROR_INVALID_ARGS,"Native candidate epoch is required");return TRUE;}
    return luma_fd_bus_handle(object,invocation,input,token,ims_owner,unused);
}

static gboolean luma_fd_bus_diagnostic_lost_open(MmGdbusSim *object,GDBusMethodInvocation *invocation,
 const gchar *handle,gpointer unused){
    gboolean lost=FALSE;
    (void)unused;
#ifdef LUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY
    if(MM_IS_SIM_QMI(object)){
        MMSimQmi *sim=MM_SIM_QMI(object);
        GHashTable *registry=g_object_get_data(G_OBJECT(sim),"luma-profile-terminal-registry");
        LumaProfileTerminal *record=registry?luma_profile_terminal_lookup(registry,handle,
            g_dbus_method_invocation_get_sender(invocation)):NULL;
        MMLumaUimChannel *channel=sim->priv->profile_channel;
        lost=record&&channel&&record==channel->terminal&&record->diagnostic_open_response_lost&&
            record->state==LUMA_PROFILE_RECOVERY_PENDING&&record->operation==channel->operation_id&&
            record->generation==channel->generation&&!channel->io_pending&&!channel->close_pending&&
            !channel->closed&&!channel->channel_known&&
            !luma_profile_terminal_safe_to_release(record);
    }
#else
    g_dbus_method_invocation_return_error_literal(invocation,MM_CORE_ERROR,MM_CORE_ERROR_UNSUPPORTED,
        "Nonshipping native diagnostic is unavailable");return TRUE;
#endif
    mm_gdbus_sim_complete_get_luma_profile_diagnostic_lost_open_status(object,invocation,lost);
    return TRUE;
}

void
mm_sim_qmi_invalidate_native_operation (MMBaseSim *sim)
{
#ifdef LUMA_NATIVE_ESIM_PRODUCTION_OWNER
    luma_native_operation_invalidate (G_OBJECT (sim));
#else
    (void) sim;
#endif
}
