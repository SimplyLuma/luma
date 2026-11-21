/* SPDX-License-Identifier: Apache-2.0 */
/* LumaAudioMeter: levels, ballistics, clipping, segments and the quiet accessible updates. */
#include "luma-ui.h"
#include "luma-ui-tokens-private.h"

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

static void measure(GtkWidget *widget, GtkOrientation orientation, int *min, int *nat) {
  gtk_widget_measure(widget, orientation, -1, min, nat, NULL, NULL);
}

static void test_segments(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *meter = luma_audio_meter_new(2);
  LumaAudioMeter *m = LUMA_AUDIO_METER(meter);
  gtk_window_set_child(GTK_WINDOW(window), meter);
  gtk_window_present(GTK_WINDOW(window));
  wait_ms(100);
  /* Continuous by default: no segments, none lit, no peak segment. */
  g_assert_cmpuint(luma_audio_meter_get_segments(m), ==, 0);
  g_assert_false(gtk_widget_has_css_class(meter, "segmented"));
  luma_audio_meter_set_levels_db(m, -6, -40);
  g_assert_cmpuint(luma_audio_meter_get_lit_segments(m, 0), ==, 0);
  g_assert_cmpint(luma_audio_meter_get_peak_segment(m, 0), ==, -1);

  /* 24 segments: 3 dB each from -60 to +12; one lights half way into it. */
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, -100), ==, 0);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, -59), ==, 0);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, -57), ==, 1);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, -18), ==, 14);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, -6), ==, 18);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, 0), ==, 20);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, 12), ==, 24);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, 40), ==, 24);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(24, NAN), ==, 0);
  g_assert_cmpuint(luma_audio_meter_segments_lit_at(0, 0), ==, 0);

  g_object_set(meter, "segments", 24, NULL);
  guint segments = 0;
  g_object_get(meter, "segments", &segments, NULL);
  g_assert_cmpuint(segments, ==, 24);
  g_assert_true(gtk_widget_has_css_class(meter, "segmented"));
  /* The level already shown now reads in segments. */
  g_assert_cmpuint(luma_audio_meter_get_lit_segments(m, 0), ==, 18);
  g_assert_cmpint(luma_audio_meter_get_peak_segment(m, 0), ==, 17);
  luma_audio_meter_set_levels_db(m, 0, -18);
  g_assert_cmpuint(luma_audio_meter_get_lit_segments(m, 0), ==, 20);
  g_assert_cmpuint(luma_audio_meter_get_lit_segments(m, 1), ==, 14);
  g_assert_cmpint(luma_audio_meter_get_peak_segment(m, 0), ==, 19);
  g_assert_cmpint(luma_audio_meter_get_peak_segment(m, 1), ==, 13);
  luma_audio_meter_set_levels_db(m, -30, -30);
  if (!luma_ui_reduced_motion()) {
    /* The bar falls; the peak's segment holds above it, lit on its own. */
    wait_ms(300);
    guint lit = luma_audio_meter_get_lit_segments(m, 0);
    g_assert_cmpuint(lit, <, 20);
    g_assert_cmpuint(lit, >, 10);
    g_assert_cmpint(luma_audio_meter_get_peak_segment(m, 0), ==, 19);
    wait_ms(3500);
    g_assert_cmpuint(luma_audio_meter_get_lit_segments(m, 0), ==, 10);
    g_assert_cmpint(luma_audio_meter_get_peak_segment(m, 0), ==, 9);
  } else {
    g_assert_cmpuint(luma_audio_meter_get_lit_segments(m, 0), ==, 10);
    g_assert_cmpint(luma_audio_meter_get_peak_segment(m, 0), ==, 9);
  }
  /* A segmented bar's own shape: its thickness is the bar's, and unset values are the tokens'. */
  GtkWidget *shaped = luma_audio_meter_new(1);
  LumaAudioMeter *s = LUMA_AUDIO_METER(shaped);
  luma_audio_meter_set_horizontal(s, TRUE);
  luma_audio_meter_set_segments(s, 32);
  double thickness, gap, radius;
  luma_audio_meter_get_segment_shape(s, &thickness, &gap, &radius);
  g_assert_cmpfloat(thickness, ==, 4);
  g_assert_cmpfloat(gap, ==, 1);
  g_assert_cmpfloat(radius, <, 0);
  int minimum, natural;
  gtk_widget_measure(shaped, GTK_ORIENTATION_VERTICAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(natural, ==, 4);
  luma_audio_meter_set_segment_shape(s, 5, 3, 2);
  luma_audio_meter_get_segment_shape(s, &thickness, &gap, &radius);
  g_assert_cmpfloat(thickness, ==, 5);
  g_assert_cmpfloat(gap, ==, 3);
  g_assert_cmpfloat(radius, ==, 2);
  gtk_widget_measure(shaped, GTK_ORIENTATION_VERTICAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(natural, ==, 5);
  /* Every segment keeps its shortest length and its gap. */
  gtk_widget_measure(shaped, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(minimum, ==, 32 * 2 + 31 * 3);
  /* An output's meter has no zones and holds no peak. */
  g_assert_true(luma_audio_meter_get_zones(s));
  g_assert_false(gtk_widget_has_css_class(shaped, "plain"));
  luma_audio_meter_set_levels_db(s, 0, 0);
  g_assert_cmpint(luma_audio_meter_get_peak_segment(s, 0), ==, 26);
  luma_audio_meter_set_zones(s, FALSE);
  g_assert_false(luma_audio_meter_get_zones(s));
  g_assert_true(gtk_widget_has_css_class(shaped, "plain"));
  g_assert_cmpuint(luma_audio_meter_get_lit_segments(s, 0), ==, 27);
  g_assert_cmpint(luma_audio_meter_get_peak_segment(s, 0), ==, -1);
  g_object_ref_sink(shaped);
  g_object_unref(shaped);

  /* Silence, and a level that is not a number, light nothing on a fresh meter. */
  GtkWidget *quiet = luma_audio_meter_new(1);
  luma_audio_meter_set_segments(LUMA_AUDIO_METER(quiet), 32);
  luma_audio_meter_set_levels_db(LUMA_AUDIO_METER(quiet), -INFINITY, -INFINITY);
  g_assert_cmpuint(luma_audio_meter_get_lit_segments(LUMA_AUDIO_METER(quiet), 0), ==, 0);
  g_assert_cmpint(luma_audio_meter_get_peak_segment(LUMA_AUDIO_METER(quiet), 0), ==, -1);
  luma_audio_meter_set_levels_db(LUMA_AUDIO_METER(quiet), NAN, NAN);
  g_assert_cmpfloat(luma_audio_meter_get_level_db(LUMA_AUDIO_METER(quiet), 0), ==, -60);
  g_object_ref_sink(quiet);
  g_object_unref(quiet);

  /* Sizes: across stays the bars; along fits every segment at its shortest. */
  int min, nat;
  const int fit28 = 28 * LUMA_UI_METER_SEGMENT_MIN + 27 * LUMA_UI_METER_SEGMENT_GAP;
  const int fit64 = 64 * LUMA_UI_METER_SEGMENT_MIN + 63 * LUMA_UI_METER_SEGMENT_GAP;
  const int across = 2 * LUMA_UI_METER_BAR + LUMA_UI_METER_GAP;
  luma_audio_meter_set_segments(m, 28);
  measure(meter, GTK_ORIENTATION_HORIZONTAL, &min, &nat);
  g_assert_cmpint(nat, ==, across);
  g_assert_cmpint(across, ==, 10);
  measure(meter, GTK_ORIENTATION_VERTICAL, &min, &nat);
  g_assert_cmpint(min, ==, MAX(LUMA_UI_METER_MIN_LENGTH, fit28));
  g_assert_cmpint(min, ==, 83);
  g_assert_cmpint(nat, ==, LUMA_UI_METER_LENGTH);
  luma_audio_meter_set_horizontal(m, TRUE);
  measure(meter, GTK_ORIENTATION_VERTICAL, &min, &nat);
  g_assert_cmpint(nat, ==, across);
  measure(meter, GTK_ORIENTATION_HORIZONTAL, &min, &nat);
  g_assert_cmpint(min, ==, 83);
  g_assert_cmpint(nat, ==, LUMA_UI_METER_LENGTH);
  luma_audio_meter_set_segments(m, 64);
  measure(meter, GTK_ORIENTATION_HORIZONTAL, &min, &nat);
  g_assert_cmpint(min, ==, fit64);
  g_assert_cmpint(nat, ==, fit64);
  luma_audio_meter_set_segments(m, 0);
  g_assert_false(gtk_widget_has_css_class(meter, "segmented"));
  measure(meter, GTK_ORIENTATION_HORIZONTAL, &min, &nat);
  g_assert_cmpint(min, ==, LUMA_UI_METER_MIN_LENGTH);
  g_assert_cmpint(nat, ==, LUMA_UI_METER_LENGTH);
  /* Drawn segmented, both ways, at a size between its minimum and natural. */
  luma_audio_meter_set_segments(m, 32);
  luma_audio_meter_set_levels_db(m, -3, -40);
  gtk_window_set_default_size(GTK_WINDOW(window), 100, 40);
  wait_ms(100);
  luma_audio_meter_set_horizontal(m, FALSE);
  wait_ms(100);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  gtk_init();
  g_test_add_func("/lumaui/audio-meter", test_meter);
  g_test_add_func("/lumaui/audio-meter/segments", test_segments);
  return g_test_run();
}
