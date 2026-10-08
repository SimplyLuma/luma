/* SPDX-License-Identifier: Apache-2.0 */
/* LumaCornerPill: the twin of structure_placement.CornerPill. */
#include "luma-ui.h"
#include "luma-corner-pill.h"

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

static GtkWidget *normal_of(GtkWidget *pill) { return gtk_widget_get_first_child(pill); }
static GtkWidget *editing_of(GtkWidget *pill) { return gtk_widget_get_last_child(pill); }

static GtkWidget *nth(GtkWidget *box, int index) {
  GtkWidget *child = gtk_widget_get_first_child(box);
  for (int i = 0; i < index && child != NULL; i++)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static int count(GtkWidget *box) {
  int n = 0;
  for (GtkWidget *c = gtk_widget_get_first_child(box); c != NULL; c = gtk_widget_get_next_sibling(c))
    n++;
  return n;
}

static void activated(GSimpleAction *action G_GNUC_UNUSED, GVariant *parameter G_GNUC_UNUSED, gpointer data) {
  (*(int *)data)++;
}

static void anchored(LumaCornerPill *pill G_GNUC_UNUSED, GtkWidget *anchor, gpointer data) {
  *(GtkWidget **)data = anchor;
}

static void counted(LumaCornerPill *pill G_GNUC_UNUSED, gpointer data) { (*(int *)data)++; }

static void test_order(void) {
  GtkWidget *pill = g_object_ref_sink(luma_corner_pill_new());
  g_assert_true(gtk_widget_has_css_class(pill, "lumaui-corner"));
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(pill)), ==, GTK_ACCESSIBLE_ROLE_TOOLBAR);
  g_assert_true(gtk_widget_has_css_class(normal_of(pill), "lumaui-corner-group"));
  g_assert_false(gtk_widget_get_visible(editing_of(pill)));
  /* Added in any order, placed in the one order. */
  g_autoptr(GMenu) menu = g_menu_new();
  g_menu_append(menu, "Print", "win.print");
  luma_corner_pill_set_more_menu(LUMA_CORNER_PILL(pill), G_MENU_MODEL(menu));
  luma_corner_pill_set_info_action(LUMA_CORNER_PILL(pill), "win.info");
  luma_corner_pill_add_state(LUMA_CORNER_PILL(pill), "heart", "Favourite", "win.favourite");
  luma_corner_pill_add_action(LUMA_CORNER_PILL(pill), "pencil", "Edit", "win.edit");
  luma_corner_pill_add_share(LUMA_CORNER_PILL(pill));
  luma_corner_pill_add_open_in(LUMA_CORNER_PILL(pill));
  GtkWidget *modes = luma_mode_switch_new("Mode");
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "view", "View", "eye");
  luma_mode_switch_add(LUMA_MODE_SWITCH(modes), "markup", "Mark up", "pen-line");
  luma_corner_pill_set_modes(LUMA_CORNER_PILL(pill), LUMA_MODE_SWITCH(modes));
  GtkWidget *normal = normal_of(pill);
  g_assert_cmpint(count(normal), ==, 7);
  g_assert_true(nth(normal, 0) == modes);
  const char *tips[] = {"Open in", "Share", "Favourite", "Information", "Edit", "More"};
  for (int i = 0; i < 6; i++) {
    GtkWidget *button = nth(normal, i + 1);
    g_assert_cmpstr(gtk_widget_get_tooltip_text(button), ==, tips[i]);
    g_assert_true(gtk_widget_has_css_class(button, "lumaui-corner-button"));
  }
  g_assert_true(GTK_IS_TOGGLE_BUTTON(nth(normal, 3)));
  g_assert_true(GTK_IS_TOGGLE_BUTTON(nth(normal, 4)));
  g_assert_false(gtk_widget_has_css_class(nth(normal, 2), "labelled"));
  /* Labelled, and Edit the key. */
  luma_corner_pill_set_labelled(LUMA_CORNER_PILL(pill), TRUE);
  luma_corner_pill_set_primary(LUMA_CORNER_PILL(pill), "Edit");
  g_assert_cmpint(count(normal), ==, 7);
  GtkWidget *share = nth(normal, 2), *edit = nth(normal, 5);
  g_assert_cmpstr(gtk_widget_get_tooltip_text(share), ==, "Share");
  g_assert_true(gtk_widget_has_css_class(share, "labelled"));
  g_assert_false(gtk_widget_has_css_class(share, "primary"));
  g_assert_true(gtk_widget_has_css_class(edit, "primary"));
  g_assert_true(gtk_widget_has_css_class(gtk_button_get_child(GTK_BUTTON(edit)), "lumaui-corner-line"));
  /* Modes go away again. */
  luma_corner_pill_set_modes(LUMA_CORNER_PILL(pill), NULL);
  g_assert_cmpint(count(normal), ==, 6);
  g_object_unref(pill);
}

static void test_commands(void) {
  GtkWidget *pill = g_object_ref_sink(luma_corner_pill_new());
  int edits = 0;
  g_autoptr(GSimpleActionGroup) group = g_simple_action_group_new();
  g_autoptr(GSimpleAction) edit = g_simple_action_new("edit", NULL);
  g_signal_connect(edit, "activate", G_CALLBACK(activated), &edits);
  g_autoptr(GSimpleAction) favourite = g_simple_action_new_stateful("favourite", NULL, g_variant_new_boolean(TRUE));
  g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(edit));
  g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(favourite));
  gtk_widget_insert_action_group(pill, "win", G_ACTION_GROUP(group));
  luma_corner_pill_add_share(LUMA_CORNER_PILL(pill));
  luma_corner_pill_add_open_in(LUMA_CORNER_PILL(pill));
  luma_corner_pill_add_action(LUMA_CORNER_PILL(pill), "pencil", "Edit", "win.edit");
  luma_corner_pill_add_state(LUMA_CORNER_PILL(pill), "heart", "Favourite", "win.favourite");
  GtkWidget *normal = normal_of(pill);
  GtkWidget *anchor = NULL, *share_anchor = NULL;
  g_signal_connect(pill, "open-in", G_CALLBACK(anchored), &anchor);
  g_signal_connect(pill, "share", G_CALLBACK(anchored), &share_anchor);
  g_signal_emit_by_name(nth(normal, 0), "clicked");
  g_assert_true(anchor == nth(normal, 0));
  g_signal_emit_by_name(nth(normal, 1), "clicked");
  g_assert_true(share_anchor == nth(normal, 1));
  g_signal_emit_by_name(nth(normal, 3), "clicked");
  g_assert_cmpint(edits, ==, 1);
  g_assert_true(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(nth(normal, 2))));
  g_signal_emit_by_name(nth(normal, 2), "clicked");
  g_assert_false(g_variant_get_boolean(g_action_get_state(G_ACTION(favourite))));
  g_object_unref(pill);
}

static void test_editing(void) {
  GtkWidget *pill = g_object_ref_sink(luma_corner_pill_new());
  luma_corner_pill_add_share(LUMA_CORNER_PILL(pill));
  int cancelled = 0, done = 0;
  g_signal_connect(pill, "edit-cancelled", G_CALLBACK(counted), &cancelled);
  g_signal_connect(pill, "edit-done", G_CALLBACK(counted), &done);
  g_assert_false(luma_corner_pill_get_editing(LUMA_CORNER_PILL(pill)));
  luma_corner_pill_edit(LUMA_CORNER_PILL(pill), "Save");
  g_assert_true(luma_corner_pill_get_editing(LUMA_CORNER_PILL(pill)));
  g_assert_false(gtk_widget_get_visible(normal_of(pill)));
  GtkWidget *editing = editing_of(pill);
  GtkWidget *cancel = nth(editing, 0), *save = nth(editing, 1);
  g_assert_true(gtk_widget_has_css_class(save, "primary"));
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(save));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(gtk_widget_get_last_child(line))), ==, "Save");
  g_signal_emit_by_name(save, "clicked");
  g_assert_cmpint(done, ==, 1);
  g_assert_false(luma_corner_pill_get_editing(LUMA_CORNER_PILL(pill)));
  luma_corner_pill_edit(LUMA_CORNER_PILL(pill), NULL);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(gtk_widget_get_last_child(line))), ==, "Done");
  g_signal_emit_by_name(cancel, "clicked");
  g_assert_cmpint(cancelled, ==, 1);
  luma_corner_pill_edit(LUMA_CORNER_PILL(pill), NULL);
  luma_corner_pill_stop_editing(LUMA_CORNER_PILL(pill));
  g_assert_false(luma_corner_pill_get_editing(LUMA_CORNER_PILL(pill)));
  g_assert_cmpint(cancelled + done, ==, 2);
  g_object_unref(pill);
}

static void test_refusals(void) {
  GtkWidget *pill = g_object_ref_sink(luma_corner_pill_new());
  luma_corner_pill_add_action(LUMA_CORNER_PILL(pill), "pencil", "Edit", "win.edit");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*not one of the actions*");
  luma_corner_pill_set_primary(LUMA_CORNER_PILL(pill), "Delete");
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*LUMA_IS_MODE_SWITCH*");
  luma_corner_pill_set_modes(LUMA_CORNER_PILL(pill), (LumaModeSwitch *)gtk_label_new("x"));
  g_test_assert_expected_messages();
  g_object_unref(pill);
  GtkWidget *window = gtk_window_new();
  gtk_window_set_child(GTK_WINDOW(window), luma_corner_pill_new());
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*at least one control*");
  gtk_window_present(GTK_WINDOW(window));
  settle();
  g_test_assert_expected_messages();
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_phone(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 400, 700);
  GtkWidget *pill = luma_corner_pill_new();
  luma_corner_pill_add_share(LUMA_CORNER_PILL(pill));
  luma_corner_pill_add_action(LUMA_CORNER_PILL(pill), "pencil", "Edit", "win.edit");
  luma_corner_pill_add_action(LUMA_CORNER_PILL(pill), "trash-2", "Delete", "win.delete");
  luma_corner_pill_set_labelled(LUMA_CORNER_PILL(pill), TRUE);
  luma_corner_pill_set_primary(LUMA_CORNER_PILL(pill), "Edit");
  gtk_window_set_child(GTK_WINDOW(window), pill);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  GtkWidget *normal = normal_of(pill);
  GtkWidget *share = nth(normal, 0), *edit = nth(normal, 1);
  g_assert_false(gtk_widget_has_css_class(share, "labelled"));
  g_assert_true(gtk_widget_has_css_class(edit, "labelled"));
  /* Cancel has no icon, so it keeps its word. */
  g_assert_true(gtk_widget_has_css_class(nth(editing_of(pill), 0), "labelled"));
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  settle();
  g_assert_true(gtk_widget_has_css_class(share, "labelled"));
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_primary_icon_only(void) {
  GtkWidget *widget = g_object_ref_sink(luma_corner_pill_new());
  LumaCornerPill *pill = LUMA_CORNER_PILL(widget);
  luma_corner_pill_add_action(pill, "play", "Present", "win.present");
  luma_corner_pill_set_primary(pill, "Present");
  luma_corner_pill_set_primary_icon_only(pill, TRUE);
  GtkWidget *key = nth(normal_of(widget), 0);
  g_assert_true(gtk_widget_has_css_class(key, "primary"));
  g_assert_true(GTK_IS_IMAGE(gtk_button_get_child(GTK_BUTTON(key))));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(key), ==, "Present");
  luma_corner_pill_set_primary_icon_only(pill, FALSE);
  key = nth(normal_of(widget), 0);
  g_assert_true(GTK_IS_BOX(gtk_button_get_child(GTK_BUTTON(key))));
  g_assert_true(gtk_widget_has_css_class(key, "primary"));
  g_object_unref(widget);
}

static void test_people_inset_style(void) {
  GtkWidget *widget = g_object_ref_sink(luma_corner_pill_new());
  LumaCornerPill *pill = LUMA_CORNER_PILL(widget);
  GtkWidget *person = g_object_ref_sink(gtk_button_new_with_label("Person"));
  luma_corner_pill_set_people(pill, person);
  g_assert_true(gtk_widget_has_css_class(person, "lumaui-corner-people"));
  luma_corner_pill_set_people(pill, NULL);
  g_assert_false(gtk_widget_has_css_class(person, "lumaui-corner-people"));
  g_object_unref(person);
  g_object_unref(widget);
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/corner-pill/order", test_order);
  g_test_add_func("/lumaui/corner-pill/commands", test_commands);
  g_test_add_func("/lumaui/corner-pill/editing", test_editing);
  g_test_add_func("/lumaui/corner-pill/refusals", test_refusals);
  g_test_add_func("/lumaui/corner-pill/phone", test_phone);
  g_test_add_func("/lumaui/corner/primary-icon-only", test_primary_icon_only);
  g_test_add_func("/lumaui/corner-pill/people-inset-style", test_people_inset_style);
  return g_test_run();
}
