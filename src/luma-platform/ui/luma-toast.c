/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_toast.Toast (Python). ToastHost is LumaLayerHost. */
#include "luma-toast.h"
#include "luma-layer-host.h"
#include "luma-ui-private.h"

/* What happened -> (Lucide glyph, tone): action_toast.TOAST_KINDS. */
static const struct {
  const char *kind, *glyph, *tone;
} toast_kinds[] = {
  {"done", "check", "good"},
  {"added", "plus", "good"},
  {"saved", "check", "good"},
  {"sent", "send", "good"},
  {"place", "map-pin", "accent"},
  {"favourite", "heart", "love"},
  {"unfavourite", "heart", "muted"},
  {"copied", "copy", "muted"},
  {"deleted", "trash-2", "muted"},
  {"archived", "archive", "muted"},
  {"downloaded", "download", "muted"},
  {"notified", "bell", "muted"},
  {"signed-out", "log-out", "muted"},
  {"paused", "pause", "muted"},
  {"undone", "undo-2", "muted"},
  {"opening", "square-arrow-out-up-right", "good"},
  {"warning", "triangle-alert", "warning"},
  {"error", "circle-alert", "danger"},
};

#define CURRENT_KEY "luma-toast-current"
/* A bar lower than this from the host's foot is one the toast clears. */
#define BAR_REACH 320

enum { UNDO, N_SIGNALS };
static guint signals[N_SIGNALS];

struct _LumaToast {
  GtkBox parent_instance;
  char *kind;
  char *message;
  gboolean undo;
  gboolean busy;
  guint timeout;
  GtkWidget *host; /* weak */
};

G_DEFINE_FINAL_TYPE(LumaToast, luma_toast, GTK_TYPE_BOX)

static int kind_index(const char *kind) {
  for (guint i = 0; i < G_N_ELEMENTS(toast_kinds); i++)
    if (g_strcmp0(toast_kinds[i].kind, kind) == 0)
      return (int)i;
  return -1;
}

gboolean luma_toast_kind_is_known(const char *kind) {
  return kind != NULL && kind_index(kind) >= 0;
}

static void forget_host(LumaToast *self) {
  if (self->host != NULL && g_object_get_data(G_OBJECT(self->host), CURRENT_KEY) == self)
    g_object_set_data(G_OBJECT(self->host), CURRENT_KEY, NULL);
  g_clear_weak_pointer(&self->host);
}

static void luma_toast_dispose(GObject *object) {
  LumaToast *self = LUMA_TOAST(object);
  g_clear_handle_id(&self->timeout, g_source_remove);
  forget_host(self);
  G_OBJECT_CLASS(luma_toast_parent_class)->dispose(object);
}

static void luma_toast_finalize(GObject *object) {
  LumaToast *self = LUMA_TOAST(object);
  g_free(self->kind);
  g_free(self->message);
  G_OBJECT_CLASS(luma_toast_parent_class)->finalize(object);
}

static void luma_toast_class_init(LumaToastClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_toast_dispose;
  G_OBJECT_CLASS(klass)->finalize = luma_toast_finalize;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_STATUS);
  /**
   * LumaToast::undo:
   * @self: the toast
   *
   * Undo was pressed; the toast is already on its way out.
   */
  signals[UNDO] = g_signal_new("undo", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                               NULL, G_TYPE_NONE, 0);
}

static void luma_toast_init(LumaToast *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_set_halign(GTK_WIDGET(self), GTK_ALIGN_CENTER);
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_END);
}

static void undo_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaToast *self = LUMA_TOAST(user_data);
  gboolean had = self->undo;
  self->undo = FALSE;
  g_object_ref(self);
  luma_toast_dismiss(self);
  if (had)
    g_signal_emit(self, signals[UNDO], 0);
  g_object_unref(self);
}

GtkWidget *luma_toast_new(const char *message, const char *kind, gboolean undo, gboolean busy) {
  g_return_val_if_fail(message != NULL, NULL);
  if (kind == NULL)
    kind = "done";
  int index = kind_index(kind);
  if (index < 0) {
    g_critical("unknown toast kind '%s'; use one of done, added, saved, sent, place, favourite, "
               "unfavourite, copied, deleted, archived, downloaded, notified, signed-out, paused, "
               "undone, opening, warning, error",
               kind);
    return NULL;
  }
  /* The role as a construct property too: a build where GtkBox's own GROUP wins over the class
   * default (save-open's host build, 26 Sep) still announces a toast as a status. */
  LumaToast *self = g_object_new(LUMA_TYPE_TOAST, "accessible-role", GTK_ACCESSIBLE_ROLE_STATUS, NULL);
  GtkWidget *widget = GTK_WIDGET(self);
  self->kind = g_strdup(kind);
  self->message = g_strdup(message);
  self->undo = undo;
  self->busy = busy;
  gtk_widget_add_css_class(widget, "lumaui-toast");
  gtk_widget_add_css_class(widget, toast_kinds[index].tone);
  GtkWidget *lead;
  if (busy) {
    lead = gtk_spinner_new();
    gtk_spinner_set_spinning(GTK_SPINNER(lead), TRUE);
    gtk_widget_add_css_class(lead, "lumaui-toast-spinner");
  } else {
    lead = luma_ui_icon_image(toast_kinds[index].glyph, 0);
    gtk_widget_add_css_class(lead, "lumaui-toast-icon");
  }
  gtk_box_append(GTK_BOX(self), lead);
  GtkWidget *text = gtk_label_new(message);
  gtk_label_set_xalign(GTK_LABEL(text), 0);
  gtk_label_set_ellipsize(GTK_LABEL(text), PANGO_ELLIPSIZE_END);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_widget_add_css_class(text, "lumaui-toast-text");
  gtk_box_append(GTK_BOX(self), text);
  if (undo) {
    GtkWidget *button = gtk_button_new_with_label("Undo");
    gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(button, "lumaui-toast-undo");
    g_signal_connect(button, "clicked", G_CALLBACK(undo_clicked), self);
    gtk_box_append(GTK_BOX(self), button);
    gtk_widget_add_css_class(widget, "has-action");
  }
  luma_ui_set_accessible_label(widget, message);
  return widget;
}

guint luma_toast_get_timeout_ms(LumaToast *self) {
  g_return_val_if_fail(LUMA_IS_TOAST(self), 0);
  if (self->busy)
    return 0;
  return luma_ui_duration(self->undo ? LUMA_UI_MOTION_TOAST_UNDO_MS : LUMA_UI_MOTION_TOAST_MS, TRUE);
}

typedef struct {
  LumaToast *toast;
  GtkWidget *host; /* weak */
} Removal;

static gboolean remove_now(gpointer user_data) {
  Removal *removal = user_data;
  GtkWidget *toast = GTK_WIDGET(removal->toast);
  if (removal->host != NULL && gtk_widget_get_parent(toast) == removal->host)
    luma_layer_host_remove_layer(removal->host, toast);
  g_clear_weak_pointer(&removal->host);
  g_object_unref(removal->toast);
  g_free(removal);
  return G_SOURCE_REMOVE;
}

void luma_toast_dismiss(LumaToast *self) {
  g_return_if_fail(LUMA_IS_TOAST(self));
  g_clear_handle_id(&self->timeout, g_source_remove);
  if (self->host == NULL)
    return;
  Removal *removal = g_new0(Removal, 1);
  removal->toast = g_object_ref(self);
  g_set_weak_pointer(&removal->host, self->host);
  forget_host(self);
  gtk_widget_remove_css_class(GTK_WIDGET(self), "shown");
  gtk_widget_set_can_target(GTK_WIDGET(self), FALSE);
  g_timeout_add(MAX(1u, luma_ui_duration(LUMA_UI_MOTION_FADE_MS, FALSE)), remove_now, removal);
}

static void place(LumaToast *self, GtkWidget *host) {
  int bottom = LUMA_UI_TOAST_INSET;
  int height = gtk_widget_get_height(host);
  GPtrArray *bars = luma_layer_host_get_tracked_bars(host);
  for (guint i = 0; bars != NULL && i < bars->len; i++) {
    GtkWidget *bar = g_ptr_array_index(bars, i);
    if (!(gtk_widget_get_mapped(bar) && gtk_widget_get_visible(bar)))
      continue;
    graphene_point_t point;
    if (gtk_widget_compute_point(bar, host, &GRAPHENE_POINT_INIT(0, 0), &point) && height > 0 &&
        height - point.y < BAR_REACH)
      bottom = MAX(bottom, (int)(height - point.y) + LUMA_UI_TOAST_ABOVE_BAR);
  }
  gtk_widget_set_margin_bottom(GTK_WIDGET(self), bottom);
  if (luma_ui_is_phone(host)) {
    gtk_widget_set_margin_start(GTK_WIDGET(self), LUMA_UI_TOAST_PHONE_MARGIN);
    gtk_widget_set_margin_end(GTK_WIDGET(self), LUMA_UI_TOAST_PHONE_MARGIN);
    gtk_widget_add_css_class(GTK_WIDGET(self), "phone");
  }
}

static gboolean shown(gpointer user_data) {
  gtk_widget_add_css_class(GTK_WIDGET(user_data), "shown");
  return G_SOURCE_REMOVE;
}

static gboolean expire(gpointer user_data) {
  LumaToast *self = LUMA_TOAST(user_data);
  self->timeout = 0;
  luma_toast_dismiss(self);
  return G_SOURCE_REMOVE;
}

static LumaToast *present(GtkWidget *toast, GtkWidget *where) {
  if (toast == NULL)
    return NULL;
  LumaToast *self = LUMA_TOAST(toast);
  LumaLayerHost *layer_host = luma_layer_host_for_widget(where);
  if (layer_host == NULL) {
    g_critical("a toast needs a widget that is inside a window");
    g_object_ref_sink(toast);
    g_object_unref(toast);
    return NULL;
  }
  GtkWidget *host = GTK_WIDGET(layer_host);
  LumaToast *previous = g_object_get_data(G_OBJECT(host), CURRENT_KEY);
  if (previous != NULL)
    luma_toast_dismiss(previous);
  g_object_set_data(G_OBJECT(host), CURRENT_KEY, self);
  g_set_weak_pointer(&self->host, host);
  place(self, host);
  luma_layer_host_add_layer(host, toast);
  luma_ui_on_next_frame(toast, shown, toast);
#if GTK_CHECK_VERSION(4, 14, 0)
  gtk_accessible_announce(GTK_ACCESSIBLE(host), self->message, GTK_ACCESSIBLE_ANNOUNCEMENT_PRIORITY_MEDIUM);
#endif
  guint timeout = luma_toast_get_timeout_ms(self);
  if (timeout > 0)
    self->timeout = g_timeout_add(timeout, expire, self);
  return self;
}

LumaToast *luma_toast_show(GtkWidget *where, const char *message, const char *kind) {
  g_return_val_if_fail(GTK_IS_WIDGET(where), NULL);
  g_return_val_if_fail(message != NULL, NULL);
  return present(luma_toast_new(message, kind, FALSE, FALSE), where);
}

LumaToast *luma_toast_show_with_undo(GtkWidget *where, const char *message, const char *kind) {
  g_return_val_if_fail(GTK_IS_WIDGET(where), NULL);
  g_return_val_if_fail(message != NULL, NULL);
  return present(luma_toast_new(message, kind, TRUE, FALSE), where);
}

LumaToast *luma_toast_show_busy(GtkWidget *where, const char *message) {
  g_return_val_if_fail(GTK_IS_WIDGET(where), NULL);
  g_return_val_if_fail(message != NULL, NULL);
  return present(luma_toast_new(message, NULL, FALSE, TRUE), where);
}
