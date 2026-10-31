// SPDX-License-Identifier: Apache-2.0
#include <gtk/gtk.h>
#include <stdio.h>
#include <stdlib.h>
static GtkWindow *window;
static guint constructions;
static gboolean quit_later(gpointer data) {
    g_application_quit(data);
    return G_SOURCE_REMOVE;
}
static gboolean record_mapped(gpointer data) {
    int oom = -999;
    FILE *status = fopen("/proc/self/oom_score_adj", "r");
    if (status) { if (fscanf(status, "%d", &oom) != 1) oom = -999; fclose(status); }
    FILE *record = fopen(g_getenv("LUMA_PREPARE_TEST_RECORD"), "w");
    if (record) {
        fprintf(record, "%u %d %d %u\n", constructions, oom,
            g_getenv("LUMA_APP_PREPARE") != NULL || g_getenv("LD_PRELOAD") != NULL,
            g_list_length(gtk_application_get_windows(GTK_APPLICATION(data))));
        fclose(record);
    }
    g_timeout_add(250, quit_later, data);
    return G_SOURCE_REMOVE;
}
static void mapped(GtkWidget *widget, gpointer data) {
    (void)widget;
    g_idle_add(record_mapped, data);
}
static void activated(GtkApplication *app, gpointer data) {
    (void)data;
    if (!window) {
        constructions++;
        window = GTK_WINDOW(gtk_application_window_new(app));
        gtk_window_set_default_size(window, 100, 100);
        gtk_window_set_child(window, gtk_label_new("Launch preparation test"));
        g_signal_connect(window, "map", G_CALLBACK(mapped), app);
    }
    gtk_window_present(window);
}
static void opened(GApplication *app, GFile **files, gint count, const gchar *hint, gpointer data) {
    (void)files; (void)count; (void)hint;
    window = NULL;  // Open chooses a new view, leaving the untouched prepared one.
    activated(GTK_APPLICATION(app), data);
}
static gboolean memory_warning(gpointer data) {
    (void)data;
    GMemoryMonitor *monitor = g_memory_monitor_dup_default();
    g_signal_emit_by_name(monitor, "low-memory-warning", G_MEMORY_MONITOR_WARNING_LEVEL_LOW);
    g_object_unref(monitor);
    return G_SOURCE_REMOVE;
}
static void startup(GApplication *app, gpointer data) {
    (void)app; (void)data;
    if (g_getenv("LUMA_PREPARE_TEST_MEMORY")) g_timeout_add(800, memory_warning, NULL);
}
int luma_prepare_test_main(void) {
    GtkApplication *app = gtk_application_new(g_getenv("LUMA_PREPARE_TEST_ID"), G_APPLICATION_HANDLES_OPEN);
    g_signal_connect(app, "startup", G_CALLBACK(startup), NULL);
    g_signal_connect(app, "activate", G_CALLBACK(activated), NULL);
    g_signal_connect(app, "open", G_CALLBACK(opened), NULL);
    char *args[] = {"luma-native-prepare-test", "--gapplication-service", NULL};
    int result = g_application_run(G_APPLICATION(app), 2, args);
    g_object_unref(app);
    return result;
}
