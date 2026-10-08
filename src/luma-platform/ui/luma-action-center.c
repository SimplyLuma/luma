/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of action_center.py: ActionCenter, ActionEditor, BarAction, BarChip,
 * BarContext, BarPrompt, SEPARATOR, SPACER, make_control (Python). */
#include "luma-action-center.h"
#include "luma-action-private.h"
#include "luma-cards.h"
#include "luma-controls.h"
#include "luma-layer-host.h"
#include "luma-ui-private.h"

#include <string.h>

/* ── LumaBarItemKind ──────────────────────────────────────────────────── */

GType luma_bar_item_kind_get_type(void) {
  static gsize type_id = 0;
  static const GEnumValue values[] = {
    {LUMA_BAR_ITEM_ACTION, "LUMA_BAR_ITEM_ACTION", "action"},
    {LUMA_BAR_ITEM_CHIP, "LUMA_BAR_ITEM_CHIP", "chip"},
    {LUMA_BAR_ITEM_PROMPT, "LUMA_BAR_ITEM_PROMPT", "prompt"},
    {LUMA_BAR_ITEM_CONTEXT, "LUMA_BAR_ITEM_CONTEXT", "context"},
    {LUMA_BAR_ITEM_SEPARATOR, "LUMA_BAR_ITEM_SEPARATOR", "separator"},
    {LUMA_BAR_ITEM_SPACER, "LUMA_BAR_ITEM_SPACER", "spacer"},
    {LUMA_BAR_ITEM_SEARCH, "LUMA_BAR_ITEM_SEARCH", "search"},
    {LUMA_BAR_ITEM_MODES, "LUMA_BAR_ITEM_MODES", "modes"},
    {LUMA_BAR_ITEM_WIDGET, "LUMA_BAR_ITEM_WIDGET", "widget"},
    {0, NULL, NULL},
  };
  if (g_once_init_enter(&type_id))
    g_once_init_leave(&type_id, g_enum_register_static(g_intern_static_string("LumaBarItemKind"), values));
  return type_id;
}

/* ── LumaBarItem ──────────────────────────────────────────────────────── */

enum { ITEM_ACTIVATED, ITEM_DISMISSED, ITEM_SEARCH_CHANGED, ITEM_SEARCH_ACTIVATED, N_ITEM_SIGNALS };
static guint item_signals[N_ITEM_SIGNALS];
static void center_apply_width(LumaActionCenter *self);
static void center_open_search(LumaActionCenter *self, LumaBarItem *item);

struct _LumaBarItem {
  GObject parent_instance;
  LumaBarItemKind kind;
  char *icon;
  char *label; /* an action's word, a chip's label, a prompt's or context's text */
  char *emphasis;
  char *action_name;
  char *tooltip;
  GdkPaintable *chip_paintable;
  char *group;
  gboolean dismissible;
  gboolean primary;
  gboolean text_glyph;
  gboolean explicit_rule;
  gboolean danger;
  gboolean active;
  gboolean sensitive;
  gboolean phone_compact;
  GtkWidget *phone_label; /* weak: the currently rendered text, if any */
  char *search_text;
  GtkWidget *hosted; /* held across bar swaps; search entry or mode switch */
  GtkWidget *search_entry; /* owned by hosted stack, for a search item */
  GtkWidget *folded_button;
  gboolean search_expanded;
  gboolean syncing_search;
  GtkWidget *active_control; /* weak, for anchored menus */
  /* v71: the panel the action grows the bar into */
  char *panel_key;
  LumaBarPanelFunc panel_func;
  gpointer panel_data;
  GDestroyNotify panel_destroy;
  /* v71: a search that is a glyph until pressed, then replaces the row */
  gboolean collapsed;
  gboolean search_keep;
  /* v71: on a phone a labelled action shows its icon alone unless it keeps its label (Contacts' Edit) */
  gboolean panel_close;
  gboolean keep_label;
  gboolean dropdown;
  gboolean compact_picker;
  gboolean view_picker;
  gboolean icon_trailing;
  gboolean chip_action;
  gboolean chip_identity;
  char *chip_detail;
};

G_DEFINE_FINAL_TYPE(LumaBarItem, luma_bar_item, G_TYPE_OBJECT)

static void luma_bar_item_finalize(GObject *object) {
  LumaBarItem *self = LUMA_BAR_ITEM(object);
  g_free(self->icon);
  g_free(self->label);
  g_free(self->chip_detail);
  g_free(self->emphasis);
  g_free(self->action_name);
  g_free(self->tooltip);
  g_clear_object(&self->chip_paintable);
  g_free(self->search_text);
  g_clear_object(&self->hosted);
  g_clear_weak_pointer(&self->active_control);
  g_clear_weak_pointer(&self->phone_label);
  g_free(self->panel_key);
  if (self->panel_destroy != NULL && self->panel_data != NULL)
    self->panel_destroy(self->panel_data);
  g_free(self->group);
  G_OBJECT_CLASS(luma_bar_item_parent_class)->finalize(object);
}

static void luma_bar_item_class_init(LumaBarItemClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = luma_bar_item_finalize;
  /**
   * LumaBarItem::activated:
   * @self: the item
   *
   * An action's control was clicked (after its #GAction), or a prompt was
   * pressed (before the action center grows).
   */
  item_signals[ITEM_ACTIVATED] = g_signal_new("activated", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                              NULL, NULL, G_TYPE_NONE, 0);
  /**
   * LumaBarItem::dismissed:
   * @self: the item
   *
   * A chip's or context line's × was pressed.
   */
  item_signals[ITEM_DISMISSED] = g_signal_new("dismissed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                              NULL, NULL, G_TYPE_NONE, 0);
  item_signals[ITEM_SEARCH_CHANGED] = g_signal_new("search-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                                                    0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
  item_signals[ITEM_SEARCH_ACTIVATED] = g_signal_new("search-activated", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                                                      0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
}

static void luma_bar_item_init(LumaBarItem *self) { self->sensitive = TRUE; }

static LumaBarItem *item_new(LumaBarItemKind kind) {
  LumaBarItem *self = g_object_new(LUMA_TYPE_BAR_ITEM, NULL);
  self->kind = kind;
  return self;
}

LumaBarItem *luma_bar_item_new_action(const char *icon, const char *label, const char *action_name) {
  gboolean has_icon = icon != NULL && *icon != '\0';
  /* A word alone (v70 .bt with no glyph: Settings' "Go back" and "Keep") or a glyph, never neither. */
  g_return_val_if_fail(has_icon || (label != NULL && *label != '\0'), NULL);
  LumaBarItem *self = item_new(LUMA_BAR_ITEM_ACTION);
  self->icon = has_icon ? g_strdup(icon) : NULL;
  self->label = label != NULL && *label != '\0' ? g_strdup(label) : NULL;
  self->action_name = g_strdup(action_name);
  return self;
}

LumaBarItem *luma_bar_item_new_chip(const char *label, const char *icon, gboolean dismissible) {
  g_return_val_if_fail(label != NULL, NULL);
  LumaBarItem *self = item_new(LUMA_BAR_ITEM_CHIP);
  self->label = g_strdup(label);
  self->icon = g_strdup(icon);
  self->dismissible = dismissible;
  return self;
}

void luma_bar_item_chip_set_identity(LumaBarItem *self, gboolean identity, const char *detail) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_CHIP);
  g_return_if_fail(!identity || (!self->dismissible && !self->chip_action));
  self->chip_identity = !!identity;
  g_free(self->chip_detail);
  self->chip_detail = g_strdup(detail);
}

void luma_bar_item_chip_set_paintable(LumaBarItem *self, GdkPaintable *paintable) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_CHIP);
  g_return_if_fail(paintable == NULL || GDK_IS_PAINTABLE(paintable));
  g_set_object(&self->chip_paintable, paintable);
}

LumaBarItem *luma_bar_item_new_chip_action(const char *label, const char *icon,
                                            const char *action_name) {
  LumaBarItem *self = luma_bar_item_new_chip(label, icon, FALSE);
  if (self == NULL)
    return NULL;
  self->chip_action = TRUE;
  self->action_name = g_strdup(action_name);
  return self;
}

LumaBarItem *luma_bar_item_new_prompt(const char *text) {
  g_return_val_if_fail(text != NULL, NULL);
  LumaBarItem *self = item_new(LUMA_BAR_ITEM_PROMPT);
  self->label = g_strdup(text);
  return self;
}

LumaBarItem *luma_bar_item_new_context(const char *icon, const char *text, const char *emphasis,
                                       gboolean dismissible) {
  g_return_val_if_fail(text != NULL, NULL);
  LumaBarItem *self = item_new(LUMA_BAR_ITEM_CONTEXT);
  self->icon = icon != NULL && *icon != '\0' ? g_strdup(icon) : NULL; /* a context with no glyph: its words alone */
  self->label = g_strdup(text);
  self->emphasis = g_strdup(emphasis);
  self->dismissible = dismissible;
  return self;
}

LumaBarItem *luma_bar_item_new_separator(void) { return item_new(LUMA_BAR_ITEM_SEPARATOR); }
LumaBarItem *luma_bar_item_new_rule(void) {
  LumaBarItem *item = item_new(LUMA_BAR_ITEM_SEPARATOR);
  item->explicit_rule = TRUE;
  return item;
}
void luma_bar_item_set_text_glyph(LumaBarItem *self, gboolean text_glyph) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  g_return_if_fail(!text_glyph || ((self->icon == NULL || *self->icon == 0) && self->label != NULL));
  self->text_glyph = !!text_glyph;
  if (self->active_control != NULL)
    luma_ui_set_css_class(self->active_control, "text-glyph", self->text_glyph);
}

LumaBarItem *luma_bar_item_new_spacer(void) { return item_new(LUMA_BAR_ITEM_SPACER); }

LumaBarItem *luma_bar_item_new_search(const char *placeholder) {
  g_return_val_if_fail(placeholder != NULL && *placeholder != '\0', NULL);
  LumaBarItem *self = item_new(LUMA_BAR_ITEM_SEARCH);
  self->label = g_strdup(placeholder);
  self->search_text = g_strdup("");
  return self;
}

void luma_bar_item_search_set_text(LumaBarItem *self, const char *text) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_SEARCH);
  const char *value = text != NULL ? text : "";
  if (g_strcmp0(self->search_text, value) == 0) return;
  g_free(self->search_text);
  self->search_text = g_strdup(value);
  if (*value != '\0') self->search_expanded = TRUE;
  if (self->search_entry != NULL) {
    self->syncing_search = TRUE;
    gtk_editable_set_text(GTK_EDITABLE(self->search_entry), value);
    self->syncing_search = FALSE;
  }
}

const char *luma_bar_item_search_get_text(LumaBarItem *self) {
  g_return_val_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_SEARCH, NULL);
  return self->search_text;
}

void luma_bar_item_search_focus(LumaBarItem *self) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_SEARCH);
  self->search_expanded = TRUE;
  if (self->hosted != NULL) {
    GtkWidget *center = gtk_widget_get_ancestor(self->hosted, LUMA_TYPE_ACTION_CENTER);
    if (center != NULL && self->collapsed) /* v71: the field replaces the row */
      center_open_search(LUMA_ACTION_CENTER(center), self);
    if (center != NULL) center_apply_width(LUMA_ACTION_CENTER(center));
  }
  if (self->search_entry != NULL) gtk_widget_grab_focus(self->search_entry);
}

LumaBarItem *luma_bar_item_new_modes(LumaModeSwitch *modes) {
  g_return_val_if_fail(LUMA_IS_MODE_SWITCH(modes), NULL);
  g_return_val_if_fail(gtk_widget_get_parent(GTK_WIDGET(modes)) == NULL, NULL);
  LumaBarItem *self = item_new(LUMA_BAR_ITEM_MODES);
  self->hosted = GTK_WIDGET(g_object_ref_sink(modes));
  gtk_widget_add_css_class(self->hosted, "in-bar");
  return self;
}

LumaBarItem *luma_bar_item_new_widget(GtkWidget *widget) {
  g_return_val_if_fail(GTK_IS_WIDGET(widget), NULL);
  g_return_val_if_fail(gtk_widget_get_parent(widget) == NULL, NULL);
  LumaBarItem *self = item_new(LUMA_BAR_ITEM_WIDGET);
  self->hosted = GTK_WIDGET(g_object_ref_sink(widget));
  gtk_widget_add_css_class(widget, "in-bar");
  gtk_widget_set_valign(widget, GTK_ALIGN_CENTER);
  return self;
}

void luma_bar_item_set_keep_label(LumaBarItem *self, gboolean keep_label) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  self->keep_label = keep_label;
}

void luma_bar_item_set_dropdown(LumaBarItem *self, gboolean dropdown, gboolean compact) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_ACTION);
  self->dropdown = !!dropdown;
  self->compact_picker = !!compact;
  self->view_picker = FALSE;
}

void luma_bar_item_set_dropdown_size(LumaBarItem *self, const char *size) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_ACTION);
  g_return_if_fail(g_strcmp0(size, "regular") == 0 || g_strcmp0(size, "compact") == 0 || g_strcmp0(size, "view") == 0);
  self->compact_picker = g_strcmp0(size, "compact") == 0;
  self->view_picker = g_strcmp0(size, "view") == 0;
}

void luma_bar_item_search_set_collapsed(LumaBarItem *self, gboolean collapsed) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_SEARCH);
  self->collapsed = collapsed;
  if (self->hosted != NULL) {
    GtkWidget *center = gtk_widget_get_ancestor(self->hosted, LUMA_TYPE_ACTION_CENTER);
    if (center != NULL)
      center_apply_width(LUMA_ACTION_CENTER(center));
  }
}

void luma_bar_item_search_set_keep(LumaBarItem *self, gboolean keep) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_SEARCH);
  self->search_keep = keep;
  if (self->search_entry != NULL)
    luma_ui_set_css_class(self->search_entry, "keep", keep);
  if (self->hosted != NULL) {
    luma_ui_set_css_class(self->hosted, "keep", keep);
    GtkWidget *center = gtk_widget_get_ancestor(self->hosted, LUMA_TYPE_ACTION_CENTER);
    if (center != NULL)
      center_apply_width(LUMA_ACTION_CENTER(center));
  }
}

GtkWidget *luma_bar_item_search_get_entry(LumaBarItem *self) {
  g_return_val_if_fail(LUMA_IS_BAR_ITEM(self) && self->kind == LUMA_BAR_ITEM_SEARCH, NULL);
  return self->search_entry;
}

void luma_bar_item_set_panel_close(LumaBarItem *self, gboolean close) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  self->panel_close = !!close;
}

void luma_bar_item_set_panel(LumaBarItem *self, const char *key, LumaBarPanelFunc func, gpointer user_data,
                             GDestroyNotify destroy) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  g_return_if_fail(self->kind == LUMA_BAR_ITEM_ACTION);
  if (self->panel_destroy != NULL && self->panel_data != NULL)
    self->panel_destroy(self->panel_data);
  g_free(self->panel_key);
  self->panel_key = g_strdup(key);
  self->panel_func = func;
  self->panel_data = user_data;
  self->panel_destroy = destroy;
}

static const char *item_panel_key(LumaBarItem *self) {
  return self->panel_key != NULL ? self->panel_key : self->label != NULL ? self->label : self->icon;
}

LumaBarItemKind luma_bar_item_get_kind(LumaBarItem *self) {
  g_return_val_if_fail(LUMA_IS_BAR_ITEM(self), LUMA_BAR_ITEM_ACTION);
  return self->kind;
}

const char *luma_bar_item_get_icon(LumaBarItem *self) {
  g_return_val_if_fail(LUMA_IS_BAR_ITEM(self), NULL);
  return self->icon;
}

void luma_bar_item_set_tooltip(LumaBarItem *self, const char *tooltip) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  g_free(self->tooltip);
  self->tooltip = tooltip != NULL && *tooltip != '\0' ? g_strdup(tooltip) : NULL;
}

void luma_bar_item_set_phone_compact(LumaBarItem *self, gboolean compact) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  g_return_if_fail(self->kind == LUMA_BAR_ITEM_CHIP ||
                   (self->kind == LUMA_BAR_ITEM_ACTION && self->icon != NULL));
  self->phone_compact = compact;
  if (self->phone_label != NULL) {
    GtkWidget *center = gtk_widget_get_ancestor(self->phone_label, LUMA_TYPE_ACTION_CENTER);
    gboolean phone = center != NULL && gtk_widget_has_css_class(center, "phone");
    if (self->kind == LUMA_BAR_ITEM_CHIP) {
      gtk_label_set_text(GTK_LABEL(self->phone_label), compact && phone ? "" : self->label);
      gtk_label_set_max_width_chars(GTK_LABEL(self->phone_label), compact && phone ? 0 : 30);
    } else
      gtk_widget_set_visible(self->phone_label, !(compact && phone));
  }
}

void luma_bar_item_set_primary(LumaBarItem *self, gboolean primary) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  self->primary = primary;
}

void luma_bar_item_set_danger(LumaBarItem *self, gboolean danger) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  self->danger = danger;
}

void luma_bar_item_set_active(LumaBarItem *self, gboolean active) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  self->active = !!active;
  if (self->active_control != NULL) {
    luma_ui_set_css_class(self->active_control, "on", self->active);
    gtk_accessible_update_state(GTK_ACCESSIBLE(self->active_control), GTK_ACCESSIBLE_STATE_PRESSED,
        self->active ? GTK_ACCESSIBLE_TRISTATE_TRUE : GTK_ACCESSIBLE_TRISTATE_FALSE, -1);
  }
}

void luma_bar_item_set_sensitive(LumaBarItem *self, gboolean sensitive) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  self->sensitive = !!sensitive;
  GtkWidget *control = self->hosted != NULL ? self->hosted : self->active_control;
  if (control != NULL) gtk_widget_set_sensitive(control, self->sensitive);
}

GtkWidget *luma_bar_item_get_active_control(LumaBarItem *self) {
  g_return_val_if_fail(LUMA_IS_BAR_ITEM(self), NULL);
  GtkWidget *control = self->hosted != NULL ? self->hosted : self->active_control;
  return control != NULL && gtk_widget_get_parent(control) != NULL ? control : NULL;
}

void luma_bar_item_set_group(LumaBarItem *self, const char *label) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  g_return_if_fail(self->kind == LUMA_BAR_ITEM_ACTION);
  g_free(self->group);
  self->group = g_strdup(label);
}

void luma_bar_item_set_icon_trailing(LumaBarItem *self, gboolean trailing) {
  g_return_if_fail(LUMA_IS_BAR_ITEM(self));
  g_return_if_fail(self->kind == LUMA_BAR_ITEM_ACTION);
  self->icon_trailing = !!trailing;
}

static char *markup_with(const char *text, const char *emphasis) {
  const char *slot = text != NULL ? strstr(text, "{}") : NULL;
  if (emphasis == NULL || slot == NULL)
    return g_markup_escape_text(text != NULL ? text : "", -1);
  g_autofree char *head = g_markup_escape_text(text, slot - text);
  g_autofree char *bold = g_markup_escape_text(emphasis, -1);
  g_autofree char *tail = g_markup_escape_text(slot + 2, -1);
  return g_strconcat(head, "<b>", bold, "</b>", tail, NULL);
}

static void center_item_pressed(LumaActionCenter *self, LumaBarItem *item, GtkWidget *control);

static void item_clicked(GtkButton *button, gpointer user_data) {
  g_object_ref(button);
  g_signal_emit(user_data, item_signals[ITEM_ACTIVATED], 0);
  GtkWidget *center = gtk_widget_get_ancestor(GTK_WIDGET(button), LUMA_TYPE_ACTION_CENTER);
  if (center != NULL)
    center_item_pressed(LUMA_ACTION_CENTER(center), user_data, GTK_WIDGET(button));
  g_object_unref(button);
}


static void item_dismissed(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  g_signal_emit(user_data, item_signals[ITEM_DISMISSED], 0);
}

/* The × of a chip or a context line. */
static GtkWidget *close_button(LumaBarItem *item, const char *name) {
  GtkWidget *close = gtk_button_new();
  gtk_widget_set_valign(close, GTK_ALIGN_CENTER);
  gtk_widget_set_tooltip_text(close, name);
  gtk_widget_add_css_class(close, "lumaui-bar-chip-close");
  gtk_button_set_child(GTK_BUTTON(close), luma_ui_icon_image("x", 0));
  gtk_widget_remove_css_class(close, "image-button"); /* a LumaUI part, not the legacy icon-button look */
  luma_ui_set_accessible_label(close, name);
  g_signal_connect_object(close, "clicked", G_CALLBACK(item_dismissed), item, 0);
  return close;
}

GtkWidget *luma_bar_item_create_control(LumaBarItem *self, const char *size) {
  g_return_val_if_fail(LUMA_IS_BAR_ITEM(self), NULL);
  if (size == NULL)
    size = "bar";
  switch (self->kind) {
  case LUMA_BAR_ITEM_SEPARATOR: {
    GtkWidget *rule = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_valign(rule, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(rule, "lumaui-bar-rule");
    /* v70: in an app window's bar the rule is an invisible 6 px spacer; tool rows draw it. */
    if (self->explicit_rule)
      gtk_widget_add_css_class(rule, "explicit-rule");
    else if (g_str_equal(size, "bar"))
      gtk_widget_add_css_class(rule, "space");
    return rule;
  }
  case LUMA_BAR_ITEM_SPACER: {
    GtkWidget *room = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_hexpand(room, TRUE);
    g_object_set_data(G_OBJECT(room), "lumaui-bar-spacer", GINT_TO_POINTER(1));
    return room;
  }
  case LUMA_BAR_ITEM_CHIP: {
    GtkWidget *chip = self->chip_action ? gtk_button_new() : gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_hexpand(chip,FALSE);
    gtk_widget_set_valign(chip, GTK_ALIGN_CENTER);
    gtk_widget_set_sensitive(chip, self->sensitive);
    gtk_widget_add_css_class(chip, self->chip_identity ? "lumaui-bar-identity" : "lumaui-bar-chip");
    GtkWidget *content = self->chip_action ? gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8) : chip;
    if (self->chip_paintable != NULL) {
      GtkWidget *frame = gtk_overlay_new();
      gtk_widget_set_valign(frame, GTK_ALIGN_CENTER);
      GtkWidget *size = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
      gtk_widget_set_size_request(size, self->chip_identity ? 32 : 28, self->chip_identity ? 32 : 28);
      gtk_overlay_set_child(GTK_OVERLAY(frame), size);
      GtkWidget *picture = gtk_picture_new_for_paintable(self->chip_paintable);
      gtk_picture_set_content_fit(GTK_PICTURE(picture), GTK_CONTENT_FIT_COVER);
      gtk_picture_set_can_shrink(GTK_PICTURE(picture), TRUE);
      /* The 28 px frame supplies the size. Expansion here made the whole
       * chip absorb surplus bar width after its phone label collapsed. */
      gtk_overlay_add_overlay(GTK_OVERLAY(frame), picture);
      gtk_overlay_set_measure_overlay(GTK_OVERLAY(frame), picture, FALSE);
      gtk_widget_set_overflow(frame, GTK_OVERFLOW_HIDDEN);
      gtk_widget_add_css_class(frame, "lumaui-bar-chip-preview");
      gtk_box_append(GTK_BOX(content), frame);
    }
    if (self->chip_paintable == NULL && self->icon != NULL && *self->icon != '\0') {
      GtkWidget *glyph = luma_ui_icon_image(self->icon, self->chip_identity ? 32 : 0);
      gtk_widget_add_css_class(glyph, "lumaui-bar-chip-icon");
      gtk_box_append(GTK_BOX(content), glyph);
    }
    GtkWidget *text = gtk_label_new(self->label);
    g_set_weak_pointer(&self->phone_label, text);
    gtk_label_set_ellipsize(GTK_LABEL(text), PANGO_ELLIPSIZE_END);
    gtk_label_set_max_width_chars(GTK_LABEL(text), 30);
    if (self->chip_identity) {
      g_autofree char *identity_name = g_strdup_printf("%s%s%s", self->label, self->chip_detail ? ", " : "", self->chip_detail ? self->chip_detail : "");
      luma_ui_set_accessible_label(chip, identity_name);
      GtkWidget *copy = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
      gtk_widget_set_valign(copy, GTK_ALIGN_CENTER);
      gtk_label_set_xalign(GTK_LABEL(text), 0);
      gtk_label_set_width_chars(GTK_LABEL(text), 1);
      gtk_widget_add_css_class(text, "title");
      gtk_box_append(GTK_BOX(copy), text);
      if (self->chip_detail != NULL && *self->chip_detail != '\0') {
        GtkWidget *detail = gtk_label_new(self->chip_detail);
        gtk_label_set_xalign(GTK_LABEL(detail), 0);
        gtk_label_set_width_chars(GTK_LABEL(detail), 1);
        gtk_label_set_max_width_chars(GTK_LABEL(detail), 30);
        gtk_label_set_ellipsize(GTK_LABEL(detail), PANGO_ELLIPSIZE_END);
        gtk_widget_add_css_class(detail, "detail");
        gtk_box_append(GTK_BOX(copy), detail);
      }
      gtk_box_append(GTK_BOX(content), copy);
    } else {
      gtk_box_append(GTK_BOX(content), text);
    }
    if (self->dismissible)
      gtk_box_append(GTK_BOX(chip), close_button(self, "Done"));
    if (self->chip_action) {
      gtk_button_set_child(GTK_BUTTON(chip), content);
      luma_ui_set_accessible_label(chip, self->tooltip != NULL ? self->tooltip : self->label);
      if (self->tooltip != NULL) gtk_widget_set_tooltip_text(chip, self->tooltip);
      if (self->action_name != NULL)
        gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(chip), self->action_name);
      g_signal_connect_object(chip, "clicked", G_CALLBACK(item_clicked), self, G_CONNECT_AFTER);
      g_object_set_data_full(G_OBJECT(chip), "luma-bar-item", g_object_ref(self), g_object_unref);
    }
    return chip;
  }
  case LUMA_BAR_ITEM_PROMPT:
    g_critical("a prompt belongs to the action center (luma_action_center_show_bar), not a free control");
    return NULL;
  case LUMA_BAR_ITEM_CONTEXT:
    g_critical("a context line belongs over an action center's bar (luma_action_center_show_bar), not a control");
    return NULL;
  case LUMA_BAR_ITEM_SEARCH:
  case LUMA_BAR_ITEM_MODES:
    g_critical("a stateful search or mode item belongs to an action center bar");
    return NULL;
  case LUMA_BAR_ITEM_WIDGET:
    g_return_val_if_fail(self->hosted != NULL && gtk_widget_get_parent(self->hosted) == NULL, NULL);
    return self->hosted;
  case LUMA_BAR_ITEM_ACTION:
  default:
    break;
  }
  if (self->label == NULL && self->tooltip == NULL)
    g_critical("an icon-only action needs a tooltip to name it ('%s')", self->icon);
  GtkWidget *button = gtk_button_new();
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  gtk_widget_set_sensitive(button, self->sensitive);
  gtk_widget_add_css_class(button, "lumaui-bar-button");
  gtk_widget_add_css_class(button, size);
  if (self->text_glyph)
    gtk_widget_add_css_class(button, "text-glyph");
  if (self->primary)
    gtk_widget_add_css_class(button, "primary");
  if (self->danger)
    gtk_widget_add_css_class(button, "danger");
  if (self->active)
    gtk_widget_add_css_class(button, "on");
  if (self->keep_label || self->dropdown)
    gtk_widget_add_css_class(button, "keep-label");
  if (self->dropdown) {
    GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_halign(line, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(line, "lumaui-bar-button-content");
    gtk_widget_add_css_class(button, "dropdown");
    luma_ui_set_css_class(button, "compact-picker", self->compact_picker);
    luma_ui_set_css_class(button, "view-picker", self->view_picker);
    if (self->icon != NULL)
      gtk_box_append(GTK_BOX(line), luma_ui_icon_image(self->icon, 0));
    GtkWidget *words = gtk_label_new(self->label != NULL ? self->label : self->tooltip);
    gtk_label_set_ellipsize(GTK_LABEL(words), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(words, "lumaui-bar-button-label");
    gtk_box_append(GTK_BOX(line), words);
    GtkWidget *chevron = luma_ui_icon_image("chevron-down", 0);
    gtk_widget_add_css_class(chevron, "lumaui-bar-dropdown-chevron");
    gtk_box_append(GTK_BOX(line), chevron);
    gtk_button_set_child(GTK_BUTTON(button), line);
  } else if (self->icon == NULL) { /* a word only: v70 .bt with no glyph, as Python's BarAction("", "Keep") */
    gtk_widget_add_css_class(button, "text");
    gtk_button_set_child(GTK_BUTTON(button), gtk_label_new(self->label));
  } else if (self->label != NULL) {
    GtkWidget *glyph = luma_ui_icon_image(self->icon, 0);
    GtkWidget *line = gtk_box_new(g_str_equal(size, "caption") ? GTK_ORIENTATION_VERTICAL : GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_halign(line, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(line, "lumaui-bar-button-content");
    if (!self->icon_trailing) gtk_box_append(GTK_BOX(line), glyph);
    GtkWidget *text = gtk_label_new(self->label);
    gtk_widget_add_css_class(text, "lumaui-bar-button-label");
    g_set_weak_pointer(&self->phone_label, text);
    gtk_box_append(GTK_BOX(line), text);
    if (self->icon_trailing) gtk_box_append(GTK_BOX(line), glyph);
    gtk_button_set_child(GTK_BUTTON(button), line);
    gtk_widget_add_css_class(button, "labelled");
  } else {
    gtk_widget_add_css_class(button, "icon");
    gtk_button_set_child(GTK_BUTTON(button), luma_ui_icon_image(self->icon, 0));
    gtk_widget_remove_css_class(button, "image-button");
  }
  gtk_widget_remove_css_class(button, "image-button"); /* the bar owns its button treatment */
  const char *name = self->tooltip != NULL ? self->tooltip : self->label != NULL ? self->label : self->icon;
  if (self->tooltip != NULL)
    gtk_widget_set_tooltip_text(button, self->tooltip);
  luma_ui_set_accessible_label(button, name);
  if (self->active)
    gtk_accessible_update_state(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_STATE_PRESSED, GTK_ACCESSIBLE_TRISTATE_TRUE,
                                -1);
  if (self->action_name != NULL)
    gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(button), self->action_name);
  g_signal_connect_object(button, "clicked", G_CALLBACK(item_clicked), self, G_CONNECT_AFTER);
  g_object_set_data_full(G_OBJECT(button), "luma-bar-item", g_object_ref(self), g_object_unref);
  g_set_weak_pointer(&self->active_control, button);
  return button;
}

/* One primary per bar. */
static void check_primaries(LumaBarItem *const *items, guint n) {
  guint primaries = 0;
  for (guint i = 0; i < n; i++)
    if (LUMA_IS_BAR_ITEM(items[i]) && items[i]->kind == LUMA_BAR_ITEM_ACTION && items[i]->primary)
      primaries++;
  if (primaries > 1)
    g_critical("a bar has one primary action (the key); this one has %u", primaries);
}

/* ── LumaActionEditor ─────────────────────────────────────────────────── */

enum { EDITOR_MODE_CHANGED, EDITOR_DISCARDED, N_EDITOR_SIGNALS };
static guint editor_signals[N_EDITOR_SIGNALS];

typedef struct {
  char *key, *label, *icon;
} Mode;

static void mode_free(Mode *mode) {
  g_free(mode->key);
  g_free(mode->label);
  g_free(mode->icon);
  g_free(mode);
}

struct _LumaActionEditor {
  GtkBox parent_instance;
  char *title, *icon, *mode;
  GPtrArray *modes; /* Mode */
  GtkWidget *mode_button;
  GtkWidget *summary;
  GtkWidget *header;
  GtkWidget *last_field; /* the last field row, or NULL */
  GHashTable *fields;    /* label -> input */
  GtkWidget *tools;
  GtkWidget *text_view;
  GtkWidget *placeholder;
  GtkWidget *body;
  GtkWidget *scroller;
  GtkWidget *footer;
  GtkWidget *hint;
  GtkWidget *discard_button;
  GtkWidget *primary_button;
  GtkWidget *cancel_button; /* a form's Cancel, or NULL */
  gboolean form;            /* v70 Settings' sheets (cfSheet): fields and a Cancel, no composer or hint */
  LumaBarItem *primary;
  gboolean submit_on_return;
  char *draft_summary;
  LumaActionCenter *center; /* weak */
};

G_DEFINE_FINAL_TYPE(LumaActionEditor, luma_action_editor, GTK_TYPE_BOX)

static void action_center_cap_text(LumaActionCenter *self);
static void editor_discard_clicked(GtkButton *button, gpointer user_data);

static void luma_action_editor_dispose(GObject *object) {
  LumaActionEditor *self = LUMA_ACTION_EDITOR(object);
  g_clear_weak_pointer(&self->center);
  g_clear_object(&self->primary);
  G_OBJECT_CLASS(luma_action_editor_parent_class)->dispose(object);
}

static void luma_action_editor_finalize(GObject *object) {
  LumaActionEditor *self = LUMA_ACTION_EDITOR(object);
  g_free(self->title);
  g_free(self->icon);
  g_free(self->mode);
  g_free(self->draft_summary);
  g_ptr_array_unref(self->modes);
  g_hash_table_unref(self->fields);
  G_OBJECT_CLASS(luma_action_editor_parent_class)->finalize(object);
}

static void editor_pick_mode(GtkWidget *widget, const char *action G_GNUC_UNUSED, GVariant *parameter) {
  LumaActionEditor *self = LUMA_ACTION_EDITOR(widget);
  const char *key = g_variant_get_string(parameter, NULL);
  luma_action_editor_set_mode(self, key);
  g_signal_emit(self, editor_signals[EDITOR_MODE_CHANGED], 0, key);
}

static void luma_action_editor_class_init(LumaActionEditorClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_action_editor_dispose;
  G_OBJECT_CLASS(klass)->finalize = luma_action_editor_finalize;
  /* The mode menu's rows pick through this (FloatingMenu rows are actions). */
  gtk_widget_class_install_action(GTK_WIDGET_CLASS(klass), "lumaui-editor.mode", "s", editor_pick_mode);
  /**
   * LumaActionEditor::mode-changed:
   * @self: the editor
   * @key: the mode chosen from the header's menu
   */
  editor_signals[EDITOR_MODE_CHANGED] = g_signal_new("mode-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                                                     0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaActionEditor::discarded:
   * @self: the editor
   *
   * Discard was pressed: the draft is already cleared.
   */
  editor_signals[EDITOR_DISCARDED] = g_signal_new("discarded", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                                  NULL, NULL, NULL, G_TYPE_NONE, 0);
}

static void editor_fill_mode(LumaActionEditor *self) {
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(line, "lumaui-ac-mode-content");
  GtkWidget *glyph = luma_ui_icon_image(self->icon, 0);
  gtk_widget_add_css_class(glyph, "lumaui-ac-mode-icon");
  gtk_box_append(GTK_BOX(line), glyph);
  gtk_box_append(GTK_BOX(line), gtk_label_new(self->title));
  if (self->modes->len > 0) {
    GtkWidget *chevron = luma_ui_icon_image("chevron-down", 0);
    gtk_widget_add_css_class(chevron, "lumaui-ac-mode-chevron");
    gtk_box_append(GTK_BOX(line), chevron);
  }
  gtk_button_set_child(GTK_BUTTON(self->mode_button), line);
  luma_ui_set_accessible_label(self->mode_button, self->title);
}

static void editor_choose_mode(GtkButton *button, gpointer user_data) {
  LumaActionEditor *self = LUMA_ACTION_EDITOR(user_data);
  if (self->modes->len == 0)
    return;
  GtkWidget *menu = luma_floating_menu_new("Mode");
  for (guint i = 0; i < self->modes->len; i++) {
    Mode *mode = g_ptr_array_index(self->modes, i);
    g_autofree char *action = g_strdup_printf("lumaui-editor.mode('%s')", mode->key);
    luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), mode->label, mode->icon, NULL, NULL, action,
                                g_strcmp0(mode->key, self->mode) == 0);
  }
  luma_floating_menu_popup(LUMA_FLOATING_MENU(menu), GTK_WIDGET(button));
}

static void editor_text_changed(GtkTextBuffer *buffer, gpointer user_data) {
  LumaActionEditor *self = LUMA_ACTION_EDITOR(user_data);
  if (self->placeholder != NULL)
    gtk_widget_set_visible(self->placeholder, gtk_text_buffer_get_char_count(buffer) == 0);
  if (self->center != NULL)
    action_center_cap_text(self->center);
}

static GtkWidget *editor_composer(LumaActionEditor *self, const char *placeholder) {
  self->text_view = gtk_text_view_new();
  GtkTextView *view = GTK_TEXT_VIEW(self->text_view);
  gtk_text_view_set_wrap_mode(view, GTK_WRAP_WORD_CHAR);
  gtk_text_view_set_accepts_tab(view, FALSE);
  gtk_widget_set_hexpand(self->text_view, TRUE);
  gtk_text_view_set_left_margin(view, LUMA_UI_ACTION_CENTER_TEXT_PADDING_X);
  gtk_text_view_set_right_margin(view, LUMA_UI_ACTION_CENTER_TEXT_PADDING_X);
  gtk_text_view_set_top_margin(view, LUMA_UI_ACTION_CENTER_TEXT_PADDING_TOP);
  gtk_text_view_set_bottom_margin(view, LUMA_UI_ACTION_CENTER_TEXT_PADDING_BOTTOM);
  gtk_widget_add_css_class(self->text_view, "lumaui-ac-text");
  self->placeholder = gtk_label_new(NULL);
  gtk_label_set_xalign(GTK_LABEL(self->placeholder), 0);
  gtk_widget_set_valign(self->placeholder, GTK_ALIGN_START);
  gtk_widget_set_halign(self->placeholder, GTK_ALIGN_START);
  gtk_widget_set_can_target(self->placeholder, FALSE);
  gtk_widget_add_css_class(self->placeholder, "lumaui-ac-placeholder");
  luma_action_editor_set_placeholder(self, placeholder);
  GtkWidget *overlay = gtk_overlay_new();
  gtk_overlay_set_child(GTK_OVERLAY(overlay), self->text_view);
  gtk_overlay_add_overlay(GTK_OVERLAY(overlay), self->placeholder);
  g_signal_connect_object(gtk_text_view_get_buffer(view), "changed", G_CALLBACK(editor_text_changed), self, 0);
  gtk_widget_add_css_class(GTK_WIDGET(self), "composer");
  return overlay;
}

static void luma_action_editor_init(LumaActionEditor *self) {
  luma_ui_install();
  self->modes = g_ptr_array_new_with_free_func((GDestroyNotify)mode_free);
  self->fields = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-ac-editor-content");

  self->header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->header, "lumaui-ac-header");
  self->mode_button = gtk_button_new();
  gtk_widget_set_valign(self->mode_button, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(self->mode_button, "lumaui-ac-mode");
  gtk_widget_add_css_class(self->mode_button, "static");
  gtk_widget_set_can_focus(self->mode_button, FALSE);
  gtk_widget_set_can_target(self->mode_button, FALSE);
  g_signal_connect(self->mode_button, "clicked", G_CALLBACK(editor_choose_mode), self);
  gtk_box_append(GTK_BOX(self->header), self->mode_button);
  self->summary = gtk_label_new(NULL);
  gtk_label_set_xalign(GTK_LABEL(self->summary), 0);
  gtk_widget_set_hexpand(self->summary, TRUE);
  gtk_label_set_ellipsize(GTK_LABEL(self->summary), PANGO_ELLIPSIZE_END);
  gtk_widget_add_css_class(self->summary, "lumaui-ac-summary");
  gtk_widget_set_visible(self->summary, FALSE);
  gtk_box_append(GTK_BOX(self->header), self->summary);
  gtk_box_append(GTK_BOX(self), self->header);

  self->scroller = gtk_scrolled_window_new();
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(self->scroller), GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
  gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(self->scroller), TRUE);
  gtk_widget_set_vexpand(self->scroller, TRUE);
  gtk_widget_add_css_class(self->scroller, "lumaui-ac-body");
  self->body = editor_composer(self, "Write something");
  gtk_scrolled_window_set_min_content_height(GTK_SCROLLED_WINDOW(self->scroller),
                                             LUMA_UI_ACTION_CENTER_TEXT_MIN_HEIGHT);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->scroller), self->body);
  gtk_box_append(GTK_BOX(self), self->scroller);

  self->footer = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->footer, "lumaui-ac-footer");
  g_autoptr(LumaBarItem) discard = luma_bar_item_new_action("trash-2", NULL, NULL);
  luma_bar_item_set_tooltip(discard, "Discard");
  self->discard_button = luma_bar_item_create_control(discard, "bar");
  g_signal_connect(self->discard_button, "clicked", G_CALLBACK(editor_discard_clicked), self);
  gtk_box_append(GTK_BOX(self->footer), self->discard_button);
  GtkWidget *room = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_hexpand(room, TRUE);
  gtk_box_append(GTK_BOX(self->footer), room);
  self->hint = gtk_label_new("Esc to fold");
  gtk_widget_add_css_class(self->hint, "lumaui-ac-hint");
  gtk_box_append(GTK_BOX(self->footer), self->hint);
  gtk_box_append(GTK_BOX(self), self->footer);
}

GtkWidget *luma_action_editor_new(const char *title, const char *icon) {
  g_return_val_if_fail(title != NULL, NULL);
  g_return_val_if_fail(icon != NULL && *icon != '\0', NULL);
  LumaActionEditor *self = g_object_new(LUMA_TYPE_ACTION_EDITOR, NULL);
  self->title = g_strdup(title);
  self->icon = g_strdup(icon);
  editor_fill_mode(self);
  return GTK_WIDGET(self);
}

void luma_action_editor_set_summary(LumaActionEditor *self, const char *text, const char *emphasis) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  gboolean has = text != NULL && *text != '\0';
  g_autofree char *markup = has ? markup_with(text, emphasis) : g_strdup("");
  gtk_label_set_markup(GTK_LABEL(self->summary), markup);
  gtk_widget_set_visible(self->summary, has);
}

void luma_action_editor_add_mode(LumaActionEditor *self, const char *key, const char *label, const char *icon) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  g_return_if_fail(key != NULL && label != NULL && icon != NULL);
  Mode *mode = g_new0(Mode, 1);
  mode->key = g_strdup(key);
  mode->label = g_strdup(label);
  mode->icon = g_strdup(icon);
  g_ptr_array_add(self->modes, mode);
  if (self->modes->len == 1) {
    gtk_widget_remove_css_class(self->mode_button, "static");
    gtk_widget_set_can_focus(self->mode_button, TRUE);
    gtk_widget_set_can_target(self->mode_button, TRUE);
    gtk_widget_set_tooltip_text(self->mode_button, "Change");
    editor_fill_mode(self);
  }
}

void luma_action_editor_set_mode(LumaActionEditor *self, const char *key) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  for (guint i = 0; i < self->modes->len; i++) {
    Mode *mode = g_ptr_array_index(self->modes, i);
    if (g_strcmp0(mode->key, key) == 0) {
      g_free(self->mode);
      self->mode = g_strdup(mode->key);
      g_free(self->title);
      self->title = g_strdup(mode->label);
      g_free(self->icon);
      self->icon = g_strdup(mode->icon);
      editor_fill_mode(self);
      return;
    }
  }
  g_critical("the editor has no mode '%s'", key);
}

static void form_cancel_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaActionEditor *self = LUMA_ACTION_EDITOR(user_data);
  if (self->center != NULL)
    luma_action_center_fold(self->center); /* as Esc: the form keeps what was typed until it is sent */
}

static void form_field_activated(GtkWidget *input G_GNUC_UNUSED, gpointer user_data) {
  LumaActionEditor *self = LUMA_ACTION_EDITOR(user_data);
  if (self->form)
    luma_action_editor_run_primary(self); /* Enter is the primary */
}

void luma_action_editor_set_form(LumaActionEditor *self, const char *cancel_label) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  self->form = TRUE;
  gtk_widget_add_css_class(GTK_WIDGET(self), "form");
  gtk_widget_set_visible(self->discard_button, FALSE);
  gtk_widget_set_visible(self->hint, FALSE);
  /* The summary says what the sheet is for, whole: it wraps (v70 .lach span, lh 1.35). */
  gtk_label_set_ellipsize(GTK_LABEL(self->summary), PANGO_ELLIPSIZE_NONE);
  gtk_label_set_wrap(GTK_LABEL(self->summary), TRUE);
  gtk_label_set_max_width_chars(GTK_LABEL(self->summary), 1);
  gtk_widget_set_valign(self->summary, GTK_ALIGN_CENTER); /* its own lines' box, centred in the 48 row */
  /* No composer: fields, and a body only when the app gives one (set_body). */
  if (self->text_view != NULL) {
    gtk_widget_set_visible(self->scroller, FALSE);
    gtk_scrolled_window_set_min_content_height(GTK_SCROLLED_WINDOW(self->scroller), 0);
  }
  if (self->cancel_button != NULL) {
    gtk_box_remove(GTK_BOX(self->footer), self->cancel_button);
    self->cancel_button = NULL;
  }
  if (cancel_label != NULL && *cancel_label != '\0') {
    self->cancel_button = luma_text_button_new(cancel_label, NULL, "fill");
    gtk_widget_add_css_class(self->cancel_button, "lumaui-ac-cancel");
    g_signal_connect(self->cancel_button, "clicked", G_CALLBACK(form_cancel_clicked), self);
    if (self->primary_button != NULL)
      gtk_box_insert_child_after(GTK_BOX(self->footer), self->cancel_button,
                                 gtk_widget_get_prev_sibling(self->primary_button));
    else
      gtk_box_append(GTK_BOX(self->footer), self->cancel_button);
  }
}

GtkWidget *luma_action_editor_add_field(LumaActionEditor *self, const char *label, GtkWidget *input) {
  g_return_val_if_fail(LUMA_IS_ACTION_EDITOR(self), NULL);
  g_return_val_if_fail(label != NULL, NULL);
  g_return_val_if_fail(input == NULL || GTK_IS_WIDGET(input), NULL);
  GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(row, "lumaui-ac-field");
  GtkWidget *caption = gtk_label_new(label);
  gtk_label_set_xalign(GTK_LABEL(caption), 0);
  gtk_widget_add_css_class(caption, "lumaui-ac-field-label");
  gtk_box_append(GTK_BOX(row), caption);
  if (input == NULL) {
    input = gtk_entry_new();
    gtk_widget_set_hexpand(input, TRUE);
  }
  gtk_widget_set_hexpand(input, TRUE);
  gtk_widget_add_css_class(input, "lumaui-ac-field-input");
  gtk_accessible_update_relation(GTK_ACCESSIBLE(input), GTK_ACCESSIBLE_RELATION_LABELLED_BY, caption, NULL, -1);
  if (GTK_IS_ENTRY(input) || GTK_IS_TEXT(input) || GTK_IS_PASSWORD_ENTRY(input))
    g_signal_connect(input, "activate", G_CALLBACK(form_field_activated), self);
  gtk_box_append(GTK_BOX(row), input);
  g_hash_table_replace(self->fields, g_strdup(label), input);
  gtk_box_insert_child_after(GTK_BOX(self), row, self->last_field != NULL ? self->last_field : self->header);
  self->last_field = row;
  return input;
}

GtkWidget *luma_action_editor_get_field(LumaActionEditor *self, const char *label) {
  g_return_val_if_fail(LUMA_IS_ACTION_EDITOR(self), NULL);
  return label != NULL ? g_hash_table_lookup(self->fields, label) : NULL;
}

void luma_action_editor_add_tool(LumaActionEditor *self, LumaBarItem *tool) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  g_return_if_fail(LUMA_IS_BAR_ITEM(tool));
  GtkWidget *control = luma_bar_item_create_control(tool, "tool");
  if (control == NULL)
    return;
  if (self->tools == NULL) {
    self->tools = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(self->tools, "lumaui-ac-tools");
    gtk_box_insert_child_after(GTK_BOX(self), self->tools, self->last_field != NULL ? self->last_field : self->header);
  }
  gtk_box_append(GTK_BOX(self->tools), control);
}

void luma_action_editor_set_body(LumaActionEditor *self, GtkWidget *body) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  g_return_if_fail(body == NULL || GTK_IS_WIDGET(body));
  if (body == NULL && self->text_view != NULL)
    return; /* the composer already */
  g_autofree char *placeholder = NULL;
  if (self->placeholder != NULL)
    placeholder = g_strdup(gtk_label_get_label(GTK_LABEL(self->placeholder)));
  self->text_view = NULL;
  self->placeholder = NULL;
  gtk_widget_remove_css_class(GTK_WIDGET(self), "composer");
  if (body == NULL)
    body = editor_composer(self, placeholder != NULL ? placeholder : "Write something");
  self->body = body;
  if (self->form)
    gtk_widget_set_visible(self->scroller, self->text_view == NULL); /* a form shows a body only when it has one */
  gtk_scrolled_window_set_min_content_height(GTK_SCROLLED_WINDOW(self->scroller),
                                             self->text_view != NULL ? LUMA_UI_ACTION_CENTER_TEXT_MIN_HEIGHT : 0);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->scroller), body);
}

void luma_action_editor_set_placeholder(LumaActionEditor *self, const char *placeholder) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  if (placeholder == NULL)
    placeholder = "Write something";
  if (self->text_view == NULL || self->placeholder == NULL)
    return;
  gtk_label_set_label(GTK_LABEL(self->placeholder), placeholder);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->text_view), GTK_ACCESSIBLE_PROPERTY_PLACEHOLDER, placeholder,
                                 GTK_ACCESSIBLE_PROPERTY_LABEL, placeholder, -1);
}

void luma_action_editor_set_submit_on_return(LumaActionEditor *self, gboolean enabled) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  self->submit_on_return = enabled;
  g_autofree char *action = self->primary != NULL && self->primary->label != NULL
      ? g_utf8_strdown(self->primary->label, -1) : g_strdup("submit");
  g_autofree char *hint = self->primary == NULL ? g_strdup("Esc to fold")
      : enabled ? g_strdup_printf("Esc to fold · Return to %s", action)
      : g_strdup("Esc to fold · Ctrl Return");
  gtk_label_set_label(GTK_LABEL(self->hint), hint);
}

void luma_action_editor_set_primary(LumaActionEditor *self, LumaBarItem *primary) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  g_return_if_fail(primary == NULL || LUMA_IS_BAR_ITEM(primary));
  if (self->primary_button != NULL) {
    gtk_box_remove(GTK_BOX(self->footer), self->primary_button);
    self->primary_button = NULL;
  }
  g_set_object(&self->primary, primary);
  luma_action_editor_set_submit_on_return(self, self->submit_on_return);
  if (primary == NULL)
    return;
  primary->primary = TRUE;
  self->primary_button = luma_bar_item_create_control(primary, "bar");
  if (self->primary_button != NULL)
    gtk_box_append(GTK_BOX(self->footer), self->primary_button); /* after a form's Cancel */
}

void luma_action_editor_set_draft_summary(LumaActionEditor *self, const char *summary) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  g_free(self->draft_summary);
  self->draft_summary = g_strdup(summary);
}

char *luma_action_editor_get_draft(LumaActionEditor *self) {
  g_return_val_if_fail(LUMA_IS_ACTION_EDITOR(self), NULL);
  if (self->draft_summary != NULL)
    return *self->draft_summary != '\0' ? g_strdup(self->draft_summary) : NULL;
  if (self->text_view == NULL)
    return NULL;
  GtkTextBuffer *buffer = gtk_text_view_get_buffer(GTK_TEXT_VIEW(self->text_view));
  GtkTextIter start, end;
  gtk_text_buffer_get_bounds(buffer, &start, &end);
  char *text = gtk_text_buffer_get_text(buffer, &start, &end, FALSE);
  if (*text == '\0')
    g_clear_pointer(&text, g_free);
  return text;
}

void luma_action_editor_clear(LumaActionEditor *self) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  if (self->text_view != NULL)
    gtk_text_buffer_set_text(gtk_text_view_get_buffer(GTK_TEXT_VIEW(self->text_view)), "", -1);
}

static GtkWidget *first_focusable(GtkWidget *widget) {
  if (widget == NULL)
    return NULL;
  if (gtk_widget_get_focusable(widget) && gtk_widget_get_can_focus(widget) && gtk_widget_is_sensitive(widget) &&
      gtk_widget_get_visible(widget))
    return widget;
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child != NULL; child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = first_focusable(child);
    if (found != NULL)
      return found;
  }
  return NULL;
}

void luma_action_editor_focus_content(LumaActionEditor *self) {
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(self));
  GtkWidget *target = self->text_view;
  if (target == NULL)
    target = first_focusable(self->body);
  if (target == NULL)
    target = self->primary_button;
  if (target != NULL)
    gtk_widget_grab_focus(target);
}

gboolean luma_action_editor_run_primary(LumaActionEditor *self) {
  g_return_val_if_fail(LUMA_IS_ACTION_EDITOR(self), FALSE);
  if (self->primary == NULL || self->primary_button == NULL || !gtk_widget_is_sensitive(self->primary_button))
    return FALSE;
  g_signal_emit_by_name(self->primary_button, "clicked");
  return TRUE;
}

/* ── LumaActionCenter ─────────────────────────────────────────────────── */

static const char *const ac_states[] = {"hidden", "bar", "double", "editor", "split", NULL};

enum { STATE_CHANGED, GROWN_CHANGED, PANEL_CHANGED, RELEASED, N_CENTER_SIGNALS };
static guint center_signals[N_CENTER_SIGNALS];
enum { PROP_0, PROP_STATE, PROP_GROWN, N_PROPS };
static GParamSpec *center_props[N_PROPS];

/* v71 (LUMA ON A PHONE): the phone bar is 6 all round, 26 corners, 34 above the foot, on the 16 px
 * gutter when it is full width; a grown bar in a window is min(380, width − 24); 20 px of air between
 * the last line of a scroller and the bar. */
#define AC_PHONE_BOTTOM 34
#define AC_PHONE_GUTTER 16
#define AC_GROWN_WIDTH 380
#define AC_GROWN_ROOM 24
#define AC_SAFE_GAP 20

typedef struct {
  GtkScrolledWindow *scroller; /* weak */
  int room;
} SafeScroller;

static void safe_scroller_free(gpointer data) {
  SafeScroller *entry = data;
  g_clear_weak_pointer(&entry->scroller);
  g_free(entry);
}

struct _LumaActionCenter {
  GtkBox parent_instance;
  const char *state; /* one of ac_states */
  GtkWidget *host;   /* weak */
  gulong position_handler;
  gboolean phone;
  int last_host_width;
  LumaBarItem *prompt;
  GtkWidget *prompt_button;
  GtkWidget *prompt_label;
  int text_cap;
  int bar_bottom;
  int phone_grown_inset;
  gboolean phone_wide;
  LumaActionEditor *editor;
  GtkWidget *bar, *context_line, *head_row, *bar_row, *card, *split;
  /* v71: the bar grows */
  GtkWidget *panel_slot;  /* the panel above the row, in the bar's glass */
  GtkWidget *entry_row;   /* a field that replaces the row (grow_entry, search) */
  char *grown_key;
  GtkWidget *grown_control; /* weak: the action whose panel is open */
  GPtrArray *items;         /* LumaBarItem, the bar's own, in order */
  GPtrArray *overflow;      /* LumaBarItem the bar put in ⋯ at phone width */
  GtkWidget *more_button;
  guint fit_source;
  /* holding (Move) */
  gboolean holding;
  GtkWidget *hold_row, *foot_row;
  char *hold_prompt;
  /* searching in place */
  gboolean searching;
  gboolean entry_from_panel; /* the entry row is grow_entry()'s field */
  LumaBarItem *search_item;
  /* the one safe area */
  GPtrArray *scrollers; /* SafeScroller, each on the heap (it holds a weak pointer) */
  guint safe_source;
  GPtrArray *shown_items; /* own every displayed item while its control is parented */
  GPtrArray *bar_controls; /* borrowed children, reset before each bar rebuild */
};

G_DEFINE_FINAL_TYPE(LumaActionCenter, luma_action_center, GTK_TYPE_BOX)

static void center_fit(LumaActionCenter *self);
static void center_schedule_safe(LumaActionCenter *self);
static void center_fold_panel(LumaActionCenter *self, gboolean restore_focus);
static void center_close_search(LumaActionCenter *self);

static gboolean center_is_phone_width(int width) {
  return width > 0 && luma_ui_tier_for_width(width) == LUMA_TIER_PHONE;
}

static void center_forget_host(LumaActionCenter *self) {
  if (self->host != NULL && self->position_handler != 0)
    g_signal_handler_disconnect(self->host, self->position_handler);
  self->position_handler = 0;
  g_clear_weak_pointer(&self->host);
}

static void luma_action_center_dispose(GObject *object) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(object);
  center_forget_host(self);
  g_clear_handle_id(&self->fit_source, g_source_remove);
  g_clear_handle_id(&self->safe_source, g_source_remove);
  g_clear_object(&self->prompt);
  g_clear_object(&self->search_item);
  g_clear_weak_pointer(&self->grown_control);
  g_clear_pointer(&self->scrollers, g_ptr_array_unref);
  g_clear_pointer(&self->items, g_ptr_array_unref);
  g_clear_pointer(&self->overflow, g_ptr_array_unref);
  g_clear_pointer(&self->shown_items, g_ptr_array_unref);
  g_clear_pointer(&self->bar_controls, g_ptr_array_unref);
  self->editor = NULL;
  G_OBJECT_CLASS(luma_action_center_parent_class)->dispose(object);
}

static void luma_action_center_finalize(GObject *object) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(object);
  g_free(self->grown_key);
  g_free(self->hold_prompt);
  G_OBJECT_CLASS(luma_action_center_parent_class)->finalize(object);
}

static void luma_action_center_get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  if (id == PROP_STATE)
    g_value_set_string(value, LUMA_ACTION_CENTER(object)->state);
  else if (id == PROP_GROWN)
    g_value_set_string(value, LUMA_ACTION_CENTER(object)->grown_key);
  else
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
}

static void luma_action_center_class_init(LumaActionCenterClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->dispose = luma_action_center_dispose;
  object_class->finalize = luma_action_center_finalize;
  object_class->get_property = luma_action_center_get_property;
  /**
   * LumaActionCenter:state:
   *
   * "hidden", "bar", "double", "editor" or "split".
   */
  center_props[PROP_STATE] = g_param_spec_string("state", NULL, NULL, "hidden",
                                                 G_PARAM_READABLE | G_PARAM_STATIC_STRINGS);
  /**
   * LumaActionCenter:grown:
   *
   * The key of the panel the bar has grown into, or %NULL.
   */
  center_props[PROP_GROWN] = g_param_spec_string("grown", NULL, NULL, NULL,
                                                 G_PARAM_READABLE | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, N_PROPS, center_props);
  /**
   * LumaActionCenter::state-changed:
   * @self: the action center
   * @state: the state it moved to
   */
  center_signals[STATE_CHANGED] = g_signal_new("state-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_FIRST, 0,
                                               NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaActionCenter::grown-changed:
   * @self: the action center
   * @key: (nullable): the panel now open, %NULL when it folded
   */
  center_signals[GROWN_CHANGED] = g_signal_new("grown-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_FIRST, 0,
                                               NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaActionCenter::panel-changed:
   * @self: the action center
   * @key: the panel now open, "" when it folded (Python's signal; #LumaActionCenter::grown-changed with NULL)
   */
  center_signals[PANEL_CHANGED] = g_signal_new("panel-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_FIRST, 0,
                                               NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaActionCenter::released:
   * @self: the action center
   *
   * The held thing's ✕ was pressed: let go of it (Python `on_release`).
   */
  center_signals[RELEASED] = g_signal_new("released", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_FIRST, 0, NULL, NULL,
                                          NULL, G_TYPE_NONE, 0);
}

static gboolean center_editor_key(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval,
                                  guint code G_GNUC_UNUSED, GdkModifierType state, gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  if (keyval == GDK_KEY_Escape) {
    luma_action_center_fold(self);
    return TRUE;
  }
  if ((keyval == GDK_KEY_Return || keyval == GDK_KEY_KP_Enter) &&
      ((state & GDK_CONTROL_MASK) || (self->editor != NULL && self->editor->submit_on_return &&
       !(state & (GDK_SHIFT_MASK | GDK_ALT_MASK | GDK_SUPER_MASK)))))
    return self->editor != NULL ? luma_action_editor_run_primary(self->editor) : FALSE;
  return FALSE;
}

/* Esc folds a grown bar, closes the search or lets go of nothing (holding stays until ✕). */
static gboolean center_bar_key(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval,
                               guint code G_GNUC_UNUSED, GdkModifierType state G_GNUC_UNUSED, gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  if (keyval != GDK_KEY_Escape)
    return FALSE;
  if (self->grown_key != NULL) {
    center_fold_panel(self, TRUE);
    return TRUE;
  }
  if (self->searching) {
    center_close_search(self);
    return TRUE;
  }
  return FALSE;
}

static void luma_action_center_init(LumaActionCenter *self) {
  luma_ui_install();
  self->state = ac_states[0];
  self->items = g_ptr_array_new_with_free_func(g_object_unref);
  self->overflow = g_ptr_array_new_with_free_func(g_object_unref);
  self->scrollers = g_ptr_array_new_with_free_func(safe_scroller_free);
  self->shown_items = g_ptr_array_new_with_free_func(g_object_unref);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_set_halign(GTK_WIDGET(self), GTK_ALIGN_FILL);
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_FILL);
  gtk_widget_set_visible(GTK_WIDGET(self), FALSE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-action-center");
  self->bar_bottom = LUMA_UI_ACTION_CENTER_BAR_BOTTOM;
  self->phone_grown_inset = -1;
  self->bar_controls = g_ptr_array_new();

  self->bar = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "accessible-role",
                           GTK_ACCESSIBLE_ROLE_TOOLBAR, NULL);
  gtk_widget_add_css_class(self->bar, "lumaui-ac-bar");
  luma_ui_set_accessible_label(self->bar, "Actions");
  /* The bar, as Python's: the panel it grows into (above the row, in the same glass), the held row, the
   * double-height context line, the row, a two-row bar's foot, and the field that replaces the row (always
   * the bottom-most thing). The occasional ones are hidden until used. */
  self->panel_slot = g_object_new(GTK_TYPE_SCROLLED_WINDOW, "hscrollbar-policy", GTK_POLICY_NEVER,
                                  "propagate-natural-height", TRUE, "visible", FALSE, NULL);
  gtk_widget_add_css_class(self->panel_slot, "lumaui-ac-panel");
  self->hold_row = g_object_new(GTK_TYPE_BOX, "visible", FALSE, NULL);
  gtk_widget_add_css_class(self->hold_row, "lumaui-ac-hold");
  self->context_line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_visible(self->context_line, FALSE);
  gtk_widget_add_css_class(self->context_line, "lumaui-ac-context");
  self->head_row = g_object_new(GTK_TYPE_BOX, "visible", FALSE, NULL);
  gtk_widget_add_css_class(self->head_row, "lumaui-ac-head");
  self->bar_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->bar_row, "lumaui-ac-row");
  self->foot_row = g_object_new(GTK_TYPE_BOX, "visible", FALSE, NULL);
  gtk_widget_add_css_class(self->foot_row, "lumaui-ac-foot");
  self->entry_row = g_object_new(GTK_TYPE_BOX, "visible", FALSE, NULL);
  gtk_widget_add_css_class(self->entry_row, "lumaui-ac-row");
  gtk_widget_add_css_class(self->entry_row, "entry");
  GtkWidget *parts[] = {self->panel_slot, self->hold_row, self->context_line, self->head_row, self->bar_row, self->foot_row,
                        self->entry_row};
  for (guint i = 0; i < G_N_ELEMENTS(parts); i++)
    gtk_box_append(GTK_BOX(self->bar), parts[i]);
  GtkEventController *bar_keys = gtk_event_controller_key_new();
  g_signal_connect(bar_keys, "key-pressed", G_CALLBACK(center_bar_key), self);
  gtk_widget_add_controller(self->bar, bar_keys);

  /* The editor card. */
  self->card = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "visible", FALSE,
                            "accessible-role", GTK_ACCESSIBLE_ROLE_GROUP, NULL);
  gtk_widget_add_css_class(self->card, "lumaui-ac-editor");
  GtkEventController *keys = gtk_event_controller_key_new();
  gtk_event_controller_set_propagation_phase(keys, GTK_PHASE_CAPTURE);
  g_signal_connect(keys, "key-pressed", G_CALLBACK(center_editor_key), self);
  gtk_widget_add_controller(self->card, keys);

  /* Split: two bars. */
  self->split = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_visible(self->split, FALSE);
  gtk_widget_add_css_class(self->split, "lumaui-ac-split");

  gtk_box_append(GTK_BOX(self), self->bar);
  gtk_box_append(GTK_BOX(self), self->card);
  gtk_box_append(GTK_BOX(self), self->split);
}

GtkWidget *luma_action_center_new(LumaActionEditor *editor) {
  g_return_val_if_fail(editor == NULL || LUMA_IS_ACTION_EDITOR(editor), NULL);
  GtkWidget *self = g_object_new(LUMA_TYPE_ACTION_CENTER, NULL);
  if (editor != NULL)
    luma_action_center_set_editor(LUMA_ACTION_CENTER(self), editor);
  return self;
}

/* Show or hide one of the bar's occasional parts (they keep their places, as Python's). */
static void bar_part(LumaActionCenter *self G_GNUC_UNUSED, GtkWidget *part, gboolean on) {
  gtk_widget_set_visible(part, on);
}

static gboolean bar_part_shown(LumaActionCenter *self G_GNUC_UNUSED, GtkWidget *part) {
  return gtk_widget_get_visible(part);
}

static void panel_clear(LumaActionCenter *self) {
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->panel_slot), NULL);
  gtk_widget_remove_css_class(self->panel_slot, "shown");
}

static gboolean panel_shown(gpointer data) {
  gtk_widget_add_css_class(GTK_WIDGET(data), "shown");
  return G_SOURCE_REMOVE;
}

static gboolean center_is_grown(LumaActionCenter *self) {
  return self->grown_key != NULL || self->holding || self->searching;
}

/* layout(): (phone, width, margin top, margin bottom) for the current state
 * in a host this wide; width -1 is "as wide as the content". */
static void center_layout(LumaActionCenter *self, int width, gboolean *phone_out, int *wanted_out, int *top_out,
                          int *bottom_out) {
  gboolean phone = center_is_phone_width(width);
  const char *state = self->state;
  int side = LUMA_UI_ACTION_CENTER_SIDE_MARGIN;
  int top = LUMA_UI_ACTION_CENTER_EDITOR_TOP;
  int bottom = phone ? AC_PHONE_BOTTOM : self->bar_bottom;
  gboolean wide = g_str_equal(state, "double") ||
                  (g_str_equal(state, "bar") && gtk_widget_has_css_class(self->bar, "wide"));
  gboolean field = g_str_equal(state, "bar") && gtk_widget_has_css_class(self->bar, "has-field");
  int wanted;
  if (width <= 0) {
    wanted = -1;
  } else if (g_str_equal(state, "editor")) {
    /* v71: on a phone the action center is the bar grown, not a sheet: the 16 px gutter, 34 up. */
    wanted = phone ? width - 2 * AC_PHONE_GUTTER : MIN(LUMA_UI_ACTION_CENTER_EDITOR_WIDTH, width - 2 * side);
    top = phone ? LUMA_UI_ACTION_CENTER_PHONE_TOP : LUMA_UI_ACTION_CENTER_EDITOR_TOP;
    bottom = phone ? AC_PHONE_BOTTOM : LUMA_UI_ACTION_CENTER_EDITOR_BOTTOM;
  } else if (center_is_grown(self) && !g_str_equal(state, "split")) {
    int gutter = self->phone_grown_inset >= 0 ? self->phone_grown_inset : AC_PHONE_GUTTER;
    wanted = phone ? width - 2 * gutter : MIN(AC_GROWN_WIDTH, width - AC_GROWN_ROOM);
  } else if (phone && (self->phone_wide || wide || field || g_str_equal(state, "split"))) {
    wanted = width - 2 * AC_PHONE_GUTTER;
  } else if (wide) {
    wanted = MIN(LUMA_UI_ACTION_CENTER_DOUBLE_WIDTH, width - 2 * side);
  } else {
    wanted = -1;
  }
  *phone_out = phone;
  *wanted_out = MAX(-1, wanted);
  *top_out = top;
  *bottom_out = bottom;
}

static int center_host_width(LumaActionCenter *self) {
  GtkWidget *host = self->host != NULL ? self->host : gtk_widget_get_parent(GTK_WIDGET(self));
  return host != NULL ? gtk_widget_get_width(host) : 0;
}

/* v71 (Python _apply_width): on a phone a labelled action shows its glyph alone ("glyph-only") unless it
 * keeps its label (keep-label: Contacts' Edit, "Move here"). */
static void center_apply_labels(LumaActionCenter *self) {
  for (GtkWidget *c = gtk_widget_get_first_child(self->bar_row); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    if (!GTK_IS_BUTTON(c) || !gtk_widget_has_css_class(c, "labelled"))
      continue;
    GtkWidget *line = gtk_button_get_child(GTK_BUTTON(c));
    GtkWidget *words = line != NULL ? gtk_widget_get_last_child(line) : NULL;
    gboolean keep = gtk_widget_has_css_class(c, "keep-label");
    LumaBarItem *item = g_object_get_data(G_OBJECT(c), "luma-bar-item");
    if (GTK_IS_LABEL(words) && !(item != NULL && item->phone_compact))
      gtk_widget_set_visible(words, !self->phone || keep);
    luma_ui_set_css_class(c, "glyph-only", self->phone && !keep);
  }
}

/* Grown on a phone, the row's buttons share the width unless a field or a labelled key takes it; icons
 * stay 48 beside a key. */
static void center_apply_share(LumaActionCenter *self) {
  gboolean grown = self->grown_key != NULL || self->holding;
  gboolean takes = FALSE;
  gboolean spaced = FALSE;
  for (GtkWidget *c = gtk_widget_get_first_child(self->bar_row); c; c = gtk_widget_get_next_sibling(c))
    spaced |= gtk_widget_get_visible(c) && g_object_get_data(G_OBJECT(c), "lumaui-bar-spacer") != NULL;
  gboolean compact_pickers = FALSE;
  for (GtkWidget *c = gtk_widget_get_first_child(self->bar_row); c != NULL; c = gtk_widget_get_next_sibling(c))
    compact_pickers |= gtk_widget_has_css_class(c, "compact-picker") || gtk_widget_has_css_class(c, "view-picker");
  luma_ui_set_css_class(self->bar_row, "compact-pickers", compact_pickers);
  for (GtkWidget *c = gtk_widget_get_first_child(self->bar_row); c != NULL; c = gtk_widget_get_next_sibling(c))
    if (gtk_widget_get_visible(c) && (gtk_widget_has_css_class(c, "keep-label") ||
                                      gtk_widget_has_css_class(c, "lumaui-ac-prompt") ||
                                      gtk_widget_has_css_class(c, "lumaui-bar-search-host")))
      takes = TRUE;
  for (GtkWidget *c = gtk_widget_get_first_child(self->bar_row); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    if (!GTK_IS_BUTTON(c) || !gtk_widget_has_css_class(c, "lumaui-bar-button"))
      continue;
    gboolean share = gtk_widget_has_css_class(c, "dropdown") ? FALSE : gtk_widget_has_css_class(c, "keep-label") ? self->phone && grown
                                                               : self->phone && grown && !takes;
    if (spaced || g_object_get_data(G_OBJECT(c), "lumaui-panel-child") != NULL ||
        (gtk_widget_has_css_class(GTK_WIDGET(self), "document-toolbar") &&
         !gtk_widget_has_css_class(c, "fill")))
      share = FALSE;
    gtk_widget_set_hexpand(c, share);
  }
}

static void center_apply_width(LumaActionCenter *self) {
  gboolean phone;
  int wanted, top, bottom;
  center_layout(self, center_host_width(self), &phone, &wanted, &top, &bottom);
  self->phone = phone;
  luma_ui_set_css_class(GTK_WIDGET(self), "phone", phone);
  gboolean field = FALSE;
  for (guint i = 0; i < self->shown_items->len; i++) {
    LumaBarItem *item = g_ptr_array_index(self->shown_items, i);
    if (item->phone_compact && item->phone_label != NULL) {
      if (item->kind == LUMA_BAR_ITEM_CHIP) {
        /* Empty the measured label as well as its visible text: GTK can keep
         * the old ellipsized label's width during an in-flight bar layout. */
        gtk_label_set_text(GTK_LABEL(item->phone_label), phone ? "" : item->label);
        gtk_label_set_max_width_chars(GTK_LABEL(item->phone_label), phone ? 0 : 30);
      } else {
        gtk_widget_set_visible(item->phone_label, !phone);
      }
      if (item->kind == LUMA_BAR_ITEM_ACTION && item->active_control != NULL)
        luma_ui_set_css_class(item->active_control, "icon", phone);
    }
    if (item->kind != LUMA_BAR_ITEM_SEARCH || item->hosted == NULL) continue;
    /* v71: a collapsed search is its glyph at every width while empty, until pressed. */
    gboolean folded = ((phone && !item->search_keep) || item->collapsed) && item->search_text[0] == '\0' && !item->search_expanded;
    gtk_stack_set_visible_child(GTK_STACK(item->hosted), folded ? item->folded_button : item->search_entry);
    luma_ui_set_css_class(item->hosted, "folded", folded);
    gtk_widget_set_hexpand(item->hosted, !folded);
    gtk_widget_set_size_request(item->search_entry, phone ? 1 : LUMA_UI_BAR_SEARCH_WIDTH, LUMA_UI_BAR_SEARCH_HEIGHT);
    /* A field that stays in the row takes the bar's spare width (the full gutter on a phone). */
    field = field || (!folded && gtk_widget_get_parent(item->hosted) == self->bar_row);
  }
  luma_ui_set_css_class(self->bar, "has-field", field);
  center_apply_labels(self);
  center_apply_share(self);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self->split), phone ? GTK_ORIENTATION_VERTICAL
                                                                    : GTK_ORIENTATION_HORIZONTAL);
  /* Compact labels change the bar's minimum width, not only its placement. */
  gtk_widget_queue_resize(GTK_WIDGET(self));
  if (self->host != NULL)
    gtk_widget_queue_resize(self->host);
}

static int center_wanted_cap(LumaActionCenter *self) {
  GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
  int height = root != NULL ? gtk_widget_get_height(GTK_WIDGET(root)) : 0;
  if (height <= 0)
    return 0;
  return MAX(LUMA_UI_ACTION_CENTER_TEXT_MIN_HEIGHT, (int)(height * LUMA_UI_ACTION_CENTER_TEXT_MAX_PCT / 100.0));
}

static void action_center_cap_text(LumaActionCenter *self) {
  int cap = center_wanted_cap(self);
  if (self->editor != NULL && cap > 0 && cap != self->text_cap) {
    self->text_cap = cap;
    gtk_scrolled_window_set_max_content_height(GTK_SCROLLED_WINDOW(self->editor->scroller), cap);
  }
}

static gboolean center_reapply(gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  center_apply_width(self);
  if (g_str_equal(self->state, "editor"))
    action_center_cap_text(self);
  center_fit(self);
  g_object_unref(self);
  return G_SOURCE_REMOVE;
}

/* Place the action center in its host (the overlay asks on every layout). */
static gboolean center_position(GtkOverlay *overlay, GtkWidget *widget, GdkRectangle *allocation,
                                gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  if (widget != GTK_WIDGET(self))
    return FALSE;
  GtkWidget *host = GTK_WIDGET(overlay);
  int host_w = gtk_widget_get_width(host), host_h = gtk_widget_get_height(host);
  gboolean phone;
  int wanted, top, bottom;
  center_layout(self, host_w, &phone, &wanted, &top, &bottom);
  if (phone != self->phone || (phone && host_w != self->last_host_width) ||
      (g_str_equal(self->state, "editor") && center_wanted_cap(self) != self->text_cap))
    g_idle_add(center_reapply, g_object_ref(self)); /* structure changes wait until this layout is done */
  self->last_host_width = host_w;
  int minimum_w = 0, natural_w = 0;
  gtk_widget_measure(widget, GTK_ORIENTATION_HORIZONTAL, -1, &minimum_w, &natural_w, NULL, NULL);
  if (wanted < 0)
    wanted = MIN(natural_w, MAX(0, host_w - 24)); /* v70: a bar is at most the island less 24 */
  int width = MAX(minimum_w, wanted);
  int minimum_h = 0, natural_h = 0;
  gtk_widget_measure(widget, GTK_ORIENTATION_VERTICAL, width, &minimum_h, &natural_h, NULL, NULL);
  int height = MAX(minimum_h, MIN(natural_h, host_h - top - bottom));
  allocation->x = (host_w - width) / 2;
  allocation->y = host_h - bottom - height;
  allocation->width = width;
  allocation->height = height;
  center_schedule_safe(self);
  return TRUE;
}

void luma_action_center_attach(LumaActionCenter *self, GtkWidget *region) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(GTK_IS_WIDGET(region));
  GtkWidget *host = NULL;
  if (LUMA_IS_LAYER_HOST(region))
    host = region;
  else if (GTK_IS_WINDOW(region))
    host = GTK_WIDGET(luma_layer_host_install(GTK_WINDOW(region)));
  else
    host = (GtkWidget *)luma_layer_host_for_widget(region);
  if (host == NULL) {
    g_critical("an action center attaches to a layer host, a widget inside a window, or a window");
    return;
  }
  g_object_ref_sink(self);
  GtkWidget *parent = gtk_widget_get_parent(GTK_WIDGET(self));
  if (parent != NULL && GTK_IS_OVERLAY(parent))
    gtk_overlay_remove_overlay(GTK_OVERLAY(parent), GTK_WIDGET(self));
  luma_layer_host_add_layer(host, GTK_WIDGET(self));
  gtk_overlay_set_measure_overlay(GTK_OVERLAY(host), GTK_WIDGET(self), FALSE);
  if (self->host != host) {
    center_forget_host(self);
    g_set_weak_pointer(&self->host, host);
    self->position_handler = g_signal_connect(host, "get-child-position", G_CALLBACK(center_position), self);
  }
  luma_layer_host_track_bar(LUMA_LAYER_HOST(host), GTK_WIDGET(self));
  g_object_set_data(G_OBJECT(host), "lumaui-action-center", self);
  g_object_unref(self);
}

LumaActionCenter *luma_action_center_find(GtkWidget *widget) {
  g_return_val_if_fail(GTK_IS_WIDGET(widget), NULL);
  if (LUMA_IS_ACTION_CENTER(widget))
    return LUMA_ACTION_CENTER(widget);
  GtkWidget *center = gtk_widget_get_ancestor(widget, LUMA_TYPE_ACTION_CENTER);
  if (center != NULL)
    return LUMA_ACTION_CENTER(center);
  /* The nearest layer host's own, then the window's. */
  for (GtkWidget *node = widget; node != NULL; node = gtk_widget_get_parent(node)) {
    if (!LUMA_IS_LAYER_HOST(node))
      continue;
    LumaActionCenter *own = g_object_get_data(G_OBJECT(node), "lumaui-action-center");
    if (own != NULL && gtk_widget_get_parent(GTK_WIDGET(own)) == node && gtk_widget_get_visible(GTK_WIDGET(own)))
      return own;
  }
  GtkRoot *root = gtk_widget_get_root(widget);
  if (root == NULL)
    return NULL;
  /* Any showing center in the window, the widest first (v71 actionBar()). */
  LumaActionCenter *best = NULL;
  int best_w = -1;
  GtkWidget *stack[512];
  int depth = 0;
  stack[depth++] = GTK_WIDGET(root);
  while (depth > 0) {
    GtkWidget *node = stack[--depth];
    if (LUMA_IS_LAYER_HOST(node)) {
      LumaActionCenter *own = g_object_get_data(G_OBJECT(node), "lumaui-action-center");
      if (own != NULL && gtk_widget_get_parent(GTK_WIDGET(own)) == node && gtk_widget_get_mapped(GTK_WIDGET(own)) &&
          gtk_widget_get_width(GTK_WIDGET(own)) > best_w) {
        best = own;
        best_w = gtk_widget_get_width(GTK_WIDGET(own));
      }
    }
    for (GtkWidget *c = gtk_widget_get_first_child(node); c != NULL && depth < (int)G_N_ELEMENTS(stack);
         c = gtk_widget_get_next_sibling(c))
      if (gtk_widget_get_visible(c))
        stack[depth++] = c;
  }
  return best;
}

/* The layer host does not measure overlays. A change inside an already
 * allocated bar can therefore retain its old width until another host resize.
 * Place it against the host's current allocation immediately. */
static void center_reposition_now(LumaActionCenter *self) {
  if (self->host == NULL || !gtk_widget_get_mapped(GTK_WIDGET(self)))
    return;
  if (gtk_widget_get_width(self->host) <= 0 || gtk_widget_get_height(self->host) <= 0)
    return;
  GdkRectangle allocation = {0};
  if (center_position(GTK_OVERLAY(self->host), GTK_WIDGET(self), &allocation, self))
    gtk_widget_size_allocate(GTK_WIDGET(self), &allocation, -1);
}

static gboolean center_reposition_idle(gpointer data) {
  center_reposition_now(LUMA_ACTION_CENTER(data));
  return G_SOURCE_REMOVE;
}

void luma_action_center_set_phone_wide(LumaActionCenter *self, gboolean wide) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  self->phone_wide = !!wide;
  center_apply_width(self);
  center_reposition_now(self);
  if (self->host != NULL) gtk_widget_queue_resize(self->host);
}

void luma_action_center_set_phone_grown_inset(LumaActionCenter *self, int pixels) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(pixels >= -1);
  self->phone_grown_inset = pixels;
  center_apply_width(self);
  center_reposition_now(self);
  if (self->host != NULL) gtk_widget_queue_resize(self->host);
}

int luma_action_center_get_phone_grown_inset(LumaActionCenter *self) {
  g_return_val_if_fail(LUMA_IS_ACTION_CENTER(self), -1);
  return self->phone_grown_inset;
}

void luma_action_center_set_bar_bottom(LumaActionCenter *self, int pixels) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(pixels >= 0);
  if (self->bar_bottom == pixels)
    return;
  self->bar_bottom = pixels;
  center_apply_width(self);
  center_reposition_now(self);
  if (self->host != NULL)
    gtk_widget_queue_resize(self->host);
}

static void center_set_state(LumaActionCenter *self, const char *state) {
  if (!g_str_equal(state, "bar") && !g_str_equal(state, "double")) {
    if (self->grown_key != NULL)
      center_fold_panel(self, FALSE);
    if (self->searching)
      center_close_search(self);
  }
  for (guint i = 0; ac_states[i] != NULL; i++)
    if (g_str_equal(ac_states[i], state))
      self->state = ac_states[i];
  gtk_widget_set_visible(GTK_WIDGET(self), !g_str_equal(state, "hidden"));
  gtk_widget_set_visible(self->bar, g_str_equal(state, "bar") || g_str_equal(state, "double"));
  gtk_widget_set_visible(self->card, g_str_equal(state, "editor"));
  gtk_widget_set_visible(self->split, g_str_equal(state, "split"));
  for (guint i = 0; ac_states[i] != NULL; i++)
    luma_ui_set_css_class(GTK_WIDGET(self), ac_states[i], g_str_equal(ac_states[i], state));
  center_apply_width(self);
  center_schedule_safe(self);
  g_object_notify_by_pspec(G_OBJECT(self), center_props[PROP_STATE]);
  g_signal_emit(self, center_signals[STATE_CHANGED], 0, self->state);
}

static gboolean add_morph(gpointer user_data) {
  gtk_widget_add_css_class(GTK_WIDGET(user_data), "morph");
  return G_SOURCE_REMOVE;
}

static void center_morph(GtkWidget *part) {
  gtk_widget_remove_css_class(part, "morph");
  luma_ui_on_next_frame(part, add_morph, part);
}

static void clear_box(GtkWidget *box) {
  GtkWidget *child = gtk_widget_get_first_child(box);
  while (child != NULL) {
    GtkWidget *following = gtk_widget_get_next_sibling(child);
    gtk_box_remove(GTK_BOX(box), child);
    child = following;
  }
}

static void center_refresh_prompt(LumaActionCenter *self) {
  if (self->prompt_button == NULL || self->prompt == NULL)
    return;
  g_autofree char *draft = self->editor != NULL ? luma_action_editor_get_draft(self->editor) : NULL;
  /* " ".join(draft.split()) */
  GString *flat = g_string_new(NULL);
  if (draft != NULL) {
    g_auto(GStrv) words = g_strsplit_set(draft, " \t\n\r\f\v", -1);
    for (guint i = 0; words[i] != NULL; i++) {
      if (words[i][0] == '\0')
        continue;
      if (flat->len > 0)
        g_string_append_c(flat, ' ');
      g_string_append(flat, words[i]);
    }
  }
  gboolean has = flat->len > 0;
  g_autofree char *text = has ? g_strdup_printf("Draft: %s", flat->str) : g_strdup(self->prompt->label);
  g_string_free(flat, TRUE);
  gtk_label_set_label(GTK_LABEL(self->prompt_label), text);
  luma_ui_set_css_class(self->prompt_button, "has-draft", has);
  luma_ui_set_accessible_label(self->prompt_button, text);
}

static void prompt_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  if (self->prompt != NULL)
    g_signal_emit(self->prompt, item_signals[ITEM_ACTIVATED], 0);
  if (self->editor != NULL)
    luma_action_center_grow(self);
}

static GtkWidget *center_make_prompt(LumaActionCenter *self, LumaBarItem *prompt) {
  GtkWidget *button = gtk_button_new();
  gtk_widget_set_hexpand(button, TRUE);
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(button, "lumaui-ac-prompt");
  GtkWidget *label = gtk_label_new(NULL);
  gtk_label_set_xalign(GTK_LABEL(label), 0);
  gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
  gtk_button_set_child(GTK_BUTTON(button), label);
  g_signal_connect(button, "clicked", G_CALLBACK(prompt_clicked), self);
  g_set_object(&self->prompt, prompt);
  self->prompt_button = button;
  self->prompt_label = label;
  center_refresh_prompt(self);
  return button;
}

static void bar_search_changed(GtkEditable *editable, gpointer user_data) {
  LumaBarItem *item = LUMA_BAR_ITEM(user_data);
  const char *text = gtk_editable_get_text(editable);
  g_free(item->search_text);
  item->search_text = g_strdup(text);
  if (*text != '\0') item->search_expanded = TRUE;
  GtkWidget *center = gtk_widget_get_ancestor(item->hosted, LUMA_TYPE_ACTION_CENTER);
  if (center != NULL) center_apply_width(LUMA_ACTION_CENTER(center));
  if (!item->syncing_search)
    g_signal_emit(item, item_signals[ITEM_SEARCH_CHANGED], 0, text);
}

static void bar_search_focus_left(GtkEventControllerFocus *controller G_GNUC_UNUSED, gpointer user_data) {
  LumaBarItem *item = LUMA_BAR_ITEM(user_data);
  if (item->search_text[0] != '\0') return;
  item->search_expanded = FALSE;
  GtkWidget *center = gtk_widget_get_ancestor(item->hosted, LUMA_TYPE_ACTION_CENTER);
  if (center != NULL) center_apply_width(LUMA_ACTION_CENTER(center));
}

static void bar_search_folded_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  luma_bar_item_search_focus(LUMA_BAR_ITEM(user_data));
}

static void bar_search_activated(GtkSearchEntry *entry, gpointer user_data) {
  LumaBarItem *item = LUMA_BAR_ITEM(user_data);
  g_signal_emit(item, item_signals[ITEM_SEARCH_ACTIVATED], 0,
                gtk_editable_get_text(GTK_EDITABLE(entry)));
}

static gboolean bar_search_key(GtkEventControllerKey *keys G_GNUC_UNUSED, guint keyval,
                               guint keycode G_GNUC_UNUSED, GdkModifierType state G_GNUC_UNUSED,
                               gpointer user_data) {
  LumaBarItem *item = LUMA_BAR_ITEM(user_data);
  if (keyval != GDK_KEY_Escape || item->search_text[0] == '\0') return FALSE;
  gtk_editable_set_text(GTK_EDITABLE(item->search_entry), "");
  return TRUE;
}

static GtkWidget *center_make_hosted(LumaBarItem *item) {
  if (item->kind == LUMA_BAR_ITEM_SEARCH && item->hosted == NULL) {
    GtkWidget *host = gtk_stack_new();
    gtk_widget_set_name(host, "lumaui-bar-search-host");
    gtk_widget_add_css_class(host, "lumaui-bar-search-host");
    luma_ui_set_css_class(host, "keep", item->search_keep);
    gtk_stack_set_hhomogeneous(GTK_STACK(host), FALSE);
    gtk_stack_set_vhomogeneous(GTK_STACK(host), FALSE);
    gtk_stack_set_transition_type(GTK_STACK(host), GTK_STACK_TRANSITION_TYPE_NONE);
    item->hosted = g_object_ref_sink(host);
    GtkWidget *entry = gtk_search_entry_new();
    /* GtkSearchEntry supplies a platform magnifier; use the same glyph as the folded key. */
    GtkWidget *search_icon = gtk_widget_get_first_child(entry);
    if (GTK_IS_IMAGE(search_icon)) {
      g_autofree char *icon_name = luma_ui_icon_name("search");
      gtk_image_set_from_icon_name(GTK_IMAGE(search_icon), icon_name);
    }
    gtk_widget_add_css_class(entry, "lumaui-bar-search");
    luma_ui_set_css_class(entry, "keep", item->search_keep);
    gtk_widget_set_hexpand(entry, TRUE);
    gtk_editable_set_width_chars(GTK_EDITABLE(entry), 1);
    gtk_widget_set_name(entry, "lumaui-bar-search");
    gtk_widget_set_valign(entry, GTK_ALIGN_CENTER);
    gtk_widget_set_size_request(entry, LUMA_UI_BAR_SEARCH_WIDTH, LUMA_UI_BAR_SEARCH_HEIGHT);
    gtk_search_entry_set_placeholder_text(GTK_SEARCH_ENTRY(entry), item->label);
    luma_ui_set_accessible_label(entry, item->label);
    item->search_entry = entry;
    gtk_editable_set_text(GTK_EDITABLE(entry), item->search_text);
    g_signal_connect(entry, "changed", G_CALLBACK(bar_search_changed), item);
    g_signal_connect(entry, "activate", G_CALLBACK(bar_search_activated), item);
    GtkEventController *keys = gtk_event_controller_key_new();
    g_signal_connect(keys, "key-pressed", G_CALLBACK(bar_search_key), item);
    gtk_widget_add_controller(entry, keys);
    GtkEventController *focus = gtk_event_controller_focus_new();
    g_signal_connect(focus, "leave", G_CALLBACK(bar_search_focus_left), item);
    gtk_widget_add_controller(entry, focus);
    GtkWidget *button = gtk_button_new();
    gtk_widget_set_name(button, "lumaui-bar-search-folded");
    gtk_widget_add_css_class(button, "lumaui-bar-button");
    gtk_widget_add_css_class(button, "icon");
    gtk_widget_add_css_class(button, "bar");
    gtk_button_set_child(GTK_BUTTON(button), luma_ui_icon_image("search", 0));
    luma_ui_set_accessible_label(button, item->label);
    g_signal_connect(button, "clicked", G_CALLBACK(bar_search_folded_clicked), item);
    item->folded_button = button;
    gtk_stack_add_child(GTK_STACK(host), entry);
    gtk_stack_add_child(GTK_STACK(host), button);
    gtk_stack_set_visible_child(GTK_STACK(host), entry);
  }
  g_return_val_if_fail(item->hosted != NULL && gtk_widget_get_parent(item->hosted) == NULL, NULL);
  return item->hosted;
}

void luma_action_center_set_phone_control_size(LumaActionCenter *self, const char *size) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(g_strcmp0(size, "regular") == 0 || g_strcmp0(size, "small") == 0);
  luma_ui_set_css_class(self->bar_row, "small-controls", g_str_equal(size, "small"));
  gtk_widget_queue_resize(self->bar_row);
}

void luma_action_center_show_bar(LumaActionCenter *self, LumaBarItem *const *items, guint n_items,
                                 LumaBarItem *context) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(items != NULL || n_items == 0);
  g_return_if_fail(context == NULL || LUMA_IS_BAR_ITEM(context));
  if (context != NULL && context->kind != LUMA_BAR_ITEM_CONTEXT) {
    g_critical("the line over a double-height bar is a context item (luma_bar_item_new_context)");
    context = NULL;
  }
  luma_action_center_set_phone_control_size(self, "regular");
  check_primaries(items, n_items);
  /* A new bar is a new subject: a grown panel or an open search folds (holding stays: it spans views). */
  if (self->grown_key != NULL)
    center_fold_panel(self, FALSE);
  if (self->searching)
    center_close_search(self);
  GPtrArray *next_items = g_ptr_array_new_with_free_func(g_object_unref);
  for (guint i = 0; i < n_items; i++) {
    g_return_if_fail(LUMA_IS_BAR_ITEM(items[i]));
    g_ptr_array_add(next_items, g_object_ref(items[i]));
  }
  g_ptr_array_set_size(self->bar_controls, 0);
  clear_box(self->bar_row);
  g_ptr_array_unref(self->shown_items);
  self->shown_items = next_items;
  g_clear_object(&self->prompt);
  g_clear_object(&self->search_item);
  self->prompt_button = NULL;
  self->prompt_label = NULL;
  self->more_button = NULL;
  g_ptr_array_set_size(self->items, 0);
  g_ptr_array_set_size(self->overflow, 0);
  GtkWidget *group = NULL;
  const char *group_name = NULL;
  for (guint i = 0; i < n_items; i++) {
    LumaBarItem *item = items[i];
    g_return_if_fail(LUMA_IS_BAR_ITEM(item));
    GtkWidget *control = item->kind == LUMA_BAR_ITEM_PROMPT ? center_make_prompt(self, item)
                         : item->kind == LUMA_BAR_ITEM_SEARCH || item->kind == LUMA_BAR_ITEM_MODES
                             ? center_make_hosted(item)
                             : luma_bar_item_create_control(item, "bar");
    if (item->kind == LUMA_BAR_ITEM_SEARCH)
      g_set_object(&self->search_item, item);
    g_ptr_array_add(self->items, g_object_ref(item));
    if (control != NULL) g_object_set_data(G_OBJECT(control), "luma-bar-item-ref", item);
    g_ptr_array_add(self->bar_controls, control);
    if (control == NULL)
      continue;
    if (item->group == NULL) {
      group = NULL;
      group_name = NULL;
      gtk_box_append(GTK_BOX(self->bar_row), control);
      continue;
    }
    if (group == NULL || g_strcmp0(group_name, item->group) != 0) {
      group = g_object_new(GTK_TYPE_BOX, "accessible-role", GTK_ACCESSIBLE_ROLE_GROUP, NULL);
      gtk_widget_add_css_class(group, "lumaui-bar-group");
      luma_ui_set_accessible_label(group, item->group);
      guint count = 0;
      for (guint j = i; j < n_items && g_strcmp0(items[j]->group, item->group) == 0; j++)
        count++;
      if (count >= 3)
        gtk_widget_add_css_class(group, "three");
      gtk_box_append(GTK_BOX(self->bar_row), group);
      group_name = item->group;
    }
    gtk_box_append(GTK_BOX(group), control);
  }
  clear_box(self->context_line);
  if (context != NULL) {
    if (context->icon != NULL) {
      GtkWidget *glyph = luma_ui_icon_image(context->icon, 0);
      gtk_widget_add_css_class(glyph, "lumaui-ac-context-icon");
      gtk_box_append(GTK_BOX(self->context_line), glyph);
    }
    GtkWidget *text = gtk_label_new(NULL);
    gtk_label_set_xalign(GTK_LABEL(text), 0);
    gtk_widget_set_hexpand(text, TRUE);
    gtk_label_set_ellipsize(GTK_LABEL(text), PANGO_ELLIPSIZE_END);
    g_autofree char *markup = markup_with(context->label, context->emphasis);
    gtk_label_set_markup(GTK_LABEL(text), markup);
    gtk_box_append(GTK_BOX(self->context_line), text);
    if (context->dismissible)
      gtk_box_append(GTK_BOX(self->context_line), close_button(context, "Cancel"));
  }
  gtk_widget_set_visible(self->context_line, context != NULL);
  gtk_widget_set_visible(self->bar_row, !self->holding);
  gboolean wide = context != NULL || self->prompt != NULL;
  luma_ui_set_css_class(self->bar, "double", context != NULL);
  luma_ui_set_css_class(self->bar, "wide", wide);
  center_set_state(self, context != NULL ? "double" : "bar");
  center_reposition_now(self);
  g_idle_add_full(G_PRIORITY_DEFAULT_IDLE, center_reposition_idle, g_object_ref(self), g_object_unref);
  gtk_widget_queue_resize(GTK_WIDGET(self));
  if (self->host != NULL)
    gtk_widget_queue_resize(self->host);
  center_morph(self->bar);
  center_fit(self);
}

GtkWidget *luma_action_center_get_bar_control(LumaActionCenter *self, guint index) {
  g_return_val_if_fail(LUMA_IS_ACTION_CENTER(self), NULL);
  if (index >= self->bar_controls->len)
    return NULL;
  return g_ptr_array_index(self->bar_controls, index);
}

void luma_action_center_show_split(LumaActionCenter *self, LumaBarItem *const *first, guint n_first,
                                   LumaBarItem *const *second, guint n_second) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(first != NULL || n_first == 0);
  g_return_if_fail(second != NULL || n_second == 0);
  clear_box(self->split);
  LumaBarItem *const *groups[] = {first, second};
  guint counts[] = {n_first, n_second};
  for (guint g = 0; g < 2; g++) {
    check_primaries(groups[g], counts[g]);
    GtkWidget *part = g_object_new(GTK_TYPE_BOX, "accessible-role", GTK_ACCESSIBLE_ROLE_TOOLBAR, NULL);
    gtk_widget_add_css_class(part, "lumaui-ac-bar");
    gtk_widget_add_css_class(part, "lumaui-ac-row");
    for (guint i = 0; i < counts[g]; i++) {
      GtkWidget *control = LUMA_IS_BAR_ITEM(groups[g][i]) ? luma_bar_item_create_control(groups[g][i], "bar") : NULL;
      if (control != NULL)
        gtk_box_append(GTK_BOX(part), control);
    }
    gtk_box_append(GTK_BOX(self->split), part);
  }
  center_set_state(self, "split");
  center_morph(self->split);
}

void luma_action_center_set_editor(LumaActionCenter *self, LumaActionEditor *editor) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(LUMA_IS_ACTION_EDITOR(editor));
  if (self->editor == editor)
    return;
  if (self->editor != NULL && gtk_widget_get_parent(GTK_WIDGET(self->editor)) == self->card)
    gtk_box_remove(GTK_BOX(self->card), GTK_WIDGET(self->editor));
  self->editor = editor;
  g_set_weak_pointer(&editor->center, self);
  gtk_box_append(GTK_BOX(self->card), GTK_WIDGET(editor));
}

LumaActionEditor *luma_action_center_get_editor(LumaActionCenter *self) {
  g_return_val_if_fail(LUMA_IS_ACTION_CENTER(self), NULL);
  return self->editor;
}

static gboolean card_shown(gpointer user_data) {
  gtk_widget_add_css_class(GTK_WIDGET(user_data), "shown");
  return G_SOURCE_REMOVE;
}

static gboolean focus_editor(gpointer user_data) {
  luma_action_editor_focus_content(LUMA_ACTION_EDITOR(user_data));
  g_object_unref(user_data);
  return G_SOURCE_REMOVE;
}

void luma_action_center_grow(LumaActionCenter *self) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  if (self->editor == NULL) {
    g_critical("luma_action_center_set_editor() first: the action center has nothing to grow into");
    return;
  }
  gboolean was_editor = g_str_equal(self->state, "editor");
  center_set_state(self, "editor");
  if (!was_editor) {
    gtk_widget_remove_css_class(self->card, "shown");
    luma_ui_on_next_frame(self->card, card_shown, self->card);
    /* A newly revealed child may not receive a frame tick on a quiet
     * compositor. Keep the editor visible even when no redraw is scheduled. */
    g_timeout_add_full(G_PRIORITY_DEFAULT, 40, card_shown,
                       g_object_ref(self->card), g_object_unref);
  }
  action_center_cap_text(self);
  g_idle_add(focus_editor, g_object_ref(self->editor));
}

void luma_action_center_fold(LumaActionCenter *self) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  if (self->grown_key != NULL) {
    center_fold_panel(self, TRUE);
    return;
  }
  if (!g_str_equal(self->state, "editor"))
    return;
  center_set_state(self, gtk_widget_get_visible(self->context_line) ? "double" : "bar");
  center_refresh_prompt(self);
  center_morph(self->bar);
  if (self->prompt_button != NULL)
    gtk_widget_grab_focus(self->prompt_button);
}

void luma_action_center_discard(LumaActionCenter *self) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  if (self->editor != NULL) {
    luma_action_editor_clear(self->editor);
    g_signal_emit(self->editor, editor_signals[EDITOR_DISCARDED], 0);
  }
  if (g_str_equal(self->state, "editor"))
    luma_action_center_fold(self);
  else
    center_refresh_prompt(self);
}

static void editor_discard_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaActionEditor *self = LUMA_ACTION_EDITOR(user_data);
  if (self->center != NULL) {
    luma_action_center_discard(self->center);
  } else {
    luma_action_editor_clear(self);
  }
}

void luma_action_center_hide_bar(LumaActionCenter *self) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  center_set_state(self, "hidden");
}

const char *luma_action_center_get_state(LumaActionCenter *self) {
  g_return_val_if_fail(LUMA_IS_ACTION_CENTER(self), NULL);
  return self->state;
}

/* ── v71: the bar grows ───────────────────────────────────────────────── */

static void center_mark_grown(LumaActionCenter *self) {
  gboolean grown = center_is_grown(self);
  luma_ui_set_css_class(GTK_WIDGET(self), "grown", grown);
  luma_ui_set_css_class(self->bar, "grown", grown);
  center_apply_width(self);
  center_schedule_safe(self);
}

static void center_set_grown_control(LumaActionCenter *self, GtkWidget *control) {
  if (self->grown_control == control)
    return;
  if (self->grown_control != NULL) {
    GtkWidget *old = self->grown_control;
    GtkWidget *child = g_object_get_data(G_OBJECT(old), "lumaui-panel-child");
    if (child != NULL) {
      gtk_button_set_child(GTK_BUTTON(old), child);
      gtk_widget_set_css_classes(old, g_object_get_data(G_OBJECT(old), "lumaui-panel-classes"));
      gtk_widget_set_tooltip_text(old, g_object_get_data(G_OBJECT(old), "lumaui-panel-tooltip"));
      LumaBarItem *item = g_object_get_data(G_OBJECT(old), "luma-bar-item");
      if (item != NULL)
        luma_ui_set_accessible_label(old, item->tooltip != NULL ? item->tooltip : (item->label != NULL ? item->label : item->icon));
      g_object_set_data(G_OBJECT(old), "lumaui-panel-child", NULL);
      g_object_set_data(G_OBJECT(old), "lumaui-panel-classes", NULL);
      g_object_set_data(G_OBJECT(old), "lumaui-panel-tooltip", NULL);
    }
    if (!g_object_get_data(G_OBJECT(self->grown_control), "lumaui-was-on"))
      gtk_widget_remove_css_class(self->grown_control, "on");
    gtk_accessible_update_state(GTK_ACCESSIBLE(self->grown_control), GTK_ACCESSIBLE_STATE_EXPANDED, FALSE, -1);
  }
  g_clear_weak_pointer(&self->grown_control);
  if (control == NULL)
    return;
  LumaBarItem *item = g_object_get_data(G_OBJECT(control), "luma-bar-item");
  if (item != NULL && item->panel_close && GTK_IS_BUTTON(control)) {
    g_object_set_data_full(G_OBJECT(control), "lumaui-panel-child",
                          g_object_ref(gtk_button_get_child(GTK_BUTTON(control))), g_object_unref);
    g_object_set_data_full(G_OBJECT(control), "lumaui-panel-classes",
                          gtk_widget_get_css_classes(control), (GDestroyNotify)g_strfreev);
    g_object_set_data_full(G_OBJECT(control), "lumaui-panel-tooltip",
                          g_strdup(gtk_widget_get_tooltip_text(control)), g_free);
    gtk_button_set_child(GTK_BUTTON(control), luma_ui_icon_image("x", 0));
    const char *classes[] = {"labelled", "keep-label", "primary", "danger", "filled", "record", "image-button", NULL};
    for (guint i = 0; classes[i] != NULL; i++)
      gtk_widget_remove_css_class(control, classes[i]);
    gtk_widget_add_css_class(control, "icon");
    gtk_widget_set_tooltip_text(control, "Close");
    luma_ui_set_accessible_label(control, "Close");
  }
  g_object_set_data(G_OBJECT(control), "lumaui-was-on", GINT_TO_POINTER(gtk_widget_has_css_class(control, "on")));
  gtk_widget_add_css_class(control, "on"); /* the open panel's action is the raised chip */
  gtk_accessible_update_state(GTK_ACCESSIBLE(control), GTK_ACCESSIBLE_STATE_EXPANDED, TRUE, -1);
  g_set_weak_pointer(&self->grown_control, control);
}

static gboolean center_can_grow(LumaActionCenter *self) {
  if (g_str_equal(self->state, "bar") || g_str_equal(self->state, "double"))
    return TRUE;
  g_critical("the bar grows from its row: luma_action_center_show_bar() first (state is \"%s\")", self->state);
  return FALSE;
}

static void center_open_panel(LumaActionCenter *self, const char *key, GtkWidget *panel, GtkWidget *entry) {
  luma_action_center_set_panel_padding(self, TRUE);
  if (panel != NULL)
    g_object_ref_sink(panel);
  if (entry != NULL)
    g_object_ref_sink(entry);
  if (self->grown_key != NULL)
    center_fold_panel(self, FALSE);
  if (self->searching)
    center_close_search(self);
  panel_clear(self);
  if (panel != NULL) {
    gtk_widget_add_css_class(panel, "lumaui-ac-panel-content");
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->panel_slot), panel);
    luma_ui_on_next_frame(self->panel_slot, panel_shown, self->panel_slot);
  }
  bar_part(self, self->panel_slot, panel != NULL);
  if (entry != NULL) {
    clear_box(self->entry_row);
    gtk_widget_set_hexpand(entry, TRUE);
    gtk_box_append(GTK_BOX(self->entry_row), entry);
    GtkWidget *close = gtk_button_new();
    gtk_button_set_child(GTK_BUTTON(close), luma_ui_icon_image("x", 0));
    gtk_widget_set_valign(close, GTK_ALIGN_CENTER);
    gtk_widget_set_tooltip_text(close, "Close");
    luma_ui_set_accessible_label(close, "Close");
    gtk_widget_add_css_class(close, "lumaui-bar-button");
    gtk_widget_add_css_class(close, "icon");
    gtk_widget_add_css_class(close, "bar");
    g_signal_connect_swapped(close, "clicked", G_CALLBACK(luma_action_center_fold), self);
    gtk_box_append(GTK_BOX(self->entry_row), close);
    bar_part(self, self->entry_row, TRUE);
    gtk_widget_set_visible(self->bar_row, FALSE);
    self->entry_from_panel = TRUE;
  }
  g_free(self->grown_key);
  self->grown_key = g_strdup(key);
  for (GtkWidget *control = gtk_widget_get_first_child(self->bar_row); control != NULL;
       control = gtk_widget_get_next_sibling(control)) {
    LumaBarItem *item = g_object_get_data(G_OBJECT(control), "luma-bar-item");
    if (item != NULL && item->panel_func != NULL && g_strcmp0(item_panel_key(item), key) == 0) {
      center_set_grown_control(self, control);
      break;
    }
  }
  center_mark_grown(self);
  g_object_notify_by_pspec(G_OBJECT(self), center_props[PROP_GROWN]);
  g_signal_emit(self, center_signals[GROWN_CHANGED], 0, self->grown_key);
  g_signal_emit(self, center_signals[PANEL_CHANGED], 0, self->grown_key);
  if (entry != NULL)
    gtk_widget_grab_focus(entry);
  g_clear_object(&panel);
  g_clear_object(&entry);
}

static void center_fold_panel(LumaActionCenter *self, gboolean restore_focus) {
  if (self->grown_key == NULL)
    return;
  GtkWidget *back = self->grown_control != NULL ? g_object_ref(self->grown_control) : NULL;
  center_set_grown_control(self, NULL);
  panel_clear(self);
  bar_part(self, self->panel_slot, FALSE);
  if (self->entry_from_panel) {
    self->entry_from_panel = FALSE;
    clear_box(self->entry_row);
    bar_part(self, self->entry_row, FALSE);
    gtk_widget_set_visible(self->bar_row, !self->holding);
  }
  g_clear_pointer(&self->grown_key, g_free);
  center_mark_grown(self);
  g_object_notify_by_pspec(G_OBJECT(self), center_props[PROP_GROWN]);
  g_signal_emit(self, center_signals[GROWN_CHANGED], 0, NULL);
  g_signal_emit(self, center_signals[PANEL_CHANGED], 0, "");
  if (restore_focus && back != NULL && gtk_widget_get_mapped(back))
    gtk_widget_grab_focus(back);
  g_clear_object(&back);
}

void luma_action_center_set_panel_padding(LumaActionCenter *self, gboolean padded) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  luma_ui_set_css_class(self->panel_slot, "unpadded", !padded);
}

void luma_action_center_grow_panel(LumaActionCenter *self, const char *key, GtkWidget *panel) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(key != NULL);
  g_return_if_fail(GTK_IS_WIDGET(panel));
  if (g_strcmp0(self->grown_key, key) == 0) { /* the same button again closes it */
    g_object_ref_sink(panel);
    g_object_unref(panel);
    center_fold_panel(self, TRUE);
    return;
  }
  if (!center_can_grow(self)) {
    g_object_ref_sink(panel);
    g_object_unref(panel);
    return;
  }
  center_open_panel(self, key, panel, NULL);
}

void luma_action_center_grow_entry(LumaActionCenter *self, const char *key, GtkWidget *panel, GtkWidget *entry) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(key != NULL);
  g_return_if_fail(panel == NULL || GTK_IS_WIDGET(panel));
  g_return_if_fail(GTK_IS_WIDGET(entry));
  if (!center_can_grow(self))
    return;
  center_open_panel(self, key, panel, entry);
}

const char *luma_action_center_get_grown(LumaActionCenter *self) {
  g_return_val_if_fail(LUMA_IS_ACTION_CENTER(self), NULL);
  return self->grown_key;
}

/* A press on one of the row's controls: a panel action grows (or folds) the bar; any other action
 * taken from the row folds an open panel, as an action in a panel does. */
static void center_item_pressed(LumaActionCenter *self, LumaBarItem *item, GtkWidget *control) {
  if (item->panel_func != NULL) {
    const char *key = item_panel_key(item);
    if (g_strcmp0(self->grown_key, key) == 0) {
      center_fold_panel(self, TRUE);
      return;
    }
    if (!center_can_grow(self))
      return;
    GtkWidget *panel = item->panel_func(item, item->panel_data);
    if (panel == NULL)
      return;
    center_open_panel(self, key, panel, NULL);
    if (gtk_widget_is_ancestor(control, self->bar_row))
      center_set_grown_control(self, control);
    return;
  }
  if (self->grown_key != NULL && gtk_widget_is_ancestor(control, self->bar_row))
    center_fold_panel(self, FALSE);
}

/* ── Panels of rows: ⋯ and menus in the bar ── */

static void panel_row_clicked(GtkButton *button, gpointer user_data G_GNUC_UNUSED) {
  LumaActionCenter *center = luma_action_center_find(GTK_WIDGET(button));
  /* GtkActionable already ran its action; the panel closes after an action (v71). */
  if (center != NULL && center->grown_key != NULL)
    center_fold_panel(center, TRUE);
}

GtkWidget *luma_bar_panel_new(const char *heading) {
  luma_ui_install();
  GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(panel, "lumaui-panel-list");
  gtk_accessible_update_property(GTK_ACCESSIBLE(panel), GTK_ACCESSIBLE_PROPERTY_LABEL, heading != NULL ? heading : "",
                                 -1);
  if (heading != NULL && *heading != '\0')
    luma_bar_panel_add_heading(panel, heading);
  return panel;
}

void luma_bar_panel_add_heading(GtkWidget *panel, const char *heading) {
  g_return_if_fail(GTK_IS_BOX(panel));
  g_return_if_fail(heading != NULL);
  GtkWidget *label = gtk_label_new(heading);
  gtk_label_set_xalign(GTK_LABEL(label), 0);
  gtk_widget_add_css_class(label, "lumaui-panel-heading");
  gtk_box_append(GTK_BOX(panel), label);
}

static GtkWidget *panel_row_new(const char *icon, GIcon *gicon, const char *label, gboolean danger, gboolean on) {
  GtkWidget *row = gtk_button_new();
  gtk_widget_add_css_class(row, "lumaui-panel-row");
  if (danger)
    gtk_widget_add_css_class(row, "danger");
  if (on)
    gtk_widget_add_css_class(row, "on");
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(line, "lumaui-panel-row-line");
  if (gicon != NULL) {
    GtkWidget *glyph = gtk_image_new_from_gicon(gicon);
    gtk_widget_add_css_class(glyph, "lumaui-ac-panel-icon");
    gtk_box_append(GTK_BOX(line), glyph);
  } else if (icon != NULL && *icon != '\0') {
    GtkWidget *glyph = luma_ui_icon_image(icon, 0);
    gtk_widget_add_css_class(glyph, "lumaui-ac-panel-icon");
    gtk_box_append(GTK_BOX(line), glyph);
  }
  GtkWidget *words = gtk_label_new(label);
  gtk_widget_add_css_class(words, "lumaui-panel-row-title");
  gtk_label_set_xalign(GTK_LABEL(words), 0);
  gtk_widget_set_hexpand(words, TRUE);
  gtk_label_set_ellipsize(GTK_LABEL(words), PANGO_ELLIPSIZE_END);
  gtk_box_append(GTK_BOX(line), words);
  gtk_button_set_child(GTK_BUTTON(row), line);
  luma_ui_set_accessible_label(row, label);
  return row;
}

GtkWidget *luma_bar_panel_add_row(GtkWidget *panel, const char *icon, const char *label, const char *action_name,
                                  gboolean danger) {
  g_return_val_if_fail(GTK_IS_BOX(panel), NULL);
  g_return_val_if_fail(label != NULL, NULL);
  GtkWidget *row = panel_row_new(icon, NULL, label, danger, FALSE);
  if (action_name != NULL)
    gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(row), action_name);
  g_signal_connect_after(row, "clicked", G_CALLBACK(panel_row_clicked), NULL);
  gtk_box_append(GTK_BOX(panel), row);
  return row;
}

/* ⋯: a row per action. A row runs its action as the bar's control would (its GAction, then
 * "activated"); an action with a panel grows into that panel in place of ⋯. */
static void more_row_clicked(GtkButton *button, gpointer user_data) {
  LumaBarItem *item = LUMA_BAR_ITEM(user_data);
  LumaActionCenter *self = luma_action_center_find(GTK_WIDGET(button));
  if (self == NULL)
    return;
  g_object_ref(item);
  if (item->kind == LUMA_BAR_ITEM_SEARCH) {
    center_fold_panel(self, FALSE);
    luma_action_center_search(self);
  } else if (item->panel_func != NULL) {
    GtkWidget *panel = item->panel_func(item, item->panel_data);
    if (panel != NULL)
      center_open_panel(self, item_panel_key(item), panel, NULL);
    else
      center_fold_panel(self, TRUE);
    g_signal_emit(item, item_signals[ITEM_ACTIVATED], 0);
  } else {
    if (item->action_name != NULL) {
      g_autofree char *name = NULL;
      g_autoptr(GVariant) target = NULL;
      g_autoptr(GError) error = NULL;
      if (g_action_parse_detailed_name(item->action_name, &name, &target, &error))
        gtk_widget_activate_action_variant(GTK_WIDGET(self), name, target);
      else
        g_warning("%s", error->message);
    }
    g_signal_emit(item, item_signals[ITEM_ACTIVATED], 0);
    center_fold_panel(self, TRUE);
  }
  g_object_unref(item);
}

static GtkWidget *more_panel_new(LumaBarItem *const *items, guint n_items) {
  GtkWidget *panel = luma_bar_panel_new(NULL);
  GtkWidget *danger = NULL;
  for (guint i = 0; i < n_items; i++) {
    LumaBarItem *item = items[i];
    if (!LUMA_IS_BAR_ITEM(item) ||
        (item->kind != LUMA_BAR_ITEM_ACTION && item->kind != LUMA_BAR_ITEM_SEARCH))
      continue;
    const char *words = item->kind == LUMA_BAR_ITEM_SEARCH ? "Search"
                        : item->label != NULL              ? item->label
                        : item->tooltip != NULL            ? item->tooltip
                                                           : item->icon;
    GtkWidget *row = panel_row_new(item->kind == LUMA_BAR_ITEM_SEARCH ? "search" : item->icon, NULL, words, item->danger,
                                   item->active);
    gtk_widget_set_sensitive(row, item->sensitive);
    g_signal_connect_object(row, "clicked", G_CALLBACK(more_row_clicked), item, 0);
    if (item->danger && danger == NULL) /* the destructive row goes last */
      danger = row;
    else
      gtk_box_append(GTK_BOX(panel), row);
  }
  if (danger != NULL)
    gtk_box_append(GTK_BOX(panel), danger);
  return panel;
}

void luma_action_center_more(LumaActionCenter *self, LumaBarItem *const *items, guint n_items) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(items != NULL || n_items == 0);
  if (g_strcmp0(self->grown_key, "more") == 0) {
    center_fold_panel(self, TRUE);
    return;
  }
  if (!center_can_grow(self))
    return;
  center_open_panel(self, "more", more_panel_new(items, n_items), NULL);
}

static void more_button_clicked(GtkButton *button, gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  gboolean open = g_strcmp0(self->grown_key, "more") == 0;
  luma_action_center_more(self, (LumaBarItem *const *)self->overflow->pdata, self->overflow->len);
  if (!open && self->grown_key != NULL)
    center_set_grown_control(self, GTK_WIDGET(button));
}

/* At phone width a bar that does not fit puts its least important actions in ⋯ (v71 phone.js fitBar):
 * the primary, a chip, a field and the first control stay; single actions leave from the end. */
static gboolean center_fit_idle(gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  self->fit_source = 0;
  center_fit(self);
  return G_SOURCE_REMOVE;
}

static gboolean fit_keeps(LumaActionCenter *self, GtkWidget *control) {
  if (control == gtk_widget_get_first_child(self->bar_row) || control == self->more_button ||
      control == self->prompt_button)
    return TRUE;
  LumaBarItem *item = g_object_get_data(G_OBJECT(control), "luma-bar-item-ref");
  if (item == NULL) {
    if (gtk_widget_has_css_class(control, "lumaui-bar-group")) {
      for (GtkWidget *child=gtk_widget_get_first_child(control); child!=NULL; child=gtk_widget_get_next_sibling(child)) {
        LumaBarItem *member=g_object_get_data(G_OBJECT(child), "luma-bar-item-ref");
        if (member!=NULL && member->primary) return TRUE;
      }
      return FALSE;
    }
    return TRUE;
  }
  return item->primary || item->kind == LUMA_BAR_ITEM_CHIP || item->kind == LUMA_BAR_ITEM_SPACER ||
         (item->kind == LUMA_BAR_ITEM_SEARCH && !item->collapsed);
}

static void center_fit(LumaActionCenter *self) {
  if (self->grown_key != NULL || self->searching)
    return; /* the row holds while grown */
  int host_w = center_host_width(self);
  /* Reset what the last fit hid. */
  for (GtkWidget *c = gtk_widget_get_first_child(self->bar_row); c != NULL; c = gtk_widget_get_next_sibling(c))
    if (g_object_get_data(G_OBJECT(c), "lumaui-fit-hidden")) {
      g_object_set_data(G_OBJECT(c), "lumaui-fit-hidden", NULL);
      gtk_widget_set_visible(c, TRUE);
    }
  if (self->more_button != NULL) {
    gtk_box_remove(GTK_BOX(self->bar_row), self->more_button);
    self->more_button = NULL;
  }
  g_ptr_array_set_size(self->overflow, 0);
  if (!center_is_phone_width(host_w) || !g_str_equal(self->state, "bar"))
    return;
  if (host_w <= 0) {
    if (self->fit_source == 0)
      self->fit_source = g_idle_add(center_fit_idle, self);
    return;
  }
  center_apply_labels(self);
  /* Use the same available width as allocation; fields and wide bars have
   * their own phone gutter. A shrinkable field contributes its minimum below. */
  gboolean phone;
  int wanted, top, bottom;
  center_layout(self, host_w, &phone, &wanted, &top, &bottom);
  int room = (wanted > 0 ? wanted : host_w - 24) - 12;
  int need = 0;
  gtk_widget_measure(self->bar_row, GTK_ORIENTATION_HORIZONTAL, -1, &need, NULL, NULL, NULL);
  if (need <= room)
    return;
  /* ⋯ goes before the primary, or at the end. */
  GtkWidget *primary = NULL;
  for (GtkWidget *c = gtk_widget_get_last_child(self->bar_row); c != NULL && primary == NULL;
       c = gtk_widget_get_prev_sibling(c))
    if (gtk_widget_has_css_class(c, "primary"))
      primary = c;
  LumaBarItem *more_item = luma_bar_item_new_action("ellipsis", NULL, NULL);
  luma_bar_item_set_tooltip(more_item, "More");
  self->more_button = luma_bar_item_create_control(more_item, "bar");
  g_signal_handlers_disconnect_matched(self->more_button, G_SIGNAL_MATCH_FUNC, 0, 0, NULL, item_clicked, NULL);
  g_signal_connect(self->more_button, "clicked", G_CALLBACK(more_button_clicked), self);
  g_object_unref(more_item);
  if (primary != NULL)
    gtk_box_insert_child_after(GTK_BOX(self->bar_row), self->more_button, gtk_widget_get_prev_sibling(primary));
  else
    gtk_box_append(GTK_BOX(self->bar_row), self->more_button);
  GPtrArray *left = g_ptr_array_new();
  for (GtkWidget *c = gtk_widget_get_last_child(self->bar_row); c != NULL; c = gtk_widget_get_prev_sibling(c)) {
    gtk_widget_measure(self->bar_row, GTK_ORIENTATION_HORIZONTAL, -1, &need, NULL, NULL, NULL);
    if (need <= room)
      break;
    if (fit_keeps(self, c) || !gtk_widget_get_visible(c))
      continue;
    gtk_widget_set_visible(c, FALSE);
    g_object_set_data(G_OBJECT(c), "lumaui-fit-hidden", GINT_TO_POINTER(1));
    LumaBarItem *item = g_object_get_data(G_OBJECT(c), "luma-bar-item-ref");
    if (item != NULL && (item->kind == LUMA_BAR_ITEM_ACTION || item->kind == LUMA_BAR_ITEM_SEARCH))
      g_ptr_array_insert(left, 0, item);
    else if (gtk_widget_has_css_class(c, "lumaui-bar-group"))
      for (GtkWidget *child=gtk_widget_get_last_child(c);child!=NULL;child=gtk_widget_get_prev_sibling(child)) {
        LumaBarItem *member=g_object_get_data(G_OBJECT(child),"luma-bar-item-ref");
        if (member!=NULL) g_ptr_array_insert(left,0,member);
      }
  }
  for (guint i = 0; i < left->len; i++)
    g_ptr_array_add(self->overflow, g_object_ref(g_ptr_array_index(left, i)));
  g_ptr_array_unref(left);
  if (self->overflow->len == 0) {
    gtk_box_remove(GTK_BOX(self->bar_row), self->more_button);
    self->more_button = NULL;
  }
}

/* ── Holding across views (Move): Python's hold() / hold_destination() ── */

static void hold_release_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  g_object_ref(self);
  luma_action_center_release(self);
  g_signal_emit(self, center_signals[RELEASED], 0);
  g_object_unref(self);
}

/* The destination row replaces the bar's row (the entry row), with no ✕: the held row has it. */
static void center_show_where(LumaActionCenter *self, const char *lead, const char *where, const char *icon,
                              LumaBarItem *action) {
  GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(row, "lumaui-ac-where");
  GtkWidget *place = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "hexpand", TRUE, "valign",
                                  GTK_ALIGN_CENTER, NULL);
  gtk_widget_add_css_class(place, "lumaui-ac-where-text");
  if (where == NULL) {
    GtkWidget *ask = gtk_label_new(self->hold_prompt != NULL ? self->hold_prompt : "Where are we moving this?");
    gtk_label_set_xalign(GTK_LABEL(ask), 0);
    gtk_label_set_ellipsize(GTK_LABEL(ask), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(ask, "lumaui-ac-where-prompt");
    gtk_box_append(GTK_BOX(place), ask);
  } else {
    GtkWidget *small = gtk_label_new(lead != NULL ? lead : "Move to");
    gtk_label_set_xalign(GTK_LABEL(small), 0);
    gtk_widget_add_css_class(small, "lumaui-ac-where-lead");
    GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(line, "lumaui-ac-where-name");
    gtk_box_append(GTK_BOX(line), luma_ui_icon_image(icon != NULL ? icon : "folder", 0));
    GtkWidget *name = gtk_label_new(where);
    gtk_label_set_xalign(GTK_LABEL(name), 0);
    gtk_label_set_ellipsize(GTK_LABEL(name), PANGO_ELLIPSIZE_END);
    gtk_box_append(GTK_BOX(line), name);
    gtk_box_append(GTK_BOX(place), small);
    gtk_box_append(GTK_BOX(place), line);
  }
  gtk_box_append(GTK_BOX(row), place);
  if (where != NULL && action != NULL) {
    luma_bar_item_set_primary(action, TRUE);
    action->keep_label = TRUE;
    GtkWidget *control = luma_bar_item_create_control(action, "bar");
    if (control != NULL)
      gtk_box_append(GTK_BOX(row), control);
  }
  clear_box(self->entry_row);
  gtk_widget_set_hexpand(row, TRUE);
  gtk_box_append(GTK_BOX(self->entry_row), row);
  bar_part(self, self->entry_row, TRUE);
  gtk_widget_set_visible(self->bar_row, FALSE);
}

void luma_action_center_hold(LumaActionCenter *self, const char *caption, const char *label, GIcon *icon,
                             const char *prompt) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(label != NULL);
  g_return_if_fail(icon == NULL || G_IS_ICON(icon));
  if (self->grown_key != NULL)
    center_fold_panel(self, FALSE);
  if (self->searching)
    center_close_search(self);
  const char *kind = caption != NULL ? caption : "Moving";
  g_free(self->hold_prompt);
  self->hold_prompt = g_strdup(prompt);
  clear_box(self->hold_row);
  GtkWidget *face = g_object_new(GTK_TYPE_BOX, "valign", GTK_ALIGN_CENTER, "halign", GTK_ALIGN_START, "overflow",
                                 GTK_OVERFLOW_HIDDEN, NULL);
  gtk_widget_add_css_class(face, "lumaui-ac-hold-face");
  gtk_widget_set_size_request(face, 44, 44);
  GtkWidget *glyph = icon != NULL ? gtk_image_new_from_gicon(icon) : luma_ui_icon_image("file-text", 0);
  gtk_widget_set_halign(glyph, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(glyph, GTK_ALIGN_CENTER);
  gtk_widget_set_size_request(glyph, 44, 44);
  gtk_box_append(GTK_BOX(face), glyph);
  gtk_box_append(GTK_BOX(self->hold_row), face);
  GtkWidget *words = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "hexpand", TRUE, "valign",
                                  GTK_ALIGN_CENTER, NULL);
  g_autofree char *upper = g_utf8_strup(kind, -1);
  GtkWidget *tag = gtk_label_new(upper);
  gtk_label_set_xalign(GTK_LABEL(tag), 0);
  gtk_widget_add_css_class(tag, "lumaui-ac-hold-kind");
  GtkWidget *name = gtk_label_new(label);
  gtk_label_set_xalign(GTK_LABEL(name), 0);
  gtk_label_set_ellipsize(GTK_LABEL(name), PANGO_ELLIPSIZE_END);
  gtk_widget_add_css_class(name, "lumaui-ac-hold-name");
  gtk_box_append(GTK_BOX(words), tag);
  gtk_box_append(GTK_BOX(words), name);
  gtk_box_append(GTK_BOX(self->hold_row), words);
  g_autofree char *lower = g_utf8_strdown(kind, -1);
  g_autofree char *stop_name = g_strdup_printf("Stop %s", lower);
  LumaBarItem *stop_item = luma_bar_item_new_action("x", NULL, NULL);
  luma_bar_item_set_tooltip(stop_item, stop_name);
  GtkWidget *stop = luma_bar_item_create_control(stop_item, "bar");
  g_signal_handlers_disconnect_matched(stop, G_SIGNAL_MATCH_FUNC, 0, 0, NULL, item_clicked, NULL);
  g_signal_connect(stop, "clicked", G_CALLBACK(hold_release_clicked), self);
  g_object_unref(stop_item);
  gtk_box_append(GTK_BOX(self->hold_row), stop);
  g_object_set_data(G_OBJECT(self->hold_row), "lumaui-let-go", stop);
  g_autofree char *spoken = g_strdup_printf("%s: %s", kind, label);
  luma_ui_set_accessible_label(self->hold_row, spoken);
  self->holding = TRUE;
  bar_part(self, self->hold_row, TRUE);
  if (g_str_equal(self->state, "hidden") || g_str_equal(self->state, "editor") || g_str_equal(self->state, "split"))
    center_set_state(self, gtk_widget_get_visible(self->context_line) ? "double" : "bar");
  center_show_where(self, NULL, NULL, NULL, NULL);
  center_mark_grown(self);
}

void luma_action_center_set_hold_target(LumaActionCenter *self, const char *heading, const char *where,
                                        const char *icon, LumaBarItem *action) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(action == NULL || LUMA_IS_BAR_ITEM(action));
  if (!self->holding) {
    g_critical("luma_action_center_hold() first: nothing is held");
    return;
  }
  center_show_where(self, heading, where, icon, action);
}

void luma_action_center_release(LumaActionCenter *self) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  if (!self->holding)
    return;
  self->holding = FALSE;
  clear_box(self->hold_row);
  bar_part(self, self->hold_row, FALSE);
  clear_box(self->entry_row);
  bar_part(self, self->entry_row, FALSE);
  gtk_widget_set_visible(self->bar_row, TRUE);
  center_mark_grown(self);
}

gboolean luma_action_center_get_holding(LumaActionCenter *self) {
  g_return_val_if_fail(LUMA_IS_ACTION_CENTER(self), FALSE);
  return self->holding;
}

/* ── Searching in place ── */

/* v71: a collapsed search, pressed, turns the bar into its field: the item's own (persistent) field
 * moves from the row into the entry row, the bottom-most thing, with ✕ at its end; the row hides. */
static void search_close_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  center_close_search(LUMA_ACTION_CENTER(user_data));
}

static void center_open_search(LumaActionCenter *self, LumaBarItem *item) {
  if (self->searching || item->hosted == NULL || !center_can_grow(self))
    return;
  if (self->grown_key != NULL)
    center_fold_panel(self, FALSE);
  g_set_object(&self->search_item, item);
  GtkWidget *hosted = item->hosted;
  g_object_ref(hosted);
  GtkWidget *before = gtk_widget_get_prev_sibling(hosted);
  g_object_set_data(G_OBJECT(self->entry_row), "lumaui-search-before", before);
  if (gtk_widget_get_parent(hosted) == self->bar_row)
    gtk_box_remove(GTK_BOX(self->bar_row), hosted);
  clear_box(self->entry_row);
  gtk_widget_set_hexpand(hosted, TRUE);
  gtk_box_append(GTK_BOX(self->entry_row), hosted);
  g_object_unref(hosted);
  GtkWidget *close = gtk_button_new();
  gtk_button_set_child(GTK_BUTTON(close), luma_ui_icon_image("x", 0));
  gtk_widget_set_valign(close, GTK_ALIGN_CENTER);
  gtk_widget_set_tooltip_text(close, "Close search");
  luma_ui_set_accessible_label(close, "Close search");
  gtk_widget_add_css_class(close, "lumaui-bar-button");
  gtk_widget_add_css_class(close, "icon");
  gtk_widget_add_css_class(close, "bar");
  gtk_widget_remove_css_class(close, "image-button");
  g_signal_connect(close, "clicked", G_CALLBACK(search_close_clicked), self);
  gtk_box_append(GTK_BOX(self->entry_row), close);
  bar_part(self, self->entry_row, TRUE);
  gtk_widget_set_visible(self->bar_row, FALSE);
  self->searching = TRUE;
  item->search_expanded = TRUE;
  luma_ui_set_css_class(self->bar, "searching", TRUE);
  center_mark_grown(self);
  if (item->search_entry != NULL)
    gtk_widget_grab_focus(item->search_entry);
}

void luma_action_center_search(LumaActionCenter *self) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  LumaBarItem *item = NULL;
  for (guint i = 0; i < self->shown_items->len && item == NULL; i++) {
    LumaBarItem *shown = g_ptr_array_index(self->shown_items, i);
    if (shown->kind == LUMA_BAR_ITEM_SEARCH)
      item = shown;
  }
  if (item == NULL) {
    g_critical("the bar has no search: give it luma_bar_item_new_search()");
    return;
  }
  if (item->collapsed || self->phone)
    center_open_search(self, item);
  else
    luma_bar_item_search_focus(item);
}

static void center_close_search(LumaActionCenter *self) {
  if (!self->searching)
    return;
  self->searching = FALSE;
  LumaBarItem *item = self->search_item;
  if (item != NULL && item->hosted != NULL && gtk_widget_get_parent(item->hosted) == self->entry_row) {
    GtkWidget *hosted = g_object_ref(item->hosted);
    GtkWidget *before = g_object_get_data(G_OBJECT(self->entry_row), "lumaui-search-before");
    gtk_box_remove(GTK_BOX(self->entry_row), hosted);
    gtk_widget_set_hexpand(hosted, FALSE);
    if (before != NULL && gtk_widget_get_parent(before) == self->bar_row)
      gtk_box_insert_child_after(GTK_BOX(self->bar_row), hosted, before);
    else
      gtk_box_prepend(GTK_BOX(self->bar_row), hosted);
    g_object_unref(hosted);
  }
  g_object_set_data(G_OBJECT(self->entry_row), "lumaui-search-before", NULL);
  clear_box(self->entry_row);
  bar_part(self, self->entry_row, FALSE);
  gtk_widget_set_visible(self->bar_row, !self->holding);
  luma_ui_set_css_class(self->bar, "searching", FALSE);
  if (item != NULL) {
    item->search_expanded = FALSE;
    /* Closing clears the field and gives the bar back, and the whole list. */
    if (item->search_entry != NULL)
      gtk_editable_set_text(GTK_EDITABLE(item->search_entry), "");
  }
  center_mark_grown(self);
  if (item != NULL && item->folded_button != NULL && gtk_widget_get_mapped(item->folded_button))
    gtk_widget_grab_focus(item->folded_button);
}

/* ── The one safe area ── */

static void scroller_set_room(GtkScrolledWindow *scroller, int room) {
  GtkWidget *child = gtk_scrolled_window_get_child(scroller);
  if (child == NULL)
    return;
  if (GTK_IS_VIEWPORT(child) && gtk_viewport_get_child(GTK_VIEWPORT(child)) != NULL) {
    gtk_widget_set_margin_bottom(gtk_viewport_get_child(GTK_VIEWPORT(child)), room);
    return;
  }
  /* A scrollable that is its own viewport (a list or column view): its padding is the room. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
  GtkCssProvider *provider = g_object_get_data(G_OBJECT(child), "lumaui-safe-provider");
  if (provider == NULL) {
    provider = gtk_css_provider_new();
    gtk_style_context_add_provider(gtk_widget_get_style_context(child), GTK_STYLE_PROVIDER(provider),
                                   GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 3);
    g_object_set_data_full(G_OBJECT(child), "lumaui-safe-provider", provider, g_object_unref);
  }
#pragma GCC diagnostic pop
  g_autofree char *css = g_strdup_printf("* { padding-bottom: %dpx; }", room);
  gtk_css_provider_load_from_string(provider, css);
}

static gboolean center_safe_idle(gpointer user_data) {
  LumaActionCenter *self = LUMA_ACTION_CENTER(user_data);
  self->safe_source = 0;
  GtkWidget *host = self->host;
  gboolean showing = host != NULL && gtk_widget_get_mapped(GTK_WIDGET(self)) &&
                     !g_str_equal(self->state, "hidden");
  graphene_rect_t bar;
  float bar_top = 0;
  if (showing && gtk_widget_compute_bounds(GTK_WIDGET(self), host, &bar)) {
    bar_top = bar.origin.y;
    /* A grown panel floats over the page: the room is to the row (less the bar's own top padding,
     * which is the panel's offset in it), as if the bar had not grown. */
    graphene_rect_t panel, next;
    GtkWidget *after = bar_part_shown(self, self->panel_slot) ? gtk_widget_get_next_sibling(self->panel_slot) : NULL;
    while (after != NULL && !gtk_widget_get_visible(after))
      after = gtk_widget_get_next_sibling(after);
    if (after != NULL && gtk_widget_compute_bounds(self->panel_slot, host, &panel) &&
        gtk_widget_compute_bounds(after, host, &next))
      bar_top = next.origin.y - (panel.origin.y - bar.origin.y);
  } else {
    showing = FALSE;
  }
  for (guint i = 0; i < self->scrollers->len;) {
    SafeScroller *entry = g_ptr_array_index(self->scrollers, i);
    if (entry->scroller == NULL) {
      g_ptr_array_remove_index(self->scrollers, i);
      continue;
    }
    int room = 0;
    graphene_rect_t box;
    if (showing && gtk_widget_get_mapped(GTK_WIDGET(entry->scroller)) &&
        gtk_widget_compute_bounds(GTK_WIDGET(entry->scroller), host, &box)) {
      float bottom = box.origin.y + box.size.height;
      if (bottom > bar_top)
        room = MAX(AC_SAFE_GAP, (int)(bottom - bar_top + 0.5f) + AC_SAFE_GAP);
    }
    if (room != entry->room) {
      entry->room = room;
      scroller_set_room(entry->scroller, room);
    }
    i++;
  }
  return G_SOURCE_REMOVE;
}

static void center_schedule_safe(LumaActionCenter *self) {
  if (self->scrollers == NULL || self->scrollers->len == 0 || self->safe_source != 0)
    return;
  self->safe_source = g_idle_add(center_safe_idle, self);
}

static void scroller_mapped(GtkWidget *widget G_GNUC_UNUSED, gpointer user_data) {
  center_schedule_safe(LUMA_ACTION_CENTER(user_data));
}

void luma_action_center_attach_scroller(LumaActionCenter *self, GtkScrolledWindow *scroller) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(GTK_IS_SCROLLED_WINDOW(scroller));
  for (guint i = 0; i < self->scrollers->len; i++)
    if (((SafeScroller *)g_ptr_array_index(self->scrollers, i))->scroller == scroller)
      return;
  SafeScroller *entry = g_new0(SafeScroller, 1);
  entry->room = -1;
  g_set_weak_pointer(&entry->scroller, scroller);
  g_ptr_array_add(self->scrollers, entry);
  g_signal_connect_object(scroller, "map", G_CALLBACK(scroller_mapped), self, 0);
  center_schedule_safe(self);
}

/* ── Menus rising from the bar ── */

typedef struct {
  GtkWidget *where; /* weak */
  char *detailed;
} RowAction;

static void row_action_free(gpointer data, GClosure *closure G_GNUC_UNUSED) {
  RowAction *row = data;
  g_clear_weak_pointer(&row->where);
  g_free(row->detailed);
  g_free(row);
}

static void menu_row_clicked(GtkButton *button, gpointer user_data) {
  RowAction *row = user_data;
  LumaActionCenter *self = luma_action_center_find(GTK_WIDGET(button));
  GtkWidget *where = row->where != NULL ? g_object_ref(row->where) : NULL;
  g_autofree char *detailed = g_strdup(row->detailed);
  if (self != NULL)
    center_fold_panel(self, TRUE); /* the panel closes after an action; the action runs where it was asked */
  if (where != NULL && detailed != NULL) {
    g_autofree char *name = NULL;
    g_autoptr(GVariant) target = NULL;
    g_autoptr(GError) error = NULL;
    if (g_action_parse_detailed_name(detailed, &name, &target, &error))
      gtk_widget_activate_action_variant(where, name, target);
    else
      g_warning("%s", error->message);
  }
  g_clear_object(&where);
}

static GtkWidget *menu_row_for(const char *label, const char *icon, GIcon *gicon, const char *detailed,
                               GtkWidget *where, gboolean danger, gboolean on) {
  GtkWidget *button = panel_row_new(icon, gicon, label, danger, on);
  RowAction *row = g_new0(RowAction, 1);
  g_set_weak_pointer(&row->where, where);
  row->detailed = g_strdup(detailed);
  g_signal_connect_data(button, "clicked", G_CALLBACK(menu_row_clicked), row, row_action_free, 0);
  return button;
}

void luma_action_center_grow_rows(LumaActionCenter *self, const char *key, GPtrArray *rows, GtkWidget *where) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(rows != NULL);
  if (!center_can_grow(self))
    return;
  GtkWidget *panel = luma_bar_panel_new(NULL);
  for (guint i = 0; i < rows->len; i++) {
    LumaMenuRow *row = g_ptr_array_index(rows, i);
    if (row->kind == LUMA_MENU_ROW_SEPARATOR) {
      GtkWidget *rule = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
      gtk_widget_add_css_class(rule, "lumaui-panel-rule");
      gtk_box_append(GTK_BOX(panel), rule);
    } else if (row->kind == LUMA_MENU_ROW_HEADING) {
      luma_bar_panel_add_heading(panel, row->label);
    } else {
      gtk_box_append(GTK_BOX(panel), menu_row_for(row->label, row->icon, row->gicon, row->action,
                                                  where != NULL ? where : GTK_WIDGET(self), FALSE, row->selected));
    }
  }
  center_open_panel(self, key != NULL ? key : "menu", panel, NULL);
}

/* GMenuModel: sections divided by a hairline, a submenu a second page in the same panel with ‹ back. */
static void menu_page_fill(GtkWidget *stack, GtkWidget *page, GMenuModel *model, GtkWidget *where, int *pages);

static void menu_show_page(GtkButton *button, gpointer user_data) {
  gtk_stack_set_visible_child_name(GTK_STACK(user_data), g_object_get_data(G_OBJECT(button), "lumaui-page"));
}

static void menu_page_fill(GtkWidget *stack, GtkWidget *page, GMenuModel *model, GtkWidget *where, int *pages) {
  int n = g_menu_model_get_n_items(model);
  for (int i = 0; i < n; i++) {
    g_autofree char *label = NULL, *action = NULL, *icon = NULL;
    g_menu_model_get_item_attribute(model, i, G_MENU_ATTRIBUTE_LABEL, "s", &label);
    g_menu_model_get_item_attribute(model, i, G_MENU_ATTRIBUTE_ACTION, "s", &action);
    g_menu_model_get_item_attribute(model, i, "lumaui-icon", "s", &icon);
    g_autoptr(GVariant) target = g_menu_model_get_item_attribute_value(model, i, G_MENU_ATTRIBUTE_TARGET, NULL);
    g_autoptr(GMenuModel) section = g_menu_model_get_item_link(model, i, G_MENU_LINK_SECTION);
    g_autoptr(GMenuModel) submenu = g_menu_model_get_item_link(model, i, G_MENU_LINK_SUBMENU);
    if (section != NULL) {
      if (gtk_widget_get_first_child(page) != NULL) {
        GtkWidget *rule = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
        gtk_widget_add_css_class(rule, "lumaui-panel-rule");
        gtk_box_append(GTK_BOX(page), rule);
      }
      if (label != NULL)
        luma_bar_panel_add_heading(page, label);
      menu_page_fill(stack, page, section, where, pages);
      continue;
    }
    if (label == NULL)
      continue;
    if (submenu != NULL) {
      g_autofree char *name = g_strdup_printf("page-%d", ++*pages);
      GtkWidget *sub = luma_bar_panel_new(NULL);
      GtkWidget *back = panel_row_new("chevron-left", NULL, label, FALSE, FALSE);
      gtk_widget_add_css_class(back, "back");
      g_object_set_data(G_OBJECT(back), "lumaui-page", (gpointer) "root");
      g_signal_connect(back, "clicked", G_CALLBACK(menu_show_page), stack);
      gtk_box_append(GTK_BOX(sub), back);
      menu_page_fill(stack, sub, submenu, where, pages);
      gtk_stack_add_named(GTK_STACK(stack), sub, name);
      GtkWidget *open = panel_row_new(icon, NULL, label, FALSE, FALSE);
      GtkWidget *chevron = luma_ui_icon_image("chevron-right", 0);
      gtk_widget_add_css_class(chevron, "lumaui-ac-panel-chevron");
      gtk_box_append(GTK_BOX(gtk_button_get_child(GTK_BUTTON(open))), chevron);
      g_object_set_data_full(G_OBJECT(open), "lumaui-page", g_strdup(name), g_free);
      g_signal_connect(open, "clicked", G_CALLBACK(menu_show_page), stack);
      gtk_box_append(GTK_BOX(page), open);
      continue;
    }
    g_autofree char *detailed = action != NULL ? g_action_print_detailed_name(action, target) : NULL;
    gboolean danger = g_str_has_prefix(label, "Delete") || g_str_has_prefix(label, "Move to Trash");
    gtk_box_append(GTK_BOX(page), menu_row_for(label, icon, NULL, detailed, where, danger, FALSE));
  }
}

void luma_action_center_grow_menu(LumaActionCenter *self, const char *key, GMenuModel *model, GtkWidget *where) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(key != NULL);
  g_return_if_fail(G_IS_MENU_MODEL(model));
  if (g_strcmp0(self->grown_key, key) == 0) {
    center_fold_panel(self, TRUE);
    return;
  }
  if (!center_can_grow(self))
    return;
  GtkWidget *stack = gtk_stack_new();
  gtk_stack_set_vhomogeneous(GTK_STACK(stack), FALSE);
  gtk_stack_set_hhomogeneous(GTK_STACK(stack), TRUE);
  gtk_stack_set_transition_type(GTK_STACK(stack), GTK_STACK_TRANSITION_TYPE_SLIDE_LEFT_RIGHT);
  gtk_stack_set_interpolate_size(GTK_STACK(stack), TRUE);
  GtkWidget *root = luma_bar_panel_new(NULL);
  int pages = 0;
  gtk_stack_add_named(GTK_STACK(stack), root, "root");
  menu_page_fill(stack, root, model, where != NULL ? where : GTK_WIDGET(self), &pages);
  gtk_stack_set_visible_child_name(GTK_STACK(stack), "root");
  center_open_panel(self, key, stack, NULL);
}

/* ── v71 panel parts: row states, tiles, choices (Python bar_panel) ── */

static void fold_from(GtkWidget *widget) {
  LumaActionCenter *center = luma_action_center_find(widget);
  if (center != NULL && center->grown_key != NULL)
    center_fold_panel(center, TRUE);
}

void luma_bar_panel_row_set_person(GtkWidget *row, gboolean person) {
  g_return_if_fail(GTK_IS_BUTTON(row));
  luma_ui_set_css_class(row, "person", person);
}

void luma_bar_panel_row_set_state(GtkWidget *row, gboolean on, gboolean current) {
  g_return_if_fail(GTK_IS_WIDGET(row));
  luma_ui_set_css_class(row, "on", on);
  luma_ui_set_css_class(row, "current", current);
  gtk_accessible_update_state(GTK_ACCESSIBLE(row), GTK_ACCESSIBLE_STATE_SELECTED, on || current, -1);
}

static void tile_clicked(GtkButton *button, gpointer data G_GNUC_UNUSED) {
  fold_from(GTK_WIDGET(button)); /* its action already ran (GtkActionable) */
}

GtkWidget *luma_bar_tiles_new(guint columns, gboolean compact, gboolean chip) {
  luma_ui_install();
  GtkWidget *grid = g_object_new(GTK_TYPE_GRID, "column-homogeneous", TRUE, "hexpand", TRUE, "row-spacing",
                                 chip ? 8 : 6, "column-spacing", chip ? 8 : 6, NULL);
  gtk_widget_add_css_class(grid, "lumaui-bar-tiles");
  gtk_widget_add_css_class(grid, compact ? "compact" : "regular");
  if (chip)
    gtk_widget_add_css_class(grid, "chip");
  g_object_set_data(G_OBJECT(grid), "lumaui-tile-columns", GUINT_TO_POINTER(MIN(columns, 5u)));
  if (columns > 0) {
    GtkWidget *spacer = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_hexpand(spacer, TRUE);
    gtk_grid_attach(GTK_GRID(grid), spacer, MIN(columns, 5u) - 1, 0, 1, 1);
    g_object_set_data(G_OBJECT(grid), "lumaui-tile-spacer", spacer);
  }
  return grid;
}

GtkWidget *luma_bar_tiles_add(GtkWidget *tiles, const char *icon, const char *label, const char *action_name,
                              gboolean on, gboolean danger) {
  g_return_val_if_fail(GTK_IS_GRID(tiles), NULL);
  g_return_val_if_fail(icon != NULL && label != NULL, NULL);
  GtkWidget *spacer = g_object_get_data(G_OBJECT(tiles), "lumaui-tile-spacer");
  guint index = 0;
  for (GtkWidget *c = gtk_widget_get_first_child(tiles); c != NULL; c = gtk_widget_get_next_sibling(c))
    if (c != spacer)
      index++;
  guint columns = GPOINTER_TO_UINT(g_object_get_data(G_OBJECT(tiles), "lumaui-tile-columns"));
  GtkWidget *button = g_object_new(GTK_TYPE_BUTTON, "hexpand", TRUE, NULL);
  gtk_widget_add_css_class(button, "lumaui-bar-tile");
  if (on) {
    gtk_widget_add_css_class(button, "on");
    gtk_accessible_update_state(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_STATE_PRESSED, GTK_ACCESSIBLE_TRISTATE_TRUE,
                                -1);
  }
  if (danger)
    gtk_widget_add_css_class(button, "danger");
  GtkWidget *stack = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "halign", GTK_ALIGN_CENTER,
                                  "valign", GTK_ALIGN_CENTER, NULL);
  gtk_widget_add_css_class(stack, "lumaui-bar-tile-stack");
  gtk_box_append(GTK_BOX(stack), luma_ui_icon_image(icon, 0));
  GtkWidget *words = g_object_new(GTK_TYPE_LABEL, "label", label, "justify", GTK_JUSTIFY_CENTER, "wrap", TRUE,
                                  "wrap-mode", PANGO_WRAP_WORD_CHAR, "max-width-chars", 10, NULL);
  gtk_box_append(GTK_BOX(stack), words);
  gtk_button_set_child(GTK_BUTTON(button), stack);
  luma_ui_set_accessible_label(button, label);
  g_object_set_data_full(G_OBJECT(button), "lumaui-tile-label", g_strdup(label), g_free);
  if (action_name != NULL)
    gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(button), action_name);
  g_signal_connect_after(button, "clicked", G_CALLBACK(tile_clicked), NULL);
  /* Columns: as asked, else one per tile up to five (re-laid as tiles arrive). */
  if (columns == 0) {
    guint count = index + 1, per_row = MIN(5u, count);
    GtkWidget *c = gtk_widget_get_first_child(tiles);
    guint i = 0;
    while (c != NULL) {
      GtkWidget *next = gtk_widget_get_next_sibling(c);
      g_object_ref(c);
      gtk_grid_remove(GTK_GRID(tiles), c);
      gtk_grid_attach(GTK_GRID(tiles), c, i % per_row, i / per_row, 1, 1);
      g_object_unref(c);
      c = next;
      i++;
    }
    gtk_grid_attach(GTK_GRID(tiles), button, index % per_row, index / per_row, 1, 1);
  } else {
    if (spacer != NULL && index + 1 >= columns) {
      gtk_grid_remove(GTK_GRID(tiles), spacer);
      g_object_set_data(G_OBJECT(tiles), "lumaui-tile-spacer", NULL);
    }
    gtk_grid_attach(GTK_GRID(tiles), button, index % columns, index / columns, 1, 1);
  }
  return button;
}

void luma_bar_tile_set_subtitle(GtkWidget *tile, const char *subtitle) {
  g_return_if_fail(GTK_IS_BUTTON(tile));
  g_return_if_fail(gtk_widget_has_css_class(tile, "lumaui-bar-tile"));
  GtkWidget *sub = g_object_get_data(G_OBJECT(tile), "lumaui-tile-subtitle");
  if (sub == NULL) {
    sub = gtk_label_new(NULL);
    gtk_label_set_ellipsize(GTK_LABEL(sub), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(sub, "lumaui-bar-tile-sub");
    gtk_box_append(GTK_BOX(gtk_button_get_child(GTK_BUTTON(tile))), sub);
    g_object_set_data(G_OBJECT(tile), "lumaui-tile-subtitle", sub);
  }
  gtk_label_set_text(GTK_LABEL(sub), subtitle != NULL ? subtitle : "");
  gtk_widget_set_visible(sub, subtitle != NULL && *subtitle != '\0');
  const char *label = g_object_get_data(G_OBJECT(tile), "lumaui-tile-label");
  g_autofree char *spoken = subtitle != NULL && *subtitle != '\0' ? g_strdup_printf("%s, %s", label, subtitle) : g_strdup(label);
  luma_ui_set_accessible_label(tile, spoken);
}

void luma_bar_tile_set_well(GtkWidget *tile, gboolean well) {
  g_return_if_fail(GTK_IS_BUTTON(tile));
  g_return_if_fail(gtk_widget_has_css_class(tile, "lumaui-bar-tile"));
  GtkWidget *glyph = gtk_widget_get_first_child(gtk_button_get_child(GTK_BUTTON(tile)));
  luma_ui_set_css_class(glyph, "lumaui-favourite-well", well);
  luma_ui_set_css_class(tile, "well", well);
}

GtkWidget *luma_panel_choices_new(const char *action_name) {
  luma_ui_install();
  GtkWidget *row = g_object_new(GTK_TYPE_BOX, "accessible-role", GTK_ACCESSIBLE_ROLE_RADIO_GROUP, NULL);
  gtk_widget_add_css_class(row, "lumaui-panel-choices");
  g_object_set_data_full(G_OBJECT(row), "lumaui-choices-action", g_strdup(action_name), g_free);
  return row;
}

static void choice_clicked(GtkButton *button, gpointer data G_GNUC_UNUSED) {
  GtkWidget *row = gtk_widget_get_parent(GTK_WIDGET(button));
  const char *key = g_object_get_data(G_OBJECT(button), "lumaui-choice-key");
  luma_panel_choices_set_selected(row, key);
  const char *action = g_object_get_data(G_OBJECT(row), "lumaui-choices-action");
  if (action != NULL)
    gtk_widget_activate_action(GTK_WIDGET(button), action, "s", key);
}

void luma_panel_choices_add(GtkWidget *choices, const char *key, const char *label, const char *icon) {
  g_return_if_fail(GTK_IS_BOX(choices));
  g_return_if_fail(key != NULL && label != NULL);
  GtkWidget *button = g_object_new(GTK_TYPE_BUTTON, "accessible-role", GTK_ACCESSIBLE_ROLE_RADIO, NULL);
  gtk_widget_add_css_class(button, "lumaui-panel-choice");
  if (g_strcmp0(key, "heading") == 0) gtk_widget_add_css_class(button, "style-heading");
  if (g_strcmp0(key, "quote") == 0) gtk_widget_add_css_class(button, "style-quote");
  gtk_widget_set_hexpand(button, gtk_widget_has_css_class(choices, "document-style"));
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  if (icon != NULL && *icon != '\0')
    gtk_box_append(GTK_BOX(line), luma_ui_icon_image(icon, 0));
  gtk_box_append(GTK_BOX(line), gtk_label_new(label));
  gtk_button_set_child(GTK_BUTTON(button), line);
  gtk_widget_set_halign(line, gtk_widget_has_css_class(choices, "document-style") ? GTK_ALIGN_CENTER : GTK_ALIGN_FILL);
  g_object_set_data_full(G_OBJECT(button), "lumaui-choice-key", g_strdup(key), g_free);
  g_signal_connect(button, "clicked", G_CALLBACK(choice_clicked), NULL);
  gtk_box_append(GTK_BOX(choices), button);
  if (gtk_widget_get_first_child(choices) == button) /* the first is chosen until told */
    luma_panel_choices_set_selected(choices, key);
}

void luma_panel_choices_set_document_style(GtkWidget *choices, gboolean document_style) {
  g_return_if_fail(GTK_IS_BOX(choices));
  luma_ui_set_css_class(choices, "document-style", document_style);
  gtk_box_set_homogeneous(GTK_BOX(choices), document_style);
  for (GtkWidget *c = gtk_widget_get_first_child(choices); c; c = gtk_widget_get_next_sibling(c)) {
    gtk_widget_set_hexpand(c, document_style);
    gtk_widget_set_halign(gtk_button_get_child(GTK_BUTTON(c)), document_style ? GTK_ALIGN_CENTER : GTK_ALIGN_FILL);
  }
}

void luma_panel_choices_set_selected(GtkWidget *choices, const char *key) {
  g_return_if_fail(GTK_IS_BOX(choices));
  for (GtkWidget *c = gtk_widget_get_first_child(choices); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    gboolean on = g_strcmp0(g_object_get_data(G_OBJECT(c), "lumaui-choice-key"), key) == 0;
    luma_ui_set_css_class(c, "on", on);
    gtk_accessible_update_state(GTK_ACCESSIBLE(c), GTK_ACCESSIBLE_STATE_CHECKED, on, -1);
  }
}

void luma_panel_choices_set_decoration(GtkWidget *choices, const char *key, const char *icon) {
  g_return_if_fail(GTK_IS_BOX(choices));
  for (GtkWidget *c = gtk_widget_get_first_child(choices); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    if (g_strcmp0(g_object_get_data(G_OBJECT(c), "lumaui-choice-key"), key) != 0)
      continue;
    GtkWidget *line = gtk_button_get_child(GTK_BUTTON(c));
    GtkWidget *old = g_object_get_data(G_OBJECT(c), "lumaui-choice-decoration");
    if (old != NULL)
      gtk_box_remove(GTK_BOX(line), old);
    GtkWidget *glyph = icon != NULL ? luma_ui_icon_image(icon, 0) : NULL;
    if (glyph != NULL)
      gtk_box_append(GTK_BOX(line), glyph);
    g_object_set_data(G_OBJECT(c), "lumaui-choice-decoration", glyph);
  }
}

/* ── the two-row bar's foot, and sheets from the bar's place ── */

void luma_action_center_set_foot(LumaActionCenter *self, GtkWidget *foot) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(foot == NULL || GTK_IS_WIDGET(foot));
  clear_box(self->foot_row);
  if (foot != NULL) {
    gtk_widget_set_hexpand(foot, TRUE);
    gtk_box_append(GTK_BOX(self->foot_row), foot);
  }
  gtk_widget_set_visible(self->foot_row, foot != NULL && !bar_part_shown(self, self->entry_row));
  luma_ui_set_css_class(self->bar, "two-row", foot != NULL);
  center_schedule_safe(self);
}

LumaModalHandle *luma_action_center_sheet(LumaActionCenter *self, GtkWidget *content, const char *title) {
  g_return_val_if_fail(LUMA_IS_ACTION_CENTER(self), NULL);
  g_return_val_if_fail(GTK_IS_WIDGET(content), NULL);
  LumaLayerHost *host = luma_layer_host_window_host(self->host != NULL ? self->host : GTK_WIDGET(self));
  if (host == NULL)
    return NULL;
  GtkWidget *card = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "accessible-role",
                                 GTK_ACCESSIBLE_ROLE_DIALOG, NULL);
  gtk_widget_add_css_class(card, "lumaui-bar-frame");
  gtk_widget_add_css_class(card, "sheet");
  if (title != NULL && *title != '\0') {
    GtkWidget *heading = gtk_label_new(title);
    gtk_label_set_xalign(GTK_LABEL(heading), 0);
    gtk_widget_add_css_class(heading, "lumaui-bar-frame-title");
    gtk_box_append(GTK_BOX(card), heading);
    luma_ui_set_accessible_label(card, title);
  }
  GtkWidget *scroller = g_object_new(GTK_TYPE_SCROLLED_WINDOW, "hscrollbar-policy", GTK_POLICY_NEVER,
                                     "propagate-natural-height", TRUE, "vexpand", TRUE, NULL);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroller), content);
  gtk_box_append(GTK_BOX(card), scroller);
  return luma_layer_host_present_modal(host, card, NULL, LUMA_DRAWER_MODE_AUTO);
}

/* ── SharePanel ── */

enum { SHARE_CHOSEN, N_SHARE_SIGNALS };
static guint share_signals[N_SHARE_SIGNALS];

struct _LumaSharePanel {
  GtkBox parent_instance;
};

G_DEFINE_FINAL_TYPE(LumaSharePanel, luma_share_panel, GTK_TYPE_BOX)

static void luma_share_panel_class_init(LumaSharePanelClass *klass) {
  /**
   * LumaSharePanel::chosen:
   * @self: the panel
   * @choice: "send-to", "messages", "mail", "copy-link" or "nearby"
   * @person: (nullable): for "send-to", who
   */
  share_signals[SHARE_CHOSEN] = g_signal_new("chosen", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                                             NULL, G_TYPE_NONE, 2, G_TYPE_STRING, G_TYPE_STRING);
}

static void luma_share_panel_init(LumaSharePanel *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-share-panel");
}

static void share_chose(GtkWidget *button, const char *choice, const char *person) {
  GtkWidget *panel = gtk_widget_get_ancestor(button, LUMA_TYPE_SHARE_PANEL);
  if (panel == NULL)
    return;
  g_object_ref(panel);
  fold_from(panel);
  g_signal_emit(panel, share_signals[SHARE_CHOSEN], 0, choice, person);
  g_object_unref(panel);
}

static void share_person_clicked(GtkButton *button, gpointer data G_GNUC_UNUSED) {
  share_chose(GTK_WIDGET(button), "send-to", g_object_get_data(G_OBJECT(button), "lumaui-share-person"));
}

static void share_target_clicked(GtkButton *button, gpointer data G_GNUC_UNUSED) {
  share_chose(GTK_WIDGET(button), g_object_get_data(G_OBJECT(button), "lumaui-share-choice"), NULL);
}

GtkWidget *luma_share_panel_new(const char *heading, const char *const *people) {
  LumaSharePanel *self = g_object_new(LUMA_TYPE_SHARE_PANEL, NULL);
  GtkWidget *title = gtk_label_new(heading != NULL ? heading : "Send to");
  gtk_label_set_xalign(GTK_LABEL(title), 0);
  gtk_widget_add_css_class(title, "lumaui-panel-heading");
  gtk_box_append(GTK_BOX(self), title);
  if (people != NULL && people[0] != NULL) {
    GtkWidget *faces = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(faces, "lumaui-share-panel-people");
    for (guint i = 0; people[i] != NULL && i < 5; i++) {
      if (i > 0) /* spread edge to edge (.fshp: space-between) */
        gtk_box_append(GTK_BOX(faces), g_object_new(GTK_TYPE_BOX, "hexpand", TRUE, NULL));
      GtkWidget *button = gtk_button_new();
      gtk_widget_add_css_class(button, "lumaui-share-panel-person");
      GtkWidget *stack = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "halign",
                                      GTK_ALIGN_CENTER, NULL);
      gtk_box_append(GTK_BOX(stack), luma_person_avatar_new(people[i], 48));
      g_auto(GStrv) words = g_strsplit(people[i], " ", 2);
      gtk_box_append(GTK_BOX(stack), gtk_label_new(words[0] != NULL ? words[0] : people[i]));
      gtk_button_set_child(GTK_BUTTON(button), stack);
      luma_ui_set_accessible_label(button, people[i]);
      g_object_set_data_full(G_OBJECT(button), "lumaui-share-person", g_strdup(people[i]), g_free);
      g_signal_connect(button, "clicked", G_CALLBACK(share_person_clicked), NULL);
      gtk_box_append(GTK_BOX(faces), button);
    }
    gtk_box_append(GTK_BOX(self), faces);
  }
  static const char *const targets[][3] = {{"message-square", "Messages", "messages"},
                                           {"mail", "Email", "mail"},
                                           {"link", "Copy link", "copy-link"},
                                           {"radio-tower", "Nearby", "nearby"}};
  GtkWidget *tiles = luma_bar_tiles_new(G_N_ELEMENTS(targets), FALSE, FALSE);
  for (guint i = 0; i < G_N_ELEMENTS(targets); i++) {
    GtkWidget *tile = luma_bar_tiles_add(tiles, targets[i][0], targets[i][1], NULL, FALSE, FALSE);
    g_signal_handlers_disconnect_by_func(tile, tile_clicked, NULL); /* the panel folds and says what was chosen */
    g_object_set_data(G_OBJECT(tile), "lumaui-share-choice", (gpointer)targets[i][2]);
    g_signal_connect(tile, "clicked", G_CALLBACK(share_target_clicked), NULL);
  }
  gtk_box_append(GTK_BOX(self), tiles);
  return GTK_WIDGET(self);
}

void luma_action_center_set_toolbar(LumaActionCenter *self, gboolean toolbar) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  luma_ui_set_css_class(GTK_WIDGET(self), "document-toolbar", toolbar);
  center_apply_share(self);
}

void luma_action_center_set_head(LumaActionCenter *self, GtkWidget *head) {
  g_return_if_fail(LUMA_IS_ACTION_CENTER(self));
  g_return_if_fail(head == NULL || GTK_IS_WIDGET(head));
  clear_box(self->head_row);
  if (head != NULL) {
    gtk_widget_set_hexpand(head, TRUE);
    gtk_box_append(GTK_BOX(self->head_row), head);
  }
  gtk_widget_set_visible(self->head_row, head != NULL);
  luma_ui_set_css_class(self->bar, "headed", head != NULL);
  luma_ui_set_css_class(self->bar, "wide", gtk_widget_has_css_class(self->bar, "wide") || head != NULL);
  center_schedule_safe(self);
}
