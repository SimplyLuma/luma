/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/* Shared value-over-caption button, also used by the LCD transport. The
 * caller connects GtkButton::clicked and controls sensitivity. */
GtkWidget *luma_media_readout_new(const char *value, const char *caption);
void luma_media_readout_set_value(GtkWidget *self, const char *value);
void luma_media_readout_set_chip(GtkWidget *self, gboolean chip);

/* The native MD1 Session transport. The app owns playback and supplies the
 * displayed values; the kit owns the controls, readouts and phone layout. */
#define LUMA_TYPE_MEDIA_TRANSPORT_LCD (luma_media_transport_lcd_get_type())
G_DECLARE_FINAL_TYPE(LumaMediaTransportLcd, luma_media_transport_lcd, LUMA, MEDIA_TRANSPORT_LCD, GtkBox)

/* Signals: play-changed (gboolean), record-changed (gboolean),
 * loop-changed (gboolean), seek-start (), readout-activated (const char *key).
 * The three readout keys are "tempo", "signature" and "key". */
GtkWidget *luma_media_transport_lcd_new(void);
void luma_media_transport_lcd_set_lcd(LumaMediaTransportLcd *self, const char *bar, const char *time);
void luma_media_transport_lcd_set_readout(LumaMediaTransportLcd *self, const char *key, const char *value);
void luma_media_transport_lcd_set_playing(LumaMediaTransportLcd *self, gboolean playing);
void luma_media_transport_lcd_set_recording(LumaMediaTransportLcd *self, gboolean recording);
void luma_media_transport_lcd_set_looping(LumaMediaTransportLcd *self, gboolean looping);

/** Return a borrowed control for action binding, accessibility or sensitivity.
 * Keys: start, play, record, loop, tempo, signature, key. Unknown keys return NULL. */
GtkWidget *luma_media_transport_lcd_get_control(LumaMediaTransportLcd *self, const char *key);

G_END_DECLS
