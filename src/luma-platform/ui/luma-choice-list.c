/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-choice-list.h"
#include "luma-type-label.h"
#include "luma-ui-kit.h"

struct _LumaChoiceList {
  GtkBox parent_instance;
  GHashTable *buttons;
  char *selected;
  gboolean panel;
};
G_DEFINE_FINAL_TYPE(LumaChoiceList, luma_choice_list, GTK_TYPE_BOX)
static guint changed_signal;

static void luma_choice_list_finalize(GObject *object) {
  LumaChoiceList *self = LUMA_CHOICE_LIST(object);
  g_hash_table_unref(self->buttons);
  g_free(self->selected);
  G_OBJECT_CLASS(luma_choice_list_parent_class)->finalize(object);
}

static void luma_choice_list_class_init(LumaChoiceListClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = luma_choice_list_finalize;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_RADIO_GROUP);
  changed_signal = g_signal_new("changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                               0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
}

static void luma_choice_list_init(LumaChoiceList *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-choice-list");
  self->buttons = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
}

GtkWidget *luma_choice_list_new(gboolean panel) {
  LumaChoiceList *self = g_object_new(LUMA_TYPE_CHOICE_LIST, "accessible-role", GTK_ACCESSIBLE_ROLE_RADIO_GROUP, NULL);
  self->panel = panel;
  if (panel) gtk_widget_add_css_class(GTK_WIDGET(self), "panel");
  return GTK_WIDGET(self);
}

void luma_choice_list_set_selected(LumaChoiceList *self, const char *key) {
  g_return_if_fail(LUMA_IS_CHOICE_LIST(self));
  g_return_if_fail(key != NULL && g_hash_table_contains(self->buttons, key));
  if (g_strcmp0(self->selected, key) == 0) return;
  g_free(self->selected);
  self->selected = g_strdup(key);
  GHashTableIter iter;
  gpointer row_key, value;
  g_hash_table_iter_init(&iter, self->buttons);
  while (g_hash_table_iter_next(&iter, &row_key, &value)) {
    gboolean selected = g_str_equal(row_key, key);
    gtk_widget_set_focusable(value, selected);
    if (selected) gtk_widget_add_css_class(value, "on");
    else gtk_widget_remove_css_class(value, "on");
    gtk_accessible_update_state(GTK_ACCESSIBLE(value), GTK_ACCESSIBLE_STATE_CHECKED,
      selected ? GTK_ACCESSIBLE_TRISTATE_TRUE : GTK_ACCESSIBLE_TRISTATE_FALSE, -1);
  }
}

const char *luma_choice_list_get_selected(LumaChoiceList *self) {
  g_return_val_if_fail(LUMA_IS_CHOICE_LIST(self), NULL);
  return self->selected;
}

static void chosen(GtkButton *button, LumaChoiceList *self) {
  const char *key = g_object_get_data(G_OBJECT(button), "choice-key");
  if (g_strcmp0(self->selected, key) == 0) return;
  luma_choice_list_set_selected(self, key);
  g_signal_emit(self, changed_signal, 0, key);
}

static gboolean key_pressed(GtkEventControllerKey *controller, guint keyval,
                            guint code G_GNUC_UNUSED, GdkModifierType state G_GNUC_UNUSED,
                            LumaChoiceList *self) {
  GtkWidget *button = gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(controller));
  GtkWidget *next;
  if (keyval == GDK_KEY_Left || keyval == GDK_KEY_Up) {
    next = gtk_widget_get_prev_sibling(button);
    if (next == NULL) next = gtk_widget_get_last_child(GTK_WIDGET(self));
  } else if (keyval == GDK_KEY_Right || keyval == GDK_KEY_Down) {
    next = gtk_widget_get_next_sibling(button);
    if (next == NULL) next = gtk_widget_get_first_child(GTK_WIDGET(self));
  } else if (keyval == GDK_KEY_Home) next = gtk_widget_get_first_child(GTK_WIDGET(self));
  else if (keyval == GDK_KEY_End) next = gtk_widget_get_last_child(GTK_WIDGET(self));
  else return FALSE;
  chosen(GTK_BUTTON(next), self);
  gtk_widget_grab_focus(next);
  return TRUE;
}

void luma_choice_list_append(LumaChoiceList *self, const char *key, const char *title, const char *detail) {
  g_return_if_fail(LUMA_IS_CHOICE_LIST(self));
  g_return_if_fail(key != NULL && !g_hash_table_contains(self->buttons, key));
  GtkWidget *button = g_object_new(GTK_TYPE_BUTTON, "accessible-role", GTK_ACCESSIBLE_ROLE_RADIO, NULL);
  gtk_widget_add_css_class(button, "lumaui-choice-row");
  gtk_widget_set_focusable(button, g_hash_table_size(self->buttons) == 0);
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(line, "lumaui-choice-content");
  GtkWidget *indicator = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_valign(indicator, GTK_ALIGN_START);
  gtk_widget_add_css_class(indicator, "lumaui-choice-indicator");
  gtk_box_append(GTK_BOX(line), indicator);
  GtkWidget *copy = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_hexpand(copy, TRUE);
  gtk_box_append(GTK_BOX(copy), luma_type_label_new(title, "choice_title"));
  if (detail != NULL && *detail) {
    GtkWidget *label = luma_type_label_new(detail, self->panel ? "choice_panel_detail" : "choice_detail");
    luma_type_label_set_wrap(LUMA_TYPE_LABEL(label), TRUE);
    gtk_box_append(GTK_BOX(copy), label);
    gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, detail, -1);
  }
  gtk_box_append(GTK_BOX(line), copy);
  gtk_button_set_child(GTK_BUTTON(button), line);
  gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_LABEL, title ? title : "", -1);
  gtk_accessible_update_state(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_STATE_CHECKED, GTK_ACCESSIBLE_TRISTATE_FALSE, -1);
  g_object_set_data_full(G_OBJECT(button), "choice-key", g_strdup(key), g_free);
  g_signal_connect(button, "clicked", G_CALLBACK(chosen), self);
  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(key_pressed), self);
  gtk_widget_add_controller(button, keys);
  g_hash_table_insert(self->buttons, g_strdup(key), button);
  gtk_box_append(GTK_BOX(self), button);
}
