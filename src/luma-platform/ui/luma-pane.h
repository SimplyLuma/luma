/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_PANE (luma_pane_get_type())
G_DECLARE_FINAL_TYPE(LumaPane, luma_pane, LUMA, PANE, GtkBox)

GtkWidget *luma_pane_new(void);
void luma_pane_set_child(LumaPane *self, GtkWidget *child);

/**
 * luma_pane_get_child:
 * @self: a #LumaPane
 *
 * Returns: (transfer none) (nullable): the pane's current child
 */
GtkWidget *luma_pane_get_child(LumaPane *self);

G_END_DECLS
