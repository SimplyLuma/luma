/* SPDX-License-Identifier: Apache-2.0 */
/* The floating island with a second sidebar app (not Settings): a "Pantry" fixture built only from
 * LumaApplicationWindow, LumaNavigationSidebar and libadwaita's own preference widgets, with no
 * TV-specific code and no luma_ui_set_part calls. In TV posture it must become the floating island
 * with a page of groups, cards and rows; with TV posture off, nothing about it may change.
 *
 *   test-tv-island          the checks
 *   test-tv-island --show   the app itself, for a look on a TV (LUMA_POSTURE=tv) */
#include <luma-ui.h>
#include <adwaita.h>
#include "luma-tv-frame-private.h"

static gboolean show_only;
static int failures;
static GtkWidget *island;
static GtkWidget *page_title;
static int pages_opened;

#define CHECK(cond) do { if (!(cond)) { g_printerr("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static void spin(int ms) {
  gint64 until = g_get_monotonic_time() + ms * 1000;
  while (g_get_monotonic_time() < until) {
    while (g_main_context_iteration(NULL, FALSE));
    g_usleep(2000);
  }
}

static GtkWidget *page_new(const char *title) {
  GtkWidget *body = gtk_box_new(GTK_ORIENTATION_VERTICAL, 18);
  page_title = gtk_label_new(title);
  gtk_label_set_xalign(GTK_LABEL(page_title), 0);
  gtk_widget_add_css_class(page_title, "title-1");
  gtk_box_append(GTK_BOX(body), page_title);

  GtkWidget *group = adw_preferences_group_new();
  adw_preferences_group_set_title(ADW_PREFERENCES_GROUP(group), "Kitchen");
  GtkWidget *sw = adw_switch_row_new();
  adw_preferences_row_set_title(ADW_PREFERENCES_ROW(sw), "Keep the screen on while cooking");
  adw_preferences_group_add(ADW_PREFERENCES_GROUP(group), sw);
  GtkWidget *combo = adw_combo_row_new();
  adw_preferences_row_set_title(ADW_PREFERENCES_ROW(combo), "Units");
  adw_combo_row_set_model(ADW_COMBO_ROW(combo),
      G_LIST_MODEL(gtk_string_list_new((const char *const[]){"Metric", "Imperial", "Cups", NULL})));
  adw_preferences_group_add(ADW_PREFERENCES_GROUP(group), combo);
  GtkWidget *entry = adw_entry_row_new();
  adw_preferences_row_set_title(ADW_PREFERENCES_ROW(entry), "Kitchen name");
  adw_preferences_group_add(ADW_PREFERENCES_GROUP(group), entry);
  GtkWidget *scale_row = adw_action_row_new();
  adw_preferences_row_set_title(ADW_PREFERENCES_ROW(scale_row), "Timer volume");
  GtkWidget *scale = gtk_scale_new_with_range(GTK_ORIENTATION_HORIZONTAL, 0, 10, 1);
  gtk_widget_set_size_request(scale, 200, -1);
  gtk_widget_set_valign(scale, GTK_ALIGN_CENTER);
  adw_action_row_add_suffix(ADW_ACTION_ROW(scale_row), scale);
  adw_preferences_group_add(ADW_PREFERENCES_GROUP(group), scale_row);
  gtk_box_append(GTK_BOX(body), group);

  GtkWidget *more = adw_preferences_group_new();
  adw_preferences_group_set_title(ADW_PREFERENCES_GROUP(more), "Pantry");
  GtkWidget *link = adw_action_row_new();
  adw_preferences_row_set_title(ADW_PREFERENCES_ROW(link), "Shelves");
  adw_action_row_set_subtitle(ADW_ACTION_ROW(link), "Four shelves, two cupboards");
  adw_action_row_add_suffix(ADW_ACTION_ROW(link), gtk_image_new_from_icon_name("go-next-symbolic"));
  gtk_list_box_row_set_activatable(GTK_LIST_BOX_ROW(link), TRUE);
  adw_preferences_group_add(ADW_PREFERENCES_GROUP(more), link);
  GtkWidget *info = adw_action_row_new();
  adw_preferences_row_set_title(ADW_PREFERENCES_ROW(info), "Items");
  adw_action_row_add_suffix(ADW_ACTION_ROW(info), gtk_label_new("128"));
  adw_preferences_group_add(ADW_PREFERENCES_GROUP(more), info);
  GtkWidget *button = adw_button_row_new();
  adw_preferences_row_set_title(ADW_PREFERENCES_ROW(button), "Clear the shopping list");
  adw_preferences_group_add(ADW_PREFERENCES_GROUP(more), button);
  gtk_box_append(GTK_BOX(body), more);

  GtkWidget *clamp = adw_clamp_new();
  adw_clamp_set_maximum_size(ADW_CLAMP(clamp), 600);
  adw_clamp_set_child(ADW_CLAMP(clamp), body);
  GtkWidget *scroll = gtk_scrolled_window_new();
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(scroll), GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroll), clamp);
  gtk_widget_set_vexpand(scroll, TRUE);
  return scroll;
}

static void row_activated(GtkListBox *list, GtkListBoxRow *row, gpointer data) {
  (void)list; (void)data;
  pages_opened++;
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(island)) != NULL) gtk_box_remove(GTK_BOX(island), child);
  gtk_box_append(GTK_BOX(island), page_new(g_object_get_data(G_OBJECT(row), "title")));
}

static GtkWidget *row_new(const char *title, const char *icon) {
  GtkWidget *row = luma_navigation_row_new(title, NULL);
  luma_navigation_row_set_lead(LUMA_NAVIGATION_ROW(row), luma_ui_icon_new(icon));
  g_object_set_data(G_OBJECT(row), "title", (gpointer)title);
  return row;
}

static GtkWidget *pantry_new(GtkApplication *app, GtkWidget **sidebar_out, GtkWidget **frame_island) {
  GtkWidget *window = luma_application_window_new(app, luma_context_new_from_environment());
  gtk_window_set_title(GTK_WINDOW(window), "Pantry");
  gtk_window_set_default_size(GTK_WINDOW(window), 1280, 720);
  GtkWidget *sidebar = luma_navigation_sidebar_new(NULL, NULL);
  LumaNavigationSidebar *nav = LUMA_NAVIGATION_SIDEBAR(sidebar);
  luma_navigation_sidebar_append_row(nav, GTK_LIST_BOX_ROW(row_new("Recipes", "book-open")));
  luma_navigation_sidebar_append_row(nav, GTK_LIST_BOX_ROW(row_new("Shopping", "shopping-cart")));
  luma_navigation_sidebar_append_section(nav, "Kitchen");
  luma_navigation_sidebar_append_row(nav, GTK_LIST_BOX_ROW(row_new("Timers", "timer")));
  luma_navigation_sidebar_append_row(nav, GTK_LIST_BOX_ROW(row_new("Appliances", "refrigerator")));
  island = luma_island_new(GTK_ORIENTATION_VERTICAL);
  gtk_box_append(GTK_BOX(island), page_new("Recipes"));
  GtkListBox *list = luma_navigation_sidebar_get_list(nav);
  g_signal_connect(list, "row-activated", G_CALLBACK(row_activated), NULL);
  gtk_list_box_select_row(list, gtk_list_box_get_row_at_index(list, 0));
  luma_application_window_set_frame(LUMA_APPLICATION_WINDOW(window), sidebar, island);
  *sidebar_out = sidebar;
  *frame_island = island;
  return window;
}

static gboolean any_with_class(GtkWidget *w, const char *cls, GType type) {
  if ((type == G_TYPE_NONE || G_TYPE_CHECK_INSTANCE_TYPE(w, type)) && gtk_widget_has_css_class(w, cls)) return TRUE;
  for (GtkWidget *c = gtk_widget_get_first_child(w); c; c = gtk_widget_get_next_sibling(c))
    if (any_with_class(c, cls, type)) return TRUE;
  return FALSE;
}

static AdwClamp *find_clamp(GtkWidget *w) {
  if (ADW_IS_CLAMP(w)) return ADW_CLAMP(w);
  for (GtkWidget *c = gtk_widget_get_first_child(w); c; c = gtk_widget_get_next_sibling(c)) {
    AdwClamp *found = find_clamp(c);
    if (found) return found;
  }
  return NULL;
}

static gboolean section_heading_visible(GtkWidget *sidebar) {
  GtkListBox *list = luma_navigation_sidebar_get_list(LUMA_NAVIGATION_SIDEBAR(sidebar));
  for (GtkWidget *row = gtk_widget_get_first_child(GTK_WIDGET(list)); row; row = gtk_widget_get_next_sibling(row))
    if (gtk_widget_has_css_class(row, "luma-navigation-section")) {
      GtkWidget *heading = gtk_widget_get_first_child(gtk_list_box_row_get_child(GTK_LIST_BOX_ROW(row)));
      return gtk_widget_get_visible(heading);
    }
  return FALSE;
}

static void check_desktop(GtkApplication *app) {
  g_setenv("LUMA_POSTURE", "desktop", TRUE);
  GtkWidget *sidebar, *page;
  GtkWidget *window = pantry_new(app, &sidebar, &page);
  gtk_window_present(GTK_WINDOW(window));
  spin(400);
  CHECK(!gtk_widget_has_css_class(window, "lumaui-tv"));
  CHECK(!luma_tv_frame_is_active(GTK_WINDOW(window)));
  CHECK(!any_with_class(window, "lumaui-tv-frame", G_TYPE_NONE));
  CHECK(!any_with_class(window, "lumaui-part-row", G_TYPE_NONE));
  CHECK(section_heading_visible(sidebar));
  CHECK(adw_clamp_get_maximum_size(find_clamp(page)) == 600);
  CHECK(gtk_widget_get_valign(sidebar) == GTK_ALIGN_FILL);       /* the desktop sidebar spans the frame */
  CHECK(!luma_tv_frame_key(GTK_WINDOW(window), GDK_KEY_Down)); /* the frame takes no keys off a TV */
  gtk_window_destroy(GTK_WINDOW(window));
  spin(100);
}

static void check_tv(GtkApplication *app) {
  g_setenv("LUMA_POSTURE", "tv", TRUE);
  GtkWidget *sidebar, *page;
  GtkWidget *window = pantry_new(app, &sidebar, &page);
  gtk_window_present(GTK_WINDOW(window));
  spin(600);
  GtkListBox *list = luma_navigation_sidebar_get_list(LUMA_NAVIGATION_SIDEBAR(sidebar));
  CHECK(gtk_widget_has_css_class(window, "lumaui-tv"));
  CHECK(luma_tv_frame_is_active(GTK_WINDOW(window)));
  CHECK(any_with_class(window, "lumaui-tv-frame", G_TYPE_NONE));
  CHECK(!section_heading_visible(sidebar));                       /* a section is a hairline */
  CHECK(any_with_class(page, "lumaui-part-group", ADW_TYPE_PREFERENCES_GROUP));
  CHECK(any_with_class(page, "lumaui-part-card", GTK_TYPE_LIST_BOX));
  CHECK(any_with_class(page, "lumaui-part-row", ADW_TYPE_SWITCH_ROW));
  CHECK(any_with_class(page, "lumaui-part-row", ADW_TYPE_COMBO_ROW));
  CHECK(any_with_class(page, "lumaui-part-row", ADW_TYPE_ENTRY_ROW));
  CHECK(any_with_class(page, "lumaui-part-row", ADW_TYPE_BUTTON_ROW));
  CHECK(adw_clamp_get_maximum_size(find_clamp(page)) > 10000);   /* runs to the safe margin */
  /* The island is as tall as its rows, not a full-height panel. */
  graphene_rect_t isle;
  CHECK(gtk_widget_compute_bounds(sidebar, window, &isle));
  CHECK(isle.size.height < gtk_widget_get_height(window) / 2);

  /* The island: Down opens the next page at once, no OK. */
  GtkWidget *focus = gtk_root_get_focus(GTK_ROOT(window));
  CHECK(focus != NULL && gtk_widget_is_ancestor(focus, sidebar));
  int opened = pages_opened;
  CHECK(luma_tv_frame_key(GTK_WINDOW(window), GDK_KEY_Down));
  spin(100);
  CHECK(pages_opened == opened + 1);
  CHECK(gtk_list_box_row_get_index(gtk_list_box_get_selected_row(list)) == 1);
  CHECK(luma_tv_frame_key(GTK_WINDOW(window), GDK_KEY_Down));   /* past the hairline */
  spin(100);
  CHECK(gtk_list_box_row_get_index(gtk_list_box_get_selected_row(list)) == 3);
  CHECK(g_str_equal(gtk_label_get_text(GTK_LABEL(page_title)), "Timers"));

  /* Right steps into the page and folds the island to the rail. */
  CHECK(luma_tv_frame_key(GTK_WINDOW(window), GDK_KEY_Right));
  spin(100);
  focus = gtk_root_get_focus(GTK_ROOT(window));
  CHECK(focus != NULL && gtk_widget_is_ancestor(focus, page));
  CHECK(gtk_widget_has_css_class(sidebar, "lumaui-tv-rail"));
  /* Down moves through the rows; Back returns to the island. */
  CHECK(luma_tv_frame_key(GTK_WINDOW(window), GDK_KEY_Down));
  spin(50);
  CHECK(gtk_widget_is_ancestor(gtk_root_get_focus(GTK_ROOT(window)), page));
  CHECK(luma_tv_frame_key(GTK_WINDOW(window), GDK_KEY_Escape));
  spin(50);
  focus = gtk_root_get_focus(GTK_ROOT(window));
  CHECK(focus != NULL && gtk_widget_is_ancestor(focus, sidebar));
  CHECK(!gtk_widget_has_css_class(sidebar, "lumaui-tv-rail"));
  /* Left from the page's left edge reopens the island too. */
  CHECK(luma_tv_frame_key(GTK_WINDOW(window), GDK_KEY_Right));
  spin(50);
  CHECK(luma_tv_frame_key(GTK_WINDOW(window), GDK_KEY_Left));
  spin(50);
  CHECK(gtk_widget_is_ancestor(gtk_root_get_focus(GTK_ROOT(window)), sidebar));

  /* A list longer than the screen stops at the bottom safe line and scrolls. */
  for (int i = 0; i < 24; i++)
    luma_navigation_sidebar_append_row(LUMA_NAVIGATION_SIDEBAR(sidebar), GTK_LIST_BOX_ROW(row_new("More", "timer")));
  spin(200);
  CHECK(gtk_widget_compute_bounds(sidebar, window, &isle));
  CHECK(isle.origin.y + isle.size.height < gtk_widget_get_height(window));
  CHECK(isle.size.height > gtk_widget_get_height(window) / 2);

  /* Leaving TV posture restores the desktop window exactly. */
  luma_tv_frame_set_active(GTK_WINDOW(window), FALSE);
  spin(200);
  CHECK(!any_with_class(window, "lumaui-tv-frame", G_TYPE_NONE));
  CHECK(!any_with_class(window, "lumaui-part-row", G_TYPE_NONE));
  CHECK(!any_with_class(window, "lumaui-part-group", G_TYPE_NONE));
  CHECK(!gtk_widget_has_css_class(sidebar, "lumaui-tv-rail"));
  CHECK(section_heading_visible(sidebar));
  CHECK(adw_clamp_get_maximum_size(find_clamp(page)) == 600);
  CHECK(gtk_widget_get_valign(sidebar) == GTK_ALIGN_FILL);
  gtk_window_destroy(GTK_WINDOW(window));
  spin(100);
}

static void activate(GtkApplication *app, gpointer data) {
  (void)data;
  if (show_only) {
    GtkWidget *sidebar, *page;
    gtk_window_present(GTK_WINDOW(pantry_new(app, &sidebar, &page)));
    return;
  }
  g_application_hold(G_APPLICATION(app));
  check_desktop(app);
  check_tv(app);
  g_application_release(G_APPLICATION(app));
  g_application_quit(G_APPLICATION(app));
}

int main(int argc, char **argv) {
  show_only = argc > 1 && g_str_equal(argv[1], "--show");
  if (!show_only)
    g_setenv("GSETTINGS_BACKEND", "memory", FALSE);
  luma_init();
  AdwApplication *app = adw_application_new("org.projectluma.tests.Pantry", G_APPLICATION_NON_UNIQUE);
  g_signal_connect(app, "activate", G_CALLBACK(activate), NULL);
  int status = g_application_run(G_APPLICATION(app), 1, argv);
  g_object_unref(app);
  if (show_only) return status;
  if (failures == 0) g_print("tv-island: all checks passed\n");
  return failures == 0 ? status : 1;
}
