/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

static GtkWidget *find_class(GtkWidget *widget, const char *css) {
  if (gtk_widget_has_css_class(widget, css)) return widget;
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child;
       child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = find_class(child, css);
    if (found) return found;
  }
  return NULL;
}
static void settle(void) {
  gint64 until = g_get_monotonic_time() + 100000;
  while (g_get_monotonic_time() < until) {
    while (g_main_context_iteration(NULL, FALSE));
    g_usleep(1000);
  }
}
int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check()) return 77;
  luma_init();
  const int widths[] = {360, 500, 1024, 1440};
  for (int dark = 0; dark < 2; dark++) {
    adw_style_manager_set_color_scheme(adw_style_manager_get_default(),
        dark ? ADW_COLOR_SCHEME_FORCE_DARK : ADW_COLOR_SCHEME_FORCE_LIGHT);
    for (int compact = 0; compact < 2; compact++) {
      for (guint n = 0; n < G_N_ELEMENTS(widths); n++) {
        GtkWidget *window = gtk_window_new();
        GtkWidget *page = adw_status_page_new();
        GtkWidget *action = gtk_button_new_with_label("Open…");
        gtk_actionable_set_action_name(GTK_ACTIONABLE(action), "win.open");
        adw_status_page_set_icon_name(ADW_STATUS_PAGE(page), "folder-symbolic");
        adw_status_page_set_title(ADW_STATUS_PAGE(page), "This folder is empty");
        adw_status_page_set_description(ADW_STATUS_PAGE(page), "Files you save here appear here.");
        adw_status_page_set_child(ADW_STATUS_PAGE(page), action);
        luma_empty_state_configure(ADW_STATUS_PAGE(page), compact);
        gtk_window_set_child(GTK_WINDOW(window), page);
        gtk_window_set_default_size(GTK_WINDOW(window), widths[n], 500);
        gtk_window_present(GTK_WINDOW(window));
        settle();
        GtkWidget *icon = find_class(page, "icon");
        g_assert_nonnull(icon);
        /* Allocated bounds include CSS padding: actual coloured disc geometry. */
        graphene_rect_t bounds;
        g_assert_true(gtk_widget_compute_bounds(icon, gtk_widget_get_parent(icon), &bounds));
        g_assert_cmpint((int)(bounds.size.width + .5f), ==, compact ? 48 : 84);
        g_assert_cmpint((int)(bounds.size.height + .5f), ==, compact ? 48 : 84);
        g_assert_true(adw_status_page_get_child(ADW_STATUS_PAGE(page)) == action);
        g_assert_cmpstr(gtk_actionable_get_action_name(GTK_ACTIONABLE(action)), ==, "win.open");
        g_assert_cmpint(gtk_widget_get_width(page), <=, widths[n]);
        gtk_widget_set_direction(page, GTK_TEXT_DIR_RTL);
        luma_empty_state_configure(ADW_STATUS_PAGE(page), !compact);
        settle();
        g_assert_true(gtk_widget_compute_bounds(icon, gtk_widget_get_parent(icon), &bounds));
        g_assert_cmpint((int)(bounds.size.width + .5f), ==, compact ? 84 : 48);
        gtk_window_destroy(GTK_WINDOW(window));
      }
    }
  }
  return 0;
}
