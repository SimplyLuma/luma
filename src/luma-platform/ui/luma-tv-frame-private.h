/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gtk/gtk.h>

G_BEGIN_DECLS
/* The floating island (luma-tv-frame.c): a window's frame of sidebar and page, laid out for a TV
 * while the window is in TV posture. @content is the window's own content widget. */
void luma_tv_frame_attach(GtkWindow *window, GtkWidget *content, GtkWidget *frame,
                          GtkWidget *sidebar, GtkWidget *island);
void luma_tv_frame_set_active(GtkWindow *window, gboolean tv);
gboolean luma_tv_frame_is_active(GtkWindow *window);
/* The page laid out again (luma-island.c): what it drew is fitted to the TV page on the next idle. */
void luma_tv_frame_page_changed(GtkWindow *window);
void luma_tv_room_load(GtkWidget *room);
/* Tests: the remote key @keyval as the frame takes it; TRUE when the frame handled it. */
gboolean luma_tv_frame_key(GtkWindow *window, guint keyval);
/* The TV keyboard's height in TV units (0 hidden), followed (luma-tv-keyboard.c). */
typedef void (*LumaTvKeyboardFunc)(guint height, gpointer data);
gpointer luma_tv_keyboard_watch(LumaTvKeyboardFunc func, gpointer data);
void luma_tv_keyboard_unwatch(gpointer watch);
G_END_DECLS
