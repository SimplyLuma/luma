/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

static GtkWidget *child_at(GtkWidget *parent, guint index) {
  GtkWidget *child = gtk_widget_get_first_child(parent);
  while (index-- && child != NULL)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static void boolean_signal(LumaMediaTransportLcd *transport G_GNUC_UNUSED,
                           gboolean value, gpointer data) {
  gboolean *observed = data;
  *observed = value;
}

static void count_signal(LumaMediaTransportLcd *transport G_GNUC_UNUSED, gpointer data) {
  (*(int *)data)++;
}

static void readout_signal(LumaMediaTransportLcd *transport G_GNUC_UNUSED,
                           const char *key, gpointer data) {
  g_strlcpy(data, key, 32);
}

static void test_lcd_controls(void) {
  GtkWidget *widget = g_object_ref_sink(luma_media_transport_lcd_new());
  LumaMediaTransportLcd *transport = LUMA_MEDIA_TRANSPORT_LCD(widget);
  GtkWidget *keys = child_at(widget, 0);
  GtkWidget *lcd = child_at(widget, 1);
  GtkWidget *start = child_at(keys, 0);
  GtkWidget *play = child_at(keys, 1);
  GtkWidget *record = child_at(keys, 2);
  GtkWidget *tempo = child_at(widget, 2);
  GtkWidget *loop = child_at(widget, 6);
  gboolean playing = FALSE, recording = FALSE, looping = FALSE;
  int seeks = 0;
  char readout[32] = "";
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-media-transport"));
  g_assert_true(gtk_widget_has_css_class(lcd, "lumaui-media-lcd"));

  g_signal_connect(transport, "play-changed", G_CALLBACK(boolean_signal), &playing);
  g_signal_connect(transport, "record-changed", G_CALLBACK(boolean_signal), &recording);
  g_signal_connect(transport, "loop-changed", G_CALLBACK(boolean_signal), &looping);
  g_signal_connect(transport, "seek-start", G_CALLBACK(count_signal), &seeks);
  g_signal_connect(transport, "readout-activated", G_CALLBACK(readout_signal), readout);
  luma_media_transport_lcd_set_lcd(transport, "12.3.1", "0:21.4");
  luma_media_transport_lcd_set_readout(transport, "tempo", "112");
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(child_at(child_at(child_at(lcd, 0), 0), 0))), ==, "12.3.1");
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(child_at(child_at(child_at(lcd, 1), 0), 0))), ==, "0:21.4");
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(child_at(child_at(tempo, 0), 0))), ==, "112");

  g_signal_emit_by_name(start, "clicked");
  g_signal_emit_by_name(play, "clicked");
  g_signal_emit_by_name(record, "clicked");
  g_signal_emit_by_name(tempo, "clicked");
  g_signal_emit_by_name(loop, "clicked");
  g_assert_cmpint(seeks, ==, 1);
  g_assert_true(playing);
  g_assert_true(recording);
  g_assert_true(looping);
  g_assert_cmpstr(readout, ==, "tempo");
  g_assert_true(gtk_widget_has_css_class(record, "on"));
  g_assert_true(gtk_widget_has_css_class(loop, "on"));

  luma_media_transport_lcd_set_playing(transport, FALSE);
  luma_media_transport_lcd_set_recording(transport, FALSE);
  luma_media_transport_lcd_set_looping(transport, FALSE);
  g_assert_cmpstr(gtk_widget_get_tooltip_text(play), ==, "Play");
  g_assert_false(gtk_widget_has_css_class(record, "on"));
  g_assert_false(gtk_widget_has_css_class(loop, "on"));
  g_object_unref(widget);
}

static void pump(void) {
  gint64 end = g_get_monotonic_time() + 300 * G_TIME_SPAN_MILLISECOND;
  while (g_get_monotonic_time() < end) {
    while (g_main_context_iteration(NULL, FALSE)) {}
    g_usleep(1000);
  }
}

static void test_phone_layout(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *transport = luma_media_transport_lcd_new();
  gtk_window_set_child(GTK_WINDOW(window), transport);
  gtk_window_set_default_size(GTK_WINDOW(window), 390, 120);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  g_assert_cmpint(gtk_widget_get_width(window), <=, 600);
  g_assert_true(gtk_widget_has_css_class(transport, "phone"));
  g_assert_false(gtk_widget_get_visible(child_at(transport, 2)));
  g_assert_true(gtk_widget_get_visible(child_at(transport, 1)));
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  gtk_test_init(&argc, &argv);
  luma_init();
  g_test_add_func("/media-transport/lcd-controls", test_lcd_controls);
  g_test_add_func("/media-transport/phone-layout", test_phone_layout);
  return g_test_run();
}
