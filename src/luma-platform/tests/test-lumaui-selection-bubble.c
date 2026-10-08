/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-selection-bubble.h"

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

static LumaBarItem *mark(const char *icon, const char *tooltip) {
  LumaBarItem *item = luma_bar_item_new_action(icon, NULL, NULL);
  luma_bar_item_set_tooltip(item, tooltip);
  return item;
}

static void test_bubble(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 800, 600);
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  GtkWidget *spacer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_size_request(spacer, -1, 200);
  GtkWidget *view = gtk_text_view_new();
  gtk_widget_set_vexpand(view, TRUE);
  gtk_box_append(GTK_BOX(box), spacer);
  gtk_box_append(GTK_BOX(box), view);
  gtk_window_set_child(GTK_WINDOW(window), box);
  LumaLayerHost *host = luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  spin_ms(300);

  LumaBarItem *items[] = {mark("bold", "Bold"), mark("italic", "Italic"), luma_bar_item_new_separator(),
                          mark("link", "Link")};
  GtkWidget *bubble = luma_selection_bubble_new(view, items, G_N_ELEMENTS(items), NULL);
  g_object_ref_sink(bubble);
  g_assert_true(gtk_widget_has_css_class(bubble, "lumaui-bubble"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(bubble)), ==, GTK_ACCESSIBLE_ROLE_TOOLBAR);
  GtkWidget *bold = gtk_widget_get_first_child(bubble);
  g_assert_true(gtk_widget_has_css_class(bold, "bubble"));
  g_assert_false(luma_selection_bubble_get_shown(LUMA_SELECTION_BUBBLE(bubble)));
  g_assert_false(luma_selection_bubble_focus_first(LUMA_SELECTION_BUBBLE(bubble)));

  luma_selection_bubble_set_active(LUMA_SELECTION_BUBBLE(bubble), "bold", TRUE);
  g_assert_true(gtk_widget_has_css_class(bold, "on"));
  luma_selection_bubble_set_active(LUMA_SELECTION_BUBBLE(bubble), "bold", FALSE);
  g_assert_false(gtk_widget_has_css_class(bold, "on"));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*no mark 'strike'*");
  luma_selection_bubble_set_active(LUMA_SELECTION_BUBBLE(bubble), "strike", TRUE);
  g_test_assert_expected_messages();

  /* Selecting text in the view shows it after the settle, above the selection. */
  GtkTextBuffer *buffer = gtk_text_view_get_buffer(GTK_TEXT_VIEW(view));
  gtk_text_buffer_set_text(buffer, "Hello selection bubble", -1);
  GtkTextIter start, end;
  gtk_text_buffer_get_iter_at_offset(buffer, &start, 6);
  gtk_text_buffer_get_iter_at_offset(buffer, &end, 15);
  gtk_text_buffer_select_range(buffer, &start, &end);
  spin_ms(500);
  g_assert_true(luma_selection_bubble_get_shown(LUMA_SELECTION_BUBBLE(bubble)));
  g_assert_true(gtk_widget_get_parent(bubble) == GTK_WIDGET(host));
  g_assert_false(gtk_widget_has_css_class(bubble, "below"));
  g_assert_cmpint(gtk_widget_get_halign(bubble), ==, GTK_ALIGN_START);
  g_assert_cmpint(gtk_widget_get_margin_top(bubble), <, 200);
  g_assert_true(luma_selection_bubble_focus_first(LUMA_SELECTION_BUBBLE(bubble)));
  g_assert_true(gtk_window_get_focus(GTK_WINDOW(window)) == bold);

  /* Typing hides it. */
  gtk_text_buffer_insert_at_cursor(buffer, "x", -1);
  g_assert_false(gtk_widget_get_visible(bubble));
  g_assert_false(luma_selection_bubble_get_shown(LUMA_SELECTION_BUBBLE(bubble)));

  /* Near the top of the region it flips below. */
  GdkRectangle top = {10, -195, 40, 16};
  luma_selection_bubble_show_for(LUMA_SELECTION_BUBBLE(bubble), &top);
  g_assert_true(gtk_widget_has_css_class(bubble, "below"));
  luma_selection_bubble_hide(LUMA_SELECTION_BUBBLE(bubble));
  g_assert_false(gtk_widget_get_visible(bubble));

  for (guint i = 0; i < G_N_ELEMENTS(items); i++)
    g_object_unref(items[i]);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(bubble);
}

static void test_refusal(void) {
  GtkWidget *view = g_object_ref_sink(gtk_text_view_new());
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*at least one action*");
  g_assert_null(luma_selection_bubble_new(view, NULL, 0, NULL));
  g_test_assert_expected_messages();
  g_object_unref(view);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/selection-bubble/bubble", test_bubble);
  g_test_add_func("/lumaui/selection-bubble/refusal", test_refusal);
  return g_test_run();
}
