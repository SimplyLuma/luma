/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <adwaita.h>

G_BEGIN_DECLS

#define LUMA_TYPE_ADAPTIVE_SCAFFOLD (luma_adaptive_scaffold_get_type())
G_DECLARE_FINAL_TYPE(LumaAdaptiveScaffold, luma_adaptive_scaffold, LUMA,
                     ADAPTIVE_SCAFFOLD, AdwBreakpointBin)

GtkWidget *luma_adaptive_scaffold_new(void);
void luma_adaptive_scaffold_set_sidebar(LumaAdaptiveScaffold *self,
                                        GtkWidget *sidebar, const char *title);
void luma_adaptive_scaffold_set_content(LumaAdaptiveScaffold *self,
                                        GtkWidget *content, const char *title);
void luma_adaptive_scaffold_set_compact_width(LumaAdaptiveScaffold *self,
                                              guint width);
/* Opt into a configured sidebar width in logical pixels. Before either setter
 * is called, native adaptive sizing is unchanged. Getter/property describe the
 * configured preference (initially 180), not the collapsed page allocation.
 * Limits default to 160..320 and must remain within 120..640. Native child
 * minimum sizes still apply; callers should provide responsive sidebar content. */
void luma_adaptive_scaffold_set_sidebar_width_limits(
    LumaAdaptiveScaffold *self, guint minimum_width, guint maximum_width);
void luma_adaptive_scaffold_set_sidebar_width(LumaAdaptiveScaffold *self,
                                              guint width);
guint luma_adaptive_scaffold_get_sidebar_width(LumaAdaptiveScaffold *self);
gboolean luma_adaptive_scaffold_get_collapsed(LumaAdaptiveScaffold *self);
void luma_adaptive_scaffold_set_show_content(LumaAdaptiveScaffold *self,
                                             gboolean show_content);

G_END_DECLS
