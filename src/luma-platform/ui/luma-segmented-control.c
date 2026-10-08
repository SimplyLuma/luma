/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-segmented-control.h"

enum { CHANGED, N_SIGNALS };
static guint signals[N_SIGNALS];

struct _LumaSegmentedControl {
  GtkBox parent_instance;
  GtkToggleButton *first;
  char *selected;
};

G_DEFINE_FINAL_TYPE(LumaSegmentedControl, luma_segmented_control, GTK_TYPE_BOX)

static void segment_toggled(GtkToggleButton *button, gpointer user_data) {
  LumaSegmentedControl *self = LUMA_SEGMENTED_CONTROL(user_data);
  if (!gtk_toggle_button_get_active(button))
    return;
  const char *id = g_object_get_data(G_OBJECT(button), "luma-segment-id");
  if (g_strcmp0(self->selected, id) == 0)
    return;
  g_free(self->selected);
  self->selected = g_strdup(id);
  g_signal_emit(self, signals[CHANGED], 0, self->selected);
}

static void luma_segmented_control_finalize(GObject *object) {
  LumaSegmentedControl *self = LUMA_SEGMENTED_CONTROL(object);
  g_clear_pointer(&self->selected, g_free);
  G_OBJECT_CLASS(luma_segmented_control_parent_class)->finalize(object);
}

static void luma_segmented_control_class_init(LumaSegmentedControlClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = luma_segmented_control_finalize;
  signals[CHANGED] = g_signal_new("changed", G_TYPE_FROM_CLASS(klass),
                                  G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                  G_TYPE_NONE, 1, G_TYPE_STRING);
}

static void luma_segmented_control_init(LumaSegmentedControl *self) {
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_box_set_homogeneous(GTK_BOX(self), TRUE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-segmented-control");
}

GtkWidget *luma_segmented_control_new(void) {
  return g_object_new(LUMA_TYPE_SEGMENTED_CONTROL, NULL);
}

void luma_segmented_control_append(LumaSegmentedControl *self,
                                   const char *id, const char *label) {
  g_return_if_fail(LUMA_IS_SEGMENTED_CONTROL(self));
  g_return_if_fail(id != NULL && id[0] != '\0');
  GtkToggleButton *button = GTK_TOGGLE_BUTTON(gtk_toggle_button_new_with_label(label));
  if (self->first == NULL)
    self->first = button;
  else
    gtk_toggle_button_set_group(button, self->first);
  g_object_set_data_full(G_OBJECT(button), "luma-segment-id", g_strdup(id), g_free);
  g_signal_connect(button, "toggled", G_CALLBACK(segment_toggled), self);
  gtk_box_append(GTK_BOX(self), GTK_WIDGET(button));
  if (self->selected == NULL)
    gtk_toggle_button_set_active(button, TRUE);
}

void luma_segmented_control_set_selected(LumaSegmentedControl *self,
                                         const char *id) {
  g_return_if_fail(LUMA_IS_SEGMENTED_CONTROL(self));
  for (GtkWidget *child = gtk_widget_get_first_child(GTK_WIDGET(self)); child != NULL;
       child = gtk_widget_get_next_sibling(child)) {
    const char *candidate = g_object_get_data(G_OBJECT(child), "luma-segment-id");
    if (g_strcmp0(candidate, id) == 0) {
      gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(child), TRUE);
      return;
    }
  }
}

const char *luma_segmented_control_get_selected(LumaSegmentedControl *self) {
  g_return_val_if_fail(LUMA_IS_SEGMENTED_CONTROL(self), NULL);
  return self->selected;
}
