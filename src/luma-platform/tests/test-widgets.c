/* SPDX-License-Identifier: Apache-2.0 */
#include <luma-ui.h>

static void test_pane_holds_its_child(void) {
  /* The identity is the toolkit's now; the pane is the kit's, and it must
   * hold and hand back the one child an application gives it. */
  GtkWidget *pane = luma_pane_new();
  GtkWidget *label = gtk_label_new("Library");
  luma_pane_set_child(LUMA_PANE(pane), label);
  g_assert_true(luma_pane_get_child(LUMA_PANE(pane)) == label);
  g_assert_true(gtk_widget_has_css_class(pane, "luma-island") ||
                gtk_widget_has_css_class(pane, "luma-pane"));
  g_object_ref_sink(pane);
  g_object_unref(pane);
}

static void test_inspector_section(void) {
  GtkWidget *section = luma_inspector_section_new("Appearance");
  GtkWidget *label = gtk_label_new("Fill");
  luma_inspector_section_set_child(LUMA_INSPECTOR_SECTION(section), label);
  g_assert_true(luma_inspector_section_get_child(LUMA_INSPECTOR_SECTION(section)) == label);
  luma_inspector_section_set_expanded(LUMA_INSPECTOR_SECTION(section), FALSE);
  g_assert_false(gtk_widget_get_visible(label));
  g_object_ref_sink(section);
  g_object_unref(section);
}

static void test_segmented_control(void) {
  GtkWidget *control = luma_segmented_control_new();
  luma_segmented_control_append(LUMA_SEGMENTED_CONTROL(control), "pages", "Pages");
  luma_segmented_control_append(LUMA_SEGMENTED_CONTROL(control), "layers", "Layers");
  luma_segmented_control_set_selected(LUMA_SEGMENTED_CONTROL(control), "layers");
  g_assert_cmpstr(luma_segmented_control_get_selected(LUMA_SEGMENTED_CONTROL(control)), ==,
                  "layers");
  g_object_ref_sink(control);
  g_object_unref(control);
}

static void test_color_well(void) {
  GtkWidget *well = luma_color_well_new();
  luma_color_well_set_text(LUMA_COLOR_WELL(well), "#4878b8");
  const GdkRGBA *rgba = luma_color_well_get_rgba(LUMA_COLOR_WELL(well));
  g_assert_cmpfloat_with_epsilon(rgba->red, 0.2823529, 0.001);
  g_assert_nonnull(luma_color_well_get_text(LUMA_COLOR_WELL(well)));
  g_object_ref_sink(well);
  g_object_unref(well);
}

int main(int argc, char **argv) {
  gtk_init();
  g_test_init(&argc, &argv, NULL);
  g_test_add_func("/luma/ui/pane", test_pane_holds_its_child);
  g_test_add_func("/luma-ui/inspector-section", test_inspector_section);
  g_test_add_func("/luma-ui/segmented-control", test_segmented_control);
  g_test_add_func("/luma-ui/color-well", test_color_well);
  return g_test_run();
}
