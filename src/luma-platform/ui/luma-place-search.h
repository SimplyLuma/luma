/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_place.py: PlaceSearch, PlaceResult, rank_places,
 * parse_coordinates (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaPlaceResult:
 *
 * A place: its name, one line of where it is, and the app's own value.
 */
#define LUMA_TYPE_PLACE_RESULT (luma_place_result_get_type())
G_DECLARE_FINAL_TYPE(LumaPlaceResult, luma_place_result, LUMA, PLACE_RESULT, GObject)

/**
 * luma_place_result_new:
 * @name: the place's name
 * @subtitle: (nullable): where it is
 * @value: (nullable) (transfer none): the app's own object for it
 *
 * Returns: (transfer full): a new result
 */
LumaPlaceResult *luma_place_result_new(const char *name, const char *subtitle, GObject *value);
const char *luma_place_result_get_name(LumaPlaceResult *self);
const char *luma_place_result_get_subtitle(LumaPlaceResult *self);
/**
 * luma_place_result_get_value:
 * @self: a result
 *
 * Returns: (transfer none) (nullable): the app's value
 */
GObject *luma_place_result_get_value(LumaPlaceResult *self);

/**
 * luma_parse_coordinates:
 * @text: what was typed
 * @latitude: (out): the latitude
 * @longitude: (out): the longitude
 *
 * "37.32, -122.03" typed into the field.
 *
 * Returns: whether @text is a coordinate pair
 */
gboolean luma_parse_coordinates(const char *text, double *latitude, double *longitude);

/**
 * LumaPlaceSearch:
 *
 * A place field whose suggestions open above it: after a 180 ms pause the
 * places are ranked (names that start with the text first, then names that
 * contain it), five at most; arrows move, Enter picks, Esc closes.
 *
 * The app gives it all its places (luma_place_search_set_places()), or
 * answers #LumaPlaceSearch::search (const char *query) with
 * luma_place_search_set_results() for a remote provider. Picking emits
 * #LumaPlaceSearch::picked (LumaPlaceResult *).
 */
#define LUMA_TYPE_PLACE_SEARCH (luma_place_search_get_type())
G_DECLARE_FINAL_TYPE(LumaPlaceSearch, luma_place_search, LUMA, PLACE_SEARCH, GtkBox)

/**
 * luma_place_search_new:
 * @placeholder: (nullable): %NULL is "Add a city or ZIP"
 *
 * Returns: (transfer floating): a new field
 */
GtkWidget *luma_place_search_new(const char *placeholder);
/**
 * luma_place_search_set_places:
 * @self: a field
 * @places: (nullable): every #LumaPlaceResult it may offer; the kit ranks them
 */
void luma_place_search_set_places(LumaPlaceSearch *self, GListModel *places);
/**
 * luma_place_search_set_results:
 * @self: a field
 * @results: (nullable): #LumaPlaceResult answers to the last search, in order
 */
void luma_place_search_set_results(LumaPlaceSearch *self, GListModel *results);
/**
 * luma_place_search_set_exclude:
 * @self: a field
 * @filter: (nullable): places it matches are never offered (cities already added)
 */
void luma_place_search_set_exclude(LumaPlaceSearch *self, GtkFilter *filter);
const char *luma_place_search_get_text(LumaPlaceSearch *self);
void luma_place_search_set_text(LumaPlaceSearch *self, const char *text);
gboolean luma_place_search_get_list_shown(LumaPlaceSearch *self);
void luma_place_search_search_now(LumaPlaceSearch *self);
/**
 * luma_place_search_pick:
 * @self: a field
 * @index: a suggestion's index, or -1 for the selected one
 *
 * Returns: %FALSE when there is none
 */
gboolean luma_place_search_pick(LumaPlaceSearch *self, int index);
void luma_place_search_close_list(LumaPlaceSearch *self);

G_END_DECLS
