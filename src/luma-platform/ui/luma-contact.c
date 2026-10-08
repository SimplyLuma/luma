/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_contact.py: Person, contact_uri, ContactActions, MiniCard,
 * ContactCard, EventCard, SongCard, PlaceCard. StatusPill is in luma-badges.c. */
#include "luma-contact.h"
#include "luma-cards.h"
#include "luma-content-private.h"
#include "luma-stacked-button.h"
#include "luma-toast.h"
#include "luma-ui-private.h"

/* ── Person ─────────────────────────────────────────────────────────────── */

struct _LumaPerson {
  GObject parent_instance;
  char *name, *phone, *email, *username;
  gboolean online;
  GdkPaintable *picture;
  int hue;
};

G_DEFINE_FINAL_TYPE(LumaPerson, luma_person, G_TYPE_OBJECT)

enum {
  PERSON_PROP_0,
  PERSON_PROP_NAME,
  PERSON_PROP_PHONE,
  PERSON_PROP_EMAIL,
  PERSON_PROP_USERNAME,
  PERSON_PROP_ONLINE,
  PERSON_PROP_PICTURE,
  PERSON_PROP_ON_LUMA,
  PERSON_PROP_HUE,
  PERSON_N_PROPS
};
static GParamSpec *person_props[PERSON_N_PROPS];

static void person_set_string(LumaPerson *self, char **field, const char *value, guint prop) {
  if (g_strcmp0(*field, value != NULL ? value : "") == 0)
    return;
  g_free(*field);
  *field = g_strdup(value != NULL ? value : "");
  g_object_notify_by_pspec(G_OBJECT(self), person_props[prop]);
}

static void person_get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  LumaPerson *self = LUMA_PERSON(object);
  switch (id) {
  case PERSON_PROP_NAME:
    g_value_set_string(value, self->name);
    break;
  case PERSON_PROP_PHONE:
    g_value_set_string(value, self->phone);
    break;
  case PERSON_PROP_EMAIL:
    g_value_set_string(value, self->email);
    break;
  case PERSON_PROP_USERNAME:
    g_value_set_string(value, self->username);
    break;
  case PERSON_PROP_ONLINE:
    g_value_set_boolean(value, self->online);
    break;
  case PERSON_PROP_PICTURE:
    g_value_set_object(value, self->picture);
    break;
  case PERSON_PROP_ON_LUMA:
    g_value_set_boolean(value, luma_person_get_on_luma(self));
    break;
  case PERSON_PROP_HUE:
    g_value_set_int(value, self->hue);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void person_set_property(GObject *object, guint id, const GValue *value, GParamSpec *pspec) {
  LumaPerson *self = LUMA_PERSON(object);
  switch (id) {
  case PERSON_PROP_NAME:
    g_free(self->name);
    self->name = g_strdup(g_value_get_string(value) != NULL ? g_value_get_string(value) : "");
    break;
  case PERSON_PROP_PHONE:
    luma_person_set_phone(self, g_value_get_string(value));
    break;
  case PERSON_PROP_EMAIL:
    luma_person_set_email(self, g_value_get_string(value));
    break;
  case PERSON_PROP_USERNAME:
    luma_person_set_username(self, g_value_get_string(value));
    break;
  case PERSON_PROP_ONLINE:
    luma_person_set_online(self, g_value_get_boolean(value));
    break;
  case PERSON_PROP_PICTURE:
    luma_person_set_picture(self, g_value_get_object(value));
    break;
  case PERSON_PROP_HUE:
    luma_person_set_hue(self, g_value_get_int(value));
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void person_finalize(GObject *object) {
  LumaPerson *self = LUMA_PERSON(object);
  g_free(self->name);
  g_free(self->phone);
  g_free(self->email);
  g_free(self->username);
  g_clear_object(&self->picture);
  G_OBJECT_CLASS(luma_person_parent_class)->finalize(object);
}

static void luma_person_class_init(LumaPersonClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->get_property = person_get_property;
  object_class->set_property = person_set_property;
  object_class->finalize = person_finalize;
  const GParamFlags rw = G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS;
  person_props[PERSON_PROP_NAME] =
      g_param_spec_string("name", NULL, NULL, "", G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);
  person_props[PERSON_PROP_PHONE] = g_param_spec_string("phone", NULL, NULL, "", rw);
  person_props[PERSON_PROP_EMAIL] = g_param_spec_string("email", NULL, NULL, "", rw);
  person_props[PERSON_PROP_USERNAME] = g_param_spec_string("username", NULL, NULL, "", rw);
  person_props[PERSON_PROP_ONLINE] = g_param_spec_boolean("online", NULL, NULL, FALSE, rw);
  person_props[PERSON_PROP_PICTURE] = g_param_spec_object("picture", NULL, NULL, GDK_TYPE_PAINTABLE, rw);
  person_props[PERSON_PROP_ON_LUMA] =
      g_param_spec_boolean("on-luma", NULL, NULL, FALSE, G_PARAM_READABLE | G_PARAM_STATIC_STRINGS);
  person_props[PERSON_PROP_HUE] = g_param_spec_int("hue", NULL, NULL, -1, 359, -1, rw);
  g_object_class_install_properties(object_class, PERSON_N_PROPS, person_props);
}

static void luma_person_init(LumaPerson *self) {
  self->name = g_strdup("");
  self->phone = g_strdup("");
  self->email = g_strdup("");
  self->username = g_strdup("");
  self->hue = -1;
}

LumaPerson *luma_person_new(const char *name) {
  g_return_val_if_fail(name != NULL, NULL);
  return g_object_new(LUMA_TYPE_PERSON, "name", name, NULL);
}

const char *luma_person_get_name(LumaPerson *self) {
  g_return_val_if_fail(LUMA_IS_PERSON(self), NULL);
  return self->name;
}

void luma_person_set_phone(LumaPerson *self, const char *phone) {
  g_return_if_fail(LUMA_IS_PERSON(self));
  person_set_string(self, &self->phone, phone, PERSON_PROP_PHONE);
}

const char *luma_person_get_phone(LumaPerson *self) {
  g_return_val_if_fail(LUMA_IS_PERSON(self), NULL);
  return self->phone;
}

void luma_person_set_email(LumaPerson *self, const char *email) {
  g_return_if_fail(LUMA_IS_PERSON(self));
  person_set_string(self, &self->email, email, PERSON_PROP_EMAIL);
}

const char *luma_person_get_email(LumaPerson *self) {
  g_return_val_if_fail(LUMA_IS_PERSON(self), NULL);
  return self->email;
}

void luma_person_set_username(LumaPerson *self, const char *username) {
  g_return_if_fail(LUMA_IS_PERSON(self));
  gboolean was = luma_person_get_on_luma(self);
  person_set_string(self, &self->username, username, PERSON_PROP_USERNAME);
  if (was != luma_person_get_on_luma(self))
    g_object_notify_by_pspec(G_OBJECT(self), person_props[PERSON_PROP_ON_LUMA]);
}

const char *luma_person_get_username(LumaPerson *self) {
  g_return_val_if_fail(LUMA_IS_PERSON(self), NULL);
  return self->username;
}

void luma_person_set_online(LumaPerson *self, gboolean online) {
  g_return_if_fail(LUMA_IS_PERSON(self));
  online = !!online;
  if (self->online == online)
    return;
  self->online = online;
  g_object_notify_by_pspec(G_OBJECT(self), person_props[PERSON_PROP_ONLINE]);
}

gboolean luma_person_get_online(LumaPerson *self) {
  g_return_val_if_fail(LUMA_IS_PERSON(self), FALSE);
  return self->online;
}

void luma_person_set_picture(LumaPerson *self, GdkPaintable *picture) {
  g_return_if_fail(LUMA_IS_PERSON(self));
  g_return_if_fail(picture == NULL || GDK_IS_PAINTABLE(picture));
  if (g_set_object(&self->picture, picture))
    g_object_notify_by_pspec(G_OBJECT(self), person_props[PERSON_PROP_PICTURE]);
}

GdkPaintable *luma_person_get_picture(LumaPerson *self) {
  g_return_val_if_fail(LUMA_IS_PERSON(self), NULL);
  return self->picture;
}

gboolean luma_person_get_on_luma(LumaPerson *self) {
  g_return_val_if_fail(LUMA_IS_PERSON(self), FALSE);
  return self->username[0] != '\0';
}

void luma_person_set_hue(LumaPerson *self, int hue) {
  g_return_if_fail(LUMA_IS_PERSON(self));
  hue = hue < 0 ? -1 : hue % 360;
  if (self->hue == hue)
    return;
  self->hue = hue;
  g_object_notify_by_pspec(G_OBJECT(self), person_props[PERSON_PROP_HUE]);
}

int luma_person_get_hue(LumaPerson *self) {
  g_return_val_if_fail(LUMA_IS_PERSON(self), -1);
  return self->hue;
}

/* ── contact_uri ────────────────────────────────────────────────────────── */

/* (action, Lucide glyph, label), in Contacts' order. */
static const struct {
  const char *action, *glyph, *label;
} contact_actions[] = {
    {"message", "message-square", "Message"},
    {"call", "phone", "Call"},
    {"video", "video", "Video"},
    {"email", "mail", "Email"},
};

static gboolean contact_action_known(const char *action) {
  for (guint i = 0; action != NULL && i < G_N_ELEMENTS(contact_actions); i++)
    if (g_str_equal(contact_actions[i].action, action))
      return TRUE;
  g_critical("unknown contact action '%s'", action != NULL ? action : "(null)");
  return FALSE;
}

char *luma_contact_uri(const char *action, LumaPerson *person) {
  g_return_val_if_fail(LUMA_IS_PERSON(person), NULL);
  if (!contact_action_known(action))
    return NULL;
  GString *phone = g_string_new(NULL);
  for (const char *p = person->phone; *p != '\0'; p = g_utf8_next_char(p)) {
    gunichar ch = g_utf8_get_char(p);
    if (g_unichar_isdigit(ch) || ch == '+')
      g_string_append_unichar(phone, ch);
  }
  g_autofree char *digits = g_string_free(phone, FALSE);
  if (g_str_equal(action, "message"))
    return digits[0] != '\0' ? g_strconcat("sms:", digits, NULL) : g_strdup("");
  if (g_str_equal(action, "call"))
    return digits[0] != '\0' ? g_strconcat("tel:", digits, NULL) : g_strdup("");
  if (g_str_equal(action, "email")) {
    if (person->email[0] == '\0')
      return g_strdup("");
    g_autofree char *quoted = g_uri_escape_string(person->email, "@", FALSE);
    return g_strconcat("mailto:", quoted, NULL);
  }
  /* A video call needs Luma's network or the hosting app; no desktop link places one. */
  return g_strdup("");
}

/* ── ContactActions ─────────────────────────────────────────────────────── */

enum { ACTIONS_ACTION, ACTIONS_N_SIGNALS };
static guint actions_signals[ACTIONS_N_SIGNALS];

struct _LumaContactActions {
  GtkBox parent_instance;
  LumaPerson *person;
  GtkWidget *buttons[G_N_ELEMENTS(contact_actions)];
};

G_DEFINE_FINAL_TYPE(LumaContactActions, luma_contact_actions, GTK_TYPE_BOX)

static gboolean contact_possible(guint index, LumaPerson *person, gboolean handled) {
  g_autofree char *uri = luma_contact_uri(contact_actions[index].action, person);
  if (uri[0] != '\0')
    return TRUE;
  if (!handled)
    return FALSE;
  /* The hosting app can do it in place when the person is reachable that way. */
  const char *action = contact_actions[index].action;
  gboolean on_luma = luma_person_get_on_luma(person);
  if (g_str_equal(action, "video"))
    return on_luma || person->online;
  if (g_str_equal(action, "email"))
    return person->email[0] != '\0';
  return on_luma;
}

/* Whether the app handles actions in place: it has connected to ::action. */
static void contact_actions_refresh(LumaContactActions *self) {
  gboolean handled = g_signal_has_handler_pending(self, actions_signals[ACTIONS_ACTION], 0, TRUE);
  for (guint i = 0; i < G_N_ELEMENTS(contact_actions); i++)
    gtk_widget_set_sensitive(self->buttons[i], contact_possible(i, self->person, handled));
}

static void contact_button_clicked(GtkButton *button, gpointer user_data) {
  const char *action = g_object_get_data(G_OBJECT(button), "luma-contact-action");
  luma_contact_actions_activate_action(LUMA_CONTACT_ACTIONS(user_data), action);
}

static void contact_actions_map(GtkWidget *widget) {
  contact_actions_refresh(LUMA_CONTACT_ACTIONS(widget));
  GTK_WIDGET_CLASS(luma_contact_actions_parent_class)->map(widget);
}

static void contact_actions_dispose(GObject *object) {
  g_clear_object(&LUMA_CONTACT_ACTIONS(object)->person);
  G_OBJECT_CLASS(luma_contact_actions_parent_class)->dispose(object);
}

static void luma_contact_actions_class_init(LumaContactActionsClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = contact_actions_dispose;
  GTK_WIDGET_CLASS(klass)->map = contact_actions_map;
  /**
   * LumaContactActions::action:
   * @self: the row
   * @action: "message", "call", "video" or "email"
   *
   * Handle @action in place (Messages opens the thread, Phone places the
   * call). Connecting also enables what the app can do without a desktop
   * link.
   *
   * Returns: %TRUE when handled
   */
  actions_signals[ACTIONS_ACTION] =
      g_signal_new("action", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, g_signal_accumulator_true_handled,
                   NULL, NULL, G_TYPE_BOOLEAN, 1, G_TYPE_STRING);
}

static void luma_contact_actions_init(LumaContactActions *self) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_box_set_homogeneous(GTK_BOX(self), TRUE);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-stacked-buttons");
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-contact-actions");
}

GtkWidget *luma_contact_actions_new(LumaPerson *person, gboolean small) {
  g_return_val_if_fail(LUMA_IS_PERSON(person), NULL);
  LumaContactActions *self = g_object_new(LUMA_TYPE_CONTACT_ACTIONS, NULL);
  self->person = g_object_ref(person);
  luma_ui_set_css_class(GTK_WIDGET(self), "lumaui-stack-small", small);
  for (guint i = 0; i < G_N_ELEMENTS(contact_actions); i++) {
    GtkWidget *button = luma_stacked_button_new(contact_actions[i].glyph, contact_actions[i].label, FALSE);
    g_object_set_data(G_OBJECT(button), "luma-contact-action", (gpointer)contact_actions[i].action);
    g_signal_connect(button, "clicked", G_CALLBACK(contact_button_clicked), self);
    self->buttons[i] = button;
    gtk_box_append(GTK_BOX(self), button);
  }
  contact_actions_refresh(self);
  g_autofree char *label = g_strdup_printf("Contact %s", person->name);
  luma_ui_set_accessible_label(GTK_WIDGET(self), label);
  return GTK_WIDGET(self);
}

gboolean luma_contact_actions_activate_action(LumaContactActions *self, const char *action) {
  g_return_val_if_fail(LUMA_IS_CONTACT_ACTIONS(self), FALSE);
  if (!contact_action_known(action))
    return FALSE;
  gboolean handled = FALSE;
  g_signal_emit(self, actions_signals[ACTIONS_ACTION], 0, action, &handled);
  if (handled)
    return TRUE;
  g_autofree char *uri = luma_contact_uri(action, self->person);
  if (uri[0] == '\0')
    return FALSE;
  GdkDisplay *display = gtk_widget_get_display(GTK_WIDGET(self));
  g_autoptr(GdkAppLaunchContext) context = display != NULL ? gdk_display_get_app_launch_context(display) : NULL;
  g_autoptr(GError) error = NULL;
  if (!g_app_info_launch_default_for_uri(uri, G_APP_LAUNCH_CONTEXT(context), &error)) {
    luma_toast_show(GTK_WIDGET(self), "Nothing on this computer can do that yet", "error");
    return FALSE;
  }
  return TRUE;
}

/* ── MiniCard: the shared shape ─────────────────────────────────────────── */

/* A 44 px lead, a title, one quiet line (text or a widget), and what to do on
 * the right. @self is a vertical GtkBox; returns the row. */
static GtkWidget *mini_card_build(GtkWidget *self, const char *kind, GtkWidget *lead, const char *title,
                                  const char *subtitle, GtkWidget *subtitle_widget, GtkWidget *trailing) {
  luma_ui_install();
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_set_halign(self, GTK_ALIGN_START);
  gtk_widget_add_css_class(self, "lumaui-mini-card");
  gtk_widget_add_css_class(self, kind);
  /* v70: 300 wide (320 for a person), narrower only when it must be. */
  _luma_content_cap_width(self, g_str_equal(kind, "person") ? LUMA_UI_MINI_CARD_PERSON_WIDTH
                                                            : LUMA_UI_MINI_CARD_WIDTH);
  GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(row, "lumaui-mini-row");
  GtkWidget *slot = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(slot, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(slot, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(slot, "lumaui-mini-lead");
  gtk_box_append(GTK_BOX(slot), lead);
  gtk_box_append(GTK_BOX(row), slot);
  GtkWidget *text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_hexpand(text, TRUE);
  gtk_widget_set_valign(text, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(text, "lumaui-mini-text");
  GtkWidget *heading = gtk_label_new(title);
  gtk_label_set_xalign(GTK_LABEL(heading), 0);
  gtk_label_set_ellipsize(GTK_LABEL(heading), PANGO_ELLIPSIZE_END);
  gtk_widget_add_css_class(heading, "lumaui-mini-title");
  gtk_box_append(GTK_BOX(text), heading);
  gboolean has_subtitle = subtitle_widget == NULL && subtitle != NULL && subtitle[0] != '\0';
  if (subtitle_widget != NULL) {
    gtk_box_append(GTK_BOX(text), subtitle_widget);
  } else if (has_subtitle) {
    GtkWidget *line = gtk_label_new(subtitle);
    gtk_label_set_xalign(GTK_LABEL(line), 0);
    gtk_label_set_ellipsize(GTK_LABEL(line), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(line, "lumaui-mini-subtitle");
    gtk_box_append(GTK_BOX(text), line);
  }
  gtk_box_append(GTK_BOX(row), text);
  if (trailing != NULL)
    gtk_box_append(GTK_BOX(row), trailing);
  gtk_box_append(GTK_BOX(self), row);
  g_autofree char *name = has_subtitle ? g_strdup_printf("%s, %s", title, subtitle) : g_strdup(title);
  luma_ui_set_accessible_label(self, name);
  return row;
}

/* " · ".join of the parts that are there. */
static char *join_dot(const char *first, const char *second) {
  gboolean a = first != NULL && first[0] != '\0', b = second != NULL && second[0] != '\0';
  if (a && b)
    return g_strdup_printf("%s · %s", first, second);
  return g_strdup(a ? first : b ? second : "");
}

static GtkWidget *small_button(const char *label) {
  GtkWidget *button = gtk_button_new_with_label(label);
  gtk_widget_set_valign(button, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(button, "lumaui-mini-button");
  return button;
}

/* A mini card's button works once the app listens for it (Python passes the
 * callback; C connects to the signal). */
static void sensitive_by_handler(GtkWidget *button, gpointer instance, guint signal_id) {
  if (button != NULL)
    gtk_widget_set_sensitive(button, g_signal_has_handler_pending(instance, signal_id, 0, TRUE));
}

/* ── ContactCard ────────────────────────────────────────────────────────── */

struct _LumaContactCard {
  GtkBox parent_instance;
  LumaContactActions *actions;
};

G_DEFINE_FINAL_TYPE(LumaContactCard, luma_contact_card, GTK_TYPE_BOX)

static void luma_contact_card_class_init(LumaContactCardClass *klass G_GNUC_UNUSED) {}
static void luma_contact_card_init(LumaContactCard *self G_GNUC_UNUSED) {}

GtkWidget *luma_contact_card_new(LumaPerson *person) {
  g_return_val_if_fail(LUMA_IS_PERSON(person), NULL);
  LumaContactCard *self = g_object_new(LUMA_TYPE_CONTACT_CARD, NULL);
  GtkWidget *line = NULL;
  const char *subtitle = NULL;
  if (luma_person_get_on_luma(person)) {
    line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(line, "lumaui-mini-subtitle");
    GtkWidget *dot = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_valign(dot, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(dot, "lumaui-on-luma-dot");
    gtk_box_append(GTK_BOX(line), dot);
    g_autofree char *words = g_strdup_printf("@%s · On Luma", person->username);
    GtkWidget *label = gtk_label_new(words);
    gtk_label_set_xalign(GTK_LABEL(label), 0);
    gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
    gtk_box_append(GTK_BOX(line), label);
  } else {
    subtitle = person->phone[0] != '\0' ? person->phone : person->email;
  }
  GtkWidget *avatar = luma_person_avatar_new(person->name, LUMA_UI_MINI_CARD_LEAD);
  if (person->hue >= 0)
    luma_person_avatar_set_hue(LUMA_PERSON_AVATAR(avatar), person->hue);
  if (person->picture != NULL)
    luma_person_avatar_set_picture(LUMA_PERSON_AVATAR(avatar), person->picture);
  mini_card_build(GTK_WIDGET(self), "person", avatar, person->name, subtitle, line, NULL);
  self->actions = LUMA_CONTACT_ACTIONS(luma_contact_actions_new(person, TRUE));
  gtk_box_append(GTK_BOX(self), GTK_WIDGET(self->actions));
  return GTK_WIDGET(self);
}

LumaContactActions *luma_contact_card_get_actions(LumaContactCard *self) {
  g_return_val_if_fail(LUMA_IS_CONTACT_CARD(self), NULL);
  return self->actions;
}

/* ── EventCard ──────────────────────────────────────────────────────────── */

enum { EVENT_ADD, EVENT_N_SIGNALS };
static guint event_signals[EVENT_N_SIGNALS];

struct _LumaEventCard {
  GtkBox parent_instance;
  GDateTime *start;
  gboolean all_day;
  char *where;
  char *title;
  GtkWidget *text;
  GtkWidget *add;
};

G_DEFINE_FINAL_TYPE(LumaEventCard, luma_event_card, GTK_TYPE_BOX)

static const char *const event_tones[] = LUMA_UI_CATEGORY_ORDER;

static void event_card_set_subtitle(LumaEventCard *self, const char *when) {
  g_autofree char *time = NULL;
  if (when == NULL) {
    if (self->all_day) {
      time = g_strdup("All day");
    } else {
      g_autofree char *clock = g_date_time_format(self->start, "%I:%M %p");
      const char *trimmed = clock;
      while (*trimmed == '0')
        trimmed++;
      time = g_strdup(trimmed);
    }
    when = time;
  }
  g_autofree char *subtitle = join_dot(when, self->where);
  /* The quiet line is the text column's second child. */
  GtkWidget *heading = gtk_widget_get_first_child(self->text);
  GtkWidget *line = gtk_widget_get_next_sibling(heading);
  if (subtitle[0] == '\0') {
    if (line != NULL)
      gtk_box_remove(GTK_BOX(self->text), line);
  } else {
    if (line == NULL) {
      line = gtk_label_new(NULL);
      gtk_label_set_xalign(GTK_LABEL(line), 0);
      gtk_label_set_ellipsize(GTK_LABEL(line), PANGO_ELLIPSIZE_END);
      gtk_widget_add_css_class(line, "lumaui-mini-subtitle");
      gtk_box_append(GTK_BOX(self->text), line);
    }
    gtk_label_set_label(GTK_LABEL(line), subtitle);
  }
  g_autofree char *name = subtitle[0] != '\0' ? g_strdup_printf("%s, %s", self->title, subtitle)
                                              : g_strdup(self->title);
  luma_ui_set_accessible_label(GTK_WIDGET(self), name);
}

static void event_add_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  g_signal_emit(user_data, event_signals[EVENT_ADD], 0);
}

static void event_card_map(GtkWidget *widget) {
  sensitive_by_handler(LUMA_EVENT_CARD(widget)->add, widget, event_signals[EVENT_ADD]);
  GTK_WIDGET_CLASS(luma_event_card_parent_class)->map(widget);
}

static void event_card_finalize(GObject *object) {
  LumaEventCard *self = LUMA_EVENT_CARD(object);
  g_clear_pointer(&self->start, g_date_time_unref);
  g_free(self->where);
  g_free(self->title);
  G_OBJECT_CLASS(luma_event_card_parent_class)->finalize(object);
}

static void luma_event_card_class_init(LumaEventCardClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = event_card_finalize;
  GTK_WIDGET_CLASS(klass)->map = event_card_map;
  /**
   * LumaEventCard::add:
   * @self: the card
   *
   * Add was pressed. Add works once something is connected here.
   */
  event_signals[EVENT_ADD] =
      g_signal_new("add", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
}

static void luma_event_card_init(LumaEventCard *self G_GNUC_UNUSED) {}

GtkWidget *luma_event_card_new(const char *title, GDateTime *start, gboolean all_day, const char *where,
                               const char *tone) {
  g_return_val_if_fail(title != NULL, NULL);
  g_return_val_if_fail(start != NULL, NULL);
  if (tone == NULL)
    tone = "work";
  gboolean known = FALSE;
  for (guint i = 0; i < G_N_ELEMENTS(event_tones); i++)
    known = known || g_str_equal(event_tones[i], tone);
  if (!known) {
    g_critical("an event's tone is one of create, work, media, play, tools (not '%s')", tone);
    return NULL;
  }
  LumaEventCard *self = g_object_new(LUMA_TYPE_EVENT_CARD, NULL);
  self->start = g_date_time_ref(start);
  self->all_day = all_day;
  self->where = g_strdup(where);
  self->title = g_strdup(title);
  GtkWidget *tile = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_valign(tile, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(tile, "lumaui-event-tile");
  gtk_widget_add_css_class(tile, tone);
  g_autofree char *month_name = g_date_time_format(start, "%b");
  g_autofree char *month_upper = g_utf8_strup(month_name, -1);
  GtkWidget *month = gtk_label_new(month_upper);
  gtk_widget_add_css_class(month, "lumaui-event-month");
  g_autofree char *day_text = g_strdup_printf("%d", g_date_time_get_day_of_month(start));
  GtkWidget *day = gtk_label_new(day_text);
  gtk_widget_add_css_class(day, "lumaui-event-day");
  gtk_box_append(GTK_BOX(tile), month);
  gtk_box_append(GTK_BOX(tile), day);
  self->add = small_button("Add");
  g_signal_connect(self->add, "clicked", G_CALLBACK(event_add_clicked), self);
  GtkWidget *row = mini_card_build(GTK_WIDGET(self), "event", tile, title, NULL, NULL, self->add);
  self->text = gtk_widget_get_prev_sibling(self->add);
  (void)row;
  event_card_set_subtitle(self, NULL);
  sensitive_by_handler(self->add, self, event_signals[EVENT_ADD]);
  return GTK_WIDGET(self);
}

void luma_event_card_set_when(LumaEventCard *self, const char *when) {
  g_return_if_fail(LUMA_IS_EVENT_CARD(self));
  event_card_set_subtitle(self, when);
}

void luma_event_card_set_add_label(LumaEventCard *self, const char *label) {
  g_return_if_fail(LUMA_IS_EVENT_CARD(self));
  if (label == NULL) {
    gtk_widget_set_visible(self->add, FALSE);
    return;
  }
  gtk_button_set_label(GTK_BUTTON(self->add), label);
  gtk_widget_set_visible(self->add, TRUE);
}

/* ── SongCard ───────────────────────────────────────────────────────────── */

enum { SONG_PLAY, SONG_N_SIGNALS };
static guint song_signals[SONG_N_SIGNALS];

struct _LumaSongCard {
  GtkBox parent_instance;
  GtkWidget *play;
};

G_DEFINE_FINAL_TYPE(LumaSongCard, luma_song_card, GTK_TYPE_BOX)

static void song_play_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  g_signal_emit(user_data, song_signals[SONG_PLAY], 0);
}

static void song_card_map(GtkWidget *widget) {
  sensitive_by_handler(LUMA_SONG_CARD(widget)->play, widget, song_signals[SONG_PLAY]);
  GTK_WIDGET_CLASS(luma_song_card_parent_class)->map(widget);
}

static void luma_song_card_class_init(LumaSongCardClass *klass) {
  GTK_WIDGET_CLASS(klass)->map = song_card_map;
  /**
   * LumaSongCard::play:
   * @self: the card
   *
   * Play was pressed. Play works once something is connected here.
   */
  song_signals[SONG_PLAY] =
      g_signal_new("play", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
}

static void luma_song_card_init(LumaSongCard *self G_GNUC_UNUSED) {}

GtkWidget *luma_song_card_new(const char *title, const char *artist, const char *album, GdkPaintable *artwork) {
  g_return_val_if_fail(title != NULL, NULL);
  g_return_val_if_fail(artwork == NULL || GDK_IS_PAINTABLE(artwork), NULL);
  LumaSongCard *self = g_object_new(LUMA_TYPE_SONG_CARD, NULL);
  GtkWidget *lead;
  if (artwork != NULL) {
    lead = _luma_content_face_new(LUMA_UI_MINI_CARD_LEAD, LUMA_UI_MINI_CARD_ARTWORK_RADIUS);
    _luma_content_face_set_paintable(lead, artwork);
  } else {
    lead = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_halign(lead, GTK_ALIGN_CENTER);
    gtk_widget_set_valign(lead, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(lead), luma_ui_icon_image("music", 0));
  }
  gtk_widget_add_css_class(lead, "lumaui-song-artwork");
  self->play = gtk_button_new();
  gtk_widget_set_valign(self->play, GTK_ALIGN_CENTER);
  gtk_widget_set_tooltip_text(self->play, "Play");
  gtk_widget_add_css_class(self->play, "lumaui-mini-play");
  gtk_button_set_child(GTK_BUTTON(self->play), luma_ui_icon_image("play", 0));
  gtk_widget_remove_css_class(self->play, "image-button"); /* a LumaUI part, not the legacy icon-button look */
  g_autofree char *play_label = g_strdup_printf("Play %s", title);
  luma_ui_set_accessible_label(self->play, play_label);
  g_signal_connect(self->play, "clicked", G_CALLBACK(song_play_clicked), self);
  g_autofree char *subtitle = join_dot(artist, album);
  mini_card_build(GTK_WIDGET(self), "song", lead, title, subtitle, NULL, self->play);
  sensitive_by_handler(self->play, self, song_signals[SONG_PLAY]);
  return GTK_WIDGET(self);
}

/* ── PlaceCard ──────────────────────────────────────────────────────────── */

enum { PLACE_DIRECTIONS, PLACE_N_SIGNALS };
static guint place_signals[PLACE_N_SIGNALS];

struct _LumaPlaceCard {
  GtkBox parent_instance;
  GtkWidget *directions;
};

G_DEFINE_FINAL_TYPE(LumaPlaceCard, luma_place_card, GTK_TYPE_BOX)

static void place_directions_clicked(GtkButton *button G_GNUC_UNUSED, gpointer user_data) {
  g_signal_emit(user_data, place_signals[PLACE_DIRECTIONS], 0);
}

static void place_card_map(GtkWidget *widget) {
  sensitive_by_handler(LUMA_PLACE_CARD(widget)->directions, widget, place_signals[PLACE_DIRECTIONS]);
  GTK_WIDGET_CLASS(luma_place_card_parent_class)->map(widget);
}

static void luma_place_card_class_init(LumaPlaceCardClass *klass) {
  GTK_WIDGET_CLASS(klass)->map = place_card_map;
  /**
   * LumaPlaceCard::directions:
   * @self: the card
   *
   * Directions was pressed. Directions works once something is connected here.
   */
  place_signals[PLACE_DIRECTIONS] =
      g_signal_new("directions", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 0);
}

static void luma_place_card_init(LumaPlaceCard *self G_GNUC_UNUSED) {}

GtkWidget *luma_place_card_new(const char *name, const char *address, const char *eta, const char *icon) {
  g_return_val_if_fail(name != NULL, NULL);
  LumaPlaceCard *self = g_object_new(LUMA_TYPE_PLACE_CARD, NULL);
  GtkWidget *pin = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(pin, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(pin, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(pin, "lumaui-place-pin");
  GtkWidget *glyph = luma_ui_icon_image(icon != NULL ? icon : "map-pin", 0);
  gtk_widget_set_halign(glyph, GTK_ALIGN_CENTER);
  gtk_widget_set_valign(glyph, GTK_ALIGN_CENTER);
  gtk_box_append(GTK_BOX(pin), glyph);
  g_autofree char *subtitle = join_dot(address, eta);
  self->directions = small_button("Directions");
  g_signal_connect(self->directions, "clicked", G_CALLBACK(place_directions_clicked), self);
  mini_card_build(GTK_WIDGET(self), "place", pin, name, subtitle, NULL, self->directions);
  sensitive_by_handler(self->directions, self, place_signals[PLACE_DIRECTIONS]);
  return GTK_WIDGET(self);
}
