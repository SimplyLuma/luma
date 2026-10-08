/* Test only: replace the application routing base with its real Adw parent.
 * This exercises the exact panel widgets/bindings; full Settings launch remains
 * a separate packaged-session gate. No guest dconf is read or written. */
#include "cc-dash-panel.c"
GType cc_panel_get_type (void) { return ADW_TYPE_NAVIGATION_PAGE; }
int main (void)
{
  gtk_init ();
  adw_init ();
  CcDashPanel *panel = g_object_ref_sink (g_object_new (CC_TYPE_DASH_PANEL, NULL));
  g_settings_set_string (panel->settings, "shelf-surface-mode", "connected");
  g_assert_false (gtk_widget_get_visible (GTK_WIDGET (panel->span_row)));
  g_settings_set_string (panel->settings, "shelf-surface-mode", "separate");
  g_assert_true (gtk_widget_get_visible (GTK_WIDGET (panel->span_row)));
  g_assert_false (gtk_widget_get_sensitive (GTK_WIDGET (panel->float_row)));
  g_assert_true (g_settings_get_boolean (panel->settings, "shelf-float-ends"));
  for (guint i = 0; i < 4; i++)
    {
      gtk_check_button_set_active (panel->positions[i], TRUE);
      g_autofree char *edge = g_settings_get_string (panel->settings, "shelf-edge");
      g_assert_cmpstr (edge, ==, edges[i]);
    }
  g_settings_set_string (panel->settings, "shelf-edge", "bottom");
  g_assert_true (gtk_check_button_get_active (panel->positions[0]));
  g_settings_set_boolean (panel->settings, "shelf-span-full", TRUE);
  g_assert_true (gtk_widget_get_sensitive (GTK_WIDGET (panel->float_row)));
  adw_switch_row_set_active (panel->float_row, FALSE);
  g_assert_false (g_settings_get_boolean (panel->settings, "shelf-float-ends"));
  g_settings_set_boolean (panel->settings, "shelf-span-full", FALSE);
  g_assert_false (gtk_widget_get_sensitive (GTK_WIDGET (panel->float_row)));
  g_assert_false (g_settings_get_boolean (panel->settings, "shelf-float-ends"));
  for (guint i = 0; i < 4; i++)
    for (guint shape = 0; shape < 16; shape++)
      {
        g_settings_set_string (panel->settings, "shelf-edge", edges[i]);
        g_settings_set_string (panel->settings, "shelf-edge-mode", shape & 1 ? "protruding" : "floating");
        g_settings_set_boolean (panel->settings, "shelf-span-full", !!(shape & 2));
        g_settings_set_boolean (panel->settings, "shelf-float-ends", !!(shape & 4));
        g_settings_set_string (panel->settings, "shelf-surface-mode", shape & 8 ? "separate" : "connected");
        cairo_surface_t *surface = cairo_image_surface_create (CAIRO_FORMAT_ARGB32, 360, 190);
        cairo_t *cr = cairo_create (surface);
        preview_draw (GTK_DRAWING_AREA (panel->preview), cr, 360, 190, panel);
        g_assert_cmpint (cairo_status (cr), ==, CAIRO_STATUS_SUCCESS);
        cairo_destroy (cr);
        cairo_surface_destroy (surface);
      }
  g_object_unref (panel);
  g_print ("PASS native panel: radio and external settings sync, dependent sensitivity, retained disabled value, 64 Cairo previews; routing base isolated\n");
  return 0;
}
