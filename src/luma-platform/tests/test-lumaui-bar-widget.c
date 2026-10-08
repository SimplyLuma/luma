/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-action-center.h"
static gboolean finish(gpointer data){*(gboolean*)data=TRUE;return G_SOURCE_REMOVE;}
static void settle(void){gboolean done=FALSE;g_timeout_add(150,finish,&done);while(!done)g_main_context_iteration(NULL,TRUE);}
static void activated(GtkButton *button G_GNUC_UNUSED,gpointer data){(*(int*)data)++;}
static void released(gpointer data,GObject *object G_GNUC_UNUSED){*(gboolean*)data=TRUE;}
static void retention(void){
  const int widths[]={360,402,720,1180};
  for(guint n=0;n<G_N_ELEMENTS(widths);n++){
    GtkWidget *window=gtk_window_new(),*content=gtk_box_new(GTK_ORIENTATION_VERTICAL,0);
    gtk_window_set_child(GTK_WINDOW(window),content);gtk_window_set_default_size(GTK_WINDOW(window),widths[n],740);
    LumaActionCenter *center=LUMA_ACTION_CENTER(luma_action_center_new(NULL));luma_action_center_attach(center,content);
    GtkWidget *control=gtk_button_new_with_label("Play");int calls=0;gboolean gone=FALSE;
    g_signal_connect(control,"clicked",G_CALLBACK(activated),&calls);g_object_weak_ref(G_OBJECT(control),released,&gone);
    LumaBarItem *item=luma_bar_item_new_widget(control);LumaBarItem *items[]={item};
    for(int i=0;i<3;i++){
      luma_action_center_show_bar(center,items,1,NULL);gtk_window_present(GTK_WINDOW(window));settle();
      g_assert_true(luma_action_center_get_bar_control(center,0)==control);
      g_assert_true(gtk_widget_get_mapped(control));g_signal_emit_by_name(control,"clicked");
      luma_action_center_grow_panel(center,"panel",gtk_label_new("Settings"));settle();luma_action_center_fold(center);
    }
    g_assert_cmpint(calls,==,3);g_object_unref(item);g_assert_false(gone);
    gtk_window_destroy(GTK_WINDOW(window));settle();g_assert_true(gone);
  }
}
int main(int argc,char**argv){g_test_init(&argc,&argv,NULL);if(!gtk_init_check())return 77;luma_ui_install();g_test_add_func("/lumaui/bar-widget/retention",retention);return g_test_run();}
