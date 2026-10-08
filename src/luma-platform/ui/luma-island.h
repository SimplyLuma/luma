/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/* The shared clipped content island on the Luma window frame. Children are
 * appended with the GtkBox API; the native part paints the inset edge above
 * them, as the Python Island does. */
#define LUMA_TYPE_ISLAND (luma_island_get_type())
G_DECLARE_FINAL_TYPE(LumaIsland, luma_island, LUMA, ISLAND, GtkBox)

GtkWidget *luma_island_new(GtkOrientation orientation);

G_END_DECLS
