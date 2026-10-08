/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-toast.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}
static void spin_ms(guint ms) {
  gboolean done = FALSE;
  g_timeout_add(ms, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}
static void spin(void) { spin_ms(250); }

static GtkWidget *window_with(GtkWidget *child, int width) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), width, 600);
  gtk_window_set_child(GTK_WINDOW(window), child);
  luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  spin();
  return window;
}

static void count(gpointer instance G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }

static void test_kinds(void) {
  g_assert_true(luma_toast_kind_is_known("copied"));
  g_assert_true(luma_toast_kind_is_known("signed-out"));
  g_assert_false(luma_toast_kind_is_known("exploded"));
  g_assert_false(luma_toast_kind_is_known(NULL));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown toast kind 'exploded'*");
  g_assert_null(luma_toast_new("Boom", "exploded", FALSE, FALSE));
  g_test_assert_expected_messages();
}

static void test_card(void) {
  GtkWidget *toast = g_object_ref_sink(luma_toast_new("Deleted 3 photos", "deleted", TRUE, FALSE));
  g_assert_true(gtk_widget_has_css_class(toast, "lumaui-toast"));
  g_assert_true(gtk_widget_has_css_class(toast, "muted"));
  g_assert_true(gtk_widget_has_css_class(toast, "has-action"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(toast)), ==, GTK_ACCESSIBLE_ROLE_STATUS);
  GtkWidget *lead = gtk_widget_get_first_child(toast);
  g_assert_true(GTK_IS_IMAGE(lead) && gtk_widget_has_css_class(lead, "lumaui-toast-icon"));
  GtkWidget *text = gtk_widget_get_next_sibling(lead);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(text)), ==, "Deleted 3 photos");
  GtkWidget *undo = gtk_widget_get_next_sibling(text);
  g_assert_true(gtk_widget_has_css_class(undo, "lumaui-toast-undo"));
  g_assert_cmpuint(luma_toast_get_timeout_ms(LUMA_TOAST(toast)), ==, 4000);
  g_object_unref(toast);

  GtkWidget *busy = g_object_ref_sink(luma_toast_new("Uploading", NULL, FALSE, TRUE));
  g_assert_true(gtk_widget_has_css_class(busy, "good"));
  g_assert_false(gtk_widget_has_css_class(busy, "has-action"));
  g_assert_true(GTK_IS_SPINNER(gtk_widget_get_first_child(busy)));
  g_assert_cmpuint(luma_toast_get_timeout_ms(LUMA_TOAST(busy)), ==, 0);
  g_object_unref(busy);
}

static void test_show(void) {
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  GtkWidget *button = gtk_button_new_with_label("Copy");
  gtk_widget_set_vexpand(button, TRUE);
  GtkWidget *bar = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_size_request(bar, -1, 60);
  gtk_box_append(GTK_BOX(box), button);
  gtk_box_append(GTK_BOX(box), bar);
  GtkWidget *window = window_with(box, 1000);

  LumaToast *first = luma_toast_show(button, "Copied", "copied");
  g_assert_nonnull(first);
  LumaLayerHost *host = luma_layer_host_for_widget(button);
  g_assert_true(gtk_widget_get_parent(GTK_WIDGET(first)) == GTK_WIDGET(host));
  g_assert_cmpuint(luma_toast_get_timeout_ms(first), ==, 2400);
  g_assert_cmpint(gtk_widget_get_margin_bottom(GTK_WIDGET(first)), ==, 18);
  g_assert_false(gtk_widget_has_css_class(GTK_WIDGET(first), "phone"));
  spin();
  g_assert_true(gtk_widget_has_css_class(GTK_WIDGET(first), "shown"));

  /* A new toast replaces the region's current one, and clears a tracked bar. */
  luma_layer_host_track_bar(host, bar);
  g_object_ref(first);
  LumaToast *second = luma_toast_show_with_undo(button, "Deleted", "deleted");
  g_assert_false(gtk_widget_has_css_class(GTK_WIDGET(first), "shown"));
  g_assert_cmpint(gtk_widget_get_margin_bottom(GTK_WIDGET(second)), >=, 60 + 12);
  g_assert_cmpuint(luma_toast_get_timeout_ms(second), ==, 4000);
  spin();
  g_assert_null(gtk_widget_get_parent(GTK_WIDGET(first)));
  g_object_unref(first);

  int undos = 0;
  g_signal_connect(second, "undo", G_CALLBACK(count), &undos);
  GtkWidget *undo = gtk_widget_get_last_child(GTK_WIDGET(second));
  g_signal_emit_by_name(undo, "clicked");
  g_assert_cmpint(undos, ==, 1);
  g_assert_false(gtk_widget_has_css_class(GTK_WIDGET(second), "shown"));
  spin();

  LumaToast *busy = luma_toast_show_busy(button, "Uploading");
  g_assert_cmpuint(luma_toast_get_timeout_ms(busy), ==, 0);
  spin_ms(500);
  g_assert_true(gtk_widget_get_parent(GTK_WIDGET(busy)) == GTK_WIDGET(host));
  luma_toast_dismiss(busy);
  spin();
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_expire(void) {
  GtkWidget *button = gtk_button_new_with_label("Save");
  GtkWidget *window = window_with(button, 1000);
  LumaToast *toast = luma_toast_show(button, "Saved", "saved");
  GtkWidget *host = gtk_widget_get_parent(GTK_WIDGET(toast));
  g_object_ref(toast);
  spin_ms(2400 + 500);
  g_assert_null(gtk_widget_get_parent(GTK_WIDGET(toast)));
  g_object_unref(toast);
  g_assert_nonnull(host);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_phone(void) {
  GtkWidget *button = gtk_button_new_with_label("Share");
  GtkWidget *window = window_with(button, 400);
  if (gtk_widget_get_width(window) > 639) {
    g_test_skip("the display did not give a phone-width window");
    gtk_window_destroy(GTK_WINDOW(window));
    return;
  }
  LumaToast *toast = luma_toast_show(button, "Shared", NULL);
  g_assert_true(gtk_widget_has_css_class(GTK_WIDGET(toast), "phone"));
  g_assert_cmpint(gtk_widget_get_margin_start(GTK_WIDGET(toast)), ==, 12);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/toast/kinds", test_kinds);
  g_test_add_func("/lumaui/toast/card", test_card);
  g_test_add_func("/lumaui/toast/show", test_show);
  g_test_add_func("/lumaui/toast/expire", test_expire);
  g_test_add_func("/lumaui/toast/phone", test_phone);
  return g_test_run();
}
