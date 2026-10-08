/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-adaptive-scaffold.h"
#include "luma-ui-kit.h"

struct _LumaAdaptiveScaffold {
  AdwBreakpointBin parent_instance;
  AdwNavigationSplitView *split;
  AdwBreakpoint *breakpoint;
  guint compact_width;
  guint sidebar_width;
  guint minimum_sidebar_width;
  guint maximum_sidebar_width;
  gboolean sidebar_width_set;
};
G_DEFINE_FINAL_TYPE(LumaAdaptiveScaffold, luma_adaptive_scaffold,
                    ADW_TYPE_BREAKPOINT_BIN)

enum { PROP_ZERO, PROP_SIDEBAR_WIDTH, N_PROPERTIES };
static GParamSpec *properties[N_PROPERTIES];

static void apply_sidebar_width(LumaAdaptiveScaffold *s) {
  g_object_freeze_notify(G_OBJECT(s->split));
  adw_navigation_split_view_set_sidebar_width_unit(s->split, ADW_LENGTH_UNIT_PX);
  /* Keep the native interval valid throughout both widening and narrowing. */
  if (s->sidebar_width > adw_navigation_split_view_get_max_sidebar_width(s->split)) {
    adw_navigation_split_view_set_max_sidebar_width(s->split, s->sidebar_width);
    adw_navigation_split_view_set_min_sidebar_width(s->split, s->sidebar_width);
  } else {
    adw_navigation_split_view_set_min_sidebar_width(s->split, s->sidebar_width);
    adw_navigation_split_view_set_max_sidebar_width(s->split, s->sidebar_width);
  }
  g_object_thaw_notify(G_OBJECT(s->split));
}

static void set_sidebar_width(LumaAdaptiveScaffold *s, guint width) {
  width = CLAMP(width, s->minimum_sidebar_width, s->maximum_sidebar_width);
  gboolean changed = s->sidebar_width != width;
  if (s->sidebar_width_set && !changed)
    return;
  s->sidebar_width_set = TRUE;
  s->sidebar_width = width;
  apply_sidebar_width(s);
  if (changed)
    g_object_notify_by_pspec(G_OBJECT(s), properties[PROP_SIDEBAR_WIDTH]);
}

static void luma_adaptive_scaffold_get_property(GObject *object, guint id,
                                               GValue *value, GParamSpec *pspec) {
  if (id == PROP_SIDEBAR_WIDTH)
    g_value_set_uint(value, LUMA_ADAPTIVE_SCAFFOLD(object)->sidebar_width);
  else
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
}

static void luma_adaptive_scaffold_set_property(GObject *object, guint id,
                                               const GValue *value, GParamSpec *pspec) {
  if (id == PROP_SIDEBAR_WIDTH)
    set_sidebar_width(LUMA_ADAPTIVE_SCAFFOLD(object), g_value_get_uint(value));
  else
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec);
}

static void rebuild_breakpoint(LumaAdaptiveScaffold *s) {
  g_autofree char *condition =
      g_strdup_printf("max-width: %upx", s->compact_width);
  if (s->breakpoint) {
    /* Retain the setter's original uncollapsed state across condition changes. */
    AdwBreakpointCondition *parsed = adw_breakpoint_condition_parse(condition);
    adw_breakpoint_set_condition(s->breakpoint, parsed);
    adw_breakpoint_condition_free(parsed);
    return;
  }
  s->breakpoint = adw_breakpoint_new(adw_breakpoint_condition_parse(condition));
  GValue value = G_VALUE_INIT;
  g_value_init(&value, G_TYPE_BOOLEAN);
  g_value_set_boolean(&value, TRUE);
  adw_breakpoint_add_setter(s->breakpoint, G_OBJECT(s->split), "collapsed",
                            &value);
  g_value_unset(&value);
  adw_breakpoint_bin_add_breakpoint(ADW_BREAKPOINT_BIN(s), s->breakpoint);
}
static void luma_adaptive_scaffold_dispose(GObject *o) {
  LumaAdaptiveScaffold *s = LUMA_ADAPTIVE_SCAFFOLD(o);
  s->split = NULL;
  s->breakpoint = NULL;
  G_OBJECT_CLASS(luma_adaptive_scaffold_parent_class)->dispose(o);
}
static void luma_adaptive_scaffold_class_init(LumaAdaptiveScaffoldClass *k) {
  G_OBJECT_CLASS(k)->dispose = luma_adaptive_scaffold_dispose;
  G_OBJECT_CLASS(k)->get_property = luma_adaptive_scaffold_get_property;
  G_OBJECT_CLASS(k)->set_property = luma_adaptive_scaffold_set_property;
  properties[PROP_SIDEBAR_WIDTH] = g_param_spec_uint(
      "sidebar-width", NULL, NULL, 120, 640, 180,
      G_PARAM_READWRITE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);
  g_object_class_install_properties(G_OBJECT_CLASS(k), N_PROPERTIES, properties);
}
static void luma_adaptive_scaffold_init(LumaAdaptiveScaffold *s) {
  /* BreakpointBin needs a nonzero floor while navigation pages change. */
  gtk_widget_set_size_request(GTK_WIDGET(s), 1, 1);
  s->compact_width = LUMA_TIER_PHONE_BELOW - 1; /* v71: the phone tier, as every kit part (was 639) */
  s->sidebar_width = 180;
  s->minimum_sidebar_width = 160;
  s->maximum_sidebar_width = 320;
  s->split = ADW_NAVIGATION_SPLIT_VIEW(adw_navigation_split_view_new());
  adw_breakpoint_bin_set_child(ADW_BREAKPOINT_BIN(s), GTK_WIDGET(s->split));
  gtk_widget_add_css_class(GTK_WIDGET(s), "luma-adaptive-scaffold");
  rebuild_breakpoint(s);
}
GtkWidget *luma_adaptive_scaffold_new(void) {
  return g_object_new(LUMA_TYPE_ADAPTIVE_SCAFFOLD, NULL);
}
void luma_adaptive_scaffold_set_sidebar(LumaAdaptiveScaffold *s, GtkWidget *w,
                                        const char *t) {
  g_return_if_fail(LUMA_IS_ADAPTIVE_SCAFFOLD(s));
  g_return_if_fail(GTK_IS_WIDGET(w));
  AdwNavigationPage *p = adw_navigation_page_new(w, t ? t : "");
  adw_navigation_split_view_set_sidebar(s->split, p);
}
void luma_adaptive_scaffold_set_content(LumaAdaptiveScaffold *s, GtkWidget *w,
                                        const char *t) {
  g_return_if_fail(LUMA_IS_ADAPTIVE_SCAFFOLD(s));
  g_return_if_fail(GTK_IS_WIDGET(w));
  AdwNavigationPage *p = adw_navigation_page_new(w, t ? t : "");
  adw_navigation_split_view_set_content(s->split, p);
}
void luma_adaptive_scaffold_set_compact_width(LumaAdaptiveScaffold *s,
                                              guint w) {
  g_return_if_fail(LUMA_IS_ADAPTIVE_SCAFFOLD(s));
  g_return_if_fail(w >= 320 && w <= 1200);
  if (s->compact_width == w)
    return;
  s->compact_width = w;
  rebuild_breakpoint(s);
}

void luma_adaptive_scaffold_set_sidebar_width_limits(
    LumaAdaptiveScaffold *s, guint minimum_width, guint maximum_width) {
  g_return_if_fail(LUMA_IS_ADAPTIVE_SCAFFOLD(s));
  g_return_if_fail(minimum_width >= 120);
  g_return_if_fail(maximum_width <= 640);
  g_return_if_fail(minimum_width <= maximum_width);
  s->minimum_sidebar_width = minimum_width;
  s->maximum_sidebar_width = maximum_width;
  set_sidebar_width(s, s->sidebar_width);
}

void luma_adaptive_scaffold_set_sidebar_width(LumaAdaptiveScaffold *s, guint width) {
  g_return_if_fail(LUMA_IS_ADAPTIVE_SCAFFOLD(s));
  set_sidebar_width(s, width);
}

guint luma_adaptive_scaffold_get_sidebar_width(LumaAdaptiveScaffold *s) {
  g_return_val_if_fail(LUMA_IS_ADAPTIVE_SCAFFOLD(s), 0);
  return s->sidebar_width;
}

gboolean luma_adaptive_scaffold_get_collapsed(LumaAdaptiveScaffold *s) {
  g_return_val_if_fail(LUMA_IS_ADAPTIVE_SCAFFOLD(s), FALSE);
  return adw_navigation_split_view_get_collapsed(s->split);
}
void luma_adaptive_scaffold_set_show_content(LumaAdaptiveScaffold *s,
                                             gboolean v) {
  g_return_if_fail(LUMA_IS_ADAPTIVE_SCAFFOLD(s));
  adw_navigation_split_view_set_show_content(s->split, v);
}
