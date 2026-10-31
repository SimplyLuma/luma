// SPDX-License-Identifier: Apache-2.0
// Opt-in startup support for native GApplication services. This library is
// loaded only by the session's bounded native-preparation controller.
#define _GNU_SOURCE
#include <gtk/gtk.h>
#include <dlfcn.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static gboolean requested;
static char *notify_path;
static gboolean preparing;

// Consume the preparation environment before application threads exist. A
// browser or terminal launched by the app must be an ordinary child process.
__attribute__((constructor)) static void preparation_environment(void) {
    const char *enabled = getenv("LUMA_APP_PREPARE");
    if (!enabled || strcmp(enabled, "1")) return;
    requested = TRUE;
    const char *path = getenv("LUMA_APP_PREPARE_NOTIFY");
    if (path) notify_path = strdup(path);
    const char *library = getenv("LUMA_APP_PREPARE_LIBRARY");
    const char *preload = getenv("LD_PRELOAD");
    if (library && preload && !strcmp(library, preload)) unsetenv("LD_PRELOAD");
    unsetenv("LUMA_APP_PREPARE");
    unsetenv("LUMA_APP_PREPARE_NOTIFY");
    unsetenv("LUMA_APP_PREPARE_LIBRARY");
}

typedef struct {
    GApplication *app;  // owned by the caller of g_application_run
    GtkWindow *surface;
    GMemoryMonitor *memory;
    GPtrArray *views;
    gboolean held;
    gboolean used;
    guint idle;
    int original_oom;
} Preparation;

static Preparation *current;

static void oom_score(int score) {
    FILE *stream = fopen("/proc/self/oom_score_adj", "we");
    if (stream) { fprintf(stream, "%d", score); fclose(stream); }
}

static void release_surface(Preparation *self) {
    if (self->surface) {
        gtk_window_destroy(self->surface);
        self->surface = NULL;
    }
}

static void low_memory(GMemoryMonitor *monitor, GMemoryMonitorWarningLevel level, Preparation *self) {
    (void)monitor;
    if (level < G_MEMORY_MONITOR_WARNING_LEVEL_LOW) return;
    if (self->used) return;
    release_surface(self);
    g_application_quit(self->app);
    if (self->held) {
        self->held = FALSE;
        g_application_release(self->app);
    }
}

static void activated(GApplication *app, Preparation *self) {
    (void)app;
    if (preparing || self->used) return;
    self->used = TRUE;
    oom_score(self->original_oom);
    release_surface(self);
    if (self->held) { self->held = FALSE; g_application_release(self->app); }
}

static void window_added(GtkApplication *app, GtkWindow *window, Preparation *self) {
    (void)app; (void)window;
    if (!preparing) oom_score(self->original_oom);
}

static void window_removed(GtkApplication *app, GtkWindow *window, Preparation *self) {
    (void)window;
    if (!gtk_application_get_windows(app)) oom_score(MAX(self->original_oom, 500));
}

static void notify_ready(void) {
    if (!notify_path) return;
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(notify_path) >= sizeof address.sun_path) return;
    strcpy(address.sun_path, notify_path);
    int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd >= 0) {
        char pid[32]; int length = snprintf(pid, sizeof pid, "%ld", (long)getpid());
        sendto(fd, pid, (size_t)length, MSG_NOSIGNAL, (struct sockaddr *)&address, sizeof address);
        close(fd);
    }
    free(notify_path); notify_path = NULL;
}

// A file-open request may create a different real window. Retire any untouched
// prepared view rather than leaving a hidden window holding the app alive.
static void finish_views(GtkWindow *presented) {
    if (!current || !current->views) return;
    GPtrArray *views = current->views;
    current->views = NULL;
    for (guint i = 0; i < views->len; i++) {
        GtkWindow *window = g_ptr_array_index(views, i);
        if (window != presented && !gtk_widget_get_mapped(GTK_WIDGET(window)) &&
            gtk_window_get_application(window) == GTK_APPLICATION(current->app))
            gtk_window_destroy(window);
    }
    g_ptr_array_unref(views);
}

// Preparation builds only these explicitly opted-in OS apps' initial view.
// Defer presentation while their ordinary activate handler constructs it;
// the next real activation presents the same window through the normal API.
void gtk_window_present(GtkWindow *window) {
    if (preparing) { gtk_widget_realize(GTK_WIDGET(window)); return; }
    if (current) activated(current->app, current);
    finish_views(window);
    void (*present)(GtkWindow *) = NULL;
    void *symbol = dlsym(RTLD_NEXT, "gtk_window_present");
    memcpy(&present, &symbol, sizeof present);
    if (present) present(window);
}

void gtk_window_present_with_time(GtkWindow *window, guint32 timestamp) {
    if (preparing) { gtk_widget_realize(GTK_WIDGET(window)); return; }
    if (current) activated(current->app, current);
    finish_views(window);
    void (*present)(GtkWindow *, guint32) = NULL;
    void *symbol = dlsym(RTLD_NEXT, "gtk_window_present_with_time");
    memcpy(&present, &symbol, sizeof present);
    if (present) present(window, timestamp);
}

static gboolean prepare_view(gpointer data) {
    Preparation *self = data;
    self->idle = 0;
    if (!self->used) {
        preparing = TRUE;
        g_application_activate(self->app);
        preparing = FALSE;
        self->views = g_ptr_array_new_with_free_func(g_object_unref);
        for (GList *item = gtk_application_get_windows(GTK_APPLICATION(self->app)); item; item = item->next)
            g_ptr_array_add(self->views, g_object_ref(item->data));
        oom_score(MAX(self->original_oom, 500));
    }
    notify_ready();
    return G_SOURCE_REMOVE;
}

static void started(GApplication *app, Preparation *self) {
    self->held = TRUE;
    g_application_hold(app);
    // Never map a helper window or load any user document. This only prepares
    // the same display/GL context later used by the application's real window.
    self->surface = GTK_WINDOW(gtk_window_new());
    gtk_window_set_default_size(self->surface, 1, 1);
    gtk_widget_realize(GTK_WIDGET(self->surface));
    self->memory = g_memory_monitor_dup_default();
    g_signal_connect(self->memory, "low-memory-warning", G_CALLBACK(low_memory), self);
    oom_score(MAX(self->original_oom, 500));
    self->idle = g_idle_add(prepare_view, self);
}

// Keep the opt-in behavior in one platform component instead of copying
// private keep-alive or GTK warm-up code into Filer and Settings.
int g_application_run(GApplication *app, int argc, char **argv) {
    int (*run)(GApplication *, int, char **) = NULL;
    void *symbol = dlsym(RTLD_NEXT, "g_application_run");
    memcpy(&run, &symbol, sizeof run);
    if (!run) return EXIT_FAILURE;
    if (!requested || !GTK_IS_APPLICATION(app)) return run(app, argc, argv);
    requested = FALSE;
    gboolean service = FALSE;
    for (int i = 1; i < argc; i++) if (!strcmp(argv[i], "--gapplication-service")) service = TRUE;
    if (!service) return run(app, argc, argv);
    Preparation self = {.app = app};
    FILE *stream = fopen("/proc/self/oom_score_adj", "re");
    if (stream) { if (fscanf(stream, "%d", &self.original_oom) != 1) self.original_oom = 0; fclose(stream); }
    g_signal_connect_after(app, "startup", G_CALLBACK(started), &self);
    g_signal_connect(app, "activate", G_CALLBACK(activated), &self);
    g_signal_connect_after(app, "window-added", G_CALLBACK(window_added), &self);
    g_signal_connect_after(app, "window-removed", G_CALLBACK(window_removed), &self);
    current = &self;
    int result = run(app, argc, argv);
    current = NULL;
    if (self.idle) g_source_remove(self.idle);
    g_signal_handlers_disconnect_by_data(app, &self);
    if (self.memory) { g_signal_handlers_disconnect_by_data(self.memory, &self); g_object_unref(self.memory); }
    release_surface(&self);
    if (self.views) g_ptr_array_unref(self.views);
    if (self.held) g_application_release(app);
    oom_score(self.original_oom);
    return result;
}
