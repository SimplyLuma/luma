/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI contact parts against content_contact.py. */
#include "luma-ui.h"

static gboolean stop(gpointer data) {
  *(gboolean *)data = TRUE;
  return G_SOURCE_REMOVE;
}

static void spin(void) {
  gboolean done = FALSE;
  g_timeout_add(250, stop, &done);
  while (!done)
    g_main_context_iteration(NULL, TRUE);
}

static GtkWidget *nth_child(GtkWidget *widget, int n) {
  GtkWidget *child = gtk_widget_get_first_child(widget);
  while (child != NULL && n-- > 0)
    child = gtk_widget_get_next_sibling(child);
  return child;
}

static void test_person(void) {
  g_autoptr(LumaPerson) person = luma_person_new("Priya Raman");
  g_assert_cmpstr(luma_person_get_name(person), ==, "Priya Raman");
  g_assert_false(luma_person_get_on_luma(person));
  luma_person_set_username(person, "priya");
  g_assert_true(luma_person_get_on_luma(person));
  gboolean on_luma = FALSE;
  g_object_get(person, "on-luma", &on_luma, NULL);
  g_assert_true(on_luma);
  g_assert_cmpint(luma_person_get_hue(person), ==, -1);
  luma_person_set_hue(person, 400);
  g_assert_cmpint(luma_person_get_hue(person), ==, 40);
}

static void test_contact_uri(void) {
  g_autoptr(LumaPerson) person = luma_person_new("Priya Raman");
  luma_person_set_phone(person, "+1 (555) 010-1");
  luma_person_set_email(person, "priya raman+x@example.org");
  const struct {
    const char *action, *uri;
  } cases[] = {{"message", "sms:+15550101"},
               {"call", "tel:+15550101"},
               {"email", "mailto:priya%20raman%2Bx@example.org"},
               {"video", ""}};
  for (guint i = 0; i < G_N_ELEMENTS(cases); i++) {
    g_autofree char *uri = luma_contact_uri(cases[i].action, person);
    g_assert_cmpstr(uri, ==, cases[i].uri);
  }
  g_autoptr(LumaPerson) nobody = luma_person_new("Nobody");
  g_autofree char *none = luma_contact_uri("call", nobody);
  g_assert_cmpstr(none, ==, "");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "unknown contact action 'fax'");
  g_assert_null(luma_contact_uri("fax", person));
  g_test_assert_expected_messages();
}

static gboolean handle_action(LumaContactActions *actions G_GNUC_UNUSED, const char *action, gpointer data) {
  g_free(*(char **)data);
  *(char **)data = g_strdup(action);
  return TRUE;
}

static void test_contact_actions(void) {
  g_autoptr(LumaPerson) person = luma_person_new("Sam Kim");
  luma_person_set_username(person, "sam");
  GtkWidget *row = luma_contact_actions_new(person, FALSE);
  LumaContactActions *self = LUMA_CONTACT_ACTIONS(row);
  g_assert_true(gtk_widget_has_css_class(row, "lumaui-stacked-buttons"));
  g_assert_true(gtk_widget_has_css_class(row, "lumaui-contact-actions"));
  g_assert_false(gtk_widget_has_css_class(row, "lumaui-stack-small"));
  g_assert_true(gtk_box_get_homogeneous(GTK_BOX(row)));
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(row), GTK_ACCESSIBLE_PROPERTY_LABEL, "Contact Sam Kim");
  const char *labels[] = {"Message", "Call", "Video", "Email"};
  for (int i = 0; i < 4; i++) {
    GtkWidget *button = nth_child(row, i);
    g_assert_true(LUMA_IS_STACKED_BUTTON(button));
    gtk_test_accessible_assert_property(GTK_ACCESSIBLE(button), GTK_ACCESSIBLE_PROPERTY_LABEL, labels[i]);
    /* No phone, no email, no one handling it: nothing can be done. */
    g_assert_false(gtk_widget_get_sensitive(button));
  }
  g_assert_false(luma_contact_actions_activate_action(self, "video"));

  /* The app handles actions in place: on Luma, it can message, call and video. */
  char *handled = NULL;
  g_signal_connect(row, "action", G_CALLBACK(handle_action), &handled);
  GtkWidget *window = gtk_window_new();
  gtk_window_set_child(GTK_WINDOW(window), row);
  gtk_window_present(GTK_WINDOW(window));
  spin();
  g_assert_true(gtk_widget_get_sensitive(nth_child(row, 0)));
  g_assert_true(gtk_widget_get_sensitive(nth_child(row, 1)));
  g_assert_true(gtk_widget_get_sensitive(nth_child(row, 2)));
  g_assert_false(gtk_widget_get_sensitive(nth_child(row, 3)));
  g_signal_emit_by_name(nth_child(row, 2), "clicked");
  g_assert_cmpstr(handled, ==, "video");
  g_assert_true(luma_contact_actions_activate_action(self, "message"));
  g_assert_cmpstr(handled, ==, "message");
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "unknown contact action 'fax'");
  g_assert_false(luma_contact_actions_activate_action(self, "fax"));
  g_test_assert_expected_messages();
  g_free(handled);
  gtk_window_destroy(GTK_WINDOW(window));
}

static void check_mini(GtkWidget *card, const char *kind, const char *title, const char *subtitle) {
  g_assert_true(gtk_widget_has_css_class(card, "lumaui-mini-card"));
  g_assert_true(gtk_widget_has_css_class(card, kind));
  g_assert_cmpint(gtk_orientable_get_orientation(GTK_ORIENTABLE(card)), ==, GTK_ORIENTATION_VERTICAL);
  g_assert_cmpint(gtk_widget_get_halign(card), ==, GTK_ALIGN_START);
  GtkWidget *row = gtk_widget_get_first_child(card);
  g_assert_true(gtk_widget_has_css_class(row, "lumaui-mini-row"));
  GtkWidget *lead = gtk_widget_get_first_child(row);
  g_assert_true(gtk_widget_has_css_class(lead, "lumaui-mini-lead"));
  GtkWidget *text = gtk_widget_get_next_sibling(lead);
  g_assert_true(gtk_widget_has_css_class(text, "lumaui-mini-text"));
  GtkWidget *heading = gtk_widget_get_first_child(text);
  g_assert_true(gtk_widget_has_css_class(heading, "lumaui-mini-title"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(heading)), ==, title);
  GtkWidget *line = gtk_widget_get_next_sibling(heading);
  if (subtitle == NULL) {
    g_assert_null(line);
  } else {
    g_assert_true(gtk_widget_has_css_class(line, "lumaui-mini-subtitle"));
    if (GTK_IS_LABEL(line))
      g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(line)), ==, subtitle);
  }
  /* The design width (300, 320 for a person, plus the card's own padding),
   * narrower only when it must be. */
  int minimum, natural;
  gtk_widget_measure(card, GTK_ORIENTATION_HORIZONTAL, -1, &minimum, &natural, NULL, NULL);
  g_assert_cmpint(natural, >=, g_str_equal(kind, "person") ? 320 : 300);
  g_assert_cmpint(minimum, <, natural);
}

static void test_contact_card(void) {
  g_autoptr(LumaPerson) person = luma_person_new("Priya Raman");
  luma_person_set_username(person, "priya");
  luma_person_set_phone(person, "+1 555 0101");
  luma_person_set_hue(person, 330);
  GtkWidget *card = g_object_ref_sink(luma_contact_card_new(person));
  check_mini(card, "person", "Priya Raman", "");
  GtkWidget *row = gtk_widget_get_first_child(card);
  GtkWidget *avatar = gtk_widget_get_first_child(gtk_widget_get_first_child(row));
  g_assert_true(LUMA_IS_PERSON_AVATAR(avatar));
  g_assert_true(gtk_widget_has_css_class(avatar, "lumaui-hue-330"));
  GtkWidget *line = gtk_widget_get_next_sibling(gtk_widget_get_first_child(gtk_widget_get_next_sibling(
      gtk_widget_get_first_child(row))));
  g_assert_true(gtk_widget_has_css_class(gtk_widget_get_first_child(line), "lumaui-on-luma-dot"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(gtk_widget_get_last_child(line))), ==, "@priya · On Luma");
  LumaContactActions *actions = luma_contact_card_get_actions(LUMA_CONTACT_CARD(card));
  g_assert_true(gtk_widget_get_parent(GTK_WIDGET(actions)) == card);
  g_assert_true(gtk_widget_has_css_class(GTK_WIDGET(actions), "lumaui-stack-small"));
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(card), GTK_ACCESSIBLE_PROPERTY_LABEL, "Priya Raman");
  g_object_unref(card);

  g_autoptr(LumaPerson) caller = luma_person_new("Tom Hale");
  luma_person_set_phone(caller, "+1 555 0199");
  card = g_object_ref_sink(luma_contact_card_new(caller));
  check_mini(card, "person", "Tom Hale", "+1 555 0199");
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(card), GTK_ACCESSIBLE_PROPERTY_LABEL, "Tom Hale, +1 555 0199");
  g_object_unref(card);
}

static void count(gpointer instance G_GNUC_UNUSED, gpointer data) {
  (*(int *)data)++;
}

static void test_event_card(void) {
  g_autoptr(GDateTime) start = g_date_time_new_local(2026, 9, 25, 14, 30, 0);
  GtkWidget *card = g_object_ref_sink(luma_event_card_new("Launch rehearsal", start, FALSE, "Studio", NULL));
  check_mini(card, "event", "Launch rehearsal", "2:30 PM · Studio");
  GtkWidget *row = gtk_widget_get_first_child(card);
  GtkWidget *tile = gtk_widget_get_first_child(gtk_widget_get_first_child(row));
  g_assert_true(gtk_widget_has_css_class(tile, "lumaui-event-tile"));
  g_assert_true(gtk_widget_has_css_class(tile, "work"));
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(gtk_widget_get_first_child(tile))), ==, "SEP");
  g_assert_cmpstr(gtk_label_get_label(GTK_LABEL(gtk_widget_get_last_child(tile))), ==, "25");
  GtkWidget *add = gtk_widget_get_last_child(row);
  g_assert_true(gtk_widget_has_css_class(add, "lumaui-mini-button"));
  g_assert_cmpstr(gtk_button_get_label(GTK_BUTTON(add)), ==, "Add");
  g_assert_false(gtk_widget_get_sensitive(add));
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(card), GTK_ACCESSIBLE_PROPERTY_LABEL,
                                      "Launch rehearsal, 2:30 PM · Studio");

  int added = 0;
  g_signal_connect(card, "add", G_CALLBACK(count), &added);
  g_signal_emit_by_name(add, "clicked");
  g_assert_cmpint(added, ==, 1);
  luma_event_card_set_when(LUMA_EVENT_CARD(card), "Tomorrow");
  check_mini(card, "event", "Launch rehearsal", "Tomorrow · Studio");
  luma_event_card_set_add_label(LUMA_EVENT_CARD(card), "Add to Work");
  g_assert_cmpstr(gtk_button_get_label(GTK_BUTTON(add)), ==, "Add to Work");
  luma_event_card_set_add_label(LUMA_EVENT_CARD(card), NULL);
  g_assert_false(gtk_widget_get_visible(add));
  g_object_unref(card);

  card = g_object_ref_sink(luma_event_card_new("Offsite", start, TRUE, NULL, "media"));
  check_mini(card, "event", "Offsite", "All day");
  g_object_unref(card);

  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "an event's tone is one of*");
  g_assert_null(luma_event_card_new("x", start, FALSE, NULL, "purple"));
  g_test_assert_expected_messages();
}

static void test_song_card(void) {
  GtkWidget *card = g_object_ref_sink(luma_song_card_new("God Only Knows", "The Beach Boys", "Pet Sounds", NULL));
  check_mini(card, "song", "God Only Knows", "The Beach Boys · Pet Sounds");
  GtkWidget *row = gtk_widget_get_first_child(card);
  GtkWidget *lead = gtk_widget_get_first_child(gtk_widget_get_first_child(row));
  g_assert_true(gtk_widget_has_css_class(lead, "lumaui-song-artwork"));
  g_assert_true(GTK_IS_IMAGE(gtk_widget_get_first_child(lead)));
  GtkWidget *play = gtk_widget_get_last_child(row);
  g_assert_true(gtk_widget_has_css_class(play, "lumaui-mini-play"));
  g_assert_false(gtk_widget_has_css_class(play, "image-button"));
  gtk_test_accessible_assert_property(GTK_ACCESSIBLE(play), GTK_ACCESSIBLE_PROPERTY_LABEL, "Play God Only Knows");
  int played = 0;
  g_signal_connect(card, "play", G_CALLBACK(count), &played);
  g_signal_emit_by_name(play, "clicked");
  g_assert_cmpint(played, ==, 1);
  g_object_unref(card);

  g_autoptr(GBytes) bytes = g_bytes_new_take(g_malloc0(16 * 16 * 4), 16 * 16 * 4);
  g_autoptr(GdkTexture) art = gdk_memory_texture_new(16, 16, GDK_MEMORY_R8G8B8A8, bytes, 16 * 4);
  card = g_object_ref_sink(luma_song_card_new("Wouldn't It Be Nice", "The Beach Boys", NULL, GDK_PAINTABLE(art)));
  check_mini(card, "song", "Wouldn't It Be Nice", "The Beach Boys");
  lead = gtk_widget_get_first_child(gtk_widget_get_first_child(gtk_widget_get_first_child(card)));
  g_assert_true(gtk_widget_has_css_class(lead, "lumaui-file-face"));
  g_assert_true(gtk_widget_has_css_class(lead, "lumaui-song-artwork"));
  g_object_unref(card);
}

static void test_place_card(void) {
  GtkWidget *card = g_object_ref_sink(luma_place_card_new("Duende", "468 19th St", "9 min", "utensils"));
  check_mini(card, "place", "Duende", "468 19th St · 9 min");
  GtkWidget *row = gtk_widget_get_first_child(card);
  GtkWidget *pin = gtk_widget_get_first_child(gtk_widget_get_first_child(row));
  g_assert_true(gtk_widget_has_css_class(pin, "lumaui-place-pin"));
  g_assert_cmpstr(gtk_image_get_icon_name(GTK_IMAGE(gtk_widget_get_first_child(pin))), ==,
                  "lumaui-utensils-symbolic");
  GtkWidget *directions = gtk_widget_get_last_child(row);
  g_assert_cmpstr(gtk_button_get_label(GTK_BUTTON(directions)), ==, "Directions");
  int went = 0;
  g_signal_connect(card, "directions", G_CALLBACK(count), &went);
  g_signal_emit_by_name(directions, "clicked");
  g_assert_cmpint(went, ==, 1);
  g_object_unref(card);

  card = g_object_ref_sink(luma_place_card_new("Home", "1 Main St", NULL, NULL));
  pin = gtk_widget_get_first_child(gtk_widget_get_first_child(gtk_widget_get_first_child(card)));
  g_assert_cmpstr(gtk_image_get_icon_name(GTK_IMAGE(gtk_widget_get_first_child(pin))), ==,
                  "lumaui-map-pin-symbolic");
  g_object_unref(card);
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  g_test_add_func("/lumaui/contact/person", test_person);
  g_test_add_func("/lumaui/contact/uri", test_contact_uri);
  g_test_add_func("/lumaui/contact/actions", test_contact_actions);
  g_test_add_func("/lumaui/contact/contact-card", test_contact_card);
  g_test_add_func("/lumaui/contact/event-card", test_event_card);
  g_test_add_func("/lumaui/contact/song-card", test_song_card);
  g_test_add_func("/lumaui/contact/place-card", test_place_card);
  return g_test_run();
}
