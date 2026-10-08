/* SPDX-License-Identifier: Apache-2.0 */

#define _GNU_SOURCE
#include <gtk/gtk.h>
#include <glib/gstdio.h>
#include <json-glib/json-glib.h>
#include <pwd.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define LUMA_PIN_DIGITS 4
#define LUMA_MAX_MESSAGE (1024U * 1024U)
#define LUMA_DEFAULT_USER "luma"
#define LUMA_DEFAULT_SESSION "/usr/libexec/luma-phosh-client-session"
#define LUMA_HOME_READY "/run/luma-display/home-ready"
#define LUMA_WALLPAPER "/usr/share/backgrounds/luma/luma-prism.png"
#define LUMA_IMS_BUS "net.catcrafts.IMS1"
#define LUMA_IMS_PATH "/net/catcrafts/IMS1"
#define LUMA_IMS_INTERFACE "net.catcrafts.IMS1"
#define LUMA_EMERGENCY_DIGITS 7
#define LUMA_IDLE_SECONDS 30
#define LUMA_BACKLIGHT_CLASS "/sys/class/backlight"
#define LUMA_LOGIN1_BUS "org.freedesktop.login1"
#define LUMA_LOGIN1_PATH "/org/freedesktop/login1"
#define LUMA_LOGIN1_INTERFACE "org.freedesktop.login1.Manager"

typedef struct {
  GtkApplication *application;
  GtkWindow *window;
  GtkStack *stack;
  GtkWidget *passcode_page;
  GtkLabel *clock_time;
  GtkLabel *clock_date;
  GtkLabel *passcode_time;
  GtkLabel *passcode_date;
  GtkLabel *feedback;
  GtkWidget *dots[LUMA_PIN_DIGITS];
  GtkWidget *keypad;
  GtkWidget *emergency;
  GtkWidget *emergency_page;
  GtkWidget *emergency_keypad;
  GtkWidget *emergency_call;
  GtkWidget *emergency_cancel;
  GtkLabel *emergency_number_label;
  GtkLabel *emergency_status;
  char pin[LUMA_PIN_DIGITS + 1];
  char emergency_number[LUMA_EMERGENCY_DIGITS + 1];
  char *emergency_call_uni;
  guint pin_length;
  guint emergency_number_length;
  guint emergency_poll_source;
  guint idle_source;
  guint panel_brightness;
  guint failure_count;
  guint handoff_frames;
  gboolean submitting;
  gboolean back_drag_armed;
  gboolean display_sleeping;
  char *backlight_name;
  char *username;
  char *session_command;
} LumaGreeter;

typedef struct {
  LumaGreeter *greeter;
  char pin[LUMA_PIN_DIGITS + 1];
  char *socket_path;
  char *username;
  char *session_command;
} AuthRequest;

static gboolean emergency_service_available(void);
static void emergency_digit_clicked(GtkButton *button, gpointer data);
static void emergency_delete_clicked(GtkButton *button, gpointer data);
static void emergency_call_clicked(GtkButton *button, gpointer data);
static void emergency_cancel_clicked(GtkButton *button, gpointer data);

static gboolean set_panel_brightness(LumaGreeter *greeter, guint brightness) {
  if (greeter->backlight_name == NULL)
    return FALSE;

  GError *error = NULL;
  GDBusConnection *bus = g_bus_get_sync(G_BUS_TYPE_SYSTEM, NULL, &error);
  if (bus == NULL) {
    g_clear_error(&error);
    return FALSE;
  }

  GVariant *reply = g_dbus_connection_call_sync(
      bus, LUMA_LOGIN1_BUS, LUMA_LOGIN1_PATH, LUMA_LOGIN1_INTERFACE,
      "SetBrightness",
      g_variant_new("(ssu)", "backlight", greeter->backlight_name, brightness),
      NULL, G_DBUS_CALL_FLAGS_NONE, 2000, NULL, &error);
  g_object_unref(bus);
  if (reply == NULL) {
    g_clear_error(&error);
    return FALSE;
  }
  g_variant_unref(reply);
  return TRUE;
}

static char *discover_backlight(guint *brightness) {
  GError *error = NULL;
  GDir *directory = g_dir_open(LUMA_BACKLIGHT_CLASS, 0, &error);
  g_clear_error(&error);
  if (directory == NULL)
    return NULL;

  const char *entry = NULL;
  char *selected = NULL;
  while ((entry = g_dir_read_name(directory)) != NULL) {
    if (selected != NULL) {
      g_clear_pointer(&selected, g_free);
      break;
    }
    selected = g_strdup(entry);
  }
  g_dir_close(directory);
  if (selected == NULL)
    return NULL;

  char *path = g_build_filename(LUMA_BACKLIGHT_CLASS, selected, "brightness", NULL);
  char *contents = NULL;
  if (!g_file_get_contents(path, &contents, NULL, NULL)) {
    g_free(path);
    g_free(selected);
    return NULL;
  }
  g_free(path);

  char *end = NULL;
  guint64 value = g_ascii_strtoull(contents, &end, 10);
  gboolean valid = end != contents && value > 0 && value <= G_MAXUINT;
  g_free(contents);
  if (!valid) {
    g_free(selected);
    return NULL;
  }
  *brightness = (guint)value;
  return selected;
}

static gboolean blank_idle_display(gpointer data) {
  LumaGreeter *greeter = data;
  greeter->idle_source = 0;
  if (greeter->submitting || greeter->display_sleeping)
    return G_SOURCE_REMOVE;
  if (set_panel_brightness(greeter, 0))
    greeter->display_sleeping = TRUE;
  return G_SOURCE_REMOVE;
}

static void arm_idle_timeout(LumaGreeter *greeter) {
  g_clear_handle_id(&greeter->idle_source, g_source_remove);
  if (!greeter->display_sleeping && !greeter->submitting)
    greeter->idle_source =
        g_timeout_add_seconds(LUMA_IDLE_SECONDS, blank_idle_display, greeter);
}

static gboolean activity_event(GtkEventControllerLegacy *controller,
                               GdkEvent *event, gpointer data) {
  (void)controller;
  LumaGreeter *greeter = data;
  GdkEventType type = gdk_event_get_event_type(event);
  gboolean actionable = type == GDK_TOUCH_BEGIN || type == GDK_BUTTON_PRESS ||
                        type == GDK_KEY_PRESS;

  if (greeter->display_sleeping && actionable) {
    if (set_panel_brightness(greeter, greeter->panel_brightness)) {
      greeter->display_sleeping = FALSE;
      arm_idle_timeout(greeter);
    }
    /* The first contact wakes the display; it must not also reveal or submit
     * Presence controls that were invisible when contact began. */
    return TRUE;
  }
  if (actionable)
    arm_idle_timeout(greeter);
  return FALSE;
}

static void secure_clear(void *memory, size_t length) {
#if defined(__GLIBC__)
  explicit_bzero(memory, length);
#else
  volatile unsigned char *cursor = memory;
  while (length-- > 0)
    *cursor++ = 0;
#endif
}

static gboolean write_all(GOutputStream *stream, const void *data, gsize length,
                          GError **error) {
  gsize written = 0;
  return g_output_stream_write_all(stream, data, length, &written, NULL, error) &&
         written == length;
}

static gboolean read_all(GInputStream *stream, void *data, gsize length,
                         GError **error) {
  gsize received = 0;
  return g_input_stream_read_all(stream, data, length, &received, NULL, error) &&
         received == length;
}

static gboolean send_node(GOutputStream *stream, JsonNode *node, GError **error) {
  JsonGenerator *generator = json_generator_new();
  json_generator_set_root(generator, node);
  gsize length = 0;
  char *payload = json_generator_to_data(generator, &length);
  gboolean ok = FALSE;
  if (length > 0 && length <= LUMA_MAX_MESSAGE) {
    uint32_t native_length = (uint32_t)length;
    ok = write_all(stream, &native_length, sizeof(native_length), error) &&
         write_all(stream, payload, length, error);
  } else {
    g_set_error_literal(error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                        "greetd message exceeds the size limit");
  }
  g_free(payload);
  g_object_unref(generator);
  return ok;
}

static JsonNode *receive_node(GInputStream *stream, GError **error) {
  uint32_t length = 0;
  if (!read_all(stream, &length, sizeof(length), error))
    return NULL;
  if (length == 0 || length > LUMA_MAX_MESSAGE) {
    g_set_error_literal(error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                        "invalid greetd response length");
    return NULL;
  }
  char *payload = g_malloc(length + 1);
  if (!read_all(stream, payload, length, error)) {
    secure_clear(payload, length + 1);
    g_free(payload);
    return NULL;
  }
  payload[length] = '\0';
  JsonParser *parser = json_parser_new();
  if (!json_parser_load_from_data(parser, payload, length, error)) {
    secure_clear(payload, length + 1);
    g_free(payload);
    g_object_unref(parser);
    return NULL;
  }
  JsonNode *node = json_node_copy(json_parser_get_root(parser));
  secure_clear(payload, length + 1);
  g_free(payload);
  g_object_unref(parser);
  return node;
}

static JsonNode *object_message(const char *type) {
  JsonBuilder *builder = json_builder_new();
  json_builder_begin_object(builder);
  json_builder_set_member_name(builder, "type");
  json_builder_add_string_value(builder, type);
  json_builder_end_object(builder);
  JsonNode *node = json_builder_get_root(builder);
  g_object_unref(builder);
  return node;
}

static JsonNode *create_session_message(const char *username) {
  JsonBuilder *builder = json_builder_new();
  json_builder_begin_object(builder);
  json_builder_set_member_name(builder, "type");
  json_builder_add_string_value(builder, "create_session");
  json_builder_set_member_name(builder, "username");
  json_builder_add_string_value(builder, username);
  json_builder_end_object(builder);
  JsonNode *node = json_builder_get_root(builder);
  g_object_unref(builder);
  return node;
}

static JsonNode *auth_response_message(const char *response) {
  JsonBuilder *builder = json_builder_new();
  json_builder_begin_object(builder);
  json_builder_set_member_name(builder, "type");
  json_builder_add_string_value(builder, "post_auth_message_response");
  json_builder_set_member_name(builder, "response");
  if (response != NULL)
    json_builder_add_string_value(builder, response);
  else
    json_builder_add_null_value(builder);
  json_builder_end_object(builder);
  JsonNode *node = json_builder_get_root(builder);
  g_object_unref(builder);
  return node;
}

static JsonNode *start_session_message(const char *command) {
  JsonBuilder *builder = json_builder_new();
  json_builder_begin_object(builder);
  json_builder_set_member_name(builder, "type");
  json_builder_add_string_value(builder, "start_session");
  json_builder_set_member_name(builder, "cmd");
  json_builder_begin_array(builder);
  json_builder_add_string_value(builder, command);
  json_builder_end_array(builder);
  json_builder_set_member_name(builder, "env");
  json_builder_begin_array(builder);
  json_builder_add_string_value(builder,
                                "WAYLAND_DISPLAY=/run/luma-display/wayland-0");
  json_builder_add_string_value(builder, "LUMA_DEVICE_CLASS=handheld");
  json_builder_add_string_value(builder,
                                "LUMA_PRESENTATION_MODE=fullscreen-mobile");
  json_builder_add_string_value(builder, "LUMA_INPUT_MODE=touch");
  /* Read by pam_systemd before the session is opened.  The session claims no
   * seat and no VT: the display belongs to the compositor that outlives the
   * PAM boundary, and this session is one of its clients. */
  json_builder_add_string_value(builder, "XDG_SESSION_TYPE=wayland");
  json_builder_end_array(builder);
  json_builder_end_object(builder);
  JsonNode *node = json_builder_get_root(builder);
  g_object_unref(builder);
  return node;
}

static gboolean exchange(GOutputStream *output, GInputStream *input,
                         JsonNode *request, JsonNode **reply, GError **error) {
  gboolean ok = send_node(output, request, error);
  json_node_free(request);
  if (!ok)
    return FALSE;
  *reply = receive_node(input, error);
  return *reply != NULL;
}

static void authenticate_thread(GTask *task, gpointer source_object,
                                gpointer task_data, GCancellable *cancellable) {
  (void)source_object;
  (void)cancellable;
  AuthRequest *request = task_data;
  GError *error = NULL;
  GSocketClient *client = g_socket_client_new();
  GSocketAddress *address = g_unix_socket_address_new(request->socket_path);
  GSocketConnection *connection =
      g_socket_client_connect(client, G_SOCKET_CONNECTABLE(address), NULL, &error);
  g_object_unref(address);
  g_object_unref(client);
  if (connection == NULL) {
    g_task_return_error(task, error);
    return;
  }

  GInputStream *input = g_io_stream_get_input_stream(G_IO_STREAM(connection));
  GOutputStream *output = g_io_stream_get_output_stream(G_IO_STREAM(connection));
  JsonNode *reply = NULL;
  gboolean starting = FALSE;
  guint secret_prompts = 0;

  if (!exchange(output, input, create_session_message(request->username), &reply,
                &error))
    goto failed;

  while (reply != NULL) {
    if (!JSON_NODE_HOLDS_OBJECT(reply)) {
      g_set_error_literal(&error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                          "invalid greetd response");
      goto failed;
    }
    JsonObject *object = json_node_get_object(reply);
    const char *type = json_object_get_string_member_with_default(object, "type", "");
    if (g_str_equal(type, "auth_message")) {
      const char *message_type = json_object_get_string_member_with_default(
          object, "auth_message_type", "");
      const char *response = NULL;
      if (g_str_equal(message_type, "secret")) {
        if (++secret_prompts != 1) {
          g_set_error_literal(&error, G_IO_ERROR, G_IO_ERROR_FAILED,
                              "authentication requested an unexpected second secret");
          goto failed;
        }
        response = request->pin;
      } else if (!g_str_equal(message_type, "info") &&
                 !g_str_equal(message_type, "error")) {
        g_set_error_literal(&error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
                            "unsupported authentication prompt");
        goto failed;
      }
      json_node_free(reply);
      reply = NULL;
      if (!exchange(output, input, auth_response_message(response), &reply, &error))
        goto failed;
    } else if (g_str_equal(type, "success")) {
      json_node_free(reply);
      reply = NULL;
      if (starting) {
        g_io_stream_close(G_IO_STREAM(connection), NULL, NULL);
        g_object_unref(connection);
        g_task_return_boolean(task, TRUE);
        return;
      }
      starting = TRUE;
      g_unlink(LUMA_HOME_READY);
      if (!exchange(output, input, start_session_message(request->session_command),
                    &reply, &error))
        goto failed;
    } else if (g_str_equal(type, "error")) {
      const char *error_type = json_object_get_string_member_with_default(
          object, "error_type", "authentication_error");
      g_set_error(&error, G_IO_ERROR, G_IO_ERROR_PERMISSION_DENIED,
                  "greetd rejected authentication (%s)", error_type);
      goto failed;
    } else {
      g_set_error_literal(&error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                          "unexpected greetd response");
      goto failed;
    }
  }

failed:
  if (reply != NULL)
    json_node_free(reply);
  JsonNode *cancel = object_message("cancel_session");
  send_node(output, cancel, NULL);
  json_node_free(cancel);
  g_io_stream_close(G_IO_STREAM(connection), NULL, NULL);
  g_object_unref(connection);
  if (error == NULL)
    error = g_error_new_literal(G_IO_ERROR, G_IO_ERROR_FAILED,
                                "authentication did not complete");
  g_task_return_error(task, error);
}

static void auth_request_free(AuthRequest *request) {
  secure_clear(request->pin, sizeof(request->pin));
  g_free(request->socket_path);
  g_free(request->username);
  g_free(request->session_command);
  g_free(request);
}

static void update_dots(LumaGreeter *greeter) {
  for (guint index = 0; index < LUMA_PIN_DIGITS; ++index) {
    if (index < greeter->pin_length)
      gtk_widget_add_css_class(greeter->dots[index], "filled");
    else
      gtk_widget_remove_css_class(greeter->dots[index], "filled");
  }
  char *description = g_strdup_printf("Passcode, %u of 4 digits entered",
                                      greeter->pin_length);
  gtk_accessible_update_property(GTK_ACCESSIBLE(greeter->keypad),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL, description, -1);
  g_free(description);
}

static void clear_pin(LumaGreeter *greeter) {
  secure_clear(greeter->pin, sizeof(greeter->pin));
  greeter->pin_length = 0;
  update_dots(greeter);
}

static gboolean home_ready(gpointer data) {
  (void)data;
  if (!g_file_test(LUMA_HOME_READY, G_FILE_TEST_IS_REGULAR))
    return G_SOURCE_CONTINUE;
  _exit(EXIT_SUCCESS);
}

static void begin_graphical_handoff(LumaGreeter *greeter) {
  /* Home renders behind Presence on the same compositor. Presence remains the
   * visible input owner until the complete first Home frame is committed. */
  gtk_widget_set_sensitive(GTK_WIDGET(greeter->window), FALSE);
  g_clear_handle_id(&greeter->idle_source, g_source_remove);
  g_timeout_add(16, home_ready, greeter);
}

static gboolean release_rate_limit(gpointer data) {
  LumaGreeter *greeter = data;
  greeter->submitting = FALSE;
  gtk_widget_set_sensitive(greeter->keypad, TRUE);
  gtk_widget_set_sensitive(greeter->emergency,
                           emergency_service_available());
  gtk_widget_grab_focus(gtk_widget_get_first_child(greeter->keypad));
  return G_SOURCE_REMOVE;
}

static void authentication_complete(GObject *source, GAsyncResult *result,
                                    gpointer data) {
  (void)source;
  LumaGreeter *greeter = data;
  GError *error = NULL;
  if (g_task_propagate_boolean(G_TASK(result), &error)) {
    begin_graphical_handoff(greeter);
    return;
  }
  g_clear_error(&error);
  clear_pin(greeter);
  greeter->failure_count++;
  gtk_label_set_text(greeter->feedback, "Passcode incorrect. Try again.");
  guint delay = 1U << MIN(greeter->failure_count - 1, 4U);
  g_timeout_add_seconds(MIN(delay, 30U), release_rate_limit, greeter);
}

static void submit_pin(LumaGreeter *greeter) {
  if (greeter->submitting || greeter->pin_length != LUMA_PIN_DIGITS)
    return;
  const char *socket_path = g_getenv("GREETD_SOCK");
  if (socket_path == NULL || *socket_path == '\0') {
    clear_pin(greeter);
    gtk_label_set_text(greeter->feedback, "Authentication service unavailable.");
    return;
  }
  AuthRequest *request = g_new0(AuthRequest, 1);
  request->greeter = greeter;
  memcpy(request->pin, greeter->pin, sizeof(request->pin));
  request->socket_path = g_strdup(socket_path);
  request->username = g_strdup(greeter->username);
  request->session_command = g_strdup(greeter->session_command);
  /* Keep the completed PIN surface visually unchanged while the authenticated
   * Home client assembles behind Presence. The credential bytes are still
   * erased immediately; only the four non-secret completion dots remain until
   * the atomic handoff or an authentication failure. */
  secure_clear(greeter->pin, sizeof(greeter->pin));
  greeter->submitting = TRUE;
  gtk_widget_set_sensitive(greeter->keypad, FALSE);
  gtk_widget_set_sensitive(greeter->emergency, FALSE);

  GTask *task = g_task_new(NULL, NULL, authentication_complete, greeter);
  g_task_set_task_data(task, request, (GDestroyNotify)auth_request_free);
  g_task_run_in_thread(task, authenticate_thread);
  g_object_unref(task);
}

static void digit_clicked(GtkButton *button, gpointer data) {
  LumaGreeter *greeter = data;
  if (greeter->submitting || greeter->pin_length >= LUMA_PIN_DIGITS)
    return;
  const char *label = gtk_button_get_label(button);
  if (label == NULL || label[0] < '0' || label[0] > '9' || label[1] != '\0')
    return;
  greeter->pin[greeter->pin_length++] = label[0];
  greeter->pin[greeter->pin_length] = '\0';
  gtk_label_set_text(greeter->feedback, "");
  update_dots(greeter);
  if (greeter->pin_length == LUMA_PIN_DIGITS)
    submit_pin(greeter);
}

static void delete_clicked(GtkButton *button, gpointer data) {
  (void)button;
  LumaGreeter *greeter = data;
  if (greeter->submitting || greeter->pin_length == 0)
    return;
  greeter->pin[--greeter->pin_length] = '\0';
  update_dots(greeter);
}

static void draw_backspace(GtkDrawingArea *area, cairo_t *cr, int width,
                           int height, gpointer data) {
  (void)area;
  (void)data;
  const double x = (width - 22.0) / 2.0;
  const double y = (height - 22.0) / 2.0;
  cairo_set_source_rgba(cr, 1, 1, 1, 0.94);
  cairo_set_line_width(cr, 1.8);
  cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
  cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
  cairo_move_to(cr, x + 9, y + 4);
  cairo_line_to(cr, x + 3, y + 11);
  cairo_line_to(cr, x + 9, y + 18);
  cairo_line_to(cr, x + 19, y + 18);
  cairo_curve_to(cr, x + 20.7, y + 18, x + 21.5, y + 17, x + 21.5, y + 15.5);
  cairo_line_to(cr, x + 21.5, y + 6.5);
  cairo_curve_to(cr, x + 21.5, y + 5, x + 20.7, y + 4, x + 19, y + 4);
  cairo_close_path(cr);
  cairo_stroke(cr);
  cairo_move_to(cr, x + 11, y + 8);
  cairo_line_to(cr, x + 17, y + 14);
  cairo_move_to(cr, x + 17, y + 8);
  cairo_line_to(cr, x + 11, y + 14);
  cairo_stroke(cr);
}

static void draw_phone(GtkDrawingArea *area, cairo_t *cr, int width, int height,
                       gpointer data) {
  (void)area;
  (void)data;
  const double scale = MIN(width, height) / 15.0;
  cairo_translate(cr, (width - 15.0 * scale) / 2.0,
                  (height - 15.0 * scale) / 2.0);
  cairo_scale(cr, scale, scale);
  cairo_set_source_rgba(cr, 1, 1, 1, 0.94);
  cairo_set_line_width(cr, 1.8);
  cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
  cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
  cairo_move_to(cr, 3.0, 1.8);
  cairo_curve_to(cr, 2.1, 1.8, 1.5, 2.5, 1.6, 3.4);
  cairo_curve_to(cr, 2.2, 8.6, 6.4, 12.8, 11.6, 13.4);
  cairo_curve_to(cr, 12.5, 13.5, 13.2, 12.9, 13.2, 12.0);
  cairo_line_to(cr, 13.2, 9.8);
  cairo_curve_to(cr, 13.2, 9.2, 12.8, 8.8, 12.3, 8.7);
  cairo_line_to(cr, 9.9, 8.3);
  cairo_curve_to(cr, 9.4, 8.2, 8.9, 8.4, 8.6, 8.8);
  cairo_line_to(cr, 7.9, 9.7);
  cairo_curve_to(cr, 6.3, 8.8, 5.0, 7.5, 4.1, 5.9);
  cairo_line_to(cr, 5.0, 5.2);
  cairo_curve_to(cr, 5.4, 4.9, 5.6, 4.4, 5.5, 3.9);
  cairo_line_to(cr, 5.1, 2.3);
  cairo_curve_to(cr, 5.0, 2.0, 4.6, 1.8, 4.3, 1.8);
  cairo_close_path(cr);
  cairo_stroke(cr);
}

static void show_passcode(LumaGreeter *greeter) {
  clear_pin(greeter);
  gtk_label_set_text(greeter->feedback, "");
  gtk_stack_set_visible_child_name(greeter->stack, "passcode");
  gtk_widget_grab_focus(gtk_widget_get_first_child(greeter->keypad));
}

static void show_clock(LumaGreeter *greeter) {
  if (greeter->submitting)
    return;
  clear_pin(greeter);
  gtk_label_set_text(greeter->feedback, "");
  gtk_stack_set_visible_child_name(greeter->stack, "clock");
}

static void back_drag_begin(GtkGestureDrag *gesture, double start_x,
                            double start_y, gpointer data) {
  (void)gesture;
  (void)start_y;
  LumaGreeter *greeter = data;
  const char *page = gtk_stack_get_visible_child_name(greeter->stack);
  greeter->back_drag_armed =
      !greeter->submitting && g_str_equal(page, "passcode") && start_x <= 36.0;
}

static void back_drag_end(GtkGestureDrag *gesture, double offset_x,
                          double offset_y, gpointer data) {
  (void)gesture;
  LumaGreeter *greeter = data;
  gboolean go_back = greeter->back_drag_armed && offset_x >= 64.0 &&
                     offset_x > 1.35 * ABS(offset_y);
  greeter->back_drag_armed = FALSE;
  if (go_back)
    show_clock(greeter);
}

static void clock_pressed(GtkGestureClick *gesture, int presses, double x,
                          double y, gpointer data) {
  (void)gesture;
  (void)presses;
  (void)x;
  (void)y;
  show_passcode(data);
}

static gboolean key_pressed(GtkEventControllerKey *controller, guint keyval,
                            guint keycode, GdkModifierType state, gpointer data) {
  (void)controller;
  (void)keycode;
  (void)state;
  LumaGreeter *greeter = data;
  const char *page = gtk_stack_get_visible_child_name(greeter->stack);
  if (g_str_equal(page, "clock")) {
    if (keyval == GDK_KEY_Return || keyval == GDK_KEY_KP_Enter ||
        keyval == GDK_KEY_space) {
      show_passcode(greeter);
      return TRUE;
    }
    return FALSE;
  }
  if (g_str_equal(page, "emergency")) {
    if (keyval == GDK_KEY_Escape || keyval == GDK_KEY_Back) {
      emergency_cancel_clicked(NULL, greeter);
      return TRUE;
    }
    if (keyval == GDK_KEY_BackSpace || keyval == GDK_KEY_Delete) {
      emergency_delete_clicked(NULL, greeter);
      return TRUE;
    }
    if (keyval == GDK_KEY_Return || keyval == GDK_KEY_KP_Enter) {
      if (gtk_widget_get_sensitive(greeter->emergency_call))
        emergency_call_clicked(GTK_BUTTON(greeter->emergency_call), greeter);
      return TRUE;
    }
    if (keyval >= GDK_KEY_0 && keyval <= GDK_KEY_9) {
      char digit[2] = {(char)('0' + keyval - GDK_KEY_0), '\0'};
      GtkWidget *temporary = gtk_button_new_with_label(digit);
      emergency_digit_clicked(GTK_BUTTON(temporary), greeter);
      g_object_ref_sink(temporary);
      g_object_unref(temporary);
      return TRUE;
    }
    return FALSE;
  }
  if (keyval == GDK_KEY_Escape || keyval == GDK_KEY_Back) {
    show_clock(greeter);
    return TRUE;
  }
  if (keyval == GDK_KEY_BackSpace || keyval == GDK_KEY_Delete) {
    delete_clicked(NULL, greeter);
    return TRUE;
  }
  if (keyval >= GDK_KEY_0 && keyval <= GDK_KEY_9) {
    char digit[2] = {(char)('0' + keyval - GDK_KEY_0), '\0'};
    GtkWidget *fake = gtk_button_new_with_label(digit);
    digit_clicked(GTK_BUTTON(fake), greeter);
    g_object_ref_sink(fake);
    g_object_unref(fake);
    return TRUE;
  }
  return FALSE;
}

static void update_emergency_number(LumaGreeter *greeter) {
  gtk_label_set_text(greeter->emergency_number_label,
                     greeter->emergency_number_length == 0
                         ? "Enter emergency number"
                         : greeter->emergency_number);
  gtk_widget_set_sensitive(greeter->emergency_call,
                           greeter->emergency_call_uni != NULL ||
                               greeter->emergency_number_length >= 3);
}

static void clear_emergency_number(LumaGreeter *greeter) {
  secure_clear(greeter->emergency_number, sizeof(greeter->emergency_number));
  greeter->emergency_number_length = 0;
  update_emergency_number(greeter);
}

static void emergency_digit_clicked(GtkButton *button, gpointer data) {
  LumaGreeter *greeter = data;
  if (greeter->emergency_call_uni != NULL ||
      greeter->emergency_number_length >= LUMA_EMERGENCY_DIGITS)
    return;
  const char *label = gtk_button_get_label(button);
  if (label == NULL || label[0] < '0' || label[0] > '9' || label[1] != '\0')
    return;
  greeter->emergency_number[greeter->emergency_number_length++] = label[0];
  greeter->emergency_number[greeter->emergency_number_length] = '\0';
  gtk_label_set_text(greeter->emergency_status, "");
  update_emergency_number(greeter);
}

static void emergency_delete_clicked(GtkButton *button, gpointer data) {
  (void)button;
  LumaGreeter *greeter = data;
  if (greeter->emergency_call_uni != NULL ||
      greeter->emergency_number_length == 0)
    return;
  greeter->emergency_number[--greeter->emergency_number_length] = '\0';
  update_emergency_number(greeter);
}

static gboolean poll_emergency_call(gpointer data) {
  LumaGreeter *greeter = data;
  if (greeter->emergency_call_uni == NULL) {
    greeter->emergency_poll_source = 0;
    return G_SOURCE_REMOVE;
  }
  GError *error = NULL;
  GDBusConnection *bus = g_bus_get_sync(G_BUS_TYPE_SYSTEM, NULL, &error);
  if (bus == NULL) {
    g_clear_error(&error);
    gtk_label_set_text(greeter->emergency_status,
                       "Emergency call status is unavailable.");
    return G_SOURCE_CONTINUE;
  }
  GVariant *reply = g_dbus_connection_call_sync(
      bus, LUMA_IMS_BUS, LUMA_IMS_PATH, LUMA_IMS_INTERFACE,
      "GetEmergencyCallState", g_variant_new("(s)", greeter->emergency_call_uni),
      G_VARIANT_TYPE("(ss)"), G_DBUS_CALL_FLAGS_NONE, 2000, NULL, &error);
  g_object_unref(bus);
  if (reply == NULL) {
    g_clear_error(&error);
    gtk_label_set_text(greeter->emergency_status,
                       "Emergency call status is unavailable.");
    return G_SOURCE_CONTINUE;
  }
  const char *state = NULL;
  const char *reason = NULL;
  g_variant_get(reply, "(&s&s)", &state, &reason);
  if (g_str_equal(state, "active"))
    gtk_label_set_text(greeter->emergency_status,
                       "Emergency call connected");
  else if (g_str_equal(state, "terminated")) {
    gtk_label_set_text(greeter->emergency_status,
                       (reason != NULL && g_str_equal(reason, "local-hangup"))
                           ? "Emergency call ended."
                           : "Emergency call disconnected.");
    secure_clear(greeter->emergency_call_uni,
                 strlen(greeter->emergency_call_uni));
    g_clear_pointer(&greeter->emergency_call_uni, g_free);
    gtk_button_set_label(GTK_BUTTON(greeter->emergency_call), "Call");
    gtk_widget_set_sensitive(greeter->emergency_keypad, TRUE);
    clear_emergency_number(greeter);
    g_variant_unref(reply);
    greeter->emergency_poll_source = 0;
    return G_SOURCE_REMOVE;
  } else
    gtk_label_set_text(greeter->emergency_status,
                       "Calling emergency services…");
  g_variant_unref(reply);
  return G_SOURCE_CONTINUE;
}

static void emergency_call_clicked(GtkButton *button, gpointer data) {
  LumaGreeter *greeter = data;
  GError *error = NULL;
  GDBusConnection *bus = g_bus_get_sync(G_BUS_TYPE_SYSTEM, NULL, &error);
  if (bus == NULL) {
    g_clear_error(&error);
    gtk_label_set_text(greeter->emergency_status,
                       "Emergency calling is unavailable.");
    return;
  }
  if (greeter->emergency_call_uni != NULL) {
    GVariant *reply = g_dbus_connection_call_sync(
        bus, LUMA_IMS_BUS, LUMA_IMS_PATH, LUMA_IMS_INTERFACE,
        "HangUpEmergency", g_variant_new("(s)", greeter->emergency_call_uni),
        NULL, G_DBUS_CALL_FLAGS_NONE, 5000, NULL, &error);
    if (reply != NULL) {
      g_variant_unref(reply);
      gtk_label_set_text(greeter->emergency_status, "Ending emergency call…");
      gtk_widget_set_sensitive(GTK_WIDGET(button), FALSE);
    } else {
      g_clear_error(&error);
      gtk_label_set_text(greeter->emergency_status,
                         "Could not end the emergency call.");
    }
    g_object_unref(bus);
    return;
  }

  GVariant *reply = g_dbus_connection_call_sync(
      bus, LUMA_IMS_BUS, LUMA_IMS_PATH, LUMA_IMS_INTERFACE, "DialEmergency",
      g_variant_new("(s)", greeter->emergency_number), G_VARIANT_TYPE("(s)"),
      G_DBUS_CALL_FLAGS_NONE, 5000, NULL, &error);
  g_object_unref(bus);
  if (reply == NULL) {
    gtk_label_set_text(
        greeter->emergency_status,
        error != NULL && g_dbus_error_is_remote_error(error)
            ? "That number is not available for emergency calling."
            : "Emergency calling is unavailable.");
    g_clear_error(&error);
    return;
  }
  const char *uni = NULL;
  g_variant_get(reply, "(&s)", &uni);
  greeter->emergency_call_uni = g_strdup(uni);
  g_variant_unref(reply);
  secure_clear(greeter->emergency_number, sizeof(greeter->emergency_number));
  greeter->emergency_number_length = 0;
  gtk_label_set_text(greeter->emergency_number_label, "Emergency call");
  gtk_label_set_text(greeter->emergency_status,
                     "Calling emergency services…");
  gtk_button_set_label(GTK_BUTTON(button), "End call");
  gtk_widget_set_sensitive(greeter->emergency_keypad, FALSE);
  if (greeter->emergency_poll_source == 0)
    greeter->emergency_poll_source =
        g_timeout_add(500, poll_emergency_call, greeter);
}

static void emergency_cancel_clicked(GtkButton *button, gpointer data) {
  (void)button;
  LumaGreeter *greeter = data;
  if (greeter->emergency_call_uni != NULL) {
    gtk_label_set_text(greeter->emergency_status,
                       "End the emergency call before returning.");
    return;
  }
  clear_emergency_number(greeter);
  gtk_label_set_text(greeter->emergency_status, "");
  gtk_stack_set_visible_child_name(greeter->stack, "passcode");
}

static void emergency_clicked(GtkButton *button, gpointer data) {
  (void)button;
  LumaGreeter *greeter = data;
  clear_pin(greeter);
  clear_emergency_number(greeter);
  gtk_label_set_text(greeter->emergency_status, "");
  gtk_stack_set_visible_child_name(greeter->stack, "emergency");
  gtk_widget_grab_focus(gtk_widget_get_first_child(greeter->emergency_keypad));
}

static gboolean emergency_service_available(void) {
  GError *error = NULL;
  GDBusConnection *bus = g_bus_get_sync(G_BUS_TYPE_SYSTEM, NULL, &error);
  if (bus == NULL) {
    g_clear_error(&error);
    return FALSE;
  }
  GVariant *reply = g_dbus_connection_call_sync(
      bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
      "org.freedesktop.DBus", "NameHasOwner",
      g_variant_new("(s)", LUMA_IMS_BUS),
      G_VARIANT_TYPE("(b)"), G_DBUS_CALL_FLAGS_NONE, 2000, NULL, &error);
  g_object_unref(bus);
  if (reply == NULL) {
    g_clear_error(&error);
    return FALSE;
  }
  gboolean available = FALSE;
  g_variant_get(reply, "(b)", &available);
  g_variant_unref(reply);
  return available;
}

static gboolean update_clock(gpointer data) {
  LumaGreeter *greeter = data;
  GDateTime *now = g_date_time_new_now_local();
  char *time = g_date_time_format(now, "%-I:%M %p");
  char *date = g_date_time_format(now, "%A, %B %-d");
  gtk_label_set_text(greeter->clock_time, time);
  gtk_label_set_text(greeter->passcode_time, time);
  gtk_label_set_text(greeter->clock_date, date);
  gtk_label_set_text(greeter->passcode_date, date);
  g_free(time);
  g_free(date);
  g_date_time_unref(now);
  return G_SOURCE_CONTINUE;
}

static GtkWidget *label_with_class(const char *text, const char *css_class) {
  GtkWidget *label = gtk_label_new(text);
  gtk_widget_add_css_class(label, css_class);
  return label;
}

static GtkWidget *build_clock_page(LumaGreeter *greeter) {
  GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(page, "presence-page");
  gtk_widget_add_css_class(page, "clock-state");
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(content, "clock-content");
  gtk_widget_set_halign(content, GTK_ALIGN_CENTER);
  gtk_widget_set_vexpand(content, TRUE);
  GtkWidget *time = label_with_class("", "clock-time");
  GtkWidget *date = label_with_class("", "clock-date");
  GtkWidget *hint = label_with_class("Tap to unlock", "unlock-hint");
  gtk_widget_set_halign(hint, GTK_ALIGN_CENTER);
  greeter->clock_time = GTK_LABEL(time);
  greeter->clock_date = GTK_LABEL(date);
  gtk_box_append(GTK_BOX(content), time);
  gtk_box_append(GTK_BOX(content), date);
  gtk_box_append(GTK_BOX(page), content);
  gtk_box_append(GTK_BOX(page), hint);
  GtkGesture *tap = gtk_gesture_click_new();
  g_signal_connect(tap, "pressed", G_CALLBACK(clock_pressed), greeter);
  gtk_widget_add_controller(page, GTK_EVENT_CONTROLLER(tap));
  gtk_accessible_update_property(GTK_ACCESSIBLE(page),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL,
                                 "Locked. Tap to unlock", -1);
  return page;
}

static GtkWidget *build_avatar(const char *username, const char *display_name) {
  GtkWidget *frame = gtk_overlay_new();
  gtk_widget_add_css_class(frame, "avatar-frame");
  gtk_widget_set_size_request(frame, 68, 68);
  gtk_widget_set_halign(frame, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(frame, GTK_ALIGN_CENTER);
  gtk_widget_set_hexpand(frame, FALSE);
  gtk_widget_set_vexpand(frame, FALSE);
  gtk_widget_set_overflow(frame, GTK_OVERFLOW_HIDDEN);
  char *path = g_strdup_printf("/var/lib/AccountsService/icons/%s", username);
  if (g_file_test(path, G_FILE_TEST_IS_REGULAR)) {
    GtkWidget *picture = gtk_picture_new_for_filename(path);
    gtk_widget_set_size_request(picture, 68, 68);
    gtk_widget_set_halign(picture, GTK_ALIGN_CENTER);
    gtk_widget_set_valign(picture, GTK_ALIGN_CENTER);
    gtk_widget_set_hexpand(picture, FALSE);
    gtk_widget_set_vexpand(picture, FALSE);
    gtk_picture_set_content_fit(GTK_PICTURE(picture), GTK_CONTENT_FIT_COVER);
    gtk_widget_add_css_class(picture, "avatar-image");
    gtk_overlay_set_child(GTK_OVERLAY(frame), picture);
  } else {
    char initial[8] = "?";
    if (display_name != NULL && *display_name != '\0') {
      gunichar codepoint = g_utf8_get_char(display_name);
      gint bytes = g_unichar_to_utf8(g_unichar_toupper(codepoint), initial);
      initial[bytes] = '\0';
    }
    GtkWidget *fallback = label_with_class(initial, "avatar-initial");
    gtk_widget_set_halign(fallback, GTK_ALIGN_CENTER);
    gtk_widget_set_valign(fallback, GTK_ALIGN_CENTER);
    gtk_overlay_set_child(GTK_OVERLAY(frame), fallback);
  }
  g_free(path);
  return frame;
}

static GtkWidget *build_keypad(LumaGreeter *greeter) {
  GtkWidget *grid = gtk_grid_new();
  greeter->keypad = grid;
  gtk_widget_add_css_class(grid, "keypad");
  gtk_widget_set_halign(grid, GTK_ALIGN_CENTER);
  gtk_grid_set_row_spacing(GTK_GRID(grid), 12);
  gtk_grid_set_column_spacing(GTK_GRID(grid), 12);
  const char *digits[] = {"1", "2", "3", "4", "5", "6", "7", "8", "9"};
  for (guint index = 0; index < G_N_ELEMENTS(digits); ++index) {
    GtkWidget *button = gtk_button_new_with_label(digits[index]);
    gtk_widget_set_size_request(button, 88, 64);
    g_signal_connect(button, "clicked", G_CALLBACK(digit_clicked), greeter);
    gtk_grid_attach(GTK_GRID(grid), button, index % 3, index / 3, 1, 1);
  }
  GtkWidget *zero = gtk_button_new_with_label("0");
  gtk_widget_set_size_request(zero, 88, 64);
  g_signal_connect(zero, "clicked", G_CALLBACK(digit_clicked), greeter);
  gtk_grid_attach(GTK_GRID(grid), zero, 1, 3, 1, 1);

  GtkWidget *delete = gtk_button_new();
  gtk_widget_set_size_request(delete, 88, 64);
  GtkWidget *icon = gtk_drawing_area_new();
  gtk_widget_add_css_class(icon, "delete-icon");
  gtk_drawing_area_set_draw_func(GTK_DRAWING_AREA(icon), draw_backspace, NULL, NULL);
  gtk_button_set_child(GTK_BUTTON(delete), icon);
  gtk_accessible_update_property(GTK_ACCESSIBLE(delete),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL,
                                 "Delete last digit", -1);
  g_signal_connect(delete, "clicked", G_CALLBACK(delete_clicked), greeter);
  gtk_grid_attach(GTK_GRID(grid), delete, 2, 3, 1, 1);
  return grid;
}

static GtkWidget *build_emergency_keypad(LumaGreeter *greeter) {
  GtkWidget *grid = gtk_grid_new();
  greeter->emergency_keypad = grid;
  gtk_widget_add_css_class(grid, "emergency-keypad");
  gtk_widget_set_halign(grid, GTK_ALIGN_CENTER);
  gtk_grid_set_row_spacing(GTK_GRID(grid), 12);
  gtk_grid_set_column_spacing(GTK_GRID(grid), 12);
  const char *digits[] = {"1", "2", "3", "4", "5", "6", "7", "8", "9"};
  for (guint index = 0; index < G_N_ELEMENTS(digits); ++index) {
    GtkWidget *button = gtk_button_new_with_label(digits[index]);
    gtk_widget_set_size_request(button, 76, 56);
    g_signal_connect(button, "clicked", G_CALLBACK(emergency_digit_clicked),
                     greeter);
    gtk_grid_attach(GTK_GRID(grid), button, index % 3, index / 3, 1, 1);
  }
  GtkWidget *zero = gtk_button_new_with_label("0");
  gtk_widget_set_size_request(zero, 76, 56);
  g_signal_connect(zero, "clicked", G_CALLBACK(emergency_digit_clicked), greeter);
  gtk_grid_attach(GTK_GRID(grid), zero, 1, 3, 1, 1);
  GtkWidget *remove = gtk_button_new();
  gtk_widget_set_size_request(remove, 76, 56);
  GtkWidget *icon = gtk_drawing_area_new();
  gtk_widget_add_css_class(icon, "delete-icon");
  gtk_drawing_area_set_draw_func(GTK_DRAWING_AREA(icon), draw_backspace, NULL,
                                 NULL);
  gtk_button_set_child(GTK_BUTTON(remove), icon);
  gtk_accessible_update_property(GTK_ACCESSIBLE(remove),
                                 GTK_ACCESSIBLE_PROPERTY_LABEL,
                                 "Delete last digit", -1);
  g_signal_connect(remove, "clicked", G_CALLBACK(emergency_delete_clicked),
                   greeter);
  gtk_grid_attach(GTK_GRID(grid), remove, 2, 3, 1, 1);
  return grid;
}

static GtkWidget *build_emergency_page(LumaGreeter *greeter) {
  GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  greeter->emergency_page = page;
  gtk_widget_add_css_class(page, "presence-page");
  gtk_widget_add_css_class(page, "emergency-state");

  GtkWidget *title = label_with_class("Emergency call", "emergency-title");
  GtkWidget *explanation = label_with_class(
      "Call emergency services without unlocking", "emergency-explanation");
  gtk_label_set_wrap(GTK_LABEL(explanation), TRUE);
  gtk_label_set_justify(GTK_LABEL(explanation), GTK_JUSTIFY_CENTER);
  gtk_box_append(GTK_BOX(page), title);
  gtk_box_append(GTK_BOX(page), explanation);

  greeter->emergency_number_label =
      GTK_LABEL(label_with_class("Enter emergency number", "emergency-number"));
  gtk_box_append(GTK_BOX(page), GTK_WIDGET(greeter->emergency_number_label));
  gtk_box_append(GTK_BOX(page), build_emergency_keypad(greeter));

  greeter->emergency_status =
      GTK_LABEL(label_with_class("", "emergency-status"));
  gtk_label_set_wrap(greeter->emergency_status, TRUE);
  gtk_label_set_justify(greeter->emergency_status, GTK_JUSTIFY_CENTER);
  gtk_box_append(GTK_BOX(page), GTK_WIDGET(greeter->emergency_status));

  GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 12);
  gtk_widget_add_css_class(actions, "emergency-actions");
  gtk_widget_set_halign(actions, GTK_ALIGN_CENTER);
  GtkWidget *cancel = gtk_button_new_with_label("Cancel");
  greeter->emergency_cancel = cancel;
  gtk_widget_add_css_class(cancel, "emergency-secondary");
  g_signal_connect(cancel, "clicked", G_CALLBACK(emergency_cancel_clicked),
                   greeter);
  GtkWidget *call = gtk_button_new_with_label("Call");
  greeter->emergency_call = call;
  gtk_widget_add_css_class(call, "emergency-primary");
  gtk_widget_set_sensitive(call, FALSE);
  g_signal_connect(call, "clicked", G_CALLBACK(emergency_call_clicked), greeter);
  gtk_box_append(GTK_BOX(actions), cancel);
  gtk_box_append(GTK_BOX(actions), call);
  gtk_box_append(GTK_BOX(page), actions);
  return page;
}

static GtkWidget *build_passcode_page(LumaGreeter *greeter,
                                      const char *display_name) {
  GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  greeter->passcode_page = page;
  gtk_widget_add_css_class(page, "presence-page");
  gtk_widget_add_css_class(page, "passcode-state");

  GtkWidget *time = label_with_class("", "passcode-time");
  GtkWidget *date = label_with_class("", "passcode-date");
  greeter->passcode_time = GTK_LABEL(time);
  greeter->passcode_date = GTK_LABEL(date);
  gtk_box_append(GTK_BOX(page), time);
  gtk_box_append(GTK_BOX(page), date);

  GtkWidget *zone = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class(zone, "identity-zone");
  gtk_widget_set_vexpand(zone, TRUE);
  gtk_widget_set_valign(zone, GTK_ALIGN_FILL);
  GtkWidget *identity = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_halign(identity, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(identity, GTK_ALIGN_CENTER);
  gtk_widget_set_vexpand(identity, TRUE);
  gtk_box_append(GTK_BOX(identity), build_avatar(greeter->username, display_name));
  GtkWidget *name = label_with_class(display_name, "identity-name");
  gtk_label_set_ellipsize(GTK_LABEL(name), PANGO_ELLIPSIZE_END);
  gtk_label_set_max_width_chars(GTK_LABEL(name), 24);
  gtk_box_append(GTK_BOX(identity), name);
  GtkWidget *dots = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 12);
  gtk_widget_add_css_class(dots, "passcode-dots");
  gtk_widget_set_halign(dots, GTK_ALIGN_CENTER);
  for (guint index = 0; index < LUMA_PIN_DIGITS; ++index) {
    greeter->dots[index] = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(greeter->dots[index], "passcode-dot");
    gtk_box_append(GTK_BOX(dots), greeter->dots[index]);
  }
  gtk_box_append(GTK_BOX(identity), dots);
  gtk_box_append(GTK_BOX(zone), identity);
  gtk_box_append(GTK_BOX(page), zone);

  gtk_box_append(GTK_BOX(page), build_keypad(greeter));
  greeter->feedback = GTK_LABEL(label_with_class("", "feedback"));
  gtk_label_set_wrap(greeter->feedback, TRUE);
  gtk_label_set_justify(greeter->feedback, GTK_JUSTIFY_CENTER);
  gtk_box_append(GTK_BOX(page), GTK_WIDGET(greeter->feedback));

  GtkWidget *emergency = gtk_button_new();
  greeter->emergency = emergency;
  gtk_widget_add_css_class(emergency, "emergency-pill");
  gtk_widget_set_halign(emergency, GTK_ALIGN_CENTER);
  GtkWidget *content = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  GtkWidget *phone = gtk_drawing_area_new();
  gtk_widget_add_css_class(phone, "emergency-icon");
  gtk_drawing_area_set_draw_func(GTK_DRAWING_AREA(phone), draw_phone, NULL, NULL);
  gtk_box_append(GTK_BOX(content), phone);
  gtk_box_append(GTK_BOX(content), gtk_label_new("Emergency call"));
  gtk_button_set_child(GTK_BUTTON(emergency), content);
  g_signal_connect(emergency, "clicked", G_CALLBACK(emergency_clicked), greeter);
  gboolean emergency_ready = emergency_service_available();
  gtk_widget_set_sensitive(emergency, emergency_ready);
  if (!emergency_ready)
    gtk_widget_set_tooltip_text(
        emergency, "Native emergency calling is not available on this device.");
  gtk_box_append(GTK_BOX(page), emergency);
  return page;
}

static void height_changed(GObject *object, GParamSpec *pspec, gpointer data) {
  (void)object;
  (void)pspec;
  LumaGreeter *greeter = data;
  if (gtk_widget_get_height(greeter->passcode_page) < 760)
    gtk_widget_add_css_class(greeter->passcode_page, "compact");
  else
    gtk_widget_remove_css_class(greeter->passcode_page, "compact");
}

static char *account_display_name(const char *username) {
  struct passwd *account = getpwnam(username);
  if (account == NULL)
    return g_strdup(username);
  const char *gecos = account->pw_gecos;
  if (gecos == NULL || *gecos == '\0')
    return g_strdup(username);
  const char *comma = strchr(gecos, ',');
  if (comma == NULL)
    return g_strdup(gecos);
  return g_strndup(gecos, comma - gecos);
}

static void load_css(void) {
  GtkCssProvider *provider = gtk_css_provider_new();
  gtk_css_provider_load_from_path(provider,
                                  "/usr/share/luma-greeter/luma-greeter.css");
  gtk_style_context_add_provider_for_display(
      gdk_display_get_default(), GTK_STYLE_PROVIDER(provider),
      GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
  g_object_unref(provider);
}

static void activate(GtkApplication *application, gpointer data) {
  LumaGreeter *greeter = data;
  greeter->application = application;
  load_css();
  char *display_name = account_display_name(greeter->username);

  GtkWidget *window = gtk_application_window_new(application);
  greeter->window = GTK_WINDOW(window);
  gtk_window_set_title(GTK_WINDOW(window), "Luma");
  gtk_window_set_default_size(GTK_WINDOW(window), 390, 844);
  gtk_window_set_decorated(GTK_WINDOW(window), FALSE);
  gtk_window_fullscreen(GTK_WINDOW(window));
  gtk_widget_add_css_class(window, "presence-root");

  GtkWidget *overlay = gtk_overlay_new();
  GtkWidget *wallpaper = gtk_picture_new_for_filename(LUMA_WALLPAPER);
  gtk_picture_set_content_fit(GTK_PICTURE(wallpaper), GTK_CONTENT_FIT_COVER);
  gtk_widget_set_can_target(wallpaper, FALSE);
  gtk_overlay_set_child(GTK_OVERLAY(overlay), wallpaper);

  GtkWidget *stack = gtk_stack_new();
  greeter->stack = GTK_STACK(stack);
  gtk_widget_add_css_class(stack, "presence-stack");
  gtk_stack_set_transition_type(GTK_STACK(stack),
                                GTK_STACK_TRANSITION_TYPE_CROSSFADE);
  gtk_stack_set_transition_duration(GTK_STACK(stack), 260);
  gtk_stack_add_named(GTK_STACK(stack), build_clock_page(greeter), "clock");
  gtk_stack_add_named(GTK_STACK(stack),
                      build_passcode_page(greeter, display_name), "passcode");
  gtk_stack_add_named(GTK_STACK(stack), build_emergency_page(greeter),
                      "emergency");
  gtk_stack_set_visible_child_name(GTK_STACK(stack), "clock");
  gtk_overlay_add_overlay(GTK_OVERLAY(overlay), stack);
  gtk_window_set_child(GTK_WINDOW(window), overlay);

  GtkEventController *keys = gtk_event_controller_key_new();
  g_signal_connect(keys, "key-pressed", G_CALLBACK(key_pressed), greeter);
  gtk_widget_add_controller(window, keys);
  GtkEventController *activity = gtk_event_controller_legacy_new();
  gtk_event_controller_set_propagation_phase(activity, GTK_PHASE_CAPTURE);
  g_signal_connect(activity, "event", G_CALLBACK(activity_event), greeter);
  gtk_widget_add_controller(window, activity);
  GtkGesture *back_drag = gtk_gesture_drag_new();
  gtk_event_controller_set_propagation_phase(GTK_EVENT_CONTROLLER(back_drag),
                                             GTK_PHASE_CAPTURE);
  g_signal_connect(back_drag, "drag-begin", G_CALLBACK(back_drag_begin),
                   greeter);
  g_signal_connect(back_drag, "drag-end", G_CALLBACK(back_drag_end), greeter);
  gtk_widget_add_controller(greeter->passcode_page,
                            GTK_EVENT_CONTROLLER(back_drag));
  g_signal_connect(greeter->passcode_page, "notify::height",
                   G_CALLBACK(height_changed), greeter);

  update_clock(greeter);
  g_timeout_add_seconds(15, update_clock, greeter);
  greeter->backlight_name = discover_backlight(&greeter->panel_brightness);
  arm_idle_timeout(greeter);
  gtk_window_present(GTK_WINDOW(window));
  g_free(display_name);
}

int main(int argc, char **argv) {
  LumaGreeter greeter = {0};
  greeter.username = g_strdup(g_getenv("LUMA_GREETER_USER") ?: LUMA_DEFAULT_USER);
  greeter.session_command =
      g_strdup(g_getenv("LUMA_GREETER_SESSION") ?: LUMA_DEFAULT_SESSION);
  GtkApplication *application =
      gtk_application_new("org.projectluma.Greeter", G_APPLICATION_NON_UNIQUE);
  g_signal_connect(application, "activate", G_CALLBACK(activate), &greeter);
  int status = g_application_run(G_APPLICATION(application), argc, argv);
  /* Widgets have been finalized after the application loop returns.  Wipe the
   * secret directly instead of asking clear_pin() to repaint stale dot
   * pointers during process teardown. */
  secure_clear(greeter.pin, sizeof(greeter.pin));
  secure_clear(greeter.emergency_number, sizeof(greeter.emergency_number));
  g_clear_handle_id(&greeter.idle_source, g_source_remove);
  if (greeter.display_sleeping)
    set_panel_brightness(&greeter, greeter.panel_brightness);
  if (greeter.emergency_call_uni != NULL) {
    secure_clear(greeter.emergency_call_uni,
                 strlen(greeter.emergency_call_uni));
    g_clear_pointer(&greeter.emergency_call_uni, g_free);
  }
  greeter.pin_length = 0;
  g_clear_object(&application);
  g_free(greeter.username);
  g_free(greeter.session_command);
  g_free(greeter.backlight_name);
  return status;
}
