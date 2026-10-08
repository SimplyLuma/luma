/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-controls.h"
#include "luma-ui-private.h"

GtkWidget *luma_text_button_new(const char *label, const char *icon, const char *style) {
  g_return_val_if_fail(label != NULL, NULL);
  if (style == NULL)
    style = "plain";
  g_return_val_if_fail(g_str_equal(style, "plain") || g_str_equal(style, "fill") || g_str_equal(style, "key") ||
                           g_str_equal(style, "danger") || g_str_equal(style, "raised"), NULL);
  luma_ui_install();
  GtkWidget *button = gtk_button_new();
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(button, "lumaui-text-button");
  if (g_str_equal(style, "danger"))
    gtk_widget_add_css_class(button, "danger-solid");
  else if (!g_str_equal(style, "plain"))
    gtk_widget_add_css_class(button, style);
  GtkWidget *words = gtk_label_new(label);
  if (icon != NULL && *icon != '\0') {
    GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_halign(line, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(line, "lumaui-text-button-content");
    gtk_box_append(GTK_BOX(line), luma_ui_icon_image(icon, 0));
    gtk_box_append(GTK_BOX(line), words);
    gtk_button_set_child(GTK_BUTTON(button), line);
  } else {
    gtk_button_set_child(GTK_BUTTON(button), words);
  }
  luma_ui_set_accessible_label(button, label);
  return button;
}

void luma_text_button_set_size(GtkButton *button, const char *size) {
  g_return_if_fail(GTK_IS_BUTTON(button) && size != NULL);
  g_return_if_fail(g_str_equal(size, "regular") || g_str_equal(size, "small") ||
                   g_str_equal(size, "hero") || g_str_equal(size, "large") || g_str_equal(size, "touch"));
  luma_ui_set_css_class(GTK_WIDGET(button), "small", g_str_equal(size, "small"));
  luma_ui_set_css_class(GTK_WIDGET(button), "hero", g_str_equal(size, "hero"));
  luma_ui_set_css_class(GTK_WIDGET(button), "large", g_str_equal(size, "large"));
  luma_ui_set_css_class(GTK_WIDGET(button), "touch", g_str_equal(size, "touch"));
}

void luma_text_button_set_small(GtkButton *button, gboolean small) {
  g_return_if_fail(GTK_IS_BUTTON(button));
  luma_ui_set_css_class(GTK_WIDGET(button), "small", small);
}

void luma_text_button_set_hero(GtkButton *button, gboolean hero) {
  g_return_if_fail(GTK_IS_BUTTON(button));
  luma_ui_set_css_class(GTK_WIDGET(button), "hero", hero);
}

void luma_text_button_set_danger(GtkButton *button, gboolean danger) {
  g_return_if_fail(GTK_IS_BUTTON(button));
  luma_ui_set_css_class(GTK_WIDGET(button), "danger", danger);
}

GtkWidget *luma_icon_button_new(const char *icon, const char *label) {
  g_return_val_if_fail(icon != NULL && *icon != '\0' && label != NULL, NULL);
  luma_ui_install();
  GtkWidget *button = gtk_button_new();
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(button, "lumaui-icon-only");
  g_object_set_data_full(G_OBJECT(button), "luma-icon-name", g_strdup(icon), g_free);
  gtk_button_set_child(GTK_BUTTON(button), luma_ui_icon_image(icon, 0));
  gtk_widget_remove_css_class(button, "image-button"); /* a LumaUI part, not the legacy icon-button look */
  gtk_widget_set_tooltip_text(button, label);
  luma_ui_set_accessible_label(button, label);
  return button;
}

void luma_icon_button_set_raised(GtkButton *button, gboolean raised) {
  g_return_if_fail(GTK_IS_BUTTON(button));
  luma_ui_set_css_class(GTK_WIDGET(button), "raised", raised);
}

void luma_icon_button_set_active(GtkButton *button, gboolean active) {
  g_return_if_fail(GTK_IS_BUTTON(button));
  luma_ui_set_css_class(GTK_WIDGET(button), "on", active);
  gtk_accessible_update_state(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_STATE_PRESSED,
                              active ? GTK_ACCESSIBLE_TRISTATE_TRUE : GTK_ACCESSIBLE_TRISTATE_FALSE, -1);
  const char *icon = g_object_get_data(G_OBJECT(button), "luma-icon-name");
  if (g_strcmp0(icon, "heart") == 0) {
    gtk_widget_add_css_class(GTK_WIDGET(button), "love");
    gtk_button_set_child(button, luma_ui_icon_image(active ? "heart-filled" : "heart", 0));
  }
}

void luma_icon_button_set_size(GtkButton *button, const char *size) {
  g_return_if_fail(GTK_IS_BUTTON(button) && size != NULL);
  g_return_if_fail(g_str_equal(size, "regular") || g_str_equal(size, "row") || g_str_equal(size, "small") || g_str_equal(size, "large"));
  luma_ui_set_css_class(GTK_WIDGET(button), "large", g_str_equal(size, "large"));
  luma_ui_set_css_class(GTK_WIDGET(button), "touch", g_str_equal(size, "touch"));
  luma_ui_set_css_class(GTK_WIDGET(button), "row", g_str_equal(size, "row"));
  luma_ui_set_css_class(GTK_WIDGET(button), "small", g_str_equal(size, "small"));
}

GtkWidget *luma_switch_new(gboolean big) {
  luma_ui_install();
  GtkWidget *sw = gtk_switch_new();
  gtk_widget_set_valign(sw, GTK_ALIGN_CENTER);
  gtk_widget_set_halign(sw, GTK_ALIGN_START);
  gtk_widget_add_css_class(sw, "lumaui-switch");
  luma_ui_set_css_class(sw, "big", big);
  return sw;
}
