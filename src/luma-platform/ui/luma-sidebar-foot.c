/* SPDX-License-Identifier: Apache-2.0 */
/* LumaSidebarFoot and LumaFilterHeading: the twins of structure_sidebar.SidebarFoot
 * and FilterHeading (Python). */
#include "luma-sidebar-foot.h"
#include "luma-menu-drawer.h"
#include "luma-ui-private.h"

/* ── FilterHeading ───────────────────────────────────────────────────── */

enum { SHOW_ALL, N_HEADING_SIGNALS };
static guint heading_signals[N_HEADING_SIGNALS];

struct _LumaFilterHeading {
  GtkBox parent_instance;
  GtkWidget *label;
  GtkWidget *show_all;
};

G_DEFINE_FINAL_TYPE(LumaFilterHeading, luma_filter_heading, GTK_TYPE_BOX)

static void show_all_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  g_signal_emit(user_data, heading_signals[SHOW_ALL], 0);
}

static void luma_filter_heading_class_init(LumaFilterHeadingClass *klass) {
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_STATUS);
  /**
   * LumaFilterHeading::show-all:
   * @self: the heading
   *
   * Show all was pressed.
   */
  heading_signals[SHOW_ALL] = g_signal_new("show-all", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                                           NULL, G_TYPE_NONE, 0);
}

static void luma_filter_heading_init(LumaFilterHeading *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-filter-heading");
  self->label = gtk_label_new("");
  gtk_label_set_xalign(GTK_LABEL(self->label), 0);
  gtk_widget_set_hexpand(self->label, TRUE);
  gtk_label_set_ellipsize(GTK_LABEL(self->label), PANGO_ELLIPSIZE_END);
  gtk_widget_add_css_class(self->label, "lumaui-filter-heading-label");
  gtk_box_append(GTK_BOX(self), self->label);
  self->show_all = gtk_button_new_with_label("Show all");
  gtk_widget_set_valign(self->show_all, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(self->show_all, "lumaui-filter-heading-button");
  g_signal_connect(self->show_all, "clicked", G_CALLBACK(show_all_clicked), self);
  gtk_box_append(GTK_BOX(self), self->show_all);
}

GtkWidget *luma_filter_heading_new(const char *label) {
  GtkWidget *self = g_object_new(LUMA_TYPE_FILTER_HEADING,
                               "accessible-role", GTK_ACCESSIBLE_ROLE_STATUS, NULL);
  if (label != NULL)
    luma_filter_heading_set_label(LUMA_FILTER_HEADING(self), label);
  return self;
}

void luma_filter_heading_set_label(LumaFilterHeading *self, const char *label) {
  g_return_if_fail(LUMA_IS_FILTER_HEADING(self));
  gtk_label_set_label(GTK_LABEL(self->label), label != NULL ? label : "");
}

/* ── SidebarFoot ─────────────────────────────────────────────────────── */

enum { SEARCH_CHANGED, FILTER_CHANGED, N_FOOT_SIGNALS };
static guint foot_signals[N_FOOT_SIGNALS];

typedef struct {
  char *key;
  char *label;
  char *icon;
  int count; /* -1: no badge */
} Filter;

struct _LumaSidebarFoot {
  GtkBox parent_instance;
  GtkWidget *field;
  GtkWidget *entry;
  GtkWidget *clear_button;
  GtkWidget *filter_button;
  GtkWidget *add_button;
  LumaFilterHeading *heading; /* strong: the app places it over the list */
  GPtrArray *filters;         /* Filter */
  char *filter;
  GSimpleActionGroup *actions;
  GtkWidget *menu; /* strong: the open filter menu */
  guint search_source;
};

G_DEFINE_FINAL_TYPE(LumaSidebarFoot, luma_sidebar_foot, GTK_TYPE_BOX)

static void filter_free(gpointer data) {
  Filter *filter = data;
  g_free(filter->key);
  g_free(filter->label);
  g_free(filter->icon);
  g_free(filter);
}

static Filter *find_filter(LumaSidebarFoot *self, const char *key) {
  for (guint i = 0; i < self->filters->len; i++) {
    Filter *filter = g_ptr_array_index(self->filters, i);
    if (g_strcmp0(filter->key, key) == 0)
      return filter;
  }
  return NULL;
}

/* Search */

static gboolean search_pause(gpointer user_data) {
  LumaSidebarFoot *self = user_data;
  self->search_source = 0;
  g_signal_emit(self, foot_signals[SEARCH_CHANGED], 0, gtk_editable_get_text(GTK_EDITABLE(self->entry)));
  return G_SOURCE_REMOVE;
}

static void clear_search(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaSidebarFoot *self = user_data;
  gtk_editable_set_text(GTK_EDITABLE(self->entry), "");
  gtk_widget_grab_focus(self->entry);
}

static void search_changed(GtkEditable *entry G_GNUC_UNUSED, gpointer user_data) {
  LumaSidebarFoot *self = user_data;
  gtk_widget_set_visible(self->clear_button, *gtk_editable_get_text(GTK_EDITABLE(self->entry)) != '\0');
  g_clear_handle_id(&self->search_source, g_source_remove);
  self->search_source = g_timeout_add(luma_ui_duration(LUMA_UI_MOTION_SEARCH_DEBOUNCE_MS, TRUE), search_pause, self);
}

static gboolean search_key(GtkEventControllerKey *keys G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                           GdkModifierType state G_GNUC_UNUSED, gpointer user_data) {
  LumaSidebarFoot *self = user_data;
  const char *text = gtk_editable_get_text(GTK_EDITABLE(self->entry));
  if (keyval == GDK_KEY_Escape && text != NULL && *text != '\0') {
    gtk_editable_set_text(GTK_EDITABLE(self->entry), ""); /* Esc clears first; a second Esc reaches the window */
    return TRUE;
  }
  return FALSE;
}

static void field_released(GtkGestureClick *click G_GNUC_UNUSED, int n G_GNUC_UNUSED, double x G_GNUC_UNUSED,
                           double y G_GNUC_UNUSED, gpointer user_data) {
  gtk_widget_grab_focus(LUMA_SIDEBAR_FOOT(user_data)->entry);
}

static void focus_enter(GtkEventControllerFocus *focus G_GNUC_UNUSED, gpointer user_data) {
  gtk_widget_add_css_class(LUMA_SIDEBAR_FOOT(user_data)->field, "focused");
}

static void focus_leave(GtkEventControllerFocus *focus G_GNUC_UNUSED, gpointer user_data) {
  gtk_widget_remove_css_class(LUMA_SIDEBAR_FOOT(user_data)->field, "focused");
}

/* Filter */

static void sync_filter(LumaSidebarFoot *self) {
  if (self->filters->len == 0)
    return;
  Filter *first = g_ptr_array_index(self->filters, 0);
  Filter *current = find_filter(self, self->filter);
  if (current == NULL)
    current = first;
  gboolean on = current != first;
  luma_ui_set_css_class(self->filter_button, "on", on);
  g_autofree char *tip = g_strdup_printf("Show: %s", current->label);
  gtk_widget_set_tooltip_text(self->filter_button, tip);
  luma_ui_set_accessible_label(self->filter_button, tip);
  luma_filter_heading_set_label(self->heading, current->label);
  gtk_widget_set_visible(GTK_WIDGET(self->heading), on);
}

static void choose_filter(LumaSidebarFoot *self, const char *key, gboolean notify) {
  gboolean changed = g_strcmp0(key, self->filter) != 0;
  if (changed) {
    g_free(self->filter);
    self->filter = g_strdup(key);
  }
  GAction *action = g_action_map_lookup_action(G_ACTION_MAP(self->actions), "filter");
  g_simple_action_set_state(G_SIMPLE_ACTION(action), g_variant_new_string(self->filter));
  sync_filter(self);
  if (notify && changed)
    g_signal_emit(self, foot_signals[FILTER_CHANGED], 0, self->filter);
}

static void filter_activated(GSimpleAction *action G_GNUC_UNUSED, GVariant *parameter, gpointer user_data) {
  LumaSidebarFoot *self = user_data;
  const char *key = g_variant_get_string(parameter, NULL);
  if (find_filter(self, key) != NULL)
    choose_filter(self, key, TRUE);
}

static void show_all(LumaFilterHeading *heading G_GNUC_UNUSED, gpointer user_data) {
  LumaSidebarFoot *self = user_data;
  if (self->filters->len > 0)
    choose_filter(self, ((Filter *)g_ptr_array_index(self->filters, 0))->key, TRUE);
}

static void filter_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  luma_sidebar_foot_open_filters(user_data);
}

static void sidebar_foot_map(GtkWidget *widget) {
  GTK_WIDGET_CLASS(luma_sidebar_foot_parent_class)->map(widget);
  if (LUMA_SIDEBAR_FOOT(widget)->filters->len == 1)
    g_critical("a filter picker offers at least two views (the first is 'everything')");
}

static void luma_sidebar_foot_dispose(GObject *object) {
  LumaSidebarFoot *self = LUMA_SIDEBAR_FOOT(object);
  g_clear_handle_id(&self->search_source, g_source_remove);
  if (self->menu != NULL && LUMA_IS_FLOATING_MENU(self->menu) &&
      luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(self->menu)))
    luma_floating_menu_close(LUMA_FLOATING_MENU(self->menu));
  g_clear_object(&self->menu);
  g_clear_object(&self->heading);
  g_clear_object(&self->actions);
  G_OBJECT_CLASS(luma_sidebar_foot_parent_class)->dispose(object);
}

static void luma_sidebar_foot_finalize(GObject *object) {
  LumaSidebarFoot *self = LUMA_SIDEBAR_FOOT(object);
  g_clear_pointer(&self->filters, g_ptr_array_unref);
  g_clear_pointer(&self->filter, g_free);
  G_OBJECT_CLASS(luma_sidebar_foot_parent_class)->finalize(object);
}

static void luma_sidebar_foot_class_init(LumaSidebarFootClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->dispose = luma_sidebar_foot_dispose;
  object_class->finalize = luma_sidebar_foot_finalize;
  GTK_WIDGET_CLASS(klass)->map = sidebar_foot_map;
  /**
   * LumaSidebarFoot::search-changed:
   * @self: the foot
   * @text: the search text, after the typing pause
   */
  foot_signals[SEARCH_CHANGED] = g_signal_new("search-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                              NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaSidebarFoot::filter-changed:
   * @self: the foot
   * @key: the view a person picked
   */
  foot_signals[FILTER_CHANGED] = g_signal_new("filter-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                              NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
}

static void luma_sidebar_foot_init(LumaSidebarFoot *self) {
  luma_ui_install();
  self->filters = g_ptr_array_new_with_free_func(filter_free);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-sidebar-foot");

  /* The field: a recessed well with the search glyph and the text. */
  self->field = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_hexpand(self->field, TRUE);
  gtk_widget_add_css_class(self->field, "lumaui-foot-search");
  GtkWidget *glyph = luma_ui_icon_image("search", 0);
  gtk_widget_add_css_class(glyph, "lumaui-foot-search-icon");
  gtk_box_append(GTK_BOX(self->field), glyph);
  self->entry = g_object_new(GTK_TYPE_TEXT, "accessible-role", GTK_ACCESSIBLE_ROLE_SEARCH_BOX, NULL); /* v70 input[type=search] */
  gtk_widget_set_hexpand(self->entry, TRUE);
  gtk_widget_set_valign(self->entry, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(self->entry, "lumaui-foot-search-text");
  g_signal_connect(self->entry, "changed", G_CALLBACK(search_changed), self);
  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(search_key), self);
  gtk_widget_add_controller(self->entry, keys);
  GtkGesture *click = gtk_gesture_click_new();
  g_signal_connect(click, "released", G_CALLBACK(field_released), self);
  gtk_widget_add_controller(self->field, GTK_EVENT_CONTROLLER(click));
  GtkEventController *focus = gtk_event_controller_focus_new();
  g_signal_connect(focus, "enter", G_CALLBACK(focus_enter), self);
  g_signal_connect(focus, "leave", G_CALLBACK(focus_leave), self);
  gtk_widget_add_controller(self->entry, focus);
  gtk_box_append(GTK_BOX(self->field), self->entry);
  self->clear_button = luma_ui_icon_button("x", "Clear search", "lumaui-foot-search-clear", FALSE, NULL);
  gtk_widget_set_valign(self->clear_button, GTK_ALIGN_CENTER);
  gtk_widget_set_visible(self->clear_button, FALSE);
  g_signal_connect(self->clear_button, "clicked", G_CALLBACK(clear_search), self);
  gtk_box_append(GTK_BOX(self->field), self->clear_button);
  gtk_box_append(GTK_BOX(self), self->field);

  self->heading = g_object_ref_sink(LUMA_FILTER_HEADING(luma_filter_heading_new(NULL)));
  gtk_widget_set_visible(GTK_WIDGET(self->heading), FALSE);
  g_signal_connect_object(self->heading, "show-all", G_CALLBACK(show_all), self, 0);

  self->actions = g_simple_action_group_new();
  GSimpleAction *action = g_simple_action_new_stateful("filter", G_VARIANT_TYPE_STRING, g_variant_new_string(""));
  g_signal_connect(action, "activate", G_CALLBACK(filter_activated), self);
  g_action_map_add_action(G_ACTION_MAP(self->actions), G_ACTION(action));
  g_object_unref(action);
  gtk_widget_insert_action_group(GTK_WIDGET(self), "lumaui-foot", G_ACTION_GROUP(self->actions));
}

GtkWidget *luma_sidebar_foot_new(const char *search) {
  g_return_val_if_fail(search != NULL, NULL);
  LumaSidebarFoot *self = g_object_new(LUMA_TYPE_SIDEBAR_FOOT, NULL);
  gtk_text_set_placeholder_text(GTK_TEXT(self->entry), search);
  luma_ui_set_accessible_label(self->entry, search);
  return GTK_WIDGET(self);
}

void luma_sidebar_foot_add_filter(LumaSidebarFoot *self, const char *key, const char *label, const char *icon) {
  g_return_if_fail(LUMA_IS_SIDEBAR_FOOT(self));
  g_return_if_fail(key != NULL && label != NULL && icon != NULL);
  if (find_filter(self, key) != NULL) {
    g_critical("filter keys are unique: '%s' is already a view", key);
    return;
  }
  Filter *filter = g_new0(Filter, 1);
  filter->key = g_strdup(key);
  filter->label = g_strdup(label);
  filter->icon = g_strdup(icon);
  filter->count = -1;
  g_ptr_array_add(self->filters, filter);
  if (self->filter_button == NULL) {
    self->filter_button = luma_ui_icon_button("list-filter", "Show", "lumaui-foot-square", FALSE, NULL);
    gtk_accessible_update_property(GTK_ACCESSIBLE(self->filter_button), GTK_ACCESSIBLE_PROPERTY_HAS_POPUP, TRUE,
                                   -1);
    g_signal_connect(self->filter_button, "clicked", G_CALLBACK(filter_clicked), self);
    gtk_box_insert_child_after(GTK_BOX(self), self->filter_button, self->field);
  }
  if (self->filter == NULL)
    choose_filter(self, key, FALSE);
  else
    sync_filter(self);
}

void luma_sidebar_foot_set_filter(LumaSidebarFoot *self, const char *key) {
  g_return_if_fail(LUMA_IS_SIDEBAR_FOOT(self));
  g_return_if_fail(key != NULL);
  if (find_filter(self, key) == NULL) {
    g_critical("unknown filter '%s'", key);
    return;
  }
  choose_filter(self, key, FALSE);
}

const char *luma_sidebar_foot_get_filter(LumaSidebarFoot *self) {
  g_return_val_if_fail(LUMA_IS_SIDEBAR_FOOT(self), NULL);
  return self->filter != NULL ? self->filter : "";
}

void luma_sidebar_foot_set_filter_count(LumaSidebarFoot *self, const char *key, int count) {
  g_return_if_fail(LUMA_IS_SIDEBAR_FOOT(self));
  g_return_if_fail(key != NULL);
  Filter *filter = find_filter(self, key);
  if (filter == NULL) {
    g_critical("unknown filter '%s'", key);
    return;
  }
  filter->count = count < 0 ? -1 : count;
}

void luma_sidebar_foot_set_add(LumaSidebarFoot *self, const char *label, const char *icon,
                               const char *action_name) {
  g_return_if_fail(LUMA_IS_SIDEBAR_FOOT(self));
  g_return_if_fail(label != NULL && icon != NULL && action_name != NULL);
  if (self->add_button != NULL)
    gtk_box_remove(GTK_BOX(self), self->add_button);
  self->add_button = luma_ui_icon_button(icon, label, "lumaui-foot-square", FALSE, NULL);
  gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(self->add_button), action_name);
  gtk_box_append(GTK_BOX(self), self->add_button);
}

void luma_sidebar_foot_open_filters(LumaSidebarFoot *self) {
  g_return_if_fail(LUMA_IS_SIDEBAR_FOOT(self));
  if (self->filter_button == NULL)
    return;
  if (self->menu != NULL && luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(self->menu)))
    luma_floating_menu_close(LUMA_FLOATING_MENU(self->menu));
  g_clear_object(&self->menu);
  /* The menu floats in the window's layer: its rows reach the foot's
   * actions through the window too. */
  GtkRoot *root = gtk_widget_get_root(GTK_WIDGET(self));
  if (root != NULL)
    gtk_widget_insert_action_group(GTK_WIDGET(root), "lumaui-foot", G_ACTION_GROUP(self->actions));
  self->menu = g_object_ref_sink(luma_floating_menu_new("Show"));
  for (guint i = 0; i < self->filters->len; i++) {
    Filter *filter = g_ptr_array_index(self->filters, i);
    g_autofree char *note = filter->count >= 0 ? luma_ui_count_text(filter->count, FALSE) : NULL;
    g_autofree char *action = g_strdup_printf("lumaui-foot.filter::%s", filter->key);
    luma_floating_menu_add_item(LUMA_FLOATING_MENU(self->menu), filter->label, filter->icon, NULL,
                                note != NULL && *note != '\0' ? note : NULL, action,
                                g_strcmp0(filter->key, self->filter) == 0);
  }
  luma_floating_menu_popup(LUMA_FLOATING_MENU(self->menu), self->filter_button);
}

const char *luma_sidebar_foot_get_text(LumaSidebarFoot *self) {
  g_return_val_if_fail(LUMA_IS_SIDEBAR_FOOT(self), NULL);
  return gtk_editable_get_text(GTK_EDITABLE(self->entry));
}

GtkWidget *luma_sidebar_foot_get_entry(LumaSidebarFoot *self) {
  g_return_val_if_fail(LUMA_IS_SIDEBAR_FOOT(self), NULL);
  return self->entry;
}

LumaFilterHeading *luma_sidebar_foot_get_heading(LumaSidebarFoot *self) {
  g_return_val_if_fail(LUMA_IS_SIDEBAR_FOOT(self), NULL);
  return self->heading;
}
