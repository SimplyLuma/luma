/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include "luma-enums.h"
#include <gio/gio.h>

G_BEGIN_DECLS

#define LUMA_TYPE_ACTION (luma_action_get_type())
G_DECLARE_FINAL_TYPE(LumaAction, luma_action, LUMA, ACTION, GObject)

LumaAction *luma_action_new(const char *id, const char *label);
const char *luma_action_get_id(LumaAction *self);
const char *luma_action_get_label(LumaAction *self);
const char *luma_action_get_description(LumaAction *self);
void luma_action_set_description(LumaAction *self, const char *description);
const char *luma_action_get_parameter_type(LumaAction *self);
void luma_action_set_parameter_type(LumaAction *self,
                                    const char *parameter_type);
LumaActionRisk luma_action_get_risk(LumaAction *self);
void luma_action_set_risk(LumaAction *self, LumaActionRisk risk);
gboolean luma_action_get_enabled(LumaAction *self);
void luma_action_set_enabled(LumaAction *self, gboolean enabled);
gboolean luma_action_get_requires_confirmation(LumaAction *self);
GVariant *luma_action_to_variant(LumaAction *self);

G_END_DECLS
