/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-action-center.h"
static gboolean finish(gpointer data){*(gboolean*)data=TRUE;return G_SOURCE_REMOVE;}
static void settle(void){gboolean done=FALSE;g_timeout_add(180,finish,&done);while(!done)g_main_context_iteration(NULL,TRUE);}
static void identity(void){
  const int widths[]={360,500,1024,1180};
  for(guint n=0;n<G_N_ELEMENTS(widths);n++){
    GtkWidget *window=gtk_window_new();gtk_window_set_default_size(GTK_WINDOW(window),widths[n],740);
    LumaBarItem *item=luma_bar_item_new_chip("Long application name","app-window",FALSE);
    luma_bar_item_chip_set_identity(item,TRUE,"Installed with Luma");
    GtkWidget *control=luma_bar_item_create_control(item,"bar");
    gtk_window_set_child(GTK_WINDOW(window),control);gtk_window_present(GTK_WINDOW(window));settle();
    g_assert_true(gtk_widget_get_mapped(control));
    g_assert_true(gtk_widget_has_css_class(control,"lumaui-bar-identity"));
    GtkWidget *copy=gtk_widget_get_last_child(control),*title=gtk_widget_get_first_child(copy),*detail=gtk_widget_get_last_child(copy);
    graphene_rect_t a,b;g_assert_true(gtk_widget_compute_bounds(title,control,&a));g_assert_true(gtk_widget_compute_bounds(detail,control,&b));
    g_assert_cmpfloat(a.origin.y,<,b.origin.y);g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(detail)),==,"Installed with Luma");
    gtk_window_destroy(GTK_WINDOW(window));g_object_unref(item);settle();
  }
  LumaBarItem *normal=luma_bar_item_new_chip("Document",NULL,FALSE);GtkWidget *widget=luma_bar_item_create_control(normal,"bar");
  g_object_ref_sink(widget);g_assert_true(gtk_widget_has_css_class(widget,"lumaui-bar-chip"));g_object_unref(widget);g_object_unref(normal);
}
int main(int argc,char**argv){g_test_init(&argc,&argv,NULL);if(!gtk_init_check())return 77;luma_ui_install();g_test_add_func("/lumaui/bar/identity",identity);return g_test_run();}
