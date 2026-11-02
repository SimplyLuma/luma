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
static gboolean tv_debug_grab(GtkWidget *w, const char *where) {
  if (g_getenv("LUMA_TV_DEBUG") != NULL) g_printerr("tv grab by %s -> %s\n", where, G_OBJECT_TYPE_NAME(w));
  return gtk_widget_grab_focus(w);
}
#define gtk_widget_grab_focus(w) tv_debug_grab((w), G_STRFUNC)
static gboolean tv_debug_child(GtkWidget *w, GtkDirectionType d, const char *where) {
  gboolean r = gtk_widget_child_focus(w, d);
  if (g_getenv("LUMA_TV_DEBUG") != NULL) g_printerr("tv child_focus by %s on %s dir %d -> %d\n", where, G_OBJECT_TYPE_NAME(w), d, r);
  return r;
}
#define gtk_widget_child_focus(w, d) tv_debug_child((w), (d), G_STRFUNC)

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
  /* 240 x 135, three box blurs of radius 11 (a Gaussian of ~11.5): scaled up 8 times this is the mockup's blur(90). */
  g_autoptr(GdkPixbuf) loaded = gdk_pixbuf_new_from_file_at_scale(path, 240, 135, FALSE, NULL);
  if (loaded == NULL)
    return;
  g_autoptr(GdkPixbuf) pixbuf = gdk_pixbuf_add_alpha(loaded, FALSE, 0, 0, 0);
  box_blur(gdk_pixbuf_get_pixels(pixbuf), gdk_pixbuf_get_width(pixbuf), gdk_pixbuf_get_height(pixbuf),
           gdk_pixbuf_get_rowstride(pixbuf), 11);
  /* The saturation (1.2), the dimming (brightness .45) and the shade (darker behind the island, 0.35 to 0.1 black at 60%)
   * are baked into the picture once: drawn live they were three more full-screen layers a frame. */
  int w = gdk_pixbuf_get_width(pixbuf), h = gdk_pixbuf_get_height(pixbuf), stride = gdk_pixbuf_get_rowstride(pixbuf);
  guchar *px = gdk_pixbuf_get_pixels(pixbuf);
  for (int x = 0; x < w; x++) {
    double t = (double)x / MAX(1, w - 1);
    double shade = t < 0.6 ? 0.35 + (0.1 - 0.35) * (t / 0.6) : 0.1;
    double keep = 0.45 * (1 - shade);
    for (int y = 0; y < h; y++) {
      guchar *p = px + y * stride + x * 4;
      /* the mockup's saturate(1.2) (the CSS filter matrix), then the dimming */
      double r = p[0], g = p[1], b = p[2], s = 1.2;
      double r2 = (0.213 + 0.787 * s) * r + (0.715 - 0.715 * s) * g + (0.072 - 0.072 * s) * b;
      double g2 = (0.213 - 0.213 * s) * r + (0.715 + 0.285 * s) * g + (0.072 - 0.072 * s) * b;
      double b2 = (0.213 - 0.213 * s) * r + (0.715 - 0.715 * s) * g + (0.072 + 0.928 * s) * b;
      p[0] = (guchar)CLAMP(r2 * keep + 0.5, 0, 255); p[1] = (guchar)CLAMP(g2 * keep + 0.5, 0, 255);
      p[2] = (guchar)CLAMP(b2 * keep + 0.5, 0, 255);
      p[3] = 255;
    }
  }
  G_GNUC_BEGIN_IGNORE_DEPRECATIONS
  self->texture = gdk_texture_new_for_pixbuf(pixbuf);
  G_GNUC_END_IGNORE_DEPRECATIONS
}

static void room_snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaTvRoom *self = LUMA_TV_ROOM(widget);
  float width = gtk_widget_get_width(widget), height = gtk_widget_get_height(widget);
  double u = luma_ui_tv_unit((int)width, (int)height);
  graphene_rect_t all = GRAPHENE_RECT_INIT(0, 0, width, height);
  if (self->texture == NULL) {
    gtk_snapshot_append_color(snapshot, &(GdkRGBA){0.02f, 0.024f, 0.04f, 1.f}, &all);
    return;
  }
  /* One opaque layer: the picture, dimmed and shaded at load, bled past the edges (the mockup's
   * inset -120) so the blur never shows a hard border. */
  float bleed = (float)(120 * u);
  graphene_rect_t cover = GRAPHENE_RECT_INIT(-bleed, -bleed, width + 2 * bleed, height + 2 * bleed);
  gtk_snapshot_push_clip(snapshot, &all);
  gtk_snapshot_append_scaled_texture(snapshot, self->texture, GSK_SCALING_FILTER_LINEAR, &cover);
  gtk_snapshot_pop(snapshot);
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
  gboolean held_field;   /* the remote was in a text field */
  guint reveal_id;
  int reveal_tries;      /* reveal_focus looks again while the page settles above the keyboard */
  int reveal_from;       /* the page's height when the keyboard came up */
  gint64 reveal_until;
  int self_moving;       /* the frame itself is moving the focus */
  char *held_title;      /* the title of the row the remote was on: rows are found by it, not by place */
  char *held_page;       /* the page's title then: a redraw keeps it, opening another page changes it */
  int keying;            /* the frame is handling a key: focus moves now are the remote's own */
  guint row_depth;       /* where the held row is in the path: below it, the path inside the row */
  gint64 probe_at;       /* LUMA_TV_DEBUG timing: when the key arrived */
  gulong active_id;
  gpointer keyboard;     /* the TV keyboard's height, followed */
  int island_bottom;     /* the island's own bottom margin, while the keyboard lifts it */
  gboolean page_zone;    /* the remote is in the page */
  /* luma_window_set_tv_frame (any window, any sidebar): */
  gboolean generic;      /* the sidebar is not a LumaNavigationSidebar: it is told by classes */
  GtkWidget *scroll;     /* a generic sidebar's scrolled list */
  GtkAlign sidebar_valign;
  GtkWidget *page_modal; /* a modal card drawn as a page in the frame (weak) */
  GtkWidget *app_focus;  /* what the app had focused when the frame took the window (weak) */
  guint restore_id;
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
/* a line as tall as the mockup's normal line height (Pango's factor is of the font's own height) */
".lumaui-tv-frame > .luma-island label { font-family: Figtree, sans-serif; line-height: 1; }\n"
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
"  letter-spacing: -1.56tv; color: @ink@; line-height: 0.88; }\n"
/* the mockup's .tv-h1 (line-height 1.05) and .tv-body under it (8 over, line-height 1.4): ratios of Figtree's own line */
".lumaui-tv-frame .lumaui-part-header .lumaui-t-meta { font-size: 26tv; font-weight: 500; color: @ink_2@; margin-top: 8tv; line-height: 1.14; }\n"
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
/* a hero's words (the mockup's .hero b and small): the first line is its title, the lines under it its detail,
 * a line of state (a dot and words) as the detail in ink 2; a control's own words keep the control's size */
".lumaui-tv-frame > .luma-island .lumaui-part-hero box.vertical > label:first-child { font-size: 34tv; font-weight: 700; letter-spacing: -0.68tv; color: @ink@; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero box.vertical > label:not(:first-child) { font-size: 22tv; font-weight: 400; color: @ink_3@; margin-top: 6tv; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero box.vertical > box { margin-top: 6tv; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero box.vertical > box label { font-size: 22tv; font-weight: 400; color: @ink_2@; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero button box.vertical > label { font-size: 26tv; font-weight: 600; letter-spacing: 0; color: inherit; margin: 0; }\n"
/* a hero's actions are TV buttons (the mockup's .tv-btn): 66 tall, radius 22, 25/650, a chip; the focused one the key */
".lumaui-tv-frame > .luma-island .lumaui-part-hero button.text-button { min-height: 66tv; padding: 0 30tv; border-radius: 22tv; border: none; outline: none;\n"
"  background-color: @chip@; background-image: none; color: @ink@; box-shadow: inset 0 1.5tv 0 @chip_glint@, 0 0 0 1.5tv @chip_rim@, 0 2tv 5tv @chip_drop@; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero button.text-button label { font-size: 25tv; font-weight: 650; color: inherit; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero button.text-button:focus-visible { background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; }\n"
/* the hero's own title over that: the mockup's .hero b, 34/700 */
".lumaui-tv-frame > .luma-island .lumaui-part-hero label.lumaui-t-hero-title, .lumaui-tv-frame > .luma-island .lumaui-part-hero label.lumaui-t-hero,\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero label.lumaui-t-title-1 { font-size: 34tv; font-weight: 700; letter-spacing: -0.68tv; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-hero image { -gtk-icon-size: 46tv; min-width: 46tv; min-height: 46tv; }\n"
/* libadwaita's rows and groups, named by the frame */
".lumaui-tv-frame row.lumaui-part-row, .lumaui-tv-frame .lumaui-part-row { min-height: 60tv; }\n"
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
".lumaui-tv-frame .lumaui-part-hero .lumaui-part-mark { transform: scale(1.533); transform-origin: 50% 50%; margin: 0 28tv 0 16tv;\n"
"  border: none; outline: none; box-shadow: none; background: none; }\n"
".lumaui-tv-frame .lumaui-part-hero .lumaui-part-mark * { border: none; outline: none; box-shadow: none; }\n"
".lumaui-tv-frame .lumaui-part-hero .lumaui-part-lead { border: none; outline: none; }\n"
/* a lead that is a picture in full colour (a logo, an app's icon): itself, no tile or ring behind it */
".lumaui-tv-frame .lumaui-part-lead.lumaui-part-picture, .lumaui-tv-frame .lumaui-part-row:focus-within .lumaui-part-lead.lumaui-part-picture {\n"
"  background: none; box-shadow: none; border: none; outline: none; }\n"
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
/* a row's value (what is chosen, a fact) in ink 2, a slider's figure at 22/650 (the mockup's .val and .tv-slider b) */
".lumaui-tv-frame .lumaui-part-row label:not(.lumaui-t-row-title):not(.lumaui-t-row-subtitle):not(.title):not(.subtitle) { color: @ink_2@; }\n"
".lumaui-tv-frame .lumaui-part-row scale ~ label { font-size: 22tv; font-weight: 650; color: @ink@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within label:not(.lumaui-t-row-title):not(.lumaui-t-row-subtitle):not(.title):not(.subtitle) { color: alpha(@key_ink@, 0.62); }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within scale ~ label { color: @key_ink@; }\n"
".lumaui-tv-frame .lumaui-part-row .lumaui-part-lead { min-width: 48tv; min-height: 48tv; border-radius: 14tv; padding: 0; margin: 0;\n"
"  background-color: alpha(white, 0.08); color: @ink_2@; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within .lumaui-part-lead { background-color: alpha(black, 0.08); color: @key_ink@; }\n"
".lumaui-tv-frame .lumaui-part-row image { -gtk-icon-size: 26tv; color: inherit; }\n"
/* a row's trailing glyph (a chevron, a lock) in ink 3 (the mockup's .val svg); a lead keeps its tile's ink */
".lumaui-tv-frame .lumaui-part-row image:not(.lumaui-part-lead) { color: @ink_3@; }\n"
".lumaui-tv-frame .lumaui-part-row .lumaui-part-lead image { color: inherit; }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within image:not(.lumaui-part-lead) { color: alpha(@key_ink@, 0.62); }\n"
".lumaui-tv-frame .lumaui-part-row:focus-within .lumaui-part-lead image { color: inherit; }\n"
/* controls at TV size, inside rows */
".lumaui-tv-frame .lumaui-part-row button:not(.lumaui-part-swatch):not(.lumaui-part-chip),\n"
".lumaui-tv-frame .lumaui-part-row button:not(.lumaui-part-swatch):not(.lumaui-part-chip):hover,\n"
".lumaui-tv-frame .lumaui-part-row button:not(.lumaui-part-swatch):not(.lumaui-part-chip):focus-visible {\n"
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
/* a value field in a row (a computer's name) has room for its value at TV size */
".lumaui-tv-frame .lumaui-part-row entry { min-width: 420tv; }\n"
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
".lumaui-tv-frame > .luma-island .lumaui-part-row button:not(.lumaui-part-swatch):not(.lumaui-part-chip):focus-visible { background: none; box-shadow: none; color: inherit; }\n"
/* chip rows (lumaui-tv.css .chips): Left/Right between chips; the row holding them never lights as
 * the key, the focused chip lifts with a white ring; the chosen one keeps a quiet ring */
".lumaui-tv-frame .lumaui-part-row.lumaui-tv-holder:focus-within { background-image: none; background-color: transparent;\n"
"  box-shadow: none; color: @ink@; }\n"
".lumaui-tv-frame .lumaui-part-chips { padding: 8tv 0; }\n"
".lumaui-tv-frame .lumaui-part-chip { border-radius: 16tv; padding: 0; margin: 0; outline: none;\n"
"  box-shadow: 0 8tv 20tv alpha(black, 0.4); transition: transform 150ms ease-out; }\n"
".lumaui-tv-frame .lumaui-part-chip.on, .lumaui-tv-frame .lumaui-part-chip:checked { box-shadow: 0 0 0 3tv alpha(white, 0.6), 0 8tv 20tv alpha(black, 0.4); }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-chip:focus-visible { transform: scale(1.06); background-image: none;\n"
"  box-shadow: 0 0 0 5tv white, 0 14tv 30tv alpha(black, 0.5); }\n"
".lumaui-tv-frame .lumaui-part-swatch { min-width: 52tv; min-height: 52tv; padding: 0; margin: 0; border-radius: 9999px; outline: none;\n"
"  box-shadow: inset 0 0 0 1.5tv alpha(white, 0.2); transition: transform 150ms ease-out; }\n"
".lumaui-tv-frame .lumaui-part-swatch.on, .lumaui-tv-frame .lumaui-part-swatch:checked {\n"
"  box-shadow: inset 0 0 0 1.5tv alpha(white, 0.2), 0 0 0 4tv alpha(white, 0.5); }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-swatch:focus-visible { transform: scale(1.15); background-image: none; box-shadow: 0 0 0 4tv white; }\n"
/* a switch outside any row (a page's on/off beside its title) holding the focus: the key's ring */
".lumaui-tv-frame > .luma-island button:focus-visible switch, .lumaui-tv-frame > .luma-island switch:focus-visible {\n"
"  box-shadow: 0 0 0 4tv white, 0 3tv 10tv @key_drop@; }\n"
".lumaui-tv-frame > .luma-island .lumaui-part-row button:focus-visible switch,\n"
".lumaui-tv-frame > .luma-island button.lumaui-part-row:focus-visible switch { box-shadow: none; }\n"
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
"window.lumaui-tv .lumaui-dialog button:focus-visible label, window.lumaui-tv dialog button:focus-visible label { color: @key_ink@; }\n"
/* the sheet floats over a dimmed screen (lumaui-tv.css .tv-sheet): near opaque, as there is no live blur */
"window.lumaui-tv .lumaui-scrim, window.lumaui-tv dialog-host > dimming { background-color: alpha(black, 0.55); }\n"
"window.lumaui-tv .lumaui-dialog, window.lumaui-tv dialog > .dialog-contents, window.lumaui-tv dialog.alert > widget,\n"
"window.lumaui-tv popover > contents { background-color: @sheet_solid@; }\n"
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

/* Parts shared by every TV frame beyond the island's own (lumaui-tv.css): a centred window, the
 * empty state, a row of TV buttons, an external link's QR. */
static const char *const tv_parts_sheet =
/* a centred window (no sidebar): the page in the safe area under the bar, no island */
".lumaui-tv-frame.lumaui-tv-centred > .luma-island { margin: 166tv 96tv 56tv 96tv; }\n"
/* a generic sidebar in the island (luma_window_set_tv_frame): the frame sizes it as the C sidebar */
".lumaui-tv-frame > .lumaui-tv-island { margin: 166tv 0 56tv 96tv; min-width: 376tv; padding: 12tv; border-radius: 36tv;\n"
"  background-color: @bar@; background-image: none; border: none; outline: none;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
".lumaui-tv-frame > .lumaui-tv-island.lumaui-tv-rail { min-width: 76tv; }\n"
".lumaui-tv-frame > .lumaui-tv-island scrolledwindow, .lumaui-tv-frame > .lumaui-tv-island list, .lumaui-tv-frame > .lumaui-tv-island viewport {\n"
"  background: none; padding: 0; margin: 0; border: none; box-shadow: none; }\n"
".lumaui-tv-frame > .lumaui-tv-island .luma-navigation-face, .lumaui-tv-frame > .lumaui-tv-island .luma-navigation-icon {\n"
"  min-width: 30tv; min-height: 30tv; padding: 0; margin: 0; background: none; box-shadow: none; border: none; }\n"
/* a page's title and lede, named as parts (a page without LumaUI type roles) */
".lumaui-tv-frame label.lumaui-part-title { font-family: Figtree, sans-serif; font-size: 52tv; font-weight: 750; letter-spacing: -1.56tv; color: @ink@; }\n"
".lumaui-tv-frame label.lumaui-part-lede { font-family: Figtree, sans-serif; font-size: 26tv; font-weight: 500; color: @ink_2@; margin-top: 8tv; }\n"
".lumaui-tv-frame .lumaui-part-header { border-spacing: 0; margin-bottom: 6tv; }\n"
".lumaui-tv-frame .lumaui-part-header > box { border-spacing: 24tv; }\n"
/* a centred window: the emblem with soft light behind it, a large title, a balanced lede, its
 * buttons in a row, the footnote on the bottom safe line */
".lumaui-tv-frame image.lumaui-part-emblem { -gtk-icon-size: 184tv; min-width: 184tv; min-height: 184tv; margin: 0;\n"
"  -gtk-icon-shadow: 0 24tv 40tv rgba(0, 0, 0, 0.45), 0 0 150tv rgba(84, 140, 255, 0.34); }\n"
".lumaui-tv-frame image.lumaui-part-emblem.lumaui-light-orange { -gtk-icon-shadow: 0 24tv 40tv rgba(0, 0, 0, 0.45), 0 0 150tv rgba(255, 128, 48, 0.38); }\n"
".lumaui-tv-centred label.lumaui-part-title { font-size: 40tv; font-weight: 700; letter-spacing: -0.8tv; margin-top: 0; }\n"
".lumaui-tv-centred image.lumaui-part-emblem ~ label.lumaui-part-title { font-size: 64tv; font-weight: 750; letter-spacing: -1.6tv; margin-top: 44tv; }\n"
".lumaui-tv-centred label.lumaui-part-lede { font-size: 28tv; margin-top: 14tv; }\n"
".lumaui-tv-centred .lumaui-part-buttons { margin-top: 44tv; }\n"
".lumaui-tv-frame label.lumaui-part-footnote { font-family: Figtree, sans-serif; font-size: 20tv; font-weight: 600; color: @ink_3@; margin: 0; }\n"
/* TV buttons (66 tall, radius 22, 25/650): a row of buttons, an empty state's actions, a page's
 * FlowBox of actions. The suggested one rests in the accent; the focused one is the key. */
".lumaui-tv-frame .lumaui-part-buttons { border-spacing: 14tv; }\n"
".lumaui-tv-frame .lumaui-part-buttons > button, .lumaui-tv-frame > .luma-island flowboxchild > button,\n"
".lumaui-tv-frame .luma-empty-actions button {\n"
"  min-height: 66tv; min-width: 130tv; padding: 0 30tv; margin: 0; border-radius: 22tv; border: none; outline: none;\n"
"  font-family: Figtree, sans-serif; font-size: 25tv; font-weight: 650; color: @ink@; background-color: @chip@; background-image: none;\n"
"  box-shadow: inset 0 1.5tv 0 @chip_glint@, 0 0 0 1.5tv @chip_rim@, 0 2tv 5tv @chip_drop@; }\n"
".lumaui-tv-frame .lumaui-part-buttons > button label, .lumaui-tv-frame > .luma-island flowboxchild > button label,\n"
".lumaui-tv-frame .luma-empty-actions button label { font-size: 25tv; font-weight: 650; color: inherit; }\n"
".lumaui-tv-frame > .luma-island flowboxchild > button image, .lumaui-tv-frame .lumaui-part-buttons > button image { -gtk-icon-size: 26tv; color: inherit; }\n"
".lumaui-tv-frame .lumaui-part-buttons > button.suggested-action, .lumaui-tv-frame > .luma-island flowboxchild > button.suggested-action,\n"
".lumaui-tv-frame .luma-empty-actions button.suggested-action, .lumaui-tv-frame .luma-empty-actions button.primary { background-color: @accent@; color: white;\n"
"  box-shadow: inset 0 1.5tv 0 rgba(255, 255, 255, 0.22); }\n"
".lumaui-tv-frame .lumaui-part-buttons > button:focus-visible, .lumaui-tv-frame > .luma-island flowboxchild > button:focus-visible,\n"
".lumaui-tv-frame .luma-empty-actions button:focus-visible {\n"
"  background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; }\n"
".lumaui-tv-frame .lumaui-part-buttons > button:disabled, .lumaui-tv-frame > .luma-island flowboxchild > button:disabled { opacity: 0.38; }\n"
".lumaui-tv-frame > .luma-island flowbox { border-spacing: 14tv; padding: 4tv 0 6tv 0; }\n"
/* a page's own buttons outside its rows (a suggested or destructive action, the header's action) */
".lumaui-tv-frame > .luma-island button.suggested-action:not(.lumaui-part-row), .lumaui-tv-frame > .luma-island button.destructive-action,\n"
".lumaui-tv-frame .lumaui-part-header button:not(.lumaui-part-back) {\n"
"  min-height: 66tv; padding: 0 30tv; border-radius: 22tv; border: none; outline: none; font-size: 25tv; font-weight: 650;\n"
"  color: @ink@; background-color: @chip@; background-image: none;\n"
"  box-shadow: inset 0 1.5tv 0 @chip_glint@, 0 0 0 1.5tv @chip_rim@, 0 2tv 5tv @chip_drop@; }\n"
".lumaui-tv-frame > .luma-island button.suggested-action:not(.lumaui-part-row) { background-color: @accent@; color: white; }\n"
".lumaui-tv-frame > .luma-island button.destructive-action { color: #ff8a80; }\n"
".lumaui-tv-frame > .luma-island button.suggested-action label, .lumaui-tv-frame > .luma-island button.destructive-action label,\n"
".lumaui-tv-frame .lumaui-part-header button label { font-size: 25tv; font-weight: 650; color: inherit; }\n"
".lumaui-tv-frame > .luma-island button.suggested-action:focus-visible, .lumaui-tv-frame > .luma-island button.destructive-action:focus-visible,\n"
".lumaui-tv-frame .lumaui-part-header button:focus-visible {\n"
"  background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; }\n"
".lumaui-tv-frame > .luma-island flowboxchild { padding: 0; margin: 0; background: none; outline: none; }\n"
/* the empty state (luma_appkit EmptyState): a bar card, a 112 icon tile with soft accent light,
 * a 34/700 title, a 24 sentence, then its actions */
".lumaui-tv-frame .luma-empty-state { margin-top: 30tv; padding: 64tv 40tv 56tv 40tv; border-radius: 30tv; border: none;\n"
"  background-color: @bar@; background-image: none; box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
".lumaui-tv-frame .luma-empty-state .luma-empty-disc { min-width: 112tv; min-height: 112tv; border-radius: 32tv; margin: 0;\n"
"  background-color: alpha(white, 0.08); background-image: none; box-shadow: 0 0 120tv 30tv rgba(84, 140, 255, 0.16); }\n"
".lumaui-tv-frame .luma-empty-state image.luma-empty-icon { -gtk-icon-size: 54tv; color: @ink_2@; }\n"
".lumaui-tv-frame .luma-empty-state label.luma-empty-title { font-family: Figtree, sans-serif; font-size: 34tv; font-weight: 700; letter-spacing: -0.68tv; color: @ink@; margin-top: 28tv; }\n"
".lumaui-tv-frame .luma-empty-state label.luma-empty-description { font-family: Figtree, sans-serif; font-size: 24tv; font-weight: 500; color: @ink_2@; margin-top: 10tv; }\n"
".lumaui-tv-frame .luma-empty-state .luma-empty-actions { margin-top: 32tv; }\n"
/* a modal card over the page (luma_appkit LayerHost): a TV sheet */
"window.lumaui-tv .lumaui-scrim { background-color: rgba(3, 4, 8, 0.78); background-image: none; }\n"
/* a modal card as a page in the frame (sign-in): no sheet, the page's type; ways as equal cards */
"window.lumaui-tv .lumaui-scrim.lumaui-tv-page-scrim { background-color: transparent; }\n"
"window.lumaui-tv .lumaui-modal.lumaui-tv-page-modal { min-width: 0; padding: 0; border-radius: 0; background: none; box-shadow: none; }\n"
"window.lumaui-tv .lumaui-tv-page-modal label.lumaui-t-title-1, window.lumaui-tv .lumaui-tv-page-modal label.lumaui-part-title {\n"
"  font-size: 52tv; font-weight: 750; letter-spacing: -1.56tv; color: @ink@; }\n"
"window.lumaui-tv .lumaui-tv-page-modal > * { border-spacing: 0; }\n"
"window.lumaui-tv .lumaui-tv-page-modal box.vertical { border-spacing: 18tv; }\n"
"window.lumaui-tv .lumaui-tv-page-modal label { font-size: 26tv; color: @ink_2@; }\n"
".lumaui-part-ways { border-spacing: 24tv; margin-top: 10tv; }\n"
"window.lumaui-tv .lumaui-part-way { padding: 36tv; border-radius: 30tv; background-color: @bar@; background-image: none; border: none;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
"window.lumaui-tv .lumaui-part-way label { font-size: 24tv; line-height: 1.42; color: @ink_2@; }\n"
"window.lumaui-tv .lumaui-part-way .lumaui-qr { min-width: 232tv; min-height: 232tv; padding: 16tv; border-radius: 22tv; background-color: white; margin-top: 18tv; }\n"
"window.lumaui-tv .lumaui-part-way label.lumaui-link-place { font-size: 21tv; font-weight: 650; color: @ink_3@; margin-top: 4tv; }\n"
/* a one-time code as boxes (luma_appkit set_code_field): wells 4 + gap + 4, the current one ringed with a caret */
"window.lumaui-tv .lumaui-code { padding: 32tv 36tv 36tv 36tv; border-radius: 30tv; background-color: @bar@; background-image: none; border-spacing: 16tv;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
"window.lumaui-tv .lumaui-code.lumaui-code-focus { box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 0 0 5tv white; }\n"
"window.lumaui-tv .lumaui-code label.lumaui-code-label { font-size: 20tv; font-weight: 650; letter-spacing: 1tv; color: @ink_3@; }\n"
"window.lumaui-tv .lumaui-code .lumaui-code-boxes { border-spacing: 14tv; }\n"
"window.lumaui-tv .lumaui-code .lumaui-code-gap { min-width: 26tv; }\n"
"window.lumaui-tv .lumaui-code label.lumaui-code-box { min-width: 84tv; min-height: 104tv; border-radius: 20tv; font-size: 52tv; font-weight: 700;\n"
"  color: @ink@; background-color: @well@; box-shadow: inset 0 0 0 1.5tv @well_rim@, inset 0 2tv 4tv @well_shade@; font-feature-settings: \"tnum\"; }\n"
"window.lumaui-tv .lumaui-code label.lumaui-code-box.now { box-shadow: inset 0 0 0 1.5tv @well_rim@, inset 0 0 0 3tv @accent@; }\n"
"window.lumaui-tv box.lumaui-code-source > label, window.lumaui-tv .lumaui-modal box.lumaui-code-source > entry, window.lumaui-tv box.lumaui-code-source > entry,\n"
"window.lumaui-tv .lumaui-modal box.lumaui-code-source > entry > text {\n"
"  min-height: 0; min-width: 0; padding: 0; margin: 0; border: none; box-shadow: none; background: none; font-size: 1px; opacity: 0; }\n"
"window.lumaui-tv .lumaui-code-source { border-spacing: 0; margin: 0; padding: 0; min-height: 0; }\n"
"window.lumaui-tv .lumaui-code label.lumaui-code-status { font-size: 22tv; font-weight: 600; color: @ink_3@; }\n"
"window.lumaui-tv .lumaui-code label.lumaui-code-status.lumaui-code-problem { color: #f2b45a; }\n"
/* a libadwaita dialog with pages (pairing): a TV sheet at TV size, its rows as the page's */
"window.lumaui-tv dialog.floating sheet { min-width: 1040tv; min-height: 780tv; border-radius: 36tv; border: none;\n"
"  background-color: @sheet@; background-image: none; color: @ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
"window.lumaui-tv dialog.floating dimming { background-color: rgba(3, 4, 8, 0.78); }\n"
"window.lumaui-tv dialog.floating sheet headerbar, window.lumaui-tv dialog.floating sheet toolbarview,\n"
"window.lumaui-tv dialog.floating sheet preferencespage, window.lumaui-tv dialog.floating sheet scrolledwindow,\n"
"window.lumaui-tv dialog.floating sheet viewport, window.lumaui-tv dialog.floating sheet navigation-view-page {\n"
"  background: none; box-shadow: none; color: @ink@; }\n"
"window.lumaui-tv dialog.floating sheet headerbar { min-height: 96tv; padding: 0 24tv; }\n"
"window.lumaui-tv dialog.floating sheet revealer.top-bar, window.lumaui-tv dialog.floating sheet revealer.top-bar windowhandle,\n"
"window.lumaui-tv dialog.floating sheet toolbarview > revealer > windowhandle { background: none; background-color: transparent; box-shadow: none; }\n"
"window.lumaui-tv dialog.floating sheet headerbar label.title { font-size: 30tv; font-weight: 700; color: @ink@; }\n"
"window.lumaui-tv dialog.floating sheet list.boxed-list { background-color: @bar@; border-radius: 30tv; border: none; padding: 8tv;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@; }\n"
"window.lumaui-tv dialog.floating sheet row { min-height: 68tv; padding: 4tv 18tv; border-radius: 22tv; background: none; color: @ink@; border: none; }\n"
"window.lumaui-tv dialog.floating sheet row label { font-size: 24tv; font-weight: 600; color: inherit; }\n"
"window.lumaui-tv dialog.floating sheet row:focus-visible, window.lumaui-tv dialog.floating sheet row:focus-within {\n"
"  background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@; outline: none;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@; }\n"
"window.lumaui-tv dialog.floating sheet checkbutton check { min-width: 30tv; min-height: 30tv; border-radius: 9tv; }\n"
"window.lumaui-tv dialog.floating sheet preferencesgroup label.heading {\n"
"  font-size: 20tv; font-weight: 650; letter-spacing: 1tv; text-transform: uppercase; color: @ink_3@; }\n"
"window.lumaui-tv dialog.floating sheet preferencesgroup label.description { font-size: 24tv; color: @ink_2@; }\n"
"window.lumaui-tv .lumaui-modal { min-width: 980tv; padding: 40tv; border-radius: 36tv; border: none;\n"
"  background-color: @sheet@; background-image: none; color: @ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @bar_glint@, 0 0 0 1.5tv @bar_rim@, 0 18tv 44tv -10tv @bar_drop@; }\n"
"window.lumaui-tv .lumaui-modal label { font-family: Figtree, sans-serif; font-size: 24tv; font-weight: 500; color: @ink_2@; line-height: 1.4; }\n"
"window.lumaui-tv .lumaui-modal label.lumaui-part-title, window.lumaui-tv .lumaui-modal label.lumaui-t-title-1 { font-size: 40tv; font-weight: 700; color: @ink@; }\n"
"window.lumaui-tv .lumaui-modal button { min-height: 66tv; padding: 0 30tv; border-radius: 22tv; border: none; outline: none;\n"
"  font-size: 25tv; font-weight: 650; color: @ink@; background-color: @chip@; background-image: none;\n"
"  box-shadow: inset 0 1.5tv 0 @chip_glint@, 0 0 0 1.5tv @chip_rim@, 0 2tv 5tv @chip_drop@; }\n"
"window.lumaui-tv .lumaui-modal button label { font-size: 25tv; font-weight: 650; color: inherit; }\n"
"window.lumaui-tv .lumaui-modal button.suggested-action, window.lumaui-tv .lumaui-modal button.lumaui-text-button.key { background-color: @accent@; color: white; }\n"
"window.lumaui-tv .lumaui-modal button:focus-visible { background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; }\n"
"window.lumaui-tv .lumaui-modal button:disabled { opacity: 0.38; }\n"
"window.lumaui-tv .lumaui-modal entry, window.lumaui-tv .lumaui-modal text { min-height: 66tv; font-size: 25tv; color: @ink@; }\n"
"window.lumaui-tv .lumaui-modal entry { padding: 0 22tv; border-radius: 22tv; border: none; background-color: @well@; background-image: none; outline: none;\n"
"  box-shadow: inset 0 0 0 1.5tv @well_rim@, inset 0 2tv 4tv @well_shade@; }\n"
"window.lumaui-tv .lumaui-modal entry:focus-within { box-shadow: inset 0 0 0 3tv @accent@; }\n";

/* The focused button is the key, whatever it rests as (suggested, a key-style text button, primary):
 * its label takes the key's ink. Last in the sheet so it outranks every resting rule. */
static const char *const tv_key_sheet =
"window.lumaui-tv button.suggested-action:focus-visible, window.lumaui-tv button.lumaui-text-button.key:focus-visible,\n"
"window.lumaui-tv button.primary:focus-visible, window.lumaui-tv button.destructive-action:focus-visible,\n"
"window.lumaui-tv .lumaui-part-buttons > button:focus-visible, window.lumaui-tv flowboxchild > button:focus-visible {\n"
"  background-color: transparent; background-image: linear-gradient(180deg, @key_top@, @key_bottom@); color: @key_ink@;\n"
"  box-shadow: inset 0 1.5tv 0 @key_glint@, 0 0 0 1.5tv @key_rim@, 0 3tv 10tv @key_drop@; }\n"
"window.lumaui-tv button.suggested-action:focus-visible label, window.lumaui-tv button.lumaui-text-button.key:focus-visible label,\n"
"window.lumaui-tv button.primary:focus-visible label, window.lumaui-tv button.destructive-action:focus-visible label,\n"
"window.lumaui-tv button.suggested-action:focus-visible image, window.lumaui-tv button.lumaui-text-button.key:focus-visible image,\n"
"window.lumaui-tv button.primary:focus-visible image { color: @key_ink@; }\n"
/* a QR code on a TV: its modules scale with the TV unit (the quiet zone is part of the drawing) */
"window.lumaui-tv dialog .lumaui-qr { min-width: 400tv; min-height: 400tv; }\n";

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
    {"@sheet_solid@", "rgba(36, 38, 42, 0.97)"},
    {"@sheet@", LUMA_UI_TV_MATERIALS_SHEET}, {"@accent@", LUMA_UI_TV_MATERIALS_ACCENT},
    {"@divider@", "rgba(255, 255, 255, 0.08)"},
  };
  /* Reduce Transparency and High Contrast make the surfaces opaque (as the TV home). */
  static const char *const surfaces[] = {"@well@", "@chip@", "@bar@", "@sheet@", NULL};
  GString *css = g_string_new(tv_sheet);
  g_string_append(css, tv_parts_sheet);
  g_string_append(css, tv_key_sheet);
  for (guint i = 0; i < G_N_ELEMENTS(colours); i++) {
    g_autofree char *value = material(colours[i].value, f->opaque && g_strv_contains(surfaces, colours[i].name));
    g_string_replace(css, colours[i].name, value, 0);
  }
  g_autofree char *pixels = in_pixels(css->str, f->unit);
  g_string_free(css, TRUE);
  if (g_strcmp0(g_getenv("LUMA_TV_DEBUG"), "noshadow") == 0) {
    /* profiling aid: the same sheet without any shadow, to weigh what shadows cost a frame */
    GString *flat = g_string_new(NULL);
    for (const char *p = pixels; *p;) {
      const char *at = strstr(p, "box-shadow:");
      if (at == NULL) { g_string_append(flat, p); break; }
      g_string_append_len(flat, p, at - p);
      g_string_append(flat, "box-shadow: none;");
      const char *end = strchr(at, ';');
      p = end != NULL ? end + 1 : at + strlen(at);
    }
    g_free(pixels);
    pixels = g_string_free(flat, FALSE);
  }
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

static GtkWidget *first_of_type(GtkWidget *w, GType type) {
  if (G_TYPE_CHECK_INSTANCE_TYPE(w, type)) return w;
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    GtkWidget *found = first_of_type(c, type);
    if (found != NULL) return found;
  }
  return NULL;
}

/* A window without a sidebar (a welcome, an error: one centred column) has no island. */
static GtkListBox *sidebar_list(TvFrame *f) {
  if (f->sidebar == NULL) return NULL;
  if (!f->generic)
    return GTK_LIST_BOX(luma_navigation_sidebar_get_list(LUMA_NAVIGATION_SIDEBAR(f->sidebar)));
  GtkWidget *list = first_of_type(f->sidebar, GTK_TYPE_LIST_BOX);
  return list != NULL ? GTK_LIST_BOX(list) : NULL;
}

static gboolean in_sidebar(TvFrame *f, GtkWidget *w) {
  return f->sidebar != NULL && w != NULL && gtk_widget_is_ancestor(w, f->sidebar);
}

/* The sidebar in (or out of) the island. A LumaNavigationSidebar does it itself; any other sidebar
 * is told by its classes (lumaui-tv-island, with lumaui-tv-rail while folded) and is sized here:
 * as tall as its rows, a long list stopping at the bottom safe line. */
static void sidebar_set_tv(TvFrame *f, gboolean tv, gboolean rail) {
  if (!f->generic) {
    luma_navigation_sidebar_set_tv(LUMA_NAVIGATION_SIDEBAR(f->sidebar), tv, rail);
    return;
  }
  if (tv && !gtk_widget_has_css_class(f->sidebar, "lumaui-tv-island")) {
    f->sidebar_valign = gtk_widget_get_valign(f->sidebar);
    f->scroll = first_of_type(f->sidebar, GTK_TYPE_SCROLLED_WINDOW);
  }
  if (f->scroll != NULL) gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(f->scroll), tv);
  gtk_widget_set_valign(f->sidebar, tv ? GTK_ALIGN_START : f->sidebar_valign);
  luma_ui_set_css_class(f->sidebar, "lumaui-tv-island", tv);
}

static gboolean row_reachable(GtkListBoxRow *row) {
  return gtk_widget_get_visible(GTK_WIDGET(row)) && gtk_list_box_row_get_selectable(row) &&
         gtk_widget_get_sensitive(GTK_WIDGET(row)) && !gtk_widget_has_css_class(GTK_WIDGET(row), "luma-navigation-section");
}

static GtkListBoxRow *selected_row(TvFrame *f) {
  GtkListBox *list = sidebar_list(f);
  if (list == NULL) return NULL;
  GtkListBoxRow *row = gtk_list_box_get_selected_row(list);
  if (row != NULL)
    return row;
  for (int i = 0; (row = gtk_list_box_get_row_at_index(list, i)) != NULL; i++)
    if (row_reachable(row))
      return row;
  return NULL;
}

static void set_rail(TvFrame *f, gboolean rail) {
  if (f->rail == rail || f->sidebar == NULL)
    return;
  f->rail = rail;
  luma_ui_set_css_class(f->sidebar, "lumaui-tv-rail", rail);
  sidebar_set_tv(f, TRUE, rail);
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

/* Parts laid out differently on a TV: a row of buttons runs across; a centred column with a
 * footnote centres its content in the safe area and puts the footnote on the bottom line; an
 * emblem takes the TV's size. What is changed is kept on the widget and put back on leaving. */
typedef struct { int orientation, valign, vexpand, halign, pixel_size, homogeneous; } TvSaved;

static void hold_layout(GtkWidget *w) {
  if (g_object_get_data(G_OBJECT(w), "luma-tv-layout") != NULL) return;
  TvSaved *saved = g_new(TvSaved, 1);
  saved->orientation = GTK_IS_ORIENTABLE(w) ? (int)gtk_orientable_get_orientation(GTK_ORIENTABLE(w)) : -1;
  saved->valign = gtk_widget_get_valign(w);
  saved->halign = gtk_widget_get_halign(w);
  saved->vexpand = gtk_widget_get_vexpand_set(w) ? gtk_widget_get_vexpand(w) : -1;
  saved->pixel_size = GTK_IS_IMAGE(w) ? gtk_image_get_pixel_size(GTK_IMAGE(w)) : 0;
  saved->homogeneous = GTK_IS_STACK(w) ? gtk_stack_get_vhomogeneous(GTK_STACK(w)) : -1;
  g_object_set_data_full(G_OBJECT(w), "luma-tv-layout", saved, g_free);
}

static void let_layout(GtkWidget *w) {
  TvSaved *saved = g_object_get_data(G_OBJECT(w), "luma-tv-layout");
  if (saved == NULL) return;
  if (saved->orientation >= 0) gtk_orientable_set_orientation(GTK_ORIENTABLE(w), saved->orientation);
  if (gtk_widget_has_css_class(w, "lumaui-part-ways") && GTK_IS_BOX(w)) gtk_box_set_homogeneous(GTK_BOX(w), FALSE);
  if (gtk_widget_has_css_class(w, "lumaui-part-way")) gtk_widget_set_hexpand_set(w, FALSE);
  gtk_widget_set_valign(w, saved->valign);
  gtk_widget_set_halign(w, saved->halign);
  if (saved->vexpand < 0) gtk_widget_set_vexpand_set(w, FALSE);
  else gtk_widget_set_vexpand(w, saved->vexpand);
  if (GTK_IS_IMAGE(w)) gtk_image_set_pixel_size(GTK_IMAGE(w), saved->pixel_size);
  if (GTK_IS_STACK(w) && saved->homogeneous >= 0) gtk_stack_set_vhomogeneous(GTK_STACK(w), saved->homogeneous);
  g_object_set_data(G_OBJECT(w), "luma-tv-layout", NULL);
}

static void lay_out_part(GtkWidget *w) {
  if (g_object_get_data(G_OBJECT(w), "luma-tv-layout") != NULL) return;
  if ((gtk_widget_has_css_class(w, "lumaui-part-buttons") || gtk_widget_has_css_class(w, "lumaui-part-ways")) && GTK_IS_BOX(w)) {
    hold_layout(w);
    gtk_orientable_set_orientation(GTK_ORIENTABLE(w), GTK_ORIENTATION_HORIZONTAL);
    if (gtk_widget_has_css_class(w, "lumaui-part-ways")) gtk_box_set_homogeneous(GTK_BOX(w), TRUE);
  } else if (gtk_widget_has_css_class(w, "lumaui-part-way")) {
    hold_layout(w);
    gtk_widget_set_hexpand(w, TRUE);
  } else if (GTK_IS_IMAGE(w) && gtk_image_get_pixel_size(GTK_IMAGE(w)) > 0) {
    hold_layout(w);
    gtk_image_set_pixel_size(GTK_IMAGE(w), -1); /* an icon the app sized: the TV sheet sizes it */
  } else if (gtk_widget_has_css_class(w, "luma-empty-state")) {
    hold_layout(w);
    gtk_widget_set_vexpand(w, FALSE); /* the empty state is a card as tall as what it says */
    gtk_widget_set_valign(w, GTK_ALIGN_START);
  } else if (gtk_widget_has_css_class(w, "lumaui-part-footnote")) {
    GtkWidget *column = gtk_widget_get_parent(w);
    GtkWidget *first = column != NULL ? gtk_widget_get_first_child(column) : NULL;
    if (column == NULL || first == NULL || first == w) return;
    hold_layout(w);
    hold_layout(column);
    hold_layout(first);
    gtk_widget_set_valign(column, GTK_ALIGN_FILL);
    gtk_widget_set_vexpand(column, TRUE);
    /* the space above the content and above the footnote share what is left: the content centres */
    gtk_widget_set_vexpand(first, TRUE);
    gtk_widget_set_valign(first, GTK_ALIGN_END);
    gtk_widget_set_vexpand(w, TRUE);
    gtk_widget_set_valign(w, GTK_ALIGN_END);
  }
}

static GtkWidget *part_row_of(TvFrame *f, GtkWidget *w);
static void release_page(TvFrame *f, GtkWidget *widget) {
  lay_out_part(widget);
  /* A centred window's own action (Try Again under a sentence) keeps the buttons row's natural width,
   * centred, instead of stretching to the column. */
  if (f->sidebar == NULL && GTK_IS_BUTTON(widget) && gtk_widget_get_halign(widget) == GTK_ALIGN_FILL &&
      g_object_get_data(G_OBJECT(widget), "luma-tv-layout") == NULL) {
    GtkWidget *parent = gtk_widget_get_parent(widget);
    if (parent != NULL && GTK_IS_BOX(parent) && gtk_orientable_get_orientation(GTK_ORIENTABLE(parent)) == GTK_ORIENTATION_VERTICAL) {
      hold_layout(widget);
      gtk_widget_set_halign(widget, GTK_ALIGN_CENTER);
    }
  }
  if ((gtk_widget_has_css_class(widget, "lumaui-part-mark") || gtk_widget_has_css_class(widget, "lumaui-part-lead")) &&
      within_row(widget))
    place_lead(f, widget);
  if (gtk_widget_has_css_class(widget, "lumaui-part-chips")) {
    /* chips 16 apart (lumaui-tv.css .chips gap), and the row that holds them is no key itself */
    GtkWidget *row = part_row_of(f, widget);
    if (row != NULL) gtk_widget_add_css_class(row, "lumaui-tv-holder");
    if (g_object_get_data(G_OBJECT(widget), "luma-tv-chips") == NULL) {
      int *saved = g_new(int, 2);
      if (ADW_IS_WRAP_BOX(widget)) {
        saved[0] = adw_wrap_box_get_child_spacing(ADW_WRAP_BOX(widget));
        saved[1] = adw_wrap_box_get_line_spacing(ADW_WRAP_BOX(widget));
        adw_wrap_box_set_child_spacing(ADW_WRAP_BOX(widget), (int)round(16 * f->unit));
        adw_wrap_box_set_line_spacing(ADW_WRAP_BOX(widget), (int)round(16 * f->unit));
      } else if (GTK_IS_BOX(widget)) {
        saved[0] = gtk_box_get_spacing(GTK_BOX(widget));
        saved[1] = -1;
        gtk_box_set_spacing(GTK_BOX(widget), (int)round(16 * f->unit));
      } else {
        saved[0] = saved[1] = -1;
      }
      g_object_set_data_full(G_OBJECT(widget), "luma-tv-chips", saved, g_free);
    }
  }
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
    /* A centred window (no sidebar: a welcome, an error) keeps one readable column, centred. */
    const gboolean centred = f->sidebar == NULL;
    adw_clamp_set_maximum_size(clamp, centred ? (int)round(1200 * f->unit) : G_MAXINT / 4);
    adw_clamp_set_tightening_threshold(clamp, centred ? (int)round(1200 * f->unit) : G_MAXINT / 4);
    if (child && centred) {
      gtk_widget_set_margin_start(child, 0);
      gtk_widget_set_margin_end(child, 0);
    } else if (child) {
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
  if (gtk_widget_has_css_class(widget, "lumaui-part-footnote")) {
    GtkWidget *column = gtk_widget_get_parent(widget);
    if (column != NULL) {
      let_layout(column);
      if (gtk_widget_get_first_child(column) != NULL) let_layout(gtk_widget_get_first_child(column));
    }
  }
  let_layout(widget);
  gtk_widget_remove_css_class(widget, "lumaui-tv-holder");
  int *chips = g_object_get_data(G_OBJECT(widget), "luma-tv-chips");
  if (chips != NULL) {
    if (ADW_IS_WRAP_BOX(widget)) {
      adw_wrap_box_set_child_spacing(ADW_WRAP_BOX(widget), chips[0]);
      adw_wrap_box_set_line_spacing(ADW_WRAP_BOX(widget), chips[1]);
    } else if (GTK_IS_BOX(widget) && chips[0] >= 0) {
      gtk_box_set_spacing(GTK_BOX(widget), chips[0]);
    }
    g_object_set_data(G_OBJECT(widget), "luma-tv-chips", NULL);
  }
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
static gboolean focus_into_parts(GtkWidget *w);
static GtkWidget *hold_modal(TvFrame *f);

/* A page that redraws under the remote (a network list refreshing) must not lose its place: the
 * focus is remembered as a path of child positions and found again in the redrawn page. */
static gboolean editable(GtkWidget *focus);

static GtkWidget *part_row_of(TvFrame *f, GtkWidget *w) {
  while (w != NULL && w != f->island && !gtk_widget_has_css_class(w, "lumaui-part-row")) w = gtk_widget_get_parent(w);
  return w != NULL && w != f->island ? w : NULL;
}

static gboolean shown(GtkWidget *w);
static const char *first_text(GtkWidget *w) {
  if (!shown(w))
    return NULL;
  if (GTK_IS_LABEL(w) && gtk_widget_get_visible(w) && *gtk_label_get_text(GTK_LABEL(w)) != '\0')
    return gtk_label_get_text(GTK_LABEL(w));
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    const char *t = first_text(c);
    if (t != NULL) return t;
  }
  return NULL;
}

/* Shown on the page: hidden widgets and a stack's other pages are never where the remote is. */
static gboolean shown(GtkWidget *w) {
  return gtk_widget_get_visible(w) && gtk_widget_get_child_visible(w);
}

static GtkWidget *row_titled(TvFrame *f, GtkWidget *w, const char *title) {
  if (!shown(w))
    return NULL;
  if (gtk_widget_has_css_class(w, "lumaui-part-row")) {
    const char *t = first_text(w);
    return g_strcmp0(t, title) == 0 ? w : NULL;
  }
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    GtkWidget *found = row_titled(f, c, title);
    if (found != NULL) return found;
  }
  return NULL;
}

static GtkWidget *find_class(GtkWidget *w, const char *cls);

/* The open page's title (its header part), which a redraw of the same page keeps. */
static const char *page_title(TvFrame *f) {
  GtkWidget *header = find_class(f->island, "lumaui-part-header");
  return header != NULL ? first_text(header) : NULL;
}

/* The focus moved by itself (no key, not the frame) away from a widget that is gone, on the same
 * page: the page was redrawn under the remote (a network list refreshing). */
static gboolean redrawn_under(TvFrame *f) {
  /* A field taking the caret is the app's own choice (a password asked for), never a fallback. */
  GtkWidget *focus = gtk_root_get_focus(GTK_ROOT(f->window));
  return f->page_zone && f->keying == 0 && f->self_moving == 0 && f->held == NULL && f->path->len > 0 &&
         !editable(focus) && g_strcmp0(page_title(f), f->held_page) == 0;
}

static void remember(TvFrame *f, GtkWidget *focus) {
  f->held_field = editable(focus);
  g_free(f->held_page);
  f->held_page = g_strdup(page_title(f));
  GtkWidget *row = part_row_of(f, focus);
  g_free(f->held_title);
  f->held_title = row != NULL ? g_strdup(first_text(row)) : NULL;
  g_clear_weak_pointer(&f->held);
  g_set_weak_pointer(&f->held, focus);
  g_array_set_size(f->path, 0);
  guint below = 0;
  for (GtkWidget *w = focus; w != NULL && w != f->island; w = gtk_widget_get_parent(w)) {
    int index = 0;
    for (GtkWidget *c = gtk_widget_get_prev_sibling(w); c != NULL; c = gtk_widget_get_prev_sibling(c)) index++;
    g_array_prepend_val(f->path, index);
    if (w == row) below = f->path->len - 1;
  }
  f->row_depth = row != NULL ? f->path->len - below : 0;
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
  /* the same row by its title (a list that reordered), its first control; else the same place */
  GtkWidget *row = f->held_title != NULL ? row_titled(f, f->island, f->held_title) : NULL;
  if (row != NULL) {
    /* the same place inside the row (a grown row's Connect), else its first control */
    GtkWidget *inside = row;
    for (guint i = f->row_depth; inside != NULL && f->row_depth > 0 && i < f->path->len; i++) {
      GtkWidget *c = gtk_widget_get_first_child(inside);
      for (int n = g_array_index(f->path, int, i); c != NULL && n > 0; n--) c = gtk_widget_get_next_sibling(c);
      inside = c;
    }
    if (inside != NULL && inside != row && gtk_widget_get_focusable(inside) && shown(inside))
      return inside;
    GtkWidget *first = gtk_widget_get_focusable(row) ? row : NULL;
    for (GtkWidget *c = row; first == NULL && c != NULL; c = gtk_widget_get_first_child(c))
      if (gtk_widget_get_focusable(c) && !GTK_IS_LABEL(c)) first = c;
    return first != NULL ? first : row;
  }
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
  if (g_getenv("LUMA_TV_DEBUG") != NULL)
    g_printerr("tv grown: title=%s row=%s field=%p\n", f->held_title ? f->held_title : "-", row && row != f->island ? first_text(row) : "-", (void *)field);
  if (field == NULL) {
    /* grown with actions only (Connect, Forget …): the first of them takes the focus */
    GtkWidget *grown = row != NULL && row != f->island && GTK_IS_BOX(row) ? gtk_widget_get_first_child(row) : NULL;
    grown = grown != NULL ? gtk_widget_get_next_sibling(grown) : NULL;
    if (grown == NULL || !gtk_widget_is_drawable(grown))
      return FALSE;
    f->ok_at = 0;
    f->self_moving++;
    gboolean moved = gtk_widget_child_focus(grown, GTK_DIR_TAB_FORWARD);
    f->self_moving--;
    return moved;
  }
  f->ok_at = 0;
  GtkWidget *focus = focus_of(f);
  f->self_moving++;
  if (focus != field && !gtk_widget_is_ancestor(focus, field))
    gtk_widget_grab_focus(field);
  f->self_moving--;
  if (focus_of(f) != NULL) remember(f, focus_of(f));
  return TRUE;
}

static gboolean refind(TvFrame *f) {
  /* Lost: no focus, or GTK's fallback put it elsewhere (the first control on the page) because the
   * widget the remote was on was redrawn away, with no key moving it. */
  gboolean redrawn = redrawn_under(f);
  if (!f->page_zone || f->path->len == 0 || (!focus_lost(f) && !redrawn))
    return FALSE;
  GtkWidget *w = path_widget(f);
  /* The remote was typing in that row, or OK just grew it with a field: the field again. */
  if (f->held_field || g_get_monotonic_time() - f->ok_at < 2 * G_USEC_PER_SEC) {
    GtkWidget *row = w;
    while (row != NULL && row != f->island && !gtk_widget_has_css_class(row, "lumaui-part-row")) row = gtk_widget_get_parent(row);
    GtkWidget *field = row != NULL && row != f->island ? first_field(row) : NULL;
    if (field != NULL && gtk_widget_grab_focus(field)) {
      f->ok_at = 0;
      remember(f, focus_of(f));
      return TRUE;
    }
  }
  if (g_getenv("LUMA_TV_DEBUG") != NULL) {
    GtkWidget *r = part_row_of(f, w);
    g_printerr("tv refind: held=%s found=%s %s drawable=%d focusable=%d\n", f->held_title ? f->held_title : "-",
               r != NULL && first_text(r) ? first_text(r) : "-", G_OBJECT_TYPE_NAME(w), gtk_widget_is_drawable(w), gtk_widget_get_focusable(w));
  }
  for (; w != NULL && w != gtk_widget_get_parent(f->island); w = gtk_widget_get_parent(w)) {
    if (!gtk_widget_is_drawable(w)) continue;
    if ((gtk_widget_get_focusable(w) && gtk_widget_grab_focus(w)) || gtk_widget_child_focus(w, GTK_DIR_TAB_FORWARD)) {
      /* found again: this is now where the remote is (never search again until the next redraw) */
      if (focus_of(f) != NULL) remember(f, focus_of(f));
      return TRUE;
    }
  }
  return FALSE;
}

static gboolean relax(gpointer data) {
  TvFrame *f = data;
  f->relax_id = 0;
  if (f->active) {
    gint64 start = g_get_monotonic_time();
    release_page(f, f->island);
    if (g_getenv("LUMA_TV_DEBUG") != NULL)
      g_printerr("tv timing: release_page %.1f ms\n", (g_get_monotonic_time() - start) / 1000.0);
    if (hold_modal(f) != NULL) return G_SOURCE_REMOVE;
    if (!grown_field(f))
      refind(f);
    /* Until the remote is used, the focus is the open page's destination (a deep link opened it). */
    GtkWidget *focus = focus_of(f);
    GtkListBoxRow *selected = sidebar_list(f) != NULL ? gtk_list_box_get_selected_row(sidebar_list(f)) : NULL;
    if (!f->keyed && selected != NULL && in_sidebar(f, focus) &&
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

/* The first control of a page in reading order, found by walking it (gtk_widget_child_focus would
 * start from a container's remembered focus child and move on past it). */
static GtkWidget *first_control(GtkWidget *w) {
  if (!gtk_widget_get_visible(w) || !gtk_widget_is_sensitive(w)) return NULL;
  if (gtk_widget_get_focusable(w) && (GTK_IS_BUTTON(w) || GTK_IS_EDITABLE(w) || GTK_IS_SWITCH(w) ||
                                      GTK_IS_LIST_BOX_ROW(w) || GTK_IS_CHECK_BUTTON(w)))
    return w;
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    GtkWidget *found = first_control(c);
    if (found != NULL) return found;
  }
  return NULL;
}

static gboolean settle(gpointer data) {
  TvFrame *f = data;
  f->settle_id = 0;
  if (!f->active)
    return G_SOURCE_REMOVE;
  release_page(f, f->island);
  gtk_window_set_focus_visible(f->window, TRUE);
  /* A centred window opening (no key yet): the app's own focus, whatever GTK moved it to while the
   * content moved under the room. */
  if (f->sidebar == NULL && !f->keyed && f->app_focus != NULL && focus_of(f) != f->app_focus &&
      in_page(f, f->app_focus) && gtk_widget_get_visible(f->app_focus) && gtk_widget_is_sensitive(f->app_focus)) {
    gtk_widget_grab_focus(f->app_focus);
    return G_SOURCE_REMOVE;
  }
  if (refind(f))
    return G_SOURCE_REMOVE;
  if (focus_lost(f)) {
    f->page_zone = FALSE;
    GtkListBoxRow *row = selected_row(f);
    if (row != NULL)
      gtk_widget_grab_focus(GTK_WIDGET(row));
    else if (f->sidebar == NULL) {
      /* A centred window: the control the app chose (it may not be drawn yet, the content having just
       * moved under the room), else its first. Never child_focus from a stale focus: that moves on to
       * the next control (OK on a welcome would press its second button). */
      GtkWidget *focus = focus_of(f);
      if (focus == NULL || !in_page(f, focus)) focus = f->app_focus; /* moving the content dropped it */
      if (focus != NULL && in_page(f, focus) && gtk_widget_get_focusable(focus) && gtk_widget_get_visible(focus) &&
          gtk_widget_is_sensitive(focus)) {
        gtk_widget_grab_focus(focus);
      } else {
        GtkWidget *first = first_control(f->island);
        if (first != NULL) gtk_widget_grab_focus(first);
      }
    }
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

/* Before the first key, a centred window's focus is the app's choice. GTK moves it on by itself when the
 * page under it is redrawn (a focus traversal from the window, run from an idle, lands on the control
 * after the one the app chose: a welcome's second button, so the first OK pressed the wrong thing). */
static gboolean restore_app_focus(gpointer data) {
  TvFrame *f = data;
  f->restore_id = 0;
  if (f->active && f->app_focus != NULL && !f->keyed && gtk_widget_get_visible(f->app_focus) &&
      gtk_widget_is_sensitive(f->app_focus) && in_page(f, f->app_focus)) {
    f->self_moving++;
    gtk_widget_grab_focus(f->app_focus);
    f->self_moving--;
  }
  return G_SOURCE_REMOVE;
}

static gboolean moved_on_from(GtkWidget *from, GtkWidget *to) {
  if (from == NULL || to == NULL || gtk_widget_get_parent(from) == NULL || gtk_widget_get_parent(from) != gtk_widget_get_parent(to))
    return FALSE;
  for (GtkWidget *w = gtk_widget_get_next_sibling(from); w != NULL; w = gtk_widget_get_next_sibling(w))
    if (gtk_widget_get_visible(w) && gtk_widget_get_focusable(w))
      return w == to;
  return FALSE;
}

static void track_app_focus(TvFrame *f, GtkWidget *focus) {
  if (f->sidebar != NULL || f->keyed || f->keying != 0 || f->self_moving != 0 || focus == NULL || !in_page(f, focus))
    return;
  if (f->app_focus != NULL && focus != f->app_focus && moved_on_from(f->app_focus, focus) &&
      gtk_widget_get_visible(f->app_focus) && gtk_widget_is_sensitive(f->app_focus)) {
    if (f->restore_id == 0)
      f->restore_id = g_idle_add_full(G_PRIORITY_HIGH_IDLE, restore_app_focus, f, NULL);
    return;
  }
  g_clear_weak_pointer(&f->app_focus);
  g_set_weak_pointer(&f->app_focus, focus);
}

/* The remote's move brings its row into view at once, as far as needed and with room to spare
 * (the mockup keeps the focused row within the page, a peek of the next below it). GTK's own
 * scroll-to-focus animates over several frames, each a full repaint of a moving page: on a TV at
 * 4K that was most of a move's cost. Setting the value ends that animation. */
static void follow_focus(TvFrame *f, GtkWidget *focus) {
  GtkWidget *row = part_row_of(f, focus);
  GtkWidget *target = row != NULL ? row : focus;
  GtkWidget *viewport = gtk_widget_get_ancestor(target, GTK_TYPE_VIEWPORT);
  GtkWidget *content = viewport != NULL ? gtk_viewport_get_child(GTK_VIEWPORT(viewport)) : NULL;
  GtkScrollable *scrollable = viewport != NULL ? GTK_SCROLLABLE(viewport) : NULL;
  GtkAdjustment *adj = scrollable != NULL ? gtk_scrollable_get_vadjustment(scrollable) : NULL;
  graphene_rect_t box;
  if (adj == NULL || content == NULL || !gtk_widget_compute_bounds(target, content, &box))
    return;
  double value = gtk_adjustment_get_value(adj), page = gtk_adjustment_get_page_size(adj);
  double above = 48 * f->unit, below = MIN(160 * f->unit, page * 0.25);
  double want = value;
  if (box.origin.y + box.size.height > value + page - below)
    want = box.origin.y + box.size.height - page + below;
  if (box.origin.y < want + above)
    want = box.origin.y - above;
  want = CLAMP(want, gtk_adjustment_get_lower(adj), gtk_adjustment_get_upper(adj) - page);
  gtk_adjustment_set_value(adj, want); /* also when unchanged: it stops GTK's animation */
}

static void focus_changed(GtkWindow *window, GParamSpec *pspec, TvFrame *f) {
  (void)window; (void)pspec;
  if (!f->active)
    return;
  GtkWidget *focus = focus_of(f);
  track_app_focus(f, focus);
  if (f->keying == 0 && f->self_moving == 0)
    f->moved_at = g_get_monotonic_time();
  if (g_getenv("LUMA_TV_DEBUG") != NULL) {
    g_auto(GStrv) classes = focus ? gtk_widget_get_css_classes(focus) : NULL;
    g_autofree char *joined = classes ? g_strjoinv(".", classes) : NULL;
    GtkWidget *row = focus != NULL && in_page(f, focus) ? part_row_of(f, focus) : NULL;
    g_printerr("tv focus: %s %s.%s page=%d row=%s keying=%d held=%s\n", focus ? G_OBJECT_TYPE_NAME(focus) : "(none)",
               focus ? gtk_widget_get_css_name(focus) : "", joined ? joined : "", focus ? in_page(f, focus) : 0,
               row != NULL && first_text(row) ? first_text(row) : "-", f->keying, f->held_title ? f->held_title : "-");
  }
  if (f->self_moving == 0 &&
      (focus_lost(f) || redrawn_under(f))) {
    queue_relax(f);  /* a page redrawn under the focus: it is found again there */
    queue_settle(f); /* or, failing that, the island takes it back */
    return;
  }
  if (in_page(f, focus) && gtk_widget_get_focusable(focus)) {
    f->page_zone = TRUE;
    if (f->keying > 0)
      follow_focus(f, focus);
    if (f->ok_at == 0 || g_get_monotonic_time() - f->ok_at > 2 * G_USEC_PER_SEC) {
      remember(f, focus);
    } else {
      /* waiting for a row to grow: keep the row's path, but the focus is held where it now is */
      g_clear_weak_pointer(&f->held);
      g_set_weak_pointer(&f->held, focus);
      f->held_field = editable(focus);
    }
  } else if (in_sidebar(f, focus)) {
    f->page_zone = FALSE;
  }
  if (in_page(f, focus) || in_sidebar(f, focus))
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

/* Into the page at its first row: the hero or the first group, past the header's own controls
 * (a page's on/off switch beside its title is reached with Up, never landed on). */
static gboolean focus_into_parts(GtkWidget *w) {
  if (!gtk_widget_is_drawable(w) || gtk_widget_has_css_class(w, "lumaui-part-header") ||
      gtk_widget_has_css_class(w, "lumaui-part-back"))
    return FALSE;
  if (gtk_widget_has_css_class(w, "lumaui-part-hero") || gtk_widget_has_css_class(w, "lumaui-part-group") ||
      gtk_widget_has_css_class(w, "lumaui-part-card") || gtk_widget_has_css_class(w, "lumaui-part-row"))
    return gtk_widget_child_focus(w, GTK_DIR_TAB_FORWARD);
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c))
    if (focus_into_parts(c)) return TRUE;
  return FALSE;
}

static gboolean to_page(TvFrame *f) {
  release_page(f, f->island);
  gtk_root_set_focus(GTK_ROOT(f->window), NULL);
  if (!focus_into_parts(f->island) && !gtk_widget_child_focus(f->island, GTK_DIR_TAB_FORWARD)) {
    to_island(f);
    return FALSE;
  }
  set_rail(f, TRUE);
  return TRUE;
}

static void open_neighbour(TvFrame *f, GtkWidget *focus, int step) {
  GtkListBox *list = sidebar_list(f);
  if (list == NULL) return;
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
  return gtk_widget_has_css_class(w, "lumaui-dialog") || gtk_widget_has_css_class(w, "lumaui-modal") || ADW_IS_DIALOG(w);
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
  if (!shown(w))
    return NULL;
  if (gtk_widget_has_css_class(w, cls) && gtk_widget_is_drawable(w))
    return w;
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c)) {
    GtkWidget *found = find_class(c, cls);
    if (found != NULL) return found;
  }
  return NULL;
}

/* Bring the remote into an open question: its Cancel first (the safe answer), else its first control. */
static void release_modal(TvFrame *f, GtkWidget *w) {
  lay_out_part(w);
  if (GTK_IS_STACK(w) && f->page_modal != NULL && g_object_get_data(G_OBJECT(w), "luma-tv-layout") == NULL) {
    hold_layout(w); /* a page's steps are each as tall as they are */
    gtk_stack_set_vhomogeneous(GTK_STACK(w), FALSE);
  }
  if (ADW_IS_CLAMP(w) && g_object_get_data(G_OBJECT(w), "luma-tv-clamp") == NULL) {
    int *saved = g_new0(int, 6);
    GtkWidget *child = adw_clamp_get_child(ADW_CLAMP(w));
    saved[0] = adw_clamp_get_maximum_size(ADW_CLAMP(w));
    saved[1] = adw_clamp_get_tightening_threshold(ADW_CLAMP(w));
    if (child) {
      saved[2] = gtk_widget_get_margin_start(child); saved[3] = gtk_widget_get_margin_end(child);
      saved[4] = gtk_widget_get_margin_top(child); saved[5] = gtk_widget_get_margin_bottom(child);
    }
    g_object_set_data_full(G_OBJECT(w), "luma-tv-clamp", saved, g_free);
    int width = f->page_modal != NULL ? G_MAXINT / 4 : (int)round(900 * f->unit);
    adw_clamp_set_maximum_size(ADW_CLAMP(w), width);
    adw_clamp_set_tightening_threshold(ADW_CLAMP(w), width);
  }
  for (GtkWidget *c = gtk_widget_get_first_child(w); c != NULL; c = gtk_widget_get_next_sibling(c))
    release_modal(f, c);
}

/* A modal card (luma_appkit LayerHost) is a page in the frame on a TV: it takes the page's place
 * in the safe area under the bar, the window's own page and island step aside until it closes. */
static void page_modal_end(TvFrame *f) {
  if (f->page_modal == NULL) return;
  GtkWidget *modal = f->page_modal;
  g_clear_weak_pointer(&f->page_modal);
  if (f->frame != NULL) gtk_widget_set_opacity(f->frame, 1.0);
  int *margins = g_object_get_data(G_OBJECT(modal), "luma-tv-margins");
  if (margins != NULL) {
    gtk_widget_set_margin_top(modal, margins[0]); gtk_widget_set_margin_bottom(modal, margins[1]);
    gtk_widget_set_margin_start(modal, margins[2]); gtk_widget_set_margin_end(modal, margins[3]);
    g_object_set_data(G_OBJECT(modal), "luma-tv-margins", NULL);
  }
  let_layout(modal);
  gtk_widget_remove_css_class(modal, "lumaui-tv-page-modal");
  GtkWidget *scrim = gtk_widget_get_prev_sibling(modal);
  if (scrim != NULL) gtk_widget_remove_css_class(scrim, "lumaui-tv-page-scrim");
  restore_page(modal);
}

static void page_modal_begin(TvFrame *f, GtkWidget *modal) {
  if (f->page_modal == modal) return;
  page_modal_end(f);
  g_set_weak_pointer(&f->page_modal, modal);
  int *margins = g_new(int, 4);
  margins[0] = gtk_widget_get_margin_top(modal); margins[1] = gtk_widget_get_margin_bottom(modal);
  margins[2] = gtk_widget_get_margin_start(modal); margins[3] = gtk_widget_get_margin_end(modal);
  g_object_set_data_full(G_OBJECT(modal), "luma-tv-margins", margins, g_free);
  hold_layout(modal);
  gtk_widget_set_halign(modal, GTK_ALIGN_FILL);
  gtk_widget_set_valign(modal, GTK_ALIGN_FILL);
  gtk_widget_set_margin_top(modal, (int)round(166 * f->unit));
  gtk_widget_set_margin_bottom(modal, (int)round(56 * f->unit));
  gtk_widget_set_margin_start(modal, (int)round(96 * f->unit));
  gtk_widget_set_margin_end(modal, (int)round(96 * f->unit));
  gtk_widget_add_css_class(modal, "lumaui-tv-page-modal");
  GtkWidget *scrim = gtk_widget_get_prev_sibling(modal);
  if (scrim != NULL && gtk_widget_has_css_class(scrim, "lumaui-scrim")) gtk_widget_add_css_class(scrim, "lumaui-tv-page-scrim");
  if (f->frame != NULL) gtk_widget_set_opacity(f->frame, 0.0);
}

static GtkWidget *hold_modal(TvFrame *f) {
  GtkWidget *modal = find_modal(GTK_WIDGET(f->window));
  if (modal == NULL) {
    page_modal_end(f);
    return NULL;
  }
  if (gtk_widget_has_css_class(modal, "lumaui-modal") && !gtk_widget_has_css_class(modal, "lumaui-dialog"))
    page_modal_begin(f, modal);
  if (gtk_widget_has_css_class(modal, "lumaui-modal") || (ADW_IS_DIALOG(modal) && !ADW_IS_ALERT_DIALOG(modal)))
    release_modal(f, modal);
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
      GtkWidget *back = back_part(modal);
      GtkWidget *cancel = find_class(modal, "lumaui-dialog-cancel");
      if (back != NULL) gtk_widget_activate(back); /* a step of the page: back to the step before */
      else if (cancel != NULL) gtk_widget_activate(cancel);
      else if (ADW_IS_DIALOG(modal)) adw_dialog_close(ADW_DIALOG(modal));
      else return GDK_EVENT_PROPAGATE; /* a modal card: its layer's own Back (luma_appkit LayerHost) */
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

/* Hold OK for the item's menu (the Python TvFocus rule: tv.focus.hold_ms, opens once, a release
 * before then is the OK). An item has a menu when it offers GTK's own "menu.popup" action: its
 * desktop context menu, the same commands, drawn as the TV's list (window.lumaui-tv popover).
 * The hold's state lives on the window: no frame fields, so other branches' frame changes merge. */
#define TV_HOLD_MS 500

typedef struct {
  GtkWidget *item;   /* weak: OK is down on it */
  guint source;      /* the hold timer */
  gboolean opened;   /* the menu opened: the release does nothing */
} TvHold;

static gboolean offers_menu(GtkWidget *w) {
  GtkWidgetClass *klass = GTK_WIDGET_GET_CLASS(w);
  GType owner;
  const char *name = NULL, *property = NULL;
  const GVariantType *parameter = NULL;
  for (guint i = 0; gtk_widget_class_query_action(klass, i, &owner, &name, &parameter, &property); i++)
    if (g_strcmp0(name, "menu.popup") == 0)
      return gtk_widget_is_sensitive(w);
  return FALSE;
}

static GtkWidget *menu_item(GtkWidget *focus, GtkWidget *stop) {
  for (GtkWidget *w = focus; w != NULL && w != stop; w = gtk_widget_get_parent(w))
    if (offers_menu(w)) return w;
  return NULL;
}

static TvHold *tv_hold(TvFrame *f) {
  TvHold *hold = g_object_get_data(G_OBJECT(f->window), "luma-tv-hold");
  if (hold == NULL) {
    hold = g_new0(TvHold, 1);
    g_object_set_data_full(G_OBJECT(f->window), "luma-tv-hold", hold, g_free);
  }
  return hold;
}

static gboolean hold_elapsed(gpointer data) {
  TvFrame *f = data;
  TvHold *hold = tv_hold(f);
  hold->source = 0;
  if (hold->item != NULL && f->active) {
    hold->opened = TRUE;
    gtk_widget_activate_action(hold->item, "menu.popup", NULL);
  }
  return G_SOURCE_REMOVE;
}

/* OK pressed on an item with a menu: nothing yet; the release is the OK, the hold the menu. */
static gboolean hold_begin(TvFrame *f, GtkWidget *focus) {
  GtkWidget *item = menu_item(focus, gtk_widget_get_parent(f->island));
  if (item == NULL || editable(focus))
    return FALSE;
  TvHold *hold = tv_hold(f);
  g_clear_handle_id(&hold->source, g_source_remove);
  g_clear_weak_pointer(&hold->item);
  g_set_weak_pointer(&hold->item, focus);
  hold->opened = FALSE;
  hold->source = g_timeout_add(TV_HOLD_MS, hold_elapsed, f);
  return TRUE;
}

/* OK let go: before the hold, the press acts now (as OK on any item); after it, nothing. */
static gboolean hold_end(TvFrame *f) {
  TvHold *hold = g_object_get_data(G_OBJECT(f->window), "luma-tv-hold");
  if (hold == NULL || (hold->item == NULL && !hold->opened))
    return FALSE;
  gboolean pending = hold->source != 0;
  g_clear_handle_id(&hold->source, g_source_remove);
  GtkWidget *item = hold->item;
  g_clear_weak_pointer(&hold->item);
  if (pending && item != NULL && item == focus_of(f))
    gtk_widget_activate(item);
  hold->opened = FALSE;
  return TRUE;
}

static gboolean key_handled(GtkEventControllerKey *controller, guint key, guint code,
                            GdkModifierType state, TvFrame *f) {
  (void)controller; (void)code;
  if (!f->active || (state & (GDK_CONTROL_MASK | GDK_ALT_MASK | GDK_SUPER_MASK)) != 0)
    return GDK_EVENT_PROPAGATE;
  if (g_getenv("LUMA_TV_DEBUG") != NULL)
    g_printerr("tv key %s\n", gdk_keyval_name(key));
  /* OK right after the focus moved by itself (the page redrew under the remote) acts on nothing:
   * the remote never acts on a control it did not choose (a power switch beside a list). */
  /* A field is exempt: a redraw that hands the caret back to the same field (a password being
   * typed) is no surprise, and OK there opens the keyboard or submits what was typed. */
  if (is_ok(key) && !editable(focus_of(f)) && g_get_monotonic_time() - f->moved_at < 600 * 1000 && f->moved_at > f->key_at) {
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
  gboolean page = in_page(f, focus) || f->sidebar == NULL; /* a centred window is all page */
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
  if (is_ok(key) && key != GDK_KEY_space && hold_begin(f, focus))
    return GDK_EVENT_STOP;
  if (key == GDK_KEY_Menu && !text) {
    GtkWidget *item = menu_item(focus, gtk_widget_get_parent(f->island));
    if (item != NULL) gtk_widget_activate_action(item, "menu.popup", NULL);
    return GDK_EVENT_STOP;
  }
  if (is_back(key)) {
    if (text && key == GDK_KEY_BackSpace)
      return GDK_EVENT_PROPAGATE;
    /* In what a row grew (a network's actions or its password): back to the row itself first. */
    GtkWidget *row = part_row_of(f, focus);
    GtkWidget *head = row != NULL && gtk_widget_has_css_class(row, "vertical") ? gtk_widget_get_first_child(row) : NULL;
    if (head != NULL && focus != head && !gtk_widget_is_ancestor(focus, head) && gtk_widget_is_drawable(head)) {
      f->self_moving++;
      gboolean moved = (gtk_widget_get_focusable(head) && gtk_widget_grab_focus(head)) || gtk_widget_child_focus(head, GTK_DIR_TAB_FORWARD);
      f->self_moving--;
      if (moved) {
        remember(f, focus_of(f));
        return GDK_EVENT_STOP;
      }
    }
    GtkWidget *back = back_part(f->island);
    if (back != NULL) {
      /* Up a level: the parent page opens, and the focus lands in it once it is drawn. */
      gtk_widget_activate(back);
      g_timeout_add_full(G_PRIORITY_LOW, 80, page_back_settled, f, NULL);
    } else if (f->sidebar == NULL) {
      gtk_window_minimize(f->window); /* a centred window: Back leaves the app, as from the island */
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
    /* the caret moves within the text; past either end the remote moves on (a field's buttons) */
    int position = gtk_editable_get_position(GTK_EDITABLE(focus));
    int length = (int)g_utf8_strlen(gtk_editable_get_text(GTK_EDITABLE(focus)), -1);
    if ((dir == GTK_DIR_RIGHT && position < length) || (dir == GTK_DIR_LEFT && position > 0))
      return GDK_EVENT_PROPAGATE;
  }
  if (text && GTK_IS_TEXT_VIEW(focus))
    return GDK_EVENT_PROPAGATE;
  if (!gtk_widget_child_focus(f->island, dir) && dir == GTK_DIR_LEFT && f->sidebar != NULL)
    to_island(f); /* off the page's left edge: the island opens again */
  return GDK_EVENT_STOP;
}

/* LUMA_TV_DEBUG timing: from a key to the first frame painted after it. */
static gint64 probe_phase[4]; /* layout start/end, paint start/end */
static void probe_layout(GdkFrameClock *clock, gpointer data) { (void)clock; (void)data; probe_phase[0] = g_get_monotonic_time(); }
static void probe_layout_done(GdkFrameClock *clock, gpointer data) { (void)clock; (void)data; probe_phase[1] = g_get_monotonic_time(); }
static void probe_paint(GdkFrameClock *clock, gpointer data) { (void)clock; (void)data; probe_phase[2] = g_get_monotonic_time(); }
static void probe_painted(GdkFrameClock *clock, TvFrame *f) {
  g_signal_handlers_disconnect_by_func(clock, probe_painted, f);
  g_signal_handlers_disconnect_by_func(clock, probe_layout, NULL);
  g_signal_handlers_disconnect_by_func(clock, probe_layout_done, NULL);
  g_signal_handlers_disconnect_by_func(clock, probe_paint, NULL);
  gint64 now = g_get_monotonic_time();
  if (f->probe_at != 0)
    g_printerr("tv timing: key to paint %.1f ms (wait %.1f, layout %.1f, draw %.1f)\n", (now - f->probe_at) / 1000.0,
               probe_phase[0] ? (probe_phase[0] - f->probe_at) / 1000.0 : -1.0,
               probe_phase[1] && probe_phase[0] ? (probe_phase[1] - probe_phase[0]) / 1000.0 : -1.0,
               probe_phase[2] && probe_phase[1] ? (probe_phase[2] - probe_phase[1]) / 1000.0 : -1.0);
  f->probe_at = 0;
  memset(probe_phase, 0, sizeof probe_phase);
}

/* Key repeat (the TV rule): a held key repeats only as moves (arrows; a focused slider's Left and
 * Right). OK, Back and Menu act once per press: a repeat belongs to the press that started it and
 * never activates what that press opened or toggles a switch again. Typing in a field repeats as
 * typing does. A press long after the key's last event (its release lost) counts as new. */
static GHashTable *held_keys(TvFrame *f) {
  GHashTable *held = g_object_get_data(G_OBJECT(f->window), "luma-tv-held-keys");
  if (held == NULL) {
    held = g_hash_table_new(g_direct_hash, g_direct_equal);
    g_object_set_data_full(G_OBJECT(f->window), "luma-tv-held-keys", held, (GDestroyNotify)g_hash_table_unref);
  }
  return held;
}

static gboolean repeat_guarded(guint key) {
  return is_ok(key) || is_back(key) || key == GDK_KEY_Menu;
}

static gboolean repeated(TvFrame *f, guint key) {
  if (!repeat_guarded(key) || editable(focus_of(f)))
    return FALSE;
  GHashTable *held = held_keys(f);
  gint64 now = g_get_monotonic_time();
  gpointer last = NULL;
  gboolean seen = g_hash_table_lookup_extended(held, GUINT_TO_POINTER(key), NULL, &last);
  /* timestamps in milliseconds fit a pointer on every target */
  g_hash_table_insert(held, GUINT_TO_POINTER(key), GSIZE_TO_POINTER((gsize)(now / 1000)));
  return seen && now / 1000 - (gint64)GPOINTER_TO_SIZE(last) < 800;
}

static gboolean key_pressed(GtkEventControllerKey *controller, guint key, guint code,
                            GdkModifierType state, TvFrame *f) {
  if (f->active && repeated(f, key))
    return GDK_EVENT_STOP;
  gboolean probe = g_getenv("LUMA_TV_DEBUG") != NULL && f->active;
  gint64 start = probe ? g_get_monotonic_time() : 0;
  f->keying++;
  gboolean result = key_handled(controller, key, code, state, f);
  f->keying--;
  if (probe) {
    g_printerr("tv timing: key handled in %.1f ms\n", (g_get_monotonic_time() - start) / 1000.0);
    GdkFrameClock *clock = gtk_widget_get_frame_clock(GTK_WIDGET(f->window));
    if (clock != NULL && f->probe_at == 0) {
      f->probe_at = start;
      memset(probe_phase, 0, sizeof probe_phase);
      /* frame phases, each handler running after GTK's own: frame start, layout done, drawn */
      g_signal_connect(clock, "before-paint", G_CALLBACK(probe_layout), NULL);
      g_signal_connect(clock, "layout", G_CALLBACK(probe_layout_done), NULL);
      g_signal_connect(clock, "paint", G_CALLBACK(probe_paint), NULL);
      g_signal_connect(clock, "after-paint", G_CALLBACK(probe_painted), f);
    }
  }
  return result;
}

/* OK released on something other than what it was pressed on (the page redrew under it and the
 * focus moved): the release does nothing, so one OK never acts twice (a switch beside a row). */
static gboolean key_released(GtkEventControllerKey *controller, guint key, guint code, GdkModifierType state, TvFrame *f) {
  (void)controller; (void)code; (void)state;
  if (f->active)
    g_hash_table_remove(held_keys(f), GUINT_TO_POINTER(key)); /* the next press is a new one */
  if (f->active && is_ok(key) && hold_end(f))
    return GDK_EVENT_STOP;
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
/* The row the remote is on, brought into view (above the keyboard once the page has shrunk). */
static void reveal_now(TvFrame *f) {
  GtkWidget *focus = f->active ? focus_of(f) : NULL;
  if (focus == NULL || !in_page(f, focus))
    return;
  GtkWidget *row = focus;
  while (row != NULL && row != f->island && !gtk_widget_has_css_class(row, "lumaui-part-row")) row = gtk_widget_get_parent(row);
  GtkWidget *target = row != NULL && row != f->island ? row : focus;
  GtkWidget *viewport = gtk_widget_get_ancestor(target, GTK_TYPE_VIEWPORT);
  if (viewport != NULL)
    gtk_viewport_scroll_to(GTK_VIEWPORT(viewport), target, NULL);
  if (g_getenv("LUMA_TV_DEBUG") != NULL)
    g_printerr("tv reveal: viewport=%p page height %d\n", (void *)viewport, viewport ? gtk_widget_get_height(viewport) : -1);
}

static gboolean reveal_focus(gpointer data) {
  TvFrame *f = data;
  f->reveal_id = 0;
  if (--f->reveal_tries > 0)
    f->reveal_id = g_timeout_add(300, reveal_focus, f);
  reveal_now(f);
  return G_SOURCE_REMOVE;
}

static gboolean reveal_tick(GtkWidget *island, GdkFrameClock *clock, gpointer data) {
  (void)clock;
  TvFrame *f = data;
  if (!f->active || g_get_monotonic_time() > f->reveal_until)
    return G_SOURCE_REMOVE;
  if (gtk_widget_get_height(island) == f->reveal_from)
    return G_SOURCE_CONTINUE;
  /* the page has its new height: bring the row up now */
  reveal_now(f);
  return G_SOURCE_REMOVE;
}

static void keyboard_height(guint height, gpointer data) {
  TvFrame *f = data;
  if (!f->active) return;
  if (g_getenv("LUMA_TV_DEBUG") != NULL) g_printerr("tv keyboard height %u\n", height);
  gtk_widget_set_margin_bottom(f->island, height > 0 ? (int)round(height * f->unit) : f->island_bottom);
  if (height > 0) {
    /* once the page has its new height: watched frame by frame (a slow device lays out late),
     * with a last look after a second whatever happened */
    g_clear_handle_id(&f->reveal_id, g_source_remove);
    f->reveal_tries = 1;
    f->reveal_id = g_timeout_add(1000, reveal_focus, f);
    f->reveal_from = gtk_widget_get_height(f->island);
    f->reveal_until = g_get_monotonic_time() + G_USEC_PER_SEC;
    gtk_widget_add_tick_callback(f->island, reveal_tick, f, NULL);
  }
}

static void active_changed(GtkWindow *window, GParamSpec *pspec, TvFrame *f) {
  (void)pspec; (void)f;
  g_printerr("tv window active=%d\n", gtk_window_is_active(window));
}

static void enter(TvFrame *f) {
  f->active = TRUE;
  f->rail = FALSE;
  gtk_widget_add_css_class(f->frame, "lumaui-tv-frame");
  luma_ui_set_css_class(f->frame, "lumaui-tv-centred", f->sidebar == NULL);
  if (f->sidebar != NULL) {
    gtk_widget_get_size_request(f->sidebar, &f->sidebar_width, &f->sidebar_height);
    f->sidebar_margin = gtk_widget_get_margin_end(f->sidebar);
    gtk_widget_set_size_request(f->sidebar, -1, -1);
    gtk_widget_set_margin_end(f->sidebar, 0);
    sidebar_set_tv(f, TRUE, FALSE);
  }

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

  /* The window's content goes over the room, under the bar's place; the app's focus is kept. */
  g_clear_weak_pointer(&f->app_focus);
  g_set_weak_pointer(&f->app_focus, focus_of(f));
  g_object_ref(f->content);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(f->window), NULL);
  gtk_overlay_add_overlay(GTK_OVERLAY(f->overlay), f->content);
  gtk_overlay_add_overlay(GTK_OVERLAY(f->overlay), f->bar);
  g_object_unref(f->content);
  adw_application_window_set_content(ADW_APPLICATION_WINDOW(f->window), f->overlay);

  f->provider = gtk_css_provider_new();
  gtk_style_context_add_provider_for_display(gtk_widget_get_display(GTK_WIDGET(f->window)),
      GTK_STYLE_PROVIDER(f->provider), GTK_STYLE_PROVIDER_PRIORITY_USER + 10U); /* over an app's own user-level rules */
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
  g_clear_handle_id(&f->reveal_id, g_source_remove);
  g_clear_handle_id(&f->restore_id, g_source_remove);
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
  page_modal_end(f);
  GtkWidget *modal = find_modal(GTK_WIDGET(f->window));
  if (modal != NULL && (gtk_widget_has_css_class(modal, "lumaui-modal") || ADW_IS_DIALOG(modal)))
    restore_page(modal);
  if (f->sidebar != NULL) {
    luma_ui_set_css_class(f->sidebar, "lumaui-tv-rail", FALSE);
    sidebar_set_tv(f, FALSE, FALSE);
    gtk_widget_set_size_request(f->sidebar, f->sidebar_width, f->sidebar_height);
    gtk_widget_set_margin_end(f->sidebar, f->sidebar_margin);
  }
  gtk_widget_remove_css_class(f->frame, "lumaui-tv-frame");
  gtk_widget_remove_css_class(f->frame, "lumaui-tv-centred");
  f->rail = FALSE;
}

static void frame_free(TvFrame *f) {
  g_clear_handle_id(&f->restore_id, g_source_remove);
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
  g_clear_weak_pointer(&f->page_modal);
  g_clear_weak_pointer(&f->app_focus);
  g_free(f->held_title);
  g_free(f->held_page);
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
  gboolean handled = f != NULL && key_pressed(NULL, keyval, 0, 0, f) == GDK_EVENT_STOP;
  if (f != NULL && f->active) /* one whole press: the key is let go again */
    g_hash_table_remove(held_keys(f), GUINT_TO_POINTER(keyval));
  return handled;
}

gboolean luma_tv_frame_key_release(GtkWindow *window, guint keyval) {
  TvFrame *f = g_object_get_data(G_OBJECT(window), "luma-tv-frame");
  return f != NULL && key_released(NULL, keyval, 0, 0, f) == GDK_EVENT_STOP;
}

gboolean luma_tv_frame_key_held(GtkWindow *window, guint keyval) {
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

/* ── any window: the public attach (luma-ui-kit.h) ─────────────────────────────────────────── */

void luma_window_set_tv_frame(GtkWindow *window, GtkWidget *content, GtkWidget *frame,
                              GtkWidget *sidebar, GtkWidget *island) {
  g_return_if_fail(ADW_IS_APPLICATION_WINDOW(window));
  g_return_if_fail(content == NULL || GTK_IS_WIDGET(content));
  g_return_if_fail(frame == NULL || GTK_IS_WIDGET(frame));
  g_return_if_fail(sidebar == NULL || GTK_IS_WIDGET(sidebar));
  g_return_if_fail(island == NULL || GTK_IS_WIDGET(island));
  TvFrame *old = g_object_get_data(G_OBJECT(window), "luma-tv-frame");
  gboolean was_active = old != NULL && old->active;
  GtkWidget *keep = gtk_root_get_focus(GTK_ROOT(window)); /* the app's focus, across the move */
  if (keep != NULL) g_object_ref(keep);
  if (content == NULL || frame == NULL || island == NULL) {
    g_clear_object(&keep);
    luma_tv_frame_attach(window, NULL, NULL, NULL, NULL); /* no frame: the window's own layout */
    return;
  }
  if (sidebar != NULL) {
    luma_tv_frame_attach(window, content, frame, sidebar, island);
  } else {
    /* A centred window: the same room, bar and page treatment, no island. */
    luma_tv_frame_attach(window, NULL, NULL, NULL, NULL);
    TvFrame *f = g_new0(TvFrame, 1);
    f->path = g_array_new(FALSE, FALSE, sizeof(int));
    f->window = window;
    f->content = content;
    f->frame = frame;
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
  TvFrame *f = g_object_get_data(G_OBJECT(window), "luma-tv-frame");
  if (f != NULL) f->generic = sidebar != NULL && !LUMA_IS_NAVIGATION_SIDEBAR(sidebar);
  if (was_active) luma_tv_frame_set_active(window, TRUE);
  /* GTK may move the focus while the content moves out of one frame and into the next: the focus the
   * app had before any of it is the one kept. */
  if (f != NULL && keep != NULL && gtk_widget_is_ancestor(keep, island)) {
    g_clear_weak_pointer(&f->app_focus);
    g_set_weak_pointer(&f->app_focus, keep);
    if (f->active) queue_settle(f);
  }
  g_clear_object(&keep);
}

void luma_window_set_tv_active(GtkWindow *window, gboolean tv) {
  g_return_if_fail(GTK_IS_WINDOW(window));
  luma_tv_frame_set_active(window, tv);
}

gboolean luma_window_get_tv_active(GtkWindow *window) {
  g_return_val_if_fail(GTK_IS_WINDOW(window), FALSE);
  return luma_tv_frame_is_active(window);
}
