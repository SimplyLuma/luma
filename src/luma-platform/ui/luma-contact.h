/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_contact.py: Person, ContactActions, MiniCard, ContactCard,
 * EventCard, SongCard, PlaceCard, contact_uri (Python). StatusPill is in
 * luma-badges.h. */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaPerson:
 *
 * Who a contact card or action row is about. #LumaPerson:username is their
 * Luma @username; a person with one is on Luma.
 */
#define LUMA_TYPE_PERSON (luma_person_get_type())
G_DECLARE_FINAL_TYPE(LumaPerson, luma_person, LUMA, PERSON, GObject)

/**
 * luma_person_new:
 * @name: their name
 *
 * Returns: (transfer full): a new person
 */
LumaPerson *luma_person_new(const char *name);
const char *luma_person_get_name(LumaPerson *self);
void luma_person_set_phone(LumaPerson *self, const char *phone);
const char *luma_person_get_phone(LumaPerson *self);
void luma_person_set_email(LumaPerson *self, const char *email);
const char *luma_person_get_email(LumaPerson *self);
void luma_person_set_username(LumaPerson *self, const char *username);
const char *luma_person_get_username(LumaPerson *self);
void luma_person_set_online(LumaPerson *self, gboolean online);
gboolean luma_person_get_online(LumaPerson *self);
/**
 * luma_person_set_picture:
 * @self: a person
 * @picture: (nullable): their photo
 */
void luma_person_set_picture(LumaPerson *self, GdkPaintable *picture);
/**
 * luma_person_get_picture:
 * @self: a person
 *
 * Returns: (transfer none) (nullable): their photo
 */
GdkPaintable *luma_person_get_picture(LumaPerson *self);
gboolean luma_person_get_on_luma(LumaPerson *self);
/**
 * luma_person_set_hue:
 * @self: a person
 * @hue: their hue, 0–359: their face and the light behind their card wear
 *   it; -1 takes it from the name
 */
void luma_person_set_hue(LumaPerson *self, int hue);
int luma_person_get_hue(LumaPerson *self);

/**
 * luma_contact_uri:
 * @action: "message", "call", "video" or "email"
 * @person: who
 *
 * The link the desktop opens for @action when the app doesn't handle it.
 *
 * Returns: (transfer full): the URI, "" when there is none
 */
char *luma_contact_uri(const char *action, LumaPerson *person);

/**
 * LumaContactActions:
 *
 * Message, Call, Video, Email for one person, in that order, each disabled
 * when impossible. #LumaContactActions::action (const char *action, returns
 * gboolean handled) lets the app handle one in place; otherwise the desktop
 * opens luma_contact_uri(), and a failure is an error toast.
 */
#define LUMA_TYPE_CONTACT_ACTIONS (luma_contact_actions_get_type())
G_DECLARE_FINAL_TYPE(LumaContactActions, luma_contact_actions, LUMA, CONTACT_ACTIONS, GtkBox)

/**
 * luma_contact_actions_new:
 * @person: who
 * @small: the compact variant (inside a mini card)
 *
 * Returns: (transfer floating): a new row
 */
GtkWidget *luma_contact_actions_new(LumaPerson *person, gboolean small);
/**
 * luma_contact_actions_activate_action:
 * @self: a row
 * @action: "message", "call", "video" or "email"
 *
 * Returns: whether it ran
 */
gboolean luma_contact_actions_activate_action(LumaContactActions *self, const char *action);

/**
 * LumaContactCard:
 *
 * Contacts' mini card: the face, the name, "@username · On Luma" or the
 * number, and the actions under it.
 */
#define LUMA_TYPE_CONTACT_CARD (luma_contact_card_get_type())
G_DECLARE_FINAL_TYPE(LumaContactCard, luma_contact_card, LUMA, CONTACT_CARD, GtkBox)

/**
 * luma_contact_card_new:
 * @person: who
 *
 * Returns: (transfer floating): a new card
 */
GtkWidget *luma_contact_card_new(LumaPerson *person);
/**
 * luma_contact_card_get_actions:
 * @self: a card
 *
 * Returns: (transfer none): its actions row (connect to its "action")
 */
LumaContactActions *luma_contact_card_get_actions(LumaContactCard *self);

/**
 * LumaEventCard:
 *
 * Calendar's mini card: the date tile in the calendar's colour, the title,
 * when and where, Add (#LumaEventCard::add).
 */
#define LUMA_TYPE_EVENT_CARD (luma_event_card_get_type())
G_DECLARE_FINAL_TYPE(LumaEventCard, luma_event_card, LUMA, EVENT_CARD, GtkBox)

/**
 * luma_event_card_new:
 * @title: the event
 * @start: when it starts
 * @all_day: whether it is all day
 * @where: (nullable): where
 * @tone: (nullable): the calendar's category hue; %NULL is "work"
 *
 * Returns: (transfer floating): a new card
 */
GtkWidget *luma_event_card_new(const char *title, GDateTime *start, gboolean all_day,
                               const char *where, const char *tone);
/**
 * luma_event_card_set_when:
 * @self: a card
 * @when: (nullable): words in place of the kit's time line
 */
void luma_event_card_set_when(LumaEventCard *self, const char *when);
/**
 * luma_event_card_set_add_label:
 * @self: a card
 * @label: (nullable): the button's word; %NULL hides Add
 */
void luma_event_card_set_add_label(LumaEventCard *self, const char *label);

/**
 * LumaSongCard:
 *
 * Tide's mini card: the artwork, the title, artist · album, Play
 * (#LumaSongCard::play).
 */
#define LUMA_TYPE_SONG_CARD (luma_song_card_get_type())
G_DECLARE_FINAL_TYPE(LumaSongCard, luma_song_card, LUMA, SONG_CARD, GtkBox)

/**
 * luma_song_card_new:
 * @title: the song
 * @artist: who
 * @album: (nullable): from
 * @artwork: (nullable): the cover
 *
 * Returns: (transfer floating): a new card
 */
GtkWidget *luma_song_card_new(const char *title, const char *artist, const char *album,
                              GdkPaintable *artwork);

/**
 * LumaPlaceCard:
 *
 * Maps' mini card: the pin, the name, the address and how far, Directions
 * (#LumaPlaceCard::directions).
 */
#define LUMA_TYPE_PLACE_CARD (luma_place_card_get_type())
G_DECLARE_FINAL_TYPE(LumaPlaceCard, luma_place_card, LUMA, PLACE_CARD, GtkBox)

/**
 * luma_place_card_new:
 * @name: the place
 * @address: where it is
 * @eta: (nullable): how far ("12 min")
 * @icon: (nullable): a Lucide glyph; %NULL is "map-pin"
 *
 * Returns: (transfer floating): a new card
 */
GtkWidget *luma_place_card_new(const char *name, const char *address, const char *eta, const char *icon);

G_END_DECLS
