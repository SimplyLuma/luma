/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gtk/gtk.h>
G_BEGIN_DECLS
#define LUMA_TYPE_APPLICATION_ICON (luma_application_icon_get_type())
G_DECLARE_FINAL_TYPE(LumaApplicationIcon, luma_application_icon, LUMA, APPLICATION_ICON, GObject)
/**
 * luma_application_icon_new:
 * @artwork: the original application artwork
 *
 * Presents artwork on Luma's square gradient tile with a proportional rounded
 * mask. Folder/file thumbnails should retain their own presentation.
 *
 * Returns: (transfer full): the normalized application paintable
 */
GdkPaintable *luma_application_icon_new(GdkPaintable *artwork);
G_END_DECLS
