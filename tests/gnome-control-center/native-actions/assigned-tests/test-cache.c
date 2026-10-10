/* SPDX-License-Identifier: GPL-2.0-or-later */
#include SETTINGS_ACTION_SOURCE
static void cache_keeps_documents (void)
{
  g_autoptr (GError) error = NULL;
  g_autofree char *base = g_dir_make_tmp ("settings-cache-test-XXXXXX", &error);
  g_assert_no_error (error);
  g_autofree char *cache = g_build_filename (base, "cache", NULL);
  g_autofree char *data = g_build_filename (base, "data", NULL);
  g_autofree char *nested = g_build_filename (cache, "nested", NULL);
  g_assert_cmpint (g_mkdir_with_parents (nested, 0700), ==, 0);
  g_assert_cmpint (g_mkdir_with_parents (data, 0700), ==, 0);
  g_autofree char *document = g_build_filename (data, "document", NULL);
  g_autofree char *cached = g_build_filename (nested, "cached", NULL);
  g_autofree char *link = g_build_filename (cache, "documents-link", NULL);
  g_assert_true (g_file_set_contents (document, "keep", -1, &error));
  g_assert_true (g_file_set_contents (cached, "remove", -1, &error));
  g_assert_cmpint (symlink (data, link), ==, 0);
  int fd = open (cache, O_DIRECTORY | O_RDONLY | O_NOFOLLOW);
  g_assert_cmpint (fd, >=, 0);
  g_assert_true (clear_cache_fd (fd, NULL, &error));
  g_assert_no_error (error);
  close (fd);
  g_assert_true (g_file_test (document, G_FILE_TEST_IS_REGULAR));
  g_assert_false (g_file_test (link, G_FILE_TEST_IS_SYMLINK));
  g_assert_false (g_file_test (cached, G_FILE_TEST_EXISTS));
  g_assert_false (g_file_test (nested, G_FILE_TEST_EXISTS));
  unlink (document); rmdir (data); rmdir (cache); rmdir (base);
}
static void cache_cancelled (void)
{
  g_autoptr (GError) error = NULL;
  g_autoptr (GCancellable) cancel = g_cancellable_new ();
  g_autofree char *dir = g_dir_make_tmp ("settings-cache-cancel-XXXXXX", &error);
  g_autofree char *file = g_build_filename (dir, "keep", NULL);
  g_assert_true (g_file_set_contents (file, "keep", -1, &error));
  int fd = open (dir, O_DIRECTORY | O_RDONLY | O_NOFOLLOW);
  g_cancellable_cancel (cancel);
  g_assert_false (clear_cache_fd (fd, cancel, &error));
  g_assert_error (error, G_IO_ERROR, G_IO_ERROR_CANCELLED);
  close (fd);
  g_assert_true (g_file_test (file, G_FILE_TEST_IS_REGULAR));
  unlink (file); rmdir (dir);
}
int main (int argc, char **argv)
{
  g_test_init (&argc, &argv, NULL);
  g_test_add_func ("/settings/apps/cache-preserves-documents", cache_keeps_documents);
  g_test_add_func ("/settings/apps/cache-cancelled", cache_cancelled);
  return g_test_run ();
}
