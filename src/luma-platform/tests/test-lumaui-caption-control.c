/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-action-center.h"
static gboolean finish(gpointer data) { *(gboolean*)data=TRUE; return G_SOURCE_REMOVE; }
static void settle(void) { gboolean done=FALSE;g_timeout_add(200,finish,&done);while(!done)g_main_context_iteration(NULL,TRUE); }
static void activated(LumaBarItem *item G_GNUC_UNUSED,gpointer data) { (*(int*)data)++; }
static void caption_controls(void) {
  const int widths[]={360,402,720,1180};
  for(guint n=0;n<G_N_ELEMENTS(widths);n++) {
    GtkWidget *window=gtk_window_new(),*row=gtk_box_new(GTK_ORIENTATION_HORIZONTAL,2);
    gtk_widget_set_valign(row,GTK_ALIGN_END);
    gtk_widget_set_margin_start(row,16);gtk_widget_set_margin_end(row,16);
    gtk_box_set_homogeneous(GTK_BOX(row),TRUE);
    gtk_window_set_child(GTK_WINDOW(window),row);gtk_window_set_default_size(GTK_WINDOW(window),widths[n],300);
    int calls=0;GtkWidget *keys[4];
    for(int i=0;i<4;i++) {
      LumaBarItem *item=luma_bar_item_new_action("copy","Duplicate",NULL);
      g_signal_connect(item,"activated",G_CALLBACK(activated),&calls);
      keys[i]=luma_bar_item_create_control(item,"caption");gtk_box_append(GTK_BOX(row),keys[i]);g_object_unref(item);
    }
    gtk_window_present(GTK_WINDOW(window));settle();
    for(int i=0;i<4;i++) {
      GtkWidget *content=gtk_button_get_child(GTK_BUTTON(keys[i]));
      GtkWidget *icon=gtk_widget_get_first_child(content),*label=gtk_widget_get_last_child(content);
      g_assert_cmpint(gtk_orientable_get_orientation(GTK_ORIENTABLE(content)),==,GTK_ORIENTATION_VERTICAL);
      g_assert_cmpint(gtk_widget_get_height(keys[i]),==,52);
      g_assert_true(gtk_widget_get_mapped(label));
      graphene_rect_t ib,lb;g_assert_true(gtk_widget_compute_bounds(icon,keys[i],&ib));g_assert_true(gtk_widget_compute_bounds(label,keys[i],&lb));
      g_assert_cmpfloat(lb.origin.y,>=,ib.origin.y+ib.size.height);
      g_assert_cmpfloat(lb.origin.y+lb.size.height,<=,52);
      g_signal_emit_by_name(keys[i],"clicked");
    }
    g_assert_cmpint(calls,==,4);gtk_window_destroy(GTK_WINDOW(window));
  }
}
static void live_action_state(void) {
  LumaBarItem *item=luma_bar_item_new_action("repeat","Loop",NULL);
  GtkWidget *control=g_object_ref_sink(luma_bar_item_create_control(item,"caption"));
  luma_bar_item_set_active(item,TRUE);
  g_assert_true(gtk_widget_has_css_class(control,"on"));
  gtk_test_accessible_assert_state(control,GTK_ACCESSIBLE_STATE_PRESSED,GTK_ACCESSIBLE_TRISTATE_TRUE);
  luma_bar_item_set_active(item,FALSE);
  g_assert_false(gtk_widget_has_css_class(control,"on"));
  gtk_test_accessible_assert_state(control,GTK_ACCESSIBLE_STATE_PRESSED,GTK_ACCESSIBLE_TRISTATE_FALSE);
  luma_bar_item_set_sensitive(item,FALSE);g_assert_false(gtk_widget_is_sensitive(control));
  luma_bar_item_set_sensitive(item,TRUE);g_assert_true(gtk_widget_is_sensitive(control));
  g_object_unref(control);
  // A control can disappear while its reusable item survives.
  luma_bar_item_set_active(item,TRUE);luma_bar_item_set_sensitive(item,FALSE);
  control=g_object_ref_sink(luma_bar_item_create_control(item,"caption"));
  g_assert_true(gtk_widget_has_css_class(control,"on"));g_assert_false(gtk_widget_is_sensitive(control));
  g_object_unref(control);g_object_unref(item);
}
int main(int argc,char**argv) {g_test_init(&argc,&argv,NULL);if(!gtk_init_check())return 77;luma_ui_install();g_test_add_func("/lumaui/caption-control/all-widths",caption_controls);g_test_add_func("/lumaui/caption-control/live-state",live_action_state);return g_test_run();}
