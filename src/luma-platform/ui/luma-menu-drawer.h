/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_drawer.MenuDrawer and action_bubble.FloatingMenu /
 * MenuItem (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/** LumaFloatAlign: horizontal alignment of a floating menu with its anchor. */
typedef enum {
  LUMA_FLOAT_ALIGN_CENTER,
  LUMA_FLOAT_ALIGN_START,
  LUMA_FLOAT_ALIGN_END,
} LumaFloatAlign;


/**
 * LumaMenuDrawer:
 *
 * A menu as a bottom drawer, the one drawer every LumaUI menu uses at phone
 * width. Rows draw their own keycaps and checks; a submenu is a second page
 * with a Back row. Emits #LumaMenuDrawer::closed.
 */
#define LUMA_TYPE_MENU_DRAWER (luma_menu_drawer_get_type())
G_DECLARE_FINAL_TYPE(LumaMenuDrawer, luma_menu_drawer, LUMA, MENU_DRAWER, GtkBox)

/**
 * luma_menu_drawer_present_model:
 * @where: a widget in the window; the model's actions are activated on it
 * @model: the menu
 * @title: (nullable): the drawer's title
 *
 * Returns: (transfer none): the drawer, valid until it closes
 */
LumaMenuDrawer *luma_menu_drawer_present_model(GtkWidget *where, GMenuModel *model, const char *title);
/** Present an app-owned grid or other menu content in the shared phone drawer.
 * @child: (transfer full): content to place below the handle and optional title
 */
LumaMenuDrawer *luma_menu_drawer_present_child(GtkWidget *where, GtkWidget *child,
                                               const char *title);
void luma_menu_drawer_close(LumaMenuDrawer *self);

/**
 * LumaFloatingMenu:
 *
 * A short menu anchored to a control: a card in the window's layer host on a
 * computer, the #LumaMenuDrawer on a phone. Rows are items, headings or
 * separators. Items activate their #GAction.
 */
#define LUMA_TYPE_FLOATING_MENU (luma_floating_menu_get_type())
G_DECLARE_FINAL_TYPE(LumaFloatingMenu, luma_floating_menu, LUMA, FLOATING_MENU, GtkBox)

/**
 * luma_floating_menu_new:
 * @label: (nullable): what it is, for assistive technology ("Menu")
 *
 * Returns: (transfer floating): a new, empty menu
 */
GtkWidget *luma_floating_menu_new(const char *label);
/**
 * luma_floating_menu_add_item:
 * @self: a menu
 * @label: the row's words
 * @icon: (nullable): a Lucide glyph
 * @gicon: (nullable): an icon in place of @icon (an app's)
 * @note: (nullable): a quiet note on the right ("Default")
 * @action_name: (nullable): the detailed #GAction name it activates
 * @selected: whether it is the chosen row
 */
void luma_floating_menu_add_item(LumaFloatingMenu *self, const char *label, const char *icon, GIcon *gicon,
                                 const char *note, const char *action_name, gboolean selected);
/**
 * luma_floating_menu_add_rich_item:
 * @self: a menu
 * @label: the row's main text
 * @icon: (nullable): a Lucide glyph
 * @gicon: (nullable): an icon in place of @icon
 * @note: (nullable): quiet text beside the main text, such as a signature
 * @description: (nullable): a second line explaining the action
 * @action_name: (nullable): the detailed #GAction name it activates
 * @selected: whether it is the chosen row
 */
void luma_floating_menu_add_rich_item(LumaFloatingMenu *self, const char *label, const char *icon, GIcon *gicon,
                                      const char *note, const char *description, const char *action_name,
                                      gboolean selected);
void luma_floating_menu_add_heading(LumaFloatingMenu *self, const char *heading);
void luma_floating_menu_add_separator(LumaFloatingMenu *self);
/**
 * luma_floating_menu_popup:
 * @self: a menu
 * @anchor: the control it opens from (under it, or over it in the window's
 *   lower half)
 */
/**
 * luma_floating_menu_set_picker:
 * @self: a floating menu
 * @picker: %TRUE for a choice among values (v70 Settings' .cfpop): the chosen item ends in a check,
 *   and the menu is as wide as its anchor, 200 at least, 6 below it
 */
void luma_floating_menu_set_picker(LumaFloatingMenu *self, gboolean picker);
void luma_floating_menu_popup(LumaFloatingMenu *self, GtkWidget *anchor);
/**
 * luma_floating_menu_popup_at:
 * @self: a menu
 * @anchor: the widget it opens from (its actions are activated there)
 * @x: the point in @anchor's coordinates: the pointer of a right click, the
 *   touch point of a long press
 * @y: its y
 *
 * A context menu: its top start corner on the point, flipped to the point's
 * other side at the window's edges, shifted inside only when neither side
 * fits. luma_floating_menu_popup() opens from the anchor itself (the Menu
 * key, Shift+F10).
 */
void luma_floating_menu_popup_at(LumaFloatingMenu *self, GtkWidget *anchor, double x, double y);
/**
 * luma_floating_menu_add_shortcut_item:
 * @self: a menu
 * @label: the row's words
 * @icon: (nullable): a Lucide glyph
 * @shortcut: its keys, shown as keycaps ("Ctrl+D")
 * @action_name: (nullable): the detailed #GAction name it activates
 */
void luma_floating_menu_add_shortcut_item(LumaFloatingMenu *self, const char *label, const char *icon,
                                          const char *shortcut, const char *action_name);
/**
 * luma_floating_menu_add_submenu:
 * @self: a menu
 * @label: the row's words
 * @icon: (nullable): a Lucide glyph
 * @submenu: (transfer floating): the menu it opens
 *
 * A row that cascades: hovering it (or → on it, or a click) opens @submenu
 * beside the menu, flipped at the window's edge; a pointer heading for it
 * keeps it open for 250 ms across other rows; ← and Esc close one level; a
 * leaf in it closes every level. On a phone it drills in place, with Back.
 */
void luma_floating_menu_add_submenu(LumaFloatingMenu *self, const char *label, const char *icon,
                                    LumaFloatingMenu *submenu);
void luma_floating_menu_close(LumaFloatingMenu *self);
gboolean luma_floating_menu_get_is_open(LumaFloatingMenu *self);

/**
 * luma_floating_menu_set_align:
 * @self: a menu
 * @align: alignment of the menu against its anchor
 */
void luma_floating_menu_set_align(LumaFloatingMenu *self, LumaFloatAlign align);

G_END_DECLS
