/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_drawer.MenuDrawer and action_bubble.FloatingMenu /
 * MenuItem / float_at / rect_in (Python). */
#include "luma-menu-drawer.h"
#include "luma-action-private.h"
#include "luma-layer-host.h"
#include "luma-ui-private.h"

#include <math.h>

/* ── placement (action_bubble.float_at / rect_in) ─────────────────────── */

static void natural_size(GtkWidget *widget, int for_width, int *width, int *height) {
  int w = 0, h = 0;
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, NULL, &w, NULL, NULL);
  if (for_width > 0)
    w = for_width;
  gtk_widget_measure(widget, GTK_ORIENTATION_VERTICAL, w, NULL, &h, NULL, NULL);
  *width = w;
  *height = h;
}

LumaFloatSide luma_ui_float_at(GtkWidget *host, GtkWidget *widget, const GdkRectangle *rect,
                               LumaFloatSide prefer, LumaFloatAlign align, int offset, int edge, int inset,
                               int width) {
  g_return_val_if_fail(GTK_IS_WIDGET(host) && GTK_IS_WIDGET(widget) && rect != NULL, LUMA_FLOAT_ABOVE);
  if (offset < 0)
    offset = LUMA_UI_SELECTION_BUBBLE_OFFSET;
  if (edge < 0)
    edge = LUMA_UI_SELECTION_BUBBLE_EDGE;
  if (inset < 0)
    inset = LUMA_UI_SELECTION_BUBBLE_INSET;
  gtk_widget_set_halign(widget, GTK_ALIGN_START);
  gtk_widget_set_valign(widget, GTK_ALIGN_START);
  gtk_widget_set_margin_start(widget, 0);
  gtk_widget_set_margin_top(widget, 0);
  gtk_widget_set_margin_end(widget, 0);
  gtk_widget_set_margin_bottom(widget, 0);
  if (width > 0)
    gtk_widget_set_size_request(widget, width, -1);
  int bw, bh;
  natural_size(widget, width > 0 ? width : -1, &bw, &bh);
  int hw = gtk_widget_get_width(host), hh = gtk_widget_get_height(host);
  int above_y = rect->y - bh - offset;
  int below_y = rect->y + rect->height + offset;
  LumaFloatSide side;
  if (prefer == LUMA_FLOAT_ABOVE)
    side = above_y >= edge ? LUMA_FLOAT_ABOVE : LUMA_FLOAT_BELOW;
  else
    side = (below_y + bh <= hh - edge || above_y < edge) ? LUMA_FLOAT_BELOW : LUMA_FLOAT_ABOVE;
  double y = side == LUMA_FLOAT_ABOVE ? above_y : below_y;
  double x;
  if (align == LUMA_FLOAT_ALIGN_START)
    x = rect->x;
  else if (align == LUMA_FLOAT_ALIGN_END)
    x = rect->x + rect->width - bw;
  else
    x = rect->x + rect->width / 2.0 - bw / 2.0;
  if (hw > 0)
    x = MAX(inset, MIN(hw - bw - inset, x));
  if (gtk_widget_get_direction(host) == GTK_TEXT_DIR_RTL && hw > 0)
    x = hw - x - bw; /* START is the right edge: measure the margin from there */
  gtk_widget_set_margin_start(widget, MAX(0, (int)round(x)));
  gtk_widget_set_margin_top(widget, MAX(0, (int)round(y)));
  luma_ui_set_css_class(widget, "below", side == LUMA_FLOAT_BELOW);
  return side;
}

GdkRectangle luma_ui_rect_in(GtkWidget *host, GtkWidget *widget, const GdkRectangle *rect) {
  GdkRectangle out = {0, 0, 0, 0};
  g_return_val_if_fail(GTK_IS_WIDGET(host) && GTK_IS_WIDGET(widget), out);
  if (rect == NULL) {
    graphene_rect_t bounds;
    if (gtk_widget_compute_bounds(widget, host, &bounds)) {
      out.x = (int)bounds.origin.x;
      out.y = (int)bounds.origin.y;
      out.width = (int)bounds.size.width;
      out.height = (int)bounds.size.height;
    } else {
      out.width = gtk_widget_get_width(widget);
      out.height = gtk_widget_get_height(widget);
    }
    return out;
  }
  graphene_point_t point;
  if (gtk_widget_compute_point(widget, host, &GRAPHENE_POINT_INIT((float)rect->x, (float)rect->y), &point)) {
    out.x = (int)point.x;
    out.y = (int)point.y;
  } else {
    out.x = rect->x;
    out.y = rect->y;
  }
  out.width = rect->width;
  out.height = rect->height;
  return out;
}

/* ── rows ─────────────────────────────────────────────────────────────── */

LumaMenuRow *luma_menu_row_new(LumaMenuRowKind kind, const char *label, const char *icon, GIcon *gicon,
                               const char *note, const char *action, gboolean selected) {
  LumaMenuRow *row = g_new0(LumaMenuRow, 1);
  row->kind = kind;
  row->label = g_strdup(label);
  row->icon = g_strdup(icon);
  row->gicon = gicon != NULL ? g_object_ref(gicon) : NULL;
  row->note = g_strdup(note);
  row->action = g_strdup(action);
  row->selected = selected;
  return row;
}

void luma_menu_row_free(LumaMenuRow *row) {
  if (row == NULL)
    return;
  g_free(row->label);
  g_free(row->icon);
  g_clear_object(&row->gicon);
  g_free(row->note);
  g_free(row->action);
  g_free(row);
}

/* Activate a detailed action name on @where, as MenuItem.on_activate would. */
static void activate_detailed(GtkWidget *where, const char *detailed) {
  g_autofree char *name = NULL;
  g_autoptr(GVariant) target = NULL;
  g_autoptr(GError) error = NULL;
  if (!g_action_parse_detailed_name(detailed, &name, &target, &error)) {
    g_critical("menu row: %s", error->message);
    return;
  }
  luma_ui_activate_action(where, name, target);
}

/* ── MenuDrawer ───────────────────────────────────────────────────────── */

enum { CLOSED, N_DRAWER_SIGNALS };
static guint drawer_signals[N_DRAWER_SIGNALS];

struct _LumaMenuDrawer {
  GtkBox parent_instance;
  GtkWidget *pages;
  GtkWidget *scroller;
  LumaModalHandle *handle; /* weak; the card keeps it */
  GtkWidget *where;        /* the widget actions are activated on */
  char *title;
  int depth;
  gboolean closed;
};

G_DEFINE_FINAL_TYPE(LumaMenuDrawer, luma_menu_drawer, GTK_TYPE_BOX)

static void luma_menu_drawer_dispose(GObject *object) {
  LumaMenuDrawer *self = LUMA_MENU_DRAWER(object);
  g_clear_weak_pointer(&self->handle);
  g_clear_object(&self->where);
  G_OBJECT_CLASS(luma_menu_drawer_parent_class)->dispose(object);
}

static void luma_menu_drawer_finalize(GObject *object) {
  g_free(LUMA_MENU_DRAWER(object)->title);
  G_OBJECT_CLASS(luma_menu_drawer_parent_class)->finalize(object);
}

static void luma_menu_drawer_class_init(LumaMenuDrawerClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_menu_drawer_dispose;
  G_OBJECT_CLASS(klass)->finalize = luma_menu_drawer_finalize;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_MENU);
  /**
   * LumaMenuDrawer::closed:
   * @self: the drawer
   *
   * The drawer closed: a row was chosen, or it was cancelled (Esc, a tap
   * outside, a swipe down), or luma_menu_drawer_close(). Emitted once.
   */
  drawer_signals[CLOSED] = g_signal_new("closed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                                        NULL, G_TYPE_NONE, 0);
}

static void luma_menu_drawer_init(LumaMenuDrawer *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-menu-drawer");
  GtkWidget *grab = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(grab, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(grab, "lumaui-drawer-handle");
  gtk_box_append(GTK_BOX(self), grab);
  self->pages = gtk_stack_new();
  gtk_stack_set_transition_type(GTK_STACK(self->pages), GTK_STACK_TRANSITION_TYPE_SLIDE_LEFT_RIGHT);
  gtk_stack_set_transition_duration(GTK_STACK(self->pages), luma_ui_duration(LUMA_UI_MOTION_MORPH_MS, FALSE));
  gtk_stack_set_vhomogeneous(GTK_STACK(self->pages), FALSE);
  gtk_stack_set_interpolate_size(GTK_STACK(self->pages), TRUE);
  self->scroller = gtk_scrolled_window_new();
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(self->scroller), GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
  gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(self->scroller), TRUE);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->scroller), self->pages);
  gtk_box_append(GTK_BOX(self), self->scroller);
}

static LumaMenuDrawer *drawer_new(const char *title) {
  LumaMenuDrawer *self = g_object_new(LUMA_TYPE_MENU_DRAWER, NULL);
  self->title = g_strdup(title);
  if (title != NULL && *title != '\0')
    luma_ui_set_accessible_label(GTK_WIDGET(self), title);
  return self;
}

static GtkWidget *menu_heading(const char *text) {
  GtkWidget *label = g_object_new(GTK_TYPE_LABEL, "label", text, "xalign", 0.0f, "ellipsize",
                                  PANGO_ELLIPSIZE_END, "accessible-role", GTK_ACCESSIBLE_ROLE_HEADING, NULL);
  gtk_widget_add_css_class(label, "lumaui-menu-heading");
  return label;
}

static GtkWidget *menu_separator(void) {
  GtkWidget *rule = g_object_new(GTK_TYPE_BOX, "accessible-role", GTK_ACCESSIBLE_ROLE_SEPARATOR, NULL);
  gtk_widget_add_css_class(rule, "lumaui-menu-separator");
  return rule;
}

/* MenuDrawer._row, for the rows C menus have (no registry: no description,
 * shortcut, count, check or danger). */
static GtkWidget *menu_row(const char *label, const char *icon, GIcon *gicon, const char *note, gboolean selected,
                           gboolean submenu, const char *css) {
  GtkWidget *row = g_object_new(GTK_TYPE_BUTTON, "accessible-role", GTK_ACCESSIBLE_ROLE_MENU_ITEM, NULL);
  gtk_widget_add_css_class(row, "lumaui-menu-row");
  if (css != NULL)
    gtk_widget_add_css_class(row, css);
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(line, "lumaui-menu-line");
  if (selected)
    gtk_widget_add_css_class(row, "on");
  if (gicon != NULL) {
    GtkWidget *glyph = gtk_image_new_from_gicon(gicon);
    gtk_widget_add_css_class(glyph, "lumaui-app-icon");
    gtk_box_append(GTK_BOX(line), glyph);
  } else if (icon != NULL && *icon != '\0') {
    GtkWidget *glyph = luma_ui_icon_image(icon, 0);
    gtk_widget_add_css_class(glyph, "lumaui-menu-icon");
    gtk_box_append(GTK_BOX(line), glyph);
  }
  GtkWidget *text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_valign(text, GTK_ALIGN_CENTER);
  gtk_widget_set_hexpand(text, TRUE);
  GtkWidget *words = gtk_label_new(label);
  gtk_label_set_xalign(GTK_LABEL(words), 0);
  gtk_label_set_ellipsize(GTK_LABEL(words), PANGO_ELLIPSIZE_END);
  gtk_widget_add_css_class(words, "lumaui-menu-label");
  gtk_box_append(GTK_BOX(text), words);
  gtk_box_append(GTK_BOX(line), text);
  if (note != NULL && *note != '\0') {
    GtkWidget *quiet = gtk_label_new(note);
    gtk_widget_add_css_class(quiet, "lumaui-menu-note");
    gtk_box_append(GTK_BOX(line), quiet);
  }
  if (submenu) {
    GtkWidget *chevron = luma_ui_icon_image("chevron-right", 0);
    gtk_widget_add_css_class(chevron, "lumaui-menu-chevron");
    gtk_box_append(GTK_BOX(line), chevron);
  }
  gtk_button_set_child(GTK_BUTTON(row), line);
  g_autofree char *spoken = note != NULL && *note != '\0' ? g_strdup_printf("%s, %s", label, note) : g_strdup(label);
  luma_ui_set_accessible_label(row, spoken);
  return row;
}

static void drawer_closed(LumaMenuDrawer *self) {
  if (self->closed)
    return;
  self->closed = TRUE;
  g_signal_emit(self, drawer_signals[CLOSED], 0);
}

static gboolean focus_page(gpointer user_data) {
  gtk_widget_child_focus(GTK_WIDGET(user_data), GTK_DIR_TAB_FORWARD);
  g_object_unref(user_data);
  return G_SOURCE_REMOVE;
}

static void drawer_push(LumaMenuDrawer *self, GtkWidget *page, gboolean root) {
  self->depth = root ? 0 : self->depth + 1;
  g_autofree char *name = g_strdup_printf("page-%d", self->depth);
  GtkWidget *old = gtk_stack_get_child_by_name(GTK_STACK(self->pages), name);
  if (old != NULL)
    gtk_stack_remove(GTK_STACK(self->pages), old);
  gtk_stack_add_named(GTK_STACK(self->pages), page, name);
  gtk_stack_set_visible_child(GTK_STACK(self->pages), page);
  if (!root)
    g_idle_add(focus_page, g_object_ref(page));
}

typedef struct {
  GtkStack *stack;
  GtkWidget *page;
} PageRemoval;

static gboolean remove_page(gpointer user_data) {
  PageRemoval *removal = user_data;
  if (gtk_widget_get_parent(removal->page) == GTK_WIDGET(removal->stack))
    gtk_stack_remove(removal->stack, removal->page);
  g_object_unref(removal->stack);
  g_object_unref(removal->page);
  g_free(removal);
  return G_SOURCE_REMOVE;
}

static void drawer_pop(LumaMenuDrawer *self) {
  if (self->depth == 0)
    return;
  GtkWidget *current = gtk_stack_get_visible_child(GTK_STACK(self->pages));
  self->depth--;
  g_autofree char *name = g_strdup_printf("page-%d", self->depth);
  GtkWidget *previous = gtk_stack_get_child_by_name(GTK_STACK(self->pages), name);
  gtk_stack_set_visible_child(GTK_STACK(self->pages), previous);
  PageRemoval *removal = g_new0(PageRemoval, 1);
  removal->stack = GTK_STACK(g_object_ref(self->pages));
  removal->page = g_object_ref(current);
  g_timeout_add(MAX(1u, gtk_stack_get_transition_duration(GTK_STACK(self->pages))), remove_page, removal);
  g_idle_add(focus_page, g_object_ref(previous));
}

static void back_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) { drawer_pop(user_data); }

static GtkWidget *drawer_page(LumaMenuDrawer *self, const char *title, gboolean back) {
  GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(page, "lumaui-menu-page");
  if (back) {
    GtkWidget *row = menu_row(title != NULL && *title != '\0' ? title : "Back", "chevron-left", NULL, NULL, FALSE,
                              FALSE, "back");
    g_signal_connect_object(row, "clicked", G_CALLBACK(back_clicked), self, 0);
    gtk_box_append(GTK_BOX(page), row);
  } else if (title != NULL && *title != '\0') {
    gtk_box_append(GTK_BOX(page), menu_heading(title));
  }
  luma_ui_arrow_keys(page, GTK_ORIENTATION_VERTICAL, FALSE);
  return page;
}

typedef struct {
  GtkWidget *where;
  char *name;
  GVariant *target;
  char *detailed;
} Choice;

static gboolean run_choice(gpointer user_data) {
  Choice *choice = user_data;
  if (choice->detailed != NULL)
    activate_detailed(choice->where, choice->detailed);
  else if (choice->name != NULL)
    luma_ui_activate_action(choice->where, choice->name, choice->target);
  g_object_unref(choice->where);
  g_free(choice->name);
  g_free(choice->detailed);
  g_clear_pointer(&choice->target, g_variant_unref);
  g_free(choice);
  return G_SOURCE_REMOVE;
}

/* Close, then run: an action may rebuild the widget the menu opened from. */
static void drawer_choose(LumaMenuDrawer *self, Choice *choice) {
  g_object_ref(self);
  if (self->handle != NULL)
    luma_modal_handle_close(self->handle);
  drawer_closed(self);
  if (choice != NULL && choice->where != NULL)
    g_idle_add(run_choice, choice);
  else if (choice != NULL)
    run_choice(choice);
  g_object_unref(self);
}

static void model_row_clicked(GtkButton *button, gpointer user_data) {
  LumaMenuDrawer *self = LUMA_MENU_DRAWER(user_data);
  if (self->where == NULL)
    return;
  Choice *choice = g_new0(Choice, 1);
  choice->where = g_object_ref(self->where);
  choice->name = g_strdup(g_object_get_data(G_OBJECT(button), "luma-menu-action"));
  GVariant *target = g_object_get_data(G_OBJECT(button), "luma-menu-target");
  choice->target = target != NULL ? g_variant_ref(target) : NULL;
  drawer_choose(self, choice);
}

static GtkWidget *model_page(LumaMenuDrawer *self, GMenuModel *model, const char *title, gboolean back);

static void submenu_clicked(GtkButton *button, gpointer user_data) {
  LumaMenuDrawer *self = LUMA_MENU_DRAWER(user_data);
  GMenuModel *submenu = g_object_get_data(G_OBJECT(button), "luma-menu-submenu");
  const char *label = g_object_get_data(G_OBJECT(button), "luma-menu-label");
  drawer_push(self, model_page(self, submenu, label, TRUE), FALSE);
}

static char *without_mnemonic(const char *text) {
  GString *out = g_string_new(NULL);
  for (const char *c = text; *c != '\0'; c++)
    if (*c != '_')
      g_string_append_c(out, *c);
  return g_string_free(out, FALSE);
}

static void fill_from_model(LumaMenuDrawer *self, GtkWidget *page, GMenuModel *model) {
  gboolean wrote = FALSE;
  int n = g_menu_model_get_n_items(model);
  for (int index = 0; index < n; index++) {
    g_autoptr(GMenuModel) section = g_menu_model_get_item_link(model, index, G_MENU_LINK_SECTION);
    g_autofree char *text = NULL;
    if (!g_menu_model_get_item_attribute(model, index, G_MENU_ATTRIBUTE_LABEL, "s", &text))
      text = g_strdup("");
    if (section != NULL) {
      if (wrote)
        gtk_box_append(GTK_BOX(page), menu_separator());
      if (*text != '\0')
        gtk_box_append(GTK_BOX(page), menu_heading(text));
      fill_from_model(self, page, section);
      wrote = TRUE;
      continue;
    }
    g_autoptr(GMenuModel) submenu = g_menu_model_get_item_link(model, index, G_MENU_LINK_SUBMENU);
    g_autofree char *action = NULL;
    g_menu_model_get_item_attribute(model, index, G_MENU_ATTRIBUTE_ACTION, "s", &action);
    GVariant *target = g_menu_model_get_item_attribute_value(model, index, G_MENU_ATTRIBUTE_TARGET, NULL);
    g_autofree char *plain = without_mnemonic(text);
    GtkWidget *row = menu_row(plain, NULL, NULL, NULL, FALSE, submenu != NULL, NULL);
    if (submenu != NULL) {
      g_object_set_data_full(G_OBJECT(row), "luma-menu-submenu", g_object_ref(submenu), g_object_unref);
      g_object_set_data_full(G_OBJECT(row), "luma-menu-label", g_strdup(plain), g_free);
      g_signal_connect_object(row, "clicked", G_CALLBACK(submenu_clicked), self, 0);
    } else if (action != NULL) {
      g_object_set_data_full(G_OBJECT(row), "luma-menu-action", g_strdup(action), g_free);
      if (target != NULL)
        g_object_set_data_full(G_OBJECT(row), "luma-menu-target", g_variant_ref(target),
                               (GDestroyNotify)g_variant_unref);
      g_signal_connect_object(row, "clicked", G_CALLBACK(model_row_clicked), self, 0);
    }
    g_clear_pointer(&target, g_variant_unref);
    gtk_box_append(GTK_BOX(page), row);
    wrote = TRUE;
  }
}

static GtkWidget *model_page(LumaMenuDrawer *self, GMenuModel *model, const char *title, gboolean back) {
  GtkWidget *page = drawer_page(self, title, back);
  fill_from_model(self, page, model);
  return page;
}

static void handle_cancelled(LumaModalHandle *handle G_GNUC_UNUSED, gpointer user_data) {
  drawer_closed(LUMA_MENU_DRAWER(user_data));
}

static gboolean drawer_show(LumaMenuDrawer *self, GtkWidget *where) {
  LumaLayerHost *host = luma_layer_host_window_host(where);
  if (host == NULL) {
    g_critical("a menu drawer needs a widget that is inside a window");
    return FALSE;
  }
  int height = gtk_widget_get_height(GTK_WIDGET(host));
  if (height > 0)
    gtk_scrolled_window_set_max_content_height(GTK_SCROLLED_WINDOW(self->scroller),
                                               MAX(120, (int)(height * LUMA_UI_DRAWER_MAX_HEIGHT_PCT / 100.0) - 40));
  LumaModalHandle *handle = luma_layer_host_present_modal(host, GTK_WIDGET(self), NULL, LUMA_DRAWER_MODE_ALWAYS);
  if (handle == NULL)
    return FALSE;
  g_set_weak_pointer(&self->handle, handle);
  g_signal_connect_object(handle, "cancelled", G_CALLBACK(handle_cancelled), self, 0);
  return TRUE;
}

static LumaMenuDrawer *drawer_present(LumaMenuDrawer *self, GtkWidget *where, GtkWidget *page) {
  g_set_object(&self->where, where);
  drawer_push(self, page, TRUE);
  if (!drawer_show(self, where)) {
    g_object_ref_sink(self);
    g_object_unref(self);
    return NULL;
  }
  return self;
}

LumaMenuDrawer *luma_menu_drawer_present_model(GtkWidget *where, GMenuModel *model, const char *title) {
  g_return_val_if_fail(GTK_IS_WIDGET(where), NULL);
  g_return_val_if_fail(G_IS_MENU_MODEL(model), NULL);
  LumaMenuDrawer *self = drawer_new(title);
  return drawer_present(self, where, model_page(self, model, title, FALSE));
}

LumaMenuDrawer *luma_menu_drawer_present_child(GtkWidget *where, GtkWidget *child,
                                               const char *title) {
  g_return_val_if_fail(GTK_IS_WIDGET(where), NULL);
  g_return_val_if_fail(GTK_IS_WIDGET(child), NULL);
  g_return_val_if_fail(gtk_widget_get_parent(child) == NULL, NULL);
  LumaMenuDrawer *self = drawer_new(title);
  GtkWidget *page = drawer_page(self, title, FALSE);
  gtk_box_append(GTK_BOX(page), child);
  return drawer_present(self, where, page);
}

static void item_row_clicked(GtkButton *button, gpointer user_data) {
  LumaMenuDrawer *self = LUMA_MENU_DRAWER(user_data);
  const char *detailed = g_object_get_data(G_OBJECT(button), "luma-menu-action");
  Choice *choice = NULL;
  if (detailed != NULL && self->where != NULL) {
    choice = g_new0(Choice, 1);
    choice->where = g_object_ref(self->where);
    choice->detailed = g_strdup(detailed);
  }
  drawer_choose(self, choice);
}

LumaMenuDrawer *luma_menu_drawer_present_items(GtkWidget *where, GPtrArray *rows, const char *title) {
  g_return_val_if_fail(GTK_IS_WIDGET(where), NULL);
  g_return_val_if_fail(rows != NULL, NULL);
  LumaMenuDrawer *self = drawer_new(title);
  GtkWidget *page = drawer_page(self, title, FALSE);
  for (guint i = 0; i < rows->len; i++) {
    LumaMenuRow *row = g_ptr_array_index(rows, i);
    if (row->kind == LUMA_MENU_ROW_SEPARATOR) {
      gtk_box_append(GTK_BOX(page), menu_separator());
    } else if (row->kind == LUMA_MENU_ROW_HEADING) {
      gtk_box_append(GTK_BOX(page), menu_heading(row->label));
    } else {
      GtkWidget *button = menu_row(row->label, row->icon, row->gicon, row->note, row->selected, FALSE, NULL);
      /* A picker's drawer marks the chosen value too (v70 .cfpop in its phone drawer). */
      if (row->selected && g_object_get_data(G_OBJECT(where), "luma-menu-picker") != NULL) {
        GtkWidget *check = luma_ui_icon_image("check", 0);
        gtk_widget_add_css_class(check, "lumaui-menu-check");
        gtk_widget_add_css_class(check, "picker");
        gtk_box_append(GTK_BOX(gtk_button_get_child(GTK_BUTTON(button))), check);
      }
      if (row->action != NULL)
        g_object_set_data_full(G_OBJECT(button), "luma-menu-action", g_strdup(row->action), g_free);
      g_signal_connect_object(button, "clicked", G_CALLBACK(item_row_clicked), self, 0);
      gtk_box_append(GTK_BOX(page), button);
    }
  }
  return drawer_present(self, where, page);
}

void luma_menu_drawer_close(LumaMenuDrawer *self) {
  g_return_if_fail(LUMA_IS_MENU_DRAWER(self));
  if (self->handle == NULL)
    return;
  g_object_ref(self);
  luma_modal_handle_close(self->handle);
  drawer_closed(self);
  g_object_unref(self);
}

/* ── FloatingMenu ─────────────────────────────────────────────────────── */

struct _LumaFloatingMenu {
  GtkBox parent_instance;
  LumaFloatAlign align;
  GPtrArray *rows;    /* LumaMenuRow */
  GPtrArray *buttons; /* the item buttons, children */
  GtkWidget *handle_bar;
  LumaMenuDrawer *drawer; /* weak */
  LumaActionCenter *grown_center; /* weak, menu holds itself until this panel closes */
  gulong grown_handler;
  GtkWidget *catcher;
  GtkWidget *host;       /* weak */
  GtkWidget *anchor;     /* weak: focus returns and actions go here */
  GtkEventController *keys;
  GtkWidget *keys_root; /* weak */
  gboolean held;        /* a reference held while open */
  gboolean picker;      /* v70 .cfpop: the chosen item carries a check; as wide as its anchor, 200 at least */
};

G_DEFINE_FINAL_TYPE(LumaFloatingMenu, luma_floating_menu, GTK_TYPE_BOX)

static void menu_release(LumaFloatingMenu *self) {
  if (self->held) {
    self->held = FALSE;
    g_object_unref(self);
  }
}

static void grown_center_gone(gpointer data, GObject *object G_GNUC_UNUSED) {
  LumaFloatingMenu *self = data;
  self->grown_center = NULL;
  self->grown_handler = 0;
  menu_release(self);
}

static void grown_menu_closed(LumaActionCenter *center, const char *key, gpointer data) {
  LumaFloatingMenu *self = data;
  if (g_strcmp0(key, "menu") == 0) return;
  if (self->grown_handler) g_signal_handler_disconnect(center, self->grown_handler);
  self->grown_handler = 0;
  g_object_weak_unref(G_OBJECT(center), grown_center_gone, self);
  self->grown_center = NULL;
  menu_release(self);
}

static void luma_floating_menu_dispose(GObject *object) {
  LumaFloatingMenu *self = LUMA_FLOATING_MENU(object);
  if (self->keys != NULL && self->keys_root != NULL)
    gtk_widget_remove_controller(self->keys_root, self->keys);
  self->keys = NULL;
  g_clear_weak_pointer(&self->keys_root);
  g_clear_weak_pointer(&self->drawer);
  g_clear_weak_pointer(&self->host);
  g_clear_weak_pointer(&self->anchor);
  G_OBJECT_CLASS(luma_floating_menu_parent_class)->dispose(object);
}

static void luma_floating_menu_finalize(GObject *object) {
  LumaFloatingMenu *self = LUMA_FLOATING_MENU(object);
  g_ptr_array_unref(self->rows);
  g_ptr_array_unref(self->buttons);
  G_OBJECT_CLASS(luma_floating_menu_parent_class)->finalize(object);
}

static void luma_floating_menu_class_init(LumaFloatingMenuClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_floating_menu_dispose;
  G_OBJECT_CLASS(klass)->finalize = luma_floating_menu_finalize;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_MENU);
}

static void luma_floating_menu_init(LumaFloatingMenu *self) {
  self->align = LUMA_FLOAT_ALIGN_END;
  luma_ui_install();
  self->rows = g_ptr_array_new_with_free_func((GDestroyNotify)luma_menu_row_free);
  self->buttons = g_ptr_array_new();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-menu");
  self->handle_bar = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(self->handle_bar, GTK_ALIGN_CENTER);
  gtk_widget_set_visible(self->handle_bar, FALSE);
  gtk_widget_add_css_class(self->handle_bar, "lumaui-drawer-handle");
  gtk_box_append(GTK_BOX(self), self->handle_bar);
}

GtkWidget *luma_floating_menu_new(const char *label) {
  GtkWidget *self = g_object_new(LUMA_TYPE_FLOATING_MENU, NULL);
  luma_ui_set_accessible_label(self, label != NULL ? label : "Menu");
  return self;
}

static void item_activated(GtkButton *button, gpointer user_data) {
  LumaFloatingMenu *self = LUMA_FLOATING_MENU(user_data);
  LumaMenuRow *row = g_object_get_data(G_OBJECT(button), "luma-menu-row");
  GtkWidget *anchor = self->anchor != NULL ? g_object_ref(self->anchor) : NULL;
  g_autofree char *action = row != NULL ? g_strdup(row->action) : NULL;
  luma_floating_menu_close(self);
  if (anchor != NULL && action != NULL)
    activate_detailed(anchor, action);
  g_clear_object(&anchor);
}

static void add_check(GtkWidget *button);

void luma_floating_menu_add_item(LumaFloatingMenu *self, const char *label, const char *icon, GIcon *gicon,
                                 const char *note, const char *action_name, gboolean selected) {
  luma_floating_menu_add_rich_item(self, label, icon, gicon, note, NULL, action_name, selected);
}

void luma_floating_menu_add_rich_item(LumaFloatingMenu *self, const char *label, const char *icon, GIcon *gicon,
                                      const char *note, const char *description, const char *action_name,
                                      gboolean selected) {
  g_return_if_fail(LUMA_IS_FLOATING_MENU(self));
  g_return_if_fail(label != NULL);
  g_return_if_fail(gicon == NULL || G_IS_ICON(gicon));
  LumaMenuRow *row = luma_menu_row_new(LUMA_MENU_ROW_ITEM, label, icon, gicon, note, action_name, selected);
  g_ptr_array_add(self->rows, row);
  GtkWidget *button = g_object_new(GTK_TYPE_BUTTON, "accessible-role", GTK_ACCESSIBLE_ROLE_MENU_ITEM, NULL);
  gtk_widget_add_css_class(button, "lumaui-menu-item");
  if (description != NULL && *description != '\0')
    gtk_widget_add_css_class(button, "rich");
  if (selected)
    gtk_widget_add_css_class(button, "on");
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
  if (gicon != NULL) {
    GtkWidget *image = gtk_image_new_from_gicon(gicon);
    gtk_widget_add_css_class(image, "lumaui-app-icon");
    gtk_box_append(GTK_BOX(line), image);
  } else if (icon != NULL && *icon != '\0') {
    gtk_box_append(GTK_BOX(line), luma_ui_icon_image(icon, 0));
  }
  GtkWidget *text = gtk_label_new(label);
  gtk_label_set_xalign(GTK_LABEL(text), 0);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_box_append(GTK_BOX(line), text);
  if (note != NULL && *note != '\0') {
    GtkWidget *quiet = gtk_label_new(note);
    gtk_widget_add_css_class(quiet, "lumaui-menu-note");
    gtk_box_append(GTK_BOX(line), quiet);
  }
  if (description != NULL && *description != '\0') {
    GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 1);
    gtk_box_append(GTK_BOX(content), line);
    GtkWidget *detail = gtk_label_new(description);
    gtk_label_set_xalign(GTK_LABEL(detail), 0);
    gtk_label_set_ellipsize(GTK_LABEL(detail), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(detail, "lumaui-menu-description");
    gtk_box_append(GTK_BOX(content), detail);
    gtk_button_set_child(GTK_BUTTON(button), content);
  } else {
    gtk_button_set_child(GTK_BUTTON(button), line);
  }
  g_autofree char *name = description != NULL && *description != '\0'
                             ? g_strdup_printf("%s, %s, %s", label, note != NULL ? note : "", description)
                             : note != NULL && *note != '\0' ? g_strdup_printf("%s, %s", label, note) : g_strdup(label);
  if (self->picker) add_check(button);
  luma_ui_set_accessible_label(button, name);
  g_object_set_data(G_OBJECT(button), "luma-menu-row", row);
  g_signal_connect(button, "clicked", G_CALLBACK(item_activated), self);
  g_ptr_array_add(self->buttons, button);
  gtk_box_append(GTK_BOX(self), button);
}

/* A picker's chosen item ends in a check (v70 .cfpop .crmi svg.i, 15 in ink-2). */
static void add_check(GtkWidget *button) {
  if (g_object_get_data(G_OBJECT(button), "luma-menu-check") != NULL)
    return;
  GtkWidget *check = luma_ui_icon_image("check", 0);
  gtk_widget_add_css_class(check, "lumaui-menu-check");
  gtk_widget_set_visible(check, gtk_widget_has_css_class(button, "on"));
  gtk_box_append(GTK_BOX(gtk_button_get_child(GTK_BUTTON(button))), check);
  g_object_set_data(G_OBJECT(button), "luma-menu-check", check);
}

void luma_floating_menu_set_picker(LumaFloatingMenu *self, gboolean picker) {
  g_return_if_fail(LUMA_IS_FLOATING_MENU(self));
  self->picker = picker;
  luma_ui_set_css_class(GTK_WIDGET(self), "picker", picker);
  for (guint i = 0; i < self->buttons->len; i++) {
    GtkWidget *button = g_ptr_array_index(self->buttons, i);
    if (picker)
      add_check(button);
    GtkWidget *check = g_object_get_data(G_OBJECT(button), "luma-menu-check");
    if (check != NULL)
      gtk_widget_set_visible(check, picker && gtk_widget_has_css_class(button, "on"));
  }
}

void luma_floating_menu_add_heading(LumaFloatingMenu *self, const char *heading) {
  g_return_if_fail(LUMA_IS_FLOATING_MENU(self));
  g_return_if_fail(heading != NULL);
  g_ptr_array_add(self->rows, luma_menu_row_new(LUMA_MENU_ROW_HEADING, heading, NULL, NULL, NULL, NULL, FALSE));
  GtkWidget *label = gtk_label_new(heading);
  gtk_label_set_xalign(GTK_LABEL(label), 0);
  gtk_widget_add_css_class(label, "lumaui-menu-heading");
  gtk_box_append(GTK_BOX(self), label);
}

void luma_floating_menu_add_separator(LumaFloatingMenu *self) {
  g_return_if_fail(LUMA_IS_FLOATING_MENU(self));
  g_ptr_array_add(self->rows, luma_menu_row_new(LUMA_MENU_ROW_SEPARATOR, NULL, NULL, NULL, NULL, NULL, FALSE));
  GtkWidget *rule = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(rule, "lumaui-menu-separator");
  gtk_box_append(GTK_BOX(self), rule);
}

static void catcher_released(GtkGestureClick *gesture G_GNUC_UNUSED, int n G_GNUC_UNUSED, double x G_GNUC_UNUSED,
                             double y G_GNUC_UNUSED, gpointer user_data) {
  luma_floating_menu_close(user_data);
}

static gboolean menu_key(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                         GdkModifierType state G_GNUC_UNUSED, gpointer user_data) {
  LumaFloatingMenu *self = LUMA_FLOATING_MENU(user_data);
  if (keyval == GDK_KEY_Escape) {
    luma_floating_menu_close(self);
    return TRUE;
  }
  if ((keyval == GDK_KEY_Up || keyval == GDK_KEY_Down) && self->buttons->len > 0) {
    GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
    GtkWidget *focus = root != NULL ? gtk_root_get_focus(root) : NULL;
    int index = -1;
    for (guint i = 0; i < self->buttons->len && focus != NULL; i++) {
      GtkWidget *button = g_ptr_array_index(self->buttons, i);
      if (focus == button || gtk_widget_is_ancestor(focus, button)) {
        index = (int)i;
        break;
      }
    }
    int n = (int)self->buttons->len;
    int step = keyval == GDK_KEY_Down ? 1 : -1;
    gtk_widget_grab_focus(g_ptr_array_index(self->buttons, (guint)(((index + step) % n + n) % n)));
    return TRUE;
  }
  return FALSE;
}

static gboolean menu_shown(gpointer user_data) {
  gtk_widget_add_css_class(GTK_WIDGET(user_data), "shown");
  return G_SOURCE_REMOVE;
}

static void menu_drawer_closed(LumaMenuDrawer *drawer G_GNUC_UNUSED, gpointer user_data) {
  LumaFloatingMenu *self = LUMA_FLOATING_MENU(user_data);
  g_clear_weak_pointer(&self->drawer);
  menu_release(self);
}

void luma_floating_menu_popup(LumaFloatingMenu *self, GtkWidget *anchor) {
  g_return_if_fail(LUMA_IS_FLOATING_MENU(self));
  g_return_if_fail(GTK_IS_WIDGET(anchor));
  if (luma_floating_menu_get_is_open(self))
    luma_floating_menu_close(self);
  LumaLayerHost *layer_host = luma_layer_host_window_host(anchor);
  if (layer_host == NULL) {
    g_critical("a floating menu needs an anchor that is inside a window");
    return;
  }
  GtkWidget *host = GTK_WIDGET(layer_host);
  GtkRoot *root = gtk_widget_get_root(anchor);
  g_set_weak_pointer(&self->anchor, anchor);
  /* The open menu keeps itself alive; an app that reuses it keeps a reference. */
  g_object_ref_sink(self);
  self->held = TRUE;
  if (luma_ui_mobile_form_factor() || luma_ui_is_phone(host)) {
    /* v71: on a phone every menu rises from the bar, as its grown panel, when a bar is showing;
     * otherwise it is the drawer in the bar's frame (16 gutter, 34 up, 26 corners). */
    LumaActionCenter *center = luma_action_center_find(anchor);
    if (center != NULL && gtk_widget_get_mapped(GTK_WIDGET(center)) &&
        (g_str_equal(luma_action_center_get_state(center), "bar") ||
         g_str_equal(luma_action_center_get_state(center), "double"))) {
      luma_action_center_grow_rows(center, "menu", self->rows, anchor);
      self->grown_center = center;
      g_object_weak_ref(G_OBJECT(center), grown_center_gone, self);
      self->grown_handler = g_signal_connect(center, "panel-changed", G_CALLBACK(grown_menu_closed), self);
      return;
    }
    g_object_set_data(G_OBJECT(anchor), "luma-menu-picker", GINT_TO_POINTER(self->picker));
    LumaMenuDrawer *drawer = luma_menu_drawer_present_items(anchor, self->rows, NULL);
    if (drawer == NULL) {
      menu_release(self);
      return;
    }
    g_set_weak_pointer(&self->drawer, drawer);
    g_signal_connect_object(drawer, "closed", G_CALLBACK(menu_drawer_closed), self, 0);
    return;
  }
  g_set_weak_pointer(&self->host, host);
  self->catcher = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_hexpand(self->catcher, TRUE);
  gtk_widget_set_vexpand(self->catcher, TRUE);
  GtkGesture *click = gtk_gesture_click_new();
  g_signal_connect_object(click, "released", G_CALLBACK(catcher_released), self, 0);
  gtk_widget_add_controller(self->catcher, GTK_EVENT_CONTROLLER(click));
  luma_layer_host_add_layer(host, self->catcher);
  luma_layer_host_add_layer(host, GTK_WIDGET(self));
  GdkRectangle rect = luma_ui_rect_in(host, anchor, NULL);
  LumaFloatSide prefer = rect.y > gtk_widget_get_height(host) * 0.5 ? LUMA_FLOAT_ABOVE : LUMA_FLOAT_BELOW;
  /* A picker is at least as wide as what it picks for (200 at least) and sits 6 from it (v70 cfPop). */
  int width = self->picker ? MAX(200, rect.width) : 0;
  luma_ui_float_at(host, GTK_WIDGET(self), &rect, prefer, self->align, self->picker ? 6 : 8, 8, -1, width);
  if (root != NULL) {
    self->keys = gtk_event_controller_key_new();
    gtk_event_controller_set_propagation_phase(self->keys, GTK_PHASE_CAPTURE);
    g_signal_connect_object(self->keys, "key-pressed", G_CALLBACK(menu_key), self, 0);
    gtk_widget_add_controller(GTK_WIDGET(root), self->keys);
    g_set_weak_pointer(&self->keys_root, GTK_WIDGET(root));
  }
  luma_ui_on_next_frame(GTK_WIDGET(self), menu_shown, self);
  if (self->buttons->len > 0)
    gtk_widget_grab_focus(g_ptr_array_index(self->buttons, 0));
}

void luma_floating_menu_close(LumaFloatingMenu *self) {
  g_return_if_fail(LUMA_IS_FLOATING_MENU(self));
  if (self->grown_center != NULL) {
    g_object_ref(self);
    luma_action_center_fold(self->grown_center);
    g_object_unref(self);
    return;
  }
  if (self->drawer != NULL) {
    LumaMenuDrawer *drawer = self->drawer;
    g_clear_weak_pointer(&self->drawer);
    g_object_ref(self);
    luma_menu_drawer_close(drawer); /* emits closed, which releases the hold */
    menu_release(self);
    g_object_unref(self);
    return;
  }
  GtkWidget *host = self->host;
  if (host == NULL)
    return;
  g_clear_weak_pointer(&self->host);
  if (self->keys != NULL && self->keys_root != NULL)
    gtk_widget_remove_controller(self->keys_root, self->keys);
  self->keys = NULL;
  g_clear_weak_pointer(&self->keys_root);
  gtk_widget_remove_css_class(GTK_WIDGET(self), "shown");
  GtkWidget *catcher = self->catcher;
  self->catcher = NULL;
  if (catcher != NULL && gtk_widget_get_parent(catcher) == host)
    luma_layer_host_remove_layer(host, catcher);
  if (gtk_widget_get_parent(GTK_WIDGET(self)) == host)
    luma_layer_host_remove_layer(host, GTK_WIDGET(self));
  if (self->anchor != NULL && gtk_widget_get_mapped(self->anchor))
    gtk_widget_grab_focus(self->anchor);
  menu_release(self);
}

gboolean luma_floating_menu_get_is_open(LumaFloatingMenu *self) {
  g_return_val_if_fail(LUMA_IS_FLOATING_MENU(self), FALSE);
  return self->host != NULL || self->drawer != NULL || self->grown_center != NULL;
}
void luma_floating_menu_set_align(LumaFloatingMenu *self, LumaFloatAlign align) {
  g_return_if_fail(LUMA_IS_FLOATING_MENU(self));
  g_return_if_fail(align == LUMA_FLOAT_ALIGN_START || align == LUMA_FLOAT_ALIGN_CENTER ||
                   align == LUMA_FLOAT_ALIGN_END);
  self->align = align;
}
