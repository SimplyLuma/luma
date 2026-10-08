/* SPDX-License-Identifier: Apache-2.0 */
/* The save sheet as a C application uses it: a plain window with a header
 * bar, the request API, and the answers in order. Keys are covered by
 * tests/save_sheet.py with real key presses. */
#include "luma-ui.h"

typedef struct {
  GArray *choices;
  GPtrArray *documents;
} Answers;

static void
settle (void)
{
  gint64 until = g_get_monotonic_time () + 400000;
  while (g_get_monotonic_time () < until) {
    while (g_main_context_iteration (NULL, FALSE));
    g_usleep (2000);
  }
}

static void
answered (LumaSaveRequest *request, LumaSaveChoice choice, LumaSaveDocument *document,
          GFile *destination, gpointer data)
{
  Answers *answers = data;
  (void) request;
  (void) destination;
  g_array_append_val (answers->choices, choice);
  g_ptr_array_add (answers->documents, document);
}

static LumaSaveChoice
last (Answers *answers)
{
  g_assert_cmpuint (answers->choices->len, >, 0);
  return g_array_index (answers->choices, LumaSaveChoice, answers->choices->len - 1);
}

static void
assert_red (GtkWidget *widget, const char *expected)
{
  GdkRGBA color, want;
  gtk_widget_get_color (widget, &color);
  g_assert_true (gdk_rgba_parse (&want, expected));
  {
    g_autofree char *got = gdk_rgba_to_string (&color);
    g_auto (GStrv) classes = gtk_widget_get_css_classes (widget);
    g_autofree char *joined = g_strjoinv (" ", classes);
    g_test_message ("%s: %s, want %s (classes: %s; state flags 0x%x)", gtk_widget_get_css_name (widget), got,
                    expected, joined, gtk_widget_get_state_flags (widget));
  }
  g_assert_cmpint ((int) (color.red * 255 + .5), ==, (int) (want.red * 255 + .5));
  g_assert_cmpint ((int) (color.green * 255 + .5), ==, (int) (want.green * 255 + .5));
  g_assert_cmpint ((int) (color.blue * 255 + .5), ==, (int) (want.blue * 255 + .5));
}

int
main (int argc, char **argv)
{
  Answers answers = { g_array_new (FALSE, FALSE, sizeof (LumaSaveChoice)), g_ptr_array_new () };
  GtkWidget *window, *view, *header, *share, *editor;
  LumaSaveDocument *named, *second;
  LumaSaveRequest *request;
  LumaSaveSheet *sheet;
  graphene_point_t top;

  g_test_init (&argc, &argv, NULL);
  if (!gtk_init_check ())
    return 77;
  luma_init ();
  adw_style_manager_set_color_scheme (adw_style_manager_get_default (), ADW_COLOR_SCHEME_FORCE_LIGHT);

  window = adw_window_new ();
  gtk_window_set_default_size (GTK_WINDOW (window), 900, 640);
  view = adw_toolbar_view_new ();
  header = adw_header_bar_new ();
  share = gtk_button_new_with_label ("Share");
  adw_header_bar_pack_start (ADW_HEADER_BAR (header), share);
  adw_toolbar_view_add_top_bar (ADW_TOOLBAR_VIEW (view), header);
  editor = gtk_text_view_new ();
  adw_toolbar_view_set_content (ADW_TOOLBAR_VIEW (view), editor);
  adw_window_set_content (ADW_WINDOW (window), view);
  gtk_window_present (GTK_WINDOW (window));
  settle ();
  gtk_widget_grab_focus (editor);

  named = luma_save_document_new ("Mixdown v3");
  luma_save_document_set_unsaved_seconds (named, 14 * 60);
  luma_save_document_set_kind (named, "project");
  luma_save_document_set_window (named, GTK_WINDOW (window));

  /* Named: hangs under the header bar, Save focused, commands wait. */
  request = luma_save_request_new (&named, 1, FALSE);
  g_signal_connect (request, "response", G_CALLBACK (answered), &answers);
  luma_save_request_present (request, GTK_WINDOW (window));
  settle ();
  sheet = luma_save_sheet_get_for_window (GTK_WINDOW (window));
  g_assert_nonnull (sheet);
  g_assert_true (luma_save_sheet_get_open (sheet));
  g_assert_cmpstr (gtk_label_get_label (luma_save_sheet_get_title_label (sheet)), ==, "Save changes to “Mixdown v3”?");
  g_assert_cmpstr (gtk_label_get_label (luma_save_sheet_get_body_label (sheet)), ==,
                   "You have 14 minutes of edits that are not saved yet.");
  g_assert_true (gtk_widget_has_focus (GTK_WIDGET (luma_save_sheet_get_primary_button (sheet))));
  g_assert_false (gtk_widget_get_can_target (share));
  g_assert_false (gtk_widget_get_can_target (editor) && gtk_widget_get_can_target (gtk_widget_get_parent (editor)));
  g_assert_true (gtk_widget_compute_point (GTK_WIDGET (sheet), window, &GRAPHENE_POINT_INIT (0, 0), &top));
  g_assert_cmpfloat (top.y, >=, gtk_widget_get_height (header) - 1);
  g_assert_cmpint (gtk_widget_get_width (luma_save_sheet_get_card (sheet)), <=, 360);
  /* The destructive text of whichever appearance the kit settled on (it follows the
   * surface the person chose, which a test on their machine inherits). */
  assert_red (GTK_WIDGET (luma_save_sheet_get_discard_button (sheet)),
              adw_style_manager_get_dark (adw_style_manager_get_default ()) ? "#ffb4ab" : "#983b34");

  /* Save, a failure shown on the sheet, then saved. */
  luma_save_sheet_activate_primary (sheet);
  g_assert_cmpint (last (&answers), ==, LUMA_SAVE_CHOICE_SAVE);
  luma_save_request_finish_save (request, "The disk is full.");
  g_assert_true (gtk_widget_get_visible (GTK_WIDGET (luma_save_sheet_get_error_label (sheet))));
  g_assert_true (luma_save_sheet_get_for_window (GTK_WINDOW (window)) == sheet);
  luma_save_sheet_activate_primary (sheet);
  g_assert_cmpuint (answers.choices->len, ==, 2);
  luma_save_request_finish_save (request, NULL);
  settle ();
  g_assert_null (luma_save_sheet_get_for_window (GTK_WINDOW (window)));
  g_assert_true (gtk_widget_get_can_target (share));
  g_assert_true (gtk_widget_has_focus (editor));
  g_object_unref (request);

  /* Cancel and Don't save. */
  request = luma_save_request_new (&named, 1, FALSE);
  g_signal_connect (request, "response", G_CALLBACK (answered), &answers);
  luma_save_request_present (request, GTK_WINDOW (window));
  settle ();
  luma_save_sheet_activate_cancel (luma_save_request_get_sheet (request));
  settle ();
  g_assert_cmpint (last (&answers), ==, LUMA_SAVE_CHOICE_CANCEL);
  g_object_unref (request);
  request = luma_save_request_new (&named, 1, FALSE);
  g_signal_connect (request, "response", G_CALLBACK (answered), &answers);
  luma_save_request_present (request, GTK_WINDOW (window));
  settle ();
  luma_save_sheet_activate_discard (luma_save_request_get_sheet (request));
  settle ();
  g_assert_cmpint (last (&answers), ==, LUMA_SAVE_CHOICE_DISCARD);
  g_assert_true (g_ptr_array_index (answers.documents, answers.documents->len - 1) == named);
  g_object_unref (request);

  /* Several: review each in turn, then quit. */
  second = luma_save_document_new ("Stems");
  luma_save_document_set_never_saved (second, TRUE);
  luma_save_document_set_kind (second, "project");
  {
    LumaSaveDocument *both[] = { named, second };
    g_array_set_size (answers.choices, 0);
    g_ptr_array_set_size (answers.documents, 0);
    request = luma_save_request_new (both, 2, TRUE);
  }
  g_signal_connect (request, "response", G_CALLBACK (answered), &answers);
  luma_save_request_present (request, GTK_WINDOW (window));
  settle ();
  sheet = luma_save_request_get_sheet (request);
  g_assert_cmpstr (luma_save_sheet_get_case (sheet), ==, "several");
  g_assert_cmpstr (gtk_label_get_label (luma_save_sheet_get_title_label (sheet)), ==,
                   "Save changes to 2 projects before quitting?");
  luma_save_sheet_activate_primary (sheet);
  settle ();
  sheet = luma_save_request_get_sheet (request);
  g_assert_cmpstr (luma_save_sheet_get_case (sheet), ==, "named");
  luma_save_sheet_activate_discard (sheet);
  settle ();
  sheet = luma_save_request_get_sheet (request);
  g_assert_cmpstr (luma_save_sheet_get_case (sheet), ==, "untitled");
  g_assert_cmpstr (gtk_label_get_label (luma_save_sheet_get_title_label (sheet)), ==, "Save “Stems” before quitting?");
  luma_save_sheet_activate_cancel (sheet);
  settle ();
  g_assert_cmpuint (answers.choices->len, ==, 2);
  g_assert_cmpint (g_array_index (answers.choices, LumaSaveChoice, 0), ==, LUMA_SAVE_CHOICE_DISCARD);
  g_assert_cmpint (g_array_index (answers.choices, LumaSaveChoice, 1), ==, LUMA_SAVE_CHOICE_CANCEL);
  g_object_unref (request);

  g_object_unref (named);
  g_object_unref (second);
  gtk_window_destroy (GTK_WINDOW (window));
  settle ();
  g_array_unref (answers.choices);
  g_ptr_array_unref (answers.documents);
  return 0;
}
