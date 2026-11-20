/* SPDX-License-Identifier: Apache-2.0 */
/* LumaColorPicker (LumaUI creative), and LumaPropertyColor opening it. */
#include "luma-ui.h"

#include <glib/gstdio.h>
#include <math.h>

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void settle(void) {
  gboolean done = FALSE;
  g_timeout_add(200, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static GtkWidget *find(GtkWidget *root, const char *css_class) {
  if (gtk_widget_has_css_class(root, css_class))
    return root;
  for (GtkWidget *child = gtk_widget_get_first_child(root); child != NULL; child = gtk_widget_get_next_sibling(child)) {
    GtkWidget *found = find(child, css_class);
    if (found != NULL)
      return found;
  }
  return NULL;
}

static GtkEventController *controller_of(GtkWidget *widget, GType type) {
  g_autoptr(GListModel) controllers = gtk_widget_observe_controllers(widget);
  for (guint i = 0; i < g_list_model_get_n_items(controllers); i++) {
    g_autoptr(GObject) controller = g_list_model_get_item(controllers, i);
    if (G_TYPE_CHECK_INSTANCE_TYPE(controller, type))
      return GTK_EVENT_CONTROLLER(controller);
  }
  g_assert_not_reached();
}

typedef struct {
  int changes, commits;
  GdkRGBA last;
} Seen;

static void on_change(GtkWidget *widget G_GNUC_UNUSED, GdkRGBA *rgba, Seen *seen) {
  seen->changes++;
  seen->last = *rgba;
}

static void on_commit(GtkWidget *widget G_GNUC_UNUSED, GdkRGBA *rgba, Seen *seen) {
  seen->commits++;
  seen->last = *rgba;
}

static void drag_on(GtkWidget *plane, double x0, double y0, double x1, double y1) {
  GtkEventController *drag = controller_of(plane, GTK_TYPE_GESTURE_DRAG);
  g_signal_emit_by_name(drag, "drag-begin", x0, y0);
  g_signal_emit_by_name(drag, "drag-update", (x1 - x0) / 2, (y1 - y0) / 2);
  g_signal_emit_by_name(drag, "drag-end", x1 - x0, y1 - y0);
}

static void assert_color(const GdkRGBA *rgba, const char *code) {
  g_autofree char *got = luma_property_color_format(rgba);
  g_assert_cmpstr(got, ==, code);
}

static gboolean host(LumaPropertyColor *field G_GNUC_UNUSED, LumaColorPicker *picker, GtkWidget **hosted) {
  g_set_object(hosted, GTK_WIDGET(picker));
  return TRUE;
}

static void test_picker(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *picker = luma_color_picker_new();
  LumaColorPicker *self = LUMA_COLOR_PICKER(picker);
  gtk_window_set_child(GTK_WINDOW(window), picker);
  gtk_window_present(GTK_WINDOW(window));
  Seen seen = {0};
  g_signal_connect(picker, "color-changed", G_CALLBACK(on_change), &seen);
  g_signal_connect(picker, "color-committed", G_CALLBACK(on_commit), &seen);
  GdkRGBA red, rgba;
  luma_property_color_parse("FF0000", &red);
  luma_color_picker_set_rgba(self, &red);
  settle();
  luma_color_picker_get_rgba(self, &rgba);
  g_assert_true(gdk_rgba_equal(&rgba, &red));
  g_assert_cmpint(seen.changes + seen.commits, ==, 0); /* the app's own colour says nothing */

  GtkWidget *area = find(picker, "lumaui-color-picker-area"), *hue = find(picker, "lumaui-color-picker-hue");
  GtkWidget *alpha = find(picker, "lumaui-color-picker-alpha"), *hex = find(picker, "lumaui-creative-hex");
  g_assert_nonnull(area);
  g_assert_nonnull(hue);
  g_assert_nonnull(alpha);
  g_assert_nonnull(hex);
  g_assert_nonnull(find(picker, "lumaui-color-picker-eyedropper"));
  g_assert_cmpint(gtk_widget_get_width(picker), >=, 248 - 2 * 12); /* the picker_width and _padding tokens */
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(hex)), ==, "FF0000");

  /* The area: a drag previews every step and commits once, at the end. */
  int w = gtk_widget_get_width(area), h = gtk_widget_get_height(area);
  drag_on(area, w, 0, w / 2.0, h / 2.0);
  g_assert_cmpint(seen.changes, >=, 2);
  g_assert_cmpint(seen.commits, ==, 1);
  luma_color_picker_get_rgba(self, &rgba);
  assert_color(&rgba, "804040"); /* s 0.5, v 0.5 of red */
  g_assert_cmpstr(gtk_editable_get_text(GTK_EDITABLE(hex)), ==, "804040");
  /* Committed colours are this app's recent ones, newest first, kept in its config. */
  guint n = 0;
  g_autofree GdkRGBA *recent = luma_color_picker_get_recent_colors(&n);
  g_assert_cmpuint(n, ==, 1);
  assert_color(&recent[0], "804040");
  g_autofree char *ini = g_build_filename(g_get_user_config_dir(), g_get_prgname() != NULL ? g_get_prgname() : "lumaui",
                                         "lumaui.ini", NULL);
  g_autofree char *kept = NULL;
  g_assert_true(g_file_get_contents(ini, &kept, NULL, NULL));
  g_assert_nonnull(strstr(kept, "804040"));

  /* The hue strip: an arrow steps a degree, Shift ten; letting go commits. */
  luma_color_picker_set_rgba(self, &red);
  int commits = seen.commits;
  GtkEventController *keys = controller_of(hue, GTK_TYPE_EVENT_CONTROLLER_KEY);
  gboolean handled = FALSE;
  g_signal_emit_by_name(keys, "key-pressed", GDK_KEY_Right, 0, GDK_SHIFT_MASK, &handled);
  g_assert_true(handled);
  g_signal_emit_by_name(keys, "key-pressed", GDK_KEY_Right, 0, GDK_SHIFT_MASK, &handled);
  g_assert_cmpint(seen.commits, ==, commits);
  g_signal_emit_by_name(keys, "key-released", GDK_KEY_Right, 0, GDK_SHIFT_MASK);
  g_assert_cmpint(seen.commits, ==, commits + 1);
  luma_color_picker_get_rgba(self, &rgba);
  assert_color(&rgba, "FF5500"); /* hue 20 */
  /* The opacity strip, dragged to its start. */
  drag_on(alpha, gtk_widget_get_width(alpha) / 2.0, 8, 0, 8);
  luma_color_picker_get_rgba(self, &rgba);
  g_assert_cmpfloat(rgba.alpha, ==, 0);
  /* A swatch picks and commits at once: Luma's blue first. */
  commits = seen.commits;
  GtkWidget *luma = find(picker, "luma");
  GtkWidget *first = gtk_flow_box_child_get_child(gtk_flow_box_get_child_at_index(GTK_FLOW_BOX(luma), 0));
  g_signal_emit_by_name(first, "clicked");
  g_assert_cmpint(seen.commits, ==, commits + 1);
  assert_color(&seen.last, "60AAF3");
  /* A typed code, with its opacity. */
  gtk_editable_set_text(GTK_EDITABLE(hex), "#00ff0080");
  g_signal_emit_by_name(hex, "activate");
  luma_color_picker_get_rgba(self, &rgba);
  assert_color(&rgba, "00FF0080");
  g_assert_cmpint(seen.commits, ==, commits + 2);
  /* Without alpha the strip goes and the colour is opaque. */
  luma_color_picker_set_with_alpha(self, FALSE);
  g_assert_false(gtk_widget_get_visible(alpha));
  luma_color_picker_get_rgba(self, &rgba);
  g_assert_cmpfloat(rgba.alpha, ==, 1);
  /* The document's palette shows under Luma's; recent colours refresh when the picker shows again. */
  GdkRGBA palette[2];
  luma_property_color_parse("112233", &palette[0]);
  luma_property_color_parse("445566", &palette[1]);
  luma_color_picker_set_palette(self, palette, 2);
  GtkWidget *document = find(picker, "document");
  g_assert_true(gtk_widget_get_visible(document));
  g_assert_nonnull(gtk_flow_box_get_child_at_index(GTK_FLOW_BOX(document), 1));
  g_assert_null(gtk_flow_box_get_child_at_index(GTK_FLOW_BOX(document), 2));
  gtk_widget_set_visible(picker, FALSE);
  gtk_widget_set_visible(picker, TRUE);
  settle();
  GtkWidget *recents = find(picker, "recent");
  GtkWidget *newest = gtk_flow_box_child_get_child(gtk_flow_box_get_child_at_index(GTK_FLOW_BOX(recents), 0));
  g_assert_cmpstr(gtk_widget_get_tooltip_text(newest), ==, "00FF0080");
  gtk_window_destroy(GTK_WINDOW(window));
}

static void test_field(void) {
  GtkWidget *window = gtk_window_new();
  GtkWidget *field = luma_property_color_new("Fill");
  gtk_window_set_child(GTK_WINDOW(window), field);
  gtk_window_present(GTK_WINDOW(window));
  settle();
  GdkRGBA blue;
  luma_property_color_parse("1F6FE0", &blue);
  luma_property_color_set_rgba(LUMA_PROPERTY_COLOR(field), &blue);
  Seen changed = {0}, committed = {0};
  g_signal_connect(field, "color-changed", G_CALLBACK(on_change), &changed);
  g_signal_connect(field, "color-committed", G_CALLBACK(on_commit), &committed);
  GtkWidget *swatch = find(field, "lumaui-creative-swatch");
  /* Without a palette or a handler the swatch opens the picker in a popover. */
  g_signal_emit_by_name(swatch, "clicked");
  settle();
  GtkWidget *popover = find(field, "lumaui-color-picker-popover");
  g_assert_nonnull(popover);
  g_assert_true(gtk_widget_get_visible(popover));
  GtkWidget *picker = gtk_popover_get_child(GTK_POPOVER(popover));
  g_assert_true(LUMA_IS_COLOR_PICKER(picker));
  GdkRGBA rgba;
  luma_color_picker_get_rgba(LUMA_COLOR_PICKER(picker), &rgba);
  g_assert_true(gdk_rgba_equal(&rgba, &blue));
  gtk_popover_popdown(GTK_POPOVER(popover));
  /* An app can host the picker itself (its own grown panel); the picker's changes are the field's. */
  GtkWidget *hosted = NULL;
  g_signal_connect(field, "open-picker", G_CALLBACK(host), &hosted);
  g_signal_emit_by_name(swatch, "clicked");
  g_assert_true(LUMA_IS_COLOR_PICKER(hosted));
  GtkWidget *panel = gtk_window_new(); /* the app's own panel */
  gtk_window_set_child(GTK_WINDOW(panel), hosted);
  gtk_window_present(GTK_WINDOW(panel));
  settle();
  GtkWidget *area = find(hosted, "lumaui-color-picker-area");
  GtkEventController *drag = controller_of(area, GTK_TYPE_GESTURE_DRAG);
  g_signal_emit_by_name(drag, "drag-begin", 0.0, 0.0);
  g_signal_emit_by_name(drag, "drag-update", 0.0, 0.0);
  g_assert_cmpint(changed.changes, ==, 1);
  g_assert_cmpint(committed.commits, ==, 0);
  g_signal_emit_by_name(drag, "drag-end", 0.0, 0.0);
  g_assert_cmpint(committed.commits, ==, 1);
  luma_property_color_get_rgba(LUMA_PROPERTY_COLOR(field), &rgba);
  assert_color(&rgba, "FFFFFF"); /* the area's top left: no saturation, full brightness */
  /* A typed code commits too. */
  GtkWidget *hex = find(field, "lumaui-creative-hex");
  gtk_editable_set_text(GTK_EDITABLE(hex), "336699");
  g_signal_emit_by_name(hex, "activate");
  g_assert_cmpint(committed.commits, ==, 2);
  g_clear_object(&hosted);
  gtk_window_destroy(GTK_WINDOW(panel));
  gtk_window_destroy(GTK_WINDOW(window));
}

int main(int argc, char **argv) {
  g_autofree char *config = g_dir_make_tmp("lumaui-picker-XXXXXX", NULL);
  g_setenv("XDG_CONFIG_HOME", config, TRUE);
  g_test_init(&argc, &argv, NULL);
  gtk_init();
  g_test_add_func("/lumaui/creative/color-picker/picker", test_picker);
  g_test_add_func("/lumaui/creative/color-picker/field", test_field);
  return g_test_run();
}
