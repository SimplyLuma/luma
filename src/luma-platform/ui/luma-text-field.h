/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_field.TextField / HeroTitleField / ParagraphField (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaTextField:
 *
 * A label over a recessed well, with an optional hint under it. Purposes:
 * "text", "name", "email", "phone", "url", "number", "password", "pin". Signals:
 * #LumaTextField::changed (const char *text), #LumaTextField::activate
 * (const char *text).
 */
#define LUMA_TYPE_TEXT_FIELD (luma_text_field_get_type())
G_DECLARE_FINAL_TYPE(LumaTextField, luma_text_field, LUMA, TEXT_FIELD, GtkBox)

/**
 * luma_text_field_new:
 * @label: what it asks for
 * @purpose: (nullable): the input purpose; %NULL is "text"
 *
 * Returns: (transfer floating): a new field
 */
GtkWidget *luma_text_field_new(const char *label, const char *purpose);
/**
 * luma_text_field_set_size:
 * @self: a field
 * @size: "regular" (38px well) or "panel" (48px well in a grown action panel)
 */
void luma_text_field_set_size(LumaTextField *self, const char *size);
const char *luma_text_field_get_size(LumaTextField *self);
const char *luma_text_field_get_text(LumaTextField *self);
void luma_text_field_set_text(LumaTextField *self, const char *text);
/**
 * luma_text_field_set_placeholder:
 * @self: a field
 * @placeholder: (nullable): the well's placeholder
 */
void luma_text_field_set_placeholder(LumaTextField *self, const char *placeholder);
/**
 * luma_text_field_set_hint:
 * @self: a field
 * @hint: (nullable): the line under the well
 */
void luma_text_field_set_hint(LumaTextField *self, const char *hint);

/**
 * LumaHeroTitleField:
 *
 * A hero title (30/700) that is also its own editor: it looks like the title
 * until focused. Enter or leaving it commits (#LumaHeroTitleField::committed,
 * const char *text); Esc puts the old title back.
 */
#define LUMA_TYPE_HERO_TITLE_FIELD (luma_hero_title_field_get_type())
G_DECLARE_FINAL_TYPE(LumaHeroTitleField, luma_hero_title_field, LUMA, HERO_TITLE_FIELD, GtkEntry)

/**
 * luma_hero_title_field_new:
 * @text: (nullable): the title
 * @placeholder: (nullable): shown when empty; %NULL is "Name"
 *
 * Returns: (transfer floating): a new field
 */
GtkWidget *luma_hero_title_field_new(const char *text, const char *placeholder);
/**
 * luma_hero_title_field_set_editable_title:
 * @self: a field
 * @editable: %FALSE shows just the title
 */
void luma_hero_title_field_set_editable_title(LumaHeroTitleField *self, gboolean editable);
void luma_hero_title_field_commit(LumaHeroTitleField *self);

/**
 * LumaParagraphField:
 *
 * A paragraph that is its own editor (Contacts' private note): body text,
 * wrapping, no frame; the placeholder shows, muted, while it is empty. For a
 * labelled single line, use #LumaTextField.
 */
#define LUMA_TYPE_PARAGRAPH_FIELD (luma_paragraph_field_get_type())
G_DECLARE_FINAL_TYPE(LumaParagraphField, luma_paragraph_field, LUMA, PARAGRAPH_FIELD, GtkTextView)

/**
 * luma_paragraph_field_new:
 * @text: (nullable): the paragraph
 * @placeholder: (nullable): shown while it is empty
 * @label: (nullable): its name for screen readers; %NULL is @placeholder
 *
 * Returns: (transfer floating): a new field
 */
GtkWidget *luma_paragraph_field_new(const char *text, const char *placeholder, const char *label);
/**
 * luma_paragraph_field_get_text:
 * @self: a field
 *
 * Returns: (transfer full): the paragraph
 */
char *luma_paragraph_field_get_text(LumaParagraphField *self);
void luma_paragraph_field_set_text(LumaParagraphField *self, const char *text);

G_END_DECLS
