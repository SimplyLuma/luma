/* SPDX-License-Identifier: Apache-2.0 */
/* LumaModeSwitch: the twin of structure_placement.ModeSwitch. */
#include "luma-ui.h"
#include "luma-mode-switch.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

/* Run the main loop for a few frames (tick callbacks need the frame clock). */
static void settle(void) {
  gboolean done = FALSE;
  g_timeout_add(250, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static GtkWidget *row_of(GtkWidget *modes) {
  return gtk_widget_get_first_child(modes);
}

/* The row's first child is the chip; the buttons follow it. */
static GtkWidget *button_at(GtkWidget *modes, int index) {
  GtkWidget *child = gtk_widget_get_next_sibling(gtk_widget_get_first_child(row_of(modes)));
  for (int i = 0; i < index; i++)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static GtkWidget *label_of(GtkWidget *button) {
  for (GtkWidget *child = gtk_widget_get_first_child(gtk_button_get_child(GTK_BUTTON(button)));
       child != NULL; child = gtk_widget_get_next_sibling(child)) {
    if (gtk_widget_has_css_class(child, "lumaui-mode-label"))
      return child;
  }
  g_assert_not_reached();
}

static void changed(LumaModeSwitch *modes G_GNUC_UNUSED, const char *key, gpointer data) {
  GPtrArray *keys = data;
  g_ptr_array_add(keys, g_strdup(key));
}

static GtkWidget *three(void) {
  GtkWidget *modes = luma_mode_switch_new("Mode");
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "view", "View", "eye");
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "markup", "Mark up", "pen-line");
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "adjust", "Adjust", "sliders-horizontal");
  return modes;
}

static void test_tree(void) {
  GtkWidget *modes = g_object_ref_sink(three());
  g_assert_true(GTK_IS_BOX(modes));
  g_assert_true(gtk_widget_has_css_class(modes, "lumaui-modes"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(modes)), ==, GTK_ACCESSIBLE_ROLE_RADIO_GROUP);
  GtkWidget *indicator = gtk_widget_get_first_child(row_of(modes));
  g_assert_true(gtk_widget_has_css_class(indicator, "lumaui-modes-indicator"));
  g_assert_false(gtk_widget_get_can_target(indicator));
  g_assert_true(gtk_widget_has_css_class(row_of(modes), "lumaui-modes-row"));
  GtkWidget *first = button_at(modes, 0);
  g_assert_true(GTK_IS_TOGGLE_BUTTON(first));
  g_assert_true(gtk_widget_has_css_class(first, "lumaui-mode"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(first)), ==, GTK_ACCESSIBLE_ROLE_RADIO);
  g_assert_cmpstr(gtk_widget_get_tooltip_text(first), ==, "View");
  g_assert_true(gtk_widget_has_css_class(label_of(first), "lumaui-mode-label"));
  GtkWidget *status = gtk_widget_get_next_sibling(label_of(first));
  g_assert_true(gtk_widget_has_css_class(status, "lumaui-mode-status"));
  g_assert_false(gtk_widget_get_visible(status));
  GtkWidget *dot = gtk_widget_get_next_sibling(status);
  g_assert_true(gtk_widget_has_css_class(dot, "lumaui-mode-dot"));
  g_assert_false(gtk_widget_get_visible(dot));
  g_assert_cmpstr(luma_mode_switch_get_current(LUMA_MODE_SWITCH(modes)), ==, "view");
  g_assert_true(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(first)));
  g_object_unref(modes);
}

static void test_choose(void) {
  GtkWidget *modes = g_object_ref_sink(three());
  g_autoptr(GPtrArray) keys = g_ptr_array_new_with_free_func(g_free);
  g_signal_connect(modes, "changed", G_CALLBACK(changed), keys);
  luma_mode_switch_set_current(LUMA_MODE_SWITCH(modes), "adjust");
  g_assert_cmpuint(keys->len, ==, 0);
  g_assert_cmpstr(luma_mode_switch_get_current(LUMA_MODE_SWITCH(modes)), ==, "adjust");
  g_assert_true(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(button_at(modes, 2))));
  g_assert_false(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(button_at(modes, 0))));
  g_assert_true(gtk_widget_has_css_class(button_at(modes, 2), "on"));
  /* A person picks Mark up. */
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(button_at(modes, 1)), TRUE);
  g_assert_cmpuint(keys->len, ==, 1);
  g_assert_cmpstr(g_ptr_array_index(keys, 0), ==, "markup");
  g_assert_cmpstr(luma_mode_switch_get_current(LUMA_MODE_SWITCH(modes)), ==, "markup");
  g_assert_false(gtk_widget_has_css_class(button_at(modes, 2), "on"));
  /* Picking the current mode again says nothing. */
  gtk_widget_activate(button_at(modes, 1));
  g_assert_cmpuint(keys->len, ==, 1);
  g_object_unref(modes);
}

static void test_refusals(void) {
  GtkWidget *modes = g_object_ref_sink(three());
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*mode keys are unique*");
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "view", "Again", "eye");
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown mode*");
  luma_mode_switch_set_current(LUMA_MODE_SWITCH(modes), "nope");
  g_test_assert_expected_messages();
  g_assert_cmpstr(luma_mode_switch_get_current(LUMA_MODE_SWITCH(modes)), ==, "view");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*LUMA_IS_MODE_SWITCH*");
  luma_mode_switch_set_current(NULL, "view");
  g_test_assert_expected_messages();
  g_object_unref(modes);
  /* One mode is not a switch: refused once shown. */
  GtkWidget *window = gtk_window_new();
  GtkWidget *one = luma_mode_switch_new(NULL);
  luma_mode_switch_add(LUMA_MODE_SWITCH(one), "only", "Only", "eye");
  gtk_window_set_child(GTK_WINDOW(window), one);
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*at least two modes*");
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_test_assert_expected_messages();
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_narrow(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 700, 500);
  GtkWidget *modes = three();
  gtk_widget_set_halign(modes, GTK_ALIGN_CENTER);
  gtk_window_set_child(GTK_WINDOW(window), modes);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_assert_true(gtk_widget_has_css_class(modes, "narrow"));
  g_assert_true(gtk_widget_get_visible(label_of(button_at(modes, 0))));
  g_assert_false(gtk_widget_get_visible(label_of(button_at(modes, 1))));
  luma_mode_switch_set_current(LUMA_MODE_SWITCH(modes), "markup");
  settle();
  settle(); /* the chip's slide (MOTION.morph) is over */
  g_assert_false(gtk_widget_get_visible(label_of(button_at(modes, 0))));
  g_assert_true(gtk_widget_get_visible(label_of(button_at(modes, 1))));
  /* The chip sits under the current mode. */
  GtkWidget *indicator = gtk_widget_get_first_child(row_of(modes));
  graphene_rect_t bounds, chip;
  g_assert_true(gtk_widget_compute_bounds(button_at(modes, 1), row_of(modes), &bounds));
  g_assert_true(gtk_widget_compute_bounds(indicator, row_of(modes), &chip));
  g_assert_cmpfloat_with_epsilon(chip.origin.x, bounds.origin.x, 1.0);
  g_assert_cmpfloat_with_epsilon(chip.size.width, bounds.size.width, 1.0);
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 500);
  settle();
  g_assert_false(gtk_widget_has_css_class(modes, "narrow"));
  g_assert_true(gtk_widget_get_visible(label_of(button_at(modes, 0))));
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_icon_only(void) {
  GtkWidget *modes = g_object_ref_sink(three());
  g_assert_true(gtk_widget_get_visible(label_of(button_at(modes, 0))));
  luma_mode_switch_set_icon_only(LUMA_MODE_SWITCH(modes), TRUE);
  g_assert_true(luma_mode_switch_get_icon_only(LUMA_MODE_SWITCH(modes)));
  for (int i = 0; i < 3; i++) {
    g_assert_false(gtk_widget_get_visible(label_of(button_at(modes, i))));
    g_assert_nonnull(gtk_widget_get_tooltip_text(button_at(modes, i)));
  }
  luma_mode_switch_set_current(LUMA_MODE_SWITCH(modes), "adjust");
  g_assert_false(gtk_widget_get_visible(label_of(button_at(modes, 2))));
  luma_mode_switch_set_icon_only(LUMA_MODE_SWITCH(modes), FALSE);
  g_assert_false(luma_mode_switch_get_icon_only(LUMA_MODE_SWITCH(modes)));
  g_assert_true(gtk_widget_get_visible(label_of(button_at(modes, 0))));
  g_object_unref(modes);
}

/* Words alone in a settings row (v70 .cfrow .seg): no glyphs, never folded, 32 tall. */
static void test_compact_words(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *modes = luma_mode_switch_new("Orientation");
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "land", "Landscape", NULL);
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "port", "Portrait", NULL);
  luma_mode_switch_set_compact(LUMA_MODE_SWITCH(modes), TRUE);
  g_assert_true(gtk_widget_has_css_class(modes, "labels-only"));
  gtk_widget_set_halign(modes, GTK_ALIGN_CENTER);
  gtk_window_set_child(GTK_WINDOW(window), modes);
  gtk_window_set_default_size(GTK_WINDOW(window), 390, 200);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_assert_false(gtk_widget_has_css_class(modes, "narrow"));
  g_assert_true(gtk_widget_get_visible(label_of(button_at(modes, 1))));
  g_assert_cmpint(gtk_widget_get_height(button_at(modes, 0)), ==, 26);
  gtk_window_destroy(GTK_WINDOW(window));
}

/* settings-02: a switch that fills its row, equal segments, the chip one cell. */
static void test_fill(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 600, 200);
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), box);
  GtkWidget *modes = luma_mode_switch_new("Scale");
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "100", "100%", NULL);
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "125", "125%", NULL);
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "200", "200% (largest)", NULL);
  luma_mode_switch_set_compact(LUMA_MODE_SWITCH(modes), TRUE);
  luma_mode_switch_set_fill(LUMA_MODE_SWITCH(modes), TRUE);
  g_assert_true(luma_mode_switch_get_fill(LUMA_MODE_SWITCH(modes)));
  g_assert_true(gtk_widget_get_hexpand(modes));
  gtk_box_append(GTK_BOX(box), modes);
  gtk_window_present(GTK_WINDOW(window));
  for (int i = 0; i < 20; i++)
    g_main_context_iteration(NULL, FALSE), g_usleep(10000);
  GtkWidget *row = gtk_widget_get_last_child(modes);
  int widths[3], n = 0;
  for (GtkWidget *c = gtk_widget_get_first_child(row); c; c = gtk_widget_get_next_sibling(c))
    if (gtk_widget_has_css_class(c, "lumaui-mode"))
      widths[n++] = gtk_widget_get_width(c);
  g_assert_cmpint(n, ==, 3);
  g_assert_cmpint(ABS(widths[0] - widths[1]), <=, 1);
  g_assert_cmpint(ABS(widths[0] - widths[2]), <=, 2);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_small_words(void) {
  GtkWidget *widget = g_object_ref_sink(luma_mode_switch_new("Performance mode"));
  LumaModeSwitch *modes = LUMA_MODE_SWITCH(widget);
  luma_mode_switch_set_small(modes, TRUE);
  luma_mode_switch_add(modes, "balanced", "Balanced", NULL);
  luma_mode_switch_add(modes, "performance", "Performance", NULL);
  g_assert_true(gtk_widget_has_css_class(widget, "small"));
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(button_at(widget, 0)));
  GtkLabel *label = GTK_LABEL(gtk_widget_get_first_child(line));
  g_assert_cmpint(gtk_label_get_ellipsize(label), ==, PANGO_ELLIPSIZE_END);
  luma_mode_switch_set_small(modes, FALSE);
  g_assert_false(gtk_widget_has_css_class(widget, "small"));
  g_assert_cmpint(gtk_label_get_ellipsize(label), ==, PANGO_ELLIPSIZE_NONE);
  g_object_unref(widget);
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/mode-switch/tree", test_tree);
  g_test_add_func("/lumaui/mode-switch/small-words", test_small_words);
  g_test_add_func("/lumaui/mode-switch/choose", test_choose);
  g_test_add_func("/lumaui/mode-switch/refusals", test_refusals);
  g_test_add_func("/lumaui/mode-switch/fill", test_fill);
  g_test_add_func("/lumaui/mode-switch/narrow", test_narrow);
  g_test_add_func("/lumaui/mode-switch/icon-only", test_icon_only);
  g_test_add_func("/lumaui/mode-switch/compact-words", test_compact_words);
  return g_test_run();
}
