/* SPDX-License-Identifier: Apache-2.0 */
/* LumaCreativeWorkspace and LumaFloatingPanel (LumaUI creative, KB-D). */
#include "luma-ui.h"

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

static void settle(void) { wait_ms(250); }

static GtkWidget *nth(GtkWidget *parent, int index) {
  GtkWidget *child = gtk_widget_get_first_child(parent);
  for (int i = 0; i < index && child != NULL; i++)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static GtkWidget *card_of(GtkWidget *panel) { return nth(panel, 0); }
static GtkWidget *pill_of(GtkWidget *panel) { return nth(panel, 1); }
static GtkWidget *header_of(GtkWidget *panel) { return nth(card_of(panel), 1); }
static GtkWidget *tabs_of(GtkWidget *panel) { return nth(card_of(panel), 2); }
/* The tab buttons: tabs > overlay > (track, indicator, row). */
static GtkWidget *tab_at(GtkWidget *panel, int i) {
  return nth(gtk_widget_get_last_child(gtk_widget_get_first_child(tabs_of(panel))), i);
}

static GtkWidget *panel_with_pages(void) {
  GtkWidget *panel = luma_floating_panel_new("Page 1", "layers");
  luma_floating_panel_set_summary(LUMA_FLOATING_PANEL(panel), "12 layers");
  luma_floating_panel_add_page(LUMA_FLOATING_PANEL(panel), "layers", "Layers", gtk_label_new("layers"));
  luma_floating_panel_add_page(LUMA_FLOATING_PANEL(panel), "assets", "Assets", gtk_label_new("assets"));
  return panel;
}

static void record(GObject *object G_GNUC_UNUSED, const char *key, gpointer data) {
  g_ptr_array_add(data, g_strdup(key));
}

static void test_panel_tree(void) {
  GtkWidget *panel = g_object_ref_sink(panel_with_pages());
  g_assert_true(gtk_widget_has_css_class(panel, "lumaui-creative-panel"));
  g_assert_true(gtk_widget_has_css_class(panel, "left"));
  g_assert_true(gtk_widget_has_css_class(card_of(panel), "lumaui-creative-panel-card"));
  g_assert_true(gtk_widget_has_css_class(nth(card_of(panel), 0), "lumaui-creative-handle"));
  g_assert_false(gtk_widget_get_visible(nth(card_of(panel), 0)));
  GtkWidget *header = header_of(panel);
  g_assert_true(gtk_widget_has_css_class(header, "lumaui-creative-panel-header"));
  g_assert_true(gtk_widget_has_css_class(nth(header, 0), "lumaui-creative-panel-icon"));
  g_assert_true(GTK_IS_LABEL(nth(header, 1)));
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(nth(header, 1))), ==, "Page 1");
  GtkWidget *close = gtk_widget_get_last_child(header);
  g_assert_true(gtk_widget_has_css_class(close, "close"));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(close), ==, "Hide layers");
  luma_floating_panel_set_holds(LUMA_FLOATING_PANEL(panel), "outline");
  g_assert_cmpstr(gtk_widget_get_tooltip_text(gtk_widget_get_last_child(header_of(panel))), ==, "Hide outline");
  g_assert_true(gtk_widget_has_css_class(tabs_of(panel), "lumaui-creative-tabs"));
  g_assert_true(gtk_widget_get_visible(tabs_of(panel)));
  g_assert_true(gtk_widget_has_css_class(tab_at(panel, 0), "on"));
  GtkWidget *indicator = nth(gtk_widget_get_first_child(tabs_of(panel)), 1);
  g_assert_true(gtk_widget_has_css_class(indicator, "lumaui-creative-tabs-indicator"));
  g_assert_true(gtk_widget_has_css_class(nth(card_of(panel), 3), "lumaui-creative-panel-scroll"));
  g_assert_true(gtk_widget_has_css_class(pill_of(panel), "lumaui-creative-pill"));
  g_assert_false(gtk_widget_get_visible(pill_of(panel)));
  /* One page: no tabs. */
  luma_floating_panel_set_child(LUMA_FLOATING_PANEL(panel), gtk_label_new("only"));
  g_assert_false(gtk_widget_get_visible(tabs_of(panel)));
  g_assert_cmpstr(luma_floating_panel_get_page(LUMA_FLOATING_PANEL(panel)), ==, "main");
  g_object_unref(panel);
}

static void test_panel_pages(void) {
  GtkWidget *panel = g_object_ref_sink(panel_with_pages());
  g_autoptr(GPtrArray) keys = g_ptr_array_new_with_free_func(g_free);
  g_signal_connect(panel, "page-changed", G_CALLBACK(record), keys);
  g_assert_cmpstr(luma_floating_panel_get_page(LUMA_FLOATING_PANEL(panel)), ==, "layers");
  luma_floating_panel_set_page(LUMA_FLOATING_PANEL(panel), "assets");
  g_assert_cmpuint(keys->len, ==, 0);
  g_assert_true(gtk_widget_has_css_class(tab_at(panel, 1), "on"));
  /* A person picks Layers. */
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(tab_at(panel, 0)), TRUE);
  g_assert_cmpuint(keys->len, ==, 1);
  g_assert_cmpstr(g_ptr_array_index(keys, 0), ==, "layers");
  g_assert_cmpstr(luma_floating_panel_get_page(LUMA_FLOATING_PANEL(panel)), ==, "layers");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*already a page*");
  luma_floating_panel_add_page(LUMA_FLOATING_PANEL(panel), "layers", "Again", gtk_label_new("x"));
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown panel page*");
  luma_floating_panel_set_page(LUMA_FLOATING_PANEL(panel), "nope");
  g_test_assert_expected_messages();
  g_object_unref(panel);
}

static void test_panel_fold(void) {
  GtkWidget *panel = g_object_ref_sink(panel_with_pages());
  GtkWidget *close = gtk_widget_get_last_child(header_of(panel));
  g_signal_emit_by_name(close, "clicked");
  g_assert_true(luma_floating_panel_get_folded(LUMA_FLOATING_PANEL(panel)));
  g_assert_true(gtk_widget_has_css_class(panel, "folded"));
  g_assert_false(gtk_widget_get_visible(card_of(panel)));
  g_assert_true(gtk_widget_get_visible(pill_of(panel)));
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(pill_of(panel)));
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(nth(line, 1))), ==, "Page 1");
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(nth(line, 2))), ==, "12 layers");
  g_assert_null(gtk_widget_get_tooltip_text(pill_of(panel)));
  g_signal_emit_by_name(pill_of(panel), "clicked");
  g_assert_false(luma_floating_panel_get_folded(LUMA_FLOATING_PANEL(panel)));
  g_assert_true(gtk_widget_get_visible(card_of(panel)));
  /* Not closable: no Hide button. */
  luma_floating_panel_set_closable(LUMA_FLOATING_PANEL(panel), FALSE);
  g_assert_false(gtk_widget_has_css_class(gtk_widget_get_last_child(header_of(panel)), "close"));
  g_object_unref(panel);
}

static void test_panel_header_kinds(void) {
  GtkWidget *panel = g_object_ref_sink(luma_floating_panel_new("Rectangle 4", "square"));
  g_autoptr(GPtrArray) names = g_ptr_array_new_with_free_func(g_free);
  g_signal_connect(panel, "renamed", G_CALLBACK(record), names);
  luma_floating_panel_set_title_editable(LUMA_FLOATING_PANEL(panel), TRUE);
  GtkWidget *entry = nth(header_of(panel), 1);
  g_assert_true(GTK_IS_ENTRY(entry));
  g_assert_true(gtk_widget_has_css_class(entry, "lumaui-creative-panel-title"));
  gtk_editable_set_text(GTK_EDITABLE(entry), "  Hero card ");
  g_signal_emit_by_name(entry, "activate");
  g_assert_cmpuint(names->len, ==, 1);
  g_assert_cmpstr(g_ptr_array_index(names, 0), ==, "Hero card");
  g_assert_cmpstr(luma_floating_panel_get_title(LUMA_FLOATING_PANEL(panel)), ==, "Hero card");
  /* An empty name is refused and the old one comes back. */
  gtk_editable_set_text(GTK_EDITABLE(entry), "   ");
  g_signal_emit_by_name(entry, "activate");
  g_assert_cmpuint(names->len, ==, 1);
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(entry)), ==, "Hero card");
  /* A title menu: a picker, and no icon leads. */
  g_autoptr(GMenu) pages = g_menu_new();
  g_menu_append(pages, "Page 1", "win.page::1");
  luma_floating_panel_set_title_menu(LUMA_FLOATING_PANEL(panel), G_MENU_MODEL(pages));
  g_assert_true(gtk_widget_has_css_class(header_of(panel), "menu"));
  GtkWidget *first = nth(header_of(panel), 0);
  g_assert_true(GTK_IS_BUTTON(first));
  g_assert_true(gtk_widget_has_css_class(first, "lumaui-creative-panel-title"));
  /* More. */
  g_autoptr(GMenu) more = g_menu_new();
  g_menu_append(more, "Duplicate", "win.duplicate");
  luma_floating_panel_set_more_menu(LUMA_FLOATING_PANEL(panel), G_MENU_MODEL(more));
  g_assert_true(gtk_widget_has_css_class(nth(header_of(panel), 1), "more"));
  g_object_unref(panel);
}

static GtkWidget *tools_bar(void) {
  /* CR1 accepts any tool widget; exercise that contract without CR2. */
  GtkWidget *tools = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
  gtk_widget_add_css_class(tools, "lumaui-creative-tools");
  GtkWidget *move = gtk_button_new_with_label("Move");
  GtkWidget *frame = gtk_button_new_with_label("Frame");
  gtk_widget_add_css_class(move, "lumaui-creative-tool");
  gtk_widget_add_css_class(frame, "lumaui-creative-tool");
  gtk_box_append(GTK_BOX(tools), move);
  gtk_box_append(GTK_BOX(tools), frame);
  return tools;
}

static GtkWidget *full_workspace(GtkWidget **left, GtkWidget **right, GtkWidget **zoom, GtkWidget **tools) {
  GtkWidget *content = gtk_label_new("canvas");
  GtkWidget *workspace = luma_creative_workspace_new(content);
  *left = panel_with_pages();
  *right = luma_floating_panel_new("Rectangle 4", "square");
  luma_floating_panel_set_child(LUMA_FLOATING_PANEL(*right), gtk_label_new("inspector"));
  luma_floating_panel_set_closable(LUMA_FLOATING_PANEL(*right), FALSE);
  *zoom = gtk_button_new_with_label("100%");
  *tools = tools_bar();
  luma_creative_workspace_set_left(LUMA_CREATIVE_WORKSPACE(workspace), LUMA_FLOATING_PANEL(*left));
  luma_creative_workspace_set_right(LUMA_CREATIVE_WORKSPACE(workspace), LUMA_FLOATING_PANEL(*right));
  luma_creative_workspace_set_zoom(LUMA_CREATIVE_WORKSPACE(workspace), *zoom);
  luma_creative_workspace_set_tools(LUMA_CREATIVE_WORKSPACE(workspace), *tools);
  return workspace;
}

static void test_workspace_tree(void) {
  GtkWidget *left, *right, *zoom, *tools;
  GtkWidget *workspace = g_object_ref_sink(full_workspace(&left, &right, &zoom, &tools));
  g_assert_true(gtk_widget_has_css_class(workspace, "lumaui-creative-workspace"));
  /* Painting order: grid node, content, left, right, zoom, tools. */
  g_assert_true(gtk_widget_has_css_class(nth(workspace, 0), "lumaui-creative-grid"));
  g_assert_true(GTK_IS_LABEL(nth(workspace, 1)));
  g_assert_true(nth(workspace, 2) == left);
  g_assert_true(nth(workspace, 3) == right);
  g_assert_true(nth(workspace, 4) == zoom);
  g_assert_true(nth(workspace, 5) == tools);
  g_assert_true(gtk_widget_has_css_class(right, "right"));
  g_assert_true(luma_floating_panel_get_side(LUMA_FLOATING_PANEL(right)) == LUMA_PANEL_SIDE_RIGHT);
  g_assert_true(gtk_widget_has_css_class(zoom, "lumaui-creative-zoom"));
  g_assert_true(luma_creative_workspace_get_left(LUMA_CREATIVE_WORKSPACE(workspace)) == LUMA_FLOATING_PANEL(left));
  /* Replacing the tools keeps the order. */
  GtkWidget *other = tools_bar();
  luma_creative_workspace_set_tools(LUMA_CREATIVE_WORKSPACE(workspace), other);
  g_assert_true(gtk_widget_get_last_child(workspace) == other);
  luma_creative_workspace_set_left(LUMA_CREATIVE_WORKSPACE(workspace), NULL);
  g_assert_null(luma_creative_workspace_get_left(LUMA_CREATIVE_WORKSPACE(workspace)));
  g_assert_cmpfloat(luma_creative_grid_spacing(1.0), ==, 64.0);
  g_assert_cmpfloat(luma_creative_grid_spacing(0.25), ==, 32.0);
  g_assert_cmpfloat(luma_creative_grid_spacing(2.0), ==, 128.0);
  g_object_unref(workspace);
}

static graphene_rect_t bounds_in(GtkWidget *widget, GtkWidget *ancestor) {
  graphene_rect_t bounds;
  g_assert_true(gtk_widget_compute_bounds(widget, ancestor, &bounds));
  return bounds;
}

static void test_workspace_layout(void) {
  GtkWidget *left, *right, *zoom, *tools;
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  GtkWidget *workspace = full_workspace(&left, &right, &zoom, &tools);
  gtk_window_set_child(GTK_WINDOW(window), workspace);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  int width = gtk_widget_get_width(workspace), height = gtk_widget_get_height(workspace);
  g_assert_cmpint(width, >=, 700);
  graphene_rect_t card = bounds_in(card_of(left), workspace);
  g_assert_cmpfloat(card.origin.x, ==, 10);
  g_assert_cmpfloat(card.origin.y, ==, 10);
  g_assert_cmpfloat(card.size.width, ==, 244);
  graphene_rect_t inspector = bounds_in(card_of(right), workspace);
  g_assert_cmpfloat(inspector.size.width, ==, 268);
  g_assert_cmpfloat(inspector.origin.x + inspector.size.width, ==, width - 10);
  graphene_rect_t bar = bounds_in(tools, workspace);
  g_assert_cmpfloat(bar.origin.y + bar.size.height, ==, height - 16);
  g_assert_cmpfloat(fabs(bar.origin.x + bar.size.width / 2 - width / 2.0), <=, 1);
  g_assert_cmpfloat(bar.size.height, ==, 48);
  g_assert_cmpfloat(card.origin.y + card.size.height, <=, height - 82);
  graphene_rect_t corner = bounds_in(zoom, workspace);
  g_assert_cmpfloat(corner.origin.x + corner.size.width, ==, width - 16);
  g_assert_cmpfloat(corner.origin.y + corner.size.height, ==, height - 20);
  g_assert_false(luma_creative_workspace_get_is_phone(LUMA_CREATIVE_WORKSPACE(workspace)));

  /* Focus mode: the panels and the zoom leave, the tools stay. */
  luma_creative_workspace_set_focused(LUMA_CREATIVE_WORKSPACE(workspace), TRUE);
  g_assert_true(gtk_widget_has_css_class(left, "gone"));
  g_assert_true(gtk_widget_has_css_class(zoom, "gone"));
  g_assert_false(gtk_widget_get_can_target(right));
  wait_ms(600);
  g_assert_false(gtk_widget_get_child_visible(left));
  g_assert_true(gtk_widget_get_child_visible(tools));
  luma_creative_workspace_set_focused(LUMA_CREATIVE_WORKSPACE(workspace), FALSE);
  g_assert_true(gtk_widget_get_child_visible(left));
  settle();
  g_assert_false(gtk_widget_has_css_class(left, "gone"));
  g_assert_true(gtk_widget_get_can_target(left));
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_workspace_phone(void) {
  GtkWidget *left, *right, *zoom, *tools;
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  GtkWidget *workspace = full_workspace(&left, &right, &zoom, &tools);
  gtk_window_set_child(GTK_WINDOW(window), workspace);
  luma_floating_panel_set_folded(LUMA_FLOATING_PANEL(right), TRUE);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  gtk_window_set_default_size(GTK_WINDOW(window), 400, 700);
  settle();
  g_assert_true(luma_creative_workspace_get_is_phone(LUMA_CREATIVE_WORKSPACE(workspace)));
  g_assert_true(gtk_widget_has_css_class(workspace, "phone"));
  /* Pills on a phone, and no zoom corner. */
  g_assert_true(luma_floating_panel_get_folded(LUMA_FLOATING_PANEL(left)));
  g_assert_true(luma_floating_panel_get_folded(LUMA_FLOATING_PANEL(right)));
  g_assert_true(gtk_widget_has_css_class(zoom, "gone"));
  /* Opened, a panel is the bottom drawer. */
  luma_floating_panel_set_folded(LUMA_FLOATING_PANEL(left), FALSE);
  settle();
  g_assert_true(luma_floating_panel_get_is_drawer(LUMA_FLOATING_PANEL(left)));
  g_assert_true(gtk_widget_has_css_class(left, "drawer"));
  g_assert_true(gtk_widget_get_visible(nth(card_of(left), 0)));
  GtkWidget *close = gtk_widget_get_last_child(header_of(left));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(close), ==, "Close");
  int width = gtk_widget_get_width(workspace), height = gtk_widget_get_height(workspace);
  graphene_rect_t drawer = bounds_in(left, workspace);
  g_assert_cmpfloat(drawer.origin.x, ==, 0);
  g_assert_cmpfloat(drawer.size.width, ==, width);
  g_assert_cmpfloat(drawer.origin.y + drawer.size.height, ==, height);
  g_assert_cmpfloat(drawer.size.height, <=, height * 0.72 + 1);
  /* One drawer at a time; the inspector's drawer can be closed though it isn't closable. */
  luma_floating_panel_set_folded(LUMA_FLOATING_PANEL(right), FALSE);
  g_assert_true(luma_floating_panel_get_folded(LUMA_FLOATING_PANEL(left)));
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_last_child(header_of(right)), "close"));
  /* Back on a computer, the panels are as they were. */
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  settle();
  g_assert_false(luma_creative_workspace_get_is_phone(LUMA_CREATIVE_WORKSPACE(workspace)));
  g_assert_false(luma_floating_panel_get_folded(LUMA_FLOATING_PANEL(left)));
  g_assert_true(luma_floating_panel_get_folded(LUMA_FLOATING_PANEL(right)));
  g_assert_false(gtk_widget_has_css_class(left, "drawer"));
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_title_menu_opens(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  GtkWidget *panel = panel_with_pages();
  g_autoptr(GMenu) pages = g_menu_new();
  g_autoptr(GMenuItem) item = g_menu_item_new("Page 2", "win.page::2");
  g_menu_item_set_attribute(item, "lumaui-icon", "s", "file");
  g_menu_item_set_attribute(item, "lumaui-selected", "b", TRUE);
  g_menu_append_item(pages, item);
  g_autoptr(GMenu) section = g_menu_new();
  g_menu_append(section, "New page", "win.new-page");
  g_menu_append_section(pages, NULL, G_MENU_MODEL(section));
  luma_floating_panel_set_title_menu(LUMA_FLOATING_PANEL(panel), G_MENU_MODEL(pages));
  GtkWidget *workspace = luma_creative_workspace_new(gtk_label_new("canvas"));
  luma_creative_workspace_set_left(LUMA_CREATIVE_WORKSPACE(workspace), LUMA_FLOATING_PANEL(panel));
  gtk_window_set_child(GTK_WINDOW(window), workspace);
  luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_signal_emit_by_name(nth(header_of(panel), 0), "clicked");
  settle();
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/creative/panel/tree", test_panel_tree);
  g_test_add_func("/lumaui/creative/panel/pages", test_panel_pages);
  g_test_add_func("/lumaui/creative/panel/fold", test_panel_fold);
  g_test_add_func("/lumaui/creative/panel/header", test_panel_header_kinds);
  g_test_add_func("/lumaui/creative/panel/title-menu", test_title_menu_opens);
  g_test_add_func("/lumaui/creative/workspace/tree", test_workspace_tree);
  g_test_add_func("/lumaui/creative/workspace/layout", test_workspace_layout);
  g_test_add_func("/lumaui/creative/workspace/phone", test_workspace_phone);
  return g_test_run();
}
