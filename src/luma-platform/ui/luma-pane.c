/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-pane.h"

struct _LumaPane {
  GtkBox parent_instance;
  GtkWidget *child;
};

G_DEFINE_FINAL_TYPE(LumaPane, luma_pane, GTK_TYPE_BOX)

static void luma_pane_dispose(GObject *object) {
  LumaPane *self = LUMA_PANE(object);
  self->child = NULL;
  G_OBJECT_CLASS(luma_pane_parent_class)->dispose(object);
}

static void luma_pane_class_init(LumaPaneClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_pane_dispose;
}

static void luma_pane_init(LumaPane *self) {
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self),
                                 GTK_ORIENTATION_VERTICAL);
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_vexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-pane");
  /* Every pane is an island; the toolkit draws that appearance. */
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-island");
}

GtkWidget *luma_pane_new(void) {
  return g_object_new(LUMA_TYPE_PANE, NULL);
}

void luma_pane_set_child(LumaPane *self, GtkWidget *child) {
  g_return_if_fail(LUMA_IS_PANE(self));
  g_return_if_fail(child == NULL || GTK_IS_WIDGET(child));
  if (self->child == child)
    return;
  if (self->child != NULL)
    gtk_box_remove(GTK_BOX(self), self->child);
  self->child = child;
  if (child != NULL) {
    gtk_widget_set_hexpand(child, TRUE);
    gtk_widget_set_vexpand(child, TRUE);
    gtk_box_append(GTK_BOX(self), child);
  }
}

GtkWidget *luma_pane_get_child(LumaPane *self) {
  g_return_val_if_fail(LUMA_IS_PANE(self), NULL);
  return self->child;
}
