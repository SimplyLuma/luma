/* SPDX-License-Identifier: Apache-2.0 */
/* The property fields (LumaUI creative, KB-D). */
#include "luma-ui.h"

static GtkWidget *nth(GtkWidget *parent, int index) {
  GtkWidget *child = gtk_widget_get_first_child(parent);
  for (int i = 0; i < index && child != NULL; i++)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static void test_evaluate(void) {
  double value = 0;
  g_assert_true(luma_property_evaluate("120*2", &value));
  g_assert_cmpfloat(value, ==, 240);
  g_assert_true(luma_property_evaluate(" 50% ", &value));
  g_assert_cmpfloat(value, ==, 50);
  g_assert_true(luma_property_evaluate("12pt", &value));
  g_assert_cmpfloat(value, ==, 12);
  g_assert_true(luma_property_evaluate("(1+2)*3", &value));
  g_assert_cmpfloat(value, ==, 9);
  g_assert_true(luma_property_evaluate("2**3", &value));
  g_assert_cmpfloat(value, ==, 8);
  g_assert_true(luma_property_evaluate("-4+1", &value));
  g_assert_cmpfloat(value, ==, -3);
  g_assert_true(luma_property_evaluate("7%3", &value));
  g_assert_cmpfloat(value, ==, 1);
  g_assert_true(luma_property_evaluate("1.5/2", &value));
  g_assert_cmpfloat(value, ==, 0.75);
  g_assert_false(luma_property_evaluate("abc", &value));
  g_assert_false(luma_property_evaluate("1/0", &value));
  g_assert_false(luma_property_evaluate("", &value));
  g_assert_false(luma_property_evaluate("1+", &value));
  g_assert_false(luma_property_evaluate("__import__('os')", &value));
}

static void on_value(LumaPropertyNumber *field G_GNUC_UNUSED, double value, gpointer data) {
  g_array_append_val((GArray *)data, value);
}

static void test_number(void) {
  GtkWidget *field = g_object_ref_sink(luma_property_number_new("W", "px"));
  g_autoptr(GArray) values = g_array_new(FALSE, FALSE, sizeof(double));
  g_signal_connect(field, "value-changed", G_CALLBACK(on_value), values);
  g_assert_true(gtk_widget_has_css_class(field, "lumaui-creative-number"));
  g_assert_cmpstr(gtk_widget_get_css_name(field), ==, "entry");
  g_assert_cmpint(gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(field)), ==, GTK_ACCESSIBLE_ROLE_TEXT_BOX);
  g_assert_true(gtk_widget_has_css_class(nth(field, 0), "lumaui-creative-number-label"));
  GtkWidget *text = nth(field, 1);
  g_assert_true(GTK_IS_TEXT(text));
  g_assert_true(gtk_widget_has_css_class(nth(field, 2), "lumaui-creative-number-unit"));
  luma_property_number_set_value(LUMA_PROPERTY_NUMBER(field), 12.4);
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(text)), ==, "12");
  g_assert_cmpuint(values->len, ==, 0);
  gtk_editable_set_text(GTK_EDITABLE(text), "10*2");
  g_signal_emit_by_name(text, "activate");
  g_assert_cmpuint(values->len, ==, 1);
  g_assert_cmpfloat(g_array_index(values, double, 0), ==, 20);
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(text)), ==, "20");
  /* Nonsense puts the number back. */
  gtk_editable_set_text(GTK_EDITABLE(text), "wide");
  g_signal_emit_by_name(text, "activate");
  g_assert_cmpuint(values->len, ==, 1);
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(text)), ==, "20");
  /* Mixed, then a typed number applies to all. */
  luma_property_number_set_mixed(LUMA_PROPERTY_NUMBER(field));
  g_assert_true(gtk_widget_has_css_class(field, "mixed"));
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(text)), ==, "Mixed");
  g_signal_emit_by_name(text, "activate");
  g_assert_cmpuint(values->len, ==, 1);
  gtk_editable_set_text(GTK_EDITABLE(text), "20");
  g_signal_emit_by_name(text, "activate");
  g_assert_cmpuint(values->len, ==, 2);
  g_assert_false(luma_property_number_get_mixed(LUMA_PROPERTY_NUMBER(field)));
  /* Range and step. */
  luma_property_number_set_range(LUMA_PROPERTY_NUMBER(field), 0, 100);
  gtk_editable_set_text(GTK_EDITABLE(text), "250");
  g_signal_emit_by_name(text, "activate");
  g_assert_cmpfloat(luma_property_number_get_value(LUMA_PROPERTY_NUMBER(field)), ==, 100);
  luma_property_number_set_step(LUMA_PROPERTY_NUMBER(field), 0.5, 1);
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(text)), ==, "100.0");
  luma_property_number_set_compact(LUMA_PROPERTY_NUMBER(field), TRUE);
  g_assert_true(gtk_widget_has_css_class(field, "compact"));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*least to its greatest*");
  luma_property_number_set_range(LUMA_PROPERTY_NUMBER(field), 5, 1);
  g_test_assert_expected_messages();
  g_object_unref(field);
  /* No label: the number and its unit. */
  GtkWidget *bare = g_object_ref_sink(luma_property_number_new(NULL, "px"));
  g_assert_true(GTK_IS_TEXT(nth(bare, 0)));
  g_object_unref(bare);
}

static void on_color(LumaPropertyColor *field G_GNUC_UNUSED, GdkRGBA *rgba, gpointer data) {
  *(GdkRGBA *)data = *rgba;
}

static void test_color(void) {
  GdkRGBA rgba;
  g_assert_true(luma_property_color_parse("#1f6fe0", &rgba));
  g_autofree char *code = luma_property_color_format(&rgba);
  g_assert_cmpstr(code, ==, "1F6FE0");
  g_assert_true(luma_property_color_parse("fff", &rgba));
  g_assert_cmpfloat(rgba.red, ==, 1);
  g_assert_true(luma_property_color_parse("00000080", &rgba));
  g_autofree char *alpha = luma_property_color_format(&rgba);
  g_assert_cmpstr(alpha, ==, "00000080");
  g_assert_false(luma_property_color_parse("12345", &rgba));
  g_assert_false(luma_property_color_parse("zzzzzz", &rgba));

  GtkWidget *field = g_object_ref_sink(luma_property_color_new("Fill"));
  GdkRGBA seen = {0, 0, 0, 0};
  g_signal_connect(field, "color-changed", G_CALLBACK(on_color), &seen);
  g_assert_true(gtk_widget_has_css_class(field, "lumaui-creative-color"));
  g_assert_true(gtk_widget_has_css_class(nth(field, 0), "lumaui-creative-swatch"));
  GtkWidget *hex = nth(field, 1);
  g_assert_true(gtk_widget_has_css_class(hex, "lumaui-creative-hex"));
  GdkRGBA blue;
  luma_property_color_parse("1F6FE0", &blue);
  luma_property_color_set_rgba(LUMA_PROPERTY_COLOR(field), &blue);
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(hex)), ==, "1F6FE0");
  g_assert_cmpfloat(seen.alpha, ==, 0);
  gtk_editable_set_text(GTK_EDITABLE(hex), "#fff");
  g_signal_emit_by_name(hex, "activate");
  g_assert_cmpfloat(seen.red, ==, 1);
  g_assert_cmpfloat(seen.alpha, ==, 1);
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(hex)), ==, "FFFFFF");
  luma_property_color_set_word(LUMA_PROPERTY_COLOR(field), "Gradient");
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(hex)), ==, "Gradient");
  g_assert_false(gtk_editable_get_editable(GTK_EDITABLE(hex)));
  GtkWidget *width = luma_property_number_new(NULL, "px");
  luma_property_number_set_compact(LUMA_PROPERTY_NUMBER(width), TRUE);
  luma_property_color_add_suffix(LUMA_PROPERTY_COLOR(field), width);
  g_assert_true(nth(field, 2) == width);
  g_object_unref(field);
}

static void on_key(GObject *object G_GNUC_UNUSED, const char *key, gpointer data) {
  g_ptr_array_add(data, g_strdup(key));
}

static void test_choice(void) {
  GtkWidget *menu = g_object_ref_sink(luma_property_choice_new(LUMA_CHOICE_KIND_MENU, "Font"));
  g_autoptr(GPtrArray) keys = g_ptr_array_new_with_free_func(g_free);
  g_signal_connect(menu, "changed", G_CALLBACK(on_key), keys);
  luma_property_choice_add(LUMA_PROPERTY_CHOICE(menu), "figtree", "Figtree", NULL);
  luma_property_choice_add(LUMA_PROPERTY_CHOICE(menu), "serif", "Instrument Serif", NULL);
  GtkWidget *well = gtk_widget_get_first_child(menu);
  g_assert_true(gtk_widget_has_css_class(well, "lumaui-creative-select"));
  GtkWidget *value = nth(gtk_button_get_child(GTK_BUTTON(well)), 0);
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(value)), ==, "Figtree");
  luma_property_choice_set_current(LUMA_PROPERTY_CHOICE(menu), "serif");
  g_assert_cmpuint(keys->len, ==, 0);
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(value)), ==, "Instrument Serif");
  gtk_widget_activate_action(menu, "creative-choice.pick", "s", "figtree");
  g_assert_cmpuint(keys->len, ==, 1);
  g_assert_cmpstr(luma_property_choice_get_current(LUMA_PROPERTY_CHOICE(menu)), ==, "figtree");
  luma_property_choice_set_mixed(LUMA_PROPERTY_CHOICE(menu));
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(value)), ==, "Mixed");
  g_assert_null(luma_property_choice_get_current(LUMA_PROPERTY_CHOICE(menu)));
  g_object_unref(menu);

  GtkWidget *segments = g_object_ref_sink(luma_property_choice_new(LUMA_CHOICE_KIND_SEGMENTS, "Align"));
  g_signal_connect(segments, "changed", G_CALLBACK(on_key), keys);
  luma_property_choice_add(LUMA_PROPERTY_CHOICE(segments), "left", "Left", "text-align-start");
  luma_property_choice_add(LUMA_PROPERTY_CHOICE(segments), "center", "Centre", "text-align-center");
  luma_property_choice_add(LUMA_PROPERTY_CHOICE(segments), "right", "Right", "text-align-end");
  GtkWidget *seg_well = gtk_widget_get_first_child(segments);
  g_assert_true(gtk_widget_has_css_class(seg_well, "lumaui-creative-segments"));
  GtkWidget *box = gtk_widget_get_last_child(gtk_widget_get_first_child(seg_well));
  g_assert_true(gtk_widget_has_css_class(box, "lumaui-creative-tabs-row"));
  g_assert_true(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(nth(box, 0))));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(nth(box, 1)), ==, "Centre");
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(nth(box, 2)), TRUE);
  g_assert_cmpuint(keys->len, ==, 2);
  g_assert_cmpstr(g_ptr_array_index(keys, 1), ==, "right");
  g_assert_true(gtk_widget_has_css_class(nth(box, 2), "on"));
  luma_property_choice_set_mixed(LUMA_PROPERTY_CHOICE(segments));
  g_assert_false(gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(nth(box, 2))));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*already a choice*");
  luma_property_choice_add(LUMA_PROPERTY_CHOICE(segments), "left", "Left", NULL);
  g_test_assert_expected_messages();
  g_object_unref(segments);
}

static void on_align_action(GSimpleAction *action G_GNUC_UNUSED, GVariant *parameter, gpointer data) {
  g_ptr_array_add(data, g_variant_dup_string(parameter, NULL));
}

static void test_alignment_and_section(void) {
  g_autoptr(GPtrArray) keys = g_ptr_array_new_with_free_func(g_free);
  g_autoptr(GPtrArray) targets = g_ptr_array_new_with_free_func(g_free);
  GtkWidget *section = g_object_ref_sink(luma_property_section_new("Align"));
  GSimpleActionGroup *group = g_simple_action_group_new();
  GSimpleAction *align = g_simple_action_new("align", G_VARIANT_TYPE_STRING);
  g_signal_connect(align, "activate", G_CALLBACK(on_align_action), targets);
  g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(align));
  GSimpleAction *stroke = g_simple_action_new("stroke", NULL);
  int strokes = 0;
  g_signal_connect_swapped(stroke, "activate", G_CALLBACK(g_atomic_int_inc), &strokes);
  g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(stroke));
  gtk_widget_insert_action_group(section, "doc", G_ACTION_GROUP(group));
  GtkWidget *actions = luma_alignment_actions_new("doc.align");
  g_signal_connect(actions, "align", G_CALLBACK(on_key), keys);
  luma_property_section_append(LUMA_PROPERTY_SECTION(section), actions);
  g_assert_true(gtk_widget_has_css_class(actions, "lumaui-creative-align"));
  int buttons = 0;
  for (GtkWidget *b = gtk_widget_get_first_child(actions); b != NULL; b = gtk_widget_get_next_sibling(b))
    buttons++;
  g_assert_cmpint(buttons, ==, 6);
  g_signal_emit_by_name(nth(actions, 4), "clicked");
  g_assert_cmpstr(g_ptr_array_index(keys, 0), ==, "middle");
  g_assert_cmpuint(targets->len, ==, 1);
  g_assert_cmpstr(g_ptr_array_index(targets, 0), ==, "middle");
  g_assert_cmpstr(gtk_widget_get_tooltip_text(nth(actions, 0)), ==, "Align left");

  GtkWidget *header = gtk_widget_get_first_child(section);
  g_assert_true(gtk_widget_has_css_class(section, "lumaui-creative-section"));
  g_assert_true(gtk_widget_has_css_class(header, "lumaui-creative-section-header"));
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(nth(header, 0))), ==, "Align");
  luma_property_section_set_action(LUMA_PROPERTY_SECTION(section), "plus", "Add a stroke", "doc.stroke");
  GtkWidget *add = nth(header, 1);
  g_assert_true(gtk_widget_has_css_class(add, "lumaui-creative-section-action"));
  g_signal_emit_by_name(add, "clicked");
  g_assert_cmpint(strokes, ==, 1);
  luma_property_section_set_action(LUMA_PROPERTY_SECTION(section), NULL, NULL, NULL);
  g_assert_null(nth(header, 1));
  /* Consecutive pairs share one grid. */
  luma_property_section_add_pair(LUMA_PROPERTY_SECTION(section), luma_property_number_new("X", NULL),
                                 luma_property_number_new("Y", NULL));
  luma_property_section_add_pair(LUMA_PROPERTY_SECTION(section), luma_property_number_new("W", NULL), NULL);
  GtkWidget *grid = gtk_widget_get_last_child(section);
  g_assert_true(GTK_IS_GRID(grid));
  g_assert_true(gtk_widget_has_css_class(grid, "lumaui-creative-pairs"));
  g_assert_nonnull(gtk_grid_get_child_at(GTK_GRID(grid), 0, 1));
  g_assert_nonnull(gtk_grid_get_child_at(GTK_GRID(grid), 1, 1));
  g_assert_true(GTK_IS_BOX(gtk_grid_get_child_at(GTK_GRID(grid), 1, 1)));
  GtkWidget *heading = g_object_ref_sink(luma_property_heading_new("Colours"));
  g_assert_true(gtk_widget_has_css_class(heading, "lumaui-creative-heading"));
  g_object_unref(heading);
  g_object_unref(stroke);
  g_object_unref(align);
  g_object_unref(group);
  g_object_unref(section);
}

int main(int argc, char **argv) {
  gtk_init();
  luma_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/creative/properties/evaluate", test_evaluate);
  g_test_add_func("/lumaui/creative/properties/number", test_number);
  g_test_add_func("/lumaui/creative/properties/color", test_color);
  g_test_add_func("/lumaui/creative/properties/choice", test_choice);
  g_test_add_func("/lumaui/creative/properties/alignment-section", test_alignment_and_section);
  return g_test_run();
}
