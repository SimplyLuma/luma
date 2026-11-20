/* SPDX-License-Identifier: Apache-2.0 */
/* Test client for luma_window_shape_v1: a GTK window with a header bar and a
 * game-like content area that draws circles on a 4:3 grid (an ellipse means
 * the content was stretched). It logs every size it lays out at as one JSON
 * line: window geometry, content size, and whether the compositor keeps the
 * ratio.
 *
 *   client --name NAME --log FILE [--ratio W:H] [--whole-window] [--size WxH]
 *          [--libadwaita]
 *
 * Without --ratio it is an ordinary window. --whole-window locks the window
 * geometry instead of the content. Built against the kit's
 * luma-window-shape.c (see run.sh). */
#include <gtk/gtk.h>
#include <stdio.h>

#include "luma-window-shape.h"

static FILE *log_file;
static const char *name = "client";
static int ratio_w;
static int ratio_h;
static gboolean whole_window;
static int last_w = -1, last_h = -1, last_cw = -1, last_ch = -1;
static gboolean enforced;

static void
draw_content (GtkDrawingArea *area, cairo_t *cr, int width, int height,
              gpointer data G_GNUC_UNUSED)
{
  int columns = 8; /* 8 x 6 cells are square only at 4:3 */
  int rows = 6;
  double cell_w = (double) width / columns;
  double cell_h = (double) height / rows;
  char text[96];

  cairo_set_source_rgb (cr, 0.11, 0.16, 0.30);
  cairo_paint (cr);
  cairo_set_source_rgb (cr, 0.98, 0.78, 0.20);
  for (int r = 0; r < rows; r++)
    for (int c = 0; c < columns; c++)
      {
        cairo_save (cr);
        cairo_translate (cr, (c + 0.5) * cell_w, (r + 0.5) * cell_h);
        cairo_scale (cr, cell_w * 0.4, cell_h * 0.4);
        cairo_arc (cr, 0, 0, 1, 0, 2 * G_PI);
        cairo_restore (cr);
        cairo_fill (cr);
      }
  cairo_set_source_rgb (cr, 1, 1, 1);
  cairo_set_line_width (cr, 4);
  cairo_rectangle (cr, 2, 2, width - 4, height - 4);
  cairo_stroke (cr);

  g_snprintf (text, sizeof text, "%s %dx%d  %.4f", name, width, height,
              height > 0 ? (double) width / height : 0);
  cairo_select_font_face (cr, "sans-serif", CAIRO_FONT_SLANT_NORMAL,
                          CAIRO_FONT_WEIGHT_BOLD);
  cairo_set_font_size (cr, 22);
  cairo_move_to (cr, 16, 34);
  cairo_set_source_rgb (cr, 0, 0, 0);
  cairo_show_text (cr, text);
}

static gboolean
tick (GtkWidget *widget, GdkFrameClock *clock, gpointer data)
{
  GtkWidget *content = data;
  GdkToplevelState state = 0;
  GdkSurface *surface = gtk_native_get_surface (GTK_NATIVE (widget));
  int w = gtk_widget_get_width (widget);
  int h = gtk_widget_get_height (widget);
  int cw = gtk_widget_get_width (content);
  int ch = gtk_widget_get_height (content);

  if (surface && GDK_IS_TOPLEVEL (surface))
    state = gdk_toplevel_get_state (GDK_TOPLEVEL (surface));
  if (w == last_w && h == last_h && cw == last_cw && ch == last_ch)
    return G_SOURCE_CONTINUE;
  last_w = w; last_h = h; last_cw = cw; last_ch = ch;
  fprintf (log_file,
           "{\"ev\":\"size\",\"t\":%" G_GINT64_FORMAT ",\"w\":%d,\"h\":%d,"
           "\"cw\":%d,\"ch\":%d,\"maximized\":%s,\"fullscreen\":%s,"
           "\"tiled\":%s,\"enforced\":%s}\n",
           gdk_frame_clock_get_frame_time (clock), w, h, cw, ch,
           (state & GDK_TOPLEVEL_STATE_MAXIMIZED) ? "true" : "false",
           (state & GDK_TOPLEVEL_STATE_FULLSCREEN) ? "true" : "false",
           (state & GDK_TOPLEVEL_STATE_TILED) ? "true" : "false",
           enforced ? "true" : "false");
  fflush (log_file);
  return G_SOURCE_CONTINUE;
}

static void
activate (GtkApplication *app, gpointer data G_GNUC_UNUSED)
{
  const char *size = g_object_get_data (G_OBJECT (app), "size");
  GtkWidget *window = gtk_application_window_new (app);
  GtkWidget *header = gtk_header_bar_new ();
  GtkWidget *content = gtk_drawing_area_new ();
  int w = 800, h = 520;

  if (size)
    sscanf (size, "%dx%d", &w, &h);
  gtk_window_set_title (GTK_WINDOW (window), name);
  gtk_window_set_titlebar (GTK_WINDOW (window), header);
  gtk_drawing_area_set_draw_func (GTK_DRAWING_AREA (content), draw_content,
                                  NULL, NULL);
  gtk_widget_set_hexpand (content, TRUE);
  gtk_widget_set_vexpand (content, TRUE);
  gtk_window_set_child (GTK_WINDOW (window), content);
  gtk_window_set_default_size (GTK_WINDOW (window), w, h);

  if (ratio_w > 0)
    {
      if (!whole_window)
        luma_window_set_aspect_content (GTK_WINDOW (window), content);
      enforced = luma_window_set_aspect_ratio (GTK_WINDOW (window),
                                               ratio_w, ratio_h);
    }
  gtk_widget_add_tick_callback (window, tick, content, NULL);
  gtk_window_present (GTK_WINDOW (window));
  fprintf (log_file, "{\"ev\":\"ready\",\"enforced\":%s}\n",
           enforced ? "true" : "false");
  fflush (log_file);
}

int
main (int argc, char **argv)
{
  const char *log_path = NULL;
  const char *ratio = NULL;
  const char *size = NULL;
  g_autoptr (GtkApplication) app = NULL;
  g_autofree char *app_id = NULL;
  GOptionEntry entries[] = {
    { "name", 0, 0, G_OPTION_ARG_STRING, &name, NULL, NULL },
    { "log", 0, 0, G_OPTION_ARG_FILENAME, &log_path, NULL, NULL },
    { "ratio", 0, 0, G_OPTION_ARG_STRING, &ratio, NULL, NULL },
    { "size", 0, 0, G_OPTION_ARG_STRING, &size, NULL, NULL },
    { "whole-window", 0, 0, G_OPTION_ARG_NONE, &whole_window, NULL, NULL },
    { NULL }
  };
  g_autoptr (GOptionContext) options = g_option_context_new (NULL);

  g_option_context_add_main_entries (options, entries, NULL);
  if (!g_option_context_parse (options, &argc, &argv, NULL) || !log_path)
    return 2;
  if (ratio && sscanf (ratio, "%d:%d", &ratio_w, &ratio_h) != 2)
    return 2;
  log_file = fopen (log_path, "a");
  if (!log_file)
    return 2;

  app_id = g_strdup_printf ("org.projectluma.WindowShapeTest.%s", name);
  app = gtk_application_new (app_id, G_APPLICATION_NON_UNIQUE);
  g_object_set_data_full (G_OBJECT (app), "size", g_strdup (size), g_free);
  g_signal_connect (app, "activate", G_CALLBACK (activate), NULL);
  return g_application_run (G_APPLICATION (app), 1, argv);
}
