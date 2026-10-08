/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_sidebar.SidebarToggle (Python). */
#pragma once

#include <gtk/gtk.h>

#include "luma-title-island.h"

G_BEGIN_DECLS

/**
 * LumaSidebarToggle:
 *
 * Hide and show a sidebar: the panel-left button, F9, and a drawer from the
 * left at phone width. The sidebar is any widget in a box (it slides away) or
 * an #AdwOverlaySplitView (its own animation). #LumaSidebarToggle:shown
 * notifies on every change; the kit does not persist it.
 */
#define LUMA_TYPE_SIDEBAR_TOGGLE (luma_sidebar_toggle_get_type())
G_DECLARE_FINAL_TYPE(LumaSidebarToggle, luma_sidebar_toggle, LUMA, SIDEBAR_TOGGLE, GtkToggleButton)

/**
 * luma_sidebar_toggle_new:
 * @sidebar: the sidebar widget, or an #AdwOverlaySplitView
 * @shown: whether it starts shown
 *
 * Returns: (transfer floating): a new toggle
 */
GtkWidget *luma_sidebar_toggle_new(GtkWidget *sidebar, gboolean shown);
gboolean luma_sidebar_toggle_get_shown(LumaSidebarToggle *self);
void luma_sidebar_toggle_set_shown(LumaSidebarToggle *self, gboolean shown);
/**
 * luma_sidebar_toggle_toggle:
 * @self: a toggle
 *
 * What the button and F9 do.
 */
void luma_sidebar_toggle_toggle(LumaSidebarToggle *self);
/* Like toggle(), with the opening drawer's initial focus (e.g. search). */
void luma_sidebar_toggle_toggle_with_focus(LumaSidebarToggle *self, GtkWidget *initial_focus);
/**
 * luma_sidebar_toggle_set_island:
 * @self: a sidebar toggle
 * @island: (nullable): v71: on a phone this title island is the sidebar's ☰: it grows into the
 *   sidebar (borrowed, given back on fold; picking a row folds it), and the toggle steps aside
 *
 * Python `SidebarToggle(sidebar, island=island)`.
 */
void luma_sidebar_toggle_set_island(LumaSidebarToggle *self, LumaTitleIsland *island);

/**
 * luma_sidebar_toggle_set_drawer_below:
 * @self: a sidebar toggle
 * @width: exclusive layout breakpoint; default560, use901 for compact drawers
 *
 * Phone title islands keep the normal phone breakpoint independently.
 */
void luma_sidebar_toggle_set_drawer_below(LumaSidebarToggle *self, int width);
int luma_sidebar_toggle_get_drawer_below(LumaSidebarToggle *self);

/* Disable sidebar navigation at phone widths when an app uses another places control. */
void luma_sidebar_toggle_set_phone_enabled(LumaSidebarToggle *self, gboolean enabled);

/* Keep the controller/menu action and adaptive sidebar while omitting the button. */
void luma_sidebar_toggle_set_control_visible(LumaSidebarToggle *self, gboolean visible);

G_END_DECLS
