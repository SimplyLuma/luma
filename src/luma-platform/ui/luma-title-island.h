/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_island.TitleIsland (Python, K-NAV, v71 lNavTtl / .fisl / .crisl). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaTitleIslandLead:
 * @LUMA_TITLE_ISLAND_LEAD_MENU: ☰, the app's places ("menu"); ☰ and the title are one control
 * @LUMA_TITLE_ISLAND_LEAD_BACK: ‹, up one level ("back"); a control of its own
 * @LUMA_TITLE_ISLAND_LEAD_NONE: no lead (Calendar's head)
 */
typedef enum {
  LUMA_TITLE_ISLAND_LEAD_MENU,
  LUMA_TITLE_ISLAND_LEAD_BACK,
  LUMA_TITLE_ISLAND_LEAD_NONE,
} LumaTitleIslandLead;
GType luma_title_island_lead_get_type(void);
#define LUMA_TYPE_TITLE_ISLAND_LEAD (luma_title_island_lead_get_type())

/**
 * LumaTitleIslandGrows:
 * @LUMA_TITLE_ISLAND_GROWS_AUTO: ☰ grows into a menu, otherwise into details
 * @LUMA_TITLE_ISLAND_GROWS_MENU: min(320, window − 24), stops 128 above the foot; a row picked folds it
 * @LUMA_TITLE_ISLAND_GROWS_DETAILS: a phone's width less 24, min(360, window − 24) in a window
 */
typedef enum {
  LUMA_TITLE_ISLAND_GROWS_AUTO,
  LUMA_TITLE_ISLAND_GROWS_MENU,
  LUMA_TITLE_ISLAND_GROWS_DETAILS,
} LumaTitleIslandGrows;
GType luma_title_island_grows_get_type(void);
#define LUMA_TYPE_TITLE_ISLAND_GROWS (luma_title_island_grows_get_type())

/**
 * LumaTitleIsland:
 *
 * The v71 title island, top left, in the bar's material: the lead as a 48 px
 * square, a full-height hairline, the title over its subtitle, optional faces
 * before it and trailing buttons after it; 48 tall, 12 from the left, 6 under
 * the title row (52 from the window's top on a phone). Tap it and it grows
 * down into its menu (the places, ☰ becoming ✕) or its information (details)
 * as one piece of glass; ✕, a second tap, Esc, a tap beside it or picking a
 * place folds it. By default it shows only at phone tier (under 560);
 * luma_title_island_set_phone_only() keeps it at every width.
 *
 * Signals: #LumaTitleIsland::lead (the lead pressed while folded: Back, or ☰
 * with nothing to grow into), #LumaTitleIsland::title (the title pressed with
 * nothing to grow into), #LumaTitleIsland::grown (gboolean).
 *
 * The same widget tree and CSS classes as Python's TitleIsland
 * (lumaui-title-island*), styled by luma-appkit-base.css.
 */
#define LUMA_TYPE_TITLE_ISLAND (luma_title_island_get_type())
G_DECLARE_FINAL_TYPE(LumaTitleIsland, luma_title_island, LUMA, TITLE_ISLAND, GtkBox)

/**
 * LumaTitleIslandGrowFunc:
 * @island: the island about to grow
 * @user_data: the data given with the function
 *
 * Make what the island grows into, each time it grows (Python `grow=callable`).
 *
 * Returns: (transfer floating) (nullable): the panel; %NULL does not grow
 */
typedef GtkWidget *(*LumaTitleIslandGrowFunc)(LumaTitleIsland *island, gpointer user_data);

/**
 * luma_title_island_new:
 * @lead: ☰, ‹ or none
 * @title: what you are looking at
 * @subtitle: (nullable): the line under it
 *
 * Returns: (transfer floating): a new title island
 */
GtkWidget *luma_title_island_new(LumaTitleIslandLead lead, const char *title, const char *subtitle);
/**
 * luma_title_island_set_title:
 * @self: an island
 * @title: the title
 * @subtitle: (nullable): the line under it; %NULL keeps the current one, "" removes it
 */
void luma_title_island_set_title(LumaTitleIsland *self, const char *title, const char *subtitle);
void luma_title_island_set_subtitle(LumaTitleIsland *self, const char *subtitle);
const char *luma_title_island_get_title(LumaTitleIsland *self);
const char *luma_title_island_get_subtitle(LumaTitleIsland *self);
/**
 * luma_title_island_set_lead:
 * @self: an island
 * @lead: ☰, ‹ or none
 * @label: (nullable): what the lead says to assistive technology ("Back to Projects");
 *   %NULL is "Places" or "Back"
 */
void luma_title_island_set_lead(LumaTitleIsland *self, LumaTitleIslandLead lead, const char *label);
LumaTitleIslandLead luma_title_island_get_lead(LumaTitleIsland *self);
/**
 * luma_title_island_set_lead_icon:
 * @self: the island
 * @icon: (nullable): a semantic icon name, or NULL for the menu glyph
 *
 * Replaces only a menu lead's folded glyph, for example with the drive kind.
 * The shared title/menu action and close-while-grown behavior are unchanged.
 * Back and lead-less islands keep their standard presentation.
 */
void luma_title_island_set_lead_icon(LumaTitleIsland *self, const char *icon);

/**
 * luma_title_island_set_faces:
 * @self: an island
 * @faces: (nullable): faces before the title (Messages' avatar, Charlie's stack)
 */
void luma_title_island_set_faces(LumaTitleIsland *self, GtkWidget *faces);
/**
 * luma_title_island_set_status:
 * @self: an island
 * @status: (nullable): a dot before the title: "record" (pulsing red), "paused", "here" (presence before the subtitle), or %NULL
 */
void luma_title_island_set_status(LumaTitleIsland *self, const char *status);
/**
 * luma_title_island_set_title_content:
 * @self: an island
 * @content: (nullable): the title half drawn by the app (Calendar's month); %NULL restores
 *   the title and subtitle. It stays one button.
 */
void luma_title_island_set_title_content(LumaTitleIsland *self, GtkWidget *content);
/**
 * luma_title_island_add_trailing:
 * @self: an island
 * @widget: a trailing button past a hairline (Messages' Call and Video), 44 × 48
 *
 * Returns: (transfer none): @widget
 */
GtkWidget *luma_title_island_add_trailing(LumaTitleIsland *self, GtkWidget *widget);
/**
 * luma_title_island_button_new:
 * @icon: a Lucide glyph
 * @label: its name, as tooltip and accessible label
 *
 * A trailing button for luma_title_island_add_trailing() (Python `TitleIsland.button`).
 *
 * Returns: (transfer floating): a new button
 */
GtkWidget *luma_title_island_button_new(const char *icon, const char *label);
/**
 * luma_title_island_set_grow_func:
 * @self: an island
 * @func: (nullable) (scope notified): makes the panel; %NULL: nothing to grow into
 * @user_data: (closure): data for @func
 * @destroy: (destroy user_data) (nullable): frees @user_data
 * @grows: menu, details, or by the lead
 */
void luma_title_island_set_grow_func(LumaTitleIsland *self, LumaTitleIslandGrowFunc func, gpointer user_data,
                                     GDestroyNotify destroy, LumaTitleIslandGrows grows);
/**
 * luma_title_island_set_grow_widget:
 * @self: an island
 * @widget: (nullable): what a tap grows into: a widget that lives in the page (the
 *   sidebar, in a #GtkBox or #GtkRevealer) is borrowed and put back on fold
 * @grows: menu, details, or by the lead
 */
void luma_title_island_set_grow_widget(LumaTitleIsland *self, GtkWidget *widget, LumaTitleIslandGrows grows);
/**
 * luma_title_island_grow_into:
 * @self: an island
 * @widget: (nullable): what to grow into; %NULL is what the grow function or widget gives
 *
 * Returns: whether it grew
 */
gboolean luma_title_island_grow_into(LumaTitleIsland *self, GtkWidget *widget);
void luma_title_island_fold(LumaTitleIsland *self);
void luma_title_island_toggle(LumaTitleIsland *self);
gboolean luma_title_island_get_grown(LumaTitleIsland *self);
/**
 * luma_title_island_get_panel:
 * @self: an island
 *
 * Returns: (transfer none) (nullable): what it has grown into
 */
GtkWidget *luma_title_island_get_panel(LumaTitleIsland *self);
/**
 * luma_title_island_float_over:
 * @self: an island
 * @where: an overlay (a #LumaLayerHost or the app's own #GtkOverlay), or a widget
 *   in a window, whose window layer host is used
 *
 * Float at the top left: 12 from the left, 6 under the title row.
 */
void luma_title_island_float_over(LumaTitleIsland *self, GtkWidget *where);
/**
 * luma_title_island_attach:
 * @self: an island
 * @region: as for luma_title_island_float_over()
 *
 * The same as luma_title_island_float_over() (the name K-C first published).
 */
void luma_title_island_attach(LumaTitleIsland *self, GtkWidget *region);
/**
 * luma_title_island_set_phone_only:
 * @self: an island
 * @phone_only: %TRUE (the default): it shows only under 560; %FALSE: at every width
 */
void luma_title_island_set_phone_only(LumaTitleIsland *self, gboolean phone_only);
gboolean luma_title_island_get_phone_only(LumaTitleIsland *self);
/**
 * luma_title_island_follow:
 * @self: an island
 * @scroller: the page that scrolls under it
 * @until: how far it follows, in px (90)
 * @fade: how far until it is gone, in px (60)
 *
 * Fade the island as @scroller scrolls (v71 Calendar's Month).
 */
void luma_title_island_follow(LumaTitleIsland *self, GtkScrolledWindow *scroller, int until, int fade);

/** Select standard (48px divided lead, default) or creative (44px single Back/title control). */
void luma_title_island_set_variant(LumaTitleIsland *self, const char *variant);

G_END_DECLS
