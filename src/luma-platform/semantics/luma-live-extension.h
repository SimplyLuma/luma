/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include "luma-action.h"
#include "luma-enums.h"
#include <gio/gio.h>

G_BEGIN_DECLS

#define LUMA_TYPE_LIVE_EXTENSION (luma_live_extension_get_type())
G_DECLARE_FINAL_TYPE(LumaLiveExtension, luma_live_extension, LUMA,
                     LIVE_EXTENSION, GObject)

LumaLiveExtension *luma_live_extension_new(const char *id, const char *app_id,
                                           LumaLiveCategory category,
                                           const char *title);
const char *luma_live_extension_get_id(LumaLiveExtension *self);
const char *luma_live_extension_get_app_id(LumaLiveExtension *self);
const char *luma_live_extension_get_title(LumaLiveExtension *self);
const char *luma_live_extension_get_subtitle(LumaLiveExtension *self);
void luma_live_extension_set_subtitle(LumaLiveExtension *self,
                                      const char *subtitle);
double luma_live_extension_get_progress(LumaLiveExtension *self);
void luma_live_extension_set_progress(LumaLiveExtension *self, double progress);
const char *luma_live_extension_get_expires_at(LumaLiveExtension *self);
void luma_live_extension_set_expires_at(LumaLiveExtension *self,
                                        const char *expires_at);
const char *luma_live_extension_get_starts_at(LumaLiveExtension *self);
void luma_live_extension_set_starts_at(LumaLiveExtension *self,
                                       const char *starts_at);
LumaPrivacy luma_live_extension_get_privacy(LumaLiveExtension *self);
void luma_live_extension_set_privacy(LumaLiveExtension *self,
                                     LumaPrivacy privacy);
/**
 * luma_live_extension_get_actions:
 * @self: a Live Extension
 *
 * Returns: (transfer none): up to three actions
 */
GListModel *luma_live_extension_get_actions(LumaLiveExtension *self);
gboolean luma_live_extension_add_action(LumaLiveExtension *self,
                                        LumaAction *action, GError **error);
GVariant *luma_live_extension_to_variant(LumaLiveExtension *self);

G_END_DECLS
