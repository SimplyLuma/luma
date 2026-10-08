/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_ACTION_BAR (luma_action_bar_get_type())
G_DECLARE_FINAL_TYPE(LumaActionBar, luma_action_bar, LUMA, ACTION_BAR, GtkBox)

GtkWidget *luma_action_bar_new(void);

/* Centre every control a bar already holds, so a control keeps the height the
 * sheet states instead of being stretched to its bar's. */
void luma_ui_centre_controls(GtkWidget *bar);
void luma_action_bar_set_title(LumaActionBar *self, const char *title);
const char *luma_action_bar_get_title(LumaActionBar *self);
void luma_action_bar_add_leading(LumaActionBar *self, GtkWidget *widget);
void luma_action_bar_add_trailing(LumaActionBar *self, GtkWidget *widget);
/**
 * luma_action_bar_get_leading:
 * @self: an action bar
 *
 * Returns: (transfer none): the leading widget box
 */
GtkWidget *luma_action_bar_get_leading(LumaActionBar *self);
/**
 * luma_action_bar_get_center:
 * @self: an action bar
 *
 * Returns: (transfer none): the center widget box
 */
GtkWidget *luma_action_bar_get_center(LumaActionBar *self);
/**
 * luma_action_bar_get_trailing:
 * @self: an action bar
 *
 * Returns: (transfer none): the trailing widget box
 */
GtkWidget *luma_action_bar_get_trailing(LumaActionBar *self);

G_END_DECLS
