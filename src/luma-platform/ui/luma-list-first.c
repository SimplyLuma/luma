/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_listfirst.ListFirst (Python, K-NAV): the same tree and classes, styled by
 * luma-appkit-base.css ("LumaUI: List first"). */
#include "luma-list-first.h"
#include "luma-layer-host.h"
#include "luma-ui-private.h"

enum { SIG_SHOWING, SIG_BACK, N_SIGNALS };
static guint signals[N_SIGNALS];

struct _LumaListFirst {
  GtkBox parent_instance;
  GtkWidget *list_page, *detail_page; /* refs */
  GtkWidget *split, *stack, *list_screen, *title_label, *detail_screen, *back_button;
  LumaTitleIsland *island; /* weak */
  gulong island_handler;
  LumaWidthWatch *watch;   /* weak */
  gulong tier_handler;
  GPtrArray *lists;        /* GtkListBox whose activation pushes the page */
  const char *showing;
  gboolean phone, placed, push;
};

G_DEFINE_FINAL_TYPE(LumaListFirst, luma_list_first, GTK_TYPE_BOX)

static void list_first_forget(LumaListFirst *self) {
  if (self->watch != NULL && self->tier_handler != 0)
    g_signal_handler_disconnect(self->watch, self->tier_handler);
  self->tier_handler = 0;
  g_clear_weak_pointer(&self->watch);
  if (self->island != NULL && self->island_handler != 0)
    g_signal_handler_disconnect(self->island, self->island_handler);
  self->island_handler = 0;
  g_clear_weak_pointer(&self->island);
}

static void row_activated(GtkListBox *list G_GNUC_UNUSED, GtkListBoxRow *row G_GNUC_UNUSED, gpointer data) {
  LumaListFirst *self = LUMA_LIST_FIRST(data);
  if (self->push)
    luma_list_first_show_detail(self);
}

static void list_first_dispose(GObject *object) {
  LumaListFirst *self = LUMA_LIST_FIRST(object);
  list_first_forget(self);
  if (self->lists != NULL) {
    for (guint i = 0; i < self->lists->len; i++)
      g_signal_handlers_disconnect_by_func(g_ptr_array_index(self->lists, i), row_activated, self);
    g_clear_pointer(&self->lists, g_ptr_array_unref);
  }
  g_clear_object(&self->list_page);
  g_clear_object(&self->detail_page);
  g_clear_object(&self->split);
  g_clear_object(&self->stack);
  G_OBJECT_CLASS(luma_list_first_parent_class)->dispose(object);
}

static void luma_list_first_class_init(LumaListFirstClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = list_first_dispose;
  /**
   * LumaListFirst::showing:
   * @self: the stack
   * @which: "list" or "detail"
   */
  signals[SIG_SHOWING] = g_signal_new("showing", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                      G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaListFirst::back:
   * @self: the stack
   *
   * Back to the list (Python `on_back`).
   */
  signals[SIG_BACK] = g_signal_new("back", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                   G_TYPE_NONE, 0);
}

static void sync_back(LumaListFirst *self) {
  gtk_widget_set_visible(self->back_button, self->phone && self->island == NULL);
}

static void detach(GtkWidget *page) {
  GtkWidget *parent = gtk_widget_get_parent(page);
  if (parent == NULL)
    return;
  if (GTK_IS_OVERLAY(parent) && gtk_overlay_get_child(GTK_OVERLAY(parent)) == page)
    gtk_overlay_set_child(GTK_OVERLAY(parent), NULL);
  else if (GTK_IS_BOX(parent))
    gtk_box_remove(GTK_BOX(parent), page);
  else
    gtk_widget_unparent(page);
}

/* Move the list and the page into the phone's stack, or side by side. */
static void list_first_place(LumaListFirst *self, gboolean phone) {
  if (phone == self->phone && self->placed)
    return;
  self->placed = TRUE;
  self->phone = phone;
  detach(self->list_page);
  detach(self->detail_page);
  if (phone) {
    if (gtk_widget_get_parent(self->split) == GTK_WIDGET(self))
      gtk_box_remove(GTK_BOX(self), self->split);
    gtk_box_append(GTK_BOX(self->list_screen), self->list_page);
    gtk_overlay_set_child(GTK_OVERLAY(self->detail_screen), self->detail_page);
    if (gtk_widget_get_parent(self->stack) == NULL)
      gtk_box_append(GTK_BOX(self), self->stack);
    gtk_stack_set_transition_duration(GTK_STACK(self->stack), 0);
    gtk_stack_set_visible_child_name(GTK_STACK(self->stack), self->showing);
  } else {
    if (gtk_widget_get_parent(self->stack) == GTK_WIDGET(self))
      gtk_box_remove(GTK_BOX(self), self->stack);
    gtk_box_append(GTK_BOX(self->split), self->list_page);
    gtk_box_append(GTK_BOX(self->split), self->detail_page);
    if (gtk_widget_get_parent(self->split) == NULL)
      gtk_box_append(GTK_BOX(self), self->split);
  }
  luma_ui_set_css_class(GTK_WIDGET(self), "phone", phone);
  sync_back(self);
}

static void tier_changed(LumaWidthWatch *watch G_GNUC_UNUSED, LumaTier tier, gpointer data) {
  list_first_place(LUMA_LIST_FIRST(data), tier == LUMA_TIER_PHONE);
}

static void rooted(GObject *object, GParamSpec *pspec G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  LumaListFirst *self = LUMA_LIST_FIRST(object);
  GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
  if (root == NULL || self->watch != NULL)
    return;
  GtkWidget *anchor = NULL;
  for (GtkWidget *node = gtk_widget_get_parent(GTK_WIDGET(self)); node != NULL && anchor == NULL;
       node = gtk_widget_get_parent(node))
    if (LUMA_IS_LAYER_HOST(node) && g_strcmp0(luma_layer_host_get_layer_name(node), "window") == 0)
      anchor = node;
  LumaWidthWatch *watch = luma_width_watch_get(anchor != NULL ? anchor : GTK_WIDGET(root));
  g_set_weak_pointer(&self->watch, watch);
  self->tier_handler = g_signal_connect(watch, "tier-changed", G_CALLBACK(tier_changed), self);
  if (luma_width_watch_get_width(watch) > 0)
    list_first_place(self, luma_width_watch_get_tier(watch) == LUMA_TIER_PHONE);
}

static void back_clicked(GtkButton *button G_GNUC_UNUSED, gpointer data) {
  luma_list_first_show_list(LUMA_LIST_FIRST(data));
}

static void luma_list_first_init(LumaListFirst *self) {
  luma_ui_install();
  self->showing = "list";
  self->push = TRUE;
  self->lists = g_ptr_array_new();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_vexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-list-first");
  /* A computer: the list and its page side by side (the app's own widths). */
  self->split = g_object_ref_sink(g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_HORIZONTAL, "hexpand",
                                               TRUE, "vexpand", TRUE, NULL));
  gtk_widget_add_css_class(self->split, "lumaui-list-first-split");
  /* A phone: the list full screen under its large title, the page pushed over it. */
  self->stack = g_object_ref_sink(g_object_new(GTK_TYPE_STACK, "hexpand", TRUE, "vexpand", TRUE, "transition-type",
                                               GTK_STACK_TRANSITION_TYPE_SLIDE_LEFT_RIGHT, "transition-duration",
                                               LUMA_UI_LIST_FIRST_PUSH_MS, NULL));
  gtk_widget_add_css_class(self->stack, "lumaui-list-first-stack");
  self->list_screen = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "hexpand", TRUE, "vexpand",
                                   TRUE, NULL);
  gtk_widget_add_css_class(self->list_screen, "lumaui-list-first-list");
  self->title_label = g_object_new(GTK_TYPE_LABEL, "xalign", 0.0f, "ellipsize", PANGO_ELLIPSIZE_END, "accessible-role",
                                   GTK_ACCESSIBLE_ROLE_HEADING, NULL);
  gtk_widget_add_css_class(self->title_label, "lumaui-list-first-title");
  gtk_box_append(GTK_BOX(self->list_screen), self->title_label);
  gtk_stack_add_named(GTK_STACK(self->stack), self->list_screen, "list");
  self->detail_screen = g_object_new(GTK_TYPE_OVERLAY, "hexpand", TRUE, "vexpand", TRUE, NULL);
  gtk_widget_add_css_class(self->detail_screen, "lumaui-list-first-page");
  gtk_stack_add_named(GTK_STACK(self->stack), self->detail_screen, "detail");
  /* The floating ‹ (v71 .phback), until the page has a title island of its own. */
  self->back_button = g_object_new(GTK_TYPE_BUTTON, "halign", GTK_ALIGN_START, "valign", GTK_ALIGN_START,
                                   "tooltip-text", "Back", NULL);
  gtk_widget_add_css_class(self->back_button, "lumaui-list-first-back");
  GtkWidget *back_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_box_append(GTK_BOX(back_row), luma_ui_icon_image("chevron-left", 0));
  gtk_button_set_child(GTK_BUTTON(self->back_button), back_row);
  gtk_widget_remove_css_class(self->back_button, "image-button");
  g_signal_connect(self->back_button, "clicked", G_CALLBACK(back_clicked), self);
  gtk_overlay_add_overlay(GTK_OVERLAY(self->detail_screen), self->back_button);
  gtk_box_append(GTK_BOX(self), self->split);
  g_signal_connect(self, "notify::root", G_CALLBACK(rooted), NULL);
}

static void collect_lists(GtkWidget *widget, GPtrArray *found) {
  if (GTK_IS_LIST_BOX(widget))
    g_ptr_array_add(found, widget);
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c))
    collect_lists(c, found);
}

GtkWidget *luma_list_first_new(GtkWidget *list_page, GtkWidget *detail_page, const char *title) {
  g_return_val_if_fail(GTK_IS_WIDGET(list_page), NULL);
  g_return_val_if_fail(GTK_IS_WIDGET(detail_page), NULL);
  LumaListFirst *self = g_object_new(LUMA_TYPE_LIST_FIRST, NULL);
  self->list_page = g_object_ref_sink(list_page);
  self->detail_page = g_object_ref_sink(detail_page);
  luma_list_first_set_title(self, title);
  /* Before allocation, a split sums both pages and can exceed a phone's
   * first Wayland configure. Start with the smaller layout; measured width
   * selects the split on wider windows. */
  list_first_place(self, TRUE);
  collect_lists(list_page, self->lists);
  for (guint i = 0; i < self->lists->len; i++)
    g_signal_connect(g_ptr_array_index(self->lists, i), "row-activated", G_CALLBACK(row_activated), self);
  return GTK_WIDGET(self);
}

void luma_list_first_set_title(LumaListFirst *self, const char *title) {
  g_return_if_fail(LUMA_IS_LIST_FIRST(self));
  gtk_label_set_label(GTK_LABEL(self->title_label), title != NULL ? title : "");
  gtk_widget_set_visible(self->title_label, title != NULL && *title != '\0');
  g_autofree char *back = title != NULL && *title != '\0' ? g_strdup_printf("Back to %s", title) : g_strdup("Back");
  luma_ui_set_accessible_label(self->back_button, back);
}

void luma_list_first_set_push_on_activate(LumaListFirst *self, gboolean push) {
  g_return_if_fail(LUMA_IS_LIST_FIRST(self));
  self->push = push;
}

static void list_first_show(LumaListFirst *self, const char *which) {
  self->showing = g_str_equal(which, "detail") ? "detail" : "list";
  if (self->phone) {
    gtk_stack_set_transition_duration(GTK_STACK(self->stack),
                                      luma_ui_reduced_motion() ? 0 : LUMA_UI_LIST_FIRST_PUSH_MS);
    gtk_stack_set_visible_child_name(GTK_STACK(self->stack), self->showing);
  }
  sync_back(self);
  g_signal_emit(self, signals[SIG_SHOWING], 0, self->showing);
}

void luma_list_first_show_detail(LumaListFirst *self) {
  g_return_if_fail(LUMA_IS_LIST_FIRST(self));
  list_first_show(self, "detail");
}

void luma_list_first_show_list(LumaListFirst *self) {
  g_return_if_fail(LUMA_IS_LIST_FIRST(self));
  if (g_str_equal(self->showing, "list"))
    return;
  list_first_show(self, "list");
  g_signal_emit(self, signals[SIG_BACK], 0);
}

const char *luma_list_first_get_showing(LumaListFirst *self) {
  g_return_val_if_fail(LUMA_IS_LIST_FIRST(self), NULL);
  return self->showing;
}

gboolean luma_list_first_get_phone(LumaListFirst *self) {
  g_return_val_if_fail(LUMA_IS_LIST_FIRST(self), FALSE);
  return self->phone;
}

static void island_lead(LumaTitleIsland *island G_GNUC_UNUSED, gpointer data) {
  LumaListFirst *self = LUMA_LIST_FIRST(data);
  if (self->phone)
    luma_list_first_show_list(self);
}

void luma_list_first_attach_island(LumaListFirst *self, LumaTitleIsland *island) {
  g_return_if_fail(LUMA_IS_LIST_FIRST(self));
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(island));
  if (self->island != NULL && self->island_handler != 0)
    g_signal_handler_disconnect(self->island, self->island_handler);
  g_set_weak_pointer(&self->island, island);
  self->island_handler = g_signal_connect(island, "lead", G_CALLBACK(island_lead), self);
  sync_back(self);
}
