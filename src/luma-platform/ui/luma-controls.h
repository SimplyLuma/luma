/* SPDX-License-Identifier: Apache-2.0 */
/* Twins of content_controls.TextButton and Switch (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * luma_text_button_new:
 * @label: its words ("Share")
 * @icon: (nullable): a Lucide glyph before them ("share-2")
 * @style: (nullable): "plain" (%NULL), "fill", "key", "danger" (the red key), or "raised" (neutral dimensional)
 *
 * The v70 `.bt` button: 36 tall, 13/500, 10 round. A #GtkButton with the kit's classes.
 *
 * Returns: (transfer floating): a new button
 */
GtkWidget *luma_text_button_new(const char *label, const char *icon, const char *style);

/**
 * luma_text_button_set_small:
 * @button: a button from luma_text_button_new()
 * @small: v70 `.bt.sm`: 28 tall, 12 at .01em, 8 round
 */
void luma_text_button_set_small(GtkButton *button, gboolean small);

/* Set one size: regular36, small28, hero34, large40 (media hero actions), or touch46. */
void luma_text_button_set_size(GtkButton *button, const char *size);

/**
 * luma_text_button_set_hero:
 * @button: a button from luma_text_button_new()
 * @hero: a header's or hero's button (v70 `.cfnowa .bt`, `.cfhda .bt.fill`): 34 tall, 11 round
 */
void luma_text_button_set_hero(GtkButton *button, gboolean hero);

/**
 * luma_text_button_set_danger:
 * @button: a button from luma_text_button_new()
 * @danger: red ink (v70 `.bt.danger`)
 */
void luma_text_button_set_danger(GtkButton *button, gboolean danger);

/**
 * luma_icon_button_new:
 * @icon: a Lucide glyph ("x")
 * @label: what it does ("Forget WH-1000XM5"): its accessible name and tooltip
 *
 * The v70 `.ib` button: a glyph alone, 36 square, 10 round, an 18 glyph in ink-2.
 *
 * Returns: (transfer floating): a new button
 */
GtkWidget *luma_icon_button_new(const char *icon, const char *label);

/**
 * luma_icon_button_set_size:
 * @button: a button from luma_icon_button_new()
 * @size: "regular" (36), "row" (28 with 14: v70 `.cfcp`) "small" (26 with 15: `.cfgrp h6 .ib`) or "large" (40)
 */
void luma_icon_button_set_size(GtkButton *button, const char *size);

/* App-owned pressed state; a heart uses the filled loved treatment. */
/* The neutral dimensional surface shared with stacked action buttons. */
void luma_icon_button_set_raised(GtkButton *button, gboolean raised);

void luma_icon_button_set_active(GtkButton *button, gboolean active);

/**
 * luma_switch_new:
 * @big: v70 `.cfsw.big` (44x26) rather than 38x22
 *
 * The v70 `.cfsw` switch: a #GtkSwitch with the kit's class.
 *
 * Returns: (transfer floating): a new switch
 */
GtkWidget *luma_switch_new(gboolean big);

G_END_DECLS
