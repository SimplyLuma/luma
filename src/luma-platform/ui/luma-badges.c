/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of content_badges.CountBadge / CategoryPill and content_contact.StatusPill. */
#include "luma-badges.h"
#include "luma-ui-private.h"

#include <string.h>

/* ── CountBadge ─────────────────────────────────────────────────────────── */

typedef struct {
  int count;
  gboolean attention;
} LumaCountBadgePrivate;

LUMA_DEFINE_OPAQUE_SUBTYPE(LumaCountBadge, luma_count_badge, GTK_TYPE_LABEL)

enum { BADGE_PROP_0, BADGE_PROP_COUNT, BADGE_PROP_ATTENTION, BADGE_N_PROPS };
static GParamSpec *badge_props[BADGE_N_PROPS];

static void count_badge_refresh(LumaCountBadge *self) {
  LumaCountBadgePrivate *priv = luma_count_badge_get_instance_private(self);
  g_autofree char *text = luma_ui_count_text(priv->count, priv->attention);
  gtk_label_set_label(GTK_LABEL(self), text);
  gtk_widget_set_visible(GTK_WIDGET(self), text[0] != '\0');
  /* A capped badge still reads the real number. */
  g_autofree char *exact = priv->count > 0 ? luma_ui_count_text(priv->count, FALSE) : g_strdup("0");
  luma_ui_set_accessible_label(GTK_WIDGET(self), exact);
}

static void count_badge_get_property(GObject *object, guint id, GValue *value, GParamSpec *pspec) {
  LumaCountBadgePrivate *priv = luma_count_badge_get_instance_private(LUMA_COUNT_BADGE(object));
  switch (id) {
  case BADGE_PROP_COUNT:
    g_value_set_int(value, priv->count);
    break;
  case BADGE_PROP_ATTENTION:
    g_value_set_boolean(value, priv->attention);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void count_badge_set_property(GObject *object, guint id, const GValue *value, GParamSpec *pspec) {
  LumaCountBadge *self = LUMA_COUNT_BADGE(object);
  switch (id) {
  case BADGE_PROP_COUNT:
    luma_count_badge_set_count(self, g_value_get_int(value));
    break;
  case BADGE_PROP_ATTENTION:
    luma_count_badge_set_attention(self, g_value_get_boolean(value));
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
  }
}

static void luma_count_badge_class_init(LumaCountBadgeClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  object_class->get_property = count_badge_get_property;
  object_class->set_property = count_badge_set_property;
  badge_props[BADGE_PROP_COUNT] =
      g_param_spec_int("count", NULL, NULL, G_MININT, G_MAXINT, 0,
                       G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  badge_props[BADGE_PROP_ATTENTION] =
      g_param_spec_boolean("attention", NULL, NULL, FALSE,
                           G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(object_class, BADGE_N_PROPS, badge_props);
}

static void luma_count_badge_init(LumaCountBadge *self) {
  luma_ui_install();
  GtkWidget *widget = GTK_WIDGET(self);
  gtk_widget_set_valign(widget, GTK_ALIGN_CENTER);
  gtk_widget_set_halign(widget, GTK_ALIGN_END);
  gtk_label_set_xalign(GTK_LABEL(self), 0.5f);
  gtk_widget_add_css_class(widget, "lumaui-count");
  count_badge_refresh(self);
}

GtkWidget *luma_count_badge_new(int count, gboolean attention) {
  LumaCountBadge *self = g_object_new(LUMA_TYPE_COUNT_BADGE, NULL);
  luma_count_badge_set_attention(self, attention);
  luma_count_badge_set_count(self, count);
  return GTK_WIDGET(self);
}

void luma_count_badge_set_count(LumaCountBadge *self, int count) {
  g_return_if_fail(LUMA_IS_COUNT_BADGE(self));
  LumaCountBadgePrivate *priv = luma_count_badge_get_instance_private(self);
  count = MAX(0, count);
  gboolean changed = priv->count != count;
  priv->count = count;
  count_badge_refresh(self);
  if (changed)
    g_object_notify_by_pspec(G_OBJECT(self), badge_props[BADGE_PROP_COUNT]);
}

int luma_count_badge_get_count(LumaCountBadge *self) {
  g_return_val_if_fail(LUMA_IS_COUNT_BADGE(self), 0);
  return ((LumaCountBadgePrivate *)luma_count_badge_get_instance_private(self))->count;
}

void luma_count_badge_set_attention(LumaCountBadge *self, gboolean attention) {
  g_return_if_fail(LUMA_IS_COUNT_BADGE(self));
  LumaCountBadgePrivate *priv = luma_count_badge_get_instance_private(self);
  attention = !!attention;
  gboolean changed = priv->attention != attention;
  priv->attention = attention;
  luma_ui_set_css_class(GTK_WIDGET(self), "attention", attention);
  count_badge_refresh(self);
  if (changed)
    g_object_notify_by_pspec(G_OBJECT(self), badge_props[BADGE_PROP_ATTENTION]);
}

gboolean luma_count_badge_get_attention(LumaCountBadge *self) {
  g_return_val_if_fail(LUMA_IS_COUNT_BADGE(self), FALSE);
  return ((LumaCountBadgePrivate *)luma_count_badge_get_instance_private(self))->attention;
}

/* ── CategoryPill ───────────────────────────────────────────────────────── */

typedef struct {
  char *category;
} LumaCategoryPillPrivate;

LUMA_DEFINE_OPAQUE_SUBTYPE(LumaCategoryPill, luma_category_pill, GTK_TYPE_LABEL)

static const struct {
  const char *key, *label;
} categories[] = {
    {"create", LUMA_UI_CATEGORY_LABELS_CREATE}, {"work", LUMA_UI_CATEGORY_LABELS_WORK},
    {"media", LUMA_UI_CATEGORY_LABELS_MEDIA},   {"play", LUMA_UI_CATEGORY_LABELS_PLAY},
    {"tools", LUMA_UI_CATEGORY_LABELS_TOOLS},
};

/* Python's str.title(): a letter after a letter is lower case, any other upper. */
static char *title_case(const char *text) {
  GString *out = g_string_new(NULL);
  gboolean after_letter = FALSE;
  for (const char *p = text; *p != '\0'; p = g_utf8_next_char(p)) {
    gunichar ch = g_utf8_get_char(p);
    gboolean letter = g_unichar_isalpha(ch);
    g_string_append_unichar(out, letter ? (after_letter ? g_unichar_tolower(ch) : g_unichar_toupper(ch)) : ch);
    after_letter = letter;
  }
  return g_string_free(out, FALSE);
}

static void category_pill_finalize(GObject *object) {
  LumaCategoryPillPrivate *priv = luma_category_pill_get_instance_private(LUMA_CATEGORY_PILL(object));
  g_clear_pointer(&priv->category, g_free);
  G_OBJECT_CLASS(luma_category_pill_parent_class)->finalize(object);
}

static void luma_category_pill_class_init(LumaCategoryPillClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = category_pill_finalize;
}

static void luma_category_pill_init(LumaCategoryPill *self) {
  luma_ui_install();
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_CENTER);
  gtk_widget_set_halign(GTK_WIDGET(self), GTK_ALIGN_START);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-category");
}

GtkWidget *luma_category_pill_new(const char *category) {
  g_return_val_if_fail(category != NULL, NULL);
  LumaCategoryPill *self = g_object_new(LUMA_TYPE_CATEGORY_PILL, NULL);
  luma_category_pill_set_category(self, category);
  return GTK_WIDGET(self);
}

void luma_category_pill_set_category(LumaCategoryPill *self, const char *category) {
  g_return_if_fail(LUMA_IS_CATEGORY_PILL(self));
  g_return_if_fail(category != NULL);
  LumaCategoryPillPrivate *priv = luma_category_pill_get_instance_private(self);
  g_autofree char *stripped = g_strstrip(g_strdup(category));
  g_autofree char *key = g_utf8_strdown(stripped, -1);
  const char *label = NULL;
  for (guint i = 0; i < G_N_ELEMENTS(categories); i++)
    if (g_str_equal(categories[i].key, key))
      label = categories[i].label;
  if (priv->category != NULL && priv->category[0] != '\0')
    gtk_widget_remove_css_class(GTK_WIDGET(self), priv->category);
  /* An unknown category keeps its own name and the neutral Work hue, as the
   * design does; it never takes a colour from the caller. */
  g_free(priv->category);
  priv->category = g_strdup(label != NULL ? key : "work");
  gtk_widget_add_css_class(GTK_WIDGET(self), priv->category);
  if (label != NULL) {
    gtk_label_set_label(GTK_LABEL(self), label);
  } else {
    g_autofree char *titled = title_case(stripped);
    gtk_label_set_label(GTK_LABEL(self), titled);
  }
}

const char *luma_category_pill_get_category(LumaCategoryPill *self) {
  g_return_val_if_fail(LUMA_IS_CATEGORY_PILL(self), NULL);
  return ((LumaCategoryPillPrivate *)luma_category_pill_get_instance_private(self))->category;
}

/* ── StatusPill ─────────────────────────────────────────────────────────── */

/* A status kind: its tone (good, busy, bad, off) and default words. */
static const struct {
  const char *kind, *tone, *words;
} status_kinds[] = {
    {"on-luma", "good", "On Luma"},   {"not-on-luma", "off", "Not on Luma"},
    {"online", "good", "Online"},     {"offline", "off", "Offline"},
    {"syncing", "busy", "Syncing"},   {"error", "bad", "Needs attention"},
};

struct _LumaStatusPill {
  GtkBox parent_instance;
  GtkWidget *dot;
  GtkWidget *label;
  const char *kind;
  const char *tone;
};

G_DEFINE_FINAL_TYPE(LumaStatusPill, luma_status_pill, GTK_TYPE_BOX)

static int status_index(const char *kind) {
  for (guint i = 0; kind != NULL && i < G_N_ELEMENTS(status_kinds); i++)
    if (g_str_equal(status_kinds[i].kind, kind))
      return (int)i;
  return -1;
}

static gboolean status_check(const char *kind) {
  if (status_index(kind) >= 0)
    return TRUE;
  g_critical("unknown status '%s'; use one of on-luma, not-on-luma, online, offline, syncing, error",
             kind != NULL ? kind : "(null)");
  return FALSE;
}

static void luma_status_pill_class_init(LumaStatusPillClass *klass G_GNUC_UNUSED) {}

static void luma_status_pill_init(LumaStatusPill *self) {
  luma_ui_install();
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_CENTER);
  gtk_widget_set_halign(GTK_WIDGET(self), GTK_ALIGN_START);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-status-pill");
  self->dot = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_valign(self->dot, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(self->dot, "lumaui-status-pill-dot");
  self->label = gtk_label_new(NULL);
  gtk_box_append(GTK_BOX(self), self->dot);
  gtk_box_append(GTK_BOX(self), self->label);
  self->kind = "";
  self->tone = "";
}

GtkWidget *luma_status_pill_new(const char *kind, const char *label) {
  if (!status_check(kind))
    return NULL;
  LumaStatusPill *self = g_object_new(LUMA_TYPE_STATUS_PILL, NULL);
  luma_status_pill_set_kind(self, kind, label);
  return GTK_WIDGET(self);
}

void luma_status_pill_set_kind(LumaStatusPill *self, const char *kind, const char *label) {
  g_return_if_fail(LUMA_IS_STATUS_PILL(self));
  if (!status_check(kind))
    return;
  int i = status_index(kind);
  if (self->tone[0] != '\0')
    gtk_widget_remove_css_class(GTK_WIDGET(self), self->tone);
  self->kind = status_kinds[i].kind;
  self->tone = status_kinds[i].tone;
  gtk_widget_add_css_class(GTK_WIDGET(self), self->tone);
  gtk_widget_set_visible(self->dot, !g_str_equal(self->tone, "off"));
  const char *words = label != NULL && label[0] != '\0' ? label : status_kinds[i].words;
  gtk_label_set_label(GTK_LABEL(self->label), words);
  luma_ui_set_accessible_label(GTK_WIDGET(self), words);
}

const char *luma_status_pill_get_kind(LumaStatusPill *self) {
  g_return_val_if_fail(LUMA_IS_STATUS_PILL(self), NULL);
  return self->kind;
}
