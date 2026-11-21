/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

#include "luma-audio-meter.h"
#include "luma-volume-slider.h"

G_BEGIN_DECLS

/**
 * LumaMixerStrip:
 *
 * A mixer's channel strip, a fixed-width column (the
 * `--lumaui-mixer-strip-width` token): the channel's name (ellipsized, the
 * full name as its tooltip) over its device, then a dB scale beside an
 * upright LumaVolumeSlider carrying a stereo LumaAudioMeter (peak hold and
 * ballistics are the meter's), the value readout, and Mute and Monitor keys.
 * An app's or device's icon can head it (luma_mixer_strip_set_gicon()), and
 * either key can be left out (an output without monitoring, a fixed input):
 * the strip is a generic channel, for an app's audio panel or the system's
 * volume mixer alike.
 *
 * The fader runs from the range's low end (spoken and shown as −∞) to its
 * top; 0 dB is marked and a double click returns to it. Up and Down step
 * 1 dB, Page Up and Down 6 dB, Home and End go to the ends, the wheel steps,
 * and M mutes from anywhere in the strip. Only the person's moves emit
 * ::moved, ::mute-toggled and ::monitor-toggled; the setters are silent.
 *
 * In RTL the strip's names and keys mirror; the scale, fader and meter do
 * not, since they are read against one another.
 *
 * Tree: lumaui-mixer-strip(.muted)(.monitoring) > box.lumaui-mixer-content >
 * box.lumaui-mixer-head > (image.lumaui-mixer-icon, label.lumaui-mixer-name,
 * label.lumaui-mixer-device), box.lumaui-mixer-body > (.lumaui-mixer-scale, lumaui-volume-slider),
 * label.lumaui-mixer-readout, box.lumaui-mixer-keys >
 * button.lumaui-mixer-key.mute, button.lumaui-mixer-key.monitor.
 */
#define LUMA_TYPE_MIXER_STRIP (luma_mixer_strip_get_type())
G_DECLARE_FINAL_TYPE(LumaMixerStrip, luma_mixer_strip, LUMA, MIXER_STRIP, GtkWidget)

/**
 * luma_mixer_strip_new:
 * @label: the channel's name
 * @subtitle: (nullable): its device, or NULL
 *
 * Returns: a strip at 0 dB on a −60 to +6 dB fader
 */
GtkWidget *luma_mixer_strip_new(const char *label, const char *subtitle);
void luma_mixer_strip_set_label(LumaMixerStrip *self, const char *label);
const char *luma_mixer_strip_get_label(LumaMixerStrip *self);
/**
 * luma_mixer_strip_set_subtitle:
 * @self: a strip
 * @subtitle: (nullable): the device, or NULL for none
 */
void luma_mixer_strip_set_subtitle(LumaMixerStrip *self, const char *subtitle);
const char *luma_mixer_strip_get_subtitle(LumaMixerStrip *self);
void luma_mixer_strip_set_range(LumaMixerStrip *self, double min_db, double max_db);
/**
 * luma_mixer_strip_get_range:
 * @self: a strip
 * @min_db: (out) (optional): the fader's low end
 * @max_db: (out) (optional): its top
 */
void luma_mixer_strip_get_range(LumaMixerStrip *self, double *min_db, double *max_db);
void luma_mixer_strip_set_db(LumaMixerStrip *self, double db);
double luma_mixer_strip_get_db(LumaMixerStrip *self);
void luma_mixer_strip_set_muted(LumaMixerStrip *self, gboolean muted);
gboolean luma_mixer_strip_get_muted(LumaMixerStrip *self);
void luma_mixer_strip_set_monitoring(LumaMixerStrip *self, gboolean monitoring);
gboolean luma_mixer_strip_get_monitoring(LumaMixerStrip *self);
/**
 * luma_mixer_strip_set_gicon:
 * @self: a strip
 * @icon: (nullable): the channel's icon (an app's, a device's), or NULL for none
 */
void luma_mixer_strip_set_gicon(LumaMixerStrip *self, GIcon *icon);
/**
 * luma_mixer_strip_get_gicon:
 * @self: a strip
 *
 * Returns: (transfer none) (nullable): the channel's icon
 */
GIcon *luma_mixer_strip_get_gicon(LumaMixerStrip *self);
/**
 * luma_mixer_strip_set_icon_name:
 * @self: a strip
 * @icon: (nullable): a Lucide name ("mic") for the kit's glyph, or a theme or application icon name
 */
void luma_mixer_strip_set_icon_name(LumaMixerStrip *self, const char *icon);
/**
 * luma_mixer_strip_set_mute_visible:
 * @self: a strip
 * @visible: whether the strip offers Mute (and M mutes)
 */
void luma_mixer_strip_set_mute_visible(LumaMixerStrip *self, gboolean visible);
gboolean luma_mixer_strip_get_mute_visible(LumaMixerStrip *self);
/**
 * luma_mixer_strip_set_monitor_visible:
 * @self: a strip
 * @visible: whether the strip offers Monitor
 */
void luma_mixer_strip_set_monitor_visible(LumaMixerStrip *self, gboolean visible);
gboolean luma_mixer_strip_get_monitor_visible(LumaMixerStrip *self);
/**
 * luma_mixer_strip_set_levels_db:
 * @self: a strip
 * @left_db: the left (or mono) level in dBFS
 * @right_db: the right level in dBFS
 *
 * Feeds the strip's meter, as luma_audio_meter_set_levels_db().
 */
void luma_mixer_strip_set_levels_db(LumaMixerStrip *self, double left_db, double right_db);
/**
 * luma_mixer_strip_set_levels:
 * @self: a strip
 * @left: the left (or mono) linear amplitude, 1.0 = 0 dBFS
 * @right: the right linear amplitude
 *
 * Feeds the strip's meter, as luma_audio_meter_set_levels().
 */
void luma_mixer_strip_set_levels(LumaMixerStrip *self, double left, double right);
/**
 * luma_mixer_strip_get_fader:
 * @self: a strip
 *
 * Returns: (transfer none): the strip's fader
 */
LumaVolumeSlider *luma_mixer_strip_get_fader(LumaMixerStrip *self);
/**
 * luma_mixer_strip_get_meter:
 * @self: a strip
 *
 * Returns: (transfer none): the meter along the fader
 */
LumaAudioMeter *luma_mixer_strip_get_meter(LumaMixerStrip *self);
/**
 * luma_mixer_strip_get_mute_button:
 * @self: a strip
 *
 * Returns: (transfer none): the Mute key
 */
GtkWidget *luma_mixer_strip_get_mute_button(LumaMixerStrip *self);
/**
 * luma_mixer_strip_get_monitor_button:
 * @self: a strip
 *
 * Returns: (transfer none): the Monitor key
 */
GtkWidget *luma_mixer_strip_get_monitor_button(LumaMixerStrip *self);

/**
 * LumaMixerRow:
 *
 * Mixer strips left to right (right to left in RTL), `--lumaui-mixer-strip-row-gap`
 * apart, in a row that scrolls sideways when they do not all fit. The row is
 * never narrower than one strip and never taller or shorter than its strips
 * ask, so a strip is never clipped and nothing spills past the panel's edge;
 * it fits a DockTile(fit=True). Left and Right move between the strips'
 * faders.
 *
 * Tree: lumaui-mixer-row > scrolledwindow > viewport > box.lumaui-mixer-strips.
 */
#define LUMA_TYPE_MIXER_ROW (luma_mixer_row_get_type())
G_DECLARE_FINAL_TYPE(LumaMixerRow, luma_mixer_row, LUMA, MIXER_ROW, GtkWidget)

GtkWidget *luma_mixer_row_new(void);
void luma_mixer_row_append(LumaMixerRow *self, LumaMixerStrip *strip);
void luma_mixer_row_remove(LumaMixerRow *self, LumaMixerStrip *strip);
guint luma_mixer_row_get_n_strips(LumaMixerRow *self);
/**
 * luma_mixer_row_get_strip:
 * @self: a row
 * @index: a strip's place, from the start
 *
 * Returns: (transfer none) (nullable): that strip, or NULL past the end
 */
LumaMixerStrip *luma_mixer_row_get_strip(LumaMixerRow *self, guint index);

G_END_DECLS
