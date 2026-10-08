/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_stack.StackedButton / StackedButtons (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaStackedButton:
 *
 * One action: a Lucide glyph over a short label. Activates its #GtkActionable
 * action or emits #GtkButton::clicked. Disabled reads as flat and muted.
 */
#define LUMA_TYPE_STACKED_BUTTON (luma_stacked_button_get_type())
G_DECLARE_FINAL_TYPE(LumaStackedButton, luma_stacked_button, LUMA, STACKED_BUTTON, GtkButton)

/**
 * luma_stacked_button_new:
 * @icon: a Lucide glyph ("log-out")
 * @label: a short word ("Leave")
 * @danger: whether it is destructive (red)
 *
 * Returns: (transfer floating): a new button
 */
GtkWidget *luma_stacked_button_new(const char *icon, const char *label, gboolean danger);

/**
 * LumaStackedButtons:
 *
 * An equal pair or trio of #LumaStackedButton. Any other count is refused
 * (critical) when the group is shown.
 */
#define LUMA_TYPE_STACKED_BUTTONS (luma_stacked_buttons_get_type())
G_DECLARE_FINAL_TYPE(LumaStackedButtons, luma_stacked_buttons, LUMA, STACKED_BUTTONS, GtkBox)

/**
 * luma_stacked_buttons_new:
 * @small: the compact variant
 *
 * Returns: (transfer floating): a new, empty group
 */
GtkWidget *luma_stacked_buttons_new(gboolean small);
void luma_stacked_buttons_append(LumaStackedButtons *self, LumaStackedButton *button);

G_END_DECLS
