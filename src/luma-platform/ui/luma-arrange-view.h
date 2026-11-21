/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI creative family: LumaArrangeView, direct manipulation of boxes
 * over a picture. A person picks, drags and resizes things laid out on a
 * fixed-size canvas (a stream's scene, a slide, a page), with snapping
 * guides and keyboard nudges. The view draws only the selection, its
 * handles and the guides; the app draws the picture itself (its content).
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaArrangeView:
 *
 * Boxes on a canvas, moved and resized by hand.
 *
 * The app gives the canvas size in its own units (1920 × 1080) and its
 * content (a picture of the canvas), and names each box with an id and a
 * rectangle in canvas units. The view fits the canvas into itself, centred,
 * and lays the content over exactly that rectangle.
 *
 * - Press a box to select it (the topmost under the pointer); press empty
 *   canvas to clear the selection. Double-click activates a box.
 * - Drag a selected box to move it (Alt: without snapping); drag one of its
 *   eight handles to resize it. A box that keeps its aspect scales,
 *   anchored at the opposite corner or edge (an edge's box stays centred
 *   across it); Shift stretches it freely (or, for a box that doesn't keep
 *   its aspect, keeps the proportions). Alt resizes from the centre: the
 *   opposite side moves as much. Ctrl crops that edge or corner instead of
 *   resizing, for a box that can be cropped, the cut-away part drawn dim
 *   outside the box. Pressing or letting go of a modifier mid-drag changes
 *   the drag at once; #LumaArrangeView:drag-mode says what it does now.
 * - Crop mode (#LumaArrangeView:cropping): double-click a box that can be
 *   cropped, press Enter on it, or set it from the app. Its handles crop
 *   (Alt: both opposite edges), a drag inside slides the source under the
 *   box, and Enter or Esc leaves it.
 * - Edges and centres snap to the canvas's edges, centre lines and thirds,
 *   and to the other boxes' edges and centres, a resize's and a crop's
 *   pulled edges too; a guide shows each snap.
 * - Several boxes: Shift+click adds or removes one; a drag from empty canvas
 *   draws a rubber band that selects what it touches (Shift: adds); Ctrl+A
 *   selects every visible box. Two or more chosen share one box with
 *   handles: dragging any of them moves all, the handles scale them all
 *   inside it (proportions kept, Shift stretches, Alt from the centre), and
 *   the arrow keys nudge all; #LumaArrangeView::group-changed and
 *   ::group-committed report every moved box (crops untouched).
 * - Keyboard: the arrow keys move the selected box one unit (Shift: ten),
 *   Tab and Shift+Tab choose the next and previous box, Esc clears the
 *   selection.
 * - A locked box can be selected but not moved or resized; a hidden one
 *   can't be picked on the canvas.
 *
 * #LumaArrangeView::item-changed reports every step of a drag (move the
 * picture with it); #LumaArrangeView::item-committed reports the end of a
 * drag or a nudge (one undo step, a save). The app owns the rectangles:
 * the view changes its own copy while a person drags, and the app may
 * correct it with luma_arrange_view_set_item_rect().
 *
 * A crop is four fractions of the source cut away (left, top, right,
 * bottom, each 0 to 0.49); the box's rectangle is what shows, so the whole
 * source is the rectangle grown by them. #LumaArrangeView::crop-changed
 * and #LumaArrangeView::crop-committed report them as
 * #LumaArrangeView::item-changed and ::item-committed report rectangles,
 * and the app keeps them with luma_arrange_view_set_item_crop().
 *
 * Tree: widget.lumaui-arrange-view
 *   ├ content
 *   └ (style nodes: .lumaui-arrange-frame, -hover, -handle, -knob, -guide, -locked, -ghost, -marquee)
 */
#define LUMA_TYPE_ARRANGE_VIEW (luma_arrange_view_get_type())
G_DECLARE_FINAL_TYPE(LumaArrangeView, luma_arrange_view, LUMA, ARRANGE_VIEW, GtkWidget)

/**
 * luma_arrange_view_new:
 * @content: (nullable): the picture of the canvas, laid over the fitted canvas
 *
 * Returns: (transfer floating): a new view with a 1920 × 1080 canvas
 */
GtkWidget *luma_arrange_view_new(GtkWidget *content);
void luma_arrange_view_set_content(LumaArrangeView *self, GtkWidget *content);
/**
 * luma_arrange_view_get_content:
 * @self: a view
 *
 * Returns: (transfer none) (nullable): the content
 */
GtkWidget *luma_arrange_view_get_content(LumaArrangeView *self);
/**
 * luma_arrange_view_set_canvas_size:
 * @self: a view
 * @width: the canvas's width in the app's units (> 0)
 * @height: its height (> 0)
 */
void luma_arrange_view_set_canvas_size(LumaArrangeView *self, double width, double height);
/**
 * luma_arrange_view_set_fit_margins:
 * @self: a view
 * @left: room to keep clear on the left, in view pixels
 * @top: at the top
 * @right: on the right
 * @bottom: at the bottom (an action bar)
 *
 * Fit the canvas into the view less these margins, so floating panels and
 * bars never cover it (follow #LumaCreativeWorkspace::work-area-changed).
 * When the margins leave no room, the canvas fits the whole view.
 */
void luma_arrange_view_set_fit_margins(LumaArrangeView *self, double left, double top, double right, double bottom);
/**
 * luma_arrange_view_add_item:
 * @self: a view
 * @id: the box's stable id
 * @label: what it is, for the accessible name ("Camera")
 * @x: left, in canvas units
 * @y: top
 * @width: width (> 0)
 * @height: height (> 0)
 *
 * Add a box on top of the others.
 */
void luma_arrange_view_add_item(LumaArrangeView *self, const char *id, const char *label, double x, double y,
                                double width, double height);
void luma_arrange_view_remove_item(LumaArrangeView *self, const char *id);
void luma_arrange_view_clear(LumaArrangeView *self);
void luma_arrange_view_set_item_rect(LumaArrangeView *self, const char *id, double x, double y, double width,
                                     double height);
/**
 * luma_arrange_view_get_item_rect:
 * @self: a view
 * @id: a box's id
 * @x: (out) (optional): left
 * @y: (out) (optional): top
 * @width: (out) (optional): width
 * @height: (out) (optional): height
 *
 * Returns: whether the view has the box
 */
gboolean luma_arrange_view_get_item_rect(LumaArrangeView *self, const char *id, double *x, double *y, double *width,
                                         double *height);
void luma_arrange_view_set_item_label(LumaArrangeView *self, const char *id, const char *label);
void luma_arrange_view_set_item_locked(LumaArrangeView *self, const char *id, gboolean locked);
void luma_arrange_view_set_item_visible(LumaArrangeView *self, const char *id, gboolean visible);
/**
 * luma_arrange_view_set_item_keeps_aspect:
 * @self: a view
 * @id: a box's id
 * @keeps: whether resizing keeps its proportions by default (a picture, a
 *   camera); Shift then frees them
 */
void luma_arrange_view_set_item_keeps_aspect(LumaArrangeView *self, const char *id, gboolean keeps);
/**
 * luma_arrange_view_set_item_croppable:
 * @self: a view
 * @id: a box's id
 * @croppable: whether a person can crop it (Ctrl on a handle, crop mode);
 *   %FALSE by default, and a double click then activates it instead
 */
void luma_arrange_view_set_item_croppable(LumaArrangeView *self, const char *id, gboolean croppable);
/**
 * luma_arrange_view_set_item_crop:
 * @self: a view
 * @id: a box's id
 * @left: the fraction of the source cut from its left, 0 to 0.49
 * @top: from its top
 * @right: from its right
 * @bottom: from its bottom
 *
 * The box's crop, the app's own record of it; its rectangle stays what
 * shows (set it with luma_arrange_view_set_item_rect()).
 */
void luma_arrange_view_set_item_crop(LumaArrangeView *self, const char *id, double left, double top, double right,
                                     double bottom);
/**
 * luma_arrange_view_get_item_crop:
 * @self: a view
 * @id: a box's id
 * @left: (out) (optional): the fraction cut from the left
 * @top: (out) (optional): top
 * @right: (out) (optional): right
 * @bottom: (out) (optional): bottom
 *
 * Returns: whether the view has the box
 */
gboolean luma_arrange_view_get_item_crop(LumaArrangeView *self, const char *id, double *left, double *top,
                                         double *right, double *bottom);
/**
 * luma_arrange_view_set_item_ghost:
 * @self: a view
 * @id: a box's id
 * @source: (nullable): the uncropped source, drawn dim where a crop cuts it
 *   away; without one, a scrim shows the cut-away part
 */
void luma_arrange_view_set_item_ghost(LumaArrangeView *self, const char *id, GdkPaintable *source);
/**
 * luma_arrange_view_set_cropping:
 * @self: a view
 * @id: (nullable): the box to crop (one that can be cropped, not locked,
 *   selected too); %NULL leaves crop mode
 */
void luma_arrange_view_set_cropping(LumaArrangeView *self, const char *id);
/**
 * luma_arrange_view_get_cropping:
 * @self: a view
 *
 * Returns: (nullable): the box in crop mode
 */
const char *luma_arrange_view_get_cropping(LumaArrangeView *self);
/**
 * luma_arrange_view_get_drag_mode:
 * @self: a view
 *
 * Returns: what the drag under way does (#LumaArrangeView:drag-mode)
 */
const char *luma_arrange_view_get_drag_mode(LumaArrangeView *self);
/**
 * luma_arrange_view_set_item_shown_rect:
 * @self: a view
 * @id: a box's id
 * @x: the left of the picture actually on screen, in canvas units
 * @y: its top
 * @width: its width
 * @height: its height
 *
 * Where the app's picture of the box is on screen now (a frame or two
 * behind a drag). While set, the selection outline and the crop frame are
 * drawn there, so they never part from the picture; the handles and hit
 * testing keep the live rectangle under the pointer. Clear it when the drag
 * is committed.
 */
void luma_arrange_view_set_item_shown_rect(LumaArrangeView *self, const char *id, double x, double y, double width,
                                           double height);
void luma_arrange_view_clear_item_shown_rect(LumaArrangeView *self, const char *id);
/**
 * luma_arrange_view_get_item_shown_rect:
 * @self: a view
 * @id: a box's id
 * @x: (out) (optional): left
 * @y: (out) (optional): top
 * @width: (out) (optional): width
 * @height: (out) (optional): height
 *
 * Returns: whether a shown rectangle is set for the box
 */
gboolean luma_arrange_view_get_item_shown_rect(LumaArrangeView *self, const char *id, double *x, double *y,
                                               double *width, double *height);
/**
 * luma_arrange_view_set_order:
 * @self: a view
 * @ids: (array zero-terminated=1): the boxes from the bottom to the top;
 *   boxes not named keep their order below the named ones
 */
void luma_arrange_view_set_order(LumaArrangeView *self, const char *const *ids);
/**
 * luma_arrange_view_set_selected:
 * @self: a view
 * @id: (nullable): the box to select; %NULL clears the selection
 *
 * Select without emitting #LumaArrangeView::selection-changed.
 */
void luma_arrange_view_set_selected(LumaArrangeView *self, const char *id);
/**
 * luma_arrange_view_set_selection:
 * @self: a view
 * @ids: (array zero-terminated=1) (nullable): the boxes to select, the last
 *   one the primary (luma_arrange_view_get_selected())
 *
 * Select without emitting anything.
 */
void luma_arrange_view_set_selection(LumaArrangeView *self, const char *const *ids);
/**
 * luma_arrange_view_get_selection:
 * @self: a view
 *
 * Returns: (array zero-terminated=1) (transfer full): every chosen box, in
 *   the order chosen
 */
char **luma_arrange_view_get_selection(LumaArrangeView *self);
/**
 * luma_arrange_view_select_all:
 * @self: a view
 *
 * Select every visible box (Ctrl+A), as a person would: emits.
 */
void luma_arrange_view_select_all(LumaArrangeView *self);
/**
 * luma_arrange_view_get_selected:
 * @self: a view
 *
 * Returns: (nullable): the selected box's id
 */
const char *luma_arrange_view_get_selected(LumaArrangeView *self);
void luma_arrange_view_set_snapping(LumaArrangeView *self, gboolean snapping);
gboolean luma_arrange_view_get_snapping(LumaArrangeView *self);
/**
 * luma_arrange_view_canvas_to_view:
 * @self: a view
 * @x: a canvas x
 * @y: a canvas y
 * @view_x: (out): where it is in the view
 * @view_y: (out): where it is in the view
 */
void luma_arrange_view_canvas_to_view(LumaArrangeView *self, double x, double y, double *view_x, double *view_y);

/**
 * luma_arrange_snap:
 * @value: the edge or centre being moved, in canvas units
 * @lines: (array length=n_lines): where it may snap
 * @n_lines: how many lines
 * @threshold: how near counts, in canvas units
 * @snapped: (out): the line it snapped to, when it did
 *
 * The snapping rule, for tests and for apps that place things themselves:
 * the nearest line within @threshold wins.
 *
 * Returns: the offset to add to @value (0 when nothing is near)
 */
double luma_arrange_snap(double value, const double *lines, guint n_lines, double threshold, double *snapped);

G_END_DECLS
