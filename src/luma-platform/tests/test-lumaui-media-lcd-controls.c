/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-media-transport-lcd.h"
static void changed(LumaMediaTransportLcd *self G_GNUC_UNUSED, gboolean value, gpointer data) { g_assert_true(value); (*(int*)data)++; }
static void seek(LumaMediaTransportLcd *self G_GNUC_UNUSED, gpointer data) { (*(int*)data)++; }
static void readout(LumaMediaTransportLcd *self G_GNUC_UNUSED, const char *key, gpointer data) { g_assert_nonnull(key); (*(int*)data)++; }
static void controls(void) {
  LumaMediaTransportLcd *lcd=LUMA_MEDIA_TRANSPORT_LCD(g_object_ref_sink(luma_media_transport_lcd_new()));
  int calls=0;
  g_signal_connect(lcd,"play-changed",G_CALLBACK(changed),&calls);
  g_signal_connect(lcd,"record-changed",G_CALLBACK(changed),&calls);
  g_signal_connect(lcd,"loop-changed",G_CALLBACK(changed),&calls);
  g_signal_connect(lcd,"seek-start",G_CALLBACK(seek),&calls);
  g_signal_connect(lcd,"readout-activated",G_CALLBACK(readout),&calls);
  const char *keys[]={"start","play","record","loop","tempo","signature","key"};
  for(guint i=0;i<G_N_ELEMENTS(keys);i++) {
    GtkWidget *control=luma_media_transport_lcd_get_control(lcd,keys[i]);
    g_assert_true(GTK_IS_BUTTON(control));
    g_assert_true(control==luma_media_transport_lcd_get_control(lcd,keys[i]));
    g_signal_emit_by_name(control,"clicked");
  }
  g_assert_cmpint(calls,==,7);
  GtkWidget *dot=gtk_button_get_child(GTK_BUTTON(luma_media_transport_lcd_get_control(lcd,"record")));
  g_assert_cmpint(gtk_widget_get_halign(dot),==,GTK_ALIGN_CENTER);
  g_assert_cmpint(gtk_widget_get_valign(dot),==,GTK_ALIGN_CENTER);
  g_assert_false(gtk_widget_get_can_target(dot));
  g_assert_null(luma_media_transport_lcd_get_control(lcd,"missing"));
  g_assert_null(luma_media_transport_lcd_get_control(lcd,NULL));
  GtkWidget *key=luma_media_transport_lcd_get_control(lcd,"key");
  gtk_widget_set_sensitive(key,FALSE);g_assert_false(gtk_widget_is_sensitive(key));
  g_object_unref(lcd);
}
static void readout_value(void) {
  GtkWidget *button=g_object_ref_sink(luma_media_readout_new("95","BPM"));
  GtkWidget *stack=gtk_button_get_child(GTK_BUTTON(button));
  GtkWidget *value=gtk_widget_get_first_child(stack);
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(value)),==,"95");
  luma_media_readout_set_value(button,"120");
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(value)),==,"120");
  g_assert_cmpstr(gtk_label_get_text(GTK_LABEL(gtk_widget_get_last_child(stack))),==,"BPM");
  luma_media_readout_set_chip(button,TRUE);g_assert_true(gtk_widget_has_css_class(button,"chip"));
  luma_media_readout_set_chip(button,FALSE);g_assert_false(gtk_widget_has_css_class(button,"chip"));
  g_object_unref(button);
}
int main(int argc,char**argv) {g_test_init(&argc,&argv,NULL);if(!gtk_init_check())return 77;luma_ui_install();g_test_add_func("/lumaui/media-lcd/controls",controls);g_test_add_func("/lumaui/media-lcd/readout",readout_value);return g_test_run();}
