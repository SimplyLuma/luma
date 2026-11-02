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
  gulong focus_id, visible_id;
  int sidebar_width, sidebar_height, sidebar_margin;
  GArray *path;          /* where the focus was in the page, child by child from the island */
  gint64 ok_at;          /* when OK last acted in the page */
  gboolean keyed;        /* the remote has been used in this window */
  GtkWidget *ok_widget;  /* what OK was pressed on (weak) */
  GtkWidget *held;       /* the page widget the remote was on (weak): gone means the page redrew */
  gint64 key_at;         /* the last key the frame saw */
  gint64 moved_at;       /* when the focus last moved without a key (a redraw, GTK's fallback) */
  gulong active_id;
  gpointer keyboard;     /* the TV keyboard's height, followed */
  int island_bottom;     /* the island's own bottom margin, while the keyboard lifts it */
  gboolean page_zone;    /* the remote is in the page */
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
/* LumaUI's type roles at TV size, wherever the page uses them (lumaui-tv.css's scale) */
".lumaui-tv-frame > .luma-island label { font-family: Figtree, sans-serif; line-height: 1.25; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-row box.vertical { border-spacing: 3tv; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-display, .lumaui-tv-frame > .luma-island .lumaui-t-page-title { font-size: 52tv; font-weight: 750; letter-spacing: -1.56tv; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-hero, .lumaui-tv-frame > .luma-island .lumaui-t-hero-title, .lumaui-tv-frame > .luma-island .lumaui-t-title-1,\n"
".lumaui-tv-frame > .luma-island .lumaui-t-detail-title { font-size: 34tv; font-weight: 700; letter-spacing: -0.68tv; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-title-2, .lumaui-tv-frame > .luma-island .lumaui-t-section-title { font-size: 30tv; font-weight: 700; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-lead, .lumaui-tv-frame > .luma-island .lumaui-t-intro, .lumaui-tv-frame > .luma-island .lumaui-t-meta,\n"
".lumaui-tv-frame > .luma-island .lumaui-t-body { font-size: 26tv; font-weight: 500; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-row-title, .lumaui-tv-frame > .luma-island .lumaui-t-list-title, .lumaui-tv-frame > .luma-island .lumaui-t-card-title { font-size: 25tv; font-weight: 600; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-label, .lumaui-tv-frame > .luma-island .lumaui-t-numeric, .lumaui-tv-frame > .luma-island .lumaui-t-button-small { font-size: 22tv; font-weight: 650; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-small, .lumaui-tv-frame > .luma-island .lumaui-t-caption, .lumaui-tv-frame > .luma-island .lumaui-t-row-subtitle { font-size: 19tv; font-weight: 500; }\n"
".lumaui-tv-frame > .luma-island .lumaui-t-group-heading { font-size: 20tv; font-weight: 650; letter-spacing: 1tv; text-transform: uppercase; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero .lumaui-t-small, .lumaui-tv-frame > .luma-island .lumaui-part-hero .lumaui-t-caption,\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero .lumaui-t-meta { font-size: 22tv; margin-top: 6tv; }\n"
".lumaui-tv-frame .lumaui-part-header .lumaui-t-page-title { font-family: Figtree, sans-serif; font-size: 52tv; font-weight: 750;\n"
"  letter-spacing: -1.56tv; color: @ink@; line-height: 1.12; }\n"
".lumaui-tv-frame .lumaui-part-header .lumaui-t-meta { font-size: 26tv; font-weight: 500; color: @ink_2@; margin-top: 10tv; }\n"
".lumaui-tv-frame .lumaui-part-header { margin: 0; padding: 0; min-height: 0; }\n"
".lumaui-tv-frame .lumaui-part-hero { margin-top: 28tv; padding: 28tv 30tv; border-radius: 30tv; border: none;\n"
"  background-color: @bar@; background-image: none;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
".lumaui-tv-frame .lumaui-part-group { margin-top: 34tv; }\n"
".lumaui-tv-frame .lumaui-part-group .lumaui-t-group-heading { font-size: 20tv; font-weight: 650; letter-spacing: 1tv;\n"
"  text-transform: uppercase; color: @ink_3@; margin: 0 0 12tv 26tv; }\n"
".lumaui-tv-frame .lumaui-part-card { padding: 8tv; border-radius: 30tv; border: none; background-color: @bar@; background-image: none;\n"
"  border-spacing: 2tv; box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
".lumaui-tv-frame .lumaui-part-row { min-height: 0; padding: 12tv 22tv; border-radius: 22tv; border: none; outline: none;\n"
"  background-color: transparent; background-image: none; box-shadow: none; color: @ink@; }\n"
/* a row is 84 tall (60 + padding) with its content centred: a row that is its own line, or the
 * first line of a row that grows (a network's join form) */
".lumaui-tv-frame .lumaui-part-row.horizontal, .lumaui-tv-frame button.lumaui-part-row,\n"
".lumaui-tv-frame .lumaui-part-row.vertical > :first-child { min-height: 60tv; }\n"
/* fallbacks for anything the page draws without a LumaUI type role or icon size */
".lumaui-tv-frame > .luma-island label { font-size: 23tv; }\n"
".lumaui-tv-frame > .luma-island image { -gtk-icon-size: 26tv; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero label { font-size: 26tv; font-weight: 600; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero image { -gtk-icon-size: 46tv; min-width: 46tv; min-height: 46tv; }\n"
/* libadwaita's rows and groups, named by the frame */
".lumaui-tv-frame row.lumaui-part-row { min-height: 60tv; }\n"
".lumaui-tv-frame .lumaui-part-row label.title { font-size: 25tv; font-weight: 600; color: inherit; }\n"
".lumaui-tv-frame .lumaui-part-row label.subtitle { font-size: 19tv; font-weight: 500; color: @ink_3@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within label.subtitle { color: alpha(@key_ink@, 0.62); }\n"
".lumaui-tv-frame .lumaui-part-row box.header { min-height: 0; padding: 0; margin: 0; border-spacing: 20tv; }\n"
".lumaui-tv-frame .lumaui-part-group label.heading, .lumaui-tv-frame .lumaui-part-group label.title-4 { font-size: 20tv; font-weight: 650; letter-spacing: 1tv;\n"
"  text-transform: uppercase; color: @ink_3@; margin: 0 0 12tv 26tv; }\n"
".lumaui-tv-frame list.lumaui-part-card { background-color: @bar@; }\n"
".lumaui-tv-frame list.lumaui-part-card > row { border: none; }\n"
/* the hero's lead: the thing's own tile, 92 in the accent (the mockup's .hero .big) */
".lumaui-tv-frame .lumaui-part-hero .lumaui-part-lead { min-width: 92tv; min-height: 92tv; border-radius: 26tv; padding: 0; margin: 0 10tv 0 0;\n"
"  background-color: @accent@; background-image: none; color: white; box-shadow: none; }\n"
".lumaui-tv-frame .lumaui-part-hero .lumaui-part-lead image { -gtk-icon-size: 46tv; min-width: 46tv; min-height: 46tv; color: white; }\n"
/* a drawn lead (signal bars): 2.5 times its size, about its centre, in the lead's place */
".lumaui-tv-frame .lumaui-part-mark { transform: scale(2.5); transform-origin: 50% 50%; }\n"
/* a hero's drawn mark (a logo, a monogram): larger, and no decorative ring around it */
".lumaui-tv-frame .lumaui-part-hero .lumaui-part-mark { transform: scale(1.8); transform-origin: 50% 50%; margin: 0 24tv;\n"
"  border: none; outline: none; box-shadow: none; background: none; }\n"
".lumaui-tv-frame .lumaui-part-hero .lumaui-part-mark * { border: none; outline: none; box-shadow: none; }\n"
".lumaui-tv-frame .lumaui-part-hero .lumaui-part-lead { border: none; outline: none; }\n"
/* dividers: a straight hairline at the top of each row after the first, inset to the text (past a
 * lead when both rows have one), drawn inside the row so it never follows a rounded corner */
".lumaui-tv-frame .lumaui-part-row:not(:focus-within) + .lumaui-part-row:not(:focus-within) { background-image: linear-gradient(@divider@, @divider@);\n"
"  background-origin: content-box; background-clip: border-box; background-repeat: no-repeat;\n"
"  background-size: 100% 1.5tv; background-position: 0 -12tv; }\n"
".lumaui-tv-frame .lumaui-part-row.lumaui-part-leads:not(:focus-within) + .lumaui-part-row.lumaui-part-leads:not(:focus-within) {\n"
"  background-image: linear-gradient(to right, transparent 68tv, @divider@ 68tv); }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within { background-image: linear-gradient(180deg, @key_top@, @key_bottom@);\n"
"  background-size: auto; background-position: 0 0; background-origin: border-box; color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within + .lumaui-part-row { background-image: none; }\n"
".lumaui-tv-frame .lumaui-part-row > box, .lumaui-tv-frame .lumaui-part-row > * > box { padding: 0; margin: 0; min-height: 0; background: none; }\n"
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
".lumaui-tv-frame > .luma-island switch { min-width: 30tv; min-height: 34tv; padding: 4tv; border-radius: 21tv; border: none;\n"
"  background-color: @well@; background-image: none; color: transparent;\n"
"  box-shadow: inset 0 0 0 1.5tv @well_rim@, inset 0 2tv 4tv @well_shade@, 0 1.5tv 0 @well_glint@; }\n"
".lumaui-tv-frame > .luma-island switch:checked { background-color: @accent@; box-shadow: inset 0 1.5tv 3tv alpha(black, 0.25); }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within switch:not(:checked) { background-color: alpha(black, 0.14); }\n"
".lumaui-tv-frame > .luma-island switch > slider { min-width: 34tv; min-height: 34tv; margin: 0; border-radius: 9999px; border: none;\n"
"  background-image: linear-gradient(white, #e6e7ea); box-shadow: 0 2tv 5tv alpha(black, 0.35); }\n"
".lumaui-tv-frame > .luma-island switch image { opacity: 0; }\n"
".lumaui-tv-frame .lumaui-part-row scale { min-width: 460tv; padding: 0; margin: 0; }\n"
".lumaui-tv-frame .lumaui-part-row scale > trough { min-height: 12tv; border-radius: 6tv; border: none; background-color: @well@;\n"
"  box-shadow: inset 0 0 0 1.5tv @well_rim@, inset 0 2tv 4tv @well_shade@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within scale > trough { background-color: alpha(black, 0.14); box-shadow: inset 0 1.5tv 3tv alpha(black, 0.2); }\n"
".lumaui-tv-frame .lumaui-part-row scale > trough > highlight { min-height: 12tv; border-radius: 6tv; border: none; background-color: @accent@; background-image: none; }\n"
".lumaui-tv-frame .lumaui-part-row scale > trough > slider { min-width: 0; min-height: 0; margin: 0; padding: 0; border: none; background: none; box-shadow: none; outline: none; }\n"
".lumaui-tv-frame .lumaui-part-row scale marks, .lumaui-tv-frame .lumaui-part-row scale value { font-size: 22tv; font-weight: 650; color: inherit; }\n"
".lumaui-tv-frame .lumaui-part-row entry, .lumaui-tv-frame .lumaui-part-row text { background: none; box-shadow: none; border: none; outline: none;\n"
"  min-height: 0; padding: 0; font-size: 23tv; font-weight: 600; color: inherit; caret-color: @accent@; }\n"
/* what a row grows in place (a network's join form): a recessed field and TV buttons, 66 tall */
".lumaui-tv-frame .lumaui-part-row.vertical > :not(:first-child) { margin-top: 14tv; border-spacing: 14tv; }\n"
".lumaui-tv-frame .lumaui-part-row.vertical > :not(:first-child) button { min-height: 66tv; padding: 0 30tv; border-radius: 22tv;\n"
"  font-size: 25tv; font-weight: 650; color: @ink@; background-color: @chip@; background-image: none;\n"
"  box-shadow: inset 0 1.5tv 0 @chip_glint@, 0 0 0 1.5tv @chip_rim@, 0 2tv 5tv @chip_drop@; }\n"
".lumaui-tv-frame .lumaui-part-row.vertical > :not(:first-child) button label { font-size: 25tv; font-weight: 650; color: inherit; }\n"
".lumaui-tv-frame .lumaui-part-row.vertical > :not(:first-child) button:focus-visible { background-color: @ink@; color: @key_ink@;\n"
"  box-shadow: 0 0 0 3tv @accent@; }\n"
".lumaui-tv-frame .lumaui-part-row.vertical > :not(:first-child) entry { min-height: 66tv; padding: 0 22tv; border-radius: 22tv; border: none;\n"
"  font-size: 25tv; font-weight: 600; color: @ink@; background-color: @well@; background-image: none; outline: none;\n"
"  box-shadow: inset 0 0 0 1.5tv @well_rim@, inset 0 2tv 4tv @well_shade@; }\n"
".lumaui-tv-frame .lumaui-part-row.vertical > :not(:first-child) entry text { font-size: 25tv; color: @ink@; background: none; }\n"
".lumaui-tv-frame .lumaui-part-row.vertical > :not(:first-child) entry:focus-within { box-shadow: inset 0 0 0 3tv @accent@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within.vertical > :not(:first-child) entry { background-color: #22252c; }\n"
/* a drawn lead on the key: the key's ink, as its text */
".lumaui-tv-frame .lumaui-part-row:focus-within .lumaui-part-mark { filter: brightness(0.15); }\n"
/* everything focusable that is not in a row: the key when it holds the focus */
".lumaui-tv-frame > .luma-island button:focus-visible { border-radius: 22tv; background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; outline: none; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-row button:focus-visible { background: none; box-shadow: none; color: inherit; }\n"
/* the toast: the shared TV toast (tv/toast.js): bar material, 76 tall, one thing said */
"window.lumaui-tv .lumaui-toast { min-height: 76tv; padding: 12tv 30tv 12tv 16tv; border-radius: 28tv; border: none; border-spacing: 18tv;\n"
"  background-color: rgba(44, 46, 52, 0.94); background-image: none; color: @ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
"window.lumaui-tv .lumaui-toast .lumaui-toast-text, window.lumaui-tv .lumaui-toast label { font-family: Figtree, sans-serif; font-size: 25tv; font-weight: 650; color: @ink@; }\n"
"window.lumaui-tv .lumaui-toast .lumaui-toast-icon { min-width: 52tv; min-height: 52tv; border-radius: 16tv; -gtk-icon-size: 28tv;\n"
"  background-color: alpha(white, 0.08); color: @ink@; }\n"
/* a question over the page (LumaUI's dialog, libadwaita's dialogs): a TV sheet, TV type and buttons */
"window.lumaui-tv .lumaui-dialog, window.lumaui-tv dialog > .dialog-contents, window.lumaui-tv dialog.alert > widget {\n"
"  min-width: 760tv; padding: 40tv; border-radius: 36tv; border: none; border-spacing: 18tv;\n"
"  background-color: @sheet@; background-image: none; color: @ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
"window.lumaui-tv .lumaui-dialog label, window.lumaui-tv dialog label { font-family: Figtree, sans-serif; font-size: 26tv; font-weight: 500;\n"
"  color: @ink_2@; line-height: 1.25; }\n"
"window.lumaui-tv .lumaui-dialog label:first-child, window.lumaui-tv .lumaui-dialog .lumaui-t-title-2, window.lumaui-tv .lumaui-dialog .lumaui-t-title-1,\n"
"window.lumaui-tv dialog label.title-2, window.lumaui-tv dialog label.heading { font-size: 34tv; font-weight: 700; color: @ink@; }\n"
"window.lumaui-tv .lumaui-dialog image, window.lumaui-tv dialog image { -gtk-icon-size: 36tv; }\n"
"window.lumaui-tv .lumaui-dialog-buttons { border-spacing: 16tv; margin-top: 12tv; }\n"
"window.lumaui-tv .lumaui-dialog button, window.lumaui-tv dialog button { min-height: 66tv; padding: 0 30tv; border-radius: 22tv; border: none;\n"
"  font-size: 25tv; font-weight: 650; color: @ink@; background-color: @chip@; background-image: none; outline: none;\n"
"  box-shadow: inset 0 1.5tv 0 @chip_glint@, 0 0 0 1.5tv @chip_rim@, 0 2tv 5tv @chip_drop@; }\n"
"window.lumaui-tv .lumaui-dialog button label, window.lumaui-tv dialog button label { font-size: 25tv; font-weight: 650; color: inherit; }\n"
"window.lumaui-tv .lumaui-dialog button.lumaui-dialog-action, window.lumaui-tv dialog button.destructive-action { background-color: #c8364a; color: white; }\n"
"window.lumaui-tv .lumaui-dialog button:focus-visible, window.lumaui-tv dialog button:focus-visible {\n"
"  background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; }\n"
"window.lumaui-tv .lumaui-dialog button.lumaui-dialog-action:focus-visible { box-shadow: 0 0 0 4tv white; background-image: none; background-color: #e0485c; color: white; }\n"
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

/* The unit comes from the screen the window is on, not the window: in TV posture the window is full
 * screen there, and taking it before the window has grown to it drew the first frames at the wrong
 * size. The window's own size is used only where no monitor is known. */
static double screen_unit(TvFrame *f) {
  GdkDisplay *display = gtk_widget_get_display(GTK_WIDGET(f->window));
  GdkSurface *surface = gtk_native_get_surface(GTK_NATIVE(f->window));
  GdkMonitor *monitor = surface != NULL ? gdk_display_get_monitor_at_surface(display, surface) : NULL;
  g_autoptr(GdkMonitor) first = NULL;
  if (monitor == NULL) {
    GListModel *monitors = gdk_display_get_monitors(display);
    if (monitors != NULL && g_list_model_get_n_items(monitors) == 1) /* one screen: it is the one */
      monitor = first = g_list_model_get_item(monitors, 0);
  }
  if (monitor != NULL) {
    GdkRectangle geometry;
    gdk_monitor_get_geometry(monitor, &geometry);
    if (geometry.width > 0 && geometry.height > 0)
      return luma_ui_tv_unit(geometry.width, geometry.height);
  }
  int w = gtk_widget_get_width(f->room), h = gtk_widget_get_height(f->room);
  return luma_ui_tv_unit(w > 0 ? w : 1920, h > 0 ? h : 1080);
}

static void room_resized(gpointer data) {
  TvFrame *f = data;
  double unit = screen_unit(f);
  /* Not during allocation: the new sheet relayouts, so it loads on the next idle. */
  if (unit > 0 && fabs(unit - f->unit) >= 0.001 && f->restyle_id == 0)
    f->restyle_id = g_idle_add(restyle, f);
}

static gboolean restyle(gpointer data) {
  TvFrame *f = data;
  f->restyle_id = 0;
  if (!f->active)
    return G_SOURCE_REMOVE;
  double unit = screen_unit(f);
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

/* The page's parts and its clamp, while on a TV.
 *
 * A page that names its parts (luma_ui_set_part) is drawn from those names. A page built from
 * libadwaita's own preference widgets needs nothing: a preferences group is a group, a boxed list
 * its card, and every preferences row (action, switch, combo, entry, expander, button, spin) a row.
 * These names are added here and taken away again when the posture ends. A clamp is released to
 * the page's full width (the safe margin, no maximum). */
static gboolean has_part(GtkWidget *widget) {
  static const char *const parts[] = {"lumaui-part-header", "lumaui-part-back", "lumaui-part-hero", "lumaui-part-group",
                                      "lumaui-part-card", "lumaui-part-row", NULL};
  for (guint i = 0; parts[i] != NULL; i++)
    if (gtk_widget_has_css_class(widget, parts[i])) return TRUE;
  return FALSE;
}

static const char *adw_part(GtkWidget *widget) {
  if (ADW_IS_PREFERENCES_GROUP(widget)) return "lumaui-part-group";
  if (GTK_IS_LIST_BOX(widget) && gtk_widget_has_css_class(widget, "boxed-list")) return "lumaui-part-card";
  if (ADW_IS_PREFERENCES_ROW(widget)) return "lumaui-part-row";
  return NULL;
}

/* A row's lead: 48 units, then 20 to the text (the mockup's .tv-row gap), so the text and the row's
 * divider (inset 22 + 48 + 20) line up whatever the lead is. A drawn lead (a mark such as signal
 * bars, not an icon) is drawn larger about its centre in that place. */
static gboolean within_row(GtkWidget *widget) {
  for (GtkWidget *w = gtk_widget_get_parent(widget); w != NULL; w = gtk_widget_get_parent(w)) {
    if (gtk_widget_has_css_class(w, "lumaui-part-row")) return TRUE;
    if (gtk_widget_has_css_class(w, "lumaui-part-hero")) return FALSE;
  }
  return FALSE;
}

static void place_lead(TvFrame *f, GtkWidget *lead) {
  if (g_object_get_data(G_OBJECT(lead), "luma-tv-lead") != NULL)
    return;
  GtkWidget *line = gtk_widget_get_parent(lead);
  int *saved = g_new(int, 3);
  saved[0] = gtk_widget_get_margin_start(lead);
  saved[1] = gtk_widget_get_margin_end(lead);
  saved[2] = GTK_IS_BOX(line) ? gtk_box_get_spacing(GTK_BOX(line)) : -1;
  g_object_set_data_full(G_OBJECT(lead), "luma-tv-lead", saved, g_free);
  if (gtk_widget_has_css_class(lead, "lumaui-part-mark")) {
    int minimum = 0, natural = 0;
    gtk_widget_measure(lead, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, &natural, NULL, NULL);
    int side = MAX(0, (int)round((48 * f->unit - natural) / 2));
    gtk_widget_set_margin_start(lead, side);
    gtk_widget_set_margin_end(lead, side);
  }
  if (saved[2] >= 0)
    gtk_box_set_spacing(GTK_BOX(line), (int)round(20 * f->unit));
}

static void unplace_lead(GtkWidget *lead) {
  int *saved = g_object_get_data(G_OBJECT(lead), "luma-tv-lead");
  if (saved == NULL)
    return;
  gtk_widget_set_margin_start(lead, saved[0]);
  gtk_widget_set_margin_end(lead, saved[1]);
  GtkWidget *line = gtk_widget_get_parent(lead);
  if (saved[2] >= 0 && GTK_IS_BOX(line))
    gtk_box_set_spacing(GTK_BOX(line), saved[2]);
  g_object_set_data(G_OBJECT(lead), "luma-tv-lead", NULL);
}

static void release_page(TvFrame *f, GtkWidget *widget) {
  if ((gtk_widget_has_css_class(widget, "lumaui-part-mark") || gtk_widget_has_css_class(widget, "lumaui-part-lead")) &&
      within_row(widget))
    place_lead(f, widget);
  const char *part = adw_part(widget);
  if (part != NULL && !has_part(widget)) {
    gtk_widget_add_css_class(widget, part);
    gtk_widget_add_css_class(widget, "lumaui-tv-named");
  }
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
  }
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c))
    release_page(f, c);
}

static void restore_page(GtkWidget *widget) {
  unplace_lead(widget);
  if (gtk_widget_has_css_class(widget, "lumaui-tv-named")) {
    gtk_widget_remove_css_class(widget, "lumaui-tv-named");
    gtk_widget_remove_css_class(widget, adw_part(widget));
  }
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
  }
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c))
    restore_page(c);
}

static void dump(GtkWidget *w, int depth);
static GtkWidget *hold_modal(TvFrame *f);

/* A page that redraws under the remote (a network list refreshing) must not lose its place: the
 * focus is remembered as a path of child positions and found again in the redrawn page. */
static void remember(TvFrame *f, GtkWidget *focus) {
  g_clear_weak_pointer(&f->held);
  g_set_weak_pointer(&f->held, focus);
  g_array_set_size(f->path, 0);
  for (GtkWidget *w = focus; w != NULL && w != f->island; w = gtk_widget_get_parent(w)) {
    int index = 0;
    for (GtkWidget *c = gtk_widget_get_prev_sibling(w); c != NULL; c = gtk_widget_get_prev_sibling(c)) index++;
    g_array_prepend_val(f->path, index);
  }
}

static GtkWidget *first_field(GtkWidget *widget) {
  if (GTK_IS_EDITABLE(widget) && gtk_widget_is_drawable(widget) && gtk_widget_get_focusable(widget) &&
      gtk_editable_get_editable(GTK_EDITABLE(widget)))
    return widget;
  for (GtkWidget *c = gtk_widget_get_first_child(widget); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    GtkWidget *found = first_field(c);
    if (found != NULL) return found;
  }
  return NULL;
}

static gboolean focus_lost(TvFrame *f) {
  GtkWidget *focus = focus_of(f);
  return focus == NULL || (in_page(f, focus) && (!gtk_widget_get_focusable(focus) || !gtk_widget_is_drawable(focus)));
}

static GtkWidget *path_widget(TvFrame *f) {
  GtkWidget *w = f->island;
  for (guint i = 0; i < f->path->len; i++) {
    GtkWidget *c = gtk_widget_get_first_child(w);
    for (int n = g_array_index(f->path, int, i); c != NULL && n > 0; n--) c = gtk_widget_get_next_sibling(c);
    if (c == NULL) break;
    w = c;
  }
  return w;
}

/* OK grew the row in place with a field (a network's password): the field takes the focus, and
 * with it the TV keyboard, wherever GTK put the focus while the page was redrawn. */
static gboolean grown_field(TvFrame *f) {
  if (f->ok_at == 0 || g_get_monotonic_time() - f->ok_at > 2 * G_USEC_PER_SEC || f->path->len == 0)
    return FALSE;
  GtkWidget *row = path_widget(f);
  while (row != NULL && row != f->island && !gtk_widget_has_css_class(row, "lumaui-part-row")) row = gtk_widget_get_parent(row);
  GtkWidget *field = row != NULL && row != f->island ? first_field(row) : NULL;
  if (field == NULL) {
    /* grown with actions only (Connect, Forget …): the first of them takes the focus */
    GtkWidget *grown = row != NULL && row != f->island && GTK_IS_BOX(row) ? gtk_widget_get_first_child(row) : NULL;
    grown = grown != NULL ? gtk_widget_get_next_sibling(grown) : NULL;
    if (grown == NULL || !gtk_widget_is_drawable(grown))
      return FALSE;
    f->ok_at = 0;
    return gtk_widget_child_focus(grown, GTK_DIR_TAB_FORWARD);
  }
  f->ok_at = 0;
  GtkWidget *focus = focus_of(f);
  if (focus != field && !gtk_widget_is_ancestor(focus, field))
    gtk_widget_grab_focus(field);
  return TRUE;
}

static gboolean refind(TvFrame *f) {
  /* Lost: no focus, or GTK's fallback put it elsewhere (the first control on the page) because the
   * widget the remote was on was redrawn away, with no key moving it. */
  gboolean redrawn = f->held == NULL && g_get_monotonic_time() - f->key_at > 400 * 1000;
  if (!f->page_zone || f->path->len == 0 || (!focus_lost(f) && !redrawn))
    return FALSE;
  GtkWidget *w = f->island;
  for (guint i = 0; i < f->path->len; i++) {
    GtkWidget *c = gtk_widget_get_first_child(w);
    for (int n = g_array_index(f->path, int, i); c != NULL && n > 0; n--) c = gtk_widget_get_next_sibling(c);
    if (c == NULL) break;
    w = c;
  }
  /* OK grew the row in place with a field (a network's password): the field takes the focus,
   * and with it the TV keyboard. */
  if (g_get_monotonic_time() - f->ok_at < 2 * G_USEC_PER_SEC) {
    GtkWidget *row = w;
    while (row != NULL && row != f->island && !gtk_widget_has_css_class(row, "lumaui-part-row")) row = gtk_widget_get_parent(row);
    GtkWidget *field = row != NULL && row != f->island ? first_field(row) : NULL;
    if (field != NULL && gtk_widget_grab_focus(field)) {
      f->ok_at = 0;
      return TRUE;
    }
  }
  for (; w != NULL && w != gtk_widget_get_parent(f->island); w = gtk_widget_get_parent(w)) {
    if (!gtk_widget_is_drawable(w)) continue;
    if ((gtk_widget_get_focusable(w) && gtk_widget_grab_focus(w)) || gtk_widget_child_focus(w, GTK_DIR_TAB_FORWARD))
      return TRUE;
  }
  return FALSE;
}

static gboolean relax(gpointer data) {
  TvFrame *f = data;
  f->relax_id = 0;
  if (f->active) {
    release_page(f, f->island);
    if (hold_modal(f) != NULL) return G_SOURCE_REMOVE;
    if (!grown_field(f))
      refind(f);
    /* Until the remote is used, the focus is the open page's destination (a deep link opened it). */
    GtkWidget *focus = focus_of(f);
    GtkListBoxRow *selected = gtk_list_box_get_selected_row(sidebar_list(f));
    if (!f->keyed && selected != NULL && focus != NULL && gtk_widget_is_ancestor(focus, f->sidebar) &&
        focus != GTK_WIDGET(selected))
      gtk_widget_grab_focus(GTK_WIDGET(selected));
    if (g_getenv("LUMA_TV_DEBUG") != NULL && g_getenv("LUMA_TV_DEBUG")[0] == '2') dump(f->island, 0);
  }
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
  gtk_window_set_focus_visible(f->window, TRUE);
  if (refind(f))
    return G_SOURCE_REMOVE;
  if (focus_lost(f)) {
    f->page_zone = FALSE;
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

static void focus_visible_changed(GtkWindow *window, GParamSpec *pspec, TvFrame *f) {
  (void)pspec;
  if (f->active && !gtk_window_get_focus_visible(window))
    gtk_window_set_focus_visible(window, TRUE); /* a TV has no pointer: the focus always shows */
}

static void focus_changed(GtkWindow *window, GParamSpec *pspec, TvFrame *f) {
  (void)window; (void)pspec;
  if (!f->active)
    return;
  GtkWidget *focus = focus_of(f);
  if (g_get_monotonic_time() - f->key_at > 300 * 1000)
    f->moved_at = g_get_monotonic_time();
  if (g_getenv("LUMA_TV_DEBUG") != NULL) {
    g_auto(GStrv) classes = focus ? gtk_widget_get_css_classes(focus) : NULL;
    g_autofree char *joined = classes ? g_strjoinv(".", classes) : NULL;
    g_printerr("tv focus: %s %s.%s page=%d\n", focus ? G_OBJECT_TYPE_NAME(focus) : "(none)",
               focus ? gtk_widget_get_css_name(focus) : "", joined ? joined : "", focus ? in_page(f, focus) : 0);
  }
  if (focus_lost(f) || (f->page_zone && f->held == NULL && g_get_monotonic_time() - f->key_at > 400 * 1000)) {
    queue_relax(f);  /* a page redrawn under the focus: it is found again there */
    queue_settle(f); /* or, failing that, the island takes it back */
    return;
  }
  if (in_page(f, focus) && gtk_widget_get_focusable(focus)) {
    f->page_zone = TRUE;
    if (f->ok_at == 0 || g_get_monotonic_time() - f->ok_at > 2 * G_USEC_PER_SEC) {
      remember(f, focus);
    } else {
      /* waiting for a row to grow: keep the row's path, but the focus is held where it now is */
      g_clear_weak_pointer(&f->held);
      g_set_weak_pointer(&f->held, focus);
    }
  } else if (gtk_widget_is_ancestor(focus, f->sidebar)) {
    f->page_zone = FALSE;
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

/* A question over the page (LumaUI's dialog in the window's modal layer, a libadwaita dialog): on a
 * TV it is driven like the page, the remote's focus held inside it, Back as its Cancel. */
static gboolean is_modal(GtkWidget *w) {
  return gtk_widget_has_css_class(w, "lumaui-dialog") || ADW_IS_DIALOG(w);
}

static GtkWidget *find_modal(GtkWidget *w) {
  if (!gtk_widget_get_visible(w))
    return NULL;
  if (is_modal(w) && gtk_widget_get_mapped(w))
    return w;
  for (GtkWidget *c = gtk_widget_get_last_child(w); c != NULL; c = gtk_widget_get_prev_sibling(c)) {
    GtkWidget *found = find_modal(c);
    if (found != NULL) return found;
  }
  return NULL;
}

static GtkWidget *find_class(GtkWidget *w, const char *cls) {
  if (gtk_widget_has_css_class(w, cls) && gtk_widget_is_drawable(w))
    return w;
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    GtkWidget *found = find_class(c, cls);
    if (found != NULL) return found;
  }
  return NULL;
}

/* Bring the remote into an open question: its Cancel first (the safe answer), else its first control. */
static GtkWidget *hold_modal(TvFrame *f) {
  GtkWidget *modal = find_modal(GTK_WIDGET(f->window));
  if (modal == NULL)
    return NULL;
  GtkWidget *focus = focus_of(f);
  if (focus == NULL || (focus != modal && !gtk_widget_is_ancestor(focus, modal))) {
    GtkWidget *cancel = find_class(modal, "lumaui-dialog-cancel");
    if (cancel == NULL || !gtk_widget_grab_focus(cancel))
      gtk_widget_child_focus(modal, GTK_DIR_TAB_FORWARD);
  }
  return modal;
}

static gboolean modal_key(TvFrame *f, GtkWidget *modal, guint key) {
  GtkDirectionType dir;
  switch (key) {
  case GDK_KEY_Up: case GDK_KEY_KP_Up: dir = GTK_DIR_UP; break;
  case GDK_KEY_Down: case GDK_KEY_KP_Down: dir = GTK_DIR_DOWN; break;
  case GDK_KEY_Left: case GDK_KEY_KP_Left: dir = GTK_DIR_LEFT; break;
  case GDK_KEY_Right: case GDK_KEY_KP_Right: dir = GTK_DIR_RIGHT; break;
  default:
    if (key == GDK_KEY_Escape || key == GDK_KEY_Back) {
      GtkWidget *cancel = find_class(modal, "lumaui-dialog-cancel");
      if (cancel != NULL) gtk_widget_activate(cancel);
      else if (ADW_IS_DIALOG(modal)) adw_dialog_close(ADW_DIALOG(modal));
      return GDK_EVENT_STOP;
    }
    return GDK_EVENT_PROPAGATE; /* OK presses the focused button; typing types */
  }
  GtkWidget *focus = focus_of(f);
  if (focus != NULL && editable(focus) && (dir == GTK_DIR_LEFT || dir == GTK_DIR_RIGHT))
    return GDK_EVENT_PROPAGATE;
  if (!gtk_widget_child_focus(modal, dir))
    gtk_widget_child_focus(modal, dir == GTK_DIR_UP || dir == GTK_DIR_LEFT ? GTK_DIR_TAB_BACKWARD : GTK_DIR_TAB_FORWARD);
  return GDK_EVENT_STOP;
}

static gboolean key_pressed(GtkEventControllerKey *controller, guint key, guint code,
                            GdkModifierType state, TvFrame *f) {
  (void)controller; (void)code;
  if (!f->active || (state & (GDK_CONTROL_MASK | GDK_ALT_MASK | GDK_SUPER_MASK)) != 0)
    return GDK_EVENT_PROPAGATE;
  /* OK right after the focus moved by itself (the page redrew under the remote) acts on nothing:
   * the remote never acts on a control it did not choose (a power switch beside a list). */
  if (is_ok(key) && g_get_monotonic_time() - f->moved_at < 600 * 1000 && f->moved_at > f->key_at) {
    f->key_at = g_get_monotonic_time();
    return GDK_EVENT_STOP;
  }
  f->key_at = g_get_monotonic_time();
  if (is_ok(key)) {
    g_clear_weak_pointer(&f->ok_widget);
    g_set_weak_pointer(&f->ok_widget, focus_of(f));
  }
  GtkWidget *modal = hold_modal(f);
  if (modal != NULL) {
    f->keyed = TRUE;
    gtk_window_set_focus_visible(f->window, TRUE);
    return modal_key(f, modal, key);
  }
  GtkWidget *focus = focus_of(f);
  gboolean page = in_page(f, focus);
  f->keyed = TRUE;
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
  if (is_ok(key) && !text)
    f->ok_at = g_get_monotonic_time();
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

/* OK released on something other than what it was pressed on (the page redrew under it and the
 * focus moved): the release does nothing, so one OK never acts twice (a switch beside a row). */
static gboolean key_released(GtkEventControllerKey *controller, guint key, guint code, GdkModifierType state, TvFrame *f) {
  (void)controller; (void)code; (void)state;
  if (!f->active || !is_ok(key))
    return GDK_EVENT_PROPAGATE;
  GtkWidget *focus = focus_of(f);
  gboolean moved = f->ok_widget == NULL || focus != f->ok_widget;
  g_clear_weak_pointer(&f->ok_widget);
  return moved ? GDK_EVENT_STOP : GDK_EVENT_PROPAGATE;
}

/* ── on and off ───────────────────────────────────────────────────────────────────────────── */

/* The TV keyboard covers the bottom of the screen: the page ends above it, so the focused field
 * (and its row) scrolls into view over the keyboard. */
static void keyboard_height(guint height, gpointer data) {
  TvFrame *f = data;
  if (!f->active) return;
  gtk_widget_set_margin_bottom(f->island, height > 0 ? (int)round(height * f->unit) : f->island_bottom);
}

static void active_changed(GtkWindow *window, GParamSpec *pspec, TvFrame *f) {
  (void)pspec; (void)f;
  g_printerr("tv window active=%d\n", gtk_window_is_active(window));
}

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
  f->unit = screen_unit(f);
  tv_css(f);

  f->keys = gtk_event_controller_key_new();
  gtk_event_controller_set_propagation_phase(f->keys, GTK_PHASE_CAPTURE);
  g_signal_connect(f->keys, "key-pressed", G_CALLBACK(key_pressed), f);
  g_signal_connect(f->keys, "key-released", G_CALLBACK(key_released), f);
  gtk_widget_add_controller(GTK_WIDGET(f->window), f->keys);
  f->focus_id = g_signal_connect(f->window, "notify::focus-widget", G_CALLBACK(focus_changed), f);
  f->island_bottom = gtk_widget_get_margin_bottom(f->island);
  f->keyboard = luma_tv_keyboard_watch(keyboard_height, f);
  f->visible_id = g_signal_connect(f->window, "notify::focus-visible", G_CALLBACK(focus_visible_changed), f);
  if (g_getenv("LUMA_TV_DEBUG") != NULL)
    f->active_id = g_signal_connect(f->window, "notify::is-active", G_CALLBACK(active_changed), f);
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
  luma_tv_keyboard_unwatch(f->keyboard);
  f->keyboard = NULL;
  gtk_widget_set_margin_bottom(f->island, f->island_bottom);
  g_clear_signal_handler(&f->visible_id, f->window);
  g_clear_signal_handler(&f->active_id, f->window);
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
  g_array_unref(f->path);
  g_clear_weak_pointer(&f->ok_widget);
  g_clear_weak_pointer(&f->held);
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
  f->path = g_array_new(FALSE, FALSE, sizeof(int));
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

gboolean luma_tv_frame_key(GtkWindow *window, guint keyval) {
  TvFrame *f = g_object_get_data(G_OBJECT(window), "luma-tv-frame");
  return f != NULL && key_pressed(NULL, keyval, 0, 0, f) == GDK_EVENT_STOP;
}

void luma_tv_frame_page_changed(GtkWindow *window) {
  TvFrame *f = window != NULL ? g_object_get_data(G_OBJECT(window), "luma-tv-frame") : NULL;
  if (f != NULL && f->active)
    queue_relax(f);
}

gboolean luma_tv_frame_is_active(GtkWindow *window) {
  TvFrame *f = g_object_get_data(G_OBJECT(window), "luma-tv-frame");
  return f != NULL && f->active;
}
