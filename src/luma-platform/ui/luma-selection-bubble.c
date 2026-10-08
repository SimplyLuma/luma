/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_bubble.SelectionBubble (Python); its placement (float_at,
 * rect_in) lives with the menus in luma-menu-drawer.c. */
#include "luma-selection-bubble.h"
#include "luma-action-private.h"
#include "luma-layer-host.h"
#include "luma-ui-private.h"

struct _LumaSelectionBubble {
  GtkBox parent_instance;
  GtkWidget *view;      /* weak */
  GtkWidget *host;      /* weak */
  GHashTable *buttons;  /* icon -> control (children) */
  GtkAdjustment *watched; /* weak: the scroll that hides it */
  gboolean down;
  guint settle;
  LumaFloatSide side;
};

G_DEFINE_FINAL_TYPE(LumaSelectionBubble, luma_selection_bubble, GTK_TYPE_BOX)

static void luma_selection_bubble_dispose(GObject *object) {
  LumaSelectionBubble *self = LUMA_SELECTION_BUBBLE(object);
  g_clear_handle_id(&self->settle, g_source_remove);
  g_clear_weak_pointer(&self->view);
  g_clear_weak_pointer(&self->host);
  g_clear_weak_pointer(&self->watched);
  G_OBJECT_CLASS(luma_selection_bubble_parent_class)->dispose(object);
}

static void luma_selection_bubble_finalize(GObject *object) {
  g_hash_table_unref(LUMA_SELECTION_BUBBLE(object)->buttons);
  G_OBJECT_CLASS(luma_selection_bubble_parent_class)->finalize(object);
}

static void luma_selection_bubble_class_init(LumaSelectionBubbleClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_selection_bubble_dispose;
  G_OBJECT_CLASS(klass)->finalize = luma_selection_bubble_finalize;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_TOOLBAR);
}

static void luma_selection_bubble_init(LumaSelectionBubble *self) {
  luma_ui_install();
  self->buttons = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
  self->side = LUMA_FLOAT_ABOVE;
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  /* The bubble itself never takes focus; its marks do (Alt+F10). Python sets
   * can-focus=False, which in GTK 4 also shuts out the marks. */
  gtk_widget_set_focusable(GTK_WIDGET(self), FALSE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-bubble");
}

/* ── keys inside the bubble ───────────────────────────────────────────── */

static GPtrArray *bubble_focusable(LumaSelectionBubble *self) {
  GPtrArray *found = g_ptr_array_new();
  for (GtkWidget *child = gtk_widget_get_first_child(GTK_WIDGET(self)); child != NULL;
       child = gtk_widget_get_next_sibling(child))
    if (GTK_IS_BUTTON(child) && gtk_widget_get_visible(child) && gtk_widget_is_sensitive(child))
      g_ptr_array_add(found, child);
  return found;
}

static gboolean bubble_key(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                           GdkModifierType state G_GNUC_UNUSED, gpointer user_data) {
  LumaSelectionBubble *self = LUMA_SELECTION_BUBBLE(user_data);
  if (keyval == GDK_KEY_Left || keyval == GDK_KEY_Right) {
    g_autoptr(GPtrArray) buttons = bubble_focusable(self);
    GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
    GtkWidget *focus = root != NULL ? gtk_root_get_focus(root) : NULL;
    int index = -1;
    for (guint i = 0; i < buttons->len; i++)
      if (g_ptr_array_index(buttons, i) == focus)
        index = (int)i;
    int step = keyval == GDK_KEY_Right ? 1 : -1;
    int n = (int)buttons->len;
    if (n > 0)
      gtk_widget_grab_focus(g_ptr_array_index(buttons, (guint)(((index + step) % n + n) % n)));
    return TRUE;
  }
  if (keyval == GDK_KEY_Escape) {
    luma_selection_bubble_hide(self);
    if (self->view != NULL)
      gtk_widget_grab_focus(self->view);
    return TRUE;
  }
  return FALSE;
}

/* ── tracking a text view ─────────────────────────────────────────────── */

static GdkRectangle *selection_rect(LumaSelectionBubble *self, GdkRectangle *out) {
  if (self->view == NULL || !GTK_IS_TEXT_VIEW(self->view))
    return NULL;
  GtkTextView *view = GTK_TEXT_VIEW(self->view);
  GtkTextIter start, end;
  if (!gtk_text_buffer_get_selection_bounds(gtk_text_view_get_buffer(view), &start, &end))
    return NULL;
  GdkRectangle first, last;
  gtk_text_view_get_iter_location(view, &start, &first);
  gtk_text_view_get_iter_location(view, &end, &last);
  int top = first.y, bottom = last.y + last.height, left, right;
  if (first.y == last.y) {
    left = first.x;
    right = last.x;
  } else { /* several lines: the width of the text column, as a DOM range's box */
    GdkRectangle visible;
    gtk_text_view_get_visible_rect(view, &visible);
    left = visible.x + gtk_text_view_get_left_margin(view);
    right = visible.x + visible.width - gtk_text_view_get_right_margin(view);
  }
  int x1, y1, x2, y2;
  gtk_text_view_buffer_to_window_coords(view, GTK_TEXT_WINDOW_WIDGET, left, top, &x1, &y1);
  gtk_text_view_buffer_to_window_coords(view, GTK_TEXT_WINDOW_WIDGET, right, bottom, &x2, &y2);
  out->x = x1;
  out->y = y1;
  out->width = MAX(1, x2 - x1);
  out->height = MAX(1, y2 - y1);
  return out;
}

static gboolean settled(gpointer user_data) {
  LumaSelectionBubble *self = LUMA_SELECTION_BUBBLE(user_data);
  self->settle = 0;
  GdkRectangle rect;
  if (selection_rect(self, &rect) == NULL)
    luma_selection_bubble_hide(self);
  else
    luma_selection_bubble_show_for(self, &rect);
  return G_SOURCE_REMOVE;
}

static void schedule(LumaSelectionBubble *self) {
  g_clear_handle_id(&self->settle, g_source_remove);
  self->settle = g_timeout_add(LUMA_UI_MOTION_BUBBLE_SETTLE_MS, settled, self);
}

static gboolean view_event(GtkEventControllerLegacy *controller G_GNUC_UNUSED, GdkEvent *event, gpointer user_data) {
  LumaSelectionBubble *self = LUMA_SELECTION_BUBBLE(user_data);
  GdkEventType kind = gdk_event_get_event_type(event);
  if (kind == GDK_BUTTON_PRESS) {
    self->down = TRUE;
    luma_selection_bubble_hide(self);
  } else if (kind == GDK_BUTTON_RELEASE) {
    self->down = FALSE;
    schedule(self);
  }
  return FALSE;
}

static gboolean view_key(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                         GdkModifierType state, gpointer user_data) {
  if (keyval == GDK_KEY_F10 && (state & GDK_ALT_MASK))
    return luma_selection_bubble_focus_first(LUMA_SELECTION_BUBBLE(user_data));
  return FALSE;
}

static void mark_set(GtkTextBuffer *buffer, const GtkTextIter *location G_GNUC_UNUSED, GtkTextMark *mark,
                     gpointer user_data) {
  LumaSelectionBubble *self = LUMA_SELECTION_BUBBLE(user_data);
  if (mark != gtk_text_buffer_get_insert(buffer) && mark != gtk_text_buffer_get_selection_bound(buffer))
    return;
  if (!gtk_text_buffer_get_has_selection(buffer))
    luma_selection_bubble_hide(self);
  else if (!self->down)
    schedule(self);
}

static void hide_now(gpointer instance G_GNUC_UNUSED, gpointer user_data) {
  luma_selection_bubble_hide(LUMA_SELECTION_BUBBLE(user_data));
}

static void watch_scroll(LumaSelectionBubble *self) {
  if (self->view == NULL || !GTK_IS_SCROLLABLE(self->view))
    return;
  GtkAdjustment *adjustment = gtk_scrollable_get_vadjustment(GTK_SCROLLABLE(self->view));
  if (adjustment == NULL || adjustment == self->watched)
    return;
  g_set_weak_pointer(&self->watched, adjustment);
  g_signal_connect_object(adjustment, "value-changed", G_CALLBACK(hide_now), self, 0);
}

static void vadjustment_changed(GObject *view G_GNUC_UNUSED, GParamSpec *pspec G_GNUC_UNUSED, gpointer user_data) {
  watch_scroll(LUMA_SELECTION_BUBBLE(user_data));
}

static void track(LumaSelectionBubble *self, GtkWidget *view) {
  GtkEventController *legacy = gtk_event_controller_legacy_new();
  gtk_event_controller_set_propagation_phase(legacy, GTK_PHASE_CAPTURE);
  g_signal_connect_object(legacy, "event", G_CALLBACK(view_event), self, 0);
  gtk_widget_add_controller(view, legacy);
  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect_object(keys, "key-pressed", G_CALLBACK(view_key), self, 0);
  gtk_widget_add_controller(view, keys);
  if (GTK_IS_TEXT_VIEW(view)) {
    GtkTextBuffer *buffer = gtk_text_view_get_buffer(GTK_TEXT_VIEW(view));
    g_signal_connect_object(buffer, "mark-set", G_CALLBACK(mark_set), self, 0);
    g_signal_connect_object(buffer, "changed", G_CALLBACK(hide_now), self, 0);
    g_signal_connect_object(view, "notify::vadjustment", G_CALLBACK(vadjustment_changed), self, 0);
    watch_scroll(self);
  }
}

GtkWidget *luma_selection_bubble_new(GtkWidget *view, LumaBarItem *const *items, guint n_items, const char *label) {
  g_return_val_if_fail(GTK_IS_WIDGET(view), NULL);
  g_return_val_if_fail(items != NULL || n_items == 0, NULL);
  if (n_items == 0) {
    g_critical("a selection bubble needs at least one action");
    return NULL;
  }
  LumaSelectionBubble *self = g_object_new(LUMA_TYPE_SELECTION_BUBBLE, NULL);
  luma_ui_set_accessible_label(GTK_WIDGET(self), label != NULL ? label : "Formatting");
  g_set_weak_pointer(&self->view, view);
  for (guint i = 0; i < n_items; i++) {
    if (!LUMA_IS_BAR_ITEM(items[i]))
      continue;
    GtkWidget *control = luma_bar_item_create_control(items[i], "bubble");
    if (control == NULL)
      continue;
    if (luma_bar_item_get_kind(items[i]) == LUMA_BAR_ITEM_ACTION)
      g_hash_table_replace(self->buttons, g_strdup(luma_bar_item_get_icon(items[i])), control);
    gtk_box_append(GTK_BOX(self), control);
  }
  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(bubble_key), self);
  gtk_widget_add_controller(GTK_WIDGET(self), keys);
  track(self, view);
  return GTK_WIDGET(self);
}

/* ── public API ───────────────────────────────────────────────────────── */

gboolean luma_selection_bubble_get_shown(LumaSelectionBubble *self) {
  g_return_val_if_fail(LUMA_IS_SELECTION_BUBBLE(self), FALSE);
  return self->host != NULL && gtk_widget_get_parent(GTK_WIDGET(self)) == self->host &&
         gtk_widget_has_css_class(GTK_WIDGET(self), "shown");
}

static gboolean bubble_shown(gpointer user_data) {
  gtk_widget_add_css_class(GTK_WIDGET(user_data), "shown");
  return G_SOURCE_REMOVE;
}

void luma_selection_bubble_show_for(LumaSelectionBubble *self, const GdkRectangle *rect) {
  g_return_if_fail(LUMA_IS_SELECTION_BUBBLE(self));
  g_return_if_fail(rect != NULL);
  if (self->down || self->view == NULL)
    return;
  LumaLayerHost *layer_host = luma_layer_host_for_widget(self->view);
  if (layer_host == NULL) {
    g_critical("a selection bubble needs a view that is inside a window");
    return;
  }
  GtkWidget *host = GTK_WIDGET(layer_host);
  luma_ui_install();
  if (gtk_widget_get_parent(GTK_WIDGET(self)) != host) {
    g_object_ref_sink(self);
    GtkWidget *parent = gtk_widget_get_parent(GTK_WIDGET(self));
    if (parent != NULL && GTK_IS_OVERLAY(parent))
      gtk_overlay_remove_overlay(GTK_OVERLAY(parent), GTK_WIDGET(self));
    else if (parent != NULL)
      gtk_widget_unparent(GTK_WIDGET(self));
    luma_layer_host_add_layer(host, GTK_WIDGET(self));
    g_set_weak_pointer(&self->host, host);
    g_object_unref(self);
  }
  gtk_widget_set_visible(GTK_WIDGET(self), TRUE);
  GdkRectangle in_host = luma_ui_rect_in(host, self->view, rect);
  self->side = luma_ui_float_at(host, GTK_WIDGET(self), &in_host, LUMA_FLOAT_ABOVE, LUMA_FLOAT_ALIGN_CENTER, -1, -1,
                                -1, 0);
  if (!gtk_widget_has_css_class(GTK_WIDGET(self), "shown"))
    luma_ui_on_next_frame(GTK_WIDGET(self), bubble_shown, self);
}

void luma_selection_bubble_hide(LumaSelectionBubble *self) {
  g_return_if_fail(LUMA_IS_SELECTION_BUBBLE(self));
  g_clear_handle_id(&self->settle, g_source_remove);
  gtk_widget_remove_css_class(GTK_WIDGET(self), "shown");
  gtk_widget_set_visible(GTK_WIDGET(self), FALSE);
}

void luma_selection_bubble_set_active(LumaSelectionBubble *self, const char *icon, gboolean active) {
  g_return_if_fail(LUMA_IS_SELECTION_BUBBLE(self));
  g_return_if_fail(icon != NULL);
  GtkWidget *button = g_hash_table_lookup(self->buttons, icon);
  if (button == NULL) {
    g_critical("the selection bubble has no mark '%s'", icon);
    return;
  }
  luma_ui_set_css_class(button, "on", active);
  gtk_accessible_update_state(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_STATE_PRESSED,
                              active ? GTK_ACCESSIBLE_TRISTATE_TRUE : GTK_ACCESSIBLE_TRISTATE_FALSE, -1);
}

gboolean luma_selection_bubble_focus_first(LumaSelectionBubble *self) {
  g_return_val_if_fail(LUMA_IS_SELECTION_BUBBLE(self), FALSE);
  if (!luma_selection_bubble_get_shown(self))
    return FALSE;
  g_autoptr(GPtrArray) buttons = bubble_focusable(self);
  if (buttons->len == 0)
    return FALSE;
  gtk_widget_grab_focus(g_ptr_array_index(buttons, 0));
  return TRUE;
}
