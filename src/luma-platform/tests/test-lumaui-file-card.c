/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI FileCard, OpenButton, OpenInMenu and split_name against content_file.py. */
#include "luma-ui.h"

int _luma_file_app_order(const char *id_a, const char *name_a, const char *id_b, const char *name_b,
                         const char *default_id);

static char *dir;

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

static GFile *make_file(const char *name, const char *contents) {
  g_autofree char *path = g_build_filename(dir, name, NULL);
  g_assert_true(g_file_set_contents(path, contents, -1, NULL));
  return g_file_new_for_path(path);
}

static GtkWidget *nth(GtkWidget *widget, int n) {
  GtkWidget *child = gtk_widget_get_first_child(widget);
  while (child != NULL && n-- > 0)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static void test_split_name(void) {
  const char *cases[][3] = {{"press-release.write", "press-re", "lease.write"},
                            {"README", "README", ""},
                            {".bashrc", ".bashrc", ""},
                            {"a.tar.gz", "a", ".tar.gz"},
                            {"résumé-final-v2.pdf", "résumé-fin", "al-v2.pdf"},
                            {"x.toolongext", "x.toolongext", ""},
                            {"photo.JPG", "p", "hoto.JPG"},
                            {"ab.c", "a", "b.c"},
                            {"Ω.txt", "Ω", ".txt"}};
  for (guint i = 0; i < G_N_ELEMENTS(cases); i++) {
    g_autofree char *head = NULL, *tail = NULL;
    luma_file_split_name(cases[i][0], &head, &tail);
    g_assert_cmpstr(head, ==, cases[i][1]);
    g_assert_cmpstr(tail, ==, cases[i][2]);
  }
}

static void test_app_order(void) {
  /* Luma's own first (not the packages it installed), the default first in its group, then by name. */
  const char *def = "org.gnome.TextEditor.desktop";
  g_assert_cmpint(_luma_file_app_order("org.projectluma.Write.desktop", "Write", def, "Text Editor", def), <, 0);
  g_assert_cmpint(_luma_file_app_order("org.projectluma.Installed.vim.desktop", "Vim", "b.desktop", "Atom", NULL),
                  >, 0);
  g_assert_cmpint(_luma_file_app_order("b.desktop", "Zed", def, "Text Editor", def), >, 0);
  g_assert_cmpint(_luma_file_app_order("a.desktop", "apple", "b.desktop", "Banana", NULL), <, 0);
}

static void test_open_button(void) {
  GtkWidget *button = g_object_ref_sink(luma_open_button_new(NULL, "text/plain", FALSE));
  g_assert_true(GTK_IS_BUTTON(button));
  g_assert_true(gtk_widget_has_css_class(button, "lumaui-open-button"));
  g_assert_false(gtk_widget_has_css_class(button, "small"));
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(button));
  g_assert_true(gtk_widget_has_css_class(line, "lumaui-open-content"));
  g_assert_true(GTK_IS_IMAGE(nth(line, 0)));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(nth(line, 1))), ==, "Open");
  luma_open_button_set_label(LUMA_OPEN_BUTTON(button), "View");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(nth(line, 1))), ==, "View");
  g_object_unref(button);
}

static gboolean handle_open(LumaFileCard *card G_GNUC_UNUSED, gpointer data) {
  (*(int *)data)++;
  return TRUE;
}

static void test_file_card(void) {
  g_autoptr(GFile) file = make_file("launch-notes.txt", "twelve bytes");
  GtkWidget *widget = g_object_ref_sink(luma_file_card_new(file, FALSE));
  LumaFileCard *self = LUMA_FILE_CARD(widget);
  g_assert_true(luma_file_card_get_file(self) == file);
  g_assert_true(gtk_widget_has_css_class(widget, "lumaui-file-card"));
  g_assert_false(gtk_widget_has_css_class(widget, "compact"));
  GtkWidget *slot = nth(widget, 0), *text = nth(widget, 1), *open = nth(widget, 2);
  g_assert_true(gtk_widget_has_css_class(slot, "lumaui-file-slot"));
  g_assert_true(gtk_widget_has_css_class(nth(slot, 0), "lumaui-file-icon"));
  g_assert_true(gtk_widget_has_css_class(nth(slot, 1), "lumaui-file-face"));
  g_assert_false(gtk_widget_get_visible(nth(slot, 1)));
  g_assert_true(gtk_widget_has_css_class(text, "lumaui-file-text"));
  GtkWidget *name_row = nth(text, 0);
  g_assert_true(gtk_widget_has_css_class(name_row, "lumaui-file-name"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(nth(name_row, 0))), ==, "launch-");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(nth(name_row, 1))), ==, "notes.txt");
  g_assert_cmpstr(gtk_widget_get_tooltip_text(name_row), ==, "launch-notes.txt");
  GtkWidget *meta = nth(text, 1);
  g_assert_true(gtk_widget_has_css_class(meta, "lumaui-file-meta"));
  g_autofree char *kind = g_content_type_get_description("text/plain");
  g_autofree char *expected = g_strdup_printf("12 bytes · %s", kind);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(meta)), ==, expected);
  g_assert_true(LUMA_IS_OPEN_BUTTON(open));
  g_assert_true(gtk_widget_has_css_class(open, "small"));
  g_autofree char *label = g_strdup_printf("launch-notes.txt, %s", expected);
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_LABEL, label);

  int opened = 0;
  g_signal_connect(widget, "open", G_CALLBACK(handle_open), &opened);
  g_signal_emit_by_name(open, "clicked");
  g_assert_cmpint(opened, ==, 1);

  luma_file_card_set_subtitle(self, "Failed: the server stopped responding");
  luma_file_card_set_tone(self, "danger");
  meta = nth(nth(widget, 1), 1);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(meta)), ==, "12 bytes · Failed: the server stopped responding");
  g_assert_true(gtk_widget_has_css_class(meta, "danger"));
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "a file card's tone is danger or warning*");
  luma_file_card_set_tone(self, "purple");
  g_test_assert_expected_messages();

  luma_file_card_set_size(self, -1);
  luma_file_card_set_subtitle(self, NULL);
  luma_file_card_set_kind(self, "Notes");
  luma_file_card_set_name(self, "Q3 notes.txt");
  meta = nth(nth(widget, 1), 1);
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(meta)), ==, "Notes");
  g_assert_cmpstr(gtk_widget_get_tooltip_text(nth(nth(widget, 1), 0)), ==, "Q3 notes.txt");

  GtkWidget *progress = gtk_progress_bar_new();
  luma_file_card_set_extra(self, progress);
  g_assert_true(nth(nth(widget, 1), 2) == progress);
  g_autoptr(LumaBarItem) pause = luma_bar_item_new_action("pause", NULL, "app.pause");
  luma_bar_item_set_tooltip(pause, "Pause");
  luma_file_card_add_action(self, pause);
  GtkWidget *control = nth(widget, 2);
  g_assert_false(LUMA_IS_OPEN_BUTTON(control));
  g_assert_true(gtk_widget_has_css_class(control, "file"));
  g_assert_true(nth(nth(widget, 1), 2) == progress);
  luma_file_card_set_selected(self, TRUE);
  g_assert_true(gtk_widget_has_css_class(widget, "selected"));

  g_autoptr(GBytes) bytes = g_bytes_new_take(g_malloc0(8 * 8 * 4), 8 * 8 * 4);
  g_autoptr(GdkTexture) thumb = gdk_memory_texture_new(8, 8, GDK_MEMORY_R8G8B8A8, bytes, 8 * 4);
  luma_file_card_set_thumbnail(self, GDK_PAINTABLE(thumb));
  g_assert_true(gtk_widget_has_css_class(widget, "picture"));
  g_assert_true(gtk_widget_get_visible(nth(nth(widget, 0), 1)));
  g_assert_false(gtk_widget_get_visible(nth(nth(widget, 0), 0)));
  g_object_unref(widget);
}

static void test_picture_face(void) {
  g_autoptr(GBytes) bytes = g_bytes_new_take(g_malloc0(16 * 16 * 4), 16 * 16 * 4);
  g_autoptr(GdkTexture) texture = gdk_memory_texture_new(16, 16, GDK_MEMORY_R8G8B8A8, bytes, 16 * 4);
  g_autofree char *path = g_build_filename(dir, "photo.png", NULL);
  g_assert_true(gdk_texture_save_to_png(texture, path));
  /* The card decodes through gdk-pixbuf, whose loaders are sandboxed (glycin, bwrap); in a
   * container that can't nest namespaces (a package build root) nothing decodes at all, and
   * the card correctly keeps its icon face. Measure the picture face where pictures load. */
  {
    g_autoptr(GError) probe_error = NULL;
    g_autoptr(GdkPixbuf) probe = gdk_pixbuf_new_from_file_at_scale(path, 16, 16, TRUE, &probe_error);
    if (probe == NULL) {
      g_test_skip_printf("images don't decode here: %s", probe_error ? probe_error->message : "no loader");
      return;
    }
  }
  g_autoptr(GFile) file = g_file_new_for_path(path);
  GtkWidget *widget = g_object_ref_sink(luma_file_card_new(file, TRUE));
  g_assert_true(gtk_widget_has_css_class(widget, "compact"));
  int minimum, natural;
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(natural, >=, 240);
  /* The picture itself is the face, loaded off the main thread. */
  /* A busy machine (the whole suite at once) can take a while. */
  for (int i = 0; i < 20 && !gtk_widget_has_css_class(widget, "picture"); i++)
    spin();
  g_assert_true(gtk_widget_has_css_class(widget, "picture"));
  g_assert_true(gtk_widget_get_visible(nth(nth(widget, 0), 1)));
  g_object_unref(widget);
}

static void test_open_in_menu(void) {
  g_autoptr(GFile) file = make_file("readme.txt", "hello");
  LumaOpenInMenu *menu = luma_open_in_menu_new(file, NULL);
  GtkWidget *window = gtk_window_new();
  GtkWidget *anchor = gtk_button_new_with_label("Open in");
  gtk_window_set_child(GTK_WINDOW(window), anchor);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  luma_open_in_menu_popup(menu, anchor);
  spin();
  luma_open_in_menu_close(menu);
  luma_open_in_menu_close(menu);
  g_object_unref(menu);
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  dir = g_dir_make_tmp("lumaui-file-card-XXXXXX", NULL);
  g_test_add_func("/lumaui/file-card/split-name", test_split_name);
  g_test_add_func("/lumaui/file-card/app-order", test_app_order);
  g_test_add_func("/lumaui/file-card/open-button", test_open_button);
  g_test_add_func("/lumaui/file-card/file-card", test_file_card);
  g_test_add_func("/lumaui/file-card/picture-face", test_picture_face);
  g_test_add_func("/lumaui/file-card/open-in-menu", test_open_in_menu);
  int result = g_test_run();
  g_autofree char *cleanup = g_strdup_printf("rm -rf '%s'", dir);
  (void)!system(cleanup);
  return result;
}
