/* SPDX-License-Identifier: Apache-2.0 */
/* LumaAudioMeter: levels, ballistics, clipping and the quiet accessible updates. */
#include "luma-ui.h"

#include <math.h>

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void wait_ms(guint ms) {
  gboolean done = FALSE;
  g_timeout_add(ms, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static void test_meter(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *meter = luma_audio_meter_new(2);
  LumaAudioMeter *m = LUMA_AUDIO_METER(meter);
  luma_audio_meter_set_horizontal(m, TRUE);
  gtk_window_set_child(GTK_WINDOW(window), meter);
  gtk_window_present(GTK_WINDOW(window));
  wait_ms(100);
  g_assert_true(gtk_widget_has_css_class(meter, "lumaui-audio-meter"));
  g_assert_true(gtk_widget_has_css_class(meter, "horizontal"));
  /* Horizontal: two 4 px bars and a 2 px gap high. */
  int min, nat;
  gtk_widget_measure(meter, GTK_ORIENTATION_VERTICAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, 10);
  luma_audio_meter_set_channels(m, 1);
  gtk_widget_measure(meter, GTK_ORIENTATION_VERTICAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, 4);
  luma_audio_meter_set_channels(m, 2);

  g_assert_cmpfloat(luma_audio_meter_db_to_fraction(-60), ==, 0);
  g_assert_cmpfloat(luma_audio_meter_db_to_fraction(12), ==, 1);
  /* A rise shows at once; a fall follows the ballistics. */
  luma_audio_meter_set_levels_db(m, -12, -20);
  g_assert_cmpfloat(luma_audio_meter_get_level_db(m, 0), ==, -12);
  g_assert_cmpfloat(luma_audio_meter_get_peak_db(m, 1), ==, -20);
  luma_audio_meter_set_levels_db(m, -60, -60);
  if (!luma_ui_reduced_motion()) {
    g_assert_cmpfloat(luma_audio_meter_get_level_db(m, 0), ==, -12);
    wait_ms(300);
    double now = luma_audio_meter_get_level_db(m, 0);
    g_assert_cmpfloat(now, <, -12);
    g_assert_cmpfloat(now, >, -60);
    /* The peak holds. */
    g_assert_cmpfloat(luma_audio_meter_get_peak_db(m, 0), ==, -12);
    wait_ms(3500);
    g_assert_cmpfloat(luma_audio_meter_get_level_db(m, 0), ==, -60);
    g_assert_cmpfloat(luma_audio_meter_get_peak_db(m, 0), ==, -60);
  }
  /* Linear amplitudes; 0 dB latches the clip until reset. */
  g_assert_false(luma_audio_meter_get_clipped(m));
  luma_audio_meter_set_levels(m, 1.0, 0.5);
  g_assert_true(luma_audio_meter_get_clipped(m));
  g_assert_true(gtk_widget_has_css_class(meter, "clipping"));
  g_assert_cmpfloat(fabs(luma_audio_meter_get_level_db(m, 1) - -6.0206), <, 0.001);
  luma_audio_meter_set_levels(m, 0, 0);
  g_assert_true(luma_audio_meter_get_clipped(m));
  luma_audio_meter_reset_clip(m);
  g_assert_false(gtk_widget_has_css_class(meter, "clipping"));
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  gtk_init();
  g_test_add_func("/lumaui/audio-meter", test_meter);
  return g_test_run();
}
