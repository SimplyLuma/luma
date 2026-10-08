/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-application-icon.h"
#include "luma-icon-tokens.h"

struct _LumaApplicationIcon { GObject parent_instance; GdkPaintable *artwork; };
static void paintable_init(GdkPaintableInterface *iface);
G_DEFINE_FINAL_TYPE_WITH_CODE(LumaApplicationIcon, luma_application_icon, G_TYPE_OBJECT,
    G_IMPLEMENT_INTERFACE(GDK_TYPE_PAINTABLE, paintable_init))

static void snapshot(GdkPaintable *paintable, GdkSnapshot *snapshot,
                     double width, double height) {
  LumaApplicationIcon *self = LUMA_APPLICATION_ICON(paintable);
  GtkSnapshot *s = GTK_SNAPSHOT(snapshot);
  double side = MIN(width, height);
  if (side <= 0 || self->artwork == NULL) return;
  graphene_rect_t bounds = GRAPHENE_RECT_INIT((width-side)/2, (height-side)/2, side, side);
  GskRoundedRect mask;
  /* Same quarter-width corner as the owned Shell dock mask; scales with art. */
  gsk_rounded_rect_init_from_rect(&mask, &bounds, side * LUMA_ICON_APPLICATION_CORNER_RATIO);
  gtk_snapshot_push_rounded_clip(s, &mask);
  graphene_point_t top = GRAPHENE_POINT_INIT(width/2, (height-side)/2);
  graphene_point_t bottom = GRAPHENE_POINT_INIT(width/2, (height+side)/2);
  GskColorStop stops[] = {{0, {1,1,1,LUMA_ICON_APPLICATION_TILE_TOP_ALPHA}}, {1, {1,1,1,LUMA_ICON_APPLICATION_TILE_BOTTOM_ALPHA}}};
  gtk_snapshot_append_linear_gradient(s, &bounds, &top, &bottom, stops, 2);
  double ratio = gdk_paintable_get_intrinsic_aspect_ratio(self->artwork);
  double art_width = side, art_height = side;
  if (ratio > 1) art_height /= ratio;
  else if (ratio > 0) art_width *= ratio;
  graphene_point_t offset = GRAPHENE_POINT_INIT((width-art_width)/2, (height-art_height)/2);
  gtk_snapshot_save(s);
  gtk_snapshot_translate(s, &offset);
  gdk_paintable_snapshot(self->artwork, snapshot, art_width, art_height);
  gtk_snapshot_restore(s);
  float borders[] = {1,1,1,1};
  GdkRGBA colors[] = {{1,1,1,LUMA_ICON_APPLICATION_OUTLINE_ALPHA},{1,1,1,LUMA_ICON_APPLICATION_OUTLINE_ALPHA},{1,1,1,LUMA_ICON_APPLICATION_OUTLINE_ALPHA},{1,1,1,LUMA_ICON_APPLICATION_OUTLINE_ALPHA}};
  gtk_snapshot_append_border(s, &mask, borders, colors);
  gtk_snapshot_pop(s);
}
static int intrinsic_size(GdkPaintable *p) {
  LumaApplicationIcon *self = LUMA_APPLICATION_ICON(p);
  return MAX(gdk_paintable_get_intrinsic_width(self->artwork),
             gdk_paintable_get_intrinsic_height(self->artwork));
}
static double aspect_ratio(GdkPaintable *p) { (void)p; return 1; }
static GdkPaintableFlags flags(GdkPaintable *p) {
  return gdk_paintable_get_flags(LUMA_APPLICATION_ICON(p)->artwork);
}
static void paintable_init(GdkPaintableInterface *iface) {
  iface->snapshot = snapshot;
  iface->get_intrinsic_width = intrinsic_size;
  iface->get_intrinsic_height = intrinsic_size;
  iface->get_intrinsic_aspect_ratio = aspect_ratio;
  iface->get_flags = flags;
}
static void dispose(GObject *object) {
  g_clear_object(&LUMA_APPLICATION_ICON(object)->artwork);
  G_OBJECT_CLASS(luma_application_icon_parent_class)->dispose(object);
}
static void luma_application_icon_class_init(LumaApplicationIconClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = dispose;
}
static void luma_application_icon_init(LumaApplicationIcon *self) { (void)self; }
GdkPaintable *luma_application_icon_new(GdkPaintable *artwork) {
  g_return_val_if_fail(GDK_IS_PAINTABLE(artwork), NULL);
  if (LUMA_IS_APPLICATION_ICON(artwork)) return g_object_ref(artwork);
  LumaApplicationIcon *self = g_object_new(LUMA_TYPE_APPLICATION_ICON, NULL);
  self->artwork = g_object_ref(artwork);
  g_signal_connect_object(artwork, "invalidate-contents", G_CALLBACK(gdk_paintable_invalidate_contents), self, G_CONNECT_SWAPPED);
  g_signal_connect_object(artwork, "invalidate-size", G_CALLBACK(gdk_paintable_invalidate_size), self, G_CONNECT_SWAPPED);
  return GDK_PAINTABLE(self);
}
