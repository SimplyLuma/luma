/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI creative family (KB-D): LumaToolBar (v70 .sutools, #vw-pal). */
#include "luma-creative-tools.h"
#include "luma-creative-private.h"
#include "luma-menu-drawer.h"

enum { TOOL_CHANGED, N_SIGNALS };
static guint signals[N_SIGNALS];

typedef struct {
  char *key;
  char *icon;
  char *label;
  char *shortcut;
  char *flyout;      /* the group's key, or NULL */
  GtkWidget *button; /* its own button, or its flyout's */
} Tool;

typedef struct {
  char *key;
  char *label;
  char *shown; /* the member the button shows */
  GtkWidget *button;
  GtkWidget *image;
} Flyout;

struct _LumaToolBar {
  GtkBox parent_instance;
  LumaToolBarKind kind;
  GPtrArray *tools;   /* Tool */
  GPtrArray *flyouts; /* Flyout */
  char *current;
  GtkEventController *shortcuts;
};

G_DEFINE_FINAL_TYPE(LumaToolBar, luma_tool_bar, GTK_TYPE_BOX)

GType luma_tool_bar_kind_get_type(void) {
  static gsize type_id = 0;
  static const GEnumValue values[] = {
    {LUMA_TOOL_BAR_KIND_BAR, "LUMA_TOOL_BAR_KIND_BAR", "bar"},
    {LUMA_TOOL_BAR_KIND_PALETTE, "LUMA_TOOL_BAR_KIND_PALETTE", "palette"},
    {0, NULL, NULL},
  };
  if (g_once_init_enter(&type_id))
    g_once_init_leave(&type_id, g_enum_register_static(g_intern_static_string("LumaToolBarKind"), values));
  return type_id;
}

static void tool_free(gpointer data) {
  Tool *tool = data;
  g_free(tool->key);
  g_free(tool->icon);
  g_free(tool->label);
  g_free(tool->shortcut);
  g_free(tool->flyout);
  g_free(tool);
}

static void flyout_free(gpointer data) {
  Flyout *flyout = data;
  g_free(flyout->key);
  g_free(flyout->label);
  g_free(flyout->shown);
  g_free(flyout);
}

static Tool *find_tool(LumaToolBar *self, const char *key) {
  for (guint i = 0; i < self->tools->len; i++) {
    Tool *tool = g_ptr_array_index(self->tools, i);
    if (g_strcmp0(tool->key, key) == 0)
      return tool;
  }
  return NULL;
}

static Flyout *find_flyout(LumaToolBar *self, const char *key) {
  for (guint i = 0; i < self->flyouts->len; i++) {
    Flyout *flyout = g_ptr_array_index(self->flyouts, i);
    if (g_strcmp0(flyout->key, key) == 0)
      return flyout;
  }
  return NULL;
}

static char *tool_tooltip(const char *label, const char *shortcut) {
  return shortcut != NULL && *shortcut != '\0' ? g_strdup_printf("%s · %s", label, shortcut) : g_strdup(label);
}

static void describe(GtkWidget *button, const char *label, const char *shortcut) {
  g_autofree char *tip = tool_tooltip(label, shortcut);
  gtk_widget_set_tooltip_text(button, tip);
  luma_ui_set_accessible_label(button, label);
  if (shortcut != NULL && *shortcut != '\0')
    gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_KEY_SHORTCUTS, shortcut, -1);
}

static void refresh(LumaToolBar *self) {
  Tool *current = find_tool(self, self->current);
  for (guint i = 0; i < self->tools->len; i++) {
    Tool *tool = g_ptr_array_index(self->tools, i);
    if (tool->flyout != NULL)
      continue;
    gboolean on = current == tool;
    luma_ui_set_css_class(tool->button, "on", on);
    gtk_accessible_update_state(GTK_ACCESSIBLE(tool->button), GTK_ACCESSIBLE_STATE_PRESSED,
                                on ? GTK_ACCESSIBLE_TRISTATE_TRUE : GTK_ACCESSIBLE_TRISTATE_FALSE, -1);
  }
  for (guint i = 0; i < self->flyouts->len; i++) {
    Flyout *flyout = g_ptr_array_index(self->flyouts, i);
    gboolean on = current != NULL && g_strcmp0(current->flyout, flyout->key) == 0;
    if (on && g_strcmp0(flyout->shown, current->key) != 0) {
      g_free(flyout->shown);
      flyout->shown = g_strdup(current->key);
    }
    Tool *shown = find_tool(self, flyout->shown);
    if (shown != NULL) {
      g_autofree char *name = luma_ui_icon_name(shown->icon);
      gtk_image_set_from_icon_name(GTK_IMAGE(flyout->image), name);
      g_autofree char *spoken = g_strdup_printf("%s, %s", flyout->label, shown->label);
      describe(flyout->button, spoken, shown->shortcut);
      g_autofree char *tip = tool_tooltip(flyout->label, shown->shortcut);
      gtk_widget_set_tooltip_text(flyout->button, tip);
    }
    luma_ui_set_css_class(flyout->button, "on", on);
    gtk_accessible_update_state(GTK_ACCESSIBLE(flyout->button), GTK_ACCESSIBLE_STATE_PRESSED,
                                on ? GTK_ACCESSIBLE_TRISTATE_TRUE : GTK_ACCESSIBLE_TRISTATE_FALSE, -1);
  }
}

static void select_tool(LumaToolBar *self, const char *key, gboolean notify) {
  gboolean changed = g_strcmp0(key, self->current) != 0;
  if (changed) {
    g_free(self->current);
    self->current = g_strdup(key);
  }
  refresh(self);
  if (notify && changed)
    g_signal_emit(self, signals[TOOL_CHANGED], 0, self->current);
}

static void pick_activated(GSimpleAction *action G_GNUC_UNUSED, GVariant *parameter, gpointer user_data) {
  LumaToolBar *self = user_data;
  const char *key = g_variant_get_string(parameter, NULL);
  if (find_tool(self, key) != NULL)
    select_tool(self, key, TRUE);
}

static void tool_clicked(GtkButton *button, gpointer user_data) {
  LumaToolBar *self = user_data;
  select_tool(self, g_object_get_data(G_OBJECT(button), "luma-tool-key"), TRUE);
}

static void flyout_clicked(GtkButton *button, gpointer user_data) {
  LumaToolBar *self = user_data;
  Flyout *flyout = find_flyout(self, g_object_get_data(G_OBJECT(button), "luma-flyout-key"));
  if (flyout != NULL && flyout->shown != NULL)
    select_tool(self, flyout->shown, TRUE);
}

static void flyout_more_pressed(GtkGestureClick *gesture, int n_press G_GNUC_UNUSED,
                                double x G_GNUC_UNUSED, double y G_GNUC_UNUSED, gpointer user_data) {
  LumaToolBar *self = user_data;
  GtkWidget *more = gtk_event_controller_get_widget(GTK_EVENT_CONTROLLER(gesture));
  GtkWidget *button = gtk_widget_get_ancestor(more, GTK_TYPE_BUTTON);
  gtk_gesture_set_state(GTK_GESTURE(gesture), GTK_EVENT_SEQUENCE_CLAIMED);
  if (button != NULL)
    luma_tool_bar_open_flyout(self, g_object_get_data(G_OBJECT(button), "luma-flyout-key"));
}

static GtkWidget *tool_button(LumaToolBar *self, const char *icon, GtkWidget **image_out, gboolean flyout) {
  GtkWidget *button = g_object_new(GTK_TYPE_BUTTON, "accessible-role", GTK_ACCESSIBLE_ROLE_TOGGLE_BUTTON, NULL);
  gtk_widget_add_css_class(button, "lumaui-creative-tool");
  /* On a phone the tools share what the bar has (v70 flex). */
  gtk_widget_set_hexpand(button, TRUE);
  GtkWidget *overlay = gtk_overlay_new();
  GtkWidget *image = luma_ui_icon_image(icon, 0);
  gtk_widget_set_halign(image, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(image, GTK_ALIGN_CENTER);
  gtk_overlay_set_child(GTK_OVERLAY(overlay), image);
  if (flyout) {
    GtkWidget *more = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(more, "lumaui-creative-tool-more");
    gtk_widget_set_halign(more, GTK_ALIGN_END);
    gtk_widget_set_valign(more, GTK_ALIGN_END);
    GtkWidget *mark = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(mark, "lumaui-creative-tool-more-mark");
    gtk_widget_set_halign(mark, GTK_ALIGN_END);
    gtk_widget_set_valign(mark, GTK_ALIGN_END);
    gtk_box_append(GTK_BOX(more), mark);
    GtkGesture *pick = gtk_gesture_click_new();
    gtk_gesture_single_set_button(GTK_GESTURE_SINGLE(pick), GDK_BUTTON_PRIMARY);
    g_signal_connect(pick, "pressed", G_CALLBACK(flyout_more_pressed), self);
    gtk_widget_add_controller(more, GTK_EVENT_CONTROLLER(pick));
    gtk_overlay_add_overlay(GTK_OVERLAY(overlay), more);
    gtk_widget_add_css_class(button, "flyout");
    gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_HAS_POPUP, TRUE, -1);
  }
  gtk_button_set_child(GTK_BUTTON(button), overlay);
  gtk_accessible_update_state(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_STATE_PRESSED, GTK_ACCESSIBLE_TRISTATE_FALSE, -1);
  if (image_out != NULL)
    *image_out = image;
  (void)self;
  return button;
}

static gboolean typing(GtkWidget *widget) {
  GtkRoot *root = gtk_widget_get_root(widget);
  GtkWidget *focus = root != NULL ? gtk_root_get_focus(root) : NULL;
  return focus != NULL && (GTK_IS_EDITABLE(focus) || GTK_IS_TEXT_VIEW(focus));
}

static gboolean shortcut_fired(GtkWidget *widget, GVariant *args, gpointer user_data G_GNUC_UNUSED) {
  LumaToolBar *self = LUMA_TOOL_BAR(widget);
  if (typing(widget) || !gtk_widget_get_mapped(widget))
    return FALSE;
  return luma_tool_bar_activate_shortcut(self, g_variant_get_string(args, NULL));
}

static void add_shortcut(LumaToolBar *self, const char *shortcut) {
  if (shortcut == NULL || *shortcut == '\0')
    return;
  g_autofree char *lower = g_utf8_strdown(shortcut, -1);
  guint keyval = gdk_keyval_from_name(lower);
  if (keyval == GDK_KEY_VoidSymbol || keyval == 0) {
    g_critical("tool shortcut '%s' is not a key", shortcut);
    return;
  }
  GtkShortcut *entry = gtk_shortcut_new(gtk_keyval_trigger_new(keyval, 0),
                                        gtk_callback_action_new(shortcut_fired, NULL, NULL));
  gtk_shortcut_set_arguments(entry, g_variant_new_string(shortcut));
  gtk_shortcut_controller_add_shortcut(GTK_SHORTCUT_CONTROLLER(self->shortcuts), entry);
}

static void luma_tool_bar_finalize(GObject *object) {
  LumaToolBar *self = LUMA_TOOL_BAR(object);
  g_clear_pointer(&self->tools, g_ptr_array_unref);
  g_clear_pointer(&self->flyouts, g_ptr_array_unref);
  g_clear_pointer(&self->current, g_free);
  G_OBJECT_CLASS(luma_tool_bar_parent_class)->finalize(object);
}

static void luma_tool_bar_class_init(LumaToolBarClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->finalize = luma_tool_bar_finalize;
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_TOOLBAR);
  /**
   * LumaToolBar::tool-changed:
   * @self: the tool bar
   * @key: the tool a person chose
   */
  signals[TOOL_CHANGED] = g_signal_new("tool-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                                       NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
}

static void luma_tool_bar_init(LumaToolBar *self) {
  luma_creative_install();
  self->tools = g_ptr_array_new_with_free_func(tool_free);
  self->flyouts = g_ptr_array_new_with_free_func(flyout_free);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_set_halign(GTK_WIDGET(self), GTK_ALIGN_CENTER);
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_END);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-creative-tools");
  /* Its tools expand (to share a phone's width); the bar itself does not. */
  gtk_widget_set_hexpand(GTK_WIDGET(self), FALSE);
  luma_ui_set_accessible_label(GTK_WIDGET(self), "Tools");
  luma_ui_arrow_keys(GTK_WIDGET(self), GTK_ORIENTATION_HORIZONTAL, FALSE);
  GSimpleActionGroup *group = g_simple_action_group_new();
  GSimpleAction *pick = g_simple_action_new("pick", G_VARIANT_TYPE_STRING);
  g_signal_connect(pick, "activate", G_CALLBACK(pick_activated), self);
  g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(pick));
  g_object_unref(pick);
  gtk_widget_insert_action_group(GTK_WIDGET(self), "creative-tools", G_ACTION_GROUP(group));
  g_object_unref(group);
  self->shortcuts = gtk_shortcut_controller_new();
  gtk_shortcut_controller_set_scope(GTK_SHORTCUT_CONTROLLER(self->shortcuts), GTK_SHORTCUT_SCOPE_GLOBAL);
  gtk_widget_add_controller(GTK_WIDGET(self), self->shortcuts);
}

GtkWidget *luma_tool_bar_new(LumaToolBarKind kind) {
  LumaToolBar *self = g_object_new(LUMA_TYPE_TOOL_BAR, "accessible-role", GTK_ACCESSIBLE_ROLE_TOOLBAR, NULL);
  self->kind = kind;
  gtk_widget_add_css_class(GTK_WIDGET(self), kind == LUMA_TOOL_BAR_KIND_PALETTE ? "palette" : "bar");
  if (kind == LUMA_TOOL_BAR_KIND_PALETTE)
    gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_START);
  return GTK_WIDGET(self);
}

static gboolean check_new_key(LumaToolBar *self, const char *key) {
  if (find_tool(self, key) != NULL || find_flyout(self, key) != NULL) {
    g_critical("tool keys are unique: '%s' is already a tool", key);
    return FALSE;
  }
  return TRUE;
}

static Tool *new_tool(const char *key, const char *icon, const char *label, const char *shortcut, const char *flyout) {
  Tool *tool = g_new0(Tool, 1);
  tool->key = g_strdup(key);
  tool->icon = g_strdup(icon);
  tool->label = g_strdup(label);
  tool->shortcut = g_strdup(shortcut);
  tool->flyout = g_strdup(flyout);
  return tool;
}

void luma_tool_bar_add_tool(LumaToolBar *self, const char *key, const char *icon, const char *label,
                            const char *shortcut) {
  g_return_if_fail(LUMA_IS_TOOL_BAR(self));
  g_return_if_fail(key != NULL && icon != NULL && label != NULL);
  if (!check_new_key(self, key))
    return;
  Tool *tool = new_tool(key, icon, label, shortcut, NULL);
  tool->button = tool_button(self, icon, NULL, FALSE);
  describe(tool->button, label, shortcut);
  g_object_set_data_full(G_OBJECT(tool->button), "luma-tool-key", g_strdup(key), g_free);
  g_signal_connect(tool->button, "clicked", G_CALLBACK(tool_clicked), self);
  g_ptr_array_add(self->tools, tool);
  gtk_box_append(GTK_BOX(self), tool->button);
  add_shortcut(self, shortcut);
  if (self->current == NULL)
    self->current = g_strdup(key);
  refresh(self);
}

void luma_tool_bar_add_flyout(LumaToolBar *self, const char *key, const char *label) {
  g_return_if_fail(LUMA_IS_TOOL_BAR(self));
  g_return_if_fail(key != NULL && label != NULL);
  if (!check_new_key(self, key))
    return;
  Flyout *flyout = g_new0(Flyout, 1);
  flyout->key = g_strdup(key);
  flyout->label = g_strdup(label);
  flyout->button = tool_button(self, "square", &flyout->image, TRUE);
  describe(flyout->button, label, NULL);
  g_object_set_data_full(G_OBJECT(flyout->button), "luma-flyout-key", g_strdup(key), g_free);
  g_signal_connect(flyout->button, "clicked", G_CALLBACK(flyout_clicked), self);
  g_ptr_array_add(self->flyouts, flyout);
  gtk_box_append(GTK_BOX(self), flyout->button);
}

void luma_tool_bar_add_flyout_tool(LumaToolBar *self, const char *flyout_key, const char *key, const char *icon,
                                   const char *label, const char *shortcut) {
  g_return_if_fail(LUMA_IS_TOOL_BAR(self));
  g_return_if_fail(flyout_key != NULL && key != NULL && icon != NULL && label != NULL);
  Flyout *flyout = find_flyout(self, flyout_key);
  if (flyout == NULL) {
    g_critical("unknown flyout '%s'", flyout_key);
    return;
  }
  if (!check_new_key(self, key))
    return;
  Tool *tool = new_tool(key, icon, label, shortcut, flyout_key);
  tool->button = flyout->button;
  g_ptr_array_add(self->tools, tool);
  add_shortcut(self, shortcut);
  if (flyout->shown == NULL)
    flyout->shown = g_strdup(key);
  if (self->current == NULL)
    self->current = g_strdup(key);
  refresh(self);
}

void luma_tool_bar_add_separator(LumaToolBar *self) {
  g_return_if_fail(LUMA_IS_TOOL_BAR(self));
  GtkWidget *separator = gtk_separator_new(GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(separator, "lumaui-creative-tools-separator");
  gtk_widget_set_valign(separator, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(self), separator);
}

void luma_tool_bar_set_current(LumaToolBar *self, const char *key) {
  g_return_if_fail(LUMA_IS_TOOL_BAR(self));
  g_return_if_fail(key != NULL);
  if (find_tool(self, key) == NULL) {
    g_critical("unknown tool '%s'", key);
    return;
  }
  select_tool(self, key, FALSE);
}

const char *luma_tool_bar_get_current(LumaToolBar *self) {
  g_return_val_if_fail(LUMA_IS_TOOL_BAR(self), NULL);
  return self->current;
}

void luma_tool_bar_open_flyout(LumaToolBar *self, const char *flyout_key) {
  g_return_if_fail(LUMA_IS_TOOL_BAR(self));
  Flyout *flyout = find_flyout(self, flyout_key);
  if (flyout == NULL) {
    g_critical("unknown flyout '%s'", flyout_key);
    return;
  }
  GtkWidget *menu = luma_floating_menu_new(flyout->label);
  for (guint i = 0; i < self->tools->len; i++) {
    Tool *tool = g_ptr_array_index(self->tools, i);
    if (g_strcmp0(tool->flyout, flyout->key) != 0)
      continue;
    g_autoptr(GVariant) target = g_variant_ref_sink(g_variant_new_string(tool->key));
    g_autofree char *action = g_action_print_detailed_name("creative-tools.pick", target);
    luma_floating_menu_add_item(LUMA_FLOATING_MENU(menu), tool->label, tool->icon, NULL, tool->shortcut, action,
                                g_strcmp0(tool->key, self->current) == 0);
  }
  luma_floating_menu_popup(LUMA_FLOATING_MENU(menu), flyout->button);
  if (g_object_is_floating(menu)) {
    g_object_ref_sink(menu);
    g_object_unref(menu);
  }
}

gboolean luma_tool_bar_activate_shortcut(LumaToolBar *self, const char *key) {
  g_return_val_if_fail(LUMA_IS_TOOL_BAR(self), FALSE);
  g_return_val_if_fail(key != NULL, FALSE);
  for (guint i = 0; i < self->tools->len; i++) {
    Tool *tool = g_ptr_array_index(self->tools, i);
    if (tool->shortcut != NULL && g_ascii_strcasecmp(tool->shortcut, key) == 0) {
      select_tool(self, tool->key, TRUE);
      return TRUE;
    }
  }
  return FALSE;
}
