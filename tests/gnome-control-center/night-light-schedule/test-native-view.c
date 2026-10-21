/* SPDX-License-Identifier: GPL-2.0-or-later */
#define main upstream_view_main
#include "test-luma-view.c"
#undef main

static void capture (GtkWidget *window, const char *name)
{
  const char *out = g_getenv ("LUMA_NIGHT_TEST_OUT");
  if (!out) return;
  g_autoptr (GdkPaintable) paintable = gtk_widget_paintable_new (window);
  g_autoptr (GskRenderNode) node = NULL;
  for (guint attempt = 0; attempt < 40 && !node; attempt++)
    {
      GtkSnapshot *snapshot = gtk_snapshot_new ();
      gdk_paintable_snapshot (paintable, GDK_SNAPSHOT (snapshot), gtk_widget_get_width (window), gtk_widget_get_height (window));
      node = gtk_snapshot_free_to_node (snapshot);
      if (!node) wait_ms (50);
    }
  g_assert_nonnull (node);
  graphene_rect_t bounds = GRAPHENE_RECT_INIT (0, 0, gtk_widget_get_width (window), gtk_widget_get_height (window));
  GskRenderer *renderer = gtk_native_get_renderer (GTK_NATIVE (window));
  g_autoptr (GdkTexture) texture = gsk_renderer_render_texture (renderer, node, &bounds);
  g_autofree char *path = g_build_filename (out, name, NULL);
  g_assert_true (gdk_texture_save_to_png (texture, path));
}

static void schedule_matrix (void)
{
  g_autoptr (GSettings) iface = g_settings_new ("org.gnome.desktop.interface");
  const char *modes[] = {"24h", "12h"};
  for (guint clock = 0; clock < 2; clock++) for (guint narrow = 0; narrow < 2; narrow++)
    {
      g_settings_set_string (iface, "clock-format", modes[clock]);
      g_autoptr (CcLumaFixture) model = load ();
      GtkWidget *window = gtk_window_new ();
      GtkWidget *view = cc_luma_view_new (model, "nightlight", NULL);
      gtk_window_set_child (GTK_WINDOW (window), view);
      gtk_window_set_default_size (GTK_WINDOW (window), narrow ? 390 : 1040, 800);
      gtk_window_present (GTK_WINDOW (window)); wait_ms (150);
      g_assert_nonnull (find_name (view, "cf-nightFrom"));
      g_assert_false (gtk_widget_get_sensitive (find_name (view, "cf-nightFrom")));
      choose (find_name (view, "cf-nightWhen-manual")); drain ();
      g_assert_true (gtk_widget_get_sensitive (find_name (view, "cf-nightFrom")));
      g_autofree char *before = g_strdup (json_node_get_string (cc_luma_fixture_get (model, "nightFrom")));
      nightlight_edit_time (view, "cf-nightFrom", clock ? 10 : 22, 17, clock ? 1 : -1, FALSE);
      g_assert_cmpstr (json_node_get_string (cc_luma_fixture_get (model, "nightFrom")), ==, before);
      nightlight_edit_time (view, "cf-nightFrom", clock ? 10 : 22, 17, clock ? 1 : -1, TRUE);
      g_assert_cmpstr (json_node_get_string (cc_luma_fixture_get (model, "nightFrom")), ==, "22:17");
      nightlight_edit_time (view, "cf-nightTo", clock ? 6 : 6, 43, clock ? 0 : -1, TRUE);
      g_assert_cmpstr (json_node_get_string (cc_luma_fixture_get (model, "nightTo")), ==, "06:43");
      g_assert_true (has_text (view, clock ? "From 10:17 PM to 6:43 AM" : "From 22:17 to 06:43"));
      g_autofree char *name = g_strdup_printf ("night-light-%s-%s.png", modes[clock], narrow ? "390" : "1040");
      capture (window, name);
      nightlight_edit_time (view, "cf-nightFrom", clock ? 12 : 0, 0, clock ? 0 : -1, TRUE);
      g_assert_cmpstr (json_node_get_string (cc_luma_fixture_get (model, "nightFrom")), ==, "00:00");
      nightlight_edit_time (view, "cf-nightFrom", 12, 0, clock ? 1 : -1, TRUE);
      g_assert_cmpstr (json_node_get_string (cc_luma_fixture_get (model, "nightFrom")), ==, "12:00");
      g_assert_cmpint (gtk_widget_get_width (window), ==, narrow ? 390 : 1040);
      g_assert_null (cc_luma_view_get_error (view));
      gtk_window_destroy (GTK_WINDOW (window)); wait_ms (100);
    }
}

int main (int argc, char **argv)
{
  g_test_init (&argc, &argv, NULL); gtk_init (); load_settings_style ();
  g_test_add_func ("/settings/view/nightlight", nightlight_view);
  g_test_add_func ("/settings/view/nightlight-schedule-matrix", schedule_matrix);
  return g_test_run ();
}
