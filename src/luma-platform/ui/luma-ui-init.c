/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-appearance.h"

#include <adwaita.h>

/* The token provider is kept so the treatment can be swapped while the
 * application runs. A window that keeps whichever treatment happened to be set
 * at startup reads as a bug the moment the desktop switches to dark. */
static GtkCssProvider *luma_token_provider = NULL;
static LumaSurfacePolicy *luma_surface_policy = NULL;
static GtkCssProvider *luma_legacy_provider = NULL;
/* LumaUI's own window and toolkit sheets (ADR-052): the frame, title row,
 * identity, controls, gutter, islands, menus, materials and libadwaita's
 * colour names, which the patched libadwaita used to carry. */
static GtkCssProvider *luma_toolkit_palette_provider = NULL;
static GtkCssProvider *luma_toolkit_provider = NULL;
static GtkCssProvider *luma_media_provider = NULL;
static GtkCssProvider *luma_bar_provider = NULL;
static GtkCssProvider *luma_rows_provider = NULL;
/* libadwaita computes its default (blue) accent in C, so the palette alone
 * cannot restate it: this names Luma's blue as the accent only while the
 * accent is the default, and a person's own accent choice still wins. */
static GtkCssProvider *luma_toolkit_accent_provider = NULL;
/* Frost and Glass restate the kit's colours above the theme (SETTINGS), where
 * Luma's patched libadwaita used to, so a translucent window is one material
 * whatever built it. The values are lumaui-palette.css's. */
static GtkCssProvider *luma_material_provider = NULL;

/* What colour a window is painted is one decision for the whole process, and
 * every toplevel carries it as `luma-treatment-<name>`, kit window or not (it
 * used to be Luma's patched libadwaita that made it, patch 0042). */
static const char *luma_treatment = NULL;
static GListModel *luma_toplevels = NULL;

static void luma_apply_treatment(void) {
  static const char *const treatments[] = {"light", "dark", "frost", "glass"};
  if (luma_treatment == NULL || luma_toplevels == NULL)
    return;
  for (guint i = 0; i < g_list_model_get_n_items(luma_toplevels); i++) {
    g_autoptr(GtkWidget) window = g_list_model_get_item(luma_toplevels, i);
    for (guint t = 0; t < G_N_ELEMENTS(treatments); t++) {
      g_autofree char *css_class = g_strconcat("luma-treatment-", treatments[t], NULL);
      if (g_str_equal(treatments[t], luma_treatment))
        gtk_widget_add_css_class(window, css_class);
      else
        gtk_widget_remove_css_class(window, css_class);
    }
  }
}

static void luma_toplevels_changed(GListModel *model G_GNUC_UNUSED, guint position G_GNUC_UNUSED,
                                   guint removed G_GNUC_UNUSED, guint added G_GNUC_UNUSED,
                                   gpointer data G_GNUC_UNUSED) {
  luma_apply_treatment();
}

static void luma_set_material(const char *treatment) {
  static const char *const roles[] = {
    "window", "content", "island", "chrome", "chrome_secondary", "menu",
    "ink", "muted", "faint", "line", "fill", "hover", "selected",
  };
  g_autoptr(GString) sheet = g_string_new(NULL);
  if (luma_material_provider == NULL)
    return;
  if (g_str_equal(treatment, "frost") || g_str_equal(treatment, "glass"))
    for (guint i = 0; i < G_N_ELEMENTS(roles); i++)
      g_string_append_printf(sheet, "@define-color luma_%s @lumaui_%s_%s;",
                             roles[i], treatment, roles[i]);
  gtk_css_provider_load_from_string(luma_material_provider, sheet->str);
}

/* Sheets an application registers through luma_ui_add_style_resource(). A
 * sheet that names an AppKit colour — @luma_ink, @luma_content — takes the
 * value current when it is parsed, so it is parsed again whenever the tokens
 * change; otherwise a sheet loaded before the desktop's preference arrived
 * keeps light colours inside a dark window. */
typedef struct {
  GtkCssProvider *provider;
  char *resource;
} LumaStyleResource;

static GPtrArray *luma_style_resources = NULL;
static const char luma_backdrop_data_key[] = "luma-ui-surface-backdrop";

static void
luma_bind_window_backdrop (GtkApplication *application G_GNUC_UNUSED,
                           GtkWindow      *window,
                           gpointer        data G_GNUC_UNUSED)
{
  LumaSurfaceBackdrop *backdrop;

  if (g_object_get_data (G_OBJECT (window), luma_backdrop_data_key) != NULL)
    return;

  backdrop = luma_surface_backdrop_new (window);
  g_object_set_data_full (G_OBJECT (window), luma_backdrop_data_key,
                          backdrop, g_object_unref);
}

static void
luma_bind_application_windows (void)
{
  GApplication *application = g_application_get_default ();

  if (!GTK_IS_APPLICATION (application))
    return;

  g_signal_connect (application, "window-added",
                    G_CALLBACK (luma_bind_window_backdrop), NULL);
  for (GList *item = gtk_application_get_windows (GTK_APPLICATION (application));
       item != NULL; item = item->next)
    luma_bind_window_backdrop (GTK_APPLICATION (application), item->data, NULL);
}

static void luma_reload_style_resources(void) {
  guint index;

  if (luma_style_resources == NULL)
    return;
  for (index = 0; index < luma_style_resources->len; index++) {
    LumaStyleResource *sheet = g_ptr_array_index(luma_style_resources, index);
    gtk_css_provider_load_from_resource(sheet->provider, sheet->resource);
  }
}

static gboolean luma_prefers_dark(void) {
  AdwStyleManager *manager;

  /* GNOME does not set gtk-application-prefer-dark-theme; there is no such
   * key. The desktop publishes its choice as the portal's
   * org.freedesktop.appearance colour-scheme, which is what AdwStyleManager
   * reads. Asking GtkSettings instead reports light on a dark desktop. */
  if (!adw_is_initialized())
    adw_init();

  manager = adw_style_manager_get_default();
  if (manager != NULL && adw_style_manager_get_dark(manager))
    return TRUE;

  /* The portal answers for the treatment, but it is not always there — it can
   * fail to start, and it is absent from a bare session. Falling through to
   * light on a dark desktop is the worst possible answer, so the desktop's own
   * setting is asked directly before believing it. */
  {
    GSettingsSchemaSource *source = g_settings_schema_source_get_default();
    GSettingsSchema *schema =
        source != NULL ? g_settings_schema_source_lookup(
                             source, "org.gnome.desktop.interface", TRUE)
                       : NULL;

    if (schema != NULL) {
      gboolean dark = FALSE;

      if (g_settings_schema_has_key(schema, "color-scheme")) {
        GSettings *interface = g_settings_new("org.gnome.desktop.interface");
        g_autofree char *scheme =
            g_settings_get_string(interface, "color-scheme");
        dark = g_strcmp0(scheme, "prefer-dark") == 0;
        g_object_unref(interface);
      }
      g_settings_schema_unref(schema);
      return dark;
    }
  }
  return FALSE;
}

/* GTK 4.20 lets each provider say which colour scheme and contrast its @media
 * queries see, and libadwaita sets its own from the style manager rather than
 * from GtkSettings. The toolkit sheets follow the same answer, or their dark
 * and high-contrast rules would stay light beside libadwaita's. */
static void luma_toolkit_follow_scheme(void) {
#if GTK_CHECK_VERSION(4, 20, 0)
  AdwStyleManager *manager = adw_style_manager_get_default();
  GtkInterfaceColorScheme scheme = adw_style_manager_get_dark(manager)
      ? GTK_INTERFACE_COLOR_SCHEME_DARK : GTK_INTERFACE_COLOR_SCHEME_LIGHT;
  GtkInterfaceContrast contrast = adw_style_manager_get_high_contrast(manager)
      ? GTK_INTERFACE_CONTRAST_MORE : GTK_INTERFACE_CONTRAST_NO_PREFERENCE;
  GtkCssProvider *providers[] = {luma_toolkit_palette_provider, luma_toolkit_provider,
                                 luma_media_provider, luma_rows_provider};
  for (guint i = 0; i < G_N_ELEMENTS(providers); i++)
    if (providers[i] != NULL)
      g_object_set(providers[i], "prefers-color-scheme", scheme,
                   "prefers-contrast", contrast, NULL);
#endif
}

static void luma_toolkit_follow_accent(void) {
  if (luma_toolkit_accent_provider == NULL)
    return;
  AdwStyleManager *manager = adw_style_manager_get_default();
  gtk_css_provider_load_from_string(
      luma_toolkit_accent_provider,
      adw_style_manager_get_accent_color(manager) == ADW_ACCENT_COLOR_BLUE
          ? "@define-color accent_bg_color @lumaui_accent_blue;" : "");
}

static void luma_accent_changed(GObject *object G_GNUC_UNUSED,
                                GParamSpec *pspec G_GNUC_UNUSED,
                                gpointer data G_GNUC_UNUSED) {
  luma_toolkit_follow_accent();
}

static void luma_load_tokens(void) {
  static gboolean loading = FALSE;
  if (loading || luma_token_provider == NULL)
    return;
  loading = TRUE;
  AdwStyleManager *manager = adw_style_manager_get_default();
  const gboolean high_contrast = adw_style_manager_get_high_contrast(manager) ||
      (luma_surface_policy && luma_surface_policy_get_locked(luma_surface_policy));
  gboolean chosen = luma_surface_policy && luma_surface_policy_get_has_selection(luma_surface_policy);
  const char *effective = luma_surface_policy != NULL ?
      luma_surface_policy_get_effective(luma_surface_policy) : "light";
  gboolean dark = chosen && g_str_equal(effective, "dark");
  gboolean frost = chosen && g_str_equal(effective, "frost");
  gboolean glass = chosen && g_str_equal(effective, "glass");
  adw_style_manager_set_color_scheme(manager, !chosen || high_contrast ? ADW_COLOR_SCHEME_DEFAULT :
      dark ? ADW_COLOR_SCHEME_FORCE_DARK : ADW_COLOR_SCHEME_FORCE_LIGHT);
  if (!chosen || high_contrast)
    dark = luma_prefers_dark();
  luma_set_material(high_contrast ? "high-contrast" : frost ? "frost" : glass ? "glass" : "");
  luma_treatment = !high_contrast && frost ? "frost" : !high_contrast && glass ? "glass"
                   : dark ? "dark" : "light";
  if (luma_toplevels == NULL) {
    luma_toplevels = gtk_window_get_toplevels();
    g_signal_connect(luma_toplevels, "items-changed", G_CALLBACK(luma_toplevels_changed), NULL);
  }
  luma_apply_treatment();

  /* Applications name the AppKit's colours, so those are the ones the library
   * has to define. The Luma themes ship gtk-3.0 directories only, which leaves
   * a GTK 4 application with no other source for them. */
  gtk_css_provider_load_from_resource(
      luma_token_provider,
      high_contrast ? "/org/projectluma/platform/luma-appkit-high-contrast-tokens.css" :
      frost ? "/org/projectluma/platform/luma-appkit-frost-tokens.css" :
      glass ? "/org/projectluma/platform/luma-appkit-glass-tokens.css" :
      dark ? "/org/projectluma/platform/luma-appkit-dark-tokens.css"
           : "/org/projectluma/platform/luma-appkit-tokens.css");

  /* The older surface names this library styles itself. They overlap the
   * AppKit set — luma_ink is in both — so loading a fixed light copy after the
   * AppKit sheet would put light ink back on dark chrome, which is exactly the
   * unreadable result this replaced. It follows the treatment as well. */
  if (luma_legacy_provider != NULL)
    gtk_css_provider_load_from_resource(
        luma_legacy_provider,
        high_contrast ? "/org/projectluma/platform/luma-appkit-high-contrast-tokens.css" :
        frost ? "/org/projectluma/platform/luma-appkit-frost-tokens.css" :
        glass ? "/org/projectluma/platform/luma-appkit-glass-tokens.css" :
        dark ? "/org/projectluma/platform/luma-tokens-dark.css"
             : "/org/projectluma/platform/luma-tokens.css");

  /* The toolkit sheet names the kit's colours, which a sheet takes when it
   * is parsed: parse it again for the new treatment. */
  if (luma_toolkit_provider != NULL)
    gtk_css_provider_load_from_resource(
        luma_toolkit_provider, "/org/projectluma/platform/lumaui-toolkit.css");
  if (luma_media_provider != NULL)
    gtk_css_provider_load_from_resource(
        luma_media_provider, "/org/projectluma/platform/luma-appkit-media.css");
  if (luma_bar_provider != NULL)
    gtk_css_provider_load_from_resource(
        luma_bar_provider, "/org/projectluma/platform/luma-appkit-bar.css");
  if (luma_rows_provider != NULL)
    gtk_css_provider_load_from_resource(
        luma_rows_provider, "/org/projectluma/platform/luma-appkit-rows.css");
  luma_toolkit_follow_scheme();

  luma_reload_style_resources();
  loading = FALSE;
}

/* Called by the native Wayland adapter when the compositor advertises (or
 * withdraws) its real background-effect capability. Unknown remains false. */
void luma_ui_set_surface_capabilities(gboolean available) {
  if (luma_surface_policy == NULL)
    return;
  luma_surface_policy_set_capabilities(
      luma_surface_policy, available, available, available);
}

void luma_ui_add_style_resource(const char *resource_path, guint priority) {
  GdkDisplay *display;
  LumaStyleResource *sheet;

  g_return_if_fail(resource_path != NULL);
  display = gdk_display_get_default();
  if (display == NULL)
    return;
  luma_init();

  sheet = g_new0(LumaStyleResource, 1);
  sheet->provider = gtk_css_provider_new();
  sheet->resource = g_strdup(resource_path);
  gtk_css_provider_load_from_resource(sheet->provider, sheet->resource);
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(sheet->provider), priority);

  if (luma_style_resources == NULL)
    luma_style_resources = g_ptr_array_new();
  g_ptr_array_add(luma_style_resources, sheet);
}

static void luma_surface_changed(LumaSurfacePolicy *policy G_GNUC_UNUSED,
                                 gpointer data G_GNUC_UNUSED) {
  luma_load_tokens();
}

static void luma_dark_changed(GObject *object G_GNUC_UNUSED,
                              GParamSpec *pspec G_GNUC_UNUSED,
                              gpointer data G_GNUC_UNUSED) {
  luma_load_tokens();
}

void luma_init(void) {
  static gsize initialized = 0;
  GdkDisplay *display;
  GtkCssProvider *provider;
  AdwStyleManager *manager;

  display = gdk_display_get_default();
  if (display == NULL)
    return;

  if (!g_once_init_enter(&initialized))
    return;

  luma_surface_policy = luma_surface_policy_new(LUMA_SURFACE_TARGET_APPLICATION);
  g_signal_connect(luma_surface_policy, "changed", G_CALLBACK(luma_surface_changed), NULL);
  luma_token_provider = gtk_css_provider_new();
  luma_legacy_provider = gtk_css_provider_new();

  /* Above whatever libadwaita the system has (THEME), below the kit's
   * components and application sheets (APPLICATION). On Luma's patched
   * libadwaita the two say the same thing and this copy decides, so nothing
   * is drawn twice; on a stock one it is what draws the Luma window. */
  luma_toolkit_palette_provider = gtk_css_provider_new();
  gtk_css_provider_load_from_resource(
      luma_toolkit_palette_provider, "/org/projectluma/platform/lumaui-palette.css");
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_toolkit_palette_provider),
      GTK_STYLE_PROVIDER_PRIORITY_THEME + 1U);
  luma_toolkit_provider = gtk_css_provider_new();
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_toolkit_provider),
      GTK_STYLE_PROVIDER_PRIORITY_THEME + 1U);
  luma_media_provider = gtk_css_provider_new();
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_media_provider),
      GTK_STYLE_PROVIDER_PRIORITY_THEME + 1U);
  luma_bar_provider = gtk_css_provider_new();
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_bar_provider),
      GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 1U);
  luma_rows_provider = gtk_css_provider_new();
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_rows_provider),
      GTK_STYLE_PROVIDER_PRIORITY_THEME + 1U);
  luma_material_provider = gtk_css_provider_new();
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_material_provider),
      GTK_STYLE_PROVIDER_PRIORITY_SETTINGS);
  luma_toolkit_accent_provider = gtk_css_provider_new();
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_toolkit_accent_provider),
      GTK_STYLE_PROVIDER_PRIORITY_THEME + 1U);
  luma_toolkit_follow_accent();

  luma_load_tokens();
  /* The older names first, then the AppKit set: at one priority the provider
   * added later wins, and where the two overlap (luma_ink: #1f2937 in the old
   * light sheet, #14161a in LumaUI) the design's value is the AppKit's, as a
   * Python LumaUI application has it. */
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_legacy_provider),
      GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(luma_token_provider),
      GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);


  /* Fable's proportions for plain GTK widgets. This is loaded at APPLICATION
   * priority, not THEME: at THEME it merely ties with Adwaita, whose selectors
   * are the more specific ones, so Adwaita won every disagreement — window
   * controls as three separate circles, buttons a third too tall, scales with
   * their own padding. Applications load their own sheets at APPLICATION + 1
   * and still have the last word. */
  provider = gtk_css_provider_new();
  gtk_css_provider_load_from_resource(
      provider, "/org/projectluma/platform/luma-appkit-base.css");
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(provider),
      GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
  g_object_unref(provider);
  /* The family sheets (rows, media) sit where Python's install_appkit puts them: at APPLICATION,
   * after the component sheet, so a family's own rules win over base's general ones as in a Python
   * app (Settings, 28 Sep: C rows kept base's ink and padding over the Settings variant's). */
  /* The token sheet also carries the generated type roles; GTK settles a property between providers
   * of one priority by the order they were added, not by specificity, so it rejoins after the component
   * sheet or base's body line height beats every role (Settings, 28 Sep: row-title 19, not 18). */
  GtkCssProvider *families[] = {luma_token_provider, luma_media_provider, luma_rows_provider};
  for (guint i = 0; i < G_N_ELEMENTS(families); i++) {
    gtk_style_context_remove_provider_for_display(display, GTK_STYLE_PROVIDER(families[i]));
    gtk_style_context_add_provider_for_display(display, GTK_STYLE_PROVIDER(families[i]),
                                               GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
  }

  provider = gtk_css_provider_new();
  gtk_css_provider_load_from_resource(provider,
                                      "/org/projectluma/platform/luma-ui.css");
  gtk_style_context_add_provider_for_display(
      display, GTK_STYLE_PROVIDER(provider),
      GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 2U);
  g_object_unref(provider);

  manager = adw_style_manager_get_default();
  if (manager != NULL) {
    g_signal_connect(manager, "notify::dark", G_CALLBACK(luma_dark_changed), NULL);
    g_signal_connect(manager, "notify::high-contrast", G_CALLBACK(luma_dark_changed), NULL);
    g_signal_connect(manager, "notify::accent-color", G_CALLBACK(luma_accent_changed), NULL);
  }

  /* One shared native binding follows every window in an application that
   * opts into luma-ui. LumaApplicationWindow already creates one explicitly;
   * luma_surface_backdrop_new() deduplicates that request. */
  luma_bind_application_windows ();

  g_once_init_leave(&initialized, 1);
}
