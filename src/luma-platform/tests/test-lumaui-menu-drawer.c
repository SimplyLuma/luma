/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-menu-drawer.h"

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

static int count_class(GtkWidget *widget, const char *css) {
  int n = gtk_widget_has_css_class(widget, css) ? 1 : 0;
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child; child = gtk_widget_get_next_sibling(child))
    n += count_class(child, css);
  return n;
}

static GtkWidget *row_labelled(GtkWidget *root, const char *text) {
  if (GTK_IS_BUTTON(root)) {
    GtkWidget *label = find_class(root, "lumaui-menu-label");
    if (label == NULL)
      for (GtkWidget *c = gtk_widget_get_first_child(gtk_button_get_child(GTK_BUTTON(root))); c;
           c = gtk_widget_get_next_sibling(c))
        if (GTK_IS_LABEL(c))
          label = c;
    if (label != NULL && g_strcmp0(gtk_label_get_label(GTK_LABEL(label)), text) == 0)
      return root;
  }
  for (GtkWidget *child = gtk_widget_get_first_child(root); child; child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = row_labelled(child, text);
    if (found)
      return found;
  }
  return NULL;
}

static char *last_sort;
static void sort_activated(GtkWidget *widget G_GNUC_UNUSED, const char *name G_GNUC_UNUSED, GVariant *param) {
  g_free(last_sort);
  last_sort = g_variant_dup_string(param, NULL);
}

static void count(gpointer instance G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }

static GtkWidget *window_with(GtkWidget *child, int width) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), width, 700);
  gtk_window_set_child(GTK_WINDOW(window), child);
  luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  spin();
  return window;
}

static GMenuModel *model(void) {
  GMenu *menu = g_menu_new();
  GMenu *sort = g_menu_new();
  g_menu_append(sort, "By _Name", "anchor.sort::name");
  g_menu_append(sort, "By Date", "anchor.sort::date");
  g_menu_append_section(menu, "Sort", G_MENU_MODEL(sort));
  GMenu *sub = g_menu_new();
  g_menu_append(sub, "Everyone", "anchor.sort::everyone");
  g_menu_append_submenu(menu, "Share", G_MENU_MODEL(sub));
  g_object_unref(sort);
  g_object_unref(sub);
  return G_MENU_MODEL(menu);
}

/* A button class with an "anchor.sort" action, so rows activate on @where. */
static GType anchor_button_get_type(void);
typedef GtkButton AnchorButton;
typedef GtkButtonClass AnchorButtonClass;
G_DEFINE_TYPE(AnchorButton, anchor_button, GTK_TYPE_BUTTON)
static void anchor_button_class_init(AnchorButtonClass *klass) {
  gtk_widget_class_install_action(GTK_WIDGET_CLASS(klass), "anchor.sort", "s",
                                  (GtkWidgetActionActivateFunc)sort_activated);
}
static void anchor_button_init(AnchorButton *self G_GNUC_UNUSED) {}

static void test_model(void) {
  GtkWidget *button = g_object_new(anchor_button_get_type(), "label", "More", NULL);
  GtkWidget *window = window_with(button, 1000);
  g_autoptr(GMenuModel) menu = model();
  int closed = 0;
  LumaMenuDrawer *drawer = luma_menu_drawer_present_model(button, menu, "View");
  g_assert_nonnull(drawer);
  g_signal_connect(drawer, "closed", G_CALLBACK(count), &closed);
  LumaModalHandle *handle = luma_layer_host_get_modal(luma_layer_host_window_host(button));
  g_assert_true(luma_modal_handle_get_is_drawer(handle));
  g_assert_true(gtk_widget_has_css_class(GTK_WIDGET(drawer), "lumaui-menu-drawer"));
  g_assert_nonnull(find_class(GTK_WIDGET(drawer), "lumaui-drawer-handle"));
  g_assert_cmpint(count_class(GTK_WIDGET(drawer), "lumaui-menu-heading"), ==, 2); /* View, Sort */
  g_assert_cmpint(count_class(GTK_WIDGET(drawer), "lumaui-menu-separator"), ==, 0);
  g_assert_cmpint(count_class(GTK_WIDGET(drawer), "lumaui-menu-row"), ==, 3);
  g_assert_cmpint(count_class(GTK_WIDGET(drawer), "lumaui-menu-chevron"), ==, 1);
  spin();

  /* A submenu is a second page with a Back row. */
  GtkWidget *share = row_labelled(GTK_WIDGET(drawer), "Share");
  g_assert_nonnull(share);
  g_signal_emit_by_name(share, "clicked");
  spin();
  GtkWidget *back = find_class(GTK_WIDGET(drawer), "back");
  g_assert_nonnull(back);
  g_signal_emit_by_name(back, "clicked");
  spin();
  spin();
  g_assert_null(find_class(GTK_WIDGET(drawer), "back"));

  GtkWidget *name = row_labelled(GTK_WIDGET(drawer), "By Name");
  g_assert_nonnull(name);
  g_signal_emit_by_name(name, "clicked");
  g_assert_cmpint(closed, ==, 1);
  g_assert_null(luma_layer_host_get_modal(luma_layer_host_window_host(button)));
  spin();
  g_assert_cmpstr(last_sort, ==, "name");

  /* Cancel and close emit closed once. */
  closed = 0;
  drawer = luma_menu_drawer_present_model(button, menu, NULL);
  g_signal_connect(drawer, "closed", G_CALLBACK(count), &closed);
  luma_modal_handle_cancel(luma_layer_host_get_modal(luma_layer_host_window_host(button)));
  g_assert_cmpint(closed, ==, 1);
  spin();
  closed = 0;
  drawer = luma_menu_drawer_present_model(button, menu, NULL);
  g_signal_connect(drawer, "closed", G_CALLBACK(count), &closed);
  luma_menu_drawer_close(drawer);
  luma_menu_drawer_close(drawer);
  g_assert_cmpint(closed, ==, 1);
  spin();
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_floating(void) {
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  GtkWidget *button = g_object_new(anchor_button_get_type(), "label", "Open in", "halign", GTK_ALIGN_END, NULL);
  gtk_box_append(GTK_BOX(box), button);
  GtkWidget *window = window_with(box, 1000);
  GtkWidget *menu = g_object_ref_sink(luma_floating_menu_new("Open in"));
  luma_floating_menu_add_heading(LUMA_FLOATING_MENU(menu), "Apps");
  luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), "Text Editor", "file-text", NULL, "Default",
                              "anchor.sort::text", TRUE);
  luma_floating_menu_add_separator(LUMA_FLOATING_MENU(menu));
  luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), "Files", "folder", NULL, NULL, "anchor.sort::files", FALSE);
  luma_floating_menu_add_rich_item(LUMA_FLOATING_MENU(menu), "SUM", NULL, NULL, "(values)",
                                   "Adds numbers", "anchor.sort::sum", FALSE);
  g_assert_true(gtk_widget_has_css_class(menu, "lumaui-menu"));
  g_assert_cmpint(count_class(menu, "lumaui-menu-item"), ==, 3);
  g_assert_cmpint(count_class(menu, "lumaui-menu-description"), ==, 1);
  g_assert_nonnull(find_class(menu, "rich"));
  g_assert_cmpint(count_class(menu, "on"), ==, 1);
  g_assert_false(gtk_widget_get_visible(find_class(menu, "lumaui-drawer-handle")));
  g_assert_false(luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(menu)));

  LumaLayerHost *host = luma_layer_host_window_host(button);
  luma_floating_menu_popup(LUMA_FLOATING_MENU(menu), button);
  g_assert_true(luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(menu)));
  g_assert_true(gtk_widget_get_parent(menu) == GTK_WIDGET(host));
  g_assert_cmpint(gtk_widget_get_halign(menu), ==, GTK_ALIGN_START);
  g_assert_cmpint(gtk_widget_get_margin_top(menu), >, 0);
  g_assert_true(gtk_widget_has_css_class(menu, "below"));
  spin();
  g_assert_true(gtk_widget_has_css_class(menu, "shown"));
  luma_floating_menu_close(LUMA_FLOATING_MENU(menu));
  g_assert_false(luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(menu)));
  g_assert_null(gtk_widget_get_parent(menu));

  luma_floating_menu_popup(LUMA_FLOATING_MENU(menu), button);
  GtkWidget *files = row_labelled(menu, "Files");
  g_signal_emit_by_name(files, "clicked");
  g_assert_false(luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(menu)));
  g_assert_cmpstr(last_sort, ==, "files");
  g_object_unref(menu);
  gtk_window_destroy(GTK_WINDOW(window));
}

/* Settings' metered picker (v70 .cfpop): 36 px rows, the chosen one ending in a check, 200 wide at least. */
static void test_picker(void) {
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  GtkWidget *button = g_object_new(anchor_button_get_type(), "label", "Automatic", "halign", GTK_ALIGN_END, NULL);
  gtk_box_append(GTK_BOX(box), button);
  GtkWidget *window = window_with(box, 1000);
  GtkWidget *menu = g_object_ref_sink(luma_floating_menu_new("Metered connection"));
  luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), "Automatic", NULL, NULL, NULL, NULL, TRUE);
  luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), "Yes", NULL, NULL, NULL, NULL, FALSE);
  luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), "No", NULL, NULL, NULL, NULL, FALSE);
  luma_floating_menu_set_picker(LUMA_FLOATING_MENU(menu), TRUE);
  luma_floating_menu_popup(LUMA_FLOATING_MENU(menu), button);
  spin();
  GtkWidget *chosen = row_labelled(menu, "Automatic");
  GtkWidget *other = row_labelled(menu, "Yes");
  g_assert_cmpint(gtk_widget_get_height(chosen) + 10, ==, 36); /* its content, less the 5 px padding */
  g_assert_true(gtk_widget_get_visible(find_class(chosen, "lumaui-menu-check")));
  g_assert_false(gtk_widget_get_visible(find_class(other, "lumaui-menu-check")));
  g_assert_cmpint(gtk_widget_get_width(menu) + 12, >=, 200);
  luma_floating_menu_close(LUMA_FLOATING_MENU(menu));
  g_object_unref(menu);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_floating_phone(void) {
  GtkWidget *button = g_object_new(anchor_button_get_type(), "label", "Open in", NULL);
  GtkWidget *window = window_with(button, 400);
  if (gtk_widget_get_width(window) > 639) {
    g_test_skip("the display did not give a phone-width window");
    gtk_window_destroy(GTK_WINDOW(window));
    return;
  }
  /* Not kept: the open menu keeps itself alive, and goes when it closes. */
  GtkWidget *menu = luma_floating_menu_new(NULL);
  luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), "Photos", "image", NULL, NULL, "anchor.sort::photos", FALSE);
  g_object_add_weak_pointer(G_OBJECT(menu), (gpointer *)&menu);
  luma_floating_menu_popup(LUMA_FLOATING_MENU(menu), button);
  g_assert_true(luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(menu)));
  g_assert_null(gtk_widget_get_parent(menu));
  LumaModalHandle *handle = luma_layer_host_get_modal(luma_layer_host_window_host(button));
  GtkWidget *card = luma_modal_handle_get_card(handle);
  g_assert_true(LUMA_IS_MENU_DRAWER(card));
  g_signal_emit_by_name(row_labelled(card, "Photos"), "clicked");
  g_assert_null(menu);
  spin();
  g_assert_cmpstr(last_sort, ==, "photos");
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_custom_child(void) {
  GtkWidget *anchor = gtk_button_new_with_label("Layouts");
  GtkWidget *window = window_with(anchor, 390);
  GtkWidget *grid = gtk_grid_new();
  GtkWidget *tile = gtk_button_new_with_label("Photo");
  gtk_grid_attach(GTK_GRID(grid), tile, 0, 0, 1, 1);
  LumaMenuDrawer *drawer = luma_menu_drawer_present_child(anchor, grid, NULL);
  g_assert_nonnull(drawer);
  g_assert_true(gtk_widget_is_ancestor(grid, GTK_WIDGET(drawer)));
  g_assert_true(gtk_widget_is_ancestor(tile, GTK_WIDGET(drawer)));
  int closed = 0;
  g_signal_connect(drawer, "closed", G_CALLBACK(count), &closed);
  luma_menu_drawer_close(drawer);
  g_assert_cmpint(closed, ==, 1);
  spin();
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_grown_menu(void) {
  GtkWidget *button=gtk_button_new_with_label("Open");
  GtkWidget *window=window_with(button,400);
  LumaActionCenter *center=LUMA_ACTION_CENTER(luma_action_center_new(NULL));
  luma_action_center_attach(center,button);
  LumaBarItem *item=luma_bar_item_new_action("plus","Add",NULL);
  luma_action_center_show_bar(center,&item,1,NULL);
  spin();
  LumaFloatingMenu *menu=LUMA_FLOATING_MENU(g_object_ref_sink(luma_floating_menu_new(NULL)));
  luma_floating_menu_add_item(menu,"Choice",NULL,NULL,NULL,NULL,FALSE);
  luma_floating_menu_popup(menu,button);
  g_assert_true(luma_floating_menu_get_is_open(menu));
  g_assert_cmpstr(luma_action_center_get_grown(center),==,"menu");
  luma_floating_menu_close(menu);
  g_assert_false(luma_floating_menu_get_is_open(menu));
  g_assert_null(luma_action_center_get_grown(center));
  luma_floating_menu_popup(menu,button);
  luma_action_center_fold(center);
  g_assert_false(luma_floating_menu_get_is_open(menu));
  luma_floating_menu_popup(menu,button);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(menu);
  g_object_unref(item);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/menu-drawer/grown-menu", test_grown_menu);
  g_test_add_func("/lumaui/menu-drawer/model", test_model);
  g_test_add_func("/lumaui/menu-drawer/floating", test_floating);
  g_test_add_func("/lumaui/menu-drawer/floating-phone", test_floating_phone);
  g_test_add_func("/lumaui/menu-drawer/picker", test_picker);
  g_test_add_func("/lumaui/menu-drawer/custom-child", test_custom_child);
  return g_test_run();
}
