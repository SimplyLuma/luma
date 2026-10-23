/* SPDX-License-Identifier: GPL-2.0-or-later
 * SOURCE draft: append inside mm-sim-qmi.c. Public transport stays FALSE.
 * ETSI TS 102 221 V18.2.0 §8.7: actual UICC deactivation closes channels.
 * Neither a QMI reset ACK nor caller/process death proves deactivation.
 */
typedef struct {
    MMSimQmi *sim;
    MMBaseModem *modem;
    QmiClientUim *client;
    gssize operation;
    guint64 generation;
    guint8 slot;
    guint phase, attempts;
    gint64 deadline;
    gboolean off_ack, power_down, on_ack, ready, pending;
    gboolean on_attempted;
    gboolean off_attempted, proof_lost;
} LumaDeactivationRecovery;

static void luma_deactivation_dispatch (GTask *task);
static void luma_deactivation_free (gpointer data)
{
    LumaDeactivationRecovery *r = data;
    g_assert (!r->pending);
    g_clear_object (&r->client);
    g_clear_object (&r->modem);
    g_clear_object (&r->sim);
    g_free (r);
}
static gboolean luma_deactivation_current (LumaDeactivationRecovery *r)
{
    guint slot = 0;
    QmiClient *current;
    g_object_get (r->sim, MM_BASE_SIM_SLOT_NUMBER, &slot, NULL);
    current = mm_shared_qmi_peek_client (MM_SHARED_QMI (r->modem),
                QMI_SERVICE_UIM, MM_PORT_QMI_FLAG_DEFAULT, NULL);
    return slot == r->slot && current == QMI_CLIENT (r->client);
}
static void luma_deactivation_fail (GTask *task)
{
    LumaDeactivationRecovery *r = g_task_get_task_data (task);
    g_assert (!r->pending);
    mm_base_modem_profile_operation_recovery_refuse (r->modem, r->operation, r->generation);
    g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_FAILED,
                            "Native same-slot deactivation recovery unqualified");
    g_object_unref (task);
}
static gboolean luma_deactivation_terminal_io (LumaDeactivationRecovery *r)
{
    gboolean terminal;
    g_assert (r->pending);
    terminal = mm_base_modem_profile_operation_io_terminal (r->modem,
                  r->operation, r->generation, FALSE);
    r->pending = FALSE;
    return terminal && luma_deactivation_current (r);
}
static gboolean luma_deactivation_card (LumaDeactivationRecovery *r,
                                        QmiMessageUimGetCardStatusOutput *output,
                                        gboolean want_ready)
{
    GArray *cards = NULL;
    QmiMessageUimGetCardStatusOutputCardStatusCardsElement *card;
    guint i;
    if (!qmi_message_uim_get_card_status_output_get_result (output, NULL) ||
        !qmi_message_uim_get_card_status_output_get_card_status (
            output, NULL, NULL, NULL, NULL, &cards, NULL) ||
        !cards || !r->slot || r->slot > cards->len || cards->len > 255)
        return FALSE;
    card = &g_array_index (cards, QmiMessageUimGetCardStatusOutputCardStatusCardsElement,
                          r->slot - 1);
    if (!want_ready)
        return card->card_state == QMI_UIM_CARD_STATE_ERROR &&
               card->error_code == QMI_UIM_CARD_ERROR_POWER_DOWN;
    if (card->card_state != QMI_UIM_CARD_STATE_PRESENT || !card->applications ||
        card->applications->len > 255)
        return FALSE;
    for (i = 0; i < card->applications->len; i++) {
        QmiMessageUimGetCardStatusOutputCardStatusCardsElementApplicationsElementV2 *app;
        app = &g_array_index (card->applications,
               QmiMessageUimGetCardStatusOutputCardStatusCardsElementApplicationsElementV2, i);
        if (app->type == QMI_UIM_CARD_APPLICATION_TYPE_USIM &&
            app->state == QMI_UIM_CARD_APPLICATION_STATE_READY)
            return TRUE;
    }
    return FALSE;
}
static void luma_deactivation_status_ready (QmiClientUim *client, GAsyncResult *res, GTask *task)
{
    LumaDeactivationRecovery *r = g_task_get_task_data (task);
    g_autoptr(QmiMessageUimGetCardStatusOutput) output = NULL;
    gboolean terminal, observed;
    output = qmi_client_uim_get_card_status_finish (client, res, NULL);
    terminal = luma_deactivation_terminal_io (r);
    observed = terminal && output && luma_deactivation_card (r, output, r->phase == 3);
    if (!terminal) {
        r->proof_lost = TRUE;
        if (r->phase == 1 && r->off_attempted && !r->on_attempted) {
            r->phase = 2;
            luma_deactivation_dispatch (task);
        } else luma_deactivation_fail (task);
        return;
    }
    if (r->phase == 1) {
        if (observed) r->power_down = TRUE;
        if (observed || r->attempts >= 3) { r->phase = 2; r->attempts = 0; }
    } else {
        if (observed) r->ready = TRUE;
        if (observed || r->attempts >= 3) r->phase = 4;
    }
    luma_deactivation_dispatch (task);
}
static void luma_deactivation_off_ready (QmiClientUim *client, GAsyncResult *res, GTask *task)
{
    LumaDeactivationRecovery *r = g_task_get_task_data (task);
    g_autoptr(QmiMessageUimPowerOffSimOutput) output = NULL;
    gboolean terminal;
    output = qmi_client_uim_power_off_sim_finish (client, res, NULL);
    terminal = luma_deactivation_terminal_io (r);
    if (!terminal) r->proof_lost = TRUE;
    r->off_ack = output && qmi_message_uim_power_off_sim_output_get_result (output, NULL);
    /* Attempt reactivation even if OFF ACK is missing; this alone is never proof. */
    r->phase = (terminal && r->off_ack) ? 1 : 2;
    luma_deactivation_dispatch (task);
}
static void luma_deactivation_on_ready (QmiClientUim *client, GAsyncResult *res, GTask *task)
{
    LumaDeactivationRecovery *r = g_task_get_task_data (task);
    g_autoptr(QmiMessageUimPowerOnSimOutput) output = NULL;
    gboolean terminal;
    output = qmi_client_uim_power_on_sim_finish (client, res, NULL);
    terminal = luma_deactivation_terminal_io (r);
    if (!terminal) { luma_deactivation_fail (task); return; }
    r->on_ack = output && qmi_message_uim_power_on_sim_output_get_result (output, NULL);
    r->phase = r->on_ack ? 3 : 4;
    r->attempts = 0;
    luma_deactivation_dispatch (task);
}
static void luma_deactivation_dispatch (GTask *task)
{
    LumaDeactivationRecovery *r = g_task_get_task_data (task);
    g_assert (!r->pending);
    /* After OFF dispatch, one exact-slot ON attempt remains required even if
     * the proof deadline expired. Its own 8s callback remains accounted; no
     * expired recovery can become terminal without fresh READY evidence. */
    if (g_get_monotonic_time () >= r->deadline && r->phase == 1) r->phase = 2;
    if (!luma_deactivation_current (r)) {
        r->proof_lost = TRUE;
        /* Retained original lease/client/slot may restore power exactly once;
         * this is cleanup only, never readiness or terminal evidence. */
        if (r->off_attempted && !r->on_attempted) r->phase = 2;
        else { luma_deactivation_fail (task); return; }
    }
    if (g_get_monotonic_time () >= r->deadline &&
        !(r->phase == 2 && r->off_attempted && !r->on_attempted)) {
        luma_deactivation_fail (task); return;
    }
    if (mm_base_modem_profile_operation_recovery_voice_pending (r->modem, r->operation, r->generation)) {
        if (r->phase == 0) { luma_deactivation_fail (task); return; }
        /* OFF already dispatched: prioritize restoring power, not further DOWN
         * polling. Missing observed DOWN still forbids native terminal release. */
        if (r->phase == 1) { r->phase = 2; r->attempts = 0; }
    }
    if (r->phase == 2 && r->on_attempted) {
        luma_deactivation_fail (task); return;
    }
    if (r->phase == 4) {
        if (r->proof_lost || !r->off_ack || !r->power_down || !r->on_ack || !r->ready ||
            !mm_base_modem_profile_operation_deactivation_confirmed (
                 r->modem, r->operation, r->generation)) {
            luma_deactivation_fail (task); return;
        }
        /* Caller still must release exact lease and publish distinct terminal state4.
         * No logical CLOSE ACK is fabricated; no process-death authority is used. */
        g_task_return_boolean (task, TRUE);
        g_object_unref (task);
        return;
    }
    if (!mm_base_modem_profile_operation_begin_recovery_io (
             r->modem, r->operation, r->generation)) {
        luma_deactivation_fail (task); return;
    }
    r->pending = TRUE;
    if (r->phase == 0) {
        g_autoptr(QmiMessageUimPowerOffSimInput) input = qmi_message_uim_power_off_sim_input_new ();
        if (!qmi_message_uim_power_off_sim_input_set_slot (input, r->slot, NULL)) {
            luma_deactivation_terminal_io (r); luma_deactivation_fail (task); return;
        }
        r->off_attempted = TRUE;
        qmi_client_uim_power_off_sim (r->client, input, 8, NULL,
            (GAsyncReadyCallback) luma_deactivation_off_ready, task);
    } else if (r->phase == 2) {
        g_autoptr(QmiMessageUimPowerOnSimInput) input = qmi_message_uim_power_on_sim_input_new ();
        r->on_attempted = TRUE;
        if (!qmi_message_uim_power_on_sim_input_set_slot (input, r->slot, NULL)) {
            luma_deactivation_terminal_io (r); luma_deactivation_fail (task); return;
        }
        qmi_client_uim_power_on_sim (r->client, input, 8, NULL,
            (GAsyncReadyCallback) luma_deactivation_on_ready, task);
    } else {
        r->attempts++;
        qmi_client_uim_get_card_status (r->client, NULL, 2, NULL,
            (GAsyncReadyCallback) luma_deactivation_status_ready, task);
    }
}
static void luma_deactivation_recover (MMSimQmi *sim, MMBaseModem *modem,
                                      gssize operation, guint64 generation, guint8 slot,
                                      GAsyncReadyCallback callback, gpointer data)
{
    GTask *task = g_task_new (sim, NULL, callback, data);
    LumaDeactivationRecovery *r = g_new0 (LumaDeactivationRecovery, 1);
    QmiClient *client;
    r->sim = g_object_ref (sim); r->modem = g_object_ref (modem);
    r->operation = operation; r->generation = generation; r->slot = slot;
    r->deadline = g_get_monotonic_time () + 40 * G_USEC_PER_SEC;
    g_task_set_task_data (task, r, luma_deactivation_free);
    client = mm_shared_qmi_peek_client (MM_SHARED_QMI (modem), QMI_SERVICE_UIM,
                                       MM_PORT_QMI_FLAG_DEFAULT, NULL);
    if (!client || !QMI_IS_CLIENT_UIM (client) || !slot ||
        !mm_base_modem_profile_operation_claim_recovery (modem, operation, generation)) {
        g_task_return_new_error (task, MM_CORE_ERROR, MM_CORE_ERROR_ABORTED,
                                "Current cancelled native operation recovery refused");
        g_object_unref (task); return;
    }
    r->client = g_object_ref (QMI_CLIENT_UIM (client));
    luma_deactivation_dispatch (task);
}
