/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Internals the content parts share; not installed, not introspected (the
 * face and width helpers start with an underscore). Defined in luma-cards.c: the twins of
 * content_file._Face and cap_width, and lumaui.hue_class / person_hue.
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/* _Face: a picture cropped to a fixed square with rounded corners (CSS class
 * lumaui-file-face). Never larger than its square. */
GtkWidget *_luma_content_face_new(int size, double radius);
void _luma_content_face_set_paintable(GtkWidget *face, GdkPaintable *paintable);

/* cap_width: give @box (a GtkBox) a design width it keeps unless the place it
 * sits in is narrower (_CappedLayout, a GtkBoxLayout whose natural width is
 * @width). */
void _luma_content_cap_width(GtkWidget *box, int width);

/* lumaui.person_hue (luma_ui_person_hue): the hue a name wears when nothing else gives one. */
int luma_ui_person_hue(const char *name);
/* lumaui.hue_class: give @widget the class "lumaui-hue-<hue>" (a hue < 0
 * removes it) and make sure the kit's hue provider carries its colours.
 * Returns the class ("" when removed); the string is interned. */
const char *luma_ui_hue_class(GtkWidget *widget, int hue);

G_END_DECLS
