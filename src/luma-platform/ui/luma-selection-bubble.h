/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_bubble.SelectionBubble (Python). */
#pragma once

#include <gtk/gtk.h>
#include "luma-action-center.h"

G_BEGIN_DECLS

/**
 * LumaSelectionBubble:
 *
 * Formatting marks that float over a text selection. Over a #GtkTextView it
 * follows the selection by itself (after a 200 ms settle, away while typing
 * or scrolling, below the text near the top); over anything else the app
 * calls luma_selection_bubble_show_for(). Arrows move between marks, Esc
 * closes it, Alt+F10 moves focus into it.
 */
#define LUMA_TYPE_SELECTION_BUBBLE (luma_selection_bubble_get_type())
G_DECLARE_FINAL_TYPE(LumaSelectionBubble, luma_selection_bubble, LUMA, SELECTION_BUBBLE, GtkBox)

/**
 * luma_selection_bubble_new:
 * @view: the text view (or canvas) it floats over
 * @items: (array length=n_items): its marks (%LUMA_BAR_ITEM_ACTION,
 *   %LUMA_BAR_ITEM_SEPARATOR)
 * @n_items: how many
 * @label: (nullable): what it is, for assistive technology ("Formatting")
 *
 * Returns: (transfer floating): a new bubble
 */
GtkWidget *luma_selection_bubble_new(GtkWidget *view, LumaBarItem *const *items, guint n_items,
                                     const char *label);
/**
 * luma_selection_bubble_show_for:
 * @self: a bubble
 * @rect: the selection's box in the view's coordinates
 */
void luma_selection_bubble_show_for(LumaSelectionBubble *self, const GdkRectangle *rect);
void luma_selection_bubble_hide(LumaSelectionBubble *self);
/**
 * luma_selection_bubble_set_active:
 * @self: a bubble
 * @icon: the mark's Lucide glyph ("bold")
 * @active: whether it is on for the current selection
 */
void luma_selection_bubble_set_active(LumaSelectionBubble *self, const char *icon, gboolean active);
gboolean luma_selection_bubble_focus_first(LumaSelectionBubble *self);
gboolean luma_selection_bubble_get_shown(LumaSelectionBubble *self);

G_END_DECLS
