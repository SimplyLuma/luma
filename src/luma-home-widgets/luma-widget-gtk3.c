/* SPDX-License-Identifier: GPL-3.0-or-later */
/* GTK host extracted from Mobile's PhoshLumaToday. The model owns all data,
 * dates, freshness, unit conversion, monitors and calendar subscriptions. */
#include "luma-widget-gtk3.h"
#include <json-glib/json-glib.h>
struct _LumaWidgetsGtk3 {
  GtkButton parent_instance;
  LumaWidgetsModel *model;
  char *id;
  char *desktop_id;
  GtkWidget *labels[4];
  gulong changed;
};
G_DEFINE_TYPE (LumaWidgetsGtk3, luma_widgets_gtk3, GTK_TYPE_BUTTON)
static guint launch_requested;
static void
render (LumaWidgetsModel *model, LumaWidgetsGtk3 *self)
{
  g_autofree char *snapshot = luma_widgets_model_dup_snapshot (model);
  g_autoptr (JsonParser) parser = json_parser_new ();
  if (!json_parser_load_from_data (parser, snapshot, -1, NULL)) return;
  JsonObject *root = json_node_get_object (json_parser_get_root (parser));
  JsonObject *data = json_object_get_object_member (root, self->id);
  atk_object_set_name (gtk_widget_get_accessible (GTK_WIDGET (self)), json_object_get_string_member (data, "name"));
  const char *fields[] = {"title", "value", "description", "detail"};
  g_autofree char *spoken = g_strdup_printf ("%s, %s, %s, %s",
    json_object_get_string_member (data, "title"), json_object_get_string_member (data, "value"),
    json_object_get_string_member (data, "description"), json_object_get_string_member (data, "detail"));
  atk_object_set_name (gtk_widget_get_accessible (GTK_WIDGET (self)), spoken);
  for (guint i = 0; i < 4; i++)
    gtk_label_set_text (GTK_LABEL (self->labels[i]), json_object_get_string_member (data, fields[i]));
  g_free (self->desktop_id);
  self->desktop_id = g_strdup (json_object_get_string_member (data, "desktop_id"));
  if (json_object_has_member (data, "unit")) {
    const char *unit = json_object_get_string_member (data, "unit");
    gtk_widget_set_tooltip_text (self->labels[1], unit);
    atk_object_set_description (gtk_widget_get_accessible (self->labels[1]), unit);
  }
}
static void
clicked (GtkButton *button)
{
  LumaWidgetsGtk3 *self = LUMA_WIDGETS_GTK3 (button);
  g_signal_emit (self, launch_requested, 0, self->desktop_id);
}
static void
dispose (GObject *object)
{
  LumaWidgetsGtk3 *self = LUMA_WIDGETS_GTK3 (object);
  if (self->changed) { g_signal_handler_disconnect (self->model, self->changed); self->changed = 0; }
  g_clear_object (&self->model);
  G_OBJECT_CLASS (luma_widgets_gtk3_parent_class)->dispose (object);
}
static void
finalize (GObject *object)
{
  LumaWidgetsGtk3 *self = LUMA_WIDGETS_GTK3 (object);
  g_free (self->id); g_free (self->desktop_id);
  G_OBJECT_CLASS (luma_widgets_gtk3_parent_class)->finalize (object);
}
static void
luma_widgets_gtk3_class_init (LumaWidgetsGtk3Class *klass)
{
  GObjectClass *object = G_OBJECT_CLASS (klass);
  object->dispose = dispose; object->finalize = finalize;
  GTK_BUTTON_CLASS (klass)->clicked = clicked;
  launch_requested = g_signal_new ("launch-requested", G_TYPE_FROM_CLASS (klass),
    G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
}
static void
luma_widgets_gtk3_init (LumaWidgetsGtk3 *self)
{
  static gboolean styles_loaded = FALSE;
  if (!styles_loaded && gdk_screen_get_default ()) {
    GtkCssProvider *provider = gtk_css_provider_new ();
    gtk_css_provider_load_from_resource (provider, "/org/project_luma/widgets/widget-gtk3.css");
    gtk_style_context_add_provider_for_screen (gdk_screen_get_default (), GTK_STYLE_PROVIDER (provider), GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 1);
    g_object_unref (provider); styles_loaded = TRUE;
  }
  gtk_style_context_add_class (gtk_widget_get_style_context (GTK_WIDGET (self)), "luma-today-card");
  gtk_widget_set_size_request (GTK_WIDGET (self), 0, 158);
  gtk_widget_set_hexpand (GTK_WIDGET (self), TRUE);
  gtk_widget_set_can_focus (GTK_WIDGET (self), TRUE);
  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_VERTICAL, 4);
  gtk_container_add (GTK_CONTAINER (self), box);
  const char *roles[] = {"luma-today-kicker", "luma-today-value", "luma-today-condition", "luma-today-detail"};
  for (guint i = 0; i < 4; i++) {
    GtkWidget *label = gtk_label_new ("");
    gtk_label_set_xalign (GTK_LABEL (label), 0);
    gtk_label_set_ellipsize (GTK_LABEL (label), PANGO_ELLIPSIZE_END);
    gtk_label_set_max_width_chars (GTK_LABEL (label), 18);
    gtk_style_context_add_class (gtk_widget_get_style_context (label), roles[i]);
    gtk_box_pack_start (GTK_BOX (box), label, FALSE, FALSE, 0);
    self->labels[i] = label;
    if (i == 1) gtk_box_pack_start (GTK_BOX (box), gtk_box_new (GTK_ORIENTATION_VERTICAL, 0), TRUE, TRUE, 0);
  }
}
GtkWidget *
luma_widgets_gtk3_new (LumaWidgetsModel *model, const char *id)
{
  g_return_val_if_fail (LUMA_WIDGETS_IS_MODEL (model), NULL);
  g_return_val_if_fail (g_strcmp0 (id, "weather") == 0 || g_strcmp0 (id, "calendar") == 0, NULL);
  LumaWidgetsGtk3 *self = g_object_new (LUMA_WIDGETS_TYPE_GTK3, NULL);
  self->model = g_object_ref (model); self->id = g_strdup (id);
  g_autofree char *css = g_strconcat ("luma-today-", id, NULL);
  gtk_style_context_add_class (gtk_widget_get_style_context (GTK_WIDGET (self)), css);
  if (g_str_equal (id, "calendar")) {
    for (guint i = 2; i < 4; i++) {
      GtkLabel *label = GTK_LABEL (self->labels[i]);
      gtk_style_context_add_class (gtk_widget_get_style_context (GTK_WIDGET (label)), "luma-today-event");
      gtk_label_set_lines (label, 2);
    }
  }
  self->changed = g_signal_connect_object (model, "changed", G_CALLBACK (render), self, 0);
  render (model, self);
  return GTK_WIDGET (self);
}
