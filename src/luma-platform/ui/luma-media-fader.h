/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_MEDIA_FADER (luma_media_fader_get_type())
G_DECLARE_FINAL_TYPE(LumaMediaFader, luma_media_fader, LUMA, MEDIA_FADER, GtkScale)

GtkWidget *luma_media_fader_new(GtkOrientation orientation);
void luma_media_fader_set_db(LumaMediaFader *self, double db);
double luma_media_fader_get_db(LumaMediaFader *self);
void luma_media_fader_set_reset_db(LumaMediaFader *self, double db);
double luma_media_fader_get_reset_db(LumaMediaFader *self);
double luma_media_fader_db_to_amplitude(double db);

G_END_DECLS
