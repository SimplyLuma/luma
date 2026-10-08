/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-ui.h"
#include "luma-empty-state-tokens.h"

/* AdwStatusPage does not expose its text clamp. Keep the integration in this
 * versioned shared adapter, and test the native subtree at package time. */
static AdwClamp *find_clamp(GtkWidget *widget) {
  if (ADW_IS_CLAMP(widget)) return ADW_CLAMP(widget);
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child;
       child = gtk_widget_get_next_sibling(child)) {
    AdwClamp *clamp = find_clamp(child);
    if (clamp) return clamp;
  }
  return NULL;
}

void luma_empty_state_configure(AdwStatusPage *page, gboolean compact) {
  g_return_if_fail(ADW_IS_STATUS_PAGE(page));
  luma_init();
  gtk_widget_add_css_class(GTK_WIDGET(page), "luma-native-empty");
  if (compact) gtk_widget_add_css_class(GTK_WIDGET(page), "compact");
  else gtk_widget_remove_css_class(GTK_WIDGET(page), "compact");
  AdwClamp *clamp = find_clamp(GTK_WIDGET(page));
  g_return_if_fail(clamp != NULL);
  int width = compact ? LUMA_EMPTY_COMPACT_MAX_WIDTH : LUMA_EMPTY_MAX_WIDTH;
  adw_clamp_set_maximum_size(clamp, width);
  adw_clamp_set_tightening_threshold(clamp, width);
}
