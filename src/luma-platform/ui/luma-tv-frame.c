/* SPDX-License-Identifier: Apache-2.0 */
/* A desktop sidebar app on a TV: the floating island (docs/developer-platform/tv-mode.md).
 *
 * A LumaApplicationWindow whose body is a frame (a LumaNavigationSidebar beside the page's
 * LumaIsland) needs no TV-specific code. In TV posture this file lays the same widgets out at TV
 * size, in TV units (one unit = min(width/1920, height/1080)):
 *
 *  - the room: the wallpaper, heavily blurred and dimmed, behind everything;
 *  - the shared top bar's place (96 in, 56 down, 74 tall): the app's name left, the clock right;
 *  - the sidebar lifted off the window edge into a floating island on the left (bar material,
 *    radius 36, rows 68 tall at 24/600, icons 30); its header and foot step aside and a section
 *    heading becomes a hairline;
 *  - the page beside it, running to the right safe margin with no maximum width, its parts
 *    (luma_ui_set_part: header, hero, group, card, row) drawn at TV size.
 *
 * The remote: while the focus is in the island, Up/Down opens each page at once; Right (or OK)
 * steps into the page and folds the island to a 100-wide rail of icons with the open page a chip;
 * Left off the page's left edge (or off a slider at its minimum) opens it again; Back in the page
 * goes up a level (a page's "back" part) or returns to the island; Back in the island minimises
 * the window, which returns to the TV home's Apps. Inside the page, Up/Down/Left/Right move the
 * focus spatially and OK acts through the widgets' own behaviour (a switch row flips, a choice
 * drops its list, a slider takes Left/Right, a field takes the focus and the shell's TV keyboard).
 *
 * Nothing here applies outside TV posture: the window's widgets, sizes and styles are restored
 * exactly when the posture ends. */
#include "luma-tv-frame-private.h"
#include "luma-ui-kit.h"
#include "luma-ui-private.h"
#include "luma-ui-tokens-private.h"
#include "luma-navigation-sidebar.h"
#include <adwaita.h>
#include <math.h>
#include <string.h>

/* ── the room ─────────────────────────────────────────────────────────────────────────────── */

#define LUMA_TYPE_TV_ROOM (luma_tv_room_get_type())
G_DECLARE_FINAL_TYPE(LumaTvRoom, luma_tv_room, LUMA, TV_ROOM, GtkWidget)

struct _LumaTvRoom {
  GtkWidget parent_instance;
  GdkTexture *texture; /* the wallpaper, small and blurred: scaled up it is the blur */
  void (*resized)(gpointer data); /* the frame: the unit follows the window */
  gpointer data;
};
G_DEFINE_FINAL_TYPE(LumaTvRoom, luma_tv_room, GTK_TYPE_WIDGET)

/* Three box blurs approximate a Gaussian; the picture is small, so this is cheap and done once. */
static void box_blur(guchar *pixels, int width, int height, int stride, int radius) {
  g_autofree guchar *row = g_malloc((gsize)MAX(width, height) * 4);
  for (int pass = 0; pass < 3; pass++) {
    for (int y = 0; y < height; y++) {
      guchar *line = pixels + (gsize)y * stride;
      for (int x = 0; x < width; x++)
        for (int c = 0; c < 3; c++) {
          int sum = 0, n = 0;
          for (int k = -radius; k <= radius; k++) {
            int xx = CLAMP(x + k, 0, width - 1);
            sum += line[xx * 4 + c];
            n++;
          }
          row[x * 4 + c] = (guchar)(sum / n);
        }
      for (int x = 0; x < width; x++)
        memcpy(line + x * 4, row + x * 4, 3);
    }
    for (int x = 0; x < width; x++) {
      for (int y = 0; y < height; y++)
        for (int c = 0; c < 3; c++) {
          int sum = 0, n = 0;
          for (int k = -radius; k <= radius; k++) {
            int yy = CLAMP(y + k, 0, height - 1);
            sum += pixels[(gsize)yy * stride + x * 4 + c];
            n++;
          }
          row[y * 4 + c] = (guchar)(sum / n);
        }
      for (int y = 0; y < height; y++)
        memcpy(pixels + (gsize)y * stride + x * 4, row + y * 4, 3);
    }
  }
}

static char *wallpaper_path(void) {
  GSettingsSchemaSource *source = g_settings_schema_source_get_default();
  g_autoptr(GSettingsSchema) schema = source != NULL
      ? g_settings_schema_source_lookup(source, "org.gnome.desktop.background", TRUE) : NULL;
  if (schema == NULL)
    return NULL;
  g_autoptr(GSettings) settings = g_settings_new("org.gnome.desktop.background");
  const char *key = adw_style_manager_get_dark(adw_style_manager_get_default()) &&
                    g_settings_schema_has_key(schema, "picture-uri-dark") ? "picture-uri-dark" : "picture-uri";
  g_autofree char *uri = g_settings_get_string(settings, key);
  return uri != NULL && *uri != '\0' ? g_filename_from_uri(uri, NULL, NULL) : NULL;
}

static void room_load(LumaTvRoom *self) {
  g_clear_object(&self->texture);
  g_autofree char *path = wallpaper_path();
  if (path == NULL)
    return;
  /* 240 x 135, blurred by ~10: scaled to the screen this is the mockup's 90-unit blur. */
  g_autoptr(GdkPixbuf) loaded = gdk_pixbuf_new_from_file_at_scale(path, 240, 135, FALSE, NULL);
  if (loaded == NULL)
    return;
  g_autoptr(GdkPixbuf) pixbuf = gdk_pixbuf_add_alpha(loaded, FALSE, 0, 0, 0);
  box_blur(gdk_pixbuf_get_pixels(pixbuf), gdk_pixbuf_get_width(pixbuf), gdk_pixbuf_get_height(pixbuf),
           gdk_pixbuf_get_rowstride(pixbuf), 6);
  G_GNUC_BEGIN_IGNORE_DEPRECATIONS
  self->texture = gdk_texture_new_for_pixbuf(pixbuf);
  G_GNUC_END_IGNORE_DEPRECATIONS
}

static void room_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaTvRoom *self = LUMA_TV_ROOM(widget);
  float width = gtk_widget_get_width(widget), height = gtk_widget_get_height(widget);
  double u = luma_ui_tv_unit((int)width, (int)height);
  graphene_rect_t all = GRAPHENE_RECT_INIT(0, 0, width, height);
  gtk_snapshot_append_color(snapshot, &(GdkRGBA){0.02f, 0.024f, 0.04f, 1.f}, &all);
  if (self->texture != NULL) {
    /* Bled past the edges (the mockup's inset -120) so the blur never shows a hard border. */
    float bleed = (float)(120 * u);
    graphene_rect_t cover = GRAPHENE_RECT_INIT(-bleed, -bleed, width + 2 * bleed, height + 2 * bleed);
    gtk_snapshot_append_scaled_texture(snapshot, self->texture, GSK_SCALING_FILTER_LINEAR, &cover);
    /* brightness(.45) */
    gtk_snapshot_append_color(snapshot, &(GdkRGBA){0, 0, 0, 0.55f}, &all);
  }
  /* The shade: darker behind the island, lighter to the right. */
  graphene_point_t start = GRAPHENE_POINT_INIT(0, 0), end = GRAPHENE_POINT_INIT(width, 0);
  GskColorStop stops[] = {{0.f, {0, 0, 0, 0.35f}}, {0.6f, {0, 0, 0, 0.1f}}, {1.f, {0, 0, 0, 0.1f}}};
  gtk_snapshot_append_linear_gradient(snapshot, &all, &start, &end, stops, G_N_ELEMENTS(stops));
}

static void room_size_allocate(GtkWidget *widget, int width, int height, int baseline) {
  (void)width; (void)height; (void)baseline;
  LumaTvRoom *self = LUMA_TV_ROOM(widget);
  if (self->resized != NULL)
    self->resized(self->data);
}

static void room_dispose(GObject *object) {
  g_clear_object(&LUMA_TV_ROOM(object)->texture);
  G_OBJECT_CLASS(luma_tv_room_parent_class)->dispose(object);
}

static void luma_tv_room_class_init(LumaTvRoomClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = room_dispose;
  GTK_WIDGET_CLASS(klass)->snapshot = room_snapshot;
  GTK_WIDGET_CLASS(klass)->size_allocate = room_size_allocate;
}

static void luma_tv_room_init(LumaTvRoom *self) {
  gtk_widget_set_can_target(GTK_WIDGET(self), FALSE);
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_vexpand(GTK_WIDGET(self), TRUE);
}

/* ── the frame ─────────────────────────────────────────────────────────────────────────────── */

typedef struct {
  GtkWindow *window; /* weak: the frame lives as the window's data */
  GtkWidget *content; /* the window's own content (its toolbar view) */
  GtkWidget *frame, *sidebar, *island;
  GtkWidget *overlay, *room, *bar, *title, *clock;
  GtkCssProvider *provider;
  GtkEventController *keys;
  GSettings *interface, *state, *a11y;
  double unit;
  gboolean active, rail, opaque;
  guint clock_id, settle_id, relax_id, restyle_id;
  gulong focus_id;
  int sidebar_width, sidebar_height, sidebar_margin;
} TvFrame;

static const char *const tv_sheet =
"window.lumaui-tv { background-color: #05060a; color: @ink@; }\n"
"window.lumaui-tv .luma-window-toolbar-view, window.lumaui-tv .luma-native-work-surface,\n"
"window.lumaui-tv .luma-window-body { background: none; box-shadow: none; border: none; border-radius: 0; padding: 0; margin: 0; }\n"
/* the shared top bar's place */
".lumaui-tv-bar { margin: 56tv 96tv 0 96tv; min-height: 74tv; }\n"
".lumaui-tv-bar .lumaui-tv-title { font-family: Figtree, sans-serif; font-size: 30tv; font-weight: 750; letter-spacing: -0.3tv; color: @ink@; }\n"
".lumaui-tv-bar .lumaui-tv-clock { font-family: Figtree, sans-serif; font-size: 28tv; font-weight: 700; color: @ink@; font-feature-settings: \"tnum\"; }\n"
/* the island */
".lumaui-tv-frame { border-spacing: 0; }\n"
".lumaui-tv-frame > .luma-navigation-sidebar { margin: 166tv 0 56tv 96tv; min-width: 376tv; padding: 12tv; border-radius: 36tv;\n"
"  background-color: @bar@; background-image: none; border: none; outline: none;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
".lumaui-tv-frame > .luma-navigation-sidebar.lumaui-tv-rail { min-width: 76tv; }\n"
".lumaui-tv-frame > .luma-navigation-sidebar scrolledwindow, .lumaui-tv-frame > .luma-navigation-sidebar list {\n"
"  background: none; padding: 0; margin: 0; border: none; box-shadow: none; }\n"
".lumaui-tv-frame scrollbar { opacity: 0; min-width: 0; min-height: 0; }\n"
".lumaui-tv-frame undershoot, .lumaui-tv-frame overshoot { background: none; box-shadow: none; }\n"
".lumaui-tv-frame .luma-navigation-row { min-height: 68tv; padding: 0 19tv; margin: 0 0 2tv 0; border-radius: 22tv;\n"
"  background: none; box-shadow: none; border: none; outline: none; color: @ink_2@; }\n"
".lumaui-tv-frame .luma-navigation-row > .luma-navigation-line { padding: 0; margin: 0; min-height: 0; border-spacing: 18tv; }\n"
".lumaui-tv-frame .luma-navigation-row label { font-family: Figtree, sans-serif; font-size: 24tv; font-weight: 600; color: inherit; margin: 0; padding: 0; }\n"
".lumaui-tv-frame .luma-navigation-row image { -gtk-icon-size: 30tv; min-width: 30tv; min-height: 30tv; margin: 0; padding: 0; color: inherit; }\n"
".lumaui-tv-frame .luma-navigation-row:selected { background-color: @chip@; background-image: none; color: @ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @chip_glint@, 0 0 0 1.5tv @chip_rim@, 0 2tv 5tv @chip_drop@; }\n"
".lumaui-tv-frame .luma-navigation-row:focus-visible { background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; outline: none; }\n"
".lumaui-tv-frame .luma-navigation-section { min-height: 1.5tv; margin: 8tv 18tv; padding: 0; border-radius: 0;\n"
"  background-color: alpha(white, 0.1); background-image: none; box-shadow: none; }\n"
".lumaui-tv-frame .luma-navigation-section * { min-height: 0; margin: 0; padding: 0; }\n"
/* the page */
".lumaui-tv-frame > .luma-island { margin: 166tv 96tv 0 44tv; padding: 0; border-radius: 0; border: none; outline: none;\n"
"  background: none; box-shadow: none; color: @ink@; }\n"
".lumaui-tv-frame > .luma-island label { color: inherit; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-muted { color: @ink_3@; }\n"
".lumaui-tv-frame > .luma-island *:focus-visible { outline: none; }\n"
".lumaui-tv-frame .lumaui-part-header .lumaui-t-page-title { font-family: Figtree, sans-serif; font-size: 52tv; font-weight: 750;\n"
"  letter-spacing: -1.56tv; color: @ink@; }\n"
".lumaui-tv-frame .lumaui-part-header .lumaui-t-meta { font-size: 26tv; font-weight: 500; color: @ink_2@; margin-top: 8tv; }\n"
".lumaui-tv-frame .lumaui-part-hero { margin-top: 28tv; padding: 28tv 30tv; border-radius: 30tv; border: none;\n"
"  background-color: @bar@; background-image: none;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
".lumaui-tv-frame .lumaui-part-group { margin-top: 34tv; }\n"
".lumaui-tv-frame .lumaui-part-group .lumaui-t-group-heading { font-size: 20tv; font-weight: 650; letter-spacing: 1tv;\n"
"  text-transform: uppercase; color: @ink_3@; margin: 0 0 12tv 26tv; }\n"
".lumaui-tv-frame .lumaui-part-card { padding: 8tv; border-radius: 30tv; border: none; background-color: @bar@; background-image: none;\n"
"  border-spacing: 2tv; box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
".lumaui-tv-frame .lumaui-part-row { min-height: 60tv; padding: 12tv 22tv; border-radius: 22tv; border: none; outline: none;\n"
"  background-color: transparent; background-image: none; box-shadow: none; color: @ink@; }\n"
/* dividers: a straight hairline at the top of each row after the first, inset to the text (past a
 * lead when both rows have one), drawn inside the row so it never follows a rounded corner */
".lumaui-tv-frame .lumaui-part-row + .lumaui-part-row { background-image: linear-gradient(@divider@, @divider@);\n"
"  background-origin: content-box; background-clip: border-box; background-repeat: no-repeat;\n"
"  background-size: 100% 1.5tv; background-position: 0 -12tv; }\n"
".lumaui-tv-frame .lumaui-part-row.lumaui-part-leads + .lumaui-part-row.lumaui-part-leads {\n"
"  background-image: linear-gradient(to right, transparent 68tv, @divider@ 68tv); }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within { background-image: linear-gradient(180deg, @key_top@, @key_bottom@);\n"
"  background-size: auto; background-position: 0 0; background-origin: border-box; color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within + .lumaui-part-row { background-image: none; }\n"
".lumaui-tv-frame .lumaui-part-row > box, .lumaui-tv-frame .lumaui-part-row > * > box { border-spacing: 20tv; padding: 0; margin: 0; min-height: 0; background: none; }\n"
".lumaui-tv-frame .lumaui-part-row .lumaui-t-row-title { font-size: 25tv; font-weight: 600; color: inherit; }\n"
".lumaui-tv-frame .lumaui-part-row .lumaui-t-row-subtitle { font-size: 19tv; font-weight: 500; color: @ink_3@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within .lumaui-t-row-subtitle, .lumaui-tv-frame .lumaui-part-row:focus-within .lumaui-t-muted,\n"
".lumaui-tv-frame .lumaui-part-row:focus-within label.dim-label { color: alpha(@key_ink@, 0.62); }\n"
".lumaui-tv-frame .lumaui-part-row label { font-size: 23tv; font-weight: 600; }\n"
".lumaui-tv-frame .lumaui-part-row .lumaui-part-lead { min-width: 48tv; min-height: 48tv; border-radius: 14tv; padding: 0; margin: 0;\n"
"  background-color: alpha(white, 0.08); color: @ink_2@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within .lumaui-part-lead { background-color: alpha(black, 0.08); color: @key_ink@; }\n"
".lumaui-tv-frame .lumaui-part-row image { -gtk-icon-size: 26tv; color: inherit; }\n"
/* controls at TV size, inside rows */
".lumaui-tv-frame .lumaui-part-row button, .lumaui-tv-frame .lumaui-part-row button:hover, .lumaui-tv-frame .lumaui-part-row button:focus-visible {\n"
"  background: none; box-shadow: none; border: none; outline: none; min-height: 0; padding: 0; color: inherit; }\n"
".lumaui-tv-frame .lumaui-part-row switch { min-width: 30tv; min-height: 34tv; padding: 4tv; border-radius: 21tv; border: none;\n"
"  background-color: @well@; background-image: none; color: transparent;\n"
"  box-shadow: inset 0 0 0 1.5tv @well_rim@, inset 0 2tv 4tv @well_shade@, 0 1.5tv 0 @well_glint@; }\n"
".lumaui-tv-frame .lumaui-part-row switch:checked { background-color: @accent@; box-shadow: inset 0 1.5tv 3tv alpha(black, 0.25); }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within switch:not(:checked) { background-color: alpha(black, 0.14); }\n"
".lumaui-tv-frame .lumaui-part-row switch > slider { min-width: 34tv; min-height: 34tv; margin: 0; border-radius: 9999px; border: none;\n"
"  background-image: linear-gradient(white, #e6e7ea); box-shadow: 0 2tv 5tv alpha(black, 0.35); }\n"
".lumaui-tv-frame .lumaui-part-row switch image { opacity: 0; }\n"
".lumaui-tv-frame .lumaui-part-row scale { min-width: 460tv; padding: 0; margin: 0; }\n"
".lumaui-tv-frame .lumaui-part-row scale > trough { min-height: 12tv; border-radius: 6tv; border: none; background-color: @well@;\n"
"  box-shadow: inset 0 0 0 1.5tv @well_rim@, inset 0 2tv 4tv @well_shade@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within scale > trough { background-color: alpha(black, 0.14); box-shadow: inset 0 1.5tv 3tv alpha(black, 0.2); }\n"
".lumaui-tv-frame .lumaui-part-row scale > trough > highlight { min-height: 12tv; border-radius: 6tv; border: none; background-color: @accent@; background-image: none; }\n"
".lumaui-tv-frame .lumaui-part-row scale > trough > slider { min-width: 0; min-height: 0; margin: 0; padding: 0; border: none; background: none; box-shadow: none; outline: none; }\n"
".lumaui-tv-frame .lumaui-part-row scale marks, .lumaui-tv-frame .lumaui-part-row scale value { font-size: 22tv; font-weight: 650; color: inherit; }\n"
".lumaui-tv-frame .lumaui-part-row entry, .lumaui-tv-frame .lumaui-part-row text { background: none; box-shadow: none; border: none; outline: none;\n"
"  min-height: 0; padding: 0; font-size: 23tv; font-weight: 600; color: inherit; caret-color: @accent@; }\n"
/* everything focusable that is not in a row: the key when it holds the focus */
".lumaui-tv-frame > .luma-island button:focus-visible { background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; outline: none; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-row button:focus-visible { background: none; box-shadow: none; color: inherit; }\n"
/* a choice's list: dropped from its value, at TV size */
"window.lumaui-tv popover > contents { padding: 12tv; border-radius: 30tv; border: none; background-color: @sheet@; color: @ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
"window.lumaui-tv popover modelbutton, window.lumaui-tv popover row, window.lumaui-tv popover button {\n"
"  min-height: 68tv; padding: 0 22tv; border-radius: 20tv; font-size: 24tv; font-weight: 600; color: @ink@; background: none; box-shadow: none; }\n"
"window.lumaui-tv popover modelbutton:focus-visible, window.lumaui-tv popover modelbutton:selected, window.lumaui-tv popover row:focus-visible,\n"
"window.lumaui-tv popover row:selected, window.lumaui-tv popover button:focus-visible {\n"
"  background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@; outline: none; }\n"
"window.lumaui-tv popover label { font-size: 24tv; font-weight: 600; color: inherit; }\n";

static char *material(const char *rgba, gboolean opaque) {
  if (!opaque || !g_str_has_prefix(rgba, "rgba("))
    return g_strdup(rgba);
  const char *last = strrchr(rgba, ',');
  return last ? g_strdup_printf("%.*s, 1)", (int)(last - rgba), rgba) : g_strdup(rgba);
}

/* Lengths written NNtv become pixels for the current unit (as luma_appkit's add_tv_style). */
static char *in_pixels(const char *css, double unit) {
  GString *out = g_string_sized_new(strlen(css) + 1024);
  const char *p = css;
  while (*p) {
    const char *start = p;
    gboolean number_start = (g_ascii_isdigit(*p) || (*p == '-' && g_ascii_isdigit(p[1])) ||
                             (*p == '.' && g_ascii_isdigit(p[1]))) &&
                            (p == css || !(g_ascii_isalnum(p[-1]) || p[-1] == '_' || p[-1] == '-' || p[-1] == '#'));
    if (number_start) {
      const char *q = p + (*p == '-' ? 1 : 0);
      while (g_ascii_isdigit(*q) || *q == '.') q++;
      if (q[0] == 't' && q[1] == 'v' && !g_ascii_isalnum(q[2])) {
        double value = g_ascii_strtod(start, NULL);
        char buf[G_ASCII_DTOSTR_BUF_SIZE];
        g_string_append(out, g_ascii_formatd(buf, sizeof buf, "%.2f", value * unit));
        g_string_append(out, "px");
        p = q + 2;
        continue;
      }
      g_string_append_len(out, start, q - start);
      p = q;
      continue;
    }
    g_string_append_c(out, *p++);
  }
  return g_string_free(out, FALSE);
}

static void tv_css(TvFrame *f) {
  if (f->provider == NULL || f->unit <= 0)
    return;
  const struct { const char *name, *value; } colours[] = {
    {"@ink@", LUMA_UI_TV_MATERIALS_INK}, {"@ink_2@", LUMA_UI_TV_MATERIALS_INK_2}, {"@ink_3@", LUMA_UI_TV_MATERIALS_INK_3},
    {"@well@", LUMA_UI_TV_MATERIALS_WELL}, {"@well_rim@", LUMA_UI_TV_MATERIALS_WELL_RIM},
    {"@well_shade@", LUMA_UI_TV_MATERIALS_WELL_SHADE}, {"@well_glint@", LUMA_UI_TV_MATERIALS_WELL_GLINT},
    {"@chip@", LUMA_UI_TV_MATERIALS_CHIP}, {"@chip_rim@", LUMA_UI_TV_MATERIALS_CHIP_RIM},
    {"@chip_drop@", LUMA_UI_TV_MATERIALS_CHIP_DROP}, {"@chip_glint@", LUMA_UI_TV_MATERIALS_CHIP_GLINT},
    {"@key_top@", LUMA_UI_TV_MATERIALS_KEY_TOP}, {"@key_bottom@", LUMA_UI_TV_MATERIALS_KEY_BOTTOM},
    {"@key_ink@", LUMA_UI_TV_MATERIALS_KEY_INK}, {"@key_rim@", LUMA_UI_TV_MATERIALS_KEY_RIM},
    {"@key_drop@", LUMA_UI_TV_MATERIALS_KEY_DROP}, {"@key_glint@", LUMA_UI_TV_MATERIALS_KEY_GLINT},
    {"@bar@", LUMA_UI_TV_MATERIALS_BAR}, {"@bar_rim@", LUMA_UI_TV_MATERIALS_BAR_RIM},
    {"@bar_drop@", LUMA_UI_TV_MATERIALS_BAR_DROP}, {"@bar_glint@", LUMA_UI_TV_MATERIALS_BAR_GLINT},
    {"@sheet@", LUMA_UI_TV_MATERIALS_SHEET}, {"@accent@", LUMA_UI_TV_MATERIALS_ACCENT},
    {"@divider@", "rgba(255, 255, 255, 0.08)"},
  };
  /* Reduce Transparency and High Contrast make the surfaces opaque (as the TV home). */
  static const char *const surfaces[] = {"@well@", "@chip@", "@bar@", "@sheet@", NULL};
  GString *css = g_string_new(tv_sheet);
  for (guint i = 0; i < G_N_ELEMENTS(colours); i++) {
    g_autofree char *value = material(colours[i].value, f->opaque && g_strv_contains(surfaces, colours[i].name));
    g_string_replace(css, colours[i].name, value, 0);
  }
  g_autofree char *pixels = in_pixels(css->str, f->unit);
  g_string_free(css, TRUE);
  gtk_css_provider_load_from_string(f->provider, pixels);
}

static gboolean setting_true(GSettings *settings, const char *key) {
  if (settings == NULL)
    return FALSE;
  g_autoptr(GSettingsSchema) schema = NULL;
  g_object_get(settings, "settings-schema", &schema, NULL);
  return schema != NULL && g_settings_schema_has_key(schema, key) && g_settings_get_boolean(settings, key);
}

static GSettings *settings_if_installed(const char *id) {
  GSettingsSchemaSource *source = g_settings_schema_source_get_default();
  g_autoptr(GSettingsSchema) schema = source != NULL ? g_settings_schema_source_lookup(source, id, TRUE) : NULL;
  return schema != NULL ? g_settings_new(id) : NULL;
}

static void opacity_changed(TvFrame *f) {
  gboolean opaque = setting_true(f->state, "reduce-transparency") || setting_true(f->a11y, "high-contrast");
  if (opaque == f->opaque)
    return;
  f->opaque = opaque;
  tv_css(f);
}

static gboolean restyle(gpointer data);

static void room_resized(gpointer data) {
  TvFrame *f = data;
  double unit = luma_ui_tv_unit(gtk_widget_get_width(f->room), gtk_widget_get_height(f->room));
  /* Not during allocation: the new sheet relayouts, so it loads on the next idle. */
  if (unit > 0 && fabs(unit - f->unit) >= 0.001 && f->restyle_id == 0)
    f->restyle_id = g_idle_add(restyle, f);
}

static gboolean restyle(gpointer data) {
  TvFrame *f = data;
  f->restyle_id = 0;
  if (!f->active)
    return G_SOURCE_REMOVE;
  double unit = luma_ui_tv_unit(gtk_widget_get_width(f->room), gtk_widget_get_height(f->room));
  if (unit > 0 && fabs(unit - f->unit) >= 0.001) {
    f->unit = unit;
    tv_css(f);
  }
  return G_SOURCE_REMOVE;
}

/* ── the clock ─────────────────────────────────────────────────────────────────────────────── */

static gboolean clock_tick(gpointer data) {
  TvFrame *f = data;
  g_autoptr(GDateTime) now = g_date_time_new_now_local();
  g_autofree char *style = f->interface ? g_settings_get_string(f->interface, "clock-format") : g_strdup("24h");
  /* The shell's wall clock (the TV home's): "7:07 PM" or "19:07", with the ratio sign. */
  g_autofree char *text = g_date_time_format(now, g_str_equal(style, "12h") ? "%l∶%M %p" : "%H∶%M");
  gtk_label_set_text(GTK_LABEL(f->clock), g_strstrip(text));
  int wait = 60 - g_date_time_get_second(now);
  f->clock_id = g_timeout_add_seconds(MAX(1, wait), clock_tick, f);
  return G_SOURCE_REMOVE;
}

/* ── where the focus is ───────────────────────────────────────────────────────────────────── */

static GtkWidget *focus_of(TvFrame *f) {
  return gtk_root_get_focus(GTK_ROOT(f->window));
}

static gboolean in_page(TvFrame *f, GtkWidget *focus) {
  return focus != NULL && (focus == f->island || gtk_widget_is_ancestor(focus, f->island));
}

static GtkListBox *sidebar_list(TvFrame *f) {
  return GTK_LIST_BOX(luma_navigation_sidebar_get_list(LUMA_NAVIGATION_SIDEBAR(f->sidebar)));
}

static gboolean row_reachable(GtkListBoxRow *row) {
  return gtk_widget_get_visible(GTK_WIDGET(row)) && gtk_list_box_row_get_selectable(row) &&
         gtk_widget_get_sensitive(GTK_WIDGET(row)) && !gtk_widget_has_css_class(GTK_WIDGET(row), "luma-navigation-section");
}

static GtkListBoxRow *selected_row(TvFrame *f) {
  GtkListBox *list = sidebar_list(f);
  GtkListBoxRow *row = gtk_list_box_get_selected_row(list);
  if (row != NULL)
    return row;
  for (int i = 0; (row = gtk_list_box_get_row_at_index(list, i)) != NULL; i++)
    if (row_reachable(row))
      return row;
  return NULL;
}

static void set_rail(TvFrame *f, gboolean rail) {
  if (f->rail == rail)
    return;
  f->rail = rail;
  luma_ui_set_css_class(f->sidebar, "lumaui-tv-rail", rail);
  luma_navigation_sidebar_set_tv(LUMA_NAVIGATION_SIDEBAR(f->sidebar), TRUE, rail);
}

/* The page's parts (luma_ui_set_part) and its clamp: released to the safe margin while on a TV. */
static void release_page(TvFrame *f, GtkWidget *widget) {
  if (ADW_IS_CLAMP(widget) && g_object_get_data(G_OBJECT(widget), "luma-tv-clamp") == NULL) {
    AdwClamp *clamp = ADW_CLAMP(widget);
    GtkWidget *child = adw_clamp_get_child(clamp);
    int *saved = g_new(int, 6);
    saved[0] = adw_clamp_get_maximum_size(clamp);
    saved[1] = adw_clamp_get_tightening_threshold(clamp);
    saved[2] = child ? gtk_widget_get_margin_start(child) : 0;
    saved[3] = child ? gtk_widget_get_margin_end(child) : 0;
    saved[4] = child ? gtk_widget_get_margin_top(child) : 0;
    saved[5] = child ? gtk_widget_get_margin_bottom(child) : 0;
    g_object_set_data_full(G_OBJECT(widget), "luma-tv-clamp", saved, g_free);
    adw_clamp_set_maximum_size(clamp, G_MAXINT / 4);
    adw_clamp_set_tightening_threshold(clamp, G_MAXINT / 4);
    if (child) {
      /* The page's own inset (the mockup's .tv-page > .inner): 6 a side, 12 over, 160 under. */
      gtk_widget_set_margin_start(child, (int)round(6 * f->unit));
      gtk_widget_set_margin_end(child, (int)round(6 * f->unit));
      gtk_widget_set_margin_top(child, (int)round(12 * f->unit));
      gtk_widget_set_margin_bottom(child, (int)round(160 * f->unit));
    }
    return; /* nothing inside a clamp is another clamp worth releasing */
  }
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c))
    release_page(f, c);
}

static void restore_page(GtkWidget *widget) {
  int *saved = ADW_IS_CLAMP(widget) ? g_object_get_data(G_OBJECT(widget), "luma-tv-clamp") : NULL;
  if (saved != NULL) {
    AdwClamp *clamp = ADW_CLAMP(widget);
    GtkWidget *child = adw_clamp_get_child(clamp);
    adw_clamp_set_maximum_size(clamp, saved[0]);
    adw_clamp_set_tightening_threshold(clamp, saved[1]);
    if (child) {
      gtk_widget_set_margin_start(child, saved[2]);
      gtk_widget_set_margin_end(child, saved[3]);
      gtk_widget_set_margin_top(child, saved[4]);
      gtk_widget_set_margin_bottom(child, saved[5]);
    }
    g_object_set_data(G_OBJECT(widget), "luma-tv-clamp", NULL);
    return;
  }
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c))
    restore_page(c);
}

static gboolean relax(gpointer data) {
  TvFrame *f = data;
  f->relax_id = 0;
  if (f->active)
    release_page(f, f->island);
  return G_SOURCE_REMOVE;
}

static void queue_relax(TvFrame *f) {
  if (f->relax_id == 0)
    f->relax_id = g_idle_add_full(G_PRIORITY_HIGH_IDLE, relax, f, NULL);
}

static void dump(GtkWidget *w, int depth) {
  if (depth > 14) return;
  g_auto(GStrv) classes = gtk_widget_get_css_classes(w);
  g_autofree char *joined = g_strjoinv(".", classes);
  g_printerr("%*s%s %s.%s [%d x %d] %s\n", depth * 2, "", G_OBJECT_TYPE_NAME(w), gtk_widget_get_css_name(w), joined,
             gtk_widget_get_width(w), gtk_widget_get_height(w), gtk_widget_get_name(w));
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c))
    if (gtk_widget_get_visible(c)) dump(c, depth + 1);
}

static gboolean settle(gpointer data) {
  TvFrame *f = data;
  f->settle_id = 0;
  if (!f->active)
    return G_SOURCE_REMOVE;
  release_page(f, f->island);
  if (g_getenv("LUMA_TV_DEBUG") != NULL) dump(f->island, 0);
  if (focus_of(f) == NULL) {
    GtkListBoxRow *row = selected_row(f);
    if (row != NULL)
      gtk_widget_grab_focus(GTK_WIDGET(row));
  }
  return G_SOURCE_REMOVE;
}

static void queue_settle(TvFrame *f) {
  if (f->settle_id == 0)
    f->settle_id = g_timeout_add_full(G_PRIORITY_LOW, 60, settle, f, NULL);
}

static void focus_changed(GtkWindow *window, GParamSpec *pspec, TvFrame *f) {
  (void)window; (void)pspec;
  if (!f->active)
    return;
  GtkWidget *focus = focus_of(f);
  if (focus == NULL) {
    queue_settle(f); /* a page rebuilt under the focus: the island takes it back */
    return;
  }
  if (in_page(f, focus) || gtk_widget_is_ancestor(focus, f->sidebar))
    set_rail(f, in_page(f, focus));
  queue_relax(f);
}

/* ── the remote ───────────────────────────────────────────────────────────────────────────── */

static void to_island(TvFrame *f) {
  GtkListBoxRow *row = selected_row(f);
  if (row != NULL)
    gtk_widget_grab_focus(GTK_WIDGET(row));
  set_rail(f, FALSE);
}

static gboolean to_page(TvFrame *f) {
  release_page(f, f->island);
  gtk_root_set_focus(GTK_ROOT(f->window), NULL);
  if (!gtk_widget_child_focus(f->island, GTK_DIR_TAB_FORWARD)) {
    to_island(f);
    return FALSE;
  }
  set_rail(f, TRUE);
  return TRUE;
}

static void open_neighbour(TvFrame *f, GtkWidget *focus, int step) {
  GtkListBox *list = sidebar_list(f);
  GtkWidget *current = focus ? gtk_widget_get_ancestor(focus, GTK_TYPE_LIST_BOX_ROW) : NULL;
  int index = current ? gtk_list_box_row_get_index(GTK_LIST_BOX_ROW(current)) : -1;
  if (index < 0) {
    GtkListBoxRow *selected = selected_row(f);
    if (selected != NULL) gtk_widget_grab_focus(GTK_WIDGET(selected));
    return;
  }
  for (int i = index + step; i >= 0; i += step) {
    GtkListBoxRow *row = gtk_list_box_get_row_at_index(list, i);
    if (row == NULL)
      return;
    if (!row_reachable(row))
      continue;
    /* Moving opens the page (as the TV home's tabs): no OK. */
    gtk_list_box_select_row(list, row);
    gtk_widget_grab_focus(GTK_WIDGET(row));
    g_signal_emit_by_name(list, "row-activated", row);
    return;
  }
}

static GtkWidget *back_part(GtkWidget *widget) {
  if (gtk_widget_has_css_class(widget, "lumaui-part-back") && gtk_widget_is_drawable(widget) &&
      gtk_widget_is_sensitive(widget))
    return widget;
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    GtkWidget *found = back_part(c);
    if (found != NULL) return found;
  }
  return NULL;
}

static gboolean page_back_settled(gpointer data) {
  TvFrame *f = data;
  if (f->active && !in_page(f, focus_of(f)))
    to_page(f);
  return G_SOURCE_REMOVE;
}

static gboolean is_back(guint key) {
  return key == GDK_KEY_Escape || key == GDK_KEY_Back || key == GDK_KEY_BackSpace;
}

static gboolean is_ok(guint key) {
  return key == GDK_KEY_Return || key == GDK_KEY_KP_Enter || key == GDK_KEY_ISO_Enter ||
         key == GDK_KEY_space || key == GDK_KEY_Select;
}

static gboolean editable(GtkWidget *focus) {
  return focus != NULL && (GTK_IS_EDITABLE(focus) || GTK_IS_TEXT_VIEW(focus));
}

static gboolean key_pressed(GtkEventControllerKey *controller, guint key, guint code,
                            GdkModifierType state, TvFrame *f) {
  (void)controller; (void)code;
  if (!f->active || (state & (GDK_CONTROL_MASK | GDK_ALT_MASK | GDK_SUPER_MASK)) != 0)
    return GDK_EVENT_PROPAGATE;
  GtkWidget *focus = focus_of(f);
  gboolean page = in_page(f, focus);
  gtk_window_set_focus_visible(f->window, TRUE);

  if (!page) {
    /* The island (or nothing yet). */
    switch (key) {
    case GDK_KEY_Up: case GDK_KEY_KP_Up:
      open_neighbour(f, focus, -1);
      return GDK_EVENT_STOP;
    case GDK_KEY_Down: case GDK_KEY_KP_Down:
      open_neighbour(f, focus, 1);
      return GDK_EVENT_STOP;
    case GDK_KEY_Right: case GDK_KEY_KP_Right:
      to_page(f);
      return GDK_EVENT_STOP;
    case GDK_KEY_Left: case GDK_KEY_KP_Left:
      return GDK_EVENT_STOP;
    default:
      if (is_ok(key)) {
        to_page(f);
        return GDK_EVENT_STOP;
      }
      if (is_back(key)) {
        /* Back from the island leaves the app: the TV home's Apps. */
        gtk_window_minimize(f->window);
        return GDK_EVENT_STOP;
      }
      return GDK_EVENT_PROPAGATE;
    }
  }

  /* The page. A field keeps its own typing, caret and Backspace. */
  gboolean text = editable(focus);
  if (is_back(key)) {
    if (text && key == GDK_KEY_BackSpace)
      return GDK_EVENT_PROPAGATE;
    GtkWidget *back = back_part(f->island);
    if (back != NULL) {
      /* Up a level: the parent page opens, and the focus lands in it once it is drawn. */
      gtk_widget_activate(back);
      g_timeout_add_full(G_PRIORITY_LOW, 80, page_back_settled, f, NULL);
    } else {
      to_island(f);
    }
    return GDK_EVENT_STOP;
  }
  GtkDirectionType dir;
  switch (key) {
  case GDK_KEY_Up: case GDK_KEY_KP_Up: dir = GTK_DIR_UP; break;
  case GDK_KEY_Down: case GDK_KEY_KP_Down: dir = GTK_DIR_DOWN; break;
  case GDK_KEY_Left: case GDK_KEY_KP_Left: dir = GTK_DIR_LEFT; break;
  case GDK_KEY_Right: case GDK_KEY_KP_Right: dir = GTK_DIR_RIGHT; break;
  default: return GDK_EVENT_PROPAGATE;
  }
  GtkWidget *range = focus ? (GTK_IS_RANGE(focus) ? focus : gtk_widget_get_ancestor(focus, GTK_TYPE_RANGE)) : NULL;
  if (range != NULL && (dir == GTK_DIR_LEFT || dir == GTK_DIR_RIGHT)) {
    /* A slider takes Left/Right while focused; Left at its minimum returns to the island. */
    GtkAdjustment *adj = gtk_range_get_adjustment(GTK_RANGE(range));
    gboolean inverted = gtk_range_get_inverted(GTK_RANGE(range)) != (gtk_widget_get_direction(range) == GTK_TEXT_DIR_RTL);
    double edge = inverted ? gtk_adjustment_get_upper(adj) - gtk_adjustment_get_page_size(adj) : gtk_adjustment_get_lower(adj);
    if (dir == GTK_DIR_LEFT && fabs(gtk_adjustment_get_value(adj) - edge) < 1e-6) {
      to_island(f);
      return GDK_EVENT_STOP;
    }
    return GDK_EVENT_PROPAGATE;
  }
  if (text && (dir == GTK_DIR_LEFT || dir == GTK_DIR_RIGHT) && GTK_IS_EDITABLE(focus)) {
    int position = gtk_editable_get_position(GTK_EDITABLE(focus));
    if (dir == GTK_DIR_RIGHT || position > 0)
      return GDK_EVENT_PROPAGATE;
  }
  if (text && GTK_IS_TEXT_VIEW(focus))
    return GDK_EVENT_PROPAGATE;
  if (!gtk_widget_child_focus(f->island, dir) && dir == GTK_DIR_LEFT)
    to_island(f); /* off the page's left edge: the island opens again */
  return GDK_EVENT_STOP;
}

/* ── on and off ───────────────────────────────────────────────────────────────────────────── */

static void enter(TvFrame *f) {
  f->active = TRUE;
  f->rail = FALSE;
  gtk_widget_add_css_class(f->frame, "lumaui-tv-frame");
  gtk_widget_get_size_request(f->sidebar, &f->sidebar_width, &f->sidebar_height);
  f->sidebar_margin = gtk_widget_get_margin_end(f->sidebar);
  gtk_widget_set_size_request(f->sidebar, -1, -1);
  gtk_widget_set_margin_end(f->sidebar, 0);
  luma_navigation_sidebar_set_tv(LUMA_NAVIGATION_SIDEBAR(f->sidebar), TRUE, FALSE);

  if (f->overlay == NULL) {
    f->overlay = g_object_ref_sink(gtk_overlay_new());
    f->room = g_object_new(LUMA_TYPE_TV_ROOM, NULL);
    gtk_overlay_set_child(GTK_OVERLAY(f->overlay), f->room);
    f->bar = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(f->bar, "lumaui-tv-bar");
    gtk_widget_set_valign(f->bar, GTK_ALIGN_START);
    gtk_widget_set_can_target(f->bar, FALSE);
    f->title = gtk_label_new("");
    gtk_widget_add_css_class(f->title, "lumaui-tv-title");
    gtk_widget_set_hexpand(f->title, TRUE);
    gtk_label_set_xalign(GTK_LABEL(f->title), 0);
    f->clock = gtk_label_new("");
    gtk_widget_add_css_class(f->clock, "lumaui-tv-clock");
    gtk_box_append(GTK_BOX(f->bar), f->title);
    gtk_box_append(GTK_BOX(f->bar), f->clock);
    LUMA_TV_ROOM(f->room)->resized = room_resized;
    LUMA_TV_ROOM(f->room)->data = f;
  }
  luma_tv_room_load(f->room);
  const char *name = gtk_window_get_title(f->window);
  gtk_label_set_text(GTK_LABEL(f->title), name && *name ? name : g_get_application_name());

  /* The window's content goes over the room, under the bar's place. */
  g_object_ref(f->content);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(f->window), NULL);
  gtk_overlay_add_overlay(GTK_OVERLAY(f->overlay), f->content);
  gtk_overlay_add_overlay(GTK_OVERLAY(f->overlay), f->bar);
  g_object_unref(f->content);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(f->window), f->overlay);

  f->provider = gtk_css_provider_new();
  gtk_style_context_add_provider_for_display(gtk_widget_get_display(GTK_WIDGET(f->window)),
      GTK_STYLE_PROVIDER(f->provider), GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 20U);
  f->opaque = setting_true(f->state, "reduce-transparency") || setting_true(f->a11y, "high-contrast");
  f->unit = 0;
  int w = gtk_widget_get_width(GTK_WIDGET(f->window)), h = gtk_widget_get_height(GTK_WIDGET(f->window));
  f->unit = luma_ui_tv_unit(w > 0 ? w : 1920, h > 0 ? h : 1080);
  tv_css(f);

  f->keys = gtk_event_controller_key_new();
  gtk_event_controller_set_propagation_phase(f->keys, GTK_PHASE_CAPTURE);
  g_signal_connect(f->keys, "key-pressed", G_CALLBACK(key_pressed), f);
  gtk_widget_add_controller(GTK_WIDGET(f->window), f->keys);
  f->focus_id = g_signal_connect(f->window, "notify::focus-widget", G_CALLBACK(focus_changed), f);
  clock_tick(f);
  gtk_window_set_focus_visible(f->window, TRUE);
  queue_settle(f);
}

static void leave(TvFrame *f) {
  f->active = FALSE;
  g_clear_handle_id(&f->clock_id, g_source_remove);
  g_clear_handle_id(&f->settle_id, g_source_remove);
  g_clear_handle_id(&f->relax_id, g_source_remove);
  g_clear_handle_id(&f->restyle_id, g_source_remove);
  g_clear_signal_handler(&f->focus_id, f->window);
  if (f->keys != NULL) {
    gtk_widget_remove_controller(GTK_WIDGET(f->window), f->keys);
    f->keys = NULL;
  }
  if (f->provider != NULL) {
    gtk_style_context_remove_provider_for_display(gtk_widget_get_display(GTK_WIDGET(f->window)),
                                                  GTK_STYLE_PROVIDER(f->provider));
    g_clear_object(&f->provider);
  }
  g_object_ref(f->content);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(f->window), NULL);
  gtk_overlay_remove_overlay(GTK_OVERLAY(f->overlay), f->content);
  gtk_overlay_remove_overlay(GTK_OVERLAY(f->overlay), f->bar);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(f->window), f->content);
  g_object_unref(f->content);

  restore_page(f->island);
  luma_ui_set_css_class(f->sidebar, "lumaui-tv-rail", FALSE);
  luma_navigation_sidebar_set_tv(LUMA_NAVIGATION_SIDEBAR(f->sidebar), FALSE, FALSE);
  gtk_widget_set_size_request(f->sidebar, f->sidebar_width, f->sidebar_height);
  gtk_widget_set_margin_end(f->sidebar, f->sidebar_margin);
  gtk_widget_remove_css_class(f->frame, "lumaui-tv-frame");
  f->rail = FALSE;
}

static void frame_free(TvFrame *f) {
  if (f->active && f->window != NULL) {
    g_clear_handle_id(&f->clock_id, g_source_remove);
    g_clear_handle_id(&f->settle_id, g_source_remove);
    g_clear_handle_id(&f->relax_id, g_source_remove);
    g_clear_handle_id(&f->restyle_id, g_source_remove);
    if (f->provider != NULL)
      gtk_style_context_remove_provider_for_display(gdk_display_get_default(), GTK_STYLE_PROVIDER(f->provider));
  }
  g_clear_object(&f->provider);
  g_clear_object(&f->overlay);
  g_clear_object(&f->interface);
  g_clear_object(&f->state);
  g_clear_object(&f->a11y);
  g_free(f);
}

void luma_tv_room_load(GtkWidget *room) {
  room_load(LUMA_TV_ROOM(room));
  gtk_widget_queue_draw(room);
}

void luma_tv_frame_attach(GtkWindow *window, GtkWidget *content, GtkWidget *frame,
                          GtkWidget *sidebar, GtkWidget *island) {
  TvFrame *old = g_object_get_data(G_OBJECT(window), "luma-tv-frame");
  if (old != NULL && old->active)
    leave(old);
  g_object_set_data(G_OBJECT(window), "luma-tv-frame", NULL);
  if (sidebar == NULL)
    return; /* no sidebar, no island: the window's TV treatment is the title row's alone */
  TvFrame *f = g_new0(TvFrame, 1);
  f->window = window;
  f->content = content;
  f->frame = frame;
  f->sidebar = sidebar;
  f->island = island;
  f->interface = settings_if_installed("org.gnome.desktop.interface");
  f->state = settings_if_installed("org.project_luma.shell-state");
  f->a11y = settings_if_installed("org.gnome.desktop.a11y.interface");
  if (f->state != NULL)
    g_signal_connect_swapped(f->state, "changed::reduce-transparency", G_CALLBACK(opacity_changed), f);
  if (f->a11y != NULL)
    g_signal_connect_swapped(f->a11y, "changed::high-contrast", G_CALLBACK(opacity_changed), f);
  g_object_set_data_full(G_OBJECT(window), "luma-tv-frame", f, (GDestroyNotify)frame_free);
}

void luma_tv_frame_set_active(GtkWindow *window, gboolean tv) {
  TvFrame *f = g_object_get_data(G_OBJECT(window), "luma-tv-frame");
  if (f == NULL || f->active == tv)
    return;
  if (tv) enter(f);
  else leave(f);
}

gboolean luma_tv_frame_is_active(GtkWindow *window) {
  TvFrame *f = g_object_get_data(G_OBJECT(window), "luma-tv-frame");
  return f != NULL && f->active;
}
