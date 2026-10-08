/* SPDX-License-Identifier: GPL-2.0-or-later */

#include <gio/gio.h>
#include <libqmi-glib.h>
#include <libqrtr-glib.h>
#include <stdlib.h>
#include <string.h>

static GMainLoop *loop;
static QmiDevice *device;
static QmiClient *client;
static QmiPdcConfigurationType config_type;
static gboolean completed;
static int result_code = EXIT_FAILURE;

static void
complete (gboolean success, const gchar *message)
{
    if (completed)
        return;
    completed = TRUE;
    result_code = success ? EXIT_SUCCESS : EXIT_FAILURE;
    if (success)
        g_print ("%s\n", message);
    else
        g_printerr ("%s\n", message);
    g_main_loop_quit (loop);
}

static gboolean
operation_timeout (gpointer unused)
{
    (void)unused;
    complete (FALSE, "direct pending-config activation timed out");
    return G_SOURCE_REMOVE;
}

static void
activate_indication (QmiClientPdc *pdc,
                     QmiIndicationPdcActivateConfigOutput *output,
                     gpointer unused)
{
    g_autoptr(GError) error = NULL;
    guint16 error_code = 0;

    (void)pdc;
    (void)unused;

    if (!qmi_indication_pdc_activate_config_output_get_indication_result (
            output, &error_code, &error)) {
        complete (FALSE, error->message);
        return;
    }
    if (error_code != 0) {
        g_autofree gchar *message = g_strdup_printf (
            "secure modem rejected pending activation: %s",
            qmi_protocol_error_get_string ((QmiProtocolError)error_code));
        complete (FALSE, message);
        return;
    }
    complete (TRUE, "pending PDC configuration activation accepted");
}

static void
activate_ready (QmiClientPdc *pdc, GAsyncResult *res, gpointer unused)
{
    g_autoptr(GError) error = NULL;
    g_autoptr(QmiMessagePdcActivateConfigOutput) output = NULL;

    (void)unused;

    output = qmi_client_pdc_activate_config_finish (pdc, res, &error);
    if (!output) {
        complete (FALSE, error->message);
        return;
    }
    if (!qmi_message_pdc_activate_config_output_get_result (output, &error))
        complete (FALSE, error->message);
    else
        complete (TRUE, "pending PDC activation request accepted");
}

static void
allocate_ready (QmiDevice *dev, GAsyncResult *res, gpointer unused)
{
    g_autoptr(GError) error = NULL;
    g_autoptr(QmiMessagePdcActivateConfigInput) input = NULL;

    (void)unused;

    client = qmi_device_allocate_client_finish (dev, res, &error);
    if (!client) {
        complete (FALSE, error->message);
        return;
    }

    g_signal_connect (client, "activate-config",
                      G_CALLBACK (activate_indication), NULL);
    input = qmi_message_pdc_activate_config_input_new ();
    if (!qmi_message_pdc_activate_config_input_set_config_type (
            input, config_type, &error) ||
        !qmi_message_pdc_activate_config_input_set_token (input, 1, &error)) {
        complete (FALSE, error->message);
        return;
    }

    qmi_client_pdc_activate_config (QMI_CLIENT_PDC (client), input, 15, NULL,
                                    (GAsyncReadyCallback)activate_ready, NULL);
}

static void
device_open_ready (QmiDevice *dev, GAsyncResult *res, gpointer unused)
{
    g_autoptr(GError) error = NULL;

    (void)unused;

    if (!qmi_device_open_finish (dev, res, &error)) {
        complete (FALSE, error->message);
        return;
    }
    qmi_device_allocate_client (dev, QMI_SERVICE_PDC, QMI_CID_NONE, 10, NULL,
                                (GAsyncReadyCallback)allocate_ready, NULL);
}

static void
device_new_ready (GObject *source, GAsyncResult *res, gpointer unused)
{
    g_autoptr(GError) error = NULL;

    (void)source;
    (void)unused;

    device = qmi_device_new_finish (res, &error);
    if (!device) {
        complete (FALSE, error->message);
        return;
    }
    qmi_device_open (device, QMI_DEVICE_OPEN_FLAGS_EXPECT_INDICATIONS, 15,
                     NULL, (GAsyncReadyCallback)device_open_ready, NULL);
}

static void
bus_ready (GObject *source, GAsyncResult *res, gpointer unused)
{
    g_autoptr(GError) error = NULL;
    QrtrBus *bus;
    QrtrNode *node;

    (void)source;
    (void)unused;

    bus = qrtr_bus_new_finish (res, &error);
    if (!bus) {
        complete (FALSE, error->message);
        return;
    }
    node = qrtr_bus_peek_node (bus, 0);
    if (!node) {
        complete (FALSE, "QRTR modem node 0 is unavailable");
        g_object_unref (bus);
        return;
    }
    qmi_device_new_from_node (node, NULL,
                              (GAsyncReadyCallback)device_new_ready, NULL);
    g_object_unref (bus);
}

int
main (int argc, char **argv)
{
    if (argc != 2 ||
        (strcmp (argv[1], "platform") && strcmp (argv[1], "software"))) {
        g_printerr ("usage: %s platform|software\n", argv[0]);
        return EXIT_FAILURE;
    }

    config_type = !strcmp (argv[1], "platform")
        ? QMI_PDC_CONFIGURATION_TYPE_PLATFORM
        : QMI_PDC_CONFIGURATION_TYPE_SOFTWARE;
    loop = g_main_loop_new (NULL, FALSE);
    g_timeout_add_seconds (20, operation_timeout, NULL);
    qrtr_bus_new (1000, NULL, (GAsyncReadyCallback)bus_ready, NULL);
    g_main_loop_run (loop);

    if (client)
        g_object_unref (client);
    if (device)
        g_object_unref (device);
    g_main_loop_unref (loop);
    return result_code;
}
