/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI layers: the one place floating parts appear (toasts, dialogs,
 * drawers, the action center). Twin of structure_layers.py (LayerHost,
 * ModalHandle) and action_toast.py (ToastHost).
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaModalHandle:
 *
 * An open modal card. luma_modal_handle_close() dismisses it without telling
 * the owner; luma_modal_handle_cancel() closes it as Esc would and emits
 * #LumaModalHandle::cancelled.
 */
#define LUMA_TYPE_MODAL_HANDLE (luma_modal_handle_get_type())
G_DECLARE_FINAL_TYPE(LumaModalHandle, luma_modal_handle, LUMA, MODAL_HANDLE, GObject)

void luma_modal_handle_cancel(LumaModalHandle *self);
void luma_modal_handle_close(LumaModalHandle *self);
/**
 * luma_modal_handle_nudge:
 * @self: a handle
 *
 * Shake the card sideways: the window behind is not available yet.
 */
void luma_modal_handle_nudge(LumaModalHandle *self);
/**
 * luma_modal_handle_get_card:
 * @self: a handle
 *
 * Returns: (transfer none): the card being shown
 */
GtkWidget *luma_modal_handle_get_card(LumaModalHandle *self);
gboolean luma_modal_handle_get_is_drawer(LumaModalHandle *self);

/**
 * LumaLayerHost:
 *
 * An overlay (CSS node `overlay`) that owns what floats over one region. A
 * window's own host is made on first use; an app may wrap its main island in
 * one so toasts centre on it (Python's ToastHost is the same host).
 */
#define LUMA_TYPE_LAYER_HOST (luma_layer_host_get_type())
G_DECLARE_FINAL_TYPE(LumaLayerHost, luma_layer_host, LUMA, LAYER_HOST, GtkWidget)

/**
 * luma_layer_host_new:
 * @child: (nullable): the region's content
 *
 * Returns: (transfer floating): a new host around @child
 */
GtkWidget *luma_layer_host_new(GtkWidget *child);
void luma_layer_host_set_child(LumaLayerHost *self, GtkWidget *child);
/**
 * luma_layer_host_get_child:
 * @self: a host
 *
 * Returns: (transfer none) (nullable): the region's content
 */
GtkWidget *luma_layer_host_get_child(LumaLayerHost *self);

/**
 * luma_layer_host_for_widget:
 * @widget: a widget in a window
 *
 * The nearest host at or above @widget; the window's own is made on first use.
 *
 * Returns: (transfer none) (nullable): the host, %NULL when @widget has no window
 */
LumaLayerHost *luma_layer_host_for_widget(GtkWidget *widget);

/**
 * luma_layer_host_window_host:
 * @widget: a widget in a window
 *
 * The host of the window @widget is in: modal cards dim the whole window
 * (below the title row on a #LumaApplicationWindow).
 *
 * Returns: (transfer none) (nullable): the host
 */
LumaLayerHost *luma_layer_host_window_host(GtkWidget *widget);

/**
 * luma_layer_host_install:
 * @window: a window
 *
 * The window's host: its own, or its content wrapped in one (once).
 *
 * Returns: (transfer none): the host
 */
LumaLayerHost *luma_layer_host_install(GtkWindow *window);

/**
 * LumaDrawerMode:
 * @LUMA_DRAWER_MODE_AUTO: a drawer at phone width, a centred card otherwise
 * @LUMA_DRAWER_MODE_NEVER: always a centred card
 * @LUMA_DRAWER_MODE_ALWAYS: always a bottom drawer
 */
typedef enum {
  LUMA_DRAWER_MODE_AUTO,
  LUMA_DRAWER_MODE_NEVER,
  LUMA_DRAWER_MODE_ALWAYS,
} LumaDrawerMode;
GType luma_drawer_mode_get_type(void);
#define LUMA_TYPE_DRAWER_MODE (luma_drawer_mode_get_type())

/**
 * luma_layer_host_present_modal:
 * @self: a host
 * @card: the card to show over a scrim
 * @initial_focus: (nullable): what takes focus first
 * @mode: whether it is a drawer
 *
 * Show @card over a scrim: Esc and a tap on the scrim cancel it, Tab stays
 * inside, focus returns where it was when it closes.
 *
 * Returns: (transfer none): the handle, valid until the card closes
 */
LumaModalHandle *luma_layer_host_present_modal(LumaLayerHost *self, GtkWidget *card,
                                               GtkWidget *initial_focus, LumaDrawerMode mode);

/**
 * luma_layer_host_get_modal:
 * @self: a host
 *
 * Returns: (transfer none) (nullable): the open modal, if any
 */
LumaModalHandle *luma_layer_host_get_modal(LumaLayerHost *self);

/**
 * luma_layer_host_track_bar:
 * @self: a host
 * @bar: a floating bar at the foot of this region
 *
 * Keep toasts above @bar while it shows. The action center does this itself.
 */
void luma_layer_host_track_bar(LumaLayerHost *self, GtkWidget *bar);

G_END_DECLS
