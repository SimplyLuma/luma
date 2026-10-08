/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_dialog.DestructiveDialog (Python). */
#pragma once

#include <gtk/gtk.h>
#include "luma-layer-host.h"
#include "luma-action-center.h"

G_BEGIN_DECLS

/**
 * LumaDestructiveDialog:
 *
 * The question before something can't come back: a red icon, the title as a
 * question naming the thing ("Uninstall Kiln?"), one body line, Cancel and the
 * red action named for what it does. A drawer at phone width. Never "Are you
 * sure?" and never "OK": both are refused.
 *
 * Signals: #LumaDestructiveDialog::confirmed (gboolean option_checked),
 * #LumaDestructiveDialog::cancelled.
 */
#define LUMA_TYPE_DESTRUCTIVE_DIALOG (luma_destructive_dialog_get_type())
G_DECLARE_FINAL_TYPE(LumaDestructiveDialog, luma_destructive_dialog, LUMA, DESTRUCTIVE_DIALOG, GtkBox)

/**
 * luma_destructive_dialog_new:
 * @title: the question ("Delete “Budget.xlsx”?")
 * @body: one line on what happens
 * @action: (nullable): the red action's word; %NULL is "Delete"
 * @icon: (nullable): a Lucide glyph; %NULL is "trash-2"
 * @option: (nullable): a check under the body ("Also delete its files")
 *
 * Returns: (transfer floating): the card; show it with
 *   luma_destructive_dialog_present()
 */
GtkWidget *luma_destructive_dialog_new(const char *title, const char *body, const char *action,
                                       const char *icon, const char *option);
/**
 * luma_destructive_dialog_present:
 * @self: a dialog
 * @where: a widget in the window to ask over
 *
 * Returns: (transfer none): the modal handle
 */
LumaModalHandle *luma_destructive_dialog_present(LumaDestructiveDialog *self, GtkWidget *where);
/**
 * luma_destructive_dialog_ask:
 * @where: a widget in the window to ask over
 * @title: the question
 * @body: one line on what happens
 * @action: (nullable): the red action's word
 * @icon: (nullable): a Lucide glyph
 * @option: (nullable): a check under the body
 *
 * Make and present one. Connect to the signals on the result.
 *
 * Returns: (transfer none): the dialog, valid until it closes
 */
LumaDestructiveDialog *luma_destructive_dialog_ask(GtkWidget *where, const char *title, const char *body,
                                                   const char *action, const char *icon,
                                                   const char *option);
/**
 * luma_destructive_dialog_in_bar:
 * @center: the action center showing the bar the action was taken from
 * @title: the question
 * @body: one line on what happens
 * @action: (nullable): the red action's word; %NULL is "Delete"
 * @icon: (nullable): unused in the bar (kept for parity with luma_destructive_dialog_ask())
 * @option: (nullable): a check under the body
 *
 * v71: a destructive action confirms inside the grown bar (Python
 * `DestructiveDialog.in_bar(center, …)`): the title, one line, then Cancel and
 * the red action side by side. Folding the bar any other way is Cancel.
 *
 * Returns: (transfer none) (nullable): the dialog, valid until it is answered
 */
LumaDestructiveDialog *luma_destructive_dialog_in_bar(LumaActionCenter *center, const char *title,
                                                      const char *body, const char *action, const char *icon,
                                                      const char *option);
gboolean luma_destructive_dialog_get_option_checked(LumaDestructiveDialog *self);
void luma_destructive_dialog_close(LumaDestructiveDialog *self);

G_END_DECLS
