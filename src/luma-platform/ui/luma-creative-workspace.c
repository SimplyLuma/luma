/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI creative family (KB-D): LumaCreativeWorkspace and LumaFloatingPanel
 * (v70 .suwork, .supanel, .supill). */
#include "luma-creative-workspace.h"
#include "luma-creative-private.h"
#include "luma-menu-drawer.h"

#include <math.h>

/* ── Family internals ── */

void luma_creative_install(void) {
  static gboolean installed = FALSE;
  luma_ui_install();
  if (installed || gdk_display_get_default() == NULL)
    return;
  installed = TRUE;
  luma_ui_add_style_resource("/org/projectluma/platform/luma-appkit-creative.css",
                             GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
}

void luma_creative_activate_detailed(GtkWidget *widget, const char *detailed_action) {
  g_autofree char *name = NULL;
  g_autoptr(GVariant) target = NULL;
  g_autoptr(GError) error = NULL;
  if (detailed_action == NULL || *detailed_action == '\0')
    return;
  if (!g_action_parse_detailed_name(detailed_action, &name, &target, &error)) {
    g_critical("'%s' is not a detailed action name: %s", detailed_action, error->message);
    return;
  }
  luma_creative_activate_action(widget, name, target);
}

gboolean luma_creative_activate_action(GtkWidget *widget, const char *name, GVariant *target) {
  /* A subtree built before an ancestor gained its group does not see it
   * (GTK 4.22); the kit's helper asks each ancestor in turn. */
  return luma_ui_activate_action(widget, name, target);
}

static void add_menu_model(LumaFloatingMenu *menu, GMenuModel *model, gboolean *first) {
  int n = g_menu_model_get_n_items(model);
  for (int i = 0; i < n; i++) {
    g_autoptr(GMenuModel) section = g_menu_model_get_item_link(model, i, G_MENU_LINK_SECTION);
    if (section != NULL) {
      if (!*first)
        luma_floating_menu_add_separator(menu);
      add_menu_model(menu, section, first);
      continue;
    }
    g_autofree char *label = NULL, *action = NULL, *icon = NULL, *note = NULL;
    gboolean selected = FALSE;
    g_menu_model_get_item_attribute(model, i, G_MENU_ATTRIBUTE_LABEL, "s", &label);
    g_menu_model_get_item_attribute(model, i, G_MENU_ATTRIBUTE_ACTION, "s", &action);
    g_menu_model_get_item_attribute(model, i, "lumaui-icon", "s", &icon);
    g_menu_model_get_item_attribute(model, i, "lumaui-note", "s", &note);
    g_menu_model_get_item_attribute(model, i, "lumaui-selected", "b", &selected);
    g_autoptr(GVariant) target = g_menu_model_get_item_attribute_value(model, i, G_MENU_ATTRIBUTE_TARGET, NULL);
    g_autofree char *detailed = action != NULL ? g_action_print_detailed_name(action, target) : NULL;
    luma_floating_menu_add_item(menu, label != NULL ? label : "", icon, NULL, note, detailed, selected);
    *first = FALSE;
  }
}

/* Present a GMenuModel as the kit's floating menu (a drawer on a phone).
 * Items may carry "lumaui-icon" (a Lucide glyph), "lumaui-note" and
 * "lumaui-selected" attributes; sections are separated. */
static void popup_model(GtkWidget *anchor, GMenuModel *model, const char *label) {
  GtkWidget *menu = luma_floating_menu_new(label);
  gboolean first = TRUE;
  add_menu_model(LUMA_FLOATING_MENU(menu), model, &first);
  luma_floating_menu_popup(LUMA_FLOATING_MENU(menu), anchor);
  if (g_object_is_floating(menu))
    g_object_ref_sink(menu), g_object_unref(menu);
}

/* Segment wells (panel tabs, PropertyChoice segments) */

static gboolean segments_position(GtkOverlay *overlay, GtkWidget *widget, GdkRectangle *allocation,
                                  gpointer user_data) {
  GtkWidget *segments = user_data;
  if (!gtk_widget_has_css_class(widget, "lumaui-creative-tabs-indicator"))
    return FALSE;
  GtkWidget *row = luma_creative_segments_get_row(segments);
  int index = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(segments), "luma-segment-index")) - 1;
  int n = 0;
  for (GtkWidget *child = gtk_widget_get_first_child(row); child != NULL; child = gtk_widget_get_next_sibling(child))
    if (gtk_widget_get_visible(child))
      n++;
  int width = gtk_widget_get_width(GTK_WIDGET(overlay)), height = gtk_widget_get_height(GTK_WIDGET(overlay));
  if (index < 0 || index >= n || width <= 0) {
    *allocation = (GdkRectangle){0, 0, 0, 0};
    return TRUE;
  }
  int x0 = width * index / n, x1 = width * (index + 1) / n;
  *allocation = (GdkRectangle){x0, 0, x1 - x0, height};
  return TRUE;
}

GtkWidget *luma_creative_segments_new(const char *css_class) {
  GtkWidget *segments = g_object_new(GTK_TYPE_BOX, "accessible-role", GTK_ACCESSIBLE_ROLE_GROUP, NULL);
  gtk_widget_add_css_class(segments, css_class);
  GtkWidget *overlay = gtk_overlay_new();
  gtk_widget_set_hexpand(overlay, TRUE);
  GtkWidget *track = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_overlay_set_child(GTK_OVERLAY(overlay), track);
  GtkWidget *indicator = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(indicator, "lumaui-creative-tabs-indicator");
  gtk_widget_set_can_target(indicator, FALSE);
  gtk_overlay_add_overlay(GTK_OVERLAY(overlay), indicator);
  GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_box_set_homogeneous(GTK_BOX(row), TRUE);
  gtk_widget_add_css_class(row, "lumaui-creative-tabs-row");
  gtk_overlay_add_overlay(GTK_OVERLAY(overlay), row);
  gtk_overlay_set_measure_overlay(GTK_OVERLAY(overlay), row, TRUE);
  g_signal_connect(overlay, "get-child-position", G_CALLBACK(segments_position), segments);
  gtk_box_append(GTK_BOX(segments), overlay);
  g_object_set_data(G_OBJECT(segments), "luma-segment-row", row);
  luma_ui_arrow_keys(row, GTK_ORIENTATION_HORIZONTAL, TRUE);
  return segments;
}

GtkWidget *luma_creative_segments_get_row(GtkWidget *segments) {
  return g_object_get_data(G_OBJECT(segments), "luma-segment-row");
}

void luma_creative_segments_set_index(GtkWidget *segments, int index) {
  g_object_set_data(G_OBJECT(segments), "luma-segment-index", GINT_TO_POINTER(index + 1));
  gtk_widget_queue_allocate(gtk_widget_get_first_child(segments));
}

GType luma_panel_side_get_type(void) {
  static gsize type_id = 0;
  static const GEnumValue values[] = {
    {LUMA_PANEL_SIDE_LEFT, "LUMA_PANEL_SIDE_LEFT", "left"},
    {LUMA_PANEL_SIDE_RIGHT, "LUMA_PANEL_SIDE_RIGHT", "right"},
    {LUMA_PANEL_SIDE_BOTTOM, "LUMA_PANEL_SIDE_BOTTOM", "bottom"},
    {0, NULL, NULL},
  };
  if (g_once_init_enter(&type_id))
    g_once_init_leave(&type_id, g_enum_register_static(g_intern_static_string("LumaPanelSide"), values));
  return type_id;
}

/* ── LumaFloatingPanel ── */

enum { PANEL_PAGE_CHANGED, PANEL_RENAMED, PANEL_N_SIGNALS };
static guint panel_signals[PANEL_N_SIGNALS];
enum { PANEL_PROP_0, PANEL_PROP_TITLE, PANEL_PROP_FOLDED, PANEL_N_PROPS };
static GParamSpec *panel_props[PANEL_N_PROPS];

typedef struct {
  char *key;
  GtkWidget *tab;
} Page;

struct _LumaFloatingPanel {
  GtkBox parent_instance;
  LumaPanelSide side;
  char *title;
  char *icon;
  char *summary;
  char *holds;
  GMenuModel *title_menu;
  GMenuModel *more_menu;
  gboolean editable;
  gboolean closable;
  gboolean folded;
  gboolean drawer;
  gboolean selecting;
  gboolean movable;
  /* The workspace a docked panel belongs to (not a reference). */
  gpointer dock;
  GtkEventController *drag;
  GtkWidget *card;
  GtkWidget *handle;
  GtkWidget *header;
  GtkWidget *title_widget;
  GtkWidget *header_actions;
  GtkWidget *tabs;
  GtkWidget *scroll;
  GtkWidget *stack;
  GtkWidget *pill;
  GtkWidget *pill_icon;
  GtkWidget *pill_title;
  GtkWidget *pill_summary;
  GPtrArray *pages; /* Page */
};

G_DEFINE_FINAL_TYPE(LumaFloatingPanel, luma_floating_panel, GTK_TYPE_BOX)

static void page_free(gpointer data) {
  Page *page = data;
  g_free(page->key);
  g_free(page);
}

static Page *find_page(LumaFloatingPanel *self, const char *key) {
  for (guint i = 0; i < self->pages->len; i++) {
    Page *page = g_ptr_array_index(self->pages, i);
    if (g_strcmp0(page->key, key) == 0)
      return page;
  }
  return NULL;
}

static void title_menu_clicked(GtkButton *button, gpointer user_data) {
  LumaFloatingPanel *self = user_data;
  if (self->title_menu != NULL)
    popup_model(GTK_WIDGET(button), self->title_menu, self->title);
}

static void dock_menu_append(LumaFloatingPanel *self, GMenu *menu);

static void more_clicked(GtkButton *button, gpointer user_data) {
  LumaFloatingPanel *self = user_data;
  if (!self->movable || self->drawer) {
    if (self->more_menu != NULL)
      popup_model(GTK_WIDGET(button), self->more_menu, "More");
    return;
  }
  /* A docked panel's More ends with where it can go: the keyboard's way to
   * move what a pointer drags by the header. */
  g_autoptr(GMenu) menu = g_menu_new();
  if (self->more_menu != NULL)
    g_menu_append_section(menu, NULL, self->more_menu);
  dock_menu_append(self, menu);
  popup_model(GTK_WIDGET(button), G_MENU_MODEL(menu), "More");
}

static void close_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  luma_floating_panel_set_folded(LUMA_FLOATING_PANEL(user_data), TRUE);
}

static void commit_name(LumaFloatingPanel *self) {
  if (!GTK_IS_ENTRY(self->title_widget))
    return;
  const char *text = gtk_editable_get_text(GTK_EDITABLE(self->title_widget));
  g_autofree char *name = g_strstrip(g_strdup(text));
  if (*name == '\0' || g_strcmp0(name, self->title) == 0) {
    gtk_editable_set_text(GTK_EDITABLE(self->title_widget), self->title);
    return;
  }
  g_free(self->title);
  self->title = g_strdup(name);
  gtk_editable_set_text(GTK_EDITABLE(self->title_widget), self->title);
  gtk_label_set_text(GTK_LABEL(self->pill_title), self->title);
  g_object_notify_by_pspec(G_OBJECT(self), panel_props[PANEL_PROP_TITLE]);
  g_signal_emit(self, panel_signals[PANEL_RENAMED], 0, self->title);
}

static void name_activated(GtkEntry *entry G_GNUC_UNUSED, gpointer user_data) {
  commit_name(LUMA_FLOATING_PANEL(user_data));
}

static void name_left(GtkEventControllerFocus *focus G_GNUC_UNUSED, gpointer user_data) {
  commit_name(LUMA_FLOATING_PANEL(user_data));
}

static gboolean name_key(GtkEventControllerKey *keys G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                         GdkModifierType state G_GNUC_UNUSED, gpointer user_data) {
  LumaFloatingPanel *self = user_data;
  if (keyval != GDK_KEY_Escape)
    return FALSE;
  gtk_editable_set_text(GTK_EDITABLE(self->title_widget), self->title);
  return TRUE;
}

static void rebuild_header(LumaFloatingPanel *self) {
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(self->header)) != NULL)
    gtk_box_remove(GTK_BOX(self->header), child);
  gboolean menu = self->title_menu != NULL;
  luma_ui_set_css_class(self->header, "menu", menu);
  if (!menu) {
    GtkWidget *icon = luma_ui_icon_image(self->icon, 0);
    gtk_widget_add_css_class(icon, "lumaui-creative-panel-icon");
    gtk_box_append(GTK_BOX(self->header), icon);
  }
  if (menu) {
    GtkWidget *button = gtk_button_new();
    GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    GtkWidget *label = gtk_label_new(self->title);
    gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
    gtk_box_append(GTK_BOX(line), label);
    gtk_box_append(GTK_BOX(line), luma_ui_icon_image("chevron-down", 0));
    gtk_button_set_child(GTK_BUTTON(button), line);
    gtk_widget_add_css_class(button, "lumaui-creative-panel-title");
    gtk_widget_set_halign(button, GTK_ALIGN_START);
    gtk_widget_set_hexpand(button, TRUE);
    gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_HAS_POPUP, TRUE, -1);
    g_autofree char *spoken = g_strdup_printf("%s, choose", self->title);
    luma_ui_set_accessible_label(button, spoken);
    g_signal_connect(button, "clicked", G_CALLBACK(title_menu_clicked), self);
    self->title_widget = button;
  } else if (self->editable) {
    GtkWidget *entry = gtk_entry_new();
    gtk_editable_set_text(GTK_EDITABLE(entry), self->title);
    gtk_widget_add_css_class(entry, "lumaui-creative-panel-title");
    gtk_widget_set_hexpand(entry, TRUE);
    gtk_editable_set_width_chars(GTK_EDITABLE(entry), 4);
    luma_ui_set_accessible_label(entry, "Name");
    g_signal_connect(entry, "activate", G_CALLBACK(name_activated), self);
    GtkEventController *focus = gtk_event_controller_focus_new();
    g_signal_connect(focus, "leave", G_CALLBACK(name_left), self);
    gtk_widget_add_controller(entry, focus);
    GtkEventController *keys = gtk_event_controller_key_new();
    g_signal_connect(keys, "key-pressed", G_CALLBACK(name_key), self);
    gtk_widget_add_controller(entry, keys);
    self->title_widget = entry;
  } else {
    GtkWidget *label = gtk_label_new(self->title);
    gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
    gtk_label_set_xalign(GTK_LABEL(label), 0);
    gtk_widget_set_hexpand(label, TRUE);
    gtk_widget_add_css_class(label, "lumaui-creative-panel-title");
    self->title_widget = label;
  }
  gtk_box_append(GTK_BOX(self->header), self->title_widget);
  if (self->header_actions != NULL)
    gtk_box_append(GTK_BOX(self->header), self->header_actions);
  if (self->more_menu != NULL || (self->movable && !self->drawer)) {
    GtkWidget *more = luma_ui_icon_button("ellipsis", "More", "lumaui-creative-panel-button", FALSE, NULL);
    gtk_widget_add_css_class(more, "more");
    gtk_accessible_update_property(GTK_ACCESSIBLE(more), GTK_ACCESSIBLE_PROPERTY_HAS_POPUP, TRUE, -1);
    g_signal_connect(more, "clicked", G_CALLBACK(more_clicked), self);
    gtk_box_append(GTK_BOX(self->header), more);
  }
  if (self->closable || self->drawer) {
    const char *glyph = self->drawer                         ? "x"
                        : self->side == LUMA_PANEL_SIDE_LEFT   ? "panel-left"
                        : self->side == LUMA_PANEL_SIDE_BOTTOM ? "panel-bottom"
                                                               : "panel-right";
    g_autofree char *hide = NULL;
    if (!self->drawer) {
      const char *what = self->holds;
      g_autofree char *derived = NULL;
      if (what == NULL) {
        GtkWidget *first = self->pages->len > 0 ? ((Page *)g_ptr_array_index(self->pages, 0))->tab : NULL;
        derived = g_utf8_strdown(first != NULL && self->pages->len > 1 ? gtk_button_get_label(GTK_BUTTON(first))
                                                                        : self->title, -1);
        what = derived;
      }
      hide = g_strdup_printf("Hide %s", what);
    }
    GtkWidget *close = luma_ui_icon_button(glyph, self->drawer ? "Close" : hide, "lumaui-creative-panel-button",
                                           FALSE, NULL);
    gtk_widget_add_css_class(close, "close");
    g_signal_connect(close, "clicked", G_CALLBACK(close_clicked), self);
    gtk_box_append(GTK_BOX(self->header), close);
  }
}

static void update_pill(LumaFloatingPanel *self) {
  g_autofree char *name = luma_ui_icon_name(self->icon);
  gtk_image_set_from_icon_name(GTK_IMAGE(self->pill_icon), name);
  gtk_label_set_text(GTK_LABEL(self->pill_title), self->title);
  gboolean has_summary = self->summary != NULL && *self->summary != '\0';
  gtk_label_set_text(GTK_LABEL(self->pill_summary), has_summary ? self->summary : "");
  gtk_widget_set_visible(self->pill_summary, has_summary);
  g_autofree char *spoken = has_summary ? g_strdup_printf("%s, %s", self->title, self->summary)
                                        : g_strdup(self->title);
  luma_ui_set_accessible_label(self->pill, spoken);
}

static void tab_toggled(GtkToggleButton *button, gpointer user_data) {
  LumaFloatingPanel *self = user_data;
  if (self->selecting || !gtk_toggle_button_get_active(button))
    return;
  const char *key = g_object_get_data(G_OBJECT(button), "luma-page-key");
  if (g_strcmp0(key, luma_floating_panel_get_page(self)) == 0)
    return;
  luma_floating_panel_set_page(self, key);
  g_signal_emit(self, panel_signals[PANEL_PAGE_CHANGED], 0, key);
}

static void pill_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  luma_floating_panel_set_folded(LUMA_FLOATING_PANEL(user_data), FALSE);
}

static gboolean panel_key(GtkEventControllerKey *keys G_GNUC_UNUSED, guint keyval, guint code G_GNUC_UNUSED,
                          GdkModifierType state G_GNUC_UNUSED, gpointer user_data) {
  LumaFloatingPanel *self = user_data;
  if (keyval == GDK_KEY_Escape && self->drawer && !self->folded) {
    luma_floating_panel_set_folded(self, TRUE);
    return TRUE;
  }
  return FALSE;
}

static void panel_set_side(LumaFloatingPanel *self, LumaPanelSide side) {
  self->side = side;
  luma_ui_set_css_class(GTK_WIDGET(self), "left", side == LUMA_PANEL_SIDE_LEFT);
  luma_ui_set_css_class(GTK_WIDGET(self), "right", side == LUMA_PANEL_SIDE_RIGHT);
  luma_ui_set_css_class(GTK_WIDGET(self), "bottom", side == LUMA_PANEL_SIDE_BOTTOM);
  /* v70 names the tab groups "Panel" and "Inspector". */
  luma_ui_set_accessible_label(self->tabs, side == LUMA_PANEL_SIDE_RIGHT ? "Inspector" : "Panel");
  rebuild_header(self);
}

static void panel_set_drawer(LumaFloatingPanel *self, gboolean drawer);

/* Docked panels move: the header is a drag source carrying the panel, and
 * More lists the docks it can move to. */
static GdkContentProvider *header_drag_prepare(GtkDragSource *source G_GNUC_UNUSED, double x G_GNUC_UNUSED,
                                               double y G_GNUC_UNUSED, gpointer user_data) {
  LumaFloatingPanel *self = user_data;
  if (!self->movable || self->drawer || self->dock == NULL)
    return NULL;
  return gdk_content_provider_new_typed(LUMA_TYPE_FLOATING_PANEL, self);
}

static void header_drag_begin(GtkDragSource *source, GdkDrag *drag G_GNUC_UNUSED, gpointer user_data) {
  LumaFloatingPanel *self = user_data;
  g_autoptr(GdkPaintable) face = gtk_widget_paintable_new(self->card);
  gtk_drag_source_set_icon(source, face, 24, 18);
  gtk_widget_add_css_class(GTK_WIDGET(self), "moving");
}

static void header_drag_end(GtkDragSource *source G_GNUC_UNUSED, GdkDrag *drag G_GNUC_UNUSED,
                            gboolean deleted G_GNUC_UNUSED, gpointer user_data) {
  gtk_widget_remove_css_class(GTK_WIDGET(user_data), "moving");
}

static void dock_activate(GSimpleAction *action, GVariant *parameter, gpointer user_data);

static void panel_set_movable(LumaFloatingPanel *self, gpointer dock) {
  gboolean movable = dock != NULL;
  self->dock = dock;
  if (self->movable == movable)
    return;
  self->movable = movable;
  if (movable && self->drag == NULL) {
    GtkDragSource *source = gtk_drag_source_new();
    gtk_drag_source_set_actions(source, GDK_ACTION_MOVE);
    g_signal_connect(source, "prepare", G_CALLBACK(header_drag_prepare), self);
    g_signal_connect(source, "drag-begin", G_CALLBACK(header_drag_begin), self);
    g_signal_connect(source, "drag-end", G_CALLBACK(header_drag_end), self);
    self->drag = GTK_EVENT_CONTROLLER(source);
    gtk_widget_add_controller(self->header, self->drag);
    GSimpleActionGroup *group = g_simple_action_group_new();
    GSimpleAction *move = g_simple_action_new("move", G_VARIANT_TYPE_STRING);
    g_signal_connect_object(move, "activate", G_CALLBACK(dock_activate), self, 0);
    g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(move));
    g_object_unref(move);
    gtk_widget_insert_action_group(GTK_WIDGET(self), "creative-dock", G_ACTION_GROUP(group));
    g_object_unref(group);
  }
  if (!movable)
    gtk_widget_insert_action_group(GTK_WIDGET(self), "creative-dock", NULL);
  if (!movable && self->drag != NULL) {
    gtk_widget_remove_controller(self->header, self->drag);
    self->drag = NULL;
  }
  rebuild_header(self);
}

static void panel_set_drawer(LumaFloatingPanel *self, gboolean drawer) {
  if (self->drawer == drawer)
    return;
  self->drawer = drawer;
  luma_ui_set_css_class(GTK_WIDGET(self), "drawer", drawer);
  gtk_widget_set_visible(self->handle, drawer);
  rebuild_header(self);
}

static void luma_floating_panel_get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  LumaFloatingPanel *self = LUMA_FLOATING_PANEL(object);
  switch (id) {
  case PANEL_PROP_TITLE: g_value_set_string(value, self->title); break;
  case PANEL_PROP_FOLDED: g_value_set_boolean(value, self->folded); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void luma_floating_panel_set_property(GObject *object, guint id, const GValue *value, GParamSpec *pspec) {
  LumaFloatingPanel *self = LUMA_FLOATING_PANEL(object);
  switch (id) {
  case PANEL_PROP_TITLE: luma_floating_panel_set_title(self, g_value_get_string(value)); break;
  case PANEL_PROP_FOLDED: luma_floating_panel_set_folded(self, g_value_get_boolean(value)); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void luma_floating_panel_dispose(GObject *object) {
  LumaFloatingPanel *self = LUMA_FLOATING_PANEL(object);
  g_clear_object(&self->header_actions);
  G_OBJECT_CLASS(luma_floating_panel_parent_class)->dispose(object);
}

static void luma_floating_panel_finalize(GObject *object) {
  LumaFloatingPanel *self = LUMA_FLOATING_PANEL(object);
  g_clear_pointer(&self->title, g_free);
  g_clear_pointer(&self->icon, g_free);
  g_clear_pointer(&self->summary, g_free);
  g_clear_pointer(&self->holds, g_free);
  g_clear_object(&self->title_menu);
  g_clear_object(&self->more_menu);
  g_clear_pointer(&self->pages, g_ptr_array_unref);
  G_OBJECT_CLASS(luma_floating_panel_parent_class)->finalize(object);
}

static void luma_floating_panel_class_init(LumaFloatingPanelClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->finalize = luma_floating_panel_finalize;
  object_class->dispose = luma_floating_panel_dispose;
  object_class->get_property = luma_floating_panel_get_property;
  object_class->set_property = luma_floating_panel_set_property;
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_GROUP);
  panel_props[PANEL_PROP_TITLE] = g_param_spec_string("title", NULL, NULL, NULL,
                                                      G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  panel_props[PANEL_PROP_FOLDED] = g_param_spec_boolean("folded", NULL, NULL, FALSE,
                                                        G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, PANEL_N_PROPS, panel_props);
  /**
   * LumaFloatingPanel::page-changed:
   * @self: the panel
   * @key: the page a person chose from the tabs
   */
  panel_signals[PANEL_PAGE_CHANGED] = g_signal_new("page-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                                   NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaFloatingPanel::renamed:
   * @self: the panel
   * @name: the subject's new name, typed in the header
   */
  panel_signals[PANEL_RENAMED] = g_signal_new("renamed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL,
                                              NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
}

static void luma_floating_panel_init(LumaFloatingPanel *self) {
  luma_creative_install();
  self->pages = g_ptr_array_new_with_free_func(page_free);
  self->title = g_strdup("");
  self->icon = g_strdup("layers");
  self->closable = TRUE;
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-creative-panel");

  self->card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(self->card, "lumaui-creative-panel-card");
  gtk_widget_set_vexpand(self->card, TRUE);
  self->handle = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->handle, "lumaui-creative-handle");
  gtk_widget_set_halign(self->handle, GTK_ALIGN_CENTER);
  gtk_widget_set_visible(self->handle, FALSE);
  gtk_box_append(GTK_BOX(self->card), self->handle);
  self->header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->header, "lumaui-creative-panel-header");
  gtk_box_append(GTK_BOX(self->card), self->header);
  self->tabs = luma_creative_segments_new("lumaui-creative-tabs");
  gtk_widget_set_visible(self->tabs, FALSE);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self->tabs), GTK_ACCESSIBLE_PROPERTY_LABEL, "Panel", -1);
  gtk_box_append(GTK_BOX(self->card), self->tabs);
  self->scroll = gtk_scrolled_window_new();
  gtk_widget_add_css_class(self->scroll, "lumaui-creative-panel-scroll");
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(self->scroll), GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
  gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(self->scroll), TRUE);
  gtk_scrolled_window_set_overlay_scrolling(GTK_SCROLLED_WINDOW(self->scroll), TRUE);
  gtk_widget_set_vexpand(self->scroll, TRUE);
  self->stack = gtk_stack_new();
  gtk_stack_set_vhomogeneous(GTK_STACK(self->stack), FALSE);
  gtk_stack_set_interpolate_size(GTK_STACK(self->stack), FALSE);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(self->scroll), self->stack);
  gtk_box_append(GTK_BOX(self->card), self->scroll);
  gtk_box_append(GTK_BOX(self), self->card);

  self->pill = gtk_button_new();
  gtk_widget_add_css_class(self->pill, "lumaui-creative-pill");
  gtk_widget_set_halign(self->pill, GTK_ALIGN_START);
  gtk_widget_set_valign(self->pill, GTK_ALIGN_START);
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  self->pill_icon = luma_ui_icon_image(self->icon, 0);
  self->pill_title = gtk_label_new(NULL);
  gtk_widget_add_css_class(self->pill_title, "lumaui-creative-pill-title");
  self->pill_summary = gtk_label_new(NULL);
  gtk_widget_add_css_class(self->pill_summary, "lumaui-creative-pill-summary");
  gtk_box_append(GTK_BOX(line), self->pill_icon);
  gtk_box_append(GTK_BOX(line), self->pill_title);
  gtk_box_append(GTK_BOX(line), self->pill_summary);
  gtk_button_set_child(GTK_BUTTON(self->pill), line);
  gtk_widget_set_visible(self->pill, FALSE);
  g_signal_connect(self->pill, "clicked", G_CALLBACK(pill_clicked), self);
  gtk_box_append(GTK_BOX(self), self->pill);

  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(panel_key), self);
  gtk_widget_add_controller(GTK_WIDGET(self), keys);
  panel_set_side(self, LUMA_PANEL_SIDE_LEFT);
  update_pill(self);
}

GtkWidget *luma_floating_panel_new(const char *title, const char *icon) {
  g_return_val_if_fail(title != NULL, NULL);
  g_return_val_if_fail(icon != NULL && *icon != '\0', NULL);
  LumaFloatingPanel *self = g_object_new(LUMA_TYPE_FLOATING_PANEL, NULL);
  g_free(self->icon);
  self->icon = g_strdup(icon);
  luma_floating_panel_set_title(self, title);
  return GTK_WIDGET(self);
}

void luma_floating_panel_set_header_actions(LumaFloatingPanel *self, GtkWidget *actions) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_return_if_fail(actions == NULL || GTK_IS_WIDGET(actions));
  if (actions == self->header_actions)
    return;
  g_return_if_fail(actions == NULL || gtk_widget_get_parent(actions) == NULL);
  if (self->header_actions != NULL && gtk_widget_get_parent(self->header_actions) == self->header)
    gtk_box_remove(GTK_BOX(self->header), self->header_actions);
  g_clear_object(&self->header_actions);
  self->header_actions = actions != NULL ? g_object_ref_sink(actions) : NULL;
  rebuild_header(self);
}

void luma_floating_panel_set_title(LumaFloatingPanel *self, const char *title) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  if (g_strcmp0(self->title, title) == 0)
    return;
  g_free(self->title);
  self->title = g_strdup(title != NULL ? title : "");
  luma_ui_set_accessible_label(GTK_WIDGET(self), self->title);
  rebuild_header(self);
  update_pill(self);
  g_object_notify_by_pspec(G_OBJECT(self), panel_props[PANEL_PROP_TITLE]);
}

const char *luma_floating_panel_get_title(LumaFloatingPanel *self) {
  g_return_val_if_fail(LUMA_IS_FLOATING_PANEL(self), NULL);
  return self->title;
}

void luma_floating_panel_set_icon(LumaFloatingPanel *self, const char *icon) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_return_if_fail(icon != NULL && *icon != '\0');
  g_free(self->icon);
  self->icon = g_strdup(icon);
  rebuild_header(self);
  update_pill(self);
}

void luma_floating_panel_set_summary(LumaFloatingPanel *self, const char *summary) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_free(self->summary);
  self->summary = g_strdup(summary);
  update_pill(self);
}

void luma_floating_panel_set_holds(LumaFloatingPanel *self, const char *holds) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_free(self->holds);
  self->holds = g_strdup(holds);
  rebuild_header(self);
}

void luma_floating_panel_set_title_menu(LumaFloatingPanel *self, GMenuModel *menu) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_return_if_fail(menu == NULL || G_IS_MENU_MODEL(menu));
  g_set_object(&self->title_menu, menu);
  rebuild_header(self);
}

void luma_floating_panel_set_title_editable(LumaFloatingPanel *self, gboolean editable) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  self->editable = editable;
  rebuild_header(self);
}

void luma_floating_panel_set_more_menu(LumaFloatingPanel *self, GMenuModel *menu) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_return_if_fail(menu == NULL || G_IS_MENU_MODEL(menu));
  g_set_object(&self->more_menu, menu);
  rebuild_header(self);
}

void luma_floating_panel_set_closable(LumaFloatingPanel *self, gboolean closable) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  self->closable = closable;
  rebuild_header(self);
}

void luma_floating_panel_add_page(LumaFloatingPanel *self, const char *key, const char *label, GtkWidget *child) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_return_if_fail(key != NULL && label != NULL);
  g_return_if_fail(GTK_IS_WIDGET(child));
  if (find_page(self, key) != NULL) {
    g_critical("panel page keys are unique: '%s' is already a page", key);
    return;
  }
  Page *page = g_new0(Page, 1);
  page->key = g_strdup(key);
  /* v70's tabs are toggle buttons in a named group (a segment well). */
  page->tab = gtk_toggle_button_new_with_label(label);
  gtk_widget_add_css_class(page->tab, "lumaui-creative-tab");
  g_object_set_data_full(G_OBJECT(page->tab), "luma-page-key", g_strdup(key), g_free);
  if (self->pages->len > 0) {
    Page *first = g_ptr_array_index(self->pages, 0);
    gtk_toggle_button_set_group(GTK_TOGGLE_BUTTON(page->tab), GTK_TOGGLE_BUTTON(first->tab));
  }
  g_ptr_array_add(self->pages, page);
  gtk_box_append(GTK_BOX(luma_creative_segments_get_row(self->tabs)), page->tab);
  gtk_stack_add_named(GTK_STACK(self->stack), child, key);
  g_signal_connect(page->tab, "toggled", G_CALLBACK(tab_toggled), self);
  gtk_widget_set_visible(self->tabs, self->pages->len >= 2);
  if (self->pages->len <= 2 && self->holds == NULL)
    rebuild_header(self);
  if (self->pages->len == 1)
    luma_floating_panel_set_page(self, key);
  else
    luma_floating_panel_set_page(self, luma_floating_panel_get_page(self));
}

void luma_floating_panel_set_child(LumaFloatingPanel *self, GtkWidget *child) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_return_if_fail(child == NULL || GTK_IS_WIDGET(child));
  GtkWidget *old;
  GtkWidget *row = luma_creative_segments_get_row(self->tabs);
  while ((old = gtk_widget_get_first_child(row)) != NULL)
    gtk_box_remove(GTK_BOX(row), old);
  while ((old = gtk_widget_get_first_child(self->stack)) != NULL)
    gtk_stack_remove(GTK_STACK(self->stack), old);
  g_ptr_array_set_size(self->pages, 0);
  gtk_widget_set_visible(self->tabs, FALSE);
  if (child != NULL)
    luma_floating_panel_add_page(self, "main", "Main", child);
}

void luma_floating_panel_set_page(LumaFloatingPanel *self, const char *key) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  g_return_if_fail(key != NULL);
  if (find_page(self, key) == NULL) {
    g_critical("unknown panel page '%s'", key);
    return;
  }
  gtk_stack_set_visible_child_name(GTK_STACK(self->stack), key);
  self->selecting = TRUE;
  for (guint i = 0; i < self->pages->len; i++) {
    Page *page = g_ptr_array_index(self->pages, i);
    gboolean on = g_strcmp0(page->key, key) == 0;
    gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(page->tab), on);
    luma_ui_set_css_class(page->tab, "on", on);
    if (on)
      luma_creative_segments_set_index(self->tabs, (int)i);
  }
  self->selecting = FALSE;
}

const char *luma_floating_panel_get_page(LumaFloatingPanel *self) {
  g_return_val_if_fail(LUMA_IS_FLOATING_PANEL(self), NULL);
  return gtk_stack_get_visible_child_name(GTK_STACK(self->stack));
}

void luma_floating_panel_set_folded(LumaFloatingPanel *self, gboolean folded) {
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(self));
  folded = !!folded;
  if (self->folded == folded)
    return;
  /* Focus follows: into the pill when folding, into the panel when opening. */
  gboolean had_focus = gtk_widget_has_focus(GTK_WIDGET(self)) ||
                       (gtk_widget_get_root(GTK_WIDGET(self)) != NULL &&
                        gtk_root_get_focus(gtk_widget_get_root(GTK_WIDGET(self))) != NULL &&
                        gtk_widget_is_ancestor(gtk_root_get_focus(gtk_widget_get_root(GTK_WIDGET(self))), GTK_WIDGET(self)));
  self->folded = folded;
  luma_ui_set_css_class(GTK_WIDGET(self), "folded", folded);
  gtk_widget_set_visible(self->card, !folded);
  gtk_widget_set_visible(self->pill, folded);
  if (had_focus)
    gtk_widget_grab_focus(folded ? self->pill : self->title_widget);
  g_object_notify_by_pspec(G_OBJECT(self), panel_props[PANEL_PROP_FOLDED]);
}

gboolean luma_floating_panel_get_folded(LumaFloatingPanel *self) {
  g_return_val_if_fail(LUMA_IS_FLOATING_PANEL(self), FALSE);
  return self->folded;
}

LumaPanelSide luma_floating_panel_get_side(LumaFloatingPanel *self) {
  g_return_val_if_fail(LUMA_IS_FLOATING_PANEL(self), LUMA_PANEL_SIDE_LEFT);
  return self->side;
}

gboolean luma_floating_panel_get_is_drawer(LumaFloatingPanel *self) {
  g_return_val_if_fail(LUMA_IS_FLOATING_PANEL(self), FALSE);
  return self->drawer;
}

/* ── LumaCreativeWorkspace ── */

enum { WS_PROP_0, WS_PROP_FOCUSED, WS_N_PROPS };
static GParamSpec *ws_props[WS_N_PROPS];
enum { WS_LAYOUT_CHANGED, WS_WORK_AREA_CHANGED, WS_N_SIGNALS };
static guint ws_signals[WS_N_SIGNALS];

/* A docked panel: its stable key, where it is, and how it was before a
 * phone folded it. The array's order is the order within each dock. */
typedef struct {
  char *key;
  LumaFloatingPanel *panel;
  LumaPanelSide side;
  gboolean was_folded;
} Docked;

struct _LumaCreativeWorkspace {
  GtkWidget parent_instance;
  GtkWidget *grid;
  GtkWidget *content;
  GtkWidget *zoom;
  GtkWidget *tools;
  GtkWidget *target;
  GPtrArray *docked;
  gboolean focused;
  gboolean phone;
  gboolean grid_visible;
  gboolean applying;
  GdkRectangle work;
  guint work_idle;
  LumaPanelSide drop_side;
  int drop_index;
  double cam_zoom;
  double cam_x;
  double cam_y;
};

G_DEFINE_FINAL_TYPE(LumaCreativeWorkspace, luma_creative_workspace, GTK_TYPE_WIDGET)

static void docked_free(gpointer data) {
  Docked *docked = data;
  g_free(docked->key);
  g_free(docked);
}

static Docked *find_docked(LumaCreativeWorkspace *self, const char *key, guint *at) {
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *docked = g_ptr_array_index(self->docked, i);
    if (g_strcmp0(docked->key, key) == 0) {
      if (at != NULL)
        *at = i;
      return docked;
    }
  }
  return NULL;
}

static Docked *docked_for_panel(LumaCreativeWorkspace *self, LumaFloatingPanel *panel) {
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *docked = g_ptr_array_index(self->docked, i);
    if (docked->panel == panel)
      return docked;
  }
  return NULL;
}

static LumaFloatingPanel *first_on(LumaCreativeWorkspace *self, LumaPanelSide side) {
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *docked = g_ptr_array_index(self->docked, i);
    if (docked->side == side)
      return docked->panel;
  }
  return NULL;
}

double luma_creative_grid_spacing(double zoom) {
  return MAX((double)LUMA_UI_CREATIVE_GRID_MIN_SPACING, LUMA_UI_CREATIVE_GRID_SPACING * zoom);
}

/* Children in painting order: the grid's node, the work, the panels, the
 * drop target, then the corner controls on top. */
static void restack(LumaCreativeWorkspace *self) {
  if (self->grid != NULL)
    gtk_widget_insert_before(self->grid, GTK_WIDGET(self), NULL);
  if (self->content != NULL)
    gtk_widget_insert_before(self->content, GTK_WIDGET(self), NULL);
  for (guint i = 0; i < self->docked->len; i++)
    gtk_widget_insert_before(GTK_WIDGET(((Docked *)g_ptr_array_index(self->docked, i))->panel), GTK_WIDGET(self), NULL);
  GtkWidget *order[] = {self->target, self->zoom, self->tools};
  for (guint i = 0; i < G_N_ELEMENTS(order); i++)
    if (order[i] != NULL)
      gtk_widget_insert_before(order[i], GTK_WIDGET(self), NULL);
}

/* Gone (focus mode): slide and fade away, then leave the focus chain. */
static gboolean gone_done(gpointer user_data) {
  GtkWidget *widget = user_data;
  g_object_set_data(G_OBJECT(widget), "luma-creative-gone-timeout", NULL);
  gtk_widget_set_child_visible(widget, FALSE);
  return G_SOURCE_REMOVE;
}

static gboolean gone_back(gpointer user_data) {
  gtk_widget_remove_css_class(GTK_WIDGET(user_data), "gone");
  return G_SOURCE_REMOVE;
}

static void set_gone(GtkWidget *widget, gboolean gone) {
  if (widget == NULL)
    return;
  guint pending = GPOINTER_TO_UINT(g_object_get_data(G_OBJECT(widget), "luma-creative-gone-timeout"));
  if (pending != 0) {
    g_source_remove(pending);
    g_object_set_data(G_OBJECT(widget), "luma-creative-gone-timeout", NULL);
  }
  gtk_widget_set_can_target(widget, !gone);
  if (gone) {
    gtk_widget_add_css_class(widget, "gone");
    guint ms = luma_ui_duration(LUMA_UI_MOTION_CREATIVE_PANEL_MOVE_MS, FALSE);
    if (ms == 0 || !gtk_widget_get_mapped(widget)) {
      gtk_widget_set_child_visible(widget, FALSE);
      return;
    }
    guint id = g_timeout_add_full(G_PRIORITY_DEFAULT, ms, gone_done, g_object_ref(widget), g_object_unref);
    g_object_set_data(G_OBJECT(widget), "luma-creative-gone-timeout", GUINT_TO_POINTER(id));
  } else {
    gboolean was_hidden = !gtk_widget_get_child_visible(widget);
    gtk_widget_set_child_visible(widget, TRUE);
    if (was_hidden && gtk_widget_get_mapped(widget) && luma_ui_duration(LUMA_UI_MOTION_CREATIVE_PANEL_MOVE_MS, FALSE) > 0)
      luma_ui_on_next_frame(widget, gone_back, widget);
    else
      gtk_widget_remove_css_class(widget, "gone");
  }
}

static void update_drawers(LumaCreativeWorkspace *self) {
  for (guint i = 0; i < self->docked->len; i++) {
    LumaFloatingPanel *panel = ((Docked *)g_ptr_array_index(self->docked, i))->panel;
    panel_set_drawer(panel, self->phone && !luma_floating_panel_get_folded(panel));
  }
  gtk_widget_queue_allocate(GTK_WIDGET(self));
}

static void layout_changed(LumaCreativeWorkspace *self) {
  if (!self->applying && !self->phone)
    g_signal_emit(self, ws_signals[WS_LAYOUT_CHANGED], 0);
}

static void panel_folded_changed(LumaFloatingPanel *panel, GParamSpec *pspec G_GNUC_UNUSED, gpointer user_data) {
  LumaCreativeWorkspace *self = user_data;
  /* A phone shows one drawer at a time. */
  if (self->phone && !luma_floating_panel_get_folded(panel)) {
    for (guint i = 0; i < self->docked->len; i++) {
      LumaFloatingPanel *other = ((Docked *)g_ptr_array_index(self->docked, i))->panel;
      if (other != panel)
        luma_floating_panel_set_folded(other, TRUE);
    }
  }
  update_drawers(self);
  layout_changed(self);
}

static void update_visibility(LumaCreativeWorkspace *self) {
  for (guint i = 0; i < self->docked->len; i++)
    set_gone(GTK_WIDGET(((Docked *)g_ptr_array_index(self->docked, i))->panel), self->focused);
  set_gone(self->zoom, self->focused || self->phone);
}

static void set_phone(LumaCreativeWorkspace *self, gboolean phone) {
  if (self->phone == phone)
    return;
  self->phone = phone;
  luma_ui_set_css_class(GTK_WIDGET(self), "phone", phone);
  /* On a phone the panels start as pills; leaving it restores how they were. */
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *docked = g_ptr_array_index(self->docked, i);
    if (phone) {
      docked->was_folded = luma_floating_panel_get_folded(docked->panel);
      luma_floating_panel_set_folded(docked->panel, TRUE);
    } else {
      luma_floating_panel_set_folded(docked->panel, docked->was_folded);
    }
  }
  update_drawers(self);
  update_visibility(self);
}

static void width_changed(GtkWidget *widget, int width, gpointer data G_GNUC_UNUSED) {
  set_phone(LUMA_CREATIVE_WORKSPACE(widget), width > 0 && width <= (LUMA_TIER_PHONE_BELOW - 1));
}

static gboolean phone_check(gpointer user_data) {
  LumaCreativeWorkspace *self = user_data;
  g_object_set_data(G_OBJECT(self), "luma-phone-check", NULL);
  set_phone(self, luma_ui_is_phone(GTK_WIDGET(self)));
  return G_SOURCE_REMOVE;
}

static void measure_child(GtkWidget *child, GtkOrientation orientation, int for_size, int *minimum, int *natural) {
  *minimum = *natural = 0;
  if (child != NULL && gtk_widget_get_visible(child))
    gtk_widget_measure(child, orientation, for_size, minimum, natural, NULL, NULL);
}

static void luma_creative_workspace_measure(GtkWidget *widget, GtkOrientation orientation, int for_size,
                                            int *minimum, int *natural, int *minimum_baseline G_GNUC_UNUSED,
                                            int *natural_baseline G_GNUC_UNUSED) {
  LumaCreativeWorkspace *self = LUMA_CREATIVE_WORKSPACE(widget);
  int content_min, content_nat, tools_min, tools_nat;
  measure_child(self->content, orientation, for_size, &content_min, &content_nat);
  measure_child(self->tools, orientation, -1, &tools_min, &tools_nat);
  *minimum = MAX(content_min, tools_min);
  *natural = MAX(content_nat, tools_nat);
}

static void place(GtkWidget *child, int x, int y, int width, int height) {
  GtkAllocation allocation = {x, y, width, height};
  gtk_widget_size_allocate(child, &allocation, -1);
}

/* A side dock: its panels top to bottom, each as wide as it asks. Folded
 * panels take their pill's height; the open ones share what is left, each
 * getting at most what it asks for (the rest goes to the others). The
 * panels' own margins are the inset, so neighbours share one. */
static int allocate_column(LumaCreativeWorkspace *self, LumaPanelSide side, int width, int bottom, int *open_width) {
  int n = 0, open = 0, used = 0, widest = 0;
  *open_width = 0;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != side || !gtk_widget_get_visible(GTK_WIDGET(d->panel)))
      continue;
    n++;
    if (luma_floating_panel_get_folded(d->panel)) {
      int min_h, nat_h;
      gtk_widget_measure(GTK_WIDGET(d->panel), GTK_ORIENTATION_VERTICAL, -1, &min_h, &nat_h, NULL, NULL);
      used += nat_h;
    } else {
      open++;
    }
  }
  if (n == 0)
    return 0;
  int overlap = LUMA_UI_CREATIVE_PANEL_INSET;
  int room = MAX(0, bottom - used + (n - 1) * overlap);
  int heights[64] = {0};
  int wants[64] = {0};
  int k = 0;
  for (guint i = 0; i < self->docked->len && k < 64; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != side || !gtk_widget_get_visible(GTK_WIDGET(d->panel)))
      continue;
    int min_w, nat_w, min_h, nat_h;
    gtk_widget_measure(GTK_WIDGET(d->panel), GTK_ORIENTATION_HORIZONTAL, -1, &min_w, &nat_w, NULL, NULL);
    int w = MAX(min_w, MIN(nat_w, width));
    gtk_widget_measure(GTK_WIDGET(d->panel), GTK_ORIENTATION_VERTICAL, w, &min_h, &nat_h, NULL, NULL);
    wants[k] = luma_floating_panel_get_folded(d->panel) ? -nat_h : MAX(min_h, nat_h);
    k++;
  }
  /* Water-fill the open panels. */
  int left = open, share_room = room;
  gboolean settled[64] = {FALSE};
  while (left > 0) {
    int share = share_room / left;
    gboolean changed = FALSE;
    for (int j = 0; j < k; j++) {
      if (wants[j] < 0 || settled[j] || wants[j] > share)
        continue;
      heights[j] = wants[j];
      settled[j] = TRUE;
      share_room -= wants[j];
      left--;
      changed = TRUE;
    }
    if (!changed) {
      for (int j = 0; j < k; j++)
        if (wants[j] >= 0 && !settled[j]) {
          heights[j] = share;
          settled[j] = TRUE;
        }
      left = 0;
    }
  }
  int y = 0;
  k = 0;
  for (guint i = 0; i < self->docked->len && k < 64; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != side || !gtk_widget_get_visible(GTK_WIDGET(d->panel)))
      continue;
    GtkWidget *child = GTK_WIDGET(d->panel);
    int min_w, nat_w;
    gtk_widget_measure(child, GTK_ORIENTATION_HORIZONTAL, -1, &min_w, &nat_w, NULL, NULL);
    int w = MAX(min_w, MIN(nat_w, width));
    int h = wants[k] < 0 ? -wants[k] : heights[k];
    if (wants[k] >= 0) {
      int min_h, nat_h;
      gtk_widget_measure(child, GTK_ORIENTATION_VERTICAL, w, &min_h, &nat_h, NULL, NULL);
      h = MAX(min_h, h);
    }
    if (!luma_floating_panel_get_is_drawer(d->panel))
      place(child, side == LUMA_PANEL_SIDE_LEFT ? 0 : width - w, y, w, h);
    widest = MAX(widest, w);
    if (!luma_floating_panel_get_folded(d->panel))
      *open_width = MAX(*open_width, w);
    y += h - overlap;
    k++;
  }
  return widest;
}

/* The bottom dock: a row above the tools between the side docks. Open
 * panels share the width; folded ones are pills. */
static int allocate_row(LumaCreativeWorkspace *self, int x0, int x1, int height, int bottom) {
  int n = 0, open = 0, pills = 0, tallest = 0;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != LUMA_PANEL_SIDE_BOTTOM || !gtk_widget_get_visible(GTK_WIDGET(d->panel)))
      continue;
    n++;
    int min_w, nat_w;
    gtk_widget_measure(GTK_WIDGET(d->panel), GTK_ORIENTATION_HORIZONTAL, -1, &min_w, &nat_w, NULL, NULL);
    if (luma_floating_panel_get_folded(d->panel))
      pills += nat_w;
    else
      open++;
  }
  if (n == 0)
    return bottom;
  int overlap = LUMA_UI_CREATIVE_PANEL_INSET;
  int span = MAX(0, x1 - x0);
  int each = open > 0 ? MAX(0, (span - pills + (n - 1) * overlap) / open) : 0;
  int cap = height * LUMA_UI_CREATIVE_DOCK_BOTTOM_MAX_HEIGHT_PCT / 100;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != LUMA_PANEL_SIDE_BOTTOM || luma_floating_panel_get_folded(d->panel) ||
        !gtk_widget_get_visible(GTK_WIDGET(d->panel)))
      continue;
    int min_h, nat_h;
    gtk_widget_measure(GTK_WIDGET(d->panel), GTK_ORIENTATION_VERTICAL, each, &min_h, &nat_h, NULL, NULL);
    tallest = MAX(tallest, MAX(min_h, MIN(nat_h, cap)));
  }
  int x = x0;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != LUMA_PANEL_SIDE_BOTTOM || !gtk_widget_get_visible(GTK_WIDGET(d->panel)))
      continue;
    GtkWidget *child = GTK_WIDGET(d->panel);
    int min_w, nat_w, min_h, nat_h;
    gtk_widget_measure(child, GTK_ORIENTATION_HORIZONTAL, -1, &min_w, &nat_w, NULL, NULL);
    gboolean folded = luma_floating_panel_get_folded(d->panel);
    int w = folded ? nat_w : MAX(min_w, each);
    gtk_widget_measure(child, GTK_ORIENTATION_VERTICAL, w, &min_h, &nat_h, NULL, NULL);
    int h = folded ? nat_h : tallest;
    if (!luma_floating_panel_get_is_drawer(d->panel))
      place(child, x, bottom - h, w, h);
    x += w - overlap;
  }
  return open > 0 ? bottom - tallest : bottom;
}

static gboolean work_area_emit(gpointer user_data) {
  LumaCreativeWorkspace *self = user_data;
  self->work_idle = 0;
  g_signal_emit(self, ws_signals[WS_WORK_AREA_CHANGED], 0, self->work.x, self->work.y, self->work.width,
                self->work.height);
  return G_SOURCE_REMOVE;
}

/* What the docks leave of the work: between the open side docks, above an
 * open bottom dock and the tools. Folded pills and drawers don't count. */
static void update_work_area(LumaCreativeWorkspace *self, int x0, int x1, int y1) {
  GdkRectangle work = {x0, 0, MAX(0, x1 - x0), MAX(0, y1)};
  if (self->phone || self->focused)
    work = (GdkRectangle){0, 0, gtk_widget_get_width(GTK_WIDGET(self)), y1};
  if (gdk_rectangle_equal(&work, &self->work))
    return;
  self->work = work;
  if (self->work_idle == 0)
    self->work_idle = g_idle_add_full(G_PRIORITY_HIGH_IDLE, work_area_emit, self, NULL);
}

static void allocate_drawers(LumaCreativeWorkspace *self, int width, int height) {
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (!luma_floating_panel_get_is_drawer(d->panel) || !gtk_widget_get_visible(GTK_WIDGET(d->panel)))
      continue;
    int min_h, nat_h;
    gtk_widget_measure(GTK_WIDGET(d->panel), GTK_ORIENTATION_VERTICAL, width, &min_h, &nat_h, NULL, NULL);
    int cap = height * LUMA_UI_CREATIVE_PANEL_PHONE_MAX_HEIGHT_PCT / 100;
    int h = MAX(min_h, MIN(nat_h, cap));
    place(GTK_WIDGET(d->panel), 0, height - h, width, h);
  }
}

/* Where a drop lands as a strip on the dock's inner edge (the drop target). */
static void allocate_target(LumaCreativeWorkspace *self, int width, int height, int bottom, int left_w, int right_w) {
  if (self->target == NULL || !gtk_widget_get_visible(self->target))
    return;
  int thick = LUMA_UI_CREATIVE_DOCK_TARGET;
  int inset = LUMA_UI_CREATIVE_PANEL_INSET;
  switch (self->drop_side) {
  case LUMA_PANEL_SIDE_LEFT:
    place(self->target, inset, inset, MAX(thick, left_w - 2 * inset), MAX(thick, bottom - 2 * inset));
    break;
  case LUMA_PANEL_SIDE_RIGHT:
    place(self->target, width - MAX(thick, right_w - inset), inset, MAX(thick, right_w - 2 * inset),
          MAX(thick, bottom - 2 * inset));
    break;
  case LUMA_PANEL_SIDE_BOTTOM:
  default: {
    int x0 = left_w + inset, x1 = width - right_w - inset;
    int h = MAX(thick, height * LUMA_UI_CREATIVE_DOCK_BOTTOM_MAX_HEIGHT_PCT / 200);
    place(self->target, x0, bottom - h - inset, MAX(thick, x1 - x0), h);
    break;
  }
  }
}

static void luma_creative_workspace_size_allocate(GtkWidget *widget, int width, int height,
                                                  int baseline G_GNUC_UNUSED) {
  LumaCreativeWorkspace *self = LUMA_CREATIVE_WORKSPACE(widget);
  int min, nat;
  /* The width watch misses a window that opens at phone width on some
   * backends; the allocation always knows. State changes wait for idle. */
  if (luma_ui_is_phone(widget) != self->phone && g_object_get_data(G_OBJECT(self), "luma-phone-check") == NULL) {
    g_object_set_data(G_OBJECT(self), "luma-phone-check", GINT_TO_POINTER(1));
    g_idle_add_full(G_PRIORITY_HIGH_IDLE, phone_check, g_object_ref(self), g_object_unref);
  }
  measure_child(self->grid, GTK_ORIENTATION_HORIZONTAL, -1, &min, &nat);
  measure_child(self->grid, GTK_ORIENTATION_VERTICAL, -1, &min, &nat);
  place(self->grid, 0, 0, 0, 0);
  if (self->content != NULL && gtk_widget_get_visible(self->content))
    place(self->content, 0, 0, width, height);
  int bottom = height;
  if (self->tools != NULL && gtk_widget_get_visible(self->tools)) {
    int min_w, nat_w, min_h, nat_h;
    gtk_widget_measure(self->tools, GTK_ORIENTATION_HORIZONTAL, -1, &min_w, &nat_w, NULL, NULL);
    /* v70: the bar keeps 12 px from each side; on a phone its tools narrow to fit
     * (their phone minimum is smaller, their full size the desktop one). */
    int want = nat_w;
    if (self->phone) {
      int tools = 0;
      for (GtkWidget *c = gtk_widget_get_first_child(self->tools); c != NULL; c = gtk_widget_get_next_sibling(c))
        if (gtk_widget_get_visible(c) && gtk_widget_has_css_class(c, "lumaui-creative-tool"))
          tools++;
      want += tools * (LUMA_UI_CREATIVE_TOOL - LUMA_UI_CREATIVE_TOOL_PHONE_MIN);
    }
    int w = MAX(min_w, MIN(want, width - 2 * LUMA_UI_CREATIVE_TOOLS_SIDE));
    gtk_widget_measure(self->tools, GTK_ORIENTATION_VERTICAL, w, &min_h, &nat_h, NULL, NULL);
    int y = height - nat_h;
    place(self->tools, (width - w) / 2, y, w, nat_h);
    /* The tools' top edge; the panels keep their own inset above it. */
    bottom = y;
  }
  if (self->zoom != NULL && gtk_widget_get_visible(self->zoom)) {
    int min_w, nat_w, min_h, nat_h;
    gtk_widget_measure(self->zoom, GTK_ORIENTATION_HORIZONTAL, -1, &min_w, &nat_w, NULL, NULL);
    gtk_widget_measure(self->zoom, GTK_ORIENTATION_VERTICAL, nat_w, &min_h, &nat_h, NULL, NULL);
    place(self->zoom, width - nat_w, height - nat_h, nat_w, nat_h);
  }
  /* An open side panel stops above the tool bar (v70: 92 px of the height
   * are the tools' and the insets'), and never runs into the tools. */
  int column_bottom = MIN(height - LUMA_UI_CREATIVE_PANEL_RESERVE + 2 * LUMA_UI_CREATIVE_PANEL_INSET, bottom);
  int left_open, right_open;
  int left_w = allocate_column(self, LUMA_PANEL_SIDE_LEFT, width, column_bottom, &left_open);
  int right_w = allocate_column(self, LUMA_PANEL_SIDE_RIGHT, width, column_bottom, &right_open);
  int row_bottom = self->tools != NULL && gtk_widget_get_visible(self->tools)
                       ? bottom + LUMA_UI_CREATIVE_PANEL_INSET
                       : height;
  int row_top = allocate_row(self, left_w > 0 ? left_w - LUMA_UI_CREATIVE_PANEL_INSET : 0,
                             right_w > 0 ? width - right_w + LUMA_UI_CREATIVE_PANEL_INSET : width, height, row_bottom);
  update_work_area(self, left_open, width - right_open, MIN(row_top, bottom));
  allocate_drawers(self, width, height);
  allocate_target(self, width, height, column_bottom, left_w, right_w);
}

static void luma_creative_workspace_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaCreativeWorkspace *self = LUMA_CREATIVE_WORKSPACE(widget);
  int width = gtk_widget_get_width(widget), height = gtk_widget_get_height(widget);
  if (self->grid_visible && width > 0 && height > 0) {
    GdkRGBA dot;
    gtk_widget_get_color(self->grid, &dot);
    double pitch = luma_creative_grid_spacing(self->cam_zoom);
    double ox = fmod(self->cam_x, pitch), oy = fmod(self->cam_y, pitch);
    if (ox > 0)
      ox -= pitch;
    if (oy > 0)
      oy -= pitch;
    float radius = (float)(LUMA_UI_CREATIVE_GRID_DOT + 0.15);
    graphene_rect_t bounds = GRAPHENE_RECT_INIT(0, 0, width, height);
    graphene_rect_t tile = GRAPHENE_RECT_INIT((float)ox, (float)oy, (float)pitch, (float)pitch);
    gtk_snapshot_push_repeat(snapshot, &bounds, &tile);
    GskRoundedRect circle;
    graphene_rect_t square = GRAPHENE_RECT_INIT((float)(ox + pitch / 2) - radius, (float)(oy + pitch / 2) - radius,
                                                radius * 2, radius * 2);
    gsk_rounded_rect_init_from_rect(&circle, &square, radius);
    gtk_snapshot_push_rounded_clip(snapshot, &circle);
    gtk_snapshot_append_color(snapshot, &dot, &square);
    gtk_snapshot_pop(snapshot);
    gtk_snapshot_pop(snapshot);
  }
  GTK_WIDGET_CLASS(luma_creative_workspace_parent_class)->snapshot(widget, snapshot);
}

static void luma_creative_workspace_get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  LumaCreativeWorkspace *self = LUMA_CREATIVE_WORKSPACE(object);
  switch (id) {
  case WS_PROP_FOCUSED: g_value_set_boolean(value, self->focused); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void luma_creative_workspace_set_property(GObject *object, guint id, const GValue *value, GParamSpec *pspec) {
  LumaCreativeWorkspace *self = LUMA_CREATIVE_WORKSPACE(object);
  switch (id) {
  case WS_PROP_FOCUSED: luma_creative_workspace_set_focused(self, g_value_get_boolean(value)); break;
  default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void drop_child(GtkWidget **slot) {
  if (*slot == NULL)
    return;
  guint pending = GPOINTER_TO_UINT(g_object_get_data(G_OBJECT(*slot), "luma-creative-gone-timeout"));
  if (pending != 0) {
    g_source_remove(pending);
    g_object_set_data(G_OBJECT(*slot), "luma-creative-gone-timeout", NULL);
  }
  gtk_widget_unparent(*slot);
  *slot = NULL;
}

static void undock(LumaCreativeWorkspace *self, guint at) {
  Docked *docked = g_ptr_array_index(self->docked, at);
  g_signal_handlers_disconnect_by_data(docked->panel, self);
  panel_set_drawer(docked->panel, FALSE);
  panel_set_movable(docked->panel, NULL);
  GtkWidget *child = GTK_WIDGET(docked->panel);
  drop_child(&child);
  g_ptr_array_remove_index(self->docked, at);
}

static void luma_creative_workspace_dispose(GObject *object) {
  LumaCreativeWorkspace *self = LUMA_CREATIVE_WORKSPACE(object);
  g_clear_handle_id(&self->work_idle, g_source_remove);
  while (self->docked != NULL && self->docked->len > 0)
    undock(self, self->docked->len - 1);
  drop_child(&self->grid);
  drop_child(&self->content);
  drop_child(&self->target);
  drop_child(&self->zoom);
  drop_child(&self->tools);
  G_OBJECT_CLASS(luma_creative_workspace_parent_class)->dispose(object);
}

static void luma_creative_workspace_finalize(GObject *object) {
  LumaCreativeWorkspace *self = LUMA_CREATIVE_WORKSPACE(object);
  g_clear_pointer(&self->docked, g_ptr_array_unref);
  G_OBJECT_CLASS(luma_creative_workspace_parent_class)->finalize(object);
}

static void luma_creative_workspace_class_init(LumaCreativeWorkspaceClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->dispose = luma_creative_workspace_dispose;
  object_class->finalize = luma_creative_workspace_finalize;
  object_class->get_property = luma_creative_workspace_get_property;
  object_class->set_property = luma_creative_workspace_set_property;
  widget_class->measure = luma_creative_workspace_measure;
  widget_class->size_allocate = luma_creative_workspace_size_allocate;
  widget_class->snapshot = luma_creative_workspace_snapshot;
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_GROUP);
  ws_props[WS_PROP_FOCUSED] = g_param_spec_boolean("focused", NULL, NULL, FALSE,
                                                   G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, WS_N_PROPS, ws_props);
  /**
   * LumaCreativeWorkspace::layout-changed:
   * @self: the workspace
   *
   * A person moved a docked panel or folded or opened one. Save
   * luma_creative_workspace_get_layout() if the app keeps layouts. Not
   * emitted for luma_creative_workspace_set_layout() or at phone width
   * (a phone's pills are not a layout).
   */
  ws_signals[WS_LAYOUT_CHANGED] = g_signal_new("layout-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                               NULL, NULL, NULL, G_TYPE_NONE, 0);
  /**
   * LumaCreativeWorkspace::work-area-changed:
   * @self: the workspace
   * @x: the free area's left, in the workspace
   * @y: its top
   * @width: its width
   * @height: its height
   *
   * The part of the work no open docked panel or the tools cover changed
   * (a panel opened, folded or moved, or the window resized). Content that
   * must stay whole, such as a stream's canvas, fits itself into it; at
   * phone width and in focus mode it is the whole work above the tools.
   */
  ws_signals[WS_WORK_AREA_CHANGED] = g_signal_new("work-area-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST,
                                                  0, NULL, NULL, NULL, G_TYPE_NONE, 4, G_TYPE_INT, G_TYPE_INT,
                                                  G_TYPE_INT, G_TYPE_INT);
}

/* ── Drag and drop between docks ── */

static LumaPanelSide side_at(LumaCreativeWorkspace *self, double x, double y) {
  int width = gtk_widget_get_width(GTK_WIDGET(self)), height = gtk_widget_get_height(GTK_WIDGET(self));
  /* The outer third on each side is that side's dock; below them, the bottom. */
  if (y > height * 2 / 3.0 && x > width / 4.0 && x < width * 3 / 4.0)
    return LUMA_PANEL_SIDE_BOTTOM;
  return x < width / 2.0 ? LUMA_PANEL_SIDE_LEFT : LUMA_PANEL_SIDE_RIGHT;
}

static int index_at(LumaCreativeWorkspace *self, LumaPanelSide side, double x, double y, LumaFloatingPanel *moving) {
  int index = 0;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != side || d->panel == moving)
      continue;
    graphene_rect_t bounds;
    if (!gtk_widget_compute_bounds(GTK_WIDGET(d->panel), GTK_WIDGET(self), &bounds))
      continue;
    double middle = side == LUMA_PANEL_SIDE_BOTTOM ? bounds.origin.x + bounds.size.width / 2
                                                   : bounds.origin.y + bounds.size.height / 2;
    if ((side == LUMA_PANEL_SIDE_BOTTOM ? x : y) < middle)
      break;
    index++;
  }
  return index;
}

static void show_target(LumaCreativeWorkspace *self, gboolean shown) {
  if (self->target != NULL)
    gtk_widget_set_visible(self->target, shown);
  gtk_widget_queue_allocate(GTK_WIDGET(self));
}

static GdkDragAction drop_motion(GtkDropTarget *target, double x, double y, gpointer user_data) {
  LumaCreativeWorkspace *self = user_data;
  const GValue *value = gtk_drop_target_get_value(target);
  LumaFloatingPanel *panel = value != NULL ? g_value_get_object(value) : NULL;
  if (panel == NULL || docked_for_panel(self, panel) == NULL || self->phone)
    return 0;
  LumaPanelSide side = side_at(self, x, y);
  if (side != self->drop_side || !gtk_widget_get_visible(self->target)) {
    self->drop_side = side;
    luma_ui_set_css_class(self->target, "left", side == LUMA_PANEL_SIDE_LEFT);
    luma_ui_set_css_class(self->target, "right", side == LUMA_PANEL_SIDE_RIGHT);
    luma_ui_set_css_class(self->target, "bottom", side == LUMA_PANEL_SIDE_BOTTOM);
    show_target(self, TRUE);
  }
  self->drop_index = index_at(self, side, x, y, panel);
  return GDK_ACTION_MOVE;
}

static void drop_leave(GtkDropTarget *target G_GNUC_UNUSED, gpointer user_data) {
  show_target(LUMA_CREATIVE_WORKSPACE(user_data), FALSE);
}

static gboolean drop_done(GtkDropTarget *target G_GNUC_UNUSED, const GValue *value, double x, double y,
                          gpointer user_data) {
  LumaCreativeWorkspace *self = user_data;
  show_target(self, FALSE);
  LumaFloatingPanel *panel = g_value_get_object(value);
  Docked *docked = panel != NULL ? docked_for_panel(self, panel) : NULL;
  if (docked == NULL || self->phone)
    return FALSE;
  LumaPanelSide side = side_at(self, x, y);
  luma_creative_workspace_move_panel(self, docked->key, side, index_at(self, side, x, y, panel));
  return TRUE;
}

static void luma_creative_workspace_init(LumaCreativeWorkspace *self) {
  luma_creative_install();
  self->grid_visible = TRUE;
  self->cam_zoom = 1.0;
  self->docked = g_ptr_array_new_with_free_func(docked_free);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-creative-workspace");
  gtk_widget_set_overflow(GTK_WIDGET(self), GTK_OVERFLOW_HIDDEN);
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_vexpand(GTK_WIDGET(self), TRUE);
  self->grid = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->grid, "lumaui-creative-grid");
  gtk_widget_set_can_target(self->grid, FALSE);
  gtk_widget_set_parent(self->grid, GTK_WIDGET(self));
  self->target = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->target, "lumaui-creative-dock-target");
  gtk_widget_set_can_target(self->target, FALSE);
  gtk_widget_set_visible(self->target, FALSE);
  gtk_widget_set_parent(self->target, GTK_WIDGET(self));
  GtkDropTarget *drop = gtk_drop_target_new(LUMA_TYPE_FLOATING_PANEL, GDK_ACTION_MOVE);
  gtk_drop_target_set_preload(drop, TRUE);
  g_signal_connect(drop, "motion", G_CALLBACK(drop_motion), self);
  g_signal_connect(drop, "enter", G_CALLBACK(drop_motion), self);
  g_signal_connect(drop, "leave", G_CALLBACK(drop_leave), self);
  g_signal_connect(drop, "drop", G_CALLBACK(drop_done), self);
  gtk_widget_add_controller(GTK_WIDGET(self), GTK_EVENT_CONTROLLER(drop));
  luma_ui_width_watch(GTK_WIDGET(self), width_changed, NULL, (LUMA_TIER_PHONE_BELOW - 1));
}

GtkWidget *luma_creative_workspace_new(GtkWidget *content) {
  GtkWidget *self = g_object_new(LUMA_TYPE_CREATIVE_WORKSPACE, NULL);
  if (content != NULL)
    luma_creative_workspace_set_content(LUMA_CREATIVE_WORKSPACE(self), content);
  return self;
}

static void set_slot(LumaCreativeWorkspace *self, GtkWidget **slot, GtkWidget *child, const char *css_class) {
  if (*slot == child)
    return;
  if (*slot != NULL && css_class != NULL)
    gtk_widget_remove_css_class(*slot, css_class);
  drop_child(slot);
  if (child != NULL) {
    *slot = child;
    gtk_widget_set_parent(child, GTK_WIDGET(self));
    if (css_class != NULL)
      gtk_widget_add_css_class(child, css_class);
  }
  restack(self);
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

void luma_creative_workspace_set_content(LumaCreativeWorkspace *self, GtkWidget *content) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  g_return_if_fail(content == NULL || GTK_IS_WIDGET(content));
  set_slot(self, &self->content, content, NULL);
}

GtkWidget *luma_creative_workspace_get_content(LumaCreativeWorkspace *self) {
  g_return_val_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self), NULL);
  return self->content;
}

/* ── Docked panels ── */

static int clamp_index(LumaCreativeWorkspace *self, LumaPanelSide side, int index, Docked *except) {
  int count = 0;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side == side && d != except)
      count++;
  }
  return index < 0 || index > count ? count : index;
}

/* Put @docked into the array as the @index-th panel of its side. */
static void insert_docked(LumaCreativeWorkspace *self, Docked *docked, int index) {
  guint at = self->docked->len;
  int seen = 0;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != docked->side)
      continue;
    if (seen == index) {
      at = i;
      break;
    }
    seen++;
    at = i + 1;
  }
  g_ptr_array_insert(self->docked, at, docked);
}

static void dock_add(LumaCreativeWorkspace *self, const char *key, LumaFloatingPanel *panel, LumaPanelSide side,
                     gboolean movable);

void luma_creative_workspace_add_panel(LumaCreativeWorkspace *self, const char *key, LumaFloatingPanel *panel,
                                       LumaPanelSide side) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  dock_add(self, key, panel, side, TRUE);
}

static void dock_add(LumaCreativeWorkspace *self, const char *key, LumaFloatingPanel *panel, LumaPanelSide side,
                     gboolean movable) {
  g_return_if_fail(key != NULL && *key != '\0');
  g_return_if_fail(LUMA_IS_FLOATING_PANEL(panel));
  g_return_if_fail(gtk_widget_get_parent(GTK_WIDGET(panel)) == NULL);
  if (find_docked(self, key, NULL) != NULL) {
    g_critical("a workspace already has a panel %s", key);
    return;
  }
  Docked *docked = g_new0(Docked, 1);
  docked->key = g_strdup(key);
  docked->panel = panel;
  docked->side = side;
  insert_docked(self, docked, -1);
  gtk_widget_set_parent(GTK_WIDGET(panel), GTK_WIDGET(self));
  panel_set_side(panel, side);
  panel_set_movable(panel, movable ? self : NULL);
  if (self->phone) {
    docked->was_folded = luma_floating_panel_get_folded(panel);
    luma_floating_panel_set_folded(panel, TRUE);
  }
  g_signal_connect(panel, "notify::folded", G_CALLBACK(panel_folded_changed), self);
  restack(self);
  update_drawers(self);
  set_gone(GTK_WIDGET(panel), self->focused);
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

void luma_creative_workspace_remove_panel(LumaCreativeWorkspace *self, const char *key) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  guint at;
  if (find_docked(self, key, &at) == NULL)
    return;
  undock(self, at);
  gtk_widget_queue_resize(GTK_WIDGET(self));
}

LumaFloatingPanel *luma_creative_workspace_get_panel(LumaCreativeWorkspace *self, const char *key) {
  g_return_val_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self), NULL);
  Docked *docked = find_docked(self, key, NULL);
  return docked != NULL ? docked->panel : NULL;
}

static void place_docked(LumaCreativeWorkspace *self, Docked *docked, LumaPanelSide side, int index) {
  index = clamp_index(self, side, index, docked);
  guint at = 0;
  for (guint i = 0; i < self->docked->len; i++)
    if (g_ptr_array_index(self->docked, i) == docked)
      at = i;
  g_ptr_array_steal_index(self->docked, at);
  docked->side = side;
  insert_docked(self, docked, index);
  panel_set_side(docked->panel, side);
}

void luma_creative_workspace_move_panel(LumaCreativeWorkspace *self, const char *key, LumaPanelSide side,
                                        int index) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  Docked *docked = find_docked(self, key, NULL);
  g_return_if_fail(docked != NULL);
  place_docked(self, docked, side, index);
  restack(self);
  gtk_widget_queue_resize(GTK_WIDGET(self));
  layout_changed(self);
}

GVariant *luma_creative_workspace_get_layout(LumaCreativeWorkspace *self) {
  g_return_val_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self), NULL);
  GVariantBuilder builder;
  g_variant_builder_init(&builder, G_VARIANT_TYPE("a(ssb)"));
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    /* At phone width, a panel's place on a computer is what was saved. */
    gboolean folded = self->phone ? d->was_folded : luma_floating_panel_get_folded(d->panel);
    const char *side = d->side == LUMA_PANEL_SIDE_LEFT ? "left" : d->side == LUMA_PANEL_SIDE_RIGHT ? "right" : "bottom";
    g_variant_builder_add(&builder, "(ssb)", d->key, side, folded);
  }
  return g_variant_ref_sink(g_variant_builder_end(&builder));
}

gboolean luma_creative_workspace_set_layout(LumaCreativeWorkspace *self, GVariant *layout) {
  g_return_val_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self), FALSE);
  g_return_val_if_fail(layout != NULL, FALSE);
  if (!g_variant_is_of_type(layout, G_VARIANT_TYPE("a(ssb)")))
    return FALSE;
  self->applying = TRUE;
  GVariantIter iter;
  const char *key, *side_name;
  gboolean folded;
  int index[3] = {0, 0, 0};
  g_variant_iter_init(&iter, layout);
  while (g_variant_iter_next(&iter, "(&s&sb)", &key, &side_name, &folded)) {
    Docked *docked = find_docked(self, key, NULL);
    if (docked == NULL)
      continue; /* a panel this version doesn't have */
    LumaPanelSide side = g_str_equal(side_name, "right")    ? LUMA_PANEL_SIDE_RIGHT
                         : g_str_equal(side_name, "bottom") ? LUMA_PANEL_SIDE_BOTTOM
                                                            : LUMA_PANEL_SIDE_LEFT;
    place_docked(self, docked, side, index[side]++);
    if (self->phone)
      docked->was_folded = folded;
    else
      luma_floating_panel_set_folded(docked->panel, folded);
  }
  self->applying = FALSE;
  restack(self);
  update_drawers(self);
  gtk_widget_queue_resize(GTK_WIDGET(self));
  return TRUE;
}

/* The panel's dock menu: where else it can go, and up or down in its dock. */
static void dock_activate(GSimpleAction *action G_GNUC_UNUSED, GVariant *parameter, gpointer user_data) {
  LumaFloatingPanel *panel = user_data;
  LumaCreativeWorkspace *self = panel->dock;
  Docked *docked = self != NULL ? docked_for_panel(self, panel) : NULL;
  if (docked == NULL)
    return;
  const char *where = g_variant_get_string(parameter, NULL);
  int at = 0;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d == docked)
      break;
    if (d->side == docked->side)
      at++;
  }
  if (g_str_equal(where, "up"))
    luma_creative_workspace_move_panel(self, docked->key, docked->side, MAX(0, at - 1));
  else if (g_str_equal(where, "down"))
    luma_creative_workspace_move_panel(self, docked->key, docked->side, at + 1);
  else
    luma_creative_workspace_move_panel(self, docked->key,
                                       g_str_equal(where, "right")    ? LUMA_PANEL_SIDE_RIGHT
                                       : g_str_equal(where, "bottom") ? LUMA_PANEL_SIDE_BOTTOM
                                                                      : LUMA_PANEL_SIDE_LEFT,
                                       -1);
}

static void dock_menu_append(LumaFloatingPanel *panel, GMenu *menu) {
  LumaCreativeWorkspace *self = panel->dock;
  Docked *docked = self != NULL ? docked_for_panel(self, panel) : NULL;
  if (docked == NULL)
    return;
  int count = 0, at = 0;
  for (guint i = 0; i < self->docked->len; i++) {
    Docked *d = g_ptr_array_index(self->docked, i);
    if (d->side != docked->side)
      continue;
    if (d == docked)
      at = count;
    count++;
  }
  g_autoptr(GMenu) section = g_menu_new();
  gboolean column = docked->side != LUMA_PANEL_SIDE_BOTTOM;
  if (at > 0)
    g_menu_append(section, column ? "Move up" : "Move left", "creative-dock.move::up");
  if (at < count - 1)
    g_menu_append(section, column ? "Move down" : "Move right", "creative-dock.move::down");
  if (docked->side != LUMA_PANEL_SIDE_LEFT)
    g_menu_append(section, "Dock on the left", "creative-dock.move::left");
  if (docked->side != LUMA_PANEL_SIDE_RIGHT)
    g_menu_append(section, "Dock on the right", "creative-dock.move::right");
  if (docked->side != LUMA_PANEL_SIDE_BOTTOM)
    g_menu_append(section, "Dock at the bottom", "creative-dock.move::bottom");
  g_menu_append_section(menu, NULL, G_MENU_MODEL(section));
}

/* set_left/set_right: the one panel keyed "left"/"right", first in its dock. */
static void set_single(LumaCreativeWorkspace *self, const char *key, LumaFloatingPanel *panel, LumaPanelSide side) {
  Docked *docked = find_docked(self, key, NULL);
  if (docked != NULL && docked->panel == panel)
    return;
  luma_creative_workspace_remove_panel(self, key);
  if (panel == NULL)
    return;
  dock_add(self, key, panel, side, FALSE);
  docked = find_docked(self, key, NULL);
  place_docked(self, docked, side, 0);
  restack(self);
}

void luma_creative_workspace_set_left(LumaCreativeWorkspace *self, LumaFloatingPanel *panel) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  g_return_if_fail(panel == NULL || LUMA_IS_FLOATING_PANEL(panel));
  set_single(self, "left", panel, LUMA_PANEL_SIDE_LEFT);
}

LumaFloatingPanel *luma_creative_workspace_get_left(LumaCreativeWorkspace *self) {
  g_return_val_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self), NULL);
  return first_on(self, LUMA_PANEL_SIDE_LEFT);
}

void luma_creative_workspace_set_right(LumaCreativeWorkspace *self, LumaFloatingPanel *panel) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  g_return_if_fail(panel == NULL || LUMA_IS_FLOATING_PANEL(panel));
  set_single(self, "right", panel, LUMA_PANEL_SIDE_RIGHT);
}

LumaFloatingPanel *luma_creative_workspace_get_right(LumaCreativeWorkspace *self) {
  g_return_val_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self), NULL);
  return first_on(self, LUMA_PANEL_SIDE_RIGHT);
}

void luma_creative_workspace_set_tools(LumaCreativeWorkspace *self, GtkWidget *tools) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  g_return_if_fail(tools == NULL || GTK_IS_WIDGET(tools));
  set_slot(self, &self->tools, tools, NULL);
  /* The workspace places the bar itself (centred, capped, shared out on a phone). */
  if (tools != NULL) {
    gtk_widget_set_halign(tools, GTK_ALIGN_FILL);
    gtk_widget_set_valign(tools, GTK_ALIGN_FILL);
  }
}

void luma_creative_workspace_set_zoom(LumaCreativeWorkspace *self, GtkWidget *zoom) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  g_return_if_fail(zoom == NULL || GTK_IS_WIDGET(zoom));
  set_slot(self, &self->zoom, zoom, "lumaui-creative-zoom");
  set_gone(self->zoom, self->focused || self->phone);
}

void luma_creative_workspace_set_focused(LumaCreativeWorkspace *self, gboolean focused) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  focused = !!focused;
  if (self->focused == focused)
    return;
  self->focused = focused;
  luma_ui_set_css_class(GTK_WIDGET(self), "focused", focused);
  update_visibility(self);
  g_object_notify_by_pspec(G_OBJECT(self), ws_props[WS_PROP_FOCUSED]);
}

gboolean luma_creative_workspace_get_focused(LumaCreativeWorkspace *self) {
  g_return_val_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self), FALSE);
  return self->focused;
}

void luma_creative_workspace_set_camera(LumaCreativeWorkspace *self, double zoom, double x, double y) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  g_return_if_fail(zoom > 0);
  self->cam_zoom = zoom;
  self->cam_x = x;
  self->cam_y = y;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

void luma_creative_workspace_set_grid_visible(LumaCreativeWorkspace *self, gboolean visible) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  self->grid_visible = visible;
  gtk_widget_queue_draw(GTK_WIDGET(self));
}

gboolean luma_creative_workspace_get_is_phone(LumaCreativeWorkspace *self) {
  g_return_val_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self), FALSE);
  return self->phone;
}

void luma_creative_workspace_get_work_area(LumaCreativeWorkspace *self, int *x, int *y, int *width, int *height) {
  g_return_if_fail(LUMA_IS_CREATIVE_WORKSPACE(self));
  if (x != NULL)
    *x = self->work.x;
  if (y != NULL)
    *y = self->work.y;
  if (width != NULL)
    *width = self->work.width;
  if (height != NULL)
    *height = self->work.height;
}
