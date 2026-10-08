/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_type.TypeLabel (Python); apply_type is luma_ui_apply_type(). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaTypeLabel:
 *
 * A label in one type role, with an optional small unit after it ("72 °").
 */
#define LUMA_TYPE_TYPE_LABEL (luma_type_label_get_type())
G_DECLARE_FINAL_TYPE(LumaTypeLabel, luma_type_label, LUMA, TYPE_LABEL, GtkBox)

/**
 * luma_type_label_new:
 * @text: (nullable): the text
 * @role: (nullable): a type role (see luma_ui_apply_type()); %NULL is "body"
 *
 * Returns: (transfer floating): a new label
 */
GtkWidget *luma_type_label_new(const char *text, const char *role);
void luma_type_label_set_text(LumaTypeLabel *self, const char *text);
const char *luma_type_label_get_text(LumaTypeLabel *self);
void luma_type_label_set_role(LumaTypeLabel *self, const char *role);
const char *luma_type_label_get_role(LumaTypeLabel *self);
/**
 * luma_type_label_set_unit:
 * @self: a label
 * @unit: (nullable): the small unit after the text
 */
void luma_type_label_set_unit(LumaTypeLabel *self, const char *unit);
void luma_type_label_set_wrap(LumaTypeLabel *self, gboolean wrap);

G_END_DECLS
