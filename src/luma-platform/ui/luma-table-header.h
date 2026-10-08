/* SPDX-License-Identifier: Apache-2.0 */
/* Twin of structure_table.py: TableHeader, Column, ColumnViewHeader,
 * Selection (Python). */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaSortDirection:
 * @LUMA_SORT_NONE: unsorted
 * @LUMA_SORT_ASCENDING: ascending ("ascending" in Python)
 * @LUMA_SORT_DESCENDING: descending ("descending" in Python)
 */
typedef enum {
  LUMA_SORT_NONE,
  LUMA_SORT_ASCENDING,
  LUMA_SORT_DESCENDING,
} LumaSortDirection;
GType luma_sort_direction_get_type(void);
#define LUMA_TYPE_SORT_DIRECTION (luma_sort_direction_get_type())

/**
 * LumaTableHeader:
 *
 * The recessed, sortable header bar for a list built from rows. A click on a
 * heading sorts by it, or reverses it; the kit shows the arrow and speaks the
 * sort, the app sorts (#LumaTableHeader::sort-changed: const char *key,
 * #LumaSortDirection direction).
 */
#define LUMA_TYPE_TABLE_HEADER (luma_table_header_get_type())
G_DECLARE_FINAL_TYPE(LumaTableHeader, luma_table_header, LUMA, TABLE_HEADER, GtkBox)

/**
 * luma_table_header_new:
 *
 * Returns: (transfer floating): a new header; add columns in order
 */
GtkWidget *luma_table_header_new(void);
/**
 * luma_table_header_add_column:
 * @self: a header
 * @key: (nullable): the column's sort key; %NULL for a plain label
 * @label: the heading
 * @expand: whether it shares what room is left
 * @width: a fixed width for a narrow column (a track number), or -1
 * @end: whether the heading (and the column's numbers) align to the end
 */
void luma_table_header_add_column(LumaTableHeader *self, const char *key, const char *label,
                                  gboolean expand, int width, gboolean end);
/**
 * luma_table_header_set_sort:
 * @self: a header
 * @key: (nullable): the sorted column, %NULL for none
 * @direction: the direction
 *
 * Show the list as sorted by @key, without emitting
 * #LumaTableHeader::sort-changed.
 */
void luma_table_header_set_sort(LumaTableHeader *self, const char *key, LumaSortDirection direction);
/**
 * luma_table_header_get_sort_key:
 * @self: a header
 *
 * Returns: (nullable): the sorted column's key
 */
const char *luma_table_header_get_sort_key(LumaTableHeader *self);
LumaSortDirection luma_table_header_get_sort_direction(LumaTableHeader *self);
/**
 * luma_table_header_activate_column:
 * @self: a header
 * @key: a sortable column's key
 *
 * What a click on its heading does.
 *
 * Returns: the new direction
 */
LumaSortDirection luma_table_header_activate_column(LumaTableHeader *self, const char *key);
/**
 * luma_table_header_align:
 * @self: a header
 * @row: a row whose children are its cells, one per column
 *
 * Give a row's cells the header's column widths.
 */
void luma_table_header_align(LumaTableHeader *self, GtkWidget *row);

/**
 * LumaColumnViewHeader:
 *
 * A #GtkColumnView's own header in the LumaUI look: the recessed bar, the
 * Lucide arrows, the sort spoken on each heading. For Filer's list view.
 * It follows the view's sorter by itself.
 */
#define LUMA_TYPE_COLUMN_VIEW_HEADER (luma_column_view_header_get_type())
G_DECLARE_FINAL_TYPE(LumaColumnViewHeader, luma_column_view_header, LUMA, COLUMN_VIEW_HEADER, GObject)

/**
 * luma_table_header_for_column_view:
 * @view: a column view
 *
 * Give @view's header the LumaUI bar, arrows and sort state (once per view).
 *
 * Returns: (transfer none): the header, owned by @view
 */
LumaColumnViewHeader *luma_table_header_for_column_view(GtkColumnView *view);
/**
 * luma_column_view_header_sync:
 * @self: a header
 *
 * Read the view's sorter again (it is also followed by itself).
 */
void luma_column_view_header_sync(LumaColumnViewHeader *self);
/**
 * luma_column_view_header_set_visible:
 * @self: a column view header
 * @visible: whether to show the sortable headings
 *
 * Hide or restore the native heading row, including its layout space. This
 * lets the same #GtkColumnView present grouped search results and a sortable
 * list without application CSS or an empty row above the results.
 */
void luma_column_view_header_set_visible(LumaColumnViewHeader *self, gboolean visible);
/**
 * luma_column_view_header_get_visible:
 * @self: a column view header
 *
 * Returns: whether the native heading row is visible
 */
gboolean luma_column_view_header_get_visible(LumaColumnViewHeader *self);
/**
 * luma_column_view_header_get_sort_key:
 * @self: a header
 *
 * Returns: (nullable): the sorted column's id (#GtkColumnViewColumn:id)
 */
const char *luma_column_view_header_get_sort_key(LumaColumnViewHeader *self);
LumaSortDirection luma_column_view_header_get_sort_direction(LumaColumnViewHeader *self);

/**
 * luma_selection_apply:
 * @widget: a #GtkListBox, #GtkFlowBox, #GtkListView, #GtkGridView or
 *   #GtkColumnView
 *
 * Its selected rows wear the one selection look, the raised chip.
 */
void luma_selection_apply(GtkWidget *widget);
/**
 * luma_selection_mark:
 * @widget: a row an app selects by hand (a tile, a column-view step)
 * @selected: whether it is selected
 */
void luma_selection_mark(GtkWidget *widget, gboolean selected);
/**
 * luma_selection_trail:
 * @widget: an earlier step of a path (column view)
 * @on: whether it is on the path
 *
 * The same shape as the chip, quieter.
 */
void luma_selection_trail(GtkWidget *widget, gboolean on);

G_END_DECLS
