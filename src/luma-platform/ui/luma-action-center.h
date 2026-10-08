/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_center.py: ActionCenter, ActionEditor, BarAction, BarChip,
 * BarContext, BarPrompt, SEPARATOR, SPACER (Python). */
#pragma once

#include <gtk/gtk.h>
#include "luma-mode-switch.h"
#include "luma-layer-host.h"

G_BEGIN_DECLS

/**
 * LumaBarItemKind:
 * @LUMA_BAR_ITEM_ACTION: a control (BarAction)
 * @LUMA_BAR_ITEM_CHIP: the context chip at the bar's start (BarChip)
 * @LUMA_BAR_ITEM_PROMPT: the well that grows into the editor (BarPrompt)
 * @LUMA_BAR_ITEM_CONTEXT: the line over a double-height bar (BarContext)
 * @LUMA_BAR_ITEM_SEPARATOR: a hairline between groups
 * @LUMA_BAR_ITEM_SPACER: pushes what follows to the end
 * @LUMA_BAR_ITEM_SEARCH: a live search well
 * @LUMA_BAR_ITEM_MODES: a hosted #LumaModeSwitch
 */
typedef enum {
  LUMA_BAR_ITEM_ACTION,
  LUMA_BAR_ITEM_CHIP,
  LUMA_BAR_ITEM_PROMPT,
  LUMA_BAR_ITEM_CONTEXT,
  LUMA_BAR_ITEM_SEPARATOR,
  LUMA_BAR_ITEM_SPACER,
  LUMA_BAR_ITEM_SEARCH,
  LUMA_BAR_ITEM_MODES,
  LUMA_BAR_ITEM_WIDGET,
} LumaBarItemKind;
GType luma_bar_item_kind_get_type(void);
#define LUMA_TYPE_BAR_ITEM_KIND (luma_bar_item_kind_get_type())

/**
 * LumaBarItem:
 *
 * One thing on a bar, said semantically. An action with an icon and a label is
 * a button; an icon alone is an icon button named by its tooltip. One action
 * per bar may be primary (the key); danger is red ink; active marks a toggle
 * that is on. Actions activate their #GAction; a chip's × emits
 * #LumaBarItem::dismissed; a prompt grows the action center into its editor
 * (and emits #LumaBarItem::activated).
 */
#define LUMA_TYPE_BAR_ITEM (luma_bar_item_get_type())
G_DECLARE_FINAL_TYPE(LumaBarItem, luma_bar_item, LUMA, BAR_ITEM, GObject)

/**
 * luma_bar_item_new_action:
 * @icon: (nullable): a Lucide glyph; %NULL (or empty) with a @label makes a word alone (v70 .bt with no glyph)
 * @label: (nullable): its word; %NULL makes an icon button
 * @action_name: (nullable): the detailed #GAction name it activates
 *
 * Returns: (transfer full): a new action
 */
LumaBarItem *luma_bar_item_new_action(const char *icon, const char *label, const char *action_name);
/**
 * luma_bar_item_new_chip:
 * @label: what the bar is about ("3 photos")
 * @icon: (nullable): a Lucide glyph
 * @dismissible: whether it has a ×
 *
 * Returns: (transfer full): a new chip
 */
LumaBarItem *luma_bar_item_new_chip(const char *label, const char *icon, gboolean dismissible);
/**
 * luma_bar_item_chip_set_identity:
 * @self: a static chip
 * @identity: TRUE for a flat stacked title/detail, FALSE for the default chip
 * @detail: (nullable): the secondary line
 *
 * Select before creating or showing the control. Dismissible/action chips stay subjects.
 */
void luma_bar_item_chip_set_identity(LumaBarItem *self, gboolean identity, const char *detail);
/**
 * luma_bar_item_chip_set_paintable:
 * @self: a subject chip
 * @paintable: (nullable): a small preview shown ahead of its label
 */
void luma_bar_item_chip_set_paintable(LumaBarItem *self, GdkPaintable *paintable);
/**
 * luma_bar_item_new_chip_action:
 * @label: the chip's visible text
 * @icon: (nullable): a Lucide glyph
 * @action_name: (nullable): the detailed #GAction name it activates
 *
 * An activatable chip with the same surface as a static chip. It emits
 * #LumaBarItem::activated after a click and remains keyboard accessible.
 * Returns: (transfer full): a new chip action
 */
LumaBarItem *luma_bar_item_new_chip_action(const char *label, const char *icon,
                                            const char *action_name);
/**
 * luma_bar_item_new_prompt:
 * @text: the prompt ("Reply to Priya and Nora…")
 *
 * Returns: (transfer full): a new prompt
 */
LumaBarItem *luma_bar_item_new_prompt(const char *text);
/**
 * luma_bar_item_new_context:
 * @icon: (nullable): a Lucide glyph; %NULL shows the words alone
 * @text: the line; "{}" is where @emphasis goes, in bold
 * @emphasis: (nullable): the bold words
 * @dismissible: whether it has a ×
 *
 * Returns: (transfer full): a new context line
 */
LumaBarItem *luma_bar_item_new_context(const char *icon, const char *text, const char *emphasis,
                                       gboolean dismissible);
/**
 * luma_bar_item_new_separator:
 *
 * Returns: (transfer full): a separator
 */
LumaBarItem *luma_bar_item_new_separator(void);
/** luma_bar_item_new_rule: Returns: (transfer full): a visible grouped toolbar divider. */
LumaBarItem *luma_bar_item_new_rule(void);
/** luma_bar_item_set_text_glyph: Use a square typographic glyph target (Aa), without an icon. */
void luma_bar_item_set_text_glyph(LumaBarItem *self, gboolean text_glyph);
/**
 * luma_bar_item_new_spacer:
 *
 * Returns: (transfer full): a spacer
 */
LumaBarItem *luma_bar_item_new_spacer(void);
/**
 * luma_bar_item_new_search:
 * @placeholder: the visible search hint, also its accessible name
 *
 * A persistent search item. Emits #LumaBarItem::search-changed and
 * #LumaBarItem::search-activated with the current text. Escape clears text.
 * Reuse this item across luma_action_center_show_bar() calls to keep its
 * actual entry, text, and signal connections.
 *
 * Returns: (transfer full): a search item
 */
LumaBarItem *luma_bar_item_new_search(const char *placeholder);
void luma_bar_item_search_set_text(LumaBarItem *self, const char *text);
const char *luma_bar_item_search_get_text(LumaBarItem *self);
void luma_bar_item_search_focus(LumaBarItem *self);
/**
 * luma_bar_item_search_get_entry:
 * @self: a search item
 *
 * Returns the persistent entry after the item has been shown in a bar.
 *
 * Returns: (transfer none) (nullable): the search entry, or %NULL before first use
 */
GtkWidget *luma_bar_item_search_get_entry(LumaBarItem *self);
/**
 * luma_bar_item_new_modes:
 * @modes: (transfer none): the kit mode switch to host in the bar
 *
 * The item holds a reference to @modes. Reuse the item across bar swaps to
 * retain selection, keyboard navigation and its #LumaModeSwitch::changed
 * signal connection.
 *
 * Returns: (transfer full): a mode item
 */
LumaBarItem *luma_bar_item_new_modes(LumaModeSwitch *modes);
/** Retain an unparented shared control (e.g. MediaTransportLcd) across bar rebuilds. */
LumaBarItem *luma_bar_item_new_widget(GtkWidget *widget);
/**
 * luma_bar_item_search_set_collapsed:
 * @self: a search item
 * @collapsed: %TRUE (v71): while empty it is a search glyph at every width, and
 *   pressed it turns the bar into the field in place, the field the bottom-most
 *   row with ✕ at its end (✕ clears and gives the row back). %FALSE (the
 *   default): a field that stays in the row, folding to the glyph on a phone
 *   while empty, as released.
 *
 * v71's search is placeholder "Search", the glyph with room round it, 6 px before
 * the next control, the clear ✕ only once there is text.
 */
void luma_bar_item_search_set_collapsed(LumaBarItem *self, gboolean collapsed);
/**
 * luma_bar_item_search_set_keep:
 * @self: a search item
 * @keep: whether an empty search remains a field at phone widths
 *
 * The persistent field shares spare bar width. Explicit collapsed mode takes
 * precedence, matching the Python BarSearch keep option.
 */
void luma_bar_item_search_set_keep(LumaBarItem *self, gboolean keep);
LumaBarItemKind luma_bar_item_get_kind(LumaBarItem *self);
/**
 * LumaBarPanelFunc:
 * @item: the action pressed
 * @user_data: the data given with the function
 *
 * Make the panel an action grows the bar into (Details, Share, ⋯, Add…), each
 * time it opens.
 *
 * Returns: (transfer floating) (nullable): the panel
 */
typedef GtkWidget *(*LumaBarPanelFunc)(LumaBarItem *item, gpointer user_data);
/**
 * luma_bar_item_set_panel:
 * @self: an action
 * @key: (nullable): the panel's name; %NULL is the action's label or glyph
 * @func: (scope notified) (nullable): makes the panel
 * @user_data: (closure): data for @func
 * @destroy: (destroy user_data) (nullable): frees @user_data
 *
 * Python `BarAction(panel=callable)`: pressing the action grows the bar into
 * the panel; pressing it again folds it. While open the action is the raised chip.
 */
/* Replace a panel action with a Close icon while open; restore it on fold. */
void luma_bar_item_set_panel_close(LumaBarItem *self, gboolean close);

void luma_bar_item_set_panel(LumaBarItem *self, const char *key, LumaBarPanelFunc func, gpointer user_data,
                             GDestroyNotify destroy);
void luma_bar_item_set_tooltip(LumaBarItem *self, const char *tooltip);
/**
 * luma_bar_item_set_phone_compact:
 * @self: an action with an icon and label, or a chip
 * @compact: hide its text at phone width, keeping its icon or preview and name
 *
 * The full label returns when the bar grows back to desktop width.
 */
void luma_bar_item_set_phone_compact(LumaBarItem *self, gboolean compact);
/**
 * luma_bar_item_set_keep_label:
 * @self: an action with a glyph and words
 * @keep_label: on a phone keep its words beside the glyph (v71 Contacts' Edit,
 *   "Move here"); every other labelled action shows its glyph alone there
 *
 * Python `BarAction(..., keep_label=True)`.
 */
void luma_bar_item_set_keep_label(LumaBarItem *self, gboolean keep_label);
void luma_bar_item_set_primary(LumaBarItem *self, gboolean primary);
void luma_bar_item_set_danger(LumaBarItem *self, gboolean danger);
void luma_bar_item_set_active(LumaBarItem *self, gboolean active);
void luma_bar_item_set_sensitive(LumaBarItem *self, gboolean sensitive);
/**
 * luma_bar_item_get_active_control:
 * @self: a bar item
 *
 * Returns: (transfer none) (nullable): the currently rendered button or
 *   hosted control. Use it as the anchor for #LumaFloatingMenu; the pointer
 *   clears when the bar replaces the control.
 */
GtkWidget *luma_bar_item_get_active_control(LumaBarItem *self);
/**
 * luma_bar_item_set_group:
 * @self: an action item
 * @label: (nullable): accessible name shared by adjacent actions
 *
 * Adjacent actions bearing the same group name form one recessed control.
 */
void luma_bar_item_set_group(LumaBarItem *self, const char *label);
/**
 * luma_bar_item_set_icon_trailing:
 * @self: an action item
 * @trailing: whether the icon follows a visible label
 */
void luma_bar_item_set_icon_trailing(LumaBarItem *self, gboolean trailing);
/**
 * luma_bar_item_create_control:
 * @self: an item
 * @size: "bar" (36), "tool" (32) or "bubble" (32 at 8)
 *
 * The widget for this item, as the kit draws it (make_control in Python).
 *
 * Returns: (transfer floating): a new widget
 */
/** @size: bar, tool, or caption (52px icon-over-label creative footer action). */
GtkWidget *luma_bar_item_create_control(LumaBarItem *self, const char *size);

/**
 * LumaActionEditor:
 *
 * The full editor the bar grows into: header (title, summary, modes), optional
 * field rows, tools, the content, footer (Discard, the primary action). With
 * no body the kit's composer is the content and its text is the draft; Esc
 * folds keeping the draft, Ctrl+Return runs the primary action. A sheet on a
 * phone. Signals: #LumaActionEditor::mode-changed (const char *key),
 * #LumaActionEditor::discarded.
 */
#define LUMA_TYPE_ACTION_EDITOR (luma_action_editor_get_type())
G_DECLARE_FINAL_TYPE(LumaActionEditor, luma_action_editor, LUMA, ACTION_EDITOR, GtkBox)

/**
 * luma_action_editor_new:
 * @title: the header's title ("Reply")
 * @icon: a Lucide glyph
 *
 * Returns: (transfer floating): a new editor with the kit's composer
 */
GtkWidget *luma_action_editor_new(const char *title, const char *icon);
/**
 * luma_action_editor_set_summary:
 * @self: an editor
 * @text: (nullable): the line under the title; "{}" is where @emphasis goes
 * @emphasis: (nullable): the bold words
 */
void luma_action_editor_set_summary(LumaActionEditor *self, const char *text, const char *emphasis);
void luma_action_editor_add_mode(LumaActionEditor *self, const char *key, const char *label,
                                 const char *icon);
void luma_action_editor_set_mode(LumaActionEditor *self, const char *key);
/**
 * luma_action_editor_add_field:
 * @self: an editor
 * @label: the row's label ("To")
 * @input: (nullable): the row's input; %NULL makes a plain text entry
 *
 * Returns: (transfer none): the input
 */
GtkWidget *luma_action_editor_add_field(LumaActionEditor *self, const char *label, GtkWidget *input);
/**
 * luma_action_editor_get_field:
 * @self: an editor
 * @label: a field row's label
 *
 * Returns: (transfer none) (nullable): that row's input
 */
GtkWidget *luma_action_editor_get_field(LumaActionEditor *self, const char *label);
void luma_action_editor_add_tool(LumaActionEditor *self, LumaBarItem *tool);
/**
 * luma_action_editor_set_body:
 * @self: an editor
 * @body: (nullable): the app's own content in place of the composer
 */
void luma_action_editor_set_body(LumaActionEditor *self, GtkWidget *body);
void luma_action_editor_set_placeholder(LumaActionEditor *self, const char *placeholder);
/**
 * luma_action_editor_set_primary:
 * @self: an editor
 * @primary: the footer's key action ("Send")
 */
void luma_action_editor_set_primary(LumaActionEditor *self, LumaBarItem *primary);
/** Enable unmodified Return for the primary action; Shift+Return keeps a newline. Default false. */
void luma_action_editor_set_submit_on_return(LumaActionEditor *self, gboolean enabled);
/**
 * luma_action_editor_set_form:
 * @self: an editor
 * @cancel_label: (nullable): the Cancel button's words ("Cancel"); %NULL for a sheet with only its primary
 *
 * v70 Settings' sheets (cfSheet, .cflac): the mode pill and summary, one 44 px row per field with a 128
 * label column, and a footer of Cancel then the primary key, right-aligned. No composer (a body only
 * when set_body gives one), no discard, no hint. Esc and Cancel fold; Enter in a field is the primary.
 */
void luma_action_editor_set_form(LumaActionEditor *self, const char *cancel_label);
/**
 * luma_action_editor_set_draft_summary:
 * @self: an editor
 * @summary: (nullable): with a custom body, what has been done so far, shown
 *   as "Draft: …" on the folded bar
 */
void luma_action_editor_set_draft_summary(LumaActionEditor *self, const char *summary);
/**
 * luma_action_editor_get_draft:
 * @self: an editor
 *
 * Returns: (transfer full) (nullable): the composer's text, or the draft
 *   summary; %NULL when nothing is written
 */
char *luma_action_editor_get_draft(LumaActionEditor *self);
void luma_action_editor_clear(LumaActionEditor *self);
void luma_action_editor_focus_content(LumaActionEditor *self);
gboolean luma_action_editor_run_primary(LumaActionEditor *self);

/**
 * LumaActionCenter:
 *
 * The floating bar of one island that grows in place into an editor. States
 * (#LumaActionCenter:state, "state-changed" (const char *state)): "hidden",
 * "bar", "double" (a context line over the bar), "editor", "split" (two bars:
 * what is selected, and what to do with it). It keeps the region's toasts
 * above it.
 */
#define LUMA_TYPE_ACTION_CENTER (luma_action_center_get_type())
G_DECLARE_FINAL_TYPE(LumaActionCenter, luma_action_center, LUMA, ACTION_CENTER, GtkBox)

/**
 * luma_action_center_new:
 * @editor: (nullable): the editor the prompt grows into
 *
 * Returns: (transfer floating): a new, hidden action center
 */
GtkWidget *luma_action_center_new(LumaActionEditor *editor);
/**
 * luma_action_center_attach:
 * @self: an action center
 * @region: a #LumaLayerHost, a widget inside one, or a window
 *
 * Float at the foot of @region.
 */
void luma_action_center_attach(LumaActionCenter *self, GtkWidget *region);
/**
 * luma_action_center_set_bar_bottom:
 * @self: an action center
 * @pixels: distance from the host's bottom edge for a bar or split bar
 *
 * Creative islands use a 16 px inset; the default remains the platform's
 * 24 px inset. The editor retains its own placement.
 */
void luma_action_center_set_bar_bottom(LumaActionCenter *self, int pixels);
/* Fill the phone span with the standard gutter, retaining natural desktop width. */
void luma_action_center_set_phone_wide(LumaActionCenter *self, gboolean wide);
/**
 * luma_action_center_set_phone_grown_inset:
 * @self: an action center
 * @pixels: nonnegative grown phone gutter, or -1 for automatic sizing
 *
 * Overrides only the grown phone panel gutter. Resting and desktop sizing
 * remain unchanged; widget minimum sizes still apply.
 */
void luma_action_center_set_phone_grown_inset(LumaActionCenter *self, int pixels);
int luma_action_center_get_phone_grown_inset(LumaActionCenter *self);
/**
 * luma_action_center_show_bar:
 * @self: an action center
 * @items: (array length=n_items): the bar's items, in order
 * @n_items: how many
 * @context: (nullable): a %LUMA_BAR_ITEM_CONTEXT line; makes it double height
 */
void luma_action_center_show_bar(LumaActionCenter *self, LumaBarItem *const *items, guint n_items,
                                 LumaBarItem *context);
/**
 * luma_action_center_get_bar_control:
 * @self: an action center
 * @index: position in the most recent luma_action_center_show_bar() items
 *
 * Returns: (transfer none) (nullable): the item's rendered control. It stays
 * valid until the next bar rebuild and can anchor an associated popup.
 */
GtkWidget *luma_action_center_get_bar_control(LumaActionCenter *self, guint index);
/**
 * luma_action_center_show_split:
 * @self: an action center
 * @first: (array length=n_first): what is selected
 * @n_first: how many
 * @second: (array length=n_second): what to do with it
 * @n_second: how many
 */
void luma_action_center_show_split(LumaActionCenter *self, LumaBarItem *const *first, guint n_first,
                                   LumaBarItem *const *second, guint n_second);
void luma_action_center_set_editor(LumaActionCenter *self, LumaActionEditor *editor);
/**
 * luma_action_center_get_editor:
 * @self: an action center
 *
 * Returns: (transfer none) (nullable): the editor
 */
LumaActionEditor *luma_action_center_get_editor(LumaActionCenter *self);
void luma_action_center_grow(LumaActionCenter *self);
void luma_action_center_fold(LumaActionCenter *self);
void luma_action_center_discard(LumaActionCenter *self);
void luma_action_center_hide_bar(LumaActionCenter *self);
const char *luma_action_center_get_state(LumaActionCenter *self);

/* ── v71: the bar grows ─────────────────────────────────────────────────
 * A panel rises above the bar's own row, inside the same glass, with a
 * hairline between; the row's layout holds (icon buttons share the width, a
 * labelled primary keeps its size). Grown width: a phone, the width less 16 a
 * side; a window, min(380, width − 24). Tapping the same action again, Esc, or
 * any action in the row folds it; panel rows fold it after they act.
 * luma_action_center_fold() folds a grown panel as it folds the editor. */

/**
 * luma_action_center_grow_panel:
 * @self: an action center showing its bar
 * @key: the panel's name; growing the open key again folds it
 * @panel: what rises above the row
 *
 * Python `ActionCenter.grow(key, widget)`.
 */
void luma_action_center_grow_panel(LumaActionCenter *self, const char *key, GtkWidget *panel);
/**
 * luma_action_center_grow_entry:
 * @self: an action center showing its bar
 * @key: the panel's name
 * @panel: (nullable): what shows above the field (suggestions, matches)
 * @entry: the field that is the point of the panel
 *
 * Python `grow(key, widget, entry=field)`: the field replaces the bar's row
 * outright, as its bottom-most line, with ✕ at its end to fold.
 */
void luma_action_center_grow_entry(LumaActionCenter *self, const char *key, GtkWidget *panel, GtkWidget *entry);
/**
 * luma_action_center_get_grown:
 * @self: an action center
 *
 * Returns: (transfer none) (nullable): the open panel's key
 */
const char *luma_action_center_get_grown(LumaActionCenter *self);
/**
 * luma_action_center_more:
 * @self: an action center
 * @items: (array length=n_items): the actions to list
 * @n_items: how many
 *
 * Python `ActionCenter.more(items)`: grow into the ⋯ panel, one row per
 * action. At phone width a bar that does not fit puts its own overflow here
 * by itself: single actions leave first, from the end; the primary, a chip and
 * the first control stay, and ⋯ sits before the primary.
 */
void luma_action_center_more(LumaActionCenter *self, LumaBarItem *const *items, guint n_items);
/**
 * luma_action_center_grow_menu:
 * @self: an action center
 * @key: the panel's name
 * @model: the menu; sections are divided by a hairline, a submenu is a second
 *   page in the same panel with ‹ back
 * @where: (nullable): the widget the actions are activated on; %NULL is the center
 *
 * Python `menus.bar_menu(host, items)`: a menu rising from the bar (on a phone
 * every #LumaFloatingMenu does this by itself).
 */
void luma_action_center_grow_menu(LumaActionCenter *self, const char *key, GMenuModel *model, GtkWidget *where);
/**
 * luma_action_center_find:
 * @widget: a widget in a window
 *
 * Returns: (transfer none) (nullable): the action center showing at the foot
 *   of @widget's window (or of its nearest layer host), if any
 */
LumaActionCenter *luma_action_center_find(GtkWidget *widget);

/**
 * luma_bar_panel_new:
 * @heading: (nullable): a quiet heading over the rows
 *
 * A panel of 48 px rows (glyph, words), for ⋯ and menus in the bar.
 *
 * Returns: (transfer floating): a new panel
 */
GtkWidget *luma_bar_panel_new(const char *heading);
/**
 * luma_bar_panel_add_row:
 * @panel: a bar panel
 * @icon: (nullable): a Lucide glyph
 * @label: the row's words
 * @action_name: (nullable): the detailed #GAction it activates
 * @danger: red ink, for the destructive row (it goes last)
 *
 * Returns: (transfer none): the row's button; pressing it folds the bar
 */
GtkWidget *luma_bar_panel_add_row(GtkWidget *panel, const char *icon, const char *label, const char *action_name,
                                  gboolean danger);
void luma_bar_panel_add_heading(GtkWidget *panel, const char *heading);

/**
 * luma_bar_panel_row_set_state:
 * @row: a row from luma_bar_panel_add_row()
 * @on: the raised chip (a toggled choice)
 * @current: the place or choice in effect (Python `PanelRow(current=)`)
 */
void luma_bar_panel_row_set_state(GtkWidget *row, gboolean on, gboolean current);
/* Compact person-row metrics, matching PanelRow(appearance="person"). */
void luma_bar_panel_row_set_person(GtkWidget *row, gboolean person);

/**
 * luma_bar_tiles_new:
 * @columns: tiles per row; 0 is one per tile, at most 5
 * @compact: 64 tall instead of 72
 * @chip: on the raised chip, 8 apart (Calendar's event view)
 *
 * Python `BarTiles`: square tiles, a glyph over its words, in a bar panel
 * (Filer's View as Grid / List, the share targets).
 *
 * Returns: (transfer floating): an empty grid of tiles
 */
GtkWidget *luma_bar_tiles_new(guint columns, gboolean compact, gboolean chip);
/**
 * luma_bar_tiles_add:
 * @tiles: from luma_bar_tiles_new()
 * @icon: a Lucide glyph
 * @label: its words
 * @action_name: (nullable): the detailed #GAction it activates
 * @on: the chosen one (raised)
 * @danger: red
 *
 * Returns: (transfer none): the tile's button; pressing it folds the bar
 */
GtkWidget *luma_bar_tiles_add(GtkWidget *tiles, const char *icon, const char *label, const char *action_name,
                              gboolean on, gboolean danger);
/**
 * luma_bar_tile_set_subtitle:
 * @tile: the button returned by luma_bar_tiles_add()
 * @subtitle: (nullable): the quiet second line, or NULL to hide it
 */
void luma_bar_tile_set_subtitle(GtkWidget *tile, const char *subtitle);
/**
 * luma_bar_tile_set_well:
 * @tile: the button returned by luma_bar_tiles_add()
 * @well: show its glyph in the shared places disc
 */
void luma_bar_tile_set_well(GtkWidget *tile, gboolean well);

/**
 * luma_panel_choices_new:
 * @action_name: a #GAction taking a string, activated with the chosen key
 *
 * Python `PanelChoices`: a row of chips, one chosen (Filer's Sort by, the search
 * scope's This folder / Everywhere). Picking leaves the panel open.
 *
 * Returns: (transfer floating): an empty row of choices
 */
GtkWidget *luma_panel_choices_new(const char *action_name);
void luma_panel_choices_add(GtkWidget *choices, const char *key, const char *label, const char *icon);
void luma_panel_choices_set_selected(GtkWidget *choices, const char *key);
/** luma_panel_choices_set_document_style: Equal-width paragraph style previews,44px high. */
void luma_panel_choices_set_document_style(GtkWidget *choices, gboolean document_style);
/**
 * luma_panel_choices_set_decoration:
 * @choices: a row of choices
 * @key: a choice
 * @icon: (nullable): a glyph after its words (Filer's sort order arrow), %NULL for none
 */
void luma_panel_choices_set_decoration(GtkWidget *choices, const char *key, const char *icon);

/**
 * luma_action_center_set_foot:
 * @self: an action center
 * @foot: (nullable): the two-row bar's always-there field (Tasks', Calendar's add
 *   field), under a hairline below the row; %NULL removes it
 */
void luma_action_center_set_foot(LumaActionCenter *self, GtkWidget *foot);

/**
 * luma_action_center_sheet:
 * @self: an action center
 * @content: what the app asks for (Ari's Add a provider)
 * @title: (nullable): the sheet's title
 *
 * Python `ActionCenter.sheet()`: on a phone the sheet rises from the bar's place
 * (16 gutter, 34 up, 26 corners, the full height less 120, scrolling); in a
 * window a centred card. Close it with luma_modal_handle_close().
 *
 * Returns: (transfer none) (nullable): the handle
 */
LumaModalHandle *luma_action_center_sheet(LumaActionCenter *self, GtkWidget *content, const char *title);

/**
 * LumaSharePanel:
 *
 * Python `bar_share.SharePanel`: "Send to", up to five faces with first names,
 * then tiles Messages, Email, Copy link, Nearby. A choice folds the bar and
 * emits #LumaSharePanel::chosen (const char *choice, const char *person): choice
 * is "send-to" (person is the name), "messages", "mail", "copy-link" or "nearby".
 */
#define LUMA_TYPE_SHARE_PANEL (luma_share_panel_get_type())
G_DECLARE_FINAL_TYPE(LumaSharePanel, luma_share_panel, LUMA, SHARE_PANEL, GtkBox)
/**
 * luma_share_panel_new:
 * @heading: (nullable): %NULL is "Send to"
 * @people: (array zero-terminated=1) (nullable): names, the first five shown
 *
 * Returns: (transfer floating): a share panel for luma_action_center_grow_panel()
 */
GtkWidget *luma_share_panel_new(const char *heading, const char *const *people);

/**
 * luma_action_center_hold:
 * @self: an action center
 * @caption: what is happening ("Moving")
 * @label: what is held ("Budget.xlsx")
 * @icon: (nullable): its face
 * @prompt: (nullable): the row under it before a destination is picked; %NULL
 *   is "Where are we moving this?"
 *
 * Python `ActionCenter.hold(label, icon, on_release)`: holding something
 * across views is a second row of the bar, with ✕ to let go
 * (#LumaActionCenter::released). The bar grows to the panel width.
 */
void luma_action_center_hold(LumaActionCenter *self, const char *caption, const char *label, GIcon *icon,
                             const char *prompt);
/**
 * luma_action_center_set_hold_target:
 * @self: a holding action center
 * @heading: (nullable): over the destination ("Move to")
 * @where: (nullable): the destination; %NULL shows the prompt again
 * @icon: (nullable): the destination's Lucide glyph ("folder")
 * @action: (nullable): the primary that completes it ("Move here")
 */
void luma_action_center_set_hold_target(LumaActionCenter *self, const char *heading, const char *where,
                                        const char *icon, LumaBarItem *action);
void luma_action_center_release(LumaActionCenter *self);
gboolean luma_action_center_get_holding(LumaActionCenter *self);

/**
 * luma_action_center_search:
 * @self: an action center whose bar has a search item
 *
 * Turn the bar into its search field in place (a collapsed search's press,
 * or "Search" from ⋯), the field the bottom-most thing with ✕ at its end.
 */
void luma_action_center_search(LumaActionCenter *self);

/**
 * luma_action_center_attach_scroller:
 * @self: an action center
 * @scroller: a scroller behind the bar
 *
 * The one safe area: @scroller's content gets room at its end equal to the
 * distance from its bottom to the top of the bar's row (a grown panel floats
 * over the page and does not count), plus 20, re-measured whenever either
 * moves. Only a showing bar counts.
 */
void luma_action_center_attach_scroller(LumaActionCenter *self, GtkScrolledWindow *scroller);

/** luma_action_center_set_toolbar: Use document toolbar spacing, preserving explicit rule items. */
void luma_action_center_set_toolbar(LumaActionCenter *self, gboolean toolbar);

/**
 * luma_bar_item_set_dropdown:
 * @self: an action item
 * @dropdown: show retained words followed by a disclosure chevron
 * @compact: use compact collection-picker spacing and main ink
 *
 * Set before adding the item to a bar. A dropdown retains its natural width.
 */
void luma_bar_item_set_dropdown(LumaBarItem *self, gboolean dropdown, gboolean compact);

/** Set dropdown geometry before hosting: regular, compact (collection) or view. */
void luma_bar_item_set_dropdown_size(LumaBarItem *self, const char *size);

/** Set after growing: FALSE lets a form supply its own inset. New panels reset to TRUE. */
void luma_action_center_set_panel_padding(LumaActionCenter *self, gboolean padded);

/** Set regular48px or small36px phone row controls. A new show_bar restores regular. */
void luma_action_center_set_phone_control_size(LumaActionCenter *self, const char *size);

/**
 * luma_action_center_set_head:
 * @self: an action center
 * @head: (nullable): the app row above the bar controls, or NULL to clear
 */
void luma_action_center_set_head(LumaActionCenter *self, GtkWidget *head);

G_END_DECLS
