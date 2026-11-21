/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI creative family: LumaArrangeView (boxes on a canvas, by hand). */
#include "luma-arrange-view.h"
#include "luma-creative-private.h"

#include <math.h>
#include <string.h>

typedef struct {
  char *id;
  char *label;
  double x, y, w, h;
  gboolean locked;
  gboolean visible;
  gboolean keeps_aspect;
  gboolean croppable;
  double crop[4]; /* left, top, right, bottom: fractions of the source cut away */
  GdkPaintable *ghost;
  gboolean has_shown;
  double shown[4]; /* where the picture on screen is (x, y, w, h), when the app says */
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

enum {
  SIG_SELECTION_CHANGED,
  SIG_ITEM_CHANGED,
  SIG_ITEM_COMMITTED,
  SIG_ITEM_ACTIVATED,
  SIG_CROP_CHANGED,
  SIG_CROP_COMMITTED,
  SIG_SELECTION_IDS_CHANGED,
  SIG_GROUP_CHANGED,
  SIG_GROUP_COMMITTED,
  N_SIGNALS
};
static guint signals[N_SIGNALS];

enum { PROP_0, PROP_CROPPING, PROP_DRAG_MODE, N_PROPS };
static GParamSpec *props[N_PROPS];

#define CROP_MAX 0.49
#define DRAG_MODS (GDK_SHIFT_MASK | GDK_CONTROL_MASK | GDK_ALT_MASK)

#define N_GUIDES 4

struct _LumaArrangeView {
  GtkWidget parent_instance;
  GtkWidget *content;
  GtkWidget *nodes[8];
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
  gboolean crop_moved;
  double drag_dx, drag_dy;   /* the drag's offset so far, in view pixels */
  GdkModifierType mods;      /* Shift, Ctrl and Alt as last seen */
  const char *drag_mode;     /* static string */
  char *cropping;            /* the box in crop mode */
  /* Guides shown during a drag, in canvas units (NAN: none). */
  double guide_x[2], guide_y[2];
  /* The selection: every chosen box, in the order chosen; `selected` is the last. */
  GPtrArray *chosen;          /* char*, owned */
  gboolean group;             /* the drag moves or scales the chosen boxes together */
  GHashTable *group_start;    /* id -> Item (geometry at the drag's start) */
  gboolean marquee;           /* a rubber band from empty canvas */
  double band[4];             /* its start and end, canvas units */
  GPtrArray *band_base;       /* the selection it adds to (Shift), char* owned */
};

enum { NODE_FRAME, NODE_HOVER, NODE_HANDLE, NODE_KNOB, NODE_GUIDE, NODE_LOCKED, NODE_GHOST, NODE_MARQUEE };
static const char *node_names[] = {"lumaui-arrange-frame", "lumaui-arrange-hover", "lumaui-arrange-handle",
                                   "lumaui-arrange-knob",  "lumaui-arrange-guide", "lumaui-arrange-locked",
                                   "lumaui-arrange-ghost", "lumaui-arrange-marquee"};

G_DEFINE_FINAL_TYPE(LumaArrangeView, luma_arrange_view, GTK_TYPE_WIDGET)

static void item_free(gpointer data) {
  Item *item = data;
  g_free(item->id);
  g_free(item->label);
  g_clear_object(&item->ghost);
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
  g_autofree char *words = g_strdup_printf("%s selected%s%s, %.0f by %.0f at %.0f, %.0f", item->label,
                                           item->locked ? ", locked" : "",
                                           g_strcmp0(self->cropping, item->id) == 0 ? ", cropping" : "", item->w,
                                           item->h, item->x, item->y);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, words, -1);
}

static gboolean chosen_has(LumaArrangeView *self, const char *id) {
  for (guint i = 0; id != NULL && i < self->chosen->len; i++)
    if (g_str_equal(g_ptr_array_index(self->chosen, i), id))
      return TRUE;
  return FALSE;
}

static char **chosen_strv(LumaArrangeView *self) {
  char **ids = g_new0(char *, self->chosen->len + 1);
  for (guint i = 0; i < self->chosen->len; i++)
    ids[i] = g_strdup(g_ptr_array_index(self->chosen, i));
  return ids;
}

/* Make `ids` (boxes the view has, once each) the selection; its last is `selected`. */
static void choose(LumaArrangeView *self, const char *const *ids, guint n, gboolean emit) {
  g_autoptr(GPtrArray) next = g_ptr_array_new_with_free_func(g_free);
  for (guint i = 0; i < n; i++) {
    gboolean seen = FALSE;
    for (guint j = 0; j < next->len; j++)
      seen = seen || g_str_equal(g_ptr_array_index(next, j), ids[i]);
    if (ids[i] != NULL && !seen && find_item(self, ids[i]) != NULL)
      g_ptr_array_add(next, g_strdup(ids[i]));
  }
  gboolean same = next->len == self->chosen->len;
  for (guint i = 0; same && i < next->len; i++)
    same = g_str_equal(g_ptr_array_index(next, i), g_ptr_array_index(self->chosen, i));
  const char *primary = next->len > 0 ? g_ptr_array_index(next, next->len - 1) : NULL;
  gboolean primary_changed = g_strcmp0(primary, self->selected) != 0;
  if (self->cropping != NULL && (next->len != 1 || g_strcmp0(self->cropping, primary) != 0))
    luma_arrange_view_set_cropping(self, NULL);
  g_ptr_array_set_size(self->chosen, 0);
  for (guint i = 0; i < next->len; i++)
    g_ptr_array_add(self->chosen, g_strdup(g_ptr_array_index(next, i)));
  g_free(self->selected);
  self->selected = g_strdup(primary);
  announce(self);
  gtk_widget_queue_draw(GTK_WIDGET(self));
  if (!emit)
    return;
  if (primary_changed)
    g_signal_emit(self, signals[SIG_SELECTION_CHANGED], 0, self->selected);
  if (!same) {
    g_auto(GStrv) all = chosen_strv(self);
    g_signal_emit(self, signals[SIG_SELECTION_IDS_CHANGED], 0, all);
  }
}

static void select_item(LumaArrangeView *self, const char *id, gboolean emit) {
  const char *ids[] = {id};
  choose(self, ids, id != NULL ? 1 : 0, emit);
}

/* Add `id` to the selection, or take it out (Shift+click). */
static void toggle_item(LumaArrangeView *self, const char *id) {
  gboolean had = chosen_has(self, id);
  g_autoptr(GPtrArray) ids = g_ptr_array_new_with_free_func(g_free);
  for (guint i = 0; i < self->chosen->len; i++)
    if (!g_str_equal(g_ptr_array_index(self->chosen, i), id))
      g_ptr_array_add(ids, g_strdup(g_ptr_array_index(self->chosen, i)));
  if (!had)
    g_ptr_array_add(ids, g_strdup(id));
  choose(self, (const char *const *)ids->pdata, ids->len, TRUE);
}

/* The box around every chosen visible box; FALSE when none. */
static gboolean chosen_bounds(LumaArrangeView *self, Item *box) {
  gboolean any = FALSE;
  double x0 = 0, y0 = 0, x1 = 0, y1 = 0;
  for (guint i = 0; i < self->chosen->len; i++) {
    Item *item = find_item(self, g_ptr_array_index(self->chosen, i));
    if (item == NULL || !item->visible)
      continue;
    if (!any) {
      x0 = item->x, y0 = item->y, x1 = item->x + item->w, y1 = item->y + item->h;
      any = TRUE;
    } else {
      x0 = MIN(x0, item->x), y0 = MIN(y0, item->y);
      x1 = MAX(x1, item->x + item->w), y1 = MAX(y1, item->y + item->h);
    }
  }
  if (any) {
    memset(box, 0, sizeof *box);
    box->id = (char *)"";
    box->x = x0, box->y = y0, box->w = MAX(x1 - x0, 1), box->h = MAX(y1 - y0, 1);
    box->visible = TRUE;
    box->keeps_aspect = TRUE; /* a group scales in proportion; Shift stretches it */
  }
  return any;
}

static gboolean chosen_movable(LumaArrangeView *self) {
  for (guint i = 0; i < self->chosen->len; i++) {
    Item *item = find_item(self, g_ptr_array_index(self->chosen, i));
    if (item != NULL && !item->locked)
      return TRUE;
  }
  return FALSE;
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
  Item box;
  Item *item = find_item(self, self->selected);
  if (self->chosen->len >= 2) {
    if (!chosen_bounds(self, &box) || !chosen_movable(self))
      return GRAB_NONE;
    item = &box;
  }
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
    if (other == moving || !other->visible || g_str_equal(other->id, moving->id) ||
        (self->group && chosen_has(self, other->id)))
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

static void emit_crop(LumaArrangeView *self, guint signal, Item *item) {
  g_signal_emit(self, signals[signal], 0, item->id, item->crop[0], item->crop[1], item->crop[2], item->crop[3]);
}

static void set_drag_mode(LumaArrangeView *self, const char *mode) {
  if (g_strcmp0(self->drag_mode, mode) == 0)
    return;
  self->drag_mode = mode;
  g_object_notify_by_pspec(G_OBJECT(self), props[PROP_DRAG_MODE]);
}

/* The source's whole extent in canvas units: the box grown by what its crop cuts away. */
static void source_rect(const Item *item, double *x, double *y, double *w, double *h) {
  *w = item->w / MAX(1 - item->crop[0] - item->crop[2], 0.02);
  *h = item->h / MAX(1 - item->crop[1] - item->crop[3], 0.02);
  *x = item->x - item->crop[0] * *w;
  *y = item->y - item->crop[1] * *h;
}

static gboolean in_crop_mode(LumaArrangeView *self, const Item *item) {
  return item->croppable && g_strcmp0(self->cropping, item->id) == 0;
}

/* What a drag with @grab does to @item while @mods are held. */
static const char *mode_for(LumaArrangeView *self, const Item *item, Grab grab, GdkModifierType mods) {
  gboolean alt = (mods & GDK_ALT_MASK) != 0;
  if (grab == GRAB_NONE)
    return "none";
  if (grab == GRAB_MOVE)
    return in_crop_mode(self, item) ? "pan" : "move";
  if (item->croppable && ((mods & GDK_CONTROL_MASK) || in_crop_mode(self, item)))
    return alt ? "crop-centre" : "crop";
  gboolean keep = item->keeps_aspect != ((mods & GDK_SHIFT_MASK) != 0);
  return keep ? (alt ? "resize-centre" : "resize") : (alt ? "stretch-centre" : "stretch");
}

/* Snap a moving edge (canvas units) and say where it went. */
static double snapped_edge(LumaArrangeView *self, Item *item, gboolean vertical, double edge, gboolean snap,
                           double threshold, double *guide) {
  if (!snap)
    return edge;
  return edge + snap_edge(self, item, vertical, edge, threshold, guide);
}

/* Resize: the pulled edges follow the pointer (Alt: the opposite ones mirror them about the centre).
 * With @keep the box scales instead: anchored at the opposite corner or edge, an edge's box centred
 * across it, Alt centred on both axes. */
static void resize(LumaArrangeView *self, Item *item, double cdx, double cdy, gboolean keep, gboolean centre,
                   gboolean snap, double threshold, double min) {
  const Item *s = &self->start;
  Grab g = self->grab;
  double cx = s->x + s->w / 2, cy = s->y + s->h / 2, k = centre ? 2 : 1;
  double w = s->w, h = s->h;
  if (g & GRAB_LEFT) {
    double e = snapped_edge(self, item, TRUE, centre ? cx - (s->w - k * cdx) / 2 : s->x + cdx, snap, threshold,
                            &self->guide_x[0]);
    w = centre ? 2 * (cx - e) : s->x + s->w - e;
  } else if (g & GRAB_RIGHT) {
    double e = snapped_edge(self, item, TRUE, centre ? cx + (s->w + k * cdx) / 2 : s->x + s->w + cdx, snap,
                            threshold, &self->guide_x[1]);
    w = centre ? 2 * (e - cx) : e - s->x;
  }
  if (g & GRAB_TOP) {
    double e = snapped_edge(self, item, FALSE, centre ? cy - (s->h - k * cdy) / 2 : s->y + cdy, snap, threshold,
                            &self->guide_y[0]);
    h = centre ? 2 * (cy - e) : s->y + s->h - e;
  } else if (g & GRAB_BOTTOM) {
    double e = snapped_edge(self, item, FALSE, centre ? cy + (s->h + k * cdy) / 2 : s->y + s->h + cdy, snap,
                            threshold, &self->guide_y[1]);
    h = centre ? 2 * (e - cy) : e - s->y;
  }
  gboolean gx = (g & (GRAB_LEFT | GRAB_RIGHT)) != 0, gy = (g & (GRAB_TOP | GRAB_BOTTOM)) != 0;
  if (keep) {
    gboolean by_width = gx && (!gy || fabs(w / s->w) >= fabs(h / s->h));
    double scale = by_width ? w / s->w : h / s->h;
    if (by_width)
      self->guide_y[0] = self->guide_y[1] = NAN;
    else
      self->guide_x[0] = self->guide_x[1] = NAN;
    double least = MAX(min / s->w, min / s->h);
    if (scale < least) {
      scale = least;
      clear_guides(self);
    }
    w = s->w * scale;
    h = s->h * scale;
  } else {
    if (w < min) {
      w = min;
      self->guide_x[0] = self->guide_x[1] = NAN;
    }
    if (h < min) {
      h = min;
      self->guide_y[0] = self->guide_y[1] = NAN;
    }
  }
  /* A pulled axis keeps its opposite edge (Alt: its centre); one that isn't stays centred. */
  item->x = gx && !centre ? ((g & GRAB_LEFT) ? s->x + s->w - w : s->x) : cx - w / 2;
  item->y = gy && !centre ? ((g & GRAB_TOP) ? s->y + s->h - h : s->y) : cy - h / 2;
  item->w = w;
  item->h = h;
}

/* One axis of a crop: @a and @b are the start's cut at the near and far edge, @near/@far the
 * proposed ones (NAN: that edge isn't pulled). Each stays within 0 and CROP_MAX and leaves at least
 * @least of the source showing; Alt (@centre) cuts the opposite edge by as much. */
static void crop_axis(double a, double b, double near, double far, gboolean centre, double least, double *out_a,
                      double *out_b) {
  double room = 1 - least;
  if (centre && (!isnan(near) || !isnan(far))) {
    double d = !isnan(near) ? near - a : far - b;
    d = CLAMP(d, MAX(-a, -b), MIN(MIN(CROP_MAX - a, CROP_MAX - b), (room - a - b) / 2));
    *out_a = a + d;
    *out_b = b + d;
    return;
  }
  *out_a = isnan(near) ? a : CLAMP(near, 0, MAX(0, MIN(CROP_MAX, room - b)));
  *out_b = isnan(far) ? b : CLAMP(far, 0, MAX(0, MIN(CROP_MAX, room - *out_a)));
}

/* Crop: the pulled edges cut into the source (or give it back, up to its edge); the box is what shows. */
static void crop(LumaArrangeView *self, Item *item, double cdx, double cdy, gboolean centre, gboolean snap,
                 double threshold, double min) {
  const Item *s = &self->start;
  Grab g = self->grab;
  double fx, fy, fw, fh;
  source_rect(s, &fx, &fy, &fw, &fh);
  double l = NAN, t = NAN, r = NAN, b = NAN;
  if (g & GRAB_LEFT)
    l = (snapped_edge(self, item, TRUE, s->x + cdx, snap, threshold, &self->guide_x[0]) - fx) / fw;
  if (g & GRAB_RIGHT)
    r = (fx + fw - snapped_edge(self, item, TRUE, s->x + s->w + cdx, snap, threshold, &self->guide_x[1])) / fw;
  if (g & GRAB_TOP)
    t = (snapped_edge(self, item, FALSE, s->y + cdy, snap, threshold, &self->guide_y[0]) - fy) / fh;
  if (g & GRAB_BOTTOM)
    b = (fy + fh - snapped_edge(self, item, FALSE, s->y + s->h + cdy, snap, threshold, &self->guide_y[1])) / fh;
  crop_axis(s->crop[0], s->crop[2], l, r, centre, MIN(min / fw, 1), &item->crop[0], &item->crop[2]);
  crop_axis(s->crop[1], s->crop[3], t, b, centre, MIN(min / fh, 1), &item->crop[1], &item->crop[3]);
  /* A guide shows only where the edge landed on it. */
  if (!isnan(l) && fabs(item->crop[0] - l) > 1e-9)
    self->guide_x[0] = NAN;
  if (!isnan(r) && fabs(item->crop[2] - r) > 1e-9)
    self->guide_x[1] = NAN;
  if (!isnan(t) && fabs(item->crop[1] - t) > 1e-9)
    self->guide_y[0] = NAN;
  if (!isnan(b) && fabs(item->crop[3] - b) > 1e-9)
    self->guide_y[1] = NAN;
  item->x = fx + item->crop[0] * fw;
  item->y = fy + item->crop[1] * fh;
  item->w = fw * (1 - item->crop[0] - item->crop[2]);
  item->h = fh * (1 - item->crop[1] - item->crop[3]);
}

/* Pan, in crop mode: the source slides under the box, which stays put. */
static void pan(LumaArrangeView *self, Item *item, double cdx, double cdy) {
  const Item *s = &self->start;
  double fx, fy, fw, fh;
  source_rect(s, &fx, &fy, &fw, &fh);
  double dx = CLAMP(-cdx / fw, MAX(-s->crop[0], s->crop[2] - CROP_MAX), MIN(CROP_MAX - s->crop[0], s->crop[2]));
  double dy = CLAMP(-cdy / fh, MAX(-s->crop[1], s->crop[3] - CROP_MAX), MIN(CROP_MAX - s->crop[1], s->crop[3]));
  item->crop[0] = s->crop[0] + dx;
  item->crop[2] = s->crop[2] - dx;
  item->crop[1] = s->crop[1] + dy;
  item->crop[3] = s->crop[3] - dy;
}

/* Lay the drag so far over the box it started from, with the modifiers held now. */
static GVariant *group_rects(LumaArrangeView *self) {
  GVariantBuilder rects;
  g_variant_builder_init(&rects, G_VARIANT_TYPE("a{s(dddd)}"));
  for (guint i = 0; i < self->chosen->len; i++) {
    Item *item = find_item(self, g_ptr_array_index(self->chosen, i));
    if (item != NULL && g_hash_table_contains(self->group_start, item->id))
      g_variant_builder_add(&rects, "{s(dddd)}", item->id, item->x, item->y, item->w, item->h);
  }
  return g_variant_builder_end(&rects);
}

/* The group's drag: every chosen box moves with the box around them, or scales inside it. */
static void apply_group(LumaArrangeView *self) {
  double scale = fit(self, NULL, NULL);
  double cdx = self->drag_dx / scale, cdy = self->drag_dy / scale;
  double threshold = LUMA_UI_CREATIVE_ARRANGE_SNAP / scale, min = 2 * LUMA_UI_CREATIVE_ARRANGE_HANDLE / scale;
  gboolean alt = (self->mods & GDK_ALT_MASK) != 0;
  Item *box = &self->start, now = self->start;
  const char *mode = mode_for(self, box, self->grab, self->mods);
  clear_guides(self);
  if (self->grab == GRAB_MOVE) {
    now.x = box->x + cdx;
    now.y = box->y + cdy;
    if (self->snapping && !alt) {
      now.x += snap_move(self, &now, TRUE, threshold, &self->guide_x[0]);
      now.y += snap_move(self, &now, FALSE, threshold, &self->guide_y[0]);
    }
  } else {
    resize(self, &now, cdx, cdy, g_str_has_prefix(mode, "resize"), alt, self->snapping, threshold, min);
  }
  set_drag_mode(self, mode);
  double sx = now.w / box->w, sy = now.h / box->h;
  GHashTableIter iter;
  gpointer key, value;
  g_hash_table_iter_init(&iter, self->group_start);
  while (g_hash_table_iter_next(&iter, &key, &value)) {
    Item *item = find_item(self, key), *start = value;
    if (item == NULL)
      continue;
    item->x = now.x + (start->x - box->x) * sx;
    item->y = now.y + (start->y - box->y) * sy;
    item->w = start->w * sx;
    item->h = start->h * sy;
  }
  if (now.x == box->x && now.y == box->y && now.w == box->w && now.h == box->h && !self->moved)
    return;
  self->moved = TRUE;
  g_signal_emit(self, signals[SIG_GROUP_CHANGED], 0, group_rects(self));
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void apply_drag(LumaArrangeView *self) {
  if (self->group) {
    apply_group(self);
    return;
  }
  Item *item = find_item(self, self->selected);
  if (item == NULL || self->grab == GRAB_NONE)
    return;
  double scale = fit(self, NULL, NULL);
  double cdx = self->drag_dx / scale, cdy = self->drag_dy / scale;
  double threshold = LUMA_UI_CREATIVE_ARRANGE_SNAP / scale, min = 2 * LUMA_UI_CREATIVE_ARRANGE_HANDLE / scale;
  gboolean alt = (self->mods & GDK_ALT_MASK) != 0;
  Item *start = &self->start;
  const char *mode = mode_for(self, start, self->grab, self->mods);
  item->x = start->x, item->y = start->y, item->w = start->w, item->h = start->h;
  memcpy(item->crop, start->crop, sizeof item->crop);
  clear_guides(self);
  if (g_str_equal(mode, "move")) {
    /* Alt moves without snapping. */
    item->x = start->x + cdx;
    item->y = start->y + cdy;
    if (self->snapping && !alt) {
      item->x += snap_move(self, item, TRUE, threshold, &self->guide_x[0]);
      item->y += snap_move(self, item, FALSE, threshold, &self->guide_y[0]);
    }
  } else if (g_str_equal(mode, "pan")) {
    pan(self, item, cdx, cdy);
  } else if (g_str_has_prefix(mode, "crop")) {
    crop(self, item, cdx, cdy, alt, self->snapping, threshold, min);
  } else {
    resize(self, item, cdx, cdy, g_str_has_prefix(mode, "resize"), alt, self->snapping, threshold, min);
  }
  set_drag_mode(self, mode);
  gboolean same_rect = item->x == start->x && item->y == start->y && item->w == start->w && item->h == start->h;
  gboolean same_crop = memcmp(item->crop, start->crop, sizeof item->crop) == 0;
  if (!same_crop || self->crop_moved) {
    self->crop_moved = TRUE;
    emit_crop(self, SIG_CROP_CHANGED, item);
  }
  if (!same_rect || self->moved) {
    self->moved = TRUE;
    emit_rect(self, SIG_ITEM_CHANGED, item);
  }
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void drag_begin(GtkGestureDrag *gesture, double x, double y, gpointer user_data) {
  LumaArrangeView *self = user_data;
  double ox, oy, scale = fit(self, &ox, &oy);
  double cx = (x - ox) / scale, cy = (y - oy) / scale;
  gtk_widget_grab_focus(GTK_WIDGET(self));
  self->moved = self->crop_moved = FALSE;
  self->drag_dx = self->drag_dy = 0;
  GdkEvent *event = gtk_event_controller_get_current_event(GTK_EVENT_CONTROLLER(gesture));
  if (event != NULL)
    self->mods = gdk_event_get_modifier_state(event) & DRAG_MODS;
  clear_guides(self);
  self->group = FALSE;
  g_hash_table_remove_all(self->group_start);
  Grab grab = handle_at(self, x, y);
  Item *item = NULL;
  gboolean shift = (self->mods & GDK_SHIFT_MASK) != 0;
  if (grab != GRAB_NONE && self->chosen->len >= 2) {
    self->group = TRUE;
  } else if (grab != GRAB_NONE) {
    item = find_item(self, self->selected);
  } else {
    item = item_at(self, cx, cy);
    if (item != NULL && shift) {
      /* Shift+click: in or out of the selection, nothing moves. */
      toggle_item(self, item->id);
      self->grab = GRAB_NONE;
      gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
      return;
    }
    if (item != NULL && self->chosen->len >= 2 && chosen_has(self, item->id)) {
      self->group = chosen_movable(self);
      grab = self->group ? GRAB_MOVE : GRAB_NONE;
    } else if (item != NULL) {
      select_item(self, item->id, TRUE);
      grab = !item->locked ? GRAB_MOVE : GRAB_NONE;
    } else {
      /* Empty canvas: a rubber band (Shift adds to the selection). */
      g_ptr_array_set_size(self->band_base, 0);
      for (guint i = 0; shift && i < self->chosen->len; i++)
        g_ptr_array_add(self->band_base, g_strdup(g_ptr_array_index(self->chosen, i)));
      if (!shift)
        select_item(self, NULL, TRUE);
      self->marquee = TRUE;
      self->band[0] = self->band[2] = cx;
      self->band[1] = self->band[3] = cy;
      self->grab = GRAB_NONE;
      gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
      return;
    }
  }
  if (self->group) {
    for (guint i = 0; i < self->chosen->len; i++) {
      Item *chosen = find_item(self, g_ptr_array_index(self->chosen, i));
      if (chosen != NULL && !chosen->locked && chosen->visible)
        g_hash_table_insert(self->group_start, g_strdup(chosen->id), g_memdup2(chosen, sizeof *chosen));
    }
    chosen_bounds(self, &self->start);
    self->grab = grab;
    set_drag_mode(self, mode_for(self, &self->start, grab, self->mods));
    gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
    return;
  }
  self->grab = grab;
  if (item != NULL) {
    self->start = *item;
    set_drag_mode(self, mode_for(self, item, grab, self->mods));
    gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
  } else {
    gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_DENIED);
  }
}

static void drag_update(GtkGestureDrag *gesture, double dx, double dy, gpointer user_data) {
  LumaArrangeView *self = user_data;
  GdkEvent *event = gtk_event_controller_get_current_event(GTK_EVENT_CONTROLLER(gesture));
  if (event != NULL)
    self->mods = gdk_event_get_modifier_state(event) & DRAG_MODS;
  self->drag_dx = dx;
  self->drag_dy = dy;
  if (self->marquee) {
    double scale = fit(self, NULL, NULL);
    self->band[2] = self->band[0] + dx / scale;
    self->band[3] = self->band[1] + dy / scale;
    double x0 = MIN(self->band[0], self->band[2]), x1 = MAX(self->band[0], self->band[2]);
    double y0 = MIN(self->band[1], self->band[3]), y1 = MAX(self->band[1], self->band[3]);
    g_autoptr(GPtrArray) ids = g_ptr_array_new();
    for (guint i = 0; i < self->band_base->len; i++)
      g_ptr_array_add(ids, g_ptr_array_index(self->band_base, i));
    for (guint i = 0; i < self->items->len; i++) {
      Item *item = g_ptr_array_index(self->items, i);
      /* Touched or enclosed. */
      if (item->visible && item->x < x1 && item->x + item->w > x0 && item->y < y1 && item->y + item->h > y0)
        g_ptr_array_add(ids, item->id);
    }
    g_autoptr(GPtrArray) copies = g_ptr_array_new_with_free_func(g_free);
    for (guint i = 0; i < ids->len; i++)
      g_ptr_array_add(copies, g_strdup(g_ptr_array_index(ids, i)));
    choose(self, (const char *const *)copies->pdata, copies->len, FALSE);
    gtk_widget_queue_draw(GTK_WIDGET(self));
    return;
  }
  apply_drag(self);
}

static void drag_end(GtkGestureDrag *gesture G_GNUC_UNUSED, double dx G_GNUC_UNUSED, double dy G_GNUC_UNUSED,
                     gpointer user_data) {
  LumaArrangeView *self = user_data;
  if (self->marquee) {
    self->marquee = FALSE;
    /* The band chose quietly as it moved; say the outcome once. */
    g_auto(GStrv) all = chosen_strv(self);
    g_signal_emit(self, signals[SIG_SELECTION_CHANGED], 0, self->selected);
    g_signal_emit(self, signals[SIG_SELECTION_IDS_CHANGED], 0, all);
    gtk_widget_queue_draw(GTK_WIDGET(self));
    return;
  }
  if (self->group) {
    clear_guides(self);
    if (self->moved)
      g_signal_emit(self, signals[SIG_GROUP_COMMITTED], 0, group_rects(self));
    self->group = FALSE;
    g_hash_table_remove_all(self->group_start);
    self->grab = GRAB_NONE;
    self->moved = FALSE;
    set_drag_mode(self, "none");
    announce(self);
    gtk_widget_queue_draw(GTK_WIDGET(self));
    return;
  }
  Item *item = find_item(self, self->selected);
  clear_guides(self);
  if (item != NULL && self->crop_moved)
    emit_crop(self, SIG_CROP_COMMITTED, item);
  if (item != NULL && self->moved) {
    announce(self);
    emit_rect(self, SIG_ITEM_COMMITTED, item);
  }
  self->grab = GRAB_NONE;
  self->moved = self->crop_moved = FALSE;
  set_drag_mode(self, "none");
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static void pressed(GtkGestureClick *click, int n_press, double x, double y, gpointer user_data) {
  LumaArrangeView *self = user_data;
  if (n_press != 2)
    return;
  double ox, oy, scale = fit(self, &ox, &oy);
  Item *item = item_at(self, (x - ox) / scale, (y - oy) / scale);
  if (item == NULL)
    return;
  gtk_gesture_set_state(GTK_GESTURE(click), GTK_EVENT_SEQUENCE_CLAIMED);
  /* A box that can be cropped goes into crop mode; any other is activated. */
  if (item->croppable && !item->locked) {
    select_item(self, item->id, TRUE);
    luma_arrange_view_set_cropping(self, item->id);
  } else {
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
  gtk_widget_set_cursor_from_name(GTK_WIDGET(self), grab == GRAB_MOVE && item != NULL && in_crop_mode(self, item)
                                                        ? "grab"
                                                        : cursor_for(grab));
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

static GdkModifierType modifier_of(guint keyval) {
  switch (keyval) {
  case GDK_KEY_Shift_L: case GDK_KEY_Shift_R: return GDK_SHIFT_MASK;
  case GDK_KEY_Control_L: case GDK_KEY_Control_R: return GDK_CONTROL_MASK;
  case GDK_KEY_Alt_L: case GDK_KEY_Alt_R: case GDK_KEY_Meta_L: case GDK_KEY_Meta_R: return GDK_ALT_MASK;
  default: return 0;
  }
}

/* A modifier pressed or let go mid-drag changes what the drag does at once. */
static void modifiers_now(LumaArrangeView *self, GdkModifierType mods) {
  mods &= DRAG_MODS;
  if (mods == self->mods)
    return;
  self->mods = mods;
  if (self->grab != GRAB_NONE)
    apply_drag(self);
}

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
  GdkModifierType modifier = modifier_of(keyval);
  if (modifier != 0) {
    modifiers_now(self, state | modifier);
    return FALSE;
  }
  if ((keyval == GDK_KEY_a || keyval == GDK_KEY_A) && (state & GDK_CONTROL_MASK)) {
    luma_arrange_view_select_all(self);
    return TRUE;
  }
  Item *item = find_item(self, self->selected);
  double step = (state & GDK_SHIFT_MASK) ? LUMA_UI_CREATIVE_ARRANGE_NUDGE_LARGE : 1;
  double dx = 0, dy = 0;
  switch (keyval) {
  case GDK_KEY_Left: case GDK_KEY_KP_Left: dx = -step; break;
  case GDK_KEY_Right: case GDK_KEY_KP_Right: dx = step; break;
  case GDK_KEY_Up: case GDK_KEY_KP_Up: dy = -step; break;
  case GDK_KEY_Down: case GDK_KEY_KP_Down: dy = step; break;
  case GDK_KEY_Return: case GDK_KEY_KP_Enter: case GDK_KEY_ISO_Enter:
    if (self->cropping != NULL) {
      luma_arrange_view_set_cropping(self, NULL);
      return TRUE;
    }
    if (item == NULL || !item->croppable || item->locked || !item->visible)
      return FALSE;
    luma_arrange_view_set_cropping(self, item->id);
    return TRUE;
  case GDK_KEY_Escape:
    if (self->cropping != NULL) {
      luma_arrange_view_set_cropping(self, NULL);
      return TRUE;
    }
    if (item == NULL)
      return FALSE;
    select_item(self, NULL, TRUE);
    return TRUE;
  case GDK_KEY_Tab: case GDK_KEY_KP_Tab: return cycle(self, 1);
  case GDK_KEY_ISO_Left_Tab: return cycle(self, -1);
  default: return FALSE;
  }
  if (self->chosen->len >= 2) {
    /* Every chosen box moves together: one undo step a key. */
    g_hash_table_remove_all(self->group_start);
    for (guint i = 0; i < self->chosen->len; i++) {
      Item *chosen = find_item(self, g_ptr_array_index(self->chosen, i));
      if (chosen == NULL || chosen->locked)
        continue;
      chosen->x = round(chosen->x + dx);
      chosen->y = round(chosen->y + dy);
      g_hash_table_insert(self->group_start, g_strdup(chosen->id), g_memdup2(chosen, sizeof *chosen));
    }
    if (g_hash_table_size(self->group_start) == 0)
      return FALSE;
    g_signal_emit(self, signals[SIG_GROUP_CHANGED], 0, group_rects(self));
    g_signal_emit(self, signals[SIG_GROUP_COMMITTED], 0, group_rects(self));
    g_hash_table_remove_all(self->group_start);
    gtk_widget_queue_draw(GTK_WIDGET(self));
    return TRUE;
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

static void key_released(GtkEventControllerKey *keys G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                         GdkModifierType state, gpointer user_data) {
  GdkModifierType modifier = modifier_of(keyval);
  if (modifier != 0)
    modifiers_now(user_data, state & ~modifier);
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

/* What a crop cuts away, dim outside the box: the source itself when the app gave one, else a scrim. */
static void draw_ghost(GtkSnapshot *snapshot, const Item *item, double ox, double oy, double scale,
                       const graphene_rect_t *box, const GdkRGBA *color) {
  double fx, fy, fw, fh;
  source_rect(item, &fx, &fy, &fw, &fh);
  graphene_rect_t full = GRAPHENE_RECT_INIT((float)(ox + fx * scale), (float)(oy + fy * scale), (float)(fw * scale),
                                            (float)(fh * scale));
  float right = box->origin.x + box->size.width, bottom = box->origin.y + box->size.height;
  float full_right = full.origin.x + full.size.width, full_bottom = full.origin.y + full.size.height;
  graphene_rect_t strips[4] = {
    GRAPHENE_RECT_INIT(full.origin.x, full.origin.y, full.size.width, box->origin.y - full.origin.y),
    GRAPHENE_RECT_INIT(full.origin.x, bottom, full.size.width, full_bottom - bottom),
    GRAPHENE_RECT_INIT(full.origin.x, box->origin.y, box->origin.x - full.origin.x, box->size.height),
    GRAPHENE_RECT_INIT(right, box->origin.y, full_right - right, box->size.height),
  };
  for (guint i = 0; i < G_N_ELEMENTS(strips); i++) {
    if (strips[i].size.width < 0.5f || strips[i].size.height < 0.5f)
      continue;
    if (item->ghost != NULL) {
      gtk_snapshot_push_clip(snapshot, &strips[i]);
      gtk_snapshot_push_opacity(snapshot, color->alpha);
      gtk_snapshot_save(snapshot);
      gtk_snapshot_translate(snapshot, &full.origin);
      gdk_paintable_snapshot(item->ghost, snapshot, full.size.width, full.size.height);
      gtk_snapshot_restore(snapshot);
      gtk_snapshot_pop(snapshot);
      gtk_snapshot_pop(snapshot);
    } else {
      gtk_snapshot_append_color(snapshot, color, &strips[i]);
    }
  }
  stroke_rect(snapshot, &full, 1, color);
}

static void luma_arrange_view_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaArrangeView *self = LUMA_ARRANGE_VIEW(widget);
  if (self->content != NULL)
    gtk_widget_snapshot_child(widget, self->content, snapshot);
  GdkRGBA colors[8];
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
  if (self->marquee) {
    double bx0 = MIN(self->band[0], self->band[2]), by0 = MIN(self->band[1], self->band[3]);
    graphene_rect_t band = GRAPHENE_RECT_INIT((float)(ox + bx0 * scale), (float)(oy + by0 * scale),
                                              (float)(fabs(self->band[2] - self->band[0]) * scale),
                                              (float)(fabs(self->band[3] - self->band[1]) * scale));
    GdkRGBA soft = colors[NODE_MARQUEE];
    soft.alpha *= 0.18f;
    gtk_snapshot_append_color(snapshot, &soft, &band);
    stroke_rect(snapshot, &band, 1, &colors[NODE_MARQUEE]);
  }
  Item group;
  if (self->chosen->len >= 2 && chosen_bounds(self, &group)) {
    for (guint i = 0; i < self->chosen->len; i++) {
      Item *one = find_item(self, g_ptr_array_index(self->chosen, i));
      if (one == NULL || !one->visible)
        continue;
      graphene_rect_t r = GRAPHENE_RECT_INIT((float)(ox + one->x * scale), (float)(oy + one->y * scale),
                                             (float)(one->w * scale), (float)(one->h * scale));
      stroke_rect(snapshot, &r, 1, one->locked ? &colors[NODE_LOCKED] : &colors[NODE_HOVER]);
    }
    double gx0 = ox + group.x * scale, gy0 = oy + group.y * scale;
    double gx1 = gx0 + group.w * scale, gy1 = gy0 + group.h * scale;
    graphene_rect_t box = GRAPHENE_RECT_INIT((float)gx0, (float)gy0, (float)(gx1 - gx0), (float)(gy1 - gy0));
    stroke_rect(snapshot, &box, LUMA_UI_CREATIVE_ARRANGE_FRAME, &colors[NODE_FRAME]);
    if (!chosen_movable(self))
      return;
    double gr = LUMA_UI_CREATIVE_ARRANGE_HANDLE * 1.5;
    gboolean gsmall = (gx1 - gx0) < 3 * gr || (gy1 - gy0) < 3 * gr;
    double gspots[8][2] = {{gx0, gy0}, {gx1, gy0}, {gx0, gy1}, {gx1, gy1},
                           {(gx0 + gx1) / 2, gy0}, {(gx0 + gx1) / 2, gy1}, {gx0, (gy0 + gy1) / 2}, {gx1, (gy0 + gy1) / 2}};
    for (guint i = 0; i < (gsmall ? 4 : 8); i++)
      draw_handle(snapshot, gspots[i][0], gspots[i][1], &colors[NODE_HANDLE], &colors[NODE_KNOB]);
    return;
  }
  Item *item = find_item(self, self->selected);
  if (item == NULL || !item->visible)
    return;
  double x0 = ox + item->x * scale, y0 = oy + item->y * scale;
  double x1 = x0 + item->w * scale, y1 = y0 + item->h * scale;
  graphene_rect_t frame = GRAPHENE_RECT_INIT((float)x0, (float)y0, (float)(x1 - x0), (float)(y1 - y0));
  /* The outline and the crop frame sit on the picture actually shown (it may land a frame or two after
   * the pointer); the handles and hit testing stay on the live rectangle, under the pointer. */
  Item shown = *item;
  if (item->has_shown) {
    shown.x = item->shown[0], shown.y = item->shown[1], shown.w = item->shown[2], shown.h = item->shown[3];
    frame = GRAPHENE_RECT_INIT((float)(ox + shown.x * scale), (float)(oy + shown.y * scale), (float)(shown.w * scale),
                               (float)(shown.h * scale));
  }
  if (in_crop_mode(self, item) || (self->drag_mode != NULL && (g_str_has_prefix(self->drag_mode, "crop") ||
                                                               g_str_equal(self->drag_mode, "pan"))))
    draw_ghost(snapshot, &shown, ox, oy, scale, &frame, &colors[NODE_GHOST]);
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
  g_clear_pointer(&self->cropping, g_free);
  g_clear_pointer(&self->chosen, g_ptr_array_unref);
  g_clear_pointer(&self->band_base, g_ptr_array_unref);
  g_clear_pointer(&self->group_start, g_hash_table_unref);
  G_OBJECT_CLASS(luma_arrange_view_parent_class)->finalize(object);
}

static void luma_arrange_view_get_property(GObject *object, guint id, GValue *value, GParamSpec *spec) {
  LumaArrangeView *self = LUMA_ARRANGE_VIEW(object);
  switch (id) {
  case PROP_CROPPING: g_value_set_string(value, self->cropping); break;
  case PROP_DRAG_MODE: g_value_set_string(value, self->drag_mode); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, spec);
  }
}

static void luma_arrange_view_set_property(GObject *object, guint id, const GValue *value, GParamSpec *spec) {
  LumaArrangeView *self = LUMA_ARRANGE_VIEW(object);
  switch (id) {
  case PROP_CROPPING: luma_arrange_view_set_cropping(self, g_value_get_string(value)); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, spec);
  }
}

static void luma_arrange_view_class_init(LumaArrangeViewClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->get_property = luma_arrange_view_get_property;
  object_class->set_property = luma_arrange_view_set_property;
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
  /**
   * LumaArrangeView::crop-changed:
   * @self: the view
   * @id: the box
   * @left: the fraction of the source cut from its left (0 to 0.49)
   * @top: from its top
   * @right: from its right
   * @bottom: from its bottom
   *
   * Every step of a crop or a pan in crop mode. The box's rectangle (what
   * shows) follows in #LumaArrangeView::item-changed.
   */
  signals[SIG_CROP_CHANGED] = g_signal_new("crop-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                           NULL, NULL, G_TYPE_NONE, 5, G_TYPE_STRING, G_TYPE_DOUBLE, G_TYPE_DOUBLE,
                                           G_TYPE_DOUBLE, G_TYPE_DOUBLE);
  /**
   * LumaArrangeView::crop-committed:
   * @self: the view
   * @id: the box
   * @left: the final fraction cut from the source's left
   * @top: top
   * @right: right
   * @bottom: bottom
   *
   * The end of a crop or pan: one undoable change (with the box's
   * #LumaArrangeView::item-committed when its rectangle changed too).
   */
  signals[SIG_CROP_COMMITTED] = g_signal_new("crop-committed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                             NULL, NULL, NULL, G_TYPE_NONE, 5, G_TYPE_STRING, G_TYPE_DOUBLE,
                                             G_TYPE_DOUBLE, G_TYPE_DOUBLE, G_TYPE_DOUBLE);
  /**
   * LumaArrangeView::selection-ids-changed:
   * @self: the view
   * @ids: every chosen box, in the order chosen (the last is #LumaArrangeView::selection-changed's)
   *
   * A person changed the selection: a click, Shift+click, a rubber band, Ctrl+A, Esc.
   */
  signals[SIG_SELECTION_IDS_CHANGED] = g_signal_new("selection-ids-changed", G_TYPE_FROM_CLASS(klass),
                                                    G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 1,
                                                    G_TYPE_STRV);
  /**
   * LumaArrangeView::group-changed:
   * @self: the view
   * @rects: `a{s(dddd)}`, each moved box's id and its x, y, width and height
   *
   * Every step of a drag or nudge of two or more boxes together.
   */
  signals[SIG_GROUP_CHANGED] = g_signal_new("group-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                            NULL, NULL, G_TYPE_NONE, 1, G_TYPE_VARIANT);
  /**
   * LumaArrangeView::group-committed:
   * @self: the view
   * @rects: `a{s(dddd)}`, the boxes' final rectangles
   *
   * The end of such a drag, or a nudge: one undoable change.
   */
  signals[SIG_GROUP_COMMITTED] = g_signal_new("group-committed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                              NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_VARIANT);
  /**
   * LumaArrangeView:cropping:
   *
   * The box in crop mode, or %NULL.
   */
  props[PROP_CROPPING] = g_param_spec_string("cropping", NULL, NULL, NULL,
                                             G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  /**
   * LumaArrangeView:drag-mode:
   *
   * What the drag under way does: "none", "move", "resize" (proportions
   * kept), "stretch" (free), "resize-centre", "stretch-centre", "crop",
   * "crop-centre" or "pan". It follows the modifiers as they change, for a
   * hint ("Alt from centre · Shift stretch · Ctrl crop").
   */
  props[PROP_DRAG_MODE] = g_param_spec_string("drag-mode", NULL, NULL, "none",
                                              G_PARAM_READABLE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, N_PROPS, props);
}

static void luma_arrange_view_init(LumaArrangeView *self) {
  luma_creative_install();
  self->items = g_ptr_array_new_with_free_func(item_free);
  self->chosen = g_ptr_array_new_with_free_func(g_free);
  self->band_base = g_ptr_array_new_with_free_func(g_free);
  self->group_start = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, g_free);
  self->canvas_w = 1920;
  self->canvas_h = 1080;
  self->snapping = TRUE;
  self->grab = GRAB_NONE;
  self->drag_mode = "none";
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
  g_signal_connect(keys, "key-released", G_CALLBACK(key_released), self);
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
  if (g_strcmp0(self->cropping, id) == 0)
    luma_arrange_view_set_cropping(self, NULL);
  if (chosen_has(self, id)) {
    g_autoptr(GPtrArray) rest = g_ptr_array_new_with_free_func(g_free);
    for (guint i = 0; i < self->chosen->len; i++)
      if (!g_str_equal(g_ptr_array_index(self->chosen, i), id))
        g_ptr_array_add(rest, g_strdup(g_ptr_array_index(self->chosen, i)));
    choose(self, (const char *const *)rest->pdata, rest->len, FALSE);
  }
  if (g_strcmp0(self->hovered, id) == 0)
    g_clear_pointer(&self->hovered, g_free);
  g_ptr_array_remove(self->items, item);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_clear(LumaArrangeView *self) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  luma_arrange_view_set_cropping(self, NULL);
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

void luma_arrange_view_set_item_croppable(LumaArrangeView *self, const char *id, gboolean croppable) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  item->croppable = !!croppable;
  if (!croppable && g_strcmp0(self->cropping, id) == 0)
    luma_arrange_view_set_cropping(self, NULL);
}

void luma_arrange_view_set_item_crop(LumaArrangeView *self, const char *id, double left, double top, double right,
                                     double bottom) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  double values[4] = {left, top, right, bottom};
  for (guint i = 0; i < 4; i++)
    item->crop[i] = isnan(values[i]) ? 0 : CLAMP(values[i], 0, CROP_MAX);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

gboolean luma_arrange_view_get_item_crop(LumaArrangeView *self, const char *id, double *left, double *top,
                                         double *right, double *bottom) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), FALSE);
  Item *item = find_item(self, id);
  if (item == NULL)
    return FALSE;
  double *out[4] = {left, top, right, bottom};
  for (guint i = 0; i < 4; i++)
    if (out[i] != NULL)
      *out[i] = item->crop[i];
  return TRUE;
}

void luma_arrange_view_set_item_ghost(LumaArrangeView *self, const char *id, GdkPaintable *source) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  g_return_if_fail(source == NULL || GDK_IS_PAINTABLE(source));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  g_set_object(&item->ghost, source);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_set_cropping(LumaArrangeView *self, const char *id) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  if (id != NULL && (item == NULL || !item->croppable || item->locked)) {
    g_critical("an arrange view crops only a box it has that can be cropped and isn't locked: %s", id);
    return;
  }
  if (g_strcmp0(self->cropping, id) == 0)
    return;
  g_free(self->cropping);
  self->cropping = g_strdup(id);
  if (id != NULL && g_strcmp0(self->selected, id) != 0) {
    /* Selected without selection-changed, as luma_arrange_view_set_selected(). */
    g_free(self->selected);
    self->selected = g_strdup(id);
  }
  announce(self);
  gtk_widget_queue_draw(GTK_WIDGET(self));
  g_object_notify_by_pspec(G_OBJECT(self), props[PROP_CROPPING]);
}

const char *luma_arrange_view_get_cropping(LumaArrangeView *self) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), NULL);
  return self->cropping;
}

const char *luma_arrange_view_get_drag_mode(LumaArrangeView *self) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), NULL);
  return self->drag_mode;
}

void luma_arrange_view_set_item_shown_rect(LumaArrangeView *self, const char *id, double x, double y, double width,
                                           double height) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  g_return_if_fail(item != NULL);
  item->has_shown = TRUE;
  item->shown[0] = x;
  item->shown[1] = y;
  item->shown[2] = MAX(width, 1);
  item->shown[3] = MAX(height, 1);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_arrange_view_clear_item_shown_rect(LumaArrangeView *self, const char *id) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  Item *item = find_item(self, id);
  if (item == NULL || !item->has_shown)
    return;
  item->has_shown = FALSE;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

gboolean luma_arrange_view_get_item_shown_rect(LumaArrangeView *self, const char *id, double *x, double *y,
                                               double *width, double *height) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), FALSE);
  Item *item = find_item(self, id);
  if (item == NULL || !item->has_shown)
    return FALSE;
  double *out[4] = {x, y, width, height};
  for (guint i = 0; i < 4; i++)
    if (out[i] != NULL)
      *out[i] = item->shown[i];
  return TRUE;
}

void luma_arrange_view_set_selection(LumaArrangeView *self, const char *const *ids) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  choose(self, ids, ids != NULL ? g_strv_length((char **)ids) : 0, FALSE);
}

char **luma_arrange_view_get_selection(LumaArrangeView *self) {
  g_return_val_if_fail(LUMA_IS_ARRANGE_VIEW(self), NULL);
  return chosen_strv(self);
}

void luma_arrange_view_select_all(LumaArrangeView *self) {
  g_return_if_fail(LUMA_IS_ARRANGE_VIEW(self));
  g_autoptr(GPtrArray) ids = g_ptr_array_new_with_free_func(g_free);
  for (guint i = 0; i < self->items->len; i++) {
    Item *item = g_ptr_array_index(self->items, i);
    if (item->visible)
      g_ptr_array_add(ids, g_strdup(item->id));
  }
  choose(self, (const char *const *)ids->pdata, ids->len, TRUE);
}
