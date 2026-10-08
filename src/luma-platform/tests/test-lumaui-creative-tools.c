/* SPDX-License-Identifier: Apache-2.0 */
/* LumaToolBar (LumaUI creative, KB-D). */
#include "luma-ui.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void settle(void) {
  gboolean done = FALSE;
  g_timeout_add(250, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static GtkWidget *nth(GtkWidget *parent, int index) {
  GtkWidget *child = gtk_widget_get_first_child(parent);
  for (int i = 0; i < index && child != NULL; i++)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static void record(GObject *object G_GNUC_UNUSED, const char *key, gpointer data) {
  g_ptr_array_add(data, g_strdup(key));
}

/* Canvas's tools (v70 cvTools). */
static GtkWidget *canvas_tools(void) {
  GtkWidget *tools = luma_tool_bar_new(LUMA_TOOL_BAR_KIND_BAR);
  LumaToolBar *bar = LUMA_TOOL_BAR(tools);
  luma_tool_bar_add_tool(bar, "move", "mouse-pointer-2", "Move", "V");
  luma_tool_bar_add_tool(bar, "frame", "hash", "Frame", "F");
  luma_tool_bar_add_flyout(bar, "shape", "Shapes");
  luma_tool_bar_add_flyout_tool(bar, "shape", "rect", "square", "Rectangle", "R");
  luma_tool_bar_add_flyout_tool(bar, "shape", "ellipse", "circle", "Ellipse", "O");
  luma_tool_bar_add_flyout_tool(bar, "shape", "line", "minus", "Line", "L");
  luma_tool_bar_add_tool(bar, "pen", "pen-tool", "Pen", "P");
  luma_tool_bar_add_separator(bar);
  luma_tool_bar_add_tool(bar, "hand", "hand", "Hand", "H");
  return tools;
}

static const char *icon_of(GtkWidget *button) {
  GtkWidget *overlay = gtk_button_get_child(GTK_BUTTON(button));
  return gtk_image_get_icon_name(GTK_IMAGE(gtk_widget_get_first_child(overlay)));
}

static void test_tree(void) {
  GtkWidget *tools = g_object_ref_sink(canvas_tools());
  g_assert_true(gtk_widget_has_css_class(tools, "lumaui-creative-tools"));
  g_assert_true(gtk_widget_has_css_class(tools, "bar"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(tools)), ==, GTK_ACCESSIBLE_ROLE_TOOLBAR);
  GtkWidget *move = nth(tools, 0);
  g_assert_true(gtk_widget_has_css_class(move, "lumaui-creative-tool"));
  g_assert_true(gtk_widget_has_css_class(move, "on"));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(move), ==, "Move · V");
  g_assert_cmpstr(icon_of(move), ==, "lumaui-mouse-pointer-2-symbolic");
  GtkWidget *shape = nth(tools, 2);
  g_assert_true(gtk_widget_has_css_class(shape, "flyout"));
  g_assert_cmpstr(icon_of(shape), ==, "lumaui-square-symbolic");
  GtkWidget *mark = gtk_widget_get_last_child(gtk_button_get_child(GTK_BUTTON(shape)));
  g_assert_true(gtk_widget_has_css_class(mark, "lumaui-creative-tool-more"));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(shape), ==, "Shapes · R");
  g_assert_true(gtk_widget_has_css_class(nth(tools, 4), "lumaui-creative-tools-separator"));
  g_assert_cmpstr(luma_tool_bar_get_current(LUMA_TOOL_BAR(tools)), ==, "move");
  GtkWidget *palette = g_object_ref_sink(luma_tool_bar_new(LUMA_TOOL_BAR_KIND_PALETTE));
  g_assert_true(gtk_widget_has_css_class(palette, "palette"));
  g_object_unref(palette);
  g_object_unref(tools);
}

static void test_choose(void) {
  GtkWidget *tools = g_object_ref_sink(canvas_tools());
  LumaToolBar *bar = LUMA_TOOL_BAR(tools);
  GtkWidget *shape = nth(tools, 2);
  g_signal_emit_by_name(shape, "clicked");
  g_assert_cmpstr(luma_tool_bar_get_current(bar), ==, "rect");
  luma_tool_bar_set_current(bar, "move");
  luma_tool_bar_set_current(bar, "line");
  luma_tool_bar_set_current(bar, "move");
  g_signal_emit_by_name(shape, "clicked");
  g_assert_cmpstr(luma_tool_bar_get_current(bar), ==, "line");
  g_autoptr(GPtrArray) keys = g_ptr_array_new_with_free_func(g_free);
  g_signal_connect(tools, "tool-changed", G_CALLBACK(record), keys);
  luma_tool_bar_set_current(bar, "pen");
  g_assert_cmpuint(keys->len, ==, 0);
  g_assert_true(gtk_widget_has_css_class(nth(tools, 3), "on"));
  g_assert_false(gtk_widget_has_css_class(nth(tools, 0), "on"));
  /* A person clicks Frame. */
  g_signal_emit_by_name(nth(tools, 1), "clicked");
  g_assert_cmpuint(keys->len, ==, 1);
  g_assert_cmpstr(g_ptr_array_index(keys, 0), ==, "frame");
  /* Shortcuts, either case; a flyout member shows on its group. */
  g_assert_true(luma_tool_bar_activate_shortcut(bar, "o"));
  g_assert_cmpstr(luma_tool_bar_get_current(bar), ==, "ellipse");
  g_assert_cmpstr(g_ptr_array_index(keys, 1), ==, "ellipse");
  g_assert_true(gtk_widget_has_css_class(shape, "on"));
  g_assert_cmpstr(icon_of(shape), ==, "lumaui-circle-symbolic");
  g_assert_cmpstr(gtk_widget_get_tooltip_text(shape), ==, "Shapes · O");
  /* The group keeps showing its last member after another tool. */
  luma_tool_bar_set_current(bar, "move");
  g_assert_false(gtk_widget_has_css_class(shape, "on"));
  g_assert_cmpstr(icon_of(shape), ==, "lumaui-circle-symbolic");
  g_assert_false(luma_tool_bar_activate_shortcut(bar, "z"));
  /* The flyout's rows pick through the bar's action. */
  gtk_widget_activate_action(tools, "creative-tools.pick", "s", "line");
  g_assert_cmpstr(luma_tool_bar_get_current(bar), ==, "line");
  g_assert_cmpuint(keys->len, ==, 3);
  g_object_unref(tools);
}

static void test_refusals(void) {
  GtkWidget *tools = g_object_ref_sink(canvas_tools());
  LumaToolBar *bar = LUMA_TOOL_BAR(tools);
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*already a tool*");
  luma_tool_bar_add_tool(bar, "move", "hand", "Again", NULL);
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown flyout*");
  luma_tool_bar_add_flyout_tool(bar, "nope", "x", "x", "X", NULL);
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown tool*");
  luma_tool_bar_set_current(bar, "shape");
  g_test_assert_expected_messages();
  g_object_unref(tools);
}

static void test_flyout_and_keys(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  GtkWidget *entry = gtk_entry_new();
  GtkWidget *tools = canvas_tools();
  gtk_box_append(GTK_BOX(box), entry);
  gtk_box_append(GTK_BOX(box), tools);
  gtk_window_set_child(GTK_WINDOW(window), box);
  luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  settle();
  luma_tool_bar_open_flyout(LUMA_TOOL_BAR(tools), "shape");
  settle();
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/creative/tools/tree", test_tree);
  g_test_add_func("/lumaui/creative/tools/choose", test_choose);
  g_test_add_func("/lumaui/creative/tools/refusals", test_refusals);
  g_test_add_func("/lumaui/creative/tools/flyout", test_flyout_and_keys);
  return g_test_run();
}
