/* SPDX-License-Identifier: Apache-2.0 */
/*
 * LumaUI creative family (KB-D): the tool bar (v70 .sutools) and its
 * floating variant, the tool palette (#vw-pal).
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

/**
 * LumaToolBarKind:
 * @LUMA_TOOL_BAR_KIND_BAR: the 40 px raised strip docked at the bottom of a
 *   #LumaCreativeWorkspace; the chosen tool is raised (a state, never the key)
 * @LUMA_TOOL_BAR_KIND_PALETTE: the floating palette over a picture (Viewer's
 *   mark-up tools); 36 px tools, the chosen one is the key
 */
typedef enum {
  LUMA_TOOL_BAR_KIND_BAR,
  LUMA_TOOL_BAR_KIND_PALETTE,
} LumaToolBarKind;

GType luma_tool_bar_kind_get_type(void) G_GNUC_CONST;
#define LUMA_TYPE_TOOL_BAR_KIND (luma_tool_bar_kind_get_type())

/**
 * LumaToolBar:
 *
 * The tools of a creative app, one of which is current. Each tool has a
 * Lucide glyph, a name and a one-key shortcut, shown as "Move · V". A
 * flyout groups alternatives (Rectangle, Ellipse, Line) behind one tool
 * that shows the member last chosen and a corner mark; pressing it opens
 * the flyout (a drawer on a phone). Arrows move between tools; the
 * shortcut keys choose them anywhere in the window, except while typing.
 *
 * Emits #LumaToolBar::tool-changed when a person picks a tool, never for
 * luma_tool_bar_set_current().
 *
 * Tree: box.lumaui-creative-tools(.bar|.palette)
 *   ├ button.lumaui-creative-tool(.on)(.flyout) > overlay > image (+ box.lumaui-creative-tool-more)
 *   └ separator.lumaui-creative-tools-separator
 */
#define LUMA_TYPE_TOOL_BAR (luma_tool_bar_get_type())
G_DECLARE_FINAL_TYPE(LumaToolBar, luma_tool_bar, LUMA, TOOL_BAR, GtkBox)

/**
 * luma_tool_bar_new:
 * @kind: the bar or the palette
 *
 * Returns: (transfer floating): a new, empty tool bar
 */
GtkWidget *luma_tool_bar_new(LumaToolBarKind kind);

/**
 * luma_tool_bar_add_tool:
 * @self: a tool bar
 * @key: the tool's stable key ("move")
 * @icon: a Lucide glyph ("mouse-pointer-2")
 * @label: its name ("Move")
 * @shortcut: (nullable): its one key ("V"), or %NULL
 *
 * Add a tool. The first tool added is current until another is chosen.
 */
void luma_tool_bar_add_tool(LumaToolBar *self, const char *key, const char *icon, const char *label,
                            const char *shortcut);

/**
 * luma_tool_bar_add_flyout:
 * @self: a tool bar
 * @key: the group's key ("shape")
 * @label: its name ("Shapes")
 *
 * Add a flyout; give it members with luma_tool_bar_add_flyout_tool(). The
 * group shows its first member until another is chosen.
 */
void luma_tool_bar_add_flyout(LumaToolBar *self, const char *key, const char *label);

/**
 * luma_tool_bar_add_flyout_tool:
 * @self: a tool bar
 * @flyout: a flyout's key
 * @key: the tool's key ("ellipse")
 * @icon: a Lucide glyph ("circle")
 * @label: its name ("Ellipse")
 * @shortcut: (nullable): its one key ("O")
 */
void luma_tool_bar_add_flyout_tool(LumaToolBar *self, const char *flyout, const char *key, const char *icon,
                                   const char *label, const char *shortcut);

/**
 * luma_tool_bar_add_separator:
 * @self: a tool bar
 *
 * A hairline between groups of tools (making tools and viewing tools).
 */
void luma_tool_bar_add_separator(LumaToolBar *self);

/**
 * luma_tool_bar_set_current:
 * @self: a tool bar
 * @key: a tool's key (a flyout member's, not the flyout's)
 */
void luma_tool_bar_set_current(LumaToolBar *self, const char *key);
const char *luma_tool_bar_get_current(LumaToolBar *self);

/**
 * luma_tool_bar_open_flyout:
 * @self: a tool bar
 * @flyout: a flyout's key
 *
 * Open a flyout as a person pressing it would.
 */
void luma_tool_bar_open_flyout(LumaToolBar *self, const char *flyout);

/**
 * luma_tool_bar_activate_shortcut:
 * @self: a tool bar
 * @key: a key name ("v" or "V")
 *
 * Choose the tool whose shortcut is @key, as pressing it would.
 *
 * Returns: whether a tool had that shortcut
 */
gboolean luma_tool_bar_activate_shortcut(LumaToolBar *self, const char *key);

G_END_DECLS
