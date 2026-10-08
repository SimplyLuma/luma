/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_sidebar.SidebarFoot and FilterHeading (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaFilterHeading:
 *
 * "Favourites · Show all" at the top of a filtered list. Emits
 * #LumaFilterHeading::show-all.
 */
#define LUMA_TYPE_FILTER_HEADING (luma_filter_heading_get_type())
G_DECLARE_FINAL_TYPE(LumaFilterHeading, luma_filter_heading, LUMA, FILTER_HEADING, GtkBox)

/**
 * luma_filter_heading_new:
 * @label: (nullable): the filter's name
 *
 * Returns: (transfer floating): a new heading
 */
GtkWidget *luma_filter_heading_new(const char *label);
void luma_filter_heading_set_label(LumaFilterHeading *self, const char *label);

/**
 * LumaSidebarFoot:
 *
 * Search, then a filter picker or the New square, at a sidebar's foot. Filter
 * and New may be used together.
 *
 * Signals: #LumaSidebarFoot::search-changed (const char *text) after the
 * typing pause, #LumaSidebarFoot::filter-changed (const char *key) when a
 * person picks a view.
 */
#define LUMA_TYPE_SIDEBAR_FOOT (luma_sidebar_foot_get_type())
G_DECLARE_FINAL_TYPE(LumaSidebarFoot, luma_sidebar_foot, LUMA, SIDEBAR_FOOT, GtkBox)

/**
 * luma_sidebar_foot_new:
 * @search: the search field's placeholder ("Search contacts")
 *
 * Returns: (transfer floating): a new foot
 */
GtkWidget *luma_sidebar_foot_new(const char *search);
/**
 * luma_sidebar_foot_add_filter:
 * @self: a foot
 * @key: the view's stable key
 * @label: its name ("Favourites")
 * @icon: a Lucide glyph
 *
 * Add a view to the filter picker. The first one added is current.
 */
void luma_sidebar_foot_add_filter(LumaSidebarFoot *self, const char *key, const char *label,
                                  const char *icon);
/**
 * luma_sidebar_foot_set_filter:
 * @self: a foot
 * @key: a view's key
 *
 * Show @key as the chosen view, without emitting
 * #LumaSidebarFoot::filter-changed.
 */
void luma_sidebar_foot_set_filter(LumaSidebarFoot *self, const char *key);
const char *luma_sidebar_foot_get_filter(LumaSidebarFoot *self);
/**
 * luma_sidebar_foot_set_filter_count:
 * @self: a foot
 * @key: a view's key
 * @count: how many it shows, or -1 for no badge
 */
void luma_sidebar_foot_set_filter_count(LumaSidebarFoot *self, const char *key, int count);
/**
 * luma_sidebar_foot_set_add:
 * @self: a foot
 * @label: what New makes ("New contact")
 * @icon: a Lucide glyph ("plus")
 * @action_name: the detailed #GAction name it activates
 */
void luma_sidebar_foot_set_add(LumaSidebarFoot *self, const char *label, const char *icon,
                               const char *action_name);
/**
 * luma_sidebar_foot_open_filters:
 * @self: a foot
 *
 * Open the filter menu upward from the picker (a drawer at phone width).
 */
void luma_sidebar_foot_open_filters(LumaSidebarFoot *self);
const char *luma_sidebar_foot_get_text(LumaSidebarFoot *self);
/**
 * luma_sidebar_foot_get_entry:
 * @self: a foot
 *
 * Returns: (transfer none): the search field (a place search may attach to it)
 */
GtkWidget *luma_sidebar_foot_get_entry(LumaSidebarFoot *self);
/**
 * luma_sidebar_foot_get_heading:
 * @self: a foot
 *
 * Returns: (transfer none): the #LumaFilterHeading to put over the list
 */
LumaFilterHeading *luma_sidebar_foot_get_heading(LumaSidebarFoot *self);

G_END_DECLS
