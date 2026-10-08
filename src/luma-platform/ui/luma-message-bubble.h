/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_message.MessageBubble (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaMessageBubble:
 *
 * One message: theirs on the raised surface, mine on the accent gradient.
 * Corners that meet the same sender's neighbours are 6, the rest 18.
 */
#define LUMA_TYPE_MESSAGE_BUBBLE (luma_message_bubble_get_type())
G_DECLARE_FINAL_TYPE(LumaMessageBubble, luma_message_bubble, LUMA, MESSAGE_BUBBLE, GtkBox)

/**
 * luma_message_bubble_new:
 * @text: (nullable): the message
 * @mine: whether I sent it
 *
 * Returns: (transfer floating): a new bubble
 */
GtkWidget *luma_message_bubble_new(const char *text, gboolean mine);
/**
 * luma_message_bubble_new_with_child:
 * @child: what the message holds (a #LumaFileCard, a mini card)
 * @mine: whether I sent it
 *
 * Returns: (transfer floating): a new bubble
 */
GtkWidget *luma_message_bubble_new_with_child(GtkWidget *child, gboolean mine);
/**
 * luma_message_bubble_set_joins:
 * @self: a bubble
 * @above: the same sender's message is just above
 * @below: the same sender's message is just below
 */
void luma_message_bubble_set_joins(LumaMessageBubble *self, gboolean above, gboolean below);
void luma_message_bubble_set_selected(LumaMessageBubble *self, gboolean selected);

G_END_DECLS
