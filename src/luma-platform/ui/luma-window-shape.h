/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * luma_window_set_aspect_ratio:
 * @window: a toplevel window
 * @width: width part of the ratio, or 0 to remove it
 * @height: height part of the ratio, or 0 to remove it
 *
 * Asks the compositor to keep @window's content at @width:@height whenever
 * the window floats: while the person drags any edge or corner, when the app
 * itself resizes the window, and when it maximizes, where the window becomes
 * the largest size with that shape, centred. Fullscreen windows and windows
 * in a tile take the size they are given, so the content must still be ready
 * to letterbox.
 *
 * The content is the widget set with luma_window_set_aspect_content(), or
 * the whole window. Only the ratio matters: 4:3 and 640:480 are the same.
 * Passing 0, 0 removes the ratio. The call may come before or after the
 * window is shown; the ratio survives hiding and showing the window.
 *
 * On a compositor without Luma's window-shape protocol (another Wayland
 * compositor, X11, macOS, Windows) this does nothing and returns %FALSE, so
 * an app keeps its own fallback, such as fitting the window after a resize
 * ends.
 *
 * Returns: %TRUE when the compositor keeps the ratio itself, live, during
 *   interactive resizes; %FALSE when it does not (also when removing)
 */
gboolean luma_window_set_aspect_ratio(GtkWindow *window, int width, int height);

/**
 * luma_window_set_aspect_content:
 * @window: a toplevel window
 * @content: (nullable): the widget that shows the ratio-locked content, or
 *   %NULL for the whole window
 *
 * Names the part of @window whose shape luma_window_set_aspect_ratio()
 * keeps, for example a game view below a header bar. Everything else in the
 * window (header bar, tool bars) is measured after each layout and stays its
 * own size, so the ratio holds for @content alone. @content should be the
 * widget that takes the window's extra space.
 */
void luma_window_set_aspect_content(GtkWindow *window, GtkWidget *content);

/**
 * luma_window_get_aspect_ratio_enforced:
 * @window: a toplevel window
 *
 * Returns: whether the compositor of @window's display can keep an aspect
 *   ratio set with luma_window_set_aspect_ratio()
 */
gboolean luma_window_get_aspect_ratio_enforced(GtkWindow *window);

G_END_DECLS
