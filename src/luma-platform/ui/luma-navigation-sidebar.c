/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-navigation-sidebar.h"
#include "luma-ui-private.h"
#include "luma-ui-tokens-private.h"

struct _LumaNavigationSidebar {
  GtkBox parent_instance;
  GtkWidget *header;
  GtkWidget *list;
  GtkWidget *footer;
  char *variant;
};
G_DEFINE_FINAL_TYPE(LumaNavigationSidebar, luma_navigation_sidebar, GTK_TYPE_BOX)

static const char *const variants[] = {"people", "destinations", "resources", "files", "tree", "settings"};
static const int variant_widths[] = {LUMA_UI_NAV_PEOPLE_WIDTH, LUMA_UI_NAV_DESTINATIONS_WIDTH,
                                     LUMA_UI_NAV_RESOURCES_WIDTH, LUMA_UI_NAV_FILES_WIDTH,
                                     LUMA_UI_NAV_TREE_WIDTH, LUMA_UI_NAV_SETTINGS_WIDTH};

static void mark_body(LumaNavigationSidebar *self, gboolean shown) {
  GtkWidget *body = gtk_widget_get_parent(GTK_WIDGET(self));
  while (body != NULL && !gtk_widget_has_css_class(body, "luma-window-body"))
    body = gtk_widget_get_parent(body);
  if (body == NULL) return;
  int count = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(body), "luma-frame-sidebars"));
  count = MAX(0, count + (shown ? 1 : -1));
  g_object_set_data(G_OBJECT(body), "luma-frame-sidebars", GINT_TO_POINTER(count));
  luma_ui_set_css_class(body, "lumaui-frame-sidebar", count > 0);
}

/* v70 .cfside's own right padding is its gap to the island: in the Settings variant the frame
 * gives none of its own (the frame box's spacing is 0 while it is shown). */
static void frame_gap(LumaNavigationSidebar *self, gboolean shown) {
  /* The frame box may hold the sidebar through a wrapper (LumaSidebarToggle's revealer). */
  GtkWidget *frame = gtk_widget_get_parent(GTK_WIDGET(self));
  while (frame != NULL && !GTK_IS_BOX(frame))
    frame = gtk_widget_get_parent(frame);
  if (frame == NULL || g_strcmp0(self->variant, "settings") != 0)
    return;
  gtk_box_set_spacing(GTK_BOX(frame), shown ? 0 : LUMA_UI_WINDOW_GUTTER);
}

static void sidebar_mapped(GtkWidget *widget, gpointer data G_GNUC_UNUSED) {
  mark_body(LUMA_NAVIGATION_SIDEBAR(widget), TRUE);
  frame_gap(LUMA_NAVIGATION_SIDEBAR(widget), TRUE);
}
static void sidebar_unmapped(GtkWidget *widget, gpointer data G_GNUC_UNUSED) {
  mark_body(LUMA_NAVIGATION_SIDEBAR(widget), FALSE);
  /* Hidden (the toggle's revealer, a phone), it gives no gap either: the body's own 8 is the island's inset. */
  frame_gap(LUMA_NAVIGATION_SIDEBAR(widget), TRUE);
}

static void luma_navigation_sidebar_dispose(GObject *object) {
  LumaNavigationSidebar *self = LUMA_NAVIGATION_SIDEBAR(object);
  g_clear_pointer(&self->variant, g_free);
  G_OBJECT_CLASS(luma_navigation_sidebar_parent_class)->dispose(object);
}
static void luma_navigation_sidebar_class_init(LumaNavigationSidebarClass *klass) {
  G_OBJECT_CLASS(klass)->dispose = luma_navigation_sidebar_dispose;
}

static void luma_navigation_sidebar_init(LumaNavigationSidebar *self) {
  gtk_orientable_set_orientation(GTK_ORIENTABLE(self), GTK_ORIENTATION_VERTICAL);
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-navigation-sidebar");
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-sidebar-surface");
  gtk_widget_set_hexpand(GTK_WIDGET(self), FALSE);
  gtk_widget_set_margin_end(GTK_WIDGET(self), LUMA_UI_SIDEBAR_GUTTER);
  gtk_widget_set_size_request(GTK_WIDGET(self), LUMA_UI_SIDEBAR_WIDTH - 2 * LUMA_UI_SIDEBAR_GUTTER, -1);
  self->header = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_visible(self->header, FALSE);
  gtk_box_append(GTK_BOX(self), self->header);
  GtkWidget *scroll = gtk_scrolled_window_new();
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(scroll), GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
  /* A visible navigation scrollbar gets its own track at the sidebar's edge;
   * it must never paint over destination labels or the selected chip. */
  gtk_scrolled_window_set_overlay_scrolling(GTK_SCROLLED_WINDOW(scroll), FALSE);
  gtk_widget_set_vexpand(scroll, TRUE);
  self->list = gtk_list_box_new();
  gtk_widget_add_css_class(self->list, "luma-navigation-list");
  gtk_list_box_set_selection_mode(GTK_LIST_BOX(self->list), GTK_SELECTION_SINGLE);
  gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroll), self->list);
  gtk_box_append(GTK_BOX(self), scroll);
  self->footer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
  gtk_widget_add_css_class(self->footer, "luma-navigation-footer");
  gtk_widget_set_visible(self->footer, FALSE);
  gtk_box_append(GTK_BOX(self), self->footer);
  g_signal_connect(self, "map", G_CALLBACK(sidebar_mapped), NULL);
  g_signal_connect(self, "unmap", G_CALLBACK(sidebar_unmapped), NULL);
}

GtkWidget *luma_navigation_sidebar_new(const char *variant, const char *width) {
  GtkWidget *self = g_object_new(LUMA_TYPE_NAVIGATION_SIDEBAR, NULL);
  luma_navigation_sidebar_set_variant(LUMA_NAVIGATION_SIDEBAR(self), variant, width);
  return self;
}

void luma_navigation_sidebar_set_variant(LumaNavigationSidebar *self, const char *variant, const char *width) {
  g_return_if_fail(LUMA_IS_NAVIGATION_SIDEBAR(self));
  int pixels = LUMA_UI_SIDEBAR_WIDTH;
  if (variant != NULL) {
    guint i;
    for (i = 0; i < G_N_ELEMENTS(variants); i++)
      if (g_str_equal(variant, variants[i])) break;
    g_return_if_fail(i < G_N_ELEMENTS(variants));
    pixels = variant_widths[i];
  }
  if (width != NULL) {
    if (g_str_equal(width, "narrow")) pixels = LUMA_UI_NAV_WIDTHS_NARROW;
    else if (g_str_equal(width, "regular")) pixels = LUMA_UI_NAV_WIDTHS_REGULAR;
    else if (g_str_equal(width, "wide")) pixels = LUMA_UI_NAV_WIDTHS_WIDE;
    else { g_return_if_fail(FALSE); }
  }
  if (self->variant != NULL) {
    g_autofree char *old = g_strconcat("lumaui-sidebar-", self->variant, NULL);
    gtk_widget_remove_css_class(GTK_WIDGET(self), old);
  }
  g_free(self->variant);
  self->variant = g_strdup(variant);
  luma_ui_set_css_class(GTK_WIDGET(self), "lumaui-sidebar-variant", variant != NULL || width != NULL);
  if (variant != NULL) {
    g_autofree char *name = g_strconcat("lumaui-sidebar-", variant, NULL);
    gtk_widget_add_css_class(GTK_WIDGET(self), name);
  }
  gtk_widget_set_size_request(GTK_WIDGET(self), pixels - 2 * LUMA_UI_SIDEBAR_GUTTER, -1);
}

void luma_navigation_sidebar_append_header(LumaNavigationSidebar *self, GtkWidget *child) {
  g_return_if_fail(LUMA_IS_NAVIGATION_SIDEBAR(self) && GTK_IS_WIDGET(child));
  gtk_box_append(GTK_BOX(self->header), child);
  gtk_widget_set_visible(self->header, TRUE);
}
void luma_navigation_sidebar_append_footer(LumaNavigationSidebar *self, GtkWidget *child) {
  g_return_if_fail(LUMA_IS_NAVIGATION_SIDEBAR(self) && GTK_IS_WIDGET(child));
  gtk_box_append(GTK_BOX(self->footer), child);
  gtk_widget_set_visible(self->footer, TRUE);
}
void luma_navigation_sidebar_append_row(LumaNavigationSidebar *self, GtkListBoxRow *row) {
  g_return_if_fail(LUMA_IS_NAVIGATION_SIDEBAR(self) && GTK_IS_LIST_BOX_ROW(row));
  gtk_list_box_append(GTK_LIST_BOX(self->list), GTK_WIDGET(row));
}
void luma_navigation_sidebar_append_section(LumaNavigationSidebar *self, const char *label) {
  g_return_if_fail(LUMA_IS_NAVIGATION_SIDEBAR(self) && label != NULL);
  GtkWidget *row = gtk_list_box_row_new();
  gtk_list_box_row_set_selectable(GTK_LIST_BOX_ROW(row), FALSE);
  gtk_list_box_row_set_activatable(GTK_LIST_BOX_ROW(row), FALSE);
  gtk_widget_add_css_class(row, "luma-navigation-section");
  gtk_widget_add_css_class(row, "lumaui-row-section");
  GtkWidget *line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(line, "luma-navigation-heading-line");
  GtkWidget *heading = gtk_label_new(label);
  gtk_label_set_xalign(GTK_LABEL(heading), 0);
  gtk_widget_set_hexpand(heading, TRUE);
  gtk_widget_add_css_class(heading, "luma-navigation-heading");
  gtk_box_append(GTK_BOX(line), heading);
  gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(row), line);
  luma_ui_set_accessible_label(row, label);
  gtk_list_box_append(GTK_LIST_BOX(self->list), row);
}
void luma_navigation_sidebar_clear(LumaNavigationSidebar *self) {
  g_return_if_fail(LUMA_IS_NAVIGATION_SIDEBAR(self));
  for (GtkWidget *child = gtk_widget_get_first_child(self->list); child != NULL; ) {
    GtkWidget *next = gtk_widget_get_next_sibling(child);
    gtk_list_box_remove(GTK_LIST_BOX(self->list), child);
    child = next;
  }
}
GtkListBox *luma_navigation_sidebar_get_list(LumaNavigationSidebar *self) {
  g_return_val_if_fail(LUMA_IS_NAVIGATION_SIDEBAR(self), NULL);
  return GTK_LIST_BOX(self->list);
}

/* Opted-in native sidebars keep their GtkListBoxRow objects, action handlers,
 * selection and DnD. A compact rail only changes the presentation of rows
 * with the common icon/label structure; other row types remain untouched.
 * Calling adapt_live again after an app adds rows dresses the new ones. */
static void adapt_live_rail_rows(GtkWidget *widget, gboolean roomy) {
  if (GTK_IS_LIST_BOX_ROW(widget)) {
    GtkWidget *child = gtk_list_box_row_get_child(GTK_LIST_BOX_ROW(widget));
    if (GTK_IS_REVEALER(child)) child = gtk_revealer_get_child(GTK_REVEALER(child));
    if (GTK_IS_BOX(child)) {
      GtkWidget *icon = gtk_widget_get_first_child(child);
      GtkWidget *label = icon != NULL ? gtk_widget_get_next_sibling(icon) : NULL;
      if (GTK_IS_IMAGE(icon) && GTK_IS_LABEL(label)) {
        gtk_orientable_set_orientation(GTK_ORIENTABLE(child), GTK_ORIENTATION_VERTICAL);
        gtk_box_set_spacing(GTK_BOX(child), roomy ? 4 : 2);
        gtk_widget_set_valign(child, GTK_ALIGN_CENTER);
        gtk_widget_set_halign(icon, GTK_ALIGN_CENTER);
        gtk_widget_set_margin_end(icon, 0);
        gtk_widget_set_halign(label, GTK_ALIGN_FILL);
        gtk_widget_set_margin_end(label, 0);
        gtk_label_set_xalign(GTK_LABEL(label), 0.5f);
        gtk_label_set_ellipsize(GTK_LABEL(label), roomy ? PANGO_ELLIPSIZE_NONE : PANGO_ELLIPSIZE_END);
        gtk_label_set_wrap(GTK_LABEL(label), roomy);
        gtk_label_set_wrap_mode(GTK_LABEL(label), PANGO_WRAP_WORD_CHAR);
        gtk_label_set_max_width_chars(GTK_LABEL(label), roomy ? 18 : 10);
        gtk_widget_set_size_request(widget, -1, roomy ? 64 : 60);
        gtk_widget_add_css_class(widget, "luma-live-rail-row");
        GtkWidget *header = gtk_list_box_row_get_header(GTK_LIST_BOX_ROW(widget));
        if (header != NULL) {
          /* The first section title adds noise to a rail. Subsequent
           * section headings mark the boundary before drives and favorites. */
          gtk_widget_set_visible(header, roomy && GTK_IS_BOX(header));
          if (roomy && GTK_IS_BOX(header)) {
            gtk_widget_add_css_class(header, "luma-live-rail-section");
            GtkWidget *heading = gtk_widget_get_last_child(header);
            if (GTK_IS_LABEL(heading)) gtk_label_set_xalign(GTK_LABEL(heading), 0.5f);
          }
        }
      }
    }
  }
  for (GtkWidget *child = gtk_widget_get_first_child(widget); child != NULL;
       child = gtk_widget_get_next_sibling(child))
    adapt_live_rail_rows(child, roomy);
}

void luma_navigation_sidebar_adapt_live(AdwOverlaySplitView *split_view,
                                        GtkWidget *live_sidebar,
                                        const char *width) {
  g_return_if_fail(ADW_IS_OVERLAY_SPLIT_VIEW(split_view));
  g_return_if_fail(GTK_IS_WIDGET(live_sidebar));
  GtkWidget *surface = adw_overlay_split_view_get_sidebar(split_view);
  g_return_if_fail(surface != NULL);
  GtkWidget *ancestor = live_sidebar;
  while (ancestor != NULL && ancestor != surface)
    ancestor = gtk_widget_get_parent(ancestor);
  g_return_if_fail(ancestor == surface);

  int pixels = 0;
  if (g_strcmp0(width, "rail") == 0) pixels = 88; /* Filer v70 outside rail */
  else if (g_strcmp0(width, "rail-wide") == 0) pixels = 120;
  else if (g_strcmp0(width, "narrow") == 0) pixels = LUMA_UI_NAV_WIDTHS_NARROW;
  else if (g_strcmp0(width, "regular") == 0) pixels = LUMA_UI_NAV_WIDTHS_REGULAR;
  else if (g_strcmp0(width, "wide") == 0) pixels = LUMA_UI_NAV_WIDTHS_WIDE;
  else { g_return_if_fail(FALSE); }

  gtk_widget_add_css_class(GTK_WIDGET(split_view), "luma-live-sidebar-split");
  gtk_widget_add_css_class(surface, "luma-live-sidebar-surface");
  gtk_widget_add_css_class(live_sidebar, "luma-live-sidebar");
  luma_ui_set_css_class(surface, "luma-live-sidebar-rail", pixels == 88 || pixels == 120);
  luma_ui_set_css_class(surface, "luma-live-sidebar-roomy", pixels == 120);
  if (pixels == 88 || pixels == 120) adapt_live_rail_rows(live_sidebar, pixels == 120);
  gtk_widget_set_overflow(surface, GTK_OVERFLOW_HIDDEN);
  gtk_widget_set_size_request(surface, pixels, -1);
  /* Set the bounds in the safe order, as libadwaita requires min <= max
   * throughout the update. This leaves the app's live split view intact. */
  double current_max = adw_overlay_split_view_get_max_sidebar_width(split_view);
  if (pixels > current_max) {
    adw_overlay_split_view_set_max_sidebar_width(split_view, pixels);
    adw_overlay_split_view_set_min_sidebar_width(split_view, pixels);
  } else {
    adw_overlay_split_view_set_min_sidebar_width(split_view, pixels);
    adw_overlay_split_view_set_max_sidebar_width(split_view, pixels);
  }
  GtkWidget *parent = gtk_widget_get_parent(GTK_WIDGET(split_view));
  if (parent != NULL) {
    gtk_widget_add_css_class(parent, "luma-window-body");
    gtk_widget_add_css_class(parent, "lumaui-frame-sidebar");
  }
}

struct _LumaNavigationRow {
  GtkListBoxRow parent_instance;
  GtkWidget *line;
  GtkWidget *lead;
  GtkWidget *trail;
  GtkWidget *title;
  GtkWidget *subtitle;
  GtkWidget *meta;
};
G_DEFINE_FINAL_TYPE(LumaNavigationRow, luma_navigation_row, GTK_TYPE_LIST_BOX_ROW)
static void luma_navigation_row_class_init(LumaNavigationRowClass *klass G_GNUC_UNUSED) {}
static void speak(LumaNavigationRow *self) {
  const char *title = gtk_label_get_text(GTK_LABEL(self->title));
  const char *subtitle = gtk_label_get_text(GTK_LABEL(self->subtitle));
  g_autofree char *text = subtitle != NULL && *subtitle ? g_strdup_printf("%s, %s", title, subtitle) : g_strdup(title);
  luma_ui_set_accessible_label(GTK_WIDGET(self), text);
}
static void luma_navigation_row_init(LumaNavigationRow *self) {
  gtk_widget_add_css_class(GTK_WIDGET(self), "luma-navigation-row");
  gtk_widget_add_css_class(GTK_WIDGET(self), "lumaui-row");
  gtk_widget_add_css_class(GTK_WIDGET(self), "lead-none");
  self->line = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(self->line, "luma-navigation-line");
  gtk_widget_add_css_class(self->line, "lumaui-row-line");
  GtkWidget *labels = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_hexpand(labels, TRUE);
  gtk_widget_set_valign(labels, GTK_ALIGN_CENTER);
  gtk_widget_add_css_class(labels, "lumaui-row-labels");
  GtkWidget *first = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(first, "lumaui-row-first");
  self->title = gtk_label_new("");
  gtk_label_set_xalign(GTK_LABEL(self->title), 0);
  gtk_widget_set_hexpand(self->title, TRUE);
  gtk_widget_add_css_class(self->title, "luma-navigation-title");
  gtk_widget_add_css_class(self->title, "lumaui-row-title");
  gtk_box_append(GTK_BOX(first), self->title);
  self->meta = gtk_label_new("");
  gtk_widget_add_css_class(self->meta, "lumaui-row-meta");
  /* A value at the row's end is short (v70 .cfside .si em: at most 92 wide) and ellipsizes. */
  gtk_label_set_ellipsize(GTK_LABEL(self->meta), PANGO_ELLIPSIZE_END);
  gtk_label_set_max_width_chars(GTK_LABEL(self->meta), 16);
  gtk_widget_set_visible(self->meta, FALSE);
  gtk_box_append(GTK_BOX(first), self->meta);
  GtkWidget *second = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class(second, "lumaui-row-second");
  self->subtitle = gtk_label_new("");
  gtk_label_set_xalign(GTK_LABEL(self->subtitle), 0);
  gtk_widget_set_hexpand(self->subtitle, TRUE);
  gtk_widget_add_css_class(self->subtitle, "luma-navigation-subtitle");
  gtk_widget_add_css_class(self->subtitle, "lumaui-row-subtitle");
  gtk_box_append(GTK_BOX(second), self->subtitle);
  gtk_box_append(GTK_BOX(labels), first);
  gtk_box_append(GTK_BOX(labels), second);
  gtk_box_append(GTK_BOX(self->line), labels);
  gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(self), self->line);
}
GtkWidget *luma_navigation_row_new(const char *title, const char *subtitle) {
  GtkWidget *row = g_object_new(LUMA_TYPE_NAVIGATION_ROW, NULL);
  luma_navigation_row_set_title(LUMA_NAVIGATION_ROW(row), title);
  luma_navigation_row_set_subtitle(LUMA_NAVIGATION_ROW(row), subtitle);
  return row;
}
void luma_navigation_row_set_title(LumaNavigationRow *self, const char *title) {
  g_return_if_fail(LUMA_IS_NAVIGATION_ROW(self));
  gtk_label_set_text(GTK_LABEL(self->title), title != NULL ? title : "");
  speak(self);
}
void luma_navigation_row_set_subtitle(LumaNavigationRow *self, const char *subtitle) {
  g_return_if_fail(LUMA_IS_NAVIGATION_ROW(self));
  gtk_label_set_text(GTK_LABEL(self->subtitle), subtitle != NULL ? subtitle : "");
  gtk_widget_set_visible(self->subtitle, subtitle != NULL && *subtitle != '\0');
  speak(self);
}
void luma_navigation_row_set_meta(LumaNavigationRow *self, const char *meta) {
  g_return_if_fail(LUMA_IS_NAVIGATION_ROW(self));
  gtk_label_set_text(GTK_LABEL(self->meta), meta != NULL ? meta : "");
  gtk_widget_set_visible(self->meta, meta != NULL && *meta != '\0');
}
void luma_navigation_row_set_lead(LumaNavigationRow *self, GtkWidget *lead) {
  g_return_if_fail(LUMA_IS_NAVIGATION_ROW(self) && (lead == NULL || GTK_IS_WIDGET(lead)));
  if (self->lead != NULL) gtk_box_remove(GTK_BOX(self->line), self->lead);
  self->lead = lead;
  luma_ui_set_css_class(GTK_WIDGET(self), "lead-none", lead == NULL);
  if (lead != NULL) {
    /* The kit's row slots, as Python's RowLead: a glyph is a lead icon (its size and ink are the variant's). */
    gtk_widget_add_css_class(lead, "lumaui-row-lead");
    if (GTK_IS_IMAGE(lead))
      gtk_widget_add_css_class(lead, "lead-icon");
    gtk_box_insert_child_after(GTK_BOX(self->line), lead, NULL);
  }
}
void luma_navigation_row_set_trail(LumaNavigationRow *self, GtkWidget *trail) {
  g_return_if_fail(LUMA_IS_NAVIGATION_ROW(self) && (trail == NULL || GTK_IS_WIDGET(trail)));
  if (self->trail != NULL) gtk_box_remove(GTK_BOX(self->line), self->trail);
  self->trail = trail;
  if (trail != NULL) {
    gtk_widget_add_css_class(trail, "lumaui-row-trail");
    gtk_box_append(GTK_BOX(self->line), trail);
  }
}
