/* SPDX-License-Identifier: Apache-2.0 */
/* LumaTableHeader, LumaColumnViewHeader, luma_selection_*: the twin of structure_table.py. */
#include "luma-ui.h"
#include "luma-table-header.h"

static GtkWidget *nth(GtkWidget *box, int index) {
  GtkWidget *child = gtk_widget_get_first_child(box);
  for (int i = 0; i < index && child != NULL; i++)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static GtkWidget *arrow_of(GtkWidget *cell) {
  return gtk_widget_get_last_child(gtk_button_get_child(GTK_BUTTON(cell)));
}

typedef struct {
  char *key;
  LumaSortDirection direction;
  int calls;
} Sorted;

static void sorted(LumaTableHeader *header G_GNUC_UNUSED, const char *key, LumaSortDirection direction,
                   gpointer data) {
  Sorted *s = data;
  g_free(s->key);
  s->key = g_strdup(key);
  s->direction = direction;
  s->calls++;
}

static GtkWidget *songs(void) {
  GtkWidget *header = luma_table_header_new();
  LumaTableHeader *h = LUMA_TABLE_HEADER(header);
  luma_table_header_add_column(h, NULL, "", FALSE, 32, FALSE);
  luma_table_header_add_column(h, "title", "Title", TRUE, -1, FALSE);
  luma_table_header_add_column(h, "album", "Album", TRUE, -1, FALSE);
  luma_table_header_add_column(h, "time", "Time", FALSE, 56, TRUE);
  return header;
}

static void test_header(void) {
  GtkWidget *header = g_object_ref_sink(songs());
  LumaTableHeader *h = LUMA_TABLE_HEADER(header);
  g_assert_true(gtk_widget_has_css_class(header, "lumaui-thead"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(header)), ==, GTK_ACCESSIBLE_ROLE_ROW);
  g_assert_true(GTK_IS_LABEL(nth(header, 0)));
  g_assert_true(gtk_widget_has_css_class(nth(header, 0), "lumaui-thead-label"));
  g_assert_true(GTK_IS_BUTTON(nth(header, 1)));
  g_assert_true(gtk_widget_has_css_class(nth(header, 3), "end"));
  g_assert_true(gtk_widget_get_hexpand(nth(header, 1)));
  g_assert_null(luma_table_header_get_sort_key(h));
  Sorted s = {0};
  g_signal_connect(header, "sort-changed", G_CALLBACK(sorted), &s);
  luma_table_header_set_sort(h, "album", LUMA_SORT_DESCENDING);
  g_assert_cmpint(s.calls, ==, 0);
  g_assert_true(gtk_widget_has_css_class(nth(header, 2), "active"));
  g_assert_true(gtk_widget_get_visible(arrow_of(nth(header, 2))));
  g_assert_false(gtk_widget_get_visible(arrow_of(nth(header, 1))));
  /* A click sorts by a heading, again reverses it. */
  g_signal_emit_by_name(nth(header, 1), "clicked");
  g_assert_cmpint(s.calls, ==, 1);
  g_assert_cmpstr(s.key, ==, "title");
  g_assert_cmpint(s.direction, ==, LUMA_SORT_ASCENDING);
  g_assert_false(gtk_widget_has_css_class(nth(header, 2), "active"));
  g_assert_cmpint(luma_table_header_activate_column(h, "title"), ==, LUMA_SORT_DESCENDING);
  g_assert_cmpint(s.direction, ==, LUMA_SORT_DESCENDING);
  g_assert_cmpstr(luma_table_header_get_sort_key(h), ==, "title");
  g_assert_cmpint(luma_table_header_get_sort_direction(h), ==, LUMA_SORT_DESCENDING);
  luma_table_header_set_sort(h, NULL, LUMA_SORT_NONE);
  g_assert_null(luma_table_header_get_sort_key(h));
  g_assert_false(gtk_widget_get_visible(arrow_of(nth(header, 1))));
  /* Refusals. */
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*no sortable column*");
  luma_table_header_set_sort(h, "nope", LUMA_SORT_ASCENDING);
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*ascending or descending*");
  luma_table_header_set_sort(h, "title", LUMA_SORT_NONE);
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*column keys are unique*");
  luma_table_header_add_column(h, "title", "Again", FALSE, -1, FALSE);
  g_test_assert_expected_messages();
  g_free(s.key);
  g_object_unref(header);
}

static void test_align(void) {
  GtkWidget *header = g_object_ref_sink(songs());
  GtkWidget *row = g_object_ref_sink(gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0));
  const char *texts[] = {"1", "Song", "Album", "3:12"};
  for (int i = 0; i < 4; i++)
    gtk_box_append(GTK_BOX(row), gtk_label_new(texts[i]));
  luma_table_header_align(LUMA_TABLE_HEADER(header), row);
  g_assert_true(gtk_widget_has_css_class(row, "lumaui-thead-row"));
  g_assert_true(gtk_widget_get_hexpand(nth(row, 1)));
  int width = 0;
  gtk_widget_get_size_request(nth(row, 3), &width, NULL);
  g_assert_cmpint(width, ==, 56);
  GtkWidget *short_row = g_object_ref_sink(gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0));
  gtk_box_append(GTK_BOX(short_row), gtk_label_new("x"));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*has 4 cells, not 1*");
  luma_table_header_align(LUMA_TABLE_HEADER(header), short_row);
  g_test_assert_expected_messages();
  g_object_unref(short_row);
  g_object_unref(row);
  g_object_unref(header);
}

static void setup(GtkSignalListItemFactory *factory G_GNUC_UNUSED, GObject *item, gpointer data G_GNUC_UNUSED) {
  gtk_list_item_set_child(GTK_LIST_ITEM(item), gtk_label_new(""));
}

static void test_column_view(void) {
  GListStore *store = g_list_store_new(GTK_TYPE_STRING_OBJECT);
  g_list_store_append(store, gtk_string_object_new("b"));
  g_list_store_append(store, gtk_string_object_new("a"));
  GtkWidget *view = g_object_ref_sink(gtk_column_view_new(NULL));
  GtkSortListModel *sorted_model = gtk_sort_list_model_new(G_LIST_MODEL(store),
                                                            g_object_ref(gtk_column_view_get_sorter(GTK_COLUMN_VIEW(view))));
  gtk_column_view_set_model(GTK_COLUMN_VIEW(view), GTK_SELECTION_MODEL(gtk_no_selection_new(G_LIST_MODEL(sorted_model))));
  const char *ids[] = {"name", "size"};
  GtkColumnViewColumn *columns[2];
  for (int i = 0; i < 2; i++) {
    GtkListItemFactory *factory = gtk_signal_list_item_factory_new();
    g_signal_connect(factory, "setup", G_CALLBACK(setup), NULL);
    columns[i] = gtk_column_view_column_new(ids[i], factory);
    gtk_column_view_column_set_id(columns[i], ids[i]);
    gtk_column_view_column_set_sorter(
        columns[i], GTK_SORTER(gtk_string_sorter_new(gtk_property_expression_new(GTK_TYPE_STRING_OBJECT, NULL, "string"))));
    gtk_column_view_append_column(GTK_COLUMN_VIEW(view), columns[i]);
  }
  LumaColumnViewHeader *header = luma_table_header_for_column_view(GTK_COLUMN_VIEW(view));
  g_assert_true(luma_table_header_for_column_view(GTK_COLUMN_VIEW(view)) == header);
  g_assert_true(gtk_widget_has_css_class(view, "lumaui-table"));
  g_assert_true(gtk_widget_has_css_class(view, "lumaui-selection"));
  g_assert_null(luma_column_view_header_get_sort_key(header));
  gtk_column_view_sort_by_column(GTK_COLUMN_VIEW(view), columns[1], GTK_SORT_DESCENDING);
  g_assert_cmpstr(luma_column_view_header_get_sort_key(header), ==, "size");
  g_assert_cmpint(luma_column_view_header_get_sort_direction(header), ==, LUMA_SORT_DESCENDING);
  /* The titles wear "active". */
  GtkWidget *titles = gtk_widget_get_first_child(view);
  while (titles != NULL && g_strcmp0(gtk_widget_get_css_name(titles), "header") != 0)
    titles = gtk_widget_get_next_sibling(titles);
  g_assert_nonnull(titles);
  g_assert_false(gtk_widget_has_css_class(nth(titles, 0), "active"));
  g_assert_true(gtk_widget_has_css_class(nth(titles, 1), "active"));
  gtk_column_view_sort_by_column(GTK_COLUMN_VIEW(view), NULL, GTK_SORT_ASCENDING);
  g_assert_null(luma_column_view_header_get_sort_key(header));
  g_assert_false(gtk_widget_has_css_class(nth(titles, 1), "active"));
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 640, 400);
  gtk_window_set_child(GTK_WINDOW(window), view);
  gtk_window_present(GTK_WINDOW(window));
  while (g_main_context_iteration(NULL, FALSE));
  g_assert_true(gtk_widget_get_mapped(titles));
  /* Search results reuse the live view but have no sortable heading row. */
  g_assert_true(luma_column_view_header_get_visible(header));
  int shown_min = 0, shown_nat = 0, hidden_min = 0, hidden_nat = 0;
  gtk_widget_measure(view, GTK_ORIENTATION_VERTICAL, 400, &shown_min, &shown_nat, NULL, NULL);
  int heading_min = 0, heading_nat = 0;
  gtk_widget_measure(titles, GTK_ORIENTATION_VERTICAL, 400, &heading_min, &heading_nat, NULL, NULL);
  g_assert_cmpint(heading_nat, >, 0);
  luma_column_view_header_set_visible(header, FALSE);
  g_assert_false(luma_column_view_header_get_visible(header));
  g_assert_false(gtk_widget_get_visible(titles));
  while (g_main_context_iteration(NULL, FALSE));
  g_assert_false(gtk_widget_get_mapped(titles));
  gtk_widget_measure(view, GTK_ORIENTATION_VERTICAL, 400, &hidden_min, &hidden_nat, NULL, NULL);
  g_assert_cmpint(shown_min - hidden_min, ==, heading_min);
  g_assert_cmpint(shown_nat - hidden_nat, ==, heading_nat);
  gtk_column_view_sort_by_column(GTK_COLUMN_VIEW(view), columns[0], GTK_SORT_ASCENDING);
  g_assert_cmpstr(luma_column_view_header_get_sort_key(header), ==, "name");
  g_assert_false(gtk_widget_get_visible(titles));
  luma_column_view_header_sync(header);
  g_assert_false(gtk_widget_get_visible(titles));
  luma_column_view_header_set_visible(header, TRUE);
  while (g_main_context_iteration(NULL, FALSE));
  g_assert_true(gtk_widget_get_visible(titles));
  g_assert_true(gtk_widget_get_mapped(titles));
  g_assert_true(luma_column_view_header_get_visible(header));
  int restored_min = 0, restored_nat = 0;
  gtk_widget_measure(view, GTK_ORIENTATION_VERTICAL, 400, &restored_min, &restored_nat, NULL, NULL);
  g_assert_cmpint(restored_min, ==, shown_min);
  g_assert_cmpint(restored_nat, ==, shown_nat);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(view);
  g_object_unref(store);
}

static void test_selection(void) {
  GtkWidget *list = g_object_ref_sink(gtk_list_box_new());
  luma_selection_apply(list);
  g_assert_true(gtk_widget_has_css_class(list, "lumaui-selection"));
  GtkWidget *label = g_object_ref_sink(gtk_label_new("x"));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*luma_selection_apply takes*");
  luma_selection_apply(label);
  g_test_assert_expected_messages();
  luma_selection_trail(label, TRUE);
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-trail"));
  luma_selection_mark(label, TRUE);
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-selected"));
  g_assert_false(gtk_widget_has_css_class(label, "lumaui-trail"));
  luma_selection_trail(label, TRUE);
  g_assert_false(gtk_widget_has_css_class(label, "lumaui-selected"));
  luma_selection_mark(label, FALSE);
  g_object_unref(label);
  g_object_unref(list);
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/table-header/header", test_header);
  g_test_add_func("/lumaui/table-header/align", test_align);
  g_test_add_func("/lumaui/table-header/column-view", test_column_view);
  g_test_add_func("/lumaui/table-header/selection", test_selection);
  return g_test_run();
}
