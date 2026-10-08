/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_HISTOGRAM (luma_histogram_get_type())
G_DECLARE_FINAL_TYPE(LumaHistogram, luma_histogram, LUMA, HISTOGRAM,
                     GtkDrawingArea)

GtkWidget *luma_histogram_new(void);
void luma_histogram_set_channels(LumaHistogram *self, GVariant *channels);
GVariant *luma_histogram_get_channels(LumaHistogram *self);
void luma_histogram_set_clipping_visible(LumaHistogram *self, gboolean visible);
gboolean luma_histogram_get_clipping_visible(LumaHistogram *self);

G_END_DECLS

