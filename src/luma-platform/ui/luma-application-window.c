/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-application-window.h"
#include "luma-native-app-updates-private.h"
#include "luma-surface-backdrop.h"
#include "luma-layer-host.h"
#include "luma-island.h"
#include "luma-navigation-sidebar.h"
#include "luma-ui-private.h"

#include <gio/gdesktopappinfo.h>
#include <string.h>

/* A Luma window, drawn by LumaUI (ADR-052).
 *
 * The frame, the 42px title row, the identity pill in the top-left corner,
 * the connected window controls, the 9px gutter and the islands come from
 * LumaUI's own sheets (lumaui-palette.css and lumaui-toolkit.css, loaded by
 * luma_init() above libadwaita) and from the identity this widget builds. It
 * does not depend on Luma's patched libadwaita: that library builds its own
 * identity only for a header bar that has none, so on a patched system
 * nothing is drawn twice, and a stock one draws the same window.
 *
 * The identity's name and icon come from the application's desktop entry,
 * so the corner never disagrees with the dock; its menu is the application
 * menu model (set_menu_model). The application-facing API is unchanged:
 * set_identity, set_menu_model, set_body. */

struct _LumaApplicationWindow {
  AdwApplicationWindow parent_instance;
  LumaContext *context;
  GtkWidget *toolbar_view;
  GtkWidget *titlebar;
  GtkWidget *body_bin;
  GtkWidget *body_pad; /* .luma-window-body, inside the layer host */
  GtkWidget *layer_host;
  GtkWidget *body;
  GtkWidget *identity;
  GtkWidget *title_leading;
  GtkWidget *title_content;
  GtkWidget *title_trailing;
  gboolean title_centred;
  LumaSurfaceBackdrop *backdrop;
};

G_DEFINE_FINAL_TYPE(LumaApplicationWindow, luma_application_window,
                    ADW_TYPE_APPLICATION_WINDOW)

static void add_identity(LumaApplicationWindow *self);

enum { PROP_0, PROP_CONTEXT, N_PROPERTIES };
static GParamSpec *properties[N_PROPERTIES];

static void apply_context(LumaApplicationWindow *self) {
  if (luma_ui_mobile_form_factor()) {
    gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-phone-device");
    gtk_window_set_decorated(GTK_WINDOW(self), FALSE);
    gtk_widget_set_visible(self->titlebar, FALSE);
    gtk_widget_set_can_target(self->titlebar, FALSE);
    return;
  }
  if (self->context == NULL)
    return;
  /* Windowed keeps the frame. Handheld fullscreen has none to keep: the shell
   * owns that surface's chrome, and the title row would be a second one. */
  const gboolean decorated = luma_context_get_decorated(self->context);
  gtk_window_set_decorated(GTK_WINDOW(self), decorated);
  gtk_widget_set_visible(self->titlebar, decorated);
  if (luma_context_get_touch_targets(self->context))
    gtk_widget_add_css_class(GTK_WIDGET(self), "luma-touch");
  else
    gtk_widget_remove_css_class(GTK_WIDGET(self), "luma-touch");
}

static void luma_application_window_get_property(GObject *object, guint id,
                                                 GValue *value,
                                                 GParamSpec *pspec) {
  LumaApplicationWindow *self = LUMA_APPLICATION_WINDOW(object);
  switch (id) {
  case PROP_CONTEXT:
    g_value_set_object(value, self->context);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void luma_application_window_set_property(GObject *object, guint id,
                                                 const GValue *value,
                                                 GParamSpec *pspec) {
  LumaApplicationWindow *self = LUMA_APPLICATION_WINDOW(object);
  switch (id) {
  case PROP_CONTEXT:
    g_set_object(&self->context, g_value_get_object(value));
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void luma_application_window_constructed(GObject *object) {
  LumaApplicationWindow *self = LUMA_APPLICATION_WINDOW(object);
  G_OBJECT_CLASS(luma_application_window_parent_class)->constructed(object);
  apply_context(self);
  /* The application is set by now (it is a construct property). */
  add_identity(self);
}

static void luma_application_window_dispose(GObject *object) {
  LumaApplicationWindow *self = LUMA_APPLICATION_WINDOW(object);
  g_clear_object(&self->context);
  g_clear_object(&self->backdrop);
  self->toolbar_view = NULL;
  self->titlebar = NULL;
  self->body_bin = NULL;
  self->body_pad = NULL;
  self->body = NULL;
  self->identity = NULL;
  self->title_leading = NULL;
  self->title_content = NULL;
  self->title_trailing = NULL;
  G_OBJECT_CLASS(luma_application_window_parent_class)->dispose(object);
}

static void luma_application_window_class_init(
    LumaApplicationWindowClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->get_property = luma_application_window_get_property;
  object_class->set_property = luma_application_window_set_property;
  object_class->constructed = luma_application_window_constructed;
  object_class->dispose = luma_application_window_dispose;
  properties[PROP_CONTEXT] = g_param_spec_object(
      "context", NULL, NULL, LUMA_TYPE_CONTEXT,
      G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, N_PROPERTIES, properties);
}

static void prepare_visible_dialog(LumaApplicationWindow *self) {
  AdwDialog *dialog = adw_application_window_get_visible_dialog(ADW_APPLICATION_WINDOW(self));
  if (ADW_IS_ALERT_DIALOG(dialog))
    gtk_widget_add_css_class(GTK_WIDGET(dialog), "luma-controls-quiet");
}

/* The name the shell shows: the desktop entry's, then the process's. */
static char *identity_name(GtkApplication *application, GtkWindow *window) {
  const char *app_id = application != NULL ?
      g_application_get_application_id(G_APPLICATION(application)) : NULL;
  if (app_id != NULL && *app_id != '\0') {
    g_autofree char *desktop_id = g_strconcat(app_id, ".desktop", NULL);
    g_autoptr(GDesktopAppInfo) info = g_desktop_app_info_new(desktop_id);
    const char *name = info != NULL ? g_app_info_get_display_name(G_APP_INFO(info)) : NULL;
    if (name != NULL && *name != '\0')
      return g_strdup(name);
  }
  /* GLib answers the program name when no name was set, which is never
   * what the corner should say while the window has a title. */
  const char *named = g_get_application_name();
  const char *candidates[] = {g_strcmp0(named, g_get_prgname()) != 0 ? named : NULL,
                              gtk_window_get_title(window), g_get_prgname()};
  for (guint i = 0; i < G_N_ELEMENTS(candidates); i++)
    if (candidates[i] != NULL && *candidates[i] != '\0')
      return g_strdup(candidates[i]);
  return g_strdup("Application");
}

static gboolean theme_has(GtkWindow *window, const char *name) {
  return name != NULL && *name != '\0' &&
         gtk_icon_theme_has_icon(gtk_icon_theme_get_for_display(
                                     gtk_widget_get_display(GTK_WIDGET(window))),
                                 name);
}

/* The registered icon, in the order the shell resolves it. */
static GtkWidget *identity_icon(GtkApplication *application, GtkWindow *window) {
  const char *app_id = application != NULL ?
      g_application_get_application_id(G_APPLICATION(application)) : NULL;
  GtkWidget *icon = NULL;
  if (theme_has(window, gtk_window_get_icon_name(window)))
    icon = gtk_image_new_from_icon_name(gtk_window_get_icon_name(window));
  if (icon == NULL && theme_has(window, gtk_window_get_default_icon_name()))
    icon = gtk_image_new_from_icon_name(gtk_window_get_default_icon_name());
  if (icon == NULL && app_id != NULL && *app_id != '\0') {
    g_autofree char *desktop_id = g_strconcat(app_id, ".desktop", NULL);
    g_autoptr(GDesktopAppInfo) info = g_desktop_app_info_new(desktop_id);
    GIcon *gicon = info != NULL ? g_app_info_get_icon(G_APP_INFO(info)) : NULL;
    if (G_IS_THEMED_ICON(gicon)) {
      const char *const *names = g_themed_icon_get_names(G_THEMED_ICON(gicon));
      for (guint i = 0; names != NULL && names[i] != NULL && icon == NULL; i++)
        if (theme_has(window, names[i]))
          icon = gtk_image_new_from_icon_name(names[i]);
    } else if (gicon != NULL) {
      icon = gtk_image_new_from_gicon(gicon);
    }
  }
  if (icon == NULL && theme_has(window, app_id))
    icon = gtk_image_new_from_icon_name(app_id);
  if (icon == NULL)
    icon = gtk_image_new_from_icon_name("application-x-executable");
  gtk_image_set_pixel_size(GTK_IMAGE(icon), 22); /* lumaui.window.identity_icon */
  return icon;
}

/* The identity pill, the same parts the Python kit's WindowIdentity has.
 * `luma-identity-button` is the class the patched libadwaita looks for before
 * building one of its own. */
static GtkWidget *identity_new(GtkApplication *application, GtkWindow *window,
                               GMenuModel *menu) {
  g_autofree char *name = identity_name(application, window);
  /* v70 .tbar: icon 22, name, chevron 14, 9 apart (lumaui.window tokens). */
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 9);
  gtk_widget_add_css_class(content, "luma-identity-content");
  gtk_widget_set_valign(content, GTK_ALIGN_CENTER);
  GtkWidget *tile = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(tile, "luma-identity-icon");
  gtk_widget_set_name(tile, "lumaui-identity-icon");
  gtk_widget_set_overflow(tile, GTK_OVERFLOW_HIDDEN);
  gtk_widget_set_valign(tile, GTK_ALIGN_CENTER);
  gtk_widget_set_size_request(tile, 22, 22);
  gtk_box_append(GTK_BOX(tile), identity_icon(application, window));
  gtk_box_append(GTK_BOX(content), tile);
  GtkWidget *label = gtk_label_new(name);
  gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
  gtk_label_set_max_width_chars(GTK_LABEL(label), 24);
  gtk_widget_add_css_class(label, "luma-identity-label");
  gtk_box_append(GTK_BOX(content), label);
  GtkWidget *chevron = gtk_image_new_from_icon_name("lumaui-chevron-down-symbolic");
  gtk_image_set_pixel_size(GTK_IMAGE(chevron), 14);
  gtk_widget_add_css_class(chevron, "luma-identity-chevron");
  gtk_box_append(GTK_BOX(content), chevron);

  GtkWidget *button = gtk_menu_button_new();
  gtk_widget_add_css_class(button, "luma-identity-button");
  gtk_widget_add_css_class(button, "luma-automatic-identity");
  gtk_widget_set_name(button, "lumaui-identity");
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  gtk_widget_set_tooltip_text(button, name);
  g_autofree char *spoken = g_strdup_printf("%s menu", name);
  gtk_accessible_update_property(GTK_ACCESSIBLE(button),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL, spoken, -1);
  gtk_menu_button_set_child(GTK_MENU_BUTTON(button), content);
  g_autoptr(GMenuModel) updated_menu=luma_native_app_updates_menu(application,menu);
  gtk_menu_button_set_menu_model(GTK_MENU_BUTTON(button), updated_menu);
  return button;
}

/* v70's window controls (.wctl), the same as the Python kit's WindowControls:
 * Lucide minus, maximize-2 and x in the order the desktop's decoration layout
 * gives (Close always), running GTK's own window actions, reachable with Tab.
 * The header bar's own title buttons are off, so no libadwaita draws a second set. */
/* On a phone the controls fold away at phone width (v70 .win.phone .wctl); a desktop keeps them
 * however narrow a window is tiled (Nick, 26 Sep). */
static void controls_width_changed(GtkWidget *window G_GNUC_UNUSED, int width, gpointer data) {
  gtk_widget_set_visible(GTK_WIDGET(data), width <= 0 || width > LUMA_UI_WINDOW_CONTROLS_HIDE_MAX_WIDTH);
}

static GtkWidget *window_controls_new(GtkWidget *widget) {
  static const struct { const char *name, *glyph, *label, *action; } buttons[] = {
    {"minimize", "lumaui-minus-symbolic", "Minimize", "window.minimize"},
    {"maximize", "lumaui-maximize-2-symbolic", "Zoom", "window.toggle-maximized"},
    {"close", "lumaui-x-symbolic", "Close", "window.close"},
  };
  g_autofree char *layout = NULL;
  g_object_get(gtk_settings_get_for_display(gtk_widget_get_display(widget)),
               "gtk-decoration-layout", &layout, NULL);
  const char *end = layout != NULL && strchr(layout, ':') != NULL ? strchr(layout, ':') + 1
                    : (layout != NULL ? layout : "minimize,maximize,close");
  g_auto(GStrv) parts = g_strsplit(end, ",", -1);
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0); /* 2 px is the sheet's border-spacing */
  gtk_widget_set_name(box, "lumaui-window-controls");
  gtk_widget_add_css_class(box, "lumaui-window-controls");
  gtk_widget_set_valign(box, GTK_ALIGN_CENTER);
  gboolean has_close = FALSE;
  for (guint i = 0; parts[i] != NULL; i++) {
    g_strstrip(parts[i]);
    for (guint j = 0; j < G_N_ELEMENTS(buttons); j++) {
      if (g_strcmp0(parts[i], buttons[j].name) != 0)
        continue;
      GtkWidget *button = gtk_button_new_from_icon_name(buttons[j].glyph);
      g_autofree char *id = g_strconcat("lumaui-window-", buttons[j].name, NULL);
      gtk_widget_set_name(button, id);
      gtk_widget_add_css_class(button, "lumaui-window-control");
      gtk_widget_add_css_class(button, buttons[j].name);
      gtk_widget_set_focus_on_click(button, FALSE);
      gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
      gtk_actionable_set_action_name(GTK_ACTIONABLE(button), buttons[j].action);
      gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_LABEL,
                                     buttons[j].label, -1);
      gtk_box_append(GTK_BOX(box), button);
      has_close = has_close || j == 2;
    }
  }
  if (!has_close) {
    GtkWidget *button = gtk_button_new_from_icon_name(buttons[2].glyph);
    gtk_widget_set_name(button, "lumaui-window-close");
    gtk_widget_add_css_class(button, "lumaui-window-control");
    gtk_widget_add_css_class(button, "close");
    gtk_widget_set_focus_on_click(button, FALSE);
    gtk_actionable_set_action_name(GTK_ACTIONABLE(button), buttons[2].action);
    gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_LABEL,
                                   buttons[2].label, -1);
    gtk_box_append(GTK_BOX(box), button);
  }
  if (luma_ui_mobile_form_factor())
    luma_ui_width_watch(widget, controls_width_changed, box, LUMA_UI_WINDOW_CONTROLS_HIDE_MAX_WIDTH);
  return box;
}

static GtkWidget *find_adw_widget(GtkWidget *root, GType type) {
  if (G_TYPE_CHECK_INSTANCE_TYPE(root, type)) return root;
  for (GtkWidget *child = gtk_widget_get_first_child(root); child != NULL;
       child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = find_adw_widget(child, type);
    if (found != NULL) return found;
  }
  return NULL;
}

/* AdwToolbarView has no public getter for individual bars. Its direct top-bar
 * wrapper is marked with libadwaita's CSS class. Search inside that wrapper
 * only: Nautilus's content has a nested bottom action header and GTK puts the
 * content before the top wrapper in child order. */
static GtkWidget *find_outer_top_header(GtkWidget *toolbar) {
  for (GtkWidget *child = gtk_widget_get_first_child(toolbar); child != NULL;
       child = gtk_widget_get_next_sibling(child))
    if (gtk_widget_has_css_class(child, "top-bar"))
      return find_adw_widget(child, ADW_TYPE_HEADER_BAR);
  return NULL;
}

GtkWidget *luma_application_window_frame_adopt(AdwApplicationWindow *window) {
  g_return_val_if_fail(ADW_IS_APPLICATION_WINDOW(window), NULL);
  GtkWidget *previous = g_object_get_data(G_OBJECT(window), "luma-adopted-titlebar");
  if (previous != NULL) return previous;

  GtkWidget *content = adw_application_window_get_content(window);
  GtkWidget *toolbar = content != NULL ? find_adw_widget(content, ADW_TYPE_TOOLBAR_VIEW) : NULL;
  GtkWidget *body = toolbar != NULL ? adw_toolbar_view_get_content(ADW_TOOLBAR_VIEW(toolbar)) : NULL;
  GtkWidget *titlebar = toolbar != NULL ? find_outer_top_header(toolbar) : NULL;
  if (titlebar == NULL && toolbar != NULL) {
    titlebar = adw_header_bar_new();
    adw_toolbar_view_add_top_bar(ADW_TOOLBAR_VIEW(toolbar), titlebar);
  } else if (titlebar == NULL) {
    /* Preserve the caller's content object and all of its signal bindings. */
    if (content != NULL) {
      g_object_ref(content);
      adw_application_window_set_content(window, NULL);
    }
    titlebar = adw_header_bar_new();
    GtkWidget *toolbar = adw_toolbar_view_new();
    adw_toolbar_view_add_top_bar(ADW_TOOLBAR_VIEW(toolbar), titlebar);
    body = adw_bin_new();
    if (content != NULL) {
      adw_bin_set_child(ADW_BIN(body), content);
      g_object_unref(content);
    }
    adw_toolbar_view_set_content(ADW_TOOLBAR_VIEW(toolbar), body);
    adw_application_window_set_content(window, toolbar);
  }

  gtk_widget_add_css_class(GTK_WIDGET(window), "luma-app-window");
  gtk_widget_add_css_class(GTK_WIDGET(window), "luma-application-window");
  gtk_widget_add_css_class(GTK_WIDGET(window), "luma-native-window");
  gtk_widget_add_css_class(GTK_WIDGET(window), "luma-adw-native-window");
  gtk_widget_add_css_class(titlebar, "luma-titlebar");
  gtk_widget_set_name(titlebar, "lumaui-title-row");
  if (body != NULL) {
    gtk_widget_add_css_class(body, "luma-window-body");
    gtk_widget_add_css_class(body, "luma-native-work-surface");
  }
  adw_header_bar_set_show_title(ADW_HEADER_BAR(titlebar), FALSE);
  adw_header_bar_set_show_start_title_buttons(ADW_HEADER_BAR(titlebar), FALSE);
  adw_header_bar_set_show_end_title_buttons(ADW_HEADER_BAR(titlebar), FALSE);
  GtkApplication *application = gtk_window_get_application(GTK_WINDOW(window));
  GMenuModel *menu = application != NULL ? gtk_application_get_menubar(application) : NULL;
  GtkWidget *identity = identity_new(application, GTK_WINDOW(window), menu);
  adw_header_bar_pack_start(ADW_HEADER_BAR(titlebar), identity);
  adw_header_bar_pack_end(ADW_HEADER_BAR(titlebar), window_controls_new(GTK_WIDGET(window)));
  if (luma_ui_mobile_form_factor()) {
    gtk_widget_add_css_class(GTK_WIDGET(window), "lumaui-phone-device");
    gtk_window_set_decorated(GTK_WINDOW(window), FALSE);
    gtk_widget_set_visible(titlebar, FALSE);
    gtk_widget_set_can_target(titlebar, FALSE);
  }
  g_object_set_data(G_OBJECT(window), "luma-adopted-titlebar", titlebar);
  g_object_set_data(G_OBJECT(window), "luma-adopted-identity", identity);
  return titlebar;
}

void luma_application_window_frame_set_menu_model(AdwApplicationWindow *window,
                                                  GMenuModel *menu_model) {
  g_return_if_fail(ADW_IS_APPLICATION_WINDOW(window));
  g_return_if_fail(menu_model == NULL || G_IS_MENU_MODEL(menu_model));
  luma_application_window_frame_adopt(window);
  GtkWidget *identity = g_object_get_data(G_OBJECT(window), "luma-adopted-identity");
  g_autoptr(GMenuModel) updated_menu=luma_native_app_updates_menu(gtk_window_get_application(GTK_WINDOW(window)),menu_model);
  gtk_menu_button_set_menu_model(GTK_MENU_BUTTON(identity), updated_menu);
}

void luma_application_window_frame_set_leading(AdwApplicationWindow *window,
                                               GtkWidget *widget) {
  g_return_if_fail(ADW_IS_APPLICATION_WINDOW(window));
  g_return_if_fail(GTK_IS_WIDGET(widget));
  GtkWidget *titlebar = luma_application_window_frame_adopt(window);
  g_return_if_fail(gtk_widget_get_parent(widget) == NULL ||
                   gtk_widget_is_ancestor(widget, titlebar));
  gboolean held = gtk_widget_get_parent(widget) != NULL;
  if (held) {
    g_object_ref(widget);
    adw_header_bar_remove(ADW_HEADER_BAR(titlebar), widget);
  }
  adw_header_bar_pack_start(ADW_HEADER_BAR(titlebar), widget);
  if (held) g_object_unref(widget);
}

typedef struct {
  GtkWidget *titlebar;
  GtkWidget *leading;
  GtkWidget *phone_host;
} LumaAdoptedLeading;

static void adopted_leading_width_changed(GtkWidget *window, int width, gpointer data) {
  (void)window;
  LumaAdoptedLeading *state = data;
  GtkWidget *parent = gtk_widget_get_parent(state->leading);
  if (width <= LUMA_UI_PHONE_MAX_WIDTH &&
      gtk_widget_is_ancestor(state->leading, state->titlebar)) {
    g_object_ref(state->leading);
    adw_header_bar_remove(ADW_HEADER_BAR(state->titlebar), state->leading);
    gtk_box_append(GTK_BOX(state->phone_host), state->leading);
    g_object_unref(state->leading);
  } else if (width > LUMA_UI_PHONE_MAX_WIDTH && parent == state->phone_host) {
    g_object_ref(state->leading);
    gtk_box_remove(GTK_BOX(state->phone_host), state->leading);
    adw_header_bar_pack_start(ADW_HEADER_BAR(state->titlebar), state->leading);
    g_object_unref(state->leading);
  }
  gtk_widget_set_visible(state->phone_host, width <= LUMA_UI_PHONE_MAX_WIDTH);
}

void luma_application_window_frame_set_leading_phone_host(AdwApplicationWindow *window,
                                                           GtkWidget *widget,
                                                           GtkWidget *phone_host) {
  g_return_if_fail(ADW_IS_APPLICATION_WINDOW(window));
  g_return_if_fail(GTK_IS_WIDGET(widget));
  g_return_if_fail(GTK_IS_BOX(phone_host));
  luma_application_window_frame_set_leading(window, widget);
  LumaAdoptedLeading *state = g_new0(LumaAdoptedLeading, 1);
  state->titlebar = luma_application_window_frame_adopt(window);
  state->leading = widget;
  state->phone_host = phone_host;
  gtk_widget_set_visible(phone_host, FALSE);
  g_object_set_data_full(G_OBJECT(window), "luma-adopted-leading-phone-host", state, g_free);
  luma_ui_width_watch(GTK_WIDGET(window), adopted_leading_width_changed, state,
                      LUMA_UI_PHONE_MAX_WIDTH);
}

static void add_identity(LumaApplicationWindow *self) {
  GtkApplication *application = gtk_window_get_application(GTK_WINDOW(self));
  if (self->identity != NULL || application == NULL)
    return;
  GMenuModel *menu = g_object_get_data(G_OBJECT(application), "luma-native-menu");
  if (menu == NULL)
    menu = gtk_application_get_menubar(application);
  self->identity = identity_new(application, GTK_WINDOW(self), menu);
  adw_header_bar_pack_start(ADW_HEADER_BAR(self->titlebar), self->identity);
}

/* The identity libadwaita builds for a title row that had none when the application arrived (an app
 * that sets its application after construction) draws its icon at 20; v70's is 22, as the kit's own.
 * Its images are brought to the kit's size when the window maps. */
static void identity_icons_to_size(GtkWidget *node) {
  for (GtkWidget *child = gtk_widget_get_first_child(node); child != NULL; child = gtk_widget_get_next_sibling(child)) {
    if (GTK_IS_IMAGE(child) && gtk_widget_has_css_class(node, "luma-identity-icon"))
      gtk_image_set_pixel_size(GTK_IMAGE(child), LUMA_UI_WINDOW_IDENTITY_ICON);
    identity_icons_to_size(child);
  }
}

static void window_mapped(GtkWidget *widget, gpointer data G_GNUC_UNUSED) {
  LumaApplicationWindow *self = LUMA_APPLICATION_WINDOW(widget);
  if (self->titlebar != NULL)
    identity_icons_to_size(self->titlebar);
}

static void luma_application_window_init(LumaApplicationWindow *self) {
  /* The classes LumaUI's toolkit sheet dresses: the frame on the window, the
   * work surface under the title row. The native-window pair is what the
   * patched libadwaita used to add at run time; the kit adds them itself so
   * the window is the same on any libadwaita. */
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-app-window");
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-application-window");
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-native-window");
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-adw-native-window");
  self->backdrop = luma_surface_backdrop_new(GTK_WINDOW(self));
  g_signal_connect_swapped(self, "notify::visible-dialog", G_CALLBACK(prepare_visible_dialog), self);

  /* A real header bar: the window handle and the controls. Its own title is
   * hidden: the identity is the title. */
  self->titlebar = adw_header_bar_new();
  gtk_widget_add_css_class(self->titlebar, "luma-titlebar");
  gtk_widget_set_name(self->titlebar, "lumaui-title-row");
  adw_header_bar_set_show_title(ADW_HEADER_BAR(self->titlebar), FALSE);
  adw_header_bar_set_show_start_title_buttons(ADW_HEADER_BAR(self->titlebar), FALSE);
  adw_header_bar_set_show_end_title_buttons(ADW_HEADER_BAR(self->titlebar), FALSE);
  adw_header_bar_pack_end(ADW_HEADER_BAR(self->titlebar), window_controls_new(GTK_WIDGET(self)));
  g_signal_connect(self, "map", G_CALLBACK(window_mapped), NULL);

  self->body_bin = adw_bin_new();
  gtk_widget_add_css_class(self->body_bin, "luma-native-work-surface");
  /* The work surface owns its rounded clip, so no child (nor an island's
   * shadow) paints over the title row or past the surface's corners. */
  gtk_widget_set_overflow(self->body_bin, GTK_OVERFLOW_HIDDEN);
  self->toolbar_view = adw_toolbar_view_new();
  gtk_widget_add_css_class(self->toolbar_view, "luma-window-toolbar-view");
  adw_toolbar_view_add_top_bar(ADW_TOOLBAR_VIEW(self->toolbar_view),
                               self->titlebar);
  /* The window's LumaUI layer host sits under the title row, so a dialog,
   * drawer or toast dims the window below its title bar (as the Python
   * AppWindow's does); the title row and controls stay undimmed. */
  /* The body's gutter (.luma-window-body) is inside the layer host, so a phone's drawer spans the
   * whole window width (v70's full-width sheet at 390), not the body less its 8 a side. */
  self->body_pad = adw_bin_new();
  gtk_widget_add_css_class(self->body_pad, "luma-window-body");
  self->layer_host = luma_layer_host_new_window(self->body_pad);
  adw_bin_set_child(ADW_BIN(self->body_bin), self->layer_host);
  g_object_set_data(G_OBJECT(self), "luma-layer-host", self->layer_host);
  adw_toolbar_view_set_content(ADW_TOOLBAR_VIEW(self->toolbar_view),
                               self->body_bin);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(self),
                                     self->toolbar_view);
}

GtkWidget *luma_application_window_new(GtkApplication *application,
                                       LumaContext *context) {
  g_return_val_if_fail(GTK_IS_APPLICATION(application), NULL);
  g_return_val_if_fail(context == NULL || LUMA_IS_CONTEXT(context), NULL);
  /* The native header resolves its menu while the window is constructed.
   * Keep a mutable model alive from that point so later command registration
   * updates the same menu through GMenuModel::items-changed. */
  if (gtk_application_get_menubar(application) == NULL) {
    GMenu *menu = g_menu_new();
    gtk_application_set_menubar(application, G_MENU_MODEL(menu));
    g_object_set_data_full(G_OBJECT(application), "luma-native-menu", menu,
                          g_object_unref);
  }
  return g_object_new(LUMA_TYPE_APPLICATION_WINDOW, "application", application,
                      "context", context, NULL);
}

void luma_application_window_set_phone_bleed(LumaApplicationWindow *self, gboolean bleed) {
  g_return_if_fail(LUMA_IS_APPLICATION_WINDOW(self));
  luma_ui_set_css_class(GTK_WIDGET(self), "lumaui-bleed", bleed);
}

int luma_application_window_get_status_inset(LumaApplicationWindow *self) {
  g_return_val_if_fail(LUMA_IS_APPLICATION_WINDOW(self), 0);
  return gtk_widget_has_css_class(GTK_WIDGET(self), "lumaui-phone-device") ? LUMA_UI_PHONE_FRAME_STATUS : 0;
}

void luma_application_window_set_body(LumaApplicationWindow *self,
                                      GtkWidget *body) {
  g_return_if_fail(LUMA_IS_APPLICATION_WINDOW(self));
  g_return_if_fail(body == NULL || GTK_IS_WIDGET(body));
  self->body = body;
  adw_bin_set_child(ADW_BIN(self->body_pad), body);
}

GtkWidget *luma_application_window_get_body(LumaApplicationWindow *self) {
  g_return_val_if_fail(LUMA_IS_APPLICATION_WINDOW(self), NULL);
  return self->body;
}

void luma_application_window_set_frame(LumaApplicationWindow *self, GtkWidget *sidebar, GtkWidget *island) {
  g_return_if_fail(LUMA_IS_APPLICATION_WINDOW(self));
  g_return_if_fail(sidebar == NULL || LUMA_IS_NAVIGATION_SIDEBAR(sidebar));
  g_return_if_fail(LUMA_IS_ISLAND(island));
  GtkWidget *frame = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  gtk_widget_set_hexpand(frame, TRUE);
  gtk_widget_set_vexpand(frame, TRUE);
  if (sidebar != NULL) gtk_box_append(GTK_BOX(frame), sidebar);
  gtk_box_append(GTK_BOX(frame), island);
  luma_application_window_set_body(self, frame);
}

static void repack_start_slots(LumaApplicationWindow *self) {
  GtkWidget *leading = self->title_leading;
  GtkWidget *content = self->title_centred ? NULL : self->title_content;
  gboolean leading_held = leading != NULL && gtk_widget_get_parent(leading) != NULL;
  gboolean content_held = content != NULL && gtk_widget_get_parent(content) != NULL;
  if (leading_held) {
    g_object_ref(leading);
    adw_header_bar_remove(ADW_HEADER_BAR(self->titlebar), leading);
  }
  if (content_held) {
    g_object_ref(content);
    adw_header_bar_remove(ADW_HEADER_BAR(self->titlebar), content);
  }
  if (leading != NULL) adw_header_bar_pack_start(ADW_HEADER_BAR(self->titlebar), leading);
  if (content != NULL) adw_header_bar_pack_start(ADW_HEADER_BAR(self->titlebar), content);
  if (leading_held) g_object_unref(leading);
  if (content_held) g_object_unref(content);
}

static void swap_title_slot(LumaApplicationWindow *self, GtkWidget **slot, GtkWidget *widget, gboolean at_start) {
  g_return_if_fail(widget == NULL || GTK_IS_WIDGET(widget));
  if (*slot == widget) return;
  if (*slot != NULL) adw_header_bar_remove(ADW_HEADER_BAR(self->titlebar), *slot);
  *slot = widget;
  if (widget != NULL) {
    if (!at_start) adw_header_bar_pack_end(ADW_HEADER_BAR(self->titlebar), widget);
  }
  if (at_start) repack_start_slots(self);
}

void luma_application_window_set_leading(LumaApplicationWindow *self, GtkWidget *widget) {
  g_return_if_fail(LUMA_IS_APPLICATION_WINDOW(self));
  swap_title_slot(self, &self->title_leading, widget, TRUE);
}

void luma_application_window_set_title_content(LumaApplicationWindow *self, GtkWidget *widget, gboolean centred) {
  g_return_if_fail(LUMA_IS_APPLICATION_WINDOW(self));
  g_return_if_fail(widget == NULL || GTK_IS_WIDGET(widget));
  if (self->title_centred && self->title_content != NULL) {
    adw_header_bar_set_title_widget(ADW_HEADER_BAR(self->titlebar), NULL);
    adw_header_bar_set_show_title(ADW_HEADER_BAR(self->titlebar), FALSE);
    self->title_content = NULL;
  }
  self->title_centred = centred;
  if (centred) {
    swap_title_slot(self, &self->title_content, NULL, TRUE);
    if (widget != NULL) {
      adw_header_bar_set_title_widget(ADW_HEADER_BAR(self->titlebar), widget);
      adw_header_bar_set_show_title(ADW_HEADER_BAR(self->titlebar), TRUE);
      self->title_content = widget;
    }
  } else {
    swap_title_slot(self, &self->title_content, widget, TRUE);
  }
}

void luma_application_window_set_trailing(LumaApplicationWindow *self, GtkWidget *widget) {
  g_return_if_fail(LUMA_IS_APPLICATION_WINDOW(self));
  swap_title_slot(self, &self->title_trailing, widget, FALSE);
}

GtkWidget *luma_application_window_get_layer_host(LumaApplicationWindow *self) {
  g_return_val_if_fail(LUMA_IS_APPLICATION_WINDOW(self), NULL);
  return self->layer_host;
}

GtkWidget *luma_application_window_get_title_bar(LumaApplicationWindow *self) {
  g_return_val_if_fail(LUMA_IS_APPLICATION_WINDOW(self), NULL);
  return self->titlebar;
}

void luma_application_window_set_identity(LumaApplicationWindow *self,
                                          const char *title,
                                          const char *subtitle,
                                          const char *icon_name) {
  g_return_if_fail(LUMA_IS_APPLICATION_WINDOW(self));
  /* The identity's name and icon come from the desktop entry, resolved by the
   * toolkit; an application cannot put a different name in its own corner
   * than the shell shows in the dock. The window title is what the window
   * holds, and the subtitle — the open document — belongs there too. */
  (void)icon_name;
  if (subtitle != NULL && *subtitle != '\0') {
    g_autofree char *full = g_strdup_printf("%s — %s", subtitle, title != NULL ? title : "");
    gtk_window_set_title(GTK_WINDOW(self), full);
  } else {
    gtk_window_set_title(GTK_WINDOW(self), title != NULL ? title : "");
  }
}

void luma_application_window_set_menu_model(LumaApplicationWindow *self,
                                            GMenuModel *menu_model) {
  g_return_if_fail(LUMA_IS_APPLICATION_WINDOW(self));
  g_return_if_fail(menu_model == NULL || G_IS_MENU_MODEL(menu_model));
  /* The identity shows the application's stable menu, so that is where the
   * model goes; a window made without luma_application_window_new() points
   * its identity at the new model directly. */
  GtkApplication *application = gtk_window_get_application(GTK_WINDOW(self));
  if (application != NULL) {
    GMenu *stable = g_object_get_data(G_OBJECT(application), "luma-native-menu");
    if (stable != NULL) {
      if (menu_model == G_MENU_MODEL(stable)) return;
      g_menu_remove_all(stable);
      if (menu_model != NULL) {
        for (int i = 0; i < g_menu_model_get_n_items(menu_model); i++) {
          GMenuItem *item = g_menu_item_new_from_model(menu_model, i);
          g_menu_append_item(stable, item);
          g_object_unref(item);
        }
      }
    } else {
      gtk_application_set_menubar(application, menu_model);
      if (self->identity != NULL)
        {
          g_autoptr(GMenuModel) updated_menu=luma_native_app_updates_menu(application,menu_model);
          gtk_menu_button_set_menu_model(GTK_MENU_BUTTON(self->identity),updated_menu);
        }
    }
  }
}
