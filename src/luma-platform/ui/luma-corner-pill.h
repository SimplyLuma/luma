/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_placement.CornerPill (Python). */
#pragma once

#include <gtk/gtk.h>
#include "luma-mode-switch.h"

G_BEGIN_DECLS

typedef struct _LumaDetailsPane LumaDetailsPane;

/**
 * LumaCornerPill:
 *
 * The top-right pill of an island: the view and the thing itself, always in
 * the one placement order, whatever order the app adds them in:
 *
 *   mode switch · who's here · Open in · Share · on/off states (Favourite) ·
 *   Information · object actions (Edit) · More
 *
 * An on state whose glyph has a "-filled" twin shows it while on; a heart
 * also takes the love colour.
 *
 * The app says what it has; the kit decides where. Plain commands are
 * #GAction names (the one stable command ID); controls whose menu anchors to
 * the pill emit a signal with the anchor.
 *
 * Signals: #LumaCornerPill::open-in (GtkWidget *anchor),
 * #LumaCornerPill::share (GtkWidget *anchor), #LumaCornerPill::edit-cancelled,
 * #LumaCornerPill::edit-done.
 */
#define LUMA_TYPE_CORNER_PILL (luma_corner_pill_get_type())
G_DECLARE_FINAL_TYPE(LumaCornerPill, luma_corner_pill, LUMA, CORNER_PILL, GtkBox)

/**
 * luma_corner_pill_new:
 *
 * Returns: (transfer floating): a new pill; add at least one control
 */
GtkWidget *luma_corner_pill_new(void);
/**
 * luma_corner_pill_set_orientation:
 * @self: a pill
 * @orientation: horizontal pill (default) or vertical stack with inset separators
 */
void luma_corner_pill_set_orientation(LumaCornerPill *self, GtkOrientation orientation);
/**
 * luma_corner_pill_set_modes:
 * @self: a pill
 * @modes: the view's mode switch
 */
void luma_corner_pill_set_modes(LumaCornerPill *self, LumaModeSwitch *modes);
/**
 * luma_corner_pill_set_people:
 * @self: a pill
 * @people: who's here (a face pile)
 */
void luma_corner_pill_set_people(LumaCornerPill *self, GtkWidget *people);
/**
 * luma_corner_pill_add_open_in:
 * @self: a pill
 *
 * Offer Open in; a press emits #LumaCornerPill::open-in with the button, to
 * anchor a #LumaOpenInMenu to.
 */
void luma_corner_pill_add_open_in(LumaCornerPill *self);
/**
 * luma_corner_pill_add_share:
 * @self: a pill
 *
 * Offer Share (Lucide "share-2"); a press emits #LumaCornerPill::share.
 */
void luma_corner_pill_add_share(LumaCornerPill *self);
/**
 * luma_corner_pill_add_action:
 * @self: a pill
 * @icon: a Lucide glyph ("pencil")
 * @label: its name ("Edit")
 * @action_name: the detailed #GAction name it activates ("win.edit")
 *
 * An object action.
 */
void luma_corner_pill_add_action(LumaCornerPill *self, const char *icon, const char *label,
                                 const char *action_name);
/**
 * luma_corner_pill_add_state:
 * @self: a pill
 * @icon: a Lucide glyph ("heart")
 * @label: its name ("Favourite")
 * @action_name: a stateful boolean #GAction; its state is the toggle's
 *
 * An on/off state of the thing.
 */
void luma_corner_pill_add_state(LumaCornerPill *self, const char *icon, const char *label,
                                const char *action_name);
/**
 * luma_corner_pill_set_info_pane:
 * @self: a pill
 * @pane: the details pane Information opens and closes
 */
void luma_corner_pill_set_info_pane(LumaCornerPill *self, LumaDetailsPane *pane);
/**
 * luma_corner_pill_set_info_action:
 * @self: a pill
 * @action_name: a stateful boolean #GAction: whether the app's details show
 */
void luma_corner_pill_set_info_action(LumaCornerPill *self, const char *action_name);
/**
 * luma_corner_pill_set_more_menu:
 * @self: a pill
 * @menu: the More menu (a drawer at phone width)
 */
void luma_corner_pill_set_more_menu(LumaCornerPill *self, GMenuModel *menu);
/**
 * luma_corner_pill_set_labelled:
 * @self: a pill
 * @labelled: whether Share and the actions show their words on a computer
 *
 * Words fold away at phone width, except the primary action's.
 */
void luma_corner_pill_set_labelled(LumaCornerPill *self, gboolean labelled);
/**
 * luma_corner_pill_set_primary:
 * @self: a pill
 * @label: (nullable): the label of one added action to raise as the key
 */
void luma_corner_pill_set_primary(LumaCornerPill *self, const char *label);
/**
 * luma_corner_pill_edit:
 * @self: a pill
 * @done_label: (nullable): the key's word ("Done", "Save")
 *
 * Show Cancel · Done in place of the pill's controls until either is pressed
 * (#LumaCornerPill::edit-cancelled, #LumaCornerPill::edit-done) or
 * luma_corner_pill_stop_editing() is called.
 */
void luma_corner_pill_edit(LumaCornerPill *self, const char *done_label);
void luma_corner_pill_stop_editing(LumaCornerPill *self);
gboolean luma_corner_pill_get_editing(LumaCornerPill *self);

/**
 * luma_corner_pill_set_keep_labels:
 * @self: the corner pill
 * @keep: whether labelled actions retain their words on a phone
 *
 * Keep project/navigation labels visible; the default folds icon labels.
 */
void luma_corner_pill_set_keep_labels(LumaCornerPill *self, gboolean keep);

/** Keep the primary action filled but icon-only; its accessible name is preserved. Default FALSE. */
void luma_corner_pill_set_primary_icon_only(LumaCornerPill *self, gboolean icon_only);

G_END_DECLS
