/* SPDX-License-Identifier: Apache-2.0 */
/* Internal native twin of appkit/app_updates.py; not installed or introspected. */
#pragma once
#include <gtk/gtk.h>

GMenuModel *luma_native_app_updates_menu(GtkApplication *application, GMenuModel *menu);
char *luma_native_app_updates_parse_identity(const char *text, gsize size);
GObject *luma_native_app_updates_new(GtkApplication *application, const char *app_id);
const char *luma_native_app_updates_state(GObject *updates);
void luma_native_app_updates_close(GObject *updates);
