/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-title-island.h"
#include "luma-sidebar-toggle.h"
#include <adwaita.h>

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}
static void spin(void) {
  gboolean done = FALSE;
  g_timeout_add(300, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static GtkWidget *find_class(GtkWidget *widget, const char *css) {
  if (gtk_widget_has_css_class(widget, css))
    return widget;
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child; child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = find_class(child, css);
    if (found)
      return found;
  }
  return NULL;
}

static void count(gpointer instance G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }

static int grows;
static GtkWidget *places(LumaTitleIsland *island G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  grows++;
  GtkWidget *list = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  for (int i = 0; i < 4; i++)
    gtk_box_append(GTK_BOX(list), gtk_button_new_with_label("A place"));
  return list;
}

static int grown_true, grown_false;
static void grown_changed(LumaTitleIsland *island G_GNUC_UNUSED, gboolean grown, gpointer data G_GNUC_UNUSED) {
  (*(grown ? &grown_true : &grown_false))++;
}

static void assert_glyph(GtkWidget *island, const char *icon) {
  GtkWidget *lead = find_class(island, "lumaui-title-island-lead");
  g_autofree char *expected = luma_ui_icon_name(icon);
  g_assert_cmpstr(gtk_image_get_icon_name(GTK_IMAGE(gtk_button_get_child(GTK_BUTTON(lead)))), ==,
                  expected);
}

static void test_menu_icon(void) {
  GtkWidget *widget = g_object_ref_sink(luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_MENU, "Drive", ""));
  LumaTitleIsland *island = LUMA_TITLE_ISLAND(widget);
  luma_title_island_set_lead_icon(island, "hard-drive");
  assert_glyph(widget, "hard-drive");
  luma_title_island_set_grow_widget(island, gtk_label_new("Drives"), LUMA_TITLE_ISLAND_GROWS_MENU);
  luma_title_island_grow_into(island, NULL);
  assert_glyph(widget, "x");
  luma_title_island_set_lead_icon(island, "usb");
  assert_glyph(widget, "x");
  luma_title_island_fold(island);
  assert_glyph(widget, "usb");
  luma_title_island_set_lead(island, LUMA_TITLE_ISLAND_LEAD_BACK, NULL);
  assert_glyph(widget, "chevron-left");
  luma_title_island_set_lead(island, LUMA_TITLE_ISLAND_LEAD_MENU, NULL);
  assert_glyph(widget, "usb");
  luma_title_island_set_lead_icon(island, NULL);
  assert_glyph(widget, "menu");
  g_object_unref(widget);
}

static void test_island(void) {
  GtkWidget *island = g_object_ref_sink(luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_MENU, "Inbox", "nick@luma"));
  g_assert_true(gtk_widget_has_css_class(island, "lumaui-title-island"));
  GtkWidget *row = find_class(island, "lumaui-title-island-row");
  GtkWidget *lead = find_class(island, "lumaui-title-island-lead");
  GtkWidget *title = find_class(island, "lumaui-title-island-title");
  g_assert_true(gtk_widget_get_first_child(row) == lead);
  g_assert_true(gtk_widget_get_visible(lead));
  g_assert_false(gtk_widget_get_focusable(lead)); /* ☰ is part of the title's one control */
  g_assert_cmpstr(luma_title_island_get_title(LUMA_TITLE_ISLAND(island)), ==, "Inbox");
  GtkWidget *sub = find_class(island, "lumaui-title-island-subtitle");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(sub)), ==, "nick@luma");
  g_assert_true(gtk_widget_get_visible(sub));
  luma_title_island_set_title(LUMA_TITLE_ISLAND(island), "Drafts", NULL); /* NULL keeps the subtitle */
  g_assert_true(gtk_widget_get_visible(sub));
  luma_title_island_set_title(LUMA_TITLE_ISLAND(island), "Drafts", ""); /* "" removes it */
  g_assert_false(gtk_widget_get_visible(sub));

  /* ☰ with nothing to grow into: the lead is heard. */
  int leads = 0, titles = 0;
  g_signal_connect(island, "lead", G_CALLBACK(count), &leads);
  g_signal_connect(island, "title", G_CALLBACK(count), &titles);
  g_signal_connect(island, "grown", G_CALLBACK(grown_changed), NULL);
  g_signal_emit_by_name(title, "clicked");
  g_assert_cmpint(leads, ==, 1);
  g_assert_false(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));

  /* ☰ grows into the places, the lead becomes ✕, and folds. */
  luma_title_island_set_grow_func(LUMA_TITLE_ISLAND(island), places, NULL, NULL, LUMA_TITLE_ISLAND_GROWS_AUTO);
  g_signal_emit_by_name(title, "clicked");
  g_assert_true(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));
  g_assert_cmpint(grows, ==, 1);
  g_assert_cmpint(leads, ==, 1);
  g_assert_cmpint(grown_true, ==, 1);
  g_assert_true(gtk_widget_has_css_class(island, "grown"));
  g_assert_true(gtk_widget_has_css_class(island, "menu"));
  g_assert_nonnull(luma_title_island_get_panel(LUMA_TITLE_ISLAND(island)));
  g_signal_emit_by_name(lead, "clicked"); /* ✕ folds, and is not a lead press */
  g_assert_false(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));
  g_assert_cmpint(leads, ==, 1);
  g_assert_cmpint(grown_false, ==, 1);
  g_assert_false(gtk_widget_has_css_class(island, "grown")); /* not mapped: folded at once */
  g_assert_null(luma_title_island_get_panel(LUMA_TITLE_ISLAND(island)));

  /* ‹: its own control; the lead goes up a level; the title grows into the details. */
  luma_title_island_set_lead(LUMA_TITLE_ISLAND(island), LUMA_TITLE_ISLAND_LEAD_BACK, "Back to Inbox");
  lead = find_class(island, "lumaui-title-island-lead");
  g_assert_true(gtk_widget_get_focusable(lead));
  g_signal_emit_by_name(lead, "clicked");
  g_assert_cmpint(leads, ==, 2);
  g_assert_false(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));
  g_signal_emit_by_name(title, "clicked");
  g_assert_true(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));
  g_assert_false(gtk_widget_has_css_class(island, "menu"));
  luma_title_island_fold(LUMA_TITLE_ISLAND(island));
  g_assert_false(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));

  /* Nothing to grow into, ‹: the title is heard. */
  luma_title_island_set_grow_func(LUMA_TITLE_ISLAND(island), NULL, NULL, NULL, LUMA_TITLE_ISLAND_GROWS_AUTO);
  g_signal_emit_by_name(title, "clicked");
  g_assert_cmpint(titles, ==, 1);

  /* Trailing buttons, faces, a status dot; no lead. */
  luma_title_island_add_trailing(LUMA_TITLE_ISLAND(island), luma_title_island_button_new("phone", "Call"));
  g_assert_true(gtk_widget_get_visible(find_class(island, "lumaui-title-island-trailing")));
  g_assert_nonnull(find_class(island, "lumaui-title-island-button"));
  luma_title_island_set_status(LUMA_TITLE_ISLAND(island), "record");
  g_assert_true(gtk_widget_has_css_class(find_class(island, "lumaui-title-island-status"), "record"));
  luma_title_island_set_lead(LUMA_TITLE_ISLAND(island), LUMA_TITLE_ISLAND_LEAD_NONE, NULL);
  g_assert_false(gtk_widget_get_visible(find_class(island, "lumaui-title-island-lead")));
  g_assert_true(gtk_widget_has_css_class(island, "no-lead"));
  g_object_unref(island);
}

static void test_borrow(void) {
  /* A sidebar that lives in the page is borrowed while grown and put back on fold. */
  GtkWidget *page = g_object_ref_sink(gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0));
  GtkWidget *sidebar = gtk_list_box_new();
  gtk_list_box_append(GTK_LIST_BOX(sidebar), gtk_label_new("Home"));
  GtkWidget *content = gtk_label_new("content");
  gtk_box_append(GTK_BOX(page), sidebar);
  gtk_box_append(GTK_BOX(page), content);
  gtk_widget_add_css_class(sidebar, "luma-island");
  GtkWidget *island = g_object_ref_sink(luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_MENU, "Home", "6 items"));
  luma_title_island_set_grow_widget(LUMA_TITLE_ISLAND(island), sidebar, LUMA_TITLE_ISLAND_GROWS_AUTO);
  g_assert_true(luma_title_island_grow_into(LUMA_TITLE_ISLAND(island), NULL));
  g_assert_true(gtk_widget_is_ancestor(sidebar, island));
  g_assert_false(gtk_widget_has_css_class(sidebar, "luma-island"));
  /* Picking a place folds it; the sidebar goes home, in its place. */
  g_signal_emit_by_name(sidebar, "row-activated", gtk_list_box_get_row_at_index(GTK_LIST_BOX(sidebar), 0));
  g_assert_false(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));
  g_assert_true(gtk_widget_get_parent(sidebar) == page);
  g_assert_true(gtk_widget_get_first_child(page) == sidebar);
  g_assert_true(gtk_widget_has_css_class(sidebar, "luma-island"));
  g_object_unref(island);
  g_object_unref(page);
}

static void test_split_sidebar(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 402, 700);
  GtkWidget *split = adw_overlay_split_view_new();
  GtkWidget *sidebar = gtk_list_box_new();
  gtk_list_box_append(GTK_LIST_BOX(sidebar), gtk_label_new("Home"));
  GtkWidget *overlay = gtk_overlay_new();
  gtk_overlay_set_child(GTK_OVERLAY(overlay), gtk_label_new("Files"));
  adw_overlay_split_view_set_sidebar(ADW_OVERLAY_SPLIT_VIEW(split), sidebar);
  adw_overlay_split_view_set_content(ADW_OVERLAY_SPLIT_VIEW(split), overlay);
  adw_overlay_split_view_set_collapsed(ADW_OVERLAY_SPLIT_VIEW(split), TRUE);
  adw_overlay_split_view_set_show_sidebar(ADW_OVERLAY_SPLIT_VIEW(split), FALSE);
  gtk_window_set_child(GTK_WINDOW(window), split);
  GtkWidget *island = luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_MENU, "Home", NULL);
  luma_title_island_attach(LUMA_TITLE_ISLAND(island), overlay);
  GtkWidget *toggle = g_object_ref_sink(luma_sidebar_toggle_new(split, FALSE));
  luma_sidebar_toggle_set_island(LUMA_SIDEBAR_TOGGLE(toggle), LUMA_TITLE_ISLAND(island));
  gtk_window_present(GTK_WINDOW(window)); spin();
  /* The split view is an ancestor of this very island: reject cyclic borrowing. */
  g_assert_false(luma_title_island_grow_into(LUMA_TITLE_ISLAND(island), split));
  g_assert_false(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));
  int activations = 0;
  g_signal_connect_swapped(sidebar, "row-activated", G_CALLBACK(g_atomic_int_inc), &activations);
  for (int i = 0; i < 3; i++) {
    g_signal_emit_by_name(find_class(island, "lumaui-title-island-title"), "clicked");
    spin();
    g_assert_true(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));
    g_assert_true(gtk_widget_is_ancestor(sidebar, island));
    g_assert_null(adw_overlay_split_view_get_sidebar(ADW_OVERLAY_SPLIT_VIEW(split)));
    g_assert_true(adw_overlay_split_view_get_content(ADW_OVERLAY_SPLIT_VIEW(split)) == overlay);
    g_signal_emit_by_name(sidebar, "row-activated", gtk_list_box_get_row_at_index(GTK_LIST_BOX(sidebar), 0));
    spin(); spin();
    g_assert_false(luma_title_island_get_grown(LUMA_TITLE_ISLAND(island)));
    g_assert_true(adw_overlay_split_view_get_sidebar(ADW_OVERLAY_SPLIT_VIEW(split)) == sidebar);
    g_assert_cmpint(activations, ==, i + 1);
  }
  g_object_unref(toggle);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_floating(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), content);
  LumaLayerHost *host = luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  spin();
  GtkWidget *island = luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_MENU, "Documents", "Home · 24 items");
  luma_title_island_set_grow_func(LUMA_TITLE_ISLAND(island), places, NULL, NULL, LUMA_TITLE_ISLAND_GROWS_AUTO);
  luma_title_island_float_over(LUMA_TITLE_ISLAND(island), content);
  g_assert_true(gtk_widget_get_parent(island) == GTK_WIDGET(host));
  spin();
  /* Phone-only by default: a 1000 px window does not show it. */
  g_assert_false(gtk_widget_get_mapped(island));
  luma_title_island_set_phone_only(LUMA_TITLE_ISLAND(island), FALSE);
  spin();
  g_assert_true(gtk_widget_get_mapped(island));
  graphene_rect_t box;
  g_assert_true(gtk_widget_compute_bounds(island, GTK_WIDGET(host), &box));
  g_assert_cmpfloat(box.origin.x, ==, 12);
  g_assert_cmpfloat(box.origin.y, ==, 6);
  g_assert_cmpfloat(box.size.height, ==, 48);
  GtkWidget *lead = find_class(island, "lumaui-title-island-lead");
  g_assert_cmpint(gtk_widget_get_width(lead), ==, 48);
  /* Grown: a drawer's width, over the dim; folding takes the dim away. */
  g_signal_emit_by_name(lead, "clicked");
  spin();
  GtkWidget *scrim = find_class(GTK_WIDGET(host), "lumaui-title-island-scrim");
  g_assert_nonnull(scrim);
  g_assert_true(gtk_widget_get_next_sibling(scrim) == island); /* under the island, over the page */
  g_assert_true(gtk_widget_compute_bounds(island, GTK_WIDGET(host), &box));
  g_assert_cmpfloat(box.size.width, ==, 320);
  g_assert_cmpfloat(box.size.height, >, 48);
  luma_title_island_fold(LUMA_TITLE_ISLAND(island));
  spin();
  spin(); /* the fold's slide and the dim's fade, on a loaded headless clock */
  g_assert_null(find_class(GTK_WIDGET(host), "lumaui-title-island-scrim"));
  g_assert_true(gtk_widget_compute_bounds(island, GTK_WIDGET(host), &box));
  g_assert_cmpfloat(box.size.height, ==, 48);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_same_tier_resize(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 480, 874);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), content);
  LumaLayerHost *host = luma_layer_host_install(GTK_WINDOW(window));
  GtkWidget *island = luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_BACK, "Prompts", "Edited today");
  luma_title_island_set_grow_func(LUMA_TITLE_ISLAND(island), places, NULL, NULL, LUMA_TITLE_ISLAND_GROWS_DETAILS);
  luma_title_island_float_over(LUMA_TITLE_ISLAND(island), content);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  luma_title_island_grow_into(LUMA_TITLE_ISLAND(island), NULL);
  spin();
  const int widths[] = {402, 360, 500, 402};
  for (guint i = 0; i < G_N_ELEMENTS(widths); i++) {
    gtk_window_set_default_size(GTK_WINDOW(window), widths[i], 874);
    spin();
    graphene_rect_t bounds;
    g_assert_cmpint(gtk_widget_get_width(window), ==, widths[i]);
    g_assert_false(gtk_widget_has_css_class(island, "flat"));
    g_assert_true(gtk_widget_compute_bounds(island, GTK_WIDGET(host), &bounds));
    g_assert_cmpfloat(bounds.origin.x, ==, 12);
    g_assert_cmpfloat(bounds.size.width, ==, widths[i] - 24);
  }
  gtk_window_destroy(GTK_WINDOW(window));
}

static void tier_changed(LumaWidthWatch *watch G_GNUC_UNUSED, LumaTier tier G_GNUC_UNUSED, gpointer data) {
  (*(int *)data)++;
}

static void test_tiers(void) {
  g_assert_cmpint(luma_ui_tier_for_width(402), ==, LUMA_TIER_PHONE);
  g_assert_cmpint(luma_ui_tier_for_width(559), ==, LUMA_TIER_PHONE);
  g_assert_cmpint(luma_ui_tier_for_width(560), ==, LUMA_TIER_COMPACT);
  g_assert_cmpint(luma_ui_tier_for_width(900), ==, LUMA_TIER_COMPACT);
  g_assert_cmpint(luma_ui_tier_for_width(901), ==, LUMA_TIER_REGULAR);
  g_assert_cmpint(luma_ui_tier_for_width(0), ==, LUMA_TIER_REGULAR);
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  GtkWidget *child = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), child);
  LumaWidthWatch *watch = luma_width_watch_get(child);
  g_assert_true(luma_width_watch_get(child) == watch);
  int changes = 0;
  g_signal_connect(watch, "tier-changed", G_CALLBACK(tier_changed), &changes);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  g_assert_cmpint(changes, ==, 1); /* the first width counts */
  g_assert_cmpint(luma_width_watch_get_tier(watch), ==, LUMA_TIER_REGULAR);
  g_assert_cmpint(luma_width_watch_get_width(watch), ==, gtk_widget_get_width(window));
  gtk_window_set_default_size(GTK_WINDOW(window), 700, 700);
  spin();
  if (gtk_widget_get_width(window) == 700) {
    g_assert_cmpint(luma_width_watch_get_tier(watch), ==, LUMA_TIER_COMPACT);
    g_assert_cmpint(changes, ==, 2);
  }
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_hidden_watch(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *child = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_visible(child, FALSE);
  LumaWidthWatch *watch = luma_width_watch_get(child);
  gtk_window_set_child(GTK_WINDOW(window), child);
  gtk_window_set_default_size(GTK_WINDOW(window), 360, 600);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  g_assert_false(gtk_widget_get_realized(child));
  g_assert_cmpint(luma_width_watch_get_width(watch), ==, gtk_widget_get_width(window));
  g_assert_cmpint(luma_width_watch_get_tier(watch), ==, LUMA_TIER_PHONE);
  gtk_window_set_default_size(GTK_WINDOW(window), 700, 600);
  for (int i = 0; i < 10 && gtk_widget_get_width(window) != 700; i++) spin();
  spin();
  g_assert_cmpint(gtk_widget_get_width(window), ==, 700);
  g_assert_cmpint(luma_width_watch_get_tier(watch), ==, LUMA_TIER_COMPACT);
  /* Removing a never-realized child must detach the root's signal handlers. */
  gtk_window_set_child(GTK_WINDOW(window), NULL);
  gtk_window_set_default_size(GTK_WINDOW(window), 900, 600);
  spin();
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_creative(void) {
  for (int i = 0; i < 4; i++) {
    const int widths[] = {360, 402, 720, 1180};
    GtkWidget *window = gtk_window_new();
    GtkWidget *host = gtk_overlay_new();
    gtk_overlay_set_child(GTK_OVERLAY(host), gtk_box_new(GTK_ORIENTATION_VERTICAL, 0));
    gtk_window_set_child(GTK_WINDOW(window), host);
    gtk_window_set_default_size(GTK_WINDOW(window), widths[i], 740);
    GtkWidget *widget = luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_BACK, "Launch walkthrough", "");
    LumaTitleIsland *island = LUMA_TITLE_ISLAND(widget);
    luma_title_island_set_phone_only(island, FALSE);
    luma_title_island_set_variant(island, "creative");
    luma_title_island_float_over(island, host);
    gtk_window_present(GTK_WINDOW(window)); spin();
    GtkWidget *lead = find_class(widget, "lumaui-title-island-lead");
    GtkWidget *title = find_class(widget, "lumaui-title-island-title");
    g_assert_cmpint(gtk_widget_get_height(widget), ==, 44);
    g_assert_cmpint(gtk_widget_get_width(lead), ==, 28);
    g_assert_false(gtk_widget_get_focusable(lead));
    int leads = 0;
    g_signal_connect(island, "lead", G_CALLBACK(count), &leads);
    g_signal_emit_by_name(title, "clicked");
    g_assert_cmpint(leads, ==, 1);
    luma_title_island_set_grow_widget(island, gtk_label_new("Details"), LUMA_TITLE_ISLAND_GROWS_DETAILS);
    g_signal_emit_by_name(title, "clicked"); spin();
    g_assert_true(luma_title_island_get_grown(island));
    g_signal_emit_by_name(title, "clicked"); spin();
    g_assert_false(luma_title_island_get_grown(island));
    luma_title_island_set_variant(island, "standard"); spin();
    lead = find_class(widget, "lumaui-title-island-lead");
    g_assert_cmpint(gtk_widget_get_width(lead), ==, 48);
    g_assert_cmpint(gtk_widget_get_height(widget), ==, 48);
    g_assert_true(gtk_widget_get_focusable(lead));
    gtk_window_destroy(GTK_WINDOW(window));
  }
}

static void test_application_visibility(void) {
  for (int floating = 0; floating < 2; floating++) {
    GtkWidget *window = gtk_window_new();
    gtk_window_set_default_size(GTK_WINDOW(window), 1180, 740);
    GtkWidget *body = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_window_set_child(GTK_WINDOW(window), body);
    LumaLayerHost *host = luma_layer_host_install(GTK_WINDOW(window));
    GtkWidget *widget = luma_title_island_new(LUMA_TITLE_ISLAND_LEAD_BACK, "Viewer", "");
    LumaTitleIsland *island = LUMA_TITLE_ISLAND(widget);
    if (floating) luma_title_island_float_over(island, body);
    else gtk_box_append(GTK_BOX(body), widget);
    gtk_window_present(GTK_WINDOW(window)); spin();
    g_assert_true(gtk_widget_get_visible(widget));
    g_assert_false(gtk_widget_get_mapped(widget));
    gtk_widget_set_visible(widget, TRUE); spin();
    g_assert_false(gtk_widget_get_mapped(widget));
    gtk_window_set_default_size(GTK_WINDOW(window), 402, 740); spin();
    g_assert_true(gtk_widget_get_mapped(widget));
    gtk_widget_set_visible(widget, FALSE);
    const int widths[] = {1180, 402, 360};
    for (unsigned i = 0; i < G_N_ELEMENTS(widths); i++) {
      gtk_window_set_default_size(GTK_WINDOW(window), widths[i], 740); spin();
      gtk_widget_set_visible(GTK_WIDGET(host), FALSE); spin();
      gtk_widget_set_visible(GTK_WIDGET(host), TRUE); spin();
      g_assert_false(gtk_widget_get_visible(widget));
      g_assert_false(gtk_widget_get_mapped(widget));
    }
    luma_title_island_set_phone_only(island, FALSE); spin();
    g_assert_false(gtk_widget_get_mapped(widget));
    luma_title_island_set_phone_only(island, TRUE);
    gtk_widget_set_visible(widget, TRUE); spin();
    g_assert_true(gtk_widget_get_mapped(widget));
    gtk_window_set_default_size(GTK_WINDOW(window), 1180, 740); spin();
    g_assert_false(gtk_widget_get_mapped(widget));
    luma_title_island_set_phone_only(island, FALSE); spin();
    g_assert_true(gtk_widget_get_mapped(widget));
    gtk_window_destroy(GTK_WINDOW(window));
  }
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/width-watch/hidden-child", test_hidden_watch);
  g_test_add_func("/lumaui/title-island/split-sidebar", test_split_sidebar);
  g_test_add_func("/lumaui/title-island/creative", test_creative);
  g_test_add_func("/lumaui/title-island/island", test_island);
  g_test_add_func("/lumaui/title-island/menu-icon", test_menu_icon);
  g_test_add_func("/lumaui/title-island/borrow", test_borrow);
  g_test_add_func("/lumaui/title-island/floating", test_floating);
  g_test_add_func("/lumaui/title-island/tiers", test_tiers);
  g_test_add_func("/lumaui/title-island/application-visibility", test_application_visibility);
  g_test_add_func("/lumaui/title-island/same-tier-resize", test_same_tier_resize);
  return g_test_run();
}
