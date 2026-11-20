/* SPDX-License-Identifier: Apache-2.0
 *
 * Window screencast consumer for run.sh: records one window through
 * org.gnome.Mutter.ScreenCast and reads it with GStreamer's pipewiresrc, as a
 * capture app does. Prints one JSON line per frame (negotiated format, buffer
 * size, the stream's video crop) and, when STOPFILE appears, a final line with
 * alpha samples around the crop and writes PREFIX-buffer.png (the whole
 * buffer) and PREFIX-crop.png (the crop).
 *
 *   capture WINDOW_ID PREFIX STOPFILE
 */
#include <cairo.h>
#include <gio/gio.h>
#include <gst/app/gstappsink.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static guint node_id;

static void
on_stream_added (GDBusConnection *connection,
                 const char      *sender,
                 const char      *path,
                 const char      *interface,
                 const char      *signal,
                 GVariant        *parameters,
                 gpointer         user_data)
{
  g_variant_get (parameters, "(u)", &node_id);
}

static GVariant *
call (GDBusConnection *connection,
      const char      *path,
      const char      *interface,
      const char      *method,
      GVariant        *args)
{
  g_autoptr (GError) error = NULL;
  GVariant *ret;

  ret = g_dbus_connection_call_sync (connection, "org.gnome.Mutter.ScreenCast",
                                     path, interface, method, args, NULL,
                                     G_DBUS_CALL_FLAGS_NONE, 10000, NULL, &error);
  if (!ret)
    {
      printf ("{\"error\": \"%s: %s\"}\n", method, error->message);
      exit (2);
    }
  return ret;
}

static int
alpha_at (const guint8 *data, int stride, int width, int height, int x, int y)
{
  if (x < 0 || y < 0 || x >= width || y >= height)
    return -1;
  return data[y * stride + x * 4 + 3];
}

static void
write_png (const guint8 *data, int stride, int x, int y, int width, int height,
           gboolean has_alpha, const char *path)
{
  cairo_surface_t *surface;
  guint8 *dst;
  int dst_stride;

  surface = cairo_image_surface_create (has_alpha ? CAIRO_FORMAT_ARGB32
                                                  : CAIRO_FORMAT_RGB24,
                                        width, height);
  cairo_surface_flush (surface);
  dst = cairo_image_surface_get_data (surface);
  dst_stride = cairo_image_surface_get_stride (surface);
  /* BGRA/BGRx little-endian is cairo's ARGB32/RGB24 layout; Mutter's
   * BGRA is premultiplied, as cairo expects. */
  for (int row = 0; row < height; row++)
    memcpy (dst + row * dst_stride, data + (y + row) * stride + x * 4, width * 4);
  cairo_surface_mark_dirty (surface);
  cairo_surface_write_to_png (surface, path);
  cairo_surface_destroy (surface);
}

int
main (int argc, char **argv)
{
  g_autoptr (GDBusConnection) connection = NULL;
  g_autoptr (GVariant) session_ret = NULL, stream_ret = NULL;
  GVariantBuilder props;
  const char *session_path, *stream_path;
  const char *prefix, *stopfile;
  guint64 window_id;
  g_autofree char *description = NULL;
  GstElement *pipeline, *sink;
  GstSample *last = NULL;
  int frames = 0;
  gint64 deadline;

  if (argc != 4)
    {
      fprintf (stderr, "usage: capture WINDOW_ID PREFIX STOPFILE\n");
      return 2;
    }
  window_id = g_ascii_strtoull (argv[1], NULL, 10);
  prefix = argv[2];
  stopfile = argv[3];
  setvbuf (stdout, NULL, _IOLBF, 0);
  gst_init (&argc, &argv);

  connection = g_bus_get_sync (G_BUS_TYPE_SESSION, NULL, NULL);
  g_dbus_connection_signal_subscribe (connection, NULL,
                                      "org.gnome.Mutter.ScreenCast.Stream",
                                      "PipeWireStreamAdded", NULL, NULL,
                                      G_DBUS_SIGNAL_FLAGS_NONE,
                                      on_stream_added, NULL, NULL);

  session_ret = call (connection, "/org/gnome/Mutter/ScreenCast",
                      "org.gnome.Mutter.ScreenCast", "CreateSession",
                      g_variant_new_parsed ("(@a{sv} {},)"));
  g_variant_get (session_ret, "(&o)", &session_path);

  g_variant_builder_init (&props, G_VARIANT_TYPE ("a{sv}"));
  g_variant_builder_add (&props, "{sv}", "window-id", g_variant_new_uint64 (window_id));
  g_variant_builder_add (&props, "{sv}", "cursor-mode", g_variant_new_uint32 (0));
  stream_ret = call (connection, session_path, "org.gnome.Mutter.ScreenCast.Session",
                     "RecordWindow", g_variant_new ("(a{sv})", &props));
  g_variant_get (stream_ret, "(&o)", &stream_path);
  g_variant_unref (call (connection, session_path, "org.gnome.Mutter.ScreenCast.Session",
                         "Start", NULL));

  deadline = g_get_monotonic_time () + 10 * G_USEC_PER_SEC;
  while (!node_id && g_get_monotonic_time () < deadline)
    g_main_context_iteration (NULL, TRUE);
  if (!node_id)
    {
      printf ("{\"error\": \"no PipeWireStreamAdded\"}\n");
      return 2;
    }

  /* Plain video/x-raw: the consumer states no format preference, so the
   * format is the first one Mutter offers that pipewiresrc can take. */
  description = g_strdup_printf ("pipewiresrc path=%u always-copy=true ! "
                                 "appsink name=sink caps=video/x-raw sync=false "
                                 "max-buffers=4 drop=true", node_id);
  pipeline = gst_parse_launch (description, NULL);
  sink = gst_bin_get_by_name (GST_BIN (pipeline), "sink");
  gst_element_set_state (pipeline, GST_STATE_PLAYING);

  while (!g_file_test (stopfile, G_FILE_TEST_EXISTS))
    {
      GstSample *sample;
      GstBuffer *buffer;
      GstVideoInfo info;
      GstVideoCropMeta *crop;

      sample = gst_app_sink_try_pull_sample (GST_APP_SINK (sink), 100 * GST_MSECOND);
      while (g_main_context_iteration (NULL, FALSE));
      if (!sample)
        continue;
      buffer = gst_sample_get_buffer (sample);
      gst_video_info_from_caps (&info, gst_sample_get_caps (sample));
      crop = gst_buffer_get_video_crop_meta (buffer);
      printf ("{\"frame\": %d, \"format\": \"%s\", \"width\": %d, \"height\": %d, "
              "\"crop\": [%d, %d, %d, %d]}\n",
              frames++, gst_video_format_to_string (GST_VIDEO_INFO_FORMAT (&info)),
              GST_VIDEO_INFO_WIDTH (&info), GST_VIDEO_INFO_HEIGHT (&info),
              crop ? (int) crop->x : -1, crop ? (int) crop->y : -1,
              crop ? (int) crop->width : -1, crop ? (int) crop->height : -1);
      if (last)
        gst_sample_unref (last);
      last = sample;
    }

  if (last)
    {
      GstBuffer *buffer = gst_sample_get_buffer (last);
      GstVideoCropMeta *crop = gst_buffer_get_video_crop_meta (buffer);
      GstVideoInfo info;
      GstMapInfo map;
      g_autofree char *buffer_png = g_strdup_printf ("%s-buffer.png", prefix);
      g_autofree char *crop_png = g_strdup_printf ("%s-crop.png", prefix);
      int w, h, stride, cx, cy, cw, ch, extent_w = 0, extent_h = 0;
      gboolean has_alpha;
      const guint8 *d;

      gst_video_info_from_caps (&info, gst_sample_get_caps (last));
      w = GST_VIDEO_INFO_WIDTH (&info);
      h = GST_VIDEO_INFO_HEIGHT (&info);
      stride = GST_VIDEO_INFO_PLANE_STRIDE (&info, 0);
      has_alpha = GST_VIDEO_INFO_HAS_ALPHA (&info);
      cx = crop ? (int) crop->x : 0;
      cy = crop ? (int) crop->y : 0;
      cw = crop ? (int) crop->width : w;
      ch = crop ? (int) crop->height : h;
      gst_buffer_map (buffer, &map, GST_MAP_READ);
      d = map.data;

      /* Extent of everything the window drew (any alpha) in the buffer. */
      for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
          if (d[y * stride + x * 4 + 3])
            {
              extent_w = MAX (extent_w, x + 1);
              extent_h = MAX (extent_h, y + 1);
            }

      printf ("{\"final\": true, \"format\": \"%s\", \"width\": %d, \"height\": %d, "
              "\"crop\": [%d, %d, %d, %d], \"drawn\": [%d, %d], "
              "\"corners\": [%d, %d, %d, %d], "
              "\"inside\": [%d, %d, %d, %d], \"outside\": [%d, %d, %d, %d]}\n",
              gst_video_format_to_string (GST_VIDEO_INFO_FORMAT (&info)), w, h,
              cx, cy, cw, ch, extent_w, extent_h,
              /* corner pixels of the crop: tl, tr, bl, br */
              alpha_at (d, stride, w, h, cx, cy),
              alpha_at (d, stride, w, h, cx + cw - 1, cy),
              alpha_at (d, stride, w, h, cx, cy + ch - 1),
              alpha_at (d, stride, w, h, cx + cw - 1, cy + ch - 1),
              /* one pixel inside each edge, mid-edge: left, right, top, bottom */
              alpha_at (d, stride, w, h, cx + 1, cy + ch / 2),
              alpha_at (d, stride, w, h, cx + cw - 2, cy + ch / 2),
              alpha_at (d, stride, w, h, cx + cw / 2, cy + 1),
              alpha_at (d, stride, w, h, cx + cw / 2, cy + ch - 2),
              /* two pixels outside each edge, mid-edge (-1: outside the buffer) */
              alpha_at (d, stride, w, h, cx - 2, cy + ch / 2),
              alpha_at (d, stride, w, h, cx + cw + 1, cy + ch / 2),
              alpha_at (d, stride, w, h, cx + cw / 2, cy - 2),
              alpha_at (d, stride, w, h, cx + cw / 2, cy + ch + 1));

      write_png (d, stride, 0, 0, MAX (MIN (MAX (extent_w, cx + cw) + 8, w), 1),
                 MAX (MIN (MAX (extent_h, cy + ch) + 8, h), 1), has_alpha, buffer_png);
      write_png (d, stride, cx, cy, cw, ch, has_alpha, crop_png);
      gst_buffer_unmap (buffer, &map);
      gst_sample_unref (last);
    }

  gst_element_set_state (pipeline, GST_STATE_NULL);
  g_dbus_connection_call_sync (connection, "org.gnome.Mutter.ScreenCast",
                               session_path, "org.gnome.Mutter.ScreenCast.Session",
                               "Stop", NULL, NULL, G_DBUS_CALL_FLAGS_NONE, 5000,
                               NULL, NULL);
  return 0;
}
