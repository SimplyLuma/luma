/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"

static void test_text(void) {
  const struct { int n; gboolean attention; const char *text; } counts[] = {
    {0, FALSE, ""}, {7, FALSE, "7"}, {1284, FALSE, "1,284"}, {1234567, FALSE, "1,234,567"},
    {99, TRUE, "99"}, {100, TRUE, "99+"}, {100, FALSE, "100"},
  };
  for (guint i = 0; i < G_N_ELEMENTS(counts); i++) {
    g_autofree char *text = luma_ui_count_text(counts[i].n, counts[i].attention);
    g_assert_cmpstr(text, ==, counts[i].text);
  }
  const char *names[][2] = {{"Priya Raman", "PR"}, {"nora", "N"}, {"@maya lee", "ML"}, {"(555) 123", ""},
                            {"Ana Maria Silva", "AS"}};
  for (guint i = 0; i < G_N_ELEMENTS(names); i++) {
    g_autofree char *initials = luma_ui_initials(names[i][0]);
    g_assert_cmpstr(initials, ==, names[i][1]);
  }
  g_autofree char *icon = luma_ui_icon_name("share-2");
  g_assert_cmpstr(icon, ==, "lumaui-share-2-symbolic");
  g_assert_cmpint(luma_ui_get_api_level(), ==, LUMA_UI_API_LEVEL);
}

static void test_tone(void) {
  /* zlib.crc32(b"Priya Raman") % 5 and friends, from the Python kit. */
  const char *tones[] = {"create", "work", "media", "play", "tools"};
  const char *tone = luma_ui_person_tone("Priya Raman");
  gboolean known = FALSE;
  for (guint i = 0; i < G_N_ELEMENTS(tones); i++)
    known |= g_str_equal(tone, tones[i]);
  g_assert_true(known);
  g_assert_cmpstr(luma_ui_person_tone("Priya Raman"), ==, "create");
  g_assert_cmpstr(luma_ui_person_tone("Nora"), ==, "work");
}

static void test_type(void) {
  GtkWidget *label = g_object_ref_sink(gtk_label_new("x"));
  luma_ui_apply_type(label, "title-1");
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-t-title-1"));
  luma_ui_apply_type(label, "caption");
  g_assert_false(gtk_widget_has_css_class(label, "lumaui-t-title-1"));
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-t-caption"));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*unknown LumaUI type role*");
  luma_ui_apply_type(label, "huge");
  g_test_assert_expected_messages();
  GtkWidget *image = g_object_ref_sink(luma_ui_icon_new("heart"));
  g_assert_cmpstr(gtk_image_get_icon_name(GTK_IMAGE(image)), ==, "lumaui-heart-symbolic");
  g_object_unref(image);
  g_object_unref(label);
}

int main(int argc, char **argv) {
  gtk_test_init(&argc, &argv, NULL);
  g_test_add_func("/lumaui/kit/text", test_text);
  g_test_add_func("/lumaui/kit/tone", test_tone);
  g_test_add_func("/lumaui/kit/type", test_type);
  return g_test_run();
}
