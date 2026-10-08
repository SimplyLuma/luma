/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <gtk/gtk.h>
G_BEGIN_DECLS
#define LUMA_TYPE_WELCOME_VIEW (luma_welcome_view_get_type())
G_DECLARE_FINAL_TYPE(LumaWelcomeView, luma_welcome_view, LUMA, WELCOME_VIEW, GtkWidget)
GtkWidget *luma_welcome_view_new(const char *title, const char *icon_name,
                                const char *description, const char *noun,
                                const char *settings_schema);
/**
 * luma_welcome_view_get_new_button:
 * @self: the welcome view
 * Returns: (transfer none): button to bind to the existing New command
 */
GtkWidget *luma_welcome_view_get_new_button(LumaWelcomeView *self);
/**
 * luma_welcome_view_get_open_button:
 * @self: the welcome view
 * Returns: (transfer none): button to bind to the existing Open command
 */
GtkWidget *luma_welcome_view_get_open_button(LumaWelcomeView *self);
gboolean luma_welcome_view_should_show(const char *settings_schema);
G_END_DECLS
