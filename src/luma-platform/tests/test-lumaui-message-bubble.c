/* SPDX-License-Identifier: Apache-2.0 */
/* LumaUI MessageBubble against content_message.py. */
#include "luma-ui.h"

static void test_bubble(void) {
  GtkWidget *bubble = g_object_ref_sink(luma_message_bubble_new("Freeze strings on the 10th?", FALSE));
  LumaMessageBubble *self = LUMA_MESSAGE_BUBBLE(bubble);
  g_assert_true(gtk_widget_has_css_class(bubble, "lumaui-message"));
  g_assert_true(gtk_widget_has_css_class(bubble, "theirs"));
  g_assert_cmpint(gtk_widget_get_halign(bubble), ==, GTK_ALIGN_START);
  GtkWidget *label = gtk_widget_get_first_child(bubble);
  g_assert_true(gtk_widget_has_css_class(label, "lumaui-message-text"));
  g_assert_true(gtk_label_get_selectable(GTK_LABEL(label)));
  g_assert_true(gtk_label_get_wrap(GTK_LABEL(label)));
  g_assert_cmpint(gtk_label_get_max_width_chars(GTK_LABEL(label)), ==, 42);
  luma_message_bubble_set_joins(self, FALSE, TRUE);
  g_assert_false(gtk_widget_has_css_class(bubble, "join-above"));
  g_assert_true(gtk_widget_has_css_class(bubble, "join-below"));
  luma_message_bubble_set_joins(self, TRUE, FALSE);
  g_assert_true(gtk_widget_has_css_class(bubble, "join-above"));
  g_assert_false(gtk_widget_has_css_class(bubble, "join-below"));
  luma_message_bubble_set_selected(self, TRUE);
  g_assert_true(gtk_widget_has_css_class(bubble, "selected"));
  g_object_unref(bubble);

  GtkWidget *photo = gtk_picture_new();
  bubble = g_object_ref_sink(luma_message_bubble_new_with_child(photo, TRUE));
  g_assert_true(gtk_widget_has_css_class(bubble, "mine"));
  g_assert_true(gtk_widget_has_css_class(bubble, "media"));
  g_assert_cmpint(gtk_widget_get_halign(bubble), ==, GTK_ALIGN_END);
  g_assert_true(gtk_widget_get_first_child(bubble) == photo);
  g_object_unref(bubble);

  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "a message bubble holds text or a child");
  g_assert_null(luma_message_bubble_new(NULL, TRUE));
  g_test_assert_expected_messages();
  g_test_expect_message(G_LOG_DOMAIN, G_LOG_LEVEL_CRITICAL, "a message bubble holds text or a child");
  g_assert_null(luma_message_bubble_new_with_child(NULL, TRUE));
  g_test_assert_expected_messages();
}

int main(int argc, char **argv) {
  g_test_init(&argc, &argv, NULL);
  if (!gtk_init_check())
    return 77;
  g_test_add_func("/lumaui/message-bubble/bubble", test_bubble);
  return g_test_run();
}
