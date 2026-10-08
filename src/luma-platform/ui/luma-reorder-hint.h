/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gtk/gtk.h>
G_BEGIN_DECLS
void luma_reorder_hint_begin (GtkWidget *list, GtkWidget *source);
void luma_reorder_hint_clear (GtkWidget *list);
void luma_reorder_hint_set_target (GtkWidget *list, GtkWidget *row, gboolean after);
gboolean luma_reorder_hint_is_after (GtkWidget *row, double y);
void luma_reorder_hint_end (GtkWidget *list);
G_END_DECLS
