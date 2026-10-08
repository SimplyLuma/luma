/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_CURVE_EDITOR (luma_curve_editor_get_type())
G_DECLARE_FINAL_TYPE(LumaCurveEditor, luma_curve_editor, LUMA, CURVE_EDITOR,
                     GtkDrawingArea)

GtkWidget *luma_curve_editor_new(void);
void luma_curve_editor_set_points(LumaCurveEditor *self, GVariant *points);
GVariant *luma_curve_editor_get_points(LumaCurveEditor *self);
void luma_curve_editor_reset(LumaCurveEditor *self);

G_END_DECLS

