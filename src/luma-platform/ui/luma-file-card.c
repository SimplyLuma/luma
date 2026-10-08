/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_file.py: split_name, rank_apps, OpenButton, FileCard,
 * OpenInMenu. Everything reads the file through GIO. */
#include "luma-file-card.h"
#include "luma-content-private.h"
#include "luma-menu-drawer.h"
#include "luma-toast.h"
#include "luma-ui-private.h"

#include <string.h>

/* Desktop ids of Luma's own applications start with this... */
#define LUMA_APP_PREFIX "org.projectluma."
/* ...except the launchers Luma writes for packages it installed (ADR-050). */
#define INSTALLED_PREFIX "org.projectluma.Installed."

#define FILE_ATTRIBUTES                                                                                   \
  "standard::display-name,standard::size,standard::content-type,standard::fast-content-type,"           \
  "thumbnail::path,thumbnail::is-valid,standard::type"

/* ── split_name ─────────────────────────────────────────────────────────── */

static gboolean ext_char(gunichar ch) {
  return ch < 128 && g_ascii_isalnum((char)ch);
}

/* Python: ^(.+?)(.{0,5}\.[A-Za-z0-9]{1,6})$ with DOTALL, on characters. */
void luma_file_split_name(const char *name, char **head, char **tail) {
  g_return_if_fail(name != NULL);
  glong n = g_utf8_strlen(name, -1);
  gunichar *chars = g_utf8_to_ucs4_fast(name, -1, NULL);
  glong cut = -1;
  /* The head is lazy: the first split that lets the tail match wins. */
  for (glong h = 1; h < n && cut < 0; h++) {
    for (glong dot = h; dot <= h + 5 && dot < n; dot++) {
      if (chars[dot] != '.')
        continue;
      glong ext = n - dot - 1;
      if (ext < 1 || ext > 6)
        continue;
      gboolean ok = TRUE;
      for (glong i = dot + 1; i < n; i++)
        ok = ok && ext_char(chars[i]);
      if (ok) {
        cut = h;
        break;
      }
    }
  }
  g_free(chars);
  if (cut < 0) {
    if (head != NULL)
      *head = g_strdup(name);
    if (tail != NULL)
      *tail = g_strdup("");
    return;
  }
  const char *split = g_utf8_offset_to_pointer(name, cut);
  if (head != NULL)
    *head = g_strndup(name, (gsize)(split - name));
  if (tail != NULL)
    *tail = g_strdup(split);
}

/* ── apps ───────────────────────────────────────────────────────────────── */

static gboolean is_luma_app(const char *id) {
  return id != NULL && g_str_has_prefix(id, LUMA_APP_PREFIX) && !g_str_has_prefix(id, INSTALLED_PREFIX);
}

int _luma_file_app_order(const char *id_a, const char *name_a, const char *id_b, const char *name_b,
                         const char *default_id) {
  int luma = (int)!is_luma_app(id_a) - (int)!is_luma_app(id_b);
  if (luma != 0)
    return luma;
  int deflt = (int)(g_strcmp0(id_a, default_id) != 0) - (int)(g_strcmp0(id_b, default_id) != 0);
  if (deflt != 0)
    return deflt;
  g_autofree char *fa = g_utf8_casefold(name_a != NULL ? name_a : "", -1);
  g_autofree char *fb = g_utf8_casefold(name_b != NULL ? name_b : "", -1);
  return strcmp(fa, fb);
}

static int app_compare(gconstpointer a, gconstpointer b, gpointer default_id) {
  GAppInfo *x = *(GAppInfo *const *)a, *y = *(GAppInfo *const *)b;
  return _luma_file_app_order(g_app_info_get_id(x), g_app_info_get_name(x), g_app_info_get_id(y),
                              g_app_info_get_name(y), default_id);
}

static GAppInfo *default_app(const char *content_type) {
  if (content_type == NULL || content_type[0] == '\0')
    return NULL;
  return g_app_info_get_default_for_type(content_type, FALSE);
}

/* rank_apps: the apps that should show, once each, in menu order. */
static GPtrArray *ranked_apps(const char *content_type, const char *default_id) {
  GPtrArray *apps = g_ptr_array_new_with_free_func(g_object_unref);
  if (content_type == NULL || content_type[0] == '\0')
    return apps;
  g_autoptr(GHashTable) seen = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
  GList *found = g_app_info_get_all_for_type(content_type);
  for (GList *l = found; l != NULL; l = l->next) {
    GAppInfo *app = l->data;
    const char *id = g_app_info_get_id(app);
    if (!g_app_info_should_show(app) || id == NULL || g_hash_table_contains(seen, id))
      continue;
    g_hash_table_add(seen, g_strdup(id));
    g_ptr_array_add(apps, g_object_ref(app));
  }
  g_list_free_full(found, g_object_unref);
  g_ptr_array_sort_with_data(apps, app_compare, (gpointer)default_id);
  return apps;
}

/* The app a file opens in: the first in Open-in order, not only Gio's default. */
static GAppInfo *best_app(const char *content_type) {
  g_autoptr(GAppInfo) deflt = default_app(content_type);
  g_autoptr(GPtrArray) ranked = ranked_apps(content_type, deflt != NULL ? g_app_info_get_id(deflt) : NULL);
  if (ranked->len > 0)
    return g_object_ref(g_ptr_array_index(ranked, 0));
  return g_steal_pointer(&deflt);
}

static char *guess_type(const char *name) {
  return g_content_type_guess(name, NULL, 0, NULL);
}

/* Open @file in @app (or the default), telling the person if it could not be done. */
static void launch(GtkWidget *widget, GFile *file, GAppInfo *app, const char *name) {
  if (file == NULL)
    return;
  if (app != NULL) {
    GdkDisplay *display = gtk_widget_get_display(widget);
    g_autoptr(GdkAppLaunchContext) context = gdk_display_get_app_launch_context(display);
    GList files = {file, NULL, NULL};
    g_autoptr(GError) error = NULL;
    if (!g_app_info_launch(app, &files, G_APP_LAUNCH_CONTEXT(context), &error)) {
      g_autofree char *message = g_strdup_printf("Couldn’t open %s", name);
      luma_toast_show(widget, message, "error");
    }
    return;
  }
  GtkRoot *root = gtk_widget_get_root(widget);
  g_autoptr(GtkFileLauncher) launcher = gtk_file_launcher_new(file);
  gtk_file_launcher_launch(launcher, GTK_IS_WINDOW(root) ? GTK_WINDOW(root) : NULL, NULL, NULL, NULL);
}

static char *file_basename_or(GFile *file, const char *fallback) {
  char *base = file != NULL ? g_file_get_basename(file) : NULL;
  return base != NULL ? base : g_strdup(fallback);
}

/* ── OpenButton ─────────────────────────────────────────────────────────── */

struct _LumaOpenButton {
  GtkButton parent_instance;
  GFile *file;
  GAppInfo *app;
  GtkWidget *label;
  char *words;
  gboolean handled_by_card;
};

G_DEFINE_FINAL_TYPE(LumaOpenButton, luma_open_button, GTK_TYPE_BUTTON)

static void open_button_refresh_name(LumaOpenButton *self) {
  g_autofree char *name = self->app != NULL ? g_strdup_printf("Open in %s", g_app_info_get_name(self->app))
                                            : g_strdup(self->words);
  gtk_widget_set_tooltip_text(GTK_WIDGET(self), name);
  luma_ui_set_accessible_label(GTK_WIDGET(self), name);
}

static void open_button_clicked(GtkButton *button) {
  LumaOpenButton *self = LUMA_OPEN_BUTTON(button);
  if (self->handled_by_card || gtk_actionable_get_action_name(GTK_ACTIONABLE(self)) != NULL)
    return;
  g_autofree char *name = file_basename_or(self->file, "it");
  launch(GTK_WIDGET(self), self->file, self->app, name);
}

static void open_button_dispose(GObject *object) {
  LumaOpenButton *self = LUMA_OPEN_BUTTON(object);
  g_clear_object(&self->file);
  g_clear_object(&self->app);
  G_OBJECT_CLASS(luma_open_button_parent_class)->dispose(object);
}

static void open_button_finalize(GObject *object) {
  g_free(LUMA_OPEN_BUTTON(object)->words);
  G_OBJECT_CLASS(luma_open_button_parent_class)->finalize(object);
}

static void luma_open_button_class_init(LumaOpenButtonClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = open_button_dispose;
  G_OBJECT_CLASS(klass)->finalize = open_button_finalize;
  GTK_BUTTON_CLASS(klass)->clicked = open_button_clicked;
}

static void luma_open_button_init(LumaOpenButton *self) {
  luma_ui_install();
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-open-button");
}

GtkWidget *luma_open_button_new(GFile *file, const char *content_type, gboolean small) {
  g_return_val_if_fail(file == NULL || G_IS_FILE(file), NULL);
  LumaOpenButton *self = g_object_new(LUMA_TYPE_OPEN_BUTTON, NULL);
  luma_ui_set_css_class(GTK_WIDGET(self), "small", small);
  self->file = file != NULL ? g_object_ref(file) : NULL;
  g_autofree char *guessed = NULL;
  if (content_type == NULL && file != NULL) {
    g_autofree char *base = file_basename_or(file, "");
    content_type = guessed = guess_type(base);
  }
  self->app = best_app(content_type);
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(line, "lumaui-open-content");
  GIcon *gicon = self->app != NULL ? g_app_info_get_icon(self->app) : NULL;
  GtkWidget *image;
  if (gicon != NULL) {
    image = gtk_image_new_from_gicon(gicon);
    gtk_widget_add_css_class(image, "lumaui-app-icon");
  } else {
    image = luma_ui_icon_image("square-arrow-out-up-right", 0);
  }
  gtk_box_append(GTK_BOX(line), image);
  self->words = g_strdup("Open");
  self->label = gtk_label_new(self->words);
  gtk_box_append(GTK_BOX(line), self->label);
  gtk_button_set_child(GTK_BUTTON(self), line);
  open_button_refresh_name(self);
  return GTK_WIDGET(self);
}

void luma_open_button_set_label(LumaOpenButton *self, const char *label) {
  g_return_if_fail(LUMA_IS_OPEN_BUTTON(self));
  g_return_if_fail(label != NULL);
  g_free(self->words);
  self->words = g_strdup(label);
  gtk_label_set_label(GTK_LABEL(self->label), label);
  open_button_refresh_name(self);
}

/* ── OpenInMenu ─────────────────────────────────────────────────────────── */

enum { MENU_APP_CHOSEN, MENU_N_SIGNALS };
static guint menu_signals[MENU_N_SIGNALS];

struct _LumaOpenInMenu {
  GObject parent_instance;
  GFile *file;
  char *content_type;
  char *default_id;
  GPtrArray *apps;
  GtkWidget *menu;
  GtkWidget *anchor;
};

G_DEFINE_FINAL_TYPE(LumaOpenInMenu, luma_open_in_menu, G_TYPE_OBJECT)

static void menu_open(GSimpleAction *action G_GNUC_UNUSED, GVariant *parameter, gpointer user_data) {
  LumaOpenInMenu *self = user_data;
  const char *id = g_variant_get_string(parameter, NULL);
  GAppInfo *app = NULL;
  for (guint i = 0; i < self->apps->len && app == NULL; i++)
    if (g_strcmp0(g_app_info_get_id(g_ptr_array_index(self->apps, i)), id) == 0)
      app = g_ptr_array_index(self->apps, i);
  if (app == NULL)
    return;
  g_object_ref(self);
  gboolean handled = FALSE;
  g_signal_emit(self, menu_signals[MENU_APP_CHOSEN], 0, app, &handled);
  if (!handled && self->anchor != NULL) {
    g_autofree char *name = file_basename_or(self->file, "it");
    launch(self->anchor, self->file, app, name);
  }
  luma_open_in_menu_close(self);
  g_object_unref(self);
}

static void menu_other(GSimpleAction *action G_GNUC_UNUSED, GVariant *parameter G_GNUC_UNUSED, gpointer user_data) {
  LumaOpenInMenu *self = user_data;
  if (self->file == NULL)
    return;
  /* Always ask: the desktop's chooser (Luma's portal) lists every app and can set the default. */
  g_autoptr(GtkFileLauncher) launcher = gtk_file_launcher_new(self->file);
  gtk_file_launcher_set_always_ask(launcher, TRUE);
  GtkRoot *root = self->anchor != NULL ? gtk_widget_get_root(self->anchor) : NULL;
  gtk_file_launcher_launch(launcher, GTK_IS_WINDOW(root) ? GTK_WINDOW(root) : NULL, NULL, NULL, NULL);
  luma_open_in_menu_close(self);
}

static void open_in_menu_dispose(GObject *object) {
  LumaOpenInMenu *self = LUMA_OPEN_IN_MENU(object);
  luma_open_in_menu_close(self);
  g_clear_weak_pointer(&self->anchor);
  g_clear_object(&self->file);
  g_clear_pointer(&self->apps, g_ptr_array_unref);
  G_OBJECT_CLASS(luma_open_in_menu_parent_class)->dispose(object);
}

static void open_in_menu_finalize(GObject *object) {
  LumaOpenInMenu *self = LUMA_OPEN_IN_MENU(object);
  g_free(self->content_type);
  g_free(self->default_id);
  G_OBJECT_CLASS(luma_open_in_menu_parent_class)->finalize(object);
}

static void luma_open_in_menu_class_init(LumaOpenInMenuClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = open_in_menu_dispose;
  G_OBJECT_CLASS(klass)->finalize = open_in_menu_finalize;
  /**
   * LumaOpenInMenu::app-chosen:
   * @self: the menu
   * @app: the app chosen
   *
   * Returns: %TRUE when handled; otherwise the file opens in @app
   */
  menu_signals[MENU_APP_CHOSEN] =
      g_signal_new("app-chosen", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, g_signal_accumulator_true_handled,
                   NULL, NULL, G_TYPE_BOOLEAN, 1, G_TYPE_APP_INFO);
}

static void luma_open_in_menu_init(LumaOpenInMenu *self G_GNUC_UNUSED) {}

LumaOpenInMenu *luma_open_in_menu_new(GFile *file, const char *content_type) {
  g_return_val_if_fail(file == NULL || G_IS_FILE(file), NULL);
  LumaOpenInMenu *self = g_object_new(LUMA_TYPE_OPEN_IN_MENU, NULL);
  self->file = file != NULL ? g_object_ref(file) : NULL;
  if (content_type == NULL && file != NULL) {
    g_autofree char *base = file_basename_or(file, "");
    self->content_type = guess_type(base);
  } else {
    self->content_type = g_strdup(content_type);
  }
  g_autoptr(GAppInfo) deflt = default_app(self->content_type);
  self->default_id = deflt != NULL ? g_strdup(g_app_info_get_id(deflt)) : NULL;
  self->apps = ranked_apps(self->content_type, self->default_id);
  return self;
}

void luma_open_in_menu_popup(LumaOpenInMenu *self, GtkWidget *anchor) {
  g_return_if_fail(LUMA_IS_OPEN_IN_MENU(self));
  g_return_if_fail(GTK_IS_WIDGET(anchor));
  luma_open_in_menu_close(self);
  g_set_weak_pointer(&self->anchor, anchor);
  LumaFloatingMenu *menu = LUMA_FLOATING_MENU(luma_floating_menu_new("Open in"));
  gtk_widget_add_css_class(GTK_WIDGET(menu), "open-in");
  GSimpleActionGroup *group = g_simple_action_group_new();
  GSimpleAction *open = g_simple_action_new("open", G_VARIANT_TYPE_STRING);
  g_signal_connect(open, "activate", G_CALLBACK(menu_open), self);
  g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(open));
  g_object_unref(open);
  GSimpleAction *other = g_simple_action_new("other", NULL);
  g_signal_connect(other, "activate", G_CALLBACK(menu_other), self);
  g_action_map_add_action(G_ACTION_MAP(group), G_ACTION(other));
  g_object_unref(other);
  gtk_widget_insert_action_group(GTK_WIDGET(menu), "openin", G_ACTION_GROUP(group));
  g_object_unref(group);
  luma_floating_menu_add_heading(menu, "Open in");
  for (guint i = 0; i < self->apps->len; i++) {
    GAppInfo *app = g_ptr_array_index(self->apps, i);
    const char *id = g_app_info_get_id(app);
    g_autofree char *action = g_action_print_detailed_name("openin.open", g_variant_new_string(id));
    luma_floating_menu_add_item(menu, g_app_info_get_name(app), NULL, g_app_info_get_icon(app),
                                g_strcmp0(id, self->default_id) == 0 ? "Default" : NULL, action, FALSE);
  }
  if (self->file != NULL) {
    luma_floating_menu_add_separator(menu);
    luma_floating_menu_add_item(menu, "Other app…", "app-window", NULL, NULL, "openin.other", FALSE);
  }
  self->menu = g_object_ref_sink(GTK_WIDGET(menu));
  luma_floating_menu_popup(menu, anchor);
}

void luma_open_in_menu_close(LumaOpenInMenu *self) {
  g_return_if_fail(LUMA_IS_OPEN_IN_MENU(self));
  if (self->menu == NULL)
    return;
  GtkWidget *menu = g_steal_pointer(&self->menu);
  if (luma_floating_menu_get_is_open(LUMA_FLOATING_MENU(menu)))
    luma_floating_menu_close(LUMA_FLOATING_MENU(menu));
  g_object_unref(menu);
}

/* ── FileCard ───────────────────────────────────────────────────────────── */

enum { CARD_OPEN, CARD_N_SIGNALS };
static guint card_signals[CARD_N_SIGNALS];

struct _LumaFileCard {
  GtkBox parent_instance;
  GFile *file;
  GFileInfo *info;
  gboolean compact;
  char *name;         /* set by the app, or NULL */
  gboolean size_set;  /* the app said the size (or -1: nothing) */
  gint64 size;
  char *kind;         /* set by the app, or NULL */
  char *content_type; /* set by the app, or NULL */
  char *subtitle;
  char *tone;
  GtkWidget *extra;
  GPtrArray *actions;
  GdkPaintable *thumbnail; /* the app's, or the one loaded */
  GAppInfo *app;
  GtkWidget *face;
  GtkWidget *face_icon;
  GtkWidget *open_button;
  GCancellable *loading;
};

G_DEFINE_FINAL_TYPE(LumaFileCard, luma_file_card, GTK_TYPE_BOX)

static const char *card_name(LumaFileCard *self, char **owned) {
  if (self->name != NULL)
    return self->name;
  if (self->info != NULL && g_file_info_get_display_name(self->info) != NULL)
    return g_file_info_get_display_name(self->info);
  return *owned = file_basename_or(self->file, "");
}

static char *card_content_type(LumaFileCard *self, const char *name) {
  if (self->content_type != NULL)
    return g_strdup(self->content_type);
  const char *type = self->info != NULL ? g_file_info_get_content_type(self->info) : NULL;
  return type != NULL ? g_strdup(type) : guess_type(name);
}

static gint64 card_size(LumaFileCard *self) {
  if (self->size_set)
    return self->size;
  if (self->info != NULL && g_file_info_get_file_type(self->info) == G_FILE_TYPE_REGULAR)
    return g_file_info_get_size(self->info);
  return -1;
}

static void card_show_face(LumaFileCard *self) {
  gboolean picture = self->thumbnail != NULL;
  if (self->face != NULL) {
    _luma_content_face_set_paintable(self->face, self->thumbnail);
    gtk_widget_set_visible(self->face, picture);
    gtk_widget_set_visible(self->face_icon, !picture);
  }
  luma_ui_set_css_class(GTK_WIDGET(self), "picture", picture);
}

static void card_open_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  LumaFileCard *self = user_data;
  gboolean handled = FALSE;
  g_signal_emit(self, card_signals[CARD_OPEN], 0, &handled);
  if (!handled) {
    g_autofree char *owned = NULL;
    launch(GTK_WIDGET(self), self->file, self->app, card_name(self, &owned));
  }
}

/* Build the card from what it knows: the same tree Python builds once. */
static void file_card_rebuild(LumaFileCard *self) {
  GtkWidget *widget = GTK_WIDGET(self);
  if (self->extra != NULL && gtk_widget_get_parent(self->extra) != NULL)
    gtk_box_remove(GTK_BOX(gtk_widget_get_parent(self->extra)), self->extra);
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(widget)) != NULL)
    gtk_box_remove(GTK_BOX(self), child);
  self->face = self->face_icon = self->open_button = NULL;

  g_autofree char *owned = NULL;
  const char *name = card_name(self, &owned);
  g_autofree char *content_type = card_content_type(self, name);
  gint64 size = card_size(self);
  g_autofree char *kind = self->kind != NULL ? g_strdup(self->kind) : g_content_type_get_description(content_type);
  g_clear_object(&self->app);
  self->app = best_app(content_type);

  /* The face: a picture cropped into its square, or the app's icon. */
  GtkWidget *slot = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(slot, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(slot, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(slot, "lumaui-file-slot");
  self->face = _luma_content_face_new(self->compact ? LUMA_UI_FILE_CARD_MINI_FACE : LUMA_UI_FILE_CARD_FACE,
                                      LUMA_UI_FILE_CARD_FACE_RADIUS);
  self->face_icon = gtk_image_new();
  gtk_widget_add_css_class(self->face_icon, "lumaui-file-icon");
  gtk_image_set_pixel_size(GTK_IMAGE(self->face_icon),
                           self->compact ? LUMA_UI_FILE_CARD_MINI_FACE : LUMA_UI_FILE_CARD_FACE_ICON);
  GIcon *app_icon = self->app != NULL ? g_app_info_get_icon(self->app) : NULL;
  g_autoptr(GIcon) type_icon = app_icon == NULL && content_type != NULL ? g_content_type_get_icon(content_type) : NULL;
  if (app_icon != NULL || type_icon != NULL)
    gtk_image_set_from_gicon(GTK_IMAGE(self->face_icon), app_icon != NULL ? app_icon : type_icon);
  gtk_box_append(GTK_BOX(slot), self->face_icon);
  gtk_box_append(GTK_BOX(slot), self->face);
  gtk_widget_set_visible(self->face, FALSE);
  gtk_box_append(GTK_BOX(self), slot);
  card_show_face(self);

  /* Name and second line. */
  GtkWidget *text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_widget_set_valign(text, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(text, "lumaui-file-text");
  g_autofree char *head = NULL, *tail = NULL;
  luma_file_split_name(name, &head, &tail);
  GtkWidget *name_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(name_row, "lumaui-file-name");
  GtkWidget *name_head = gtk_label_new(head);
  gtk_label_set_xalign(GTK_LABEL(name_head), 0);
  gtk_label_set_ellipsize(GTK_LABEL(name_head), PANGO_ELLIPSIZE_END);
  gtk_label_set_width_chars(GTK_LABEL(name_head), 1);
  gtk_box_append(GTK_BOX(name_row), name_head);
  if (tail[0] != '\0') {
    GtkWidget *name_tail = gtk_label_new(tail);
    gtk_label_set_xalign(GTK_LABEL(name_tail), 0);
    gtk_box_append(GTK_BOX(name_row), name_tail);
  }
  gtk_widget_set_tooltip_text(name_row, name);
  gtk_box_append(GTK_BOX(text), name_row);
  g_autofree char *size_text = size >= 0 ? g_format_size((guint64)size) : NULL;
  const char *what = self->subtitle != NULL && self->subtitle[0] != '\0' ? self->subtitle : kind;
  g_autofree char *second = size_text != NULL && what != NULL && what[0] != '\0'
                                ? g_strdup_printf("%s · %s", size_text, what)
                                : g_strdup(size_text != NULL ? size_text : (what != NULL ? what : ""));
  GtkWidget *second_line = gtk_label_new(second);
  gtk_label_set_xalign(GTK_LABEL(second_line), 0);
  gtk_label_set_ellipsize(GTK_LABEL(second_line), PANGO_ELLIPSIZE_END);
  gtk_widget_add_css_class(second_line, "lumaui-file-meta");
  if (self->tone != NULL)
    gtk_widget_add_css_class(second_line, self->tone);
  gtk_box_append(GTK_BOX(text), second_line);
  if (self->extra != NULL)
    gtk_box_append(GTK_BOX(text), self->extra);
  gtk_box_append(GTK_BOX(self), text);

  /* Open, or the card's own actions. */
  if (self->actions->len > 0) {
    for (guint i = 0; i < self->actions->len; i++)
      gtk_box_append(GTK_BOX(self), luma_bar_item_create_control(g_ptr_array_index(self->actions, i), "file"));
  } else {
    self->open_button = luma_open_button_new(self->file, content_type, TRUE);
    LUMA_OPEN_BUTTON(self->open_button)->handled_by_card = TRUE;
    g_signal_connect(self->open_button, "clicked", G_CALLBACK(card_open_clicked), self);
    gtk_box_append(GTK_BOX(self), self->open_button);
  }
  g_autofree char *label = g_strdup_printf("%s, %s", name, second);
  luma_ui_set_accessible_label(widget, label);
}

/* The face loads off the main thread: the file's cached thumbnail, or the
 * picture itself when it is one. */
static void load_face_thread(GTask *task, gpointer source G_GNUC_UNUSED, gpointer data,
                             GCancellable *cancellable G_GNUC_UNUSED) {
  const char *path = data;
  GError *error = NULL;
  int scale = 2 * LUMA_UI_FILE_CARD_FACE;
  g_autoptr(GdkPixbuf) pixbuf = gdk_pixbuf_new_from_file_at_scale(path, scale, scale, TRUE, &error);
  if (pixbuf == NULL) {
    g_task_return_error(task, error);
    return;
  }
  G_GNUC_BEGIN_IGNORE_DEPRECATIONS
  GdkTexture *texture = gdk_texture_new_for_pixbuf(pixbuf);
  G_GNUC_END_IGNORE_DEPRECATIONS
  g_task_return_pointer(task, texture, g_object_unref);
}

static void load_face_done(GObject *source, GAsyncResult *result, gpointer data G_GNUC_UNUSED) {
  LumaFileCard *self = LUMA_FILE_CARD(source);
  g_autoptr(GdkTexture) texture = g_task_propagate_pointer(G_TASK(result), NULL);
  if (texture == NULL || self->thumbnail != NULL)
    return;
  self->thumbnail = GDK_PAINTABLE(g_steal_pointer(&texture));
  card_show_face(self);
}

static void file_card_load_face(LumaFileCard *self) {
  g_autofree char *path = NULL;
  if (self->info != NULL && g_file_info_get_attribute_boolean(self->info, G_FILE_ATTRIBUTE_THUMBNAIL_IS_VALID))
    path = g_strdup(g_file_info_get_attribute_byte_string(self->info, G_FILE_ATTRIBUTE_THUMBNAIL_PATH));
  if (path == NULL) {
    g_autofree char *owned = NULL;
    g_autofree char *type = card_content_type(self, card_name(self, &owned));
    gint64 size = card_size(self);
    if (type != NULL && g_content_type_is_mime_type(type, "image/*") && MAX(size, 0) < 40000000)
      path = g_file_get_path(self->file);
  }
  if (path == NULL)
    return;
  GTask *task = g_task_new(self, self->loading, load_face_done, NULL);
  g_task_set_task_data(task, g_steal_pointer(&path), g_free);
  g_task_run_in_thread(task, load_face_thread);
  g_object_unref(task);
}

static void file_card_dispose(GObject *object) {
  LumaFileCard *self = LUMA_FILE_CARD(object);
  g_cancellable_cancel(self->loading);
  g_clear_object(&self->loading);
  g_clear_object(&self->file);
  g_clear_object(&self->info);
  g_clear_object(&self->extra);
  g_clear_object(&self->thumbnail);
  g_clear_object(&self->app);
  g_clear_pointer(&self->actions, g_ptr_array_unref);
  G_OBJECT_CLASS(luma_file_card_parent_class)->dispose(object);
}

static void file_card_finalize(GObject *object) {
  LumaFileCard *self = LUMA_FILE_CARD(object);
  g_free(self->name);
  g_free(self->kind);
  g_free(self->content_type);
  g_free(self->subtitle);
  g_free(self->tone);
  G_OBJECT_CLASS(luma_file_card_parent_class)->finalize(object);
}

static void luma_file_card_class_init(LumaFileCardClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = file_card_dispose;
  G_OBJECT_CLASS(klass)->finalize = file_card_finalize;
  /**
   * LumaFileCard::open:
   * @self: the card
   *
   * Open was pressed.
   *
   * Returns: %TRUE when handled; otherwise the file opens in its app
   */
  card_signals[CARD_OPEN] = g_signal_new("open", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0,
                                         g_signal_accumulator_true_handled, NULL, NULL, G_TYPE_BOOLEAN, 0);
}

static void luma_file_card_init(LumaFileCard *self) {
  luma_ui_install();
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_CENTER);
  gtk_widget_set_halign(GTK_WIDGET(self), GTK_ALIGN_START);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-file-card");
  self->actions = g_ptr_array_new_with_free_func(g_object_unref);
  self->loading = g_cancellable_new();
  self->size = -1;
}

GtkWidget *luma_file_card_new(GFile *file, gboolean compact) {
  g_return_val_if_fail(G_IS_FILE(file), NULL);
  LumaFileCard *self = g_object_new(LUMA_TYPE_FILE_CARD, NULL);
  self->file = g_object_ref(file);
  self->compact = compact;
  luma_ui_set_css_class(GTK_WIDGET(self), "compact", compact);
  _luma_content_cap_width(GTK_WIDGET(self), compact ? LUMA_UI_FILE_CARD_MINI_WIDTH : LUMA_UI_FILE_CARD_WIDTH);
  self->info = g_file_query_info(file, FILE_ATTRIBUTES, G_FILE_QUERY_INFO_NONE, NULL, NULL);
  file_card_rebuild(self);
  file_card_load_face(self);
  return GTK_WIDGET(self);
}

static void replace_string(char **field, const char *value) {
  g_free(*field);
  *field = g_strdup(value);
}

void luma_file_card_set_name(LumaFileCard *self, const char *name) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  replace_string(&self->name, name != NULL && name[0] != '\0' ? name : NULL);
  file_card_rebuild(self);
}

void luma_file_card_set_size(LumaFileCard *self, gint64 size) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  self->size_set = TRUE;
  self->size = size < 0 ? -1 : size;
  file_card_rebuild(self);
}

void luma_file_card_set_kind(LumaFileCard *self, const char *kind) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  replace_string(&self->kind, kind != NULL && kind[0] != '\0' ? kind : NULL);
  file_card_rebuild(self);
}

void luma_file_card_set_content_type(LumaFileCard *self, const char *content_type) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  replace_string(&self->content_type, content_type != NULL && content_type[0] != '\0' ? content_type : NULL);
  file_card_rebuild(self);
}

void luma_file_card_set_subtitle(LumaFileCard *self, const char *subtitle) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  replace_string(&self->subtitle, subtitle);
  file_card_rebuild(self);
}

void luma_file_card_set_tone(LumaFileCard *self, const char *tone) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  if (tone != NULL && !g_str_equal(tone, "danger") && !g_str_equal(tone, "warning")) {
    g_critical("a file card's tone is danger or warning (not '%s')", tone);
    return;
  }
  replace_string(&self->tone, tone);
  file_card_rebuild(self);
}

void luma_file_card_set_thumbnail(LumaFileCard *self, GdkPaintable *thumbnail) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  g_return_if_fail(thumbnail == NULL || GDK_IS_PAINTABLE(thumbnail));
  g_set_object(&self->thumbnail, thumbnail);
  card_show_face(self);
}

void luma_file_card_set_extra(LumaFileCard *self, GtkWidget *extra) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  g_return_if_fail(extra == NULL || GTK_IS_WIDGET(extra));
  if (self->extra != NULL && gtk_widget_get_parent(self->extra) != NULL)
    gtk_box_remove(GTK_BOX(gtk_widget_get_parent(self->extra)), self->extra);
  g_clear_object(&self->extra);
  if (extra != NULL)
    self->extra = g_object_ref_sink(extra);
  file_card_rebuild(self);
}

void luma_file_card_add_action(LumaFileCard *self, LumaBarItem *action) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  g_return_if_fail(LUMA_IS_BAR_ITEM(action));
  if (luma_bar_item_get_kind(action) != LUMA_BAR_ITEM_ACTION) {
    g_critical("a file card's actions are bar actions");
    return;
  }
  g_ptr_array_add(self->actions, g_object_ref(action));
  file_card_rebuild(self);
}

void luma_file_card_set_selected(LumaFileCard *self, gboolean selected) {
  g_return_if_fail(LUMA_IS_FILE_CARD(self));
  luma_ui_set_css_class(GTK_WIDGET(self), "selected", selected);
}

GFile *luma_file_card_get_file(LumaFileCard *self) {
  g_return_val_if_fail(LUMA_IS_FILE_CARD(self), NULL);
  return self->file;
}
