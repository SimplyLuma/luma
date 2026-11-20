/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

/* Without Luma's window-shape protocol (X11 here, or another Wayland
 * compositor) every call is a silent no-op that says the compositor does not
 * keep the ratio, so the app's own fallback stays in charge. The compositor
 * side is tested under headless Mutter by tests/mutter/window-shape. */
static void
test_no_op_without_protocol (void)
{
  GtkWidget *window = gtk_window_new ();
  GtkWidget *content = gtk_drawing_area_new ();

  gtk_window_set_child (GTK_WINDOW (window), content);
  g_assert_false (luma_window_get_aspect_ratio_enforced (GTK_WINDOW (window)));
  g_assert_false (luma_window_set_aspect_ratio (GTK_WINDOW (window), 4, 3));
  luma_window_set_aspect_content (GTK_WINDOW (window), content);
  gtk_window_set_default_size (GTK_WINDOW (window), 300, 200);
  gtk_window_present (GTK_WINDOW (window));
  while (g_main_context_iteration (NULL, FALSE))
    ;

  /* Nothing constrains the window: it keeps the size the app asked for. */
  g_assert_cmpint (gtk_widget_get_width (window), ==, 300);
  g_assert_false (luma_window_set_aspect_ratio (GTK_WINDOW (window), 16, 9));
  g_assert_false (luma_window_set_aspect_ratio (GTK_WINDOW (window), 0, 0));
  luma_window_set_aspect_content (GTK_WINDOW (window), NULL);

  gtk_window_destroy (GTK_WINDOW (window));
  while (g_main_context_iteration (NULL, FALSE))
    ;
}

/* The ratio and content may be set before the window exists on screen, and
 * survive the content widget going away first. */
static void
test_set_before_realize_and_content_gone (void)
{
  GtkWidget *window = gtk_window_new ();
  GtkWidget *content = gtk_drawing_area_new ();

  luma_window_set_aspect_ratio (GTK_WINDOW (window), 640, 480);
  gtk_window_set_child (GTK_WINDOW (window), content);
  luma_window_set_aspect_content (GTK_WINDOW (window), content);
  gtk_window_set_child (GTK_WINDOW (window), NULL);
  gtk_window_present (GTK_WINDOW (window));
  while (g_main_context_iteration (NULL, FALSE))
    ;
  gtk_widget_unrealize (window);
  gtk_widget_realize (window);
  gtk_window_destroy (GTK_WINDOW (window));
  while (g_main_context_iteration (NULL, FALSE))
    ;
}

static void
test_invalid_ratio_is_refused (void)
{
  if (g_test_subprocess ())
    {
      GtkWidget *window = gtk_window_new ();
      luma_window_set_aspect_ratio (GTK_WINDOW (window), 4, 0);
      return;
    }
  g_test_trap_subprocess (NULL, 0, G_TEST_SUBPROCESS_DEFAULT);
  g_test_trap_assert_failed ();
  g_test_trap_assert_stderr ("*CRITICAL*luma_window_set_aspect_ratio*");
}

int
main (int argc, char **argv)
{
  gtk_test_init (&argc, &argv, NULL);
  g_test_add_func ("/window-shape/no-op-without-protocol",
                   test_no_op_without_protocol);
  g_test_add_func ("/window-shape/before-realize",
                   test_set_before_realize_and_content_gone);
  g_test_add_func ("/window-shape/invalid-ratio",
                   test_invalid_ratio_is_refused);
  return g_test_run ();
}
