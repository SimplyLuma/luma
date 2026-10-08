/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include <stdint.h>
static void mask_and_background(void) {
  for (guint transparent = 0; transparent < 2; transparent++) {
    guint8 pixels[16*16*4];
    for (guint i=0; i<sizeof pixels; i+=4) {
      pixels[i]=255; pixels[i+1]=0; pixels[i+2]=0; pixels[i+3]=transparent ? 0 : 255;
    }
    g_autoptr(GBytes) bytes = g_bytes_new(pixels, sizeof pixels);
    g_autoptr(GdkTexture) source = gdk_memory_texture_new(16,16,GDK_MEMORY_R8G8B8A8,bytes,16*4);
    g_autoptr(GdkPaintable) icon = luma_application_icon_new(GDK_PAINTABLE(source));
    g_assert_cmpfloat(gdk_paintable_get_intrinsic_aspect_ratio(icon), ==, 1);
    for (guint size=24; size<=96; size*=2) {
      GtkSnapshot *snapshot = gtk_snapshot_new();
      gdk_paintable_snapshot(icon,GDK_SNAPSHOT(snapshot),size,size);
      GskRenderNode *node = gtk_snapshot_free_to_node(snapshot);
      cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32,size,size);
      cairo_t *cr = cairo_create(surface); gsk_render_node_draw(node,cr); cairo_destroy(cr);
      cairo_surface_flush(surface);
      uint32_t *data = (uint32_t *)cairo_image_surface_get_data(surface);
      int stride = cairo_image_surface_get_stride(surface)/4;
      g_assert_cmpuint(data[0] >> 24, ==, 0);
      g_assert_cmpuint(data[(size-1)*stride+size-1] >> 24, ==, 0);
      guint center = data[(size/2)*stride+size/2] >> 24;
      g_assert_cmpuint(center, >, 0);
      if (!transparent) g_assert_cmpuint(center, ==, 255);
      else g_assert_cmpuint(data[3*stride+size/2] >> 24, >, data[(size-4)*stride+size/2] >> 24);
      cairo_surface_destroy(surface); gsk_render_node_unref(node);
    }
  }
}
int main(int argc,char **argv) {
  gtk_init(); g_test_init(&argc,&argv,NULL);
  g_test_add_func("/luma/application-icon/mask-and-gradient",mask_and_background);
  return g_test_run();
}
