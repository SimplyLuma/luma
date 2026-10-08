/* SPDX-License-Identifier: Apache-2.0 */
/* LumaModeSwitch: the twin of structure_placement.ModeSwitch (Python). */
#include "luma-mode-switch.h"
#include "luma-ui-private.h"

enum { CHANGED, N_SIGNALS };
static guint signals[N_SIGNALS];

typedef struct {
  char *key;
  GtkWidget *button;
  GtkWidget *label;
  GtkWidget *status_text, *status_dot;
} Mode;

struct _LumaModeSwitch {
  GtkBox parent_instance;
  GtkWidget *indicator;
  GtkWidget *row;
  GPtrArray *modes; /* Mode */
  char *current;
  gboolean narrow;
  gboolean icon_only;
  gboolean fill; /* v71: equal shares of the width it is given (Python ModeSwitch(fill=True)) */
  gboolean small;
  gboolean ellipsize;
  int stop_width; /* zero: content-sized; positive: fixed numerical stops */
  gboolean selecting;
  guint anim;
  gint64 began;
  guint duration;
};

G_DEFINE_FINAL_TYPE(LumaModeSwitch, luma_mode_switch, GTK_TYPE_BOX)

static void mode_stop_style(LumaModeSwitch *self, Mode *mode) {
  GtkWidget *line = gtk_button_get_child(GTK_BUTTON(mode->button));
  gboolean fixed = self->stop_width > 0;
  gboolean has_icon = gtk_widget_get_first_child(line) != mode->label;
  gtk_widget_set_halign(line, fixed ? GTK_ALIGN_FILL : GTK_ALIGN_CENTER);
  gtk_label_set_ellipsize(GTK_LABEL(mode->label), fixed || self->small || has_icon || self->ellipsize ? PANGO_ELLIPSIZE_END : PANGO_ELLIPSIZE_NONE);
  gtk_label_set_max_width_chars(GTK_LABEL(mode->label), fixed ? 1 : -1);
  gtk_widget_set_hexpand(mode->label, fixed);
  gtk_label_set_xalign(GTK_LABEL(mode->label), .5f);
}

static void mode_free(gpointer data) {
  Mode *mode = data;
  g_free(mode->key);
  g_free(mode);
}

static Mode *find_mode(LumaModeSwitch *self, const char *key) {
  for (guint i = 0; i < self->modes->len; i++) {
    Mode *mode = g_ptr_array_index(self->modes, i);
    if (g_strcmp0(mode->key, key) == 0)
      return mode;
  }
  return NULL;
}

/* Placing the chip. The chip is the row's first child, placed by the row's own layout over the
 * current button's real allocation in the same pass: right at first paint, after labels fold or the
 * width changes, and during the slide (Nick, 26 Sep: a half pill behind the icon). As Python's
 * _ModesLayout. */

#define LUMA_TYPE_MODES_LAYOUT (luma_modes_layout_get_type())
G_DECLARE_FINAL_TYPE(LumaModesLayout, luma_modes_layout, LUMA, MODES_LAYOUT, GtkLayoutManager)
struct _LumaModesLayout {
  GtkLayoutManager parent_instance;
  LumaModeSwitch *owner; /* unowned: the switch owns the row that owns this */
  gboolean sliding;
  double start_x, start_w;
  double progress;
  gboolean has_shown;
  double shown_x, shown_w;
};
G_DEFINE_FINAL_TYPE(LumaModesLayout, luma_modes_layout, GTK_TYPE_LAYOUT_MANAGER)

static gboolean is_button(LumaModesLayout *layout, GtkWidget *child) {
  return child != layout->owner->indicator && gtk_widget_get_visible(child);
}

static GtkSizeRequestMode modes_layout_request_mode(GtkLayoutManager *manager G_GNUC_UNUSED,
                                                    GtkWidget *widget G_GNUC_UNUSED) {
  return GTK_SIZE_REQUEST_CONSTANT_SIZE;
}

static void modes_layout_measure(GtkLayoutManager *manager, GtkWidget *widget, GtkOrientation orientation,
                                 int for_size G_GNUC_UNUSED, int *minimum, int *natural, int *min_baseline,
                                 int *nat_baseline) {
  LumaModesLayout *layout = LUMA_MODES_LAYOUT(manager);
  int min = 0, nat = 0, widest_min = 0, widest_nat = 0, count = 0;
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child; child = gtk_widget_get_next_sibling(child)) {
    if (!is_button(layout, child))
      continue;
    int m = 0, n = 0;
    gtk_widget_measure(child, orientation, -1, &m, &n, NULL, NULL);
    count++;
    widest_min = MAX(widest_min, m);
    widest_nat = MAX(widest_nat, n);
    if (orientation == GTK_ORIENTATION_HORIZONTAL) {
      min += m;
      nat += n;
    } else {
      min = MAX(min, m);
      nat = MAX(nat, n);
    }
  }
  if (orientation == GTK_ORIENTATION_HORIZONTAL && layout->owner->stop_width > 0) {
    min = nat = count * layout->owner->stop_width + MAX(0, count - 1) * LUMA_UI_STRUCTURE_MODES_STOP_GAP;
  } else if (orientation == GTK_ORIENTATION_HORIZONTAL && layout->owner->fill && count > 0) {
    min = widest_min * count; /* equal cells: each as wide as the widest needs */
    nat = widest_nat * count;
  }
  *minimum = min;
  *natural = nat;
  *min_baseline = *nat_baseline = -1;
}

static void modes_layout_allocate(GtkLayoutManager *manager, GtkWidget *widget, int width, int height, int baseline) {
  LumaModesLayout *layout = LUMA_MODES_LAYOUT(manager);
  LumaModeSwitch *self = layout->owner;
  int total_min = 0, total_nat = 0, count = 0;
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child; child = gtk_widget_get_next_sibling(child)) {
    if (!is_button(layout, child))
      continue;
    int m = 0, n = 0;
    gtk_widget_measure(child, GTK_ORIENTATION_HORIZONTAL, -1, &m, &n, NULL, NULL);
    total_min += m;
    total_nat += n;
    count++;
  }
  Mode *current = self->current ? find_mode(self, self->current) : NULL;
  gboolean found = FALSE;
  double target_x = 0, target_w = 0;
  int x = 0, index = 0;
  int spare = MAX(0, width - total_min), room = MAX(1, total_nat - total_min);
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child; child = gtk_widget_get_next_sibling(child)) {
    if (!is_button(layout, child))
      continue;
    int m = 0, n = 0;
    gtk_widget_measure(child, GTK_ORIENTATION_HORIZONTAL, -1, &m, &n, NULL, NULL);
    /* Narrower than natural: each its minimum, then what is left shared by what each wants more. */
    int w = total_nat <= width ? n : m + (int)((gint64)spare * (n - m) / room);
    if (self->stop_width > 0)
      w = self->stop_width;
    else if (self->fill && count > 0) /* equal cells; the last takes the remainder */
      w = index == count - 1 ? width - x : width / count;
    index++;
    GskTransform *at = gsk_transform_translate(NULL, &GRAPHENE_POINT_INIT(x, 0));
    gtk_widget_allocate(child, w, height, baseline, at);
    if (current != NULL && child == current->button) {
      found = TRUE;
      target_x = x;
      target_w = w;
    }
    x += w + (self->stop_width > 0 ? LUMA_UI_STRUCTURE_MODES_STOP_GAP : 0);
  }
  if (!found) {
    gtk_widget_set_child_visible(self->indicator, FALSE);
    return;
  }
  double chip_x = target_x, chip_w = target_w;
  if (layout->sliding && layout->progress < 1.0) {
    chip_x = layout->start_x + (target_x - layout->start_x) * layout->progress;
    chip_w = layout->start_w + (target_w - layout->start_w) * layout->progress;
  }
  layout->has_shown = TRUE;
  layout->shown_x = chip_x;
  layout->shown_w = chip_w;
  gtk_widget_set_child_visible(self->indicator, TRUE);
  int im = 0, in = 0;
  gtk_widget_measure(self->indicator, GTK_ORIENTATION_HORIZONTAL, -1, &im, &in, NULL, NULL);
  GskTransform *at = gsk_transform_translate(NULL, &GRAPHENE_POINT_INIT((float)round(chip_x), 0));
  gtk_widget_allocate(self->indicator, MAX(0, (int)round(chip_w)), height, -1, at);
}

static void luma_modes_layout_class_init(LumaModesLayoutClass *klass) {
  GtkLayoutManagerClass *manager_class = GTK_LAYOUT_MANAGER_CLASS(klass);
  manager_class->get_request_mode = modes_layout_request_mode;
  manager_class->measure = modes_layout_measure;
  manager_class->allocate = modes_layout_allocate;
}

static void luma_modes_layout_init(LumaModesLayout *layout) {
  layout->progress = 1.0;
}

static LumaModesLayout *modes_layout(LumaModeSwitch *self) {
  return LUMA_MODES_LAYOUT(gtk_widget_get_layout_manager(self->row));
}

static gboolean anim_tick(GtkWidget *widget, GdkFrameClock *clock, gpointer user_data) {
  LumaModeSwitch *self = user_data;
  LumaModesLayout *layout = modes_layout(self);
  gint64 now = gdk_frame_clock_get_frame_time(clock);
  if (self->began == 0)
    self->began = now;
  double progress = MIN(1.0, (double)(now - self->began) / ((double)self->duration * 1000.0));
  layout->progress = luma_ui_ease(progress);
  gtk_widget_queue_allocate(widget);
  if (progress >= 1.0) {
    self->anim = 0;
    layout->sliding = FALSE;
    return G_SOURCE_REMOVE;
  }
  return G_SOURCE_CONTINUE;
}

/* Slide the chip from where it is to the current button (the layout knows both). */
static void place(LumaModeSwitch *self, gboolean animate) {
  LumaModesLayout *layout = modes_layout(self);
  if (self->anim != 0) {
    gtk_widget_remove_tick_callback(self->row, self->anim);
    self->anim = 0;
  }
  guint duration = animate ? luma_ui_duration(LUMA_UI_MOTION_MORPH_MS, FALSE) : 0;
  if (duration == 0 || luma_ui_reduced_motion() || !layout->has_shown) {
    layout->sliding = FALSE;
    layout->progress = 1.0;
    gtk_widget_queue_allocate(self->row);
    return;
  }
  layout->sliding = TRUE;
  layout->start_x = layout->shown_x;
  layout->start_w = layout->shown_w;
  layout->progress = 0.0;
  self->began = 0;
  self->duration = duration;
  self->anim = gtk_widget_add_tick_callback(self->row, anim_tick, self, NULL);
}

static void schedule_place(LumaModeSwitch *self, gboolean animate) {
  place(self, animate);
}

/* Choosing */

static void update_labels(LumaModeSwitch *self) {
  for (guint i = 0; i < self->modes->len; i++) {
    Mode *mode = g_ptr_array_index(self->modes, i);
    gboolean on = g_strcmp0(mode->key, self->current) == 0;
    gtk_widget_set_visible(mode->label, !self->icon_only && (!self->narrow || on));
    gtk_widget_set_visible(mode->status_text, *gtk_label_get_label(GTK_LABEL(mode->status_text)) != '\0' &&
                           gtk_widget_get_visible(mode->label) &&
                           !(self->narrow && gtk_widget_has_css_class(GTK_WIDGET(self), "in-bar")));
    luma_ui_set_css_class(mode->button, "on", on);
  }
}

static void select_mode(LumaModeSwitch *self, const char *key, gboolean notify) {
  gboolean changed = g_strcmp0(key, self->current) != 0;
  if (changed) {
    g_free(self->current);
    self->current = g_strdup(key);
  }
  self->selecting = TRUE;
  for (guint i = 0; i < self->modes->len; i++) {
    Mode *mode = g_ptr_array_index(self->modes, i);
    gboolean on = g_strcmp0(mode->key, self->current) == 0;
    if (gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(mode->button)) != on)
      gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(mode->button), on);
    luma_ui_set_css_class(mode->button, "on", on);
  }
  self->selecting = FALSE;
  update_labels(self);
  schedule_place(self, TRUE);
  if (notify && changed)
    g_signal_emit(self, signals[CHANGED], 0, self->current);
}

static void mode_toggled(GtkToggleButton *button, gpointer user_data) {
  LumaModeSwitch *self = user_data;
  if (self->selecting || !gtk_toggle_button_get_active(button))
    return;
  const char *key = g_object_get_data(G_OBJECT(button), "luma-mode-key");
  if (g_strcmp0(key, self->current) != 0)
    select_mode(self, key, TRUE);
}

static void width_changed(GtkWidget *widget, int width, gpointer data G_GNUC_UNUSED) {
  LumaModeSwitch *self = LUMA_MODE_SWITCH(widget);
  gboolean narrow = width > 0 && width <= LUMA_UI_STRUCTURE_MODES_NARROW_MAX_WIDTH &&
                   !gtk_widget_has_css_class(widget, "labels-only");
  if (narrow != self->narrow) {
    self->narrow = narrow;
    luma_ui_set_css_class(widget, "narrow", narrow);
    update_labels(self);
    schedule_place(self, FALSE);
  }
}

static void mode_switch_map(GtkWidget *widget) {
  LumaModeSwitch *self = LUMA_MODE_SWITCH(widget);
  GTK_WIDGET_CLASS(luma_mode_switch_parent_class)->map(widget);
  if (self->modes->len < 2)
    g_critical("a mode switch offers at least two modes");
  schedule_place(self, FALSE);
}

static void luma_mode_switch_dispose(GObject *object) {
  LumaModeSwitch *self = LUMA_MODE_SWITCH(object);
  if (self->anim != 0 && self->row != NULL)
    gtk_widget_remove_tick_callback(self->row, self->anim);
  self->anim = 0;
  G_OBJECT_CLASS(luma_mode_switch_parent_class)->dispose(object);
}

static void luma_mode_switch_finalize(GObject *object) {
  LumaModeSwitch *self = LUMA_MODE_SWITCH(object);
  g_clear_pointer(&self->modes, g_ptr_array_unref);
  g_clear_pointer(&self->current, g_free);
  G_OBJECT_CLASS(luma_mode_switch_parent_class)->finalize(object);
}

static void luma_mode_switch_class_init(LumaModeSwitchClass *klass) {
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GtkWidgetClass *widget_class = GTK_WIDGET_CLASS(klass);
  object_class->dispose = luma_mode_switch_dispose;
  object_class->finalize = luma_mode_switch_finalize;
  widget_class->map = mode_switch_map;
  gtk_widget_class_set_accessible_role(widget_class, GTK_ACCESSIBLE_ROLE_RADIO_GROUP);
  /**
   * LumaModeSwitch::changed:
   * @self: the switch
   * @key: the mode a person chose
   */
  signals[CHANGED] = g_signal_new("changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                                  NULL, G_TYPE_NONE, 1, G_TYPE_STRING);
}

static void luma_mode_switch_init(LumaModeSwitch *self) {
  luma_ui_install();
  self->modes = g_ptr_array_new_with_free_func(mode_free);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_set_valign(GTK_WIDGET(self), GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-modes");
  luma_ui_set_accessible_label(GTK_WIDGET(self), "Mode");

  self->row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->row, "lumaui-modes-row");
  self->indicator = g_object_new(GTK_TYPE_BOX, "orientation", GTK_ORIENTATION_HORIZONTAL, "can-target", FALSE,
                                 "accessible-role", GTK_ACCESSIBLE_ROLE_PRESENTATION, NULL);
  gtk_widget_add_css_class(self->indicator, "lumaui-modes-indicator");
  gtk_box_append(GTK_BOX(self->row), self->indicator);
  LumaModesLayout *layout = g_object_new(LUMA_TYPE_MODES_LAYOUT, NULL);
  layout->owner = self;
  gtk_widget_set_layout_manager(self->row, GTK_LAYOUT_MANAGER(layout));
  gtk_box_append(GTK_BOX(self), self->row);
  luma_ui_arrow_keys(self->row, GTK_ORIENTATION_HORIZONTAL, TRUE);
  luma_ui_width_watch(GTK_WIDGET(self), width_changed, NULL, 0);
}

GtkWidget *luma_mode_switch_new(const char *label) {
  /* The role as a construct property too: GtkBox's own GENERIC otherwise wins on some builds
   * (Settings saw "generic" for its hero switches, as save-open did for the toast). */
  GtkWidget *self = g_object_new(LUMA_TYPE_MODE_SWITCH, "accessible-role", GTK_ACCESSIBLE_ROLE_RADIO_GROUP, NULL);
  luma_ui_set_accessible_label(self, label != NULL ? label : "Mode");
  return self;
}

void luma_mode_switch_add(LumaModeSwitch *self, const char *key, const char *label, const char *icon) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  g_return_if_fail(key != NULL && label != NULL);
  if (find_mode(self, key) != NULL) {
    g_critical("mode keys are unique: '%s' is already a mode", key);
    return;
  }
  Mode *mode = g_new0(Mode, 1);
  mode->key = g_strdup(key);
  mode->button = g_object_new(GTK_TYPE_TOGGLE_BUTTON, "accessible-role", GTK_ACCESSIBLE_ROLE_RADIO, NULL);
  if (self->modes->len > 0) {
    Mode *first = g_ptr_array_index(self->modes, 0);
    gtk_toggle_button_set_group(GTK_TOGGLE_BUTTON(mode->button), GTK_TOGGLE_BUTTON(first->button));
  }
  gtk_widget_add_css_class(mode->button, "lumaui-mode");
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_set_halign(line, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(line, "lumaui-mode-line");
  /* A mode without a glyph is words alone; the switch is then labels-only and never folds them. */
  if (icon != NULL && *icon != '\0')
    gtk_box_append(GTK_BOX(line), luma_ui_icon_image(icon, 0));
  else
    gtk_widget_add_css_class(GTK_WIDGET(self), "labels-only");
  mode->label = gtk_label_new(label);
  /* Words alone keep their width (v70 .seg: the row around it wraps instead, Settings' Scale at 390). */
  gtk_label_set_ellipsize(GTK_LABEL(mode->label), icon != NULL && *icon != '\0' ? PANGO_ELLIPSIZE_END
                                                                             : PANGO_ELLIPSIZE_NONE);
  gtk_widget_add_css_class(mode->label, "lumaui-mode-label");
  gtk_box_append(GTK_BOX(line), mode->label);
  mode->status_text = g_object_new(GTK_TYPE_LABEL, "visible", FALSE, NULL);
  gtk_widget_add_css_class(mode->status_text, "lumaui-mode-status");
  mode->status_dot = g_object_new(GTK_TYPE_BOX, "valign", GTK_ALIGN_CENTER, "visible", FALSE, "can-target", FALSE, NULL);
  gtk_widget_add_css_class(mode->status_dot, "lumaui-mode-dot");
  gtk_box_append(GTK_BOX(line), mode->status_text);
  gtk_box_append(GTK_BOX(line), mode->status_dot);
  gtk_button_set_child(GTK_BUTTON(mode->button), line);
  mode_stop_style(self, mode);
  gtk_widget_set_tooltip_text(mode->button, label);
  luma_ui_set_accessible_label(mode->button, label);
  g_object_set_data_full(G_OBJECT(mode->button), "luma-mode-key", g_strdup(key), g_free);
  if (self->current == NULL)
    self->current = g_strdup(key);
  gboolean on = g_strcmp0(key, self->current) == 0;
  self->selecting = TRUE;
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(mode->button), on);
  self->selecting = FALSE;
  g_signal_connect(mode->button, "toggled", G_CALLBACK(mode_toggled), self);
  g_ptr_array_add(self->modes, mode);
  gtk_box_append(GTK_BOX(self->row), mode->button);
  /* A narrow switch folds the new mode's label as the rest. */
  if (self->narrow || self->icon_only)
    update_labels(self);
}

void luma_mode_switch_set_icon_only(LumaModeSwitch *self, gboolean icon_only) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  icon_only = !!icon_only;
  if (self->icon_only == icon_only)
    return;
  self->icon_only = icon_only;
  luma_ui_set_css_class(GTK_WIDGET(self), "icon-only", icon_only);
  update_labels(self);
  schedule_place(self, FALSE);
}

gboolean luma_mode_switch_get_icon_only(LumaModeSwitch *self) {
  g_return_val_if_fail(LUMA_IS_MODE_SWITCH(self), FALSE);
  return self->icon_only;
}

void luma_mode_switch_set_current(LumaModeSwitch *self, const char *key) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  g_return_if_fail(key != NULL);
  if (find_mode(self, key) == NULL) {
    g_critical("unknown mode '%s'", key);
    return;
  }
  select_mode(self, key, FALSE);
}

void luma_mode_switch_set_fill(LumaModeSwitch *self, gboolean fill) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  fill = !!fill;
  if (self->fill == fill)
    return;
  self->fill = fill;
  gtk_widget_set_hexpand(GTK_WIDGET(self), fill && self->stop_width == 0);
  gtk_widget_set_hexpand(self->row, fill && self->stop_width == 0);
  gtk_widget_queue_resize(self->row);
  schedule_place(self, FALSE);
}

gboolean luma_mode_switch_get_fill(LumaModeSwitch *self) {
  g_return_val_if_fail(LUMA_IS_MODE_SWITCH(self), FALSE);
  return self->fill;
}

void luma_mode_switch_set_ellipsize(LumaModeSwitch *self, gboolean ellipsize) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  self->ellipsize = !!ellipsize;
  for (guint i = 0; i < self->modes->len; i++)
    mode_stop_style(self, g_ptr_array_index(self->modes, i));
  gtk_widget_queue_resize(self->row);
  schedule_place(self, FALSE);
}

gboolean luma_mode_switch_get_ellipsize(LumaModeSwitch *self) {
  g_return_val_if_fail(LUMA_IS_MODE_SWITCH(self), FALSE);
  return self->ellipsize;
}

void luma_mode_switch_set_stop_width(LumaModeSwitch *self, int width) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  g_return_if_fail(width >= 0);
  if (self->stop_width == width)
    return;
  self->stop_width = width;
  luma_ui_set_css_class(GTK_WIDGET(self), "fixed-stops", width > 0);
  gtk_widget_set_hexpand(GTK_WIDGET(self), self->fill && width == 0);
  gtk_widget_set_hexpand(self->row, self->fill && width == 0);
  for (guint i = 0; i < self->modes->len; i++)
    mode_stop_style(self, g_ptr_array_index(self->modes, i));
  gtk_widget_queue_resize(self->row);
  schedule_place(self, FALSE);
}

int luma_mode_switch_get_stop_width(LumaModeSwitch *self) {
  g_return_val_if_fail(LUMA_IS_MODE_SWITCH(self), 0);
  return self->stop_width;
}

void luma_mode_switch_set_small(LumaModeSwitch *self, gboolean small) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  self->small = !!small;
  luma_ui_set_css_class(GTK_WIDGET(self), "small", self->small);
  for (guint i = 0; i < self->modes->len; i++)
    mode_stop_style(self, g_ptr_array_index(self->modes, i));
  gtk_widget_queue_resize(self->row);
}

void luma_mode_switch_set_compact(LumaModeSwitch *self, gboolean compact) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  luma_ui_set_css_class(GTK_WIDGET(self), "compact", compact);
}

const char *luma_mode_switch_get_current(LumaModeSwitch *self) {
  g_return_val_if_fail(LUMA_IS_MODE_SWITCH(self), NULL);
  return self->current;
}

void luma_mode_switch_set_status(LumaModeSwitch *self, const char *key, const char *status) {
  g_return_if_fail(LUMA_IS_MODE_SWITCH(self));
  Mode *mode = find_mode(self, key);
  g_return_if_fail(mode != NULL);
  gboolean running = g_strcmp0(status, "running") == 0;
  gtk_accessible_update_property(GTK_ACCESSIBLE(mode->button), GTK_ACCESSIBLE_PROPERTY_DESCRIPTION,
                                  running ? "Running" : (status != NULL ? status : ""), -1);
  gtk_label_set_label(GTK_LABEL(mode->status_text), status != NULL && !running ? status : "");
  gtk_widget_set_visible(mode->status_dot, running);
  update_labels(self);
}
