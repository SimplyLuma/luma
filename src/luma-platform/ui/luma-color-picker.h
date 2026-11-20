/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI creative family: LumaColorPicker, the kit's one colour picker
 * (Canvas, Grid, Streamy). #LumaPropertyColor opens it from its swatch; an
 * app may also put it in its own panel (an ActionCenter grown panel).
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaColorPicker:
 *
 * A colour, picked by hand.
 *
 * - A saturation and brightness area over a hue strip and an opacity strip
 *   (drag, click, or focus and use the arrow keys; Shift steps ten times).
 * - Luma's palette, the document's colours (luma_color_picker_set_palette())
 *   and the colours recently committed in this app (kept in the app's config,
 *   `$XDG_CONFIG_HOME/<app id>/lumaui.ini`), as swatches.
 * - The eyedropper picks a colour off the screen through the Screenshot
 *   portal's PickColor; a hex field takes a typed code.
 *
 * #LumaColorPicker::color-changed follows every change (preview it live);
 * #LumaColorPicker::color-committed fires once when the person finishes: a
 * drag ends, a swatch or the eyedropper picks, a code is entered, an arrow
 * key is let go. Commit is one undo step. luma_color_picker_set_rgba()
 * emits neither.
 *
 * Tree: box.lumaui-color-picker
 *   ├ drawingarea.lumaui-color-picker-area
 *   ├ drawingarea.lumaui-color-picker-hue
 *   ├ drawingarea.lumaui-color-picker-alpha
 *   ├ flowbox.lumaui-color-picker-swatches (.luma, .document, .recent) …
 *   └ box.lumaui-color-picker-foot > button.lumaui-color-picker-eyedropper, entry.lumaui-creative-hex
 */
#define LUMA_TYPE_COLOR_PICKER (luma_color_picker_get_type())
G_DECLARE_FINAL_TYPE(LumaColorPicker, luma_color_picker, LUMA, COLOR_PICKER, GtkBox)

/**
 * luma_color_picker_new:
 *
 * Returns: (transfer floating): a new picker, black
 */
GtkWidget *luma_color_picker_new(void);
/**
 * luma_color_picker_set_rgba:
 * @self: a picker
 * @rgba: the colour to show; emits nothing
 */
void luma_color_picker_set_rgba(LumaColorPicker *self, const GdkRGBA *rgba);
/**
 * luma_color_picker_get_rgba:
 * @self: a picker
 * @rgba: (out caller-allocates): the colour
 */
void luma_color_picker_get_rgba(LumaColorPicker *self, GdkRGBA *rgba);
/**
 * luma_color_picker_set_palette:
 * @self: a picker
 * @colors: (array length=n_colors) (nullable): the document's colours, shown
 *   after Luma's
 * @n_colors: how many
 */
void luma_color_picker_set_palette(LumaColorPicker *self, const GdkRGBA *colors, guint n_colors);
/**
 * luma_color_picker_set_with_alpha:
 * @self: a picker
 * @with_alpha: whether the colour has an opacity (the strip shows); %TRUE by default
 */
void luma_color_picker_set_with_alpha(LumaColorPicker *self, gboolean with_alpha);
/**
 * luma_color_picker_pick_from_screen:
 * @self: a picker
 *
 * The eyedropper: ask the Screenshot portal for a colour on the screen. The
 * picked colour (with the current opacity) is changed and committed; a
 * cancelled pick does nothing. Without the portal the eyedropper is greyed.
 */
void luma_color_picker_pick_from_screen(LumaColorPicker *self);
/**
 * luma_color_picker_get_recent_colors:
 * @n_colors: (out): how many
 *
 * The colours recently committed in this app, newest first.
 *
 * Returns: (array length=n_colors) (transfer full): the colours
 */
GdkRGBA *luma_color_picker_get_recent_colors(guint *n_colors);
/**
 * luma_color_picker_add_recent_color:
 * @rgba: a colour to keep at the front of the recent ones (pickers do this
 *   when they commit)
 */
void luma_color_picker_add_recent_color(const GdkRGBA *rgba);

G_END_DECLS
