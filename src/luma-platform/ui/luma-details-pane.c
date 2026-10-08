/* SPDX-License-Identifier: Apache-2.0 */
/* The details pane and its rows: the twin of structure_details.py (Python). */
#include "luma-details-pane.h"
#include "luma-layer-host.h"
#include "luma-toast.h"
#include "luma-island.h"
#include "luma-ui-private.h"

/* A label that asks for no width: the pane's width decides (_label). */
static GtkWidget *pane_label(const char *text, const char *css, float xalign, gboolean ellipsize) {
  GtkWidget *label = gtk_label_new(text);
  gtk_label_set_xalign(GTK_LABEL(label), xalign);
  if (ellipsize) {
    gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
    gtk_label_set_max_width_chars(GTK_LABEL(label), 1);
  }
  gtk_widget_add_css_class(label, css);
  return label;
}

static char *who_label(const char *title, const char *subtitle) {
  if (subtitle != NULL && *subtitle != '\0')
    return g_strdup_printf("%s, %s", title, subtitle);
  return g_strdup(title);
}

/* ── DetailsRow ──────────────────────────────────────────────────────── */

enum { ROW_ACTIVATED, N_ROW_SIGNALS };
static guint row_signals[N_ROW_SIGNALS];

struct _LumaDetailsRow {
  GtkBox parent_instance;
  GtkWidget *button;
  GtkWidget *actions;
};

G_DEFINE_FINAL_TYPE(LumaDetailsRow, luma_details_row, GTK_TYPE_BOX)

static void row_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  g_signal_emit(user_data, row_signals[ROW_ACTIVATED], 0);
}

static void luma_details_row_class_init(LumaDetailsRowClass *klass) {
  /**
   * LumaDetailsRow::activated:
   * @self: the row
   *
   * The row itself (not one of its actions) was activated.
   */
  row_signals[ROW_ACTIVATED] = g_signal_new("activated", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                            NULL, NULL, G_TYPE_NONE, 0);
}

static void luma_details_row_init(LumaDetailsRow *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-details-row");
}

GtkWidget *luma_details_row_new(const char *title, const char *subtitle, GtkWidget *lead) {
  g_return_val_if_fail(title != NULL, NULL);
  g_return_val_if_fail(lead == NULL || GTK_IS_WIDGET(lead), NULL);
  LumaDetailsRow *self = g_object_new(LUMA_TYPE_DETAILS_ROW, NULL);
  self->button = gtk_button_new();
  gtk_widget_set_hexpand(self->button, TRUE);
  gtk_widget_add_css_class(self->button, "lumaui-details-who");
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(line, "lumaui-details-line");
  if (lead != NULL) {
    gtk_widget_set_valign(lead, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(line), lead);
  }
  GtkWidget *text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_valign(text, GTK_ALIGN_CENTER);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_box_append(GTK_BOX(text), pane_label(title, "lumaui-details-name", 0, TRUE));
  if (subtitle != NULL && *subtitle != '\0') {
    GtkWidget *sub = pane_label(subtitle, "lumaui-details-sub", 0, TRUE);
    gtk_widget_set_tooltip_text(sub, subtitle);
    gtk_box_append(GTK_BOX(text), sub);
  }
  gtk_box_append(GTK_BOX(line), text);
  gtk_button_set_child(GTK_BUTTON(self->button), line);
  g_autofree char *name = who_label(title, subtitle);
  luma_ui_set_accessible_label(self->button, name);
  g_signal_connect(self->button, "clicked", G_CALLBACK(row_clicked), self);
  gtk_box_append(GTK_BOX(self), self->button);
  self->actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_valign(self->actions, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(self->actions, "lumaui-details-actions");
  gtk_widget_set_visible(self->actions, FALSE);
  gtk_box_append(GTK_BOX(self), self->actions);
  return GTK_WIDGET(self);
}

void luma_details_row_add_action(LumaDetailsRow *self, const char *icon, const char *label,
                                 const char *action_name) {
  g_return_if_fail(LUMA_IS_DETAILS_ROW(self));
  g_return_if_fail(icon != NULL && label != NULL && action_name != NULL);
  GtkWidget *button = luma_ui_icon_button(icon, label, "lumaui-details-action", FALSE, NULL);
  gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(button), action_name);
  gtk_box_append(GTK_BOX(self->actions), button);
  gtk_widget_set_visible(self->actions, TRUE);
}

/* ── DetailsItem ─────────────────────────────────────────────────────── */

struct _LumaDetailsItem {
  GtkButton parent_instance;
  GtkWidget *line;
  GtkWidget *lead; /* the icon or the lead widget */
  GtkWidget *trail;
};

G_DEFINE_FINAL_TYPE(LumaDetailsItem, luma_details_item, GTK_TYPE_BUTTON)

static void luma_details_item_class_init(LumaDetailsItemClass *klass G_GNUC_UNUSED) {}

static void luma_details_item_init(LumaDetailsItem *self) {
  luma_ui_install();
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-details-item");
  self->line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->line, "lumaui-details-line");
  gtk_button_set_child(GTK_BUTTON(self), self->line);
}

GtkWidget *luma_details_item_new(const char *title, const char *subtitle, const char *icon) {
  g_return_val_if_fail(title != NULL, NULL);
  LumaDetailsItem *self = g_object_new(LUMA_TYPE_DETAILS_ITEM, NULL);
  if (icon != NULL) {
    self->lead = luma_ui_icon_image(icon, 0);
    gtk_widget_add_css_class(self->lead, "lumaui-details-item-icon");
    gtk_widget_add_css_class(self->lead, "lumaui-hue-tint"); /* the hue of the page it is on, if any */
    gtk_box_append(GTK_BOX(self->line), self->lead);
  }
  GtkWidget *text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_valign(text, GTK_ALIGN_CENTER);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_box_append(GTK_BOX(text), pane_label(title, "lumaui-details-name", 0, TRUE));
  if (subtitle != NULL && *subtitle != '\0')
    gtk_box_append(GTK_BOX(text), pane_label(subtitle, "lumaui-details-sub", 0, TRUE));
  gtk_box_append(GTK_BOX(self->line), text);
  g_autofree char *name = who_label(title, subtitle);
  luma_ui_set_accessible_label(GTK_WIDGET(self), name);
  return GTK_WIDGET(self);
}

void luma_details_item_set_lead(LumaDetailsItem *self, GtkWidget *lead) {
  g_return_if_fail(LUMA_IS_DETAILS_ITEM(self));
  g_return_if_fail(lead == NULL || GTK_IS_WIDGET(lead));
  if (self->lead != NULL)
    gtk_box_remove(GTK_BOX(self->line), self->lead);
  self->lead = lead;
  if (lead != NULL) {
    gtk_widget_set_valign(lead, GTK_ALIGN_CENTER);
    gtk_box_prepend(GTK_BOX(self->line), lead);
  }
}

void luma_details_item_set_trail(LumaDetailsItem *self, GtkWidget *trail) {
  g_return_if_fail(LUMA_IS_DETAILS_ITEM(self));
  g_return_if_fail(trail == NULL || GTK_IS_WIDGET(trail));
  if (self->trail != NULL)
    gtk_box_remove(GTK_BOX(self->line), self->trail);
  self->trail = trail;
  if (trail != NULL) {
    gtk_widget_set_valign(trail, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(self->line), trail);
  }
}

void luma_details_item_set_selected(LumaDetailsItem *self, gboolean selected) {
  g_return_if_fail(LUMA_IS_DETAILS_ITEM(self));
  luma_ui_set_css_class(GTK_WIDGET(self), "on", selected);
}

/* ── AddRow ──────────────────────────────────────────────────────────── */

struct _LumaAddRow {
  GtkButton parent_instance;
  GtkWidget *shortcut;
  GtkWidget *text;
};

G_DEFINE_FINAL_TYPE(LumaAddRow, luma_add_row, GTK_TYPE_BUTTON)

static void luma_add_row_class_init(LumaAddRowClass *klass G_GNUC_UNUSED) {}

static void luma_add_row_init(LumaAddRow *self) {
  luma_ui_install();
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-add-row");
}

void luma_add_row_set_document(LumaAddRow *self, gboolean document) {
  g_return_if_fail(LUMA_IS_ADD_ROW(self));
  luma_ui_set_css_class(GTK_WIDGET(self), "document", document);
  if (self->text != NULL)
    gtk_label_set_max_width_chars(GTK_LABEL(self->text), document ? -1 : 1);
  gtk_widget_set_hexpand(GTK_WIDGET(self), !document);
  gtk_widget_set_halign(GTK_WIDGET(self), document ? GTK_ALIGN_START : GTK_ALIGN_FILL);
}

static void add_row_width(GtkWidget *widget, int width G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  LumaAddRow *self = LUMA_ADD_ROW(widget);
  gtk_widget_set_visible(self->shortcut, !luma_ui_is_phone(widget));
}

GtkWidget *luma_add_row_new(const char *label, const char *icon, const char *shortcut) {
  g_return_val_if_fail(label != NULL, NULL);
  LumaAddRow *self = g_object_new(LUMA_TYPE_ADD_ROW, NULL);
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(line, "lumaui-details-line");
  GtkWidget *mark = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(mark, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(mark, GTK_ALIGN_CENTER);
  gtk_widget_set_hexpand(mark, FALSE);
  gtk_widget_add_css_class(mark, "lumaui-add-mark");
  GtkWidget *glyph = luma_ui_icon_image(icon != NULL ? icon : "user-plus", 0);
  gtk_widget_set_hexpand(glyph, TRUE);
  gtk_widget_set_halign(glyph, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(mark), glyph);
  gtk_box_append(GTK_BOX(line), mark);
  GtkWidget *text = pane_label(label, "lumaui-add-label", 0, TRUE);
  self->text = text;
  gtk_widget_set_hexpand(text, TRUE);
  gtk_box_append(GTK_BOX(line), text);
  if (shortcut != NULL && *shortcut != '\0') {
    guint key = 0;
    GdkModifierType mods = 0;
    g_autofree char *shown = NULL;
    if (gtk_accelerator_parse(shortcut, &key, &mods) && key != 0)
      shown = gtk_accelerator_get_label(key, mods);
    self->shortcut = g_object_new(GTK_TYPE_LABEL, "label", shown != NULL ? shown : shortcut, "valign",
                                  GTK_ALIGN_CENTER, "accessible-role", GTK_ACCESSIBLE_ROLE_PRESENTATION, NULL);
    gtk_widget_add_css_class(self->shortcut, "lumaui-add-shortcut");
    gtk_box_append(GTK_BOX(line), self->shortcut);
    gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_KEY_SHORTCUTS, shortcut, -1);
    luma_ui_width_watch(GTK_WIDGET(self), add_row_width, NULL, (LUMA_TIER_PHONE_BELOW - 1));
  }
  gtk_button_set_child(GTK_BUTTON(self), line);
  luma_ui_set_accessible_label(GTK_WIDGET(self), label);
  return GTK_WIDGET(self);
}

/* ── FactRow ─────────────────────────────────────────────────────────── */

struct _LumaFactRow {
  GtkBox parent_instance;
  char *value;
};

G_DEFINE_FINAL_TYPE(LumaFactRow, luma_fact_row, GTK_TYPE_BOX)

static void luma_fact_row_finalize(GObject *object) {
  g_free(LUMA_FACT_ROW(object)->value);
  G_OBJECT_CLASS(luma_fact_row_parent_class)->finalize(object);
}

static void luma_fact_row_class_init(LumaFactRowClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = luma_fact_row_finalize;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_GROUP);
}

static void luma_fact_row_init(LumaFactRow *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-fact-row");
}

static void fact_copy_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  luma_fact_row_copy(user_data);
}

GtkWidget *luma_fact_row_new(const char *icon, const char *label, const char *value, gboolean copy) {
  g_return_val_if_fail(icon != NULL && label != NULL && value != NULL, NULL);
  LumaFactRow *self = g_object_new(LUMA_TYPE_FACT_ROW, NULL);
  self->value = g_strdup(value);
  GtkWidget *glyph = luma_ui_icon_image(icon, 0);
  gtk_widget_add_css_class(glyph, "lumaui-fact-icon");
  gtk_box_append(GTK_BOX(self), glyph);
  GtkWidget *text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_valign(text, GTK_ALIGN_CENTER);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_box_append(GTK_BOX(text), pane_label(label, "lumaui-fact-label", 0, TRUE));
  GtkWidget *shown = pane_label(value, "lumaui-fact-value", 0, TRUE);
  gtk_label_set_selectable(GTK_LABEL(shown), TRUE);
  gtk_widget_set_can_focus(shown, FALSE);
  gtk_widget_set_tooltip_text(shown, value);
  gtk_box_append(GTK_BOX(text), shown);
  gtk_box_append(GTK_BOX(self), text);
  g_autofree char *name = g_strdup_printf("%s: %s", label, value);
  luma_ui_set_accessible_label(GTK_WIDGET(self), name);
  if (copy) {
    g_autofree char *lower = g_utf8_strdown(label, -1);
    g_autofree char *tip = g_strdup_printf("Copy %s", lower);
    GtkWidget *button = luma_ui_icon_button("copy", tip, "lumaui-fact-copy", FALSE, NULL);
    g_signal_connect(button, "clicked", G_CALLBACK(fact_copy_clicked), self);
    gtk_box_append(GTK_BOX(self), button);
  }
  return GTK_WIDGET(self);
}

void luma_fact_row_copy(LumaFactRow *self) {
  g_return_if_fail(LUMA_IS_FACT_ROW(self));
  gdk_clipboard_set_text(gtk_widget_get_clipboard(GTK_WIDGET(self)), self->value);
  if (gtk_widget_get_root(GTK_WIDGET(self)) != NULL)
    luma_toast_show(GTK_WIDGET(self), "Copied", "copied");
}

/* ── DetailsPhotos ───────────────────────────────────────────────────── */

#define PHOTO_COLUMNS 3

enum { PHOTO_ACTIVATED, N_PHOTO_SIGNALS };
static guint photo_signals[N_PHOTO_SIGNALS];

struct _LumaDetailsPhotos {
  GtkGrid parent_instance;
};

G_DEFINE_FINAL_TYPE(LumaDetailsPhotos, luma_details_photos, GTK_TYPE_GRID)

static void luma_details_photos_class_init(LumaDetailsPhotosClass *klass) {
  /**
   * LumaDetailsPhotos::photo-activated:
   * @self: the grid
   * @index: the photo's place in the list
   */
  photo_signals[PHOTO_ACTIVATED] = g_signal_new("photo-activated", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                                                0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_UINT);
}

static void luma_details_photos_init(LumaDetailsPhotos *self) {
  luma_ui_install();
  gtk_grid_set_column_homogeneous(GTK_GRID(self), TRUE);
  gtk_grid_set_row_homogeneous(GTK_GRID(self), TRUE);
  gtk_grid_set_column_spacing(GTK_GRID(self), LUMA_UI_STRUCTURE_DETAILS_PHOTO_GAP);
  gtk_grid_set_row_spacing(GTK_GRID(self), LUMA_UI_STRUCTURE_DETAILS_PHOTO_GAP);
  gtk_widget_set_overflow(GTK_WIDGET(self), GTK_OVERFLOW_HIDDEN);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-details-photos");
}

static void photo_released(GtkGestureClick *click, int n_press G_GNUC_UNUSED, double x G_GNUC_UNUSED,
                           double y G_GNUC_UNUSED, gpointer user_data) {
  GtkWidget *frame = gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(click));
  guint index = GPOINTER_TO_UINT(g_object_get_data(G_OBJECT(frame), "luma-photo-index"));
  g_signal_emit(user_data, photo_signals[PHOTO_ACTIVATED], 0, index);
}

GtkWidget *luma_details_photos_new(GListModel *items) {
  g_return_val_if_fail(items == NULL || G_IS_LIST_MODEL(items), NULL);
  GtkWidget *self = g_object_new(LUMA_TYPE_DETAILS_PHOTOS, NULL);
  luma_details_photos_set_items(LUMA_DETAILS_PHOTOS(self), items);
  return self;
}

void luma_details_photos_set_items(LumaDetailsPhotos *self, GListModel *items) {
  g_return_if_fail(LUMA_IS_DETAILS_PHOTOS(self));
  g_return_if_fail(items == NULL || G_IS_LIST_MODEL(items));
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(GTK_WIDGET(self))) != NULL)
    gtk_grid_remove(GTK_GRID(self), child);
  guint n = items != NULL ? g_list_model_get_n_items(items) : 0;
  for (guint index = 0; index < n; index++) {
    g_autoptr(GObject) item = g_list_model_get_item(items, index);
    if (!GDK_IS_PAINTABLE(item) && !G_IS_FILE(item)) {
      g_critical("a details photo is a GdkPaintable or a GFile, not a %s", G_OBJECT_TYPE_NAME(item));
      continue;
    }
    GtkWidget *picture = gtk_picture_new();
    gtk_picture_set_content_fit(GTK_PICTURE(picture), GTK_CONTENT_FIT_COVER);
    gtk_picture_set_can_shrink(GTK_PICTURE(picture), TRUE);
    if (GDK_IS_PAINTABLE(item))
      gtk_picture_set_paintable(GTK_PICTURE(picture), GDK_PAINTABLE(item));
    else
      gtk_picture_set_file(GTK_PICTURE(picture), G_FILE(item));
    GtkWidget *frame = gtk_aspect_frame_new(0.5f, 0.5f, 1.0f, FALSE);
    gtk_aspect_frame_set_child(GTK_ASPECT_FRAME(frame), picture);
    gtk_widget_set_hexpand(frame, TRUE);
    gtk_widget_add_css_class(frame, "lumaui-details-photo");
    g_object_set_data(G_OBJECT(frame), "luma-photo-index", GUINT_TO_POINTER(index));
    GtkGesture *click = gtk_gesture_click_new();
    g_signal_connect(click, "released", G_CALLBACK(photo_released), self);
    gtk_widget_add_controller(frame, GTK_EVENT_CONTROLLER(click));
    gtk_grid_attach(GTK_GRID(self), frame, (int)(index % PHOTO_COLUMNS), (int)(index / PHOTO_COLUMNS), 1, 1);
  }
}

/* ── DetailsPane ─────────────────────────────────────────────────────── */

enum { PROP_0, PROP_SHOWN, N_PROPS };
static GParamSpec *pane_props[N_PROPS];

struct _LumaDetailsPane {
  GtkBox parent_instance;
  GtkWidget *sheet; /* strong: it travels between the slot and a drawer */
  GtkWidget *handle;
  GtkWidget *header;
  GtkWidget *leading;
  GtkWidget *title_label;
  GtkWidget *title_content;
  GtkWidget *close_button;
  GtkWidget *back_button; /* DP1: "Back to Tuesday", hidden until set */
  GCallback back_callback;
  gpointer back_data;
  int closable; /* DP1: -1 follows main (a side pane closes), 0 or 1 as asked */
  GtkWidget *body;
  GtkWidget *scroller;
  GtkWidget *footer;
  guint drawer_tick;
  gboolean presenting_drawer;
  GtkWidget *last_facts; /* the block facts join, while it is the body's last child */
  GtkWidget *last_list;  /* the list rows join, likewise */
  gboolean main;
  gboolean embedded;
  gboolean wanted;
  gboolean shown;
  GObject *subject;
  LumaModalHandle *drawer;
  gboolean drawer_closed;
  gulong drawer_handler;
  GtkEventController *esc;
  GtkWidget *esc_root; /* weak */
  GtkWidget *return_focus; /* weak */
  GPtrArray *info_buttons; /* weak GtkToggleButton pointers */
};

G_DEFINE_FINAL_TYPE(LumaDetailsPane, luma_details_pane, GTK_TYPE_BOX)

static void pane_apply(LumaDetailsPane *self, gboolean on);
static void pane_reclaim(LumaDetailsPane *self);
static void info_button_gone(gpointer data, GObject *where);

static gboolean pane_is_drawer(LumaDetailsPane *self) {
  return self->drawer != NULL && !self->drawer_closed;
}

static void pane_set_shown(LumaDetailsPane *self, gboolean on) {
  if (on != self->shown) {
    self->shown = on;
    g_object_notify_by_pspec(G_OBJECT(self), pane_props[PROP_SHOWN]);
  }
}

static void pane_sync_info_buttons(LumaDetailsPane *self, gboolean on) {
  for (guint i = 0; i < self->info_buttons->len; i++) {
    GtkToggleButton *button = g_ptr_array_index(self->info_buttons, i);
    if (gtk_toggle_button_get_active(button) != on)
      gtk_toggle_button_set_active(button, on);
  }
}

/* Esc */

static gboolean pane_key(GtkEventControllerKey *keys G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                         GdkModifierType state G_GNUC_UNUSED, gpointer user_data) {
  LumaDetailsPane *self = user_data;
  if (keyval != GDK_KEY_Escape || self->main || !self->shown || pane_is_drawer(self))
    return FALSE;
  GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
  GtkWidget *focus = root != NULL ? gtk_root_get_focus(root) : NULL;
  if (focus != NULL && (GTK_IS_EDITABLE(focus) ||
                        (gtk_widget_get_parent(focus) != NULL && GTK_IS_EDITABLE(gtk_widget_get_parent(focus)))))
    return FALSE;
  luma_details_pane_close(self);
  return TRUE;
}

static void pane_listen_esc(LumaDetailsPane *self, gboolean on) {
  GtkWidget *root = GTK_WIDGET(gtk_widget_get_root(GTK_WIDGET(self)));
  if (self->esc != NULL && (!on || self->esc_root != root)) {
    if (self->esc_root != NULL)
      gtk_widget_remove_controller(self->esc_root, self->esc);
    self->esc = NULL;
    g_clear_weak_pointer(&self->esc_root);
  }
  if (on && self->esc == NULL && root != NULL && !self->main) {
    self->esc = gtk_event_controller_key_new();
    g_signal_connect(self->esc, "key-pressed", G_CALLBACK(pane_key), self);
    gtk_widget_add_controller(root, self->esc);
    g_set_weak_pointer(&self->esc_root, root);
  }
}

/* Focus */

static void pane_remember_focus(LumaDetailsPane *self) {
  GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
  GtkWidget *focus = root != NULL ? gtk_root_get_focus(root) : NULL;
  if (focus != NULL && !gtk_widget_is_ancestor(focus, self->sheet))
    g_set_weak_pointer(&self->return_focus, focus);
}

static void pane_return_focus(LumaDetailsPane *self) {
  GtkWidget *target = self->return_focus;
  g_clear_weak_pointer(&self->return_focus);
  GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
  GtkWidget *focus = root != NULL ? gtk_root_get_focus(root) : NULL;
  gboolean in_pane = focus == NULL || gtk_widget_is_ancestor(focus, self->sheet) || focus == self->sheet;
  if (in_pane && target != NULL && gtk_widget_get_root(target) == root && gtk_widget_get_mapped(target))
    gtk_widget_grab_focus(target);
}

/* Beside the island, or a drawer */

static void pane_drop_drawer(LumaDetailsPane *self) {
  if (self->drawer_tick != 0) {
    gtk_widget_remove_tick_callback(self->sheet, self->drawer_tick);
    self->drawer_tick = 0;
  }
  if (self->drawer == NULL)
    return;
  if (!self->drawer_closed) {
    self->drawer_closed = TRUE;
    luma_modal_handle_close(self->drawer);
  }
  g_clear_signal_handler(&self->drawer_handler, self->drawer);
  g_clear_object(&self->drawer);
}

/* Bring the sheet back beside the island (from a drawer). */
static void pane_reclaim(LumaDetailsPane *self) {
  pane_drop_drawer(self);
  GtkWidget *parent = gtk_widget_get_parent(self->sheet);
  if (parent == GTK_WIDGET(self))
    return;
  if (parent != NULL) {
    if (GTK_IS_OVERLAY(parent))
      gtk_overlay_remove_overlay(GTK_OVERLAY(parent), self->sheet);
    else
      gtk_widget_unparent(self->sheet);
  }
  const char *drop[] = {"lumaui-modal", "drawer", "shown", "nudge"};
  for (guint i = 0; i < G_N_ELEMENTS(drop); i++)
    gtk_widget_remove_css_class(self->sheet, drop[i]);
  luma_ui_set_css_class(self->sheet, "luma-island", !self->embedded);
  gtk_widget_set_can_target(self->sheet, TRUE);
  gtk_widget_set_halign(self->sheet, GTK_ALIGN_FILL);
  gtk_widget_set_valign(self->sheet, GTK_ALIGN_FILL);
  gtk_widget_set_vexpand(self->sheet, TRUE);
  gtk_widget_set_visible(self->handle, FALSE);
  gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(self->scroller), FALSE);
  gtk_scrolled_window_set_max_content_height(GTK_SCROLLED_WINDOW(self->scroller), -1);
  gtk_widget_set_visible(self->sheet, self->shown && !luma_ui_is_phone(GTK_WIDGET(self)));
  gtk_box_append(GTK_BOX(self), self->sheet);
}

static gboolean reclaim_later_cb(gpointer user_data) {
  LumaDetailsPane *self = user_data;
  if (!pane_is_drawer(self) && gtk_widget_get_parent(self->sheet) != GTK_WIDGET(self))
    pane_reclaim(self);
  return G_SOURCE_REMOVE;
}

static void pane_reclaim_later(LumaDetailsPane *self) {
  guint delay = MAX(1u, luma_ui_duration(LUMA_UI_MOTION_DIALOG_FADE_MS, FALSE)) + 20;
  g_timeout_add_full(G_PRIORITY_DEFAULT, delay, reclaim_later_cb, g_object_ref(self), g_object_unref);
}

static gboolean entering_done(gpointer user_data) {
  gtk_widget_remove_css_class(user_data, "entering");
  return G_SOURCE_REMOVE;
}

static void pane_as_side(LumaDetailsPane *self) {
  gboolean entering = !gtk_widget_get_visible(self->sheet) || pane_is_drawer(self);
  pane_reclaim(self);
  gtk_widget_set_visible(self->sheet, TRUE);
  pane_listen_esc(self, !self->embedded);
  if (self->embedded) {
    gtk_widget_set_vexpand(self->sheet, FALSE);
    gtk_widget_set_vexpand(self->scroller, FALSE);
    gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(self->scroller), TRUE);
  }
  if (entering) {
    pane_remember_focus(self);
    if (!luma_ui_reduced_motion()) {
      gtk_widget_add_css_class(self->sheet, "entering");
      luma_ui_on_next_frame(self->sheet, entering_done, self->sheet);
    }
  }
}

static void drawer_cancelled(LumaModalHandle *handle G_GNUC_UNUSED, gpointer user_data) {
  /* Tapped outside, swiped down or Esc: the drawer fades, then the sheet comes home. */
  LumaDetailsPane *self = user_data;
  self->drawer_closed = TRUE;
  self->wanted = FALSE;
  if (self->shown) {
    pane_set_shown(self, FALSE);
    pane_sync_info_buttons(self, FALSE);
  }
  pane_reclaim_later(self);
}

static void pane_limit_drawer_body(LumaDetailsPane *self, int height) {
  if (height <= 0)
    return;
  int footer_height = 0;
  if (gtk_widget_get_visible(self->footer))
    gtk_widget_measure(self->footer, GTK_ORIENTATION_VERTICAL, -1, NULL, &footer_height, NULL, NULL);
  int share = height * LUMA_UI_STRUCTURE_DETAILS_PHONE_MAX_HEIGHT_PCT / 100;
  int maximum = MAX(120, share - LUMA_UI_STRUCTURE_DETAILS_HEADER_HEIGHT - 24 - footer_height);
  if (gtk_scrolled_window_get_max_content_height(GTK_SCROLLED_WINDOW(self->scroller)) != maximum)
    gtk_scrolled_window_set_max_content_height(GTK_SCROLLED_WINDOW(self->scroller), maximum);
}

static gboolean pane_drawer_resized(GtkWidget *widget G_GNUC_UNUSED,
                                    GdkFrameClock *clock G_GNUC_UNUSED, gpointer data) {
  LumaDetailsPane *self = data;
  if (!pane_is_drawer(self)) {
    self->drawer_tick = 0;
    return G_SOURCE_REMOVE;
  }
  LumaLayerHost *host = luma_layer_host_window_host(GTK_WIDGET(self));
  if (host != NULL)
    pane_limit_drawer_body(self, gtk_widget_get_height(GTK_WIDGET(host)));
  return G_SOURCE_CONTINUE;
}

static void pane_as_drawer(LumaDetailsPane *self) {
  if (self->presenting_drawer || pane_is_drawer(self))
    return;
  self->presenting_drawer = TRUE;
  pane_remember_focus(self);
  pane_listen_esc(self, FALSE);
  LumaLayerHost *host = luma_layer_host_window_host(GTK_WIDGET(self));
  if (host == NULL) {
    self->presenting_drawer = FALSE;
    return;
  }
  pane_reclaim(self);
  gtk_box_remove(GTK_BOX(self), self->sheet);
  gtk_widget_set_visible(self->sheet, TRUE);
  gtk_widget_remove_css_class(self->sheet, "luma-island");
  gtk_widget_add_css_class(self->sheet, "drawer");
  gtk_widget_set_visible(self->handle, TRUE);
  pane_limit_drawer_body(self, gtk_widget_get_height(GTK_WIDGET(host)));
  gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(self->scroller), TRUE);
  gtk_widget_set_vexpand(self->sheet, FALSE);
  LumaModalHandle *handle = luma_layer_host_present_modal(host, self->sheet, NULL, LUMA_DRAWER_MODE_ALWAYS);
  if (handle == NULL) {
    self->presenting_drawer = FALSE;
    return;
  }
  self->drawer = g_object_ref(handle);
  self->drawer_closed = FALSE;
  self->drawer_handler = g_signal_connect(handle, "cancelled", G_CALLBACK(drawer_cancelled), self);
  self->drawer_tick = gtk_widget_add_tick_callback(self->sheet, pane_drawer_resized, self, NULL);
  self->presenting_drawer = FALSE;
}

static void pane_hide(LumaDetailsPane *self) {
  pane_listen_esc(self, FALSE);
  if (pane_is_drawer(self)) {
    self->drawer_closed = TRUE;
    luma_modal_handle_close(self->drawer); /* it fades; the sheet comes home afterwards */
    pane_reclaim_later(self);
  } else {
    pane_reclaim(self);
    gtk_widget_set_visible(self->sheet, FALSE);
  }
}

static void pane_apply(LumaDetailsPane *self, gboolean on) {
  if (!on)
    pane_hide(self);
  else if (!self->embedded && luma_ui_is_phone(GTK_WIDGET(self))) {
    if (gtk_widget_get_mapped(GTK_WIDGET(self)))
      pane_as_drawer(self);
  } else {
    pane_as_side(self);
  }
  pane_set_shown(self, on);
  pane_sync_info_buttons(self, on);
  if (!on)
    pane_return_focus(self);
}

static void pane_reflow(LumaDetailsPane *self) {
  if (self->shown)
    pane_apply(self, TRUE);
}

static void pane_width(GtkWidget *widget, int width G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  pane_reflow(LUMA_DETAILS_PANE(widget));
}

static void pane_map(GtkWidget *widget) {
  GTK_WIDGET_CLASS(luma_details_pane_parent_class)->map(widget);
  pane_reflow(LUMA_DETAILS_PANE(widget));
}

static void pane_unmap(GtkWidget *widget) {
  LumaDetailsPane *self = LUMA_DETAILS_PANE(widget);
  /* A pane in a view that is not showing never floats over the window. */
  if (pane_is_drawer(self))
    pane_reclaim(self);
  GTK_WIDGET_CLASS(luma_details_pane_parent_class)->unmap(widget);
}

static gboolean pane_update(LumaDetailsPane *self) {
  if (self->subject == NULL && !self->main)
    self->wanted = FALSE;
  gboolean on = self->main || (self->wanted && self->subject != NULL);
  pane_apply(self, on);
  return on;
}

static void luma_details_pane_get_property(GObject *object, guint prop_id, GValue *value, GParamSpec *pspec) {
  LumaDetailsPane *self = LUMA_DETAILS_PANE(object);
  switch (prop_id) {
  case PROP_SHOWN:
    g_value_set_boolean(value, self->shown);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
  }
}

static void luma_details_pane_dispose(GObject *object) {
  LumaDetailsPane *self = LUMA_DETAILS_PANE(object);
  pane_drop_drawer(self);
  if (self->esc != NULL && self->esc_root != NULL)
    gtk_widget_remove_controller(self->esc_root, self->esc);
  self->esc = NULL;
  g_clear_weak_pointer(&self->esc_root);
  g_clear_weak_pointer(&self->return_focus);
  if (self->info_buttons != NULL) {
    for (guint i = 0; i < self->info_buttons->len; i++)
      g_object_weak_unref(g_ptr_array_index(self->info_buttons, i), info_button_gone, self);
    g_ptr_array_set_size(self->info_buttons, 0);
  }
  g_clear_object(&self->subject);
  if (self->sheet != NULL && gtk_widget_get_parent(self->sheet) != NULL &&
      gtk_widget_get_parent(self->sheet) != GTK_WIDGET(self)) {
    GtkWidget *parent = gtk_widget_get_parent(self->sheet);
    if (GTK_IS_OVERLAY(parent))
      gtk_overlay_remove_overlay(GTK_OVERLAY(parent), self->sheet);
    else
      gtk_widget_unparent(self->sheet);
  }
  g_clear_object(&self->sheet);
  G_OBJECT_CLASS(luma_details_pane_parent_class)->dispose(object);
}

static void luma_details_pane_finalize(GObject *object) {
  g_clear_pointer(&LUMA_DETAILS_PANE(object)->info_buttons, g_ptr_array_unref);
  G_OBJECT_CLASS(luma_details_pane_parent_class)->finalize(object);
}

static void luma_details_pane_class_init(LumaDetailsPaneClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->get_property = luma_details_pane_get_property;
  object_class->dispose = luma_details_pane_dispose;
  object_class->finalize = luma_details_pane_finalize;
  widget_class->map = pane_map;
  widget_class->unmap = pane_unmap;
  /**
   * LumaDetailsPane:shown:
   *
   * Whether the pane shows (beside the island or as a drawer).
   */
  pane_props[PROP_SHOWN] = g_param_spec_boolean("shown", NULL, NULL, FALSE,
                                                G_PARAM_READABLE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, N_PROPS, pane_props);
}

static void close_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  luma_details_pane_close(user_data);
}

static void back_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaDetailsPane *self = user_data;
  if (self->back_callback != NULL)
    ((void (*)(gpointer))self->back_callback)(self->back_data);
}

static gboolean pane_closable(LumaDetailsPane *self) {
  return self->closable < 0 ? !self->main : self->closable;
}

static void luma_details_pane_init(LumaDetailsPane *self) {
  luma_ui_install();
  self->info_buttons = g_ptr_array_new();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_set_hexpand(GTK_WIDGET(self), FALSE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-details-slot");

  /* The sheet is the pane itself: beside the island on a computer, the
   * drawer's card on a phone. The slot stays in the app's layout. */
  self->sheet = g_object_ref_sink(g_object_new(LUMA_TYPE_ISLAND, "orientation", GTK_ORIENTATION_VERTICAL, "hexpand",
                                               FALSE, "vexpand", TRUE, "accessible-role",
                                               GTK_ACCESSIBLE_ROLE_REGION, NULL));
  gtk_widget_add_css_class(self->sheet, "lumaui-details");
  gtk_widget_add_css_class(self->sheet, "luma-island");
  gtk_widget_set_overflow(self->sheet, GTK_OVERFLOW_HIDDEN);
  self->handle = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(self->handle, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(self->handle, "lumaui-drawer-handle");
  gtk_widget_set_visible(self->handle, FALSE);
  gtk_box_append(GTK_BOX(self->sheet), self->handle);

  self->header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->header, "lumaui-details-header");
  self->closable = -1;
  self->back_button = luma_ui_icon_button("chevron-left", "Back", "lumaui-details-close", FALSE, NULL);
  gtk_widget_add_css_class(self->back_button, "lumaui-details-back");
  gtk_widget_set_visible(self->back_button, FALSE);
  g_signal_connect(self->back_button, "clicked", G_CALLBACK(back_clicked), self);
  gtk_box_append(GTK_BOX(self->header), self->back_button);
  self->title_label = pane_label("Details", "lumaui-details-title", 0, TRUE);
  gtk_widget_set_hexpand(self->title_label, TRUE);
  gtk_box_append(GTK_BOX(self->header), self->title_label);
  self->close_button = luma_ui_icon_button("x", "Close", "lumaui-details-close", FALSE, NULL);
  g_signal_connect(self->close_button, "clicked", G_CALLBACK(close_clicked), self);
  gtk_box_append(GTK_BOX(self->header), self->close_button);
  gtk_box_append(GTK_BOX(self->sheet), self->header);

  self->body = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(self->body, "lumaui-details-body");
  self->scroller = gtk_scrolled_window_new();
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(self->scroller), GTK_POLICY_NEVER, GTK_POLICY_EXTERNAL);
  gtk_widget_set_vexpand(self->scroller, TRUE);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->scroller), self->body);
  gtk_widget_add_css_class(self->scroller, "lumaui-details-scroll");
  gtk_box_append(GTK_BOX(self->sheet), self->scroller);
  self->footer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(self->footer, "lumaui-details-footer");
  gtk_widget_set_visible(self->footer, FALSE);
  gtk_box_append(GTK_BOX(self->sheet), self->footer);
  gtk_box_append(GTK_BOX(self), self->sheet);
  gtk_widget_set_visible(self->sheet, FALSE);
  luma_ui_width_watch(GTK_WIDGET(self), pane_width, NULL, (LUMA_TIER_PHONE_BELOW - 1));
}

GtkWidget *luma_details_pane_new(const char *title, gboolean main) {
  LumaDetailsPane *self = g_object_new(LUMA_TYPE_DETAILS_PANE, NULL);
  luma_details_pane_set_title(self, title != NULL ? title : "Details");
  if (main)
    luma_details_pane_set_main(self, TRUE);
  return GTK_WIDGET(self);
}

void luma_details_pane_set_title(LumaDetailsPane *self, const char *title) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(title != NULL);
  gtk_label_set_label(GTK_LABEL(self->title_label), title);
  luma_ui_set_accessible_label(self->sheet, title);
}

void luma_details_pane_set_leading(LumaDetailsPane *self, GtkWidget *leading) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  if (self->leading == leading)
    return;
  g_return_if_fail(leading == NULL || (GTK_IS_WIDGET(leading) && gtk_widget_get_parent(leading) == NULL));
  if (self->leading != NULL)
    gtk_box_remove(GTK_BOX(self->header), self->leading);
  self->leading = leading;
  if (leading != NULL) {
    gtk_widget_set_valign(leading, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(leading, "lumaui-details-leading");
    gtk_box_insert_child_after(GTK_BOX(self->header), leading, self->back_button);
  }
}

void luma_details_pane_set_title_content(LumaDetailsPane *self, GtkWidget *content) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  if (self->title_content == content)
    return;
  g_return_if_fail(content == NULL || (GTK_IS_WIDGET(content) && gtk_widget_get_parent(content) == NULL));
  if (self->title_content != NULL)
    gtk_box_remove(GTK_BOX(self->header), self->title_content);
  self->title_content = content;
  gtk_widget_set_visible(self->title_label, content == NULL);
  if (content != NULL) {
    gtk_widget_set_valign(content, GTK_ALIGN_CENTER);
    gtk_widget_set_hexpand(content, TRUE);
    gtk_widget_add_css_class(content, "lumaui-details-title-content");
    gtk_box_insert_child_after(GTK_BOX(self->header), content, self->title_label);
  }
}

void luma_details_pane_add(LumaDetailsPane *self, GtkWidget *widget) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(GTK_IS_WIDGET(widget));
  gtk_box_append(GTK_BOX(self->body), widget);
}

void luma_details_pane_add_hero(LumaDetailsPane *self, const char *title, const char *subtitle,
                                GtkWidget *lead) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(title != NULL);
  g_return_if_fail(lead == NULL || GTK_IS_WIDGET(lead));
  GtkWidget *hero = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_halign(hero, GTK_ALIGN_FILL);
  gtk_widget_add_css_class(hero, "lumaui-details-hero");
  if (lead != NULL) {
    gtk_widget_set_halign(lead, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(hero), lead);
  }
  GtkWidget *name = gtk_label_new(title);
  gtk_label_set_wrap(GTK_LABEL(name), TRUE);
  gtk_label_set_justify(GTK_LABEL(name), GTK_JUSTIFY_CENTER);
  gtk_label_set_wrap_mode(GTK_LABEL(name), PANGO_WRAP_WORD_CHAR);
  gtk_label_set_max_width_chars(GTK_LABEL(name), 1);
  gtk_widget_set_hexpand(name, TRUE);
  gtk_widget_add_css_class(name, "lumaui-details-hero-title");
  gtk_box_append(GTK_BOX(hero), name);
  if (subtitle != NULL && *subtitle != '\0')
    gtk_box_append(GTK_BOX(hero), pane_label(subtitle, "lumaui-details-hero-sub", 0.5f, TRUE));
  luma_details_pane_add(self, hero);
}

void luma_details_pane_set_header_visible(LumaDetailsPane *self, gboolean visible) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  gtk_widget_set_visible(self->header, visible);
}

void luma_details_pane_add_subject(LumaDetailsPane *self, const char *title,
                                   const char *subtitle, GtkWidget *lead) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(title != NULL);
  g_return_if_fail(lead == NULL || GTK_IS_WIDGET(lead));
  GtkWidget *subject = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(subject, "lumaui-details-subject");
  if (lead != NULL) {
    gtk_widget_set_valign(lead, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(subject), lead);
  }
  GtkWidget *words = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_valign(words, GTK_ALIGN_CENTER);
  gtk_widget_set_hexpand(words, TRUE);
  GtkWidget *name = pane_label(title, "lumaui-file-summary-title", 0, FALSE);
  gtk_label_set_ellipsize(GTK_LABEL(name), PANGO_ELLIPSIZE_END);
  gtk_label_set_max_width_chars(GTK_LABEL(name), 1);
  gtk_box_append(GTK_BOX(words), name);
  if (subtitle != NULL && *subtitle != '\0') {
    GtkWidget *caption = pane_label(subtitle, "lumaui-file-summary-subtitle", 0, FALSE);
    gtk_label_set_ellipsize(GTK_LABEL(caption), PANGO_ELLIPSIZE_END);
    gtk_label_set_max_width_chars(GTK_LABEL(caption), 1);
    gtk_box_append(GTK_BOX(words), caption);
  }
  gtk_box_append(GTK_BOX(subject), words);
  luma_details_pane_add(self, subject);
}

void luma_details_pane_add_section(LumaDetailsPane *self, const char *label, const char *action_label,
                                   const char *action_name) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(label != NULL);
  GtkWidget *heading = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_HORIZONTAL, "accessible-role",
                                    GTK_ACCESSIBLE_ROLE_HEADING, NULL);
  gtk_widget_add_css_class(heading, "lumaui-details-section");
  GtkWidget *text = pane_label(label, "lumaui-details-section-label", 0, TRUE);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_box_append(GTK_BOX(heading), text);
  luma_ui_set_accessible_label(heading, label);
  if (action_label != NULL) {
    GtkWidget *button = gtk_button_new_with_label(action_label);
    gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(button, "lumaui-details-section-action");
    if (action_name != NULL)
      gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(button), action_name);
    gtk_box_append(GTK_BOX(heading), button);
  }
  luma_details_pane_add(self, heading);
}

void luma_details_pane_add_fact(LumaDetailsPane *self, const char *key, const char *value) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(key != NULL && value != NULL);
  GtkWidget *facts = self->last_facts;
  if (facts == NULL || gtk_widget_get_last_child(self->body) != facts) {
    facts = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_widget_add_css_class(facts, "lumaui-details-facts");
    luma_details_pane_add(self, facts);
    self->last_facts = facts;
  }
  GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(row, "lumaui-details-fact");
  GtkWidget *name = gtk_label_new(key);
  gtk_label_set_xalign(GTK_LABEL(name), 0);
  gtk_widget_set_valign(name, GTK_ALIGN_START);
  gtk_widget_add_css_class(name, "lumaui-details-key");
  gtk_box_append(GTK_BOX(row), name);
  GtkWidget *shown = gtk_label_new(value);
  gtk_label_set_xalign(GTK_LABEL(shown), 1);
  gtk_widget_set_hexpand(shown, TRUE);
  gtk_label_set_wrap(GTK_LABEL(shown), TRUE);
  gtk_label_set_selectable(GTK_LABEL(shown), TRUE);
  gtk_label_set_wrap_mode(GTK_LABEL(shown), PANGO_WRAP_WORD_CHAR);
  gtk_label_set_max_width_chars(GTK_LABEL(shown), 1);
  gtk_label_set_justify(GTK_LABEL(shown), GTK_JUSTIFY_RIGHT);
  gtk_widget_add_css_class(shown, "lumaui-details-value");
  gtk_widget_set_can_focus(shown, FALSE); /* selectable by pointer; Tab goes to controls */
  gtk_box_append(GTK_BOX(row), shown);
  g_autofree char *spoken = g_strdup_printf("%s: %s", key, value);
  luma_ui_set_accessible_label(row, spoken);
  gtk_box_append(GTK_BOX(facts), row);
}

void luma_details_pane_add_row(LumaDetailsPane *self, GtkWidget *row) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(GTK_IS_WIDGET(row));
  if (!LUMA_IS_DETAILS_ROW(row) && !LUMA_IS_DETAILS_ITEM(row) && !LUMA_IS_FACT_ROW(row) && !LUMA_IS_ADD_ROW(row)) {
    g_critical("a details list holds LumaDetailsRow, LumaDetailsItem, LumaFactRow and LumaAddRow, not a %s",
               G_OBJECT_TYPE_NAME(row));
    if (g_object_is_floating(row))
      g_object_unref(g_object_ref_sink(row));
    return;
  }
  GtkWidget *group = self->last_list;
  if (group == NULL || gtk_widget_get_last_child(self->body) != group) {
    group = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL, "accessible-role",
                         GTK_ACCESSIBLE_ROLE_LIST, NULL);
    gtk_widget_add_css_class(group, "lumaui-details-list");
    luma_details_pane_add(self, group);
    self->last_list = group;
  }
  gtk_box_append(GTK_BOX(group), row);
}

LumaDetailsPhotos *luma_details_pane_add_photos(LumaDetailsPane *self, GListModel *items) {
  g_return_val_if_fail(LUMA_IS_DETAILS_PANE(self), NULL);
  g_return_val_if_fail(G_IS_LIST_MODEL(items), NULL);
  GtkWidget *photos = luma_details_photos_new(items);
  luma_details_pane_add(self, photos);
  return LUMA_DETAILS_PHOTOS(photos);
}

void luma_details_pane_set_footer(LumaDetailsPane *self, GtkWidget *widget) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(widget == NULL || GTK_IS_WIDGET(widget));
  GtkWidget *current = gtk_widget_get_first_child(self->footer);
  if (current == widget)
    return;
  g_return_if_fail(widget == NULL || gtk_widget_get_parent(widget) == NULL);
  if (current != NULL)
    gtk_box_remove(GTK_BOX(self->footer), current);
  if (widget != NULL)
    gtk_box_append(GTK_BOX(self->footer), widget);
  gtk_widget_set_visible(self->footer, widget != NULL);
}

void luma_details_pane_clear(LumaDetailsPane *self) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(self->body)) != NULL)
    gtk_box_remove(GTK_BOX(self->body), child);
  self->last_facts = self->last_list = NULL;
  luma_details_pane_set_footer(self, NULL);
}

gboolean luma_details_pane_show(LumaDetailsPane *self, gboolean open) {
  g_return_val_if_fail(LUMA_IS_DETAILS_PANE(self), FALSE);
  self->wanted = !!open;
  return pane_update(self);
}

void luma_details_pane_set_subject(LumaDetailsPane *self, GObject *subject) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  g_return_if_fail(subject == NULL || G_IS_OBJECT(subject));
  g_set_object(&self->subject, subject);
  pane_update(self);
}

GObject *luma_details_pane_get_subject(LumaDetailsPane *self) {
  g_return_val_if_fail(LUMA_IS_DETAILS_PANE(self), NULL);
  return self->subject;
}

void luma_details_pane_set_embedded(LumaDetailsPane *self, gboolean embedded) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  self->embedded = !!embedded;
  luma_ui_set_css_class(self->sheet, "embedded", self->embedded);
  luma_ui_set_css_class(self->sheet, "luma-island", !self->embedded);
  gtk_widget_set_vexpand(self->sheet, !self->embedded);
  gtk_widget_set_vexpand(self->scroller, !self->embedded);
  gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(self->scroller), self->embedded);
  pane_reflow(self);
}

void luma_details_pane_set_main(LumaDetailsPane *self, gboolean main) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  self->main = !!main;
  gtk_widget_set_visible(self->close_button, pane_closable(self));
  pane_update(self);
}

void luma_details_pane_set_closable(LumaDetailsPane *self, gboolean closable) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  self->closable = !!closable;
  gtk_widget_set_visible(self->close_button, pane_closable(self));
}

void luma_details_pane_set_back(LumaDetailsPane *self, const char *label, GCallback on_back, gpointer user_data) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  gboolean shown = label != NULL && *label != '\0';
  self->back_callback = shown ? on_back : NULL;
  self->back_data = shown ? user_data : NULL;
  gtk_widget_set_visible(self->back_button, shown);
  GtkWidget *header = gtk_widget_get_parent(self->back_button);
  if (shown)
    gtk_widget_add_css_class(header, "with-back");
  else
    gtk_widget_remove_css_class(header, "with-back");
  g_autofree char *name = shown ? g_strdup_printf("Back to %s", label) : g_strdup("Back");
  gtk_widget_set_tooltip_text(self->back_button, shown ? name : NULL);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->back_button), GTK_ACCESSIBLE_PROPERTY_LABEL, name, -1);
}

gboolean luma_details_pane_get_main(LumaDetailsPane *self) {
  g_return_val_if_fail(LUMA_IS_DETAILS_PANE(self), FALSE);
  return self->main;
}

void luma_details_pane_close(LumaDetailsPane *self) {
  g_return_if_fail(LUMA_IS_DETAILS_PANE(self));
  if (self->main)
    return;
  self->wanted = FALSE;
  pane_apply(self, FALSE);
}

gboolean luma_details_pane_toggle(LumaDetailsPane *self) {
  g_return_val_if_fail(LUMA_IS_DETAILS_PANE(self), FALSE);
  return luma_details_pane_show(self, !self->shown);
}

gboolean luma_details_pane_get_shown(LumaDetailsPane *self) {
  g_return_val_if_fail(LUMA_IS_DETAILS_PANE(self), FALSE);
  return self->shown;
}

gboolean luma_details_pane_get_is_drawer(LumaDetailsPane *self) {
  g_return_val_if_fail(LUMA_IS_DETAILS_PANE(self), FALSE);
  return pane_is_drawer(self);
}

static void info_toggled(GtkToggleButton *button, gpointer user_data) {
  LumaDetailsPane *self = user_data;
  gboolean active = gtk_toggle_button_get_active(button);
  if (active != self->shown) {
    luma_details_pane_show(self, active);
    if (gtk_toggle_button_get_active(button) != self->shown) /* nothing to describe: it stays closed */
      gtk_toggle_button_set_active(button, self->shown);
  }
}

static void info_button_gone(gpointer data, GObject *where) {
  LumaDetailsPane *self = data;
  g_ptr_array_remove(self->info_buttons, where);
}

GtkWidget *luma_details_pane_info_button(LumaDetailsPane *self) {
  g_return_val_if_fail(LUMA_IS_DETAILS_PANE(self), NULL);
  GtkWidget *button = luma_ui_icon_button("info", "Information", "lumaui-corner-button", TRUE, NULL);
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(button), self->shown);
  g_signal_connect_object(button, "toggled", G_CALLBACK(info_toggled), self, 0);
  g_ptr_array_add(self->info_buttons, button);
  g_object_weak_ref(G_OBJECT(button), info_button_gone, self);
  return button;
}
