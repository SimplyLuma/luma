/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>
#include <adwaita.h>

G_BEGIN_DECLS

/* A frame sidebar. A named variant owns its width and row rhythm; the app
 * supplies destinations and selection through the real GtkListBox. */
#define LUMA_TYPE_NAVIGATION_SIDEBAR (luma_navigation_sidebar_get_type())
G_DECLARE_FINAL_TYPE(LumaNavigationSidebar, luma_navigation_sidebar, LUMA, NAVIGATION_SIDEBAR, GtkBox)

/* variant: NULL, "people", "destinations", "resources", "files", "tree";
 * width: NULL, "narrow", "regular", "wide". */
GtkWidget *luma_navigation_sidebar_new(const char *variant, const char *width);
void luma_navigation_sidebar_set_variant(LumaNavigationSidebar *self, const char *variant, const char *width);
void luma_navigation_sidebar_append_header(LumaNavigationSidebar *self, GtkWidget *child);
void luma_navigation_sidebar_append_footer(LumaNavigationSidebar *self, GtkWidget *child);
void luma_navigation_sidebar_append_row(LumaNavigationSidebar *self, GtkListBoxRow *row);
void luma_navigation_sidebar_append_section(LumaNavigationSidebar *self, const char *label);
void luma_navigation_sidebar_clear(LumaNavigationSidebar *self);
/**
 * luma_navigation_sidebar_get_list:
 * @self: a navigation sidebar
 *
 * Returns: (transfer none): the selectable list for row activation and selection
 */
GtkListBox *luma_navigation_sidebar_get_list(LumaNavigationSidebar *self);

/* Keep an application's live sidebar and split view. This sets the shared
 * sidebar width and frame classes without moving or recreating semantic rows.
 * In "rail" mode existing icon/label rows become 60px icon-over-label rows;
 * call again after adding rows to dress the new ones. Other row types retain
 * their presentation. width: "rail" (88px), "narrow", "regular", or "wide". */
void luma_navigation_sidebar_adapt_live(AdwOverlaySplitView *split_view,
                                        GtkWidget *live_sidebar,
                                        const char *width);

/* A shared destination row: title, optional subtitle, lead and trail slots.
 * The app may update the text without replacing the row or losing selection. */
#define LUMA_TYPE_NAVIGATION_ROW (luma_navigation_row_get_type())
G_DECLARE_FINAL_TYPE(LumaNavigationRow, luma_navigation_row, LUMA, NAVIGATION_ROW, GtkListBoxRow)

GtkWidget *luma_navigation_row_new(const char *title, const char *subtitle);
void luma_navigation_row_set_title(LumaNavigationRow *self, const char *title);
void luma_navigation_row_set_subtitle(LumaNavigationRow *self, const char *subtitle);
void luma_navigation_row_set_meta(LumaNavigationRow *self, const char *meta);
void luma_navigation_row_set_lead(LumaNavigationRow *self, GtkWidget *lead);
void luma_navigation_row_set_trail(LumaNavigationRow *self, GtkWidget *trail);

G_END_DECLS
