/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_place.py: PlaceResult, rank_places, parse_coordinates,
 * PlaceSearch. The list floats in the nearest LayerHost. */
#include "luma-place-search.h"
#include "luma-action-private.h"
#include "luma-layer-host.h"
#include "luma-ui-private.h"

#include <string.h>

#define NO_PLACES "No places match"

/* ── PlaceResult ────────────────────────────────────────────────────────── */

struct _LumaPlaceResult {
  GObject parent_instance;
  char *name, *subtitle;
  GObject *value;
};

G_DEFINE_FINAL_TYPE(LumaPlaceResult, luma_place_result, G_TYPE_OBJECT)

static void place_result_finalize(GObject *object) {
  LumaPlaceResult *self = LUMA_PLACE_RESULT(object);
  g_free(self->name);
  g_free(self->subtitle);
  g_clear_object(&self->value);
  G_OBJECT_CLASS(luma_place_result_parent_class)->finalize(object);
}

static void luma_place_result_class_init(LumaPlaceResultClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = place_result_finalize;
}

static void luma_place_result_init(LumaPlaceResult *self G_GNUC_UNUSED) {}

LumaPlaceResult *luma_place_result_new(const char *name, const char *subtitle, GObject *value) {
  g_return_val_if_fail(name != NULL, NULL);
  g_return_val_if_fail(value == NULL || G_IS_OBJECT(value), NULL);
  LumaPlaceResult *self = g_object_new(LUMA_TYPE_PLACE_RESULT, NULL);
  self->name = g_strdup(name);
  self->subtitle = g_strdup(subtitle != NULL ? subtitle : "");
  self->value = value != NULL ? g_object_ref(value) : NULL;
  return self;
}

const char *luma_place_result_get_name(LumaPlaceResult *self) {
  g_return_val_if_fail(LUMA_IS_PLACE_RESULT(self), NULL);
  return self->name;
}

const char *luma_place_result_get_subtitle(LumaPlaceResult *self) {
  g_return_val_if_fail(LUMA_IS_PLACE_RESULT(self), NULL);
  return self->subtitle;
}

GObject *luma_place_result_get_value(LumaPlaceResult *self) {
  g_return_val_if_fail(LUMA_IS_PLACE_RESULT(self), NULL);
  return self->value;
}

/* ── parse_coordinates, rank_places ─────────────────────────────────────── */

static gboolean parse_number(const char *text, double *out) {
  g_autofree char *part = g_strstrip(g_strdup(text));
  if (part[0] == '\0')
    return FALSE;
  char *end = NULL;
  double value = g_ascii_strtod(part, &end);
  if (end == part || *end != '\0')
    return FALSE;
  *out = value;
  return TRUE;
}

gboolean luma_parse_coordinates(const char *text, double *latitude, double *longitude) {
  g_autofree char *commas = g_strdelimit(g_strdup(text != NULL ? text : ""), ";", ',');
  g_auto(GStrv) parts = g_strsplit(commas, ",", -1);
  double lat, lon;
  if (g_strv_length(parts) != 2 || !parse_number(parts[0], &lat) || !parse_number(parts[1], &lon))
    return FALSE;
  if (!(lat >= -90 && lat <= 90) || !(lon >= -180 && lon <= 180))
    return FALSE;
  if (latitude != NULL)
    *latitude = lat;
  if (longitude != NULL)
    *longitude = lon;
  return TRUE;
}

/* Places matching @query: names that start with it first, then names (or
 * where they are) that contain it (Weather's order). */
static GPtrArray *rank_places(GListModel *places, const char *query) {
  GPtrArray *found = g_ptr_array_new_with_free_func(g_object_unref);
  g_autofree char *stripped = g_strstrip(g_strdup(query != NULL ? query : ""));
  g_autofree char *text = g_utf8_casefold(stripped, -1);
  if (text[0] == '\0' || places == NULL)
    return found;
  guint n = g_list_model_get_n_items(places);
  g_autoptr(GPtrArray) contains = g_ptr_array_new_with_free_func(g_object_unref);
  for (guint i = 0; i < n; i++) {
    g_autoptr(GObject) item = g_list_model_get_item(places, i);
    if (!LUMA_IS_PLACE_RESULT(item))
      continue;
    LumaPlaceResult *place = LUMA_PLACE_RESULT(item);
    g_autofree char *name = g_utf8_casefold(place->name, -1);
    g_autofree char *whole = g_strdup_printf("%s %s", place->name, place->subtitle);
    g_autofree char *key = g_utf8_casefold(whole, -1);
    if (g_str_has_prefix(name, text))
      g_ptr_array_add(found, g_object_ref(item));
    else if (strstr(key, text) != NULL)
      g_ptr_array_add(contains, g_object_ref(item));
  }
  for (guint i = 0; i < contains->len; i++)
    g_ptr_array_add(found, g_object_ref(g_ptr_array_index(contains, i)));
  return found;
}

/* ── PlaceSearch ────────────────────────────────────────────────────────── */

enum { SEARCH, PICKED, N_SIGNALS };
static guint signals[N_SIGNALS];

struct _LumaPlaceSearch {
  GtkBox parent_instance;
  GtkWidget *entry;
  GtkWidget *list;
  GListModel *places;
  GtkFilter *exclude;
  GPtrArray *results;
  int selected;
  guint debounce;
  guint leave;
};

G_DEFINE_FINAL_TYPE(LumaPlaceSearch, luma_place_search, GTK_TYPE_BOX)

static void place_search_cancel(LumaPlaceSearch *self) {
  g_clear_handle_id(&self->debounce, g_source_remove);
}

/* The results the field offers: none excluded, five at most. */
static void place_search_take(LumaPlaceSearch *self, GPtrArray *places) {
  g_ptr_array_set_size(self->results, 0);
  for (guint i = 0; i < places->len && self->results->len < LUMA_UI_PLACE_SEARCH_LIMIT_COUNT; i++) {
    GObject *place = g_ptr_array_index(places, i);
    if (self->exclude != NULL && gtk_filter_match(self->exclude, place))
      continue;
    g_ptr_array_add(self->results, g_object_ref(place));
  }
  self->selected = 0;
}

static void place_search_present(LumaPlaceSearch *self) {
  LumaLayerHost *host = luma_layer_host_for_widget(GTK_WIDGET(self));
  if (host == NULL)
    return;
  GtkWidget *parent = gtk_widget_get_parent(self->list);
  if (parent != GTK_WIDGET(host)) {
    if (parent != NULL)
      luma_layer_host_remove_layer(parent, self->list);
    luma_layer_host_add_layer(host, self->list);
  }
  gtk_widget_set_visible(self->list, TRUE);
  GdkRectangle field = luma_ui_rect_in(GTK_WIDGET(host), GTK_WIDGET(self), NULL);
  /* As wide as the field, never narrower than the list can be (a field not
   * laid out yet reports its minimum). */
  int least = 0;
  gtk_widget_set_size_request(self->list, -1, -1);
  gtk_widget_measure(self->list, GTK_ORIENTATION_HORIZONTAL, -1, &least, NULL, NULL, NULL);
  luma_ui_float_at(GTK_WIDGET(host), self->list, &field, LUMA_FLOAT_ABOVE, LUMA_FLOAT_ALIGN_START,
                   LUMA_UI_PLACE_SEARCH_OFFSET, 0, 0, MAX(field.width, least));
}

static void row_clicked(GtkButton *row, gpointer user_data) {
  luma_place_search_pick(user_data, GPOINTER_TO_INT(g_object_get_data(G_OBJECT(row), "luma-place-index")));
}

static GtkWidget *place_search_row(LumaPlaceSearch *self, int index, LumaPlaceResult *place) {
  gboolean on = index == self->selected;
  GtkWidget *row = g_object_new(GTK_TYPE_BUTTON, "can-focus", FALSE, "accessible-role",
                                GTK_ACCESSIBLE_ROLE_OPTION, NULL);
  gtk_widget_add_css_class(row, "lumaui-place-row");
  luma_ui_set_css_class(row, "on", on);
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  GtkWidget *glyph = luma_ui_icon_image("map-pin", 0);
  gtk_widget_add_css_class(glyph, "lumaui-place-row-icon");
  gtk_box_append(GTK_BOX(line), glyph);
  GtkWidget *text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_widget_set_valign(text, GTK_ALIGN_CENTER);
  GtkWidget *name = gtk_label_new(place->name);
  gtk_label_set_xalign(GTK_LABEL(name), 0);
  gtk_label_set_ellipsize(GTK_LABEL(name), PANGO_ELLIPSIZE_END);
  gtk_widget_add_css_class(name, "lumaui-place-name");
  gtk_box_append(GTK_BOX(text), name);
  if (place->subtitle[0] != '\0') {
    GtkWidget *where = gtk_label_new(place->subtitle);
    gtk_label_set_xalign(GTK_LABEL(where), 0);
    gtk_label_set_ellipsize(GTK_LABEL(where), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(where, "lumaui-t-caption");
    gtk_box_append(GTK_BOX(text), where);
  }
  gtk_box_append(GTK_BOX(line), text);
  GtkWidget *hint = gtk_label_new("Return");
  gtk_widget_set_visible(hint, on);
  gtk_widget_add_css_class(hint, "lumaui-place-key");
  gtk_box_append(GTK_BOX(line), hint);
  gtk_button_set_child(GTK_BUTTON(row), line);
  g_autofree char *label = place->subtitle[0] != '\0' ? g_strdup_printf("%s, %s", place->name, place->subtitle)
                                                      : g_strdup(place->name);
  luma_ui_set_accessible_label(row, label);
  gtk_accessible_update_state(GTK_ACCESSIBLE(row), GTK_ACCESSIBLE_STATE_SELECTED, on, -1);
  g_object_set_data(G_OBJECT(row), "luma-place-index", GINT_TO_POINTER(index));
  g_signal_connect(row, "clicked", G_CALLBACK(row_clicked), self);
  return row;
}

static void place_search_fill(LumaPlaceSearch *self) {
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child(self->list)) != NULL)
    gtk_box_remove(GTK_BOX(self->list), child);
  if (self->results->len == 0) {
    GtkWidget *none = gtk_label_new(NO_PLACES);
    gtk_label_set_xalign(GTK_LABEL(none), 0);
    gtk_widget_add_css_class(none, "lumaui-place-none");
    gtk_box_append(GTK_BOX(self->list), none);
  }
  for (guint i = 0; i < self->results->len; i++)
    gtk_box_append(GTK_BOX(self->list), place_search_row(self, (int)i, g_ptr_array_index(self->results, i)));
  place_search_present(self);
}

static gboolean text_is_blank(const char *text) {
  for (const char *p = text; *p != '\0'; p = g_utf8_next_char(p))
    if (!g_unichar_isspace(g_utf8_get_char(p)))
      return FALSE;
  return TRUE;
}

static void place_search_run(LumaPlaceSearch *self, const char *text) {
  if (text_is_blank(text)) {
    g_ptr_array_set_size(self->results, 0);
    luma_place_search_close_list(self);
    return;
  }
  if (self->places != NULL) {
    g_autoptr(GPtrArray) ranked = rank_places(self->places, text);
    place_search_take(self, ranked);
    place_search_fill(self);
    return;
  }
  /* A remote provider answers ::search with luma_place_search_set_results(). */
  g_ptr_array_set_size(self->results, 0);
  self->selected = 0;
  if (!g_signal_has_handler_pending(self, signals[SEARCH], 0, FALSE)) {
    place_search_fill(self);
    return;
  }
  g_signal_emit(self, signals[SEARCH], 0, text);
}

static gboolean place_search_debounced(gpointer user_data) {
  LumaPlaceSearch *self = user_data;
  self->debounce = 0;
  g_autofree char *text = g_strdup(luma_place_search_get_text(self));
  place_search_run(self, text);
  return G_SOURCE_REMOVE;
}

static void entry_changed(GtkEditable *editable, gpointer user_data) {
  LumaPlaceSearch *self = user_data;
  place_search_cancel(self);
  if (text_is_blank(gtk_editable_get_text(editable))) {
    g_ptr_array_set_size(self->results, 0);
    luma_place_search_close_list(self);
    return;
  }
  self->debounce = g_timeout_add(LUMA_UI_MOTION_SEARCH_DEBOUNCE_MS, place_search_debounced, self);
}

static void place_search_select(LumaPlaceSearch *self, int index) {
  if (self->results->len == 0)
    return;
  int n = (int)self->results->len;
  self->selected = ((index % n) + n) % n;
  place_search_fill(self);
}

static gboolean entry_key_pressed(GtkEventControllerKey *controller G_GNUC_UNUSED, guint keyval,
                                  guint keycode G_GNUC_UNUSED, GdkModifierType state G_GNUC_UNUSED,
                                  gpointer user_data) {
  LumaPlaceSearch *self = user_data;
  if ((keyval == GDK_KEY_Down || keyval == GDK_KEY_Up) && luma_place_search_get_list_shown(self)) {
    place_search_select(self, self->selected + (keyval == GDK_KEY_Down ? 1 : -1));
    return TRUE;
  }
  if (keyval == GDK_KEY_Return || keyval == GDK_KEY_KP_Enter) {
    if (self->debounce != 0) /* typed faster than the pause: search now, then add the top match */
      luma_place_search_search_now(self);
    return luma_place_search_pick(self, -1);
  }
  if (keyval == GDK_KEY_Escape) {
    if (luma_place_search_get_list_shown(self))
      luma_place_search_close_list(self);
    else if (luma_place_search_get_text(self)[0] != '\0')
      gtk_editable_set_text(GTK_EDITABLE(self->entry), "");
    else
      return FALSE;
    return TRUE;
  }
  return FALSE;
}

static gboolean place_search_left(gpointer user_data) {
  LumaPlaceSearch *self = user_data;
  self->leave = 0;
  if (!gtk_widget_has_focus(self->entry))
    luma_place_search_close_list(self);
  return G_SOURCE_REMOVE;
}

static void entry_focus_left(GtkEventControllerFocus *controller G_GNUC_UNUSED, gpointer user_data) {
  LumaPlaceSearch *self = user_data;
  g_clear_handle_id(&self->leave, g_source_remove);
  self->leave = g_timeout_add(150, place_search_left, self);
}

static void field_pressed(GtkGestureClick *gesture G_GNUC_UNUSED, int n_press G_GNUC_UNUSED,
                          double x G_GNUC_UNUSED, double y G_GNUC_UNUSED, gpointer user_data) {
  gtk_widget_grab_focus(LUMA_PLACE_SEARCH(user_data)->entry);
}

static void place_search_dispose(GObject *object) {
  LumaPlaceSearch *self = LUMA_PLACE_SEARCH(object);
  place_search_cancel(self);
  g_clear_handle_id(&self->leave, g_source_remove);
  if (self->list != NULL) {
    luma_place_search_close_list(self);
    g_clear_object(&self->list);
  }
  g_clear_object(&self->places);
  g_clear_object(&self->exclude);
  g_clear_pointer(&self->results, g_ptr_array_unref);
  G_OBJECT_CLASS(luma_place_search_parent_class)->dispose(object);
}

static void luma_place_search_class_init(LumaPlaceSearchClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = place_search_dispose;
  /**
   * LumaPlaceSearch::search:
   * @self: the field
   * @query: what was typed
   *
   * Asked after the typing pause when the field has no places of its own;
   * answer with luma_place_search_set_results().
   */
  signals[SEARCH] = g_signal_new("search", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                 G_TYPE_NONE, 1, G_TYPE_STRING);
  /**
   * LumaPlaceSearch::picked:
   * @self: the field
   * @place: the place chosen
   */
  signals[PICKED] = g_signal_new("picked", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL,
                                 G_TYPE_NONE, 1, LUMA_TYPE_PLACE_RESULT);
}

static void luma_place_search_init(LumaPlaceSearch *self) {
  luma_ui_install();
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_END);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-place-search");
  self->results = g_ptr_array_new_with_free_func(g_object_unref);
  GtkWidget *glyph = luma_ui_icon_image("search", 0);
  gtk_widget_add_css_class(glyph, "lumaui-place-search-icon");
  gtk_box_append(GTK_BOX(self), glyph);
  self->entry = g_object_new(GTK_TYPE_TEXT, "accessible-role", GTK_ACCESSIBLE_ROLE_SEARCH_BOX, NULL);
  gtk_widget_set_hexpand(self->entry, TRUE);
  gtk_widget_add_css_class(self->entry, "lumaui-place-entry");
  gtk_box_append(GTK_BOX(self), self->entry);
  self->list = g_object_ref_sink(g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_VERTICAL,
                                              "accessible-role", GTK_ACCESSIBLE_ROLE_LIST_BOX, NULL));
  gtk_widget_add_css_class(self->list, "lumaui-place-list");
  luma_ui_set_accessible_label(self->list, "Places");
  gtk_accessible_update_relation(GTK_ACCESSIBLE(self->entry), GTK_ACCESSIBLE_RELATION_CONTROLS, self->list, NULL,
                                 -1);
  g_signal_connect(self->entry, "changed", G_CALLBACK(entry_changed), self);
  GtkEventController *keys = gtk_event_controller_key_new();
  gtk_event_controller_set_propagation_phase(keys, GTK_PHASE_CAPTURE);
  g_signal_connect(keys, "key-pressed", G_CALLBACK(entry_key_pressed), self);
  gtk_widget_add_controller(self->entry, keys);
  GtkEventController *focus = gtk_event_controller_focus_new();
  g_signal_connect(focus, "leave", G_CALLBACK(entry_focus_left), self);
  gtk_widget_add_controller(self->entry, focus);
  GtkGesture *click = gtk_gesture_click_new();
  g_signal_connect(click, "pressed", G_CALLBACK(field_pressed), self);
  gtk_widget_add_controller(GTK_WIDGET(self), GTK_EVENT_CONTROLLER(click));
}

GtkWidget *luma_place_search_new(const char *placeholder) {
  if (placeholder == NULL)
    placeholder = "Add a city or ZIP";
  LumaPlaceSearch *self = g_object_new(LUMA_TYPE_PLACE_SEARCH, NULL);
  gtk_text_set_placeholder_text(GTK_TEXT(self->entry), placeholder);
  luma_ui_set_accessible_label(self->entry, placeholder);
  return GTK_WIDGET(self);
}

void luma_place_search_set_places(LumaPlaceSearch *self, GListModel *places) {
  g_return_if_fail(LUMA_IS_PLACE_SEARCH(self));
  g_return_if_fail(places == NULL || G_IS_LIST_MODEL(places));
  g_set_object(&self->places, places);
}

void luma_place_search_set_results(LumaPlaceSearch *self, GListModel *results) {
  g_return_if_fail(LUMA_IS_PLACE_SEARCH(self));
  g_return_if_fail(results == NULL || G_IS_LIST_MODEL(results));
  g_autoptr(GPtrArray) places = g_ptr_array_new_with_free_func(g_object_unref);
  guint n = results != NULL ? g_list_model_get_n_items(results) : 0;
  for (guint i = 0; i < n; i++) {
    GObject *item = g_list_model_get_item(results, i);
    if (LUMA_IS_PLACE_RESULT(item))
      g_ptr_array_add(places, item);
    else
      g_clear_object(&item);
  }
  /* An answer that comes after the field was cleared shows nothing. */
  if (text_is_blank(luma_place_search_get_text(self)))
    return;
  place_search_take(self, places);
  place_search_fill(self);
}

void luma_place_search_set_exclude(LumaPlaceSearch *self, GtkFilter *filter) {
  g_return_if_fail(LUMA_IS_PLACE_SEARCH(self));
  g_return_if_fail(filter == NULL || GTK_IS_FILTER(filter));
  g_set_object(&self->exclude, filter);
}

const char *luma_place_search_get_text(LumaPlaceSearch *self) {
  g_return_val_if_fail(LUMA_IS_PLACE_SEARCH(self), NULL);
  return gtk_editable_get_text(GTK_EDITABLE(self->entry));
}

void luma_place_search_set_text(LumaPlaceSearch *self, const char *text) {
  g_return_if_fail(LUMA_IS_PLACE_SEARCH(self));
  gtk_editable_set_text(GTK_EDITABLE(self->entry), text != NULL ? text : "");
}

gboolean luma_place_search_get_list_shown(LumaPlaceSearch *self) {
  g_return_val_if_fail(LUMA_IS_PLACE_SEARCH(self), FALSE);
  return gtk_widget_get_parent(self->list) != NULL && gtk_widget_get_visible(self->list);
}

void luma_place_search_search_now(LumaPlaceSearch *self) {
  g_return_if_fail(LUMA_IS_PLACE_SEARCH(self));
  place_search_cancel(self);
  g_autofree char *text = g_strdup(luma_place_search_get_text(self));
  place_search_run(self, text);
}

gboolean luma_place_search_pick(LumaPlaceSearch *self, int index) {
  g_return_val_if_fail(LUMA_IS_PLACE_SEARCH(self), FALSE);
  if (index < 0)
    index = self->selected;
  if (index >= (int)self->results->len)
    return FALSE;
  g_autoptr(LumaPlaceResult) place = g_object_ref(g_ptr_array_index(self->results, (guint)index));
  place_search_cancel(self);
  gtk_editable_set_text(GTK_EDITABLE(self->entry), "");
  luma_place_search_close_list(self);
  g_signal_emit(self, signals[PICKED], 0, place);
  return TRUE;
}

void luma_place_search_close_list(LumaPlaceSearch *self) {
  g_return_if_fail(LUMA_IS_PLACE_SEARCH(self));
  GtkWidget *parent = gtk_widget_get_parent(self->list);
  if (parent != NULL)
    luma_layer_host_remove_layer(parent, self->list);
}
