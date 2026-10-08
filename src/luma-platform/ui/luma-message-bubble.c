/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_message.MessageBubble. */
#include "luma-message-bubble.h"
#include "luma-ui-private.h"

struct _LumaMessageBubble {
  GtkBox parent_instance;
};

G_DEFINE_FINAL_TYPE(LumaMessageBubble, luma_message_bubble, GTK_TYPE_BOX)

static void luma_message_bubble_class_init(LumaMessageBubbleClass *klass G_GNUC_UNUSED) {}

static void luma_message_bubble_init(LumaMessageBubble *self) {
  luma_ui_install();
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-message");
}

static LumaMessageBubble *message_bubble_new(gboolean mine) {
  LumaMessageBubble *self = g_object_new(LUMA_TYPE_MESSAGE_BUBBLE, NULL);
  gtk_widget_set_halign(GTK_WIDGET(self), mine ? GTK_ALIGN_END : GTK_ALIGN_START);
  gtk_widget_add_css_class(GTK_WIDGET(self), mine ? "mine" : "theirs");
  return self;
}

GtkWidget *luma_message_bubble_new(const char *text, gboolean mine) {
  if (text == NULL) {
    g_critical("a message bubble holds text or a child");
    return NULL;
  }
  LumaMessageBubble *self = message_bubble_new(mine);
  GtkWidget *label = gtk_label_new(text);
  gtk_label_set_xalign(GTK_LABEL(label), 0);
  gtk_widget_set_hexpand(label, TRUE);
  gtk_label_set_wrap(GTK_LABEL(label), TRUE);
  gtk_label_set_selectable(GTK_LABEL(label), TRUE);
  gtk_label_set_wrap_mode(GTK_LABEL(label), PANGO_WRAP_WORD_CHAR);
  gtk_label_set_max_width_chars(GTK_LABEL(label), 42);
  gtk_widget_add_css_class(label, "lumaui-message-text");
  gtk_box_append(GTK_BOX(self), label);
  return GTK_WIDGET(self);
}

GtkWidget *luma_message_bubble_new_with_child(GtkWidget *child, gboolean mine) {
  if (!GTK_IS_WIDGET(child)) {
    g_critical("a message bubble holds text or a child");
    return NULL;
  }
  LumaMessageBubble *self = message_bubble_new(mine);
  gtk_widget_add_css_class(GTK_WIDGET(self), "media");
  gtk_box_append(GTK_BOX(self), child);
  return GTK_WIDGET(self);
}

void luma_message_bubble_set_joins(LumaMessageBubble *self, gboolean above, gboolean below) {
  g_return_if_fail(LUMA_IS_MESSAGE_BUBBLE(self));
  luma_ui_set_css_class(GTK_WIDGET(self), "join-above", above);
  luma_ui_set_css_class(GTK_WIDGET(self), "join-below", below);
}

void luma_message_bubble_set_selected(LumaMessageBubble *self, gboolean selected) {
  g_return_if_fail(LUMA_IS_MESSAGE_BUBBLE(self));
  luma_ui_set_css_class(GTK_WIDGET(self), "selected", selected);
}
