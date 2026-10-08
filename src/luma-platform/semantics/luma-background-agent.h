/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gio/gio.h>

G_BEGIN_DECLS

#define LUMA_BACKGROUND_AGENT_ERROR (luma_background_agent_error_quark())
GQuark luma_background_agent_error_quark(void);

/**
 * LumaBackgroundAgentError:
 * @LUMA_BACKGROUND_AGENT_ERROR_INVALID: an identifier or value was rejected
 * @LUMA_BACKGROUND_AGENT_ERROR_NOT_RUNNING: the agent is not on the bus
 * @LUMA_BACKGROUND_AGENT_ERROR_REFUSED: luma-background refused the request
 */
typedef enum {
  LUMA_BACKGROUND_AGENT_ERROR_INVALID,
  LUMA_BACKGROUND_AGENT_ERROR_NOT_RUNNING,
  LUMA_BACKGROUND_AGENT_ERROR_REFUSED,
} LumaBackgroundAgentError;

#define LUMA_TYPE_BACKGROUND_AGENT (luma_background_agent_get_type())
G_DECLARE_FINAL_TYPE(LumaBackgroundAgent, luma_background_agent, LUMA,
                     BACKGROUND_AGENT, GObject)

LumaBackgroundAgent *luma_background_agent_new(const char *app_id,
                                               const char *agent_id);
const char *luma_background_agent_get_app_id(LumaBackgroundAgent *self);
const char *luma_background_agent_get_agent_id(LumaBackgroundAgent *self);

gboolean luma_background_agent_is_requested(int argc, char **argv);

gboolean luma_background_agent_publish(LumaBackgroundAgent *self,
                                       const char *name, GVariant *value,
                                       GError **error);
void luma_background_agent_unpublish(LumaBackgroundAgent *self,
                                     const char *name);
/**
 * luma_background_agent_dup_values:
 * @self: an agent
 *
 * Returns: (transfer full): every published value, as `a{sv}`
 */
GVariant *luma_background_agent_dup_values(LumaBackgroundAgent *self);

gboolean luma_background_agent_schedule_at(LumaBackgroundAgent *self,
                                           const char *name, GDateTime *at,
                                           GError **error);
gboolean luma_background_agent_schedule_every(LumaBackgroundAgent *self,
                                              const char *name,
                                              guint seconds, GError **error);
gboolean luma_background_agent_unschedule(LumaBackgroundAgent *self,
                                          const char *name, GError **error);

gboolean luma_background_agent_register(LumaBackgroundAgent *self,
                                        GDBusConnection *connection,
                                        GError **error);
void luma_background_agent_quit(LumaBackgroundAgent *self, int status);
int luma_background_agent_run(LumaBackgroundAgent *self, int argc,
                              char **argv);

G_END_DECLS
