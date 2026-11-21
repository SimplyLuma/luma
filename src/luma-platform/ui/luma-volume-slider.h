/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

#include "luma-audio-meter.h"

G_BEGIN_DECLS

/**
 * LumaVolumeSlider:
 *
 * A volume: LumaUI's slider (a 4 px recessed track filled to the value and a
 * raised 14 px knob) standing upright for a mixer's channel strip or lying
 * flat for a row, with an optional LumaAudioMeter laid along the track over
 * exactly the track's length, so the level and the volume read on one scale.
 *
 * The range is any pair of numbers (0–100 %, 0–150 %, −60–+6 dB). A unity
 * value (100 %, 0 dB) inside the range is marked across the track, and a
 * double click returns to it. Muted, the fill and the meter go quiet but the
 * knob stays where the volume is.
 *
 * Arrows along the slider step by the small step (arrows across it are left
 * for focus), Page Up/Down by the page step, Home and End
 * go to the ends; the wheel steps; a press on the track jumps there and drags.
 * Only those moves emit ::moved; luma_volume_slider_set_value() does not.
 *
 * Tree: lumaui-volume-slider(.vertical|.horizontal)(.compact)(.muted) with style nodes
 * .lumaui-volume-track, -fill, -knob, -ring, -shadow, -mark, -focus, and the
 * meter (box.lumaui-audio-meter) as its child.
 */
#define LUMA_TYPE_VOLUME_SLIDER (luma_volume_slider_get_type())
G_DECLARE_FINAL_TYPE(LumaVolumeSlider, luma_volume_slider, LUMA, VOLUME_SLIDER, GtkWidget)

GtkWidget *luma_volume_slider_new(GtkOrientation orientation, double lower, double upper);
void luma_volume_slider_set_value(LumaVolumeSlider *self, double value);
double luma_volume_slider_get_value(LumaVolumeSlider *self);
void luma_volume_slider_set_range(LumaVolumeSlider *self, double lower, double upper);
void luma_volume_slider_get_range(LumaVolumeSlider *self, double *lower, double *upper);
void luma_volume_slider_set_steps(LumaVolumeSlider *self, double step, double page);
/**
 * luma_volume_slider_set_unity:
 * @self: a slider
 * @unity: the value marked on the track and returned to by a double click,
 *   or NAN for none
 */
void luma_volume_slider_set_unity(LumaVolumeSlider *self, double unity);
double luma_volume_slider_get_unity(LumaVolumeSlider *self);
/**
 * luma_volume_slider_set_unit:
 * @self: a slider
 * @unit: (nullable): "%" (the default) or "dB": how the value is spoken
 */
void luma_volume_slider_set_unit(LumaVolumeSlider *self, const char *unit);
/**
 * luma_volume_slider_set_compact:
 * @self: a slider
 * @compact: TRUE for a row in a panel: a 12 px knob in a 16 px slot (the
 *   `--lumaui-volume-slider-compact-*` tokens) on the same 4 px track
 */
void luma_volume_slider_set_compact(LumaVolumeSlider *self, gboolean compact);
gboolean luma_volume_slider_get_compact(LumaVolumeSlider *self);
void luma_volume_slider_set_muted(LumaVolumeSlider *self, gboolean muted);
gboolean luma_volume_slider_get_muted(LumaVolumeSlider *self);
/**
 * luma_volume_slider_set_meter_channels:
 * @self: a slider
 * @channels: 0 (no meter), 1 or 2
 *
 * Lays a LumaAudioMeter along the track: beside it standing, under it lying.
 */
void luma_volume_slider_set_meter_channels(LumaVolumeSlider *self, guint channels);
/**
 * luma_volume_slider_get_meter:
 * @self: a slider
 *
 * Returns: (transfer none) (nullable): the meter along the track, to feed
 *   with luma_audio_meter_set_levels_db()
 */
LumaAudioMeter *luma_volume_slider_get_meter(LumaVolumeSlider *self);
/**
 * luma_volume_slider_get_track_bounds:
 * @self: a slider
 * @bounds: (out caller-allocates): where the track's ends are, in the slider
 *
 * The track as drawn: along the slider from the knob's centre at one end to
 * its centre at the other plus its rounded ends, and 4 px across. The meter
 * lies over exactly this length.
 */
void luma_volume_slider_get_track_bounds(LumaVolumeSlider *self, graphene_rect_t *bounds);

G_END_DECLS
