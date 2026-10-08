/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

static char *changed_to;
static void changed(LumaTabBar *bar G_GNUC_UNUSED, const char *key, gpointer data G_GNUC_UNUSED) {
  g_free(changed_to);
  changed_to = g_strdup(key);
}

static void test_tabs(void) {
  GtkWidget *bar = g_object_ref_sink(luma_tab_bar_new(FALSE));
  luma_tab_bar_add(LUMA_TAB_BAR(bar), "world", "World", "globe");
  luma_tab_bar_add(LUMA_TAB_BAR(bar), "alarms", "Alarms", "alarm-clock");
  luma_tab_bar_add(LUMA_TAB_BAR(bar), "timer", "Timer", "hourglass");
  g_assert_true(gtk_widget_has_css_class(bar, "lumaui-tab-bar"));
  g_assert_cmpstr(luma_tab_bar_get_current(LUMA_TAB_BAR(bar)), ==, "world");
  g_signal_connect(bar, "changed", G_CALLBACK(changed), NULL);
  GtkWidget *alarms = gtk_widget_get_next_sibling(gtk_widget_get_first_child(bar));
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(alarms), TRUE);
  g_assert_cmpstr(changed_to, ==, "alarms");
  g_assert_cmpstr(luma_tab_bar_get_current(LUMA_TAB_BAR(bar)), ==, "alarms");
  luma_tab_bar_set_current(LUMA_TAB_BAR(bar), "timer", FALSE);
  g_assert_cmpstr(changed_to, ==, "alarms"); /* quiet */
  luma_tab_bar_set_count(LUMA_TAB_BAR(bar), "alarms", 3, FALSE);
  g_assert_nonnull(gtk_widget_get_last_child(gtk_button_get_child(GTK_BUTTON(alarms))));
  luma_tab_bar_set_running(LUMA_TAB_BAR(bar), "timer", TRUE);
  g_assert_true(luma_tab_bar_wants_tabs(3, TRUE));
  g_assert_false(luma_tab_bar_wants_tabs(2, TRUE));
  g_object_unref(bar);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/tab-bar/tabs", test_tabs);
  return g_test_run();
}
