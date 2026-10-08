/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_placement.ModeSwitch (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaModeSwitch:
 *
 * Icon and label modes on a recessed well; the current mode is the raised
 * chip, which slides to the new one. In a window 820 px wide or less only the
 * current mode keeps its label. Arrows move between modes and choose them.
 * A switch offers at least two modes, with unique keys.
 *
 * Emits #LumaModeSwitch::changed (the new key) when a person picks a mode,
 * never for luma_mode_switch_set_current().
 */
#define LUMA_TYPE_MODE_SWITCH (luma_mode_switch_get_type())
G_DECLARE_FINAL_TYPE(LumaModeSwitch, luma_mode_switch, LUMA, MODE_SWITCH, GtkBox)

/**
 * luma_mode_switch_new:
 * @label: (nullable): what the group is, for assistive technology ("Mode")
 *
 * Returns: (transfer floating): a new, empty mode switch
 */
GtkWidget *luma_mode_switch_new(const char *label);
/**
 * luma_mode_switch_add:
 * @self: a mode switch
 * @key: the mode's stable key
 * @label: its name ("Mark up")
 * @icon: (nullable): a Lucide glyph ("pen-line"); %NULL makes the mode words alone (the switch is then labels-only)
 *
 * Add a mode. The first one added is current until another is chosen.
 */
void luma_mode_switch_add(LumaModeSwitch *self, const char *key, const char *label, const char *icon);
/* Keep every mode's accessible name while showing only its icon. */
void luma_mode_switch_set_icon_only(LumaModeSwitch *self, gboolean icon_only);
gboolean luma_mode_switch_get_icon_only(LumaModeSwitch *self);
/**
 * luma_mode_switch_set_current:
 * @self: a mode switch
 * @key: a key already added
 *
 * Choose a mode without emitting #LumaModeSwitch::changed.
 */
void luma_mode_switch_set_current(LumaModeSwitch *self, const char *key);
const char *luma_mode_switch_get_current(LumaModeSwitch *self);

/**
 * luma_mode_switch_set_fill:
 * @self: a mode switch
 * @fill: %TRUE (v71 Settings' segmented rows on a phone): it takes the width it is
 *   given, its segments share it equally, and the chip is one cell (Python
 *   `ModeSwitch(fill=True)`); works with compact
 */
void luma_mode_switch_set_fill(LumaModeSwitch *self, gboolean fill);
gboolean luma_mode_switch_get_fill(LumaModeSwitch *self);
/**
 * luma_mode_switch_set_small:
 * @self: a mode switch
 * @small: TRUE for 12px labels and 6px side padding in a regular-height property row
 *
 * Labels can ellipsize in constrained rows; their full accessible names remain.
 */
void luma_mode_switch_set_small(LumaModeSwitch *self, gboolean small);
/**
 * luma_mode_switch_set_compact:
 * @self: a mode switch
 * @compact: %TRUE for a choice inside a settings row (v70 `.cfrow .seg`: 32 tall, 26 buttons, 12.5)
 */
void luma_mode_switch_set_compact(LumaModeSwitch *self, gboolean compact);

/**
 * luma_mode_switch_set_stop_width:
 * @self: a mode switch
 * @width: fixed segment width, or zero to restore content-sized segments
 *
 * Numerical stops keep equal widths and a token gap even when fill is enabled.
 * Long labels ellipsize; their accessible names and tooltips remain complete.
 */
/* Let text-only mode labels shrink with ellipsis; full accessible names remain. */
void luma_mode_switch_set_ellipsize(LumaModeSwitch *self, gboolean ellipsize);
gboolean luma_mode_switch_get_ellipsize(LumaModeSwitch *self);
void luma_mode_switch_set_stop_width(LumaModeSwitch *self, int width);
int luma_mode_switch_get_stop_width(LumaModeSwitch *self);

/**
 * luma_mode_switch_set_status:
 * @self: a mode switch
 * @key: the mode key
 * @status: (nullable): "running" for a dot, other status text, or NULL to clear
 */
void luma_mode_switch_set_status(LumaModeSwitch *self, const char *key, const char *status);

G_END_DECLS
