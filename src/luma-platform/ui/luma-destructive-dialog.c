/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_dialog.DestructiveDialog (Python). */
#include "luma-destructive-dialog.h"
#include "luma-ui-private.h"
#include "luma-ui-tokens-private.h"

#include <math.h>

#include <string.h>

enum { CONFIRMED, CANCELLED, N_SIGNALS };
static guint signals[N_SIGNALS];

struct _LumaDestructiveDialog {
  GtkBox parent_instance;
  GtkWidget *option;
  GtkWidget *buttons;
  GtkWidget *cancel_button;
  GtkWidget *action_button;
  GtkWidget *grab, *badge, *title, *body;
  LumaModalHandle *handle; /* weak; the card keeps it alive */
  LumaActionCenter *center; /* weak: asked inside this bar (v71) */
  gboolean pending;         /* in a bar, not yet answered (holds a reference) */
};

G_DEFINE_FINAL_TYPE(LumaDestructiveDialog, luma_destructive_dialog, GTK_TYPE_BOX)

static void luma_destructive_dialog_dispose(GObject *object) {
  LumaDestructiveDialog *self = LUMA_DESTRUCTIVE_DIALOG(object);
  g_clear_weak_pointer(&self->handle);
  g_clear_weak_pointer(&self->center);
  G_OBJECT_CLASS(luma_destructive_dialog_parent_class)->dispose(object);
}

static void luma_destructive_dialog_class_init(LumaDestructiveDialogClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_destructive_dialog_dispose;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_ALERT_DIALOG);
  /**
   * LumaDestructiveDialog::confirmed:
   * @self: the dialog
   * @option_checked: whether the option under the body was checked
   *
   * The red action was pressed; the dialog is already closing.
   */
  signals[CONFIRMED] = g_signal_new("confirmed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                    NULL, NULL, G_TYPE_NONE, 1, G_TYPE_BOOLEAN);
  /**
   * LumaDestructiveDialog::cancelled:
   * @self: the dialog
   *
   * Cancel, Esc, or a tap outside a drawer closed it.
   */
  signals[CANCELLED] = g_signal_new("cancelled", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                    NULL, NULL, G_TYPE_NONE, 0);
}

static void luma_destructive_dialog_init(LumaDestructiveDialog *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
}

/* In a bar: answered once; the reference taken when it was asked is dropped. */
static gboolean settle(LumaDestructiveDialog *self) {
  if (!self->pending)
    return FALSE;
  self->pending = FALSE;
  return TRUE;
}

static void cancel_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaDestructiveDialog *self = LUMA_DESTRUCTIVE_DIALOG(user_data);
  if (self->handle != NULL)
    luma_modal_handle_cancel(self->handle);
  else if (self->center != NULL && self->pending)
    luma_action_center_fold(self->center); /* the fold answers Cancel (bar_folded) */
}

static void action_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaDestructiveDialog *self = LUMA_DESTRUCTIVE_DIALOG(user_data);
  gboolean checked = luma_destructive_dialog_get_option_checked(self);
  g_object_ref(self);
  gboolean in_bar = settle(self);
  if (self->handle != NULL)
    luma_modal_handle_close(self->handle);
  if (in_bar && self->center != NULL)
    luma_action_center_fold(self->center);
  g_signal_emit(self, signals[CONFIRMED], 0, checked);
  if (in_bar)
    g_object_unref(self); /* the asking reference */
  g_object_unref(self);
}

static void bar_folded(LumaActionCenter *center G_GNUC_UNUSED, const char *key, gpointer user_data) {
  LumaDestructiveDialog *self = LUMA_DESTRUCTIVE_DIALOG(user_data);
  if (g_strcmp0(key, "confirm") == 0 && gtk_widget_get_parent(GTK_WIDGET(self)) != NULL)
    return;
  if (!settle(self))
    return;
  g_signal_handlers_disconnect_by_func(center, bar_folded, self);
  g_signal_emit(self, signals[CANCELLED], 0);
  g_object_unref(self); /* the asking reference */
}

static void handle_cancelled(LumaModalHandle *handle G_GNUC_UNUSED, gpointer user_data) {
  g_signal_emit(user_data, signals[CANCELLED], 0);
}

static gboolean is_vague(const char *action) {
  static const char *const vague[] = {"ok", "okay", "yes", "confirm", "continue", NULL};
  g_autofree char *key = g_utf8_strdown(action, -1);
  return g_strv_contains(vague, g_strstrip(key));
}

static GtkWidget *centred_label(const char *text, const char *css_class, int max_chars G_GNUC_UNUSED) {
  GtkWidget *label = gtk_label_new(text);
  gtk_label_set_wrap(GTK_LABEL(label), TRUE);
  gtk_label_set_justify(GTK_LABEL(label), GTK_JUSTIFY_CENTER);
  gtk_label_set_wrap_mode(GTK_LABEL(label), PANGO_WRAP_WORD);
  /* v70 lConfirm: the words wrap in the card's 300 column (340 less 20 a side), so the card is
   * 340 wide however long the body is (a long body made it 378, on one line). */
  gtk_label_set_max_width_chars(GTK_LABEL(label), 1);
  gtk_widget_set_size_request(label, LUMA_UI_DIALOG_WIDTH - 2 * LUMA_UI_DIALOG_PADDING_X, -1);
  gtk_widget_add_css_class(label, css_class);
  return label;
}

GtkWidget *luma_destructive_dialog_new(const char *title, const char *body, const char *action,
                                       const char *icon, const char *option) {
  g_return_val_if_fail(title != NULL, NULL);
  g_return_val_if_fail(body != NULL, NULL);
  if (action == NULL)
    action = "Delete";
  if (icon == NULL)
    icon = "trash-2";
  g_autofree char *question = g_utf8_strdown(title, -1);
  g_strstrip(question);
  if (question[0] == '\0' || g_str_has_prefix(question, "are you sure")) {
    g_critical("a destructive dialog's title is the question, naming the thing");
    return NULL;
  }
  if (is_vague(action)) {
    g_critical("name the action for what it does (Delete, Leave, Uninstall), never OK");
    return NULL;
  }

  LumaDestructiveDialog *self = g_object_new(LUMA_TYPE_DESTRUCTIVE_DIALOG, NULL);
  GtkBox *box = GTK_BOX(self);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-dialog");

  GtkWidget *grab = self->grab = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(grab, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(grab, "lumaui-drawer-handle");
  gtk_box_append(box, grab);

  GtkWidget *badge = self->badge = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(badge, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(badge, "lumaui-dialog-icon");
  GtkWidget *glyph = luma_ui_icon_image(icon, 0);
  gtk_widget_set_hexpand(glyph, TRUE);
  gtk_widget_set_halign(glyph, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(badge), glyph);
  gtk_box_append(box, badge);

  self->title = centred_label(title, "lumaui-dialog-title", 34);
  self->body = centred_label(body, "lumaui-dialog-body", 40);
  gtk_box_append(box, self->title);
  gtk_box_append(box, self->body);

  if (option != NULL && option[0] != '\0') {
    self->option = gtk_check_button_new_with_label(option);
    gtk_widget_set_halign(self->option, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(self->option, "lumaui-dialog-option");
    gtk_box_append(box, self->option);
  }

  self->buttons = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_box_set_homogeneous(GTK_BOX(self->buttons), TRUE);
  gtk_widget_add_css_class(self->buttons, "lumaui-dialog-buttons");
  self->cancel_button = gtk_button_new_with_label("Cancel");
  gtk_widget_set_hexpand(self->cancel_button, TRUE);
  gtk_widget_add_css_class(self->cancel_button, "lumaui-dialog-cancel");
  self->action_button = gtk_button_new_with_label(action);
  gtk_widget_set_hexpand(self->action_button, TRUE);
  gtk_widget_add_css_class(self->action_button, "lumaui-dialog-action");
  g_signal_connect(self->cancel_button, "clicked", G_CALLBACK(cancel_clicked), self);
  g_signal_connect(self->action_button, "clicked", G_CALLBACK(action_clicked), self);
  gtk_box_append(GTK_BOX(self->buttons), self->cancel_button);
  gtk_box_append(GTK_BOX(self->buttons), self->action_button);
  gtk_box_append(box, self->buttons);

  /* The alert dialog reads as its question and its consequence. */
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_LABEL, title,
                                 GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, body, -1);
  return GTK_WIDGET(self);
}

/* v71's in-bar confirm (.cconf) and the phone's lConfirm: no grabber, no icon, the words left-aligned
 * and as wide as the bar, Cancel and the red action side by side. */
static void swap_class(GtkWidget *widget, const char *from, const char *to) {
  gtk_widget_remove_css_class(widget, from);
  gtk_widget_add_css_class(widget, to);
}

static void look_in_bar(LumaDestructiveDialog *self, const char *css_class) {
  gtk_widget_add_css_class(GTK_WIDGET(self), css_class);
  if (g_str_equal(css_class, "in-bar")) {
    /* Inside the grown bar it is Python's PanelConfirm, node for node, styled by the bar's sheet. */
    swap_class(GTK_WIDGET(self), "lumaui-dialog", "lumaui-panel-confirm");
    swap_class(self->title, "lumaui-dialog-title", "lumaui-panel-confirm-title");
    swap_class(self->body, "lumaui-dialog-body", "lumaui-panel-confirm-body");
    swap_class(self->buttons, "lumaui-dialog-buttons", "lumaui-panel-confirm-buttons");
    swap_class(self->cancel_button, "lumaui-dialog-cancel", "lumaui-panel-confirm-cancel");
    swap_class(self->action_button, "lumaui-dialog-action", "lumaui-panel-confirm-action");
  }
  gtk_widget_set_visible(self->grab, FALSE);
  gtk_widget_set_visible(self->badge, FALSE);
  GtkWidget *labels[] = {self->title, self->body};
  for (guint i = 0; i < G_N_ELEMENTS(labels); i++) {
    gtk_label_set_xalign(GTK_LABEL(labels[i]), 0);
    gtk_label_set_justify(GTK_LABEL(labels[i]), GTK_JUSTIFY_LEFT);
    gtk_widget_set_size_request(labels[i], -1, -1);
    gtk_label_set_max_width_chars(GTK_LABEL(labels[i]), 40);
  }
  if (self->option != NULL)
    gtk_widget_set_halign(self->option, GTK_ALIGN_START);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self->buttons), GTK_ORIENTATION_HORIZONTAL);
  gtk_box_set_homogeneous(GTK_BOX(self->buttons), TRUE);
}

/* The lift, once the host is laid out: a frame at a time until it is. */
static gboolean lift_tick(GtkWidget *card, GdkFrameClock *clock G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  if (gtk_widget_has_css_class(card, "drawer"))
    return G_SOURCE_REMOVE;
  GtkWidget *host = gtk_widget_get_parent(card);
  GtkRoot *root = host != NULL ? gtk_widget_get_root(host) : NULL;
  graphene_rect_t bounds;
  if (root == NULL || gtk_widget_get_height(host) <= 0 || !gtk_widget_compute_bounds(host, GTK_WIDGET(root), &bounds))
    return G_SOURCE_CONTINUE;
  int lift = (int)round(bounds.origin.y);
  if (gtk_widget_get_margin_bottom(card) != lift)
    gtk_widget_set_margin_bottom(card, lift);
  return G_SOURCE_REMOVE;
}

static void lift_to_window(GtkWidget *card, gpointer data G_GNUC_UNUSED) {
  if (gtk_widget_get_parent(card) != NULL)
    gtk_widget_add_tick_callback(card, lift_tick, NULL, NULL);
}

LumaModalHandle *luma_destructive_dialog_present(LumaDestructiveDialog *self, GtkWidget *where) {
  g_return_val_if_fail(LUMA_IS_DESTRUCTIVE_DIALOG(self), NULL);
  g_return_val_if_fail(GTK_IS_WIDGET(where), NULL);
  LumaLayerHost *host = luma_layer_host_window_host(where);
  if (host == NULL) {
    g_critical("a destructive dialog needs a widget that is inside a window");
    return NULL;
  }
  /* A phone always asks from a drawer, however early (v70's phone lConfirm); elsewhere the width decides. */
  gboolean drawer = luma_ui_mobile_form_factor() || luma_ui_is_phone(GTK_WIDGET(host));
  if (drawer) /* v71: the phone's confirm looks like the in-bar confirm, in the bar's frame */
    look_in_bar(self, "phone");
  /* v70 centres the card on the window, not on the work surface under the title row: lift it by
   * half the title row (a bottom margin the height of the host's offset in the window), measured
   * again when the card maps, since a dialog asked for before the window is laid out (a page opened
   * straight into its confirm) finds the host at 0 (Settings' Forget, 28 Sep). */
  LumaModalHandle *handle = luma_layer_host_present_modal(
      host, GTK_WIDGET(self), self->cancel_button, drawer ? LUMA_DRAWER_MODE_ALWAYS : LUMA_DRAWER_MODE_NEVER);
  if (handle == NULL)
    return NULL;
  if (!drawer) {
    lift_to_window(GTK_WIDGET(self), NULL);
    g_signal_connect(self, "map", G_CALLBACK(lift_to_window), NULL);
  }
  g_set_weak_pointer(&self->handle, handle);
  g_signal_connect_object(handle, "cancelled", G_CALLBACK(handle_cancelled), self, 0);
  return handle;
}

LumaDestructiveDialog *luma_destructive_dialog_in_bar(LumaActionCenter *center, const char *title,
                                                      const char *body, const char *action, const char *icon,
                                                      const char *option) {
  g_return_val_if_fail(LUMA_IS_ACTION_CENTER(center), NULL);
  GtkWidget *dialog = luma_destructive_dialog_new(title, body, action, icon, option);
  if (dialog == NULL)
    return NULL;
  LumaDestructiveDialog *self = LUMA_DESTRUCTIVE_DIALOG(dialog);
  look_in_bar(self, "in-bar");
  g_object_ref_sink(self); /* the asking reference, dropped when it is answered */
  self->pending = TRUE;
  g_set_weak_pointer(&self->center, center);
  luma_action_center_grow_panel(center, "confirm", dialog);
  if (g_strcmp0(luma_action_center_get_grown(center), "confirm") != 0) {
    self->pending = FALSE;
    g_object_unref(self);
    return NULL;
  }
  g_signal_connect_object(center, "grown-changed", G_CALLBACK(bar_folded), self, 0);
  gtk_widget_grab_focus(self->cancel_button);
  return self;
}

LumaDestructiveDialog *luma_destructive_dialog_ask(GtkWidget *where, const char *title, const char *body,
                                                   const char *action, const char *icon,
                                                   const char *option) {
  g_return_val_if_fail(GTK_IS_WIDGET(where), NULL);
  GtkWidget *dialog = luma_destructive_dialog_new(title, body, action, icon, option);
  if (dialog == NULL)
    return NULL;
  if (luma_destructive_dialog_present(LUMA_DESTRUCTIVE_DIALOG(dialog), where) == NULL) {
    g_object_ref_sink(dialog);
    g_object_unref(dialog);
    return NULL;
  }
  return LUMA_DESTRUCTIVE_DIALOG(dialog);
}

gboolean luma_destructive_dialog_get_option_checked(LumaDestructiveDialog *self) {
  g_return_val_if_fail(LUMA_IS_DESTRUCTIVE_DIALOG(self), FALSE);
  return self->option != NULL && gtk_check_button_get_active(GTK_CHECK_BUTTON(self->option));
}

void luma_destructive_dialog_close(LumaDestructiveDialog *self) {
  g_return_if_fail(LUMA_IS_DESTRUCTIVE_DIALOG(self));
  if (self->handle != NULL)
    luma_modal_handle_close(self->handle);
}
