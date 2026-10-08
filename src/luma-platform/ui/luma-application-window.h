/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <adwaita.h>

#include "luma-context.h"

G_BEGIN_DECLS

#define LUMA_TYPE_APPLICATION_WINDOW (luma_application_window_get_type())
G_DECLARE_FINAL_TYPE(LumaApplicationWindow, luma_application_window, LUMA,
                     APPLICATION_WINDOW, AdwApplicationWindow)

GtkWidget *luma_application_window_new(GtkApplication *application,
                                       LumaContext *context);
/* Match Python AppWindow: a bleeding page owns the phone status inset itself.
 * Both have no visual effect on a desktop, regardless of window width. */
void luma_application_window_set_phone_bleed(LumaApplicationWindow *self, gboolean bleed);
int luma_application_window_get_status_inset(LumaApplicationWindow *self);
void luma_application_window_set_body(LumaApplicationWindow *self,
                                      GtkWidget *body);

/**
 * luma_application_window_get_body:
 * @self: an application window
 *
 * Returns: (transfer none) (nullable): the application body
 */
GtkWidget *luma_application_window_get_body(LumaApplicationWindow *self);
/* C1 AppWindow composition: place the frame sidebar beside the content
 * island, while retaining the native title row, window layer host and gutter. */
void luma_application_window_set_frame(LumaApplicationWindow *self, GtkWidget *sidebar, GtkWidget *island);
/* Adopt the frame of an existing AdwApplicationWindow subclass. Its content,
 * actions and template children stay in place. The first top AdwHeaderBar is
 * dressed as the Luma title row; if absent, a toolbar view is added around
 * the existing content. Returns the title row, borrowed from the window. */
GtkWidget *luma_application_window_frame_adopt(AdwApplicationWindow *window);
/* Move a pre-existing start action behind the kit identity, or pack a new
 * unparented action there. The widget and its signals remain unchanged. */
void luma_application_window_frame_set_leading(AdwApplicationWindow *window,
                                               GtkWidget *widget);
/* Move an adopted frame's leading control into @phone_host below 640 px,
 * returning it to the title row when the window grows. */
void luma_application_window_frame_set_leading_phone_host(AdwApplicationWindow *window,
                                                           GtkWidget *widget,
                                                           GtkWidget *phone_host);
void luma_application_window_frame_set_menu_model(AdwApplicationWindow *window,
                                                  GMenuModel *menu_model);
/* Named title-row slots; a new widget replaces the previous one, NULL clears. */
void luma_application_window_set_leading(LumaApplicationWindow *self, GtkWidget *widget);
void luma_application_window_set_title_content(LumaApplicationWindow *self, GtkWidget *widget, gboolean centred);
void luma_application_window_set_trailing(LumaApplicationWindow *self, GtkWidget *widget);
/**
 * luma_application_window_get_layer_host:
 * @self: a native application window
 *
 * Returns: (transfer none): the window's shared overlay host
 */
GtkWidget *luma_application_window_get_layer_host(LumaApplicationWindow *self);
/**
 * luma_application_window_get_title_bar:
 * @self: a native application window
 *
 * Returns: (transfer none): the title row for semantic title actions
 */
GtkWidget *luma_application_window_get_title_bar(LumaApplicationWindow *self);
void luma_application_window_set_identity(LumaApplicationWindow *self,
                                          const char *title,
                                          const char *subtitle,
                                          const char *icon_name);
void luma_application_window_set_menu_model(LumaApplicationWindow *self,
                                            GMenuModel *menu_model);

G_END_DECLS
