/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#include <gtk/gtk.h>
#include "luma-widgets-model.h"
G_BEGIN_DECLS
#define LUMA_WIDGETS_TYPE_GTK3 (luma_widgets_gtk3_get_type())
G_DECLARE_FINAL_TYPE (LumaWidgetsGtk3, luma_widgets_gtk3, LUMA_WIDGETS, GTK3, GtkButton)
/**
 * luma_widgets_gtk3_new:
 * @model: shared widget model
 * @id: weather or calendar
 *
 * Returns: (transfer none): a native widget view; emits launch-requested with
 *   the definition's desktop ID when activated
 */
GtkWidget *luma_widgets_gtk3_new (LumaWidgetsModel *model, const char *id);
G_END_DECLS
