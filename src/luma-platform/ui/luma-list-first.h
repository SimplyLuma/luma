/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_listfirst.ListFirst (Python, K-NAV, v71 phone.js stackStart / .phstack). */
#pragma once

#include <gtk/gtk.h>

#include "luma-title-island.h"

G_BEGIN_DECLS

/**
 * LumaListFirst:
 *
 * A list and its page: side by side on a computer (the app's own widths), list
 * first on a phone (under 560). There the list is the screen, under its large
 * title (32/750 on the 16 px gutter); picking a row in any #GtkListBox inside
 * the list pushes the page in from the right; Back (the page's title island ‹,
 * or a floating 44 px ‹ until it has one) returns. Pages are reparented when
 * the window crosses 560, so keep your own references.
 *
 * Signal: #LumaListFirst::showing ("list" or "detail").
 * The same widget tree and CSS classes as Python's ListFirst (lumaui-list-first*).
 */
#define LUMA_TYPE_LIST_FIRST (luma_list_first_get_type())
G_DECLARE_FINAL_TYPE(LumaListFirst, luma_list_first, LUMA, LIST_FIRST, GtkBox)

/**
 * luma_list_first_new:
 * @list_page: the list (the sidebar)
 * @detail_page: the page an item opens
 * @title: (nullable): the list's large title on a phone ("Settings")
 *
 * Returns: (transfer floating): a new list-first stack
 */
GtkWidget *luma_list_first_new(GtkWidget *list_page, GtkWidget *detail_page, const char *title);
void luma_list_first_set_title(LumaListFirst *self, const char *title);
/**
 * luma_list_first_set_push_on_activate:
 * @self: a list-first stack
 * @push: whether a row activated in the list pushes the page (the default)
 */
void luma_list_first_set_push_on_activate(LumaListFirst *self, gboolean push);
/**
 * luma_list_first_show_detail:
 * @self: a list-first stack
 *
 * Push the page (a phone; a computer already shows it).
 */
void luma_list_first_show_detail(LumaListFirst *self);
/**
 * luma_list_first_show_list:
 * @self: a list-first stack
 *
 * Back to the list (a phone). Emits #LumaListFirst::back.
 */
void luma_list_first_show_list(LumaListFirst *self);
/**
 * luma_list_first_get_showing:
 * @self: a list-first stack
 *
 * Returns: "list" or "detail": what a phone shows
 */
const char *luma_list_first_get_showing(LumaListFirst *self);
gboolean luma_list_first_get_phone(LumaListFirst *self);
/**
 * luma_list_first_attach_island:
 * @self: a list-first stack
 * @island: the page's title island: its ‹ returns to the list, and the floating ‹ steps aside
 */
void luma_list_first_attach_island(LumaListFirst *self, LumaTitleIsland *island);

G_END_DECLS
