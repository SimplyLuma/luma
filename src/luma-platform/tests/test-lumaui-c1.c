/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-ui-private.h"

typedef struct { AdwApplicationWindow parent_instance; } TestAdoptWindow;
typedef struct { AdwApplicationWindowClass parent_class; } TestAdoptWindowClass;
G_DEFINE_TYPE(TestAdoptWindow, test_adopt_window, ADW_TYPE_APPLICATION_WINDOW)
static void test_adopt_window_class_init(TestAdoptWindowClass *klass G_GNUC_UNUSED) {}
static void test_adopt_window_init(TestAdoptWindow *self G_GNUC_UNUSED) {}

static void pump(void) {
  gint64 end = g_get_monotonic_time() + 250 * G_TIME_SPAN_MILLISECOND;
  while (g_get_monotonic_time() < end) {
    while (g_main_context_iteration(NULL, FALSE)) {}
    g_usleep(1000);
  }
}

static void test_island(void) {
  GtkWidget *island = g_object_ref_sink(luma_island_new(GTK_ORIENTATION_VERTICAL));
  g_assert_true(LUMA_IS_ISLAND(island));
  g_assert_true(gtk_widget_has_css_class(island, "luma-island"));
  g_assert_cmpint(gtk_widget_get_overflow(island), ==, GTK_OVERFLOW_HIDDEN);
  GtkWidget *child = gtk_label_new("Content");
  gtk_box_append(GTK_BOX(island), child);
  g_assert_true(gtk_widget_get_parent(child) == island);
  g_object_unref(island);
}

/* A wide island content asks for little width and is clipped, not pushed past the window (Nick, 26 Sep). */
static void test_island_yields(void) {
  GtkWidget *island = g_object_ref_sink(luma_island_new(GTK_ORIENTATION_HORIZONTAL));
  GtkWidget *wide = gtk_label_new("1234567890123456789012345678901234567890123456789012345678901234567890");
  gtk_box_append(GTK_BOX(island), wide);
  gtk_box_set_spacing(GTK_BOX(island), 4); /* still a box layout underneath */
  int minimum = 0, natural = 0;
  gtk_widget_measure(island, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(minimum, <=, 160);
  g_assert_cmpint(natural, >, 200);
  gtk_widget_allocate(island, 200, 40, -1, NULL);
  g_assert_cmpint(gtk_widget_get_width(wide), >, 200);
  g_assert_cmpint(GPOINTER_TO_INT(g_object_get_data(G_OBJECT(island), "lumaui-overflow")), >, 0);
  g_object_unref(island);
}

/* Narrower than its content, the island asks its content's height at the content's need, never below
 * its minimum width (Settings' shell warned "Trying to measure … for width of 160, but it needs at least 320"). */
static void test_island_height_at_need(void) {
  if (g_test_subprocess()) {
    g_log_set_always_fatal(G_LOG_LEVEL_WARNING | G_LOG_LEVEL_CRITICAL);
    GtkWidget *island = g_object_ref_sink(luma_island_new(GTK_ORIENTATION_VERTICAL));
    GtkWidget *shell = gtk_label_new("A page whose content needs three hundred and twenty pixels of width");
    gtk_label_set_wrap(GTK_LABEL(shell), TRUE);
    gtk_widget_set_size_request(shell, 320, -1);
    gtk_box_append(GTK_BOX(island), shell);
    int minimum = 0, natural = 0;
    gtk_widget_measure(island, GTK_ORIENTATION_VERTICAL, 160, &minimum, &natural, NULL, NULL);
    g_assert_cmpint(minimum, >, 0);
    gtk_widget_allocate(island, 160, 200, -1, NULL);
    g_assert_cmpint(gtk_widget_get_width(shell), >=, 320);
    g_object_unref(island);
    return;
  }
  g_test_trap_subprocess(NULL, 0, G_TEST_SUBPROCESS_INHERIT_STDERR);
  g_test_trap_assert_passed();
}

static void test_sidebar(void) {
  GtkWidget *sidebar = g_object_ref_sink(luma_navigation_sidebar_new("destinations", NULL));
  int width;
  gtk_widget_get_size_request(sidebar, &width, NULL);
  g_assert_cmpint(width, ==, 224);
  g_assert_true(gtk_widget_has_css_class(sidebar, "lumaui-sidebar-destinations"));
  GtkWidget *row = luma_navigation_row_new("Library", "All sounds");
  GtkWidget *lead = luma_ui_icon_new("music-2");
  luma_navigation_row_set_lead(LUMA_NAVIGATION_ROW(row), lead);
  luma_navigation_row_set_meta(LUMA_NAVIGATION_ROW(row), "12");
  luma_navigation_sidebar_append_section(LUMA_NAVIGATION_SIDEBAR(sidebar), "Locations");
  luma_navigation_sidebar_append_row(LUMA_NAVIGATION_SIDEBAR(sidebar), GTK_LIST_BOX_ROW(row));
  GtkListBox *list = luma_navigation_sidebar_get_list(LUMA_NAVIGATION_SIDEBAR(sidebar));
  g_assert_true(gtk_list_box_get_row_at_index(list, 1) == GTK_LIST_BOX_ROW(row));
  gtk_list_box_select_row(list, GTK_LIST_BOX_ROW(row));
  luma_navigation_row_set_title(LUMA_NAVIGATION_ROW(row), "Samples");
  g_assert_true(gtk_list_box_get_selected_row(list) == GTK_LIST_BOX_ROW(row));
  luma_navigation_sidebar_set_variant(LUMA_NAVIGATION_SIDEBAR(sidebar), "resources", "narrow");
  gtk_widget_get_size_request(sidebar, &width, NULL);
  g_assert_cmpint(width, ==, 236);
  g_assert_false(gtk_widget_has_css_class(sidebar, "lumaui-sidebar-destinations"));
  g_assert_true(gtk_widget_has_css_class(sidebar, "lumaui-sidebar-resources"));
  /* Settings (v70 .cfside): 236 outside, less the frame's 8 it gives as its own right padding. */
  luma_navigation_sidebar_set_variant(LUMA_NAVIGATION_SIDEBAR(sidebar), "settings", NULL);
  gtk_widget_get_size_request(sidebar, &width, NULL);
  g_assert_cmpint(width, ==, 236);
  g_assert_true(gtk_widget_has_css_class(sidebar, "lumaui-sidebar-settings"));
  GtkWidget *glyph = luma_ui_icon_new("wifi");
  GtkWidget *wifi = luma_navigation_row_new("Wi-Fi", NULL);
  luma_navigation_row_set_lead(LUMA_NAVIGATION_ROW(wifi), glyph);
  g_assert_true(gtk_widget_has_css_class(glyph, "lumaui-row-lead"));
  luma_navigation_sidebar_clear(LUMA_NAVIGATION_SIDEBAR(sidebar));
  g_assert_null(gtk_list_box_get_row_at_index(list, 0));
  g_object_unref(sidebar);
}

static void test_window_frame(void) {
  GtkApplication *app = gtk_application_new("org.projectluma.C1Test", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = luma_application_window_new(app, NULL);
  GtkWidget *sidebar = luma_navigation_sidebar_new("destinations", NULL);
  GtkWidget *island = luma_island_new(GTK_ORIENTATION_VERTICAL);
  luma_application_window_set_frame(LUMA_APPLICATION_WINDOW(window), sidebar, island);
  GtkWidget *frame = luma_application_window_get_body(LUMA_APPLICATION_WINDOW(window));
  g_assert_true(gtk_widget_get_first_child(frame) == sidebar);
  g_assert_true(gtk_widget_get_last_child(frame) == island);
  g_assert_nonnull(luma_application_window_get_layer_host(LUMA_APPLICATION_WINDOW(window)));
  g_assert_true(ADW_IS_HEADER_BAR(luma_application_window_get_title_bar(LUMA_APPLICATION_WINDOW(window))));
  GtkWidget *lead = gtk_button_new_with_label("Sidebar");
  GtkWidget *title = gtk_label_new("Session · Saved");
  GtkWidget *trail = gtk_button_new_with_label("Export");
  luma_application_window_set_leading(LUMA_APPLICATION_WINDOW(window), lead);
  luma_application_window_set_title_content(LUMA_APPLICATION_WINDOW(window), title, FALSE);
  luma_application_window_set_trailing(LUMA_APPLICATION_WINDOW(window), trail);
  g_assert_nonnull(gtk_widget_get_parent(lead));
  g_assert_nonnull(gtk_widget_get_parent(title));
  g_assert_nonnull(gtk_widget_get_parent(trail));
  luma_application_window_set_title_content(LUMA_APPLICATION_WINDOW(window), gtk_label_new("Centred"), TRUE);
  luma_application_window_set_title_content(LUMA_APPLICATION_WINDOW(window), NULL, FALSE);
  luma_application_window_set_leading(LUMA_APPLICATION_WINDOW(window), NULL);
  luma_application_window_set_trailing(LUMA_APPLICATION_WINDOW(window), NULL);
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 600);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  /* The body's gutter box is inside the layer host (a phone drawer spans the window): the frame's parent. */
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_parent(luma_application_window_get_body(
                    LUMA_APPLICATION_WINDOW(window))), "lumaui-frame-sidebar"));
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
}

static GtkWidget *find_named(GtkWidget *root, const char *name) {
  if (g_strcmp0(gtk_widget_get_name(root), name) == 0) return root;
  for (GtkWidget *child = gtk_widget_get_first_child(root); child != NULL;
       child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = find_named(child, name);
    if (found != NULL) return found;
  }
  return NULL;
}

static GtkWidget *first_header_in_child_order(GtkWidget *root) {
  if (ADW_IS_HEADER_BAR(root)) return root;
  for (GtkWidget *child = gtk_widget_get_first_child(root); child != NULL;
       child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = first_header_in_child_order(child);
    if (found != NULL) return found;
  }
  return NULL;
}

static void test_adopt_live_window(void) {
  GtkApplication *app = gtk_application_new("org.projectluma.C1AdoptTest", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = g_object_new(test_adopt_window_get_type(), "application", app, NULL);
  g_assert_true(G_TYPE_CHECK_INSTANCE_TYPE(window, test_adopt_window_get_type()));
  GtkWidget *toolbar = adw_toolbar_view_new();
  GtkWidget *header = adw_header_bar_new();
  GtkWidget *existing_action = gtk_button_new_with_label("Sidebar");
  adw_header_bar_pack_start(ADW_HEADER_BAR(header), existing_action);
  adw_toolbar_view_add_top_bar(ADW_TOOLBAR_VIEW(toolbar), header);
  GtkWidget *body = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  GtkWidget *split = adw_overlay_split_view_new();
  GtkWidget *surface = adw_toolbar_view_new();
  GtkWidget *sidebar_content = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  GtkWidget *live = gtk_list_box_new();
  GtkWidget *row = gtk_list_box_row_new();
  GtkWidget *revealer = gtk_revealer_new();
  GtkWidget *row_content = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  GtkWidget *row_icon = gtk_image_new_from_icon_name("folder-symbolic");
  GtkWidget *row_label = gtk_label_new("Documents and downloads");
  gtk_widget_set_margin_end(row_icon, 9);
  gtk_label_set_ellipsize(GTK_LABEL(row_label), PANGO_ELLIPSIZE_NONE);
  gtk_box_append(GTK_BOX(row_content), row_icon);
  gtk_box_append(GTK_BOX(row_content), row_label);
  gtk_revealer_set_child(GTK_REVEALER(revealer), row_content);
  gtk_revealer_set_reveal_child(GTK_REVEALER(revealer), TRUE);
  gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(row), revealer);
  GtkWidget *section = gtk_label_new("PLACES");
  gtk_list_box_row_set_header(GTK_LIST_BOX_ROW(row), section);
  gtk_list_box_append(GTK_LIST_BOX(live), row);
  gtk_box_append(GTK_BOX(sidebar_content), live);
  GtkWidget *resize_handle = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_size_request(resize_handle, 8, -1);
  gtk_box_append(GTK_BOX(sidebar_content), resize_handle);
  adw_toolbar_view_set_content(ADW_TOOLBAR_VIEW(surface), sidebar_content);
  adw_overlay_split_view_set_sidebar(ADW_OVERLAY_SPLIT_VIEW(split), surface);
  GtkWidget *island = luma_island_new(GTK_ORIENTATION_VERTICAL);
  /* Nautilus keeps a second header in the work area's bottom action toolbar.
   * Traversal order must not make that the window title row. */
  GtkWidget *action_toolbar = adw_toolbar_view_new();
  GtkWidget *action_header = adw_header_bar_new();
  adw_toolbar_view_add_bottom_bar(ADW_TOOLBAR_VIEW(action_toolbar), action_header);
  adw_toolbar_view_set_content(ADW_TOOLBAR_VIEW(action_toolbar), gtk_label_new("Files"));
  gtk_box_append(GTK_BOX(island), action_toolbar);
  adw_overlay_split_view_set_content(ADW_OVERLAY_SPLIT_VIEW(split), island);
  gtk_box_append(GTK_BOX(body), split);
  adw_toolbar_view_set_content(ADW_TOOLBAR_VIEW(toolbar), body);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(window), toolbar);
  g_assert_true(first_header_in_child_order(toolbar) == action_header);

  g_assert_true(luma_application_window_frame_adopt(ADW_APPLICATION_WINDOW(window)) == header);
  g_assert_true(luma_application_window_frame_adopt(ADW_APPLICATION_WINDOW(window)) == header);
  g_assert_true(adw_application_window_get_content(ADW_APPLICATION_WINDOW(window)) == toolbar);
  g_assert_nonnull(find_named(header, "lumaui-identity"));
  g_assert_nonnull(find_named(header, "lumaui-window-controls"));
  g_assert_null(find_named(action_header, "lumaui-identity"));
  luma_application_window_frame_set_leading(ADW_APPLICATION_WINDOW(window), existing_action);
  GMenu *menu = g_menu_new();
  g_menu_append(menu, "About Filer", "app.about");
  luma_application_window_frame_set_menu_model(ADW_APPLICATION_WINDOW(window), G_MENU_MODEL(menu));
  g_assert_true(gtk_menu_button_get_menu_model(GTK_MENU_BUTTON(
      find_named(header, "lumaui-identity"))) == G_MENU_MODEL(menu));
  g_object_unref(menu);
  g_assert_true(gtk_widget_has_css_class(window, "luma-app-window"));
  g_assert_true(gtk_widget_has_css_class(body, "luma-window-body"));
  luma_navigation_sidebar_adapt_live(ADW_OVERLAY_SPLIT_VIEW(split), live, "rail");
  g_assert_cmpint(gtk_orientable_get_orientation(GTK_ORIENTABLE(row_content)), ==, GTK_ORIENTATION_VERTICAL);
  g_assert_false(gtk_widget_get_visible(section));
  g_assert_cmpint(gtk_widget_get_valign(row_content), ==, GTK_ALIGN_CENTER);
  g_assert_true(gtk_widget_has_css_class(row, "luma-live-rail-row"));
  g_assert_true(gtk_widget_get_parent(row_icon) == row_content);
  g_assert_true(gtk_widget_get_parent(row_label) == row_content);
  GtkWidget *later = gtk_list_box_row_new();
  GtkWidget *later_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_box_append(GTK_BOX(later_box), gtk_image_new_from_icon_name("drive-harddisk-symbolic"));
  gtk_box_append(GTK_BOX(later_box), gtk_label_new("Archive"));
  gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(later), later_box);
  gtk_list_box_append(GTK_LIST_BOX(live), later);
  luma_navigation_sidebar_adapt_live(ADW_OVERLAY_SPLIT_VIEW(split), live, "rail");
  g_assert_true(gtk_widget_has_css_class(later, "luma-live-rail-row"));
  g_assert_true(gtk_widget_get_parent(live) == sidebar_content);
  g_assert_true(gtk_list_box_get_row_at_index(GTK_LIST_BOX(live), 0) == GTK_LIST_BOX_ROW(row));
  g_assert_cmpfloat(adw_overlay_split_view_get_min_sidebar_width(ADW_OVERLAY_SPLIT_VIEW(split)), ==, 88);
  g_assert_cmpfloat(adw_overlay_split_view_get_max_sidebar_width(ADW_OVERLAY_SPLIT_VIEW(split)), ==, 88);
  g_assert_true(gtk_widget_has_css_class(body, "lumaui-frame-sidebar"));

  GtkWidget *drive_section = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_box_append(GTK_BOX(drive_section), gtk_separator_new(GTK_ORIENTATION_HORIZONTAL));
  gtk_box_append(GTK_BOX(drive_section), gtk_label_new("DRIVES"));
  gtk_list_box_row_set_header(GTK_LIST_BOX_ROW(later), drive_section);
  luma_navigation_sidebar_adapt_live(ADW_OVERLAY_SPLIT_VIEW(split), live, "rail-wide");
  GtkWidget *later_label = gtk_widget_get_next_sibling(gtk_widget_get_first_child(later_box));
  g_assert_cmpfloat(adw_overlay_split_view_get_min_sidebar_width(ADW_OVERLAY_SPLIT_VIEW(split)), ==, 120);
  g_assert_cmpfloat(adw_overlay_split_view_get_max_sidebar_width(ADW_OVERLAY_SPLIT_VIEW(split)), ==, 120);
  g_assert_cmpint(gtk_label_get_ellipsize(GTK_LABEL(later_label)), ==, PANGO_ELLIPSIZE_NONE);
  g_assert_true(gtk_label_get_wrap(GTK_LABEL(later_label)));
  g_assert_cmpint(gtk_widget_get_valign(later_box), ==, GTK_ALIGN_CENTER);
  g_assert_true(gtk_widget_get_visible(drive_section));
  g_assert_false(gtk_widget_get_visible(section));
  luma_navigation_sidebar_adapt_live(ADW_OVERLAY_SPLIT_VIEW(split), live, "rail");

  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 600);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  g_assert_true(gtk_widget_get_mapped(live));
  g_assert_cmpint(gtk_widget_get_width(surface), ==, 88);
  g_assert_true(gtk_widget_get_parent(row) == live);
  g_assert_nonnull(gtk_widget_get_parent(existing_action));
  graphene_rect_t identity_bounds, existing_bounds;
  g_assert_true(gtk_widget_compute_bounds(find_named(header, "lumaui-identity"), header, &identity_bounds));
  g_assert_true(gtk_widget_compute_bounds(existing_action, header, &existing_bounds));
  g_assert_cmpfloat(identity_bounds.origin.x, <, existing_bounds.origin.x);
  graphene_rect_t island_bounds;
  g_assert_true(gtk_widget_compute_bounds(island, split, &island_bounds));
  g_assert_cmpfloat(island_bounds.origin.x, ==, 88);
  adw_overlay_split_view_set_collapsed(ADW_OVERLAY_SPLIT_VIEW(split), TRUE);
  adw_overlay_split_view_set_show_sidebar(ADW_OVERLAY_SPLIT_VIEW(split), TRUE);
  gtk_window_set_default_size(GTK_WINDOW(window), 420, 700);
  pump();
  g_assert_true(gtk_widget_get_mapped(live));
  g_assert_cmpint(gtk_widget_get_width(surface), ==, 88);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
}

static void test_adopt_without_header(void) {
  GtkApplication *app = gtk_application_new("org.projectluma.C1BareTest", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = g_object_new(ADW_TYPE_APPLICATION_WINDOW, "application", app, NULL);
  GtkWidget *content = gtk_label_new("Existing content");
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(window), content);
  GtkWidget *header = luma_application_window_frame_adopt(ADW_APPLICATION_WINDOW(window));
  g_assert_true(ADW_IS_HEADER_BAR(header));
  g_assert_true(gtk_widget_is_ancestor(content, window));
  g_assert_nonnull(find_named(header, "lumaui-identity"));
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
}

static void test_adopt_does_not_use_outer_bottom_bar(void) {
  GtkApplication *app = gtk_application_new("org.projectluma.C1BottomTest", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = g_object_new(ADW_TYPE_APPLICATION_WINDOW, "application", app, NULL);
  GtkWidget *toolbar = adw_toolbar_view_new();
  GtkWidget *bottom = adw_header_bar_new();
  adw_toolbar_view_add_bottom_bar(ADW_TOOLBAR_VIEW(toolbar), bottom);
  GtkWidget *content = gtk_label_new("Existing content");
  adw_toolbar_view_set_content(ADW_TOOLBAR_VIEW(toolbar), content);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(window), toolbar);
  GtkWidget *header = luma_application_window_frame_adopt(ADW_APPLICATION_WINDOW(window));
  g_assert_true(ADW_IS_HEADER_BAR(header));
  g_assert_true(header != bottom);
  g_assert_true(adw_toolbar_view_get_content(ADW_TOOLBAR_VIEW(toolbar)) == content);
  g_assert_nonnull(find_named(header, "lumaui-identity"));
  g_assert_null(find_named(bottom, "lumaui-identity"));
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
}

/* Settings' frame (v70 .cfside): the island at 236, rows' words 10 in, headings a text line high. */
static void test_settings_frame(void) {
  GtkApplication *app = gtk_application_new("org.projectluma.C1Settings", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = luma_application_window_new(app, NULL);
  GtkWidget *sidebar = luma_navigation_sidebar_new("settings", NULL);
  GtkWidget *island = luma_island_new(GTK_ORIENTATION_VERTICAL);
  GtkWidget *wifi = luma_navigation_row_new("Wi-Fi", NULL);
  luma_navigation_row_set_lead(LUMA_NAVIGATION_ROW(wifi), luma_ui_icon_new("wifi"));
  luma_navigation_sidebar_append_row(LUMA_NAVIGATION_SIDEBAR(sidebar), GTK_LIST_BOX_ROW(wifi));
  luma_navigation_sidebar_append_section(LUMA_NAVIGATION_SIDEBAR(sidebar), "Desktop");
  for (int i = 0; i < 40; i++) {
    GtkWidget *destination = luma_navigation_row_new("More destinations", NULL);
    luma_navigation_sidebar_append_row(LUMA_NAVIGATION_SIDEBAR(sidebar), GTK_LIST_BOX_ROW(destination));
  }
  luma_application_window_set_frame(LUMA_APPLICATION_WINDOW(window), sidebar, island);
  /* Settings' order: the toggle after the frame wraps the sidebar in its revealer. */
  GtkWidget *toggle = luma_sidebar_toggle_new(sidebar, TRUE);
  luma_application_window_set_leading(LUMA_APPLICATION_WINDOW(window), toggle);
  gtk_window_set_default_size(GTK_WINDOW(window), 1180, 740);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  pump();
  GtkWidget *frame = gtk_widget_get_parent(island);
  g_assert_cmpint(gtk_box_get_spacing(GTK_BOX(frame)), ==, 0);
  graphene_rect_t isle, line, row, heading;
  g_assert_true(gtk_widget_compute_bounds(island, frame, &isle));
  g_assert_cmpfloat_with_epsilon(isle.origin.x, 236, 0.5);
  GtkWidget *wifi_line = gtk_list_box_row_get_child(GTK_LIST_BOX_ROW(wifi));
  g_assert_true(gtk_widget_compute_bounds(wifi_line, frame, &line));
  g_assert_true(gtk_widget_compute_bounds(wifi, frame, &row));
  g_assert_cmpfloat_with_epsilon(line.origin.x, 20, 0.5); /* 10 sidebar padding + 10 row padding */
  g_assert_cmpfloat_with_epsilon(row.size.height, 32, 0.5);
  GtkWidget *section = GTK_WIDGET(gtk_list_box_get_row_at_index(
      GTK_LIST_BOX(gtk_widget_get_parent(wifi)), 1));
  g_assert_true(gtk_widget_compute_bounds(section, frame, &heading));
  g_assert_cmpfloat(heading.size.height, <, 24);
  GtkWidget *list = gtk_widget_get_parent(wifi);
  GtkWidget *scroll = gtk_widget_get_parent(gtk_widget_get_parent(list));
  g_assert_true(GTK_IS_SCROLLED_WINDOW(scroll));
  GtkWidget *bar = gtk_scrolled_window_get_vscrollbar(GTK_SCROLLED_WINDOW(scroll));
  graphene_rect_t track;
  g_assert_true(gtk_widget_get_mapped(bar));
  g_assert_true(gtk_widget_compute_bounds(bar, frame, &track));
  g_assert_cmpfloat(track.origin.x, >=, row.origin.x + row.size.width);
  g_assert_cmpfloat(track.origin.x + track.size.width, <, isle.origin.x);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
}

/* A type role's line height holds inside a Luma window (v70 .cft b: 13 x 1.35 = 17.55, an 18 px box). */
static void test_role_line_height(void) {
  GtkApplication *app = gtk_application_new("org.projectluma.C1Roles", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = luma_application_window_new(app, NULL);
  GtkWidget *island = luma_island_new(GTK_ORIENTATION_VERTICAL);
  GtkWidget *title = gtk_label_new("Wi-Fi");
  gtk_widget_add_css_class(title, "lumaui-t-row-title");
  GtkWidget *subtitle = gtk_label_new("Connected, secured");
  gtk_widget_add_css_class(subtitle, "lumaui-t-row-subtitle");
  gtk_box_append(GTK_BOX(island), title);
  gtk_box_append(GTK_BOX(island), subtitle);
  luma_application_window_set_frame(LUMA_APPLICATION_WINDOW(window), NULL, island);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  int nat = 0;
  gtk_widget_measure(title, GTK_ORIENTATION_VERTICAL, -1, NULL, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, 18);
  /* A role's tracking reaches the label (v70 .cfhd h1: -0.02em, -0.52 px at 26). */
  GtkWidget *h1 = gtk_label_new("Wi-Fi Networks");
  gtk_widget_add_css_class(h1, "lumaui-t-page-title");
  GtkWidget *plain = gtk_label_new("Wi-Fi Networks");
  gtk_widget_add_css_class(plain, "lumaui-t-page-title");
  gtk_widget_add_css_class(plain, "k-untracked");
  GtkCssProvider *untracked = gtk_css_provider_new();
  gtk_css_provider_load_from_string(untracked, "label.k-untracked { letter-spacing: 0; }");
  gtk_style_context_add_provider_for_display(gdk_display_get_default(), GTK_STYLE_PROVIDER(untracked), 900);
  gtk_box_append(GTK_BOX(island), h1);
  gtk_box_append(GTK_BOX(island), plain);
  pump();
  int tracked = 0, loose = 0;
  gtk_widget_measure(h1, GTK_ORIENTATION_HORIZONTAL, -1, NULL, &tracked, NULL, NULL);
  gtk_widget_measure(plain, GTK_ORIENTATION_HORIZONTAL, -1, NULL, &loose, NULL, NULL);
  g_assert_cmpint(tracked, <, loose - 4); /* 14 letters x 0.52 */
  gtk_widget_measure(subtitle, GTK_ORIENTATION_VERTICAL, -1, NULL, &nat, NULL, NULL);
  g_assert_cmpint(nat, ==, 16); /* 12 x 1.35 = 16.2, to the nearest pixel */
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
}

/* v70 lConfirm is centred on the window, not on the work surface under the title row. */
static void test_dialog_centred_on_window(void) {
  GtkApplication *app = gtk_application_new("org.projectluma.C1Dialog", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = luma_application_window_new(app, NULL);
  GtkWidget *island = luma_island_new(GTK_ORIENTATION_VERTICAL);
  GtkWidget *button = gtk_button_new_with_label("Forget");
  /* As Settings: the page sits in its own (content) layer host inside the island. */
  gtk_box_append(GTK_BOX(island), luma_layer_host_new(button));
  luma_application_window_set_frame(LUMA_APPLICATION_WINDOW(window), NULL, island);
  gtk_window_set_default_size(GTK_WINDOW(window), 1180, 740);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  LumaDestructiveDialog *dialog = luma_destructive_dialog_ask(button, "Forget Studio North?",
      "This computer won’t join it on its own any more, and the password is removed.", "Forget", NULL, NULL);
  g_assert_nonnull(dialog);
  pump();
  pump();
  g_assert_true(luma_layer_host_get_modal(luma_layer_host_window_host(button)) != NULL);
  g_assert_true(gtk_widget_get_parent(GTK_WIDGET(dialog)) != NULL);
  graphene_rect_t card;
  g_assert_true(gtk_widget_compute_bounds(GTK_WIDGET(dialog), window, &card));
  int height = gtk_widget_get_height(window);
  /* within the card's own asymmetric shadow of the window's centre (it was 23 low: centred on the work surface) */
  g_assert_cmpfloat_with_epsilon(card.origin.y + card.size.height / 2, height / 2.0, 6.0);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
}

static GtkWidget *find_class_c1(GtkWidget *root, const char *name) {
  if (gtk_widget_has_css_class(root, name))
    return root;
  for (GtkWidget *child = gtk_widget_get_first_child(root); child != NULL; child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = find_class_c1(child, name);
    if (found != NULL)
      return found;
  }
  return NULL;
}

/* The identity icon is v70's 22 whoever built the identity (an app that sets its application late gets
 * libadwaita's, drawn at 20 until the window maps). */
static void test_identity_icon_22(void) {
  GtkApplication *app = gtk_application_new("org.projectluma.C1Identity", G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = g_object_new(LUMA_TYPE_APPLICATION_WINDOW, NULL);
  gtk_window_set_application(GTK_WINDOW(window), app);
  luma_application_window_set_frame(LUMA_APPLICATION_WINDOW(window), NULL, luma_island_new(GTK_ORIENTATION_VERTICAL));
  gtk_window_present(GTK_WINDOW(window));
  pump();
  GtkWidget *tile = find_class_c1(luma_application_window_get_title_bar(LUMA_APPLICATION_WINDOW(window)),
                                  "luma-identity-icon");
  if (tile != NULL)
    for (GtkWidget *image = gtk_widget_get_first_child(tile); image != NULL; image = gtk_widget_get_next_sibling(image))
      if (GTK_IS_IMAGE(image))
        g_assert_cmpint(gtk_image_get_pixel_size(GTK_IMAGE(image)), ==, 22);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
}

/* A phone folds the window controls at phone width; a desktop window that narrow keeps them. */
static void controls_at(const char *form_factor, gboolean expect_visible) {
  static guint serial = 0;
  g_setenv("LUMA_FORM_FACTOR", form_factor, TRUE);
  g_autofree char *id = g_strdup_printf("org.projectluma.C1Controls.n%u", ++serial);
  GtkApplication *app = gtk_application_new(id, G_APPLICATION_NON_UNIQUE);
  g_assert_true(g_application_register(G_APPLICATION(app), NULL, NULL));
  GtkWidget *window = luma_application_window_new(app, NULL);
  luma_application_window_set_frame(LUMA_APPLICATION_WINDOW(window), NULL, luma_island_new(GTK_ORIENTATION_VERTICAL));
  gtk_window_set_default_size(GTK_WINDOW(window), 390, 800);
  gtk_window_present(GTK_WINDOW(window));
  pump();
  pump();
  GtkWidget *controls = find_named(luma_application_window_get_title_bar(LUMA_APPLICATION_WINDOW(window)),
                                   "lumaui-window-controls");
  g_assert_nonnull(controls);
  g_assert_cmpint(gtk_widget_get_visible(controls), ==, expect_visible);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(app);
  g_unsetenv("LUMA_FORM_FACTOR");
}

static void test_controls_fold_on_a_phone(void) {
  controls_at("phone", FALSE);
  controls_at("desktop", TRUE);
}

/* The composed phone exports LUMA_DEVICE_CLASS, without a test-only form factor. */
static void test_composed_handheld_identity(void) {
  g_unsetenv("LUMA_FORM_FACTOR");
  g_setenv("LUMA_DEVICE_CLASS", "handheld", TRUE);
  g_assert_true(luma_ui_mobile_form_factor());
  controls_at("", FALSE);
  g_setenv("LUMA_DEVICE_CLASS", "desktop", TRUE);
  g_assert_false(luma_ui_mobile_form_factor());
  controls_at("", TRUE);
  g_setenv("LUMA_DEVICE_CLASS", "tablet", TRUE);
  g_assert_false(luma_ui_mobile_form_factor());
  g_setenv("LUMA_DEVICE_CLASS", " Handheld ", TRUE);
  g_assert_true(luma_ui_mobile_form_factor());
  controls_at("desktop", TRUE);
  g_unsetenv("LUMA_DEVICE_CLASS");
}

int main(int argc, char **argv) {
  gtk_test_init(&argc, &argv);
  luma_init();
  g_test_add_func("/c1/island", test_island);
  g_test_add_func("/c1/island-yields", test_island_yields);
  g_test_add_func("/c1/island-height-at-need", test_island_height_at_need);
  g_test_add_func("/c1/sidebar", test_sidebar);
  g_test_add_func("/c1/window-frame", test_window_frame);
  g_test_add_func("/c1/controls-fold-on-a-phone", test_controls_fold_on_a_phone);
  g_test_add_func("/c1/composed-handheld-identity", test_composed_handheld_identity);
  g_test_add_func("/c1/settings-frame", test_settings_frame);
  g_test_add_func("/c1/identity-icon-22", test_identity_icon_22);
  g_test_add_func("/c1/dialog-centred-on-window", test_dialog_centred_on_window);
  g_test_add_func("/c1/role-line-height", test_role_line_height);
  g_test_add_func("/c1/adopt-live-window", test_adopt_live_window);
  g_test_add_func("/c1/adopt-without-header", test_adopt_without_header);
  g_test_add_func("/c1/adopt-bottom-only-toolbar", test_adopt_does_not_use_outer_bottom_bar);
  return g_test_run();
}
