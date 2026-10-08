/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_INSPECTOR_SECTION (luma_inspector_section_get_type())
G_DECLARE_FINAL_TYPE(LumaInspectorSection, luma_inspector_section, LUMA,
                     INSPECTOR_SECTION, GtkBox)

GtkWidget *luma_inspector_section_new(const char *title);
void luma_inspector_section_set_title(LumaInspectorSection *self,
                                      const char *title);
void luma_inspector_section_set_child(LumaInspectorSection *self,
                                      GtkWidget *child);
GtkWidget *luma_inspector_section_get_child(LumaInspectorSection *self);
void luma_inspector_section_set_expanded(LumaInspectorSection *self,
                                         gboolean expanded);
gboolean luma_inspector_section_get_expanded(LumaInspectorSection *self);

G_END_DECLS
