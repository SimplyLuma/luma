/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include "luma-action.h"
#include "luma-enums.h"
#include <gio/gio.h>

G_BEGIN_DECLS

#define LUMA_TYPE_SEMANTIC_OBJECT (luma_semantic_object_get_type())
G_DECLARE_FINAL_TYPE(LumaSemanticObject, luma_semantic_object, LUMA,
                     SEMANTIC_OBJECT, GObject)

LumaSemanticObject *luma_semantic_object_new(const char *id, const char *kind,
                                             const char *name);
const char *luma_semantic_object_get_id(LumaSemanticObject *self);
const char *luma_semantic_object_get_kind(LumaSemanticObject *self);
const char *luma_semantic_object_get_name(LumaSemanticObject *self);
void luma_semantic_object_set_name(LumaSemanticObject *self, const char *name);
LumaPrivacy luma_semantic_object_get_privacy(LumaSemanticObject *self);
void luma_semantic_object_set_privacy(LumaSemanticObject *self,
                                      LumaPrivacy privacy);
/**
 * luma_semantic_object_get_actions:
 * @self: a semantic object
 *
 * Returns: (transfer none): the object's actions
 */
GListModel *luma_semantic_object_get_actions(LumaSemanticObject *self);
/**
 * luma_semantic_object_get_children:
 * @self: a semantic object
 *
 * Returns: (transfer none): child objects
 */
GListModel *luma_semantic_object_get_children(LumaSemanticObject *self);
void luma_semantic_object_add_action(LumaSemanticObject *self,
                                     LumaAction *action);
void luma_semantic_object_add_child(LumaSemanticObject *self,
                                    LumaSemanticObject *child);
GVariant *luma_semantic_object_to_variant(LumaSemanticObject *self);

G_END_DECLS
