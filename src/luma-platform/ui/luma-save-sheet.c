/* SPDX-License-Identifier: Apache-2.0 */
/* The save changes sheet: the one way every Luma application asks before it
 * closes or quits with unsaved work, in C and, through GObject introspection,
 * in Python.
 *
 * The application gives facts (the document, whether it was ever saved, when
 * its first unsaved edit happened, where its files usually go) and the sheet
 * supplies every word, the layout and the keys, so the question reads the same
 * in every application and never states anything the application did not
 * record.
 *
 * The sheet belongs to the window that asked. It hangs from the bottom of that
 * window's title bar and dims only that window's content; other windows and
 * the desktop keep working. Save (or Review changes… for several documents) is
 * the one full-width primary, Cancel sits under it, and the destructive choice
 * is set apart below as a small red text button. Enter takes the primary unless
 * focus is on another control, Escape cancels, and Ctrl+D is the one key that
 * discards. Nothing destructive is ever the default. */

#include "luma-save-sheet.h"
#include "luma-ui.h"

#include <errno.h>
#include <math.h>
#include <string.h>

#define LUMA_SAVE_DOMAIN "luma-appkit"
#define S_(text) g_dgettext (LUMA_SAVE_DOMAIN, text)
#define NS_(one, many, n) g_dngettext (LUMA_SAVE_DOMAIN, one, many, n)

#define OPEN_MS 240
#define NAME_LIMIT 36
#define SHEET_WIDTH 360
#define LIST_VISIBLE_ROWS 5
#define LIST_ROW_HEIGHT 32

G_DEFINE_ENUM_TYPE (LumaSaveChoice, luma_save_choice,
  G_DEFINE_ENUM_VALUE (LUMA_SAVE_CHOICE_SAVE, "save"),
  G_DEFINE_ENUM_VALUE (LUMA_SAVE_CHOICE_DISCARD, "discard"),
  G_DEFINE_ENUM_VALUE (LUMA_SAVE_CHOICE_CANCEL, "cancel"),
  G_DEFINE_ENUM_VALUE (LUMA_SAVE_CHOICE_QUIT, "quit"))

/* ── Vitals ───────────────────────────────────────────────────────────── */

static void
vitals (GtkWindow *window, const char *kind, const char *save_case)
{
  GtkApplication *application = window ? gtk_window_get_application (window) : NULL;
  const char *unit = application ? g_application_get_application_id (G_APPLICATION (application)) : NULL;
  g_autofree char *message = NULL;

  if (unit == NULL)
    unit = "unknown";
  message = g_strdup_printf ("%s: %s (%s)", unit, kind, save_case);
  g_log_structured ("luma-appkit", G_LOG_LEVEL_INFO,
                    "MESSAGE", "%s", message,
                    "SYSLOG_IDENTIFIER", "luma-appkit",
                    "LUMA_VITALS_KIND", kind,
                    "LUMA_VITALS_UNIT", unit,
                    "LUMA_APPKIT_SAVE_CASE", save_case);
}

/* ── LumaSaveDocument ─────────────────────────────────────────────────── */

typedef struct {
  char *label;
  char *path;
} Place;

static void
place_free (gpointer data)
{
  Place *place = data;
  g_free (place->label);
  g_free (place->path);
  g_free (place);
}

struct _LumaSaveDocument {
  GObject parent_instance;
  char *name;
  gboolean never_saved;
  double unsaved_seconds;
  GWeakRef window;
  char *extension;
  GPtrArray *places;
  gboolean recovery_copy;
  char *kind;
};

G_DEFINE_FINAL_TYPE (LumaSaveDocument, luma_save_document, G_TYPE_OBJECT)

static void
luma_save_document_finalize (GObject *object)
{
  LumaSaveDocument *self = LUMA_SAVE_DOCUMENT (object);
  g_free (self->name);
  g_free (self->extension);
  g_free (self->kind);
  g_ptr_array_unref (self->places);
  g_weak_ref_clear (&self->window);
  G_OBJECT_CLASS (luma_save_document_parent_class)->finalize (object);
}

static void
luma_save_document_class_init (LumaSaveDocumentClass *klass)
{
  G_OBJECT_CLASS (klass)->finalize = luma_save_document_finalize;
}

static void
luma_save_document_init (LumaSaveDocument *self)
{
  self->unsaved_seconds = -1;
  self->places = g_ptr_array_new_with_free_func (place_free);
  self->kind = g_strdup ("document");
  self->extension = g_strdup ("");
  g_weak_ref_init (&self->window, NULL);
}

/**
 * luma_save_document_new:
 * @name: the document's name as the person knows it
 *
 * Returns: (transfer full): the facts about one unsaved document
 */
LumaSaveDocument *
luma_save_document_new (const char *name)
{
  LumaSaveDocument *self = g_object_new (LUMA_TYPE_SAVE_DOCUMENT, NULL);
  self->name = g_strdup (name ? name : "");
  return self;
}

const char *
luma_save_document_get_name (LumaSaveDocument *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_DOCUMENT (self), NULL);
  return self->name;
}

/**
 * luma_save_document_set_never_saved:
 * @self: a document
 * @never_saved: the document has never been written anywhere
 *
 * An untitled document is named and placed on the sheet itself.
 */
void
luma_save_document_set_never_saved (LumaSaveDocument *self, gboolean never_saved)
{
  g_return_if_fail (LUMA_IS_SAVE_DOCUMENT (self));
  self->never_saved = never_saved;
}

gboolean
luma_save_document_get_never_saved (LumaSaveDocument *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_DOCUMENT (self), FALSE);
  return self->never_saved;
}

/**
 * luma_save_document_set_unsaved_seconds:
 * @self: a document
 * @seconds: seconds since the first edit that is not saved yet, measured by
 *   the application; negative when the application does not record it
 *
 * The sheet states an edit age only when it is given one. It never estimates.
 */
void
luma_save_document_set_unsaved_seconds (LumaSaveDocument *self, double seconds)
{
  g_return_if_fail (LUMA_IS_SAVE_DOCUMENT (self));
  self->unsaved_seconds = seconds;
}

double
luma_save_document_get_unsaved_seconds (LumaSaveDocument *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_DOCUMENT (self), -1);
  return self->unsaved_seconds;
}

/**
 * luma_save_document_set_window:
 * @self: a document
 * @window: (nullable): the window that shows this document
 *
 * When quitting with several documents, each is reviewed in its own window.
 */
void
luma_save_document_set_window (LumaSaveDocument *self, GtkWindow *window)
{
  g_return_if_fail (LUMA_IS_SAVE_DOCUMENT (self));
  g_weak_ref_set (&self->window, window);
}

/**
 * luma_save_document_get_window:
 * @self: a document
 *
 * Returns: (transfer none) (nullable): the window, while it exists
 */
GtkWindow *
luma_save_document_get_window (LumaSaveDocument *self)
{
  GtkWindow *window;
  g_return_val_if_fail (LUMA_IS_SAVE_DOCUMENT (self), NULL);
  window = g_weak_ref_get (&self->window);
  if (window != NULL)
    g_object_unref (window);
  return window;
}

/**
 * luma_save_document_set_extension:
 * @self: a document
 * @extension: (nullable): the extension a new file gets, such as ".layouts"
 */
void
luma_save_document_set_extension (LumaSaveDocument *self, const char *extension)
{
  g_return_if_fail (LUMA_IS_SAVE_DOCUMENT (self));
  g_free (self->extension);
  self->extension = g_strdup (extension ? extension : "");
}

/**
 * luma_save_document_add_place:
 * @self: a document
 * @label: what the person calls the folder, such as "Projects"
 * @path: the folder
 *
 * The application's own folders come first in Where, then Documents and
 * Desktop, then Other location….
 */
void
luma_save_document_add_place (LumaSaveDocument *self, const char *label, const char *path)
{
  Place *place;
  g_return_if_fail (LUMA_IS_SAVE_DOCUMENT (self));
  g_return_if_fail (label != NULL && path != NULL);
  place = g_new0 (Place, 1);
  place->label = g_strdup (label);
  place->path = g_strdup (path);
  g_ptr_array_add (self->places, place);
}

/**
 * luma_save_document_set_recovery_copy:
 * @self: a document
 * @recovery_copy: the application keeps a recovery copy until the person
 *   saves or discards
 */
void
luma_save_document_set_recovery_copy (LumaSaveDocument *self, gboolean recovery_copy)
{
  g_return_if_fail (LUMA_IS_SAVE_DOCUMENT (self));
  self->recovery_copy = recovery_copy;
}

/**
 * luma_save_document_set_kind:
 * @self: a document
 * @kind: "document", "project" or "note"
 */
void
luma_save_document_set_kind (LumaSaveDocument *self, const char *kind)
{
  g_return_if_fail (LUMA_IS_SAVE_DOCUMENT (self));
  g_free (self->kind);
  self->kind = g_strdup (kind && *kind ? kind : "document");
}

/* ── Words ────────────────────────────────────────────────────────────── */

static char *
display_name (const char *name)
{
  g_auto (GStrv) words = g_strsplit_set (name ? name : "", " \t\n\r", -1);
  GString *joined = g_string_new (NULL);
  for (guint i = 0; words[i] != NULL; i++) {
    if (!*words[i])
      continue;
    if (joined->len)
      g_string_append_c (joined, ' ');
    g_string_append (joined, words[i]);
  }
  if (joined->len == 0)
    g_string_append (joined, S_("Untitled"));
  return g_string_free (joined, FALSE);
}

/* A document name in curly quotes, shortened in the middle when long. */
static char *
quoted (const char *name)
{
  g_autofree char *clean = display_name (name);
  glong length = g_utf8_strlen (clean, -1);

  if (length > NAME_LIMIT) {
    glong keep = (NAME_LIMIT - 1) / 2;
    g_autofree char *head = g_utf8_substring (clean, 0, keep);
    g_autofree char *tail = g_utf8_substring (clean, length - keep, length);
    return g_strdup_printf ("“%s…%s”", g_strchomp (head), g_strchug (tail));
  }
  return g_strdup_printf ("“%s”", clean);
}

static const char *
several_kind (LumaSaveDocument * const *documents, guint n)
{
  for (guint i = 1; i < n; i++)
    if (g_strcmp0 (documents[i]->kind, documents[0]->kind) != 0)
      return "document";
  return documents[0]->kind;
}

static char *
at_stake (LumaSaveDocument *document)
{
  double seconds = document->unsaved_seconds;
  long minutes, hours, days;

  if (seconds < 0)
    return g_strdup (S_("Your changes haven’t been saved."));
  if (seconds < 60)
    return g_strdup (S_("You have less than a minute of edits that are not saved yet."));
  minutes = lround (seconds / 60);
  if (minutes < 60)
    return g_strdup_printf (NS_("You have %ld minute of edits that are not saved yet.",
                                "You have %ld minutes of edits that are not saved yet.", minutes), minutes);
  hours = lround (minutes / 60.0);
  if (hours < 48)
    return g_strdup_printf (NS_("You have %ld hour of edits that are not saved yet.",
                                "You have %ld hours of edits that are not saved yet.", hours), hours);
  days = lround (hours / 24.0);
  return g_strdup_printf (NS_("You have %ld day of edits that are not saved yet.",
                              "You have %ld days of edits that are not saved yet.", days), days);
}

static char *
at_stake_short (LumaSaveDocument *document)
{
  double seconds = document->unsaved_seconds;
  long minutes, hours, days;

  if (document->never_saved)
    return g_strdup (S_("Never saved"));
  if (seconds < 0)
    return g_strdup (S_("Not saved"));
  if (seconds < 60)
    return g_strdup (S_("Under 1 min of edits"));
  minutes = lround (seconds / 60);
  if (minutes < 60)
    return g_strdup_printf (NS_("%ld min of edits", "%ld min of edits", minutes), minutes);
  hours = lround (minutes / 60.0);
  if (hours < 48)
    return g_strdup_printf (NS_("%ld hr of edits", "%ld hr of edits", hours), hours);
  days = lround (hours / 24.0);
  return g_strdup_printf (NS_("%ld day of edits", "%ld days of edits", days), days);
}

static char *
several_title (LumaSaveDocument * const *documents, guint n)
{
  const char *kind = several_kind (documents, n);
  if (g_str_equal (kind, "project"))
    return g_strdup_printf (NS_("Save changes to %u project before quitting?",
                                "Save changes to %u projects before quitting?", n), n);
  if (g_str_equal (kind, "note"))
    return g_strdup_printf (NS_("Save changes to %u note before quitting?",
                                "Save changes to %u notes before quitting?", n), n);
  return g_strdup_printf (NS_("Save changes to %u document before quitting?",
                              "Save changes to %u documents before quitting?", n), n);
}

static char *
several_discard_name (LumaSaveDocument * const *documents, guint n)
{
  const char *kind = several_kind (documents, n);
  if (g_str_equal (kind, "project"))
    return g_strdup_printf (NS_("Discard all %u project", "Discard all %u projects", n), n);
  if (g_str_equal (kind, "note"))
    return g_strdup_printf (NS_("Discard all %u note", "Discard all %u notes", n), n);
  return g_strdup_printf (NS_("Discard all %u document", "Discard all %u documents", n), n);
}

/* The file a name makes in a folder, or why it cannot be used. Never
 * overwrites: an existing file, or a link, is an error to show. */
static GFile *
check_name (const char *raw, const char *folder, const char *place,
            const char *extension, char **problem)
{
  g_autofree char *name = g_strstrip (g_strdup (raw ? raw : ""));
  g_autofree char *filename = NULL;
  g_autofree char *path = NULL;
  g_autofree char *shown = NULL;

  if (!*name) {
    *problem = g_strdup (S_("Give it a name."));
    return NULL;
  }
  if (strchr (name, '/') != NULL) {
    *problem = g_strdup (S_("A name can’t contain “/”."));
    return NULL;
  }
  if (name[0] == '.') {
    *problem = g_strdup (S_("A name can’t start with a dot."));
    return NULL;
  }
  {
    g_autofree char *folded_name = g_utf8_casefold (name, -1);
    g_autofree char *folded_ext = g_utf8_casefold (extension ? extension : "", -1);
    if (extension && *extension && !g_str_has_suffix (folded_name, folded_ext))
      filename = g_strconcat (name, extension, NULL);
    else
      filename = g_strdup (name);
  }
  if (strlen (filename) > 255) {
    *problem = g_strdup (S_("That name is too long."));
    return NULL;
  }
  path = g_build_filename (folder, filename, NULL);
  {
    g_autoptr (GFile) existing = g_file_new_for_path (path);
    /* A dangling link counts too: nothing is ever written over. */
    if (g_file_query_file_type (existing, G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS, NULL) == G_FILE_TYPE_UNKNOWN)
      return g_file_new_for_path (path);
  }
  {
    shown = quoted (filename);
    *problem = g_strdup_printf (S_("There is already a %s in %s."), shown, place);
    return NULL;
  }
}

/* ── The window's sheet layer ─────────────────────────────────────────── */

typedef struct {
  GtkWidget *widget;
  gboolean can_target;
} ParkedWidget;

typedef struct {
  GtkEventController *controller;
  GtkPropagationPhase phase;
} ParkedController;

typedef struct {
  GtkOverlay *layer;           /* (weak, owned by the window) */
  GtkWidget *content;          /* (weak) */
  GtkWidget *title_bar;        /* (weak, nullable) */
  gboolean automatic;          /* installed here: hang below the title bar */
  GtkWidget *sheet;            /* (owned) */
  GtkEventController *keys;    /* (owned) */
  GArray *parked_controllers;
  GArray *parked_widgets;
  GWeakRef focus_before;
} SheetHost;

static const char host_key[] = "luma-sheet-host";
static const char keep_key[] = "luma-sheet-keep";

static void
host_free (gpointer data)
{
  SheetHost *host = data;
  for (guint i = 0; i < host->parked_controllers->len; i++)
    g_object_unref (g_array_index (host->parked_controllers, ParkedController, i).controller);
  for (guint i = 0; i < host->parked_widgets->len; i++)
    g_object_unref (g_array_index (host->parked_widgets, ParkedWidget, i).widget);
  g_array_unref (host->parked_controllers);
  g_array_unref (host->parked_widgets);
  g_clear_object (&host->sheet);
  g_clear_object (&host->keys);
  g_weak_ref_clear (&host->focus_before);
  g_free (host);
}

static SheetHost *
host_new (void)
{
  SheetHost *host = g_new0 (SheetHost, 1);
  host->parked_controllers = g_array_new (FALSE, FALSE, sizeof (ParkedController));
  host->parked_widgets = g_array_new (FALSE, FALSE, sizeof (ParkedWidget));
  g_weak_ref_init (&host->focus_before, NULL);
  return host;
}

static void
keep_current_controllers (GtkWindow *window)
{
  GListModel *model = gtk_widget_observe_controllers (GTK_WIDGET (window));
  guint n = g_list_model_get_n_items (model);
  for (guint i = 0; i < n; i++) {
    g_autoptr (GtkEventController) controller = g_list_model_get_item (model, i);
    if (g_strcmp0 (gtk_event_controller_get_name (controller), "gtk-application-shortcuts") != 0)
      g_object_set_data (G_OBJECT (controller), keep_key, GINT_TO_POINTER (1));
  }
  g_object_unref (model);
}

/**
 * luma_window_set_sheet_layer:
 * @window: a window
 * @layer: the overlay above the window's content and below its title bar
 * @content: the content the sheet dims and makes wait
 * @title_bar: (nullable): the title bar, whose controls also wait
 *
 * For a window that builds its own sheet layer, such as the AppKit's
 * AppWindow. Key handlers already on the window are the toolkit's and the
 * kit's and keep working while a sheet is up; ones added later are the
 * application's and are parked. Any other window gets a layer the first time
 * it presents a sheet.
 */
void
luma_window_set_sheet_layer (GtkWindow *window, GtkOverlay *layer,
                             GtkWidget *content, GtkWidget *title_bar)
{
  SheetHost *host;
  g_return_if_fail (GTK_IS_WINDOW (window));
  g_return_if_fail (GTK_IS_OVERLAY (layer));
  g_return_if_fail (GTK_IS_WIDGET (content));
  host = host_new ();
  host->layer = layer;
  host->content = content;
  host->title_bar = title_bar;
  keep_current_controllers (window);
  g_object_set_data_full (G_OBJECT (window), host_key, host, host_free);
}

static void
collect_top_bars (GtkWidget *widget, GtkWidget *relative, float *bottom, GPtrArray *bars)
{
  if (!gtk_widget_get_mapped (widget))
    return;
  if (ADW_IS_HEADER_BAR (widget) || GTK_IS_HEADER_BAR (widget)) {
    graphene_rect_t bounds;
    if (gtk_widget_compute_bounds (widget, relative, &bounds) && bounds.origin.y <= 1.0f) {
      *bottom = MAX (*bottom, bounds.origin.y + bounds.size.height);
      if (bars)
        g_ptr_array_add (bars, widget);
    }
    return;
  }
  for (GtkWidget *child = gtk_widget_get_first_child (widget); child;
       child = gtk_widget_get_next_sibling (child))
    collect_top_bars (child, relative, bottom, bars);
}

/* An installed layer covers the whole window content, so the sheet and its
 * dimming start below the tallest header bar at the top of the window. */
static gboolean
position_sheet (GtkOverlay *overlay, GtkWidget *child, GdkRectangle *allocation, gpointer data)
{
  GtkWidget *content = gtk_overlay_get_child (overlay);
  float bottom = 0;
  int width = gtk_widget_get_width (GTK_WIDGET (overlay));
  int height = gtk_widget_get_height (GTK_WIDGET (overlay));
  (void) data;
  (void) child;
  if (content != NULL)
    collect_top_bars (content, GTK_WIDGET (overlay), &bottom, NULL);
  bottom = MIN (bottom, (float) height);
  allocation->x = 0;
  allocation->y = (int) ceilf (bottom);
  allocation->width = width;
  allocation->height = height - allocation->y;
  return TRUE;
}

static SheetHost *
host_for_window (GtkWindow *window)
{
  SheetHost *host = g_object_get_data (G_OBJECT (window), host_key);
  GtkWidget *content;
  GtkWidget *overlay;

  if (host != NULL)
    return host;
  if (ADW_IS_APPLICATION_WINDOW (window))
    content = adw_application_window_get_content (ADW_APPLICATION_WINDOW (window));
  else if (ADW_IS_WINDOW (window))
    content = adw_window_get_content (ADW_WINDOW (window));
  else
    content = gtk_window_get_child (window);
  if (content == NULL)
    return NULL;

  /* Done once per window and kept: the content moves into an overlay so the
   * sheet can hang over it. Focus is restored when the sheet closes. */
  overlay = gtk_overlay_new ();
  gtk_widget_add_css_class (overlay, "luma-sheet-layer");
  g_object_ref (content);
  if (ADW_IS_APPLICATION_WINDOW (window))
    adw_application_window_set_content (ADW_APPLICATION_WINDOW (window), NULL);
  else if (ADW_IS_WINDOW (window))
    adw_window_set_content (ADW_WINDOW (window), NULL);
  else
    gtk_window_set_child (window, NULL);
  gtk_overlay_set_child (GTK_OVERLAY (overlay), content);
  g_object_unref (content);
  if (ADW_IS_APPLICATION_WINDOW (window))
    adw_application_window_set_content (ADW_APPLICATION_WINDOW (window), overlay);
  else if (ADW_IS_WINDOW (window))
    adw_window_set_content (ADW_WINDOW (window), overlay);
  else
    gtk_window_set_child (window, overlay);
  g_signal_connect (overlay, "get-child-position", G_CALLBACK (position_sheet), NULL);

  host = host_new ();
  host->layer = GTK_OVERLAY (overlay);
  host->content = content;
  host->automatic = TRUE;
  g_object_set_data_full (G_OBJECT (window), host_key, host, host_free);
  return host;
}

static gboolean
inside_window_controls (GtkWidget *widget, GtkWidget *stop)
{
  for (GtkWidget *w = widget; w != NULL && w != stop; w = gtk_widget_get_parent (w))
    if (GTK_IS_WINDOW_CONTROLS (w))
      return TRUE;
  return FALSE;
}

/* The title bar keeps its window controls and its drag; the application's
 * own commands in it wait with the content. */
static void
park_title_commands (SheetHost *host, GtkWidget *bar, GtkWidget *widget)
{
  for (GtkWidget *child = gtk_widget_get_first_child (widget); child;
       child = gtk_widget_get_next_sibling (child)) {
    if (GTK_IS_WINDOW_CONTROLS (child))
      continue;
    if ((GTK_IS_BUTTON (child) || GTK_IS_MENU_BUTTON (child) || GTK_IS_EDITABLE (child)
         || GTK_IS_DROP_DOWN (child) || GTK_IS_SWITCH (child) || GTK_IS_SCALE (child))
        && !inside_window_controls (child, bar)) {
      ParkedWidget parked = { g_object_ref (child), gtk_widget_get_can_target (child) };
      g_array_append_val (host->parked_widgets, parked);
      gtk_widget_set_can_target (child, FALSE);
      gtk_widget_set_can_focus (child, FALSE);
      continue;
    }
    park_title_commands (host, bar, child);
  }
}

/**
 * luma_window_present_sheet:
 * @window: the window that asked
 * @sheet: the sheet to hang from its title bar
 * @keys: (nullable): the sheet's own key handler; it listens on the window,
 *   ahead of everything under it, while the sheet is up
 *
 * Only this window's content dims and stops taking focus and pointer; other
 * windows stay usable. The application's key handlers and accelerators are
 * parked until the sheet is dismissed.
 */
void
luma_window_present_sheet (GtkWindow *window, GtkWidget *sheet, GtkEventController *keys)
{
  SheetHost *host;
  GListModel *model;
  guint n;

  g_return_if_fail (GTK_IS_WINDOW (window));
  g_return_if_fail (GTK_IS_WIDGET (sheet));
  host = host_for_window (window);
  g_return_if_fail (host != NULL);
  if (host->sheet != NULL)
    luma_window_dismiss_sheet (window);

  g_weak_ref_set (&host->focus_before, gtk_window_get_focus (window));
  host->sheet = g_object_ref (sheet);
  gtk_overlay_add_overlay (host->layer, sheet);
  gtk_overlay_set_measure_overlay (host->layer, sheet, FALSE);
  gtk_overlay_set_clip_overlay (host->layer, sheet, FALSE);
  gtk_widget_set_can_focus (host->content, FALSE);
  gtk_widget_set_can_target (host->content, FALSE);
  gtk_accessible_update_state (GTK_ACCESSIBLE (host->content),
                               GTK_ACCESSIBLE_STATE_HIDDEN, TRUE, -1);

  if (host->title_bar != NULL) {
    gtk_widget_set_can_focus (host->title_bar, FALSE);
    park_title_commands (host, host->title_bar, host->title_bar);
  } else if (host->automatic) {
    g_autoptr (GPtrArray) bars = g_ptr_array_new ();
    float bottom = 0;
    collect_top_bars (host->content, GTK_WIDGET (host->layer), &bottom, bars);
    for (guint i = 0; i < bars->len; i++)
      park_title_commands (host, bars->pdata[i], bars->pdata[i]);
  }

  model = gtk_widget_observe_controllers (GTK_WIDGET (window));
  n = g_list_model_get_n_items (model);
  for (guint i = 0; i < n; i++) {
    g_autoptr (GtkEventController) controller = g_list_model_get_item (model, i);
    ParkedController parked;
    if (g_object_get_data (G_OBJECT (controller), keep_key) != NULL)
      continue;
    if (!GTK_IS_EVENT_CONTROLLER_KEY (controller) && !GTK_IS_SHORTCUT_CONTROLLER (controller))
      continue;
    parked.controller = g_object_ref (controller);
    parked.phase = gtk_event_controller_get_propagation_phase (controller);
    g_array_append_val (host->parked_controllers, parked);
    gtk_event_controller_set_propagation_phase (controller, GTK_PHASE_NONE);
  }
  g_object_unref (model);

  if (keys != NULL) {
    host->keys = g_object_ref (keys);
    gtk_event_controller_set_propagation_phase (keys, GTK_PHASE_CAPTURE);
    gtk_widget_add_controller (GTK_WIDGET (window), g_object_ref (keys));
  }
}

/**
 * luma_window_dismiss_sheet:
 * @window: a window with a sheet up
 *
 * Takes the sheet down, gives the window back, and returns focus to where it
 * was when the sheet opened.
 */
void
luma_window_dismiss_sheet (GtkWindow *window)
{
  SheetHost *host;
  g_autoptr (GtkWidget) focus = NULL;

  g_return_if_fail (GTK_IS_WINDOW (window));
  host = g_object_get_data (G_OBJECT (window), host_key);
  if (host == NULL || host->sheet == NULL)
    return;
  if (host->keys != NULL) {
    gtk_widget_remove_controller (GTK_WIDGET (window), host->keys);
    g_clear_object (&host->keys);
  }
  if (gtk_widget_get_parent (host->sheet) == GTK_WIDGET (host->layer))
    gtk_overlay_remove_overlay (host->layer, host->sheet);
  g_clear_object (&host->sheet);

  for (guint i = 0; i < host->parked_controllers->len; i++) {
    ParkedController *parked = &g_array_index (host->parked_controllers, ParkedController, i);
    gtk_event_controller_set_propagation_phase (parked->controller, parked->phase);
    g_object_unref (parked->controller);
  }
  g_array_set_size (host->parked_controllers, 0);
  for (guint i = 0; i < host->parked_widgets->len; i++) {
    ParkedWidget *parked = &g_array_index (host->parked_widgets, ParkedWidget, i);
    gtk_widget_set_can_target (parked->widget, parked->can_target);
    gtk_widget_set_can_focus (parked->widget, TRUE);
    g_object_unref (parked->widget);
  }
  g_array_set_size (host->parked_widgets, 0);

  gtk_widget_set_can_focus (host->content, TRUE);
  gtk_widget_set_can_target (host->content, TRUE);
  gtk_accessible_update_state (GTK_ACCESSIBLE (host->content),
                               GTK_ACCESSIBLE_STATE_HIDDEN, FALSE, -1);
  if (host->title_bar != NULL)
    gtk_widget_set_can_focus (host->title_bar, TRUE);

  focus = g_weak_ref_get (&host->focus_before);
  g_weak_ref_set (&host->focus_before, NULL);
  if (focus != NULL && gtk_widget_get_root (focus) == GTK_ROOT (window)
      && gtk_widget_get_mapped (focus))
    gtk_widget_grab_focus (focus);
}

/**
 * luma_window_get_presented_sheet:
 * @window: a window
 *
 * Returns: (transfer none) (nullable): the sheet hanging from its title bar
 */
GtkWidget *
luma_window_get_presented_sheet (GtkWindow *window)
{
  SheetHost *host;
  g_return_val_if_fail (GTK_IS_WINDOW (window), NULL);
  host = g_object_get_data (G_OBJECT (window), host_key);
  return host ? host->sheet : NULL;
}

/* ── Style ────────────────────────────────────────────────────────────── */

static gboolean tokens_from_application = FALSE;

/**
 * luma_ui_tokens_provided_by_application:
 *
 * The process defines the AppKit colour tokens itself and keeps its sheets
 * in step with the treatment (the Python AppKit does). The save sheet then
 * loads only its own style sheet and leaves the rest of the process alone.
 */
void
luma_ui_tokens_provided_by_application (void)
{
  tokens_from_application = TRUE;
}

static void
install_style (void)
{
  static gboolean installed = FALSE;
  if (installed || gdk_display_get_default () == NULL)
    return;
  installed = TRUE;
  if (tokens_from_application)
    return; /* The application loads luma-save-sheet.css through its own kit. */
  luma_init ();
  luma_ui_add_style_resource ("/org/projectluma/platform/luma-save-sheet.css",
                              GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 1);
}

/* ── LumaSaveSheet ────────────────────────────────────────────────────── */

struct _LumaSaveSheet {
  GtkWidget parent_instance;

  GPtrArray *documents;
  LumaSaveDocument *document;
  gboolean several;
  gboolean untitled;
  gboolean quitting;

  GtkWidget *clamp;
  GtkWidget *card;
  GtkWidget *title;
  GtkWidget *body;
  GtkWidget *name_row;
  GtkWidget *name_entry;
  GtkWidget *where;
  GtkStringList *place_labels;
  GPtrArray *places;          /* Place, in dropdown order */
  guint previous_place;
  GtkWidget *error;
  GtkWidget *primary;
  GtkWidget *cancel;
  GtkWidget *discard;
  GtkEventController *keys;

  GtkWindow *window;          /* (weak) */
  gboolean presented;
  gboolean open;
  gboolean closing;
  gboolean saving;
  guint close_source;
};

enum { SHEET_RESPONSE, SHEET_CLOSED, N_SHEET_SIGNALS };
static guint sheet_signals[N_SHEET_SIGNALS];

G_DEFINE_FINAL_TYPE (LumaSaveSheet, luma_save_sheet, GTK_TYPE_WIDGET)

static void
luma_save_sheet_dispose (GObject *object)
{
  LumaSaveSheet *self = LUMA_SAVE_SHEET (object);
  g_clear_handle_id (&self->close_source, g_source_remove);
  if (self->window != NULL) {
    g_object_remove_weak_pointer (G_OBJECT (self->window), (gpointer *) &self->window);
    self->window = NULL;
  }
  g_clear_pointer (&self->clamp, gtk_widget_unparent);
  g_clear_object (&self->keys);
  G_OBJECT_CLASS (luma_save_sheet_parent_class)->dispose (object);
}

static void
luma_save_sheet_finalize (GObject *object)
{
  LumaSaveSheet *self = LUMA_SAVE_SHEET (object);
  g_ptr_array_unref (self->documents);
  g_ptr_array_unref (self->places);
  g_clear_object (&self->place_labels);
  G_OBJECT_CLASS (luma_save_sheet_parent_class)->finalize (object);
}

static void
luma_save_sheet_class_init (LumaSaveSheetClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS (klass);

  object_class->dispose = luma_save_sheet_dispose;
  object_class->finalize = luma_save_sheet_finalize;

  /**
   * LumaSaveSheet::response:
   * @sheet: the sheet
   * @choice: what the person chose
   * @document: (nullable): the document it is about; %NULL for several
   * @destination: (nullable): for an untitled document, the file to write
   *
   * A SAVE keeps the sheet up, busy, until luma_save_sheet_finish_save().
   */
  sheet_signals[SHEET_RESPONSE] =
    g_signal_new ("response", G_TYPE_FROM_CLASS (klass), G_SIGNAL_RUN_LAST,
                  0, NULL, NULL, NULL, G_TYPE_NONE, 3,
                  LUMA_TYPE_SAVE_CHOICE, LUMA_TYPE_SAVE_DOCUMENT, G_TYPE_FILE);
  /**
   * LumaSaveSheet::closed:
   * @sheet: the sheet
   *
   * The sheet is down and its window is given back.
   */
  sheet_signals[SHEET_CLOSED] =
    g_signal_new ("closed", G_TYPE_FROM_CLASS (klass), G_SIGNAL_RUN_LAST,
                  0, NULL, NULL, NULL, G_TYPE_NONE, 0);

  gtk_widget_class_set_layout_manager_type (widget_class, GTK_TYPE_BIN_LAYOUT);
  gtk_widget_class_set_css_name (widget_class, "luma-save-scrim");
  gtk_widget_class_set_accessible_role (widget_class, GTK_ACCESSIBLE_ROLE_GENERIC);
}

static void
luma_save_sheet_init (LumaSaveSheet *self)
{
  self->documents = g_ptr_array_new_with_free_func (g_object_unref);
  self->places = g_ptr_array_new_with_free_func (place_free);
  gtk_widget_add_css_class (GTK_WIDGET (self), "luma-save-scrim");
  gtk_widget_set_hexpand (GTK_WIDGET (self), TRUE);
  gtk_widget_set_vexpand (GTK_WIDGET (self), TRUE);
}

static const char *
sheet_case (LumaSaveSheet *self)
{
  return self->several ? "several" : self->untitled ? "untitled" : "named";
}

static GtkWidget *
piece (GtkWidget *mark, const char *css, int width, int height, int x, int y)
{
  GtkWidget *part = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class (part, css);
  gtk_widget_set_size_request (part, width, height);
  gtk_widget_set_can_target (part, FALSE);
  gtk_fixed_put (GTK_FIXED (mark), part, x, y);
  return part;
}

/* A page with a folded corner and three lines, wearing the edited dot. */
static GtkWidget *
document_mark (gboolean several)
{
  GtkWidget *mark = g_object_new (GTK_TYPE_FIXED,
                                  "halign", GTK_ALIGN_CENTER,
                                  "can-target", FALSE,
                                  "can-focus", FALSE,
                                  "accessible-role", GTK_ACCESSIBLE_ROLE_PRESENTATION,
                                  NULL);
  gtk_widget_add_css_class (mark, "luma-save-mark");
  gtk_widget_set_size_request (mark, 54, 56);
  if (several)
    piece (mark, "luma-save-page-behind", 40, 48, 13, 0);
  piece (mark, "luma-save-page", 40, 48, 7, 6);
  piece (mark, "luma-save-fold", 14, 14, 33, 6);
  for (int line = 0; line < 3; line++)
    piece (mark, "luma-save-rule", 22, 2, 16, 27 + 6 * line);
  piece (mark, "luma-save-dot", 14, 14, 38, 41);
  return mark;
}

/* CSS has no text-wrap: balance. A wrapped label is as wide as its maximum
 * width in characters, so asking for the length of an even share of the lines
 * gives balanced lines instead of a long first line and a lonely last word. */
static void
balance (GtkWidget *label, const char *text, int limit)
{
  glong length = g_utf8_strlen (text, -1);
  glong lines = MAX (1, (length + limit - 1) / limit);
  gtk_label_set_max_width_chars (GTK_LABEL (label), (int) MIN (limit, (length + lines - 1) / lines + 2));
}

/* A wrapping label given the whole card wraps at the card's width, and
 * centring it does not help: alignment measures a wrapping label's width for
 * its one-line height. A clamp at the label's own natural width (its
 * character limit, in the font it is drawn in) makes the limit hold, and
 * still shrinks with a narrow window. */
static void
fit_clamp_to_label (GtkWidget *label, gpointer data)
{
  AdwClamp *clamp = data;
  int natural = 0;
  gtk_widget_measure (label, GTK_ORIENTATION_HORIZONTAL, -1, NULL, &natural, NULL, NULL);
  if (natural > 0 && natural != adw_clamp_get_maximum_size (clamp)) {
    adw_clamp_set_maximum_size (clamp, natural);
    adw_clamp_set_tightening_threshold (clamp, natural);
  }
}

static GtkWidget *
wrapped_label (const char *text, const char *css, int limit, GtkWidget **label_out)
{
  GtkWidget *label = gtk_label_new (text);
  GtkWidget *clamp = adw_clamp_new ();
  gtk_label_set_wrap (GTK_LABEL (label), TRUE);
  gtk_label_set_wrap_mode (GTK_LABEL (label), PANGO_WRAP_WORD_CHAR);
  gtk_label_set_justify (GTK_LABEL (label), GTK_JUSTIFY_CENTER);
  gtk_widget_add_css_class (label, css);
  balance (label, text, limit);
  adw_clamp_set_child (ADW_CLAMP (clamp), label);
  adw_clamp_set_maximum_size (ADW_CLAMP (clamp), SHEET_WIDTH);
  g_signal_connect (label, "map", G_CALLBACK (fit_clamp_to_label), clamp);
  *label_out = label;
  return clamp;
}

static void
words (LumaSaveSheet *self, char **title, char **body)
{
  g_autofree char *name = NULL;
  g_autofree char *fact = NULL;

  if (self->several) {
    *title = several_title ((LumaSaveDocument * const *) self->documents->pdata, self->documents->len);
    *body = g_strdup (S_("Go through them one at a time, or leave without saving any of them."));
    return;
  }
  name = quoted (self->document->name);
  if (self->untitled) {
    *title = g_strdup_printf (self->quitting ? S_("Save %s before quitting?") : S_("Save %s before closing?"), name);
    fact = g_strdup (S_("It has never been saved. Give it a name and a place."));
  } else {
    *title = g_strdup_printf (S_("Save changes to %s?"), name);
    fact = at_stake (self->document);
  }
  if (self->document->recovery_copy)
    *body = g_strdup_printf ("%s %s", fact, S_("A recovery copy is kept until you save or discard."));
  else
    *body = g_steal_pointer (&fact);
}

static void clear_error (LumaSaveSheet *self);
static void place_selected (GObject *dropdown, GParamSpec *pspec, gpointer data);

static GtkWidget *
field (const char *label_text, GtkWidget *control)
{
  GtkWidget *row = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 10);
  GtkWidget *label = gtk_label_new (label_text);
  gtk_widget_add_css_class (row, "luma-save-field");
  gtk_label_set_xalign (GTK_LABEL (label), 0);
  gtk_widget_set_size_request (label, 52, -1);
  gtk_widget_set_hexpand (control, TRUE);
  gtk_box_append (GTK_BOX (row), label);
  gtk_box_append (GTK_BOX (row), control);
  gtk_accessible_update_relation (GTK_ACCESSIBLE (control),
                                  GTK_ACCESSIBLE_RELATION_LABELLED_BY, label, NULL, -1);
  return row;
}

static gboolean
same_folder (const char *a, const char *b)
{
  g_autofree char *ca = g_canonicalize_filename (a, NULL);
  g_autofree char *cb = g_canonicalize_filename (b, NULL);
  return g_str_equal (ca, cb);
}

static void
add_place (LumaSaveSheet *self, const char *label, const char *path)
{
  Place *place;
  if (path == NULL || *path == '\0')
    return;
  for (guint i = 0; i < self->places->len; i++)
    if (same_folder (((Place *) self->places->pdata[i])->path, path))
      return;
  place = g_new0 (Place, 1);
  place->label = g_strdup (label);
  place->path = g_strdup (path);
  g_ptr_array_add (self->places, place);
}

static void
build_fields (LumaSaveSheet *self)
{
  GtkWidget *fields = gtk_box_new (GTK_ORIENTATION_VERTICAL, 8);
  g_autofree char *untitled = display_name (self->document->name);
  const char *home = g_get_home_dir ();
  const char *documents = g_get_user_special_dir (G_USER_DIRECTORY_DOCUMENTS);
  const char *desktop = g_get_user_special_dir (G_USER_DIRECTORY_DESKTOP);
  g_autoptr (GPtrArray) labels = g_ptr_array_new ();

  gtk_widget_set_margin_top (fields, 16);
  gtk_widget_add_css_class (fields, "luma-save-fields");

  self->name_entry = gtk_entry_new ();
  gtk_editable_set_text (GTK_EDITABLE (self->name_entry), untitled);
  gtk_entry_set_activates_default (GTK_ENTRY (self->name_entry), FALSE);
  g_signal_connect_swapped (self->name_entry, "changed", G_CALLBACK (clear_error), self);
  self->name_row = field (S_("Name"), self->name_entry);
  gtk_box_append (GTK_BOX (fields), self->name_row);

  /* The application's own folders first, then Documents and Desktop. */
  for (guint i = 0; i < self->document->places->len; i++) {
    Place *place = self->document->places->pdata[i];
    add_place (self, place->label, place->path);
  }
  if (documents && !same_folder (documents, home))
    add_place (self, S_("Documents"), documents);
  if (desktop && !same_folder (desktop, home))
    add_place (self, S_("Desktop"), desktop);
  if (self->places->len == 0)
    add_place (self, S_("Home"), home);
  for (guint i = 0; i < self->places->len; i++)
    g_ptr_array_add (labels, ((Place *) self->places->pdata[i])->label);
  g_ptr_array_add (labels, (gpointer) S_("Other location…"));
  g_ptr_array_add (labels, NULL);
  self->place_labels = gtk_string_list_new ((const char * const *) labels->pdata);
  self->where = gtk_drop_down_new (G_LIST_MODEL (g_object_ref (self->place_labels)), NULL);
  g_signal_connect (self->where, "notify::selected", G_CALLBACK (place_selected), self);
  gtk_box_append (GTK_BOX (fields), field (S_("Where"), self->where));
  gtk_box_append (GTK_BOX (self->card), fields);
}

static void
build_list (LumaSaveSheet *self)
{
  GtkWidget *scroller = gtk_scrolled_window_new ();
  GtkWidget *listing = g_object_new (GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL,
                                     "accessible-role", GTK_ACCESSIBLE_ROLE_LIST, NULL);

  gtk_widget_add_css_class (scroller, "luma-save-list");
  gtk_widget_set_margin_top (scroller, 14);
  gtk_scrolled_window_set_policy (GTK_SCROLLED_WINDOW (scroller), GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
  gtk_scrolled_window_set_propagate_natural_height (GTK_SCROLLED_WINDOW (scroller), TRUE);
  /* More than five documents scroll. */
  gtk_scrolled_window_set_max_content_height (GTK_SCROLLED_WINDOW (scroller),
                                              LIST_VISIBLE_ROWS * LIST_ROW_HEIGHT + 8);
  gtk_accessible_update_property (GTK_ACCESSIBLE (listing),
                                  GTK_ACCESSIBLE_PROPERTY_LABEL, S_("Unsaved documents"), -1);
  for (guint i = 0; i < self->documents->len; i++) {
    LumaSaveDocument *document = self->documents->pdata[i];
    g_autofree char *shown = display_name (document->name);
    g_autofree char *state = at_stake_short (document);
    g_autofree char *spoken = g_strdup_printf ("%s, %s", shown, state);
    GtkWidget *row = g_object_new (GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_HORIZONTAL,
                                   "spacing", 10, "accessible-role", GTK_ACCESSIBLE_ROLE_LIST_ITEM, NULL);
    GtkWidget *name = gtk_label_new (shown);
    GtkWidget *stake = gtk_label_new (state);
    gtk_widget_add_css_class (row, "luma-save-row");
    gtk_label_set_xalign (GTK_LABEL (name), 0);
    gtk_label_set_ellipsize (GTK_LABEL (name), PANGO_ELLIPSIZE_MIDDLE);
    gtk_widget_set_hexpand (name, TRUE);
    gtk_label_set_xalign (GTK_LABEL (stake), 1);
    gtk_widget_add_css_class (stake, "luma-save-stake");
    gtk_box_append (GTK_BOX (row), name);
    gtk_box_append (GTK_BOX (row), stake);
    gtk_accessible_update_property (GTK_ACCESSIBLE (row), GTK_ACCESSIBLE_PROPERTY_LABEL, spoken, -1);
    gtk_box_append (GTK_BOX (listing), row);
  }
  gtk_scrolled_window_set_child (GTK_SCROLLED_WINDOW (scroller), listing);
  gtk_box_append (GTK_BOX (self->card), scroller);
}

static gboolean key_pressed (GtkEventControllerKey *controller, guint keyval, guint keycode,
                             GdkModifierType state, gpointer data);

static void
primary_clicked (LumaSaveSheet *self)
{
  luma_save_sheet_activate_primary (self);
}

/**
 * luma_save_sheet_new:
 * @documents: (array length=n_documents): the unsaved documents
 * @n_documents: how many; more than one asks about several when quitting
 * @quitting: the application is quitting rather than closing one window
 *
 * Most applications use #LumaSaveRequest, which also reviews several
 * documents one at a time.
 *
 * Returns: (transfer full): the sheet, not yet presented
 */
LumaSaveSheet *
luma_save_sheet_new (LumaSaveDocument * const *documents, guint n_documents, gboolean quitting)
{
  LumaSaveSheet *self;
  g_autofree char *title = NULL;
  g_autofree char *body = NULL;
  g_autofree char *discard_name = NULL;
  GtkWidget *actions, *content, *phrase, *shortcut;

  g_return_val_if_fail (documents != NULL && n_documents > 0, NULL);
  for (guint i = 0; i < n_documents; i++)
    g_return_val_if_fail (LUMA_IS_SAVE_DOCUMENT (documents[i]), NULL);

  install_style ();
  self = g_object_new (LUMA_TYPE_SAVE_SHEET, NULL);
  g_object_ref_sink (self);
  for (guint i = 0; i < n_documents; i++)
    g_ptr_array_add (self->documents, g_object_ref (documents[i]));
  self->document = documents[0];
  self->several = n_documents > 1;
  self->untitled = !self->several && self->document->never_saved;
  self->quitting = quitting || self->several;

  /* 360px wide, or the window width minus 16px each side when narrower. */
  self->clamp = g_object_new (ADW_TYPE_CLAMP, "maximum-size", SHEET_WIDTH,
                              "tightening-threshold", SHEET_WIDTH,
                              "valign", GTK_ALIGN_START,
                              "margin-start", 16, "margin-end", 16, NULL);
  gtk_widget_set_parent (self->clamp, GTK_WIDGET (self));
  self->card = g_object_new (GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL,
                             "accessible-role", GTK_ACCESSIBLE_ROLE_ALERT_DIALOG, NULL);
  gtk_widget_add_css_class (self->card, "luma-save-sheet");
  gtk_widget_add_css_class (self->card, sheet_case (self));
  adw_clamp_set_child (ADW_CLAMP (self->clamp), self->card);

  gtk_box_append (GTK_BOX (self->card), document_mark (self->several));
  words (self, &title, &body);
  gtk_box_append (GTK_BOX (self->card), wrapped_label (title, "luma-save-title", 42, &self->title));
  gtk_box_append (GTK_BOX (self->card), wrapped_label (body, "luma-save-body", 34, &self->body));

  if (self->untitled)
    build_fields (self);
  if (self->several)
    build_list (self);

  self->error = gtk_label_new (NULL);
  gtk_widget_set_visible (self->error, FALSE);
  gtk_label_set_wrap (GTK_LABEL (self->error), TRUE);
  gtk_label_set_xalign (GTK_LABEL (self->error), self->untitled ? 0 : 0.5);
  gtk_label_set_justify (GTK_LABEL (self->error), self->untitled ? GTK_JUSTIFY_LEFT : GTK_JUSTIFY_CENTER);
  gtk_widget_add_css_class (self->error, "luma-save-error");
  gtk_box_append (GTK_BOX (self->card), self->error);

  actions = gtk_box_new (GTK_ORIENTATION_VERTICAL, 8);
  gtk_widget_set_margin_top (actions, 18);
  self->primary = gtk_button_new_with_label (self->several ? S_("Review changes…") : S_("Save"));
  gtk_widget_add_css_class (self->primary, "luma-save-primary");
  g_signal_connect_swapped (self->primary, "clicked", G_CALLBACK (primary_clicked), self);
  self->cancel = gtk_button_new_with_label (S_("Cancel"));
  gtk_widget_add_css_class (self->cancel, "luma-save-secondary");
  g_signal_connect_swapped (self->cancel, "clicked", G_CALLBACK (luma_save_sheet_activate_cancel), self);
  gtk_box_append (GTK_BOX (actions), self->primary);
  gtk_box_append (GTK_BOX (actions), self->cancel);
  gtk_box_append (GTK_BOX (self->card), actions);

  /* The destructive choice, apart and quiet: the hardest one to hit by
   * accident. The hint names its one key. */
  phrase = gtk_label_new (self->several ? S_("Discard all") : S_("Don’t save"));
  shortcut = gtk_label_new (S_("Ctrl D"));
  gtk_widget_add_css_class (shortcut, "luma-save-shortcut");
  content = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 6);
  gtk_box_append (GTK_BOX (content), phrase);
  gtk_box_append (GTK_BOX (content), shortcut);
  self->discard = g_object_new (GTK_TYPE_BUTTON, "child", content, "halign", GTK_ALIGN_CENTER, NULL);
  gtk_widget_add_css_class (self->discard, "luma-save-discard");
  g_signal_connect_swapped (self->discard, "clicked", G_CALLBACK (luma_save_sheet_activate_discard), self);
  if (self->several) {
    discard_name = several_discard_name ((LumaSaveDocument * const *) self->documents->pdata, n_documents);
  } else {
    g_autofree char *shown = display_name (self->document->name);
    discard_name = g_strdup_printf (S_("Don’t save %s"), shown);
  }
  gtk_accessible_update_property (GTK_ACCESSIBLE (self->discard),
                                  GTK_ACCESSIBLE_PROPERTY_LABEL, discard_name,
                                  GTK_ACCESSIBLE_PROPERTY_KEY_SHORTCUTS, "Control+D", -1);
  gtk_box_append (GTK_BOX (self->card), self->discard);

  /* The title names the sheet through labelled-by. The body is the
   * description as a property: a described-by relation takes precedence over
   * it, and GTK resolves that relation to an empty AT-SPI description, so a
   * screen reader would read the title alone. */
  gtk_accessible_update_property (GTK_ACCESSIBLE (self->card),
                                  GTK_ACCESSIBLE_PROPERTY_MODAL, TRUE,
                                  GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, body, -1);
  gtk_accessible_update_relation (GTK_ACCESSIBLE (self->card),
                                  GTK_ACCESSIBLE_RELATION_LABELLED_BY, self->title, NULL, -1);

  self->keys = gtk_event_controller_key_new ();
  gtk_event_controller_set_name (self->keys, "luma-save-sheet-keys");
  g_signal_connect (self->keys, "key-pressed", G_CALLBACK (key_pressed), self);
  return self;
}

/* ── Places ───────────────────────────────────────────────────────────── */

static void
folder_chosen (GObject *source, GAsyncResult *result, gpointer data)
{
  LumaSaveSheet *self = data;
  g_autoptr (GFile) folder = gtk_file_dialog_select_folder_finish (GTK_FILE_DIALOG (source), result, NULL);
  g_autofree char *path = folder ? g_file_get_path (folder) : NULL;

  if (path == NULL || self->closing) {
    gtk_drop_down_set_selected (GTK_DROP_DOWN (self->where), self->previous_place);
  } else {
    g_autofree char *label = g_filename_display_basename (path);
    guint index = self->places->len;
    for (guint i = 0; i < self->places->len; i++)
      if (same_folder (((Place *) self->places->pdata[i])->path, path)) {
        index = i;
        break;
      }
    if (index == self->places->len) {
      const char *added[] = { label, NULL };
      add_place (self, label, path);
      gtk_string_list_splice (self->place_labels, index, 0, added);
    }
    gtk_drop_down_set_selected (GTK_DROP_DOWN (self->where), index);
  }
  gtk_widget_grab_focus (self->where);
  g_object_unref (self);
}

static void
place_selected (GObject *dropdown, GParamSpec *pspec, gpointer data)
{
  LumaSaveSheet *self = data;
  guint selected = gtk_drop_down_get_selected (GTK_DROP_DOWN (dropdown));
  g_autoptr (GtkFileDialog) chooser = NULL;
  g_autoptr (GFile) initial = NULL;
  (void) pspec;

  clear_error (self);
  if (selected < self->places->len) {
    self->previous_place = selected;
    return;
  }
  if (selected == GTK_INVALID_LIST_POSITION)
    return;
  /* Other location…: the system folder chooser, then back here. */
  chooser = gtk_file_dialog_new ();
  gtk_file_dialog_set_title (chooser, S_("Choose a Place"));
  gtk_file_dialog_set_modal (chooser, TRUE);
  initial = g_file_new_for_path (((Place *) self->places->pdata[self->previous_place])->path);
  gtk_file_dialog_set_initial_folder (chooser, initial);
  gtk_file_dialog_select_folder (chooser, GTK_WINDOW (gtk_widget_get_root (GTK_WIDGET (self))),
                                 NULL, folder_chosen, g_object_ref (self));
}

/* ── Presenting ───────────────────────────────────────────────────────── */

static void
focus_first (LumaSaveSheet *self)
{
  if (self->untitled && self->name_entry != NULL) {
    gtk_widget_grab_focus (self->name_entry);
    gtk_editable_select_region (GTK_EDITABLE (self->name_entry), 0, -1);
  } else {
    gtk_widget_grab_focus (self->primary);
  }
}

static gboolean
open_on_first_frame (GtkWidget *widget, GdkFrameClock *clock, gpointer data)
{
  LumaSaveSheet *self = LUMA_SAVE_SHEET (widget);
  (void) clock;
  (void) data;
  if (!self->closing) {
    gtk_widget_add_css_class (widget, "open");
    self->open = TRUE;
    focus_first (self);
  }
  return G_SOURCE_REMOVE;
}

/**
 * luma_save_sheet_present:
 * @self: a sheet
 * @window: the window that asked
 *
 * Hangs the sheet from the window's title bar and brings the window forward.
 */
void
luma_save_sheet_present (LumaSaveSheet *self, GtkWindow *window)
{
  g_return_if_fail (LUMA_IS_SAVE_SHEET (self));
  g_return_if_fail (GTK_IS_WINDOW (window));
  g_return_if_fail (!self->presented);

  self->presented = TRUE;
  self->window = window;
  g_object_add_weak_pointer (G_OBJECT (window), (gpointer *) &self->window);
  luma_window_present_sheet (window, GTK_WIDGET (self), self->keys);
  gtk_window_present (window);
  if (adw_get_enable_animations (GTK_WIDGET (self))) {
    gtk_widget_add_tick_callback (GTK_WIDGET (self), open_on_first_frame, NULL, NULL);
  } else {
    gtk_widget_add_css_class (GTK_WIDGET (self), "open");
    self->open = TRUE;
    focus_first (self);
  }
  vitals (window, "save-sheet-shown", sheet_case (self));
}

typedef struct {
  LumaSaveSheet *sheet;
  LumaSaveChoice choice;
  LumaSaveDocument *document;
  GFile *destination;
  gboolean respond;
} Closing;

static void
finish_close (Closing *closing)
{
  LumaSaveSheet *self = closing->sheet;

  self->close_source = 0;
  if (self->window != NULL
      && luma_window_get_presented_sheet (self->window) == GTK_WIDGET (self))
    luma_window_dismiss_sheet (self->window);
  g_signal_emit (self, sheet_signals[SHEET_CLOSED], 0);
  if (closing->respond)
    g_signal_emit (self, sheet_signals[SHEET_RESPONSE], 0,
                   closing->choice, closing->document, closing->destination);
  g_clear_object (&closing->document);
  g_clear_object (&closing->destination);
  g_object_unref (self);
  g_free (closing);
}

static gboolean
finish_close_later (gpointer data)
{
  finish_close (data);
  return G_SOURCE_REMOVE;
}

static void
close_sheet (LumaSaveSheet *self, gboolean respond, LumaSaveChoice choice,
             LumaSaveDocument *document, GFile *destination)
{
  Closing *closing;
  guint delay;

  if (self->closing)
    return;
  self->closing = TRUE;
  closing = g_new0 (Closing, 1);
  closing->sheet = g_object_ref (self);
  closing->respond = respond;
  closing->choice = choice;
  closing->document = document ? g_object_ref (document) : NULL;
  closing->destination = destination ? g_object_ref (destination) : NULL;
  gtk_widget_remove_css_class (GTK_WIDGET (self), "open");
  self->open = FALSE;
  delay = self->presented && adw_get_enable_animations (GTK_WIDGET (self)) ? OPEN_MS : 0;
  if (delay)
    self->close_source = g_timeout_add (delay, finish_close_later, closing);
  else
    finish_close (closing);
}

/* ── Choices ──────────────────────────────────────────────────────────── */

static void
set_busy (LumaSaveSheet *self, gboolean busy)
{
  self->saving = busy;
  gtk_widget_set_sensitive (self->primary, !busy);
  if (self->name_entry)
    gtk_widget_set_sensitive (self->name_entry, !busy);
  if (self->where)
    gtk_widget_set_sensitive (self->where, !busy);
}

/**
 * luma_save_sheet_activate_primary:
 * @self: a sheet
 *
 * Save, or Review changes… for several documents: as Enter or a click.
 */
void
luma_save_sheet_activate_primary (LumaSaveSheet *self)
{
  g_autoptr (GFile) destination = NULL;

  g_return_if_fail (LUMA_IS_SAVE_SHEET (self));
  if (self->closing || self->saving || !gtk_widget_get_sensitive (self->primary))
    return;
  if (self->several) {
    vitals (self->window, "save-sheet-review", sheet_case (self));
    close_sheet (self, TRUE, LUMA_SAVE_CHOICE_SAVE, NULL, NULL);
    return;
  }
  if (self->untitled) {
    guint index = MIN (gtk_drop_down_get_selected (GTK_DROP_DOWN (self->where)), self->places->len - 1);
    Place *place = self->places->pdata[index];
    g_autofree char *problem = NULL;
    destination = check_name (gtk_editable_get_text (GTK_EDITABLE (self->name_entry)),
                              place->path, place->label, self->document->extension, &problem);
    if (destination == NULL) {
      luma_save_sheet_show_error (self, problem);
      gtk_widget_grab_focus (self->name_entry);
      return;
    }
    if (g_mkdir_with_parents (place->path, 0755) != 0) {
      g_autofree char *message = g_strdup_printf (S_("%s can’t be written to. Choose another place."), place->label);
      luma_save_sheet_show_error (self, message);
      return;
    }
  }
  vitals (self->window, "save-sheet-save", sheet_case (self));
  set_busy (self, TRUE);
  g_signal_emit (self, sheet_signals[SHEET_RESPONSE], 0,
                 LUMA_SAVE_CHOICE_SAVE, self->document, destination);
}

/**
 * luma_save_sheet_finish_save:
 * @self: a sheet that answered SAVE
 * @problem: (nullable): %NULL when saved; otherwise why it could not be, in a
 *   sentence the person can act on. The sheet stays up to try again.
 */
void
luma_save_sheet_finish_save (LumaSaveSheet *self, const char *problem)
{
  g_return_if_fail (LUMA_IS_SAVE_SHEET (self));
  if (self->closing)
    return;
  set_busy (self, FALSE);
  if (problem != NULL) {
    luma_save_sheet_show_error (self, *problem ? problem : S_("It could not be saved."));
    gtk_widget_grab_focus (self->name_entry ? self->name_entry : self->primary);
    return;
  }
  close_sheet (self, FALSE, LUMA_SAVE_CHOICE_SAVE, NULL, NULL);
}

/**
 * luma_save_sheet_activate_cancel:
 * @self: a sheet
 *
 * Cancel: as Escape or a click. Nothing is saved or discarded.
 */
void
luma_save_sheet_activate_cancel (LumaSaveSheet *self)
{
  g_return_if_fail (LUMA_IS_SAVE_SHEET (self));
  if (self->closing)
    return;
  vitals (self->window, "save-sheet-cancel", sheet_case (self));
  close_sheet (self, TRUE, LUMA_SAVE_CHOICE_CANCEL, self->several ? NULL : self->document, NULL);
}

/**
 * luma_save_sheet_activate_discard:
 * @self: a sheet
 *
 * Don't save, or Discard all: as Ctrl+D or a click.
 */
void
luma_save_sheet_activate_discard (LumaSaveSheet *self)
{
  g_return_if_fail (LUMA_IS_SAVE_SHEET (self));
  if (self->closing || self->saving)
    return;
  vitals (self->window, "save-sheet-discard", sheet_case (self));
  if (self->several)
    close_sheet (self, TRUE, LUMA_SAVE_CHOICE_QUIT, NULL, NULL);
  else
    close_sheet (self, TRUE, LUMA_SAVE_CHOICE_DISCARD, self->document, NULL);
}

/**
 * luma_save_sheet_show_error:
 * @self: a sheet
 * @message: what went wrong, shown under the fields and announced
 */
void
luma_save_sheet_show_error (LumaSaveSheet *self, const char *message)
{
  g_return_if_fail (LUMA_IS_SAVE_SHEET (self));
  gtk_label_set_label (GTK_LABEL (self->error), message);
  gtk_widget_set_visible (self->error, TRUE);
  if (self->untitled) {
    gtk_widget_add_css_class (self->name_row, "error");
    gtk_accessible_update_state (GTK_ACCESSIBLE (self->name_entry),
                                 GTK_ACCESSIBLE_STATE_INVALID, GTK_ACCESSIBLE_INVALID_TRUE, -1);
    gtk_accessible_update_property (GTK_ACCESSIBLE (self->name_entry),
                                    GTK_ACCESSIBLE_PROPERTY_DESCRIPTION, message, -1);
  }
  gtk_accessible_announce (GTK_ACCESSIBLE (self->card), message,
                           GTK_ACCESSIBLE_ANNOUNCEMENT_PRIORITY_HIGH);
}

static void
clear_error (LumaSaveSheet *self)
{
  if (!gtk_widget_get_visible (self->error))
    return;
  gtk_widget_set_visible (self->error, FALSE);
  if (self->untitled) {
    gtk_widget_remove_css_class (self->name_row, "error");
    gtk_accessible_update_state (GTK_ACCESSIBLE (self->name_entry),
                                 GTK_ACCESSIBLE_STATE_INVALID, GTK_ACCESSIBLE_INVALID_FALSE, -1);
    gtk_accessible_reset_property (GTK_ACCESSIBLE (self->name_entry),
                                   GTK_ACCESSIBLE_PROPERTY_DESCRIPTION);
  }
}

/* ── Keys ─────────────────────────────────────────────────────────────── */

static gboolean
focus_within (GtkWidget *focus, GtkWidget *widget)
{
  return widget != NULL && focus != NULL && (focus == widget || gtk_widget_is_ancestor (focus, widget));
}

static void
focus_chain (LumaSaveSheet *self, GPtrArray *chain)
{
  GtkWidget *all[] = { self->name_entry, self->where, self->primary, self->cancel, self->discard };
  for (guint i = 0; i < G_N_ELEMENTS (all); i++)
    if (all[i] != NULL && gtk_widget_get_visible (all[i]) && gtk_widget_is_sensitive (all[i]))
      g_ptr_array_add (chain, all[i]);
}

static gboolean
key_pressed (GtkEventControllerKey *controller, guint keyval, guint keycode,
             GdkModifierType state, gpointer data)
{
  LumaSaveSheet *self = data;
  GdkEvent *event = gtk_event_controller_get_current_event (GTK_EVENT_CONTROLLER (controller));
  GtkNative *native = gtk_widget_get_native (GTK_WIDGET (self));
  GtkRoot *root = gtk_widget_get_root (GTK_WIDGET (self));
  GtkWidget *focus = root ? gtk_root_get_focus (root) : NULL;
  GdkModifierType modifiers = state & (GDK_CONTROL_MASK | GDK_ALT_MASK | GDK_SUPER_MASK
                                       | GDK_SHIFT_MASK | GDK_META_MASK);
  (void) keycode;

  if (self->closing)
    return TRUE;
  /* A menu opened from the sheet (Where) takes its own keys. */
  if (event != NULL && native != NULL && gdk_event_get_surface (event) != gtk_native_get_surface (native))
    return FALSE;

  if (keyval == GDK_KEY_Escape && !modifiers) {
    luma_save_sheet_activate_cancel (self);
    return TRUE;
  }
  if ((keyval == GDK_KEY_d || keyval == GDK_KEY_D) && modifiers == GDK_CONTROL_MASK) {
    luma_save_sheet_activate_discard (self);
    return TRUE;
  }
  if ((keyval == GDK_KEY_Return || keyval == GDK_KEY_KP_Enter || keyval == GDK_KEY_ISO_Enter)
      && !(modifiers & ~GDK_SHIFT_MASK)) {
    /* Enter answers with the primary, except on a control that has its own
     * answer: Cancel, Don't save, and the Where menu. */
    if (focus_within (focus, self->cancel) || focus_within (focus, self->discard)
        || focus_within (focus, self->where))
      return FALSE;
    luma_save_sheet_activate_primary (self);
    return TRUE;
  }
  if ((keyval == GDK_KEY_Tab || keyval == GDK_KEY_ISO_Left_Tab || keyval == GDK_KEY_KP_Tab)
      && !(modifiers & ~GDK_SHIFT_MASK)) {
    g_autoptr (GPtrArray) chain = g_ptr_array_new ();
    int step = (keyval == GDK_KEY_ISO_Left_Tab || (modifiers & GDK_SHIFT_MASK)) ? -1 : 1;
    int index = -1;
    focus_chain (self, chain);
    if (chain->len == 0)
      return TRUE;
    for (guint i = 0; i < chain->len; i++)
      if (focus_within (focus, chain->pdata[i])) {
        index = (int) i;
        break;
      }
    if (index < 0)
      index = step > 0 ? 0 : (int) chain->len - 1;
    else
      index = (index + step + (int) chain->len) % (int) chain->len;
    gtk_widget_grab_focus (chain->pdata[index]);
    return TRUE;
  }
  return FALSE;
}

/* ── Accessors ────────────────────────────────────────────────────────── */

const char *
luma_save_sheet_get_case (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return sheet_case (self);
}

gboolean
luma_save_sheet_get_open (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), FALSE);
  return self->open;
}

/**
 * luma_save_sheet_get_title_label:
 * @self: a sheet
 *
 * Returns: (transfer none): the title
 */
GtkLabel *
luma_save_sheet_get_title_label (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return GTK_LABEL (self->title);
}

/**
 * luma_save_sheet_get_body_label:
 * @self: a sheet
 *
 * Returns: (transfer none): the body
 */
GtkLabel *
luma_save_sheet_get_body_label (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return GTK_LABEL (self->body);
}

/**
 * luma_save_sheet_get_error_label:
 * @self: a sheet
 *
 * Returns: (transfer none): the inline error
 */
GtkLabel *
luma_save_sheet_get_error_label (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return GTK_LABEL (self->error);
}

/**
 * luma_save_sheet_get_primary_button:
 * @self: a sheet
 *
 * Returns: (transfer none): Save or Review changes…
 */
GtkButton *
luma_save_sheet_get_primary_button (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return GTK_BUTTON (self->primary);
}

/**
 * luma_save_sheet_get_cancel_button:
 * @self: a sheet
 *
 * Returns: (transfer none): Cancel
 */
GtkButton *
luma_save_sheet_get_cancel_button (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return GTK_BUTTON (self->cancel);
}

/**
 * luma_save_sheet_get_discard_button:
 * @self: a sheet
 *
 * Returns: (transfer none): Don't save or Discard all
 */
GtkButton *
luma_save_sheet_get_discard_button (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return GTK_BUTTON (self->discard);
}

/**
 * luma_save_sheet_get_name_entry:
 * @self: a sheet
 *
 * Returns: (transfer none) (nullable): Name, untitled only
 */
GtkEntry *
luma_save_sheet_get_name_entry (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return self->name_entry ? GTK_ENTRY (self->name_entry) : NULL;
}

/**
 * luma_save_sheet_get_where:
 * @self: a sheet
 *
 * Returns: (transfer none) (nullable): Where, untitled only
 */
GtkDropDown *
luma_save_sheet_get_where (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return self->where ? GTK_DROP_DOWN (self->where) : NULL;
}

/**
 * luma_save_sheet_get_card:
 * @self: a sheet
 *
 * Returns: (transfer none): the sheet's card
 */
GtkWidget *
luma_save_sheet_get_card (LumaSaveSheet *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_SHEET (self), NULL);
  return self->card;
}


/**
 * luma_save_sheet_get_for_window:
 * @window: a window
 *
 * Returns: (transfer none) (nullable): the save sheet hanging from it
 */
LumaSaveSheet *
luma_save_sheet_get_for_window (GtkWindow *window)
{
  GtkWidget *sheet = luma_window_get_presented_sheet (window);
  return LUMA_IS_SAVE_SHEET (sheet) ? LUMA_SAVE_SHEET (sheet) : NULL;
}

/* ── LumaSaveRequest ──────────────────────────────────────────────────── */

struct _LumaSaveRequest {
  GObject parent_instance;
  GPtrArray *documents;
  gboolean quitting;
  GWeakRef fallback;
  LumaSaveSheet *sheet;       /* the sheet up now */
  gulong response_id;
  int review;                 /* -1: not reviewing; else the document asked about */
  gboolean active;
};

enum { REQUEST_RESPONSE, N_REQUEST_SIGNALS };
static guint request_signals[N_REQUEST_SIGNALS];

G_DEFINE_FINAL_TYPE (LumaSaveRequest, luma_save_request, G_TYPE_OBJECT)

static void
detach_sheet (LumaSaveRequest *self)
{
  if (self->sheet == NULL)
    return;
  g_clear_signal_handler (&self->response_id, self->sheet);
  g_clear_object (&self->sheet);
}

static void
luma_save_request_dispose (GObject *object)
{
  LumaSaveRequest *self = LUMA_SAVE_REQUEST (object);
  detach_sheet (self);
  G_OBJECT_CLASS (luma_save_request_parent_class)->dispose (object);
}

static void
luma_save_request_finalize (GObject *object)
{
  LumaSaveRequest *self = LUMA_SAVE_REQUEST (object);
  g_ptr_array_unref (self->documents);
  g_weak_ref_clear (&self->fallback);
  G_OBJECT_CLASS (luma_save_request_parent_class)->finalize (object);
}

static void
luma_save_request_class_init (LumaSaveRequestClass *klass)
{
  G_OBJECT_CLASS (klass)->dispose = luma_save_request_dispose;
  G_OBJECT_CLASS (klass)->finalize = luma_save_request_finalize;
  /**
   * LumaSaveRequest::response:
   * @request: the request
   * @choice: what the person chose
   * @document: (nullable): the document it is about
   * @destination: (nullable): for an untitled document, the file to write
   *
   * One document: SAVE (call luma_save_request_finish_save() once written, or
   * with the problem), DISCARD or CANCEL. Several: while reviewing, SAVE or
   * DISCARD for each document in turn; CANCEL at any point stops the quit;
   * QUIT once every document is answered, or after Discard all.
   */
  request_signals[REQUEST_RESPONSE] =
    g_signal_new ("response", G_TYPE_FROM_CLASS (klass), G_SIGNAL_RUN_LAST,
                  0, NULL, NULL, NULL, G_TYPE_NONE, 3,
                  LUMA_TYPE_SAVE_CHOICE, LUMA_TYPE_SAVE_DOCUMENT, G_TYPE_FILE);
}

static void
luma_save_request_init (LumaSaveRequest *self)
{
  self->documents = g_ptr_array_new_with_free_func (g_object_unref);
  self->review = -1;
  g_weak_ref_init (&self->fallback, NULL);
}

/**
 * luma_save_request_new:
 * @documents: (array length=n_documents): the unsaved documents
 * @n_documents: how many
 * @quitting: the application is quitting
 *
 * Returns: (transfer full): a request; connect #LumaSaveRequest::response,
 *   then luma_save_request_present()
 */
LumaSaveRequest *
luma_save_request_new (LumaSaveDocument * const *documents, guint n_documents, gboolean quitting)
{
  LumaSaveRequest *self;
  g_return_val_if_fail (documents != NULL && n_documents > 0, NULL);
  self = g_object_new (LUMA_TYPE_SAVE_REQUEST, NULL);
  for (guint i = 0; i < n_documents; i++) {
    g_return_val_if_fail (LUMA_IS_SAVE_DOCUMENT (documents[i]), self);
    g_ptr_array_add (self->documents, g_object_ref (documents[i]));
  }
  self->quitting = quitting || n_documents > 1;
  return self;
}

static void finish (LumaSaveRequest *self, LumaSaveChoice choice, LumaSaveDocument *document, GFile *destination);
static void ask (LumaSaveRequest *self, LumaSaveDocument * const *documents, guint n, GtkWindow *window);
static void review_next (LumaSaveRequest *self);

static void
emit (LumaSaveRequest *self, LumaSaveChoice choice, LumaSaveDocument *document, GFile *destination)
{
  g_signal_emit (self, request_signals[REQUEST_RESPONSE], 0, choice, document, destination);
}

static void
finish (LumaSaveRequest *self, LumaSaveChoice choice, LumaSaveDocument *document, GFile *destination)
{
  gboolean was_active = self->active;
  self->active = FALSE;
  detach_sheet (self);
  emit (self, choice, document, destination);
  if (was_active)
    g_object_unref (self);
}

static void
sheet_answered (LumaSaveSheet *sheet, LumaSaveChoice choice, LumaSaveDocument *document,
                GFile *destination, gpointer data)
{
  LumaSaveRequest *self = data;
  (void) sheet;

  if (self->review < 0 && self->documents->len > 1) {
    /* The several sheet: Review changes…, Discard all, or Cancel. */
    if (choice == LUMA_SAVE_CHOICE_SAVE) {
      detach_sheet (self);
      self->review = 0;
      review_next (self);
    } else {
      finish (self, choice, NULL, NULL);
    }
    return;
  }
  if (choice == LUMA_SAVE_CHOICE_CANCEL) {
    finish (self, LUMA_SAVE_CHOICE_CANCEL, document, NULL);
    return;
  }
  if (choice == LUMA_SAVE_CHOICE_SAVE) {
    /* The sheet stays up, busy, until luma_save_request_finish_save(). */
    emit (self, choice, document, destination);
    return;
  }
  /* DISCARD. */
  if (self->review < 0) {
    finish (self, choice, document, NULL);
    return;
  }
  detach_sheet (self);
  emit (self, choice, document, NULL);
  self->review++;
  review_next (self);
}

static GtkWindow *
window_for (LumaSaveRequest *self, LumaSaveDocument *document)
{
  GtkWindow *window = luma_save_document_get_window (document);
  g_autoptr (GtkWindow) fallback = NULL;
  if (window != NULL && gtk_widget_get_realized (GTK_WIDGET (window)))
    return window;
  fallback = g_weak_ref_get (&self->fallback);
  if (fallback != NULL && gtk_widget_get_realized (GTK_WIDGET (fallback)))
    return fallback;
  /* Any window of the quit that is still open. */
  for (guint i = 0; i < self->documents->len; i++) {
    GtkWindow *other = luma_save_document_get_window (self->documents->pdata[i]);
    if (other != NULL && gtk_widget_get_realized (GTK_WIDGET (other)))
      return other;
  }
  return NULL;
}

static void
review_next (LumaSaveRequest *self)
{
  LumaSaveDocument *document;
  GtkWindow *window;

  if ((guint) self->review >= self->documents->len) {
    finish (self, LUMA_SAVE_CHOICE_QUIT, NULL, NULL);
    return;
  }
  document = self->documents->pdata[self->review];
  window = window_for (self, document);
  if (window == NULL) {
    /* Nowhere left to ask: keep the work rather than guess. */
    finish (self, LUMA_SAVE_CHOICE_CANCEL, document, NULL);
    return;
  }
  ask (self, &document, 1, window);
}

static void
ask (LumaSaveRequest *self, LumaSaveDocument * const *documents, guint n, GtkWindow *window)
{
  LumaSaveSheet *existing = luma_save_sheet_get_for_window (window);
  if (existing != NULL)
    luma_save_sheet_activate_cancel (existing);
  self->sheet = luma_save_sheet_new (documents, n, self->quitting);
  self->response_id = g_signal_connect (self->sheet, "response", G_CALLBACK (sheet_answered), self);
  luma_save_sheet_present (self->sheet, window);
}

/**
 * luma_save_request_present:
 * @self: a request
 * @window: the window that asked
 *
 * One document asks in @window. Several ask once in @window; Review changes…
 * then brings each document's own window forward in turn and asks there. If
 * @window already shows a save sheet, that sheet is focused instead and this
 * request answers CANCEL.
 */
void
luma_save_request_present (LumaSaveRequest *self, GtkWindow *window)
{
  LumaSaveSheet *existing;

  g_return_if_fail (LUMA_IS_SAVE_REQUEST (self));
  g_return_if_fail (GTK_IS_WINDOW (window));
  g_return_if_fail (!self->active);

  existing = luma_save_sheet_get_for_window (window);
  if (existing != NULL && !existing->closing) {
    focus_first (existing);
    gtk_window_present (window);
    emit (self, LUMA_SAVE_CHOICE_CANCEL, NULL, NULL);
    return;
  }
  g_weak_ref_set (&self->fallback, window);
  self->active = TRUE;
  g_object_ref (self); /* Alive until answered. */
  ask (self, (LumaSaveDocument * const *) self->documents->pdata, self->documents->len, window);
}

/**
 * luma_save_request_finish_save:
 * @self: a request that answered SAVE
 * @problem: (nullable): %NULL once the document is written; otherwise why it
 *   could not be, shown on the sheet, which stays up
 */
void
luma_save_request_finish_save (LumaSaveRequest *self, const char *problem)
{
  LumaSaveSheet *sheet;
  g_return_if_fail (LUMA_IS_SAVE_REQUEST (self));
  if (self->sheet == NULL)
    return;
  sheet = g_object_ref (self->sheet);
  luma_save_sheet_finish_save (sheet, problem);
  if (problem == NULL) {
    if (self->review >= 0) {
      detach_sheet (self);
      self->review++;
      review_next (self);
    } else {
      self->active = FALSE;
      detach_sheet (self);
      g_object_unref (self);
    }
  }
  g_object_unref (sheet);
}

/**
 * luma_save_request_cancel:
 * @self: a request
 *
 * Takes the sheet down as Cancel would, for an application that must stop
 * asking (its window is going away for another reason).
 */
void
luma_save_request_cancel (LumaSaveRequest *self)
{
  g_return_if_fail (LUMA_IS_SAVE_REQUEST (self));
  if (self->sheet != NULL)
    luma_save_sheet_activate_cancel (self->sheet);
}

/**
 * luma_save_request_get_sheet:
 * @self: a request
 *
 * Returns: (transfer none) (nullable): the sheet up now
 */
LumaSaveSheet *
luma_save_request_get_sheet (LumaSaveRequest *self)
{
  g_return_val_if_fail (LUMA_IS_SAVE_REQUEST (self), NULL);
  return self->sheet;
}
