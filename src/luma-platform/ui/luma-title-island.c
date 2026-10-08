/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_island.TitleIsland (Python, K-NAV): v71's lNavTtl / .fisl / .crisl. The widget tree
 * and CSS classes are Python's, node for node, so luma-appkit-base.css styles both. */
#include "luma-title-island.h"
#include <adwaita.h>
#include "luma-layer-host.h"
#include "luma-ui-private.h"

G_DEFINE_ENUM_TYPE(LumaTitleIslandLead, luma_title_island_lead,
                   G_DEFINE_ENUM_VALUE(LUMA_TITLE_ISLAND_LEAD_MENU, "menu"),
                   G_DEFINE_ENUM_VALUE(LUMA_TITLE_ISLAND_LEAD_BACK, "back"),
                   G_DEFINE_ENUM_VALUE(LUMA_TITLE_ISLAND_LEAD_NONE, "none"))
G_DEFINE_ENUM_TYPE(LumaTitleIslandGrows, luma_title_island_grows,
                   G_DEFINE_ENUM_VALUE(LUMA_TITLE_ISLAND_GROWS_AUTO, "auto"),
                   G_DEFINE_ENUM_VALUE(LUMA_TITLE_ISLAND_GROWS_MENU, "menu"),
                   G_DEFINE_ENUM_VALUE(LUMA_TITLE_ISLAND_GROWS_DETAILS, "details"))

enum { SIG_LEAD, SIG_TITLE, SIG_GROWN, N_SIGNALS };
static guint signals[N_SIGNALS];

struct _LumaTitleIsland {
  GtkBox parent_instance;
  LumaTitleIslandLead lead;
  gboolean lead_merged, creative;
  char *lead_label;
  char *lead_icon;
  GtkWidget *row, *lead_button, *title_button, *inner, *faces_slot, *status_dot, *text;
  GtkWidget *title_label, *subtitle_label, *subtitle_row, *presence_dot, *disclosure, *trailing, *revealer, *panel;
  GtkWidget *panel_child;   /* what it has grown into */
  GtkWidget *home;          /* weak: where a borrowed widget lives (a box or revealer) */
  GtkWidget *home_previous; /* weak: its sibling before it there */
  gboolean was_island;
  int home_width, home_height;
  GPtrArray *row_lists;     /* GtkListBox whose activation folds a grown menu */
  GtkWidget *scrim;         /* weak */
  GtkWidget *host;          /* weak: the overlay it floats over */
  LumaWidthWatch *watch;    /* weak */
  gulong tier_handler;
  gulong width_handler;
  gboolean grown, phone_only;
  LumaTitleIslandGrows grows;
  GtkWidget *grow_widget;   /* a widget to grow into (ref) */
  LumaTitleIslandGrowFunc grow_func;
  gpointer grow_data;
  GDestroyNotify grow_destroy;
};

G_DEFINE_FINAL_TYPE(LumaTitleIsland, luma_title_island, GTK_TYPE_BOX)

static void island_release_child(LumaTitleIsland *self);
static void island_folded(LumaTitleIsland *self);

static void island_clear_grow(LumaTitleIsland *self) {
  if (self->grow_destroy != NULL && self->grow_data != NULL)
    self->grow_destroy(self->grow_data);
  self->grow_func = NULL;
  self->grow_data = NULL;
  self->grow_destroy = NULL;
  g_clear_object(&self->grow_widget);
}

static void island_forget_watch(LumaTitleIsland *self) {
  if (self->watch != NULL && self->tier_handler != 0)
    g_signal_handler_disconnect(self->watch, self->tier_handler);
  if (self->watch != NULL && self->width_handler != 0)
    g_signal_handler_disconnect(self->watch, self->width_handler);
  self->width_handler = 0;
  self->tier_handler = 0;
  g_clear_weak_pointer(&self->watch);
}

static void luma_title_island_dispose(GObject *object) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(object);
  island_forget_watch(self);
  if (self->scrim != NULL && gtk_widget_get_parent(self->scrim) != NULL)
    gtk_widget_unparent(self->scrim);
  g_clear_weak_pointer(&self->scrim);
  g_clear_weak_pointer(&self->host);
  if (self->row_lists != NULL && self->panel != NULL)
    island_release_child(self);
  self->panel = NULL;
  g_clear_weak_pointer(&self->home);
  g_clear_weak_pointer(&self->home_previous);
  g_clear_pointer(&self->row_lists, g_ptr_array_unref);
  island_clear_grow(self);
  G_OBJECT_CLASS(luma_title_island_parent_class)->dispose(object);
}

static void luma_title_island_finalize(GObject *object) {
  g_free(LUMA_TITLE_ISLAND(object)->lead_label);
  g_free(LUMA_TITLE_ISLAND(object)->lead_icon);
  G_OBJECT_CLASS(luma_title_island_parent_class)->finalize(object);
}

static void luma_title_island_class_init(LumaTitleIslandClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_title_island_dispose;
  G_OBJECT_CLASS(klass)->finalize = luma_title_island_finalize;
  /**
   * LumaTitleIsland::lead:
   * @self: the island
   *
   * The lead was pressed while folded: Back (go up one level), or ☰ with nothing to grow into.
   */
  signals[SIG_LEAD] = g_signal_new("lead", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                   G_TYPE_NONE, 0);
  /**
   * LumaTitleIsland::title:
   * @self: the island
   *
   * The title was pressed and there is nothing to grow into (Python `on_title`).
   */
  signals[SIG_TITLE] = g_signal_new("title", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                    G_TYPE_NONE, 0);
  /**
   * LumaTitleIsland::grown:
   * @self: the island
   * @grown: %TRUE when it grew, %FALSE when it folded
   */
  signals[SIG_GROWN] = g_signal_new("grown", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                    G_TYPE_NONE, 1, G_TYPE_BOOLEAN);
}

static gboolean island_has_grow(LumaTitleIsland *self) {
  return self->grow_func != NULL || self->grow_widget != NULL;
}

static LumaTitleIslandGrows island_kind(LumaTitleIsland *self) {
  if (self->grows != LUMA_TITLE_ISLAND_GROWS_AUTO)
    return self->grows;
  return self->lead == LUMA_TITLE_ISLAND_LEAD_MENU ? LUMA_TITLE_ISLAND_GROWS_MENU : LUMA_TITLE_ISLAND_GROWS_DETAILS;
}

static void island_describe(LumaTitleIsland *self) {
  const char *title = gtk_label_get_label(GTK_LABEL(self->title_label));
  g_autofree char *spoken =
      island_has_grow(self) ? g_strdup_printf("%s. %s", title,
                                              island_kind(self) == LUMA_TITLE_ISLAND_GROWS_MENU ? "Show the places"
                                                                                                 : "Show the details")
                            : g_strdup(title);
  luma_ui_set_accessible_label(self->title_button, spoken);
  const char *subtitle = gtk_label_get_label(GTK_LABEL(self->subtitle_label));
  gtk_widget_set_tooltip_text(self->title_button, subtitle != NULL && *subtitle != '\0' ? subtitle : NULL);
}

static void island_sync_lead(LumaTitleIsland *self) {
  /* ☰ becomes ✕ while grown into the places; ‹ stays ‹ (it folds first). */
  gboolean closing = self->grown && self->lead == LUMA_TITLE_ISLAND_LEAD_MENU;
  const char *glyph = closing                                     ? "x"
                      : self->lead == LUMA_TITLE_ISLAND_LEAD_MENU ? (self->lead_icon != NULL ? self->lead_icon : "menu")
                      : self->lead == LUMA_TITLE_ISLAND_LEAD_BACK ? "chevron-left"
                                                                  : NULL;
  if (glyph != NULL)
    gtk_button_set_child(GTK_BUTTON(self->lead_button), luma_ui_icon_image(glyph, 0));
  const char *label = closing ? "Close" : self->lead_label;
  /* ☰ beside its title is part of the title's control; it names itself only as ✕. */
  gtk_widget_set_tooltip_text(self->lead_button, closing || !self->lead_merged ? label : NULL);
  luma_ui_set_accessible_label(self->lead_button, label);
  if (self->lead == LUMA_TITLE_ISLAND_LEAD_MENU || closing)
    gtk_accessible_update_state(GTK_ACCESSIBLE(self->lead_button), GTK_ACCESSIBLE_STATE_EXPANDED, self->grown, -1);
}

static void lead_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data);

static GtkWidget *island_make_lead(LumaTitleIsland *self, gboolean merged) {
  /* Beside a title that does the same (☰), the lead is part of that one control for assistive
   * technology and the keyboard; Back is a control of its own. */
  GtkWidget *button = g_object_new(GTK_TYPE_BUTTON, "accessible-role",
                                   merged ? GTK_ACCESSIBLE_ROLE_PRESENTATION : GTK_ACCESSIBLE_ROLE_BUTTON,
                                   "focusable", !merged, NULL);
  gtk_widget_add_css_class(button, "lumaui-title-island-lead");
  gtk_widget_remove_css_class(button, "image-button");
  g_signal_connect(button, "clicked", G_CALLBACK(lead_clicked), self);
  self->lead_merged = merged;
  return button;
}

static void island_size(LumaTitleIsland *self) {
  int width = luma_ui_window_width(GTK_WIDGET(self));
  int height = self->host != NULL ? gtk_widget_get_height(self->host) : 0;
  if (height <= 0) {
    GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
    height = root != NULL ? gtk_widget_get_height(GTK_WIDGET(root)) : 0;
  }
  if (width <= 0)
    return;
  int left = self->creative ? LUMA_UI_TITLE_ISLAND_CREATIVE_LEFT : LUMA_UI_TITLE_ISLAND_LEFT;
  int side = 2 * left, grown, foot;
  if (island_kind(self) == LUMA_TITLE_ISLAND_GROWS_MENU) {
    grown = MIN(LUMA_UI_TITLE_ISLAND_MENU_WIDTH, width - side);
    foot = LUMA_UI_TITLE_ISLAND_MENU_FOOT;
  } else {
    gboolean phone = luma_ui_tier_for_width(width) == LUMA_TIER_PHONE;
    grown = phone ? width - side : MIN(LUMA_UI_TITLE_ISLAND_DETAILS_WIDTH, width - side);
    foot = LUMA_UI_TITLE_ISLAND_DETAILS_FOOT;
  }
  /* A size request counts the CSS margin, and floating, the island's 12 px margin is CSS (.floating):
   * ask for that too, so the glass itself is min(320, w − 24) wide, as v71 measures it. */
  int margin = gtk_widget_has_css_class(GTK_WIDGET(self), "floating") ? left : 0;
  gtk_widget_set_size_request(GTK_WIDGET(self), MAX(0, grown) + margin, -1);
  if (height > 0)
    gtk_scrolled_window_set_max_content_height(
        GTK_SCROLLED_WINDOW(self->panel),
        MAX(120, height - LUMA_UI_TITLE_ISLAND_TOP - LUMA_UI_TITLE_ISLAND_HEIGHT - foot));
}

static void island_size_after_paint(GdkFrameClock *clock, gpointer user_data) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(user_data);
  g_signal_handlers_disconnect_by_func(clock, island_size_after_paint, self);
  if (self->grown) island_size(self);
}

static void island_queue_size(LumaTitleIsland *self) {
  GdkFrameClock *clock = gtk_widget_get_frame_clock(GTK_WIDGET(self));
  if (!self->grown || clock == NULL) return;
  island_size(self);
  g_signal_handlers_disconnect_by_func(clock, island_size_after_paint, self);
  g_signal_connect_object(clock, "after-paint", G_CALLBACK(island_size_after_paint), self, G_CONNECT_AFTER);
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

static gboolean scrim_shown(gpointer data) {
  gtk_widget_add_css_class(GTK_WIDGET(data), "shown");
  return G_SOURCE_REMOVE;
}

static gboolean scrim_gone(gpointer data) {
  GtkWidget *scrim = data;
  if (gtk_widget_get_parent(scrim) != NULL)
    gtk_widget_unparent(scrim);
  g_object_unref(scrim);
  return G_SOURCE_REMOVE;
}

static void scrim_released(GtkGestureClick *gesture G_GNUC_UNUSED, int n G_GNUC_UNUSED, double x G_GNUC_UNUSED,
                           double y G_GNUC_UNUSED, gpointer user_data) {
  luma_title_island_fold(LUMA_TITLE_ISLAND(user_data));
}

static void island_show_scrim(LumaTitleIsland *self, gboolean on) {
  if (!on) {
    if (self->scrim != NULL) {
      GtkWidget *scrim = g_object_ref(self->scrim);
      g_clear_weak_pointer(&self->scrim);
      gtk_widget_remove_css_class(scrim, "shown");
      g_timeout_add(MAX(1u, luma_ui_duration(LUMA_UI_MOTION_SCRIM_MS, FALSE)) + 20, scrim_gone, scrim);
    }
    return;
  }
  if (self->host == NULL || self->scrim != NULL || gtk_widget_get_parent(GTK_WIDGET(self)) != self->host)
    return;
  /* Under the island, over the page: a tap beside the grown island folds it. */
  GtkWidget *scrim = g_object_new(GTK_TYPE_BOX, "hexpand", TRUE, "vexpand", TRUE, "can-focus", FALSE, NULL);
  gtk_widget_add_css_class(scrim, "lumaui-title-island-scrim");
  GtkGesture *click = gtk_gesture_click_new();
  g_signal_connect_object(click, "released", G_CALLBACK(scrim_released), self, 0);
  gtk_widget_add_controller(scrim, GTK_EVENT_CONTROLLER(click));
  gtk_widget_insert_before(scrim, self->host, GTK_WIDGET(self));
  g_set_weak_pointer(&self->scrim, scrim);
  luma_ui_on_next_frame(scrim, scrim_shown, scrim);
}

static void row_activated_fold(GtkListBox *list G_GNUC_UNUSED, GtkListBoxRow *row G_GNUC_UNUSED, gpointer data) {
  luma_title_island_fold(LUMA_TITLE_ISLAND(data));
}

static void collect_lists(GtkWidget *widget, GPtrArray *found) {
  if (GTK_IS_LIST_BOX(widget))
    g_ptr_array_add(found, widget);
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c))
    collect_lists(c, found);
}

static void island_take(LumaTitleIsland *self, GtkWidget *child) {
  GtkWidget *parent = gtk_widget_get_parent(child);
  /* Adwaita owns an internal bin around each split-view pane. Borrow via
   * its public sidebar property, never by detaching that private bin. */
  GtkWidget *split = parent;
  while (split != NULL && !ADW_IS_OVERLAY_SPLIT_VIEW(split))
    split = gtk_widget_get_parent(split);
  if (split != NULL && adw_overlay_split_view_get_sidebar(ADW_OVERLAY_SPLIT_VIEW(split)) == child)
    parent = split;
  g_object_ref(child);
  if (parent != NULL && parent != self->panel && gtk_widget_get_parent(parent) != self->panel) {
    /* A sidebar that lives in the page: borrow it, put it back on fold. */
    if (ADW_IS_OVERLAY_SPLIT_VIEW(parent)) {
      g_set_weak_pointer(&self->home, parent);
      adw_overlay_split_view_set_sidebar(ADW_OVERLAY_SPLIT_VIEW(parent), NULL);
    } else if (GTK_IS_REVEALER(parent)) {
      g_set_weak_pointer(&self->home, parent);
      gtk_revealer_set_child(GTK_REVEALER(parent), NULL);
    } else if (GTK_IS_BOX(parent)) {
      g_set_weak_pointer(&self->home, parent);
      g_set_weak_pointer(&self->home_previous, gtk_widget_get_prev_sibling(child));
      gtk_box_remove(GTK_BOX(parent), child);
    } else {
      g_critical("a title island borrows a widget that lives in a GtkBox or a GtkRevealer");
      g_object_unref(child);
      return;
    }
    self->was_island = gtk_widget_has_css_class(child, "luma-island");
    gtk_widget_remove_css_class(child, "luma-island");
    gtk_widget_get_size_request(child, &self->home_width, &self->home_height);
    gtk_widget_set_size_request(child, -1, self->home_height);
    gtk_widget_set_visible(child, TRUE);
  }
  self->panel_child = child;
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->panel), child);
  g_object_unref(child);
  if (island_kind(self) == LUMA_TITLE_ISLAND_GROWS_MENU) { /* picking a place folds it */
    collect_lists(child, self->row_lists);
    for (guint i = 0; i < self->row_lists->len; i++)
      g_signal_connect(g_ptr_array_index(self->row_lists, i), "row-activated", G_CALLBACK(row_activated_fold), self);
  }
}

static void island_release_child(LumaTitleIsland *self) {
  for (guint i = 0; i < self->row_lists->len; i++)
    g_signal_handlers_disconnect_by_func(g_ptr_array_index(self->row_lists, i), row_activated_fold, self);
  g_ptr_array_set_size(self->row_lists, 0);
  GtkWidget *child = self->panel_child;
  self->panel_child = NULL;
  if (child == NULL)
    return;
  g_object_ref(child);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->panel), NULL);
  GtkWidget *home = self->home;
  if (home != NULL) {
    if (self->was_island)
      gtk_widget_add_css_class(child, "luma-island");
    gtk_widget_set_size_request(child, self->home_width, self->home_height);
    if (ADW_IS_OVERLAY_SPLIT_VIEW(home))
      adw_overlay_split_view_set_sidebar(ADW_OVERLAY_SPLIT_VIEW(home), child);
    else if (GTK_IS_REVEALER(home))
      gtk_revealer_set_child(GTK_REVEALER(home), child);
    else
      gtk_box_insert_child_after(GTK_BOX(home), child, self->home_previous);
  }
  g_clear_weak_pointer(&self->home);
  g_clear_weak_pointer(&self->home_previous);
  g_object_unref(child);
}

gboolean luma_title_island_grow_into(LumaTitleIsland *self, GtkWidget *widget) {
  g_return_val_if_fail(LUMA_IS_TITLE_ISLAND(self), FALSE);
  g_return_val_if_fail(widget == NULL || GTK_IS_WIDGET(widget), FALSE);
  GtkWidget *target = widget;
  if (target == NULL && self->grow_widget != NULL)
    target = self->grow_widget;
  else if (target == NULL && self->grow_func != NULL)
    target = self->grow_func(self, self->grow_data);
  if (target == NULL)
    return FALSE;
  /* Reparenting an ancestor here makes GTK traverse a cyclic widget tree. */
  if (target == GTK_WIDGET(self) || gtk_widget_is_ancestor(GTK_WIDGET(self), target))
    return FALSE;
  g_object_ref_sink(target);
  if (self->grown)
    island_release_child(self);
  island_take(self, target);
  g_object_unref(target);
  self->grown = TRUE;
  gtk_widget_add_css_class(GTK_WIDGET(self), "grown");
  luma_ui_set_css_class(GTK_WIDGET(self), "menu", island_kind(self) == LUMA_TITLE_ISLAND_GROWS_MENU);
  island_size(self);
  island_queue_size(self);
  island_show_scrim(self, TRUE);
  gtk_revealer_set_transition_duration(GTK_REVEALER(self->revealer), luma_ui_duration(LUMA_UI_MOTION_GROW_MS, FALSE));
  gtk_revealer_set_reveal_child(GTK_REVEALER(self->revealer), TRUE);
  island_sync_lead(self);
  g_signal_emit(self, signals[SIG_GROWN], 0, TRUE);
  return TRUE;
}

void luma_title_island_fold(LumaTitleIsland *self) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  if (!self->grown)
    return;
  self->grown = FALSE;
  gtk_revealer_set_transition_duration(GTK_REVEALER(self->revealer),
                                       luma_ui_duration(LUMA_UI_MOTION_GROW_MS, FALSE) / 2);
  gtk_revealer_set_reveal_child(GTK_REVEALER(self->revealer), FALSE);
  island_show_scrim(self, FALSE);
  gtk_widget_remove_css_class(GTK_WIDGET(self), "scrolled");
  island_sync_lead(self);
  if (gtk_revealer_get_transition_duration(GTK_REVEALER(self->revealer)) == 0 ||
      !gtk_widget_get_mapped(GTK_WIDGET(self)))
    island_folded(self);
  g_signal_emit(self, signals[SIG_GROWN], 0, FALSE);
}

static void island_folded(LumaTitleIsland *self) {
  if (self->grown)
    return;
  gtk_widget_remove_css_class(GTK_WIDGET(self), "grown");
  gtk_widget_remove_css_class(GTK_WIDGET(self), "menu");
  gtk_widget_set_size_request(GTK_WIDGET(self), -1, -1);
  island_release_child(self);
}

static void revealed(GObject *revealer G_GNUC_UNUSED, GParamSpec *pspec G_GNUC_UNUSED, gpointer user_data) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(user_data);
  if (!self->grown && !gtk_revealer_get_child_revealed(GTK_REVEALER(self->revealer)))
    island_folded(self);
}

void luma_title_island_toggle(LumaTitleIsland *self) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  if (self->grown)
    luma_title_island_fold(self);
  else
    luma_title_island_grow_into(self, NULL);
}

static void lead_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(user_data);
  if (self->grown) {
    luma_title_island_fold(self);
    return;
  }
  if (self->lead == LUMA_TITLE_ISLAND_LEAD_MENU && island_has_grow(self)) {
    luma_title_island_grow_into(self, NULL);
    return;
  }
  g_signal_emit(self, signals[SIG_LEAD], 0);
}

static void title_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(user_data);
  if (self->grown)
    luma_title_island_fold(self);
  else if (island_has_grow(self))
    luma_title_island_grow_into(self, NULL);
  else if (self->lead == LUMA_TITLE_ISLAND_LEAD_MENU ||
           (self->creative && self->lead == LUMA_TITLE_ISLAND_LEAD_BACK))
    lead_clicked(NULL, self);
  else
    g_signal_emit(self, signals[SIG_TITLE], 0);
}

static void panel_scrolled(GtkAdjustment *adjustment, gpointer user_data) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(user_data);
  /* Once the list scrolls, the header row casts a soft shadow over it, like a sticky header. */
  luma_ui_set_css_class(GTK_WIDGET(self), "scrolled", self->grown && gtk_adjustment_get_value(adjustment) > 2);
}

static gboolean island_key(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                           GdkModifierType state G_GNUC_UNUSED, gpointer user_data) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(user_data);
  if (keyval == GDK_KEY_Escape && self->grown) {
    luma_title_island_fold(self);
    return TRUE;
  }
  return FALSE;
}

/* ── tiers ── */

static void island_tiered(LumaTitleIsland *self, LumaTier tier) {
  luma_ui_set_css_class(GTK_WIDGET(self), "phone", tier == LUMA_TIER_PHONE);
  /* Narrow desktop windows retain the same floating surface as handheld layouts. */
  luma_ui_set_css_class(GTK_WIDGET(self), "flat", tier != LUMA_TIER_PHONE && !luma_ui_mobile_form_factor());
  if (self->phone_only) {
    if (tier != LUMA_TIER_PHONE && self->grown) {
      luma_title_island_fold(self);
      island_folded(self);
    }
    /* Preserve application-owned visible across responsive tier changes. */
    gtk_widget_set_child_visible(GTK_WIDGET(self), tier == LUMA_TIER_PHONE);
  }
  if (self->grown)
    island_size(self);
}

static void island_width_changed(LumaWidthWatch *watch G_GNUC_UNUSED, GParamSpec *pspec G_GNUC_UNUSED, gpointer user_data) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(user_data);
  if (self->grown)
    island_queue_size(self);
}

static void tier_changed(LumaWidthWatch *watch G_GNUC_UNUSED, LumaTier tier, gpointer user_data) {
  island_tiered(LUMA_TITLE_ISLAND(user_data), tier);
}

static GtkWidget *island_tier_anchor(LumaTitleIsland *self) {
  for (GtkWidget *node = gtk_widget_get_parent(GTK_WIDGET(self)); node != NULL; node = gtk_widget_get_parent(node))
    if (LUMA_IS_LAYER_HOST(node) && g_strcmp0(luma_layer_host_get_layer_name(node), "window") == 0)
      return node;
  GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
  return root != NULL ? GTK_WIDGET(root) : GTK_WIDGET(self);
}

static void island_rooted(GObject *object, GParamSpec *pspec G_GNUC_UNUSED, gpointer user_data G_GNUC_UNUSED) {
  LumaTitleIsland *self = LUMA_TITLE_ISLAND(object);
  island_forget_watch(self);
  if (gtk_widget_get_root(GTK_WIDGET(self)) == NULL)
    return;
  LumaWidthWatch *watch = luma_width_watch_get(island_tier_anchor(self));
  g_set_weak_pointer(&self->watch, watch);
  self->tier_handler = g_signal_connect(watch, "tier-changed", G_CALLBACK(tier_changed), self);
  self->width_handler = g_signal_connect(watch, "notify::width", G_CALLBACK(island_width_changed), self);
  if (luma_width_watch_get_width(watch) > 0)
    island_tiered(self, luma_width_watch_get_tier(watch));
}

static void luma_title_island_init(LumaTitleIsland *self) {
  luma_ui_install();
  self->phone_only = TRUE;
  self->row_lists = g_ptr_array_new();
  self->lead_label = g_strdup("Places");
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_set_halign(GTK_WIDGET(self), GTK_ALIGN_START);
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_START);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-title-island");
  gtk_widget_set_name(GTK_WIDGET(self), "lumaui-title-island");
  gtk_widget_set_overflow(GTK_WIDGET(self), GTK_OVERFLOW_HIDDEN);

  /* The row: lead | title (and faces) | trailing buttons. It stays the header when grown. */
  self->row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->row, "lumaui-title-island-row");
  gtk_box_append(GTK_BOX(self), self->row);
  self->lead_button = island_make_lead(self, TRUE);
  gtk_box_append(GTK_BOX(self->row), self->lead_button);

  self->title_button = g_object_new(GTK_TYPE_BUTTON, "hexpand", FALSE, NULL);
  gtk_widget_add_css_class(self->title_button, "lumaui-title-island-title");
  g_signal_connect(self->title_button, "clicked", G_CALLBACK(title_clicked), self);
  self->inner = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->inner, "lumaui-title-island-inner");
  self->faces_slot = g_object_new(GTK_TYPE_BOX, "valign", GTK_ALIGN_CENTER, "visible", FALSE, NULL);
  gtk_widget_add_css_class(self->faces_slot, "lumaui-title-island-faces");
  gtk_box_append(GTK_BOX(self->inner), self->faces_slot);
  self->status_dot = g_object_new(GTK_TYPE_BOX, "valign", GTK_ALIGN_CENTER, "visible", FALSE, NULL);
  gtk_widget_add_css_class(self->status_dot, "lumaui-title-island-status");
  gtk_box_append(GTK_BOX(self->inner), self->status_dot);
  self->text = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "valign", GTK_ALIGN_CENTER, NULL);
  gtk_widget_add_css_class(self->text, "lumaui-title-island-text");
  self->title_label = g_object_new(GTK_TYPE_LABEL, "xalign", 0.0f, "ellipsize", PANGO_ELLIPSIZE_END,
                                   "single-line-mode", TRUE, NULL);
  gtk_widget_add_css_class(self->title_label, "lumaui-title-island-label");
  self->subtitle_label = g_object_new(GTK_TYPE_LABEL, "xalign", 0.0f, "ellipsize", PANGO_ELLIPSIZE_END,
                                      "single-line-mode", TRUE, NULL);
  gtk_widget_add_css_class(self->subtitle_label, "lumaui-title-island-subtitle");
  gtk_box_append(GTK_BOX(self->text), self->title_label);
  self->subtitle_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->subtitle_row, "lumaui-title-island-subtitle-row");
  self->presence_dot = g_object_new(GTK_TYPE_BOX, "valign", GTK_ALIGN_CENTER, "visible", FALSE, NULL);
  gtk_widget_add_css_class(self->presence_dot, "lumaui-title-island-presence");
  gtk_box_append(GTK_BOX(self->subtitle_row), self->presence_dot);
  gtk_box_append(GTK_BOX(self->subtitle_row), self->subtitle_label);
  gtk_box_append(GTK_BOX(self->text), self->subtitle_row);
  gtk_box_append(GTK_BOX(self->inner), self->text);
  self->disclosure = luma_ui_icon_image("chevron-down", 0);
  gtk_widget_add_css_class(self->disclosure, "lumaui-title-island-disclosure");
  gtk_widget_set_valign(self->disclosure, GTK_ALIGN_CENTER);
  gtk_widget_set_visible(self->disclosure, FALSE);
  gtk_box_append(GTK_BOX(self->inner), self->disclosure);
  gtk_button_set_child(GTK_BUTTON(self->title_button), self->inner);
  gtk_box_append(GTK_BOX(self->row), self->title_button);

  self->trailing = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->trailing, "lumaui-title-island-trailing");
  gtk_widget_set_visible(self->trailing, FALSE);
  gtk_box_append(GTK_BOX(self->row), self->trailing);

  /* The panel the island grows into, inside the same glass. */
  self->revealer = g_object_new(GTK_TYPE_REVEALER, "transition-type", GTK_REVEALER_TRANSITION_TYPE_SLIDE_DOWN,
                                "transition-duration", luma_ui_duration(LUMA_UI_MOTION_GROW_MS, FALSE),
                                "reveal-child", FALSE, NULL);
  gtk_widget_add_css_class(self->revealer, "lumaui-title-island-revealer");
  self->panel = g_object_new(GTK_TYPE_SCROLLED_WINDOW, "hscrollbar-policy", GTK_POLICY_NEVER,
                             "propagate-natural-height", TRUE, NULL);
  gtk_widget_add_css_class(self->panel, "lumaui-title-island-panel");
  g_signal_connect(gtk_scrolled_window_get_vadjustment(GTK_SCROLLED_WINDOW(self->panel)), "value-changed",
                   G_CALLBACK(panel_scrolled), self);
  gtk_revealer_set_child(GTK_REVEALER(self->revealer), self->panel);
  gtk_box_append(GTK_BOX(self), self->revealer);
  g_signal_connect(self->revealer, "notify::child-revealed", G_CALLBACK(revealed), self);

  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(island_key), self);
  gtk_widget_add_controller(GTK_WIDGET(self), keys);
  g_signal_connect(self, "notify::root", G_CALLBACK(island_rooted), NULL);
}

GtkWidget *luma_title_island_new(LumaTitleIslandLead lead, const char *title, const char *subtitle) {
  LumaTitleIsland *self = g_object_new(LUMA_TYPE_TITLE_ISLAND, NULL);
  luma_title_island_set_lead(self, lead, NULL);
  luma_title_island_set_title(self, title != NULL ? title : "", subtitle != NULL ? subtitle : "");
  return GTK_WIDGET(self);
}

void luma_title_island_set_title(LumaTitleIsland *self, const char *title, const char *subtitle) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_return_if_fail(title != NULL);
  gtk_label_set_label(GTK_LABEL(self->title_label), title);
  if (subtitle != NULL) {
    gtk_label_set_label(GTK_LABEL(self->subtitle_label), subtitle);
    gtk_widget_set_visible(self->subtitle_label, *subtitle != '\0');
  }
  gtk_widget_set_visible(self->subtitle_row, gtk_widget_get_visible(self->subtitle_label) ||
                           gtk_widget_get_visible(self->presence_dot));
  island_describe(self);
}

void luma_title_island_set_subtitle(LumaTitleIsland *self, const char *subtitle) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_autofree char *title = g_strdup(gtk_label_get_label(GTK_LABEL(self->title_label)));
  luma_title_island_set_title(self, title, subtitle != NULL ? subtitle : "");
}

const char *luma_title_island_get_title(LumaTitleIsland *self) {
  g_return_val_if_fail(LUMA_IS_TITLE_ISLAND(self), NULL);
  return gtk_label_get_label(GTK_LABEL(self->title_label));
}

const char *luma_title_island_get_subtitle(LumaTitleIsland *self) {
  g_return_val_if_fail(LUMA_IS_TITLE_ISLAND(self), NULL);
  return gtk_label_get_label(GTK_LABEL(self->subtitle_label));
}

void luma_title_island_set_lead_icon(LumaTitleIsland *self, const char *icon) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_return_if_fail(icon == NULL || *icon != '\0');
  g_free(self->lead_icon);
  self->lead_icon = g_strdup(icon);
  island_sync_lead(self);
}

void luma_title_island_set_lead(LumaTitleIsland *self, LumaTitleIslandLead lead, const char *label) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  self->lead = lead;
  gboolean merged = lead == LUMA_TITLE_ISLAND_LEAD_MENU ||
                    (self->creative && lead == LUMA_TITLE_ISLAND_LEAD_BACK);
  if (merged != self->lead_merged && gtk_widget_get_parent(self->lead_button) == self->row) {
    /* ☰ and the title are one control (v71 lNavTtl's one button); ‹ is its own. */
    GtkWidget *button = island_make_lead(self, merged);
    gtk_box_insert_child_after(GTK_BOX(self->row), button, self->lead_button);
    gtk_box_remove(GTK_BOX(self->row), self->lead_button);
    self->lead_button = button;
  }
  self->lead_merged = merged;
  g_free(self->lead_label);
  self->lead_label = g_strdup(label != NULL                              ? label
                              : lead == LUMA_TITLE_ISLAND_LEAD_MENU ? "Places"
                              : lead == LUMA_TITLE_ISLAND_LEAD_BACK ? "Back"
                                                                    : "");
  gtk_widget_set_visible(self->lead_button, lead != LUMA_TITLE_ISLAND_LEAD_NONE);
  luma_ui_set_css_class(GTK_WIDGET(self), "no-lead", lead == LUMA_TITLE_ISLAND_LEAD_NONE);
  island_sync_lead(self);
  island_describe(self);
}

LumaTitleIslandLead luma_title_island_get_lead(LumaTitleIsland *self) {
  g_return_val_if_fail(LUMA_IS_TITLE_ISLAND(self), LUMA_TITLE_ISLAND_LEAD_MENU);
  return self->lead;
}

void luma_title_island_set_faces(LumaTitleIsland *self, GtkWidget *faces) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_return_if_fail(faces == NULL || GTK_IS_WIDGET(faces));
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(self->faces_slot)) != NULL)
    gtk_box_remove(GTK_BOX(self->faces_slot), child);
  if (faces != NULL)
    gtk_box_append(GTK_BOX(self->faces_slot), faces);
  gtk_widget_set_visible(self->faces_slot, faces != NULL);
  luma_ui_set_css_class(GTK_WIDGET(self), "has-faces", faces != NULL);
}

void luma_title_island_set_status(LumaTitleIsland *self, const char *status) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  if (status != NULL && !g_str_equal(status, "record") && !g_str_equal(status, "paused") && !g_str_equal(status, "here")) {
    g_critical("a title island's status is \"record\", \"paused\", \"here\" or NULL");
    return;
  }
  luma_ui_set_css_class(self->status_dot, "record", g_strcmp0(status, "record") == 0);
  luma_ui_set_css_class(self->status_dot, "paused", g_strcmp0(status, "paused") == 0);
  gtk_widget_set_visible(self->status_dot, g_strcmp0(status, "record") == 0 || g_strcmp0(status, "paused") == 0);
  gtk_widget_set_visible(self->presence_dot, g_strcmp0(status, "here") == 0);
  gtk_widget_set_visible(self->subtitle_row, gtk_widget_get_visible(self->subtitle_label) ||
                           gtk_widget_get_visible(self->presence_dot));
}

void luma_title_island_set_title_content(LumaTitleIsland *self, GtkWidget *content) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_return_if_fail(content == NULL || GTK_IS_WIDGET(content));
  GtkWidget *current = gtk_widget_get_next_sibling(self->text);
  if (current != NULL)
    gtk_box_remove(GTK_BOX(self->inner), current);
  gtk_widget_set_visible(self->text, content == NULL);
  if (content != NULL) {
    gtk_widget_set_valign(content, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(self->inner), content);
  }
}

GtkWidget *luma_title_island_add_trailing(LumaTitleIsland *self, GtkWidget *widget) {
  g_return_val_if_fail(LUMA_IS_TITLE_ISLAND(self), NULL);
  g_return_val_if_fail(GTK_IS_WIDGET(widget), NULL);
  gtk_widget_add_css_class(widget, "lumaui-title-island-button");
  gtk_widget_remove_css_class(widget, "image-button");
  gtk_box_append(GTK_BOX(self->trailing), widget);
  gtk_widget_set_visible(self->trailing, TRUE);
  return widget;
}

GtkWidget *luma_title_island_button_new(const char *icon, const char *label) {
  g_return_val_if_fail(icon != NULL && label != NULL, NULL);
  GtkWidget *button = gtk_button_new();
  gtk_widget_set_tooltip_text(button, label);
  gtk_button_set_child(GTK_BUTTON(button), luma_ui_icon_image(icon, 0));
  luma_ui_set_accessible_label(button, label);
  return button;
}

void luma_title_island_set_grow_func(LumaTitleIsland *self, LumaTitleIslandGrowFunc func, gpointer user_data,
                                     GDestroyNotify destroy, LumaTitleIslandGrows grows) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  if (self->grown)
    luma_title_island_fold(self);
  island_clear_grow(self);
  self->grow_func = func;
  self->grow_data = user_data;
  self->grow_destroy = destroy;
  self->grows = grows;
  island_describe(self);
}

void luma_title_island_set_grow_widget(LumaTitleIsland *self, GtkWidget *widget, LumaTitleIslandGrows grows) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_return_if_fail(widget == NULL || GTK_IS_WIDGET(widget));
  if (self->grown)
    luma_title_island_fold(self);
  island_clear_grow(self);
  if (widget != NULL)
    self->grow_widget = g_object_ref(widget);
  self->grows = grows;
  island_describe(self);
}

gboolean luma_title_island_get_grown(LumaTitleIsland *self) {
  g_return_val_if_fail(LUMA_IS_TITLE_ISLAND(self), FALSE);
  return self->grown;
}

GtkWidget *luma_title_island_get_panel(LumaTitleIsland *self) {
  g_return_val_if_fail(LUMA_IS_TITLE_ISLAND(self), NULL);
  return self->grown ? self->panel_child : NULL;
}

void luma_title_island_float_over(LumaTitleIsland *self, GtkWidget *where) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_return_if_fail(GTK_IS_WIDGET(where));
  GtkWidget *host = GTK_IS_OVERLAY(where) || LUMA_IS_LAYER_HOST(where)
                        ? where
                        : GTK_WIDGET(luma_layer_host_window_host(where));
  if (host == NULL)
    return;
  g_object_ref(self);
  GtkWidget *parent = gtk_widget_get_parent(GTK_WIDGET(self));
  if (parent != NULL) {
    if (GTK_IS_OVERLAY(parent))
      gtk_overlay_remove_overlay(GTK_OVERLAY(parent), GTK_WIDGET(self));
    else if (GTK_IS_BOX(parent))
      gtk_box_remove(GTK_BOX(parent), GTK_WIDGET(self));
    else
      gtk_widget_unparent(GTK_WIDGET(self));
  }
  g_set_weak_pointer(&self->host, host);
  gtk_widget_add_css_class(GTK_WIDGET(self), "floating");
  gtk_widget_set_halign(GTK_WIDGET(self), GTK_ALIGN_START);
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_START);
  gtk_overlay_add_overlay(GTK_OVERLAY(host), GTK_WIDGET(self));
  gtk_overlay_set_clip_overlay(GTK_OVERLAY(host), GTK_WIDGET(self), FALSE);
  gtk_overlay_set_measure_overlay(GTK_OVERLAY(host), GTK_WIDGET(self), FALSE);
  g_object_unref(self);
}

void luma_title_island_attach(LumaTitleIsland *self, GtkWidget *region) {
  luma_title_island_float_over(self, region);
}

void luma_title_island_set_phone_only(LumaTitleIsland *self, gboolean phone_only) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  self->phone_only = phone_only;
  if (!phone_only)
    gtk_widget_set_child_visible(GTK_WIDGET(self), TRUE);
  else if (self->watch != NULL && luma_width_watch_get_width(self->watch) > 0)
    island_tiered(self, luma_width_watch_get_tier(self->watch));
}

gboolean luma_title_island_get_phone_only(LumaTitleIsland *self) {
  g_return_val_if_fail(LUMA_IS_TITLE_ISLAND(self), TRUE);
  return self->phone_only;
}

typedef struct {
  GtkWidget *island; /* weak */
  int until, fade;
} Follow;

static void follow_free(gpointer data, GClosure *closure G_GNUC_UNUSED) {
  Follow *follow = data;
  g_clear_weak_pointer(&follow->island);
  g_free(follow);
}

static void follow_moved(GtkAdjustment *adjustment, gpointer data) {
  Follow *follow = data;
  if (follow->island == NULL)
    return;
  double y = CLAMP(gtk_adjustment_get_value(adjustment), 0, follow->until);
  double opacity = MAX(0.0, 1 - y / MAX(1, follow->fade));
  gtk_widget_set_opacity(follow->island, opacity);
  gtk_widget_set_can_target(follow->island, opacity > 0.05);
}

void luma_title_island_follow(LumaTitleIsland *self, GtkScrolledWindow *scroller, int until, int fade) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_return_if_fail(GTK_IS_SCROLLED_WINDOW(scroller));
  /* GTK cannot translate a widget, so the island fades and stops taking taps instead of sliding up. */
  Follow *follow = g_new0(Follow, 1);
  g_set_weak_pointer(&follow->island, GTK_WIDGET(self));
  follow->until = until > 0 ? until : 90;
  follow->fade = fade > 0 ? fade : 60;
  GtkAdjustment *adjustment = gtk_scrolled_window_get_vadjustment(scroller);
  g_signal_connect_data(adjustment, "value-changed", G_CALLBACK(follow_moved), follow, follow_free, 0);
  follow_moved(adjustment, follow);
}

void luma_title_island_set_variant(LumaTitleIsland *self, const char *variant) {
  g_return_if_fail(LUMA_IS_TITLE_ISLAND(self));
  g_return_if_fail(g_strcmp0(variant, "standard") == 0 || g_strcmp0(variant, "creative") == 0);
  self->creative = g_str_equal(variant, "creative");
  luma_ui_set_css_class(GTK_WIDGET(self), "creative", self->creative);
  g_autofree char *label = g_strdup(self->lead_label);
  luma_title_island_set_lead(self, self->lead, label);
}
