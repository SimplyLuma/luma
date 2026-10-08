/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-action-bar.h"

struct _LumaActionBar {
  GtkBox parent_instance;
  GtkWidget *leading;
  GtkWidget *center;
  GtkWidget *trailing;
  GtkWidget *title;
};
G_DEFINE_FINAL_TYPE(LumaActionBar, luma_action_bar, GTK_TYPE_BOX)
static void luma_action_bar_class_init(LumaActionBarClass *klass) {
  (void)klass;
}
static void luma_action_bar_init(LumaActionBar *s) {
  gtk_orientable_set_orientation(GTK_ORIENTABLE(s), GTK_ORIENTATION_HORIZONTAL);
  gtk_box_set_spacing(GTK_BOX(s), 9);
  gtk_widget_add_css_class(GTK_WIDGET(s), "luma-action-bar");
  s->leading = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
  s->center = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
  s->trailing = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
  gtk_widget_set_hexpand(s->center, TRUE);
  gtk_widget_set_halign(s->center, GTK_ALIGN_FILL);
  gtk_widget_set_halign(s->trailing, GTK_ALIGN_END);
  s->title = gtk_label_new("");
  gtk_label_set_ellipsize(GTK_LABEL(s->title), PANGO_ELLIPSIZE_END);
  gtk_label_set_xalign(GTK_LABEL(s->title), 0);
  gtk_widget_set_hexpand(s->title, TRUE);
  gtk_widget_add_css_class(s->title, "luma-action-title");
  gtk_box_append(GTK_BOX(s->center), s->title);
  /* A bar centres what it holds. A box hands each child its own full height,
   * so a 28px control inside a 46px bar is drawn 46px tall however the sheet
   * states it — the reason Luma's controls came out uniformly too tall. */
  gtk_widget_set_valign(s->leading, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(s->center, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(s->trailing, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(s->title, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(s), s->leading);
  gtk_box_append(GTK_BOX(s), s->center);
  gtk_box_append(GTK_BOX(s), s->trailing);
}

/* Centre every control a bar already holds. Applications that fill a plain box
 * with the toolbar class call this once the bar is built; the kit's own bars
 * do it as they go. */
void luma_ui_centre_controls(GtkWidget *bar) {
  GtkWidget *child;

  g_return_if_fail(GTK_IS_WIDGET(bar));
  for (child = gtk_widget_get_first_child(bar); child != NULL;
       child = gtk_widget_get_next_sibling(child)) {
    if (gtk_widget_get_valign(child) == GTK_ALIGN_FILL)
      gtk_widget_set_valign(child, GTK_ALIGN_CENTER);
  }
}
GtkWidget *luma_action_bar_new(void) {
  return g_object_new(LUMA_TYPE_ACTION_BAR, NULL);
}
void luma_action_bar_set_title(LumaActionBar *s, const char *t) {
  g_return_if_fail(LUMA_IS_ACTION_BAR(s));
  gtk_label_set_label(GTK_LABEL(s->title), t ? t : "");
}
const char *luma_action_bar_get_title(LumaActionBar *s) {
  g_return_val_if_fail(LUMA_IS_ACTION_BAR(s), NULL);
  return gtk_label_get_label(GTK_LABEL(s->title));
}
void luma_action_bar_add_leading(LumaActionBar *s, GtkWidget *w) {
  g_return_if_fail(LUMA_IS_ACTION_BAR(s));
  g_return_if_fail(GTK_IS_WIDGET(w));
  if (gtk_widget_get_valign(w) == GTK_ALIGN_FILL)
    gtk_widget_set_valign(w, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(s->leading), w);
}
void luma_action_bar_add_trailing(LumaActionBar *s, GtkWidget *w) {
  g_return_if_fail(LUMA_IS_ACTION_BAR(s));
  g_return_if_fail(GTK_IS_WIDGET(w));
  if (gtk_widget_get_valign(w) == GTK_ALIGN_FILL)
    gtk_widget_set_valign(w, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(s->trailing), w);
}
GtkWidget *luma_action_bar_get_leading(LumaActionBar *s) {
  g_return_val_if_fail(LUMA_IS_ACTION_BAR(s), NULL);
  return s->leading;
}
GtkWidget *luma_action_bar_get_center(LumaActionBar *s) {
  g_return_val_if_fail(LUMA_IS_ACTION_BAR(s), NULL);
  return s->center;
}
GtkWidget *luma_action_bar_get_trailing(LumaActionBar *s) {
  g_return_val_if_fail(LUMA_IS_ACTION_BAR(s), NULL);
  return s->trailing;
}
