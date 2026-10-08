/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_file.py: FileCard, OpenButton, OpenInMenu, split_name
 * (Python). */
#pragma once

#include <gtk/gtk.h>
#include "luma-action-center.h"

G_BEGIN_DECLS

/**
 * luma_file_split_name:
 * @name: a file name
 * @head: (out) (transfer full): what may be cut
 * @tail: (out) (transfer full): what always shows: the extension and a few
 *   letters before it
 */
void luma_file_split_name(const char *name, char **head, char **tail);

/**
 * LumaOpenButton:
 *
 * Open, wearing the default app's icon ("Open in Stage"). A press opens the
 * file in its default app, or activates the button's #GtkActionable action
 * when it has one.
 */
#define LUMA_TYPE_OPEN_BUTTON (luma_open_button_get_type())
G_DECLARE_FINAL_TYPE(LumaOpenButton, luma_open_button, LUMA, OPEN_BUTTON, GtkButton)

/**
 * luma_open_button_new:
 * @file: (nullable): the file
 * @content_type: (nullable): its type, when there is no file yet
 * @small: the compact variant
 *
 * Returns: (transfer floating): a new button
 */
GtkWidget *luma_open_button_new(GFile *file, const char *content_type, gboolean small);
void luma_open_button_set_label(LumaOpenButton *self, const char *label);

/**
 * LumaOpenInMenu:
 *
 * The apps that can open a file: Luma's own first (the default first among
 * them), then the rest by name, the default marked, "Other app…" last when
 * there is a file. A card on a computer, a drawer on a phone. By default
 * choosing an app opens the file in it; connect to
 * #LumaOpenInMenu::app-chosen (GAppInfo *app, returns gboolean handled) to do
 * it yourself.
 */
#define LUMA_TYPE_OPEN_IN_MENU (luma_open_in_menu_get_type())
G_DECLARE_FINAL_TYPE(LumaOpenInMenu, luma_open_in_menu, LUMA, OPEN_IN_MENU, GObject)

/**
 * luma_open_in_menu_new:
 * @file: (nullable): the file
 * @content_type: (nullable): its type, when there is no file
 *
 * Returns: (transfer full): a new menu
 */
LumaOpenInMenu *luma_open_in_menu_new(GFile *file, const char *content_type);
/**
 * luma_open_in_menu_popup:
 * @self: a menu
 * @anchor: the control it opens from
 */
void luma_open_in_menu_popup(LumaOpenInMenu *self, GtkWidget *anchor);
void luma_open_in_menu_close(LumaOpenInMenu *self);

/**
 * LumaFileCard:
 *
 * One file, one look: its face (thumbnail or type icon), its name cut in the
 * middle so the extension always shows, what kind and how big, and Open (or
 * the app's actions). Compact for a message or a list. Emits
 * #LumaFileCard::open.
 */
#define LUMA_TYPE_FILE_CARD (luma_file_card_get_type())
G_DECLARE_FINAL_TYPE(LumaFileCard, luma_file_card, LUMA, FILE_CARD, GtkBox)

/**
 * luma_file_card_new:
 * @file: the file
 * @compact: the compact variant
 *
 * Returns: (transfer floating): a new card; its name, kind and size come
 *   from @file unless set
 */
GtkWidget *luma_file_card_new(GFile *file, gboolean compact);
void luma_file_card_set_name(LumaFileCard *self, const char *name);
/**
 * luma_file_card_set_size:
 * @self: a card
 * @size: its size in bytes, or -1 to say nothing
 */
void luma_file_card_set_size(LumaFileCard *self, gint64 size);
/**
 * luma_file_card_set_kind:
 * @self: a card
 * @kind: (nullable): what it is ("Stage presentation"); %NULL asks the
 *   type database
 */
void luma_file_card_set_kind(LumaFileCard *self, const char *kind);
void luma_file_card_set_content_type(LumaFileCard *self, const char *content_type);
/**
 * luma_file_card_set_subtitle:
 * @self: a card
 * @subtitle: (nullable): a line in place of kind · size
 */
void luma_file_card_set_subtitle(LumaFileCard *self, const char *subtitle);
/**
 * luma_file_card_set_tone:
 * @self: a card
 * @tone: (nullable): "danger" or "warning": the second line's state
 *   (a failed or stalled transfer); anything else is refused with a critical
 */
void luma_file_card_set_tone(LumaFileCard *self, const char *tone);
/**
 * luma_file_card_set_thumbnail:
 * @self: a card
 * @thumbnail: (nullable): its picture
 */
void luma_file_card_set_thumbnail(LumaFileCard *self, GdkPaintable *thumbnail);
/**
 * luma_file_card_set_extra:
 * @self: a card
 * @extra: (nullable): a widget under the name (progress)
 */
void luma_file_card_set_extra(LumaFileCard *self, GtkWidget *extra);
/**
 * luma_file_card_add_action:
 * @self: a card
 * @action: a %LUMA_BAR_ITEM_ACTION in place of Open
 */
void luma_file_card_add_action(LumaFileCard *self, LumaBarItem *action);
void luma_file_card_set_selected(LumaFileCard *self, gboolean selected);
/**
 * luma_file_card_get_file:
 * @self: a card
 *
 * Returns: (transfer none): the file
 */
GFile *luma_file_card_get_file(LumaFileCard *self);

G_END_DECLS
