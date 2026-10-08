/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_details.py: DetailsPane, DetailsRow, DetailsItem, AddRow,
 * FactRow, DetailsPhotos (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaDetailsRow:
 *
 * A person or thing in a details list: lead, name, one quiet line, quiet
 * actions. The whole row highlights; its actions appear on hover or focus
 * (always on a phone). Emits #LumaDetailsRow::activated when the row itself
 * is activated.
 */
#define LUMA_TYPE_DETAILS_ROW (luma_details_row_get_type())
G_DECLARE_FINAL_TYPE(LumaDetailsRow, luma_details_row, LUMA, DETAILS_ROW, GtkBox)

/**
 * luma_details_row_new:
 * @title: the name
 * @subtitle: (nullable): one quiet line
 * @lead: (nullable): the lead (a #LumaPersonAvatar)
 *
 * Returns: (transfer floating): a new row
 */
GtkWidget *luma_details_row_new(const char *title, const char *subtitle, GtkWidget *lead);
/**
 * luma_details_row_add_action:
 * @self: a row
 * @icon: a Lucide glyph
 * @label: its name (tooltip and accessible label)
 * @action_name: the detailed #GAction name it activates
 */
void luma_details_row_add_action(LumaDetailsRow *self, const char *icon, const char *label,
                                 const char *action_name);

/**
 * LumaDetailsItem:
 *
 * A thing in a details list (an event, a thread): its mark, a title, one
 * quiet line, a trail. A #GtkButton: use #GtkActionable or
 * #GtkButton::clicked.
 */
#define LUMA_TYPE_DETAILS_ITEM (luma_details_item_get_type())
G_DECLARE_FINAL_TYPE(LumaDetailsItem, luma_details_item, LUMA, DETAILS_ITEM, GtkButton)

/**
 * luma_details_item_new:
 * @title: the title
 * @subtitle: (nullable): one quiet line
 * @icon: (nullable): a Lucide glyph as its mark
 *
 * Returns: (transfer floating): a new item
 */
GtkWidget *luma_details_item_new(const char *title, const char *subtitle, const char *icon);
/**
 * luma_details_item_set_lead:
 * @self: an item
 * @lead: (nullable): a lead widget in place of the icon
 */
void luma_details_item_set_lead(LumaDetailsItem *self, GtkWidget *lead);
/**
 * luma_details_item_set_trail:
 * @self: an item
 * @trail: (nullable): what sits at its end (a time, a count badge)
 */
void luma_details_item_set_trail(LumaDetailsItem *self, GtkWidget *trail);
void luma_details_item_set_selected(LumaDetailsItem *self, gboolean selected);

/**
 * LumaAddRow:
 *
 * "Add people", "Add source": a list row, not a framed button. The + sits in
 * the avatar column; an optional shortcut shows as keycaps (never on a phone).
 */
#define LUMA_TYPE_ADD_ROW (luma_add_row_get_type())
G_DECLARE_FINAL_TYPE(LumaAddRow, luma_add_row, LUMA, ADD_ROW, GtkButton)

/**
 * luma_add_row_new:
 * @label: what it adds ("Add people")
 * @icon: (nullable): a Lucide glyph; %NULL is "user-plus"
 * @shortcut: (nullable): an accelerator ("<Control>n")
 *
 * Returns: (transfer floating): a new row
 */
GtkWidget *luma_add_row_new(const char *label, const char *icon, const char *shortcut);
/* A document add action has a plain glyph and content-sized inline geometry. */
void luma_add_row_set_document(LumaAddRow *self, gboolean document);

/**
 * LumaFactRow:
 *
 * A labelled fact: its icon, a small label over the value, and Copy on hover
 * (always in a phone drawer). Copy puts the value on the clipboard and a
 * "Copied" toast confirms it.
 */
#define LUMA_TYPE_FACT_ROW (luma_fact_row_get_type())
G_DECLARE_FINAL_TYPE(LumaFactRow, luma_fact_row, LUMA, FACT_ROW, GtkBox)

/**
 * luma_fact_row_new:
 * @icon: a Lucide glyph ("phone")
 * @label: the small label ("mobile")
 * @value: the value
 * @copy: whether it offers Copy
 *
 * Returns: (transfer floating): a new row
 */
GtkWidget *luma_fact_row_new(const char *icon, const char *label, const char *value, gboolean copy);
/**
 * luma_fact_row_copy:
 * @self: a row
 *
 * Put the value on the clipboard and say so.
 */
void luma_fact_row_copy(LumaFactRow *self);

/**
 * LumaDetailsPhotos:
 *
 * Photos as square thumbnails, three a row. Emits
 * #LumaDetailsPhotos::photo-activated (guint index).
 */
#define LUMA_TYPE_DETAILS_PHOTOS (luma_details_photos_get_type())
G_DECLARE_FINAL_TYPE(LumaDetailsPhotos, luma_details_photos, LUMA, DETAILS_PHOTOS, GtkGrid)

/**
 * luma_details_photos_new:
 * @items: (nullable): a list of #GdkPaintable or #GFile
 *
 * Returns: (transfer floating): a new grid
 */
GtkWidget *luma_details_photos_new(GListModel *items);
/**
 * luma_details_photos_set_items:
 * @self: a grid
 * @items: (nullable): a list of #GdkPaintable or #GFile
 */
void luma_details_photos_set_items(LumaDetailsPhotos *self, GListModel *items);

/**
 * LumaDetailsPane:
 *
 * The details pane: put it beside the island. It opens and closes itself
 * (luma_details_pane_show()), becomes a bottom drawer at phone width, and
 * keeps the person's Info wish across subjects. A main pane stays open.
 * #LumaDetailsPane:shown notifies on every change.
 */
#define LUMA_TYPE_DETAILS_PANE (luma_details_pane_get_type())
G_DECLARE_FINAL_TYPE(LumaDetailsPane, luma_details_pane, LUMA, DETAILS_PANE, GtkBox)

/**
 * luma_details_pane_new:
 * @title: (nullable): the header's title; %NULL is "Details"
 * @main: whether it is the view's main pane (no close button, stays open)
 *
 * Returns: (transfer floating): a new, closed pane
 */
GtkWidget *luma_details_pane_new(const char *title, gboolean main);
void luma_details_pane_set_title(LumaDetailsPane *self, const char *title);
/**
 * luma_details_pane_set_leading:
 * @self: a pane
 * @leading: (nullable) (transfer floating): a small icon or widget before the header title
 *
 * Replaces the previous leading widget. Pass %NULL to remove it. The pane
 * takes ownership of @leading; it must not already have a parent.
 */
void luma_details_pane_set_leading(LumaDetailsPane *self, GtkWidget *leading);
/**
 * luma_details_pane_set_title_content:
 * @self: a pane
 * @content: (nullable) (transfer floating): an editable title or custom header widget
 *
 * Replaces the visible title label while set, between the optional leading
 * widget and the close button. Pass %NULL to restore the pane's title label.
 * The pane takes ownership of @content; it must not already have a parent.
 */
void luma_details_pane_set_title_content(LumaDetailsPane *self, GtkWidget *content);
/**
 * luma_details_pane_add:
 * @self: a pane
 * @widget: any part (a #LumaFileCard, #LumaStackedButtons)
 *
 * Append @widget to the body, in order.
 */
void luma_details_pane_add(LumaDetailsPane *self, GtkWidget *widget);

/**
 * luma_details_pane_set_footer:
 * @self: a details pane
 * @widget: (nullable): an unparented fixed action widget, or %NULL to clear
 *
 * Keep an action below the scrolling body in both side panes and drawers.
 * luma_details_pane_clear() removes this footer together with the body.
 */
void luma_details_pane_set_footer(LumaDetailsPane *self, GtkWidget *widget);
/**
 * luma_details_pane_add_hero:
 * @self: a pane
 * @title: the subject's name
 * @subtitle: (nullable): one caption
 * @lead: (nullable): its picture
 */
void luma_details_pane_add_hero(LumaDetailsPane *self, const char *title, const char *subtitle,
                                GtkWidget *lead);
/**
 * luma_details_pane_add_subject:
 * @self: a pane
 * @title: the subject's name
 * @subtitle: (nullable): one caption
 * @lead: (nullable) (transfer floating): a compact picture or icon
 *
 * Append a compact horizontal subject summary for an embedded action panel.
 * The caller supplies the lead at the appropriate compact size.
 */
void luma_details_pane_add_subject(LumaDetailsPane *self, const char *title,
                                   const char *subtitle, GtkWidget *lead);
/**
 * luma_details_pane_set_header_visible:
 * @self: a pane
 * @visible: whether to show the pane's title/back/close header
 *
 * A panel whose subject names it can omit the redundant pane heading.
 * The containing action bar remains responsible for dismissal.
 */
void luma_details_pane_set_header_visible(LumaDetailsPane *self, gboolean visible);

/**
 * luma_details_pane_add_section:
 * @self: a pane
 * @label: the section label
 * @action_label: (nullable): a quiet button at its end ("View all")
 * @action_name: (nullable): the detailed #GAction name that button activates
 */
void luma_details_pane_add_section(LumaDetailsPane *self, const char *label,
                                   const char *action_label, const char *action_name);
/**
 * luma_details_pane_add_fact:
 * @self: a pane
 * @key: the fact's name ("Size")
 * @value: its value, selectable, wrapping
 *
 * A key and value fact. Facts added one after another share one block, a
 * hairline between each.
 */
void luma_details_pane_add_fact(LumaDetailsPane *self, const char *key, const char *value);
/**
 * luma_details_pane_add_row:
 * @self: a pane
 * @row: a #LumaDetailsRow, #LumaDetailsItem, #LumaFactRow or #LumaAddRow
 *
 * Rows added one after another share one list: no dividers, full-width hover.
 */
void luma_details_pane_add_row(LumaDetailsPane *self, GtkWidget *row);
/**
 * luma_details_pane_add_photos:
 * @self: a pane
 * @items: a list of #GdkPaintable or #GFile
 *
 * Returns: (transfer none): the photos grid
 */
LumaDetailsPhotos *luma_details_pane_add_photos(LumaDetailsPane *self, GListModel *items);
/**
 * luma_details_pane_clear:
 * @self: a pane
 *
 * Empty the body, to fill it again for a new subject.
 */
void luma_details_pane_clear(LumaDetailsPane *self);
/**
 * luma_details_pane_show:
 * @self: a pane
 * @open: whether it should show
 *
 * Open or close (the person's wish, kept across subjects).
 *
 * Returns: whether it shows
 */
gboolean luma_details_pane_show(LumaDetailsPane *self, gboolean open);
/**
 * luma_details_pane_set_subject:
 * @self: a pane
 * @subject: (nullable) (transfer none): what it describes now; %NULL
 *   closes the pane until there is a subject again
 */
void luma_details_pane_set_subject(LumaDetailsPane *self, GObject *subject);
/**
 * luma_details_pane_get_subject:
 * @self: a pane
 *
 * Returns: (transfer none) (nullable): the subject
 */
GObject *luma_details_pane_get_subject(LumaDetailsPane *self);
/**
 * luma_details_pane_set_embedded:
 * @self: a pane
 * @embedded: keep the contents in its parent at every width
 *
 * For details hosted by an ActionCenter panel: the parent owns scrolling,
 * the surface and dismissal. Defaults to %FALSE (responsive side pane/drawer).
 */
void luma_details_pane_set_embedded(LumaDetailsPane *self, gboolean embedded);
void luma_details_pane_set_main(LumaDetailsPane *self, gboolean main);
gboolean luma_details_pane_get_main(LumaDetailsPane *self);
/**
 * luma_details_pane_set_closable:
 * @self: the pane
 * @closable: whether it shows its close button
 *
 * A side pane closes; a main pane only when made closable (a day in Calendar).
 */
void luma_details_pane_set_closable(LumaDetailsPane *self, gboolean closable);
/**
 * luma_details_pane_set_back:
 * @self: the pane
 * @label: (nullable): where Back goes ("Tuesday"), or %NULL to remove it
 * @on_back: (scope forever) (nullable): called with @user_data when Back is pressed
 * @user_data: (closure): data for @on_back
 *
 * A back button before the title, for a pane that went one level in.
 */
void luma_details_pane_set_back(LumaDetailsPane *self, const char *label, GCallback on_back, gpointer user_data);
/**
 * luma_details_pane_close:
 * @self: a pane
 *
 * Close, as the close button and Esc do. A main pane stays.
 */
void luma_details_pane_close(LumaDetailsPane *self);
gboolean luma_details_pane_toggle(LumaDetailsPane *self);
gboolean luma_details_pane_get_shown(LumaDetailsPane *self);
gboolean luma_details_pane_get_is_drawer(LumaDetailsPane *self);
/**
 * luma_details_pane_info_button:
 * @self: a pane
 *
 * The Information control for this pane, pressed while it shows. The
 * #LumaCornerPill uses it.
 *
 * Returns: (transfer none): the toggle button
 */
GtkWidget *luma_details_pane_info_button(LumaDetailsPane *self);

G_END_DECLS
