/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
static void clicked(GtkButton *button, gpointer data) { (void)button; (*(int*)data)++; }
static void welcome_contract(void) {
  const char *titles[] = {"Canvas", "Write", "Grid", "Stage"};
  const char *nouns[] = {"design", "document", "spreadsheet", "presentation"};
  for (unsigned i = 0; i < 4; i++) {
    GtkWidget *widget = luma_welcome_view_new(titles[i], "document-new-symbolic",
        "A translated description with enough words to wrap on a narrow screen.", nouns[i], "");
    g_object_ref_sink(widget);
    int count = 0;
    GtkWidget *create = luma_welcome_view_get_new_button(LUMA_WELCOME_VIEW(widget));
    GtkWidget *open = luma_welcome_view_get_open_button(LUMA_WELCOME_VIEW(widget));
    g_signal_connect(create, "clicked", G_CALLBACK(clicked), &count);
    g_signal_connect(open, "clicked", G_CALLBACK(clicked), &count);
    g_signal_emit_by_name(create, "clicked"); g_signal_emit_by_name(open, "clicked");
    g_assert_cmpint(count, ==, 2);
    g_assert_true(gtk_widget_get_focusable(create));
    const int widths[] = {360, 500, 900, 1440};
    for (unsigned j = 0; j < 4; j++) {
      gtk_widget_allocate(widget, widths[j], 500, -1, NULL);
      graphene_rect_t bounds;
      g_assert_true(gtk_widget_compute_bounds(create, widget, &bounds));
      g_assert_cmpfloat(bounds.origin.x, >=, 0);
      g_assert_cmpfloat(bounds.origin.x + bounds.size.width, <=, widths[j]);
      g_assert_true(gtk_widget_compute_bounds(open, widget, &bounds));
      g_assert_cmpfloat(bounds.origin.x + bounds.size.width, <=, widths[j]);
    }
    g_object_unref(widget);
  }
}
int main(int argc, char **argv) {
  gtk_init(); luma_init(); g_test_init(&argc, &argv, NULL);
  g_test_add_func("/luma/welcome/layout-and-actions", welcome_contract);
  return g_test_run();
}
