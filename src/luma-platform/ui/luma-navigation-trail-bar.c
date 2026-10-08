/* SPDX-License-Identifier: Apache-2.0 */
/* LumaNavigationTrailBar: the twin of structure_trail.NavigationTrailBar (Python). */
#include "luma-navigation-trail-bar.h"
#include "luma-ui-private.h"

enum { BACK, N_SIGNALS };
static guint signals[N_SIGNALS];

typedef struct {
  char *text;
  char *action_name; /* NULL: plain text */
} Meta;

struct _LumaNavigationTrailBar {
  GtkBox parent_instance;
  gboolean with_title;
  GtkWidget *back;
  GtkWidget *back_glyph;
  GtkWidget *back_label;
  GtkWidget *title;
  GtkWidget *meta_box;
  GPtrArray *meta; /* Meta */
  char *back_to;
};

G_DEFINE_FINAL_TYPE(LumaNavigationTrailBar, luma_navigation_trail_bar, GTK_TYPE_BOX)

static void meta_free(gpointer data) {
  Meta *meta = data;
  g_free(meta->text);
  g_free(meta->action_name);
  g_free(meta);
}

static void fill_meta(LumaNavigationTrailBar *self) {
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(self->meta_box)) != NULL)
    gtk_box_remove(GTK_BOX(self->meta_box), child);
  guint shown = 0;
  for (guint i = 0; i < self->meta->len; i++) {
    Meta *item = g_ptr_array_index(self->meta, i);
    if (item->text == NULL || *item->text == '\0')
      continue;
    if (self->with_title || shown > 0) {
      GtkWidget *dot = g_object_new(GTK_TYPE_BOX, "valign", GTK_ALIGN_CENTER, "accessible-role",
                                    GTK_ACCESSIBLE_ROLE_PRESENTATION, NULL);
      gtk_widget_add_css_class(dot, "lumaui-trail-dot");
      gtk_box_append(GTK_BOX(self->meta_box), dot);
    }
    if (item->action_name != NULL) {
      GtkWidget *button = gtk_button_new_with_label(item->text);
      gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
      gtk_widget_add_css_class(button, "lumaui-trail-link");
      gtk_actionable_set_detailed_action_name(GTK_ACTIONABLE(button), item->action_name);
      gtk_box_append(GTK_BOX(self->meta_box), button);
    } else {
      GtkWidget *label = gtk_label_new(item->text);
      gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
      gtk_box_append(GTK_BOX(self->meta_box), label);
    }
    shown++;
  }
  gtk_widget_set_visible(self->meta_box, shown > 0);
}

static void refresh(LumaNavigationTrailBar *self) {
  gboolean rtl = gtk_widget_get_direction(GTK_WIDGET(self)) == GTK_TEXT_DIR_RTL;
  g_autofree char *glyph = luma_ui_icon_name(rtl ? "chevron-right" : "chevron-left");
  gtk_image_set_from_icon_name(GTK_IMAGE(self->back_glyph), glyph);
  gtk_widget_set_visible(self->back, self->back_to != NULL);
  if (self->back_to != NULL) {
    g_autofree char *where = g_strdup_printf("Back to %s", self->back_to);
    gtk_widget_set_tooltip_text(self->back, where);
    luma_ui_set_accessible_label(self->back, where);
    gtk_label_set_label(GTK_LABEL(self->back_label), self->back_to);
    /* GTK rounds a tracked 11.5 px label's natural width down and then
     * ellipsizes it at exactly that width; asking for its characters avoids it. */
    gtk_label_set_width_chars(GTK_LABEL(self->back_label), (int)MIN(g_utf8_strlen(self->back_to, -1), 24));
  }
  fill_meta(self);
}

static void back_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  g_signal_emit(user_data, signals[BACK], 0);
}

static void trail_direction_changed(GtkWidget *widget, GtkTextDirection previous) {
  GTK_WIDGET_CLASS(luma_navigation_trail_bar_parent_class)->direction_changed(widget, previous);
  refresh(LUMA_NAVIGATION_TRAIL_BAR(widget));
}

static void luma_navigation_trail_bar_finalize(GObject *object) {
  LumaNavigationTrailBar *self = LUMA_NAVIGATION_TRAIL_BAR(object);
  g_clear_pointer(&self->meta, g_ptr_array_unref);
  g_clear_pointer(&self->back_to, g_free);
  G_OBJECT_CLASS(luma_navigation_trail_bar_parent_class)->finalize(object);
}

static void luma_navigation_trail_bar_class_init(LumaNavigationTrailBarClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = luma_navigation_trail_bar_finalize;
  GTK_WIDGET_CLASS(klass)->direction_changed = trail_direction_changed;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_NAVIGATION);
  /**
   * LumaNavigationTrailBar::back:
   * @self: the bar
   *
   * The back chevron was pressed: the app steps its history back and calls
   * luma_navigation_trail_bar_set_place().
   */
  signals[BACK] = g_signal_new("back", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                               G_TYPE_NONE, 0);
}

static void luma_navigation_trail_bar_init(LumaNavigationTrailBar *self) {
  luma_ui_install();
  self->meta = g_ptr_array_new_with_free_func(meta_free);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-trail");
  luma_ui_set_accessible_label(GTK_WIDGET(self), "Navigation");

  self->back = gtk_button_new();
  gtk_widget_set_valign(self->back, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(self->back, "lumaui-trail-back");
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  self->back_glyph = luma_ui_icon_image("chevron-left", 0);
  gtk_box_append(GTK_BOX(line), self->back_glyph);
  self->back_label = gtk_label_new(NULL);
  gtk_label_set_ellipsize(GTK_LABEL(self->back_label), PANGO_ELLIPSIZE_END);
  gtk_label_set_max_width_chars(GTK_LABEL(self->back_label), 24);
  gtk_box_append(GTK_BOX(line), self->back_label);
  gtk_button_set_child(GTK_BUTTON(self->back), line);
  g_signal_connect(self->back, "clicked", G_CALLBACK(back_clicked), self);
  gtk_box_append(GTK_BOX(self), self->back);

  self->title = g_object_new(GTK_TYPE_LABEL, "xalign", 0.0f, "ellipsize", PANGO_ELLIPSIZE_END, "accessible-role",
                             GTK_ACCESSIBLE_ROLE_HEADING, NULL);
  gtk_widget_add_css_class(self->title, "lumaui-trail-title");
  gtk_box_append(GTK_BOX(self), self->title);

  self->meta_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_valign(self->meta_box, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(self->meta_box, "lumaui-trail-meta");
  gtk_box_append(GTK_BOX(self), self->meta_box);
}

GtkWidget *luma_navigation_trail_bar_new(gboolean with_title) {
  LumaNavigationTrailBar *self = g_object_new(LUMA_TYPE_NAVIGATION_TRAIL_BAR, NULL);
  self->with_title = !!with_title;
  if (with_title) {
    gtk_widget_add_css_class(self->back, "icon-only");
    gtk_widget_set_visible(self->back_label, FALSE);
  }
  gtk_widget_set_visible(self->title, with_title);
  refresh(self);
  return GTK_WIDGET(self);
}

void luma_navigation_trail_bar_set_place(LumaNavigationTrailBar *self, const char *back_to, const char *title) {
  g_return_if_fail(LUMA_IS_NAVIGATION_TRAIL_BAR(self));
  g_return_if_fail(title != NULL);
  g_free(self->back_to);
  self->back_to = g_strdup(back_to);
  gtk_label_set_label(GTK_LABEL(self->title), title);
  refresh(self);
}

void luma_navigation_trail_bar_clear_meta(LumaNavigationTrailBar *self) {
  g_return_if_fail(LUMA_IS_NAVIGATION_TRAIL_BAR(self));
  g_ptr_array_set_size(self->meta, 0);
  fill_meta(self);
}

void luma_navigation_trail_bar_add_meta(LumaNavigationTrailBar *self, const char *text, const char *action_name) {
  g_return_if_fail(LUMA_IS_NAVIGATION_TRAIL_BAR(self));
  g_return_if_fail(text != NULL);
  Meta *meta = g_new0(Meta, 1);
  meta->text = g_strdup(text);
  meta->action_name = g_strdup(action_name);
  g_ptr_array_add(self->meta, meta);
  fill_meta(self);
}
