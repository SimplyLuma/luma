/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-color-well.h"

enum { CHANGED, N_SIGNALS };
static guint signals[N_SIGNALS];

struct _LumaColorWell {
  GtkBox parent_instance;
  GtkColorDialogButton *button;
  GtkEntry *entry;
};

G_DEFINE_FINAL_TYPE(LumaColorWell, luma_color_well, GTK_TYPE_BOX)

static void update_entry(LumaColorWell *self) {
  const GdkRGBA *rgba = gtk_color_dialog_button_get_rgba(self->button);
  g_autofree char *value = gdk_rgba_to_string(rgba);
  gtk_editable_set_text(GTK_EDITABLE(self->entry), value);
}

static void color_changed(GObject *object, GParamSpec *spec, gpointer user_data) {
  LumaColorWell *self = LUMA_COLOR_WELL(user_data);
  (void)object;
  (void)spec;
  update_entry(self);
  g_signal_emit(self, signals[CHANGED], 0);
}

static void text_committed(GtkEntry *entry, gpointer user_data) {
  LumaColorWell *self = LUMA_COLOR_WELL(user_data);
  GdkRGBA rgba;
  if (gdk_rgba_parse(&rgba, gtk_editable_get_text(GTK_EDITABLE(entry))))
    gtk_color_dialog_button_set_rgba(self->button, &rgba);
  else
    update_entry(self);
}

static void luma_color_well_class_init(LumaColorWellClass *klass) {
  signals[CHANGED] = g_signal_new("changed", G_TYPE_FROM_CLASS(klass),
                                  G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                  G_TYPE_NONE, 0);
}

static void luma_color_well_init(LumaColorWell *self) {
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_box_set_spacing(GTK_BOX(self), 6);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-color-well");
  GtkColorDialog *dialog = gtk_color_dialog_new();
  gtk_color_dialog_set_title(dialog, "Choose color");
  gtk_color_dialog_set_with_alpha(dialog, TRUE);
  self->button = GTK_COLOR_DIALOG_BUTTON(gtk_color_dialog_button_new(dialog));
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->button),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL,
                                 "Choose color", -1);
  self->entry = GTK_ENTRY(gtk_entry_new());
  gtk_entry_set_placeholder_text(self->entry, "#000000");
  gtk_widget_set_hexpand(GTK_WIDGET(self->entry), TRUE);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->entry),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL,
                                 "Color value", -1);
  gtk_box_append(GTK_BOX(self), GTK_WIDGET(self->button));
  gtk_box_append(GTK_BOX(self), GTK_WIDGET(self->entry));
  g_signal_connect(self->button, "notify::rgba", G_CALLBACK(color_changed), self);
  g_signal_connect(self->entry, "activate", G_CALLBACK(text_committed), self);
  update_entry(self);
}

GtkWidget *luma_color_well_new(void) {
  return g_object_new(LUMA_TYPE_COLOR_WELL, NULL);
}

void luma_color_well_set_rgba(LumaColorWell *self, const GdkRGBA *rgba) {
  g_return_if_fail(LUMA_IS_COLOR_WELL(self));
  g_return_if_fail(rgba != NULL);
  gtk_color_dialog_button_set_rgba(self->button, rgba);
}

const GdkRGBA *luma_color_well_get_rgba(LumaColorWell *self) {
  g_return_val_if_fail(LUMA_IS_COLOR_WELL(self), NULL);
  return gtk_color_dialog_button_get_rgba(self->button);
}

void luma_color_well_set_text(LumaColorWell *self, const char *text) {
  g_return_if_fail(LUMA_IS_COLOR_WELL(self));
  GdkRGBA rgba;
  if (text != NULL && gdk_rgba_parse(&rgba, text))
    gtk_color_dialog_button_set_rgba(self->button, &rgba);
}

const char *luma_color_well_get_text(LumaColorWell *self) {
  g_return_val_if_fail(LUMA_IS_COLOR_WELL(self), NULL);
  return gtk_editable_get_text(GTK_EDITABLE(self->entry));
}
