/* SPDX-License-Identifier: Apache-2.0 */
/* LumaMixerStrip and LumaMixerRow: the fixed width against the token, no
 * clipping in a narrow panel, keys, the double click to 0 dB, the wheel,
 * mute and monitor, accessibility, RTL and reduced motion. */
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

static gpointer controller_of(GtkWidget *widget, GType type) {
  GListModel *list = gtk_widget_observe_controllers(widget);
  gpointer found = NULL;
  for (guint i = 0; i < g_list_model_get_n_items(list) && !found; i++) {
    g_autoptr(GObject) item = g_list_model_get_item(list, i);
    if (G_TYPE_CHECK_INSTANCE_TYPE(item, type))
      found = item;
  }
  g_object_unref(list);
  return found;
}

static gboolean press(GtkWidget *widget, guint keyval) {
  gboolean handled = FALSE;
  g_signal_emit_by_name(controller_of(widget, GTK_TYPE_EVENT_CONTROLLER_KEY), "key-pressed", keyval, 0, 0, &handled);
  return handled;
}

/* A key as GTK delivers it: the fader's own handler first, then the strip's. */
static gboolean press_in(GtkWidget *focus, GtkWidget *outer, guint keyval) {
  for (GtkWidget *w = focus; w != NULL; w = gtk_widget_get_parent(w)) {
    if (controller_of(w, GTK_TYPE_EVENT_CONTROLLER_KEY) && press(w, keyval))
      return TRUE;
    if (w == outer)
      break;
  }
  return FALSE;
}

static void count_double(gpointer object G_GNUC_UNUSED, double value G_GNUC_UNUSED, int *n) { (*n)++; }
static void count_bool(gpointer object G_GNUC_UNUSED, gboolean value G_GNUC_UNUSED, int *n) { (*n)++; }

static GtkWidget *present(GtkWidget *child, int width, int height) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), width, height);
  gtk_window_set_child(GTK_WINDOW(window), child);
  gtk_window_present(GTK_WINDOW(window));
  wait_ms(150);
  return window;
}

static void test_strip(void) {
  GtkWidget *widget = luma_mixer_strip_new("Desktop audio", "Built-in speakers");
  LumaMixerStrip *strip = LUMA_MIXER_STRIP(widget);
  gtk_widget_set_halign(widget, GTK_ALIGN_START);
  GtkWidget *window = present(widget, 400, 420);
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-mixer-strip"));

  /* The token's width, padding included, whatever the name asks. */
  int min, nat;
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(min, ==, LUMA_UI_MIXER_STRIP_WIDTH);
  g_assert_cmpint(nat, ==, LUMA_UI_MIXER_STRIP_WIDTH);
  graphene_rect_t box;
  g_assert_true(gtk_widget_compute_bounds(widget, widget, &box));
  g_assert_cmpfloat(box.size.width, ==, LUMA_UI_MIXER_STRIP_WIDTH);
  luma_mixer_strip_set_label(strip, "A very long channel name that never fits a strip");
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, LUMA_UI_MIXER_STRIP_WIDTH);
  luma_mixer_strip_set_label(strip, "Desktop audio");

  /* Defaults: 0 dB on −60 to +6, unity at 0, a stereo meter. */
  double lower, upper;
  luma_mixer_strip_get_range(strip, &lower, &upper);
  g_assert_cmpfloat(lower, ==, -60);
  g_assert_cmpfloat(upper, ==, 6);
  LumaVolumeSlider *fader = luma_mixer_strip_get_fader(strip);
  g_assert_cmpfloat(luma_volume_slider_get_unity(fader), ==, 0);
  g_assert_cmpint(luma_audio_meter_get_channels(luma_mixer_strip_get_meter(strip)), ==, 2);
  /* The fader fills the strip's height; the scale stands beside it. */
  graphene_rect_t fader_box, scale_box;
  GtkWidget *scale = gtk_widget_get_prev_sibling(GTK_WIDGET(fader));
  g_assert_true(gtk_widget_has_css_class(scale, "lumaui-mixer-scale"));
  g_assert_true(gtk_widget_compute_bounds(GTK_WIDGET(fader), widget, &fader_box));
  g_assert_true(gtk_widget_compute_bounds(scale, widget, &scale_box));
  g_assert_cmpfloat(scale_box.size.width, ==, LUMA_UI_MIXER_STRIP_SCALE_WIDTH);
  g_assert_cmpfloat(scale_box.origin.x, <, fader_box.origin.x);
  g_assert_cmpfloat(fader_box.size.height, >, LUMA_UI_VOLUME_SLIDER_LENGTH);
  g_assert_cmpfloat(fader_box.origin.x + fader_box.size.width, <=, LUMA_UI_MIXER_STRIP_WIDTH);

  int moves = 0, mutes = 0, monitors = 0;
  g_signal_connect(strip, "moved", G_CALLBACK(count_double), &moves);
  g_signal_connect(strip, "mute-toggled", G_CALLBACK(count_bool), &mutes);
  g_signal_connect(strip, "monitor-toggled", G_CALLBACK(count_bool), &monitors);

  /* Keys: Up and Down step 1 dB, Page 6, End the top; set_db is silent. */
  luma_mixer_strip_set_db(strip, -12);
  g_assert_cmpint(moves, ==, 0);
  GtkWidget *readout = gtk_widget_get_next_sibling(gtk_widget_get_parent(GTK_WIDGET(fader)));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(readout)), ==, "−12.0 dB");
  g_assert_true(press_in(GTK_WIDGET(fader), widget, GDK_KEY_Up));
  g_assert_cmpfloat(luma_mixer_strip_get_db(strip), ==, -11);
  g_assert_true(press_in(GTK_WIDGET(fader), widget, GDK_KEY_Page_Down));
  g_assert_cmpfloat(luma_mixer_strip_get_db(strip), ==, -17);
  g_assert_true(press_in(GTK_WIDGET(fader), widget, GDK_KEY_End));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(readout)), ==, "+6.0 dB");
  g_assert_true(press_in(GTK_WIDGET(fader), widget, GDK_KEY_Home));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(readout)), ==, "−∞ dB");
  g_assert_cmpint(moves, ==, 4);

  /* The wheel steps; a sideways scroll is left for the row. */
  luma_mixer_strip_set_db(strip, -10);
  GtkEventController *wheel = controller_of(GTK_WIDGET(fader), GTK_TYPE_EVENT_CONTROLLER_SCROLL);
  gboolean handled = FALSE;
  g_signal_emit_by_name(wheel, "scroll", 0.0, -1.0, &handled);
  g_assert_true(handled);
  g_assert_cmpfloat(luma_mixer_strip_get_db(strip), ==, -9);
  handled = TRUE;
  g_signal_emit_by_name(wheel, "scroll", 2.0, 0.0, &handled);
  g_assert_false(handled);
  g_assert_cmpfloat(luma_mixer_strip_get_db(strip), ==, -9);

  /* A double click returns to 0 dB. */
  GtkGesture *drag = controller_of(GTK_WIDGET(fader), GTK_TYPE_GESTURE_DRAG);
  double y = gtk_widget_get_height(GTK_WIDGET(fader)) - 20;
  g_signal_emit_by_name(drag, "drag-begin", 10.0, y);
  g_assert_cmpfloat(luma_mixer_strip_get_db(strip), !=, 0);
  g_signal_emit_by_name(drag, "drag-begin", 10.0, y);
  g_assert_cmpfloat(luma_mixer_strip_get_db(strip), ==, 0);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(readout)), ==, "0.0 dB");

  /* M mutes from the fader or a key; the Mute key shows it; set_muted is silent. */
  GtkWidget *mute = luma_mixer_strip_get_mute_button(strip);
  GtkWidget *monitor = luma_mixer_strip_get_monitor_button(strip);
  g_assert_true(press_in(GTK_WIDGET(fader), widget, GDK_KEY_m));
  g_assert_true(luma_mixer_strip_get_muted(strip));
  g_assert_true(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(mute)));
  g_assert_true(luma_volume_slider_get_muted(fader));
  g_assert_true(gtk_widget_has_css_class(widget, "muted"));
  gtk_test_accessible_assert_state(GTK_ACCESSIBLE(mute), GTK_ACCESSIBLE_STATE_PRESSED, GTK_ACCESSIBLE_TRISTATE_TRUE);
  g_assert_true(press_in(monitor, widget, GDK_KEY_M));
  g_assert_false(luma_mixer_strip_get_muted(strip));
  g_assert_cmpint(mutes, ==, 2);
  g_assert_false(press_in(GTK_WIDGET(fader), widget, GDK_KEY_x));
  g_signal_emit_by_name(mute, "clicked");
  g_assert_true(luma_mixer_strip_get_muted(strip));
  g_assert_cmpint(mutes, ==, 3);
  luma_mixer_strip_set_muted(strip, FALSE);
  g_assert_false(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(mute)));
  g_assert_false(gtk_widget_has_css_class(widget, "muted"));
  g_assert_cmpint(mutes, ==, 3);

  /* Monitor: the person's toggle is heard, the setter is not. */
  g_signal_emit_by_name(monitor, "clicked");
  g_assert_true(luma_mixer_strip_get_monitoring(strip));
  g_assert_true(gtk_widget_has_css_class(widget, "monitoring"));
  luma_mixer_strip_set_monitoring(strip, FALSE);
  g_assert_false(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(monitor)));
  g_assert_cmpint(monitors, ==, 1);

  /* Accessibility: a named group; every control says which channel it is. */
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(widget)), ==, GTK_ACCESSIBLE_ROLE_GROUP);
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_LABEL, "Desktop audio");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION,
                                      "Built-in speakers");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(fader), GTK_ACCESSIBLE_PROPERTY_LABEL, "Desktop audio volume");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(fader), GTK_ACCESSIBLE_PROPERTY_VALUE_TEXT, "0 dB");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(luma_mixer_strip_get_meter(strip)),
                                      GTK_ACCESSIBLE_PROPERTY_LABEL, "Desktop audio level");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(mute), GTK_ACCESSIBLE_PROPERTY_LABEL, "Mute Desktop audio");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(monitor), GTK_ACCESSIBLE_PROPERTY_LABEL,
                                      "Monitor Desktop audio");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(mute), GTK_ACCESSIBLE_PROPERTY_KEY_SHORTCUTS, "M");
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(fader)), ==, GTK_ACCESSIBLE_ROLE_SLIDER);
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(mute)), ==, GTK_ACCESSIBLE_ROLE_TOGGLE_BUTTON);
  g_assert_true(gtk_widget_get_focusable(GTK_WIDGET(fader)));
  /* The full name is the name's tooltip; without a device its line stays, empty, so faders line up. */
  /* The head: an icon slot (empty, taking no room until set), the name, the device. */
  GtkWidget *icon = gtk_widget_get_first_child(gtk_widget_get_first_child(gtk_widget_get_first_child(widget)));
  g_assert_true(gtk_widget_has_css_class(icon, "lumaui-mixer-icon") && !gtk_widget_get_visible(icon));
  luma_mixer_strip_set_icon_name(strip, "mic");
  g_assert_true(gtk_widget_get_visible(icon));
  g_assert_cmpstr(g_themed_icon_get_names(G_THEMED_ICON(luma_mixer_strip_get_gicon(strip)))[0], ==, "lumaui-mic-symbolic");
  luma_mixer_strip_set_icon_name(strip, NULL);
  g_assert_false(gtk_widget_get_visible(icon));
  GtkWidget *name = gtk_widget_get_next_sibling(icon);
  g_assert_true(gtk_widget_has_css_class(name, "lumaui-mixer-name"));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(name), ==, "Desktop audio");
  g_assert_true(gtk_label_get_ellipsize(GTK_LABEL(name)) == PANGO_ELLIPSIZE_END);
  int with_device, without_device;
  gtk_widget_measure(widget, GTK_ORIENTATION_VERTICAL, -1, &with_device, &nat, NULL, NULL);
  luma_mixer_strip_set_subtitle(strip, NULL);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(gtk_widget_get_next_sibling(name))), ==, "");
  gtk_widget_measure(widget, GTK_ORIENTATION_VERTICAL, -1, &without_device, &nat, NULL, NULL);
  g_assert_cmpint(with_device, ==, without_device);

  /* Levels: linear or dB, as the meter takes them. */
  luma_mixer_strip_set_levels(strip, 0.5, 0.25);
  g_assert_cmpfloat(fabs(luma_audio_meter_get_level_db(luma_mixer_strip_get_meter(strip), 0) - -6.0206), <, 0.001);
  luma_mixer_strip_set_levels_db(strip, -20, -30);
  g_assert_cmpfloat(luma_audio_meter_get_peak_db(luma_mixer_strip_get_meter(strip), 1), >=, -30);

  /* Another range keeps unity where it is in range. */
  luma_mixer_strip_set_range(strip, -40, 12);
  luma_mixer_strip_get_range(strip, &lower, &upper);
  g_assert_cmpfloat(upper, ==, 12);
  g_assert_cmpfloat(luma_volume_slider_get_unity(fader), ==, 0);
  luma_mixer_strip_set_range(strip, -60, 6);

  /* RTL mirrors the keys, not the instrument: the scale stays beside the knob, away from the meter. */
  graphene_rect_t mute_box, monitor_box;
  g_assert_true(gtk_widget_compute_bounds(mute, widget, &mute_box));
  g_assert_true(gtk_widget_compute_bounds(monitor, widget, &monitor_box));
  g_assert_cmpfloat(mute_box.origin.x, <, monitor_box.origin.x);
  gtk_widget_set_default_direction(GTK_TEXT_DIR_RTL);
  wait_ms(100);
  g_assert_true(gtk_widget_compute_bounds(GTK_WIDGET(fader), widget, &fader_box));
  g_assert_true(gtk_widget_compute_bounds(scale, widget, &scale_box));
  g_assert_cmpfloat(scale_box.origin.x, <, fader_box.origin.x);
  g_assert_true(gtk_widget_compute_bounds(mute, widget, &mute_box));
  g_assert_true(gtk_widget_compute_bounds(monitor, widget, &monitor_box));
  g_assert_cmpfloat(mute_box.origin.x, >, monitor_box.origin.x);
  gtk_widget_set_default_direction(GTK_TEXT_DIR_LTR);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_row(void) {
  GtkWidget *row = luma_mixer_row_new();
  LumaMixerRow *r = LUMA_MIXER_ROW(row);
  const char *names[] = {"Mic", "Desktop audio", "Music", "Game capture", "Browser", "Alerts"};
  for (guint i = 0; i < G_N_ELEMENTS(names); i++)
    luma_mixer_row_append(r, LUMA_MIXER_STRIP(luma_mixer_strip_new(names[i], "Device")));
  g_assert_cmpuint(luma_mixer_row_get_n_strips(r), ==, 6);
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(row)), ==, GTK_ACCESSIBLE_ROLE_GROUP);
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(row), GTK_ACCESSIBLE_PROPERTY_LABEL, "Mixer");

  /* A row is never narrower than one strip, nor shorter than its strips. */
  int min_w, nat_w, min_h, nat_h, strip_min, strip_nat;
  gtk_widget_measure(row, GTK_ORIENTATION_HORIZONTAL, -1, &min_w, &nat_w, NULL, NULL);
  g_assert_cmpint(min_w, >=, LUMA_UI_MIXER_STRIP_WIDTH);
  g_assert_cmpint(min_w, <, 2 * LUMA_UI_MIXER_STRIP_WIDTH);
  g_assert_cmpint(nat_w, ==, 6 * LUMA_UI_MIXER_STRIP_WIDTH + 5 * LUMA_UI_MIXER_STRIP_ROW_GAP + 2 * LUMA_UI_MIXER_STRIP_ROW_PADDING);
  gtk_widget_measure(row, GTK_ORIENTATION_VERTICAL, -1, &min_h, &nat_h, NULL, NULL);
  gtk_widget_measure(GTK_WIDGET(luma_mixer_row_get_strip(r, 0)), GTK_ORIENTATION_VERTICAL, -1, &strip_min,
                     &strip_nat, NULL, NULL);
  g_assert_cmpint(min_h, >=, strip_min);

  /* A narrow panel: the row stays inside it and scrolls; every strip is whole. */
  GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_size_request(panel, 240, -1);
  gtk_widget_set_halign(panel, GTK_ALIGN_START);
  gtk_widget_set_overflow(panel, GTK_OVERFLOW_HIDDEN);
  gtk_box_append(GTK_BOX(panel), row);
  GtkWidget *window = present(panel, 240, 200);
  g_assert_cmpint(gtk_widget_get_width(row), <=, gtk_widget_get_width(panel));
  g_assert_cmpint(gtk_widget_get_height(row), >=, strip_min);
  for (guint i = 0; i < 6; i++) {
    GtkWidget *strip = GTK_WIDGET(luma_mixer_row_get_strip(r, i));
    graphene_rect_t box;
    g_assert_true(gtk_widget_compute_bounds(strip, row, &box));
    g_assert_cmpfloat(box.size.width, ==, LUMA_UI_MIXER_STRIP_WIDTH);
    g_assert_cmpfloat(box.size.height, >=, strip_min);
    g_assert_cmpfloat(box.origin.y, >=, 0);
    g_assert_cmpfloat(box.origin.y + box.size.height, <=, gtk_widget_get_height(row));
  }
  GtkWidget *scroller = gtk_widget_get_first_child(row);
  GtkAdjustment *h = gtk_scrolled_window_get_hadjustment(GTK_SCROLLED_WINDOW(scroller));
  g_assert_cmpfloat(gtk_adjustment_get_upper(h), >, gtk_adjustment_get_page_size(h));

  /* Left and Right walk the faders, mirrored in RTL. */
  GtkWidget *first = GTK_WIDGET(luma_mixer_strip_get_fader(luma_mixer_row_get_strip(r, 0)));
  GtkWidget *second = GTK_WIDGET(luma_mixer_strip_get_fader(luma_mixer_row_get_strip(r, 1)));
  gtk_widget_grab_focus(first);
  g_assert_true(press(row, GDK_KEY_Right));
  g_assert_true(gtk_root_get_focus(gtk_widget_get_root(second)) == second);
  g_assert_true(press(row, GDK_KEY_Left));
  g_assert_true(gtk_root_get_focus(gtk_widget_get_root(first)) == first);
  g_assert_true(press(row, GDK_KEY_Left));
  g_assert_true(gtk_root_get_focus(gtk_widget_get_root(first)) == first);
  gtk_widget_set_default_direction(GTK_TEXT_DIR_RTL);
  g_assert_true(press(row, GDK_KEY_Left));
  g_assert_true(gtk_root_get_focus(gtk_widget_get_root(second)) == second);
  gtk_widget_set_default_direction(GTK_TEXT_DIR_LTR);

  /* Remove and append. */
  LumaMixerStrip *last = luma_mixer_row_get_strip(r, 5);
  g_object_ref(last);
  luma_mixer_row_remove(r, last);
  g_assert_cmpuint(luma_mixer_row_get_n_strips(r), ==, 5);
  g_assert_null(luma_mixer_row_get_strip(r, 5));
  luma_mixer_row_append(r, last);
  g_object_unref(last);
  g_assert_true(luma_mixer_row_get_strip(r, 5) == last);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_reduced_motion(void) {
  g_object_set(gtk_settings_get_default(), "gtk-enable-animations", FALSE, NULL);
  GtkWidget *widget = luma_mixer_strip_new("Mic", NULL);
  LumaMixerStrip *strip = LUMA_MIXER_STRIP(widget);
  GtkWidget *window = present(widget, 200, 400);
  /* With motion reduced the meter jumps to each level; nothing falls. */
  luma_mixer_strip_set_levels_db(strip, -6, -6);
  luma_mixer_strip_set_levels_db(strip, -60, -60);
  g_assert_cmpfloat(luma_audio_meter_get_level_db(luma_mixer_strip_get_meter(strip), 0), ==, -60);
  g_assert_cmpfloat(luma_audio_meter_get_peak_db(luma_mixer_strip_get_meter(strip), 0), ==, -60);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_set(gtk_settings_get_default(), "gtk-enable-animations", TRUE, NULL);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  gtk_init();
  g_test_add_func("/lumaui/mixer-strip/strip", test_strip);
  g_test_add_func("/lumaui/mixer-strip/row", test_row);
  g_test_add_func("/lumaui/mixer-strip/reduced-motion", test_reduced_motion);
  return g_test_run();
}
