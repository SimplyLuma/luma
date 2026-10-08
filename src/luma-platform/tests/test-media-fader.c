/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-media-fader.h"

static void test_construct_use_dispose(void) {
  const GtkOrientation orientations[] = {
      GTK_ORIENTATION_HORIZONTAL, GTK_ORIENTATION_VERTICAL};

  for (guint orientation = 0; orientation < G_N_ELEMENTS(orientations);
       orientation++) {
    for (guint iteration = 0; iteration < 64; iteration++) {
      GtkWidget *widget = luma_media_fader_new(orientations[orientation]);
      LumaMediaFader *fader = LUMA_MEDIA_FADER(widget);
      GtkAdjustment *adjustment = gtk_range_get_adjustment(GTK_RANGE(widget));
      GWeakRef widget_ref;
      GWeakRef adjustment_ref;

      g_object_ref_sink(widget);
      g_weak_ref_init(&widget_ref, widget);
      g_weak_ref_init(&adjustment_ref, adjustment);
      g_assert_true(GTK_IS_ADJUSTMENT(adjustment));
      g_assert_false(g_object_is_floating(adjustment));
      g_assert_cmpint(gtk_orientable_get_orientation(GTK_ORIENTABLE(widget)),
                      ==, orientations[orientation]);
      g_assert_cmpfloat(gtk_adjustment_get_lower(adjustment), ==, -60.0);
      g_assert_cmpfloat(gtk_adjustment_get_upper(adjustment), ==, 12.0);
      g_assert_cmpfloat(luma_media_fader_get_db(fader), ==, 0.0);

      luma_media_fader_set_db(fader, -6.0);
      g_assert_cmpfloat(gtk_adjustment_get_value(adjustment), ==, -6.0);
      gtk_adjustment_set_value(adjustment, 3.0);
      g_assert_cmpfloat(luma_media_fader_get_db(fader), ==, 3.0);
      luma_media_fader_set_db(fader, -80.0);
      g_assert_cmpfloat(luma_media_fader_get_db(fader), ==, -60.0);
      luma_media_fader_set_db(fader, 20.0);
      g_assert_cmpfloat(luma_media_fader_get_db(fader), ==, 12.0);

      g_object_unref(widget);
      g_assert_null(g_weak_ref_get(&widget_ref));
      g_assert_null(g_weak_ref_get(&adjustment_ref));
      g_weak_ref_clear(&widget_ref);
      g_weak_ref_clear(&adjustment_ref);
    }
  }
}

static void test_replace_adjustment(void) {
  GtkWidget *widget = luma_media_fader_new(GTK_ORIENTATION_HORIZONTAL);
  GtkAdjustment *original = gtk_range_get_adjustment(GTK_RANGE(widget));
  GtkAdjustment *replacement = gtk_adjustment_new(-12.0, -60.0, 12.0, 0.1, 3.0, 0.0);
  GWeakRef original_ref;
  GWeakRef replacement_ref;

  g_object_ref_sink(widget);
  g_weak_ref_init(&original_ref, original);
  g_weak_ref_init(&replacement_ref, replacement);
  gtk_range_set_adjustment(GTK_RANGE(widget), replacement);
  g_assert_null(g_weak_ref_get(&original_ref));
  g_assert_cmpfloat(luma_media_fader_get_db(LUMA_MEDIA_FADER(widget)), ==, -12.0);
  luma_media_fader_set_db(LUMA_MEDIA_FADER(widget), -4.0);
  g_assert_cmpfloat(gtk_adjustment_get_value(replacement), ==, -4.0);
  g_object_unref(widget);
  g_assert_null(g_weak_ref_get(&replacement_ref));
  g_weak_ref_clear(&original_ref);
  g_weak_ref_clear(&replacement_ref);
}

static void test_retained_adjustment_outlives_fader(void) {
  GtkWidget *widget = luma_media_fader_new(GTK_ORIENTATION_VERTICAL);
  GtkAdjustment *adjustment = gtk_range_get_adjustment(GTK_RANGE(widget));
  GWeakRef adjustment_ref;

  g_object_ref_sink(widget);
  g_object_ref(adjustment);
  g_weak_ref_init(&adjustment_ref, adjustment);
  g_object_unref(widget);
  /* Destroyed ranges must disconnect their callbacks from a retained model. */
  gtk_adjustment_set_value(adjustment, -9.0);
  g_assert_cmpfloat(gtk_adjustment_get_value(adjustment), ==, -9.0);
  g_object_unref(adjustment);
  g_assert_null(g_weak_ref_get(&adjustment_ref));
  g_weak_ref_clear(&adjustment_ref);
}

static void test_gobject_construction(void) {
  for (guint explicit_null = 0; explicit_null < 2; explicit_null++) {
    GtkWidget *widget = explicit_null
        ? g_object_new(LUMA_TYPE_MEDIA_FADER, "adjustment", NULL, NULL)
        : g_object_new(LUMA_TYPE_MEDIA_FADER, NULL);
    GtkOrientation orientation;
    g_object_ref_sink(widget);
    g_assert_cmpfloat(gtk_adjustment_get_lower(gtk_range_get_adjustment(GTK_RANGE(widget))),
                      ==, -60.0);
    luma_media_fader_set_db(LUMA_MEDIA_FADER(widget), -18.0);
    g_assert_cmpfloat(luma_media_fader_get_db(LUMA_MEDIA_FADER(widget)), ==, -18.0);
    g_object_set(widget, "orientation", GTK_ORIENTATION_VERTICAL, NULL);
    g_object_get(widget, "orientation", &orientation, NULL);
    g_assert_cmpint(orientation, ==, GTK_ORIENTATION_VERTICAL);
    g_object_unref(widget);
  }
}

static void adjustment_notified(GObject *object, GParamSpec *pspec, gpointer data) {
  guint *count = data;
  g_assert_true(LUMA_IS_MEDIA_FADER(object));
  g_assert_cmpstr(pspec->name, ==, "adjustment");
  (*count)++;
}

static void test_adjustment_property_notifications(void) {
  GtkWidget *widget = luma_media_fader_new(GTK_ORIENTATION_HORIZONTAL);
  GtkAdjustment *replacement = gtk_adjustment_new(-3.0, -60.0, 12.0, 0.1, 3.0, 0.0);
  guint notifications = 0;
  g_object_ref_sink(widget);
  g_signal_connect(widget, "notify::adjustment", G_CALLBACK(adjustment_notified),
                   &notifications);
  g_object_set(widget, "adjustment", replacement, NULL);
  g_assert_cmpuint(notifications, ==, 1);
  g_assert_true(gtk_range_get_adjustment(GTK_RANGE(widget)) == replacement);
  g_object_set(widget, "adjustment", replacement, NULL);
  g_assert_cmpuint(notifications, ==, 1);
  g_object_set(widget, "adjustment", NULL, NULL);
  g_assert_cmpuint(notifications, ==, 2);
  g_assert_true(GTK_IS_ADJUSTMENT(gtk_range_get_adjustment(GTK_RANGE(widget))));
  g_signal_handlers_disconnect_by_data(widget, &notifications);
  g_object_unref(widget);
}

static void test_supplied_adjustment(void) {
  for (guint empty = 0; empty < 2; empty++) {
    GtkAdjustment *adjustment = empty
        ? gtk_adjustment_new(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        : gtk_adjustment_new(-12.0, -48.0, 6.0, 0.5, 2.0, 0.0);
    GWeakRef adjustment_ref;
    g_weak_ref_init(&adjustment_ref, adjustment);
    GtkWidget *widget = g_object_new(LUMA_TYPE_MEDIA_FADER,
                                     "adjustment", adjustment, NULL);
    g_object_ref_sink(widget);
    g_assert_true(gtk_range_get_adjustment(GTK_RANGE(widget)) == adjustment);
    g_assert_cmpfloat(gtk_adjustment_get_value(adjustment), ==, empty ? 0.0 : -12.0);
    g_assert_cmpfloat(gtk_adjustment_get_lower(adjustment), ==, empty ? 0.0 : -48.0);
    g_assert_cmpfloat(gtk_adjustment_get_upper(adjustment), ==, empty ? 0.0 : 6.0);
    g_assert_cmpfloat(gtk_adjustment_get_step_increment(adjustment), ==, empty ? 0.0 : 0.5);
    GtkAdjustment *read_back = NULL;
    g_object_get(widget, "adjustment", &read_back, NULL);
    g_assert_true(read_back == adjustment);
    g_object_unref(read_back);
    g_object_unref(widget);
    g_assert_null(g_weak_ref_get(&adjustment_ref));
    g_weak_ref_clear(&adjustment_ref);
  }
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check()) {
    g_test_message("A GTK display is required; run this test under Xvfb or Wayland.");
    return 77;
  }
  g_test_add_func("/luma/media-fader/construct-use-dispose", test_construct_use_dispose);
  g_test_add_func("/luma/media-fader/replace-adjustment", test_replace_adjustment);
  g_test_add_func("/luma/media-fader/retained-adjustment", test_retained_adjustment_outlives_fader);
  g_test_add_func("/luma/media-fader/gobject-construction", test_gobject_construction);
  g_test_add_func("/luma/media-fader/supplied-adjustment", test_supplied_adjustment);
  g_test_add_func("/luma/media-fader/adjustment-property-notifications", test_adjustment_property_notifications);
  return g_test_run();
}
