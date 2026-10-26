/* SPDX-License-Identifier: GPL-3.0-or-later */

/* A native presentation of the shared Weather cache and Phosh CalendarServer.
 * No forecast requests, copied calendar database, polling or resident helper.
 */
#define _GNU_SOURCE
#include "luma-widgets-model.h"
#include <gio/gdesktopappinfo.h>
#include "widget-tokens.h"

#include <gio/gio.h>
#include <json-glib/json-glib.h>
#include <glib/gi18n-lib.h>
#include <fcntl.h>
#include <math.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_JSON_BYTES (512 * 1024)
#define MAX_EVENTS 512
#define WEATHER_MAX_AGE (6 * 60 * 60)
/* Today's remaining events, then the next ones within a week. */
#define CALENDAR_DAYS 7

typedef struct {
  char *summary;
  gint64 begin;
  gint64 end;
} TodayEvent;

typedef struct {
  GWeakRef widget;
} TodayCall;

struct _LumaWidgetsModel {
  GObject parent_instance;
  char *calendar_service;
  char *calendar_path;
  char *weather_unit;
  guint changed_id;
  char *weather_place;
  char *weather_value;
  char *weather_condition;
  char *weather_detail;
  gboolean   weather_imperial;
  char *calendar_weekday;
  char *calendar_day;
  char *calendar_events[2];
  char *places_path;
  char *cache_path;
  GFileMonitor *places_monitor;
  GFileMonitor *cache_monitor;
  GFileMonitor *timezone_monitor;
  GCancellable *cancel;
  GDBusProxy *calendar;
  GHashTable *events;
  guint refresh_id;
  guint expiry_id;
  guint date_id;
  guint event_id;
  gboolean calendar_loaded;
  gboolean stopped;
};

G_DEFINE_TYPE (LumaWidgetsModel, luma_widgets_model, G_TYPE_OBJECT)

enum { CHANGED, N_SIGNALS };
static guint signals[N_SIGNALS];

static void refresh_weather (LumaWidgetsModel *self);
static void refresh_calendar (LumaWidgetsModel *self);
static void refresh_date (LumaWidgetsModel *self);
static void request_calendar (LumaWidgetsModel *self);
static void watch_files (LumaWidgetsModel *self);

static void
event_free (gpointer data)
{
  TodayEvent *event = data;
  g_free (event->summary);
  g_free (event);
}

static TodayCall *
call_new (LumaWidgetsModel *self)
{
  TodayCall *call = g_new0 (TodayCall, 1);
  g_weak_ref_init (&call->widget, self);
  return call;
}

static void
call_free (TodayCall *call)
{
  g_weak_ref_clear (&call->widget);
  g_free (call);
}

/* Read only owned regular files, with a hard cap even if the file grows during
 * a concurrent atomic cache replacement. Damaged or absent data is an empty state.
 */
static JsonParser *
read_json (const char *path)
{
  g_autofree char *text = g_malloc (MAX_JSON_BYTES + 1);
  g_autoptr (JsonParser) parser = json_parser_new ();
  struct stat st;
  gsize used = 0;
  int fd;

  fd = open (path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
  if (fd < 0)
    return NULL;
  if (fstat (fd, &st) < 0 || !S_ISREG (st.st_mode) ||
      st.st_uid != geteuid () || st.st_size < 0 || st.st_size > MAX_JSON_BYTES) {
    close (fd);
    return NULL;
  }
  while (used <= MAX_JSON_BYTES) {
    ssize_t n = read (fd, text + used, MAX_JSON_BYTES + 1 - used);
    if (n < 0) {
      close (fd);
      return NULL;
    }
    if (n == 0)
      break;
    used += n;
  }
  close (fd);
  if (used > MAX_JSON_BYTES || !g_utf8_validate (text, used, NULL))
    return NULL;
  if (!json_parser_load_from_data (parser, text, used, NULL))
    return NULL;
  return g_steal_pointer (&parser);
}

static const char *
json_string (JsonObject *object, const char *key)
{
  JsonNode *node = json_object_get_member (object, key);
  const char *value;

  if (!node || !JSON_NODE_HOLDS_VALUE (node) || json_node_get_value_type (node) != G_TYPE_STRING)
    return NULL;
  value = json_node_get_string (node);
  return value && g_utf8_validate (value, -1, NULL) && strlen (value) <= 512 ? value : NULL;
}

static gboolean
json_number (JsonObject *object, const char *key, double *value)
{
  JsonNode *node = json_object_get_member (object, key);
  GType type;

  if (!node || !JSON_NODE_HOLDS_VALUE (node))
    return FALSE;
  type = json_node_get_value_type (node);
  if (type != G_TYPE_INT64 && type != G_TYPE_DOUBLE)
    return FALSE;
  *value = json_node_get_double (node);
  return isfinite (*value);
}

static JsonObject *
json_child (JsonObject *object, const char *key)
{
  JsonNode *node = json_object_get_member (object, key);
  return node && JSON_NODE_HOLDS_OBJECT (node) ? json_node_get_object (node) : NULL;
}

static char *
cache_filename (const char *uid)
{
  g_autoptr (GString) safe = g_string_new (NULL);

  for (const char *p = uid; *p; p = g_utf8_next_char (p)) {
    gunichar c = g_utf8_get_char (p);
    if (g_unichar_isalnum (c) || c == '-' || c == '_')
      g_string_append_unichar (safe, c);
  }
  return g_strconcat (safe->len ? safe->str : "place", ".json", NULL);
}

static gboolean
imperial_locale (void)
{
  const char *locale = g_getenv ("LC_MEASUREMENT");
  const char *country;

  if (!locale)
    locale = g_getenv ("LANG");
  country = locale ? strchr (locale, '_') : NULL;
  if (!country)
    return FALSE;
  country++;
  return (g_ascii_strncasecmp (country, "US", 2) == 0 ||
          g_ascii_strncasecmp (country, "LR", 2) == 0 ||
          g_ascii_strncasecmp (country, "MM", 2) == 0) &&
         (country[2] == '\0' || country[2] == '.' || country[2] == '@');
}

static char *
temperature (LumaWidgetsModel *self, double celsius)
{
  double degrees = self->weather_imperial ? celsius * 9.0 / 5.0 + 32 : celsius;

  /* Match shared Weather's whole-degree rule, including negative half values. */
  return g_strdup_printf ("%d°", (int) floor (degrees + 0.5));
}

static const char *
condition (const char *symbol)
{
  if (!symbol)
    return _("Forecast available");
  if (strstr (symbol, "thunder")) return _("Thunderstorms");
  if (g_str_has_prefix (symbol, "clearsky")) return _("Clear");
  if (g_str_has_prefix (symbol, "fair")) return _("Fair");
  if (g_str_has_prefix (symbol, "partlycloudy")) return _("Partly cloudy");
  if (g_str_has_prefix (symbol, "cloudy")) return _("Cloudy");
  if (g_str_has_prefix (symbol, "fog")) return _("Fog");
  if (strstr (symbol, "sleet")) return _("Sleet");
  if (strstr (symbol, "snow")) return _("Snow");
  if (strstr (symbol, "rain")) return _("Rain");
  return _("Forecast available");
}

static gboolean
weather_expired (gpointer data)
{
  LumaWidgetsModel *self = data;
  self->expiry_id = 0;
  refresh_weather (self);
  return G_SOURCE_REMOVE;
}

static gboolean
emit_changed (gpointer data)
{
  LumaWidgetsModel *self = data;
  self->changed_id = 0;
  if (!self->stopped) g_signal_emit (self, signals[CHANGED], 0);
  return G_SOURCE_REMOVE;
}

static void
set_text (LumaWidgetsModel *self, char **field, const char *text)
{
  if (g_strcmp0 (*field, text) == 0) return;
  g_free (*field);
  *field = g_strdup (text);
  if (!self->changed_id && !self->stopped)
    self->changed_id = g_idle_add (emit_changed, self);
}

static void
set_weather_unit_label (LumaWidgetsModel *self)
{
  set_text (self, &self->weather_unit,
    self->weather_imperial ? _("Degrees Fahrenheit") : _("Degrees Celsius"));
}


static void
refresh_weather (LumaWidgetsModel *self)
{
  g_autoptr (JsonParser) places = NULL;
  g_autoptr (JsonParser) forecast = NULL;
  g_autoptr (GDateTime) fetched = NULL;
  g_autoptr (GDateTime) now = g_date_time_new_now_utc ();
  g_autofree char *filename = NULL;
  g_autofree char *path = NULL;
  g_autofree char *temp = NULL;
  g_autofree char *range = NULL;
  JsonNode *node;
  JsonArray *array = NULL;
  JsonObject *place = NULL, *root, *current, *cached_place;
  const char *uid = NULL, *name = NULL, *stamp;
  double version, degrees, high, low;
  gint64 age;

  g_clear_handle_id (&self->expiry_id, g_source_remove);
  set_text (self, &self->weather_place, _("Weather"));
  set_text (self, &self->weather_value, "—");
  set_text (self, &self->weather_condition, _("Open Weather"));
  set_text (self, &self->weather_detail, _("Choose a city"));
  places = read_json (self->places_path);
  if (!places) {
    self->weather_imperial = imperial_locale ();
    set_weather_unit_label (self);
    return;
  }
  node = json_parser_get_root (places);
  /* The unit is Weather's own setting (preferences.units in this file):
   * metric or imperial, else automatic, which follows the locale. */
  self->weather_imperial = imperial_locale ();
  if (JSON_NODE_HOLDS_OBJECT (node)) {
    JsonObject *preferences = json_child (json_node_get_object (node), "preferences");
    const char *units = preferences ? json_string (preferences, "units") : NULL;

    if (g_strcmp0 (units, "imperial") == 0)
      self->weather_imperial = TRUE;
    else if (g_strcmp0 (units, "metric") == 0)
      self->weather_imperial = FALSE;
  }
  set_weather_unit_label (self);
  if (JSON_NODE_HOLDS_OBJECT (node))
    node = json_object_get_member (json_node_get_object (node), "places");
  if (!node || !JSON_NODE_HOLDS_ARRAY (node))
    return;
  array = json_node_get_array (node);
  for (guint i = 0; i < MIN (json_array_get_length (array), 256); i++) {
    node = json_array_get_element (array, i);
    if (!JSON_NODE_HOLDS_OBJECT (node))
      continue;
    place = json_node_get_object (node);
    uid = json_string (place, "uid");
    name = json_string (place, "name");
    if (uid && *uid && name && *name)
      break;
    uid = name = NULL;
  }
  if (!uid)
    return;
  set_text (self, &self->weather_place, name);
  set_text (self, &self->weather_detail, _("Update in Weather"));
  filename = cache_filename (uid);
  path = g_build_filename (self->cache_path, filename, NULL);
  forecast = read_json (path);
  if (!forecast)
    return;
  node = json_parser_get_root (forecast);
  if (!JSON_NODE_HOLDS_OBJECT (node))
    return;
  root = json_node_get_object (node);
  current = json_child (root, "current");
  cached_place = json_child (root, "place");
  stamp = json_string (root, "fetched_at");
  if (!current || !cached_place || !stamp ||
      g_strcmp0 (json_string (cached_place, "uid"), uid) != 0 ||
      !json_number (root, "version", &version) || version != 1 ||
      !json_number (current, "temperature_c", &degrees) || degrees < -100 || degrees > 100)
    return;
  fetched = g_date_time_new_from_iso8601 (stamp, NULL);
  if (!fetched)
    return;
  age = g_date_time_difference (now, fetched) / G_TIME_SPAN_SECOND;
  if (age < -300 || age >= WEATHER_MAX_AGE)
    return;
  temp = temperature (self, degrees);
  set_text (self, &self->weather_value, temp);
  set_text (self, &self->weather_condition, condition (json_string (current, "symbol")));
  if (json_number (current, "high_c", &high) && json_number (current, "low_c", &low) &&
      high >= low && high <= 100 && low >= -100) {
    g_autofree char *hi = temperature (self, high);
    g_autofree char *lo = temperature (self, low);
    range = g_strdup_printf (_("H %s · L %s"), hi, lo);
  } else {
    range = g_strdup (_("Cached forecast"));
  }
  set_text (self, &self->weather_detail, range);
  self->expiry_id = g_timeout_add_seconds (WEATHER_MAX_AGE - MIN (age, WEATHER_MAX_AGE) + 1,
                                           weather_expired, self);
}

static gboolean
events_changed (gpointer data)
{
  LumaWidgetsModel *self = data;
  self->event_id = 0;
  refresh_calendar (self);
  return G_SOURCE_REMOVE;
}

static void
refresh_calendar (LumaWidgetsModel *self)
{
  GHashTableIter iter;
  gpointer value;
  TodayEvent *first = NULL, *second = NULL;
  gint64 now = g_get_real_time () / G_TIME_SPAN_SECOND;

  g_clear_handle_id (&self->event_id, g_source_remove);
  g_hash_table_iter_init (&iter, self->events);
  while (g_hash_table_iter_next (&iter, NULL, &value)) {
    TodayEvent *event = value;
    if (event->end <= now || event->begin > now + CALENDAR_DAYS * 24 * 60 * 60)
      continue;
    if (!first || event->begin < first->begin) {
      second = first;
      first = event;
    } else if (!second || event->begin < second->begin) {
      second = event;
    }
  }
  set_text (self, &self->calendar_events[0],
                      self->calendar_loaded ? _("No upcoming events") : _("Open Calendar"));
  set_text (self, &self->calendar_events[1], "");
  for (guint i = 0; i < 2; i++) {
    TodayEvent *event = i ? second : first;
    g_autoptr (GDateTime) date = NULL;
    g_autofree char *time = NULL;
    g_autofree char *text = NULL;
    if (!event)
      continue;
    date = g_date_time_new_from_unix_local (event->begin);
    if (!date)
      continue;
    {
      g_autoptr (GDateTime) today = g_date_time_new_now_local ();
      gboolean same_day = g_date_time_get_year (date) == g_date_time_get_year (today) &&
                          g_date_time_get_day_of_year (date) == g_date_time_get_day_of_year (today);

      time = g_date_time_format (date, same_day ? "%H:%M" : "%a %H:%M");
    }
    text = g_strdup_printf ("%s\n%s", event->summary, event->begin <= now ? _("Now") : time);
    set_text (self, &self->calendar_events[i], text);
  }
  if (first) {
    gint64 boundary = first->begin > now ? first->begin : first->end;
    self->event_id = g_timeout_add_seconds (MAX (1, boundary - now + 1), events_changed, self);
  }
}

static void
calendar_signal (GDBusProxy *proxy G_GNUC_UNUSED, const char *sender G_GNUC_UNUSED, const char *signal,
                 GVariant *parameters, LumaWidgetsModel *self)
{
  if (self->stopped)
    return;
  if (g_str_equal (signal, "EventsAddedOrUpdated") &&
      g_variant_is_of_type (parameters, G_VARIANT_TYPE ("(a(ssxxa{sv}))"))) {
    g_autoptr (GVariant) records = g_variant_get_child_value (parameters, 0);
    GVariantIter iter;
    const char *id, *summary;
    gint64 begin, end;
    GVariant *extra;
    guint count = 0;
    g_variant_iter_init (&iter, records);
    while (count++ < MAX_EVENTS &&
           g_variant_iter_next (&iter, "(&s&sxx@a{sv})", &id, &summary, &begin, &end, &extra)) {
      g_variant_unref (extra);
      if (strlen (id) > 512 || strlen (summary) > 1024 || end < begin ||
          !g_utf8_validate (summary, -1, NULL))
        continue;
      if (!g_hash_table_contains (self->events, id) && g_hash_table_size (self->events) >= MAX_EVENTS)
        continue;
      TodayEvent *event = g_new0 (TodayEvent, 1);
      event->summary = g_strdup (*summary ? summary : _("Event"));
      event->begin = begin;
      event->end = end;
      g_hash_table_replace (self->events, g_strdup (id), event);
    }
    self->calendar_loaded = TRUE;
  } else if (g_str_equal (signal, "EventsRemoved") &&
             g_variant_is_of_type (parameters, G_VARIANT_TYPE ("(as)"))) {
    g_autoptr (GVariant) ids = g_variant_get_child_value (parameters, 0);
    GVariantIter iter;
    const char *id;
    g_variant_iter_init (&iter, ids);
    while (g_variant_iter_next (&iter, "&s", &id))
      g_hash_table_remove (self->events, id);
  } else if (g_str_equal (signal, "ClientDisappeared") &&
             g_variant_is_of_type (parameters, G_VARIANT_TYPE ("(s)"))) {
    const char *source;
    GHashTableIter iter;
    gpointer key;
    g_variant_get (parameters, "(&s)", &source);
    g_hash_table_iter_init (&iter, self->events);
    while (g_hash_table_iter_next (&iter, &key, NULL))
      if (g_str_has_prefix (key, source) && ((char *)key)[strlen (source)] == '\n')
        g_hash_table_iter_remove (&iter);
  }
  refresh_calendar (self);
}

static void
calendar_range_ready (GObject *object, GAsyncResult *result, gpointer data)
{
  TodayCall *call = data;
  g_autoptr (LumaWidgetsModel) self = g_weak_ref_get (&call->widget);
  g_autoptr (GVariant) reply = g_dbus_proxy_call_finish (G_DBUS_PROXY (object), result, NULL);
  if (self && !self->stopped) {
    self->calendar_loaded = reply != NULL;
    refresh_calendar (self);
  }
  call_free (call);
}

static void
request_calendar (LumaWidgetsModel *self)
{
  g_autofree char *owner = NULL;
  g_autoptr (GVariant) since = NULL, until = NULL;
  g_autoptr (GDateTime) now = NULL, end = NULL;

  if (!self->calendar || self->stopped)
    return;
  /* Not running yet: the request below starts it, and its events follow. */
  owner = g_dbus_proxy_get_name_owner (self->calendar);
  if (!owner) {
    self->calendar_loaded = FALSE;
    g_hash_table_remove_all (self->events);
    refresh_calendar (self);
  }
  now = g_date_time_new_now_local ();
  end = g_date_time_add_days (now, CALENDAR_DAYS);
  since = g_dbus_proxy_get_cached_property (self->calendar, "Since");
  until = g_dbus_proxy_get_cached_property (self->calendar, "Until");
  /* CalendarServer has one global query. Reuse its live range and request a
   * forced refresh to receive the snapshot; never narrow another presenter's range.
   */
  gint64 start_time = g_date_time_to_unix (now), end_time = g_date_time_to_unix (end);
  if (since && until && g_variant_is_of_type (since, G_VARIANT_TYPE_INT64) &&
      g_variant_is_of_type (until, G_VARIANT_TYPE_INT64) &&
      g_variant_get_int64 (since) <= start_time && g_variant_get_int64 (until) >= end_time) {
    start_time = g_variant_get_int64 (since);
    end_time = g_variant_get_int64 (until);
  }
  g_dbus_proxy_call (self->calendar, "SetTimeRange",
                     g_variant_new ("(xxb)", start_time, end_time, TRUE),
                     /* The server starts on this request: nothing else runs it. */
                     G_DBUS_CALL_FLAGS_NONE, 5000, self->cancel,
                     calendar_range_ready, call_new (self));
}

static void
calendar_owner_changed (GObject *object G_GNUC_UNUSED, GParamSpec *pspec G_GNUC_UNUSED, LumaWidgetsModel *self)
{
  if (self->stopped)
    return;
  self->calendar_loaded = FALSE;
  g_hash_table_remove_all (self->events);
  request_calendar (self);
}

static void
calendar_ready (GObject *object G_GNUC_UNUSED, GAsyncResult *result, gpointer data)
{
  TodayCall *call = data;
  g_autoptr (LumaWidgetsModel) self = g_weak_ref_get (&call->widget);
  g_autoptr (GDBusProxy) proxy = g_dbus_proxy_new_for_bus_finish (result, NULL);
  if (self && !self->stopped && proxy) {
    self->calendar = g_steal_pointer (&proxy);
    g_signal_connect_object (self->calendar, "g-signal", G_CALLBACK (calendar_signal), self, 0);
    g_signal_connect_object (self->calendar, "notify::g-name-owner",
                             G_CALLBACK (calendar_owner_changed), self, 0);
    request_calendar (self);
  }
  call_free (call);
}

static gboolean
date_changed (gpointer data)
{
  LumaWidgetsModel *self = data;
  self->date_id = 0;
  refresh_date (self);
  request_calendar (self);
  return G_SOURCE_REMOVE;
}

static void
refresh_date (LumaWidgetsModel *self)
{
  g_autoptr (GDateTime) now = g_date_time_new_now_local ();
  g_autoptr (GDateTime) next = g_date_time_add_days (now, 1);
  g_autoptr (GDateTime) midnight = g_date_time_new_local (
    g_date_time_get_year (next), g_date_time_get_month (next), g_date_time_get_day_of_month (next), 0, 0, 0);
  g_autofree char *weekday = g_date_time_format (now, "%A");
  g_autofree char *upper = g_utf8_strup (weekday, -1);
  g_autofree char *day = g_date_time_format (now, "%e");

  g_clear_handle_id (&self->date_id, g_source_remove);
  set_text (self, &self->calendar_weekday, upper);
  set_text (self, &self->calendar_day, g_strstrip (day));
  self->date_id = g_timeout_add_seconds (
    MAX (1, g_date_time_difference (midnight, now) / G_TIME_SPAN_SECOND + 1), date_changed, self);
  refresh_calendar (self);
}

static gboolean
files_refresh (gpointer data)
{
  LumaWidgetsModel *self = data;
  self->refresh_id = 0;
  watch_files (self);
  refresh_weather (self);
  refresh_date (self);
  return G_SOURCE_REMOVE;
}

static void
file_changed (GFileMonitor *monitor, GFile *file, GFile *other,
              GFileMonitorEvent event G_GNUC_UNUSED, LumaWidgetsModel *self)
{
  g_autofree char *path = file ? g_file_get_path (file) : NULL;
  g_autofree char *other_path = other ? g_file_get_path (other) : NULL;
  const char *target = monitor == self->places_monitor ? self->places_path : self->cache_path;
  gboolean relevant = monitor == self->timezone_monitor;

  for (guint i = 0; i < 2; i++) {
    const char *changed = i ? other_path : path;
    if (changed && (g_str_equal (changed, target) ||
                    (g_str_has_prefix (target, changed) && target[strlen (changed)] == '/') ||
                    (monitor == self->cache_monitor && g_str_has_prefix (changed, target) &&
                     changed[strlen (target)] == '/')))
      relevant = TRUE;
  }
  if (relevant && !self->stopped && !self->refresh_id)
    self->refresh_id = g_timeout_add (150, files_refresh, self);
}

static GFileMonitor *
watch_directory (LumaWidgetsModel *self, const char *directory)
{
  g_autofree char *path = g_strdup (directory);
  g_autoptr (GFile) file = NULL;
  GFileMonitor *monitor;

  /* Watching the nearest existing ancestor also catches first-use creation.
   * Re-arm deeper after directory creation; never create the application's data.
   */
  while (!g_file_test (path, G_FILE_TEST_IS_DIR)) {
    char *parent = g_path_get_dirname (path);
    if (g_str_equal (parent, path)) {
      g_free (parent);
      return NULL;
    }
    g_free (path);
    path = parent;
  }
  file = g_file_new_for_path (path);
  monitor = g_file_monitor_directory (file, G_FILE_MONITOR_WATCH_MOVES, self->cancel, NULL);
  if (monitor)
    g_signal_connect_object (monitor, "changed", G_CALLBACK (file_changed), self, 0);
  return monitor;
}

static void
watch_files (LumaWidgetsModel *self)
{
  g_autofree char *directory = g_path_get_dirname (self->places_path);
  if (self->places_monitor) g_file_monitor_cancel (self->places_monitor);
  if (self->cache_monitor) g_file_monitor_cancel (self->cache_monitor);
  g_clear_object (&self->places_monitor);
  g_clear_object (&self->cache_monitor);
  self->places_monitor = watch_directory (self, directory);
  self->cache_monitor = watch_directory (self, self->cache_path);
}

static void
stop (LumaWidgetsModel *self)
{
  if (self->stopped)
    return;
  self->stopped = TRUE;
  g_cancellable_cancel (self->cancel);
  g_clear_handle_id (&self->changed_id, g_source_remove);
  g_clear_handle_id (&self->refresh_id, g_source_remove);
  g_clear_handle_id (&self->expiry_id, g_source_remove);
  g_clear_handle_id (&self->date_id, g_source_remove);
  g_clear_handle_id (&self->event_id, g_source_remove);
  if (self->places_monitor) g_file_monitor_cancel (self->places_monitor);
  if (self->cache_monitor) g_file_monitor_cancel (self->cache_monitor);
  if (self->timezone_monitor) g_file_monitor_cancel (self->timezone_monitor);
  g_clear_object (&self->places_monitor);
  g_clear_object (&self->cache_monitor);
  g_clear_object (&self->timezone_monitor);
  g_clear_object (&self->calendar);
}

static void
luma_widgets_model_dispose (GObject *object)
{
  stop (LUMA_WIDGETS_MODEL (object));
  G_OBJECT_CLASS (luma_widgets_model_parent_class)->dispose (object);
}

static void
luma_widgets_model_finalize (GObject *object)
{
  LumaWidgetsModel *self = LUMA_WIDGETS_MODEL (object);
  g_clear_object (&self->cancel);
  g_clear_pointer (&self->events, g_hash_table_unref);
  g_free (self->places_path);
  g_free (self->cache_path);
  g_free (self->calendar_service); g_free (self->calendar_path);
  g_free (self->weather_place); g_free (self->weather_value);
  g_free (self->weather_condition); g_free (self->weather_detail); g_free (self->weather_unit);
  g_free (self->calendar_weekday); g_free (self->calendar_day);
  for (guint i = 0; i < 2; i++) g_free (self->calendar_events[i]);
  G_OBJECT_CLASS (luma_widgets_model_parent_class)->finalize (object);
}

static void
luma_widgets_model_class_init (LumaWidgetsModelClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);
  object_class->dispose = luma_widgets_model_dispose;
  object_class->finalize = luma_widgets_model_finalize;
  signals[CHANGED] = g_signal_new ("changed", G_TYPE_FROM_CLASS (klass),
    G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
}

static void
luma_widgets_model_init (LumaWidgetsModel *self)
{
  self->cancel = g_cancellable_new ();
  self->events = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, event_free);
  self->places_path = g_build_filename (g_get_user_data_dir (), "prairie", "weather", "places.json", NULL);
  self->cache_path = g_build_filename (g_get_user_cache_dir (), "prairie-weather", NULL);
  /* Follow the installed Weather launcher. Flatpak keeps the same application's
   * XDG state in its sandbox; never copy or invent another Weather database. */
  g_autoptr (GDesktopAppInfo) app = g_desktop_app_info_new ("org.projectluma.Weather.desktop");
  g_autofree char *flatpak = app ? g_desktop_app_info_get_string (app, "X-Flatpak") : NULL;
  if (g_strcmp0 (flatpak, "org.projectluma.Weather") == 0) {
    g_free (self->places_path);
    g_free (self->cache_path);
    self->places_path = g_build_filename (g_get_home_dir (), ".var", "app", flatpak,
                                         "data", "prairie", "weather", "places.json", NULL);
    self->cache_path = g_build_filename (g_get_home_dir (), ".var", "app", flatpak,
                                        "cache", "prairie-weather", NULL);
  }
}

LumaWidgetsModel *
luma_widgets_model_new (const char *calendar_service)
{
  g_return_val_if_fail (g_strcmp0 (calendar_service, "mobi.phosh.Shell.CalendarServer") == 0 ||
                        g_strcmp0 (calendar_service, "org.gnome.Shell.CalendarServer") == 0, NULL);
  LumaWidgetsModel *self = g_object_new (LUMA_WIDGETS_TYPE_MODEL, NULL);
  g_autoptr (GFile) timezone = g_file_new_for_path ("/etc/localtime");
  self->calendar_service = g_strdup (calendar_service);
  self->calendar_path = g_strdup (g_str_has_prefix (calendar_service, "mobi.")
    ? "/mobi/phosh/Shell/CalendarServer" : "/org/gnome/Shell/CalendarServer");
  refresh_weather (self);
  refresh_date (self);
  watch_files (self);
  self->timezone_monitor = g_file_monitor_file (timezone, G_FILE_MONITOR_NONE, self->cancel, NULL);
  if (self->timezone_monitor)
    g_signal_connect_object (self->timezone_monitor, "changed", G_CALLBACK (file_changed), self, 0);
  g_dbus_proxy_new_for_bus (G_BUS_TYPE_SESSION, G_DBUS_PROXY_FLAGS_DO_NOT_AUTO_START_AT_CONSTRUCTION,
    NULL, self->calendar_service, self->calendar_path, self->calendar_service, self->cancel,
    calendar_ready, call_new (self));
  return self;
}

char *
luma_widgets_model_dup_snapshot (LumaWidgetsModel *self)
{
  g_return_val_if_fail (LUMA_WIDGETS_IS_MODEL (self), NULL);
  g_autoptr (JsonBuilder) builder = json_builder_new ();
  g_autoptr (JsonGenerator) generator = json_generator_new ();
  json_builder_begin_object (builder);
  const char *ids[] = {"weather", "calendar"};
  const char *values[][4] = {
    {self->weather_place, self->weather_value, self->weather_condition, self->weather_detail},
    {self->calendar_weekday, self->calendar_day, self->calendar_events[0], self->calendar_events[1]}
  };
  const char *keys[] = {"title", "value", "description", "detail"};
  for (guint widget = 0; widget < 2; widget++) {
    json_builder_set_member_name (builder, ids[widget]);
    json_builder_begin_object (builder);
    json_builder_set_member_name (builder, "name");
    json_builder_add_string_value (builder, widget == 0 ? _("Weather") : _("Calendar"));
    json_builder_set_member_name (builder, "desktop_id");
    json_builder_add_string_value (builder, widget == 0 ? "org.projectluma.Weather.desktop" : "org.projectluma.Calendar.desktop");
    json_builder_set_member_name (builder, "gap"); json_builder_add_int_value (builder, LUMA_HOME_WIDGET_GAP);
    json_builder_set_member_name (builder, "columns"); json_builder_add_int_value (builder, 2);
    json_builder_set_member_name (builder, "rows"); json_builder_add_int_value (builder, 2);
    for (guint field = 0; field < 4; field++) {
      json_builder_set_member_name (builder, keys[field]);
      json_builder_add_string_value (builder, values[widget][field] ? values[widget][field] : "");
    }
    if (widget == 0) {
      json_builder_set_member_name (builder, "unit");
      json_builder_add_string_value (builder, self->weather_unit ? self->weather_unit : "");
    }
    json_builder_end_object (builder);
  }
  json_builder_end_object (builder);
  g_autoptr (JsonNode) root = json_builder_get_root (builder);
  json_generator_set_root (generator, root);
  return json_generator_to_data (generator, NULL);
}
