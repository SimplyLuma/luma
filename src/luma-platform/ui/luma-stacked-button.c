/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_stack.StackedButton / StackedButtons (Python). */
#include "luma-stacked-button.h"
#include "luma-ui-private.h"

#define STACKED_LABEL_CHARS 14

struct _LumaStackedButton {
  GtkButton parent_instance;
  gboolean danger;
};

G_DEFINE_FINAL_TYPE(LumaStackedButton, luma_stacked_button, GTK_TYPE_BUTTON)

static void luma_stacked_button_class_init(LumaStackedButtonClass *klass G_GNUC_UNUSED) {}

static void luma_stacked_button_init(LumaStackedButton *self) {
  luma_ui_install();
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-stacked-button");
}

GtkWidget *luma_stacked_button_new(const char *icon, const char *label, gboolean danger) {
  g_return_val_if_fail(icon != NULL && *icon != '\0', NULL);
  g_return_val_if_fail(label != NULL, NULL);
  LumaStackedButton *self = g_object_new(LUMA_TYPE_STACKED_BUTTON, NULL);
  self->danger = danger;
  if (danger)
    gtk_widget_add_css_class(GTK_WIDGET(self), "danger");
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_halign(box, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(box, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(box, "lumaui-stacked-content");
  GtkWidget *glyph = luma_ui_icon_image(icon, 0);
  gtk_widget_add_css_class(glyph, "lumaui-stacked-icon");
  gtk_widget_set_halign(glyph, GTK_ALIGN_CENTER);
  /* A short label always fits (its width is asked for); a long one ellipsizes. */
  glong length = g_utf8_strlen(label, -1);
  GtkWidget *text = gtk_label_new(label);
  gtk_label_set_ellipsize(GTK_LABEL(text), PANGO_ELLIPSIZE_END);
  gtk_label_set_width_chars(GTK_LABEL(text), (int)MIN(length, STACKED_LABEL_CHARS));
  gtk_label_set_max_width_chars(GTK_LABEL(text), STACKED_LABEL_CHARS);
  gtk_widget_add_css_class(text, "lumaui-stacked-label");
  gtk_box_append(GTK_BOX(box), glyph);
  gtk_box_append(GTK_BOX(box), text);
  gtk_button_set_child(GTK_BUTTON(self), box);
  if (length > STACKED_LABEL_CHARS)
    gtk_widget_set_tooltip_text(GTK_WIDGET(self), label);
  luma_ui_set_accessible_label(GTK_WIDGET(self), label);
  return GTK_WIDGET(self);
}

struct _LumaStackedButtons {
  GtkBox parent_instance;
  guint count;
};

G_DEFINE_FINAL_TYPE(LumaStackedButtons, luma_stacked_buttons, GTK_TYPE_BOX)

static void luma_stacked_buttons_map(GtkWidget *widget) {
  LumaStackedButtons *self = LUMA_STACKED_BUTTONS(widget);
  if (self->count != 2 && self->count != 3)
    g_critical("stacked buttons come in equal pairs or trios (this group has %u)", self->count);
  GTK_WIDGET_CLASS(luma_stacked_buttons_parent_class)->map(widget);
}

static void luma_stacked_buttons_class_init(LumaStackedButtonsClass *klass) {
  GTK_WIDGET_CLASS(klass)->map = luma_stacked_buttons_map;
}

static void luma_stacked_buttons_init(LumaStackedButtons *self) {
  luma_ui_install();
  /* Spacing is the stack token (border-spacing in the sheet), not a number here. */
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_box_set_homogeneous(GTK_BOX(self), TRUE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-stacked-buttons");
}

GtkWidget *luma_stacked_buttons_new(gboolean small) {
  GtkWidget *self = g_object_new(LUMA_TYPE_STACKED_BUTTONS, NULL);
  if (small)
    gtk_widget_add_css_class(self, "lumaui-stack-small");
  return self;
}

void luma_stacked_buttons_append(LumaStackedButtons *self, LumaStackedButton *button) {
  g_return_if_fail(LUMA_IS_STACKED_BUTTONS(self));
  g_return_if_fail(LUMA_IS_STACKED_BUTTON(button));
  if (self->count >= 3) {
    g_critical("stacked buttons come in equal pairs or trios; not adding a fourth");
    return;
  }
  self->count++;
  gtk_box_append(GTK_BOX(self), GTK_WIDGET(button));
}
