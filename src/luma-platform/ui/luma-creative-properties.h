/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI creative family (KB-D): the inspector's property fields (v70
 * .susec, .sufld, .suclrrow, .suselect, .seg, .sualign), used by Canvas,
 * Grid, Stage, Reel and Write.
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * luma_property_evaluate:
 * @text: what a person typed where a number is wanted ("120*2", "50%", "12pt")
 * @value: (out): the number
 *
 * Read a number, or the arithmetic typed instead of one (+ - * / % **,
 * parentheses); a trailing %, px or pt is ignored. Nothing else is evaluated.
 *
 * Returns: whether @text was a number
 */
gboolean luma_property_evaluate(const char *text, double *value);

/**
 * luma_property_heading_new:
 * @text: a panel's group heading ("Colours", "Named ranges")
 *
 * The quiet heading between groups in a panel (v70 .suh).
 *
 * Returns: (transfer floating): a new label
 */
GtkWidget *luma_property_heading_new(const char *text);

/**
 * LumaPropertySection:
 *
 * One group of an inspector (v70 .susec): a title, an optional action
 * (Add a stroke, Remove), and its fields; a hairline divides it from the
 * section above. Fields go in one per row, or two a row with
 * luma_property_section_add_pair().
 *
 * Tree: box.lumaui-creative-section
 *   ├ box.lumaui-creative-section-header > label.lumaui-creative-section-title,
 *   │                                      button.lumaui-creative-section-action
 *   └ fields … (grid.lumaui-creative-pairs for pairs)
 */
#define LUMA_TYPE_PROPERTY_SECTION (luma_property_section_get_type())
G_DECLARE_FINAL_TYPE(LumaPropertySection, luma_property_section, LUMA, PROPERTY_SECTION, GtkBox)

/**
 * luma_property_section_new:
 * @title: the section's name ("Layout", "Fill")
 * Returns: (transfer floating): a new, empty section
 */
GtkWidget *luma_property_section_new(const char *title);
/**
 * luma_property_section_set_action:
 * @self: a section
 * @icon: (nullable): a Lucide glyph ("plus", "minus"); %NULL removes the action
 * @label: (nullable): what it does ("Add a stroke")
 * @action_name: (nullable): the detailed #GAction it activates
 */
void luma_property_section_set_action(LumaPropertySection *self, const char *icon, const char *label,
                                      const char *action_name);
/**
 * luma_property_section_append:
 * @self: a section
 * @field: a field, full width
 */
void luma_property_section_append(LumaPropertySection *self, GtkWidget *field);
/**
 * luma_property_section_add_pair:
 * @self: a section
 * @start: the left field
 * @end: (nullable): the right field; %NULL leaves the column empty
 *
 * Two fields side by side, in equal columns (v70 .sugrid2).
 */
void luma_property_section_add_pair(LumaPropertySection *self, GtkWidget *start, GtkWidget *end);

/**
 * LumaPropertyNumber:
 *
 * A number in a well (v70 .sufld) with a short label before it ("X", "W",
 * "↻") and an optional unit after it ("°", "%", "px"). It behaves the way
 * a design tool's numbers do: dragging the label scrubs the value, Up and
 * Down step it (Shift ten times, Ctrl a tenth), arithmetic can be typed in,
 * and a field standing for several objects that disagree says "Mixed";
 * typing then applies to all. Enter or leaving commits; Esc puts it back.
 *
 * Emits #LumaPropertyNumber::value-changed when a person changes it, never
 * for luma_property_number_set_value().
 *
 * Tree: entry.lumaui-creative-number(.compact)(.mixed) > label.lumaui-creative-number-label,
 *   text, label.lumaui-creative-number-unit
 */
#define LUMA_TYPE_PROPERTY_NUMBER (luma_property_number_get_type())
G_DECLARE_FINAL_TYPE(LumaPropertyNumber, luma_property_number, LUMA, PROPERTY_NUMBER, GtkBox)

/**
 * luma_property_number_new:
 * @label: (nullable): the short label before the number ("W"); %NULL for none
 * @unit: (nullable): the unit after it ("px")
 * Returns: (transfer floating): a new field at 0
 */
GtkWidget *luma_property_number_new(const char *label, const char *unit);
/**
 * luma_property_number_set_name:
 * @self: a field
 * @name: what the number is, when its label is a symbol ("Rotation" for "↻")
 */
void luma_property_number_set_name(LumaPropertyNumber *self, const char *name);
void luma_property_number_set_value(LumaPropertyNumber *self, double value);
double luma_property_number_get_value(LumaPropertyNumber *self);
/**
 * luma_property_number_set_mixed:
 * @self: a field
 *
 * Say the selection disagrees, rather than showing one of its values.
 */
void luma_property_number_set_mixed(LumaPropertyNumber *self);
gboolean luma_property_number_get_mixed(LumaPropertyNumber *self);
/**
 * luma_property_number_set_range:
 * @self: a field
 * @minimum: the least value
 * @maximum: the greatest value
 */
void luma_property_number_set_range(LumaPropertyNumber *self, double minimum, double maximum);
/**
 * luma_property_number_set_step:
 * @self: a field
 * @step: one arrow press (default 1)
 * @digits: decimals shown (default 0)
 */
void luma_property_number_set_step(LumaPropertyNumber *self, double step, guint digits);
/**
 * luma_property_number_set_compact:
 * @self: a field
 * @compact: the narrow 64 px field that sits in a row with others (a stroke's width)
 */
void luma_property_number_set_compact(LumaPropertyNumber *self, gboolean compact);

/**
 * LumaPropertyColor:
 *
 * A colour (v70 .suclrrow): its swatch, and its hex code to type. The
 * swatch opens the document's palette when it has one. Mixed selections and
 * fills a code can't say (gradients) show a word instead, not editable.
 * Extra controls (a stroke's width, Remove) follow with
 * luma_property_color_add_suffix().
 *
 * Emits #LumaPropertyColor::color-changed when a person picks or types a
 * colour, never for luma_property_color_set_rgba().
 *
 * Tree: box.lumaui-creative-color > button.lumaui-creative-swatch > drawingarea,
 *   entry.lumaui-creative-hex, suffixes …
 */
#define LUMA_TYPE_PROPERTY_COLOR (luma_property_color_get_type())
G_DECLARE_FINAL_TYPE(LumaPropertyColor, luma_property_color, LUMA, PROPERTY_COLOR, GtkBox)

/**
 * luma_property_color_new:
 * @label: what the colour is, for assistive technology ("Fill", "Stroke")
 * Returns: (transfer floating): a new field, black
 */
GtkWidget *luma_property_color_new(const char *label);
void luma_property_color_set_rgba(LumaPropertyColor *self, const GdkRGBA *rgba);
/**
 * luma_property_color_get_rgba:
 * @self: a field
 * @rgba: (out caller-allocates): the colour
 */
void luma_property_color_get_rgba(LumaPropertyColor *self, GdkRGBA *rgba);
/**
 * luma_property_color_set_word:
 * @self: a field
 * @word: (nullable): what shows instead of a code ("Gradient", "Mixed");
 *   %NULL shows the code again
 */
void luma_property_color_set_word(LumaPropertyColor *self, const char *word);
/**
 * luma_property_color_set_palette:
 * @self: a field
 * @colors: (array length=n_colors) (nullable): the document's colours
 * @n_colors: how many
 */
void luma_property_color_set_palette(LumaPropertyColor *self, const GdkRGBA *colors, guint n_colors);
/**
 * luma_property_color_add_suffix:
 * @self: a field
 * @widget: a control after the code (a compact #LumaPropertyNumber, a Remove button)
 */
void luma_property_color_add_suffix(LumaPropertyColor *self, GtkWidget *widget);
/**
 * luma_property_color_format:
 * @rgba: a colour
 *
 * The code the field shows: "1F6FE0", or "1F6FE080" with alpha.
 *
 * Returns: (transfer full): the code
 */
char *luma_property_color_format(const GdkRGBA *rgba);
/**
 * luma_property_color_parse:
 * @text: a typed code ("1f6fe0", "#1F6FE0", "fff", "1F6FE080")
 * @rgba: (out caller-allocates): the colour
 * Returns: whether @text was a colour code
 */
gboolean luma_property_color_parse(const char *text, GdkRGBA *rgba);

/**
 * LumaChoiceKind:
 * @LUMA_CHOICE_KIND_MENU: a well naming the choice, opening a menu (v70 .suselect)
 * @LUMA_CHOICE_KIND_SEGMENTS: every choice at once, for two to four short
 *   ones (v70 .seg); a choice with an icon shows only its icon
 */
typedef enum {
  LUMA_CHOICE_KIND_MENU,
  LUMA_CHOICE_KIND_SEGMENTS,
} LumaChoiceKind;

GType luma_choice_kind_get_type(void) G_GNUC_CONST;
#define LUMA_TYPE_CHOICE_KIND (luma_choice_kind_get_type())

/**
 * LumaPropertyChoice:
 *
 * One of several named choices: a font, a frame size, a text alignment, an
 * export format. Emits #LumaPropertyChoice::changed when a person chooses,
 * never for luma_property_choice_set_current().
 *
 * Tree: button.lumaui-creative-select > box > label, image (menu), or
 *   box.lumaui-creative-segments > button.lumaui-creative-tab … (segments)
 */
#define LUMA_TYPE_PROPERTY_CHOICE (luma_property_choice_get_type())
G_DECLARE_FINAL_TYPE(LumaPropertyChoice, luma_property_choice, LUMA, PROPERTY_CHOICE, GtkWidget)

/**
 * luma_property_choice_new:
 * @kind: a menu or segments
 * @label: what is being chosen, for assistive technology ("Font", "Align")
 * Returns: (transfer floating): a new, empty choice
 */
GtkWidget *luma_property_choice_new(LumaChoiceKind kind, const char *label);
/**
 * luma_property_choice_add:
 * @self: a choice
 * @key: the choice's stable key ("center")
 * @label: its name ("Centre")
 * @icon: (nullable): a Lucide glyph ("text-align-center")
 *
 * The first choice added is current until another is chosen.
 */
void luma_property_choice_add(LumaPropertyChoice *self, const char *key, const char *label, const char *icon);
void luma_property_choice_set_current(LumaPropertyChoice *self, const char *key);
const char *luma_property_choice_get_current(LumaPropertyChoice *self);
/**
 * luma_property_choice_set_mixed:
 * @self: a choice
 *
 * The selection disagrees: the menu says "Mixed", no segment is chosen.
 */
void luma_property_choice_set_mixed(LumaPropertyChoice *self);

/**
 * LumaAlignmentActions:
 *
 * The six alignments of a selection (v70 .sualign): left, centre and right
 * edges, then top, middle and bottom. Emits #LumaAlignmentActions::align
 * with "left", "center", "right", "top", "middle" or "bottom", and
 * activates the action given, with that key as its target.
 *
 * Tree: box.lumaui-creative-align > button.lumaui-creative-align-button × 6
 */
#define LUMA_TYPE_ALIGNMENT_ACTIONS (luma_alignment_actions_get_type())
G_DECLARE_FINAL_TYPE(LumaAlignmentActions, luma_alignment_actions, LUMA, ALIGNMENT_ACTIONS, GtkBox)

/**
 * luma_alignment_actions_new:
 * @action_name: (nullable): a #GAction taking a string ("win.align"), or %NULL
 * Returns: (transfer floating): the six alignments
 */
GtkWidget *luma_alignment_actions_new(const char *action_name);

G_END_DECLS
