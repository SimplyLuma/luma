/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI internals of the action parts (menus, the action center, the
 * selection bubble); not installed. The twins of action_bubble.float_at /
 * rect_in and structure_drawer.MenuDrawer.present_items.
 */
#pragma once

#include <gtk/gtk.h>

#include "luma-action-center.h"
#include "luma-menu-drawer.h"

G_BEGIN_DECLS

typedef enum {
  LUMA_FLOAT_ABOVE,
  LUMA_FLOAT_BELOW,
} LumaFloatSide;


/* float_at: place @widget (already a layer of @host) beside @rect, in host
 * coordinates: @offset above it (or below), flipped within @edge of the host's
 * edge, kept @inset inside the host's sides. Negative @offset/@edge/@inset
 * take the selection bubble's tokens; @width > 0 fixes the width. Adds or
 * removes class "below". Returns the side it went to. */
LumaFloatSide luma_ui_float_at(GtkWidget *host, GtkWidget *widget, const GdkRectangle *rect,
                               LumaFloatSide prefer, LumaFloatAlign align, int offset, int edge, int inset,
                               int width);
/* rect_in: @rect (NULL: the whole widget, border box) moved into @host's
 * coordinates. */
GdkRectangle luma_ui_rect_in(GtkWidget *host, GtkWidget *widget, const GdkRectangle *rect);

/* One plain menu row (action_bubble.MenuItem): an item, a heading (only
 * @label) or a separator (all NULL). The action is a detailed GAction name,
 * activated on the widget the menu opened from. */
typedef enum {
  LUMA_MENU_ROW_ITEM,
  LUMA_MENU_ROW_HEADING,
  LUMA_MENU_ROW_SEPARATOR,
} LumaMenuRowKind;

typedef struct {
  LumaMenuRowKind kind;
  char *label;
  char *icon;
  GIcon *gicon;
  char *note;
  char *action;
  gboolean selected;
} LumaMenuRow;

LumaMenuRow *luma_menu_row_new(LumaMenuRowKind kind, const char *label, const char *icon, GIcon *gicon,
                               const char *note, const char *action, gboolean selected);
void luma_menu_row_free(LumaMenuRow *row);

/* MenuDrawer.present_items: @rows (LumaMenuRow) as a drawer over the window
 * @where is in; items' actions are activated on @where. */
LumaMenuDrawer *luma_menu_drawer_present_items(GtkWidget *where, GPtrArray *rows, const char *title);

/* v71: a menu's rows (LumaMenuRow) rising from @self's bar as a grown panel; items' actions are
 * activated on @where (NULL: the center). Every menu does this on a phone when a bar is showing. */
void luma_action_center_grow_rows(LumaActionCenter *self, const char *key, GPtrArray *rows, GtkWidget *where);

/* A bar item's Lucide glyph (BarAction.icon), or NULL. */
const char *luma_bar_item_get_icon(LumaBarItem *item);

G_END_DECLS
