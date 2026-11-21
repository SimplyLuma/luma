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
 * #LumaAudioMeter:segments 0 draws each bar as one continuous fill. N > 0
 * draws N equal segments (LED style), --lumaui-meter-segment-gap apart: a
 * segment lights once the level reaches half way into it, each in the zone
 * colour of its middle, unlit ones in the track colour, and the peak hold is
 * the one segment it reaches, lit. A segmented bar redraws only when a
 * segment changes. `LevelMeter` in Python is this part.
 *
 * Tree: box.lumaui-audio-meter(.horizontal)(.segmented)(.clipping) with style
 * nodes .lumaui-meter-track, -low, -mid, -high, -peak.
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
 * luma_audio_meter_set_segments:
 * @self: a meter
 * @segments: 0 for a continuous bar, or 1 to 128 segments per bar
 */
void luma_audio_meter_set_segments(LumaAudioMeter *self, guint segments);
guint luma_audio_meter_get_segments(LumaAudioMeter *self);
/**
 * luma_audio_meter_get_lit_segments:
 * @self: a meter
 * @channel: 0 (left or mono) or 1 (right)
 *
 * Returns: how many segments the level lights now, from the floor; 0 when
 *   the meter is continuous
 */
guint luma_audio_meter_get_lit_segments(LumaAudioMeter *self, guint channel);
/**
 * luma_audio_meter_get_peak_segment:
 * @self: a meter
 * @channel: 0 or 1
 *
 * Returns: the index from the floor of the segment the peak hold lights, or
 *   -1 when no peak shows or the meter is continuous
 */
int luma_audio_meter_get_peak_segment(LumaAudioMeter *self, guint channel);
/**
 * luma_audio_meter_segments_lit_at:
 * @segments: segments per bar
 * @db: a level in dBFS
 *
 * Returns: how many of @segments segments a bar at @db lights
 */
guint luma_audio_meter_segments_lit_at(guint segments, double db);
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
