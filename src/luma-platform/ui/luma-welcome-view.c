/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-welcome-view.h"
#include <math.h>

struct _LumaWelcomeView {
  GtkWidget parent_instance;
  GtkWidget *panel, *identity, *words, *description, *actions, *checkbox;
  GtkWidget *new_button, *open_button;
  GdkTexture *photograph;
  GSettings *settings;
  gboolean compact;
};
G_DEFINE_FINAL_TYPE(LumaWelcomeView, luma_welcome_view, GTK_TYPE_WIDGET)

static GSettings *settings_for(const char *name) {
  if (!name || !*name) return NULL;
  GSettingsSchemaSource *source = g_settings_schema_source_get_default();
  if (!source) return NULL;
  GSettingsSchema *schema = g_settings_schema_source_lookup(source, name, TRUE);
  if (!schema) return NULL;
  GSettings *settings = g_settings_schema_has_key(schema, "show-welcome")
      ? g_settings_new_full(schema, NULL, NULL) : NULL;
  g_settings_schema_unref(schema);
  return settings;
}
gboolean luma_welcome_view_should_show(const char *name) {
  GSettings *settings = settings_for(name);
  gboolean result = !settings || g_settings_get_boolean(settings, "show-welcome");
  g_clear_object(&settings);
  return result;
}
static void dispose(GObject *object) {
  LumaWelcomeView *self = LUMA_WELCOME_VIEW(object);
  if (self->panel) { gtk_widget_unparent(self->panel); self->panel = NULL; }
  g_clear_object(&self->photograph);
  g_clear_object(&self->settings);
  G_OBJECT_CLASS(luma_welcome_view_parent_class)->dispose(object);
}
static void measure(GtkWidget *widget, GtkOrientation orientation, int for_size,
                    int *minimum, int *natural, int *min_baseline, int *nat_baseline) {
  (void)widget; (void)for_size;
  *minimum = orientation == GTK_ORIENTATION_HORIZONTAL ? 320 : 360;
  *natural = orientation == GTK_ORIENTATION_HORIZONTAL ? 882 : 500;
  *min_baseline = *nat_baseline = -1;
}
static void allocate(GtkWidget *widget, int width, int height, int baseline) {
  LumaWelcomeView *self = LUMA_WELCOME_VIEW(widget);
  gboolean compact = width < 640;
  if (self->compact != compact) {
    self->compact = compact;
    gtk_orientable_set_orientation(GTK_ORIENTABLE(self->identity),
        compact ? GTK_ORIENTATION_VERTICAL : GTK_ORIENTATION_HORIZONTAL);
    gtk_label_set_justify(GTK_LABEL(self->description), compact ? GTK_JUSTIFY_CENTER : GTK_JUSTIFY_LEFT);
    gtk_label_set_xalign(GTK_LABEL(self->description), compact ? .5 : 0);
    gtk_widget_set_halign(self->identity, compact ? GTK_ALIGN_CENTER : GTK_ALIGN_START);
    gtk_widget_set_halign(self->actions, compact ? GTK_ALIGN_CENTER : GTK_ALIGN_START);
    gtk_widget_set_halign(self->checkbox, compact ? GTK_ALIGN_CENTER : GTK_ALIGN_START);
    for (GtkWidget *child = gtk_widget_get_first_child(self->words); child;
         child = gtk_widget_get_next_sibling(child))
      gtk_label_set_xalign(GTK_LABEL(child), compact ? .5 : 0);
    if (compact) gtk_widget_add_css_class(widget, "compact");
    else gtk_widget_remove_css_class(widget, "compact");
  }
  const int padding = compact ? 24 : 32;
  int panel_width = compact ? width : (int)round(width * .44);
  int x = (!compact && gtk_widget_get_direction(widget) == GTK_TEXT_DIR_RTL)
      ? width - panel_width + padding : padding;
  graphene_point_t point = GRAPHENE_POINT_INIT(x, padding);
  gtk_widget_allocate(self->panel, MAX(1, panel_width - 2 * padding),
      MAX(1, height - 2 * padding), baseline, gsk_transform_translate(NULL, &point));
}
static void snapshot(GtkWidget *widget, GtkSnapshot *snapshot) {
  LumaWelcomeView *self = LUMA_WELCOME_VIEW(widget);
  float w = gtk_widget_get_width(widget), h = gtk_widget_get_height(widget);
  graphene_rect_t whole = GRAPHENE_RECT_INIT(0, 0, w, h);
  GskRoundedRect rounded;
  gsk_rounded_rect_init_from_rect(&rounded, &whole, 10);
  gtk_snapshot_push_rounded_clip(snapshot, &rounded);
  const GdkRGBA panel = {25/255., 28/255., 33/255., 1};
  gtk_snapshot_append_color(snapshot, &panel, &whole);
  gboolean rtl = gtk_widget_get_direction(widget) == GTK_TEXT_DIR_RTL;
  float panel_width = self->compact ? 0 : roundf(w * .44f);
  float photo_x = rtl ? 0 : panel_width;
  float photo_width = w - panel_width;
  graphene_rect_t photo = GRAPHENE_RECT_INIT(photo_x, 0, photo_width, h);
  if (self->photograph) {
    float tw = gdk_texture_get_width(self->photograph), th = gdk_texture_get_height(self->photograph);
    float scale = MAX(photo_width / tw, h / th);
    graphene_rect_t destination = GRAPHENE_RECT_INIT(photo_x + (photo_width - tw * scale) / 2,
        (h - th * scale) * .32f, tw * scale, th * scale);
    gtk_snapshot_push_clip(snapshot, &photo);
    gtk_snapshot_append_texture(snapshot, self->photograph, &destination);
    gtk_snapshot_pop(snapshot);
  }
  if (self->compact) {
    // Fixed dark scrim preserves contrast in both system themes, with no animation.
    const GdkRGBA scrim = {16/255., 19/255., 23/255., .82};
    gtk_snapshot_append_color(snapshot, &scrim, &whole);
  } else {
    graphene_point_t start = GRAPHENE_POINT_INIT(rtl ? photo_width : panel_width, 0);
    graphene_point_t end = GRAPHENE_POINT_INIT(start.x + (rtl ? -1 : 1) * photo_width * .34f, 0);
    GskColorStop stops[] = {{0, panel}, {1, {25/255.,28/255.,33/255.,0}}};
    gtk_snapshot_append_linear_gradient(snapshot, &photo, &start, &end, stops, 2);
  }
  gtk_widget_snapshot_child(widget, self->panel, snapshot);
  gtk_snapshot_pop(snapshot);
}
static void luma_welcome_view_class_init(LumaWelcomeViewClass *klass) {
  GObjectClass *object = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget = GTK_WIDGET_CLASS(klass);
  object->dispose = dispose;
  widget->measure = measure; widget->size_allocate = allocate; widget->snapshot = snapshot;
  gtk_widget_class_set_css_name(widget, "lumawelcomeview");
  gtk_widget_class_set_accessible_role(widget, GTK_ACCESSIBLE_ROLE_GROUP);
}
static void luma_welcome_view_init(LumaWelcomeView *self) {
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-creative-welcome");
  gtk_widget_set_hexpand(GTK_WIDGET(self), TRUE);
  gtk_widget_set_vexpand(GTK_WIDGET(self), TRUE);
  self->photograph = gdk_texture_new_from_resource("/org/projectluma/platform/luma-creatives.png");
}
static GtkWidget *text(const char *value, const char *style) {
  GtkWidget *label = gtk_label_new(value);
  gtk_label_set_xalign(GTK_LABEL(label), 0);
  gtk_widget_add_css_class(label, style);
  return label;
}
static GtkWidget *button(const char *name, const char *symbol, const char *style) {
  GtkWidget *button = gtk_button_new();
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 9);
  gtk_box_append(GTK_BOX(box), gtk_image_new_from_icon_name(symbol));
  gtk_box_append(GTK_BOX(box), gtk_label_new(name));
  gtk_button_set_child(GTK_BUTTON(button), box);
  gtk_widget_add_css_class(button, style);
  gtk_accessible_update_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_LABEL, name, -1);
  return button;
}
GtkWidget *luma_welcome_view_new(const char *title, const char *icon_name,
                                const char *description, const char *noun,
                                const char *schema) {
  LumaWelcomeView *self = g_object_new(LUMA_TYPE_WELCOME_VIEW, NULL);
  self->panel = gtk_center_box_new();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self->panel), GTK_ORIENTATION_VERTICAL);
  gtk_widget_set_parent(self->panel, GTK_WIDGET(self));
  GtkWidget *group = gtk_box_new(GTK_ORIENTATION_VERTICAL, 28);
  self->identity = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 14);
  GtkWidget *icon = gtk_image_new_from_icon_name(icon_name);
  gtk_image_set_pixel_size(GTK_IMAGE(icon), 56);
  gtk_widget_add_css_class(icon, "creative-icon");
  gtk_box_append(GTK_BOX(self->identity), icon);
  self->words = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
  gtk_widget_set_valign(self->words, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(self->words), text("CREATIVE COLLECTION", "creative-eyebrow"));
  gtk_box_append(GTK_BOX(self->words), text(title, "creative-title"));
  gtk_box_append(GTK_BOX(self->identity), self->words);
  gtk_box_append(GTK_BOX(group), self->identity);
  self->description = text(description, "creative-description");
  gtk_label_set_wrap(GTK_LABEL(self->description), TRUE);
  gtk_label_set_wrap_mode(GTK_LABEL(self->description), PANGO_WRAP_WORD_CHAR);
  gtk_box_append(GTK_BOX(group), self->description);
  self->actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
  char *new_label = g_strdup_printf("New %s", noun);
  self->new_button = button(new_label, "list-add-symbolic", "creative-primary");
  g_free(new_label);
  self->open_button = button("Open…", "document-open-symbolic", "creative-secondary");
  gtk_box_append(GTK_BOX(self->actions), self->new_button);
  gtk_box_append(GTK_BOX(self->actions), self->open_button);
  gtk_widget_set_halign(self->actions, GTK_ALIGN_START);
  gtk_box_append(GTK_BOX(group), self->actions);
  gtk_center_box_set_center_widget(GTK_CENTER_BOX(self->panel), group);
  char *preference = g_strdup_printf("Show this when %s opens", title);
  self->checkbox = gtk_check_button_new_with_label(preference);
  g_free(preference);
  gtk_widget_set_halign(self->checkbox, GTK_ALIGN_START);
  gtk_check_button_set_active(GTK_CHECK_BUTTON(self->checkbox), TRUE);
  self->settings = settings_for(schema);
  if (self->settings) g_settings_bind(self->settings, "show-welcome", self->checkbox, "active", G_SETTINGS_BIND_DEFAULT);
  else gtk_widget_set_sensitive(self->checkbox, FALSE);
  gtk_center_box_set_end_widget(GTK_CENTER_BOX(self->panel), self->checkbox);
  gtk_accessible_update_property(GTK_ACCESSIBLE(self), GTK_ACCESSIBLE_PROPERTY_LABEL, title, -1);
  return GTK_WIDGET(self);
}
GtkWidget *luma_welcome_view_get_new_button(LumaWelcomeView *self) { return self->new_button; }
GtkWidget *luma_welcome_view_get_open_button(LumaWelcomeView *self) { return self->open_button; }
