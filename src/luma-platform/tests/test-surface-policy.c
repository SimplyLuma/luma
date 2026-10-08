/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-appearance.h"

static GSettings *settings(const char *id) { return g_settings_new(id); }
static void test_policy(void) {
  g_autoptr(GSettings) shared = settings("org.project_luma.shell-state");
  g_autoptr(GSettings) interface = settings("org.gnome.desktop.interface");
  g_autoptr(GSettings) a11y = settings("org.gnome.desktop.a11y.interface");
  g_autoptr(LumaSurfacePolicy) shell = luma_surface_policy_new_for_settings(
      LUMA_SURFACE_TARGET_SHELL, shared, interface, a11y);
  g_autoptr(LumaSurfacePolicy) app = luma_surface_policy_new_for_settings(
      LUMA_SURFACE_TARGET_APPLICATION, shared, interface, a11y);
  g_autoptr(GError) error = NULL;
  g_settings_set_string(interface, "color-scheme", "prefer-dark");
  g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, "dark");
  g_assert_false(luma_surface_policy_select(shell, "frost", &error));
  g_assert_error(error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED);
  g_clear_error(&error);
  luma_surface_policy_set_capabilities(shell, TRUE, TRUE, FALSE);
  luma_surface_policy_set_capabilities(app, TRUE, TRUE, FALSE);
  const char *modes[] = {"light", "dark", "frost", "glass"};
  const double radius[] = {0, 0, 38, 18};
  const double saturation[] = {1, 1, 1.6, 1.35};
  for (guint i = 0; i < G_N_ELEMENTS(modes); i++) {
    g_assert_true(luma_surface_policy_select(shell, modes[i], &error));
    g_assert_no_error(error);
    g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, modes[i]);
    g_autoptr(GVariant) recipe = luma_surface_policy_get_recipe(shell);
    double actual_radius, actual_saturation;
    g_assert_true(g_variant_lookup(recipe, "blur_px", "d", &actual_radius));
    g_assert_true(g_variant_lookup(recipe, "saturation", "d", &actual_saturation));
    g_assert_cmpfloat(actual_radius, ==, radius[i]);
    g_assert_cmpfloat(actual_saturation, ==, saturation[i]);
    if (i >= 2) {
      g_assert_cmpstr(luma_surface_policy_get_effective(app), ==, "light");
      g_assert_cmpstr(luma_surface_policy_get_reason(app), ==, "shell-only");
      g_settings_set_boolean(shared, "reduce-transparency", TRUE);
      g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, "light");
      g_assert_cmpstr(luma_surface_policy_get_requested(shell), ==, modes[i]);
      g_assert_false(luma_surface_policy_get_translucency_available(shell));
      g_settings_set_boolean(shared, "reduce-transparency", FALSE);
      g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, modes[i]);
    }
  }
  // Direct settings writes bypass the picker but cannot bypass the renderer gate.
  luma_surface_policy_set_capabilities(shell, FALSE, TRUE, FALSE);
  g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, "light");
  g_assert_cmpstr(luma_surface_policy_get_requested(shell), ==, "glass");
  g_assert_cmpstr(luma_surface_policy_get_reason(shell), ==, "renderer-unavailable");
  luma_surface_policy_set_capabilities(shell, TRUE, TRUE, FALSE);
  g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, "glass");
  g_settings_set_boolean(a11y, "high-contrast", TRUE);
  g_assert_true(luma_surface_policy_get_locked(shell));
  g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, "dark");
  g_assert_false(luma_surface_policy_select(shell, "light", &error));
  g_clear_error(&error);
  g_settings_set_string(interface, "color-scheme", "prefer-light");
  g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, "light");
  g_settings_set_boolean(a11y, "high-contrast", FALSE);
  g_assert_cmpstr(luma_surface_policy_get_effective(shell), ==, "glass");
  g_assert_false(luma_surface_policy_select(shell, "dark-glass", &error));
  g_clear_error(&error);
}
static void test_no_schema(void) {
  g_autoptr(LumaSurfacePolicy) self = luma_surface_policy_new_for_settings(
      LUMA_SURFACE_TARGET_APPLICATION, NULL, NULL, NULL);
  g_assert_cmpstr(luma_surface_policy_get_effective(self), ==, "light");
  g_assert_false(luma_surface_policy_get_translucency_available(self));
}
int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/appearance/policy", test_policy);
  g_test_add_func("/appearance/missing-schema", test_no_schema);
  return g_test_run();
}
