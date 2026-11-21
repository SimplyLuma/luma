/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

#include "luma-audio-meter.h"
#include "luma-volume-slider.h"

G_BEGIN_DECLS

/**
 * LumaMediaRowDensity:
 * @LUMA_MEDIA_ROW_REGULAR: a card's block: 44 px artwork, the transport at
 *   the top right, then a volume line (glyph, slider, readout), a trailing
 *   picker and a level meter under the slider
 * @LUMA_MEDIA_ROW_COMPACT: a row in a panel's well: 40 px artwork, the slider
 *   under the words, one round play key
 * @LUMA_MEDIA_ROW_TOUCH: the compact row on a phone or tablet: a 64 px box
 *   (44 px artwork, 10 px above and below) with a 44 px play key
 */
typedef enum {
  LUMA_MEDIA_ROW_REGULAR,
  LUMA_MEDIA_ROW_COMPACT,
  LUMA_MEDIA_ROW_TOUCH,
} LumaMediaRowDensity;

/**
 * LumaMediaRowTransport:
 * @LUMA_MEDIA_ROW_PLAY_PAUSE: the player can play or pause
 * @LUMA_MEDIA_ROW_PREVIOUS: it can go to the previous track
 * @LUMA_MEDIA_ROW_NEXT: it can go to the next track
 *
 * What the player says it can do. A row with none has no transport.
 */
typedef enum {
  LUMA_MEDIA_ROW_NO_TRANSPORT = 0,
  LUMA_MEDIA_ROW_PLAY_PAUSE = 1 << 0,
  LUMA_MEDIA_ROW_PREVIOUS = 1 << 1,
  LUMA_MEDIA_ROW_NEXT = 1 << 2,
} LumaMediaRowTransport;

/**
 * LumaMediaRow:
 *
 * One playing app as its content: the artwork badged with the app's icon, a
 * title and a line, the transport its player offers and its volume on
 * LumaUI's slider. Without artwork the app's icon sits on a neutral tile.
 * The same row is a block in a card (regular), a row in a panel (compact)
 * and that row under a finger (touch); the Shell's St row is drawn from the
 * same tokens (`--lumaui-media-row-*`).
 *
 * A press on the artwork emits ::raise; a second press on it, or a press on
 * the badge, emits ::mute-toggled. Space (and OK on a remote) plays or
 * pauses, M mutes, and Left and Right on the slider move it by 5 %. Only the person's actions emit
 * signals; the setters are silent. The row never talks to a player or a
 * mixer itself.
 *
 * Narrower than `--lumaui-media-row-regular-stack-below` a regular row
 * stacks: the trailing picker drops under the slider, full width.
 *
 * Tree: lumaui-media-row(.regular|.compact|.touch)(.paused)(.muted)(.stacked) >
 * box.lumaui-media-row-art(.tile) > (picture | image), box.lumaui-media-row-badge > image,
 * label.lumaui-media-row-title, box.lumaui-media-row-line > (lumaui-media-row-level, label),
 * box.lumaui-media-row-keys > button.lumaui-media-row-key(.play),
 * image.lumaui-media-row-volume, lumaui-volume-slider, label.lumaui-media-row-readout,
 * box.lumaui-audio-meter.segmented, and the trailing widget.
 */
#define LUMA_TYPE_MEDIA_ROW (luma_media_row_get_type())
G_DECLARE_FINAL_TYPE(LumaMediaRow, luma_media_row, LUMA, MEDIA_ROW, GtkWidget)

GtkWidget *luma_media_row_new(void);
/**
 * luma_media_row_focus_sibling:
 * @self: a row
 * @direction: GTK_DIR_UP or GTK_DIR_DOWN
 *
 * Moves focus to the slider of the row before or after this one in its
 * list (rows are siblings), so Up and Down step row by row whatever sits
 * beside the sliders.
 *
 * Returns: TRUE when there was such a row
 */
gboolean luma_media_row_focus_sibling(LumaMediaRow *self, GtkDirectionType direction);
void luma_media_row_set_density(LumaMediaRow *self, LumaMediaRowDensity density);
LumaMediaRowDensity luma_media_row_get_density(LumaMediaRow *self);
/**
 * luma_media_row_set_title:
 * @self: a row
 * @title: what is playing, or the app's name when it does not say
 */
void luma_media_row_set_title(LumaMediaRow *self, const char *title);
const char *luma_media_row_get_title(LumaMediaRow *self);
/**
 * luma_media_row_set_line:
 * @self: a row
 * @line: (nullable): the second line ("Tide · The Beach Boys", "Viola · Paused")
 */
void luma_media_row_set_line(LumaMediaRow *self, const char *line);
const char *luma_media_row_get_line(LumaMediaRow *self);
/**
 * luma_media_row_set_app:
 * @self: a row
 * @name: (nullable): the app's name, for the row's spoken label
 * @icon: (nullable): the app's icon: the badge, and the tile without artwork
 */
void luma_media_row_set_app(LumaMediaRow *self, const char *name, GIcon *icon);
/**
 * luma_media_row_set_art:
 * @self: a row
 * @art: (nullable): the artwork, or NULL for the app's icon on a tile
 */
void luma_media_row_set_art(LumaMediaRow *self, GdkPaintable *art);
/**
 * luma_media_row_set_art_file:
 * @self: a row
 * @file: (nullable): an image file to load as the artwork, off the main
 *   loop; NULL, or a file that is not a picture, leaves the app's tile
 */
void luma_media_row_set_art_file(LumaMediaRow *self, GFile *file);
gboolean luma_media_row_get_has_art(LumaMediaRow *self);
/**
 * luma_media_row_set_transport:
 * @self: a row
 * @transport: what the player can do; a compact row shows only play/pause
 */
void luma_media_row_set_transport(LumaMediaRow *self, LumaMediaRowTransport transport);
LumaMediaRowTransport luma_media_row_get_transport(LumaMediaRow *self);
/**
 * luma_media_row_set_playing:
 * @self: a row
 * @playing: FALSE for a paused app: the play key offers Play, the meter
 *   rests and a compact row's title and artwork dim
 */
void luma_media_row_set_playing(LumaMediaRow *self, gboolean playing);
gboolean luma_media_row_get_playing(LumaMediaRow *self);
/**
 * luma_media_row_set_sounding:
 * @self: a row
 * @sounding: whether sound is coming out now: the level glyph before the
 *   line moves (regular rows)
 */
void luma_media_row_set_sounding(LumaMediaRow *self, gboolean sounding);
gboolean luma_media_row_get_sounding(LumaMediaRow *self);
/**
 * luma_media_row_set_volume:
 * @self: a row
 * @percent: 0 to 100 (to the slider's top when it allows more)
 */
void luma_media_row_set_volume(LumaMediaRow *self, double percent);
double luma_media_row_get_volume(LumaMediaRow *self);
void luma_media_row_set_muted(LumaMediaRow *self, gboolean muted);
gboolean luma_media_row_get_muted(LumaMediaRow *self);
/**
 * luma_media_row_set_meter_segments:
 * @self: a row
 * @segments: segments of the level meter under the slider (28), or 0 for
 *   none; regular rows only. The meter is a segmented LumaAudioMeter.
 */
void luma_media_row_set_meter_segments(LumaMediaRow *self, guint segments);
/**
 * luma_media_row_set_level:
 * @self: a row
 * @position: the meter position, 0 (−60 dBFS) to 1 (0 dBFS)
 */
void luma_media_row_set_level(LumaMediaRow *self, double position);
/**
 * luma_media_row_set_trailing:
 * @self: a row
 * @widget: (nullable) (transfer none): a picker at the end of the volume
 *   line (luma_media_row_chip_new()); regular rows only
 */
void luma_media_row_set_trailing(LumaMediaRow *self, GtkWidget *widget);
/**
 * luma_media_row_get_trailing:
 * @self: a row
 *
 * Returns: (transfer none) (nullable): the trailing widget
 */
GtkWidget *luma_media_row_get_trailing(LumaMediaRow *self);
/**
 * luma_media_row_get_slider:
 * @self: a row
 *
 * Returns: (transfer none): the volume slider
 */
LumaVolumeSlider *luma_media_row_get_slider(LumaMediaRow *self);
/**
 * luma_media_row_get_meter:
 * @self: a row
 *
 * Returns: (transfer none) (nullable): the level meter, when it has one
 */
LumaAudioMeter *luma_media_row_get_meter(LumaMediaRow *self);
/**
 * luma_media_row_get_play_button:
 * @self: a row
 *
 * Returns: (transfer none): the play/pause key
 */
GtkWidget *luma_media_row_get_play_button(LumaMediaRow *self);
/**
 * luma_media_row_get_art_widget:
 * @self: a row
 *
 * Returns: (transfer none): the artwork
 */
GtkWidget *luma_media_row_get_art_widget(LumaMediaRow *self);
/**
 * luma_media_row_get_badge_widget:
 * @self: a row
 *
 * Returns: (transfer none): the app badge
 */
GtkWidget *luma_media_row_get_badge_widget(LumaMediaRow *self);

/**
 * luma_media_row_chip_new:
 * @icon_name: a Lucide glyph (`lumaui-…-symbolic`)
 * @label: what is chosen
 *
 * A row's trailing picker: a 28 px chip with a glyph, the choice and a
 * chevron, a plain fill at rest. It is a GtkMenuButton: give it a popover or
 * a menu model.
 *
 * Returns: (transfer floating): the chip
 */
GtkWidget *luma_media_row_chip_new(const char *icon_name, const char *label);
/**
 * luma_media_row_chip_set:
 * @chip: a chip from luma_media_row_chip_new()
 * @icon_name: its glyph
 * @label: its choice
 * @raised: TRUE when the choice is not the default one: the chip is raised
 */
void luma_media_row_chip_set(GtkWidget *chip, const char *icon_name, const char *label, gboolean raised);

G_END_DECLS
