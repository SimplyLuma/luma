/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

static void settle(void) {
  for (int i = 0; i < 30; i++) {
    while (g_main_context_iteration(NULL, FALSE));
    g_usleep(4000);
  }
}

static void test_text_button(void) {
  GtkWidget *share = g_object_ref_sink(luma_text_button_new("Share", "share-2", NULL));
  g_assert_true(gtk_widget_has_css_class(share, "lumaui-text-button"));
  g_assert_true(gtk_widget_has_css_class(gtk_button_get_child(GTK_BUTTON(share)), "lumaui-text-button-content"));
  GtkWidget *change = g_object_ref_sink(luma_text_button_new("Change…", NULL, "fill"));
  luma_text_button_set_small(GTK_BUTTON(change), TRUE);
  g_assert_true(gtk_widget_has_css_class(change, "fill") && gtk_widget_has_css_class(change, "small"));
  g_assert_true(GTK_IS_LABEL(gtk_button_get_child(GTK_BUTTON(change))));
  GtkWidget *raised = g_object_ref_sink(luma_text_button_new("Today", NULL, "raised"));
  g_assert_true(gtk_widget_has_css_class(raised, "raised"));
  g_object_unref(raised);
  GtkWidget *forget = g_object_ref_sink(luma_text_button_new("Forget", NULL, "danger"));
  g_assert_true(gtk_widget_has_css_class(forget, "danger-solid"));
  GtkWidget *out = g_object_ref_sink(luma_text_button_new("Sign out…", NULL, NULL));
  luma_text_button_set_danger(GTK_BUTTON(out), TRUE);
  g_assert_true(gtk_widget_has_css_class(out, "danger"));
  GtkWidget *window = gtk_window_new();
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_box_append(GTK_BOX(box), share);
  gtk_box_append(GTK_BOX(box), change);
  gtk_window_set_child(GTK_WINDOW(window), box);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_assert_cmpint(gtk_widget_get_height(share), ==, 36);
  g_assert_cmpint(gtk_widget_get_height(change), ==, 28);
  luma_text_button_set_hero(GTK_BUTTON(share), TRUE);
  settle();
  g_assert_cmpint(gtk_widget_get_height(share), ==, 34);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(share);
  g_object_unref(change);
  g_object_unref(forget);
  g_object_unref(out);
}

static void test_icon_button(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  GtkWidget *forget = luma_icon_button_new("x", "Forget WH-1000XM5");
  GtkWidget *copy = luma_icon_button_new("copy", "Copy");
  GtkWidget *add = luma_icon_button_new("plus", "Add");
  luma_icon_button_set_raised(GTK_BUTTON(forget), TRUE);
  g_assert_true(gtk_widget_has_css_class(forget, "raised"));
  luma_icon_button_set_raised(GTK_BUTTON(forget), FALSE);
  g_assert_false(gtk_widget_has_css_class(forget, "raised"));
  luma_icon_button_set_size(GTK_BUTTON(copy), "row");
  luma_icon_button_set_size(GTK_BUTTON(add), "small");
  g_assert_cmpstr(gtk_widget_get_tooltip_text(forget), ==, "Forget WH-1000XM5");
  gtk_box_append(GTK_BOX(box), forget);
  gtk_box_append(GTK_BOX(box), copy);
  gtk_box_append(GTK_BOX(box), add);
  gtk_widget_set_valign(box, GTK_ALIGN_START);
  gtk_window_set_child(GTK_WINDOW(window), box);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_assert_cmpint(gtk_widget_get_width(forget), ==, 36);
  g_assert_cmpint(gtk_widget_get_height(forget), ==, 36);
  g_assert_cmpint(gtk_widget_get_width(copy), ==, 28);
  g_assert_cmpint(gtk_widget_get_width(add), ==, 26);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_switch(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  GtkWidget *small = luma_switch_new(FALSE);
  GtkWidget *big = luma_switch_new(TRUE);
  g_assert_true(GTK_IS_SWITCH(small) && gtk_widget_has_css_class(small, "lumaui-switch"));
  g_assert_true(gtk_widget_has_css_class(big, "big"));
  gtk_box_append(GTK_BOX(box), small);
  gtk_box_append(GTK_BOX(box), big);
  gtk_window_set_child(GTK_WINDOW(window), box);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_assert_cmpint(gtk_widget_get_width(small), ==, 38);
  g_assert_cmpint(gtk_widget_get_height(small), ==, 22);
  g_assert_cmpint(gtk_widget_get_width(big), ==, 44);
  g_assert_cmpint(gtk_widget_get_height(big), ==, 26);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void choice_changed(LumaChoiceList *self G_GNUC_UNUSED, const char *key, guint *count) {
  g_assert_cmpstr(key, ==, "other");
  (*count)++;
}

static void test_choice_list(void) {
  GtkWidget *widget = g_object_ref_sink(luma_choice_list_new(TRUE));
  LumaChoiceList *list = LUMA_CHOICE_LIST(widget);
  luma_choice_list_append(list, "luma", "For Luma", "Snapshots and compression");
  luma_choice_list_append(list, "other", "For every computer", "Portable files");
  guint changed = 0;
  g_signal_connect(list, "changed", G_CALLBACK(choice_changed), &changed);
  luma_choice_list_set_selected(list, "luma");
  g_assert_cmpuint(changed, ==, 0);
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(list)), ==, GTK_ACCESSIBLE_ROLE_RADIO_GROUP);
  GtkWidget *first = gtk_widget_get_first_child(widget);
  GtkWidget *second = gtk_widget_get_next_sibling(first);
  g_assert_true(gtk_widget_has_css_class(first, "on"));
  g_signal_emit_by_name(second, "clicked");
  g_assert_cmpstr(luma_choice_list_get_selected(list), ==, "other");
  g_assert_cmpuint(changed, ==, 1);
  g_assert_false(gtk_widget_has_css_class(first, "on"));
  g_assert_true(gtk_widget_has_css_class(second, "on"));
  g_signal_emit_by_name(second, "clicked");
  g_assert_cmpuint(changed, ==, 1);
  g_object_unref(widget);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/controls/text-button", test_text_button);
  g_test_add_func("/lumaui/controls/switch", test_switch);
  g_test_add_func("/lumaui/controls/choice-list", test_choice_list);
  g_test_add_func("/lumaui/controls/icon-button", test_icon_button);
  return g_test_run();
}
