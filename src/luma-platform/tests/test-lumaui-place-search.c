/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI PlaceSearch against content_place.py. */
#include "luma-ui.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void spin(void) {
  gboolean done = FALSE;
  g_timeout_add(250, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static void test_parse_coordinates(void) {
  double lat = 0, lon = 0;
  g_assert_true(luma_parse_coordinates("37.32, -122.03", &lat, &lon));
  g_assert_cmpfloat_with_epsilon(lat, 37.32, 1e-9);
  g_assert_cmpfloat_with_epsilon(lon, -122.03, 1e-9);
  g_assert_true(luma_parse_coordinates(" 0 ; 180 ", &lat, &lon));
  g_assert_cmpfloat(lon, ==, 180);
  const char *refused[] = {"91, 0", "0, -181", "a, b", "1,2,3", "", "37.32", "nan, 0", "inf, 0", "1x, 2"};
  for (guint i = 0; i < G_N_ELEMENTS(refused); i++)
    g_assert_false(luma_parse_coordinates(refused[i], NULL, NULL));
}

static GListStore *places(void) {
  GListStore *store = g_list_store_new(LUMA_TYPE_PLACE_RESULT);
  const char *names[][2] = {{"Cupertino", "California"}, {"Los Santos", ""},  {"San Jose", "California"},
                            {"Sunnyvale", "near San Jose"}, {"Santa Cruz", "California"}, {"Oslo", "Norway"}};
  for (guint i = 0; i < G_N_ELEMENTS(names); i++) {
    g_autoptr(LumaPlaceResult) place = luma_place_result_new(names[i][0], names[i][1], NULL);
    g_list_store_append(store, place);
  }
  return store;
}

static GtkWidget *list_of(void) {
  /* The list floats in the window's layer host: find it by its class. */
  GtkWidget *found = NULL;
  GListModel *toplevels = gtk_window_get_toplevels();
  for (guint i = 0; found == NULL && i < g_list_model_get_n_items(toplevels); i++) {
    g_autoptr(GtkWidget) window = g_list_model_get_item(toplevels, i);
    GPtrArray *stack = g_ptr_array_new();
    g_ptr_array_add(stack, window);
    while (stack->len > 0 && found == NULL) {
      GtkWidget *widget = g_ptr_array_steal_index(stack, stack->len - 1);
      if (gtk_widget_has_css_class(widget, "lumaui-place-list"))
        found = widget;
      for (GtkWidget *child = gtk_widget_get_first_child(widget); child; child = gtk_widget_get_next_sibling(child))
        g_ptr_array_add(stack, child);
    }
    g_ptr_array_unref(stack);
  }
  return found;
}

static const char *row_name(GtkWidget *row) {
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(row));
  GtkWidget *text = gtk_widget_get_next_sibling(gtk_widget_get_first_child(line));
  return gtk_label_get_label(GTK_LABEL(gtk_widget_get_first_child(text)));
}

static void picked(LumaPlaceSearch *search G_GNUC_UNUSED, LumaPlaceResult *place, gpointer data) {
  g_free(*(char **)data);
  *(char **)data = g_strdup(luma_place_result_get_name(place));
}

static gboolean excluded(gpointer item, gpointer data G_GNUC_UNUSED) {
  return g_str_equal(luma_place_result_get_name(item), "Sunnyvale");
}

static void test_place_search(void) {
  GtkWidget *widget = luma_place_search_new(NULL);
  LumaPlaceSearch *self = LUMA_PLACE_SEARCH(widget);
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-place-search"));
  g_assert_cmpint(gtk_widget_get_valign(widget), ==, GTK_ALIGN_END);
  GtkWidget *glyph = gtk_widget_get_first_child(widget);
  GtkWidget *entry = gtk_widget_get_next_sibling(glyph);
  g_assert_true(gtk_widget_has_css_class(glyph, "lumaui-place-search-icon"));
  g_assert_true(GTK_IS_TEXT(entry));
  g_assert_true(gtk_widget_has_css_class(entry, "lumaui-place-entry"));
  g_assert_cmpstr(gtk_text_get_placeholder_text(GTK_TEXT(entry)), ==, "Add a city or ZIP");

  g_autoptr(GListStore) store = places();
  luma_place_search_set_places(self, G_LIST_MODEL(store));
  char *chosen = NULL;
  g_signal_connect(widget, "picked", G_CALLBACK(picked), &chosen);

  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 500, 400);
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_vexpand(box, TRUE);
  gtk_box_append(GTK_BOX(box), widget);
  gtk_widget_set_vexpand(widget, TRUE);
  gtk_window_set_child(GTK_WINDOW(window), box);
  gtk_window_present(GTK_WINDOW(window));
  spin();

  /* After the typing pause: names that start with it, then names that contain it. */
  luma_place_search_set_text(self, "san");
  g_assert_false(luma_place_search_get_list_shown(self));
  for (int i = 0; i < 8 && !luma_place_search_get_list_shown(self); i++)
    spin();
  g_assert_true(luma_place_search_get_list_shown(self));
  GtkWidget *list = list_of();
  g_assert_nonnull(list);
  const char *order[] = {"San Jose", "Santa Cruz", "Los Santos", "Sunnyvale"};
  GtkWidget *row = gtk_widget_get_first_child(list);
  for (guint i = 0; i < G_N_ELEMENTS(order); i++, row = gtk_widget_get_next_sibling(row)) {
    g_assert_true(gtk_widget_has_css_class(row, "lumaui-place-row"));
    g_assert_cmpstr(row_name(row), ==, order[i]);
    g_assert_true(gtk_widget_has_css_class(row, "on") == (i == 0));
  }
  g_assert_null(row);

  /* Places already added are left out. */
  g_autoptr(GtkFilter) filter = GTK_FILTER(gtk_custom_filter_new(excluded, NULL, NULL));
  luma_place_search_set_exclude(self, filter);
  luma_place_search_search_now(self);
  g_assert_cmpstr(row_name(gtk_widget_get_last_child(list)), ==, "Los Santos");

  g_assert_true(luma_place_search_pick(self, 1));
  g_assert_cmpstr(chosen, ==, "Santa Cruz");
  g_assert_cmpstr(luma_place_search_get_text(self), ==, "");
  g_assert_false(luma_place_search_get_list_shown(self));
  g_assert_false(luma_place_search_pick(self, -1));

  luma_place_search_set_text(self, "zzz");
  luma_place_search_search_now(self);
  g_assert_true(luma_place_search_get_list_shown(self));
  GtkWidget *none = gtk_widget_get_first_child(list);
  g_assert_true(gtk_widget_has_css_class(none, "lumaui-place-none"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(none)), ==, "No places match");
  luma_place_search_close_list(self);
  g_assert_false(luma_place_search_get_list_shown(self));
  g_free(chosen);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void answer(LumaPlaceSearch *search, const char *query, gpointer data G_GNUC_UNUSED) {
  g_autoptr(GListStore) store = g_list_store_new(LUMA_TYPE_PLACE_RESULT);
  for (int i = 0; i < 8; i++) {
    g_autofree char *name = g_strdup_printf("%s %d", query, i);
    g_autoptr(LumaPlaceResult) place = luma_place_result_new(name, NULL, NULL);
    g_list_store_append(store, place);
  }
  luma_place_search_set_results(search, G_LIST_MODEL(store));
}

static void test_remote_provider(void) {
  GtkWidget *widget = luma_place_search_new("Find a place");
  LumaPlaceSearch *self = LUMA_PLACE_SEARCH(widget);
  g_signal_connect(widget, "search", G_CALLBACK(answer), NULL);
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 400, 300);
  gtk_window_set_child(GTK_WINDOW(window), widget);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  luma_place_search_set_text(self, "Oslo");
  luma_place_search_search_now(self);
  GtkWidget *list = list_of();
  /* Five at most. */
  int rows = 0;
  for (GtkWidget *row = gtk_widget_get_first_child(list); row; row = gtk_widget_get_next_sibling(row))
    rows++;
  g_assert_cmpint(rows, ==, 5);
  g_assert_cmpstr(row_name(gtk_widget_get_first_child(list)), ==, "Oslo 0");
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  g_test_add_func("/lumaui/place-search/parse-coordinates", test_parse_coordinates);
  g_test_add_func("/lumaui/place-search/search", test_place_search);
  g_test_add_func("/lumaui/place-search/remote-provider", test_remote_provider);
  return g_test_run();
}
