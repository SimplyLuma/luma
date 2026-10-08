/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_COLOR_WELL (luma_color_well_get_type())
G_DECLARE_FINAL_TYPE(LumaColorWell, luma_color_well, LUMA, COLOR_WELL, GtkBox)

GtkWidget *luma_color_well_new(void);
void luma_color_well_set_rgba(LumaColorWell *self, const GdkRGBA *rgba);
const GdkRGBA *luma_color_well_get_rgba(LumaColorWell *self);
void luma_color_well_set_text(LumaColorWell *self, const char *text);
const char *luma_color_well_get_text(LumaColorWell *self);

G_END_DECLS
