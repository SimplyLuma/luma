/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI badges: CountBadge, CategoryPill and StatusPill against content_badges.py / content_contact.py. */
#include "luma-ui.h"

static void test_count_text(void) {
  const struct {
    int count;
    gboolean attention;
    const char *text;
  } cases[] = {{0, FALSE, ""},     {-4, TRUE, ""},       {7, FALSE, "7"},          {1284, FALSE, "1,284"},
               {99, TRUE, "99"},   {100, TRUE, "99+"},   {100, FALSE, "100"},      {1000000, FALSE, "1,000,000"},
               {999, FALSE, "999"}, {12345, TRUE, "99+"}};
  for (guint i = 0; i < G_N_ELEMENTS(cases); i++) {
    g_autofree char *text = luma_ui_count_text(cases[i].count, cases[i].attention);
    g_assert_cmpstr(text, ==, cases[i].text);
  }
}

static void test_count_badge(void) {
  GtkWidget *badge = g_object_ref_sink(luma_count_badge_new(1284, FALSE));
  LumaCountBadge *self = LUMA_COUNT_BADGE(badge);
  g_assert_true(GTK_IS_LABEL(badge));
  g_assert_cmpstr(gtk_widget_get_css_name(badge), ==, "label");
  g_assert_true(gtk_widget_has_css_class(badge, "lumaui-count"));
  g_assert_false(gtk_widget_has_css_class(badge, "attention"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(badge)), ==, "1,284");
  g_assert_true(gtk_widget_get_visible(badge));
  g_assert_cmpint(gtk_widget_get_halign(badge), ==, GTK_ALIGN_END);
  g_assert_cmpint(gtk_widget_get_valign(badge), ==, GTK_ALIGN_CENTER);
  g_assert_cmpfloat(gtk_label_get_xalign(GTK_LABEL(badge)), ==, 0.5);
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(badge), GTK_ACCESSIBLE_PROPERTY_LABEL, "1,284");

  luma_count_badge_set_attention(self, TRUE);
  luma_count_badge_set_count(self, 214);
  g_assert_true(gtk_widget_has_css_class(badge, "attention"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(badge)), ==, "99+");
  /* A capped badge still reads the real number. */
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(badge), GTK_ACCESSIBLE_PROPERTY_LABEL, "214");
  g_assert_cmpint(luma_count_badge_get_count(self), ==, 214);
  g_assert_true(luma_count_badge_get_attention(self));

  luma_count_badge_set_count(self, -3);
  g_assert_cmpint(luma_count_badge_get_count(self), ==, 0);
  g_assert_false(gtk_widget_get_visible(badge));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(badge)), ==, "");

  /* The properties, as Python's GObject properties. */
  g_object_set(badge, "count", 3, "attention", FALSE, NULL);
  int count = 0;
  gboolean attention = TRUE;
  g_object_get(badge, "count", &count, "attention", &attention, NULL);
  g_assert_cmpint(count, ==, 3);
  g_assert_false(attention);
  g_assert_false(gtk_widget_has_css_class(badge, "attention"));
  g_assert_true(gtk_widget_get_visible(badge));
  g_object_unref(badge);

  GtkWidget *zero = g_object_ref_sink(luma_count_badge_new(0, TRUE));
  g_assert_false(gtk_widget_get_visible(zero));
  g_assert_true(gtk_widget_has_css_class(zero, "attention"));
  g_object_unref(zero);

  if (g_test_undefined()) {
    g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "*LUMA_IS_COUNT_BADGE*");
    luma_count_badge_set_count(NULL, 1);
    g_test_assert_expected_messages();
  }
}

static void count_notified(GObject *object G_GNUC_UNUSED, GParamSpec *pspec G_GNUC_UNUSED, gpointer data) {
  (*(int *)data)++;
}

static void test_count_notify(void) {
  GtkWidget *badge = g_object_ref_sink(luma_count_badge_new(1, FALSE));
  int notified = 0;
  g_signal_connect(badge, "notify::count", G_CALLBACK(count_notified), &notified);
  luma_count_badge_set_count(LUMA_COUNT_BADGE(badge), 1);
  g_assert_cmpint(notified, ==, 0);
  luma_count_badge_set_count(LUMA_COUNT_BADGE(badge), 2);
  g_assert_cmpint(notified, ==, 1);
  g_object_unref(badge);
}

static void test_category_pill(void) {
  GtkWidget *pill = g_object_ref_sink(luma_category_pill_new(" Media "));
  LumaCategoryPill *self = LUMA_CATEGORY_PILL(pill);
  g_assert_true(GTK_IS_LABEL(pill));
  g_assert_cmpstr(gtk_widget_get_css_name(pill), ==, "label");
  g_assert_true(gtk_widget_has_css_class(pill, "lumaui-category"));
  g_assert_true(gtk_widget_has_css_class(pill, "media"));
  g_assert_cmpstr(luma_category_pill_get_category(self), ==, "media");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(pill)), ==, "Media");
  g_assert_cmpint(gtk_widget_get_halign(pill), ==, GTK_ALIGN_START);

  luma_category_pill_set_category(self, "play");
  g_assert_false(gtk_widget_has_css_class(pill, "media"));
  g_assert_true(gtk_widget_has_css_class(pill, "play"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(pill)), ==, "Play");

  /* An unknown category keeps its name (title-cased) and wears Work. */
  luma_category_pill_set_category(self, "home automation");
  g_assert_false(gtk_widget_has_css_class(pill, "play"));
  g_assert_true(gtk_widget_has_css_class(pill, "work"));
  g_assert_cmpstr(luma_category_pill_get_category(self), ==, "work");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(pill)), ==, "Home Automation");
  luma_category_pill_set_category(self, "hi-FI stuff");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(pill)), ==, "Hi-Fi Stuff");
  g_object_unref(pill);
}

static void test_status_pill(void) {
  GtkWidget *pill = g_object_ref_sink(luma_status_pill_new("on-luma", NULL));
  LumaStatusPill *self = LUMA_STATUS_PILL(pill);
  g_assert_true(GTK_IS_BOX(pill));
  g_assert_true(gtk_widget_has_css_class(pill, "lumaui-status-pill"));
  g_assert_true(gtk_widget_has_css_class(pill, "good"));
  GtkWidget *dot = gtk_widget_get_first_child(pill);
  GtkWidget *label = gtk_widget_get_next_sibling(dot);
  g_assert_true(gtk_widget_has_css_class(dot, "lumaui-status-pill-dot"));
  g_assert_true(gtk_widget_get_visible(dot));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "On Luma");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(pill), GTK_ACCESSIBLE_PROPERTY_LABEL, "On Luma");

  luma_status_pill_set_kind(self, "offline", NULL);
  g_assert_false(gtk_widget_has_css_class(pill, "good"));
  g_assert_true(gtk_widget_has_css_class(pill, "off"));
  g_assert_false(gtk_widget_get_visible(dot));
  g_assert_cmpstr(luma_status_pill_get_kind(self), ==, "offline");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "Offline");

  luma_status_pill_set_kind(self, "syncing", "Syncing 214 songs");
  g_assert_true(gtk_widget_has_css_class(pill, "busy"));
  g_assert_true(gtk_widget_get_visible(dot));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "Syncing 214 songs");
  luma_status_pill_set_kind(self, "error", "");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(label)), ==, "Needs attention");
  g_assert_true(gtk_widget_has_css_class(pill, "bad"));

  /* An unknown kind is refused and changes nothing. */
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "unknown status 'away'*");
  luma_status_pill_set_kind(self, "away", NULL);
  g_test_assert_expected_messages();
  g_assert_cmpstr(luma_status_pill_get_kind(self), ==, "error");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "unknown status 'away'*");
  g_assert_null(luma_status_pill_new("away", NULL));
  g_test_assert_expected_messages();
  g_object_unref(pill);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  g_test_add_func("/lumaui/badges/count-text", test_count_text);
  g_test_add_func("/lumaui/badges/count-badge", test_count_badge);
  g_test_add_func("/lumaui/badges/count-notify", test_count_notify);
  g_test_add_func("/lumaui/badges/category-pill", test_category_pill);
  g_test_add_func("/lumaui/badges/status-pill", test_status_pill);
  return g_test_run();
}
