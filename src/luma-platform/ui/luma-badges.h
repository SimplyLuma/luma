/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_badges.CountBadge / CategoryPill and content_contact.StatusPill (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaCountBadge:
 *
 * A count as a pill (CSS node `label`): a total ("1,284"), or with
 * attention, unread ("3", "99+"). Zero hides it.
 */
#define LUMA_TYPE_COUNT_BADGE (luma_count_badge_get_type())
G_DECLARE_FINAL_TYPE(LumaCountBadge, luma_count_badge, LUMA, COUNT_BADGE, GtkWidget)

/**
 * luma_count_badge_new:
 * @count: the count
 * @attention: whether it is unread
 *
 * Returns: (transfer floating): a new badge
 */
GtkWidget *luma_count_badge_new(int count, gboolean attention);
void luma_count_badge_set_count(LumaCountBadge *self, int count);
int luma_count_badge_get_count(LumaCountBadge *self);
void luma_count_badge_set_attention(LumaCountBadge *self, gboolean attention);
gboolean luma_count_badge_get_attention(LumaCountBadge *self);

/**
 * LumaCategoryPill:
 *
 * The category's name in the category's colour (CSS node `label`).
 * Categories: "create", "work", "media", "play", "tools".
 */
#define LUMA_TYPE_CATEGORY_PILL (luma_category_pill_get_type())
G_DECLARE_FINAL_TYPE(LumaCategoryPill, luma_category_pill, LUMA, CATEGORY_PILL, GtkWidget)

/**
 * luma_category_pill_new:
 * @category: a category key
 *
 * Returns: (transfer floating): a new pill
 */
GtkWidget *luma_category_pill_new(const char *category);
void luma_category_pill_set_category(LumaCategoryPill *self, const char *category);
const char *luma_category_pill_get_category(LumaCategoryPill *self);

/**
 * LumaStatusPill:
 *
 * A status as a pill: a dot in the status colour and the words for it. Kinds:
 * "on-luma", "not-on-luma", "online", "offline", "syncing", "error".
 */
#define LUMA_TYPE_STATUS_PILL (luma_status_pill_get_type())
G_DECLARE_FINAL_TYPE(LumaStatusPill, luma_status_pill, LUMA, STATUS_PILL, GtkBox)

/**
 * luma_status_pill_new:
 * @kind: a status kind
 * @label: (nullable): words in place of the kind's own
 *
 * Returns: (transfer floating): a new pill
 */
GtkWidget *luma_status_pill_new(const char *kind, const char *label);
/**
 * luma_status_pill_set_kind:
 * @self: a pill
 * @kind: a status kind
 * @label: (nullable): words in place of the kind's own
 */
void luma_status_pill_set_kind(LumaStatusPill *self, const char *kind, const char *label);
const char *luma_status_pill_get_kind(LumaStatusPill *self);

G_END_DECLS
