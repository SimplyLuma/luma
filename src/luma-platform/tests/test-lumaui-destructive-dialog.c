/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-destructive-dialog.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}
static void spin(void) {
  gboolean done = FALSE;
  g_timeout_add(300, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static GtkWidget *window_with(GtkWidget *child, int width) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), width, 700);
  gtk_window_set_child(GTK_WINDOW(window), child);
  luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  spin();
  return window;
}

static GtkWidget *find_class(GtkWidget *widget, const char *css) {
  if (gtk_widget_has_css_class(widget, css))
    return widget;
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child; child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = find_class(child, css);
    if (found)
      return found;
  }
  return NULL;
}

static void count(gpointer instance G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }
static void confirmed(gpointer instance G_GNUC_UNUSED, gboolean checked, gpointer data) {
  *(int *)data = checked ? 2 : 1;
}

static void test_refusals(void) {
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*the question*");
  g_assert_null(luma_destructive_dialog_new("  Are you sure?", "Gone.", NULL, NULL, NULL));
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*the question*");
  g_assert_null(luma_destructive_dialog_new("   ", "Gone.", NULL, NULL, NULL));
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*never OK*");
  g_assert_null(luma_destructive_dialog_new("Delete Budget?", "Gone.", " OK ", NULL, NULL));
  g_test_assert_expected_messages();
}

static void test_card(void) {
  GtkWidget *dialog = g_object_ref_sink(
      luma_destructive_dialog_new("Uninstall Kiln?", "Its settings go too.", "Uninstall", NULL, "Also delete its files"));
  g_assert_true(gtk_widget_has_css_class(dialog, "lumaui-dialog"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(dialog)), ==, GTK_ACCESSIBLE_ROLE_ALERT_DIALOG);
  g_assert_nonnull(find_class(dialog, "lumaui-drawer-handle"));
  g_assert_nonnull(find_class(dialog, "lumaui-dialog-icon"));
  GtkWidget *title = find_class(dialog, "lumaui-dialog-title");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(title)), ==, "Uninstall Kiln?");
  GtkWidget *action = find_class(dialog, "lumaui-dialog-action");
  g_assert_cmpstr(gtk_button_get_label(GTK_BUTTON(action)), ==, "Uninstall");
  GtkWidget *option = find_class(dialog, "lumaui-dialog-option");
  g_assert_true(GTK_IS_CHECK_BUTTON(option));
  g_assert_false(luma_destructive_dialog_get_option_checked(LUMA_DESTRUCTIVE_DIALOG(dialog)));
  gtk_check_button_set_active(GTK_CHECK_BUTTON(option), TRUE);
  g_assert_true(luma_destructive_dialog_get_option_checked(LUMA_DESTRUCTIVE_DIALOG(dialog)));
  g_object_unref(dialog);

  GtkWidget *plain = g_object_ref_sink(luma_destructive_dialog_new("Delete Budget?", "Gone.", NULL, NULL, NULL));
  g_assert_null(find_class(plain, "lumaui-dialog-option"));
  g_assert_cmpstr(gtk_button_get_label(GTK_BUTTON(find_class(plain, "lumaui-dialog-action"))), ==, "Delete");
  g_object_unref(plain);
}

static void test_ask(void) {
  GtkWidget *button = gtk_button_new_with_label("Delete");
  GtkWidget *window = window_with(button, 1000);

  int cancels = 0, confirms = 0;
  LumaDestructiveDialog *dialog = luma_destructive_dialog_ask(button, "Delete Budget.xlsx?", "It goes to the Bin.",
                                                              NULL, NULL, "Also delete versions");
  g_assert_nonnull(dialog);
  g_signal_connect(dialog, "cancelled", G_CALLBACK(count), &cancels);
  g_signal_connect(dialog, "confirmed", G_CALLBACK(confirmed), &confirms);
  LumaLayerHost *host = luma_layer_host_window_host(button);
  LumaModalHandle *handle = luma_layer_host_get_modal(host);
  g_assert_nonnull(handle);
  g_assert_true(luma_modal_handle_get_card(handle) == GTK_WIDGET(dialog));
  g_assert_false(luma_modal_handle_get_is_drawer(handle));
  GtkWidget *buttons = find_class(GTK_WIDGET(dialog), "lumaui-dialog-buttons");
  g_assert_cmpint(gtk_orientable_get_orientation(GTK_ORIENTABLE(buttons)), ==, GTK_ORIENTATION_HORIZONTAL);
  GtkWidget *cancel = find_class(GTK_WIDGET(dialog), "lumaui-dialog-cancel");
  g_assert_true(gtk_widget_get_first_child(buttons) == cancel);
  spin();
  g_assert_true(gtk_window_get_focus(GTK_WINDOW(window)) == cancel);
  g_signal_emit_by_name(cancel, "clicked");
  g_assert_cmpint(cancels, ==, 1);
  g_assert_cmpint(confirms, ==, 0);
  g_assert_null(luma_layer_host_get_modal(host));
  spin();

  dialog = luma_destructive_dialog_ask(button, "Delete Budget.xlsx?", "It goes to the Bin.", NULL, NULL,
                                       "Also delete versions");
  g_signal_connect(dialog, "cancelled", G_CALLBACK(count), &cancels);
  g_signal_connect(dialog, "confirmed", G_CALLBACK(confirmed), &confirms);
  gtk_check_button_set_active(GTK_CHECK_BUTTON(find_class(GTK_WIDGET(dialog), "lumaui-dialog-option")), TRUE);
  g_signal_emit_by_name(find_class(GTK_WIDGET(dialog), "lumaui-dialog-action"), "clicked");
  g_assert_cmpint(confirms, ==, 2);
  g_assert_cmpint(cancels, ==, 1);
  g_assert_null(luma_layer_host_get_modal(host));
  spin();

  dialog = luma_destructive_dialog_ask(button, "Leave the group?", "You can be added again.", "Leave", "log-out", NULL);
  g_signal_connect(dialog, "cancelled", G_CALLBACK(count), &cancels);
  luma_destructive_dialog_close(dialog);
  g_assert_cmpint(cancels, ==, 1);
  g_assert_null(luma_layer_host_get_modal(host));
  spin();
  gtk_window_destroy(GTK_WINDOW(window));
}

/* Asked before the window is laid out, at a phone's size: still the drawer (Settings' phone Forget). */
static void test_drawer_before_layout(void) {
  GtkWidget *button = gtk_button_new_with_label("Forget");
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 390, 800);
  gtk_window_set_child(GTK_WINDOW(window), button);
  luma_layer_host_install(GTK_WINDOW(window));
  LumaDestructiveDialog *dialog = luma_destructive_dialog_ask(button, "Forget Studio North?", "The password is removed.",
                                                              "Forget", NULL, NULL);
  g_assert_nonnull(dialog);
  LumaModalHandle *handle = luma_layer_host_get_modal(luma_layer_host_window_host(button));
  g_assert_true(luma_modal_handle_get_is_drawer(handle));
  gtk_window_destroy(GTK_WINDOW(window));
}

/* v70 lConfirm: 340 wide however long the body is; the body wraps in the 300 column. */
static void test_width(void) {
  GtkWidget *button = gtk_button_new_with_label("Forget");
  GtkWidget *window = window_with(button, 1180);
  LumaDestructiveDialog *dialog = luma_destructive_dialog_ask(
      button, "Forget Studio North?", "This computer won’t join it on its own any more, and the password is removed.",
      "Forget", NULL, NULL);
  g_assert_nonnull(dialog);
  spin();
  g_assert_cmpint(gtk_widget_get_width(GTK_WIDGET(dialog)), ==, 300); /* content: 340 less 20 a side */
  GtkWidget *body = find_class(GTK_WIDGET(dialog), "lumaui-dialog-body");
  g_assert_cmpint(gtk_widget_get_width(body), ==, 300);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_drawer(void) {
  GtkWidget *button = gtk_button_new_with_label("Delete");
  GtkWidget *window = window_with(button, 400);
  if (gtk_widget_get_width(window) >= LUMA_TIER_PHONE_BELOW) {
    g_test_skip("the display did not give a phone-width window");
    gtk_window_destroy(GTK_WINDOW(window));
    return;
  }
  LumaDestructiveDialog *dialog = luma_destructive_dialog_ask(button, "Delete Budget.xlsx?", "Gone.", NULL, NULL, NULL);
  LumaModalHandle *handle = luma_layer_host_get_modal(luma_layer_host_window_host(button));
  g_assert_true(luma_modal_handle_get_is_drawer(handle));
  g_assert_true(gtk_widget_has_css_class(GTK_WIDGET(dialog), "drawer"));
  /* v71: the phone's confirm looks like the in-bar confirm: no grabber, no icon, Cancel and the red
   * action side by side, in the bar's frame (16 gutter, 34 up). */
  g_assert_true(gtk_widget_has_css_class(GTK_WIDGET(dialog), "phone"));
  g_assert_false(gtk_widget_get_visible(find_class(GTK_WIDGET(dialog), "lumaui-dialog-icon")));
  g_assert_false(gtk_widget_get_visible(find_class(GTK_WIDGET(dialog), "lumaui-drawer-handle")));
  GtkWidget *buttons = find_class(GTK_WIDGET(dialog), "lumaui-dialog-buttons");
  g_assert_cmpint(gtk_orientable_get_orientation(GTK_ORIENTABLE(buttons)), ==, GTK_ORIENTATION_HORIZONTAL);
  g_assert_true(gtk_widget_get_first_child(buttons) == find_class(GTK_WIDGET(dialog), "lumaui-dialog-cancel"));
  g_assert_cmpint(gtk_widget_get_margin_start(GTK_WIDGET(dialog)), ==, 16);
  g_assert_cmpint(gtk_widget_get_margin_end(GTK_WIDGET(dialog)), ==, 16);
  g_assert_cmpint(gtk_widget_get_margin_bottom(GTK_WIDGET(dialog)), ==, 34);
  spin();
  g_assert_true(gtk_window_get_focus(GTK_WINDOW(window)) == find_class(GTK_WIDGET(dialog), "lumaui-dialog-cancel"));
  luma_modal_handle_cancel(handle);
  spin();
  gtk_window_destroy(GTK_WINDOW(window));
}

/* v71: a destructive action confirms inside the grown bar; folding it any other way is Cancel. */
static int bar_confirms, bar_cancels;
static void bar_confirmed(gpointer instance G_GNUC_UNUSED, gboolean checked G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  bar_confirms++;
}
static void test_in_bar(void) {
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  GtkWidget *window = window_with(content, 1000);
  GtkWidget *center = luma_action_center_new(NULL);
  luma_action_center_attach(LUMA_ACTION_CENTER(center), content);
  LumaBarItem *trash = luma_bar_item_new_action("trash-2", NULL, NULL);
  luma_bar_item_set_tooltip(trash, "Delete");
  luma_action_center_show_bar(LUMA_ACTION_CENTER(center), &trash, 1, NULL);
  spin();
  LumaDestructiveDialog *dialog = luma_destructive_dialog_in_bar(LUMA_ACTION_CENTER(center), "Delete this photo?",
                                                                 "It stays in Deleted for 30 days.", NULL, NULL, NULL);
  g_assert_nonnull(dialog);
  g_signal_connect(dialog, "confirmed", G_CALLBACK(bar_confirmed), NULL);
  g_signal_connect(dialog, "cancelled", G_CALLBACK(count), &bar_cancels);
  g_assert_cmpstr(luma_action_center_get_grown(LUMA_ACTION_CENTER(center)), ==, "confirm");
  g_assert_true(gtk_widget_has_css_class(GTK_WIDGET(dialog), "lumaui-panel-confirm")); /* Python's PanelConfirm */
  g_assert_null(luma_layer_host_get_modal(luma_layer_host_window_host(content))); /* no modal card */
  g_assert_false(gtk_widget_get_visible(find_class(GTK_WIDGET(dialog), "lumaui-dialog-icon")));
  spin();
  /* The grown bar is the panel width in a window: min(380, width − 24). */
  g_assert_cmpint(gtk_widget_get_width(center), ==, 380);
  g_signal_emit_by_name(find_class(GTK_WIDGET(dialog), "lumaui-panel-confirm-cancel"), "clicked");
  g_assert_cmpint(bar_cancels, ==, 1);
  g_assert_null(luma_action_center_get_grown(LUMA_ACTION_CENTER(center)));
  /* Again, and the red action: confirmed once, never also cancelled. */
  dialog = luma_destructive_dialog_in_bar(LUMA_ACTION_CENTER(center), "Delete this photo?", "Gone.", NULL, NULL, NULL);
  g_signal_connect(dialog, "confirmed", G_CALLBACK(bar_confirmed), NULL);
  g_signal_connect(dialog, "cancelled", G_CALLBACK(count), &bar_cancels);
  g_signal_emit_by_name(find_class(GTK_WIDGET(dialog), "lumaui-panel-confirm-action"), "clicked");
  g_assert_cmpint(bar_confirms, ==, 1);
  g_assert_cmpint(bar_cancels, ==, 1);
  g_assert_null(luma_action_center_get_grown(LUMA_ACTION_CENTER(center)));
  /* A third, folded by Esc's path (fold): that is Cancel. */
  dialog = luma_destructive_dialog_in_bar(LUMA_ACTION_CENTER(center), "Delete this photo?", "Gone.", NULL, NULL, NULL);
  g_signal_connect(dialog, "cancelled", G_CALLBACK(count), &bar_cancels);
  luma_action_center_fold(LUMA_ACTION_CENTER(center));
  g_assert_cmpint(bar_cancels, ==, 2);
  g_object_unref(trash);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/destructive-dialog/refusals", test_refusals);
  g_test_add_func("/lumaui/destructive-dialog/card", test_card);
  g_test_add_func("/lumaui/destructive-dialog/ask", test_ask);
  g_test_add_func("/lumaui/destructive-dialog/drawer", test_drawer);
  g_test_add_func("/lumaui/destructive-dialog/in-bar", test_in_bar);
  g_test_add_func("/lumaui/destructive-dialog/width", test_width);
  g_test_add_func("/lumaui/destructive-dialog/drawer-before-layout", test_drawer_before_layout);
  return g_test_run();
}
