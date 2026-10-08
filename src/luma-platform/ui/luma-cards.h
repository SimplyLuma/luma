/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_cards.py: Card, ContentLitCard, AccountCard, PersonAvatar,
 * ContentLitHeader (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaCard:
 *
 * The plain card: the raised surface, 16 round, the chip edge, 12/14/14
 * inside (or unpadded).
 */
#define LUMA_TYPE_CARD (luma_card_get_type())
G_DECLARE_FINAL_TYPE(LumaCard, luma_card, LUMA, CARD, GtkBox)

/**
 * luma_card_new:
 * @child: (nullable): the card's content
 * @padded: whether the kit pads it
 *
 * Returns: (transfer floating): a new card
 */
GtkWidget *luma_card_new(GtkWidget *child, gboolean padded);
/**
 * luma_card_set_recessed:
 * @self: a card
 * @recessed: a well in the page rather than a chip on it (Contacts' cards)
 */
void luma_card_set_recessed(LumaCard *self, gboolean recessed);
/**
 * luma_card_set_shape:
 * @self: a card
 * @shape: "regular" or "stat" (a compact statistic well)
 */
void luma_card_set_shape(LumaCard *self, const char *shape);

/**
 * LumaContentLitCard:
 *
 * A card over a lit picture (sky, photo): darker than what is behind it, with
 * white text that passes AA.
 */
#define LUMA_TYPE_CONTENT_LIT_CARD (luma_content_lit_card_get_type())
G_DECLARE_FINAL_TYPE(LumaContentLitCard, luma_content_lit_card, LUMA, CONTENT_LIT_CARD, GtkBox)

/**
 * luma_content_lit_card_new:
 * @child: (nullable): the card's content
 * @padded: whether the kit pads it
 *
 * Returns: (transfer floating): a new card
 */
GtkWidget *luma_content_lit_card_new(GtkWidget *child, gboolean padded);
/** Use a light veil over a dark scene without changing card geometry. */
void luma_content_lit_card_set_night(LumaContentLitCard *self, gboolean night);

/**
 * LumaPersonAvatar:
 *
 * A round face: a photo, or initials on the person's category hue, sized by
 * the kit.
 */
#define LUMA_TYPE_PERSON_AVATAR (luma_person_avatar_get_type())
G_DECLARE_FINAL_TYPE(LumaPersonAvatar, luma_person_avatar, LUMA, PERSON_AVATAR, GtkWidget)

/**
 * luma_person_avatar_new:
 * @name: whose face
 * @size: its diameter in px (the kit's sizes: 32, 34, 36, 40, 44)
 *
 * Returns: (transfer floating): a new avatar
 */
GtkWidget *luma_person_avatar_new(const char *name, int size);
/**
 * luma_person_avatar_set_picture:
 * @self: an avatar
 * @picture: (nullable): their photo
 */
void luma_person_avatar_set_picture(LumaPersonAvatar *self, GdkPaintable *picture);
void luma_person_avatar_set_name(LumaPersonAvatar *self, const char *name);
/**
 * luma_person_avatar_set_hue:
 * @self: an avatar
 * @hue: the person's hue, 0–359; -1 takes it from the name
 *
 * The hue the face wears (class `lumaui-hue-<hue>`), so a person looks the
 * same in every app.
 */
void luma_person_avatar_set_hue(LumaPersonAvatar *self, int hue);

/**
 * LumaAccountCard:
 *
 * The person at the top of a sidebar: avatar, name, a caption, a chevron;
 * opens the account (#GtkActionable or #GtkButton::clicked).
 */
#define LUMA_TYPE_ACCOUNT_CARD (luma_account_card_get_type())
G_DECLARE_FINAL_TYPE(LumaAccountCard, luma_account_card, LUMA, ACCOUNT_CARD, GtkButton)

/**
 * luma_account_card_new:
 * @name: the person's name
 * @caption: (nullable): the line under it; %NULL is "Luma account"
 *
 * Returns: (transfer floating): a new card
 */
GtkWidget *luma_account_card_new(const char *name, const char *caption);
/**
 * luma_account_card_set_picture:
 * @self: a card
 * @picture: (nullable): their photo
 */
void luma_account_card_set_picture(LumaAccountCard *self, GdkPaintable *picture);
void luma_account_card_set_selected(LumaAccountCard *self, gboolean selected);
/**
 * luma_account_card_set_recessed:
 * @self: a card
 * @recessed: the recessed row (a smaller face), as Contacts' sidebar has it
 */
void luma_account_card_set_recessed(LumaAccountCard *self, gboolean recessed);
/**
 * luma_account_card_set_compact:
 * @self: an account card
 * @compact: %TRUE for v70 Settings' card (.cfme.lacct): a 34 avatar, the name 13.5/600, the caption 11.5/400
 */
void luma_account_card_set_compact(LumaAccountCard *self, gboolean compact);
/**
 * luma_account_card_set_hue:
 * @self: a card
 * @hue: the person's hue, 0–359; -1 takes it from the name
 */
void luma_account_card_set_hue(LumaAccountCard *self, int hue);

/**
 * LumaContentLitHeader:
 *
 * The light at the top of an island, from a photo and a hue: the photo
 * enlarged, blurred and faded down, or the hue's wash when there is none.
 */
#define LUMA_TYPE_CONTENT_LIT_HEADER (luma_content_lit_header_get_type())
G_DECLARE_FINAL_TYPE(LumaContentLitHeader, luma_content_lit_header, LUMA, CONTENT_LIT_HEADER, GtkWidget)

/**
 * luma_content_lit_header_new:
 *
 * Returns: (transfer floating): a new header with no light yet
 */
GtkWidget *luma_content_lit_header_new(void);
/**
 * luma_content_lit_header_set_source:
 * @self: a header
 * @picture: (nullable): their photo
 * @tone: (nullable): a category hue; %NULL takes it from @name
 * @name: (nullable): whose light it is
 */
void luma_content_lit_header_set_source(LumaContentLitHeader *self, GdkPaintable *picture,
                                        const char *tone, const char *name);
/**
 * luma_content_lit_header_set_focus:
 * @self: a header
 * @x: where in the photo the light centres, 0–1
 * @y: where in the photo the light centres, 0–1
 */
/**
 * luma_content_lit_header_set_hue:
 * @self: a header
 * @hue: a person's hue, 0–359, which wins over the source's tone; -1 goes
 *   back to the tone (or the name's hue)
 */
void luma_content_lit_header_set_hue(LumaContentLitHeader *self, int hue);
void luma_content_lit_header_set_focus(LumaContentLitHeader *self, double x, double y);

G_END_DECLS
