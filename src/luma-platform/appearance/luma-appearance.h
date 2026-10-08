/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gio/gio.h>

G_BEGIN_DECLS
#define LUMA_TYPE_SURFACE_POLICY (luma_surface_policy_get_type())
G_DECLARE_FINAL_TYPE(LumaSurfacePolicy, luma_surface_policy, LUMA, SURFACE_POLICY, GObject)

/**
 * LumaSurfaceTarget:
 * @LUMA_SURFACE_TARGET_SHELL: A compositor-owned shell actor.
 * @LUMA_SURFACE_TARGET_APPLICATION: A native application window.
 */
typedef enum {
  LUMA_SURFACE_TARGET_SHELL,
  LUMA_SURFACE_TARGET_APPLICATION,
} LumaSurfaceTarget;
#define LUMA_TYPE_SURFACE_TARGET (luma_surface_target_get_type())
GType luma_surface_target_get_type(void) G_GNUC_CONST;

LumaSurfacePolicy *luma_surface_policy_new(LumaSurfaceTarget target);
/**
 * luma_surface_policy_new_for_settings:
 * @target: Surface owner.
 * @settings: (nullable): Luma shell-state schema, or NULL for opaque defaults.
 * @interface_settings: (nullable): GNOME desktop interface schema.
 * @a11y_settings: (nullable): GNOME accessibility interface schema.
 *
 * Returns: (transfer full): A signal-driven policy; no resident helper or polling.
 */
LumaSurfacePolicy *luma_surface_policy_new_for_settings(
    LumaSurfaceTarget target, GSettings *settings, GSettings *interface_settings,
    GSettings *a11y_settings);
/**
 * luma_surface_policy_set_capabilities:
 * @self: The policy.
 * @hardware: Renderer has reported hardware acceleration.
 * @shell_blur: Shell owns a working backdrop renderer including saturation.
 * @application_blur: Client has negotiated a live compositor backdrop interface.
 *
 * Unknown capability is false. A stored user preference is never proof of
 * renderer support. Application capability must remain false until a real
 * per-window protocol adapter owns its blur region and capture path.
 */
void luma_surface_policy_set_capabilities(LumaSurfacePolicy *self,
    gboolean hardware, gboolean shell_blur, gboolean application_blur);
const char *luma_surface_policy_get_requested(LumaSurfacePolicy *self);
const char *luma_surface_policy_get_effective(LumaSurfacePolicy *self);
/**
 * luma_surface_policy_get_surface:
 * @self: The policy.
 *
 * The treatment a surface is painted in. It differs from the effective
 * treatment in what it refuses for: only the accessibility answers, high
 * contrast and reduced transparency. A renderer that cannot blur is a reason
 * not to ask for a blur region, not a reason for one application to be a
 * different colour from the window beside it.
 *
 * Returns: One of light, dark, frost or glass.
 */
const char *luma_surface_policy_get_surface(LumaSurfacePolicy *self);
const char *luma_surface_policy_get_reason(LumaSurfacePolicy *self);
gboolean luma_surface_policy_get_has_selection(LumaSurfacePolicy *self);
gboolean luma_surface_policy_get_locked(LumaSurfacePolicy *self);
gboolean luma_surface_policy_get_translucency_available(LumaSurfacePolicy *self);
gboolean luma_surface_policy_select(LumaSurfacePolicy *self, const char *treatment,
                                    GError **error);
/**
 * luma_surface_policy_get_recipe:
 * @self: The policy.
 *
 * Returns: (transfer full): The effective surface's typed token dictionary.
 */
GVariant *luma_surface_policy_get_recipe(LumaSurfacePolicy *self);
G_END_DECLS
