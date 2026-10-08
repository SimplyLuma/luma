/* SPDX-License-Identifier: Apache-2.0 */
/* LumaCornerPill: the twin of structure_placement.CornerPill (Python). */
#include "luma-corner-pill.h"
#include "luma-details-pane.h"
#include "luma-menu-drawer.h"
#include "luma-ui-private.h"

enum { OPEN_IN, SHARE, EDIT_CANCELLED, EDIT_DONE, N_SIGNALS };
static guint signals[N_SIGNALS];

typedef struct {
  char *icon;
  char *label;
  char *action_name;
  GtkWidget *button;
} Action;

struct _LumaCornerPill {
  GtkBox parent_instance;
  GtkWidget *normal;
  GtkWidget *editing;
  GtkWidget *cancel_button;
  GtkWidget *done_button;
  /* The controls, in their slots (strong refs). */
  GtkWidget *modes;
  GtkWidget *people;
  GtkWidget *open_in;
  GtkWidget *share;
  GPtrArray *actions; /* Action */
  GPtrArray *states;  /* GtkWidget, strong */
  GtkWidget *info;
  GtkWidget *more;
  GMenuModel *more_menu;
  GPtrArray *labelled_buttons; /* GtkWidget, borrowed: the words that fold at phone width */
  gboolean labelled;
  gboolean keep_labels, primary_icon_only;
  char *primary;
};

G_DEFINE_FINAL_TYPE(LumaCornerPill, luma_corner_pill, GTK_TYPE_BOX)

static void action_free(gpointer data) {
  Action *action = data;
  g_free(action->icon);
  g_free(action->label);
  g_free(action->action_name);
  g_clear_object(&action->button);
  g_free(action);
}

/* A labelled or icon-only pill button (CornerPill._button). */
static GtkWidget *make_button(LumaCornerPill *self, const char *icon, const char *label, gboolean labelled,
                              gboolean primary) {
  if (!labelled || (primary && self->primary_icon_only && icon != NULL)) {
    GtkWidget *button = luma_ui_icon_button(icon, label, "lumaui-corner-button", FALSE, NULL);
    if (primary) gtk_widget_add_css_class(button, "primary");
    return button;
  }
  GtkWidget *button = gtk_button_new();
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(button, "lumaui-corner-button");
  gtk_widget_add_css_class(button, "labelled");
  if (primary)
    gtk_widget_add_css_class(button, "primary");
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(line, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(line, "lumaui-corner-line");
  if (icon != NULL)
    gtk_box_append(GTK_BOX(line), luma_ui_icon_image(icon, 0));
  GtkWidget *text = gtk_label_new(label);
  gtk_box_append(GTK_BOX(line), text);
  gtk_button_set_child(GTK_BUTTON(button), line);
  g_object_set_data(G_OBJECT(button), "luma-corner-label", text);
  g_object_set_data(G_OBJECT(button), "luma-corner-primary", GINT_TO_POINTER(primary));
  g_object_set_data(G_OBJECT(button), "luma-corner-has-icon", GINT_TO_POINTER(icon != NULL));
  gtk_widget_set_tooltip_text(button, label);
  luma_ui_set_accessible_label(button, label);
  g_ptr_array_add(self->labelled_buttons, button);
  return button;
}

/* At phone width words fold away (the key and icon-less words keep theirs). */
static void collapse(LumaCornerPill *self) {
  gboolean phone = luma_ui_is_phone(GTK_WIDGET(self));
  for (guint i = 0; i < self->labelled_buttons->len; i++) {
    GtkWidget *button = g_ptr_array_index(self->labelled_buttons, i);
    gboolean keep = self->keep_labels || GPOINTER_TO_INT(g_object_get_data(G_OBJECT(button), "luma-corner-primary")) ||
                    !GPOINTER_TO_INT(g_object_get_data(G_OBJECT(button), "luma-corner-has-icon"));
    GtkWidget *text = g_object_get_data(G_OBJECT(button), "luma-corner-label");
    gtk_widget_set_visible(text, keep || !phone);
    luma_ui_set_css_class(button, "labelled", keep || !phone);
  }
}

static void width_crossed(GtkWidget *widget, int width G_GNUC_UNUSED, gpointer data G_GNUC_UNUSED) {
  collapse(LUMA_CORNER_PILL(widget));
}

/* Put every control in the one placement order. */
static void arrange(LumaCornerPill *self) {
  g_autoptr(GPtrArray) order = g_ptr_array_new();
  if (self->modes != NULL)
    g_ptr_array_add(order, self->modes);
  if (self->people != NULL)
    g_ptr_array_add(order, self->people);
  if (self->open_in != NULL)
    g_ptr_array_add(order, self->open_in);
  if (self->share != NULL)
    g_ptr_array_add(order, self->share);
  for (guint i = 0; i < self->states->len; i++)
    g_ptr_array_add(order, g_ptr_array_index(self->states, i));
  if (self->info != NULL)
    g_ptr_array_add(order, self->info);
  for (guint i = 0; i < self->actions->len; i++)
    g_ptr_array_add(order, ((Action *)g_ptr_array_index(self->actions, i))->button);
  if (self->more != NULL)
    g_ptr_array_add(order, self->more);
  /* Drop what left its slot. */
  GtkWidget *child = gtk_widget_get_first_child(self->normal);
  while (child != NULL) {
    GtkWidget *next = gtk_widget_get_next_sibling(child);
    if (!g_ptr_array_find(order, child, NULL))
      gtk_box_remove(GTK_BOX(self->normal), child);
    child = next;
  }
  GtkWidget *previous = NULL;
  for (guint i = 0; i < order->len; i++) {
    GtkWidget *widget = g_ptr_array_index(order, i);
    if (gtk_widget_get_parent(widget) != self->normal) {
      if (gtk_widget_get_parent(widget) != NULL)
        gtk_widget_unparent(widget);
      gtk_box_append(GTK_BOX(self->normal), widget);
    }
    if (i > 0 && gtk_orientable_get_orientation(GTK_ORIENTABLE(self)) == GTK_ORIENTATION_VERTICAL) {
      GtkWidget *separator = gtk_separator_new(GTK_ORIENTATION_HORIZONTAL);
      gtk_widget_add_css_class(separator, "lumaui-corner-separator");
      gtk_box_insert_child_after(GTK_BOX(self->normal), separator, previous);
      previous = separator;
    }
    gtk_box_reorder_child_after(GTK_BOX(self->normal), widget, previous);
    previous = widget;
  }
  collapse(self);
}

void luma_corner_pill_set_orientation(LumaCornerPill *self, GtkOrientation orientation) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  g_return_if_fail(orientation == GTK_ORIENTATION_HORIZONTAL || orientation == GTK_ORIENTATION_VERTICAL);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), orientation);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self->normal), orientation);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self->editing), orientation);
  luma_ui_set_css_class(GTK_WIDGET(self), "vertical-stack", orientation == GTK_ORIENTATION_VERTICAL);
  GtkWidget *child = gtk_widget_get_first_child(self->editing);
  while (child != NULL) {
    GtkWidget *next = gtk_widget_get_next_sibling(child);
    if (GTK_IS_SEPARATOR(child)) gtk_box_remove(GTK_BOX(self->editing), child);
    child = next;
  }
  if (orientation == GTK_ORIENTATION_VERTICAL) {
    GtkWidget *separator = gtk_separator_new(GTK_ORIENTATION_HORIZONTAL);
    gtk_widget_add_css_class(separator, "lumaui-corner-separator");
    gtk_box_insert_child_after(GTK_BOX(self->editing), separator, self->cancel_button);
  }
  arrange(self);
}

static void set_slot(GtkWidget **slot, GtkWidget *widget) {
  if (widget != NULL)
    g_object_ref_sink(widget);
  g_clear_object(slot);
  *slot = widget;
}

static void forget_labelled(LumaCornerPill *self, GtkWidget *button) {
  if (button != NULL)
    g_ptr_array_remove(self->labelled_buttons, button);
}

static void share_clicked(GtkButton *button, gpointer user_data) {
  g_signal_emit(user_data, signals[SHARE], 0, button);
}

static void open_in_clicked(GtkButton *button, gpointer user_data) {
  g_signal_emit(user_data, signals[OPEN_IN], 0, button);
}

/* Share and the actions change shape with labelled and primary. */
static void build_share(LumaCornerPill *self) {
  forget_labelled(self, self->share);
  GtkWidget *button = make_button(self, "share-2", "Share", self->labelled, FALSE);
  g_signal_connect(button, "clicked", G_CALLBACK(share_clicked), self);
  set_slot(&self->share, button);
}

static void build_action(LumaCornerPill *self, Action *action) {
  forget_labelled(self, action->button);
  gboolean primary = g_strcmp0(action->label, self->primary) == 0;
  GtkWidget *button = make_button(self, action->icon, action->label, self->labelled || primary, primary);
  gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(button), action->action_name);
  set_slot(&action->button, button);
}

static void rebuild_words(LumaCornerPill *self) {
  if (self->share != NULL)
    build_share(self);
  for (guint i = 0; i < self->actions->len; i++)
    build_action(self, g_ptr_array_index(self->actions, i));
  arrange(self);
}

static void finish(LumaCornerPill *self, guint signal) {
  luma_corner_pill_stop_editing(self);
  g_signal_emit(self, signals[signal], 0);
}

static void cancel_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  finish(user_data, EDIT_CANCELLED);
}

static void done_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  finish(user_data, EDIT_DONE);
}

static gboolean unparent_closed(gpointer user_data) {
  GtkWidget *menu = user_data;
  if (gtk_widget_get_parent(menu) != NULL && !gtk_widget_get_visible(menu))
    gtk_widget_unparent(menu);
  return G_SOURCE_REMOVE;
}

static void menu_closed(GtkPopover *menu, gpointer user_data G_GNUC_UNUSED) {
  g_idle_add_full(G_PRIORITY_DEFAULT_IDLE, unparent_closed, g_object_ref(menu), g_object_unref);
}

static void more_clicked(GtkButton *button, gpointer user_data) {
  LumaCornerPill *self = user_data;
  if (self->more_menu == NULL)
    return;
  if (luma_ui_is_phone(GTK_WIDGET(button))) {
    luma_menu_drawer_present_model(GTK_WIDGET(button), self->more_menu, NULL);
    return;
  }
  GtkWidget *menu = gtk_popover_menu_new_from_model(self->more_menu);
  gtk_widget_set_parent(menu, GTK_WIDGET(button));
  gtk_popover_set_position(GTK_POPOVER(menu), GTK_POS_BOTTOM);
  /* A menu built per click is unparented once it closes. */
  g_signal_connect(menu, "closed", G_CALLBACK(menu_closed), NULL);
  gtk_popover_popup(GTK_POPOVER(menu));
}

static gboolean has_controls(LumaCornerPill *self) {
  return self->modes != NULL || self->people != NULL || self->open_in != NULL || self->share != NULL ||
         self->actions->len > 0 || self->states->len > 0 || self->info != NULL || self->more != NULL;
}

static void corner_pill_map(GtkWidget *widget) {
  GTK_WIDGET_CLASS(luma_corner_pill_parent_class)->map(widget);
  if (!has_controls(LUMA_CORNER_PILL(widget)))
    g_critical("a corner pill holds at least one control");
}

static void luma_corner_pill_dispose(GObject *object) {
  LumaCornerPill *self = LUMA_CORNER_PILL(object);
  g_clear_object(&self->modes);
  g_clear_object(&self->people);
  g_clear_object(&self->open_in);
  g_clear_object(&self->share);
  g_clear_object(&self->info);
  g_clear_object(&self->more);
  g_clear_object(&self->more_menu);
  if (self->actions != NULL)
    g_ptr_array_set_size(self->actions, 0);
  if (self->states != NULL)
    g_ptr_array_set_size(self->states, 0);
  if (self->labelled_buttons != NULL)
    g_ptr_array_set_size(self->labelled_buttons, 0);
  G_OBJECT_CLASS(luma_corner_pill_parent_class)->dispose(object);
}

static void luma_corner_pill_finalize(GObject *object) {
  LumaCornerPill *self = LUMA_CORNER_PILL(object);
  g_clear_pointer(&self->actions, g_ptr_array_unref);
  g_clear_pointer(&self->states, g_ptr_array_unref);
  g_clear_pointer(&self->labelled_buttons, g_ptr_array_unref);
  g_clear_pointer(&self->primary, g_free);
  G_OBJECT_CLASS(luma_corner_pill_parent_class)->finalize(object);
}

static void luma_corner_pill_class_init(LumaCornerPillClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->dispose = luma_corner_pill_dispose;
  object_class->finalize = luma_corner_pill_finalize;
  widget_class->map = corner_pill_map;
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_TOOLBAR);
  /**
   * LumaCornerPill::open-in:
   * @self: the pill
   * @anchor: the Open in button, to anchor the menu to
   */
  signals[OPEN_IN] = g_signal_new("open-in", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                  G_TYPE_NONE, 1, GTK_TYPE_WIDGET);
  /**
   * LumaCornerPill::share:
   * @self: the pill
   * @anchor: the Share button
   */
  signals[SHARE] = g_signal_new("share", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                G_TYPE_NONE, 1, GTK_TYPE_WIDGET);
  /**
   * LumaCornerPill::edit-cancelled:
   * @self: the pill
   */
  signals[EDIT_CANCELLED] = g_signal_new("edit-cancelled", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                         NULL, NULL, NULL, G_TYPE_NONE, 0);
  /**
   * LumaCornerPill::edit-done:
   * @self: the pill
   */
  signals[EDIT_DONE] = g_signal_new("edit-done", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                                    NULL, G_TYPE_NONE, 0);
}

static void luma_corner_pill_init(LumaCornerPill *self) {
  luma_ui_install();
  self->actions = g_ptr_array_new_with_free_func(action_free);
  self->states = g_ptr_array_new_with_free_func(g_object_unref);
  self->labelled_buttons = g_ptr_array_new();
  GtkWidget *widget = GTK_WIDGET(self);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_set_halign(widget, GTK_ALIGN_END);
  gtk_widget_set_valign(widget, GTK_ALIGN_START);
  gtk_widget_add_css_class(widget, "lumaui-corner");
  luma_ui_set_accessible_label(widget, "View and item");
  self->normal = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->normal, "lumaui-corner-group");
  self->editing = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_visible(self->editing, FALSE);
  gtk_widget_add_css_class(self->editing, "lumaui-corner-group");
  gtk_box_append(GTK_BOX(self), self->normal);
  gtk_box_append(GTK_BOX(self), self->editing);
  self->cancel_button = make_button(self, NULL, "Cancel", TRUE, FALSE);
  g_signal_connect(self->cancel_button, "clicked", G_CALLBACK(cancel_clicked), self);
  self->done_button = make_button(self, NULL, "Done", TRUE, TRUE); /* v70: words only */
  g_signal_connect(self->done_button, "clicked", G_CALLBACK(done_clicked), self);
  gtk_box_append(GTK_BOX(self->editing), self->cancel_button);
  gtk_box_append(GTK_BOX(self->editing), self->done_button);
  luma_ui_arrow_keys(self->normal, GTK_ORIENTATION_HORIZONTAL, FALSE);
  luma_ui_arrow_keys(self->editing, GTK_ORIENTATION_HORIZONTAL, FALSE);
  luma_ui_width_watch(widget, width_crossed, NULL, (LUMA_TIER_PHONE_BELOW - 1));
}

GtkWidget *luma_corner_pill_new(void) { return g_object_new(LUMA_TYPE_CORNER_PILL, "accessible-role", GTK_ACCESSIBLE_ROLE_TOOLBAR, NULL); }

void luma_corner_pill_set_modes(LumaCornerPill *self, LumaModeSwitch *modes) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  g_return_if_fail(modes == NULL || LUMA_IS_MODE_SWITCH(modes));
  if (self->modes == GTK_WIDGET(modes))
    return;
  set_slot(&self->modes, GTK_WIDGET(modes));
  arrange(self);
}

void luma_corner_pill_set_people(LumaCornerPill *self, GtkWidget *people) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  g_return_if_fail(people == NULL || GTK_IS_WIDGET(people));
  if (self->people == people)
    return;
  if (self->people != NULL)
    gtk_widget_remove_css_class(self->people, "lumaui-corner-people");
  if (people != NULL)
    gtk_widget_add_css_class(people, "lumaui-corner-people");
  set_slot(&self->people, people);
  arrange(self);
}

void luma_corner_pill_add_open_in(LumaCornerPill *self) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  if (self->open_in != NULL)
    return;
  GtkWidget *button = luma_ui_icon_button("square-arrow-out-up-right", "Open in", "lumaui-corner-button",
                                          FALSE, NULL);
  gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_HAS_POPUP, TRUE, -1);
  g_signal_connect(button, "clicked", G_CALLBACK(open_in_clicked), self);
  set_slot(&self->open_in, button);
  arrange(self);
}

void luma_corner_pill_add_share(LumaCornerPill *self) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  if (self->share != NULL)
    return;
  build_share(self);
  arrange(self);
}

void luma_corner_pill_add_action(LumaCornerPill *self, const char *icon, const char *label,
                                 const char *action_name) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  g_return_if_fail(icon != NULL && label != NULL && action_name != NULL);
  Action *action = g_new0(Action, 1);
  action->icon = g_strdup(icon);
  action->label = g_strdup(label);
  action->action_name = g_strdup(action_name);
  g_ptr_array_add(self->actions, action);
  build_action(self, action);
  arrange(self);
}

/* v70 draws an on state filled (the Favourite heart, .ib.loved): swap to the
 * glyph's -filled twin when there is one, and a heart takes the love colour
 * (structure_placement._fill_when_on). */
static void fill_sync(GtkToggleButton *button, gpointer user_data G_GNUC_UNUSED) {
  const char *icon = g_object_get_data(G_OBJECT(button), "luma-corner-icon");
  GtkWidget *image = gtk_button_get_child(GTK_BUTTON(button));
  if (icon == NULL || !GTK_IS_IMAGE(image))
    return;
  g_autofree char *filled = g_strconcat(icon, "-filled", NULL);
  g_autofree char *name = luma_ui_icon_name(gtk_toggle_button_get_active(button) ? filled : icon);
  gtk_image_set_from_icon_name(GTK_IMAGE(image), name);
}

static void fill_when_on(GtkWidget *button, const char *icon) {
  GdkDisplay *display = gdk_display_get_default();
  g_autofree char *filled = g_strconcat(icon, "-filled", NULL);
  g_autofree char *name = luma_ui_icon_name(filled);
  if (display == NULL || !gtk_icon_theme_has_icon(gtk_icon_theme_get_for_display(display), name))
    return;
  /* As Python's _fill_when_on: a heart takes the love colour only when it can fill. */
  if (g_str_equal(icon, "heart"))
    gtk_widget_add_css_class(button, "love");
  g_object_set_data_full(G_OBJECT(button), "luma-corner-icon", g_strdup(icon), g_free);
  g_signal_connect(button, "toggled", G_CALLBACK(fill_sync), NULL);
  g_signal_connect(button, "realize", G_CALLBACK(fill_sync), NULL);
}

void luma_corner_pill_add_state(LumaCornerPill *self, const char *icon, const char *label,
                                const char *action_name) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  g_return_if_fail(icon != NULL && label != NULL && action_name != NULL);
  GtkWidget *button = luma_ui_icon_button(icon, label, "lumaui-corner-button", TRUE, NULL);
  fill_when_on(button, icon);
  gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(button), action_name);
  g_ptr_array_add(self->states, g_object_ref_sink(button));
  arrange(self);
}

void luma_corner_pill_set_info_pane(LumaCornerPill *self, LumaDetailsPane *pane) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  g_return_if_fail(LUMA_IS_DETAILS_PANE(pane));
  set_slot(&self->info, luma_details_pane_info_button(pane));
  arrange(self);
}

void luma_corner_pill_set_info_action(LumaCornerPill *self, const char *action_name) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  g_return_if_fail(action_name != NULL);
  GtkWidget *button = luma_ui_icon_button("info", "Information", "lumaui-corner-button", TRUE, NULL);
  gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(button), action_name);
  set_slot(&self->info, button);
  arrange(self);
}

void luma_corner_pill_set_more_menu(LumaCornerPill *self, GMenuModel *menu) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  g_return_if_fail(menu == NULL || G_IS_MENU_MODEL(menu));
  g_set_object(&self->more_menu, menu);
  if (menu != NULL && self->more == NULL) {
    GtkWidget *button = luma_ui_icon_button("ellipsis", "More", "lumaui-corner-button", FALSE, NULL);
    gtk_widget_add_css_class(button, "more");
    gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_HAS_POPUP, TRUE, -1);
    g_signal_connect(button, "clicked", G_CALLBACK(more_clicked), self);
    set_slot(&self->more, button);
  } else if (menu == NULL) {
    set_slot(&self->more, NULL);
  }
  arrange(self);
}

void luma_corner_pill_set_labelled(LumaCornerPill *self, gboolean labelled) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  labelled = !!labelled;
  if (self->labelled == labelled)
    return;
  self->labelled = labelled;
  rebuild_words(self);
}

void luma_corner_pill_set_primary(LumaCornerPill *self, const char *label) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  if (label != NULL) {
    gboolean found = FALSE;
    for (guint i = 0; i < self->actions->len && !found; i++)
      found = g_strcmp0(((Action *)g_ptr_array_index(self->actions, i))->label, label) == 0;
    if (!found) {
      g_critical("the primary action '%s' is not one of the actions", label);
      return;
    }
  }
  if (g_strcmp0(self->primary, label) == 0)
    return;
  g_free(self->primary);
  self->primary = g_strdup(label);
  rebuild_words(self);
}

void luma_corner_pill_edit(LumaCornerPill *self, const char *done_label) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  const char *done = done_label != NULL ? done_label : "Done";
  GtkWidget *text = g_object_get_data(G_OBJECT(self->done_button), "luma-corner-label");
  gtk_label_set_label(GTK_LABEL(text), done);
  luma_ui_set_accessible_label(self->done_button, done);
  gtk_widget_set_visible(self->normal, FALSE);
  gtk_widget_set_visible(self->editing, TRUE);
  gtk_widget_grab_focus(self->done_button);
}

void luma_corner_pill_stop_editing(LumaCornerPill *self) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  gtk_widget_set_visible(self->editing, FALSE);
  gtk_widget_set_visible(self->normal, TRUE);
}

gboolean luma_corner_pill_get_editing(LumaCornerPill *self) {
  g_return_val_if_fail(LUMA_IS_CORNER_PILL(self), FALSE);
  return gtk_widget_get_visible(self->editing);
}

void luma_corner_pill_set_keep_labels(LumaCornerPill *self, gboolean keep) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  self->keep_labels = !!keep;
  collapse(self);
}

void luma_corner_pill_set_primary_icon_only(LumaCornerPill *self, gboolean icon_only) {
  g_return_if_fail(LUMA_IS_CORNER_PILL(self));
  if (self->primary_icon_only == icon_only) return;
  self->primary_icon_only = icon_only;
  rebuild_words(self);
}
