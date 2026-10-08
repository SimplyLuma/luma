/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_tabs.TabBar (Python, K-NAV, v71 phone.js tabBar / .phtabs, Clock's .cktabbar). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaTabBar:
 *
 * Parallel destinations (3 or more peer places, each with a glyph): each tab an
 * icon over its name, full width at the foot on a phone (16 from each side, 34
 * up, bar material). Compact: Clock's icon-only tabs inside an action bar (48,
 * the raised chip, a corner count, a green running dot). Signal
 * #LumaTabBar::changed (const char *key). Same tree and classes as Python's
 * TabBar (lumaui-tab-bar, lumaui-tab, lumaui-tab-face, lumaui-tab-label,
 * lumaui-tab-dot, lumaui-tab-count).
 */
#define LUMA_TYPE_TAB_BAR (luma_tab_bar_get_type())
G_DECLARE_FINAL_TYPE(LumaTabBar, luma_tab_bar, LUMA, TAB_BAR, GtkBox)

/**
 * luma_tab_bar_new:
 * @compact: Clock's icon-only tabs for inside an action bar
 *
 * Returns: (transfer floating): a new, empty tab bar
 */
GtkWidget *luma_tab_bar_new(gboolean compact);
/**
 * luma_tab_bar_add:
 * @self: a tab bar
 * @key: the place's key
 * @label: its name
 * @icon: its Lucide glyph
 *
 * The first place added is current until luma_tab_bar_set_current().
 */
void luma_tab_bar_add(LumaTabBar *self, const char *key, const char *label, const char *icon);
const char *luma_tab_bar_get_current(LumaTabBar *self);
/**
 * luma_tab_bar_set_current:
 * @self: a tab bar
 * @key: a place's key
 * @notify: emit #LumaTabBar::changed when it changes
 */
void luma_tab_bar_set_current(LumaTabBar *self, const char *key, gboolean notify);
/**
 * luma_tab_bar_set_count:
 * @self: a tab bar
 * @key: a place's key
 * @count: a count badge (Alarms: 3); 0 removes it
 * @attention: the red one (Phone's Voicemail)
 */
void luma_tab_bar_set_count(LumaTabBar *self, const char *key, int count, gboolean attention);
/**
 * luma_tab_bar_set_running:
 * @self: a tab bar
 * @key: a place's key
 * @running: a green dot on the tab (a running stopwatch or timer)
 */
void luma_tab_bar_set_running(LumaTabBar *self, const char *key, gboolean running);
/**
 * luma_tab_bar_float_over:
 * @self: a tab bar
 * @where: an overlay, or a widget in a window (its window layer host)
 *
 * Sit full width at the foot (16 from each side, 34 up; CSS .floating).
 */
void luma_tab_bar_float_over(LumaTabBar *self, GtkWidget *where);
/**
 * luma_tab_bar_wants_tabs:
 * @n_places: how many places
 * @all_have_icons: whether every place has a glyph
 *
 * v71's rule: a switch between 3 or more places, each with an icon, is a tab bar on a phone.
 *
 * Returns: whether it should be one
 */
gboolean luma_tab_bar_wants_tabs(guint n_places, gboolean all_have_icons);

G_END_DECLS
