/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI cards against content_cards.py. */
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

static GdkPaintable *photo(int width, int height) {
  g_autoptr(GBytes) bytes = g_bytes_new_take(g_malloc0((gsize)width * height * 4), (gsize)width * height * 4);
  return GDK_PAINTABLE(gdk_memory_texture_new(width, height, GDK_MEMORY_R8G8B8A8, bytes, (gsize)width * 4));
}

static void test_card(void) {
  GtkWidget *card = g_object_ref_sink(luma_card_new(gtk_label_new("x"), TRUE));
  g_assert_true(GTK_IS_BOX(card));
  g_assert_cmpint(gtk_orientable_get_orientation(GTK_ORIENTABLE(card)), ==, GTK_ORIENTATION_VERTICAL);
  g_assert_true(gtk_widget_has_css_class(card, "lumaui-card"));
  g_assert_true(gtk_widget_has_css_class(card, "padded"));
  g_assert_true(GTK_IS_LABEL(gtk_widget_get_first_child(card)));
  luma_card_set_recessed(LUMA_CARD(card), TRUE);
  g_assert_true(gtk_widget_has_css_class(card, "recessed"));
  g_object_unref(card);

  GtkWidget *lit = g_object_ref_sink(luma_content_lit_card_new(NULL, FALSE));
  g_assert_true(gtk_widget_has_css_class(lit, "lumaui-lit-card"));
  g_assert_false(gtk_widget_has_css_class(lit, "lumaui-card"));
  g_assert_false(gtk_widget_has_css_class(lit, "padded"));
  g_assert_null(gtk_widget_get_first_child(lit));
  g_object_unref(lit);
}

static void test_avatar(void) {
  GtkWidget *avatar = g_object_ref_sink(luma_person_avatar_new("Priya Raman", 44));
  LumaPersonAvatar *self = LUMA_PERSON_AVATAR(avatar);
  g_assert_true(gtk_widget_has_css_class(avatar, "lumaui-avatar"));
  /* The hue from the name, as Python's zlib.crc32 of the folded name. */
  g_assert_true(gtk_widget_has_css_class(avatar, "lumaui-hue-354"));
  g_assert_true(gtk_widget_has_css_class(avatar, "large"));
  g_assert_cmpint(gtk_widget_get_overflow(avatar), ==, GTK_OVERFLOW_HIDDEN);
  GtkWidget *initials = gtk_widget_get_first_child(avatar);
  g_assert_true(GTK_IS_LABEL(initials));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(initials)), ==, "PR");
  g_assert_true(gtk_widget_has_css_class(initials, "lumaui-avatar-initials"));
  int minimum, natural;
  gtk_widget_measure(avatar, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(minimum, ==, 44);
  g_assert_cmpint(natural, ==, 44);

  luma_person_avatar_set_hue(self, 330);
  g_assert_false(gtk_widget_has_css_class(avatar, "lumaui-hue-354"));
  g_assert_true(gtk_widget_has_css_class(avatar, "lumaui-hue-330"));
  luma_person_avatar_set_hue(self, -1);
  luma_person_avatar_set_name(self, "  NICK ");
  g_assert_true(gtk_widget_has_css_class(avatar, "lumaui-hue-199"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(gtk_widget_get_first_child(avatar))), ==, "N");

  g_autoptr(GdkPaintable) picture = photo(20, 10);
  luma_person_avatar_set_picture(self, picture);
  g_assert_true(gtk_widget_has_css_class(avatar, "picture"));
  GtkWidget *face = gtk_widget_get_first_child(avatar);
  g_assert_true(gtk_widget_has_css_class(face, "lumaui-file-face"));
  g_assert_null(gtk_widget_get_next_sibling(face));
  luma_person_avatar_set_picture(self, NULL);
  g_assert_false(gtk_widget_has_css_class(avatar, "picture"));
  g_object_unref(avatar);

  GtkWidget *nobody = g_object_ref_sink(luma_person_avatar_new("", 32));
  g_assert_true(GTK_IS_IMAGE(gtk_widget_get_first_child(nobody)));
  g_assert_true(gtk_widget_has_css_class(nobody, "small"));
  g_assert_true(gtk_widget_has_css_class(nobody, "lumaui-hue-0"));
  g_object_unref(nobody);
}

static void clicked(GtkButton *button G_GNUC_UNUSED, gpointer data) {
  (*(int *)data)++;
}

static void test_account_card(void) {
  GtkWidget *card = g_object_ref_sink(luma_account_card_new("Nick", NULL));
  LumaAccountCard *self = LUMA_ACCOUNT_CARD(card);
  g_assert_true(GTK_IS_BUTTON(card));
  g_assert_true(gtk_widget_has_css_class(card, "lumaui-account-card"));
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(card));
  g_assert_true(gtk_widget_has_css_class(line, "lumaui-account-content"));
  GtkWidget *avatar = gtk_widget_get_first_child(line);
  g_assert_true(LUMA_IS_PERSON_AVATAR(avatar));
  GtkWidget *text = gtk_widget_get_next_sibling(avatar);
  GtkWidget *name = gtk_widget_get_first_child(text);
  GtkWidget *caption = gtk_widget_get_next_sibling(name);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(name)), ==, "Nick");
  g_assert_true(gtk_widget_has_css_class(name, "lumaui-account-name"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(caption)), ==, "Luma account");
  g_assert_true(gtk_widget_has_css_class(caption, "lumaui-t-caption"));
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_next_sibling(text), "lumaui-account-chevron"));
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(card), GTK_ACCESSIBLE_PROPERTY_LABEL, "Nick, Luma account");

  luma_account_card_set_selected(self, TRUE);
  g_assert_true(gtk_widget_has_css_class(card, "on"));
  luma_account_card_set_selected(self, FALSE);
  g_assert_false(gtk_widget_has_css_class(card, "on"));
  luma_account_card_set_recessed(self, TRUE);
  g_assert_true(gtk_widget_has_css_class(card, "recessed"));
  int minimum, natural;
  gtk_widget_measure(avatar, GTK_ORIENTATION_VERTICAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(natural, ==, 34);
  luma_account_card_set_hue(self, 12);
  g_assert_true(gtk_widget_has_css_class(avatar, "lumaui-hue-12"));

  int count = 0;
  g_signal_connect(card, "clicked", G_CALLBACK(clicked), &count);
  g_signal_emit_by_name(card, "clicked");
  g_assert_cmpint(count, ==, 1);
  g_object_unref(card);
}

static void test_lit_header(void) {
  GtkWidget *header = luma_content_lit_header_new();
  LumaContentLitHeader *self = LUMA_CONTENT_LIT_HEADER(header);
  g_assert_true(gtk_widget_has_css_class(header, "lumaui-lit-header"));
  g_assert_false(gtk_widget_get_can_target(header));
  GtkWidget *picture = gtk_widget_get_first_child(header);
  GtkWidget *wash = gtk_widget_get_next_sibling(picture);
  g_assert_true(gtk_widget_has_css_class(picture, "lumaui-lit-picture"));
  g_assert_false(gtk_widget_get_visible(picture));
  g_assert_true(gtk_widget_has_css_class(wash, "lumaui-lit-wash"));
  g_assert_true(gtk_widget_has_css_class(wash, "lumaui-hue-0"));

  luma_content_lit_header_set_source(self, NULL, "media", NULL);
  g_assert_true(gtk_widget_has_css_class(wash, "media"));
  g_assert_false(gtk_widget_has_css_class(wash, "lumaui-hue-0"));
  luma_content_lit_header_set_hue(self, 330);
  g_assert_false(gtk_widget_has_css_class(wash, "media"));
  g_assert_true(gtk_widget_has_css_class(wash, "lumaui-hue-330"));
  luma_content_lit_header_set_hue(self, -1);
  g_assert_true(gtk_widget_has_css_class(wash, "media"));
  g_assert_false(gtk_widget_has_css_class(wash, "lumaui-hue-330"));

  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "a lit header's tone is one of*");
  luma_content_lit_header_set_source(self, NULL, "purple", NULL);
  g_test_assert_expected_messages();
  g_assert_true(gtk_widget_has_css_class(wash, "media"));

  g_autoptr(GdkPaintable) texture = photo(64, 48);
  luma_content_lit_header_set_source(self, texture, NULL, "Priya Raman");
  luma_content_lit_header_set_focus(self, 0.3, 0.2);
  g_assert_true(gtk_widget_get_visible(picture));
  g_assert_false(gtk_widget_has_css_class(wash, "media"));
  g_assert_true(gtk_widget_has_css_class(wash, "lumaui-hue-354"));

  /* Draws, and lays the light out to where it fades. */
  GtkWidget *window = gtk_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 600, 500);
  gtk_window_set_child(GTK_WINDOW(window), header);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  g_assert_cmpint(gtk_widget_get_height(header), ==, 380);
  g_assert_cmpint(gtk_widget_get_height(picture), ==, 360);
  g_assert_cmpint(gtk_widget_get_height(wash), ==, 380);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  g_test_add_func("/lumaui/cards/card", test_card);
  g_test_add_func("/lumaui/cards/avatar", test_avatar);
  g_test_add_func("/lumaui/cards/account-card", test_account_card);
  g_test_add_func("/lumaui/cards/lit-header", test_lit_header);
  return g_test_run();
}
