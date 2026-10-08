/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-action-center.h"

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

static char *last_state;
static void state_changed(LumaActionCenter *center G_GNUC_UNUSED, const char *state, gpointer data G_GNUC_UNUSED) {
  g_free(last_state);
  last_state = g_strdup(state);
}

static int sends;
static char *last_mode;
static void send_activated(GSimpleAction *action G_GNUC_UNUSED, GVariant *param G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  sends++;
}
static void mode_changed(LumaActionEditor *editor G_GNUC_UNUSED, const char *key, gpointer data G_GNUC_UNUSED) {
  g_free(last_mode);
  last_mode = g_strdup(key);
}

static LumaBarItem *action(const char *icon, const char *label, const char *tooltip);

static char *search_event;
static int search_changes, search_activations;
static void search_changed(LumaBarItem *item G_GNUC_UNUSED, const char *text, gpointer data G_GNUC_UNUSED) {
  search_changes++;
  g_free(search_event);
  search_event = g_strdup(text);
}
static void search_activated(LumaBarItem *item G_GNUC_UNUSED, const char *text, gpointer data G_GNUC_UNUSED) {
  search_activations++;
  g_free(search_event);
  search_event = g_strdup(text);
}

static void test_search_and_modes(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), content);
  LumaActionCenter *center = LUMA_ACTION_CENTER(luma_action_center_new(NULL));
  luma_action_center_attach(center, content);
  GtkWidget *modes_widget = luma_mode_switch_new("View mode");
  LumaModeSwitch *modes = LUMA_MODE_SWITCH(modes_widget);
  luma_mode_switch_add(modes, "grid", "Grid", "layout-grid");
  luma_mode_switch_add(modes, "list", "List", "list");
  luma_mode_switch_add(modes, "columns", "Columns", "columns-3");
  LumaBarItem *mode_item = luma_bar_item_new_modes(modes);
  LumaBarItem *search = luma_bar_item_new_search("Search Launch");
  g_signal_connect(search, "search-changed", G_CALLBACK(search_changed), NULL);
  g_signal_connect(search, "search-activated", G_CALLBACK(search_activated), NULL);
  LumaBarItem *back = action("arrow-left", NULL, "Back");
  LumaBarItem *normal[] = {back, search, mode_item};
  luma_action_center_show_bar(center, normal, G_N_ELEMENTS(normal), NULL);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  GtkWidget *entry = find_class(GTK_WIDGET(center), "lumaui-bar-search");
  g_assert_true(GTK_IS_SEARCH_ENTRY(entry));
  g_assert_true(find_class(GTK_WIDGET(center), "lumaui-modes") == modes_widget);
  g_assert_true(gtk_widget_has_css_class(modes_widget, "in-bar"));
  g_assert_cmpstr(luma_bar_item_search_get_text(search), ==, "");
  gtk_editable_set_text(GTK_EDITABLE(entry), "invoice");
  g_assert_cmpint(search_changes, ==, 1);
  g_assert_cmpstr(search_event, ==, "invoice");
  g_signal_emit_by_name(entry, "activate");
  g_assert_cmpint(search_activations, ==, 1);
  luma_mode_switch_set_current(modes, "list");
  LumaBarItem *selected[] = {luma_bar_item_new_chip("invoice.pdf", "file", FALSE),
                             action("copy", NULL, "Copy"), action("ellipsis", NULL, "More")};
  luma_action_center_show_bar(center, selected, G_N_ELEMENTS(selected), NULL);
  g_assert_true(GTK_IS_BUTTON(luma_bar_item_get_active_control(selected[1])));
  GtkWidget *more_button = luma_bar_item_get_active_control(selected[2]);
  g_assert_true(GTK_IS_BUTTON(more_button));
  GtkWidget *menu = luma_floating_menu_new("File actions");
  luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), "Rename", "pencil", NULL, NULL, "win.rename", FALSE);
  luma_floating_menu_popup(LUMA_FLOATING_MENU(menu), more_button);
  g_assert_true(luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(menu)));
  luma_floating_menu_close(LUMA_FLOATING_MENU(menu));
  g_assert_null(luma_bar_item_get_active_control(back));
  GtkWidget *search_host = gtk_widget_get_parent(entry);
  g_assert_nonnull(search_host);
  g_assert_null(gtk_widget_get_parent(search_host));
  g_assert_null(gtk_widget_get_parent(modes_widget));
  luma_action_center_show_bar(center, normal, G_N_ELEMENTS(normal), NULL);
  g_assert_null(luma_bar_item_get_active_control(selected[1]));
  g_assert_true(find_class(GTK_WIDGET(center), "lumaui-bar-search") == entry);
  g_assert_true(find_class(GTK_WIDGET(center), "lumaui-modes") == modes_widget);
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(entry)), ==, "invoice");
  g_assert_cmpstr(luma_mode_switch_get_current(modes), ==, "list");
  luma_bar_item_search_set_text(search, "report");
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(entry)), ==, "report");
  g_assert_cmpint(search_changes, ==, 1); /* programmatic sync is quiet */
  luma_bar_item_search_focus(search);
  GtkWidget *focused = gtk_window_get_focus(GTK_WINDOW(window));
  g_assert_true(focused == entry || (focused != NULL && gtk_widget_is_ancestor(focused, entry)));
  g_object_unref(selected[0]);
  g_object_unref(selected[1]);
  g_object_unref(selected[2]);
  g_object_unref(back);
  g_object_unref(search);
  g_object_unref(mode_item);
  gtk_window_destroy(GTK_WINDOW(window));
  g_clear_pointer(&search_event, g_free);
  search_changes = search_activations = 0;
}

static void test_search_phone_fold(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 390, 700);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), content);
  LumaActionCenter *center = LUMA_ACTION_CENTER(luma_action_center_new(NULL));
  luma_action_center_attach(center, content);
  LumaBarItem *search = luma_bar_item_new_search("Search places");
  LumaBarItem *items[] = {search};
  luma_action_center_show_bar(center, items, 1, NULL);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  GtkWidget *host = find_class(GTK_WIDGET(center), "lumaui-bar-search-host");
  GtkWidget *entry = find_class(host, "lumaui-bar-search");
  g_assert_cmpint(gtk_widget_get_width(GTK_WIDGET(window)), <, LUMA_TIER_PHONE_BELOW);
  g_assert_true(gtk_widget_has_css_class(host, "folded"));
  g_assert_cmpint(gtk_widget_get_width(host), <=, 48); /* v71: a phone bar's controls are 48 */
  luma_bar_item_search_set_keep(search, TRUE);
  spin();
  g_assert_false(gtk_widget_has_css_class(host, "folded"));
  g_assert_true(gtk_stack_get_visible_child(GTK_STACK(host)) == entry);
  g_assert_true(luma_bar_item_search_get_entry(search) == entry);
  g_assert_true(gtk_widget_has_css_class(entry, "keep"));
  g_assert_cmpint(gtk_widget_get_height(entry), ==, 48);
  g_assert_cmpint(gtk_widget_get_width(host), >=, gtk_widget_get_width(window) - 44);
  graphene_rect_t entry_bounds;
  g_assert_true(gtk_widget_compute_bounds(entry, host, &entry_bounds));
  g_assert_cmpfloat(entry_bounds.size.width, ==, gtk_widget_get_width(host));
  luma_bar_item_search_set_keep(search, FALSE);
  spin();
  g_assert_true(gtk_widget_has_css_class(host, "folded"));
  GtkWidget *button = find_class(host, "lumaui-bar-button");
  g_signal_emit_by_name(button, "clicked");
  g_assert_true(gtk_stack_get_visible_child(GTK_STACK(host)) == entry);
  gtk_editable_set_text(GTK_EDITABLE(entry), "Kansas City");
  g_assert_cmpstr(luma_bar_item_search_get_text(search), ==, "Kansas City");
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(search);
}

static LumaBarItem *action(const char *icon, const char *label, const char *tooltip) {
  LumaBarItem *item = luma_bar_item_new_action(icon, label, NULL);
  if (tooltip)
    luma_bar_item_set_tooltip(item, tooltip);
  return item;
}

static void test_items(void) {
  LumaBarItem *share = luma_bar_item_new_action("share-2", "Share", "win.share");
  g_assert_cmpint(luma_bar_item_get_kind(share), ==, LUMA_BAR_ITEM_ACTION);
  luma_bar_item_set_primary(share, TRUE);
  GtkWidget *button = g_object_ref_sink(luma_bar_item_create_control(share, "tool"));
  g_assert_true(gtk_widget_has_css_class(button, "lumaui-bar-button"));
  g_assert_true(gtk_widget_has_css_class(button, "tool"));
  g_assert_true(gtk_widget_has_css_class(button, "primary"));
  g_assert_false(gtk_widget_has_css_class(button, "icon"));
  g_assert_cmpstr(gtk_actionable_get_action_name(GTK_ACTIONABLE(button)), ==, "win.share");
  int activated = 0;
  g_signal_connect(share, "activated", G_CALLBACK(count), &activated);
  g_signal_emit_by_name(button, "clicked");
  g_assert_cmpint(activated, ==, 1);
  g_object_unref(button);

  LumaBarItem *summary = luma_bar_item_new_chip_action("Σ 42 · 3 cells", "square-dashed", NULL);
  luma_bar_item_set_tooltip(summary, "Average 14 · min 2 · max 30");
  int summary_activated = 0;
  g_signal_connect(summary, "activated", G_CALLBACK(count), &summary_activated);
  GtkWidget *summary_button = g_object_ref_sink(luma_bar_item_create_control(summary, "bar"));
  g_assert_true(GTK_IS_BUTTON(summary_button));
  g_assert_true(gtk_widget_has_css_class(summary_button, "lumaui-bar-chip"));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(summary_button), ==, "Average 14 · min 2 · max 30");
  g_signal_emit_by_name(summary_button, "clicked");
  g_assert_cmpint(summary_activated, ==, 1);
  g_object_unref(summary_button);
  g_object_unref(summary);

  LumaBarItem *heart = action("heart", NULL, "Favourite");
  luma_bar_item_set_active(heart, TRUE);
  luma_bar_item_set_danger(heart, TRUE);
  luma_bar_item_set_sensitive(heart, FALSE);
  button = g_object_ref_sink(luma_bar_item_create_control(heart, "bubble"));
  g_assert_true(gtk_widget_has_css_class(button, "icon"));
  g_assert_true(gtk_widget_has_css_class(button, "on"));
  g_assert_true(gtk_widget_has_css_class(button, "danger"));
  g_assert_false(gtk_widget_get_sensitive(button));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(button), ==, "Favourite");
  g_object_unref(button);

  LumaBarItem *chip = luma_bar_item_new_chip("3 photos", "image", TRUE);
  GtkWidget *control = g_object_ref_sink(luma_bar_item_create_control(chip, NULL));
  g_assert_true(gtk_widget_has_css_class(control, "lumaui-bar-chip"));
  GtkWidget *close = find_class(control, "lumaui-bar-chip-close");
  int dismissed = 0;
  g_signal_connect(chip, "dismissed", G_CALLBACK(count), &dismissed);
  g_signal_emit_by_name(close, "clicked");
  g_assert_cmpint(dismissed, ==, 1);
  g_object_unref(control);

  /* A thumbnail replaces the fallback glyph, while Done remains actionable. */
  GdkPaintable *preview = gdk_paintable_new_empty(28, 28);
  luma_bar_item_chip_set_paintable(chip, preview);
  g_object_unref(preview);
  control = g_object_ref_sink(luma_bar_item_create_control(chip, NULL));
  g_assert_nonnull(find_class(control, "lumaui-bar-chip-preview"));
  g_assert_null(find_class(control, "lumaui-bar-chip-icon"));
  g_signal_emit_by_name(find_class(control, "lumaui-bar-chip-close"), "clicked");
  g_assert_cmpint(dismissed, ==, 2);
  g_object_unref(control);

  /* Refusals. */
  LumaBarItem *bare = luma_bar_item_new_action("bold", NULL, NULL);
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*needs a tooltip*");
  control = g_object_ref_sink(luma_bar_item_create_control(bare, "bar"));
  g_test_assert_expected_messages();
  g_object_unref(control);
  LumaBarItem *prompt = luma_bar_item_new_prompt("Reply…");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*prompt belongs to the action center*");
  g_assert_null(luma_bar_item_create_control(prompt, "bar"));
  g_test_assert_expected_messages();

  g_object_unref(share);
  g_object_unref(heart);
  g_object_unref(chip);
  g_object_unref(bare);
  g_object_unref(prompt);
}

static void test_center(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 1000, 700);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), content);
  GSimpleActionGroup *group = g_simple_action_group_new();
  GSimpleAction *send = g_simple_action_new("send", NULL);
  g_signal_connect(send, "activate", G_CALLBACK(send_activated), NULL);
  g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(send));
  gtk_widget_insert_action_group(window, "test", G_ACTION_GROUP(group));
  LumaLayerHost *host = luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  spin();

  GtkWidget *editor = luma_action_editor_new("Reply", "reply");
  luma_action_editor_add_mode(LUMA_ACTION_EDITOR(editor), "one", "Reply", "reply");
  luma_action_editor_add_mode(LUMA_ACTION_EDITOR(editor), "fwd", "Forward", "forward");
  luma_action_editor_set_summary(LUMA_ACTION_EDITOR(editor), "to {}", "Priya <3");
  GtkWidget *to = luma_action_editor_add_field(LUMA_ACTION_EDITOR(editor), "To", NULL);
  g_assert_true(GTK_IS_ENTRY(to));
  g_assert_true(luma_action_editor_get_field(LUMA_ACTION_EDITOR(editor), "To") == to);
  g_assert_null(luma_action_editor_get_field(LUMA_ACTION_EDITOR(editor), "Cc"));
  LumaBarItem *send_item = luma_bar_item_new_action("send-horizontal", "Send", "test.send");
  luma_action_editor_set_primary(LUMA_ACTION_EDITOR(editor), send_item);
  g_signal_connect(editor, "mode-changed", G_CALLBACK(mode_changed), NULL);
  int discarded = 0;
  g_signal_connect(editor, "discarded", G_CALLBACK(count), &discarded);

  GtkWidget *center = luma_action_center_new(LUMA_ACTION_EDITOR(editor));
  g_assert_cmpstr(luma_action_center_get_state(LUMA_ACTION_CENTER(center)), ==, "hidden");
  g_assert_false(gtk_widget_get_visible(center));
  g_signal_connect(center, "state-changed", G_CALLBACK(state_changed), NULL);
  luma_action_center_attach(LUMA_ACTION_CENTER(center), content);
  g_assert_true(gtk_widget_get_parent(center) == GTK_WIDGET(host));

  LumaBarItem *prompt = luma_bar_item_new_prompt("Reply to Priya…");
  LumaBarItem *attach = action("paperclip", NULL, "Attach");
  LumaBarItem *items[] = {prompt, attach};
  int prompted = 0;
  g_signal_connect(prompt, "activated", G_CALLBACK(count), &prompted);
  luma_action_center_show_bar(LUMA_ACTION_CENTER(center), items, 2, NULL);
  GtkWidget *prompt_control = luma_action_center_get_bar_control(LUMA_ACTION_CENTER(center), 0);
  GtkWidget *attach_control = luma_action_center_get_bar_control(LUMA_ACTION_CENTER(center), 1);
  g_assert_true(GTK_IS_BUTTON(prompt_control));
  g_assert_true(GTK_IS_BUTTON(attach_control));
  g_assert_null(luma_action_center_get_bar_control(LUMA_ACTION_CENTER(center), 2));
  g_assert_cmpstr(last_state, ==, "bar");
  g_assert_true(gtk_widget_has_css_class(center, "bar"));
  GtkWidget *bar = find_class(center, "lumaui-ac-bar");
  g_assert_true(gtk_widget_has_css_class(bar, "wide"));
  g_assert_false(gtk_widget_has_css_class(bar, "double"));
  spin();
  /* A wide bar: min(700, width - 48), 24 above the foot. */
  graphene_rect_t bounds;
  g_assert_true(gtk_widget_compute_bounds(center, GTK_WIDGET(host), &bounds));
  g_assert_cmpint((int)bounds.size.width, ==, 700);
  g_assert_cmpint((int)(bounds.origin.y + bounds.size.height), ==, gtk_widget_get_height(GTK_WIDGET(host)) - 24);

  /* A toast clears the bar. */
  LumaToast *toast = luma_toast_show(content, "Saved", NULL);
  g_assert_cmpint(gtk_widget_get_margin_bottom(GTK_WIDGET(toast)), >=, 24 + (int)bounds.size.height);
  luma_toast_dismiss(toast);

  /* The prompt grows the editor. */
  GtkWidget *prompt_button = find_class(center, "lumaui-ac-prompt");
  g_signal_emit_by_name(prompt_button, "clicked");
  g_assert_cmpint(prompted, ==, 1);
  g_assert_cmpstr(luma_action_center_get_state(LUMA_ACTION_CENTER(center)), ==, "editor");
  g_assert_true(gtk_widget_get_visible(find_class(center, "lumaui-ac-editor")));
  g_assert_false(gtk_widget_get_visible(bar));
  spin();
  g_assert_true(gtk_widget_has_css_class(find_class(center, "lumaui-ac-editor"), "shown"));

  /* Write, fold: the prompt shows the draft. */
  GtkWidget *view = find_class(editor, "lumaui-ac-text");
  gtk_text_buffer_set_text(gtk_text_view_get_buffer(GTK_TEXT_VIEW(view)), "See you\n  at  noon", -1);
  g_assert_false(gtk_widget_get_visible(find_class(editor, "lumaui-ac-placeholder")));
  luma_action_center_fold(LUMA_ACTION_CENTER(center));
  g_assert_cmpstr(last_state, ==, "bar");
  GtkWidget *label = gtk_button_get_child(GTK_BUTTON(prompt_button));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "Draft: See you at noon");
  g_assert_true(gtk_widget_has_css_class(prompt_button, "has-draft"));
  g_autofree char *draft = luma_action_editor_get_draft(LUMA_ACTION_EDITOR(editor));
  g_assert_cmpstr(draft, ==, "See you\n  at  noon");

  /* Ctrl+Return's primary runs the GAction. */
  luma_action_center_grow(LUMA_ACTION_CENTER(center));
  g_assert_true(luma_action_editor_run_primary(LUMA_ACTION_EDITOR(editor)));
  g_assert_cmpint(sends, ==, 1);

  /* Modes: the header's menu picks through the editor's action. */
  GtkWidget *mode = find_class(editor, "lumaui-ac-mode");
  g_assert_false(gtk_widget_has_css_class(mode, "static"));
  g_assert_nonnull(find_class(mode, "lumaui-ac-mode-chevron"));
  gtk_widget_activate_action(mode, "lumaui-editor.mode", "s", "fwd");
  g_assert_cmpstr(last_mode, ==, "fwd");

  /* Discard forgets the draft and folds. */
  GtkWidget *discard = find_class(find_class(editor, "lumaui-ac-footer"), "lumaui-bar-button");
  g_signal_emit_by_name(discard, "clicked");
  g_assert_cmpint(discarded, ==, 1);
  g_assert_cmpstr(last_state, ==, "bar");
  g_assert_null(luma_action_editor_get_draft(LUMA_ACTION_EDITOR(editor)));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "Reply to Priya…");

  /* Double height, then split, then hidden. */
  LumaBarItem *context = luma_bar_item_new_context("reply", "Replying to {}", "Priya", TRUE);
  luma_action_center_show_bar(LUMA_ACTION_CENTER(center), items, 2, context);
  g_assert_cmpstr(last_state, ==, "double");
  g_assert_true(gtk_widget_get_visible(find_class(center, "lumaui-ac-context")));
  LumaBarItem *first[] = {luma_bar_item_new_chip("3 photos", NULL, FALSE)};
  LumaBarItem *second[] = {action("trash-2", NULL, "Delete")};
  luma_action_center_show_split(LUMA_ACTION_CENTER(center), first, 1, second, 1);
  g_assert_cmpstr(last_state, ==, "split");
  g_assert_true(gtk_widget_has_css_class(center, "split"));
  g_assert_cmpint(gtk_orientable_get_orientation(GTK_ORIENTABLE(find_class(center, "lumaui-ac-split"))), ==,
                  GTK_ORIENTATION_HORIZONTAL);
  luma_action_center_hide_bar(LUMA_ACTION_CENTER(center));
  g_assert_false(gtk_widget_get_visible(center));

  /* Two primaries on one bar are refused. */
  LumaBarItem *a = action("check", "Done", NULL), *b = action("send", "Send", NULL);
  luma_bar_item_set_primary(a, TRUE);
  luma_bar_item_set_primary(b, TRUE);
  LumaBarItem *both[] = {a, b};
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*one primary*");
  luma_action_center_show_bar(LUMA_ACTION_CENTER(center), both, 2, NULL);
  g_test_assert_expected_messages();

  g_object_unref(a);
  g_object_unref(b);
  g_object_unref(first[0]);
  g_object_unref(second[0]);
  g_object_unref(context);
  g_object_unref(prompt);
  g_object_unref(attach);
  g_object_unref(send_item);
  g_object_unref(send);
  g_object_unref(group);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_no_editor(void) {
  GtkWidget *center = g_object_ref_sink(luma_action_center_new(NULL));
  g_assert_null(luma_action_center_get_editor(LUMA_ACTION_CENTER(center)));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*nothing to grow into*");
  luma_action_center_grow(LUMA_ACTION_CENTER(center));
  g_test_assert_expected_messages();
  GtkWidget *editor = luma_action_editor_new("Edit", "pencil");
  GtkWidget *mode = find_class(editor, "lumaui-ac-mode");
  g_assert_true(gtk_widget_has_css_class(mode, "static"));
  g_assert_false(gtk_widget_get_can_target(mode));
  g_assert_true(gtk_widget_has_css_class(editor, "composer"));
  GtkWidget *form = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_box_append(GTK_BOX(form), gtk_entry_new());
  luma_action_editor_set_body(LUMA_ACTION_EDITOR(editor), form);
  g_assert_false(gtk_widget_has_css_class(editor, "composer"));
  g_assert_null(find_class(editor, "lumaui-ac-text"));
  g_assert_null(luma_action_editor_get_draft(LUMA_ACTION_EDITOR(editor)));
  luma_action_editor_set_draft_summary(LUMA_ACTION_EDITOR(editor), "2 changes");
  g_autofree char *draft = luma_action_editor_get_draft(LUMA_ACTION_EDITOR(editor));
  g_assert_cmpstr(draft, ==, "2 changes");
  g_assert_false(luma_action_editor_run_primary(LUMA_ACTION_EDITOR(editor)));
  luma_action_center_set_editor(LUMA_ACTION_CENTER(center), LUMA_ACTION_EDITOR(editor));
  g_assert_true(luma_action_center_get_editor(LUMA_ACTION_CENTER(center)) == LUMA_ACTION_EDITOR(editor));
  g_object_unref(center);
}

/* Settings' display check (v70 cfKeep): a question chip, then "Go back" and the "Keep" key, words alone. */
static void test_words_alone(void) {
  LumaBarItem *back = luma_bar_item_new_action(NULL, "Go back", NULL);
  LumaBarItem *keep = luma_bar_item_new_action("", "Keep", NULL);
  g_assert_nonnull(back);
  luma_bar_item_set_primary(keep, TRUE);
  GtkWidget *button = g_object_ref_sink(luma_bar_item_create_control(back, "bar"));
  g_assert_true(gtk_widget_has_css_class(button, "text"));
  GtkWidget *child = gtk_button_get_child(GTK_BUTTON(button));
  g_assert_true(GTK_IS_LABEL(child));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(child)), ==, "Go back");
  GtkWidget *key = g_object_ref_sink(luma_bar_item_create_control(keep, "bar"));
  g_assert_true(gtk_widget_has_css_class(key, "primary"));
  LumaBarItem *question = luma_bar_item_new_context(NULL, "Keep these display settings?", NULL, FALSE);
  g_assert_nonnull(question);
  GtkWidget *center = g_object_ref_sink(luma_action_center_new(NULL));
  LumaBarItem *items[] = {back, keep};
  luma_action_center_show_bar(LUMA_ACTION_CENTER(center), items, 2, question);
  g_object_unref(center);
  g_object_unref(key);
  g_object_unref(button);
  g_object_unref(question);
  g_object_unref(keep);
  g_object_unref(back);
}

/* Settings' "Hidden network" sheet (v70 cfSheet): two field rows, Cancel then Join; no composer, discard or hint. */
static void test_form(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *editor = luma_action_editor_new("Hidden network", "wifi");
  luma_action_editor_set_summary(LUMA_ACTION_EDITOR(editor), "Join a network that doesn’t show its name.", NULL);
  luma_action_editor_add_field(LUMA_ACTION_EDITOR(editor), "Name", NULL);
  luma_action_editor_add_field(LUMA_ACTION_EDITOR(editor), "Password", NULL);
  LumaBarItem *join = luma_bar_item_new_action(NULL, "Join", NULL);
  luma_action_editor_set_primary(LUMA_ACTION_EDITOR(editor), join);
  luma_action_editor_set_form(LUMA_ACTION_EDITOR(editor), "Cancel");
  gtk_widget_set_size_request(editor, 720, -1);
  gtk_window_set_child(GTK_WINDOW(window), editor);
  gtk_window_present(GTK_WINDOW(window));
  for (int i = 0; i < 30; i++) {
    while (g_main_context_iteration(NULL, FALSE));
    g_usleep(4000);
  }
  g_assert_true(gtk_widget_has_css_class(editor, "form"));
  GtkWidget *footer = gtk_widget_get_last_child(editor);
  GtkWidget *primary = gtk_widget_get_last_child(footer);
  GtkWidget *cancel = gtk_widget_get_prev_sibling(primary);
  g_assert_true(gtk_widget_has_css_class(primary, "primary"));
  g_assert_true(gtk_widget_has_css_class(cancel, "lumaui-ac-cancel"));
  g_assert_false(gtk_widget_get_visible(gtk_widget_get_first_child(footer))); /* discard */
  int visible_rows = 0;
  for (GtkWidget *child = gtk_widget_get_first_child(editor); child; child = gtk_widget_get_next_sibling(child))
    if (gtk_widget_has_css_class(child, "lumaui-ac-field")) {
      g_assert_cmpint(gtk_widget_get_height(child), ==, 44 - 1); /* its content, above the hairline */
      visible_rows++;
    } else if (GTK_IS_SCROLLED_WINDOW(child)) {
      g_assert_false(gtk_widget_get_visible(child));
    }
  g_assert_cmpint(visible_rows, ==, 2);
  g_assert_cmpint(gtk_widget_get_height(cancel), ==, 36);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(join);
}

/* ── v71: the bar grows ── */

static GtkWidget *center_window(int width, GtkWidget **content_out, GtkWidget **center_out) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), width, 700);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), content);
  luma_layer_host_install(GTK_WINDOW(window));
  gtk_window_present(GTK_WINDOW(window));
  spin();
  GtkWidget *center = luma_action_center_new(NULL);
  luma_action_center_attach(LUMA_ACTION_CENTER(center), content);
  *content_out = content;
  *center_out = center;
  return window;
}

static int panels_made;
static GtkWidget *details_panel(LumaBarItem *item G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  panels_made++;
  GtkWidget *panel = luma_bar_panel_new("Details");
  luma_bar_panel_add_row(panel, "copy", "Copy link", NULL, FALSE);
  luma_bar_panel_add_row(panel, "trash-2", "Move to Trash", NULL, TRUE);
  return panel;
}

static char *last_grown;
static int grown_changes;
static void grown_changed(LumaActionCenter *center G_GNUC_UNUSED, const char *key, gpointer data G_GNUC_UNUSED) {
  g_free(last_grown);
  last_grown = g_strdup(key);
  grown_changes++;
}

static GtkWidget *control_named(GtkWidget *row, const char *name) {
  for (GtkWidget *c = gtk_widget_get_first_child(row); c; c = gtk_widget_get_next_sibling(c))
    if (g_strcmp0(gtk_widget_get_tooltip_text(c), name) == 0)
      return c;
  return NULL;
}

static void test_panel_close(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(402, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  LumaBarItem *item = action("mic", "New", "New memo");
  luma_bar_item_set_primary(item, TRUE);
  luma_bar_item_set_keep_label(item, TRUE);
  luma_bar_item_set_panel(item, "new", details_panel, NULL, NULL);
  luma_bar_item_set_panel_close(item, TRUE);
  LumaBarItem *items[] = {item};
  luma_action_center_show_bar(ac, items, 1, NULL);
  spin();
  GtkWidget *button = control_named(find_class(center, "lumaui-ac-row"), "New memo");
  g_assert_nonnull(button);
  GtkWidget *original = gtk_button_get_child(GTK_BUTTON(button));
  for (guint i = 0; i < 3; i++) {
    if (i == 1)
      luma_action_center_grow_panel(ac, "new", luma_bar_panel_new("New memo"));
    else
      g_signal_emit_by_name(button, "clicked");
    spin();
    g_assert_cmpstr(luma_action_center_get_grown(ac), ==, "new");
    g_assert_true(GTK_IS_IMAGE(gtk_button_get_child(GTK_BUTTON(button))));
    g_assert_cmpstr(gtk_widget_get_tooltip_text(button), ==, "Close");
    g_assert_false(gtk_widget_has_css_class(button, "primary"));
    g_assert_cmpint(gtk_widget_get_width(button), ==, 48);
    if (i == 1)
      luma_action_center_fold(ac);
    else
      g_signal_emit_by_name(button, "clicked");
    spin();
    g_assert_null(luma_action_center_get_grown(ac));
    g_assert_true(gtk_button_get_child(GTK_BUTTON(button)) == original);
    g_assert_true(gtk_widget_has_css_class(button, "primary"));
    g_assert_true(gtk_widget_has_css_class(button, "labelled"));
    g_assert_cmpstr(gtk_widget_get_tooltip_text(button), ==, "New memo");
  }
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(item);
}

static void test_document_toolbar_panel_width(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(402, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  LumaBarItem *items[] = {action("type", NULL, "Formatting"),
                         action("list-checks", NULL, "Checklist"),
                         action("image", NULL, "Photo"),
                         luma_bar_item_new_rule(), action("square-pen", NULL, "New note")};
  luma_bar_item_set_primary(items[4], TRUE);
  luma_action_center_show_bar(ac, items, G_N_ELEMENTS(items), NULL);
  luma_action_center_grow_panel(ac, "format", luma_bar_panel_new("Formatting"));
  spin();
  luma_action_center_set_toolbar(ac, TRUE);
  spin();
  GtkWidget *row = find_class(center, "lumaui-ac-row");
  for (GtkWidget *child = gtk_widget_get_first_child(row); child; child = gtk_widget_get_next_sibling(child)) {
    if (GTK_IS_BUTTON(child)) {
      graphene_rect_t bounds;
      g_assert_true(gtk_widget_compute_bounds(child, center, &bounds));
      g_assert_cmpfloat(bounds.size.width, ==, 48);
    }
  }
  luma_action_center_fold(ac);
  gtk_window_destroy(GTK_WINDOW(window));
  for (guint i = 0; i < G_N_ELEMENTS(items); i++)
    g_object_unref(items[i]);
}

static void test_grow(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(1000, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  LumaBarItem *items[] = {action("copy", NULL, "Copy"), action("info", NULL, "Details"), action("x", NULL, "Clear")};
  luma_bar_item_set_panel(items[1], "details", details_panel, NULL, NULL);
  luma_action_center_show_bar(ac, items, G_N_ELEMENTS(items), NULL);
  g_signal_connect(center, "grown-changed", G_CALLBACK(grown_changed), NULL);
  spin();
  GtkWidget *bar = find_class(center, "lumaui-ac-bar");
  GtkWidget *row = find_class(center, "lumaui-ac-row");
  int idle_width = gtk_widget_get_width(center);
  g_assert_cmpint(idle_width, <, 380);
  /* An idle bar's panel is there, hidden, as Python's. */
  g_assert_false(gtk_widget_get_visible(find_class(center, "lumaui-ac-panel")));

  GtkWidget *details = control_named(row, "Details");
  g_assert_nonnull(details);
  g_signal_emit_by_name(details, "clicked");
  g_assert_cmpstr(luma_action_center_get_grown(ac), ==, "details");
  g_assert_cmpstr(last_grown, ==, "details");
  g_assert_cmpint(panels_made, ==, 1);
  GtkWidget *panel = find_class(center, "lumaui-ac-panel");
  g_assert_true(gtk_widget_get_visible(panel));
  g_assert_true(gtk_widget_get_parent(panel) == bar);
  g_assert_true(gtk_widget_get_first_child(bar) == panel); /* above the row, in the same glass */
  g_assert_true(gtk_widget_has_css_class(details, "on")); /* the open panel's action is the raised chip */
  g_assert_true(gtk_widget_has_css_class(center, "grown"));
  g_assert_false(gtk_widget_get_hexpand(control_named(row, "Copy"))); /* they share the width only on a phone */
  spin();
  g_assert_cmpint(gtk_widget_get_width(center), ==, 380); /* min(380, 1000 − 24) */
  g_assert_true(gtk_widget_get_visible(row)); /* the row holds */

  /* The same button again folds it. */
  g_signal_emit_by_name(details, "clicked");
  g_assert_null(luma_action_center_get_grown(ac));
  g_assert_null(last_grown);
  g_assert_false(gtk_widget_has_css_class(details, "on"));
  g_assert_false(gtk_widget_get_visible(find_class(center, "lumaui-ac-panel")));
  spin();
  g_assert_cmpint(gtk_widget_get_width(center), ==, idle_width);

  /* An action in the row folds an open panel; so does a panel row after it acts. */
  g_signal_emit_by_name(details, "clicked");
  g_signal_emit_by_name(control_named(row, "Copy"), "clicked");
  g_assert_null(luma_action_center_get_grown(ac));
  g_signal_emit_by_name(details, "clicked");
  GtkWidget *panel_row = find_class(find_class(center, "lumaui-ac-panel"), "lumaui-panel-row");
  g_signal_emit_by_name(panel_row, "clicked");
  g_assert_null(luma_action_center_get_grown(ac));

  /* grow_panel and fold, directly; a new bar folds an open panel. */
  luma_action_center_grow_panel(ac, "share", luma_bar_panel_new("Share"));
  g_assert_cmpstr(luma_action_center_get_grown(ac), ==, "share");
  luma_action_center_fold(ac);
  g_assert_null(luma_action_center_get_grown(ac));
  luma_action_center_grow_panel(ac, "share", luma_bar_panel_new("Share"));
  luma_action_center_show_bar(ac, items, G_N_ELEMENTS(items), NULL);
  g_assert_null(luma_action_center_get_grown(ac));

  /* A field that is the point of the panel replaces the row, the bottom-most thing. */
  GtkWidget *to = gtk_entry_new();
  luma_action_center_grow_entry(ac, "new", luma_bar_panel_new("Suggestions"), to);
  GtkWidget *entry_row = gtk_widget_get_last_child(bar); /* lumaui-ac-row.entry, the bottom-most */
  g_assert_true(gtk_widget_has_css_class(entry_row, "entry"));
  g_assert_true(gtk_widget_get_visible(entry_row));
  g_assert_false(gtk_widget_get_visible(row));
  g_assert_true(gtk_widget_get_parent(to) == entry_row);
  luma_action_center_fold(ac);
  g_assert_true(gtk_widget_get_visible(row));
  g_assert_false(gtk_widget_get_visible(entry_row));

  /* A menu rises from the bar: sections, and a submenu as a second page with ‹ back. */
  GMenu *menu = g_menu_new();
  g_menu_append(menu, "Rename", "win.rename");
  GMenu *open_in = g_menu_new();
  g_menu_append(open_in, "Viewer", "win.open::viewer");
  g_menu_append_submenu(menu, "Open in", G_MENU_MODEL(open_in));
  luma_action_center_grow_menu(ac, "more", G_MENU_MODEL(menu), NULL);
  g_assert_cmpstr(luma_action_center_get_grown(ac), ==, "more");
  GtkWidget *stack = find_class(center, "lumaui-ac-panel-content");
  g_assert_true(GTK_IS_STACK(stack));
  g_assert_cmpstr(gtk_stack_get_visible_child_name(GTK_STACK(stack)), ==, "root");
  luma_action_center_grow_menu(ac, "more", G_MENU_MODEL(menu), NULL); /* again: folds */
  g_assert_null(luma_action_center_get_grown(ac));
  g_object_unref(open_in);
  g_object_unref(menu);

  for (guint i = 0; i < G_N_ELEMENTS(items); i++)
    g_object_unref(items[i]);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_phone(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(400, &content, &center);
  if (gtk_widget_get_width(window) >= LUMA_TIER_PHONE_BELOW) {
    g_test_skip("the display did not give a phone-width window");
    gtk_window_destroy(GTK_WINDOW(window));
    return;
  }
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  LumaBarItem *items[] = {
      action("search", "Search", NULL),     action("folder-plus", "New folder", NULL),
      action("copy", "Copy", NULL),         action("share-2", "Share", NULL),
      action("move", "Move", NULL),         action("info", "Details", NULL),
      action("pencil", "Rename", NULL),     action("archive", "Compress", NULL),
      action("trash-2", "Delete", NULL),    luma_bar_item_new_action("check", "Done", NULL),
  };
  /* Adjacent formatting actions must remain reachable when their entire
   * recessed group moves into the phone overflow panel. */
  for (guint i=3;i<9;i++) luma_bar_item_set_group(items[i], "Grouped tools");
  luma_bar_item_set_primary(items[9], TRUE);
  luma_bar_item_set_keep_label(items[9], TRUE);
  luma_bar_item_set_danger(items[8], TRUE);
  luma_action_center_show_bar(ac, items, G_N_ELEMENTS(items), NULL);
  spin();
  spin();
  g_assert_true(gtk_widget_has_css_class(center, "phone"));
  GtkWidget *row = find_class(center, "lumaui-ac-row");
  /* Icons only on a phone; the labelled primary keeps its words. */
  for (GtkWidget *c = gtk_widget_get_first_child(row); c; c = gtk_widget_get_next_sibling(c)) {
    if (!gtk_widget_has_css_class(c, "lumaui-bar-button"))
      continue;
    g_assert_cmpint(gtk_widget_get_height(c) >= 48 || !gtk_widget_get_visible(c), ==, TRUE);
    if (gtk_widget_has_css_class(c, "primary"))
      g_assert_false(gtk_widget_has_css_class(c, "glyph-only")); /* it keeps its label */
    else if (gtk_widget_has_css_class(c, "labelled"))
      g_assert_true(gtk_widget_has_css_class(c, "glyph-only"));
  }
  /* Too many for the width: the rest go in ⋯, before the primary; the first control and the primary stay. */
  GtkWidget *more = control_named(row, "More");
  g_assert_nonnull(more);
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_next_sibling(more), "primary"));
  g_assert_true(gtk_widget_get_visible(gtk_widget_get_first_child(row)));
  g_assert_cmpint(gtk_widget_get_width(center), <=, 400 - 24);
  g_signal_emit_by_name(more, "clicked");
  g_assert_cmpstr(luma_action_center_get_grown(LUMA_ACTION_CENTER(center)), ==, "more");
  GtkWidget *panel = find_class(find_class(center, "lumaui-ac-panel"), "lumaui-panel-list");
  int rows = 0;
  GtkWidget *last = NULL;
  for (GtkWidget *c = gtk_widget_get_first_child(panel); c; c = gtk_widget_get_next_sibling(c))
    if (gtk_widget_has_css_class(c, "lumaui-panel-row")) {
      rows++;
      last = c;
    }
  g_assert_cmpint(rows, >=, 6); /* every grouped member stays available */
  g_assert_true(gtk_widget_has_css_class(last, "danger")); /* the destructive row goes last */
  spin();
  /* Grown on a phone: the full width less 16 a side. */
  g_assert_cmpint(gtk_widget_get_width(center), ==, gtk_widget_get_width(content) - 32);
  luma_action_center_fold(ac);
  luma_action_center_show_bar(ac, items, 1, NULL);
  luma_action_center_set_phone_wide(ac, TRUE);
  spin();
  g_assert_cmpint(gtk_widget_get_width(center), ==, gtk_widget_get_width(content) - 32);
  luma_action_center_set_phone_wide(ac, FALSE);
  spin();
  g_assert_cmpint(gtk_widget_get_width(center), <, gtk_widget_get_width(content) - 32);
  for (guint i = 0; i < G_N_ELEMENTS(items); i++)
    g_object_unref(items[i]);
  gtk_window_destroy(GTK_WINDOW(window));
}

static int releases;
static void test_hold(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(1000, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  LumaBarItem *items[] = {action("copy", NULL, "Copy")};
  luma_action_center_show_bar(ac, items, 1, NULL);
  g_signal_connect(center, "released", G_CALLBACK(count), &releases);
  luma_action_center_hold(ac, "Moving", "Budget.xlsx", NULL, NULL);
  g_assert_true(luma_action_center_get_holding(ac));
  GtkWidget *hold = find_class(center, "lumaui-ac-hold");
  g_assert_true(gtk_widget_get_visible(hold));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(find_class(hold, "lumaui-ac-hold-kind"))), ==, "MOVING");
  GtkWidget *ask = find_class(center, "lumaui-ac-where-prompt");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(ask)), ==, "Where are we moving this?");
  g_assert_false(gtk_widget_get_visible(find_class(center, "lumaui-ac-row")));
  LumaBarItem *here = luma_bar_item_new_action(NULL, "Move here", NULL);
  luma_action_center_set_hold_target(ac, "Move to", "Documents", "folder", here);
  g_assert_null(find_class(center, "lumaui-ac-where-prompt"));
  GtkWidget *where = find_class(center, "lumaui-ac-where");
  g_assert_nonnull(find_class(where, "primary"));
  g_assert_nonnull(find_class(where, "keep-label"));
  /* The bar shows across views: a new bar keeps the held row. */
  luma_action_center_show_bar(ac, items, 1, NULL);
  g_assert_true(luma_action_center_get_holding(ac));
  spin();
  g_assert_cmpint(gtk_widget_get_width(center), ==, 380);
  /* ✕ lets go. */
  GtkWidget *let_go = g_object_get_data(G_OBJECT(hold), "lumaui-let-go");
  g_signal_emit_by_name(let_go, "clicked");
  g_assert_cmpint(releases, ==, 1);
  g_assert_false(luma_action_center_get_holding(ac));
  g_assert_false(gtk_widget_get_visible(find_class(center, "lumaui-ac-hold")));
  g_assert_true(gtk_widget_get_visible(find_class(center, "lumaui-ac-row")));
  g_object_unref(here);
  g_object_unref(items[0]);
  gtk_window_destroy(GTK_WINDOW(window));
}

static char *v71_searched;
static void v71_search_changed(LumaBarItem *item G_GNUC_UNUSED, const char *text, gpointer data G_GNUC_UNUSED) {
  g_free(v71_searched);
  v71_searched = g_strdup(text);
}

static void test_search(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(1000, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  /* The released persistent search item (platform .89), made v71's collapsed glyph. */
  LumaBarItem *search = luma_bar_item_new_search("Search");
  luma_bar_item_search_set_collapsed(search, TRUE);
  g_assert_cmpint(luma_bar_item_get_kind(search), ==, LUMA_BAR_ITEM_SEARCH);
  g_signal_connect(search, "search-changed", G_CALLBACK(v71_search_changed), NULL);
  LumaBarItem *items[] = {search, action("plus", NULL, "Add")};
  luma_action_center_show_bar(ac, items, 2, NULL);
  spin();
  GtkWidget *row = find_class(center, "lumaui-ac-row");
  GtkWidget *host = find_class(row, "lumaui-bar-search-host");
  g_assert_nonnull(host);
  g_assert_true(gtk_widget_has_css_class(host, "folded")); /* a glyph at every width while empty */
  /* Pressed, the bar turns into the field in place: the bottom-most row, with ✕. */
  GtkWidget *glyph = gtk_stack_get_visible_child(GTK_STACK(host));
  g_signal_emit_by_name(glyph, "clicked");
  GtkWidget *entry_row = gtk_widget_get_last_child(find_class(center, "lumaui-ac-bar"));
  g_assert_true(gtk_widget_get_visible(entry_row));
  g_assert_true(gtk_widget_get_parent(host) == entry_row);
  g_assert_false(gtk_widget_get_visible(row));
  g_assert_false(gtk_widget_has_css_class(host, "folded"));
  GtkWidget *entry = find_class(host, "lumaui-bar-search");
  g_assert_cmpstr(gtk_search_entry_get_placeholder_text(GTK_SEARCH_ENTRY(entry)), ==, "Search");
  GtkWidget *search_icon = gtk_widget_get_first_child(entry);
  g_assert_true(GTK_IS_IMAGE(search_icon));
  g_autofree char *expected_icon = luma_ui_icon_name("search");
  g_assert_cmpstr(gtk_image_get_icon_name(GTK_IMAGE(search_icon)), ==, expected_icon);
  gtk_editable_set_text(GTK_EDITABLE(entry), "budget");
  g_assert_cmpstr(v71_searched, ==, "budget");
  g_assert_cmpstr(luma_bar_item_search_get_text(search), ==, "budget");
  /* ✕ closes it: the field clears, the row comes back with the glyph in its place. */
  GtkWidget *close = control_named(entry_row, "Close search");
  g_assert_nonnull(close);
  g_signal_emit_by_name(close, "clicked");
  g_assert_cmpstr(v71_searched, ==, "");
  g_assert_true(gtk_widget_get_visible(row));
  g_assert_true(gtk_widget_get_parent(host) == row);
  g_assert_true(gtk_widget_get_first_child(row) == host);
  g_assert_false(gtk_widget_get_visible(entry_row));
  g_assert_true(gtk_widget_has_css_class(host, "folded"));
  /* The released default (not collapsed): a field that stays in the row at desktop width. */
  LumaBarItem *keep = luma_bar_item_new_search("Search files");
  LumaBarItem *kept[] = {keep, action("plus", NULL, "Add")};
  luma_action_center_show_bar(ac, kept, 2, NULL);
  GtkWidget *kept_host = find_class(find_class(center, "lumaui-ac-row"), "lumaui-bar-search-host");
  g_assert_nonnull(kept_host);
  g_assert_false(gtk_widget_has_css_class(kept_host, "folded"));
  g_object_unref(keep);
  g_object_unref(kept[1]);
  g_object_unref(items[1]);
  g_object_unref(search);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_safe_area(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(1000, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  GtkWidget *scroller = gtk_scrolled_window_new();
  gtk_widget_set_vexpand(scroller, TRUE);
  GtkWidget *list = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  for (int i = 0; i < 60; i++)
    gtk_box_append(GTK_BOX(list), gtk_label_new("A line"));
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroller), list);
  gtk_box_append(GTK_BOX(content), scroller);
  luma_action_center_attach_scroller(ac, GTK_SCROLLED_WINDOW(scroller));
  LumaBarItem *items[] = {action("copy", NULL, "Copy")};
  luma_action_center_show_bar(ac, items, 1, NULL);
  spin();
  graphene_rect_t bar, box;
  GtkWidget *host = GTK_WIDGET(luma_layer_host_window_host(content));
  g_assert_true(gtk_widget_compute_bounds(center, host, &bar));
  g_assert_true(gtk_widget_compute_bounds(scroller, host, &box));
  int expected = (int)(box.origin.y + box.size.height - bar.origin.y + 0.5f) + 20;
  g_assert_cmpint(gtk_widget_get_margin_bottom(list), ==, expected);
  /* A grown panel floats over the page: the room stays the row's. */
  luma_action_center_grow_panel(ac, "details", luma_bar_panel_new("Details"));
  spin();
  g_assert_cmpint(gtk_widget_get_margin_bottom(list), ==, expected);
  /* Only a showing bar counts. */
  luma_action_center_hide_bar(ac);
  spin();
  g_assert_cmpint(gtk_widget_get_margin_bottom(list), ==, 0);
  g_object_unref(items[0]);
  gtk_window_destroy(GTK_WINDOW(window));
}

/* v71 panel parts, the foot, a sheet, SharePanel, panel-changed. */
static char *panel_key, *share_choice, *share_person;
static void panel_changed(LumaActionCenter *c G_GNUC_UNUSED, const char *key, gpointer d G_GNUC_UNUSED) {
  g_free(panel_key);
  panel_key = g_strdup(key);
}
static void shared(LumaSharePanel *p G_GNUC_UNUSED, const char *choice, const char *person, gpointer d G_GNUC_UNUSED) {
  g_free(share_choice);
  g_free(share_person);
  share_choice = g_strdup(choice);
  share_person = g_strdup(person);
}

static void test_parts(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(1000, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  LumaBarItem *items[] = {action("share-2", NULL, "Share")};
  luma_action_center_show_bar(ac, items, 1, NULL);
  g_signal_connect(center, "panel-changed", G_CALLBACK(panel_changed), NULL);
  const char *people[] = {"Priya Raman", "Nora Lind", NULL};
  GtkWidget *share = luma_share_panel_new(NULL, people);
  g_signal_connect(share, "chosen", G_CALLBACK(shared), NULL);
  luma_action_center_grow_panel(ac, "share", share);
  g_assert_cmpstr(panel_key, ==, "share");
  GtkWidget *person = find_class(share, "lumaui-share-panel-person");
  g_signal_emit_by_name(person, "clicked");
  g_assert_cmpstr(share_choice, ==, "send-to");
  g_assert_cmpstr(share_person, ==, "Priya Raman");
  g_assert_cmpstr(panel_key, ==, ""); /* it folded */
  /* Tiles and choices. */
  GtkWidget *panel = luma_bar_panel_new("View as");
  GtkWidget *tiles = luma_bar_tiles_new(2, FALSE, FALSE);
  luma_bar_tiles_add(tiles, "layout-grid", "Grid", NULL, TRUE, FALSE);
  GtkWidget *list = luma_bar_tiles_add(tiles, "list", "List", NULL, FALSE, FALSE);
  gtk_box_append(GTK_BOX(panel), tiles);
  GtkWidget *choices = luma_panel_choices_new(NULL);
  luma_panel_choices_add(choices, "name", "Name", NULL);
  luma_panel_choices_add(choices, "date", "Date", NULL);
  luma_panel_choices_set_decoration(choices, "name", "arrow-up");
  luma_panel_choices_set_document_style(choices, TRUE);
  g_assert_true(gtk_box_get_homogeneous(GTK_BOX(choices)));
  g_assert_true(gtk_widget_get_hexpand(gtk_widget_get_first_child(choices)));
  luma_panel_choices_set_document_style(choices, FALSE);
  g_assert_false(gtk_box_get_homogeneous(GTK_BOX(choices)));
  g_assert_false(gtk_widget_get_hexpand(gtk_widget_get_first_child(choices)));
  gtk_box_append(GTK_BOX(panel), choices);
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_first_child(choices), "on"));
  g_signal_emit_by_name(gtk_widget_get_last_child(choices), "clicked");
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_last_child(choices), "on"));
  luma_action_center_grow_panel(ac, "view", panel);
  g_signal_emit_by_name(gtk_widget_get_last_child(choices), "clicked");
  g_assert_cmpstr(luma_action_center_get_grown(ac), ==, "view"); /* a choice leaves it open */
  g_signal_emit_by_name(list, "clicked");
  g_assert_null(luma_action_center_get_grown(ac)); /* a tile folds it */
  GtkWidget *row = luma_bar_panel_add_row(luma_bar_panel_new(NULL), "folder", "Home", NULL, FALSE);
  luma_bar_panel_row_set_state(row, FALSE, TRUE);
  g_assert_true(gtk_widget_has_css_class(row, "current"));
  /* The two-row foot, and a sheet. */
  GtkWidget *field = gtk_entry_new();
  luma_action_center_set_foot(ac, field);
  GtkWidget *foot = find_class(center, "lumaui-ac-foot");
  g_assert_true(gtk_widget_get_visible(foot));
  g_assert_true(gtk_widget_get_parent(field) == foot);
  luma_action_center_set_foot(ac, NULL);
  g_assert_false(gtk_widget_get_visible(foot));
  LumaModalHandle *sheet = luma_action_center_sheet(ac, gtk_label_new("form"), "Add a provider");
  g_assert_nonnull(sheet);
  g_assert_true(gtk_widget_has_css_class(luma_modal_handle_get_card(sheet), "lumaui-bar-frame"));
  luma_modal_handle_close(sheet);
  spin();
  g_object_unref(items[0]);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_compact_picker(void) {
  LumaBarItem *item = luma_bar_item_new_action("image", "Library", NULL);
  luma_bar_item_set_dropdown(item, TRUE, TRUE);
  GtkWidget *control = g_object_ref_sink(luma_bar_item_create_control(item, "bar"));
  g_assert_true(gtk_widget_has_css_class(control, "compact-picker"));
  g_assert_true(gtk_widget_has_css_class(control, "keep-label"));
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(control));
  g_assert_cmpstr(gtk_image_get_icon_name(GTK_IMAGE(gtk_widget_get_last_child(line))), ==, "lumaui-chevron-down-symbolic");
  g_object_unref(control);
  g_object_unref(item);
}

static void test_bar_bottom(void) {
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 600, 420);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_window_set_child(GTK_WINDOW(window), content);
  LumaLayerHost *host = luma_layer_host_install(GTK_WINDOW(window));
  GtkWidget *center = luma_action_center_new(NULL);
  luma_action_center_set_bar_bottom(LUMA_ACTION_CENTER(center), 16);
  luma_action_center_attach(LUMA_ACTION_CENTER(center), content);
  LumaBarItem *icon = action("check", NULL, "Confirm");
  LumaBarItem *items[] = {icon};
  luma_action_center_show_bar(LUMA_ACTION_CENTER(center), items, 1, NULL);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  graphene_rect_t bounds;
  g_assert_true(gtk_widget_compute_bounds(center, GTK_WIDGET(host), &bounds));
  g_assert_cmpint((int)(bounds.origin.y + bounds.size.height), ==,
                  gtk_widget_get_height(GTK_WIDGET(host)) - 16);
  GtkWidget *button = find_class(center, "lumaui-bar-button");
  g_assert_cmpint(gtk_widget_get_width(button), ==, 36);
  g_assert_cmpint(gtk_widget_get_height(button), ==, 36);

  LumaBarItem *format = action("chevron-down", "Currency", NULL);
  luma_bar_item_set_icon_trailing(format, TRUE);
  LumaBarItem *align[3] = {
    action("text-align-start", NULL, "Left"),
    action("text-align-center", NULL, "Centre"),
    action("text-align-end", NULL, "Right"),
  };
  for (guint i = 0; i < 3; i++)
    luma_bar_item_set_group(align[i], "Align");
  LumaBarItem *grouped[] = {format, align[0], align[1], align[2]};
  luma_action_center_show_bar(LUMA_ACTION_CENTER(center), grouped, G_N_ELEMENTS(grouped), NULL);
  spin();
  GtkWidget *format_control = gtk_widget_get_first_child(find_class(center, "lumaui-ac-row"));
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(format_control));
  g_assert_true(GTK_IS_LABEL(gtk_widget_get_first_child(line)));
  GtkWidget *group = find_class(center, "lumaui-bar-group");
  g_assert_nonnull(group);
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(group)), ==, GTK_ACCESSIBLE_ROLE_GROUP);
  g_assert_cmpint(gtk_widget_get_width(group), >=, 126);
  for (GtkWidget *child = gtk_widget_get_first_child(group); child; child = gtk_widget_get_next_sibling(child))
    g_assert_cmpint(gtk_widget_get_width(child), ==, 42);
  g_object_unref(format);
  for (guint i = 0; i < 3; i++) g_object_unref(align[i]);
  g_object_unref(icon);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_spacer_preserves_navigation_width(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(402, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  LumaBarItem *items[] = {action("chevron-left", "Thursday", "Back"),
                         luma_bar_item_new_spacer(), action("x", NULL, "Close")};
  luma_bar_item_set_keep_label(items[0], TRUE);
  luma_action_center_show_bar(ac, items, 3, NULL);
  spin();
  GtkWidget *back = luma_action_center_get_bar_control(ac, 0);
  int width = gtk_widget_get_width(back);
  luma_action_center_grow_panel(ac, "details", luma_bar_panel_new("Details"));
  spin();
  g_assert_false(gtk_widget_get_hexpand(back));
  g_assert_cmpint(gtk_widget_get_width(back), ==, width);
  g_assert_cmpint(width, <, 150);
  gtk_window_destroy(GTK_WINDOW(window));
  for (guint i = 0; i < 3; i++) g_object_unref(items[i]);
}

static void test_phone_grown_inset(void) {
  GtkWidget *content, *center;
  GtkWidget *window = center_window(402, &content, &center);
  LumaActionCenter *ac = LUMA_ACTION_CENTER(center);
  LumaBarItem *items[] = {action("info", NULL, "Details")};
  luma_action_center_show_bar(ac, items, 1, NULL);
  spin();
  int resting = gtk_widget_get_width(center);
  g_assert_cmpint(luma_action_center_get_phone_grown_inset(ac), ==, -1);
  luma_action_center_grow_panel(ac, "details", luma_bar_panel_new("Details"));
  spin();
  g_assert_cmpint(gtk_widget_get_width(center), ==, 370);
  luma_action_center_set_phone_grown_inset(ac, 20);
  spin();
  g_assert_cmpint(gtk_widget_get_width(center), ==, 362);
  luma_action_center_set_phone_grown_inset(ac, 16);
  const int widths[] = {720, 1180, 360, 402};
  for (guint i = 0; i < G_N_ELEMENTS(widths); i++) {
    gtk_window_set_default_size(GTK_WINDOW(window), widths[i], 700);
    spin();
    g_assert_cmpint(gtk_widget_get_width(center), ==, widths[i] < 560 ? widths[i] - 32 : 380);
  }
  luma_action_center_fold(ac);
  spin();
  g_assert_cmpint(gtk_widget_get_width(center), ==, resting);
  luma_action_center_set_phone_grown_inset(ac, -1);
  g_assert_cmpint(luma_action_center_get_phone_grown_inset(ac), ==, -1);
  gtk_window_destroy(GTK_WINDOW(window));
  g_object_unref(items[0]);
}

static void test_square_chip_preview(void) {
  const int widths[] = {1000, 400};
  for (guint i = 0; i < G_N_ELEMENTS(widths); i++) {
    GtkWidget *window = gtk_window_new();
    gtk_window_set_default_size(GTK_WINDOW(window), widths[i], 500);
    GtkWidget *host = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_window_set_child(GTK_WINDOW(window), host);
    LumaActionCenter *center = LUMA_ACTION_CENTER(luma_action_center_new(NULL));
    luma_action_center_attach(center, host);
    LumaBarItem *chip = luma_bar_item_new_chip("Camera", "app-window", TRUE);
    GdkPaintable *portrait = gdk_paintable_new_empty(100, 300);
    luma_bar_item_chip_set_paintable(chip, portrait);
    g_object_unref(portrait);
    LumaBarItem *items[] = {chip};
    luma_action_center_show_bar(center, items, 1, NULL);
    gtk_window_present(GTK_WINDOW(window));
    spin();
    GtkWidget *preview = find_class(GTK_WIDGET(center), "lumaui-bar-chip-preview");
    g_assert_nonnull(preview);
    g_assert_cmpint(gtk_widget_get_width(preview), ==, 28);
    g_assert_cmpint(gtk_widget_get_height(preview), ==, 28);
    gtk_window_destroy(GTK_WINDOW(window));
    g_object_unref(chip);
  }
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  luma_ui_install();
  g_test_add_func("/lumaui/action-center/items", test_items);
  g_test_add_func("/lumaui/action-center/square-chip-preview", test_square_chip_preview);
  g_test_add_func("/lumaui/action-center/phone-grown-inset", test_phone_grown_inset);
  g_test_add_func("/lumaui/action-center/spacer-navigation", test_spacer_preserves_navigation_width);
  g_test_add_func("/lumaui/action-center/compact-picker", test_compact_picker);
  g_test_add_func("/lumaui/action-center/center", test_center);
  g_test_add_func("/lumaui/action-center/no-editor", test_no_editor);
  g_test_add_func("/lumaui/action-center/words-alone", test_words_alone);
  g_test_add_func("/lumaui/action-center/search-modes", test_search_and_modes);
  g_test_add_func("/lumaui/action-center/search-phone", test_search_phone_fold);
  g_test_add_func("/lumaui/action-center/form", test_form);
  g_test_add_func("/lumaui/action-center/grow", test_grow);
  g_test_add_func("/lumaui/action-center/panel-close", test_panel_close);
  g_test_add_func("/lumaui/action-center/document-toolbar-panel-width", test_document_toolbar_panel_width);
  g_test_add_func("/lumaui/action-center/phone", test_phone);
  g_test_add_func("/lumaui/action-center/hold", test_hold);
  g_test_add_func("/lumaui/action-center/search", test_search);
  g_test_add_func("/lumaui/action-center/safe-area", test_safe_area);
  g_test_add_func("/lumaui/action-center/parts", test_parts);
  g_test_add_func("/lumaui/action-center/bar-bottom", test_bar_bottom);
  return g_test_run();
}
