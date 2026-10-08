/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define LUMA_TYPE_SEGMENTED_CONTROL (luma_segmented_control_get_type())
G_DECLARE_FINAL_TYPE(LumaSegmentedControl, luma_segmented_control, LUMA,
                     SEGMENTED_CONTROL, GtkBox)

GtkWidget *luma_segmented_control_new(void);
void luma_segmented_control_append(LumaSegmentedControl *self,
                                   const char *id, const char *label);
void luma_segmented_control_set_selected(LumaSegmentedControl *self,
                                         const char *id);
const char *luma_segmented_control_get_selected(LumaSegmentedControl *self);

G_END_DECLS
