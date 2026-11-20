/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI creative family: LumaArrangeView (boxes on a canvas, by hand). */
#include "luma-arrange-view.h"
#include "luma-creative-private.h"

#include <math.h>

typedef struct {
  char *id;
  char *label;
  double x, y, w, h;
  gboolean locked;
  gboolean visible;
  gboolean keeps_aspect;
} Item;

/* What a drag does: move the box, or pull one of its edges or corners. */
typedef enum {
  GRAB_NONE = 0,
  GRAB_MOVE = 1,
  GRAB_LEFT = 1 << 1,
  GRAB_RIGHT = 1 << 2,
  GRAB_TOP = 1 << 3,
  GRAB_BOTTOM = 1 << 4,
} Grab;

enum { SIG_SELECTION_CHANGED, SIG_ITEM_CHANGED, SIG_ITEM_COMMITTED, SIG_ITEM_ACTIVATED, N_SIGNALS };
static guint signals[N_SIGNALS];

#define N_GUIDES 4

struct _LumaArrangeView {
  GtkWidget parent_instance;
  GtkWidget *content;
  GtkWidget *nodes[6];
  GPtrArray *items; /* bottom to top */
  char *selected;
  char *hovered;
  double canvas_w, canvas_h;
  double fit_left, fit_top, fit_right, fit_bottom;
  gboolean snapping;
  /* The current drag. */
  Grab grab;
  Item start;
  gboolean moved;
  /* Guides shown during a drag, in canvas units (NAN: none). */
  double guide_x[2], guide_y[2];
};

enum { NODE_FRAME, NODE_HOVER, NODE_HANDLE, NODE_KNOB, NODE_GUIDE, NODE_LOCKED };
static const char *node_names[] = {"lumaui-arrange-frame", "lumaui-arrange-hover", "lumaui-arrange-handle",
                                   "lumaui-arrange-knob",  "lumaui-arrange-guide", "lumaui-arrange-locked"};

G_DEFINE_FINAL_TYPE(LumaArrangeView, luma_arrange_view, GTK_TYPE_WIDGET)

static void item_free(gpointer data) {
  Item *item = data;
  g_free(item->id);
  g_free(item->label);
  g_free(item);
}

static Item *find_item(LumaArrangeView *self, const char *id) {
  if (id == NULL)
    return NULL;
  for (guint i = 0; i < self->items->len; i++) {
    Item *item = g_ptr_array_index(self->items, i);
    if (g_str_equal(item->id, id))
      return item;
  }
  return NULL;
}

double luma_arrange_snap(double value, const double *lines, guint n_lines, double threshold, double *snapped) {
  double best = threshold, offset = 0;
  gboolean found = FALSE;
  for (guint i = 0; i < n_lines; i++) {
    double d = lines[i] - value;
    if (fabs(d) <= best) {
      best = fabs(d);
      offset = d;
      found = TRUE;
      if (snapped != NULL)
        *snapped = lines[i];
    }
  }
  if (!found && snapped != NULL)
    *snapped = NAN;
  return found ? offset : 0;
}

/* The fitted canvas in the view: scale and origin. */
static double fit(LumaArrangeView *self, double *ox, double *oy) {
  double width = gtk_widget_get_width(GTK_WIDGET(self)) - self->fit_left - self->fit_right;
  double height = gtk_widget_get_height(GTK_WIDGET(self)) - self->fit_top - self->fit_bottom;
  double scale = MIN(width / self->canvas_w, height / self->canvas_h);
  if (!(scale > 0))
    scale = MIN(gtk_widget_get_width(GTK_WIDGET(self)) / self->canvas_w,
                gtk_widget_get_height(GTK_WIDGET(self)) / self->canvas_h);
  if (!(scale > 0))
    scale = 1;
  if (ox != NULL)
    *ox = floor(self->fit_left + (MAX(width, 0) - self->canvas_w * scale) / 2);
  if (oy != NULL)
    *oy = floor(self->fit_top + (MAX(height, 0) - self->canvas_h * scale) / 2);
  return scale;
}

void luma_arrange_view_set_fit_margins(LumaArrangeView *self, double left, double top, double right, double bottom) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  self->fit_left = MAX(0, left);
  self->fit_top = MAX(0, top);
  self->fit_right = MAX(0, right);
  self->fit_bottom = MAX(0, bottom);
  gtk_widget_queue_allocate(GTK_WIDGET(self));
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_canvas_to_view(LumaArrangeView *self, double x, double y, double *view_x, double *view_y) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  double ox, oy, scale = fit(self, &ox, &oy);
  *view_x = ox + x * scale;
  *view_y = oy + y * scale;
}

static gboolean touch_input(LumaArrangeView *self) {
  GdkSeat *seat = gdk_display_get_default_seat(gtk_widget_get_display(GTK_WIDGET(self)));
  return luma_ui_is_phone(GTK_WIDGET(self)) ||
         (seat != NULL && (gdk_seat_get_capabilities(seat) & GDK_SEAT_CAPABILITY_TOUCH) &&
          !(gdk_seat_get_capabilities(seat) & GDK_SEAT_CAPABILITY_POINTER));
}

static void announce(LumaArrangeView *self) {
  Item *item = find_item(self, self->selected);
  if (item == NULL) {
    gtk_accessible_reset_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION);
    return;
  }
  g_autofree char *words = g_strdup_printf("%s selected%s, %.0f by %.0f at %.0f, %.0f", item->label,
                                           item->locked ? ", locked" : "", item->w, item->h, item->x, item->y);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, words, -1);
}

static void select_item(LumaArrangeView *self, const char *id, gboolean emit) {
  if (g_strcmp0(self->selected, id) == 0)
    return;
  g_free(self->selected);
  self->selected = g_strdup(id);
  announce(self);
  gtk_widget_queue_draw(GTK_WIDGET(self));
  if (emit)
    g_signal_emit(self, signals[SIG_SELECTION_CHANGED], 0, self->selected);
}

/* ── Hit testing ── */

static Item *item_at(LumaArrangeView *self, double cx, double cy) {
  for (guint i = self->items->len; i > 0; i--) {
    Item *item = g_ptr_array_index(self->items, i - 1);
    if (item->visible && cx >= item->x && cx <= item->x + item->w && cy >= item->y && cy <= item->y + item->h)
      return item;
  }
  return NULL;
}

static double handle_hit(LumaArrangeView *self) {
  return touch_input(self) ? LUMA_UI_CREATIVE_ARRANGE_HANDLE_HIT_TOUCH : LUMA_UI_CREATIVE_ARRANGE_HANDLE_HIT;
}

/* Which handle of the selected box is under a view point. */
static Grab handle_at(LumaArrangeView *self, double vx, double vy) {
  Item *item = find_item(self, self->selected);
  if (item == NULL || item->locked || !item->visible)
    return GRAB_NONE;
  double ox, oy, scale = fit(self, &ox, &oy);
  double x0 = ox + item->x * scale, y0 = oy + item->y * scale;
  double x1 = x0 + item->w * scale, y1 = y0 + item->h * scale;
  double r = handle_hit(self) / 2;
  gboolean small = (x1 - x0) < 3 * r || (y1 - y0) < 3 * r;
  struct {
    double x, y;
    Grab grab;
  } spots[] = {
    {x0, y0, GRAB_LEFT | GRAB_TOP},           {x1, y0, GRAB_RIGHT | GRAB_TOP},
    {x0, y1, GRAB_LEFT | GRAB_BOTTOM},        {x1, y1, GRAB_RIGHT | GRAB_BOTTOM},
    {(x0 + x1) / 2, y0, GRAB_TOP},            {(x0 + x1) / 2, y1, GRAB_BOTTOM},
    {x0, (y0 + y1) / 2, GRAB_LEFT},           {x1, (y0 + y1) / 2, GRAB_RIGHT},
  };
  for (guint i = 0; i < G_N_ELEMENTS(spots); i++) {
    if (small && i >= 4)
      break;
    if (fabs(vx - spots[i].x) <= r && fabs(vy - spots[i].y) <= r)
      return spots[i].grab;
  }
  return GRAB_NONE;
}

static const char *cursor_for(Grab grab) {
  switch ((int)grab) {
  case GRAB_LEFT | GRAB_TOP:
  case GRAB_RIGHT | GRAB_BOTTOM: return "nwse-resize";
  case GRAB_RIGHT | GRAB_TOP:
  case GRAB_LEFT | GRAB_BOTTOM: return "nesw-resize";
  case GRAB_TOP:
  case GRAB_BOTTOM: return "ns-resize";
  case GRAB_LEFT:
  case GRAB_RIGHT: return "ew-resize";
  case GRAB_MOVE: return "move";
  default: return NULL;
  }
}

/* ── Snapping ── */

static guint snap_lines(LumaArrangeView *self, Item *moving, gboolean vertical, double *lines, guint room) {
  double size = vertical ? self->canvas_w : self->canvas_h;
  guint n = 0;
  double fixed[] = {0, size / 3, size / 2, size * 2 / 3, size};
  for (guint i = 0; i < G_N_ELEMENTS(fixed) && n < room; i++)
    lines[n++] = fixed[i];
  for (guint i = 0; i < self->items->len && n + 3 <= room; i++) {
    Item *other = g_ptr_array_index(self->items, i);
    if (other == moving || !other->visible || g_str_equal(other->id, moving->id))
      continue;
    double a = vertical ? other->x : other->y, l = vertical ? other->w : other->h;
    lines[n++] = a;
    lines[n++] = a + l / 2;
    lines[n++] = a + l;
  }
  return n;
}

/* Snap one axis of a move: the box's start, centre and end, the nearest wins. */
static double snap_move(LumaArrangeView *self, Item *item, gboolean vertical, double threshold, double *guide) {
  double lines[5 + 3 * 64];
  guint n = snap_lines(self, item, vertical, lines, G_N_ELEMENTS(lines));
  double a = vertical ? item->x : item->y, l = vertical ? item->w : item->h;
  double best = INFINITY, offset = 0;
  *guide = NAN;
  double points[] = {a, a + l / 2, a + l};
  for (guint i = 0; i < 3; i++) {
    double line;
    double d = luma_arrange_snap(points[i], lines, n, threshold, &line);
    if (!isnan(line) && fabs(d) < best) {
      best = fabs(d);
      offset = d;
      *guide = line;
    }
  }
  return offset;
}

static double snap_edge(LumaArrangeView *self, Item *item, gboolean vertical, double edge, double threshold,
                        double *guide) {
  double lines[5 + 3 * 64];
  guint n = snap_lines(self, item, vertical, lines, G_N_ELEMENTS(lines));
  return luma_arrange_snap(edge, lines, n, threshold, guide);
}

/* ── Drag ── */

static void clear_guides(LumaArrangeView *self) {
  self->guide_x[0] = self->guide_x[1] = self->guide_y[0] = self->guide_y[1] = NAN;
}

static void emit_rect(LumaArrangeView *self, guint signal, Item *item) {
  g_signal_emit(self, signals[signal], 0, item->id, item->x, item->y, item->w, item->h);
}

static void drag_begin(GtkGestureDrag *gesture, double x, double y, gpointer user_data) {
  LumaArrangeView *self = user_data;
  double ox, oy, scale = fit(self, &ox, &oy);
  double cx = (x - ox) / scale, cy = (y - oy) / scale;
  gtk_widget_grab_focus(GTK_WIDGET(self));
  self->moved = FALSE;
  clear_guides(self);
  Grab grab = handle_at(self, x, y);
  Item *item = NULL;
  if (grab != GRAB_NONE) {
    item = find_item(self, self->selected);
  } else {
    item = item_at(self, cx, cy);
    select_item(self, item != NULL ? item->id : NULL, TRUE);
    grab = item != NULL && !item->locked ? GRAB_MOVE : GRAB_NONE;
  }
  self->grab = grab;
  if (item != NULL) {
    self->start = *item;
    gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
  } else {
    gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_DENIED);
  }
}

static void drag_update(GtkGestureDrag *gesture, double dx, double dy, gpointer user_data) {
  LumaArrangeView *self = user_data;
  Item *item = find_item(self, self->selected);
  if (item == NULL || self->grab == GRAB_NONE)
    return;
  double scale = fit(self, NULL, NULL);
  double cdx = dx / scale, cdy = dy / scale;
  GdkModifierType mods = gtk_event_controller_get_current_event_state(GTK_EVENT_CONTROLLER(gesture));
  gboolean snap = self->snapping && !(mods & GDK_ALT_MASK);
  double threshold = LUMA_UI_CREATIVE_ARRANGE_SNAP / scale;
  Item *start = &self->start;
  clear_guides(self);
  if (self->grab == GRAB_MOVE) {
    item->x = start->x + cdx;
    item->y = start->y + cdy;
    if (snap) {
      item->x += snap_move(self, item, TRUE, threshold, &self->guide_x[0]);
      item->y += snap_move(self, item, FALSE, threshold, &self->guide_y[0]);
    }
  } else {
    double x0 = start->x, y0 = start->y, x1 = start->x + start->w, y1 = start->y + start->h;
    if (self->grab & GRAB_LEFT)
      x0 += cdx;
    if (self->grab & GRAB_RIGHT)
      x1 += cdx;
    if (self->grab & GRAB_TOP)
      y0 += cdy;
    if (self->grab & GRAB_BOTTOM)
      y1 += cdy;
    if (snap) {
      if (self->grab & GRAB_LEFT)
        x0 += snap_edge(self, item, TRUE, x0, threshold, &self->guide_x[0]);
      if (self->grab & GRAB_RIGHT)
        x1 += snap_edge(self, item, TRUE, x1, threshold, &self->guide_x[1]);
      if (self->grab & GRAB_TOP)
        y0 += snap_edge(self, item, FALSE, y0, threshold, &self->guide_y[0]);
      if (self->grab & GRAB_BOTTOM)
        y1 += snap_edge(self, item, FALSE, y1, threshold, &self->guide_y[1]);
    }
    double min = 2 * LUMA_UI_CREATIVE_ARRANGE_HANDLE / scale;
    if (x1 - x0 < min) {
      if (self->grab & GRAB_LEFT)
        x0 = x1 - min;
      else
        x1 = x0 + min;
    }
    if (y1 - y0 < min) {
      if (self->grab & GRAB_TOP)
        y0 = y1 - min;
      else
        y1 = y0 + min;
    }
    gboolean shift = (mods & GDK_SHIFT_MASK) != 0;
    gboolean keep = start->keeps_aspect != shift;
    gboolean corner = (self->grab & (GRAB_LEFT | GRAB_RIGHT)) && (self->grab & (GRAB_TOP | GRAB_BOTTOM));
    if (keep && start->w > 0 && start->h > 0) {
      double aspect = start->w / start->h;
      double w = x1 - x0, h = y1 - y0;
      if (corner ? fabs(w / start->w) >= fabs(h / start->h) : (self->grab & (GRAB_LEFT | GRAB_RIGHT)) != 0) {
        double nh = w / aspect;
        if (self->grab & GRAB_TOP)
          y0 = y1 - nh;
        else if (self->grab & GRAB_BOTTOM)
          y1 = y0 + nh;
        else {
          double c = start->y + start->h / 2;
          y0 = c - nh / 2;
          y1 = c + nh / 2;
        }
        self->guide_y[0] = self->guide_y[1] = NAN;
      } else {
        double nw = h * aspect;
        if (self->grab & GRAB_LEFT)
          x0 = x1 - nw;
        else if (self->grab & GRAB_RIGHT)
          x1 = x0 + nw;
        else {
          double c = start->x + start->w / 2;
          x0 = c - nw / 2;
          x1 = c + nw / 2;
        }
        self->guide_x[0] = self->guide_x[1] = NAN;
      }
    }
    item->x = x0;
    item->y = y0;
    item->w = x1 - x0;
    item->h = y1 - y0;
  }
  if (item->x == start->x && item->y == start->y && item->w == start->w && item->h == start->h && !self->moved)
    return;
  self->moved = TRUE;
  emit_rect(self, SIG_ITEM_CHANGED, item);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void drag_end(GtkGestureDrag *gesture G_GNUC_UNUSED, double dx G_GNUC_UNUSED, double dy G_GNUC_UNUSED,
                     gpointer user_data) {
  LumaArrangeView *self = user_data;
  Item *item = find_item(self, self->selected);
  clear_guides(self);
  if (item != NULL && self->moved) {
    announce(self);
    emit_rect(self, SIG_ITEM_COMMITTED, item);
  }
  self->grab = GRAB_NONE;
  self->moved = FALSE;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void pressed(GtkGestureClick *click, int n_press, double x, double y, gpointer user_data) {
  LumaArrangeView *self = user_data;
  if (n_press != 2)
    return;
  double ox, oy, scale = fit(self, &ox, &oy);
  Item *item = item_at(self, (x - ox) / scale, (y - oy) / scale);
  if (item != NULL) {
    gtk_gesture_set_state(GTK_GESTURE(click), GTK_EVENT_SEQUENCE_CLAIMED);
    g_signal_emit(self, signals[SIG_ITEM_ACTIVATED], 0, item->id);
  }
}

static void motion(GtkEventControllerMotion *controller G_GNUC_UNUSED, double x, double y, gpointer user_data) {
  LumaArrangeView *self = user_data;
  if (self->grab != GRAB_NONE)
    return;
  double ox, oy, scale = fit(self, &ox, &oy);
  Grab grab = handle_at(self, x, y);
  Item *item = item_at(self, (x - ox) / scale, (y - oy) / scale);
  if (grab == GRAB_NONE && item != NULL && !item->locked && g_strcmp0(item->id, self->selected) == 0)
    grab = GRAB_MOVE;
  gtk_widget_set_cursor_from_name(GTK_WIDGET(self), cursor_for(grab));
  const char *id = item != NULL ? item->id : NULL;
  if (g_strcmp0(id, self->hovered) != 0) {
    g_free(self->hovered);
    self->hovered = g_strdup(id);
    gtk_widget_queue_draw(GTK_WIDGET(self));
  }
}

static void left(GtkEventControllerMotion *controller G_GNUC_UNUSED, gpointer user_data) {
  LumaArrangeView *self = user_data;
  g_clear_pointer(&self->hovered, g_free);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

/* ── Keyboard ── */

static gboolean cycle(LumaArrangeView *self, int direction) {
  int n = (int)self->items->len, at = -1;
  for (int i = 0; i < n; i++)
    if (g_strcmp0(((Item *)g_ptr_array_index(self->items, i))->id, self->selected) == 0)
      at = i;
  /* Tab walks from the top of the stack down, as a layer list reads. */
  for (int step = 1; step <= n; step++) {
    int i = at < 0 ? (direction > 0 ? n - step : step - 1) : at - direction * step;
    if (i < 0 || i >= n)
      return FALSE; /* past the end: focus moves on */
    Item *item = g_ptr_array_index(self->items, i);
    if (item->visible) {
      select_item(self, item->id, TRUE);
      return TRUE;
    }
  }
  return FALSE;
}

static gboolean key_pressed(GtkEventControllerKey *keys G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                            GdkModifierType state, gpointer user_data) {
  LumaArrangeView *self = user_data;
  Item *item = find_item(self, self->selected);
  double step = (state & GDK_SHIFT_MASK) ? LUMA_UI_CREATIVE_ARRANGE_NUDGE_LARGE : 1;
  double dx = 0, dy = 0;
  switch (keyval) {
  case GDK_KEY_Left: case GDK_KEY_KP_Left: dx = -step; break;
  case GDK_KEY_Right: case GDK_KEY_KP_Right: dx = step; break;
  case GDK_KEY_Up: case GDK_KEY_KP_Up: dy = -step; break;
  case GDK_KEY_Down: case GDK_KEY_KP_Down: dy = step; break;
  case GDK_KEY_Escape:
    if (item == NULL)
      return FALSE;
    select_item(self, NULL, TRUE);
    return TRUE;
  case GDK_KEY_Tab: case GDK_KEY_KP_Tab: return cycle(self, 1);
  case GDK_KEY_ISO_Left_Tab: return cycle(self, -1);
  default: return FALSE;
  }
  if (item == NULL || item->locked)
    return FALSE;
  item->x = round(item->x + dx);
  item->y = round(item->y + dy);
  announce(self);
  emit_rect(self, SIG_ITEM_CHANGED, item);
  emit_rect(self, SIG_ITEM_COMMITTED, item);
  gtk_widget_queue_draw(GTK_WIDGET(self));
  return TRUE;
}

/* ── Layout and drawing ── */

static void luma_arrange_view_measure(GtkWidget *widget, GtkOrientation orientation, int for_size, int *minimum,
                                      int *natural, int *minimum_baseline G_GNUC_UNUSED,
                                      int *natural_baseline G_GNUC_UNUSED) {
  LumaArrangeView *self = LUMA_ARRANGE_VIEW(widget);
  *minimum = *natural = 0;
  if (self->content != NULL && gtk_widget_get_visible(self->content))
    gtk_widget_measure(self->content, orientation, for_size, minimum, natural, NULL, NULL);
  *minimum = 0;
}

static void luma_arrange_view_size_allocate(GtkWidget *widget, int width G_GNUC_UNUSED, int height G_GNUC_UNUSED,
                                            int baseline G_GNUC_UNUSED) {
  LumaArrangeView *self = LUMA_ARRANGE_VIEW(widget);
  for (guint i = 0; i < G_N_ELEMENTS(self->nodes); i++) {
    int min, nat;
    gtk_widget_measure(self->nodes[i], GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat, NULL, NULL);
    gtk_widget_size_allocate(self->nodes[i], &(GtkAllocation){0, 0, 0, 0}, -1);
  }
  if (self->content == NULL || !gtk_widget_get_visible(self->content))
    return;
  double ox, oy, scale = fit(self, &ox, &oy);
  GtkAllocation box = {(int)ox, (int)oy, (int)ceil(self->canvas_w * scale), (int)ceil(self->canvas_h * scale)};
  gtk_widget_size_allocate(self->content, &box, -1);
}

static void stroke_rect(GtkSnapshot *snapshot, const graphene_rect_t *rect, float width, const GdkRGBA *color) {
  GskRoundedRect outline;
  gsk_rounded_rect_init_from_rect(&outline, rect, 0);
  float widths[4] = {width, width, width, width};
  GdkRGBA colors[4] = {*color, *color, *color, *color};
  gtk_snapshot_append_border(snapshot, &outline, widths, colors);
}

static void draw_handle(GtkSnapshot *snapshot, double x, double y, const GdkRGBA *ring, const GdkRGBA *knob) {
  float s = LUMA_UI_CREATIVE_ARRANGE_HANDLE;
  graphene_rect_t square = GRAPHENE_RECT_INIT((float)(round(x) - s / 2), (float)(round(y) - s / 2), s, s);
  GskRoundedRect rounded;
  gsk_rounded_rect_init_from_rect(&rounded, &square, 2);
  gtk_snapshot_push_rounded_clip(snapshot, &rounded);
  gtk_snapshot_append_color(snapshot, knob, &square);
  gtk_snapshot_pop(snapshot);
  float widths[4] = {1, 1, 1, 1};
  GdkRGBA colors[4] = {*ring, *ring, *ring, *ring};
  gtk_snapshot_append_border(snapshot, &rounded, widths, colors);
}

static void luma_arrange_view_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaArrangeView *self = LUMA_ARRANGE_VIEW(widget);
  if (self->content != NULL)
    gtk_widget_snapshot_child(widget, self->content, snapshot);
  GdkRGBA colors[6];
  for (guint i = 0; i < G_N_ELEMENTS(self->nodes); i++)
    gtk_widget_get_color(self->nodes[i], &colors[i]);
  double ox, oy, scale = fit(self, &ox, &oy);
  graphene_rect_t canvas = GRAPHENE_RECT_INIT((float)ox, (float)oy, (float)(self->canvas_w * scale),
                                              (float)(self->canvas_h * scale));
  float line = LUMA_UI_CREATIVE_ARRANGE_GUIDE;
  for (guint i = 0; i < 2; i++) {
    if (!isnan(self->guide_x[i])) {
      graphene_rect_t r = GRAPHENE_RECT_INIT((float)(round(ox + self->guide_x[i] * scale) - line / 2), canvas.origin.y,
                                             line, canvas.size.height);
      gtk_snapshot_append_color(snapshot, &colors[NODE_GUIDE], &r);
    }
    if (!isnan(self->guide_y[i])) {
      graphene_rect_t r = GRAPHENE_RECT_INIT(canvas.origin.x, (float)(round(oy + self->guide_y[i] * scale) - line / 2),
                                             canvas.size.width, line);
      gtk_snapshot_append_color(snapshot, &colors[NODE_GUIDE], &r);
    }
  }
  Item *hover = find_item(self, self->hovered);
  if (hover != NULL && hover->visible && g_strcmp0(self->hovered, self->selected) != 0 && self->grab == GRAB_NONE) {
    graphene_rect_t r = GRAPHENE_RECT_INIT((float)(ox + hover->x * scale), (float)(oy + hover->y * scale),
                                           (float)(hover->w * scale), (float)(hover->h * scale));
    stroke_rect(snapshot, &r, 1, &colors[NODE_HOVER]);
  }
  Item *item = find_item(self, self->selected);
  if (item == NULL || !item->visible)
    return;
  double x0 = ox + item->x * scale, y0 = oy + item->y * scale;
  double x1 = x0 + item->w * scale, y1 = y0 + item->h * scale;
  graphene_rect_t frame = GRAPHENE_RECT_INIT((float)x0, (float)y0, (float)(x1 - x0), (float)(y1 - y0));
  const GdkRGBA *ring = item->locked ? &colors[NODE_LOCKED] : &colors[NODE_FRAME];
  stroke_rect(snapshot, &frame, LUMA_UI_CREATIVE_ARRANGE_FRAME, ring);
  if (item->locked)
    return;
  double r = LUMA_UI_CREATIVE_ARRANGE_HANDLE * 1.5;
  gboolean small = (x1 - x0) < 3 * r || (y1 - y0) < 3 * r;
  double spots[8][2] = {{x0, y0}, {x1, y0}, {x0, y1}, {x1, y1},
                        {(x0 + x1) / 2, y0}, {(x0 + x1) / 2, y1}, {x0, (y0 + y1) / 2}, {x1, (y0 + y1) / 2}};
  for (guint i = 0; i < (small ? 4 : 8); i++)
    draw_handle(snapshot, spots[i][0], spots[i][1], &colors[NODE_HANDLE], &colors[NODE_KNOB]);
}

static void luma_arrange_view_dispose(GObject *object) {
  LumaArrangeView *self = LUMA_ARRANGE_VIEW(object);
  g_clear_pointer(&self->content, gtk_widget_unparent);
  for (guint i = 0; i < G_N_ELEMENTS(self->nodes); i++)
    g_clear_pointer(&self->nodes[i], gtk_widget_unparent);
  G_OBJECT_CLASS(luma_arrange_view_parent_class)->dispose(object);
}

static void luma_arrange_view_finalize(GObject *object) {
  LumaArrangeView *self = LUMA_ARRANGE_VIEW(object);
  g_clear_pointer(&self->items, g_ptr_array_unref);
  g_clear_pointer(&self->selected, g_free);
  g_clear_pointer(&self->hovered, g_free);
  G_OBJECT_CLASS(luma_arrange_view_parent_class)->finalize(object);
}

static void luma_arrange_view_class_init(LumaArrangeViewClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->dispose = luma_arrange_view_dispose;
  object_class->finalize = luma_arrange_view_finalize;
  widget_class->measure = luma_arrange_view_measure;
  widget_class->size_allocate = luma_arrange_view_size_allocate;
  widget_class->snapshot = luma_arrange_view_snapshot;
  gtk_widget_class_set_css_name(widget_class, "widget");
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_GROUP);
  /**
   * LumaArrangeView::selection-changed:
   * @self: the view
   * @id: (nullable): the box a person selected, or %NULL
   */
  signals[SIG_SELECTION_CHANGED] = g_signal_new("selection-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                                                0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaArrangeView::item-changed:
   * @self: the view
   * @id: the box
   * @x: its new left, in canvas units
   * @y: top
   * @width: width
   * @height: height
   *
   * Every step of a drag or nudge.
   */
  signals[SIG_ITEM_CHANGED] = g_signal_new("item-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                           NULL, NULL, G_TYPE_NONE, 5, G_TYPE_STRING, G_TYPE_DOUBLE, G_TYPE_DOUBLE,
                                           G_TYPE_DOUBLE, G_TYPE_DOUBLE);
  /**
   * LumaArrangeView::item-committed:
   * @self: the view
   * @id: the box
   * @x: its final left
   * @y: top
   * @width: width
   * @height: height
   *
   * The end of a drag, or a nudge: one undoable change.
   */
  signals[SIG_ITEM_COMMITTED] = g_signal_new("item-committed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                             NULL, NULL, NULL, G_TYPE_NONE, 5, G_TYPE_STRING, G_TYPE_DOUBLE,
                                             G_TYPE_DOUBLE, G_TYPE_DOUBLE, G_TYPE_DOUBLE);
  /**
   * LumaArrangeView::item-activated:
   * @self: the view
   * @id: the box a person double-clicked
   */
  signals[SIG_ITEM_ACTIVATED] = g_signal_new("item-activated", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                             NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
}

static void luma_arrange_view_init(LumaArrangeView *self) {
  luma_creative_install();
  self->items = g_ptr_array_new_with_free_func(item_free);
  self->canvas_w = 1920;
  self->canvas_h = 1080;
  self->snapping = TRUE;
  self->grab = GRAB_NONE;
  clear_guides(self);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-arrange-view");
  gtk_widget_set_focusable(GTK_WIDGET(self), TRUE);
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_vexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_overflow(GTK_WIDGET(self), GTK_OVERFLOW_HIDDEN);
  luma_ui_set_accessible_label(GTK_WIDGET(self), "Canvas");
  for (guint i = 0; i < G_N_ELEMENTS(self->nodes); i++) {
    self->nodes[i] = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(self->nodes[i], node_names[i]);
    gtk_widget_set_can_target(self->nodes[i], FALSE);
    gtk_widget_set_child_visible(self->nodes[i], FALSE);
    gtk_accessible_update_state(GTK_ACCESSIBLE(self->nodes[i]), GTK_ACCESSIBLE_STATE_HIDDEN, TRUE, -1);
    gtk_widget_set_parent(self->nodes[i], GTK_WIDGET(self));
  }
  GtkGesture *drag = gtk_gesture_drag_new();
  gtk_gesture_single_set_button(GTK_GESTURE_SINGLE(drag), GDK_BUTTON_PRIMARY);
  g_signal_connect(drag, "drag-begin", G_CALLBACK(drag_begin), self);
  g_signal_connect(drag, "drag-update", G_CALLBACK(drag_update), self);
  g_signal_connect(drag, "drag-end", G_CALLBACK(drag_end), self);
  gtk_widget_add_controller(GTK_WIDGET(self), GTK_EVENT_CONTROLLER(drag));
  GtkGesture *click = gtk_gesture_click_new();
  gtk_gesture_single_set_button(GTK_GESTURE_SINGLE(click), GDK_BUTTON_PRIMARY);
  g_signal_connect(click, "pressed", G_CALLBACK(pressed), self);
  gtk_widget_add_controller(GTK_WIDGET(self), GTK_EVENT_CONTROLLER(click));
  gtk_gesture_group(click, drag);
  GtkEventController *hover = gtk_event_controller_motion_new();
  g_signal_connect(hover, "motion", G_CALLBACK(motion), self);
  g_signal_connect(hover, "leave", G_CALLBACK(left), self);
  gtk_widget_add_controller(GTK_WIDGET(self), hover);
  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(key_pressed), self);
  gtk_widget_add_controller(GTK_WIDGET(self), keys);
}

GtkWidget *luma_arrange_view_new(GtkWidget *content) {
  GtkWidget *self = g_object_new(LUMA_TYPE_ARRANGE_VIEW, NULL);
  if (content != NULL)
    luma_arrange_view_set_content(LUMA_ARRANGE_VIEW(self), content);
  return self;
}

void luma_arrange_view_set_content(LumaArrangeView *self, GtkWidget *content) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  g_return_if_fail(content == NULL || GTK_IS_WIDGET(content));
  if (self->content == content)
    return;
  g_clear_pointer(&self->content, gtk_widget_unparent);
  if (content != NULL) {
    self->content = content;
    gtk_widget_insert_after(content, GTK_WIDGET(self), NULL);
    gtk_widget_set_can_target(content, FALSE);
  }
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

GtkWidget *luma_arrange_view_get_content(LumaArrangeView *self) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), NULL);
  return self->content;
}

void luma_arrange_view_set_canvas_size(LumaArrangeView *self, double width, double height) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  g_return_if_fail(width > 0 && height > 0);
  self->canvas_w = width;
  self->canvas_h = height;
  gtk_widget_queue_allocate(GTK_WIDGET(self));
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_add_item(LumaArrangeView *self, const char *id, const char *label, double x, double y,
                                double width, double height) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  g_return_if_fail(id != NULL && *id != '\0');
  g_return_if_fail(width > 0 && height > 0);
  if (find_item(self, id) != NULL) {
    g_critical("an arrange view already has a box %s", id);
    return;
  }
  Item *item = g_new0(Item, 1);
  item->id = g_strdup(id);
  item->label = g_strdup(label != NULL ? label : id);
  item->x = x;
  item->y = y;
  item->w = width;
  item->h = height;
  item->visible = TRUE;
  g_ptr_array_add(self->items, item);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_remove_item(LumaArrangeView *self, const char *id) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  if (item == NULL)
    return;
  if (g_strcmp0(self->selected, id) == 0)
    select_item(self, NULL, FALSE);
  if (g_strcmp0(self->hovered, id) == 0)
    g_clear_pointer(&self->hovered, g_free);
  g_ptr_array_remove(self->items, item);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_clear(LumaArrangeView *self) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  select_item(self, NULL, FALSE);
  g_clear_pointer(&self->hovered, g_free);
  g_ptr_array_set_size(self->items, 0);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_set_item_rect(LumaArrangeView *self, const char *id, double x, double y, double width,
                                     double height) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  item->x = x;
  item->y = y;
  item->w = MAX(width, 1);
  item->h = MAX(height, 1);
  if (g_strcmp0(self->selected, id) == 0)
    announce(self);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

gboolean luma_arrange_view_get_item_rect(LumaArrangeView *self, const char *id, double *x, double *y, double *width,
                                         double *height) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), FALSE);
  Item *item = find_item(self, id);
  if (item == NULL)
    return FALSE;
  if (x != NULL)
    *x = item->x;
  if (y != NULL)
    *y = item->y;
  if (width != NULL)
    *width = item->w;
  if (height != NULL)
    *height = item->h;
  return TRUE;
}

void luma_arrange_view_set_item_label(LumaArrangeView *self, const char *id, const char *label) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  g_free(item->label);
  item->label = g_strdup(label != NULL ? label : id);
  if (g_strcmp0(self->selected, id) == 0)
    announce(self);
}

void luma_arrange_view_set_item_locked(LumaArrangeView *self, const char *id, gboolean locked) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  item->locked = !!locked;
  if (g_strcmp0(self->selected, id) == 0)
    announce(self);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_set_item_visible(LumaArrangeView *self, const char *id, gboolean visible) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  item->visible = !!visible;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_set_item_keeps_aspect(LumaArrangeView *self, const char *id, gboolean keeps) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  item->keeps_aspect = !!keeps;
}

void luma_arrange_view_set_order(LumaArrangeView *self, const char *const *ids) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  GPtrArray *ordered = g_ptr_array_new_with_free_func(item_free);
  for (guint i = 0; i < self->items->len; i++) {
    Item *item = g_ptr_array_index(self->items, i);
    if (ids == NULL || !g_strv_contains(ids, item->id))
      g_ptr_array_add(ordered, item);
  }
  for (guint j = 0; ids != NULL && ids[j] != NULL; j++) {
    Item *item = find_item(self, ids[j]);
    if (item != NULL && !g_ptr_array_find(ordered, item, NULL))
      g_ptr_array_add(ordered, item);
  }
  g_ptr_array_set_free_func(self->items, NULL);
  g_ptr_array_unref(self->items);
  self->items = ordered;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_set_selected(LumaArrangeView *self, const char *id) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  select_item(self, find_item(self, id) != NULL ? id : NULL, FALSE);
}

const char *luma_arrange_view_get_selected(LumaArrangeView *self) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), NULL);
  return self->selected;
}

void luma_arrange_view_set_snapping(LumaArrangeView *self, gboolean snapping) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  self->snapping = !!snapping;
}

gboolean luma_arrange_view_get_snapping(LumaArrangeView *self) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), FALSE);
  return self->snapping;
}
