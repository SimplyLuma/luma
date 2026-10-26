/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#include <gio/gio.h>
G_BEGIN_DECLS
#define LUMA_WIDGETS_TYPE_MODEL (luma_widgets_model_get_type())
G_DECLARE_FINAL_TYPE (LumaWidgetsModel, luma_widgets_model, LUMA_WIDGETS, MODEL, GObject)
/**
 * luma_widgets_model_new:
 * @calendar_service: native calendar server bus name
 *
 * Returns: (transfer full): the shared Weather and Calendar widget model
 */
LumaWidgetsModel *luma_widgets_model_new (const char *calendar_service);
/**
 * luma_widgets_model_dup_snapshot:
 * @self: shared widget model
 *
 * Returns: (transfer full): JSON containing the widget's display fields
 */
char *luma_widgets_model_dup_snapshot (LumaWidgetsModel *self);
G_END_DECLS
