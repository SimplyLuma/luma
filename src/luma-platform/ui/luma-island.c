/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-island.h"
#include "luma-ui-tokens-private.h"
#include "luma-tv-frame-private.h"
#include "luma-ui-kit.h"

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

/* TV posture: a page whose content runs on past an edge fades out there, never a hard cut (the
 * mockup's .tv-page mask). The fades are long and eased so they read as light falling off rather
 * than a band: 48 units at the top, 128 at the bottom, alpha leaving 1 gently and reaching 0 only
 * at the very edge. An edge fades only while there is more beyond it, growing in over its own
 * length as the page scrolls (so a page at its top has no top fade, and at its end no bottom one). */
static GtkAdjustment *tv_scroll(GtkWidget *widget) {
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    if (!gtk_widget_get_visible(c) || !gtk_widget_get_child_visible(c))
      continue;
    if (GTK_IS_SCROLLED_WINDOW(c))
      return gtk_scrolled_window_get_vadjustment(GTK_SCROLLED_WINDOW(c));
    GtkAdjustment *found = tv_scroll(c);
    if (found != NULL)
      return found;
  }
  return NULL;
}

static void tv_fade_follow(GtkWidget *widget, GtkAdjustment *adjustment) {
  /* redraw the fades as the page scrolls; one adjustment followed at a time */
  GtkAdjustment *followed = g_object_get_data(G_OBJECT(widget), "lumaui-tv-fade-adjustment");
  if (followed == adjustment)
    return;
  if (followed != NULL)
    g_signal_handlers_disconnect_by_func(followed, gtk_widget_queue_draw, widget);
  if (adjustment != NULL) {
    g_signal_connect_object(adjustment, "value-changed", G_CALLBACK(gtk_widget_queue_draw), widget, G_CONNECT_SWAPPED);
    g_signal_connect_object(adjustment, "changed", G_CALLBACK(gtk_widget_queue_draw), widget, G_CONNECT_SWAPPED);
  }
  g_object_set_data_full(G_OBJECT(widget), "lumaui-tv-fade-adjustment", adjustment != NULL ? g_object_ref(adjustment) : NULL,
                         g_object_unref);
}

#define TV_FADE_STOPS 8
/* one edge's stops, t from the edge (alpha 0 at full strength) inwards to full alpha at `length` (a share
 * of the height) */
static void tv_fade_edge(GskColorStop *stops, float edge, float length, float strength) {
  for (int i = 0; i < TV_FADE_STOPS; i++) {
    float t = (float)i / (TV_FADE_STOPS - 1);
    float eased = 1.f - (1.f - t) * (1.f - t); /* ease-out: steep only at the very edge, gentle into 1 */
    stops[i].offset = edge < 0.5f ? edge + t * length : edge - t * length;
    float a = 1.f - strength * (1.f - eased);
    stops[i].color = (GdkRGBA){0, 0, 0, a};
  }
}

static gboolean tv_faded(GtkWidget *widget, GtkSnapshot *snapshot) {
  GtkRoot *root = gtk_widget_get_root(widget);
  if (root == NULL || !gtk_widget_has_css_class(GTK_WIDGET(root), "lumaui-tv")) {
    tv_fade_follow(widget, NULL);
    return FALSE;
  }
  float width = gtk_widget_get_width(widget), height = gtk_widget_get_height(widget);
  double u = luma_ui_tv_unit(gtk_widget_get_width(GTK_WIDGET(root)), gtk_widget_get_height(GTK_WIDGET(root)));
  if (width <= 0 || height <= 0 || u <= 0)
    return FALSE;
  GtkAdjustment *adjustment = tv_scroll(widget);
  tv_fade_follow(widget, adjustment);
  double above = 1, below = 1; /* how much content lies past each edge, in units of the fade's length */
  if (adjustment != NULL) {
    double value = gtk_adjustment_get_value(adjustment);
    double rest = gtk_adjustment_get_upper(adjustment) - gtk_adjustment_get_page_size(adjustment) - value;
    above = CLAMP(value / (48 * u), 0, 1);
    below = CLAMP(rest / (128 * u), 0, 1);
  }
  /* Only the edge strips are masked: a mask over the whole page draws all of it offscreen on
   * every frame (a focus move in a long list paid for the full page). The rest is drawn as is;
   * the children's render nodes are cached, so drawing them again per strip costs no snapshot. */
  float top = (float)MIN(0.45 * height, 48 * u) * (above > 0), bottom = (float)MIN(0.45 * height, 128 * u) * (below > 0);
  GtkWidgetClass *parent = GTK_WIDGET_CLASS(luma_island_parent_class);
  gtk_snapshot_push_clip(snapshot, &GRAPHENE_RECT_INIT(0, top, width, height - top - bottom));
  parent->snapshot(widget, snapshot);
  gtk_snapshot_pop(snapshot);
  for (int edge = 0; edge < 2; edge++) {
    float strip = edge == 0 ? top : bottom;
    if (strip <= 0)
      continue;
    graphene_rect_t box = GRAPHENE_RECT_INIT(0, edge == 0 ? 0 : height - strip, width, strip);
    GskColorStop stops[TV_FADE_STOPS];
    tv_fade_edge(stops, 0.f, 1.f, (float)(edge == 0 ? above : below));
    /* along the strip from the page's edge inwards: top to bottom for the top, bottom to top below */
    graphene_point_t from = GRAPHENE_POINT_INIT(0, edge == 0 ? box.origin.y : box.origin.y + strip);
    graphene_point_t to = GRAPHENE_POINT_INIT(0, edge == 0 ? box.origin.y + strip : box.origin.y);
    gtk_snapshot_push_mask(snapshot, GSK_MASK_MODE_ALPHA);
    gtk_snapshot_append_linear_gradient(snapshot, &box, &from, &to, stops, TV_FADE_STOPS);
    gtk_snapshot_pop(snapshot);
    gtk_snapshot_push_clip(snapshot, &box);
    parent->snapshot(widget, snapshot);
    gtk_snapshot_pop(snapshot);
    gtk_snapshot_pop(snapshot);
  }
  return TRUE;
}

static void luma_island_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  if (tv_faded(widget, snapshot))
    return; /* on a TV the page is open to the room: no rim */
  GTK_WIDGET_CLASS(luma_island_parent_class)->snapshot(widget, snapshot);
  GtkRoot *root = gtk_widget_get_root(widget);
  if (root != NULL && gtk_widget_has_css_class(GTK_WIDGET(root), "lumaui-phone-device"))
    return; /* a physical phone has no desktop island rim */
  if (root != NULL && gtk_widget_has_css_class(GTK_WIDGET(root), "lumaui-tv"))
    return; /* on a TV the page is open to the room (luma-tv-frame.c) */
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
  GtkRoot *root = gtk_widget_get_root(widget);
  if (root != NULL && gtk_widget_has_css_class(GTK_WIDGET(root), "lumaui-tv"))
    luma_tv_frame_page_changed(GTK_WINDOW(root)); /* a page drawn or redrawn: the TV page takes its width */
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
