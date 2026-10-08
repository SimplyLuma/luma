/* SPDX-License-Identifier: Apache-2.0 */
/* The table header, the column view's header and the one selection look:
 * the twin of structure_table.py (Python). */
#include "luma-table-header.h"
#include "luma-ui-private.h"

G_DEFINE_ENUM_TYPE(LumaSortDirection, luma_sort_direction,
                   G_DEFINE_ENUM_VALUE(LUMA_SORT_NONE, "none"),
                   G_DEFINE_ENUM_VALUE(LUMA_SORT_ASCENDING, "ascending"),
                   G_DEFINE_ENUM_VALUE(LUMA_SORT_DESCENDING, "descending"))

static GtkAccessibleSort accessible_sort(LumaSortDirection direction) {
  switch (direction) {
  case LUMA_SORT_ASCENDING:
    return GTK_ACCESSIBLE_SORT_ASCENDING;
  case LUMA_SORT_DESCENDING:
    return GTK_ACCESSIBLE_SORT_DESCENDING;
  case LUMA_SORT_NONE:
  default:
    return GTK_ACCESSIBLE_SORT_NONE;
  }
}

static void speak_sort(GtkWidget *widget, LumaSortDirection direction) {
  gtk_accessible_update_property(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_PROPERTY_SORT, accessible_sort(direction),
                                 -1);
}

/* ── TableHeader ─────────────────────────────────────────────────────── */

enum { SORT_CHANGED, N_SIGNALS };
static guint signals[N_SIGNALS];

typedef struct {
  char *key; /* NULL: a plain label */
  gboolean expand;
  int width;
  GtkWidget *cell;
  GtkWidget *arrow;
  GtkSizeGroup *group;
} Column;

struct _LumaTableHeader {
  GtkBox parent_instance;
  GPtrArray *columns; /* Column */
  char *sort_key;
  LumaSortDirection sort_direction;
};

G_DEFINE_FINAL_TYPE(LumaTableHeader, luma_table_header, GTK_TYPE_BOX)

static void column_free(gpointer data) {
  Column *column = data;
  g_free(column->key);
  g_clear_object(&column->group);
  g_free(column);
}

static Column *find_column(LumaTableHeader *self, const char *key) {
  for (guint i = 0; key != NULL && i < self->columns->len; i++) {
    Column *column = g_ptr_array_index(self->columns, i);
    if (g_strcmp0(column->key, key) == 0)
      return column;
  }
  return NULL;
}

static void heading_clicked(GtkButton *button, gpointer user_data) {
  const char *key = g_object_get_data(G_OBJECT(button), "luma-column-key");
  luma_table_header_activate_column(user_data, key);
}

static void table_header_map(GtkWidget *widget) {
  GTK_WIDGET_CLASS(luma_table_header_parent_class)->map(widget);
  if (LUMA_TABLE_HEADER(widget)->columns->len == 0)
    g_critical("a table header has at least one column");
}

static void luma_table_header_finalize(GObject *object) {
  LumaTableHeader *self = LUMA_TABLE_HEADER(object);
  g_clear_pointer(&self->columns, g_ptr_array_unref);
  g_clear_pointer(&self->sort_key, g_free);
  G_OBJECT_CLASS(luma_table_header_parent_class)->finalize(object);
}

static void luma_table_header_class_init(LumaTableHeaderClass *klass) {
  G_OBJECT_CLASS(klass)->finalize = luma_table_header_finalize;
  GTK_WIDGET_CLASS(klass)->map = table_header_map;
  gtk_widget_class_set_accessible_role(GTK_WIDGET_CLASS(klass), GTK_ACCESSIBLE_ROLE_ROW);
  /**
   * LumaTableHeader::sort-changed:
   * @self: the header
   * @key: the column to sort by
   * @direction: which way
   *
   * A person sorted by a heading; the app sorts its rows.
   */
  signals[SORT_CHANGED] = g_signal_new("sort-changed", G_TYPE_FROM_CLASS(klass), G_SIGNAL_RUN_LAST, 0, NULL, NULL,
                                       NULL, G_TYPE_NONE, 2, G_TYPE_STRING, LUMA_TYPE_SORT_DIRECTION);
}

static void luma_table_header_init(LumaTableHeader *self) {
  luma_ui_install();
  self->columns = g_ptr_array_new_with_free_func(column_free);
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_HORIZONTAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-thead");
  luma_ui_arrow_keys(GTK_WIDGET(self), GTK_ORIENTATION_HORIZONTAL, FALSE);
}

GtkWidget *luma_table_header_new(void) { return g_object_new(LUMA_TYPE_TABLE_HEADER, NULL); }

void luma_table_header_add_column(LumaTableHeader *self, const char *key, const char *label, gboolean expand,
                                  int width, gboolean end) {
  g_return_if_fail(LUMA_IS_TABLE_HEADER(self));
  if (label == NULL)
    label = "";
  if (key != NULL && *key == '\0')
    key = NULL;
  if (find_column(self, key) != NULL) {
    g_critical("column keys are unique: '%s' is already a column", key);
    return;
  }
  Column *column = g_new0(Column, 1);
  column->key = g_strdup(key);
  column->expand = expand;
  column->width = width;
  GtkWidget *cell;
  if (key == NULL) {
    cell = g_object_new(GTK_TYPE_LABEL, "label", label, "xalign", end ? 1.0f : 0.0f, "ellipsize",
                        PANGO_ELLIPSIZE_END, "accessible-role", GTK_ACCESSIBLE_ROLE_COLUMN_HEADER, NULL);
    gtk_widget_add_css_class(cell, "lumaui-thead-label");
  } else {
    cell = g_object_new(GTK_TYPE_BUTTON, "accessible-role", GTK_ACCESSIBLE_ROLE_COLUMN_HEADER, NULL);
    gtk_widget_add_css_class(cell, "lumaui-thead-cell");
    GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_halign(line, end ? GTK_ALIGN_END : GTK_ALIGN_START);
    gtk_widget_add_css_class(line, "lumaui-thead-line");
    gtk_box_append(GTK_BOX(line), gtk_label_new(label)); /* a heading is a word or two: it never truncates */
    column->arrow = luma_ui_icon_image("arrow-up", 0);
    gtk_widget_add_css_class(column->arrow, "lumaui-thead-arrow");
    gtk_widget_set_visible(column->arrow, FALSE);
    gtk_box_append(GTK_BOX(line), column->arrow);
    gtk_button_set_child(GTK_BUTTON(cell), line);
    luma_ui_set_accessible_label(cell, label);
    speak_sort(cell, LUMA_SORT_NONE);
    g_object_set_data_full(G_OBJECT(cell), "luma-column-key", g_strdup(key), g_free);
    g_signal_connect(cell, "clicked", G_CALLBACK(heading_clicked), self);
  }
  if (end)
    gtk_widget_add_css_class(cell, "end");
  gtk_widget_set_hexpand(cell, expand);
  if (width > 0)
    gtk_widget_set_size_request(cell, width, -1);
  column->cell = cell;
  /* Every column shares one width across the header and its rows, the
   * expanding ones too: otherwise the columns wander from row to row. */
  column->group = gtk_size_group_new(GTK_SIZE_GROUP_HORIZONTAL);
  gtk_size_group_add_widget(column->group, cell);
  g_ptr_array_add(self->columns, column);
  gtk_box_append(GTK_BOX(self), cell);
}

void luma_table_header_set_sort(LumaTableHeader *self, const char *key, LumaSortDirection direction) {
  g_return_if_fail(LUMA_IS_TABLE_HEADER(self));
  if (key == NULL) {
    g_clear_pointer(&self->sort_key, g_free);
    self->sort_direction = LUMA_SORT_NONE;
  } else {
    if (find_column(self, key) == NULL) {
      g_critical("no sortable column '%s'", key);
      return;
    }
    if (direction != LUMA_SORT_ASCENDING && direction != LUMA_SORT_DESCENDING) {
      g_critical("sort direction is ascending or descending");
      return;
    }
    if (self->sort_key != key) {
      g_free(self->sort_key);
      self->sort_key = g_strdup(key);
    }
    self->sort_direction = direction;
  }
  for (guint i = 0; i < self->columns->len; i++) {
    Column *column = g_ptr_array_index(self->columns, i);
    if (column->key == NULL)
      continue;
    gboolean active = g_strcmp0(self->sort_key, column->key) == 0;
    LumaSortDirection state = active ? self->sort_direction : LUMA_SORT_NONE;
    luma_ui_set_css_class(column->cell, "active", active);
    g_autofree char *icon = luma_ui_icon_name(state == LUMA_SORT_ASCENDING ? "arrow-up" : "arrow-down");
    gtk_image_set_from_icon_name(GTK_IMAGE(column->arrow), icon);
    gtk_widget_set_visible(column->arrow, active);
    speak_sort(column->cell, state);
  }
}

const char *luma_table_header_get_sort_key(LumaTableHeader *self) {
  g_return_val_if_fail(LUMA_IS_TABLE_HEADER(self), NULL);
  return self->sort_key;
}

LumaSortDirection luma_table_header_get_sort_direction(LumaTableHeader *self) {
  g_return_val_if_fail(LUMA_IS_TABLE_HEADER(self), LUMA_SORT_NONE);
  return self->sort_direction;
}

LumaSortDirection luma_table_header_activate_column(LumaTableHeader *self, const char *key) {
  g_return_val_if_fail(LUMA_IS_TABLE_HEADER(self), LUMA_SORT_NONE);
  g_return_val_if_fail(key != NULL, LUMA_SORT_NONE);
  if (find_column(self, key) == NULL) {
    g_critical("no sortable column '%s'", key);
    return self->sort_direction;
  }
  LumaSortDirection direction = LUMA_SORT_ASCENDING;
  if (g_strcmp0(self->sort_key, key) == 0)
    direction = self->sort_direction == LUMA_SORT_ASCENDING ? LUMA_SORT_DESCENDING : LUMA_SORT_ASCENDING;
  g_autofree char *owned = g_strdup(key);
  luma_table_header_set_sort(self, owned, direction);
  g_signal_emit(self, signals[SORT_CHANGED], 0, owned, direction);
  return direction;
}

void luma_table_header_align(LumaTableHeader *self, GtkWidget *row) {
  g_return_if_fail(LUMA_IS_TABLE_HEADER(self));
  g_return_if_fail(GTK_IS_WIDGET(row));
  guint n = 0;
  for (GtkWidget *child = gtk_widget_get_first_child(row); child != NULL; child = gtk_widget_get_next_sibling(child))
    n++;
  if (n != self->columns->len) {
    g_critical("a row aligned to this header has %u cells, not %u", self->columns->len, n);
    return;
  }
  gtk_widget_add_css_class(row, "lumaui-thead-row");
  GtkWidget *cell = gtk_widget_get_first_child(row);
  for (guint i = 0; i < self->columns->len; i++, cell = gtk_widget_get_next_sibling(cell)) {
    Column *column = g_ptr_array_index(self->columns, i);
    gtk_widget_set_hexpand(cell, column->expand);
    if (column->width > 0)
      gtk_widget_set_size_request(cell, column->width, -1);
    gtk_size_group_add_widget(column->group, cell);
  }
}

/* ── ColumnViewHeader ────────────────────────────────────────────────── */

struct _LumaColumnViewHeader {
  GObject parent_instance;
  GtkColumnView *view; /* weak: the view owns the header */
  GtkSorter *sorter;
  gulong sorter_handler;
  char *sort_key;
  LumaSortDirection sort_direction;
  gboolean visible;
};

G_DEFINE_FINAL_TYPE(LumaColumnViewHeader, luma_column_view_header, G_TYPE_OBJECT)

static void luma_column_view_header_dispose(GObject *object) {
  LumaColumnViewHeader *self = LUMA_COLUMN_VIEW_HEADER(object);
  if (self->sorter != NULL)
    g_clear_signal_handler(&self->sorter_handler, self->sorter);
  g_clear_object(&self->sorter);
  self->view = NULL;
  G_OBJECT_CLASS(luma_column_view_header_parent_class)->dispose(object);
}

static void luma_column_view_header_finalize(GObject *object) {
  g_clear_pointer(&LUMA_COLUMN_VIEW_HEADER(object)->sort_key, g_free);
  G_OBJECT_CLASS(luma_column_view_header_parent_class)->finalize(object);
}

static void luma_column_view_header_class_init(LumaColumnViewHeaderClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_column_view_header_dispose;
  G_OBJECT_CLASS(klass)->finalize = luma_column_view_header_finalize;
}

static void luma_column_view_header_init(LumaColumnViewHeader *self) { self->visible = TRUE; }

static GtkWidget *column_view_header_widget(GtkColumnView *view) {
  GtkWidget *header = gtk_widget_get_first_child(GTK_WIDGET(view));
  while (header != NULL && g_strcmp0(gtk_widget_get_css_name(header), "header") != 0)
    header = gtk_widget_get_next_sibling(header);
  return header;
}

static void sorter_changed(GtkSorter *sorter G_GNUC_UNUSED, GtkSorterChange change G_GNUC_UNUSED,
                           gpointer user_data) {
  luma_column_view_header_sync(user_data);
}

static void view_mapped(GtkWidget *view G_GNUC_UNUSED, gpointer user_data) {
  luma_column_view_header_sync(user_data);
}

LumaColumnViewHeader *luma_table_header_for_column_view(GtkColumnView *view) {
  g_return_val_if_fail(GTK_IS_COLUMN_VIEW(view), NULL);
  LumaColumnViewHeader *self = g_object_get_data(G_OBJECT(view), "luma-column-view-header");
  if (self != NULL)
    return self;
  luma_ui_install();
  self = g_object_new(LUMA_TYPE_COLUMN_VIEW_HEADER, NULL);
  self->view = view;
  g_object_set_data_full(G_OBJECT(view), "luma-column-view-header", self, g_object_unref);
  gtk_widget_add_css_class(GTK_WIDGET(view), "lumaui-table");
  luma_selection_apply(GTK_WIDGET(view));
  GtkSorter *sorter = gtk_column_view_get_sorter(view);
  if (sorter != NULL) {
    self->sorter = g_object_ref(sorter);
    self->sorter_handler = g_signal_connect(sorter, "changed", G_CALLBACK(sorter_changed), self);
  }
  g_signal_connect_object(view, "map", G_CALLBACK(view_mapped), self, 0);
  luma_column_view_header_sync(self);
  return self;
}

void luma_column_view_header_sync(LumaColumnViewHeader *self) {
  g_return_if_fail(LUMA_IS_COLUMN_VIEW_HEADER(self));
  if (self->view == NULL)
    return;
  GtkColumnViewSorter *sorter = GTK_IS_COLUMN_VIEW_SORTER(self->sorter) ? GTK_COLUMN_VIEW_SORTER(self->sorter) : NULL;
  GtkColumnViewColumn *column = sorter != NULL ? gtk_column_view_sorter_get_primary_sort_column(sorter) : NULL;
  LumaSortDirection direction = LUMA_SORT_NONE;
  if (column != NULL)
    direction = gtk_column_view_sorter_get_primary_sort_order(sorter) == GTK_SORT_ASCENDING ? LUMA_SORT_ASCENDING
                                                                                             : LUMA_SORT_DESCENDING;
  /* The column title widgets, in column order (GTK's columnview > header > button). */
  GtkWidget *header = column_view_header_widget(self->view);
  if (header != NULL)
    gtk_widget_set_visible(header, self->visible);
  GListModel *columns = gtk_column_view_get_columns(self->view);
  GtkWidget *title = header != NULL ? gtk_widget_get_first_child(header) : NULL;
  for (guint index = 0; index < g_list_model_get_n_items(columns) && title != NULL; index++) {
    g_autoptr(GtkColumnViewColumn) item = g_list_model_get_item(columns, index);
    gboolean active = item == column;
    luma_ui_set_css_class(title, "active", active);
    speak_sort(title, active ? direction : LUMA_SORT_NONE);
    title = gtk_widget_get_next_sibling(title);
  }
  g_free(self->sort_key);
  self->sort_key = column != NULL ? g_strdup(gtk_column_view_column_get_id(column)) : NULL;
  self->sort_direction = direction;
}

void luma_column_view_header_set_visible(LumaColumnViewHeader *self, gboolean visible) {
  g_return_if_fail(LUMA_IS_COLUMN_VIEW_HEADER(self));
  self->visible = !!visible;
  if (self->view != NULL) {
    GtkWidget *header = column_view_header_widget(self->view);
    if (header != NULL)
      gtk_widget_set_visible(header, self->visible);
  }
}

gboolean luma_column_view_header_get_visible(LumaColumnViewHeader *self) {
  g_return_val_if_fail(LUMA_IS_COLUMN_VIEW_HEADER(self), FALSE);
  return self->visible;
}

const char *luma_column_view_header_get_sort_key(LumaColumnViewHeader *self) {
  g_return_val_if_fail(LUMA_IS_COLUMN_VIEW_HEADER(self), NULL);
  return self->sort_key;
}

LumaSortDirection luma_column_view_header_get_sort_direction(LumaColumnViewHeader *self) {
  g_return_val_if_fail(LUMA_IS_COLUMN_VIEW_HEADER(self), LUMA_SORT_NONE);
  return self->sort_direction;
}

/* ── Selection ───────────────────────────────────────────────────────── */

void luma_selection_apply(GtkWidget *widget) {
  g_return_if_fail(GTK_IS_WIDGET(widget));
  if (!GTK_IS_LIST_BOX(widget) && !GTK_IS_LIST_VIEW(widget) && !GTK_IS_GRID_VIEW(widget) &&
      !GTK_IS_FLOW_BOX(widget) && !GTK_IS_COLUMN_VIEW(widget)) {
    g_critical("luma_selection_apply takes a GtkListBox, GtkListView, GtkGridView, GtkFlowBox or GtkColumnView");
    return;
  }
  gtk_widget_add_css_class(widget, "lumaui-selection");
}

void luma_selection_mark(GtkWidget *widget, gboolean selected) {
  g_return_if_fail(GTK_IS_WIDGET(widget));
  luma_ui_set_css_class(widget, "lumaui-selected", selected);
  if (selected)
    luma_ui_set_css_class(widget, "lumaui-trail", FALSE);
  gtk_accessible_update_state(GTK_ACCESSIBLE(widget), GTK_ACCESSIBLE_STATE_SELECTED, selected ? TRUE : FALSE, -1);
}

void luma_selection_trail(GtkWidget *widget, gboolean on) {
  g_return_if_fail(GTK_IS_WIDGET(widget));
  luma_ui_set_css_class(widget, "lumaui-trail", on);
  if (on)
    luma_ui_set_css_class(widget, "lumaui-selected", FALSE);
}
