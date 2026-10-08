/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI creative family (KB-D): the layer tree (v70 .sulyr, .sucar,
 * .sueye) and the layer it shows.
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaLayer:
 *
 * One thing in a document's layer tree: a frame, shape, text, image, chart
 * or heading. The app owns its layers and keeps them current; the tree
 * shows whatever they say, and follows their changes.
 */
#define LUMA_TYPE_LAYER (luma_layer_get_type())
G_DECLARE_FINAL_TYPE(LumaLayer, luma_layer, LUMA, LAYER, GObject)

/**
 * luma_layer_new:
 * @id: the layer's stable id in the document
 * @name: its name ("Hero")
 * @icon: a Lucide glyph for its kind ("hash" for a frame, "square", "type")
 *
 * Returns: (transfer full): a new, visible, expanded layer without children
 */
LumaLayer *luma_layer_new(const char *id, const char *name, const char *icon);

const char *luma_layer_get_id(LumaLayer *self);
const char *luma_layer_get_name(LumaLayer *self);
void luma_layer_set_name(LumaLayer *self, const char *name);
const char *luma_layer_get_icon(LumaLayer *self);
void luma_layer_set_icon(LumaLayer *self, const char *icon);
gboolean luma_layer_get_visible(LumaLayer *self);
void luma_layer_set_visible(LumaLayer *self, gboolean visible);
/**
 * luma_layer_set_can_hide:
 * @self: a layer
 * @can_hide: whether its row has the eye (an outline's headings have none)
 */
void luma_layer_set_can_hide(LumaLayer *self, gboolean can_hide);
gboolean luma_layer_get_can_hide(LumaLayer *self);
/**
 * luma_layer_set_detail:
 * @self: a layer
 * @detail: (nullable): a quiet note at the row's end ("E2:E24", a count)
 */
void luma_layer_set_detail(LumaLayer *self, const char *detail);
const char *luma_layer_get_detail(LumaLayer *self);
void luma_layer_set_expanded(LumaLayer *self, gboolean expanded);
gboolean luma_layer_get_expanded(LumaLayer *self);

/**
 * luma_layer_get_children:
 * @self: a layer
 *
 * The layer's children, topmost first, as a #GListStore of #LumaLayer the
 * app may edit directly.
 *
 * Returns: (transfer none): the children
 */
GListModel *luma_layer_get_children(LumaLayer *self);
/**
 * luma_layer_append:
 * @self: a layer
 * @child: a layer to put last (lowest) among its children
 */
void luma_layer_append(LumaLayer *self, LumaLayer *child);

/**
 * LumaLayerTree:
 *
 * A document's layers as a tree (v70 .sulyr), topmost first: a caret opens
 * a layer's children, its icon says what it is, and an eye (shown on hover,
 * or always while hidden) shows or hides it. Selection is the object
 * selection (one token, shared with the canvas): click chooses, Ctrl adds,
 * Shift extends. Enter activates a layer; F2 or a double click renames it;
 * dragging a row, or Alt+Up and Alt+Down, asks to move it.
 *
 * The tree changes the #LumaLayer properties a person edits (name, visible,
 * expanded) and says so; it never moves layers itself, it asks with
 * #LumaLayerTree::move-requested.
 *
 * Tree: list.lumaui-creative-layers > row > box.lumaui-creative-layer(.selected)(.hidden)
 *   ├ button.lumaui-creative-layer-caret(.open) | box.lumaui-creative-layer-caret
 *   ├ image.lumaui-creative-layer-icon
 *   ├ label.lumaui-creative-layer-name (entry.lumaui-creative-layer-rename while renaming)
 *   ├ label.lumaui-creative-layer-detail
 *   └ button.lumaui-creative-layer-eye
 */
#define LUMA_TYPE_LAYER_TREE (luma_layer_tree_get_type())
G_DECLARE_FINAL_TYPE(LumaLayerTree, luma_layer_tree, LUMA, LAYER_TREE, GtkWidget)

/**
 * luma_layer_tree_new:
 * @layers: (nullable): the top-level layers, topmost first (#LumaLayer items)
 *
 * Returns: (transfer floating): a new layer tree
 */
GtkWidget *luma_layer_tree_new(GListModel *layers);

void luma_layer_tree_set_layers(LumaLayerTree *self, GListModel *layers);
/**
 * luma_layer_tree_get_layers:
 * @self: a layer tree
 * Returns: (transfer none) (nullable): the top-level layers
 */
GListModel *luma_layer_tree_get_layers(LumaLayerTree *self);

/**
 * luma_layer_tree_set_selected:
 * @self: a layer tree
 * @ids: (array zero-terminated=1) (nullable): the selected layers' ids
 *
 * Select layers (the canvas's selection) without emitting
 * #LumaLayerTree::selection-changed. Collapsed parents of a selected layer
 * open so it shows.
 */
void luma_layer_tree_set_selected(LumaLayerTree *self, const char *const *ids);
/**
 * luma_layer_tree_get_selected:
 * @self: a layer tree
 * Returns: (transfer full) (array zero-terminated=1): the selected ids, topmost first
 */
char **luma_layer_tree_get_selected(LumaLayerTree *self);

/**
 * luma_layer_tree_find:
 * @self: a layer tree
 * @id: a layer's id
 * Returns: (transfer none) (nullable): the layer
 */
LumaLayer *luma_layer_tree_find(LumaLayerTree *self, const char *id);

/**
 * luma_layer_tree_rename:
 * @self: a layer tree
 * @id: a layer's id
 *
 * Start renaming a layer in place, as F2 would.
 */
void luma_layer_tree_rename(LumaLayerTree *self, const char *id);

G_END_DECLS
