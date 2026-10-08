/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include <glib.h>

static void test_explicit_context(void) {
  g_autoptr(LumaContext) context =
      luma_context_new(LUMA_PRESENTATION_FULLSCREEN_MOBILE, LUMA_INPUT_TOUCH);
  g_assert_false(luma_context_get_decorated(context));
  g_assert_true(luma_context_get_touch_targets(context));
}

static void test_environment_axes_are_independent(void) {
  g_setenv("LUMA_DEVICE_CLASS", "handheld", TRUE);
  g_setenv("LUMA_PRESENTATION_MODE", "windowed", TRUE);
  g_setenv("LUMA_INPUT_MODE", "pointer", TRUE);
  g_autoptr(LumaContext) context = luma_context_new_from_environment();
  g_assert_true(luma_context_get_decorated(context));
  g_assert_false(luma_context_get_touch_targets(context));
  g_unsetenv("LUMA_DEVICE_CLASS");
  g_unsetenv("LUMA_PRESENTATION_MODE");
  g_unsetenv("LUMA_INPUT_MODE");
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/luma/context/explicit", test_explicit_context);
  g_test_add_func("/luma/context/environment",
                  test_environment_axes_are_independent);
  return g_test_run();
}
