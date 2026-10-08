/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI TextField, HeroTitleField and ParagraphField against content_field.py. */
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

static void remember(gpointer instance G_GNUC_UNUSED, const char *text, gpointer data) {
  g_free(*(char **)data);
  *(char **)data = g_strdup(text);
}

static void test_text_field(void) {
  GtkWidget *widget = g_object_ref_sink(luma_text_field_new("Server address", "url"));
  LumaTextField *self = LUMA_TEXT_FIELD(widget);
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-field"));
  GtkWidget *label = gtk_widget_get_first_child(widget);
  GtkWidget *entry = gtk_widget_get_next_sibling(label);
  GtkWidget *hint = gtk_widget_get_next_sibling(entry);
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-field-label"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "Server address");
  g_assert_true(GTK_IS_ENTRY(entry));
  g_assert_true(gtk_widget_has_css_class(entry, "lumaui-field-well"));
  g_assert_cmpint(gtk_entry_get_input_purpose(GTK_ENTRY(entry)), ==, GTK_INPUT_PURPOSE_URL);
  g_assert_true(gtk_entry_get_visibility(GTK_ENTRY(entry)));
  g_assert_true(gtk_widget_has_css_class(hint, "lumaui-field-hint"));
  g_assert_false(gtk_widget_get_visible(hint));

  luma_text_field_set_hint(self, "Luma signs in with a token.");
  g_assert_true(gtk_widget_get_visible(hint));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(hint)), ==, "Luma signs in with a token.");
  luma_text_field_set_hint(self, NULL);
  g_assert_false(gtk_widget_get_visible(hint));
  luma_text_field_set_placeholder(self, "music.example.com");
  g_assert_cmpstr(gtk_entry_get_placeholder_text(GTK_ENTRY(entry)), ==, "music.example.com");

  char *changed = NULL, *activated = NULL;
  g_signal_connect(widget, "changed", G_CALLBACK(remember), &changed);
  g_signal_connect(widget, "activate", G_CALLBACK(remember), &activated);
  luma_text_field_set_text(self, "music.example.org");
  g_assert_cmpstr(changed, ==, "music.example.org");
  g_assert_cmpstr(luma_text_field_get_text(self), ==, "music.example.org");
  g_signal_emit_by_name(entry, "activate");
  g_assert_cmpstr(activated, ==, "music.example.org");
  g_free(changed);
  g_free(activated);
  g_object_unref(widget);

  widget = g_object_ref_sink(luma_text_field_new("PIN", "pin"));
  entry = gtk_widget_get_next_sibling(gtk_widget_get_first_child(widget));
  g_assert_false(gtk_entry_get_visibility(GTK_ENTRY(entry)));
  g_object_unref(widget);

  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "a field's purpose is one of*");
  g_assert_null(luma_text_field_new("Fax", "fax"));
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "a text field needs a label");
  g_assert_null(luma_text_field_new("  ", NULL));
  g_test_assert_expected_messages();
}

static void test_hero_title_field(void) {
  GtkWidget *widget = luma_hero_title_field_new("Priya Raman", NULL);
  LumaHeroTitleField *self = LUMA_HERO_TITLE_FIELD(widget);
  g_assert_true(GTK_IS_ENTRY(widget));
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-hero-field"));
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-t-hero"));
  g_assert_true(gtk_widget_has_css_class(widget, "editable"));
  g_assert_cmpstr(gtk_entry_get_placeholder_text(GTK_ENTRY(widget)), ==, "Name");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_LABEL, "Name");
  g_assert_cmpfloat(gtk_editable_get_alignment(GTK_EDITABLE(widget)), ==, 0.5);

  GtkWidget *window = gtk_window_new();
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  GtkWidget *other = gtk_button_new_with_label("x");
  gtk_box_append(GTK_BOX(box), widget);
  gtk_box_append(GTK_BOX(box), other);
  gtk_window_set_child(GTK_WINDOW(window), box);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  char *committed = NULL;
  g_signal_connect(widget, "committed", G_CALLBACK(remember), &committed);
  gtk_widget_grab_focus(widget);
  spin();
  gtk_editable_set_text(GTK_EDITABLE(widget), "Priya R.");
  g_signal_emit_by_name(widget, "activate");
  g_assert_cmpstr(committed, ==, "Priya R.");

  /* Esc puts back what was there when it was entered. */
  gtk_widget_grab_focus(widget);
  spin();
  gtk_editable_set_text(GTK_EDITABLE(widget), "Oops");
  luma_hero_title_field_commit(self);
  g_assert_cmpstr(committed, ==, "Oops");
  g_free(committed);

  luma_hero_title_field_set_editable_title(self, FALSE);
  g_assert_false(gtk_widget_has_css_class(widget, "editable"));
  g_assert_false(gtk_editable_get_editable(GTK_EDITABLE(widget)));
  g_assert_false(gtk_widget_get_focusable(widget));
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_paragraph_field(void) {
  GtkWidget *widget = g_object_ref_sink(luma_paragraph_field_new(NULL, "Add a note", NULL));
  LumaParagraphField *self = LUMA_PARAGRAPH_FIELD(widget);
  g_assert_true(GTK_IS_TEXT_VIEW(widget));
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-paragraph-field"));
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-t-body"));
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_LABEL, "Add a note");
  GtkWidget *placeholder = find_class(widget, "lumaui-paragraph-placeholder");
  g_assert_nonnull(placeholder);
  g_assert_true(gtk_widget_get_visible(placeholder));
  luma_paragraph_field_set_text(self, "Allergic to peanuts");
  g_assert_false(gtk_widget_get_visible(placeholder));
  g_autofree char *text = luma_paragraph_field_get_text(self);
  g_assert_cmpstr(text, ==, "Allergic to peanuts");
  luma_paragraph_field_set_text(self, NULL);
  g_assert_true(gtk_widget_get_visible(placeholder));
  g_object_unref(widget);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  g_test_add_func("/lumaui/text-field/text-field", test_text_field);
  g_test_add_func("/lumaui/text-field/hero-title-field", test_hero_title_field);
  g_test_add_func("/lumaui/text-field/paragraph-field", test_paragraph_field);
  return g_test_run();
}
