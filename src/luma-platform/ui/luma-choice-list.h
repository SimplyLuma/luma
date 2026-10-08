/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gtk/gtk.h>
G_BEGIN_DECLS
#define LUMA_TYPE_CHOICE_LIST (luma_choice_list_get_type())
G_DECLARE_FINAL_TYPE(LumaChoiceList, luma_choice_list, LUMA, CHOICE_LIST, GtkBox)
/** A described single-selection list. "changed" emits the chosen string key. */
GtkWidget *luma_choice_list_new(gboolean panel);
void luma_choice_list_append(LumaChoiceList *self, const char *key, const char *title, const char *detail);
/** Programmatic selection does not emit "changed". */
void luma_choice_list_set_selected(LumaChoiceList *self, const char *key);
const char *luma_choice_list_get_selected(LumaChoiceList *self);
G_END_DECLS
