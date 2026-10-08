/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-island.h"
#include "luma-ui-tokens-private.h"

/* GTK 4.22 has no non-deprecated lookup for named CSS colours. Keep this
 * compatibility call local until the style API supplies one. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"

struct _LumaIsland { GtkBox parent_instance; };
G_DEFINE_FINAL_TYPE(LumaIsland, luma_island, GTK_TYPE_BOX)

static void inset(GtkSnapshot *snapshot, GtkStyleContext *style, const GskRoundedRect *outline,
                  const char *token, float dx, float dy, float spread, float blur) {
  GdkRGBA colour;
  if (gtk_style_context_lookup_color(style, token, &colour) && colour.alpha > 0)
    gtk_snapshot_append_inset_shadow(snapshot, outline, &colour, dx, dy, spread, blur);
}

static void luma_island_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  GTK_WIDGET_CLASS(luma_island_parent_class)->snapshot(widget, snapshot);
  GtkRoot *root = gtk_widget_get_root(widget);
  if (root != NULL && gtk_widget_has_css_class(GTK_WIDGET(root), "lumaui-phone-device"))
    return; /* a physical phone has no desktop island rim */
  int width = gtk_widget_get_width(widget), height = gtk_widget_get_height(widget);
  if (width <= 0 || height <= 0 || gtk_widget_has_css_class(widget, "compact") ||
      gtk_widget_has_css_class(widget, "drawer") || gtk_widget_has_css_class(widget, "embedded"))
    return;
  GskRoundedRect outline;
  gsk_rounded_rect_init_from_rect(&outline, &GRAPHENE_RECT_INIT(0, 0, width, height),
                                   LUMA_UI_WINDOW_ISLAND_RADIUS);
  GtkStyleContext *style = gtk_widget_get_style_context(widget);
  inset(snapshot, style, &outline, "luma_island_edge", 0, 0, 1, 0);
  inset(snapshot, style, &outline, "luma_island_rim", 0, 1, 0, 0);
  inset(snapshot, style, &outline, "luma_island_shade", 0, 3, -2, 8);
}

/* The island's layout: GtkBoxLayout, except that it asks for little width and, given less than its
 * content needs, lays the content out at that need and lets the island clip it. A tiled window
 * narrower than an app's content then keeps its frame, title row and controls (Nick, 26 Sep).
 * GtkBoxLayout's structs are private, so the subclass is registered from its type's sizes. */
#define LUMA_ISLAND_FLOOR 160
static GtkLayoutManagerClass *box_layout_class;

static void island_layout_measure(GtkLayoutManager *manager, GtkWidget *widget, GtkOrientation orientation,
                                  int for_size, int *minimum, int *natural, int *min_baseline, int *nat_baseline) {
  /* Given less width than the content needs, the content is laid out at its need (allocate, below), so its
   * height is asked for at that need too: GtkBox would hand a child less than its minimum ("Trying to
   * measure … for width of 160, but it needs at least 320"). */
  if (orientation == GTK_ORIENTATION_VERTICAL && for_size >= 0) {
    int need = 0, natural_width = 0, mb = -1, nb = -1;
    box_layout_class->measure(manager, widget, GTK_ORIENTATION_HORIZONTAL, -1, &need, &natural_width, &mb, &nb);
    for_size = MAX(for_size, need);
  }
  box_layout_class->measure(manager, widget, orientation, for_size, minimum, natural, min_baseline, nat_baseline);
  if (orientation == GTK_ORIENTATION_HORIZONTAL && *minimum > LUMA_ISLAND_FLOOR)
    *minimum = LUMA_ISLAND_FLOOR;
}

static void island_layout_allocate(GtkLayoutManager *manager, GtkWidget *widget, int width, int height, int baseline) {
  int need = 0, natural = 0, mb = -1, nb = -1;
  box_layout_class->measure(manager, widget, GTK_ORIENTATION_HORIZONTAL, -1, &need, &natural, &mb, &nb);
  g_object_set_data(G_OBJECT(widget), "lumaui-overflow", GINT_TO_POINTER(MAX(0, need - width)));
  box_layout_class->allocate(manager, widget, MAX(width, need), height, baseline);
}

static void island_layout_class_init(gpointer klass, gpointer data G_GNUC_UNUSED) {
  box_layout_class = GTK_LAYOUT_MANAGER_CLASS(g_type_class_peek_parent(klass));
  GTK_LAYOUT_MANAGER_CLASS(klass)->measure = island_layout_measure;
  GTK_LAYOUT_MANAGER_CLASS(klass)->allocate = island_layout_allocate;
}

static GType island_layout_get_type(void) {
  /* GTK widgets live on the main thread, so a plain once is enough. */
  static GType type = G_TYPE_INVALID;
  static gboolean tried = FALSE;
  if (!tried) {
    tried = TRUE;
    if (!G_TYPE_IS_FINAL(GTK_TYPE_BOX_LAYOUT)) {
      GTypeQuery query;
      g_type_query(GTK_TYPE_BOX_LAYOUT, &query);
      type = g_type_register_static_simple(GTK_TYPE_BOX_LAYOUT, "LumaIslandLayout", query.class_size,
                                           island_layout_class_init, query.instance_size, NULL, 0);
    }
  }
  return type;
}

static void luma_island_class_init(LumaIslandClass *klass) {
  GTK_WIDGET_CLASS(klass)->snapshot = luma_island_snapshot;
}

static void luma_island_init(LumaIsland *self) {
  GType layout = island_layout_get_type();
  if (layout != G_TYPE_INVALID)
    gtk_widget_set_layout_manager(GTK_WIDGET(self), g_object_new(layout, NULL));
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-island");
  gtk_widget_set_overflow(GTK_WIDGET(self), GTK_OVERFLOW_HIDDEN);
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_vexpand(GTK_WIDGET(self), TRUE);
}

GtkWidget *luma_island_new(GtkOrientation orientation) {
  g_return_val_if_fail(orientation == GTK_ORIENTATION_HORIZONTAL || orientation == GTK_ORIENTATION_VERTICAL, NULL);
  GtkWidget *island = g_object_new(LUMA_TYPE_ISLAND, NULL);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(island), orientation);
  return island;
}
#pragma GCC diagnostic pop
