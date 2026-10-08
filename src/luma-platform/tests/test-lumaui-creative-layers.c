/* SPDX-License-Identifier: Apache-2.0 */
/* LumaLayer and LumaLayerTree (LumaUI creative, KB-D). */
#include "luma-ui.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void settle(void) {
  gboolean done = FALSE;
  g_timeout_add(100, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static GtkWidget *nth(GtkWidget *parent, int index) {
  GtkWidget *child = gtk_widget_get_first_child(parent);
  for (int i = 0; i < index && child != NULL; i++)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static int count(GtkWidget *parent) {
  int n = 0;
  for (GtkWidget *child = gtk_widget_get_first_child(parent); child != NULL; child = gtk_widget_get_next_sibling(child))
    n++;
  return n;
}

static GtkWidget *list_of(GtkWidget *tree) { return gtk_widget_get_first_child(tree); }
static GtkWidget *row_box(GtkWidget *tree, int index) {
  return gtk_list_box_row_get_child(gtk_list_box_get_row_at_index(GTK_LIST_BOX(list_of(tree)), index));
}
static int rows(GtkWidget *tree) { return count(list_of(tree)); }

/* A frame holding a title and a photo, over a rectangle. */
static GListStore *document(void) {
  GListStore *store = g_list_store_new(LUMA_TYPE_LAYER);
  g_autoptr(LumaLayer) hero = luma_layer_new("hero", "Hero", "hash");
  g_autoptr(LumaLayer) title = luma_layer_new("title", "Title", "type");
  g_autoptr(LumaLayer) photo = luma_layer_new("photo", "Photo", "image");
  g_autoptr(LumaLayer) rect = luma_layer_new("rect", "Rectangle 4", "square");
  luma_layer_append(hero, title);
  luma_layer_append(hero, photo);
  g_list_store_append(store, hero);
  g_list_store_append(store, rect);
  return store;
}

static void count_signal(gpointer instance G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }
static void layer_signal(gpointer instance G_GNUC_UNUSED, LumaLayer *layer, gpointer data) {
  g_ptr_array_add(data, g_strdup(luma_layer_get_id(layer)));
}

static void test_layer(void) {
  g_autoptr(LumaLayer) layer = luma_layer_new("a", "A", "square");
  g_assert_true(luma_layer_get_visible(layer));
  g_assert_true(luma_layer_get_can_hide(layer));
  g_assert_true(luma_layer_get_expanded(layer));
  g_assert_cmpuint(g_list_model_get_n_items(luma_layer_get_children(layer)), ==, 0);
  luma_layer_set_detail(layer, "E2:E24");
  g_assert_cmpstr(luma_layer_get_detail(layer), ==, "E2:E24");
}

static void test_tree(void) {
  g_autoptr(GListStore) store = document();
  GtkWidget *tree = g_object_ref_sink(luma_layer_tree_new(G_LIST_MODEL(store)));
  g_assert_true(gtk_widget_has_css_class(list_of(tree), "lumaui-creative-layers"));
  g_assert_cmpint(rows(tree), ==, 4);
  GtkWidget *hero = row_box(tree, 0);
  g_assert_true(gtk_widget_has_css_class(hero, "lumaui-creative-layer"));
  GtkWidget *caret = nth(hero, 0);
  g_assert_true(GTK_IS_BUTTON(caret));
  g_assert_true(gtk_widget_has_css_class(caret, "lumaui-creative-layer-caret"));
  g_assert_true(gtk_widget_has_css_class(caret, "open"));
  g_assert_cmpstr(gtk_image_get_icon_name(GTK_IMAGE(nth(hero, 1))), ==, "lumaui-hash-symbolic");
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(nth(hero, 2))), ==, "Hero");
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_last_child(hero), "lumaui-creative-layer-eye"));
  /* A child: a blank caret indented one level. */
  GtkWidget *title = row_box(tree, 1);
  g_assert_false(GTK_IS_BUTTON(nth(title, 0)));
  g_assert_cmpint(gtk_widget_get_margin_start(nth(title, 0)), ==, 16);
  g_assert_cmpint(gtk_widget_get_margin_start(nth(hero, 0)), ==, 0);
  /* Collapsing hides the children; the model follows. */
  g_signal_emit_by_name(caret, "clicked");
  settle();
  g_assert_false(luma_layer_get_expanded(luma_layer_tree_find(LUMA_LAYER_TREE(tree), "hero")));
  g_assert_cmpint(rows(tree), ==, 2);
  /* New layers show up. */
  g_autoptr(LumaLayer) text = luma_layer_new("text", "Caption", "type");
  luma_layer_set_can_hide(text, FALSE);
  luma_layer_set_detail(text, "3");
  g_list_store_append(store, text);
  settle();
  g_assert_cmpint(rows(tree), ==, 3);
  GtkWidget *caption = row_box(tree, 2);
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_last_child(caption), "lumaui-creative-layer-detail"));
  g_object_unref(tree);
}

static void test_eye_and_rename(void) {
  g_autoptr(GListStore) store = document();
  GtkWidget *tree = g_object_ref_sink(luma_layer_tree_new(G_LIST_MODEL(store)));
  g_autoptr(GPtrArray) shown = g_ptr_array_new_with_free_func(g_free);
  g_autoptr(GPtrArray) renamed = g_ptr_array_new_with_free_func(g_free);
  g_signal_connect(tree, "visibility-changed", G_CALLBACK(layer_signal), shown);
  g_signal_connect(tree, "renamed", G_CALLBACK(layer_signal), renamed);
  GtkWidget *eye = gtk_widget_get_last_child(row_box(tree, 3));
  g_signal_emit_by_name(eye, "clicked");
  g_assert_cmpuint(shown->len, ==, 1);
  g_assert_cmpstr(g_ptr_array_index(shown, 0), ==, "rect");
  settle();
  LumaLayer *rect = luma_layer_tree_find(LUMA_LAYER_TREE(tree), "rect");
  g_assert_false(luma_layer_get_visible(rect));
  g_assert_true(gtk_widget_has_css_class(row_box(tree, 3), "hidden"));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(gtk_widget_get_last_child(row_box(tree, 3))), ==, "Show Rectangle 4");
  luma_layer_tree_rename(LUMA_LAYER_TREE(tree), "rect");
  GtkWidget *entry = nth(row_box(tree, 3), 2);
  g_assert_true(GTK_IS_ENTRY(entry));
  g_assert_true(gtk_widget_has_css_class(entry, "lumaui-creative-layer-rename"));
  gtk_editable_set_text(GTK_EDITABLE(entry), "Backdrop");
  g_signal_emit_by_name(entry, "activate");
  g_assert_cmpuint(renamed->len, ==, 1);
  g_assert_cmpstr(luma_layer_get_name(rect), ==, "Backdrop");
  settle();
  g_assert_true(GTK_IS_LABEL(nth(row_box(tree, 3), 2)));
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(nth(row_box(tree, 3), 2))), ==, "Backdrop");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown layer*");
  luma_layer_tree_rename(LUMA_LAYER_TREE(tree), "nope");
  g_test_assert_expected_messages();
  g_object_unref(tree);
}

static void test_selection(void) {
  g_autoptr(GListStore) store = document();
  GtkWidget *tree = g_object_ref_sink(luma_layer_tree_new(G_LIST_MODEL(store)));
  int changes = 0;
  g_signal_connect(tree, "selection-changed", G_CALLBACK(count_signal), &changes);
  luma_layer_set_expanded(luma_layer_tree_find(LUMA_LAYER_TREE(tree), "hero"), FALSE);
  settle();
  g_assert_cmpint(rows(tree), ==, 2);
  /* The canvas selects the title: its frame opens, no signal. */
  const char *ids[] = {"title", NULL};
  luma_layer_tree_set_selected(LUMA_LAYER_TREE(tree), ids);
  g_assert_cmpint(changes, ==, 0);
  g_assert_cmpint(rows(tree), ==, 4);
  g_assert_true(gtk_list_box_row_is_selected(gtk_list_box_get_row_at_index(GTK_LIST_BOX(list_of(tree)), 1)));
  g_assert_true(gtk_widget_has_css_class(row_box(tree, 1), "selected"));
  /* A person adds the rectangle. */
  gtk_list_box_select_row(GTK_LIST_BOX(list_of(tree)), gtk_list_box_get_row_at_index(GTK_LIST_BOX(list_of(tree)), 3));
  g_assert_cmpint(changes, ==, 1);
  g_auto(GStrv) now = luma_layer_tree_get_selected(LUMA_LAYER_TREE(tree));
  g_assert_cmpuint(g_strv_length(now), ==, 2);
  g_assert_cmpstr(now[0], ==, "title");
  g_assert_cmpstr(now[1], ==, "rect");
  g_object_unref(tree);
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/creative/layers/layer", test_layer);
  g_test_add_func("/lumaui/creative/layers/tree", test_tree);
  g_test_add_func("/lumaui/creative/layers/eye-rename", test_eye_and_rename);
  g_test_add_func("/lumaui/creative/layers/selection", test_selection);
  return g_test_run();
}
