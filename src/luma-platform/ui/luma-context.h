/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <glib-object.h>

G_BEGIN_DECLS

typedef enum {
  LUMA_PRESENTATION_WINDOWED,
  LUMA_PRESENTATION_FULLSCREEN_MOBILE,
} LumaPresentationMode;

typedef enum {
  LUMA_INPUT_POINTER,
  LUMA_INPUT_TOUCH,
} LumaInputMode;

#define LUMA_TYPE_PRESENTATION_MODE (luma_presentation_mode_get_type())
#define LUMA_TYPE_INPUT_MODE (luma_input_mode_get_type())
GType luma_presentation_mode_get_type(void) G_GNUC_CONST;
GType luma_input_mode_get_type(void) G_GNUC_CONST;

#define LUMA_TYPE_CONTEXT (luma_context_get_type())
G_DECLARE_FINAL_TYPE(LumaContext, luma_context, LUMA, CONTEXT, GObject)

LumaContext *luma_context_new(LumaPresentationMode presentation,
                              LumaInputMode input);
LumaContext *luma_context_new_from_environment(void);
LumaPresentationMode luma_context_get_presentation(LumaContext *self);
LumaInputMode luma_context_get_input(LumaContext *self);
gboolean luma_context_get_decorated(LumaContext *self);
gboolean luma_context_get_touch_targets(LumaContext *self);

G_END_DECLS
