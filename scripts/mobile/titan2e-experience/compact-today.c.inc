/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Keep the Titan pocket/fold sizing contract with the shared native cards. */
void
phosh_luma_today_set_compact (PhoshLumaToday *self, gboolean compact)
{
  GList *cards;
  g_return_if_fail (PHOSH_IS_LUMA_TODAY (self));
  cards = gtk_container_get_children (GTK_CONTAINER (self));
  for (GList *p = cards; p; p = p->next) {
    GtkWidget *card = p->data;
    GtkWidget *box = gtk_bin_get_child (GTK_BIN (card));
    GList *children = gtk_container_get_children (GTK_CONTAINER (box));
    gboolean calendar = gtk_style_context_has_class (gtk_widget_get_style_context (card), "luma-today-calendar");
    gtk_widget_set_size_request (card, 0, compact ? 114 : 158);
    for (GList *q = children; q; q = q->next) {
      GtkWidget *child = q->data;
      GtkStyleContext *style = gtk_widget_get_style_context (child);
      if (gtk_style_context_has_class (style, "luma-today-detail")) {
        gtk_widget_set_no_show_all (child, compact);
        gtk_widget_set_visible (child, !compact);
      }
      if (calendar && GTK_IS_LABEL (child) && gtk_style_context_has_class (style, "luma-today-condition"))
        gtk_label_set_lines (GTK_LABEL (child), compact ? 1 : 2);
    }
    g_list_free (children);
  }
  g_list_free (cards);
}
