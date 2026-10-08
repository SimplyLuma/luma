/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI creative family (KB-D): the creative suite's work area and its
 * floating panels (v70 .suwork, .supanel, .supill). Canvas, Grid, Stage,
 * Session and Write build their windows from these; Python reaches them
 * through gi.repository.LumaUI (luma_appkit.creative_workspace).
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaPanelSide:
 * @LUMA_PANEL_SIDE_LEFT: the left panel (layers, outline, sheets; 244 px)
 * @LUMA_PANEL_SIDE_RIGHT: the right panel (the inspector; 268 px)
 *
 * Which side of a #LumaCreativeWorkspace a #LumaFloatingPanel floats on.
 */
typedef enum {
  LUMA_PANEL_SIDE_LEFT,
  LUMA_PANEL_SIDE_RIGHT,
} LumaPanelSide;

GType luma_panel_side_get_type(void) G_GNUC_CONST;
#define LUMA_TYPE_PANEL_SIDE (luma_panel_side_get_type())

/**
 * LumaFloatingPanel:
 *
 * A panel floating over the work (v70 .supanel): a header, optional tabs,
 * and a scrolling body. It folds to a pill naming what it holds ("Page 1 ·
 * 12 layers"), and a person unfolds it again from the pill. On a phone the
 * open panel is a bottom drawer and only one is open at a time; Esc folds
 * it.
 *
 * The header says *what* the panel is about:
 * - with a title menu (luma_floating_panel_set_title_menu()), the title is a
 *   picker with a chevron (the page picker, .suph1), and no icon leads;
 * - otherwise the icon leads, and the title is a label or, when editable, a
 *   name field (the inspector's subject, .suinsph), which emits
 *   #LumaFloatingPanel::renamed.
 *
 * Tree: box.lumaui-creative-panel(.left|.right)(.folded)(.gone)(.drawer)
 *   ├ box.lumaui-creative-panel-card
 *   │   ├ box.lumaui-creative-handle
 *   │   ├ box.lumaui-creative-panel-header(.menu)
 *   │   │   ├ image.lumaui-creative-panel-icon
 *   │   │   ├ label | button | entry .lumaui-creative-panel-title
 *   │   │   ├ button.lumaui-creative-panel-button.more
 *   │   │   └ button.lumaui-creative-panel-button.close
 *   │   ├ box.lumaui-creative-tabs > button.lumaui-creative-tab …
 *   │   └ scrolledwindow.lumaui-creative-panel-scroll > viewport > stack
 *   └ button.lumaui-creative-pill > box > image, label.lumaui-creative-pill-title,
 *                                         label.lumaui-creative-pill-summary
 */
#define LUMA_TYPE_FLOATING_PANEL (luma_floating_panel_get_type())
G_DECLARE_FINAL_TYPE(LumaFloatingPanel, luma_floating_panel, LUMA, FLOATING_PANEL, GtkBox)

/**
 * luma_floating_panel_new:
 * @title: what it holds ("Page 1", "Rectangle 4", "Outline")
 * @icon: a Lucide glyph for the pill and the header ("layers")
 *
 * Returns: (transfer floating): a new, open, empty panel
 */
GtkWidget *luma_floating_panel_new(const char *title, const char *icon);

/**
 * luma_floating_panel_set_header_actions:
 * @self: a floating panel
 * @actions: (nullable): unparented actions after the title; NULL removes them
 *
 * Keep the same action widgets when the header title or folded state changes.
 */
void luma_floating_panel_set_header_actions(LumaFloatingPanel *self, GtkWidget *actions);

void luma_floating_panel_set_title(LumaFloatingPanel *self, const char *title);
const char *luma_floating_panel_get_title(LumaFloatingPanel *self);
/**
 * luma_floating_panel_set_icon:
 * @self: a panel
 * @icon: a Lucide glyph
 */
void luma_floating_panel_set_icon(LumaFloatingPanel *self, const char *icon);

/**
 * luma_floating_panel_set_summary:
 * @self: a panel
 * @summary: (nullable): the quiet words after the title on the pill
 *   ("12 layers", "5 headings")
 */
void luma_floating_panel_set_summary(LumaFloatingPanel *self, const char *summary);

/**
 * luma_floating_panel_set_holds:
 * @self: a panel
 * @holds: (nullable): what the panel holds, for its Hide button ("layers",
 *   "outline", "inspector": "Hide layers"). By default, the first tab's name,
 *   or the title.
 */
void luma_floating_panel_set_holds(LumaFloatingPanel *self, const char *holds);

/**
 * luma_floating_panel_set_title_menu:
 * @self: a panel
 * @menu: (nullable): what the title picks between (pages, sheets); its
 *   actions are activated on the panel
 *
 * Make the title a picker (.suph1). %NULL makes it a plain title again.
 */
void luma_floating_panel_set_title_menu(LumaFloatingPanel *self, GMenuModel *menu);

/**
 * luma_floating_panel_set_title_editable:
 * @self: a panel
 * @editable: whether a person can rename the subject in the header
 *
 * The inspector's name field (.suname): Enter or leaving it commits and
 * emits #LumaFloatingPanel::renamed; Esc puts the name back.
 */
void luma_floating_panel_set_title_editable(LumaFloatingPanel *self, gboolean editable);

/**
 * luma_floating_panel_set_more_menu:
 * @self: a panel
 * @menu: (nullable): the subject's More menu (the ellipsis button)
 */
void luma_floating_panel_set_more_menu(LumaFloatingPanel *self, GMenuModel *menu);

/**
 * luma_floating_panel_set_closable:
 * @self: a panel
 * @closable: whether the header has the Hide button (panel-left on the left,
 *   panel-right on the right, x in a phone's drawer). A drawer can always
 *   be closed, closable or not.
 */
void luma_floating_panel_set_closable(LumaFloatingPanel *self, gboolean closable);

/**
 * luma_floating_panel_add_page:
 * @self: a panel
 * @key: the page's stable key ("layers")
 * @label: its tab's name ("Layers")
 * @child: what it shows; it scrolls in the panel
 *
 * Add a page. With two or more pages the panel shows its tabs (.sutabs);
 * with one, just the page. The first page added is current.
 */
void luma_floating_panel_add_page(LumaFloatingPanel *self, const char *key, const char *label, GtkWidget *child);

/**
 * luma_floating_panel_set_child:
 * @self: a panel
 * @child: (nullable): the panel's one page
 *
 * A panel without tabs: replaces every page with @child.
 */
void luma_floating_panel_set_child(LumaFloatingPanel *self, GtkWidget *child);

/**
 * luma_floating_panel_set_page:
 * @self: a panel
 * @key: a page's key
 *
 * Show a page without emitting #LumaFloatingPanel::page-changed.
 */
void luma_floating_panel_set_page(LumaFloatingPanel *self, const char *key);
const char *luma_floating_panel_get_page(LumaFloatingPanel *self);

/**
 * luma_floating_panel_set_folded:
 * @self: a panel
 * @folded: whether it is folded to its pill
 */
void luma_floating_panel_set_folded(LumaFloatingPanel *self, gboolean folded);
gboolean luma_floating_panel_get_folded(LumaFloatingPanel *self);

LumaPanelSide luma_floating_panel_get_side(LumaFloatingPanel *self);

/**
 * luma_floating_panel_get_is_drawer:
 * @self: a panel
 *
 * Returns: whether the panel is open as a phone's bottom drawer
 */
gboolean luma_floating_panel_get_is_drawer(LumaFloatingPanel *self);

/**
 * LumaCreativeWorkspace:
 *
 * The creative suite's work area (v70 .suwork): the canvas ground with its
 * dot grid, the app's own content, a left and a right #LumaFloatingPanel, a
 * #LumaToolBar docked at the bottom centre, and a zoom control in the bottom
 * right corner.
 *
 * The workspace owns the placement: panels float 10 px in from the top
 * corners and stop above the tool bar; in focus mode (#LumaCreativeWorkspace:focused)
 * the panels and zoom slide and fade away while the tools stay. At phone
 * width the panels fold to their pills; an unfolded one is a bottom drawer
 * (72 % of the height at most), one at a time, and the zoom control leaves.
 *
 * The dot grid follows the content's camera: luma_creative_workspace_set_camera().
 *
 * Tree: widget.lumaui-creative-workspace(.phone)(.focused)
 *   ├ widget.lumaui-creative-grid   (the dot colour; painted by the workspace)
 *   ├ content
 *   ├ box.lumaui-creative-panel.left, box.lumaui-creative-panel.right
 *   ├ zoom (.lumaui-creative-zoom)
 *   └ box.lumaui-creative-tools
 */
#define LUMA_TYPE_CREATIVE_WORKSPACE (luma_creative_workspace_get_type())
G_DECLARE_FINAL_TYPE(LumaCreativeWorkspace, luma_creative_workspace, LUMA, CREATIVE_WORKSPACE, GtkWidget)

/**
 * luma_creative_workspace_new:
 * @content: (nullable): the work: the canvas, sheet, slide or page; it
 *   fills the workspace under the panels and should leave its ground clear
 *
 * Returns: (transfer floating): a new workspace
 */
GtkWidget *luma_creative_workspace_new(GtkWidget *content);

void luma_creative_workspace_set_content(LumaCreativeWorkspace *self, GtkWidget *content);
/**
 * luma_creative_workspace_get_content:
 * @self: a workspace
 * Returns: (transfer none) (nullable): the content
 */
GtkWidget *luma_creative_workspace_get_content(LumaCreativeWorkspace *self);

/**
 * luma_creative_workspace_set_left:
 * @self: a workspace
 * @panel: (nullable): the left panel
 */
void luma_creative_workspace_set_left(LumaCreativeWorkspace *self, LumaFloatingPanel *panel);
/**
 * luma_creative_workspace_get_left:
 * @self: a workspace
 * Returns: (transfer none) (nullable): the left panel
 */
LumaFloatingPanel *luma_creative_workspace_get_left(LumaCreativeWorkspace *self);
/**
 * luma_creative_workspace_set_right:
 * @self: a workspace
 * @panel: (nullable): the right panel (the inspector)
 */
void luma_creative_workspace_set_right(LumaCreativeWorkspace *self, LumaFloatingPanel *panel);
/**
 * luma_creative_workspace_get_right:
 * @self: a workspace
 * Returns: (transfer none) (nullable): the right panel
 */
LumaFloatingPanel *luma_creative_workspace_get_right(LumaCreativeWorkspace *self);

/**
 * luma_creative_workspace_set_tools:
 * @self: a workspace
 * @tools: (nullable): the tool bar, usually a #LumaToolBar
 */
void luma_creative_workspace_set_tools(LumaCreativeWorkspace *self, GtkWidget *tools);

/**
 * luma_creative_workspace_set_zoom:
 * @self: a workspace
 * @zoom: (nullable): the zoom control for the bottom right corner (the
 *   kit's zoom pill); it leaves in focus mode and at phone width
 */
void luma_creative_workspace_set_zoom(LumaCreativeWorkspace *self, GtkWidget *zoom);

/**
 * luma_creative_workspace_set_focused:
 * @self: a workspace
 * @focused: focus mode: only the work and its tools stay
 */
void luma_creative_workspace_set_focused(LumaCreativeWorkspace *self, gboolean focused);
gboolean luma_creative_workspace_get_focused(LumaCreativeWorkspace *self);

/**
 * luma_creative_workspace_set_camera:
 * @self: a workspace
 * @zoom: the content's scale (1 is 100 %)
 * @x: the content origin's x in the workspace, in pixels
 * @y: the content origin's y
 *
 * Keep the dot grid with the content: the dots sit 64 px apart at 100 %,
 * never closer than 32 px, and move with the origin.
 */
void luma_creative_workspace_set_camera(LumaCreativeWorkspace *self, double zoom, double x, double y);

/**
 * luma_creative_workspace_set_grid_visible:
 * @self: a workspace
 * @visible: whether the ground shows its dot grid (Grid's sheet and Write's
 *   page have none)
 */
void luma_creative_workspace_set_grid_visible(LumaCreativeWorkspace *self, gboolean visible);

/**
 * luma_creative_workspace_get_is_phone:
 * @self: a workspace
 * Returns: whether the workspace is laid out for a phone
 */
gboolean luma_creative_workspace_get_is_phone(LumaCreativeWorkspace *self);

/**
 * luma_creative_grid_spacing:
 * @zoom: the content's scale
 *
 * The dot grid's pitch at @zoom: 64 px at 100 %, never under 32 px.
 *
 * Returns: the spacing in pixels
 */
double luma_creative_grid_spacing(double zoom);

G_END_DECLS
