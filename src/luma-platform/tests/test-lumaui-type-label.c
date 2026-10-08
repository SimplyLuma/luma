/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI TypeLabel against content_type.TypeLabel. */
#include "luma-ui.h"

static void test_type_label(void) {
  GtkWidget *widget = g_object_ref_sink(luma_type_label_new("9:41", "display"));
  LumaTypeLabel *self = LUMA_TYPE_LABEL(widget);
  g_assert_true(GTK_IS_BOX(widget));
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-type"));
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-t-display"));
  g_assert_cmpint(gtk_box_get_baseline_position(GTK_BOX(widget)), ==, GTK_BASELINE_POSITION_CENTER);
  GtkWidget *label = gtk_widget_get_first_child(widget);
  GtkWidget *unit = gtk_widget_get_next_sibling(label);
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-t-display"));
  g_assert_true(gtk_widget_has_css_class(unit, "lumaui-t-unit"));
  g_assert_false(gtk_widget_get_visible(unit));
  g_assert_cmpint(gtk_widget_get_valign(label), ==, GTK_ALIGN_BASELINE_FILL);
  g_assert_cmpstr(luma_type_label_get_text(self), ==, "9:41");
  g_assert_cmpstr(luma_type_label_get_role(self), ==, "display");

  luma_type_label_set_unit(self, ":07 AM");
  g_assert_true(gtk_widget_get_visible(unit));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(unit)), ==, ":07 AM");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_LABEL, "9:41 :07 AM");
  luma_type_label_set_unit(self, NULL);
  g_assert_false(gtk_widget_get_visible(unit));
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_LABEL, "9:41");

  /* One role at a time; "title_1" is "title-1". */
  luma_type_label_set_role(self, "title_1");
  g_assert_cmpstr(luma_type_label_get_role(self), ==, "title-1");
  g_assert_false(gtk_widget_has_css_class(widget, "lumaui-t-display"));
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-t-title-1"));
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-t-title-1"));
  g_assert_false(gtk_widget_has_css_class(label, "lumaui-t-display"));

  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "unknown LumaUI type role 'huge'");
  luma_type_label_set_role(self, "huge");
  g_test_assert_expected_messages();
  g_assert_cmpstr(luma_type_label_get_role(self), ==, "title-1");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "unknown LumaUI type role 'huge'");
  g_assert_null(luma_type_label_new("x", "huge"));
  g_test_assert_expected_messages();

  luma_type_label_set_wrap(self, TRUE);
  g_assert_true(gtk_label_get_wrap(GTK_LABEL(label)));
  g_assert_cmpint(gtk_label_get_wrap_mode(GTK_LABEL(label)), ==, PANGO_WRAP_WORD_CHAR);
  g_object_unref(widget);

  GtkWidget *plain = g_object_ref_sink(luma_type_label_new(NULL, NULL));
  g_assert_cmpstr(luma_type_label_get_role(LUMA_TYPE_LABEL(plain)), ==, "body");
  g_assert_cmpstr(luma_type_label_get_text(LUMA_TYPE_LABEL(plain)), ==, "");
  g_assert_true(gtk_widget_has_css_class(plain, "lumaui-t-body"));
  g_object_unref(plain);
}

static void test_apply_type(void) {
  GtkWidget *label = g_object_ref_sink(gtk_label_new("x"));
  luma_ui_apply_type(label, "display");
  PangoAttrList *attributes = gtk_label_get_attributes(GTK_LABEL(label));
  g_assert_nonnull(attributes);
  PangoAttrIterator *iterator = pango_attr_list_get_iterator(attributes);
  PangoAttribute *line_height = pango_attr_iterator_get(iterator, PANGO_ATTR_ABSOLUTE_LINE_HEIGHT);
  g_assert_nonnull(line_height);
  g_assert_cmpint(((PangoAttrInt *) line_height)->value, ==,
                  (int) (79.2 * PANGO_SCALE + 0.5));
  pango_attr_iterator_destroy(iterator);
  luma_ui_apply_type(label, "caption");
  iterator = pango_attr_list_get_iterator(gtk_label_get_attributes(GTK_LABEL(label)));
  g_assert_null(pango_attr_iterator_get(iterator, PANGO_ATTR_ABSOLUTE_LINE_HEIGHT));
  pango_attr_iterator_destroy(iterator);
  luma_ui_apply_type(label, "label");
  g_assert_false(gtk_widget_has_css_class(label, "lumaui-t-caption"));
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-t-label"));
  g_object_unref(label);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  g_test_add_func("/lumaui/type-label/label", test_type_label);
  g_test_add_func("/lumaui/type-label/apply-type", test_apply_type);
  return g_test_run();
}
