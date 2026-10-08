/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <adwaita.h>

G_BEGIN_DECLS

/**
 * LumaSaveChoice:
 * @LUMA_SAVE_CHOICE_SAVE: save this document; for an untitled one, into the
 *   destination the person chose on the sheet
 * @LUMA_SAVE_CHOICE_DISCARD: close this document without saving it
 * @LUMA_SAVE_CHOICE_CANCEL: keep editing; a quit in progress stops
 * @LUMA_SAVE_CHOICE_QUIT: every document of a quit is answered, or the person
 *   chose Discard all: quit now
 *
 * What the person chose on the save sheet. The application acts on each one.
 */
typedef enum {
  LUMA_SAVE_CHOICE_SAVE,
  LUMA_SAVE_CHOICE_DISCARD,
  LUMA_SAVE_CHOICE_CANCEL,
  LUMA_SAVE_CHOICE_QUIT,
} LumaSaveChoice;

GType luma_save_choice_get_type (void);
#define LUMA_TYPE_SAVE_CHOICE (luma_save_choice_get_type ())

/* ── The facts about one unsaved document ─────────────────────────────── */

#define LUMA_TYPE_SAVE_DOCUMENT (luma_save_document_get_type ())
G_DECLARE_FINAL_TYPE (LumaSaveDocument, luma_save_document, LUMA, SAVE_DOCUMENT, GObject)

LumaSaveDocument *luma_save_document_new              (const char       *name);
const char       *luma_save_document_get_name         (LumaSaveDocument *self);
void              luma_save_document_set_never_saved  (LumaSaveDocument *self,
                                                       gboolean          never_saved);
gboolean          luma_save_document_get_never_saved  (LumaSaveDocument *self);
void              luma_save_document_set_unsaved_seconds (LumaSaveDocument *self,
                                                          double            seconds);
double            luma_save_document_get_unsaved_seconds (LumaSaveDocument *self);
void              luma_save_document_set_window       (LumaSaveDocument *self,
                                                       GtkWindow        *window);
GtkWindow        *luma_save_document_get_window       (LumaSaveDocument *self);
void              luma_save_document_set_extension    (LumaSaveDocument *self,
                                                       const char       *extension);
void              luma_save_document_add_place        (LumaSaveDocument *self,
                                                       const char       *label,
                                                       const char       *path);
void              luma_save_document_set_recovery_copy (LumaSaveDocument *self,
                                                        gboolean          recovery_copy);
void              luma_save_document_set_kind         (LumaSaveDocument *self,
                                                       const char       *kind);

/* ── The sheet ─────────────────────────────────────────────────────────── */

#define LUMA_TYPE_SAVE_SHEET (luma_save_sheet_get_type ())
G_DECLARE_FINAL_TYPE (LumaSaveSheet, luma_save_sheet, LUMA, SAVE_SHEET, GtkWidget)

LumaSaveSheet *luma_save_sheet_new              (LumaSaveDocument * const *documents,
                                                 guint                     n_documents,
                                                 gboolean                  quitting);
void           luma_save_sheet_present          (LumaSaveSheet *self,
                                                 GtkWindow     *window);
void           luma_save_sheet_activate_primary (LumaSaveSheet *self);
void           luma_save_sheet_activate_cancel  (LumaSaveSheet *self);
void           luma_save_sheet_activate_discard (LumaSaveSheet *self);
void           luma_save_sheet_finish_save      (LumaSaveSheet *self,
                                                 const char    *problem);
void           luma_save_sheet_show_error       (LumaSaveSheet *self,
                                                 const char    *message);
const char    *luma_save_sheet_get_case         (LumaSaveSheet *self);
gboolean       luma_save_sheet_get_open         (LumaSaveSheet *self);
GtkLabel      *luma_save_sheet_get_title_label  (LumaSaveSheet *self);
GtkLabel      *luma_save_sheet_get_body_label   (LumaSaveSheet *self);
GtkLabel      *luma_save_sheet_get_error_label  (LumaSaveSheet *self);
GtkButton     *luma_save_sheet_get_primary_button (LumaSaveSheet *self);
GtkButton     *luma_save_sheet_get_cancel_button  (LumaSaveSheet *self);
GtkButton     *luma_save_sheet_get_discard_button (LumaSaveSheet *self);
GtkEntry      *luma_save_sheet_get_name_entry   (LumaSaveSheet *self);
GtkDropDown   *luma_save_sheet_get_where        (LumaSaveSheet *self);
GtkWidget     *luma_save_sheet_get_card         (LumaSaveSheet *self);
LumaSaveSheet *luma_save_sheet_get_for_window   (GtkWindow     *window);

/* ── The request: one question, or a quit over several documents ──────── */

#define LUMA_TYPE_SAVE_REQUEST (luma_save_request_get_type ())
G_DECLARE_FINAL_TYPE (LumaSaveRequest, luma_save_request, LUMA, SAVE_REQUEST, GObject)

LumaSaveRequest *luma_save_request_new         (LumaSaveDocument * const *documents,
                                                guint                     n_documents,
                                                gboolean                  quitting);
void             luma_save_request_present     (LumaSaveRequest *self,
                                                GtkWindow       *window);
void             luma_save_request_finish_save (LumaSaveRequest *self,
                                                const char      *problem);
void             luma_save_request_cancel      (LumaSaveRequest *self);
LumaSaveSheet   *luma_save_request_get_sheet   (LumaSaveRequest *self);

/* ── The window's sheet layer ─────────────────────────────────────────── */

void       luma_window_set_sheet_layer       (GtkWindow          *window,
                                              GtkOverlay         *layer,
                                              GtkWidget          *content,
                                              GtkWidget          *title_bar);
void       luma_window_present_sheet         (GtkWindow          *window,
                                              GtkWidget          *sheet,
                                              GtkEventController *keys);
void       luma_window_dismiss_sheet         (GtkWindow          *window);
GtkWidget *luma_window_get_presented_sheet   (GtkWindow          *window);

void       luma_ui_tokens_provided_by_application (void);

G_END_DECLS
