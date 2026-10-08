/* SPDX-License-Identifier: Apache-2.0
 * Native decoration evidence: run with an isolated display and the candidate
 * toolkit library. Uses the toolkit's computed style and GSK renderer, never
 * an application-local copy of the window recipe. */
#include <adwaita.h>
#include <stdio.h>

static void settle(void) {
  gint64 end = g_get_monotonic_time() + 100000;
  do { while (g_main_context_iteration(NULL, FALSE)); g_usleep(1000); }
  while (g_get_monotonic_time() < end);
}

static void capture(GtkWidget *window, const char *dir, const char *name) {
  GtkSnapshot *snapshot = gtk_snapshot_new();
  GtkStyleContext *style = gtk_widget_get_style_context(window);
  /* Generous space on all sides exposes clipped or missing far layers. */
  gtk_snapshot_render_background(snapshot, style, 180, 180, 360, 240);
  gtk_snapshot_render_frame(snapshot, style, 180, 180, 360, 240);
  GskRenderNode *node = gtk_snapshot_free_to_node(snapshot);
  g_assert_nonnull(node);
  g_autofree char *path = g_strdup_printf("%s/%s.node", dir, name);
  g_assert_true(gsk_render_node_write_to_file(node, path, NULL));
  graphene_rect_t viewport = GRAPHENE_RECT_INIT(0, 0, 720, 660);
  GskRenderer *renderer = gtk_native_get_renderer(GTK_NATIVE(window));
  GdkTexture *texture = gsk_renderer_render_texture(renderer, node, &viewport);
  g_autofree char *png = g_strdup_printf("%s/%s.png", dir, name);
  g_assert_true(gdk_texture_save_to_png(texture, png));
  g_object_unref(texture);
  gsk_render_node_unref(node);
}

int main(int argc, char **argv) {
  g_assert_cmpint(argc, >=, 2);
  gboolean plain = argc > 2 && g_str_equal(argv[2], "gtk");
  if (plain) gtk_init(); else adw_init();
  g_mkdir_with_parents(argv[1], 0755);
  GtkWidget *window = plain ? gtk_window_new() : adw_window_new();
  gtk_window_set_default_size(GTK_WINDOW(window), 360, 240);
  GtkWidget *child = gtk_label_new("Native window elevation");
  if (plain) gtk_window_set_child(GTK_WINDOW(window), child);
  else adw_window_set_content(ADW_WINDOW(window), child);
  if (plain) gtk_window_set_titlebar(GTK_WINDOW(window), gtk_header_bar_new());
  gtk_window_present(GTK_WINDOW(window)); settle();
  const char *modes[] = {"light", "dark", "frost", "glass"};
  for (guint i = 0; i < G_N_ELEMENTS(modes); i++) {
    g_autofree char *klass = g_strdup_printf("luma-treatment-%s", modes[i]);
    gtk_widget_add_css_class(window, klass); settle();
    for (int backdrop = 0; backdrop < 2; backdrop++) {
      if (backdrop) gtk_widget_set_state_flags(window, GTK_STATE_FLAG_BACKDROP, FALSE);
      else gtk_widget_unset_state_flags(window, GTK_STATE_FLAG_BACKDROP);
      g_autofree char *name = g_strdup_printf("%s-%s", modes[i], backdrop ? "backdrop" : "focused");
      capture(window, argv[1], name);
    }
    gtk_widget_add_css_class(window, "luma-no-frame-shadow"); settle();
    g_autofree char *off = g_strdup_printf("%s-disabled", modes[i]);
    capture(window, argv[1], off);
    gtk_widget_remove_css_class(window, "luma-no-frame-shadow");
    for (guint state = 0; state < 3; state++) {
      const char *states[] = {"maximized", "tiled", "fullscreen"};
      gtk_widget_add_css_class(window, states[state]); settle();
      g_autofree char *name = g_strdup_printf("%s-%s", modes[i], states[state]);
      capture(window, argv[1], name);
      gtk_widget_remove_css_class(window, states[state]);
    }
    gtk_widget_remove_css_class(window, klass);
  }
  double x, y; gtk_native_get_surface_transform(GTK_NATIVE(window), &x, &y);
  GdkSurface *surface = gtk_native_get_surface(GTK_NATIVE(window));
  printf("native surface: %d x %d, scale %d, shadow origin %.1f %.1f\n",
         gdk_surface_get_width(surface), gdk_surface_get_height(surface),
         gdk_surface_get_scale_factor(surface), x, y);
  gtk_window_destroy(GTK_WINDOW(window));
  return 0;
}
