/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaAudioMeter:
 *
 * A mono or stereo level meter, −60 dB to +12 dB: a recessed track per
 * channel filled in three zones (quiet, loud from −18 dB, hot from −6 dB),
 * with a peak tick that holds 1.5 s. Rises show at once and falls follow
 * meter ballistics (24 dB/s); it animates only while a bar or tick is
 * falling, so a still meter costs nothing. A level at or above 0 dB latches
 * the `clipping` style until luma_audio_meter_reset_clip(). The accessible
 * value and description change in 6 dB steps, not every frame.
 *
 * Bars run bottom to top by default; luma_audio_meter_set_horizontal()
 * runs them left to right, one under the other (a mixer row).
 *
 * Tree: box.lumaui-audio-meter(.horizontal)(.clipping) with style nodes
 * .lumaui-meter-track, -low, -mid, -high, -peak.
 */
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
void luma_audio_meter_set_horizontal(LumaAudioMeter *self, gboolean horizontal);
gboolean luma_audio_meter_get_horizontal(LumaAudioMeter *self);
/**
 * luma_audio_meter_get_level_db:
 * @self: a meter
 * @channel: 0 (left or mono) or 1 (right)
 *
 * Returns: the level the bar shows now, after ballistics
 */
double luma_audio_meter_get_level_db(LumaAudioMeter *self, guint channel);
/**
 * luma_audio_meter_get_peak_db:
 * @self: a meter
 * @channel: 0 or 1
 *
 * Returns: where the peak tick is now
 */
double luma_audio_meter_get_peak_db(LumaAudioMeter *self, guint channel);

G_END_DECLS
