/* SPDX-License-Identifier: Apache-2.0 */
/* TV posture's eased edge fades (the mockups' .tv-page / .tv-fade mask), shared by a page
 * (LumaIsland, luma-island.c) and the floating island's list (LumaTvFade, below).
 *
 * Content that runs on past an edge fades out there, never a hard cut. The fades are long and eased so
 * they read as light falling off rather than a band: alpha leaves 1 gently and reaches 0 only at the very
 * edge. An edge fades only while there is more beyond it, growing in over its own length as the content
 * scrolls (so a list at its top has no top fade, and at its end no bottom one). */
#include "luma-tv-frame-private.h"
#include "luma-ui-kit.h"
#include "luma-ui-tokens-private.h"
#include <math.h>

static void fade_follow(GtkWidget *widget, GtkAdjustment *adjustment) {
  /* redraw the fades as the content scrolls; one adjustment followed at a time */
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

/* The unit the frame styles with (luma-tv-frame.c screen_unit): the window's screen, else the window.
 * The fades and the rows they keep clear are measured in the same unit as everything else the frame draws. */
double luma_tv_fade_unit(GtkWidget *widget) {
  GtkRoot *root = gtk_widget_get_root(widget);
  if (root == NULL || !gtk_widget_has_css_class(GTK_WIDGET(root), "lumaui-tv"))
    return 0;
  GdkSurface *surface = GTK_IS_NATIVE(root) ? gtk_native_get_surface(GTK_NATIVE(root)) : NULL;
  GdkMonitor *monitor = surface != NULL ? gdk_display_get_monitor_at_surface(gtk_widget_get_display(widget), surface) : NULL;
  if (monitor != NULL) {
    GdkRectangle geometry;
    gdk_monitor_get_geometry(monitor, &geometry);
    if (geometry.width > 0 && geometry.height > 0)
      return luma_ui_tv_unit(geometry.width, geometry.height);
  }
  return luma_ui_tv_unit(gtk_widget_get_width(GTK_WIDGET(root)), gtk_widget_get_height(GTK_WIDGET(root)));
}

#define FADE_STOPS LUMA_UI_TV_SCROLL_FADE_STOPS /* design-tokens.json tv.scroll */
gboolean luma_tv_fade_snapshot(GtkWidget *widget, GtkSnapshot *snapshot, GtkAdjustment *adjustment,
                               double top_units, double bottom_units, LumaTvFadeDraw draw) {
  double u = luma_tv_fade_unit(widget);
  float width = gtk_widget_get_width(widget), height = gtk_widget_get_height(widget);
  if (u <= 0 || width <= 0 || height <= 0) {
    fade_follow(widget, NULL);
    return FALSE;
  }
  fade_follow(widget, adjustment);
  double above = 1, below = 1; /* how much content lies past each edge, in units of the fade's length */
  if (adjustment != NULL) {
    double value = gtk_adjustment_get_value(adjustment) - gtk_adjustment_get_lower(adjustment);
    double rest = gtk_adjustment_get_upper(adjustment) - gtk_adjustment_get_page_size(adjustment) -
                  gtk_adjustment_get_value(adjustment);
    above = CLAMP(value / (top_units * u), 0, 1);
    below = CLAMP(rest / (bottom_units * u), 0, 1);
  }
  /* Only the edge strips are masked: a mask over the whole content draws all of it offscreen on every
   * frame (a focus move in a long list paid for all of it). The rest is drawn as is; the children's
   * render nodes are cached, so drawing them again per strip costs no snapshot. */
  float top = (float)MIN(0.45 * height, top_units * u) * (above > 0);
  float bottom = (float)MIN(0.45 * height, bottom_units * u) * (below > 0);
  gtk_snapshot_push_clip(snapshot, &GRAPHENE_RECT_INIT(0, top, width, height - top - bottom));
  draw(widget, snapshot);
  gtk_snapshot_pop(snapshot);
  for (int edge = 0; edge < 2; edge++) {
    float strip = edge == 0 ? top : bottom;
    if (strip <= 0)
      continue;
    float strength = (float)(edge == 0 ? above : below);
    graphene_rect_t box = GRAPHENE_RECT_INIT(0, edge == 0 ? 0 : height - strip, width, strip);
    GskColorStop stops[FADE_STOPS];
    for (int i = 0; i < FADE_STOPS; i++) {
      float t = (float)i / (FADE_STOPS - 1);
      float eased = 1.f - (1.f - t) * (1.f - t); /* ease-out: steep only at the very edge, gentle into 1 */
      stops[i].offset = t;
      stops[i].color = (GdkRGBA){0, 0, 0, 1.f - strength * (1.f - eased)};
    }
    /* along the strip from the edge inwards: top to bottom for the top, bottom to top below */
    graphene_point_t from = GRAPHENE_POINT_INIT(0, edge == 0 ? box.origin.y : box.origin.y + strip);
    graphene_point_t to = GRAPHENE_POINT_INIT(0, edge == 0 ? box.origin.y + strip : box.origin.y);
    gtk_snapshot_push_mask(snapshot, GSK_MASK_MODE_ALPHA);
    gtk_snapshot_append_linear_gradient(snapshot, &box, &from, &to, stops, FADE_STOPS);
    gtk_snapshot_pop(snapshot);
    gtk_snapshot_push_clip(snapshot, &box);
    draw(widget, snapshot);
    gtk_snapshot_pop(snapshot);
    gtk_snapshot_pop(snapshot);
  }
  return TRUE;
}

/* ── LumaTvFade: a scrolled list's fades ─────────────────────────────────────────────────────────
 *
 * The island's list scrolls inside the island's plate, so it is the list, not the island, that fades:
 * the plate stays whole. The frame puts this between the island's GtkScrolledWindow and what it scrolls
 * (luma_tv_fade_wrap), which works for any sidebar; it passes the scrolled window's adjustments through
 * (a GtkScrollable), so it is the size of what shows and its edges are the list's visible edges.
 *
 * The remote never rests in a fade: when the focus moves to a row inside one (and there is more beyond),
 * the list scrolls on by the fade's length, so the focused row is whole and the next one peeks. */
struct _LumaTvFade {
  GtkWidget parent_instance;
  GtkWidget *child;    /* a GtkScrollable: the list's GtkViewport, or the scrolled widget itself */
  double top, bottom;  /* the fades' lengths in TV units */
  gulong focus_id;
  GtkRoot *root;
};

enum { PROP_0, PROP_HADJUSTMENT, PROP_VADJUSTMENT, PROP_HSCROLL_POLICY, PROP_VSCROLL_POLICY };

static gboolean fade_border(GtkScrollable *scrollable, GtkBorder *border) {
  LumaTvFade *self = LUMA_TV_FADE(scrollable);
  return self->child != NULL && gtk_scrollable_get_border(GTK_SCROLLABLE(self->child), border);
}

static void fade_scrollable_init(GtkScrollableInterface *iface) {
  iface->get_border = fade_border;
}

G_DEFINE_FINAL_TYPE_WITH_CODE(LumaTvFade, luma_tv_fade, GTK_TYPE_WIDGET,
                              G_IMPLEMENT_INTERFACE(GTK_TYPE_SCROLLABLE, fade_scrollable_init))

static const char *const fade_props[] = {NULL, "hadjustment", "vadjustment", "hscroll-policy", "vscroll-policy"};

static void fade_set_property(GObject *object, guint id, const GValue *value, GParamSpec *pspec) {
  LumaTvFade *self = LUMA_TV_FADE(object);
  if (id < PROP_HADJUSTMENT || id > PROP_VSCROLL_POLICY) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
    return;
  }
  if (self->child != NULL)
    g_object_set_property(G_OBJECT(self->child), fade_props[id], value);
  if (id == PROP_VADJUSTMENT)
    gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void fade_get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  LumaTvFade *self = LUMA_TV_FADE(object);
  if (id < PROP_HADJUSTMENT || id > PROP_VSCROLL_POLICY) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
    return;
  }
  if (self->child != NULL)
    g_object_get_property(G_OBJECT(self->child), fade_props[id], value);
  else if (id == PROP_HSCROLL_POLICY || id == PROP_VSCROLL_POLICY)
    g_value_set_enum(value, GTK_SCROLL_MINIMUM);
}

static GtkAdjustment *fade_adjustment(LumaTvFade *self) {
  return self->child != NULL ? gtk_scrollable_get_vadjustment(GTK_SCROLLABLE(self->child)) : NULL;
}

static void fade_draw(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaTvFade *self = LUMA_TV_FADE(widget);
  if (self->child != NULL)
    gtk_widget_snapshot_child(widget, self->child, snapshot);
}

static void fade_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaTvFade *self = LUMA_TV_FADE(widget);
  if (!luma_tv_fade_snapshot(widget, snapshot, fade_adjustment(self), self->top, self->bottom, fade_draw))
    fade_draw(widget, snapshot);
}

/* the focused row whole, out of the fades: positions are the content's own (stable while it scrolls) */
static void fade_focus_changed(GtkRoot *root, GParamSpec *pspec G_GNUC_UNUSED, LumaTvFade *self) {
  GtkWidget *focus = gtk_root_get_focus(root);
  GtkAdjustment *adjustment = fade_adjustment(self);
  GtkWidget *content = GTK_IS_VIEWPORT(self->child) ? gtk_viewport_get_child(GTK_VIEWPORT(self->child)) : NULL;
  double u = luma_tv_fade_unit(GTK_WIDGET(self));
  if (focus == NULL || adjustment == NULL || content == NULL || u <= 0 || !gtk_widget_is_ancestor(focus, content))
    return;
  GtkWidget *row = focus;
  while (gtk_widget_get_parent(row) != content && gtk_widget_get_parent(row) != NULL)
    row = gtk_widget_get_parent(row); /* the list's own child: the whole row, not a widget inside it */
  graphene_rect_t box;
  if (!gtk_widget_compute_bounds(row, content, &box))
    return;
  double value = gtk_adjustment_get_value(adjustment), page = gtk_adjustment_get_page_size(adjustment);
  double lower = gtk_adjustment_get_lower(adjustment), upper = gtk_adjustment_get_upper(adjustment);
  double top = MIN(self->top * u, 0.45 * page), bottom = MIN(self->bottom * u, 0.45 * page);
  double target = value;
  if (box.origin.y - top < value)
    target = box.origin.y - top;
  else if (box.origin.y + box.size.height + bottom > value + page)
    target = box.origin.y + box.size.height + bottom - page;
  target = CLAMP(target, lower, MAX(lower, upper - page));
  if (fabs(target - value) >= 0.5)
    gtk_adjustment_set_value(adjustment, target);
}

static void fade_root(GtkWidget *widget) {
  LumaTvFade *self = LUMA_TV_FADE(widget);
  GTK_WIDGET_CLASS(luma_tv_fade_parent_class)->root(widget);
  self->root = gtk_widget_get_root(widget);
  self->focus_id = g_signal_connect_after(self->root, "notify::focus-widget", G_CALLBACK(fade_focus_changed), self);
}

static void fade_unroot(GtkWidget *widget) {
  LumaTvFade *self = LUMA_TV_FADE(widget);
  if (self->root != NULL && self->focus_id != 0)
    g_signal_handler_disconnect(self->root, self->focus_id);
  self->focus_id = 0;
  self->root = NULL;
  GTK_WIDGET_CLASS(luma_tv_fade_parent_class)->unroot(widget);
}

static void fade_dispose(GObject *object) {
  LumaTvFade *self = LUMA_TV_FADE(object);
  g_clear_pointer(&self->child, gtk_widget_unparent);
  G_OBJECT_CLASS(luma_tv_fade_parent_class)->dispose(object);
}

static void luma_tv_fade_class_init(LumaTvFadeClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->set_property = fade_set_property;
  object_class->get_property = fade_get_property;
  object_class->dispose = fade_dispose;
  widget_class->snapshot = fade_snapshot;
  widget_class->root = fade_root;
  widget_class->unroot = fade_unroot;
  g_object_class_override_property(object_class, PROP_HADJUSTMENT, "hadjustment");
  g_object_class_override_property(object_class, PROP_VADJUSTMENT, "vadjustment");
  g_object_class_override_property(object_class, PROP_HSCROLL_POLICY, "hscroll-policy");
  g_object_class_override_property(object_class, PROP_VSCROLL_POLICY, "vscroll-policy");
  gtk_widget_class_set_layout_manager_type(widget_class, GTK_TYPE_BIN_LAYOUT);
}

static void luma_tv_fade_init(LumaTvFade *self G_GNUC_UNUSED) {}

/* Put @scroll's list behind fades (@tv) or take them away again. Idempotent: the frame calls it each time the
 * island is laid out. The scrolled window's viewport and the list in it are moved, never made again, so an app
 * that holds either keeps it. A viewport the scrolled window added itself is emptied as the scrolled window lets
 * go of it, so the list is held meanwhile and put back once the viewport has moved. */
void luma_tv_fade_wrap(GtkScrolledWindow *scroll, gboolean tv, double top_units, double bottom_units) {
  GtkWidget *child = gtk_scrolled_window_get_child(scroll);
  if (child == NULL || tv == LUMA_IS_TV_FADE(child))
    return;
  LumaTvFade *self = tv ? g_object_new(LUMA_TYPE_TV_FADE, NULL) : g_object_ref(LUMA_TV_FADE(child));
  GtkWidget *moving = tv ? child : self->child; /* the scrollable: the viewport, or a list that scrolls itself */
  if (moving == NULL) {
    g_object_unref(self);
    return;
  }
  g_object_ref(moving);
  GtkWidget *content = GTK_IS_VIEWPORT(moving) ? gtk_viewport_get_child(GTK_VIEWPORT(moving)) : NULL;
  if (content != NULL)
    g_object_ref(content);
  gtk_scrolled_window_set_child(scroll, NULL);
  if (tv) {
    self->top = top_units;
    self->bottom = bottom_units;
    if (!GTK_IS_SCROLLABLE(moving)) { /* a list whose viewport the scrolled window did not hand back */
      g_clear_object(&content);
      content = moving;
      moving = g_object_ref(gtk_viewport_new(NULL, NULL));
    }
    self->child = moving;
    gtk_widget_set_parent(moving, GTK_WIDGET(self));
    gtk_scrolled_window_set_child(scroll, GTK_WIDGET(self));
  } else {
    gtk_widget_unparent(moving);
    self->child = NULL;
    gtk_scrolled_window_set_child(scroll, moving);
  }
  if (content != NULL) {
    if (gtk_viewport_get_child(GTK_VIEWPORT(moving)) != content)
      gtk_viewport_set_child(GTK_VIEWPORT(moving), content);
    g_object_unref(content);
  }
  g_object_unref(moving);
  if (!tv)
    g_object_unref(self);
}
