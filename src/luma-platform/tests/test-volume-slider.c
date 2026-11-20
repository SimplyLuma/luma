/* SPDX-License-Identifier: Apache-2.0 */
/* LumaVolumeSlider: geometry against the tokens, the meter on the track's
 * length, keys, ::moved only for the person's moves, unity and mute. */
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

static void count_moves(LumaVolumeSlider *slider G_GNUC_UNUSED, double value G_GNUC_UNUSED, int *moves) { (*moves)++; }

static gboolean press(GtkWidget *widget, guint keyval) {
  GListModel *list = gtk_widget_observe_controllers(widget);
  gboolean handled = FALSE;
  for (guint i = 0; i < g_list_model_get_n_items(list); i++) {
    g_autoptr(GObject) item = g_list_model_get_item(list, i);
    if (GTK_IS_EVENT_CONTROLLER_KEY(item))
      g_signal_emit_by_name(item, "key-pressed", keyval, 0, 0, &handled);
  }
  g_object_unref(list);
  return handled;
}

static void test_upright(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *widget = luma_volume_slider_new(GTK_ORIENTATION_VERTICAL, 0, 100);
  LumaVolumeSlider *s = LUMA_VOLUME_SLIDER(widget);
  gtk_window_set_child(GTK_WINDOW(window), widget);
  gtk_widget_set_halign(widget, GTK_ALIGN_CENTER);
  gtk_window_present(GTK_WINDOW(window));
  wait_ms(100);
  g_assert_true(gtk_widget_has_css_class(widget, "vertical"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(widget)), ==, GTK_ACCESSIBLE_ROLE_SLIDER);

  /* No meter: the 24 px hit width, 160 px long. */
  int min, nat;
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, 24);
  gtk_widget_measure(widget, GTK_ORIENTATION_VERTICAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(min, ==, 48);
  g_assert_cmpint(nat, ==, 160);

  /* A mono meter: knob 14 + gap 4 + bar 4 = 22, held to the 24 px hit. */
  luma_volume_slider_set_meter_channels(s, 1);
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, 24);
  luma_volume_slider_set_meter_channels(s, 2);
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, 28);
  gtk_widget_set_size_request(widget, -1, 200);
  wait_ms(100);

  /* The meter spans the track's length exactly, beside it. */
  graphene_rect_t track, meter;
  luma_volume_slider_get_track_bounds(s, &track);
  g_assert_cmpfloat(track.size.width, ==, 4);
  g_assert_cmpfloat(track.origin.y, ==, 5);
  g_assert_cmpfloat(track.size.height, ==, 200 - 14 + 4);
  LumaAudioMeter *m = luma_volume_slider_get_meter(s);
  g_assert_nonnull(m);
  g_assert_false(luma_audio_meter_get_horizontal(m));
  g_assert_true(gtk_widget_compute_bounds(GTK_WIDGET(m), widget, &meter));
  g_assert_cmpfloat(meter.origin.y, ==, track.origin.y);
  g_assert_cmpfloat(meter.size.height, ==, track.size.height);
  g_assert_cmpfloat(meter.origin.x, ==, track.origin.x + 4 / 2.0 + 7 + 4);

  /* Keys move it and say so; set_value does not. */
  int moves = 0;
  g_signal_connect(s, "moved", G_CALLBACK(count_moves), &moves);
  luma_volume_slider_set_value(s, 50);
  g_assert_cmpint(moves, ==, 0);
  g_assert_true(press(widget, GDK_KEY_Up));
  g_assert_cmpfloat(luma_volume_slider_get_value(s), ==, 51);
  g_assert_false(press(widget, GDK_KEY_Left));
  g_assert_true(press(widget, GDK_KEY_Page_Down));
  g_assert_cmpfloat(luma_volume_slider_get_value(s), ==, 41);
  g_assert_true(press(widget, GDK_KEY_End));
  g_assert_cmpfloat(luma_volume_slider_get_value(s), ==, 100);
  g_assert_true(press(widget, GDK_KEY_End));
  g_assert_cmpint(moves, ==, 3);

  /* A narrower range clamps without a move. */
  luma_volume_slider_set_range(s, 0, 80);
  g_assert_cmpfloat(luma_volume_slider_get_value(s), ==, 80);
  g_assert_cmpint(moves, ==, 3);

  /* Muted keeps the value, quiets the meter. */
  luma_audio_meter_set_levels_db(m, -6, -6);
  luma_volume_slider_set_muted(s, TRUE);
  g_assert_true(gtk_widget_has_css_class(widget, "muted"));
  g_assert_cmpfloat(luma_volume_slider_get_value(s), ==, 80);
  luma_volume_slider_set_meter_channels(s, 0);
  g_assert_null(luma_volume_slider_get_meter(s));
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_flat(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *widget = luma_volume_slider_new(GTK_ORIENTATION_HORIZONTAL, -60, 6);
  LumaVolumeSlider *s = LUMA_VOLUME_SLIDER(widget);
  luma_volume_slider_set_unity(s, 0);
  luma_volume_slider_set_unit(s, "dB");
  luma_volume_slider_set_meter_channels(s, 2);
  gtk_widget_set_size_request(widget, 300, -1);
  gtk_window_set_child(GTK_WINDOW(window), widget);
  gtk_widget_set_valign(widget, GTK_ALIGN_CENTER);
  gtk_window_present(GTK_WINDOW(window));
  wait_ms(100);
  int min, nat;
  gtk_widget_measure(widget, GTK_ORIENTATION_VERTICAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, 28);
  graphene_rect_t track, meter;
  luma_volume_slider_get_track_bounds(s, &track);
  g_assert_cmpfloat(track.origin.x, ==, 5);
  g_assert_cmpfloat(track.size.width, ==, 300 - 14 + 4);
  LumaAudioMeter *m = luma_volume_slider_get_meter(s);
  g_assert_true(luma_audio_meter_get_horizontal(m));
  g_assert_true(gtk_widget_compute_bounds(GTK_WIDGET(m), widget, &meter));
  g_assert_cmpfloat(meter.origin.x, ==, track.origin.x);
  g_assert_cmpfloat(meter.size.width, ==, track.size.width);
  g_assert_cmpfloat(meter.origin.y, ==, 14 + 4);
  g_assert_cmpfloat(meter.size.height, ==, 10);
  luma_volume_slider_set_value(s, -12);
  g_assert_true(press(widget, GDK_KEY_Right));
  g_assert_cmpfloat(luma_volume_slider_get_value(s), ==, -11);
  /* Across a flat slider, arrows are left for focus. */
  g_assert_false(press(widget, GDK_KEY_Up));
  g_assert_true(press(widget, GDK_KEY_Home));
  g_assert_cmpfloat(luma_volume_slider_get_value(s), ==, -60);
  g_assert_cmpfloat(luma_volume_slider_get_unity(s), ==, 0);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  gtk_init();
  g_test_add_func("/lumaui/volume-slider/upright", test_upright);
  g_test_add_func("/lumaui/volume-slider/flat", test_flat);
  return g_test_run();
}
