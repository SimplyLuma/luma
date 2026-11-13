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
/* A press without its release: pressed again, it is the key repeating (tests). */
gboolean luma_tv_frame_key_held(GtkWindow *window, guint keyval);
/* The key let go (tests). */
gboolean luma_tv_frame_key_release(GtkWindow *window, guint keyval);
/* The TV keyboard's height in TV units (0 hidden), followed (luma-tv-keyboard.c). */
typedef void (*LumaTvKeyboardFunc)(guint height, gpointer data);
gpointer luma_tv_keyboard_watch(LumaTvKeyboardFunc func, gpointer data);
void luma_tv_keyboard_unwatch(gpointer watch);
/* The TV action bar on demand (luma-action-center.c): on a TV page an action center shows only while
 * the remote asked for it (the menu key, a held OK), holds the remote, and gives it back on Back or
 * once an action has acted. */
typedef struct _LumaActionCenter LumaActionCenter;
void luma_action_center_set_tv_demand(LumaActionCenter *self, gboolean demand);
gboolean luma_action_center_get_tv_demand(LumaActionCenter *self);
void luma_action_center_tv_open(LumaActionCenter *self, GtkWidget *from);
void luma_action_center_tv_close(LumaActionCenter *self);
gboolean luma_action_center_get_tv_open(LumaActionCenter *self);
/* TV posture's eased edge fades (luma-tv-fade.c), the mockups' .tv-page / .tv-fade: @draw draws what fades.
 * FALSE (nothing drawn) off a TV. */
typedef void (*LumaTvFadeDraw)(GtkWidget *widget, GtkSnapshot *snapshot);
double luma_tv_fade_unit(GtkWidget *widget);
gboolean luma_tv_fade_snapshot(GtkWidget *widget, GtkSnapshot *snapshot, GtkAdjustment *adjustment,
                               double top_units, double bottom_units, LumaTvFadeDraw draw);
/* A scrolled list's fades (the floating island's): put between @scroll and what it scrolls, or taken away. */
#define LUMA_TYPE_TV_FADE (luma_tv_fade_get_type())
G_DECLARE_FINAL_TYPE(LumaTvFade, luma_tv_fade, LUMA, TV_FADE, GtkWidget)
void luma_tv_fade_wrap(GtkScrolledWindow *scroll, gboolean tv, double top_units, double bottom_units);
G_END_DECLS
