/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_AUDIO_METER (luma_audio_meter_get_type())
G_DECLARE_FINAL_TYPE(LumaAudioMeter, luma_audio_meter, LUMA, AUDIO_METER, GtkBox)

GtkWidget *luma_audio_meter_new(guint channels);
void luma_audio_meter_set_channels(LumaAudioMeter *self, guint channels);
guint luma_audio_meter_get_channels(LumaAudioMeter *self);
void luma_audio_meter_set_levels_db(LumaAudioMeter *self, double left_db,
                                    double right_db);
void luma_audio_meter_set_levels(LumaAudioMeter *self, double left,
                                 double right);
void luma_audio_meter_reset_clip(LumaAudioMeter *self);
void luma_audio_meter_set_clipped(LumaAudioMeter *self, gboolean clipped);
gboolean luma_audio_meter_get_clipped(LumaAudioMeter *self);
double luma_audio_meter_db_to_fraction(double db);

G_END_DECLS
